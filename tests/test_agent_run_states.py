"""The one run model: ``agent_run_states`` (design §5.4, §5.5).

Every row is hand-built through the ORM; no transcript is parsed. The later
tasks (stop step, Codex live process, snapshot) read this helper's output,
so these tests are the oracle for them.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from twicc.core.agent_runs import (
    RunStateExclude,
    agent_run_states,
    serialize_run_state,
    serialize_runs,
)
from twicc.core.models import (
    AgentInteraction,
    AgentInteractionKind,
    AgentLink,
    AgentRunEnd,
    AgentRunEndSource,
    Project,
    Session,
    SessionItem,
    SessionType,
    ToolResultLink,
)

ROOT_ID = "root-1"


def t(minute, second=0, ms=0):
    """A fixed UTC time on the test day."""
    return datetime(2026, 9, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=minute, seconds=second, milliseconds=ms)


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-agent-run-states", directory="/tmp/agent-run-states")


def make_session(project, session_id, *, parent=None, provider="claude_code", **extra):
    return Session.objects.create(
        id=session_id, project=project, provider=provider, file_path=f"{session_id}.jsonl",
        type=SessionType.SUBAGENT if parent else SessionType.SESSION,
        parent_session=parent, **extra,
    )


@pytest.fixture
def root(project):
    return make_session(project, ROOT_ID)


@pytest.fixture
def codex_root(project):
    return make_session(project, "codex-root", provider="codex")


def child(root, agent_id):
    return make_session(root.project, agent_id, parent=root, provider=root.provider)


def link(owner, agent_id, tool_use_id, *, line=1, background=False, started_at=None):
    return AgentLink.objects.create(
        session=owner, tool_use_line_num=line, tool_use_id=tool_use_id, agent_id=agent_id,
        is_background=background, started_at=started_at,
    )


def interaction(owner, agent_id, tool_use_id, *, kind=AgentInteractionKind.MESSAGE, line=1,
                event_line=None, opens_run=False, started_at=None):
    return AgentInteraction.objects.create(
        session=owner, tool_use_line_num=line, event_line_num=event_line if event_line is not None else line,
        tool_use_id=tool_use_id, agent_id=agent_id, kind=kind, opens_run=opens_run, started_at=started_at,
    )


def result(owner, tool_use_id, *, line, at, use_line=1, error=None):
    return ToolResultLink.objects.create(
        session=owner, tool_use_line_num=use_line, tool_result_line_num=line, tool_use_id=tool_use_id,
        tool_result_at=at, error=error,
    )


def end(session, agent_id, *, tool_use_id="", line=None, at=None, status="completed",
        source=AgentRunEndSource.TRANSCRIPT):
    return AgentRunEnd.objects.create(
        session=session, line_num=line, source=source, agent_id=agent_id, tool_use_id=tool_use_id,
        ended_at=at, status=status,
    )


def ui_stop(root, agent_id, at):
    return end(root, agent_id, at=at, status="ui_stopped", source=AgentRunEndSource.UI)


def state_of(root, agent_id, **kwargs):
    return agent_run_states(root, [agent_id], **kwargs)[agent_id]


def item(session, line, at):
    return SessionItem.objects.create(session=session, line_num=line, content="{}", timestamp=at)


# --- Rule 1: result counts -------------------------------------------------


def test_foreground_spawn_closes_at_one_result(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    assert state_of(root, "a1").running is True
    result(root, "tu-spawn", line=5, at=t(3))
    state = state_of(root, "a1")
    assert state.known is True
    assert state.running is False
    assert state.runs[0].closed_at == t(3)
    assert state.stopped_at == t(3)


def test_background_spawn_needs_two_distinct_results(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    result(root, "tu-spawn", line=5, at=t(1))
    state = state_of(root, "a1")
    assert state.running is True
    assert state.run_background is True
    assert state.run_started_at == t(0)
    result(root, "tu-spawn", line=9, at=t(4))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.runs[0].closed_at == t(4)


def test_compaction_copy_of_the_ack_does_not_reach_the_count(root):
    # The aa6d89b7469be1a2c shape: the ack copied at two lines, same timestamp.
    link(root, "aa6d89b7469be1a2c", "tu-spawn", background=True, started_at=t(0))
    result(root, "tu-spawn", line=10228, at=t(1, ms=546))
    result(root, "tu-spawn", line=13306, at=t(1, ms=546))
    assert state_of(root, "aa6d89b7469be1a2c").running is True


def test_interaction_run_needs_two_results(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-msg", line=11, at=t(5))
    state = state_of(root, "a1")
    assert state.running is True
    assert state.run_started_at == t(5)
    assert state.run_background is True
    result(root, "tu-msg", line=20, at=t(8))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(8)


def test_interaction_without_opens_run_is_not_a_run(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=False, started_at=t(5))
    state = state_of(root, "a1")
    assert state.running is False
    assert len(state.runs) == 1


def test_two_open_runs_closing_one_keeps_running(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-msg", line=11, at=t(5))
    assert state_of(root, "a1").running is True
    result(root, "tu-spawn", line=12, at=t(6))  # spawn closes
    state = state_of(root, "a1")
    assert state.running is True
    assert [run.open for run in state.runs] == [False, True]
    assert state.stopped_at == t(6)


# --- Rule 2: explicit stops ------------------------------------------------


def _stop(owner, agent_id, tool_use_id, *, line, at, result_line=None, error=None):
    interaction(owner, agent_id, tool_use_id, kind=AgentInteractionKind.STOP, line=line)
    result(owner, tool_use_id, line=result_line or line + 1, at=at, error=error)


def test_stop_after_start_closes(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    _stop(root, "a1", "tu-stop", line=20, at=t(4))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.runs[0].closed_at == t(4)


def test_stop_before_start_does_not_close(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    _stop(root, "a1", "tu-stop", line=4, at=t(2))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    state = state_of(root, "a1")
    assert state.running is True
    assert [run.tool_use_id for run in state.runs if run.open] == ["tu-msg"]


def test_error_stop_is_not_a_stop_record(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    _stop(root, "a1", "tu-stop", line=20, at=t(4), error="no such task")
    assert state_of(root, "a1").running is True


def test_null_started_at_is_closed_by_any_stop(root):
    link(root, "a1", "tu-spawn", background=True, started_at=None)
    _stop(root, "a1", "tu-stop", line=20, at=t(4))
    assert state_of(root, "a1").running is False


def test_stop_with_null_time_closes_only_null_started_runs(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    _stop(root, "a1", "tu-stop", line=20, at=None)
    assert state_of(root, "a1").running is True
    link(root, "a2", "tu-spawn2", background=True, started_at=None)
    _stop(root, "a2", "tu-stop2", line=30, at=None)
    state = state_of(root, "a2")
    assert state.running is False
    assert state.runs[0].closed_at is None
    assert state.stopped_at is None


def test_tie_break_stop_result_line_first_keeps_resumed_run_open(root):
    # TaskStop(A) then SendMessage(to=A) in one message, both results at the same ms.
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=10)
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-stop", line=11, at=t(5))
    result(root, "tu-msg", line=12, at=t(5))
    state = state_of(root, "a1")
    assert state.running is True


def test_tie_break_stop_on_a_later_line_closes(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=10)
    result(root, "tu-msg", line=11, at=t(5))
    result(root, "tu-stop", line=12, at=t(5))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(5)


def test_tie_break_reads_non_error_results_only(root):
    # An error result before the run's opening line does not make the stop "first".
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=10)
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-stop", line=11, at=t(5), error="busy")
    result(root, "tu-msg", line=12, at=t(5))
    result(root, "tu-stop", line=13, at=t(5))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(5)


def test_tie_break_codex_uses_event_line(codex_root):
    link(codex_root, "a1", "call-spawn", started_at=t(0))
    result(codex_root, "call-spawn", line=3, at=t(1))
    interaction(codex_root, "a1", "call-stop", kind=AgentInteractionKind.STOP, line=10, event_line=12)
    result(codex_root, "call-stop", line=11, at=t(5))
    interaction(codex_root, "a1", "call-follow", kind=AgentInteractionKind.RESUME, line=13, event_line=14,
                opens_run=True, started_at=t(5))
    assert state_of(codex_root, "a1").running is True


def test_tie_break_does_not_apply_across_owner_files(root):
    sub = child(root, "sub")
    link(root, "sub", "tu-sub", started_at=t(0))
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=10)
    result(root, "tu-stop", line=11, at=t(5))
    interaction(sub, "a1", "tu-msg", line=50, opens_run=True, started_at=t(5))
    result(sub, "tu-msg", line=51, at=t(5))
    assert state_of(root, "a1").running is False


def test_ui_stop_at_equal_time_closes(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-msg", line=11, at=t(5))
    ui_stop(root, "a1", t(5))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(5)


def test_ui_stop_before_start_does_not_close(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(5))
    ui_stop(root, "a1", t(4))
    assert state_of(root, "a1").running is True


def test_ui_stop_on_another_session_is_ignored(root, project):
    other_root = make_session(project, "other-root")
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    ui_stop(other_root, "a1", t(4))
    assert state_of(root, "a1").running is True


def test_spawn_run_at_equal_time_closes(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(5), line=10)
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=4)
    result(root, "tu-stop", line=5, at=t(5))
    assert state_of(root, "a1").running is False


# --- Rule 3: root cutoff -----------------------------------------------------


def test_cutoff_closes_runs_started_before_it(root):
    root.last_stopped_at = t(10)
    root.save(update_fields=["last_stopped_at"])
    link(root, "a1", "tu-before", background=True, started_at=t(5))
    link(root, "a2", "tu-null", background=True, started_at=None)
    link(root, "a3", "tu-after", background=True, started_at=t(12))
    states = agent_run_states(root, ["a1", "a2", "a3"])
    assert states["a1"].running is False
    assert states["a1"].stopped_at == t(10)
    assert states["a2"].running is False
    assert states["a2"].runs[0].closed_at == t(10)
    assert states["a3"].running is True


# --- Rule 4: end rows matching the run's call --------------------------------


def test_end_row_in_root_closes_run_owned_by_subagent(root):
    sub = child(root, "sub")
    link(root, "sub", "tu-sub", background=True, started_at=t(0))
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(sub, "a1", "toolu_01USQDKRXhX9zuv97Uxq46U2", line=20, opens_run=True, started_at=t(5))
    result(sub, "toolu_01USQDKRXhX9zuv97Uxq46U2", line=21, at=t(5))
    assert state_of(root, "a1").running is True
    end(root, "a1", tool_use_id="toolu_01USQDKRXhX9zuv97Uxq46U2", line=40, at=t(9), status="completed")
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(9)


def test_end_row_for_another_call_does_not_close(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    end(root, "a1", tool_use_id="tu-other", line=40, at=t(9))
    assert state_of(root, "a1").running is True


# --- Rule 5: agent-level turn ends -------------------------------------------


@pytest.mark.parametrize("status", ["turn_complete", "interrupted"])
def test_agent_level_end_after_start_closes(root, status):
    agent = child(root, "a1")
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    end(agent, "a1", line=30, at=t(4), status=status)
    state = state_of(root, "a1")
    assert state.running is False
    assert state.stopped_at == t(4)


@pytest.mark.parametrize("status", ["turn_complete", "interrupted"])
def test_agent_level_end_before_start_does_not_close(root, status):
    # A child task_complete before a followup_task does not close the follow-up run.
    agent = child(root, "a1")
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    end(agent, "a1", line=30, at=t(4), status=status)
    interaction(root, "a1", "tu-follow", line=50, opens_run=True, started_at=t(6))
    state = state_of(root, "a1")
    assert state.running is True
    assert [run.open for run in state.runs] == [False, True]


def test_agent_level_end_with_null_times_is_ignored(root):
    agent = child(root, "a1")
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    end(agent, "a1", line=30, at=None, status="turn_complete")
    assert state_of(root, "a1").running is True
    link(root, "a2", "tu-spawn2", background=True, started_at=None)
    end(agent, "a2", line=31, at=t(4), status="turn_complete")
    assert state_of(root, "a2").running is True


def test_agent_level_end_with_other_status_is_ignored(root):
    agent = child(root, "a1")
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    end(agent, "a1", line=30, at=t(4), status="completed")
    assert state_of(root, "a1").running is True


# --- closed_at --------------------------------------------------------------


def test_closed_at_with_mixed_evidence_uses_the_timed_piece(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    end(root, "a1", tool_use_id="tu-spawn", line=40, at=None)
    result(root, "tu-spawn", line=41, at=t(7))
    state = state_of(root, "a1")
    assert state.running is False
    assert state.runs[0].closed_at == t(7)


def test_closed_at_is_null_when_every_piece_is_null(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    end(root, "a1", tool_use_id="tu-spawn", line=40, at=None)
    state = state_of(root, "a1")
    assert state.running is False
    assert state.runs[0].closed_at is None
    assert state.stopped_at is None


def test_earliest_evidence_wins_t20_shape(codex_root):
    # Closed by the child's turn end at 03:50:03Z; the FINAL_ANSWER at 06:32:24Z does not move it.
    agent = child(codex_root, "t20")
    started = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    turn_end = datetime(2026, 9, 1, 3, 50, 3, tzinfo=UTC)
    final_answer = datetime(2026, 9, 1, 6, 32, 24, tzinfo=UTC)
    link(codex_root, "t20", "call-spawn", line=76275, background=True, started_at=started)
    result(codex_root, "call-spawn", line=76277, at=started)
    end(agent, "t20", line=90, at=turn_end, status="turn_complete")
    result(codex_root, "call-spawn", line=76417, at=final_answer)
    state = state_of(codex_root, "t20")
    assert state.running is False
    assert state.runs[0].closed_at == turn_end
    assert state.stopped_at == turn_end


def test_stopped_at_is_the_max_over_closed_runs(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(2))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-msg", line=11, at=t(5))
    result(root, "tu-msg", line=12, at=t(9))
    assert state_of(root, "a1").stopped_at == t(9)


# --- Tree rule -------------------------------------------------------------


def test_target_without_link_is_unknown(root):
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    state = state_of(root, "a1")
    assert state.known is False
    assert state.running is False
    assert state.runs == ()


def test_interaction_written_before_its_link_counts_once_the_link_exists(root):
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    assert state_of(root, "a1").known is False
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    state = state_of(root, "a1")
    assert state.known is True
    assert state.running is True
    assert [run.tool_use_id for run in state.runs] == ["tu-spawn", "tu-msg"]


def test_owner_outside_the_tree_opens_and_stops_nothing(root, project):
    fork_root = make_session(project, "fork-root")
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(fork_root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    assert state_of(root, "a1").running is False
    link(root, "a2", "tu-spawn2", background=True, started_at=t(0))
    _stop(fork_root, "a2", "tu-stop", line=20, at=t(4))
    assert state_of(root, "a2").running is True


def test_link_owned_outside_the_tree_is_ignored(root, project):
    other_root = make_session(project, "other-root")
    link(other_root, "a1", "tu-spawn", started_at=t(0))
    assert state_of(root, "a1").known is False


def test_shell_task_id_end_row_is_unknown(root):
    end(root, "bash-task-1", tool_use_id="tu-bash", line=40, at=t(4), status="completed")
    states = agent_run_states(root, ["bash-task-1", "main"])
    assert states["bash-task-1"].known is False
    assert states["main"].known is False


def test_every_requested_id_has_an_entry(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    states = agent_run_states(root, ["a1", "unknown", ROOT_ID])
    assert set(states) == {"a1", "unknown", ROOT_ID}
    assert states["unknown"].known is False
    assert states[ROOT_ID].known is False
    assert states["unknown"].run_started_at is None
    assert states["unknown"].run_background is None
    assert states["unknown"].stopped_at is None


# --- Exclude -----------------------------------------------------------------


def test_exclude_created_link_hides_its_spawn_run(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    state = state_of(root, "a1", exclude=RunStateExclude(agent_links=frozenset({(ROOT_ID, "tu-spawn")})))
    assert state.known is False
    assert state.runs == ()


def test_exclude_late_tree_rule_hides_interactions_of_a_newly_linked_target(root):
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    state = state_of(root, "a1", exclude=RunStateExclude(agent_links=frozenset({(ROOT_ID, "tu-spawn")})))
    assert state.known is False


def test_exclude_second_link_keeps_runs_of_an_already_linked_target(root):
    sub = child(root, "sub")
    link(root, "sub", "tu-sub", background=True, started_at=t(0))
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    link(sub, "a1", "tu-spawn-again", started_at=t(6))
    state = state_of(root, "a1", exclude=RunStateExclude(agent_links=frozenset({("sub", "tu-spawn-again")})))
    assert [run.tool_use_id for run in state.runs] == ["tu-spawn", "tu-msg"]
    assert state.running is True


def test_exclude_run_interactions(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    state = state_of(root, "a1", exclude=RunStateExclude(run_interactions=frozenset({(ROOT_ID, "tu-msg")})))
    assert [run.tool_use_id for run in state.runs] == ["tu-spawn"]
    assert state.running is False


def test_exclude_stop_records(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    _stop(root, "a1", "tu-stop", line=20, at=t(4))
    state = state_of(root, "a1", exclude=RunStateExclude(stop_records=frozenset({(ROOT_ID, "tu-stop")})))
    assert state.running is True


def test_exclude_tool_result_link_ids(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    row = result(root, "tu-spawn", line=3, at=t(1))
    state = state_of(root, "a1", exclude=RunStateExclude(tool_result_link_ids=frozenset({row.id})))
    assert state.running is True


def test_exclude_tool_result_link_ids_on_a_stop_result(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    interaction(root, "a1", "tu-stop", kind=AgentInteractionKind.STOP, line=20)
    row = result(root, "tu-stop", line=21, at=t(4))
    state = state_of(root, "a1", exclude=RunStateExclude(tool_result_link_ids=frozenset({row.id})))
    assert state.running is True


def test_exclude_run_end_ids(root):
    link(root, "a1", "tu-spawn", background=True, started_at=t(0))
    transcript_row = end(root, "a1", tool_use_id="tu-spawn", line=40, at=t(9))
    exclude = RunStateExclude(run_end_ids=frozenset({transcript_row.id}))
    assert state_of(root, "a1", exclude=exclude).running is True
    ui_row = ui_stop(root, "a1", t(10))
    exclude = RunStateExclude(run_end_ids=frozenset({transcript_row.id, ui_row.id}))
    assert state_of(root, "a1", exclude=exclude).running is True
    assert state_of(root, "a1").running is False


# --- Frozen path -------------------------------------------------------------


def test_frozen_root_send_message_whose_first_result_is_after_the_freeze_is_not_a_run(root):
    link(root, "a1", "tu-spawn", line=2, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    result(root, "tu-msg", line=15, at=t(5))
    frozen = state_of(root, "a1", frozen_at_line=12)
    assert [run.tool_use_id for run in frozen.runs] == ["tu-spawn"]
    assert frozen.running is False
    assert state_of(root, "a1", frozen_at_line=15).running is True


def test_frozen_codex_followup_whose_event_line_is_after_the_freeze_is_not_a_run(codex_root):
    link(codex_root, "a1", "call-spawn", line=2, started_at=t(0))
    result(codex_root, "call-spawn", line=3, at=t(1))
    interaction(codex_root, "a1", "call-follow", kind=AgentInteractionKind.RESUME, line=10, event_line=14,
                opens_run=True, started_at=t(5))
    assert state_of(codex_root, "a1", frozen_at_line=12).running is False
    assert state_of(codex_root, "a1", frozen_at_line=14).running is True


def test_frozen_root_results_and_ends_after_the_freeze_are_dropped(root):
    link(root, "a1", "tu-spawn", line=2, background=True, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    result(root, "tu-spawn", line=20, at=t(8))
    end(root, "a1", tool_use_id="tu-spawn", line=21, at=t(8))
    assert state_of(root, "a1", frozen_at_line=10).running is True
    assert state_of(root, "a1").running is False


def test_frozen_spawn_after_the_freeze_is_not_a_run(root):
    link(root, "a1", "tu-spawn", line=20, background=True, started_at=t(0))
    assert state_of(root, "a1", frozen_at_line=10).known is False


def test_frozen_post_freeze_agent_resume_of_a_visible_agent_is_not_a_run(root):
    late = child(root, "late")
    link(root, "a1", "tu-spawn", line=2, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    link(root, "late", "tu-late", line=20, background=True, started_at=t(6))
    interaction(late, "a1", "tu-msg", line=5, opens_run=True, started_at=t(7))
    result(late, "tu-msg", line=6, at=t(7))
    assert state_of(root, "a1", frozen_at_line=10).running is False
    assert state_of(root, "a1").running is True


def test_frozen_run_owned_by_a_visible_agent_is_kept(root):
    visible = child(root, "visible")
    link(root, "a1", "tu-spawn", line=2, started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    link(root, "visible", "tu-visible", line=4, background=True, started_at=t(0))
    interaction(visible, "a1", "tu-msg", line=50, opens_run=True, started_at=t(7))
    result(visible, "tu-msg", line=51, at=t(7))
    assert state_of(root, "a1", frozen_at_line=10).running is True


def test_frozen_stop_owned_by_a_non_visible_agent_is_ignored(root):
    late = child(root, "late")
    link(root, "a1", "tu-spawn", line=2, background=True, started_at=t(0))
    link(root, "late", "tu-late", line=20, background=True, started_at=t(6))
    _stop(late, "a1", "tu-stop", line=5, at=t(7))
    assert state_of(root, "a1", frozen_at_line=10).running is True
    assert state_of(root, "a1").running is False


def test_frozen_codex_root_stop_with_interrupted_line_after_the_freeze_is_not_a_stop(codex_root):
    link(codex_root, "a1", "call-spawn", line=2, background=True, started_at=t(0))
    interaction(codex_root, "a1", "call-stop", kind=AgentInteractionKind.STOP, line=8, event_line=14)
    result(codex_root, "call-stop", line=9, at=t(4))
    assert state_of(codex_root, "a1", frozen_at_line=12).running is True
    assert state_of(codex_root, "a1", frozen_at_line=14).running is False


def test_frozen_root_ui_row_uses_the_freeze_time(root):
    link(root, "a1", "tu-spawn", line=2, background=True, started_at=t(0))
    item(root, 2, t(0))
    item(root, 10, t(5))
    item(root, 11, None)
    item(root, 20, t(9))
    ui_stop(root, "a1", t(5))
    assert state_of(root, "a1", frozen_at_line=11).running is False
    SessionItem.objects.filter(session=root, line_num=10).update(timestamp=t(4))
    assert state_of(root, "a1", frozen_at_line=11).running is True
    assert state_of(root, "a1").running is False


def test_frozen_root_ui_row_dropped_without_a_freeze_time(root):
    link(root, "a1", "tu-spawn", line=2, background=True, started_at=None)
    item(root, 2, None)
    ui_stop(root, "a1", t(5))
    assert state_of(root, "a1", frozen_at_line=5).running is True
    assert state_of(root, "a1").running is False


# --- Newest open run, ordering, serialization ------------------------------


def test_run_started_at_and_background_describe_the_newest_open_run(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    state = state_of(root, "a1")
    assert state.run_started_at == t(5)
    assert state.run_background is True
    result(root, "tu-msg", line=11, at=t(5))
    result(root, "tu-msg", line=12, at=t(6))
    state = state_of(root, "a1")
    assert state.running is True
    assert state.run_started_at == t(0)
    assert state.run_background is False


def test_runs_are_sorted_nulls_first_then_owner_then_tool_use_id(root):
    sub = child(root, "sub")
    link(root, "sub", "tu-sub", background=True, started_at=t(0))
    link(root, "a1", "tu-b", started_at=t(1))
    link(root, "a1", "tu-a", started_at=t(1))
    interaction(sub, "a1", "tu-c", line=5, opens_run=True, started_at=t(1))
    link(root, "a1", "tu-z", started_at=None)
    state = state_of(root, "a1")
    assert [(run.owner_session_id, run.tool_use_id) for run in state.runs] == [
        (ROOT_ID, "tu-z"), (ROOT_ID, "tu-a"), (ROOT_ID, "tu-b"), ("sub", "tu-c"),
    ]


def test_serialize_run_state(root):
    link(root, "a1", "tu-spawn", started_at=t(0))
    result(root, "tu-spawn", line=3, at=t(1))
    interaction(root, "a1", "tu-msg", line=10, opens_run=True, started_at=t(5))
    state = state_of(root, "a1")
    runs = [
        {"owner_session_id": ROOT_ID, "tool_use_id": "tu-spawn", "started_at": t(0).isoformat(),
         "open": False, "closed_at": t(1).isoformat()},
        {"owner_session_id": ROOT_ID, "tool_use_id": "tu-msg", "started_at": t(5).isoformat(),
         "open": True, "closed_at": None},
    ]
    assert serialize_runs(state) == runs
    assert serialize_run_state(ROOT_ID, "a1", state) == {
        "root_session_id": ROOT_ID,
        "agent_session_id": "a1",
        "running": True,
        "run_started_at": t(5).isoformat(),
        "run_background": True,
        "runs": runs,
    }


def test_serialize_run_state_of_an_unknown_agent(root):
    state = state_of(root, "nobody")
    assert serialize_run_state(ROOT_ID, "nobody", state) == {
        "root_session_id": ROOT_ID, "agent_session_id": "nobody", "running": False,
        "run_started_at": None, "run_background": None, "runs": [],
    }


# --- Query budget ------------------------------------------------------------


def _build_tree(root, agent_count, root_result_count):
    project = root.project
    Session.objects.bulk_create([
        Session(id=f"agent-{i}", project=project, provider=root.provider, file_path=f"agent-{i}.jsonl",
                type=SessionType.SUBAGENT, parent_session=root)
        for i in range(agent_count)
    ])
    AgentLink.objects.bulk_create([
        AgentLink(session=root, tool_use_line_num=i + 1, tool_use_id=f"tu-{i}", agent_id=f"agent-{i}",
                  is_background=True, started_at=t(0))
        for i in range(agent_count)
    ])
    AgentInteraction.objects.bulk_create([
        AgentInteraction(session=root, tool_use_line_num=100_000 + i, event_line_num=100_000 + i,
                         tool_use_id=f"tu-msg-{i}", agent_id=f"agent-{i}", kind=AgentInteractionKind.MESSAGE,
                         opens_run=True, started_at=t(5))
        for i in range(agent_count)
    ])
    AgentInteraction.objects.bulk_create([
        AgentInteraction(session=root, tool_use_line_num=200_000 + i, event_line_num=200_000 + i,
                         tool_use_id=f"tu-stop-{i}", agent_id=f"agent-{i}", kind=AgentInteractionKind.STOP)
        for i in range(agent_count)
    ])
    ToolResultLink.objects.bulk_create([
        ToolResultLink(session=root, tool_use_line_num=i % agent_count + 1, tool_result_line_num=300_000 + i,
                       tool_use_id=f"tu-{i % agent_count}", tool_result_at=t(1, ms=i))
        for i in range(root_result_count)
    ])
    AgentRunEnd.objects.bulk_create([
        AgentRunEnd(session=root, line_num=400_000 + i, agent_id=f"agent-{i}", tool_use_id=f"tu-msg-{i}",
                    ended_at=t(9), status="completed")
        for i in range(agent_count)
    ])
    # A root ui row, so the frozen path reads its freeze time too.
    AgentRunEnd.objects.create(session=root, source=AgentRunEndSource.UI, agent_id="agent-1",
                               ended_at=t(10), status="ui_stopped")


def _count_queries(root, agent_ids, **kwargs):
    with CaptureQueriesContext(connection) as ctx:
        agent_run_states(root, agent_ids, **kwargs)
    return len(ctx.captured_queries)


def _drop_tree(root):
    """Delete a tree's subagents and rows so the next tree can reuse the agent ids."""
    AgentLink.objects.all().delete()
    AgentInteraction.objects.all().delete()
    ToolResultLink.objects.all().delete()
    AgentRunEnd.objects.all().delete()
    Session.objects.filter(parent_session=root).delete()


def test_query_budget_is_independent_of_the_tree_size(project, django_assert_num_queries,
                                                      django_assert_max_num_queries):
    small = make_session(project, "small-root")
    _build_tree(small, 5, 20)
    small_count = _count_queries(small, ["agent-1"])
    _drop_tree(small)
    big = make_session(project, "big-root")
    _build_tree(big, 200, 15_000)

    with django_assert_num_queries(small_count):
        state = agent_run_states(big, ["agent-1"])["agent-1"]
    assert state.known is True
    assert state.running is False
    with django_assert_max_num_queries(5):
        agent_run_states(big, ["agent-1"])


def test_frozen_query_budget_is_independent_of_the_tree_size(project, django_assert_num_queries,
                                                             django_assert_max_num_queries):
    small = make_session(project, "small-root")
    _build_tree(small, 5, 20)
    small_count = _count_queries(small, ["agent-1"], frozen_at_line=500_000)
    _drop_tree(small)
    big = make_session(project, "big-root")
    _build_tree(big, 200, 15_000)
    with django_assert_num_queries(small_count):
        state = agent_run_states(big, ["agent-1"], frozen_at_line=500_000)["agent-1"]
    assert state.known is True
    with django_assert_max_num_queries(5):
        agent_run_states(big, ["agent-1"], frozen_at_line=500_000)
