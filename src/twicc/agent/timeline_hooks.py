# src/twicc/agent/timeline_hooks.py
"""Timeline recording for the agent manager (design §6, §7).

Sync helpers, called from worker threads inside the manager's
``run_under_db_write_lock`` callbacks. They never raise.
"""

from __future__ import annotations

import functools
import logging
from typing import NamedTuple
from datetime import datetime

from django.utils import timezone

from twicc.agent.auto_deny import AUTO_DENY_DELAY_SECONDS
from twicc.agent.states import DELIBERATE_STOP_REASONS, AgentState
from twicc.core.models import TimelineActor, TimelineKind, TimelineEvent
from twicc.agent.completion_types import CompletionSnapshot
from twicc.core.services.session_timeline import latest_event_id, record_event_sync
from twicc.core.timeline import process_transition_events
from twicc.request_origin import DEFAULT_ORIGIN, SYSTEM_ORIGIN, RequestOrigin

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


def _provider_of(agent) -> str | None:
    """The agent's provider as the ``provider`` hint of the recording service (a free source)."""
    provider = getattr(agent, "provider", None)
    return getattr(provider, "value", provider)


def record_logical_turn_boundary(*, agent, snapshot: CompletionSnapshot, kind: str,
                                 occurred_at: datetime, background_work: dict | None) -> int | None:
    """Record a journal-owned boundary under the caller's writer admission."""
    if not _enabled(agent):
        return None
    key = f"turn:{snapshot.turn_id}:{kind}"
    refs = agent._timeline_refs_for_turn(snapshot, start=kind == TimelineKind.TURN_STARTED)
    ending = getattr(agent, "_completion_endings", {}).get(snapshot.turn_id)
    if kind == TimelineKind.TURN_ENDED and ending is not None and snapshot.outcome == "normal":
        refs = ending.payload["boundary_refs"]
    data = {"message_refs": refs, "input_origins": agent._timeline_origins_for_refs(refs) or None}
    if kind == TimelineKind.TURN_ENDED:
        data["background_work"] = background_work
        if ending is not None and snapshot.outcome == "normal":
            data["final_message"] = {"state": "pending",
                "storage_incarnation": ending.scope.storage_incarnation, "history_epoch": ending.scope.history_epoch}
    reason = ((getattr(agent, "kill_reason", None) or "error")
              if kind == TimelineKind.TURN_ENDED and snapshot.outcome in ("ended", "provider_error") else "")
    origin = _end_origin(agent, reason) if reason else None
    event = record_event_sync(kind, session_id=agent.session_id, project_id=agent.project_id,
        occurred_at=occurred_at, actor=TimelineActor.AGENT, origin=origin, provider=_provider_of(agent),
        data=data, reason=reason, dedupe_key=key)
    from twicc.core.services.timeline_decisions import _wake_after_commit
    _wake_after_commit()
    return event.pk if event is not None else TimelineEvent.objects.filter(
        session_id=agent.session_id, dedupe_key=key, kind=kind, occurred_at=occurred_at).values_list("id", flat=True).first()


def _managed_specs(agent, specs):
    # Ownership starts at manager registration, before the first provider fact.
    # Send-time state changes must not create a second, legacy turn boundary.
    if (getattr(agent, "_completion_enabled", False)
            or getattr(agent, "_completion_persist_callback", None) is not None):
        return [spec for spec in specs if spec.kind not in (TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED)]
    return specs


def _first_pending(agent):
    pending = tuple(getattr(agent, "pending_requests", None) or ())
    return pending[0] if pending else None


def _write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT, origin=None, reasons=None) -> None:
    """Write ``specs``; ``reasons`` maps a kind to the ``reason`` of its row."""
    for spec in specs:
        data = spec.data
        if spec.kind in (TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED):
            origins_for = getattr(agent, "_timeline_origins_for_refs", None)
            if origins_for is not None:
                data = {**data, "input_origins": origins_for(data.get("message_refs") or []) or None}
        record_event_sync(
            spec.kind, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
            occurred_at=occurred_at, actor=actor, origin=origin, provider=_provider_of(agent), data=data,
            reason=(reasons or {}).get(spec.kind, ""),
        )


@_never_raises
def record_process_started(agent, *, resume: bool) -> None:
    """Write ``process.started``. Request-caused: the carrier of the awaited request chain, else the default.

    An automatic start (cron restart, boot adoption, pending-settings restart) runs under the system
    marker, so it is recorded as ``system`` with NULL origin fields (design 2026-10-14, section 7).
    """
    if not _enabled(agent):
        return
    run = agent.process_run
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        provider=_provider_of(agent),
        data={"provider": agent.provider.value, "cause": "resume" if resume else "new",
              "twicc_pid": run.twicc_pid if run is not None else None,
              # ``HybridClaudeAgent.is_hybrid`` is a class attribute; other agents lack it.
              "hybrid": bool(getattr(agent, "is_hybrid", False))},
    )


def _end_origin(agent, reason: str) -> RequestOrigin:
    """Origin of ``process.ended`` and of the rows closed with it (design 2026-10-14, section 7).

    A deliberate stop: the origin captured on the agent when the stop was requested, else the default;
    NEVER the carrier (this runs in the agent's own task with the starter's stale context). Anything
    else is automatic: ``system`` with NULL fields.
    """
    if reason in DELIBERATE_STOP_REASONS:
        return getattr(agent, "stop_origin", None) or DEFAULT_ORIGIN
    return SYSTEM_ORIGIN


@_never_raises
def record_process_changes(agent, prev: ProcessSnapshot | None, *, new_state: str, new_awaiting: bool,
                           new_work: dict | None, message_refs: list | None = None) -> None:
    """Events of a non-fatal transition (also used for background-work-only changes)."""
    if prev is None or not _enabled(agent):
        return
    pending = _first_pending(agent)
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=new_state, new_awaiting=new_awaiting, new_work=new_work,
        request_type=getattr(pending, "request_type", None), tool_name=getattr(pending, "tool_name", None),
    )
    specs = _managed_specs(agent, specs)
    specs = [spec._replace(data={**spec.data, "message_refs": message_refs})
             if spec.kind in (TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED) else spec
             for spec in specs]
    # The wait ended because TwiCC denied the request after its delay, not because someone answered.
    auto_denied = any(spec.kind == TimelineKind.AWAITING_INPUT_ENDED for spec in specs) and bool(
        getattr(agent, "auto_denied_request_ids", None),
    )
    if auto_denied:
        agent.auto_denied_request_ids.clear()
        # The delay is stored with the event: a later change of the constant leaves old rows right.
        specs = [
            spec._replace(data={**spec.data, "auto_deny_after": AUTO_DENY_DELAY_SECONDS})
            if spec.kind == TimelineKind.AWAITING_INPUT_ENDED else spec
            for spec in specs
        ]
    _write(
        agent, specs, occurred_at=timezone.now(),
        origin=SYSTEM_ORIGIN if auto_denied else None,
        reasons={TimelineKind.AWAITING_INPUT_ENDED: "auto_denied"} if auto_denied else None,
    )


@_never_raises
def record_process_ended(agent, prev: ProcessSnapshot | None, *, message_refs: list | None = None) -> None:
    """Close the open turn and wait, then write ``process.ended`` (one ``occurred_at``).

    ``provider``, ``cause``, ``twicc_pid`` and ``hybrid`` of the row are inherited
    from ``process.started`` by the recording service.
    """
    if prev is None or prev.state == AgentState.DEAD.value or not _enabled(agent):
        return
    now = timezone.now()
    reason = getattr(agent, "kill_reason", None) or "error"
    ended_origin = _end_origin(agent, reason)
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
    )
    specs = _managed_specs(agent, specs)
    specs = [spec._replace(data={**spec.data, "message_refs": message_refs})
             if spec.kind == TimelineKind.TURN_ENDED else spec for spec in specs]
    # The rows closed with the end take its origin: the stop caused them.
    _write(agent, specs, occurred_at=now, origin=ended_origin)
    # Its workflows die with the CLI process: their transcript will never end them. A run the
    # agent saw end keeps its open row: the watcher has not synced its notification yet.
    from twicc.core.services.timeline_workflows import close_open_workflows
    ended_ids = getattr(agent, "ended_workflow_task_ids", None)
    close_open_workflows(agent.session_id, occurred_at=now, reason=reason, origin=ended_origin,
                         exclude_task_ids=ended_ids() if callable(ended_ids) else frozenset())
    error = getattr(agent, "error", None)
    caused_by = latest_event_id(agent.session_id, TimelineKind.ARCHIVED_STARTED) if reason == "archived" else None
    error_data = {"error": str(error)[:_ERROR_TEXT_LIMIT]} if error else None
    if error_data is not None and getattr(agent, "error_ui_message", None) is not None:
        error_data["error_ui_message"] = agent.error_ui_message
    record_event_sync(
        TimelineKind.PROCESS_ENDED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        occurred_at=now, reason=reason, caused_by=caused_by, origin=ended_origin, provider=_provider_of(agent),
        data=error_data,
    )
