# src/twicc/core/timeline.py
"""Payload registry and pure helpers of the session timeline.

Design: ``docs/plans/2026-10-13-session-timeline-design.md`` (§3-§5).
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent.states import AgentState
from twicc.core.models import TimelineKind

#: Version of the payload shapes below. Bump it when a kind's payload changes shape (design §3).
SCHEMA_VERSION = 1

#: Entry surfaces accepted in the reserved ``data["via"]`` key (design §3, ``actor``).
VIA_VALUES = frozenset({"ui", "cli", "mcp"})


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


class TurnEndedData(NamedTuple):
    # The ProcessRun.background_work_in_progress snapshot ({"subagents": n, "shells": n,
    # "monitors": n, "scheduled_wakeup_at": iso | None, "goal": bool}), or None when
    # nothing runs. Non-None = waiting for the user while work still runs behind the agent.
    background_work: dict | None = None


class AwaitingInputStartedData(NamedTuple):
    request_type: str | None = None
    tool_name: str | None = None


class AwaitingInputEndedData(NamedTuple):
    # Repeats AwaitingInputStartedData (inherited, see STARTED_OF).
    request_type: str | None = None
    tool_name: str | None = None


class PinnedStartedData(NamedTuple):
    scope: str  # "project" | "workspace" | "all"


class PinnedEndedData(NamedTuple):
    scope: str | None = None  # inherited from the started row, see STARTED_OF


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


#: ``None`` means "no payload allowed" (only the reserved ``via`` key).
PAYLOAD_TYPES: dict[str, type | None] = {
    TimelineKind.SESSION_CREATED: SessionCreatedData,
    TimelineKind.TITLE_CHANGED: TitleChangedData,
    TimelineKind.SETTINGS_CHANGED: SettingsChangedData,
    TimelineKind.PROCESS_STARTED: ProcessStartedData,
    TimelineKind.PROCESS_ENDED: ProcessEndedData,
    TimelineKind.TURN_STARTED: None,
    TimelineKind.TURN_ENDED: TurnEndedData,
    TimelineKind.AWAITING_INPUT_STARTED: AwaitingInputStartedData,
    TimelineKind.AWAITING_INPUT_ENDED: AwaitingInputEndedData,
    TimelineKind.ARCHIVED_STARTED: None,
    TimelineKind.ARCHIVED_ENDED: None,
    TimelineKind.HIDDEN_STARTED: None,
    TimelineKind.HIDDEN_ENDED: None,
    TimelineKind.MUTED_STARTED: None,
    TimelineKind.MUTED_ENDED: None,
    TimelineKind.PINNED_STARTED: PinnedStartedData,
    TimelineKind.PINNED_ENDED: PinnedEndedData,
    TimelineKind.BACKEND_STARTED: BackendStartedData,
    TimelineKind.BACKEND_ENDED: BackendEndedData,
}

#: ``ended`` kind -> its ``started`` kind. An ``ended`` row repeats the data of its
#: OPEN ``started`` row (no newer ``ended`` row) so that each line reads alone
#: (design §3). The recording service applies it in one place (``record_event_sync``); ``turn.*``, ``archived.*``,
#: ``hidden.*`` and ``muted.*`` have no started payload, so they are not listed.
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
    ``None``) nothing is copied. The reserved ``via`` key
    is never copied. Pure: no validation, see :func:`validate_data`.
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

    Raises ``ValueError`` on an unknown kind, an unknown or missing key, or a
    bad ``via``. The reserved ``via`` key is accepted on every kind.
    """
    try:
        payload_type = PAYLOAD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown timeline kind {kind!r}") from None
    data = dict(data or {})
    via = data.pop("via", None)
    if via is not None and via not in VIA_VALUES:
        raise ValueError(f"invalid via {via!r}")
    if payload_type is None:
        if data:
            raise ValueError(f"{kind} takes no payload, got {sorted(data)}")
        result = {}
    else:
        unknown = set(data) - set(payload_type._fields)
        if unknown:
            raise ValueError(f"{kind}: unknown payload keys {sorted(unknown)}")
        try:
            result = payload_type(**data)._asdict()
        except TypeError as exc:
            raise ValueError(f"{kind}: {exc}") from exc
    if via is not None:
        result["via"] = via
    return result
