"""Timeline rows of Claude Code ``Workflow`` runs: ``workflow.started`` / ``workflow.ended``.

One row pair per attempt (a ``task_id``). A resume (``resumeFromRunId``) is a new attempt of the
same run (same ``run_id``). Attempts of one session can overlap, so an end pairs with its start by
``task_id`` (``data__task_id``), never through ``STARTED_OF``.

Writers:

- :func:`record_workflow_signals`: the live sync, from the session's transcript. New starts only,
  like the message snapshots: a launch dated before the watcher started is history, never recorded.
  An end is written only for an attempt the timeline saw start, which also keeps agent
  notifications out (they end the same way).
- :func:`close_open_workflows`: the agent manager when a process ends, and the boot reconciliation
  for a process found dead. The attempts still open then died with their process: their transcript
  will never say so. The watcher reads the transcript late, so the two orders are handled:
  the manager closes every open run but those its agent saw end (they keep their row open for their
  notification), and a launch synced after its process ended is closed at once with that end.

Limit: an attempt of a session TwiCC does not drive (a terminal ``claude``) is closed only by its
own notification. If that CLI dies mid-run, the attempt stays open (a later resume ends it ``paused``).

Every write goes through ``record_event_sync``: a failure is logged and never raised.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

from twicc.core.models import Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.services.session_timeline import record_event_sync

logger = logging.getLogger(__name__)

#: The status of an attempt resumed while it had no end of its own: the CLI refuses to resume a
#: running run, and a terminal one writes its notification, so it was paused.
PAUSED_STATUS = "paused"
#: The status of an attempt closed by the end of its process (the row's ``reason`` says why).
INTERRUPTED_STATUS = "interrupted"


def _started_key(task_id: str) -> str:
    return f"workflow:{task_id}:started"


def _ended_key(task_id: str) -> str:
    return f"workflow:{task_id}:ended"


def open_workflow_attempts(session_id: str) -> list[TimelineEvent]:
    """The ``workflow.started`` rows of the session with no ``workflow.ended`` of the same ``task_id``."""
    ended = set(
        TimelineEvent.objects.filter(session_id=session_id, kind=TimelineKind.WORKFLOW_ENDED)
        .values_list("data__task_id", flat=True)
    )
    return [
        row for row in TimelineEvent.objects.filter(session_id=session_id, kind=TimelineKind.WORKFLOW_STARTED)
        .order_by("id")
        if row.data.get("task_id") not in ended
    ]


def _write_end(started: TimelineEvent, *, status: str | None, occurred_at, precision=TimelinePrecision.EXACT,
               reason: str = "", actor=TimelineActor.SYSTEM, origin=None) -> TimelineEvent | None:
    data = started.data
    return record_event_sync(
        TimelineKind.WORKFLOW_ENDED, session_id=started.session_id, project_id=started.project_id,
        provider=started.provider, occurred_at=occurred_at, precision=precision, reason=reason,
        actor=actor, origin=origin,
        data={"run_id": data.get("run_id"), "task_id": data.get("task_id"), "name": data.get("name"),
              "status": status},
        dedupe_key=_ended_key(data.get("task_id")),
    )


def record_workflow_signals(session: Session, signals: Iterable, *, timeline_since: datetime | None) -> None:
    """Write the workflow rows of freshly indexed transcript lines (``WorkflowTimelineSignal``)."""
    if timeline_since is None or session.type != SessionType.SESSION:
        return
    signals = sorted(signals, key=lambda signal: signal.line_num)
    # A run that started and ended within the sync delay: its own end, read in the same batch, wins.
    ended_in_batch = {signal.task_id for signal in signals if signal.kind == "ended"}
    for signal in signals:
        # A start is new only when dated after the watcher started. An end needs no such proof:
        # it is written only for a start the timeline recorded.
        if signal.kind == "started" and (signal.timestamp is None or signal.timestamp < timeline_since):
            continue
        try:
            if signal.kind == "started":
                _record_started(session, signal, ends_in_batch=signal.task_id in ended_in_batch)
            elif signal.kind == "ended":
                _record_ended(session, signal)
        except Exception:
            logger.exception("Workflow timeline signal failed for session %s", session.id)


def _record_started(session: Session, signal, *, ends_in_batch: bool = False) -> None:
    if signal.resumed:
        # The attempt this one resumes never wrote its end: it was paused.
        for started in open_workflow_attempts(session.id):
            if started.data.get("run_id") == signal.run_id and started.data.get("task_id") != signal.task_id:
                _write_end(started, status=PAUSED_STATUS, occurred_at=signal.timestamp,
                           precision=TimelinePrecision.ESTIMATED)
    started = record_event_sync(
        TimelineKind.WORKFLOW_STARTED, session_id=session.id, project_id=session.project_id,
        provider=session.provider, occurred_at=signal.timestamp, actor=TimelineActor.AGENT,
        data={"run_id": signal.run_id, "task_id": signal.task_id, "name": signal.name,
              "cause": "resume" if signal.resumed else "new"},
        dedupe_key=_started_key(signal.task_id),
    )
    if started is None or ends_in_batch:
        return
    # The launch was synced after its process ended (a stop or a restart right after the call):
    # that end already closed the open attempts, without this one. The first end dated at or after
    # the launch is its process's: a later process may already run.
    process_end = (
        TimelineEvent.objects.filter(session_id=session.id, kind=TimelineKind.PROCESS_ENDED,
                                     occurred_at__gte=signal.timestamp)
        .order_by("occurred_at", "id").first()
    )
    if process_end is not None:
        _write_end(started, status=INTERRUPTED_STATUS, occurred_at=process_end.occurred_at,
                   precision=process_end.precision, reason=process_end.reason)


def _record_ended(session: Session, signal) -> None:
    started = (
        TimelineEvent.objects.filter(session_id=session.id, kind=TimelineKind.WORKFLOW_STARTED,
                                     dedupe_key=_started_key(signal.task_id))
        .first()
    )
    if started is None:
        return  # not a workflow, or an attempt started before the timeline watched
    # An old payload-only completion has no status: it is a completion.
    _write_end(started, status=signal.status or "completed", occurred_at=signal.timestamp)


def close_open_workflows(session_id: str, *, occurred_at, reason: str, precision=TimelinePrecision.EXACT,
                         origin=None, exclude_task_ids: frozenset[str] = frozenset()) -> None:
    """End the attempts of the session still open: their process ended (``reason``) and took them along.

    ``exclude_task_ids``: the runs the agent saw end (``BaseAgent.ended_workflow_task_ids``); their own
    notification ends them. Every other open attempt is closed.
    """
    try:
        for started in open_workflow_attempts(session_id):
            if started.data.get("task_id") in exclude_task_ids:
                continue
            _write_end(started, status=INTERRUPTED_STATUS, occurred_at=occurred_at, precision=precision,
                       reason=reason, origin=origin)
    except Exception:
        logger.exception("Could not close the open workflows of session %s", session_id)
