"""Creation rows of the sessions TwiCC did not see being created (spec 2026-10-18).

A session is "known" to the timeline when it has a ``session.created`` row. For a session that
is not, :func:`write_creation_rows_sync` writes ``session.created`` and the flag intervals, never
a process row: ``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL
file and cannot prove that TwiCC ran a process. It is called by the final transaction of the
background compute and by the step that marks a Codex session unavailable.
"""

from __future__ import annotations

import logging

from django.db import transaction

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services import timeline_signal
from twicc.core.services.session_timeline import insert_event_rows
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.core.timeline import validate_data

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


def _row(session: Session, kind: str, occurred_at, precision: str, data: dict | None) -> TimelineEvent:
    return TimelineEvent(
        session_id=session.id, project_id=session.project_id, kind=kind, occurred_at=occurred_at,
        precision=precision, actor=TimelineActor.SYSTEM, provider=session.provider, data=validate_data(kind, data),
        dedupe_key=f"{BACKFILL_PREFIX}{kind}",
    )


def _build_rows(session: Session, existing: set[str]) -> list[TimelineEvent]:
    """The rows of one session. ``created_at`` is never empty here (the caller requires it)."""
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


def write_creation_rows_sync(session_id: str, *, require_computed: bool = False) -> int:
    """Write the creation rows of ``session_id`` if it is not yet known. Returns the rows inserted.

    Sync: the caller already holds the DB write lock and usually runs its own transaction (the
    compute's final transaction, the DB writer job that marks a session unavailable). The rows are
    written in a savepoint: a failure discards only them, is logged, and returns 0. Never raises.

    Nothing is written for a subagent, a missing session, a session without ``created_at``, a
    known session, and, with ``require_computed``, a session whose ``compute_version`` is empty.
    The signal is requested after the caller's transaction commits, only when rows were written.
    """
    try:
        with transaction.atomic():
            sessions = Session.objects.filter(id=session_id, type=SessionType.SESSION, created_at__isnull=False)
            if require_computed:
                sessions = sessions.filter(compute_version__isnull=False)
            session = sessions.first()
            if session is None:
                return 0
            existing = set(
                TimelineEvent.objects.filter(session_id=session_id, kind__in=_RELEVANT_KINDS)
                .values_list("kind", flat=True).distinct()
            )
            if TimelineKind.SESSION_CREATED in existing:
                return 0
            count = insert_event_rows(_build_rows(session, existing))
        if count:
            timeline_signal.poke_on_commit()
        return count
    except Exception:
        logger.exception("Timeline creation rows could not be written for session %s", session_id)
        return 0
