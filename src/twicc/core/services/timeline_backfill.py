"""Conservative backfill of the timeline for existing sessions (design §8).

Writes only ``session.created`` and the flag intervals. Never a process row:
``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL
file and cannot prove that TwiCC ran a process. Idempotent: every row carries
a ``backfill:`` dedupe key, and a subject that already has a row (live or
backfilled) is skipped.
"""

from __future__ import annotations

import asyncio
import logging

from django.db import transaction

from twicc.core.models import Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

BATCH_SIZE = 200

#: (flag attribute, started kind, subject prefix) — "set" means truthy / not null.
_FLAGS = (
    ("archived", TimelineKind.ARCHIVED_STARTED, "archived."),
    ("hidden", TimelineKind.HIDDEN_STARTED, "hidden."),
    ("mute_on_user_turn", TimelineKind.MUTED_STARTED, "muted."),
    ("pinned", TimelineKind.PINNED_STARTED, "pinned."),
)


def _existing_kinds(session_ids: list[str]) -> dict[str, set[str]]:
    existing: dict[str, set[str]] = {sid: set() for sid in session_ids}
    for session_id, kind in TimelineEvent.objects.filter(session_id__in=session_ids).values_list("session_id", "kind"):
        existing[session_id].add(kind)
    return existing


def _backfill_session(session: Session, existing: set[str]) -> int:
    written = 0
    common = dict(session_id=session.id, project_id=session.project_id, actor=TimelineActor.SYSTEM)
    if session.created_at is not None and TimelineKind.SESSION_CREATED not in existing:
        row = record_event_sync(
            TimelineKind.SESSION_CREATED, occurred_at=session.created_at, precision=TimelinePrecision.DERIVED,
            dedupe_key=f"{BACKFILL_PREFIX}{TimelineKind.SESSION_CREATED}",
            data={
                "origin": "sync",
                "provider": session.provider,
                "settings": None,
                "spawned_by": session.spawned_by_id,
            },
            **common,
        )
        written += row is not None
    flag_time = session.last_updated_at or session.created_at
    if flag_time is None:
        return written
    for attribute, kind, prefix in _FLAGS:
        value = getattr(session, attribute)
        if not value or any(known.startswith(prefix) for known in existing):
            continue
        row = record_event_sync(
            kind, occurred_at=flag_time, precision=TimelinePrecision.ESTIMATED,
            dedupe_key=f"{BACKFILL_PREFIX}{kind}",
            data={"scope": value} if attribute == "pinned" else None, **common,
        )
        written += row is not None
    return written


def backfill_batch_sync(after_id: str, batch_size: int = BATCH_SIZE) -> tuple[str | None, int]:
    """Backfill the next ``batch_size`` sessions after ``after_id`` (keyset by id).

    Returns ``(last session id seen, rows written)``; ``(None, 0)`` when no
    session is left. Sync: call under the DB write lock from a worker thread.
    The ``existing`` snapshot is read under the same lock every live write
    takes, so no live row can appear between the read and the inserts.
    """
    sessions = list(
        Session.objects.filter(type=SessionType.SESSION, id__gt=after_id).order_by("id")[:batch_size]
    )
    if not sessions:
        return None, 0
    existing = _existing_kinds([s.id for s in sessions])
    written = 0
    with transaction.atomic():
        for session in sessions:
            written += _backfill_session(session, existing[session.id])
    return sessions[-1].id, written


async def run_timeline_backfill() -> None:
    """Backfill every session, one locked batch at a time, yielding in between."""
    after_id = ""
    total = 0
    while True:
        last_id, written = await run_under_db_write_lock(
            lambda current=after_id: asyncio.to_thread(backfill_batch_sync, current)
        )
        if last_id is None:
            break
        after_id = last_id
        total += written
        await asyncio.sleep(0)
    if total:
        logger.info("Timeline backfill wrote %d row(s)", total)
