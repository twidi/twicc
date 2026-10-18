"""Creation rows of the sessions TwiCC did not see being created (spec 2026-10-18).

A session is "known" to the timeline when it has a ``session.created`` row. For a session that
is not, :func:`write_creation_rows_sync` writes ``session.created`` only, dated by the session's
``created_at`` (the first timestamp of its JSONL file). It never writes a flag row: a flag set
before TwiCC observed it has no known date. It never writes a process row:
``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL file and cannot
prove that TwiCC ran a process. It is called by the final transaction of the background compute
and by the step that marks a Codex session unavailable.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import QuerySet

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services import timeline_signal
from twicc.core.services.session_timeline import insert_event_rows
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.core.timeline import validate_data

logger = logging.getLogger(__name__)


#: The only ``Session`` columns :func:`_creation_row` reads. The session is loaded with them alone:
#: the row also carries large JSON fields, and a deferred field read later would cost one more query.
_SESSION_COLUMNS = ("id", "project_id", "provider", "created_at", "spawned_by_id")


def _creation_rows(session_id: str) -> QuerySet[TimelineEvent]:
    """The ``session.created`` rows of ``session_id``: served by the ``(session_id, kind)`` index."""
    return TimelineEvent.objects.filter(session_id=session_id, kind=TimelineKind.SESSION_CREATED)


def _creation_row(session: Session) -> TimelineEvent:
    """The ``session.created`` row of one session. ``created_at`` is never empty here (the caller requires it)."""
    kind = TimelineKind.SESSION_CREATED
    data = {"origin": "sync", "provider": session.provider, "settings": None, "spawned_by": session.spawned_by_id}
    return TimelineEvent(
        session_id=session.id, project_id=session.project_id, kind=kind, occurred_at=session.created_at,
        precision=TimelinePrecision.DERIVED, actor=TimelineActor.SYSTEM, provider=session.provider,
        data=validate_data(kind, data), dedupe_key=f"{BACKFILL_PREFIX}{kind}",
    )


def write_creation_rows_sync(session_id: str, *, require_computed: bool = False) -> int:
    """Write the ``session.created`` row of ``session_id`` if it is not yet known. Returns the rows inserted.

    Sync: the caller already holds the DB write lock and usually runs its own transaction (the
    compute's final transaction, the DB writer job that marks a session unavailable). The row is
    written in a savepoint: a failure discards only it, is logged, and returns 0. Never raises.

    Nothing is written for a subagent, a missing session, a session without ``created_at``, a
    known session, and, with ``require_computed``, a session whose ``compute_version`` is empty.
    The signal is requested after the caller's transaction commits, only when a row was written.
    """
    try:
        with transaction.atomic():
            # The cheap index query first: almost every call is for a known session.
            if _creation_rows(session_id).exists():
                return 0
            sessions = Session.objects.filter(id=session_id, type=SessionType.SESSION, created_at__isnull=False)
            if require_computed:
                sessions = sessions.filter(compute_version__isnull=False)
            session = sessions.only(*_SESSION_COLUMNS).first()
            if session is None:
                return 0
            count = insert_event_rows([_creation_row(session)])
        if count:
            timeline_signal.poke_on_commit()
        return count
    except Exception:
        logger.exception("Timeline creation row could not be written for session %s", session_id)
        return 0


def write_unavailable_root_rows_sync(session_id: str) -> int:
    """Write the ``session.created`` row of the root of ``session_id``'s tree (C9). Returns the rows inserted.

    Called by the DB writer job that marks a Codex session unavailable: such a session, and the
    root of its tree, never reach the compute's final transaction. The root is ``session_id`` itself
    when it is a root. Only a root with a ``compute_version`` is written: the conservative proxy for
    a stored ``created_at`` read from the file's first line (spec section 5.4). Never raises: a
    missing session or a cyclic tree is logged and returns 0.
    """
    try:
        from twicc.providers.compute_tree import resolve_native_root

        # The whole step runs in a savepoint: a failure anywhere in it discards only this step.
        with transaction.atomic():
            root = resolve_native_root(session_id)
            return write_creation_rows_sync(root, require_computed=True)
    except Exception:
        logger.exception("Timeline creation row could not be written for the root of session %s", session_id)
        return 0
