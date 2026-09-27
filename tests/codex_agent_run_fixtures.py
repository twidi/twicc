"""Codex agent-run rollouts, shared by the batch tests and the live/batch parity test.

Each ``fixture_*`` builder returns a :class:`CodexAgentRunFixture`: the rollout
lines of every session it involves (the root first, then its subagents), plus
named line numbers (``marks``) the assertions refer to. The batch test
(``tests/test_codex_agent_runs_batch.py``) seeds and recomputes them; the
parity test replays the very same lines through the live hooks. Shapes follow
design §4.2 / §5.2 / §5.6 / §9 (``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md``).

Every line of a file has its own timestamp, except in
:func:`fixture_same_time_ack_and_final_answer` (on purpose) and
:func:`fixture_flat_timestamp_child` (the child file, whose flat times are the
§10 shape under test).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import orjson

T0 = datetime(2026, 9, 27, 10, tzinfo=UTC)

PROJECT_ID = "codex-agent-runs-project"
ROOT = "019f0000-0000-7000-8000-00000000r00t"
AGENT_A = "019f0000-0000-7000-8000-0000000000a1"
AGENT_B = "019f0000-0000-7000-8000-0000000000b2"
AGENT_C = "019f0000-0000-7000-8000-0000000000c3"
AGENT_G = "019f0000-0000-7000-8000-0000000000g4"  # nested: spawned by AGENT_A
PATH_A = "/root/task_a"
PATH_B = "/root/task_b"
PATH_C = "/root/task_c"
PATH_G = "/root/task_a/task_g"
# Output of an ``interrupt_agent`` call; ``followup_task`` / ``send_message``
# acks are empty (08-11 rollout, line 189).
INTERRUPT_AGENT_OUTPUT = '{"previous_status":"running"}'


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class FixtureSession(NamedTuple):
    """One rollout of a fixture: the root, or a subagent stored flat under it."""

    session_id: str
    is_subagent: bool
    lines: tuple[str, ...]


class CodexAgentRunFixture(NamedTuple):
    """The sessions of one scenario (root first) and its named line numbers."""

    name: str
    sessions: tuple[FixtureSession, ...]
    marks: Mapping[str, Mapping[str, int]]

    def session(self, session_id: str) -> FixtureSession:
        return next(session for session in self.sessions if session.session_id == session_id)

    def line(self, session_id: str, mark: str) -> int:
        return self.marks[session_id][mark]


class Rollout:
    """Appends Codex rollout lines and records named line numbers."""

    def __init__(self, session_id: str, *, subagent: bool = False) -> None:
        self.session_id = session_id
        self.subagent = subagent
        self.lines: list[str] = []
        self.marks: dict[str, int] = {}

    def add(self, entry: dict, mark: str | None = None) -> int:
        self.lines.append(orjson.dumps(entry).decode())
        line_num = len(self.lines)
        if mark is not None:
            assert mark not in self.marks, mark
            self.marks[mark] = line_num
        return line_num

    # -- line shapes --------------------------------------------------------

    @staticmethod
    def _entry(seconds: float, type_: str, payload: dict, ordinal: int | None = None) -> dict:
        stamp = at(seconds).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        entry = {"timestamp": stamp, "type": type_, "payload": payload}
        if ordinal is not None:
            entry["ordinal"] = ordinal
        return entry

    def meta(self, seconds: float, *, forked_from_id: str | None = None,
             history_start_ordinal: int | None = None, mark: str | None = None) -> int:
        payload = {"id": self.session_id, "cwd": "/tmp/codex-agent-runs", "originator": "codex_sdk_ts"}
        if forked_from_id is not None:
            payload["forked_from_id"] = forked_from_id
        if history_start_ordinal is not None:
            payload["subagent_history_start_ordinal"] = history_start_ordinal
        return self.add(self._entry(seconds, "session_meta", payload, ordinal=0), mark)

    def task_started(self, seconds: float, turn_id: str, *, ordinal: int | None = None,
                     mark: str | None = None) -> int:
        return self.add(self._entry(seconds, "event_msg", {"type": "task_started", "turn_id": turn_id},
                                    ordinal), mark)

    def task_complete(self, seconds: float, turn_id: str, *, error_info: str | None = None,
                      ordinal: int | None = None, mark: str | None = None) -> int:
        payload: dict = {"type": "task_complete", "turn_id": turn_id, "last_agent_message": "done"}
        if error_info is not None:
            payload["error"] = {"message": f"turn failed: {error_info}", "codex_error_info": error_info}
        return self.add(self._entry(seconds, "event_msg", payload, ordinal), mark)

    def turn_aborted(self, seconds: float, turn_id: str, *, reason: str = "interrupted",
                     mark: str | None = None) -> int:
        return self.add(self._entry(seconds, "event_msg",
                                    {"type": "turn_aborted", "turn_id": turn_id, "reason": reason}), mark)

    def call(self, seconds: float, name: str, call_id: str, arguments: dict, *,
             namespace: str | None = "collaboration", mark: str | None = None) -> int:
        payload = {"type": "function_call", "name": name, "call_id": call_id,
                   "arguments": orjson.dumps(arguments).decode()}
        if namespace is not None:
            payload["namespace"] = namespace
        return self.add(self._entry(seconds, "response_item", payload), mark)

    def output(self, seconds: float, call_id: str, output: str, *, mark: str | None = None) -> int:
        return self.add(self._entry(seconds, "response_item",
                                    {"type": "function_call_output", "call_id": call_id, "output": output}), mark)

    def activity(self, seconds: float, kind: str, event_id: str, agent_id: str, agent_path: str, *,
                 mark: str | None = None) -> int:
        payload = {
            "type": "item_completed",
            "thread_id": self.session_id,
            "turn_id": "turn",
            "item": {"type": "SubAgentActivity", "id": event_id, "agent_thread_id": agent_id,
                     "agent_path": agent_path, "kind": kind},
        }
        return self.add(self._entry(seconds, "event_msg", payload), mark)

    def final_answer(self, seconds: float, agent_path: str, *, mark: str | None = None) -> int:
        text = f"Message Type: FINAL_ANSWER\nTask name: /root\nSender: {agent_path}\nPayload:\ndone"
        return self.add(self._entry(seconds, "response_item", {
            "type": "agent_message", "content": [{"type": "input_text", "text": text}],
        }), mark)

    # -- composite steps ----------------------------------------------------

    def spawn(self, seconds: float, call_id: str, agent_id: str, agent_path: str, *,
              mark: str | None = None) -> None:
        """v2 spawn: the call, its ``started`` event, its ack (three lines, 0.1 s apart)."""
        self.call(seconds, "spawn_agent", call_id,
                  {"task_name": agent_path.rsplit("/", 1)[-1], "fork_turns": "none", "message": "gAAAA"},
                  mark=mark)
        self.activity(seconds + 0.1, "started", call_id, agent_id, agent_path)
        self.output(seconds + 0.2, call_id, orjson.dumps({"task_name": agent_path}).decode())

    def control(self, seconds: float, tool: str, call_id: str, agent_path: str, *,
                event_kind: str | None = "interacted", agent_id: str | None = None,
                output: str = "", mark: str | None = None) -> None:
        """A collaboration control call, its activity event, its output (0.1 s apart)."""
        self.call(seconds, tool, call_id, {"target": agent_path, "message": "gAAAA"}, mark=mark)
        if event_kind is not None:
            self.activity(seconds + 0.1, event_kind, call_id, agent_id, agent_path,
                          mark=f"{mark}_event" if mark else None)
        self.output(seconds + 0.2, call_id, output, mark=f"{mark}_output" if mark else None)

    def followup(self, seconds: float, call_id: str, agent_id: str, agent_path: str, *,
                 mark: str | None = None) -> None:
        self.control(seconds, "followup_task", call_id, agent_path, agent_id=agent_id, mark=mark)

    def completed(self, seconds: float, agent_id: str, agent_path: str, child_turn: str, *,
                  mark: str | None = None) -> int:
        return self.activity(seconds, "completed", f"subagent-completed-{child_turn}", agent_id, agent_path,
                             mark=mark)


def _fixture(name: str, *rollouts: Rollout) -> CodexAgentRunFixture:
    return CodexAgentRunFixture(
        name=name,
        sessions=tuple(FixtureSession(r.session_id, r.subagent, tuple(r.lines)) for r in rollouts),
        marks={r.session_id: dict(r.marks) for r in rollouts},
    )


def _root_start() -> Rollout:
    root = Rollout(ROOT)
    root.meta(0)
    root.task_started(1, "t1", mark="t1_started")
    return root


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def fixture_idle_followup_opens_run() -> CodexAgentRunFixture:
    """Spawn → ``completed`` → ``FINAL_ANSWER`` → ``followup_task`` on the idle agent → its own pair."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.task_complete(3, "t1")
    root.task_started(10, "t2")
    root.completed(11, AGENT_A, PATH_A, "a1", mark="completed_1")
    root.final_answer(12, PATH_A, mark="final_1")
    root.followup(13, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.completed(20, AGENT_A, PATH_A, "a2", mark="completed_2")
    root.final_answer(21, PATH_A, mark="final_2")
    root.task_complete(22, "t2")
    return _fixture("idle_followup_opens_run", root)


def fixture_merged_followup() -> CodexAgentRunFixture:
    """08-31: a ``followup_task`` on a running agent merges; the single pair lands on the spawn."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.followup(5, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.completed(8, AGENT_A, PATH_A, "a1", mark="completed")
    root.final_answer(9, PATH_A, mark="final")
    root.task_complete(10, "t1")
    return _fixture("merged_followup", root)


def fixture_reaudit_limitation() -> CodexAgentRunFixture:
    """09-06 ``reaudit_backend``: the merged follow-up's later pair lands on the spawn (extra signals)."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.followup(5, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.completed(8, AGENT_A, PATH_A, "a1", mark="completed_1")
    root.final_answer(9, PATH_A, mark="final_1")
    root.completed(146, AGENT_A, PATH_A, "a2", mark="completed_2")
    root.final_answer(147, PATH_A, mark="final_2")
    root.task_complete(148, "t1")
    return _fixture("reaudit_limitation", root)


# Owner abort kinds: the two that cut, and the two that do not.
OWNER_ABORT_KINDS = ("turn_aborted", "usage_limit", "other_reason", "other_error", "unknown_turn")
OWNER_ABORT_CUTTING_KINDS = ("turn_aborted", "usage_limit")


def fixture_owner_abort(kind: str = "turn_aborted") -> CodexAgentRunFixture:
    """Owner turn abort (``01a08171…`` 17910 / ``01a0796d…`` 704 shapes).

    Turn t1 spawns A (ends with its pair) and C (still running). Turn t2
    follows A up (a resume run) and spawns B, then ends by ``kind``. Turn t3
    follows A up again. The cutting kinds cut A's t2 run and B's spawn, not
    C's spawn (an earlier turn); ``unknown_turn`` names a turn with no
    ``task_started`` and cuts nothing.
    """
    root = _root_start()
    root.spawn(2, "c_a", AGENT_A, PATH_A, mark="spawn_a")
    root.spawn(5, "c_c", AGENT_C, PATH_C, mark="spawn_c")
    root.completed(8, AGENT_A, PATH_A, "a1", mark="completed_a1")
    root.final_answer(9, PATH_A, mark="final_a1")
    root.task_complete(10, "t1")
    root.task_started(20, "t2", mark="t2_started")
    root.followup(21, "c_fa1", AGENT_A, PATH_A, mark="fa1")
    root.spawn(24, "c_b", AGENT_B, PATH_B, mark="spawn_b")
    if kind == "turn_aborted":
        root.turn_aborted(30, "t2", mark="abort")
    elif kind == "usage_limit":
        root.task_complete(30, "t2", error_info="usage_limit_exceeded", mark="abort")
    elif kind == "other_reason":
        root.turn_aborted(30, "t2", reason="replaced", mark="abort")
    elif kind == "other_error":
        root.task_complete(30, "t2", error_info="server_overloaded", mark="abort")
    elif kind == "unknown_turn":
        root.turn_aborted(30, "t-missing", mark="abort")
    else:
        raise ValueError(kind)
    root.task_started(40, "t3")
    root.followup(41, "c_fa2", AGENT_A, PATH_A, mark="fa2")
    root.completed(50, AGENT_A, PATH_A, "a3", mark="completed_a3")
    root.final_answer(51, PATH_A, mark="final_a3")
    root.task_complete(52, "t3")
    return _fixture(f"owner_abort_{kind}", root)


def fixture_owner_abort_repeated_turn_id() -> CodexAgentRunFixture:
    """A turn id with two ``task_started`` lines: the abort cuts from the newest one.

    A's spawn sits between the two starts and is not cut; B's spawn follows
    the second start and is cut.
    """
    root = _root_start()
    root.task_complete(1.5, "t1")
    root.task_started(2, "t2", mark="first_started")
    root.spawn(3, "c_a", AGENT_A, PATH_A, mark="spawn_a")
    root.task_started(10, "t2", mark="second_started")
    root.spawn(11, "c_b", AGENT_B, PATH_B, mark="spawn_b")
    root.turn_aborted(20, "t2", mark="abort")
    return _fixture("owner_abort_repeated_turn_id", root)


def fixture_subagent_owner_usage_limit() -> CodexAgentRunFixture:
    """A subagent that owns a run ends its turn on a usage limit: both rows at that line."""
    root = _root_start()
    root.spawn(2, "c_a", AGENT_A, PATH_A, mark="spawn_a")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(3)
    child.task_started(4, "ta1", mark="ta1_started")
    child.spawn(5, "c_g", AGENT_G, PATH_G, mark="spawn_g")
    # Messages to the owner itself and to the root write no row (§5.1).
    child.control(6, "send_message", "c_to_owner", PATH_A, agent_id=AGENT_A)
    child.control(7, "send_message", "c_to_root", "/root", agent_id=ROOT)
    child.task_complete(9, "ta1", error_info="usage_limit_exceeded", mark="usage_limit")
    return _fixture("subagent_owner_usage_limit", root, child)


def fixture_control_calls() -> CodexAgentRunFixture:
    """``send_message`` → no run; ignored events; ``interrupt_agent`` → a stop record."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.control(5, "send_message", "c_msg", PATH_A, agent_id=AGENT_A, mark="msg")
    root.activity(6, "interacted", "c_ghost", AGENT_A, PATH_A, mark="ghost")  # no call
    root.control(7, "wait_agent", "c_wait", PATH_A, agent_id=AGENT_A, mark="wait")
    root.call(8, "send_input", "c_v1", {"id": AGENT_A, "message": "hi"}, namespace=None, mark="v1")
    root.activity(8.1, "interacted", "c_v1", AGENT_A, PATH_A)
    root.output(8.2, "c_v1", "{}")
    root.control(9, "send_message", "c_to_root", "/root", agent_id=ROOT, mark="to_root")
    root.control(12, "interrupt_agent", "c_stop", PATH_A, event_kind="interrupted", agent_id=AGENT_A,
                 output=INTERRUPT_AGENT_OUTPUT, mark="stop")
    return _fixture("control_calls", root)


def fixture_child_turn_ends() -> CodexAgentRunFixture:
    """Non-fork child: each ``task_complete`` writes a ``turn_complete`` row at its line time."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.completed(31, AGENT_A, PATH_A, "c1", mark="completed")
    root.final_answer(32, PATH_A, mark="final")
    root.task_complete(33, "t1")
    root.task_started(50, "t2")
    root.followup(51, "c_fu", AGENT_A, PATH_A, mark="fu")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    child.task_started(2.5, "c1")
    child.task_complete(30, "c1", mark="end_1")
    child.task_started(51.5, "c2", mark="c2_started")
    child.task_complete(90, "c2", mark="end_2")
    return _fixture("child_turn_ends", root, child)


def fixture_forked_child() -> CodexAgentRunFixture:
    """09-08 ``01a08112-b173…``: copied ``task_complete`` lines below the history ordinal write nothing."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.040, forked_from_id=ROOT, history_start_ordinal=10)
    child.task_started(2.041, "p1", ordinal=1)
    child.task_complete(2.042, "p1", ordinal=2, mark="copied_1")
    child.task_started(2.043, "p2", ordinal=3)
    child.task_complete(2.044, "p2", ordinal=4, mark="copied_2")
    child.task_started(2.5, "own1", ordinal=10, mark="own_started")
    child.task_complete(40, "own1", ordinal=11, mark="own_end")
    return _fixture("forked_child", root, child)


def fixture_non_fork_child_with_history_field() -> CodexAgentRunFixture:
    """``01a05541-635c…``: a non-fork child whose real turn end sits below the field still writes."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1, history_start_ordinal=232)
    child.task_started(2.5, "c1", ordinal=1)
    child.task_complete(30, "c1", ordinal=231, mark="end")
    return _fixture("non_fork_child_with_history_field", root, child)


def fixture_v1_notification() -> CodexAgentRunFixture:
    """Multi-agent v1: the ``<subagent_notification>`` rebinds to its spawn by agent id."""
    root = _root_start()
    root.call(2, "spawn_agent", "c_v1_spawn", {"message": "go", "fork_context": "none"}, namespace=None,
              mark="spawn")
    root.output(3, "c_v1_spawn", orjson.dumps({"agent_id": AGENT_A, "nickname": "Ptolemy"}).decode(),
                mark="ack")
    body = orjson.dumps({"agent_path": AGENT_A, "status": {"completed": "done"}}).decode()
    root.add(Rollout._entry(9, "response_item", {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": f"<subagent_notification>\n{body}\n</subagent_notification>"}],
    }), mark="notification")
    return _fixture("v1_notification", root)


# -- attribution sequences (§5.6 / §9), replayed live by the parity test ------


def fixture_t20_sequence() -> CodexAgentRunFixture:
    """``01a08171…`` t20: the spawn ends with ``FINAL_ANSWER`` only; each follow-up gets its own pair.

    The child's turn end (rule 5) closes the spawn run before its late
    ``FINAL_ANSWER``.
    """
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.task_complete(3, "t1")
    root.task_started(100, "t2")
    root.final_answer(101, PATH_A, mark="final_0")
    root.followup(102, "c_fu1", AGENT_A, PATH_A, mark="fu1")
    root.completed(110, AGENT_A, PATH_A, "a2", mark="completed_1")
    root.final_answer(111, PATH_A, mark="final_1")
    root.followup(112, "c_fu2", AGENT_A, PATH_A, mark="fu2")
    root.completed(120, AGENT_A, PATH_A, "a3", mark="completed_2")
    root.final_answer(121, PATH_A, mark="final_2")
    root.task_complete(122, "t2")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    child.task_started(2.5, "a1")
    child.task_complete(50, "a1", mark="end_1")
    return _fixture("t20_sequence", root, child)


def fixture_completed_between_call_and_interacted() -> CodexAgentRunFixture:
    """``01a0796d-72bd…`` line 210: the previous run's ``completed`` lands between the call and its event."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.call(10, "followup_task", "c_fu", {"target": PATH_A, "message": "gAAAA"}, mark="fu")
    root.completed(11, AGENT_A, PATH_A, "a1", mark="completed_0")
    root.activity(12, "interacted", "c_fu", AGENT_A, PATH_A, mark="fu_event")
    root.output(13, "c_fu", "", mark="fu_output")
    root.final_answer(14, PATH_A, mark="final_0")
    root.completed(20, AGENT_A, PATH_A, "a2", mark="completed_1")
    root.final_answer(21, PATH_A, mark="final_1")
    root.task_complete(22, "t1")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    child.task_started(2.5, "a1")
    child.task_complete(11.5, "a1", mark="end_1")
    child.task_started(12.5, "a2")
    child.task_complete(19.5, "a2", mark="end_2")
    return _fixture("completed_between_call_and_interacted", root, child)


def fixture_stop_in_completed_final_gap() -> CodexAgentRunFixture:
    """S0 gets ``completed``, a stop, a follow-up F1; S0's late ``FINAL_ANSWER`` stays on S0; F1 stays open."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.completed(10, AGENT_A, PATH_A, "a1", mark="completed_0")
    root.control(11, "interrupt_agent", "c_stop", PATH_A, event_kind="interrupted", agent_id=AGENT_A,
                 output=INTERRUPT_AGENT_OUTPUT, mark="stop")
    root.followup(14, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.final_answer(17, PATH_A, mark="final_0")
    return _fixture("stop_in_completed_final_gap", root)


def fixture_merged_then_real_followups() -> CodexAgentRunFixture:
    """09-07 ``spec_provider_review`` / 09-06 ``reaudit_accessibility``: merged, then two real runs."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.followup(5, "c_fu0", AGENT_A, PATH_A, mark="fu0")
    root.completed(8, AGENT_A, PATH_A, "a1", mark="completed_0")
    root.final_answer(9, PATH_A, mark="final_0")
    root.followup(10, "c_fu1", AGENT_A, PATH_A, mark="fu1")
    root.completed(15, AGENT_A, PATH_A, "a2", mark="completed_1")
    root.final_answer(16, PATH_A, mark="final_1")
    root.followup(17, "c_fu2", AGENT_A, PATH_A, mark="fu2")
    root.completed(25, AGENT_A, PATH_A, "a3", mark="completed_2")
    root.final_answer(26, PATH_A, mark="final_2")
    root.task_complete(27, "t1")
    return _fixture("merged_then_real_followups", root)


def fixture_followup_after_interrupt() -> CodexAgentRunFixture:
    """08-12 ``019ff497`` 4448/4452: a follow-up seconds after ``interrupt_agent`` opens a run."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.control(10, "interrupt_agent", "c_stop", PATH_A, event_kind="interrupted", agent_id=AGENT_A,
                 output=INTERRUPT_AGENT_OUTPUT, mark="stop")
    root.followup(14, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.completed(20, AGENT_A, PATH_A, "a2", mark="completed")
    root.final_answer(21, PATH_A, mark="final")
    root.task_complete(22, "t1")
    return _fixture("followup_after_interrupt", root)


def fixture_long_gap_with_followup() -> CodexAgentRunFixture:
    """A long ``completed`` → ``FINAL_ANSWER`` gap with a follow-up inside: the answer goes to the first run."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.completed(10, AGENT_A, PATH_A, "a1", mark="completed_0")
    root.followup(11, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.final_answer(600, PATH_A, mark="final_0")
    root.completed(610, AGENT_A, PATH_A, "a2", mark="completed_1")
    root.final_answer(611, PATH_A, mark="final_1")
    root.task_complete(612, "t1")
    return _fixture("long_gap_with_followup", root)


def fixture_owner_abort_after_signal() -> CodexAgentRunFixture:
    """An owner abort after L: the merged follow-up's decision at L stands; the abort cuts the spawn."""
    root = _root_start()
    root.task_complete(1.5, "t1")
    root.task_started(2, "t2", mark="t2_started")
    root.spawn(3, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.followup(6, "c_fu0", AGENT_A, PATH_A, mark="fu0")
    root.turn_aborted(10, "t2", mark="abort")
    root.task_started(20, "t3")
    root.followup(21, "c_fu1", AGENT_A, PATH_A, mark="fu1")
    root.completed(30, AGENT_A, PATH_A, "a2", mark="completed_1")
    root.final_answer(31, PATH_A, mark="final_1")
    root.task_complete(32, "t3")
    return _fixture("owner_abort_after_signal", root)


def fixture_stop_result_before_event() -> CodexAgentRunFixture:
    """A stop whose result precedes its ``interrupted`` line, with a ``completed`` between them."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.call(10, "interrupt_agent", "c_stop", {"target": PATH_A}, mark="stop")
    root.output(11, "c_stop", INTERRUPT_AGENT_OUTPUT, mark="stop_output")
    root.completed(12, AGENT_A, PATH_A, "a1", mark="completed")
    root.activity(13, "interrupted", "c_stop", AGENT_A, PATH_A, mark="stop_event")
    root.final_answer(14, PATH_A, mark="final")
    return _fixture("stop_result_before_event", root)


def fixture_interrupt_race() -> CodexAgentRunFixture:
    """Interrupt call → ``completed`` → ``interrupted`` → result → ``FINAL_ANSWER``: both on the spawn."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.call(10, "interrupt_agent", "c_stop", {"target": PATH_A}, mark="stop")
    root.completed(11, AGENT_A, PATH_A, "a1", mark="completed")
    root.activity(12, "interrupted", "c_stop", AGENT_A, PATH_A, mark="stop_event")
    root.output(13, "c_stop", INTERRUPT_AGENT_OUTPUT, mark="stop_output")
    root.final_answer(14, PATH_A, mark="final")
    return _fixture("interrupt_race", root)


def fixture_duplicate_interacted() -> CodexAgentRunFixture:
    """A duplicated ``interacted`` line: the first line wins."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.completed(5, AGENT_A, PATH_A, "a1")
    root.final_answer(6, PATH_A)
    root.followup(10, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.activity(11, "interacted", "c_fu", AGENT_A, PATH_A, mark="fu_duplicate")
    return _fixture("duplicate_interacted", root)


def fixture_same_time_ack_and_final_answer() -> CodexAgentRunFixture:
    """Ack and ``FINAL_ANSWER`` share a timestamp (on purpose): they count one result."""
    root = _root_start()
    root.call(2, "spawn_agent", "c_spawn", {"task_name": "task_a", "message": "gAAAA"}, mark="spawn")
    root.activity(2.1, "started", "c_spawn", AGENT_A, PATH_A)
    root.output(2.2, "c_spawn", orjson.dumps({"task_name": PATH_A}).decode(), mark="ack")
    root.final_answer(2.2, PATH_A, mark="final")
    root.followup(5, "c_fu", AGENT_A, PATH_A, mark="fu")
    return _fixture("same_time_ack_and_final_answer", root)


# -- old shapes --------------------------------------------------------------


def fixture_pre_completed_rollout() -> CodexAgentRunFixture:
    """Pre-08-27: no ``completed`` event; runs end by ``FINAL_ANSWER`` only."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.final_answer(9, PATH_A, mark="final_0")
    root.followup(10, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.final_answer(20, PATH_A, mark="final_1")
    root.task_complete(21, "t1")
    return _fixture("pre_completed_rollout", root)


def fixture_flat_timestamp_child() -> CodexAgentRunFixture:
    """§10: a child whose every line carries its thread creation time (spawn + 0.1 s)."""
    root = _root_start()
    root.spawn(2, "c_spawn", AGENT_A, PATH_A, mark="spawn")
    root.final_answer(60, PATH_A, mark="final")
    root.task_complete(61, "t1")
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    child.task_started(2.1, "a1")
    child.task_complete(2.1, "a1", mark="end")
    return _fixture("flat_timestamp_child", root, child)


def fixture_started_without_spawn_call() -> CodexAgentRunFixture:
    """A ``started`` event whose ``spawn_agent`` call is not in the file: no spawn link.

    The run-opening follow-up is the agent's only run here; its
    ``FINAL_ANSWER`` lands on it (the agent comes from the ``started``
    event, as live reads it, not from a spawn link).
    """
    root = _root_start()
    root.activity(2, "started", "c_spawn", AGENT_A, PATH_A, mark="started")
    root.followup(5, "c_fu", AGENT_A, PATH_A, mark="fu")
    root.final_answer(9, PATH_A, mark="final")
    root.task_complete(10, "t1")
    return _fixture("started_without_spawn_call", root)


# Every scenario, by name, for the parity test.
ALL_FIXTURES: dict[str, Callable[[], CodexAgentRunFixture]] = {
    "idle_followup_opens_run": fixture_idle_followup_opens_run,
    "merged_followup": fixture_merged_followup,
    "reaudit_limitation": fixture_reaudit_limitation,
    **{f"owner_abort_{kind}": (lambda kind=kind: fixture_owner_abort(kind)) for kind in OWNER_ABORT_KINDS},
    "owner_abort_repeated_turn_id": fixture_owner_abort_repeated_turn_id,
    "subagent_owner_usage_limit": fixture_subagent_owner_usage_limit,
    "control_calls": fixture_control_calls,
    "child_turn_ends": fixture_child_turn_ends,
    "forked_child": fixture_forked_child,
    "non_fork_child_with_history_field": fixture_non_fork_child_with_history_field,
    "v1_notification": fixture_v1_notification,
    "t20_sequence": fixture_t20_sequence,
    "completed_between_call_and_interacted": fixture_completed_between_call_and_interacted,
    "stop_in_completed_final_gap": fixture_stop_in_completed_final_gap,
    "merged_then_real_followups": fixture_merged_then_real_followups,
    "followup_after_interrupt": fixture_followup_after_interrupt,
    "long_gap_with_followup": fixture_long_gap_with_followup,
    "owner_abort_after_signal": fixture_owner_abort_after_signal,
    "stop_result_before_event": fixture_stop_result_before_event,
    "interrupt_race": fixture_interrupt_race,
    "duplicate_interacted": fixture_duplicate_interacted,
    "same_time_ack_and_final_answer": fixture_same_time_ack_and_final_answer,
    "pre_completed_rollout": fixture_pre_completed_rollout,
    "flat_timestamp_child": fixture_flat_timestamp_child,
    "started_without_spawn_call": fixture_started_without_spawn_call,
}
