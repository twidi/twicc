"""Codex agent-run parsing and file-local run attribution (design §5.6, §6.2).

Two groups of pure functions, with no Django import:

- **Line parsers** for the Codex rollout events the run tracking reads:
  ``SubAgentActivity`` items (all four kinds), the owner turn aborts, the
  turn boundaries, the line-1 ``session_meta`` fork fields and the
  top-level ``ordinal``.
- **Attribution rules** over a :class:`FileEvidence` value: one owner
  rollout, one agent, every row of that file. Each function only reads the
  evidence whose line is before its reference line ``L``, so the batch pass
  (which builds the value from its in-pass dicts) and the live pass (which
  builds it from the DB rows) take the same decisions: the rules depend on
  line order inside one rollout only.

The attribution never reads the root cutoff, other files' evidence or the
tree rule: those feed the running state (§5.4), not the choice of the call
an end signal belongs to.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from twicc.providers.codex.canonical import completed_item

if TYPE_CHECKING:
    from twicc.providers.compute_base import BatchAgentState

# Qualified name (``collaboration__<name>``) of a Codex control-tool call →
# the ``AgentInteraction.kind`` its ``interacted`` / ``interrupted`` event
# writes. Any other qualified name (v1 ``send_input`` / ``resume_agent`` /
# ``close_agent``, ``collaboration__wait_agent``, …) writes no row (§6.2).
INTERACTION_KIND_BY_TOOL = {
    "collaboration__followup_task": "resume",
    "collaboration__send_message": "message",
    "collaboration__interrupt_agent": "stop",
}

_SUB_AGENT_ACTIVITY_TYPE = "SubAgentActivity"
_SUB_AGENT_ACTIVITY_KINDS = frozenset({"started", "interacted", "interrupted", "completed"})

_TYPE_EVENT_MSG = "event_msg"
_TYPE_SESSION_META = "session_meta"
_PAYLOAD_TURN_ABORTED = "turn_aborted"
_PAYLOAD_TASK_STARTED = "task_started"
_PAYLOAD_TASK_COMPLETE = "task_complete"
# The only ``turn_aborted`` reason that cuts the owner's running children
# (the only one in the data, §5.2).
_ABORT_REASON_INTERRUPTED = "interrupted"
# The only ``task_complete.error.codex_error_info`` observed with running
# children; ``server_overloaded`` / ``cyber_policy`` / ``other`` do not cut
# (§5.2, §10).
_ERROR_INFO_USAGE_LIMIT = "usage_limit_exceeded"

RUN_KIND_SPAWN = "spawn"
RUN_KIND_RESUME = "resume"

# ``AgentInteraction.kind`` values the evidence reads (the model's
# ``AgentInteractionKind``; this module stays Django-free).
_INTERACTION_KIND_RESUME = "resume"
_INTERACTION_KIND_STOP = "stop"

# ``AgentRunEnd.status`` values of the Codex rows (§5.2).
END_STATUS_COMPLETED = "completed"
END_STATUS_OWNER_TURN_ABORTED = "owner_turn_aborted"
END_STATUS_TURN_COMPLETE = "turn_complete"


class SubAgentActivity(NamedTuple):
    """One decoded ``SubAgentActivity`` item of a caller's rollout.

    - ``kind``: ``started`` | ``interacted`` | ``interrupted`` | ``completed``.
    - ``event_id``: the originating call id for the first three kinds;
      ``subagent-completed-<child turn id>`` for ``completed`` (never parsed).
    - ``agent_id``: the subagent's thread id (``agent_thread_id``).
    - ``agent_path``: the collaboration handle (``/root/<task>``).
    """

    kind: str
    event_id: str
    agent_id: str
    agent_path: str


class ForkFields(NamedTuple):
    """The line-1 ``session_meta`` fields of the copied-history gate (§5.2)."""

    forked_from_id: str | None
    history_start_ordinal: int | None


class FileRun(NamedTuple):
    """One run of the agent owned by the file: a spawn, or a run-opening resume.

    - ``call_line``: ``AgentLink.tool_use_line_num`` (spawn) or
      ``AgentInteraction.tool_use_line_num`` (resume); orders the runs.
    - ``event_line``: the resume's ``interacted`` line
      (``AgentInteraction.event_line_num``); ``None`` for a spawn.
    - ``kind``: :data:`RUN_KIND_SPAWN` or :data:`RUN_KIND_RESUME`.
    """

    tool_use_id: str
    call_line: int
    event_line: int | None
    kind: str


class FileEvidence(NamedTuple):
    """Every attribution input of one owner file for one agent, at all lines.

    The functions below filter each piece by its line, so a value holding
    rows after the reference line gives the same answer as one that stops
    before it.

    - ``runs``: the spawns and the ``resume`` interactions with
      ``opens_run = true``.
    - ``stops``: ``(event_line_num, first non-error result line or None)``
      of each ``stop`` interaction of the file.
    - ``completed_lines``: call id → lines of its ``completed`` rows.
    - ``aborted_lines``: call id → lines of its ``owner_turn_aborted`` rows.
    - ``results``: call id → ``(line, tool_result_at)`` of its result rows.
    """

    runs: tuple[FileRun, ...]
    stops: tuple[tuple[int, int | None], ...]
    completed_lines: Mapping[str, tuple[int, ...]]
    aborted_lines: Mapping[str, tuple[int, ...]]
    results: Mapping[str, tuple[tuple[int, datetime | None], ...]]


# ---------------------------------------------------------------------------
# Line parsers
# ---------------------------------------------------------------------------


def _non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _event_payload(parsed: dict, payload_type: str) -> dict | None:
    if parsed.get("type") != _TYPE_EVENT_MSG:
        return None
    payload = parsed.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != payload_type:
        return None
    return payload


def parse_sub_agent_activity(parsed: dict) -> SubAgentActivity | None:
    """Decode a ``SubAgentActivity`` item of any of its four kinds.

    The spawn-only ``_parse_sub_agent_activity_started`` in
    ``codex/compute.py`` delegates here and keeps only its ``started``
    filter: it gates ``is_tool_result_item`` and must never accept
    ``completed``.
    Returns ``None`` for any other line shape, kind or malformed payload.
    """
    item = completed_item(parsed)
    if item is None or item.get("type") != _SUB_AGENT_ACTIVITY_TYPE:
        return None
    kind = item.get("kind")
    if kind not in _SUB_AGENT_ACTIVITY_KINDS:
        return None
    event_id = _non_empty_str(item.get("id"))
    agent_id = _non_empty_str(item.get("agent_thread_id"))
    agent_path = _non_empty_str(item.get("agent_path"))
    if event_id is None or agent_id is None or agent_path is None:
        return None
    return SubAgentActivity(kind, event_id, agent_id, agent_path)


def owner_turn_abort_turn_id(parsed: dict) -> str | None:
    """Return the ``turn_id`` of a turn end that cuts the owner's running children.

    Two shapes (§5.2): ``turn_aborted`` with ``reason == "interrupted"``, and
    ``task_complete`` whose ``error.codex_error_info`` is the string
    ``usage_limit_exceeded``. Any other line, reason or error kind → ``None``.
    """
    payload = _event_payload(parsed, _PAYLOAD_TURN_ABORTED)
    if payload is not None:
        if payload.get("reason") != _ABORT_REASON_INTERRUPTED:
            return None
        return _non_empty_str(payload.get("turn_id"))
    payload = _event_payload(parsed, _PAYLOAD_TASK_COMPLETE)
    if payload is None:
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("codex_error_info") != _ERROR_INFO_USAGE_LIMIT:
        return None
    return _non_empty_str(payload.get("turn_id"))


def task_started_turn_id(parsed: dict) -> str | None:
    """Return the ``turn_id`` of a ``task_started`` event, else ``None``."""
    payload = _event_payload(parsed, _PAYLOAD_TASK_STARTED)
    if payload is None:
        return None
    return _non_empty_str(payload.get("turn_id"))


def is_task_complete(parsed: dict) -> bool:
    """True for a ``task_complete`` event (any outcome: it ends the turn)."""
    return _event_payload(parsed, _PAYLOAD_TASK_COMPLETE) is not None


def fork_fields(parsed_line_1: dict) -> ForkFields:
    """Read ``forked_from_id`` / ``subagent_history_start_ordinal`` from ``session_meta``.

    Any other line (or a missing / malformed field) gives ``None`` for that field.
    """
    if parsed_line_1.get("type") != _TYPE_SESSION_META:
        return ForkFields(None, None)
    payload = parsed_line_1.get("payload")
    if not isinstance(payload, dict):
        return ForkFields(None, None)
    return ForkFields(
        _non_empty_str(payload.get("forked_from_id")),
        _strict_int(payload.get("subagent_history_start_ordinal")),
    )


def line_ordinal(parsed: dict) -> int | None:
    """Return the line's top-level ``ordinal``, or ``None`` when absent / not an int."""
    return _strict_int(parsed.get("ordinal"))


# ---------------------------------------------------------------------------
# Attribution rules (§5.6)
# ---------------------------------------------------------------------------


def _opening_line(run: FileRun) -> int:
    """The line a run enters the candidate set: spawn call line, resume event line."""
    return run.call_line if run.event_line is None else run.event_line


def _has_completed(ev: FileEvidence, run: FileRun, before_line: int) -> bool:
    return any(line < before_line for line in ev.completed_lines.get(run.tool_use_id, ()))


def _has_final_answer(ev: FileEvidence, run: FileRun, before_line: int) -> bool:
    """Two distinct ``tool_result_at`` among the result lines before ``before_line``.

    The ack counts one; a ``FINAL_ANSWER`` sharing its timestamp adds nothing
    (the distinct-time count of rule 1, §5.4). A ``None`` time is not
    counted, like SQL ``COUNT(DISTINCT ...)`` skips ``NULL`` (rule 1 also
    states that no agent result row has a null ``tool_result_at``).
    """
    times = {
        at for line, at in ev.results.get(run.tool_use_id, ())
        if line < before_line and at is not None
    }
    return len(times) >= 2


def _candidates(ev: FileEvidence, before_line: int, cache: dict[int, list[FileRun]]) -> list[FileRun]:
    cached = cache.get(before_line)
    if cached is not None:
        return cached
    entered = sorted(
        (
            run for run in ev.runs
            if _opening_line(run) < before_line
            and not any(line < before_line for line in ev.aborted_lines.get(run.tool_use_id, ()))
        ),
        key=lambda run: run.call_line,
    )
    stopped: set[FileRun] = set()
    for event_line, result_line in ev.stops:
        if event_line < before_line and result_line is not None and result_line < before_line:
            # Recursive on a strictly smaller line: terminates.
            stopped.update(_file_open_runs(ev, event_line, cache))
    result = [run for run in entered if run not in stopped]
    cache[before_line] = result
    return result


def _file_open_runs(ev: FileEvidence, before_line: int, cache: dict[int, list[FileRun]]) -> list[FileRun]:
    return [
        run for run in _candidates(ev, before_line, cache)
        if not _has_completed(ev, run, before_line) and not _has_final_answer(ev, run, before_line)
    ]


def candidates(ev: FileEvidence, before_line: int) -> list[FileRun]:
    """The runs an end signal at ``before_line`` may belong to, ordered by call line.

    Spawns whose call line and run-opening resumes whose ``interacted`` line
    are before the reference line, minus the runs cut by an owner turn abort
    before it, and minus the runs **stopped in line order**: for each stop
    whose event line and non-error result line are both before the reference
    line, the runs file-open at the stop's event line. A run that already had
    its ``completed`` at the stop's line is not file-open there, so it stays a
    candidate for its own ``FINAL_ANSWER``.
    """
    return _candidates(ev, before_line, {})


def file_open_runs(ev: FileEvidence, before_line: int) -> list[FileRun]:
    """The candidates with neither ``completed`` nor ``FINAL_ANSWER`` before the line."""
    return _file_open_runs(ev, before_line, {})


def attribute_completed(ev: FileEvidence, line: int) -> FileRun | None:
    """The run a ``completed`` event at ``line`` ends.

    Oldest candidate with neither signal; else oldest with a ``FINAL_ANSWER``
    but no ``completed``; else the newest candidate (extra signal). With no
    candidate at all, the newest spawn of the file whose call line is before
    ``line``; ``None`` (no row) when the file has none. The design says "the
    newest spawn in the file"; the ``call_line < line`` bound keeps the rule
    line-ordered, so live (which has no later rows yet) and batch agree. It
    changes nothing in practice: no signal of an agent comes before its
    spawn's ``started`` event, and this branch was never hit in the replay.
    """
    runs = candidates(ev, line)
    for run in runs:
        if not _has_completed(ev, run, line) and not _has_final_answer(ev, run, line):
            return run
    for run in runs:
        if _has_final_answer(ev, run, line) and not _has_completed(ev, run, line):
            return run
    if runs:
        return runs[-1]
    spawns = [run for run in ev.runs if run.kind == RUN_KIND_SPAWN and run.call_line < line]
    return max(spawns, key=lambda run: run.call_line) if spawns else None


def attribute_final_answer(ev: FileEvidence, line: int) -> FileRun | None:
    """The run a ``FINAL_ANSWER`` at ``line`` ends.

    Oldest candidate with ``completed`` but no ``FINAL_ANSWER``; else oldest
    with neither; else the newest candidate (extra signal). ``None`` when there
    is no candidate: the caller falls back to the newest spawn for the sender
    path.
    """
    runs = candidates(ev, line)
    for run in runs:
        if _has_completed(ev, run, line) and not _has_final_answer(ev, run, line):
            return run
    for run in runs:
        if not _has_completed(ev, run, line) and not _has_final_answer(ev, run, line):
            return run
    return runs[-1] if runs else None


# ---------------------------------------------------------------------------
# Batch evidence (§6.2)
# ---------------------------------------------------------------------------


def _iso_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def agents_with_file_runs(batch_state: BatchAgentState, session_id: str) -> set[str]:
    """The agents with at least one run owned by ``session_id``: a spawn or a run-opening resume."""
    agents = {link["agent_id"] for link in batch_state.all_agent_links.values() if link["session_id"] == session_id}
    agents.update(
        row["agent_id"] for row in batch_state.all_agent_interactions.values()
        if row["session_id"] == session_id and row["kind"] == _INTERACTION_KIND_RESUME and row["opens_run"]
    )
    return agents


def evidence_from_batch_state(batch_state: BatchAgentState, session_id: str, agent_id: str) -> FileEvidence:
    """Build the :class:`FileEvidence` of ``agent_id`` in ``session_id`` from the batch loop's dicts.

    ``batch_state`` holds the rows built so far: earlier lines, plus the
    current line's result link when the hook calls this (the ``FINAL_ANSWER``
    rebind calls it before that link is written). It reads the spawn links,
    the run-opening ``resume`` interactions, the ``stop`` interactions with
    their first non-error result line, the ``completed`` /
    ``owner_turn_aborted`` rows, and the results of every run call, all
    owned by ``session_id`` and targeting ``agent_id``. The rules filter them
    by line, like the live twin reads the DB rows below its line.
    """
    runs = [
        FileRun(link["tool_use_id"], link["tool_use_line_num"], None, RUN_KIND_SPAWN)
        for link in batch_state.all_agent_links.values()
        if link["session_id"] == session_id and link["agent_id"] == agent_id
    ]
    stops: list[tuple[int, int | None]] = []
    for row in batch_state.all_agent_interactions.values():
        if row["session_id"] != session_id or row["agent_id"] != agent_id:
            continue
        if row["kind"] == _INTERACTION_KIND_RESUME and row["opens_run"]:
            runs.append(FileRun(row["tool_use_id"], row["tool_use_line_num"], row["event_line_num"], RUN_KIND_RESUME))
        elif row["kind"] == _INTERACTION_KIND_STOP:
            ok_lines = [
                result["tool_result_line_num"]
                for result in batch_state.results_by_tool_use.get(row["tool_use_id"], ())
                if result["session_id"] == session_id and result["error"] is None
            ]
            stops.append((row["event_line_num"], min(ok_lines, default=None)))
    completed_lines: dict[str, list[int]] = {}
    aborted_lines: dict[str, list[int]] = {}
    for end in batch_state.all_agent_run_ends.values():
        if end["session_id"] != session_id or end["agent_id"] != agent_id:
            continue
        if end["status"] == END_STATUS_COMPLETED:
            completed_lines.setdefault(end["tool_use_id"], []).append(end["line_num"])
        elif end["status"] == END_STATUS_OWNER_TURN_ABORTED:
            aborted_lines.setdefault(end["tool_use_id"], []).append(end["line_num"])
    results = {
        run.tool_use_id: tuple(
            (result["tool_result_line_num"], _iso_datetime(result["tool_result_at"]))
            for result in batch_state.results_by_tool_use.get(run.tool_use_id, ())
            if result["session_id"] == session_id
        )
        for run in runs
    }
    return FileEvidence(
        runs=tuple(runs),
        stops=tuple(stops),
        completed_lines={tool_use_id: tuple(lines) for tool_use_id, lines in completed_lines.items()},
        aborted_lines={tool_use_id: tuple(lines) for tool_use_id, lines in aborted_lines.items()},
        results=results,
    )
