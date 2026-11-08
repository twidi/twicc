"""Payload registry and pure helpers of the session timeline.

Design: ``docs/plans/2026-10-13-session-timeline-design.md`` (§3-§5).
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent.states import AgentState
from twicc.core.models import TimelineKind

#: Version of the payload shapes below. Bump it when a kind's payload changes shape (design §3).
SCHEMA_VERSION = 4


class SessionCreatedData(NamedTuple):
    origin: str  # "twicc" | "external" | "sync"
    provider: str | None = None
    settings: dict | None = None  # non-null AgentSettings fields of the created row; origin "twicc" only
    spawned_by: str | None = None  # Session.spawned_by_id


class TitleChangedData(NamedTuple):
    source: str  # "auto" | "user" | "agent"
    new: str
    old: str | None = None  # previous title; None when there was none or it is unknown


class SettingsChangedData(NamedTuple):
    changes: dict  # {field: [old, new]}


class ProcessStartedData(NamedTuple):
    provider: str
    cause: str = "new"  # "new" | "resume"
    twicc_pid: int | None = None
    hybrid: bool = False  # the agent runs in hybrid CLI mode


class ProcessEndedData(NamedTuple):
    # The first four fields repeat ProcessStartedData (inherited by the recording
    # service, see STARTED_OF). All optional: the started row may predate the rollout.
    provider: str | None = None
    cause: str | None = None
    twicc_pid: int | None = None
    hybrid: bool | None = None
    error: str | None = None
    upper_bound: str | None = None  # ISO time of the boot that detected a lost process


class MessageSentData(NamedTuple):
    text: str
    source_refs: list
    source_line: int
    storage_incarnation: str | None = None
    history_epoch: int | None = None


class TurnStartedData(NamedTuple):
    message_refs: list | None = None
    input_origins: list | None = None


class TurnEndedData(NamedTuple):
    # The ProcessRun.background_work_in_progress snapshot ({"subagents": n, "shells": n,
    # "monitors": n, "workflows": n, "scheduled_wakeup_at": iso | None, "goal": bool}), or None when
    # nothing runs. Non-None = waiting for the user while work still runs behind the agent.
    background_work: dict | None = None
    message_refs: list | None = None
    input_origins: list | None = None


class AwaitingInputStartedData(NamedTuple):
    request_type: str | None = None
    tool_name: str | None = None


class AwaitingInputEndedData(NamedTuple):
    # The first two fields repeat AwaitingInputStartedData (inherited, see STARTED_OF).
    request_type: str | None = None
    tool_name: str | None = None
    # Set with reason "auto_denied": the delay (seconds) TwiCC waited before denying the request.
    auto_deny_after: int | None = None


class PinnedStartedData(NamedTuple):
    scope: str  # "project" | "workspace" | "all"


class PinnedEndedData(NamedTuple):
    scope: str | None = None  # inherited from the started row, see STARTED_OF


class WorkflowStartedData(NamedTuple):
    # One attempt of a Claude Code ``Workflow`` run, read from the session's transcript
    # (``timeline_workflows``). A resume (``resumeFromRunId``) is a new attempt: same
    # ``run_id``, new ``task_id``. Attempts of one session can overlap, so ``workflow.ended``
    # pairs with its start by ``task_id`` and is NOT in ``STARTED_OF``.
    run_id: str
    task_id: str
    name: str | None = None  # the script's meta.name
    cause: str = "new"  # "new" | "resume"


class WorkflowEndedData(NamedTuple):
    # The first three fields repeat the matching ``workflow.started`` (copied by the writer).
    run_id: str | None = None
    task_id: str | None = None
    name: str | None = None
    # The CLI's terminal status ("completed", "failed", "killed", "stopped", ...), or one TwiCC
    # infers: "paused" (resumed later without an end of its own), "interrupted" (its process
    # ended; the row's ``reason`` says why, "lost" when found dead at boot).
    status: str | None = None


class BackendStartedData(NamedTuple):
    pid: int
    version: str | None = None
    boot_id: str | None = None
    machine_restarted: bool | None = None  # None = unknown


class BackendEndedData(NamedTuple):
    clean: bool
    # Repeats BackendStartedData (inherited, see STARTED_OF); all optional.
    pid: int | None = None
    version: str | None = None
    boot_id: str | None = None
    machine_restarted: bool | None = None


#: ``None`` means "no payload allowed".
PAYLOAD_TYPES: dict[str, type | None] = {
    TimelineKind.SESSION_CREATED: SessionCreatedData,
    TimelineKind.TITLE_CHANGED: TitleChangedData,
    TimelineKind.SETTINGS_CHANGED: SettingsChangedData,
    TimelineKind.PROCESS_STARTED: ProcessStartedData,
    TimelineKind.PROCESS_ENDED: ProcessEndedData,
    TimelineKind.USER_MESSAGE_SENT: MessageSentData,
    TimelineKind.ASSISTANT_MESSAGE_SENT: MessageSentData,
    TimelineKind.TURN_STARTED: TurnStartedData,
    TimelineKind.TURN_ENDED: TurnEndedData,
    TimelineKind.AWAITING_INPUT_STARTED: AwaitingInputStartedData,
    TimelineKind.AWAITING_INPUT_ENDED: AwaitingInputEndedData,
    TimelineKind.ARCHIVED_STARTED: None,
    TimelineKind.ARCHIVED_ENDED: None,
    TimelineKind.HIDDEN_STARTED: None,
    TimelineKind.HIDDEN_ENDED: None,
    TimelineKind.MUTED_STARTED: None,
    TimelineKind.MUTED_ENDED: None,
    TimelineKind.TREE_DETACHED_STARTED: None,
    TimelineKind.TREE_DETACHED_ENDED: None,
    TimelineKind.PINNED_STARTED: PinnedStartedData,
    TimelineKind.PINNED_ENDED: PinnedEndedData,
    TimelineKind.WORKFLOW_STARTED: WorkflowStartedData,
    TimelineKind.WORKFLOW_ENDED: WorkflowEndedData,
    TimelineKind.BACKEND_STARTED: BackendStartedData,
    TimelineKind.BACKEND_ENDED: BackendEndedData,
}

#: ``ended`` kind -> its ``started`` kind. An ``ended`` row repeats the data of its
#: OPEN ``started`` row (no newer ``ended`` row) so that each line reads alone
#: (design §3). The recording service applies it in one place (``record_event_sync``); ``turn.*``, ``archived.*``,
#: ``hidden.*``, ``muted.*`` and ``tree_detached.*`` have no started payload, so they are not listed.
STARTED_OF: dict[str, str] = {
    TimelineKind.AWAITING_INPUT_ENDED: TimelineKind.AWAITING_INPUT_STARTED,
    TimelineKind.PROCESS_ENDED: TimelineKind.PROCESS_STARTED,
    TimelineKind.PINNED_ENDED: TimelineKind.PINNED_STARTED,
    TimelineKind.BACKEND_ENDED: TimelineKind.BACKEND_STARTED,
}


def inherit_started_fields(kind: str, data: dict | None, started_data: dict | None) -> dict:
    """Return ``data`` of the ``ended`` kind ``kind`` completed from ``started_data``.

    Every payload field of ``kind`` that the caller did not provide is copied from
    ``started_data`` (the data of the OPEN ``started`` row: the caller found no
    newer ``ended`` row), when that row has it. A caller-provided field wins, even
    when it is ``None``. Without an open ``started`` row (``started_data`` is
    ``None``) nothing is copied. Pure: no validation, see :func:`validate_data`.
    """
    merged = dict(data or {})
    if not started_data:
        return merged
    for name in PAYLOAD_TYPES[kind]._fields:
        if name not in merged and name in started_data:
            merged[name] = started_data[name]
    return merged


def validate_data(kind: str, data: dict | None) -> dict:
    """Return the validated payload of ``kind``, defaults filled in.

    Raises ``ValueError`` on an unknown kind or an unknown or missing key. The origin of an event is
    not part of the payload (it lives in the ``via`` / ``actor_ref`` / ``actor_name`` columns), so a
    ``via`` key is an unknown key like any other.
    """
    try:
        payload_type = PAYLOAD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown timeline kind {kind!r}") from None
    data = dict(data or {})
    if payload_type is None:
        if data:
            raise ValueError(f"{kind} takes no payload, got {sorted(data)}")
        return {}
    unknown = set(data) - set(payload_type._fields)
    if unknown:
        raise ValueError(f"{kind}: unknown payload keys {sorted(unknown)}")
    try:
        return payload_type(**data)._asdict()
    except TypeError as exc:
        raise ValueError(f"{kind}: {exc}") from exc


class EventSpec(NamedTuple):
    """One event to record: the kind and its payload (the caller adds the rest)."""

    kind: str
    data: dict


def process_transition_events(
    *,
    prev_state: str,
    prev_awaiting: bool,
    prev_work: dict | None,
    new_state: str,
    new_awaiting: bool,
    new_work: dict | None,
    request_type: str | None = None,
    tool_name: str | None = None,
) -> list[EventSpec]:
    """Events of one ``ProcessRun`` change, ends first, then starts.

    Pure function of the previous and the new row values (design §6). The
    background work is not an interval: ``turn.ended`` carries the snapshot at
    the logical turn boundary. Later metadata changes create no boundary.
    On a transition to ``dead`` the caller passes
    ``new_awaiting=False`` and ``new_work=None`` (the row forces both), so every
    open inner interval closes; the caller then appends ``process.ended``.
    """
    assistant = AgentState.ASSISTANT_TURN.value
    dead = AgentState.DEAD.value
    ends: list[EventSpec] = []
    starts: list[EventSpec] = []

    if prev_state == assistant and new_state != assistant:
        # A dead process runs nothing, whatever the caller passed.
        work = None if new_state == dead else new_work
        ends.append(EventSpec(TimelineKind.TURN_ENDED, {"background_work": work}))
    elif prev_state != assistant and new_state == assistant:
        starts.append(EventSpec(TimelineKind.TURN_STARTED, {}))

    if prev_awaiting and not new_awaiting:
        ends.append(EventSpec(TimelineKind.AWAITING_INPUT_ENDED, {}))
    elif new_awaiting and not prev_awaiting:
        starts.append(EventSpec(
            TimelineKind.AWAITING_INPUT_STARTED, {"request_type": request_type, "tool_name": tool_name},
        ))

    return ends + starts
