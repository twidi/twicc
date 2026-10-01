"""Codex agent-run signals in the batch recompute (design §5.1, §5.2, §5.6, §6.2, §9).

Each scenario seeds the rollouts of a :mod:`tests.codex_agent_run_fixtures`
builder, recomputes every session through ``compute_session_metadata`` (the
``collect_agent_run_signals`` hook and the ``FINAL_ANSWER`` rebind of
``remap_tool_result_id``) and asserts the stored rows and the resulting
``agent_run_states``. The parity test replays the same builders live.
"""

from __future__ import annotations

from datetime import datetime
from queue import Queue

import orjson
import pytest

from twicc.core.agent_runs import agent_run_states
from twicc.core.enums import Provider
from twicc.core.models import (
    AgentInteraction,
    AgentLink,
    AgentRunEnd,
    Project,
    Session,
    SessionItem,
    SessionType,
    ToolResultLink,
)
from twicc.providers.codex.agent_runs import FileEvidence, FileRun, evidence_from_batch_state
from twicc.providers.codex.compute import CodexSessionCompute, _SpawnTarget, get_compute
from twicc.providers.compute_base import BatchAgentState

from tests.codex_agent_run_fixtures import (
    AGENT_A,
    AGENT_B,
    AGENT_C,
    AGENT_G,
    ALL_FIXTURES,
    INTERRUPT_AGENT_OUTPUT,
    OWNER_ABORT_CUTTING_KINDS,
    OWNER_ABORT_KINDS,
    PATH_A,
    PROJECT_ID,
    ROOT,
    CodexAgentRunFixture,
    at,
    fixture_child_turn_ends,
    fixture_completed_between_call_and_interacted,
    fixture_control_calls,
    fixture_duplicate_interacted,
    fixture_flat_timestamp_child,
    fixture_followup_after_interrupt,
    fixture_forked_child,
    fixture_idle_followup_opens_run,
    fixture_interrupt_race,
    fixture_long_gap_with_followup,
    fixture_merged_followup,
    fixture_merged_then_real_followups,
    fixture_non_fork_child_with_history_field,
    fixture_owner_abort,
    fixture_owner_abort_after_signal,
    fixture_owner_abort_repeated_turn_id,
    fixture_pre_completed_rollout,
    fixture_reaudit_limitation,
    fixture_same_time_ack_and_final_answer,
    fixture_stop_in_completed_final_gap,
    fixture_stop_result_before_event,
    fixture_subagent_owner_usage_limit,
    fixture_t20_sequence,
    fixture_v1_notification,
)

# A subagent file's mtime, far after every line: the rows must carry the line
# time, never this (the batch sets the child's ``last_stopped_at`` to it).
CHILD_MTIME = at(86_400).timestamp()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def recompute(session: Session) -> None:
    compute = get_compute()
    queue: Queue = Queue()
    compute.compute_session_metadata(session.id, queue, "codex-agent-runs-test")
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    msg = next(m for m in messages if m["type"] == "session_complete")
    assert compute.apply_session_complete(msg).outcome == "applied"


def play(fixture: CodexAgentRunFixture, upto: dict[str, str] | None = None) -> None:
    """Seed every session of ``fixture`` (optionally cut after a mark) and recompute each one.

    Calling it again for the same fixture re-seeds the sessions from scratch.
    """
    upto = upto or {}
    project, _ = Project.objects.get_or_create(id=PROJECT_ID)
    Session.objects.filter(project=project).delete()
    root = None
    sessions = []
    for fixture_session in fixture.sessions:
        session = Session.objects.create(
            id=fixture_session.session_id,
            project=project,
            provider=Provider.CODEX,
            file_path=f"{PROJECT_ID}/{fixture_session.session_id}.jsonl",
            type=SessionType.SUBAGENT if fixture_session.is_subagent else SessionType.SESSION,
            parent_session=root if fixture_session.is_subagent else None,
            mtime=CHILD_MTIME if fixture_session.is_subagent else 0,
        )
        if root is None:
            root = session
        lines = fixture_session.lines
        if fixture_session.session_id in upto:
            lines = lines[:fixture.line(fixture_session.session_id, upto[fixture_session.session_id])]
        SessionItem.objects.bulk_create(
            SessionItem(session=session, line_num=n, content=content) for n, content in enumerate(lines, 1)
        )
        sessions.append(session)
    for session in sessions:
        recompute(session)


def interactions(session_id: str = ROOT) -> dict[str, dict]:
    return {
        row["tool_use_id"]: row
        for row in AgentInteraction.objects.filter(session_id=session_id).values(
            "tool_use_id", "kind", "agent_id", "opens_run", "tool_use_line_num", "event_line_num", "started_at",
        )
    }


def run_ends(session_id: str = ROOT) -> list[tuple]:
    return list(
        AgentRunEnd.objects.filter(session_id=session_id).order_by("line_num", "tool_use_id").values_list(
            "line_num", "tool_use_id", "agent_id", "status", "ended_at",
        )
    )


def result_lines(tool_use_id: str, session_id: str = ROOT) -> list[int]:
    return sorted(ToolResultLink.objects.filter(session_id=session_id, tool_use_id=tool_use_id).values_list(
        "tool_result_line_num", flat=True,
    ))


def state(agent_id: str = AGENT_A):
    return agent_run_states(Session.objects.get(id=ROOT), [agent_id])[agent_id]


def closed_at(agent_id: str = AGENT_A) -> dict[str, datetime | None]:
    return {run.tool_use_id: run.closed_at for run in state(agent_id).runs}


def mark(fixture: CodexAgentRunFixture, name: str, session_id: str = ROOT) -> int:
    return fixture.line(session_id, name)


def ack_line(fixture: CodexAgentRunFixture, spawn_mark: str = "spawn", session_id: str = ROOT) -> int:
    """A v2 spawn is three lines: call, ``started``, ack."""
    return mark(fixture, spawn_mark, session_id) + 2


def completed_end(fixture, name, tool_use_id, seconds, agent_id=AGENT_A):
    return (mark(fixture, name), tool_use_id, agent_id, "completed", at(seconds))


# ---------------------------------------------------------------------------
# Runs and attribution
# ---------------------------------------------------------------------------


def test_idle_followup_opens_its_own_run(db):
    fx = fixture_idle_followup_opens_run()
    play(fx, upto={ROOT: "fu_output"})
    current = state()
    assert current.running and current.run_started_at == at(13.1)

    play(fx)
    assert interactions() == {"c_fu": {
        "tool_use_id": "c_fu", "kind": "resume", "agent_id": AGENT_A, "opens_run": True,
        "tool_use_line_num": mark(fx, "fu"), "event_line_num": mark(fx, "fu_event"), "started_at": at(13.1),
    }}
    assert run_ends() == [
        completed_end(fx, "completed_1", "c_spawn", 11),
        completed_end(fx, "completed_2", "c_fu", 20),
    ]
    # ``completed`` creates no ToolResultLink: ack + FINAL_ANSWER only.
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_1")]
    assert result_lines("c_fu") == [mark(fx, "fu_output"), mark(fx, "final_2")]
    current = state()
    assert not current.running and current.stopped_at == at(20)
    assert closed_at() == {"c_spawn": at(11), "c_fu": at(20)}


def test_merged_followup_keeps_the_pair_on_the_spawn(db):
    fx = fixture_merged_followup()
    play(fx, upto={ROOT: "fu_output"})
    assert interactions()["c_fu"]["opens_run"] is False
    assert state().running and [run.tool_use_id for run in state().runs] == ["c_spawn"]

    play(fx)
    assert interactions()["c_fu"]["opens_run"] is False
    assert run_ends() == [completed_end(fx, "completed", "c_spawn", 8)]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final")]
    assert result_lines("c_fu") == [mark(fx, "fu_output")]
    current = state()
    assert not current.running and current.stopped_at == at(8)


def test_reaudit_limitation_lands_the_second_pair_on_the_spawn(db):
    fx = fixture_reaudit_limitation()
    # Documented limitation (§5.6): not running between the two runs.
    play(fx, upto={ROOT: "final_1"})
    assert not state().running

    play(fx)
    assert run_ends() == [
        completed_end(fx, "completed_1", "c_spawn", 8),
        completed_end(fx, "completed_2", "c_spawn", 146),
    ]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_1"), mark(fx, "final_2")]
    current = state()
    assert not current.running and current.stopped_at == at(8)


@pytest.mark.parametrize("kind", OWNER_ABORT_KINDS)
def test_owner_turn_abort(db, kind):
    fx = fixture_owner_abort(kind)
    abort = mark(fx, "abort")
    cuts = kind in OWNER_ABORT_CUTTING_KINDS

    play(fx, upto={ROOT: "abort"})
    assert not state(AGENT_B).running if cuts else state(AGENT_B).running
    assert state(AGENT_C).running

    play(fx)
    aborted = [end for end in run_ends() if end[3] == "owner_turn_aborted"]
    if not cuts:
        # Another reason, another error kind, or no ``task_started`` for the turn: nothing.
        assert aborted == []
        assert interactions()["c_fa2"]["opens_run"] is False  # c_fa1 is still file-open
        assert completed_end(fx, "completed_a3", "c_fa1", 50) in run_ends()
        assert result_lines("c_fa1") == [mark(fx, "fa1_output"), mark(fx, "final_a3")]
        assert state(AGENT_B).running
        assert state(AGENT_C).running
        return
    # One row per file-open run opened in the aborted turn: A's resume, B's
    # spawn; C's spawn (turn t1) is not cut.
    assert aborted == [
        (abort, "c_b", AGENT_B, "owner_turn_aborted", at(30)),
        (abort, "c_fa1", AGENT_A, "owner_turn_aborted", at(30)),
    ]
    # The cut run is no candidate: the next follow-up opens its own run and
    # gets its own signals.
    assert interactions()["c_fa2"]["opens_run"] is True
    assert completed_end(fx, "completed_a3", "c_fa2", 50) in run_ends()
    assert result_lines("c_fa1") == [mark(fx, "fa1_output")]
    assert result_lines("c_fa2") == [mark(fx, "fa2_output"), mark(fx, "final_a3")]
    assert closed_at(AGENT_A) == {"c_a": at(8), "c_fa1": at(30), "c_fa2": at(50)}
    current = state(AGENT_B)
    assert not current.running and current.stopped_at == at(30)
    assert state(AGENT_C).running


def test_owner_abort_cuts_from_the_newest_start_of_its_turn(db):
    fx = fixture_owner_abort_repeated_turn_id()
    play(fx)
    # The abort's turn started twice: only the run opened after the newest
    # ``task_started`` (B) is cut; A, between the two starts, keeps running.
    assert run_ends() == [(mark(fx, "abort"), "c_b", AGENT_B, "owner_turn_aborted", at(20))]
    assert state(AGENT_A).running
    current = state(AGENT_B)
    assert not current.running and current.stopped_at == at(20)


def test_subagent_owner_usage_limit_writes_both_rows(db):
    fx = fixture_subagent_owner_usage_limit()
    play(fx)
    line = mark(fx, "usage_limit", AGENT_A)
    assert run_ends(AGENT_A) == [
        (line, "", AGENT_A, "turn_complete", at(9)),
        (line, "c_g", AGENT_G, "owner_turn_aborted", at(9)),
    ]
    # Messages to the owner itself and to the root write no row.
    assert interactions(AGENT_A) == {}
    assert not state(AGENT_G).running and state(AGENT_G).stopped_at == at(9)
    assert not state(AGENT_A).running


def test_control_calls(db):
    fx = fixture_control_calls()
    play(fx, upto={ROOT: "to_root_output"})
    assert state().running

    play(fx)
    rows = interactions()
    # send_message → a message row (no run); interrupt_agent → a stop row.
    # No row for an event with no call, ``wait_agent``, a v1 name, or the root.
    assert set(rows) == {"c_msg", "c_stop"}
    assert (rows["c_msg"]["kind"], rows["c_msg"]["opens_run"], rows["c_msg"]["event_line_num"]) == \
        ("message", False, mark(fx, "msg_event"))
    assert (rows["c_stop"]["kind"], rows["c_stop"]["tool_use_line_num"], rows["c_stop"]["event_line_num"]) == \
        ("stop", mark(fx, "stop"), mark(fx, "stop_event"))
    assert run_ends() == []
    current = state()
    assert not current.running and current.stopped_at == at(12.2)
    assert [run.tool_use_id for run in current.runs] == ["c_spawn"]


# ---------------------------------------------------------------------------
# Child turn ends
# ---------------------------------------------------------------------------


def test_child_turn_ends_at_the_line_time(db):
    fx = fixture_child_turn_ends()
    play(fx, upto={AGENT_A: "c2_started"})
    assert interactions()["c_fu"]["opens_run"] is True
    current = state()
    assert current.running and current.run_started_at == at(51.1)

    play(fx)
    assert run_ends(AGENT_A) == [
        (mark(fx, "end_1", AGENT_A), "", AGENT_A, "turn_complete", at(30)),
        (mark(fx, "end_2", AGENT_A), "", AGENT_A, "turn_complete", at(90)),
    ]
    assert Session.objects.get(id=AGENT_A).last_stopped_at == datetime.fromtimestamp(CHILD_MTIME, at(0).tzinfo)
    assert closed_at() == {"c_spawn": at(30), "c_fu": at(90)}
    assert state().stopped_at == at(90)


def test_forked_child_skips_copied_turn_ends(db):
    fx = fixture_forked_child()
    play(fx, upto={AGENT_A: "own_started"})
    assert run_ends(AGENT_A) == []
    assert state().running

    play(fx)
    assert run_ends(AGENT_A) == [(mark(fx, "own_end", AGENT_A), "", AGENT_A, "turn_complete", at(40))]
    current = state()
    assert not current.running and current.stopped_at == at(40)


def test_non_fork_child_with_history_field_still_writes(db):
    fx = fixture_non_fork_child_with_history_field()
    play(fx)
    assert run_ends(AGENT_A) == [(mark(fx, "end", AGENT_A), "", AGENT_A, "turn_complete", at(30))]
    assert not state().running


def test_root_task_complete_writes_no_turn_end(db):
    play(fixture_idle_followup_opens_run())
    assert not any(end[3] == "turn_complete" for end in run_ends())


def test_v1_notification_still_rebinds_to_its_spawn(db):
    fx = fixture_v1_notification()
    play(fx)
    assert AgentLink.objects.filter(session_id=ROOT, tool_use_id="c_v1_spawn", agent_id=AGENT_A).exists()
    assert result_lines("c_v1_spawn") == [mark(fx, "ack"), mark(fx, "notification")]
    assert interactions() == {}
    assert run_ends() == []


# ---------------------------------------------------------------------------
# Attribution sequences (replayed live by the parity test)
# ---------------------------------------------------------------------------


def test_t20_sequence(db):
    fx = fixture_t20_sequence()
    play(fx)
    rows = interactions()
    assert rows["c_fu1"]["opens_run"] and rows["c_fu2"]["opens_run"]
    assert run_ends() == [
        completed_end(fx, "completed_1", "c_fu1", 110),
        completed_end(fx, "completed_2", "c_fu2", 120),
    ]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu1") == [mark(fx, "fu1_output"), mark(fx, "final_1")]
    assert result_lines("c_fu2") == [mark(fx, "fu2_output"), mark(fx, "final_2")]
    # The child's turn end closes the spawn run, not its late FINAL_ANSWER.
    assert closed_at() == {"c_spawn": at(50), "c_fu1": at(110), "c_fu2": at(120)}


def test_completed_between_call_and_interacted(db):
    fx = fixture_completed_between_call_and_interacted()
    play(fx, upto={ROOT: "final_0", AGENT_A: "end_1"})
    row = interactions()["c_fu"]
    assert row["opens_run"] is True and row["started_at"] == at(12)
    assert (row["tool_use_line_num"], row["event_line_num"]) == (mark(fx, "fu"), mark(fx, "fu_event"))
    # The previous run's completed (and its child turn end) do not close the follow-up.
    assert state().running and state().run_started_at == at(12)

    play(fx)
    assert run_ends() == [
        completed_end(fx, "completed_0", "c_spawn", 11),
        completed_end(fx, "completed_1", "c_fu", 20),
    ]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu") == [mark(fx, "fu_output"), mark(fx, "final_1")]
    assert closed_at() == {"c_spawn": at(11), "c_fu": at(19.5)}


def test_stop_in_completed_final_gap(db):
    fx = fixture_stop_in_completed_final_gap()
    play(fx)
    rows = interactions()
    assert rows["c_stop"]["kind"] == "stop" and rows["c_fu"]["opens_run"] is True
    assert run_ends() == [completed_end(fx, "completed_0", "c_spawn", 10)]
    # S0 keeps its candidacy for its own late FINAL_ANSWER; F1 stays open.
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu") == [mark(fx, "fu_output")]
    current = state()
    assert current.running and current.run_started_at == at(14.1)


def test_merged_then_real_followups(db):
    fx = fixture_merged_then_real_followups()
    play(fx)
    rows = interactions()
    assert [rows[tool]["opens_run"] for tool in ("c_fu0", "c_fu1", "c_fu2")] == [False, True, True]
    assert run_ends() == [
        completed_end(fx, "completed_0", "c_spawn", 8),
        completed_end(fx, "completed_1", "c_fu1", 15),
        completed_end(fx, "completed_2", "c_fu2", 25),
    ]
    assert result_lines("c_fu0") == [mark(fx, "fu0_output")]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu1") == [mark(fx, "fu1_output"), mark(fx, "final_1")]
    assert result_lines("c_fu2") == [mark(fx, "fu2_output"), mark(fx, "final_2")]
    assert not state().running


def test_followup_after_interrupt(db):
    fx = fixture_followup_after_interrupt()
    play(fx, upto={ROOT: "fu_output"})
    assert interactions()["c_fu"]["opens_run"] is True
    assert state().running

    play(fx)
    assert run_ends() == [completed_end(fx, "completed", "c_fu", 20)]
    assert result_lines("c_spawn") == [ack_line(fx)]
    assert result_lines("c_fu") == [mark(fx, "fu_output"), mark(fx, "final")]
    current = state()
    assert not current.running and current.stopped_at == at(20)


def test_long_gap_with_followup(db):
    fx = fixture_long_gap_with_followup()
    play(fx)
    assert interactions()["c_fu"]["opens_run"] is True
    assert run_ends() == [
        completed_end(fx, "completed_0", "c_spawn", 10),
        completed_end(fx, "completed_1", "c_fu", 610),
    ]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu") == [mark(fx, "fu_output"), mark(fx, "final_1")]


def test_owner_abort_after_signal(db):
    fx = fixture_owner_abort_after_signal()
    play(fx)
    rows = interactions()
    # The merged decision at its line stands; the later abort cuts the spawn.
    assert rows["c_fu0"]["opens_run"] is False and rows["c_fu1"]["opens_run"] is True
    assert run_ends() == [
        (mark(fx, "abort"), "c_spawn", AGENT_A, "owner_turn_aborted", at(10)),
        completed_end(fx, "completed_1", "c_fu1", 30),
    ]
    assert result_lines("c_fu1") == [mark(fx, "fu1_output"), mark(fx, "final_1")]


def test_stop_result_before_its_event(db):
    fx = fixture_stop_result_before_event()
    play(fx)
    row = interactions()["c_stop"]
    assert (row["kind"], row["tool_use_line_num"], row["event_line_num"]) == \
        ("stop", mark(fx, "stop"), mark(fx, "stop_event"))
    # The completed between the result and the event still finds the spawn.
    assert run_ends() == [completed_end(fx, "completed", "c_spawn", 12)]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final")]
    assert not state().running


def test_interrupt_race(db):
    fx = fixture_interrupt_race()
    play(fx)
    assert run_ends() == [completed_end(fx, "completed", "c_spawn", 11)]
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final")]
    assert result_lines("c_stop") == [mark(fx, "stop_output")]
    assert not state().running


def test_duplicate_interacted_first_line_wins(db):
    fx = fixture_duplicate_interacted()
    play(fx)
    row = interactions()["c_fu"]
    assert (row["event_line_num"], row["started_at"], row["opens_run"]) == (mark(fx, "fu_event"), at(10.1), True)


def test_same_time_ack_and_final_answer_count_one_result(db):
    fx = fixture_same_time_ack_and_final_answer()
    play(fx)
    assert result_lines("c_spawn") == [mark(fx, "ack"), mark(fx, "final")]
    # One distinct time: no FINAL_ANSWER yet, the spawn is file-open, the follow-up merges.
    assert interactions()["c_fu"]["opens_run"] is False
    assert state().running


# ---------------------------------------------------------------------------
# Old shapes
# ---------------------------------------------------------------------------


def test_pre_completed_rollout(db):
    fx = fixture_pre_completed_rollout()
    play(fx)
    assert interactions()["c_fu"]["opens_run"] is True
    assert run_ends() == []
    assert result_lines("c_spawn") == [ack_line(fx), mark(fx, "final_0")]
    assert result_lines("c_fu") == [mark(fx, "fu_output"), mark(fx, "final_1")]
    assert closed_at() == {"c_spawn": at(9), "c_fu": at(20)}


def test_flat_timestamp_child(db):
    fx = fixture_flat_timestamp_child()
    play(fx)
    # §10: the child's turn end carries its thread creation time.
    assert run_ends(AGENT_A) == [(mark(fx, "end", AGENT_A), "", AGENT_A, "turn_complete", at(2.1))]
    assert closed_at() == {"c_spawn": at(2.1)}


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_final_answer_without_batch_state_keeps_the_spawn_map():
    compute = CodexSessionCompute()
    compute.begin_session_compute("s")
    compute._spawn_target_map("s")[PATH_A] = _SpawnTarget("c_spawn", AGENT_A)
    parsed = orjson.loads(fixture_idle_followup_opens_run().session(ROOT).lines[
        fixture_idle_followup_opens_run().line(ROOT, "final_2") - 1])
    assert compute.remap_tool_result_id(parsed, PATH_A, session_id="s", tool_use_map={}) == "c_spawn"


def test_evidence_from_batch_state():
    iso = at(1).isoformat()
    link = {"session_id": "s", "tool_use_line_num": 3, "tool_use_id": "c_spawn", "agent_id": AGENT_A,
            "is_background": True, "started_at": iso}

    def result(tool, line, seconds, error=None):
        return {"session_id": "s", "tool_use_line_num": 1, "tool_result_line_num": line, "tool_use_id": tool,
                "tool_name": "x", "tool_result_at": at(seconds).isoformat(), "extra": None, "error": error}

    def interaction(tool, kind, call, event, opens_run, agent=AGENT_A):
        return {"session_id": "s", "tool_use_line_num": call, "event_line_num": event, "tool_use_id": tool,
                "agent_id": agent, "kind": kind, "opens_run": opens_run, "started_at": iso}

    def end(line, tool, status, agent=AGENT_A):
        return {"session_id": "s", "line_num": line, "tool_use_id": tool, "agent_id": agent, "ended_at": iso,
                "status": status}

    results = {
        "c_spawn": [result("c_spawn", 5, 5), result("c_spawn", 9, 9)],
        "c_fu": [result("c_fu", 12, 12)],
        "c_stop": [result("c_stop", 14, 14, error="failed"), result("c_stop", 16, 16)],
    }
    state_ = BatchAgentState(
        session_id="s", root_session_id="s", session_type=SessionType.SESSION, tool_use_map={},
        all_tool_result_links={}, results_by_tool_use=results,
        all_agent_links={(AGENT_A, "c_spawn"): link, (AGENT_B, "c_b"): {**link, "tool_use_id": "c_b",
                                                                          "agent_id": AGENT_B}},
        all_agent_interactions={
            "c_fu": interaction("c_fu", "resume", 10, 11, True),
            "c_merged": interaction("c_merged", "resume", 17, 18, False),
            "c_msg": interaction("c_msg", "message", 19, 20, False),
            "c_stop": interaction("c_stop", "stop", 13, 15, False),
            "c_stop_b": interaction("c_stop_b", "stop", 21, 22, False, agent=AGENT_B),
        },
        all_agent_run_ends={
            (8, "c_spawn"): end(8, "c_spawn", "completed"),
            (30, "c_fu"): end(30, "c_fu", "owner_turn_aborted"),
            (31, ""): end(31, "", "turn_complete"),
            (32, "c_b"): end(32, "c_b", "completed", agent=AGENT_B),
        },
    )
    assert evidence_from_batch_state(state_, "s", AGENT_A) == FileEvidence(
        runs=(FileRun("c_spawn", 3, None, "spawn"), FileRun("c_fu", 10, 11, "resume")),
        stops=((15, 16),),
        completed_lines={"c_spawn": (8,)},
        aborted_lines={"c_fu": (30,)},
        results={"c_spawn": ((5, at(5)), (9, at(9))), "c_fu": ((12, at(12)),)},
    )


def test_fixture_control_outputs_have_the_real_shapes():
    """08-11 rollout line 189: ``followup_task`` / ``send_message`` ack with ``""``; ``interrupt_agent`` names the prior status."""
    expected = {"followup_task": "", "send_message": "", "interrupt_agent": INTERRUPT_AGENT_OUTPUT}
    checked = set()
    for build in ALL_FIXTURES.values():
        for fixture_session in build().sessions:
            names = {}
            for raw in fixture_session.lines:
                payload = orjson.loads(raw)["payload"]
                if payload.get("type") == "function_call":
                    names[payload["call_id"]] = payload["name"]
                elif payload.get("type") == "function_call_output" and names.get(payload["call_id"]) in expected:
                    name = names[payload["call_id"]]
                    assert payload["output"] == expected[name], (build.__name__, payload["call_id"])
                    checked.add(name)
    assert checked == set(expected)
