"""File-local Codex run attribution (design §5.6) and the Codex line parsers.

The attribution rules are pure functions over a :class:`FileEvidence` value
(one owner rollout, one agent). The batch and live paths only build that
value, so these tests pin the rules once for both.

Most cases replay a line sequence through :class:`_Rollout`, a tiny
stand-in for the compute passes: it records each row the way 5b/5c do
(a follow-up opens a run when the agent has no file-open run at its
``interacted`` line; a ``completed`` / ``FINAL_ANSWER`` lands on the run the
rules choose), so every later decision reads the rows written before it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from twicc.providers.codex.agent_runs import (
    INTERACTION_KIND_BY_TOOL,
    FileEvidence,
    FileRun,
    ForkFields,
    SubAgentActivity,
    attribute_completed,
    attribute_final_answer,
    candidates,
    file_open_runs,
    fork_fields,
    is_copied_history,
    is_task_complete,
    line_ordinal,
    owner_abort_cut_runs,
    owner_turn_abort_turn_id,
    parse_sub_agent_activity,
    task_started_turn_id,
)

_BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _t(seconds: float) -> datetime:
    return _BASE + timedelta(seconds=seconds)


class _Rollout:
    """One owner rollout and one agent, replayed line by line."""

    def __init__(self) -> None:
        self.runs: list[FileRun] = []
        self.stops: list[tuple[int, int | None]] = []
        self.completed: dict[str, list[int]] = {}
        self.aborted: dict[str, list[int]] = {}
        self.results: dict[str, list[tuple[int, datetime | None]]] = {}

    def evidence(self) -> FileEvidence:
        return FileEvidence(
            runs=tuple(self.runs),
            stops=tuple(self.stops),
            completed_lines={key: tuple(lines) for key, lines in self.completed.items()},
            aborted_lines={key: tuple(lines) for key, lines in self.aborted.items()},
            results={key: tuple(rows) for key, rows in self.results.items()},
        )

    def result(self, tool_use_id: str, line: int, at: datetime | None) -> None:
        self.results.setdefault(tool_use_id, []).append((line, at))

    def spawn(self, tool_use_id: str, call_line: int, ack_line: int, ack_at: datetime) -> FileRun:
        run = FileRun(tool_use_id, call_line, None, "spawn")
        self.runs.append(run)
        self.result(tool_use_id, ack_line, ack_at)
        return run

    def followup(
        self, tool_use_id: str, call_line: int, event_line: int, ack_line: int, ack_at: datetime
    ) -> FileRun | None:
        """Return the run the follow-up opened, or ``None`` when it merged."""
        opens_run = not file_open_runs(self.evidence(), event_line)
        run = FileRun(tool_use_id, call_line, event_line, "resume") if opens_run else None
        if run is not None:
            self.runs.append(run)
        self.result(tool_use_id, ack_line, ack_at)
        return run

    def interrupt(self, event_line: int, result_line: int) -> None:
        self.stops.append((event_line, result_line))

    def completed_at(self, line: int) -> FileRun | None:
        run = attribute_completed(self.evidence(), line)
        if run is not None:
            self.completed.setdefault(run.tool_use_id, []).append(line)
        return run

    def final_answer_at(self, line: int, at: datetime) -> FileRun | None:
        run = attribute_final_answer(self.evidence(), line)
        if run is not None:
            self.result(run.tool_use_id, line, at)
        return run

    def owner_abort_at(self, line: int) -> list[FileRun]:
        cut = file_open_runs(self.evidence(), line)
        for run in cut:
            self.aborted.setdefault(run.tool_use_id, []).append(line)
        return cut


def _ids(runs: list[FileRun]) -> list[str]:
    return [run.tool_use_id for run in runs]


# ---------------------------------------------------------------------------
# Attribution cases (§9)
# ---------------------------------------------------------------------------


def test_t20_sequence_each_signal_on_its_own_followup_no_cascade():
    """``01a08171…`` agent t20: the spawn ends with a ``FINAL_ANSWER`` only."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 76275, 76278, _t(0))
    assert rollout.final_answer_at(76417, _t(100)) == spawn

    first = rollout.followup("fu1", 76422, 76423, 76424, _t(110))
    assert first is not None  # the spawn has its FINAL_ANSWER: nothing file-open
    assert rollout.completed_at(76508) == first
    assert rollout.final_answer_at(76517, _t(200)) == first

    second = rollout.followup("fu2", 76532, 76533, 76534, _t(210))
    assert second is not None
    assert rollout.completed_at(76583) == second
    assert rollout.final_answer_at(76590, _t(300)) == second
    assert "spawn" not in rollout.completed
    assert file_open_runs(rollout.evidence(), 76600) == []


def test_previous_completed_between_followup_call_and_interacted_goes_to_previous_run():
    """``01a0796d-72bd…`` line 210: candidates read the stored ``event_line``."""
    evidence = FileEvidence(
        runs=(
            FileRun("spawn", 10, None, "spawn"),
            FileRun("fu", 205, 212, "resume"),
        ),
        stops=(),
        completed_lines={},
        aborted_lines={},
        results={"spawn": ((12, _t(0)),), "fu": ((213, _t(50)),)},
    )
    assert _ids(candidates(evidence, 210)) == ["spawn"]
    assert attribute_completed(evidence, 210).tool_use_id == "spawn"


def test_previous_completed_before_interacted_lets_the_followup_open_a_run():
    rollout = _Rollout()
    rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.completed_at(210).tool_use_id == "spawn"
    followup = rollout.followup("fu", 205, 212, 213, _t(50))
    assert followup is not None
    assert rollout.final_answer_at(215, _t(51)).tool_use_id == "spawn"
    assert rollout.completed_at(300) == followup


def test_interrupt_race_signals_land_on_the_interrupted_run():
    """Call, target ``completed``, ``interrupted``, interrupt result, FINAL_ANSWER."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    # interrupt_agent call at line 20
    assert rollout.completed_at(21) == spawn
    rollout.interrupt(event_line=22, result_line=23)
    assert _ids(candidates(rollout.evidence(), 24)) == ["spawn"]
    assert rollout.final_answer_at(24, _t(30)) == spawn


def test_stop_in_the_gap_late_final_answer_goes_to_s0_and_f1_stays_open():
    rollout = _Rollout()
    s0 = rollout.spawn("s0", 10, 12, _t(0))
    assert rollout.completed_at(20) == s0
    rollout.interrupt(event_line=31, result_line=32)
    f1 = rollout.followup("f1", 40, 41, 42, _t(40))
    assert f1 is not None
    assert rollout.final_answer_at(50, _t(50)) == s0
    assert file_open_runs(rollout.evidence(), 51) == [f1]


def test_stop_result_before_its_interrupted_line_keeps_the_run_candidate():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    # interrupt call 20, its result 21, a completed at 22, the interrupted event 23.
    rollout.interrupt(event_line=23, result_line=21)
    assert _ids(candidates(rollout.evidence(), 22)) == ["spawn"]
    assert rollout.completed_at(22) == spawn


def test_stop_in_line_order_excludes_the_interrupted_run_after_both_lines():
    rollout = _Rollout()
    rollout.spawn("spawn", 10, 12, _t(0))
    rollout.interrupt(event_line=23, result_line=21)
    assert candidates(rollout.evidence(), 24) == []
    assert file_open_runs(rollout.evidence(), 24) == []


def test_stop_without_result_line_does_not_exclude():
    evidence = FileEvidence(
        runs=(FileRun("spawn", 10, None, "spawn"),),
        stops=((20, None),),
        completed_lines={},
        aborted_lines={},
        results={"spawn": ((12, _t(0)),)},
    )
    assert _ids(candidates(evidence, 30)) == ["spawn"]


def test_owner_abort_after_l_does_not_yet_exclude():
    evidence = FileEvidence(
        runs=(FileRun("spawn", 10, None, "spawn"),),
        stops=(),
        completed_lines={},
        aborted_lines={"spawn": (40,)},
        results={"spawn": ((12, _t(0)),)},
    )
    assert _ids(candidates(evidence, 30)) == ["spawn"]
    assert attribute_completed(evidence, 30).tool_use_id == "spawn"
    assert _ids(candidates(evidence, 40)) == ["spawn"]
    assert candidates(evidence, 41) == []


def test_owner_abort_cut_run_falls_back_to_newest_spawn_for_completed():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.owner_abort_at(20) == [spawn]
    assert candidates(rollout.evidence(), 30) == []
    assert attribute_completed(rollout.evidence(), 30) == spawn
    assert attribute_final_answer(rollout.evidence(), 30) is None


def test_merged_followup_leaves_the_single_pair_on_the_spawn():
    """08-31 ``/root/task2_backend_notifications``: follow-up on a running agent."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 1285, 1287, _t(0))
    assert rollout.followup("fu", 1307, 1308, 1309, _t(10)) is None
    assert rollout.completed_at(1318) == spawn
    assert rollout.final_answer_at(1322, _t(20)) == spawn
    assert file_open_runs(rollout.evidence(), 1323) == []


def test_reaudit_backend_second_pair_lands_on_spawn_as_extra_signals():
    """09-06 ``/root/reaudit_backend``: documented limitation (§5.6)."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 4117, 4119, _t(0))
    assert rollout.followup("fu", 4241, 4242, 4243, _t(10)) is None
    assert rollout.completed_at(4246) == spawn
    assert rollout.final_answer_at(4251, _t(20)) == spawn
    assert rollout.completed_at(4293) == spawn
    assert rollout.final_answer_at(4300, _t(157)) == spawn
    assert rollout.completed["spawn"] == [4246, 4293]


def test_merged_then_real_followups_each_get_their_own_pair():
    """09-07 ``spec_provider_review`` / 09-06 ``reaudit_accessibility`` shapes."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.followup("merged", 20, 21, 22, _t(5)) is None
    assert rollout.completed_at(30) == spawn
    assert rollout.final_answer_at(31, _t(30)) == spawn

    first = rollout.followup("real1", 40, 41, 42, _t(40))
    assert first is not None
    assert rollout.completed_at(50) == first
    assert rollout.final_answer_at(51, _t(50)) == first

    second = rollout.followup("real2", 60, 61, 62, _t(60))
    assert second is not None
    assert rollout.completed_at(70) == second
    assert rollout.final_answer_at(71, _t(70)) == second
    assert "merged" not in rollout.completed
    assert file_open_runs(rollout.evidence(), 72) == []


def test_followup_a_few_seconds_after_interrupt_gets_the_next_pair():
    """08-12 ``019ff497`` lines 4448/4452."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 4400, 4402, _t(0))
    rollout.interrupt(event_line=4448, result_line=4449)
    followup = rollout.followup("fu", 4452, 4453, 4454, _t(5))
    assert followup is not None
    assert spawn not in candidates(rollout.evidence(), 4460)
    assert rollout.completed_at(4460) == followup
    assert rollout.final_answer_at(4461, _t(30)) == followup


def test_long_completed_to_final_answer_gap_with_followup_inside():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.completed_at(20) == spawn
    followup = rollout.followup("fu", 30, 31, 32, _t(30))
    assert followup is not None
    assert rollout.final_answer_at(40, _t(213)) == spawn
    assert rollout.completed_at(50) == followup
    assert rollout.final_answer_at(51, _t(300)) == followup


def test_runs_ending_with_final_answer_only_and_the_followup_after_one():
    """Pre-08-27 rollouts: no ``completed`` event at all."""
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.final_answer_at(20, _t(20)) == spawn
    followup = rollout.followup("fu", 30, 31, 32, _t(30))
    assert followup is not None
    assert rollout.final_answer_at(40, _t(40)) == followup
    assert file_open_runs(rollout.evidence(), 41) == []


def test_ack_and_final_answer_with_same_timestamp_count_one_result():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.final_answer_at(20, _t(0)) == spawn
    assert file_open_runs(rollout.evidence(), 21) == [spawn]
    assert rollout.followup("fu", 30, 31, 32, _t(30)) is None


def test_final_answer_counts_only_result_lines_before_l():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.final_answer_at(20, _t(20)) == spawn
    assert file_open_runs(rollout.evidence(), 20) == [spawn]
    assert file_open_runs(rollout.evidence(), 21) == []


def test_extra_signal_goes_to_newest_candidate():
    rollout = _Rollout()
    rollout.spawn("spawn", 10, 12, _t(0))
    rollout.completed_at(20)
    rollout.final_answer_at(21, _t(21))
    followup = rollout.followup("fu", 30, 31, 32, _t(30))
    rollout.completed_at(40)
    rollout.final_answer_at(41, _t(41))
    assert attribute_completed(rollout.evidence(), 50) == followup
    assert attribute_final_answer(rollout.evidence(), 50) == followup


def test_completed_prefers_final_answer_only_run_when_none_has_neither():
    rollout = _Rollout()
    spawn = rollout.spawn("spawn", 10, 12, _t(0))
    assert rollout.final_answer_at(20, _t(20)) == spawn
    assert rollout.completed_at(21) == spawn


def test_completed_picks_the_oldest_final_answer_only_run():
    """No run has neither signal: the OLDEST FA-only run wins, not the newest."""
    evidence = FileEvidence(
        runs=(
            FileRun("spawn", 10, None, "spawn"),
            FileRun("fu1", 20, 21, "resume"),
            FileRun("fu2", 30, 31, "resume"),
        ),
        stops=(),
        completed_lines={"spawn": (15,)},
        aborted_lines={},
        results={
            "spawn": ((12, _t(0)), (16, _t(5))),
            "fu1": ((22, _t(20)), (25, _t(25))),
            "fu2": ((32, _t(30)), (35, _t(35))),
        },
    )
    assert attribute_completed(evidence, 40).tool_use_id == "fu1"


def test_final_answer_prefers_a_completed_run_over_an_older_run_with_neither():
    """The ``completed``-without-FA run wins even when an older run has neither."""
    evidence = FileEvidence(
        runs=(
            FileRun("spawn", 10, None, "spawn"),
            FileRun("fu", 20, 21, "resume"),
        ),
        stops=(),
        completed_lines={"fu": (30,)},
        aborted_lines={},
        results={"spawn": ((12, _t(0)),), "fu": ((22, _t(20)),)},
    )
    assert attribute_final_answer(evidence, 40).tool_use_id == "fu"


def test_null_result_time_is_not_a_distinct_timestamp():
    evidence = FileEvidence(
        runs=(FileRun("spawn", 10, None, "spawn"),),
        stops=(),
        completed_lines={},
        aborted_lines={},
        results={"spawn": ((12, _t(0)), (20, None))},
    )
    assert _ids(file_open_runs(evidence, 30)) == ["spawn"]
    both_null = evidence._replace(results={"spawn": ((12, None), (20, None))})
    assert _ids(file_open_runs(both_null, 30)) == ["spawn"]


def test_no_run_at_all_attributes_nothing():
    evidence = FileEvidence(runs=(), stops=(), completed_lines={}, aborted_lines={}, results={})
    assert candidates(evidence, 10) == []
    assert attribute_completed(evidence, 10) is None
    assert attribute_final_answer(evidence, 10) is None


def test_candidates_are_ordered_by_call_line():
    evidence = FileEvidence(
        runs=(
            FileRun("fu", 30, 31, "resume"),
            FileRun("spawn", 10, None, "spawn"),
        ),
        stops=(),
        completed_lines={},
        aborted_lines={},
        results={},
    )
    assert _ids(candidates(evidence, 40)) == ["spawn", "fu"]
    assert _ids(candidates(evidence, 31)) == ["spawn"]
    assert candidates(evidence, 10) == []
    assert _ids(candidates(evidence, 11)) == ["spawn"]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _activity_line(kind: str, event_id: str) -> dict:
    return {
        "timestamp": "2026-09-01T12:00:00.000Z",
        "ordinal": 12,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "SubAgentActivity",
                "id": event_id,
                "kind": kind,
                "agent_thread_id": "01a06b41-5ac1-7493-9247-b7eb8608c746",
                "agent_path": "/root/code_review",
            },
        },
    }


def test_interaction_kind_by_tool():
    assert INTERACTION_KIND_BY_TOOL == {
        "collaboration__followup_task": "resume",
        "collaboration__send_message": "message",
        "collaboration__interrupt_agent": "stop",
    }


def test_parse_sub_agent_activity_all_four_kinds():
    for kind, event_id in (
        ("started", "call_a"),
        ("interacted", "call_b"),
        ("interrupted", "call_c"),
        ("completed", "subagent-completed-01a06b41-5aea-7d03-9671-ec86ad485656"),
    ):
        assert parse_sub_agent_activity(_activity_line(kind, event_id)) == SubAgentActivity(
            kind, event_id, "01a06b41-5ac1-7493-9247-b7eb8608c746", "/root/code_review"
        )


def test_parse_sub_agent_activity_rejects_other_shapes():
    assert parse_sub_agent_activity(_activity_line("unknown", "call_a")) is None
    assert parse_sub_agent_activity(_activity_line("started", "")) is None
    line = _activity_line("started", "call_a")
    line["payload"]["item"]["type"] = "AgentMessage"
    assert parse_sub_agent_activity(line) is None
    line = _activity_line("started", "call_a")
    del line["payload"]["item"]["agent_thread_id"]
    assert parse_sub_agent_activity(line) is None
    assert parse_sub_agent_activity({"type": "response_item", "payload": {}}) is None


def test_started_only_parser_keeps_rejecting_completed():
    from twicc.providers.codex.compute import _parse_sub_agent_activity_started

    assert _parse_sub_agent_activity_started(_activity_line("completed", "subagent-completed-x")) is None
    assert _parse_sub_agent_activity_started(_activity_line("started", "call_a")) is not None


def _event(payload: dict) -> dict:
    return {"timestamp": "2026-09-01T12:00:00.000Z", "ordinal": 5, "type": "event_msg", "payload": payload}


def test_owner_turn_abort_turn_id():
    assert owner_turn_abort_turn_id(_event({"type": "turn_aborted", "turn_id": "t1", "reason": "interrupted"})) == "t1"
    assert owner_turn_abort_turn_id(_event({"type": "turn_aborted", "turn_id": "t1", "reason": "replaced"})) is None
    assert owner_turn_abort_turn_id(_event({"type": "turn_aborted", "reason": "interrupted"})) is None
    usage = {
        "type": "task_complete",
        "turn_id": "t2",
        "last_agent_message": None,
        "error": {"message": "You've hit your usage limit.", "codex_error_info": "usage_limit_exceeded"},
    }
    assert owner_turn_abort_turn_id(_event(usage)) == "t2"
    for info in ("server_overloaded", "cyber_policy", "other"):
        other = {**usage, "error": {"message": "x", "codex_error_info": info}}
        assert owner_turn_abort_turn_id(_event(other)) is None
    assert owner_turn_abort_turn_id(_event({"type": "task_complete", "turn_id": "t3"})) is None
    assert owner_turn_abort_turn_id({"type": "response_item", "payload": {"type": "turn_aborted"}}) is None


def test_task_started_turn_id_and_is_task_complete():
    started = _event({"type": "task_started", "turn_id": "t1", "model_context_window": 258400})
    assert task_started_turn_id(started) == "t1"
    assert task_started_turn_id(_event({"type": "task_complete", "turn_id": "t1"})) is None
    assert task_started_turn_id(_event({"type": "task_started"})) is None
    assert is_task_complete(_event({"type": "task_complete", "turn_id": "t1"})) is True
    assert is_task_complete(_event({"type": "task_started", "turn_id": "t1"})) is False
    assert is_task_complete({"type": "response_item", "payload": {"type": "task_complete"}}) is False


def test_fork_fields():
    meta = {
        "timestamp": "2026-09-08T12:51:12.959Z",
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "id": "01a08112-91ba-7021-844f-ae173b49d3e7",
            "forked_from_id": "01a08017-3fa4-7b82-aeee-f417e8db093d",
            "subagent_history_start_ordinal": 53,
        },
    }
    assert fork_fields(meta) == ForkFields("01a08017-3fa4-7b82-aeee-f417e8db093d", 53)
    no_fork = {**meta, "payload": {"id": "x", "subagent_history_start_ordinal": 232}}
    assert fork_fields(no_fork) == ForkFields(None, 232)
    assert fork_fields(_event({"type": "task_started", "forked_from_id": "a"})) == ForkFields(None, None)
    assert fork_fields({**meta, "type": "turn_context"}) == ForkFields(None, None)


def test_line_ordinal():
    assert line_ordinal({"ordinal": 54, "type": "event_msg"}) == 54
    assert line_ordinal({"ordinal": 0}) == 0
    assert line_ordinal({"type": "event_msg"}) is None
    assert line_ordinal({"ordinal": True}) is None
    assert line_ordinal({"ordinal": "3"}) is None


def test_owner_abort_cut_runs_keeps_earlier_turns_and_ended_runs():
    """§5.2: only the file-open runs whose call follows the aborted turn's ``task_started``."""
    earlier = FileRun("earlier", 3, None, "spawn")  # previous turn: outlives the turn end
    at_start = FileRun("at_start", 10, None, "spawn")  # call line == started line: not after it
    resumed = FileRun("fu", 12, 13, "resume")
    spawned = FileRun("spawned", 15, None, "spawn")
    ended = FileRun("ended", 17, None, "spawn")
    ev = FileEvidence(
        runs=(earlier, at_start, resumed, spawned, ended),
        stops=(),
        completed_lines={"ended": (19,)},
        aborted_lines={},
        results={},
    )
    assert owner_abort_cut_runs(ev, 30, 10) == [resumed, spawned]
    # Only lines before the abort count: the end at line 19 does not exist at line 19.
    assert owner_abort_cut_runs(ev, 19, 10) == [resumed, spawned, ended]
    # A later turn start cuts only what follows it.
    assert owner_abort_cut_runs(ev, 30, 15) == []
    assert owner_abort_cut_runs(ev, 30, 2) == [earlier, at_start, resumed, spawned]


def test_owner_abort_cut_runs_skips_stopped_and_aborted_runs():
    rollout = _Rollout()
    rollout.spawn("stopped", 5, 6, _t(0))
    rollout.interrupt(8, 7)  # stopped in line order: result 7, event 8
    live = rollout.spawn("live", 9, 10, _t(1))
    assert owner_abort_cut_runs(rollout.evidence(), 20, 2) == [live]
    rollout.aborted.setdefault("live", []).append(15)  # already cut by an earlier abort
    assert owner_abort_cut_runs(rollout.evidence(), 20, 2) == []


def test_is_copied_history():
    forked = ForkFields("01a08017-3fa4-7b82-aeee-f417e8db093d", 54)
    assert is_copied_history(forked, {"ordinal": 53}) is True
    assert is_copied_history(forked, {"ordinal": 54}) is False
    assert is_copied_history(forked, {"type": "event_msg"}) is False  # no ordinal
    # Non-fork children carry the field with real turn ends below it: never gated.
    assert is_copied_history(ForkFields(None, 232), {"ordinal": 231}) is False
    assert is_copied_history(ForkFields("x", None), {"ordinal": 1}) is False
    assert is_copied_history(ForkFields(None, None), {"ordinal": 1}) is False
