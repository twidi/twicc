"""Boot-time timeline reconciliation and backend instance events (design §7)."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from django.db.models import Max
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.core.models import (
    ProcessRun, Session, SessionItem, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.session_timeline import record_event, record_event_sync
from twicc.core.services.timeline_workflows import close_open_workflows
from twicc.core.timeline import process_transition_events
from twicc.version import get_version

logger = logging.getLogger(__name__)

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_UNSET = object()
BACKFILL_PREFIX = "backfill:"


def read_boot_id() -> str | None:
    """The kernel boot id (Linux), or ``None`` where the platform has none."""
    try:
        return _BOOT_ID_PATH.read_text().strip() or None
    except OSError:
        return None


def _safe_version() -> str | None:
    """The TwiCC version, or ``None`` when it cannot be read (must never break the boot)."""
    try:
        return get_version()
    except Exception:
        logger.warning("Could not read the TwiCC version for the timeline", exc_info=True)
        return None


def _last_evidence(run: ProcessRun, session: Session | None, boot: datetime) -> datetime:
    """Latest sign of life of a lost process, never later than the boot that found it."""
    candidates = [run.last_state_change_at]
    if session is not None and session.last_updated_at:
        candidates.append(session.last_updated_at)
    last_item = SessionItem.objects.filter(session_id=run.session_id).aggregate(last=Max("timestamp"))["last"]
    if last_item is not None:
        candidates.append(last_item)
    return min(max(candidates), boot)


def _reconcile_lost_processes(now: datetime) -> None:
    """Spec §7 step 1. Must run before the bulk DEAD update."""
    for run in ProcessRun.objects.exclude(state=AgentState.DEAD.value).order_by("started_at"):
        session = Session.objects.filter(id=run.session_id).first()
        common = {
            "session_id": run.session_id, "project_id": session.project_id if session else None,
            "occurred_at": _last_evidence(run, session, now),
            "precision": TimelinePrecision.ESTIMATED, "actor": TimelineActor.SYSTEM, "provider": run.provider,
        }
        for spec in process_transition_events(
            prev_state=run.state, prev_awaiting=run.awaiting_user_input,
            prev_work=run.background_work_in_progress,
            new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
        ):
            record_event_sync(spec.kind, data=spec.data, **common)
        # Its workflows died with it (like the inner intervals above).
        close_open_workflows(run.session_id, occurred_at=common["occurred_at"], reason="lost",
                             precision=TimelinePrecision.ESTIMATED)
        record_event_sync(
            TimelineKind.PROCESS_ENDED, reason="lost", data={"upper_bound": now.isoformat()}, **common,
        )


def _close_previous_instance(now: datetime) -> TimelineEvent | None:
    """Spec §7 step 3. Returns the previous ``backend.started`` row, if any.

    Runs after :func:`_reconcile_lost_processes`. "Later" means a higher ``id``, never a
    later ``occurred_at`` (lost-end rows are back-dated). ``now`` is the ``occurred_at``
    that this boot's ``backend.started`` will get: the date of ``backend.ended`` is clamped
    to [previous ``backend.started``, ``now``] so it is never dated before its own start
    (a back-dated lost row written after it) nor after the next instance began (a wrong
    clock after a power loss). If the clock went backwards so that the previous start is
    dated after ``now``, ``now`` wins (a negative duration; the id order stays correct).
    The evidence rows keep their own dates; row order is by id.
    """
    started = TimelineEvent.objects.filter(kind=TimelineKind.BACKEND_STARTED).order_by("-id").first()
    if started is None:
        return None
    if TimelineEvent.objects.filter(kind=TimelineKind.BACKEND_ENDED, id__gt=started.id).exists():
        return started
    # Every row written after ``started`` (by id) counts; in practice those are this boot's
    # step-1 rows (estimated, last evidence of life of processes the previous instance ran,
    # so it was alive then) and live rows it wrote. Only backfilled rows are ignored.
    last_alive = (
        TimelineEvent.objects.filter(id__gt=started.id)
        .exclude(dedupe_key__startswith=BACKFILL_PREFIX)
        .order_by("-occurred_at")
        .first()
    )
    when = last_alive.occurred_at if last_alive else started.occurred_at
    when = min(max(when, started.occurred_at), now)  # lower bound first, then the upper one
    record_event_sync(
        TimelineKind.BACKEND_ENDED, occurred_at=when,
        precision=TimelinePrecision.ESTIMATED, actor=TimelineActor.SYSTEM, data={"clean": False},
    )
    return started


def reconcile_at_boot(*, now: datetime | None = None, boot_id=_UNSET) -> None:
    """Reconcile what the previous instance left behind, then record this boot.

    Sync. Called by the cleanup job in its own transaction, BEFORE the bulk
    update that forces stale ``ProcessRun`` rows to DEAD.
    """
    now = now or timezone.now()
    boot_id = read_boot_id() if boot_id is _UNSET else boot_id
    _reconcile_lost_processes(now)
    previous = _close_previous_instance(now)
    previous_boot_id = previous.data.get("boot_id") if previous else None
    machine_restarted = None if (boot_id is None or previous_boot_id is None) else boot_id != previous_boot_id
    record_event_sync(
        TimelineKind.BACKEND_STARTED, occurred_at=now, actor=TimelineActor.SYSTEM,
        data={"pid": os.getpid(), "version": _safe_version(), "boot_id": boot_id,
              "machine_restarted": machine_restarted},
    )


async def record_backend_ended_clean() -> None:
    """Record the clean end of this instance (call after every manager shut down)."""
    await record_event(TimelineKind.BACKEND_ENDED, actor=TimelineActor.SYSTEM, data={"clean": True})
