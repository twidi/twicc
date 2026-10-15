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
import time
from collections import Counter
from enum import StrEnum
from typing import NamedTuple

from django.db import transaction
from django.db.models import Exists, OuterRef

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.session_timeline import insert_event_rows
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.core.timeline import validate_data
from twicc.providers.db_writer import is_db_writer_started, run_under_db_write_lock

logger = logging.getLogger(__name__)

#: (flag attribute, started kind) — a flag is "set" when truthy / not null.
_FLAGS = (
    ("archived", TimelineKind.ARCHIVED_STARTED),
    ("hidden", TimelineKind.HIDDEN_STARTED),
    ("mute_on_user_turn", TimelineKind.MUTED_STARTED),
    ("pinned", TimelineKind.PINNED_STARTED),
)


def _subject_prefix(started_kind: str) -> str:
    """``"archived.started"`` -> ``"archived."``: the prefix shared by both directions of a flag."""
    return started_kind.removesuffix("started")


#: The only kinds read to know what a session already has: its creation and, per flag subject,
#: both directions. Bounded by a handful of rows whatever the session's activity.
_RELEVANT_KINDS = (TimelineKind.SESSION_CREATED, *(
    kind for _, started in _FLAGS for kind in (started, f"{_subject_prefix(started)}ended")
))


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
        precision=precision, actor=TimelineActor.SYSTEM, provider=session.provider, data=validate_data(kind, data),
        dedupe_key=f"{BACKFILL_PREFIX}{kind}",
    )


def _build_rows(session: Session, existing: set[str]) -> list[TimelineEvent]:
    """The rows of one session. ``created_at`` is never empty here (candidates require it)."""
    rows = [_row(
        session, TimelineKind.SESSION_CREATED, session.created_at, TimelinePrecision.DERIVED,
        {"origin": "sync", "provider": session.provider, "settings": None, "spawned_by": session.spawned_by_id},
    )]
    flag_time = session.last_updated_at or session.created_at
    for attribute, kind in _FLAGS:
        value = getattr(session, attribute)
        if not value or any(known.startswith(_subject_prefix(kind)) for known in existing):
            continue
        rows.append(_row(
            session, kind, flag_time, TimelinePrecision.ESTIMATED,
            {"scope": value} if attribute == "pinned" else None,
        ))
    return rows


def _insert_rows(rows: list[TimelineEvent]) -> int:
    """The backfill's own insert path: errors propagate (no swallowing) so the session's
    transaction rolls back as a whole. A key already present is skipped by the database and not
    counted (it counts as written, not as a failure). See :func:`insert_event_rows`."""
    return insert_event_rows(rows)


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
        return SessionOutcome(BackfillStatus.WRITTEN, _insert_rows(_build_rows(session, existing)))


#: Pauses (seconds) before each retry round of the sessions that failed: three rounds, growing,
#: under about 10 s in total. They happen outside the lock.
RETRY_PAUSES = (1.0, 3.0, 5.0)

_WRITER_STOPPING = "DB writer is stopping"
_WRITER_NOT_STARTED = "DB writer not started"


class _WriterStopping(Exception):
    """The DB writer refuses new work: the run ends. It is not a session failure."""


def _writer_is_stopping(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and _WRITER_STOPPING in str(exc)


def _writer_is_not_started(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and _WRITER_NOT_STARTED in str(exc)


async def _run_pass(session_ids: list[str], stats: Counter, *, retry: bool = False) -> dict[str, str]:
    """One pass over ``session_ids``, one lock acquisition each. Returns ``{failed id: cause}``.

    The traceback of a failure is logged on the first pass only; a retry logs one line."""
    failed: dict[str, str] = {}
    for session_id in session_ids:
        try:
            outcome = await run_under_db_write_lock(
                lambda current=session_id: asyncio.to_thread(backfill_session_sync, current)
            )
        except Exception as exc:  # noqa: BLE001 - a failed session must never stop the run
            if _writer_is_stopping(exc) or _writer_is_not_started(exc):
                raise _WriterStopping from exc
            logger.warning("Timeline backfill: session %s failed: %s", session_id, exc, exc_info=not retry)
            failed[session_id] = repr(exc)
            continue
        stats[outcome.status] += 1
        stats["rows"] += outcome.rows
        await asyncio.sleep(0)
    return failed


async def run_timeline_backfill() -> None:
    """Process every candidate session, one lock acquisition each, newest first.

    Sessions that failed are retried after the list is exhausted, at most ``len(RETRY_PAUSES)``
    more times with a growing pause; the ones still failing are logged at error level and left
    for the next boot. Does nothing when the DB writer is not started. ``CancelledError`` propagates
    (shutdown).
    """
    if not is_db_writer_started():
        # The backfill needs the writer's lock: without it, do nothing (no read, no retry round).
        logger.warning("Timeline backfill not run: the DB writer is not started")
        return
    started = time.monotonic()
    stats: Counter = Counter()
    session_ids = await asyncio.to_thread(candidate_ids)
    failed: dict[str, str] = {}
    try:
        failed = await _run_pass(session_ids, stats)
        for pause in RETRY_PAUSES:
            if not failed:
                break
            await asyncio.sleep(pause)
            failed = await _run_pass(list(failed), stats, retry=True)
    except _WriterStopping:
        logger.info("Timeline backfill stopped: the DB writer is stopping or gone")
        return
    for session_id, cause in failed.items():
        logger.error(
            "Timeline backfill gave up on session %s after %d retries: %s", session_id, len(RETRY_PAUSES), cause,
        )
    if session_ids:
        logger.info(
            "Timeline backfill: %d session(s) written (%d row(s)), %d skipped, %d failed in %.1f s",
            stats[BackfillStatus.WRITTEN], stats["rows"], stats[BackfillStatus.SKIPPED], len(failed),
            time.monotonic() - started,
        )
