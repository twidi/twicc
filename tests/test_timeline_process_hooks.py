from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.utils import timezone

from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from tests.db_write_helpers import run_with_direct_writes
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)

WORK = {"subagents": 1, "shells": 0, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}


class _Manager(BaseAgentManager):
    provider = Provider.CODEX


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-proc", directory="/tmp/tl-proc")


def make_agent(project, **kw):
    now = timezone.now()
    run = ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False,
        process_run=run, pending_requests=(), kill_reason=None, error=None, _work=None, is_hybrid=False,
        get_pid=lambda: 123, background_work_snapshot=lambda: agent._work,
        _completion_pending_publications={}, _completion_input_receipts={},
        start=AsyncMock(),
    )
    for key, value in kw.items():
        setattr(agent, key, value)
    return agent


def transition(agent, state):
    manager = _Manager()
    return run_with_direct_writes(lambda: manager._persist_process_run_transition(agent, state))


def test_register_and_start_records_process_started(project, monkeypatch):
    manager = _Manager()
    agent = make_agent(project)
    agent.process_run.delete()
    agent.process_run = None
    agent._background_work_callback = None
    monkeypatch.setattr(manager, "_broadcast_session_updated", AsyncMock())
    monkeypatch.setattr(manager, "_on_state_change", AsyncMock())
    monkeypatch.setattr(manager, "_ensure_timeout_monitor_running", lambda: None)
    run_with_direct_writes(lambda: manager._register_and_start(agent, "hello", resume=True))
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert row.data["provider"] == "codex" and row.data["cause"] == "resume" and row.data["hybrid"] is False
    assert row.project_id == project.id and row.actor == "user"


def test_a_hybrid_agent_records_hybrid_true(project, monkeypatch):
    manager = _Manager()
    agent = make_agent(project, is_hybrid=True)
    agent.process_run.delete()
    agent.process_run = None
    agent._background_work_callback = None
    monkeypatch.setattr(manager, "_broadcast_session_updated", AsyncMock())
    monkeypatch.setattr(manager, "_on_state_change", AsyncMock())
    monkeypatch.setattr(manager, "_ensure_timeout_monitor_running", lambda: None)
    run_with_direct_writes(lambda: manager._register_and_start(agent, "hello", resume=False))
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert row.data["hybrid"] is True and row.data["cause"] == "new"


def test_a_turn_round_trip_records_started_then_ended(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    transition(agent, AgentState.USER_TURN)
    assert kinds("s1") == ["turn.started", "turn.ended"]
    turn_ended = TimelineEvent.objects.get(session_id="s1", kind="turn.ended")
    assert turn_ended.data == {"background_work": None, "message_refs": None}


def test_waiting_for_the_user_records_the_request_and_the_tool(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.pending_requests = (SimpleNamespace(request_type="tool_approval", tool_name="Bash"),)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.pending_requests = ()
    transition(agent, AgentState.ASSISTANT_TURN)
    rows = list(TimelineEvent.objects.filter(session_id="s1", kind__startswith="awaiting_input").order_by("id"))
    assert [r.kind for r in rows] == ["awaiting_input.started", "awaiting_input.ended"]
    assert rows[0].data == {"request_type": "tool_approval", "tool_name": "Bash"}
    # The tool is inherited by the recording service; nobody auto-denied the request.
    assert rows[1].data == {**rows[0].data, "auto_deny_after": None}


def test_turn_ended_carries_the_background_work_running_behind_the_agent(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    transition(agent, AgentState.USER_TURN)
    assert TimelineEvent.objects.get(session_id="s1", kind="turn.ended").data == {"background_work": WORK, "message_refs": None}


def test_background_work_changes_while_idle_preserve_one_logical_turn_end(project):
    agent = make_agent(project)
    manager = _Manager()
    transition(agent, AgentState.ASSISTANT_TURN)
    transition(agent, AgentState.USER_TURN)
    completed = list(TimelineEvent.objects.filter(session_id="s1").order_by("id").values())
    changed_at = agent.process_run.last_state_change_at
    shell = {**WORK, "subagents": 0, "shells": 1}
    for snapshot in (shell, {**shell, "shells": 2}, None, None):
        agent._work = snapshot
        run_with_direct_writes(lambda: manager._persist_background_work(agent, snapshot))
        agent.process_run.refresh_from_db()
        assert agent.process_run.background_work_in_progress == snapshot
        assert agent.process_run.last_state_change_at == changed_at
    assert list(TimelineEvent.objects.filter(session_id="s1").order_by("id").values()) == completed
    assert kinds("s1") == ["turn.started", "turn.ended"]
    assert TimelineEvent.objects.get(session_id="s1", kind="turn.ended").data == {"background_work": None, "message_refs": None}


def test_background_work_changes_during_an_assistant_turn_record_nothing(project):
    agent = make_agent(project)
    manager = _Manager()
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    run_with_direct_writes(lambda: manager._persist_background_work(agent, WORK))
    agent._work = None
    run_with_direct_writes(lambda: manager._persist_background_work(agent, None))
    assert kinds("s1") == ["turn.started"]


def test_death_closes_open_intervals_then_ends_the_process_with_one_timestamp(project):
    agent = make_agent(project)
    agent._work = WORK
    agent.pending_requests = (SimpleNamespace(request_type="question", tool_name="AskUserQuestion"),)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    tail = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))[-3:]
    assert [r.kind for r in tail] == ["turn.ended", "awaiting_input.ended", "process.ended"]
    assert tail[0].data == {"background_work": None, "message_refs": None}  # a dead process runs nothing
    assert len({r.occurred_at for r in tail}) == 1
    assert tail[-1].reason == "manual" and tail[-1].actor == "user"


def test_death_from_user_turn_with_background_work_writes_only_process_ended(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    transition(agent, AgentState.USER_TURN)
    agent.kill_reason = "shutdown"
    transition(agent, AgentState.DEAD)
    assert kinds("s1") == ["turn.started", "turn.ended", "process.ended"]


def test_process_ended_inherits_the_data_of_process_started(project):
    from twicc.agent.timeline_hooks import record_process_started

    agent = make_agent(project, is_hybrid=True)
    record_process_started(agent, resume=True)
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    started = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    ended = TimelineEvent.objects.get(session_id="s1", kind="process.ended")
    for field in ("provider", "cause", "twicc_pid", "hybrid"):
        assert ended.data[field] == started.data[field]
    assert ended.data["provider"] == "codex" and ended.data["cause"] == "resume" and ended.data["hybrid"] is True


def test_unsolicited_death_is_an_error_by_the_system(project):
    agent = make_agent(project, error="boom")
    transition(agent, AgentState.DEAD)
    row = TimelineEvent.objects.get(session_id="s1", kind="process.ended")
    assert row.reason == "error" and row.actor == "system" and row.data["error"] == "boom"


def test_archive_death_points_at_the_archive_event(project):
    cause = record_event_sync("archived.started", session_id="s1", actor="user")
    agent = make_agent(project, kill_reason="archived")
    transition(agent, AgentState.DEAD)
    assert TimelineEvent.objects.get(session_id="s1", kind="process.ended").caused_by_id == cause.id


def test_ephemeral_agents_record_nothing(project):
    agent = make_agent(project, ephemeral=True)
    transition(agent, AgentState.ASSISTANT_TURN)
    assert kinds("s1") == []


def test_a_failing_hook_never_blocks_the_process_run_update(project, monkeypatch):
    agent = make_agent(project)
    monkeypatch.setattr("twicc.agent.timeline_hooks.record_event_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    assert not ProcessRun.objects.filter(session_id="s1").exists()


def test_a_failing_process_started_hook_never_reaches_the_caller(project, monkeypatch):
    from twicc.agent import timeline_hooks

    agent = make_agent(project)
    monkeypatch.setattr(timeline_hooks, "record_event_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert timeline_hooks.record_process_started(agent, resume=False) is None
    assert kinds("s1") == []
    del agent.provider  # an error while reading the agent is swallowed too
    assert timeline_hooks.record_process_started(agent, resume=False) is None


def test_an_agent_without_project_id_still_records(project):
    agent = make_agent(project)
    del agent.project_id
    transition(agent, AgentState.ASSISTANT_TURN)
    assert TimelineEvent.objects.get(session_id="s1").project_id is None


def test_a_second_death_notification_with_a_kept_row_writes_no_second_process_ended(project):
    agent = make_agent(project)
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    from twicc.agent.timeline_hooks import record_process_ended

    record_process_ended(agent, SimpleNamespace(state=AgentState.DEAD.value, awaiting=False, work=None))
    assert kinds("s1").count("process.ended") == 1
