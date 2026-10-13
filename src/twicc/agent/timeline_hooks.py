# src/twicc/agent/timeline_hooks.py
"""Timeline recording for the agent manager (design §6, §7).

Sync helpers, called from worker threads inside the manager's
``run_under_db_write_lock`` callbacks. They never raise.
"""

from __future__ import annotations

import functools
import logging
from typing import NamedTuple

from django.utils import timezone

from twicc.agent.states import DELIBERATE_STOP_REASONS, AgentState
from twicc.core.models import TimelineActor, TimelineKind
from twicc.core.services.session_timeline import latest_event_id, record_event_sync
from twicc.core.timeline import process_transition_events

logger = logging.getLogger(__name__)

_ERROR_TEXT_LIMIT = 500


def _never_raises(function):
    """Make a hook harmless: a failure is logged and never reaches the manager.

    ``record_event_sync`` already swallows write errors, but the hooks also read
    the agent (``project_id``, ``pending_requests``, ``process_run``). An error
    there must not skip the ``ProcessRun`` update or delete that follows.
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            logger.exception("Timeline hook %s failed", function.__name__)

    return wrapper


class ProcessSnapshot(NamedTuple):
    """The three ProcessRun values the timeline diffs between transitions."""

    state: str
    awaiting: bool
    work: dict | None


def snapshot_of(process_run) -> ProcessSnapshot | None:
    """The diffed values of a ``ProcessRun`` row, or ``None`` when the agent has no row."""
    if process_run is None:
        return None
    return ProcessSnapshot(
        process_run.state, bool(process_run.awaiting_user_input), process_run.background_work_in_progress,
    )


def _enabled(agent) -> bool:
    return not getattr(agent, "ephemeral", False)


def _first_pending(agent):
    pending = tuple(getattr(agent, "pending_requests", None) or ())
    return pending[0] if pending else None


def _write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT) -> None:
    for spec in specs:
        record_event_sync(
            spec.kind, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
            occurred_at=occurred_at, actor=actor, data=spec.data,
        )


@_never_raises
def record_process_started(agent, *, resume: bool) -> None:
    if not _enabled(agent):
        return
    run = agent.process_run
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        actor=TimelineActor.USER,
        data={"provider": agent.provider.value, "cause": "resume" if resume else "new",
              "twicc_pid": run.twicc_pid if run is not None else None,
              # ``HybridClaudeAgent.is_hybrid`` is a class attribute; other agents lack it.
              "hybrid": bool(getattr(agent, "is_hybrid", False))},
    )


@_never_raises
def record_process_changes(agent, prev: ProcessSnapshot | None, *, new_state: str, new_awaiting: bool,
                           new_work: dict | None) -> None:
    """Events of a non-fatal transition (also used for background-work-only changes)."""
    if prev is None or not _enabled(agent):
        return
    pending = _first_pending(agent)
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=new_state, new_awaiting=new_awaiting, new_work=new_work,
        request_type=getattr(pending, "request_type", None), tool_name=getattr(pending, "tool_name", None),
    )
    _write(agent, specs, occurred_at=timezone.now())


@_never_raises
def record_process_ended(agent, prev: ProcessSnapshot | None) -> None:
    """Close the open turn and wait, then write ``process.ended`` (one ``occurred_at``).

    ``provider``, ``cause``, ``twicc_pid`` and ``hybrid`` of the row are inherited
    from ``process.started`` by the recording service.
    """
    if prev is None or not _enabled(agent):
        return
    now = timezone.now()
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
    )
    _write(agent, specs, occurred_at=now)
    reason = getattr(agent, "kill_reason", None) or "error"
    error = getattr(agent, "error", None)
    caused_by = latest_event_id(agent.session_id, TimelineKind.ARCHIVED_STARTED) if reason == "archived" else None
    record_event_sync(
        TimelineKind.PROCESS_ENDED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        occurred_at=now, reason=reason, caused_by=caused_by,
        actor=TimelineActor.USER if reason in DELIBERATE_STOP_REASONS else TimelineActor.SYSTEM,
        data={"error": str(error)[:_ERROR_TEXT_LIMIT]} if error else None,
    )
