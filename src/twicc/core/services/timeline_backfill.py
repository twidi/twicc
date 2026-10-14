"""Conservative, incremental backfill of the timeline (design 2026-10-14).

A session is "processed" when it has a ``session.created`` timeline row. The
backfill lists the candidate sessions once (ids only, newest ``mtime`` first),
then handles them one at a time, each inside its own lock acquisition. It
writes only ``session.created`` and the flag intervals, never a process row:
``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL
file and cannot prove that TwiCC ran a process.
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import NamedTuple

from django.db import transaction
from django.db.models import Exists, OuterRef

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.core.timeline import validate_data
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

#: (flag attribute, started kind, subject prefix) — a flag is "set" when truthy / not null.
_FLAGS = (
    ("archived", TimelineKind.ARCHIVED_STARTED, "archived."),
    ("hidden", TimelineKind.HIDDEN_STARTED, "hidden."),
    ("mute_on_user_turn", TimelineKind.MUTED_STARTED, "muted."),
    ("pinned", TimelineKind.PINNED_STARTED, "pinned."),
)

#: The only kinds read to know what a session already has: its creation and, per flag subject,
#: both directions. Bounded by a handful of rows whatever the session's activity.
_RELEVANT_KINDS = (
    TimelineKind.SESSION_CREATED,
    TimelineKind.ARCHIVED_STARTED, TimelineKind.ARCHIVED_ENDED,
    TimelineKind.HIDDEN_STARTED, TimelineKind.HIDDEN_ENDED,
    TimelineKind.MUTED_STARTED, TimelineKind.MUTED_ENDED,
    TimelineKind.PINNED_STARTED, TimelineKind.PINNED_ENDED,
)


class BackfillStatus(StrEnum):
    WRITTEN = "written"
    SKIPPED = "skipped"


class SessionOutcome(NamedTuple):
    status: BackfillStatus
    rows: int = 0


def candidate_ids() -> list[str]:
    """Ids of the sessions to process, newest ``mtime`` first (ties: id descending).

    Sync, outside the lock, called once per run. A candidate is a normal session with
    ``created_at`` set and no ``session.created`` row: the anti-join is a direct seek on the
    ``(session_id, kind)`` index, whatever the number of timeline events.
    """
    has_created = TimelineEvent.objects.filter(session_id=OuterRef("id"), kind=TimelineKind.SESSION_CREATED)
    return list(
        Session.objects.filter(type=SessionType.SESSION, created_at__isnull=False)
        .filter(~Exists(has_created))
        .order_by("-mtime", "-id")
        .values_list("id", flat=True)
    )


def _row(session: Session, kind: str, occurred_at, precision: str, data: dict | None) -> TimelineEvent:
    return TimelineEvent(
        session_id=session.id, project_id=session.project_id, kind=kind, occurred_at=occurred_at,
        precision=precision, actor=TimelineActor.SYSTEM, data=validate_data(kind, data),
        dedupe_key=f"{BACKFILL_PREFIX}{kind}",
    )


def _build_rows(session: Session, existing: set[str]) -> list[TimelineEvent]:
    """The rows of one session. ``created_at`` is never empty here (candidates require it)."""
    rows = [_row(
        session, TimelineKind.SESSION_CREATED, session.created_at, TimelinePrecision.DERIVED,
        {"origin": "sync", "provider": session.provider, "settings": None, "spawned_by": session.spawned_by_id},
    )]
    flag_time = session.last_updated_at or session.created_at
    for attribute, kind, prefix in _FLAGS:
        value = getattr(session, attribute)
        if not value or any(known.startswith(prefix) for known in existing):
            continue
        rows.append(_row(
            session, kind, flag_time, TimelinePrecision.ESTIMATED,
            {"scope": value} if attribute == "pinned" else None,
        ))
    return rows


def _drop_present(session_id: str, rows: list[TimelineEvent]) -> list[TimelineEvent]:
    """Drop the rows whose ``dedupe_key`` already exists: that counts as written, not as a failure."""
    present = set(
        TimelineEvent.objects.filter(session_id=session_id, dedupe_key__in=[r.dedupe_key for r in rows])
        .values_list("dedupe_key", flat=True)
    )
    return [row for row in rows if row.dedupe_key not in present]


def _insert_rows(rows: list[TimelineEvent]) -> None:
    """The backfill's own insert path: it validates before (see ``_row``), shares the duplicate
    check with ``record_event_sync`` (``_drop_present``) and, unlike it, lets every error propagate
    so the session's transaction is rolled back as a whole."""
    TimelineEvent.objects.bulk_create(rows)


def backfill_session_sync(session_id: str) -> SessionOutcome:
    """Process one session, all-or-nothing, in one transaction. Raises on any write failure.

    Sync: call under the DB write lock from a worker thread, one acquisition per session.
    Everything is read fresh here, so a hook that wrote ``session.created`` since the id list was
    read is seen and the session is skipped.
    """
    with transaction.atomic():
        session = Session.objects.filter(
            id=session_id, type=SessionType.SESSION, created_at__isnull=False,
        ).first()
        if session is None:
            return SessionOutcome(BackfillStatus.SKIPPED)
        existing = set(
            TimelineEvent.objects.filter(session_id=session_id, kind__in=_RELEVANT_KINDS)
            .values_list("kind", flat=True).distinct()
        )
        if TimelineKind.SESSION_CREATED in existing:
            return SessionOutcome(BackfillStatus.SKIPPED)
        rows = _drop_present(session_id, _build_rows(session, existing))
        if rows:
            _insert_rows(rows)
        return SessionOutcome(BackfillStatus.WRITTEN, len(rows))


async def run_timeline_backfill() -> None:
    """Process every candidate session, one lock acquisition each, newest first (single pass)."""
    session_ids = await asyncio.to_thread(candidate_ids)
    written = rows = 0
    for session_id in session_ids:
        outcome = await run_under_db_write_lock(
            lambda current=session_id: asyncio.to_thread(backfill_session_sync, current)
        )
        if outcome.status == BackfillStatus.WRITTEN:
            written += 1
            rows += outcome.rows
        await asyncio.sleep(0)
    if written:
        logger.info("Timeline backfill wrote %d row(s) for %d session(s)", rows, written)
