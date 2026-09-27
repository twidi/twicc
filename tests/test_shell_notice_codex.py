"""Codex bookkeeping for the background shell notice (spec §3.1, §3.2, §5.4)."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.shell_notice_helpers import first_step, run_async
from twicc.agent.shell_notice import OwnerFacts, ShellOwner
from twicc.agent.states import AgentState
from twicc.asgi import WSConsumer
from twicc.providers.helpers import AgentSettings
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.manager import CodexAgentManager


def make_agent():
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "codex-root"
    agent.state = AgentState.USER_TURN
    agent._live_subagents = {}
    agent._live_shells = {}
    agent._first_shell_started_at = None
    agent._recently_ended_shells = {}
    agent._goal_continuation_active = False
    agent._subagent_hold_active = False
    agent._manual_compaction = False
    agent._current_turn = None
    agent._pending_requests = {}
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._background_work_broadcast_failures = 0
    agent._subagent_set_lock = asyncio.Lock()
    agent._dead_event = asyncio.Event()
    agent._init_shell_notice_state()
    agent._init_codex_shell_notice_state()
    return agent


def command_event(method, process_id, command="sleep 60"):
    item = SimpleNamespace(type="commandExecution", process_id=process_id, command=command)
    return method, SimpleNamespace(thread_id="codex-root", item=item, started_at_ms=None)


def activity(kind, thread_id, path):
    return SimpleNamespace(agent_thread_id=thread_id, kind=kind, agent_path=path)


def test_own_shell_keeps_its_command():
    agent = make_agent()
    agent._note_command_execution(*command_event("item/started", "7"))
    [shell] = agent.shell_notice_state().shells
    assert (shell.key, shell.shell_id, shell.owner, shell.command) == ("codex-root:7", "7", ShellOwner.MAIN, "sleep 60")


@run_async
async def test_first_level_owner_path_survives_the_run_end():
    agent = make_agent()
    agent._note_sub_agent_activity(activity("started", "t1", "/root/review"))
    await agent.notify_shells_started("t1", {"9": time.time()})
    [shell] = agent.shell_notice_state().shells
    assert (shell.owner, shell.owner_ref, shell.owner_running) == (ShellOwner.SUBAGENT, "/root/review", True)
    agent._note_sub_agent_activity(activity("completed", "t1", "/root/review"))
    [shell] = agent.shell_notice_state().shells
    assert shell.owner_running is False and shell.owner_run_ended_at is not None
    assert agent._subagent_paths == {"t1": "/root/review"}


@run_async
async def test_unknown_thread_is_unresolved_and_needs_the_run_model():
    agent = make_agent()
    await agent.notify_shells_started("nested-1", {"4": time.time()})
    [shell] = agent.shell_notice_state().shells
    assert shell.owner is ShellOwner.UNRESOLVED
    [lookup] = agent.shell_notice_lookups({"nested-1:4"}, time.time())
    assert (lookup.owner_id, lookup.codex) == ("nested-1", True)
    agent.store_shell_resolutions([OwnerFacts("nested-1:4", "nested-1", None, "t1", "Nested", True, False, 5.0)],
                                  time.time())
    [placed] = agent.shell_notice_state().shells
    assert (placed.owner, placed.owner_ref, placed.owner_spawner_ref, placed.owner_run_ended_at) == (
        ShellOwner.SUBAGENT, "nested-1", "t1", 5.0,
    )
    # Codex re-reads the run state at each tick.
    assert len(agent.shell_notice_lookups({"nested-1:4"}, time.time())) == 1


def test_idle_predicate():
    agent = make_agent()
    assert agent.shell_notice_state().idle is True
    agent.state = AgentState.ASSISTANT_TURN
    assert agent.shell_notice_state().idle is False
    agent._subagent_hold_active = True
    assert agent.shell_notice_state().idle is True
    agent._goal_continuation_active = True
    assert agent.shell_notice_state().idle is False
    agent._goal_continuation_active = False
    agent._manual_compaction = True
    assert agent.shell_notice_state().idle is False


@run_async
async def test_send_returns_true_in_user_turn_and_clears_only_on_external_send():
    agent = make_agent()
    agent._notify_state_change = AsyncMock()
    agent._schedule_turn = lambda text, images: None
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("notice", shell_notice=True) is True
    assert agent._shell_notice_notified == {"codex-root:7"}
    agent.state = AgentState.USER_TURN
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_hold_break_and_steer_are_external_sends():
    agent = make_agent()
    agent._broadcast_process_label = AsyncMock()
    agent._schedule_turn = lambda text, images: None
    agent._note_command_execution(*command_event("item/started", "7"))
    # Hold break: ASSISTANT_TURN parked in the hold, no active turn.
    agent.state = AgentState.ASSISTANT_TURN
    agent._subagent_hold_active = True
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()
    # Steer: a real turn is running.
    agent._subagent_hold_active = False
    agent._current_turn_ready = asyncio.Event()
    agent._current_turn_ready.set()
    agent._current_turn = MagicMock(steer=AsyncMock())
    agent._build_turn_input = AsyncMock(return_value="input")
    agent.mark_shells_noticed(["codex-root:7"])
    assert await agent.send("more") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_hardcoded_command_clears_the_set():
    agent = make_agent()
    agent.run_plan_command = AsyncMock()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    await agent.run_hardcoded_command(SimpleNamespace(name="plan", args="do it"))
    assert agent._shell_notice_notified == set()


def test_forget_shell_drops_the_notice_state():
    agent = make_agent()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    agent._note_command_execution(*command_event("item/completed", "7"))
    assert agent._shell_notice_notified == set()


def test_drop_gone_shells_only_drops_the_snapshot():
    agent = make_agent()
    agent._track_shell(("codex-root", "1"), 100.0)
    agent._track_shell(("t1", "2"), 100.0)
    snapshot = {("codex-root", "1")}
    assert agent.drop_gone_shells(1000.0, keys=snapshot) is True
    assert set(agent._live_shells) == {("t1", "2")}


def test_subagent_run_end_recorded_at_the_activity_pop():
    agent = make_agent()
    agent._note_sub_agent_activity(activity("started", "t1", "/root/a"))
    agent._note_sub_agent_activity(activity("interrupted", "t1", "/root/a"))
    assert "t1" in agent._subagent_run_ended_at


@run_async
async def test_run_end_recorded_by_the_prune(monkeypatch):
    agent = make_agent()
    agent._live_subagents = {"t1": "/root/a", "t2": "/root/b"}
    monkeypatch.setattr("twicc.providers.codex.agent.agent._stopped_subagent_ids",
                        lambda root_id, session_ids: ["t1"])
    await agent._prune_finished_subagents()
    assert set(agent._subagent_run_ended_at) == {"t1"}
    agent.ephemeral = True
    agent._ephemeral_finished_subagents = AsyncMock(return_value=["t2"])
    await agent._prune_finished_subagents()
    assert set(agent._subagent_run_ended_at) == {"t1", "t2"}


@run_async
async def test_run_end_recorded_by_the_stop_relay(monkeypatch):
    agent = make_agent()
    agent._live_subagents = {"t1": "/root/a"}
    monkeypatch.setattr("twicc.providers.codex.agent.agent._stopped_subagent_ids",
                        lambda root_id, session_ids: ["t1"])
    await agent._apply_subagents_stopped(["t1"], retry_on_error=False)
    assert "t1" in agent._subagent_run_ended_at


@run_async
async def test_plan_implementation_turn_is_an_external_send():
    from twicc.providers.codex.agent.agent import _PLAN_IMPLEMENTATION_MESSAGE

    agent = make_agent()
    agent._run_turn = AsyncMock()
    agent._note_command_execution(*command_event("item/started", "7"))
    agent.mark_shells_noticed(["codex-root:7"])
    await agent._run_plan_implementation_turn()
    assert agent._shell_notice_notified == set()
    agent._run_turn.assert_awaited_once_with(_PLAN_IMPLEMENTATION_MESSAGE, None)


def test_turn_openings_restart_the_delay():
    for opener in (
        lambda agent: agent._run_turn("x", None),
        lambda agent: agent._run_goal_continuation(MagicMock()),
        lambda agent: agent.compact(),
    ):
        agent = make_agent()
        agent._shell_notice_idle_since = 1.0
        first_step(opener(agent))
        assert agent._shell_notice_idle_since is None


def _probe_manager(monkeypatch):
    manager = CodexAgentManager.__new__(CodexAgentManager)
    manager._state_based_timeout = lambda agent, now: None
    monkeypatch.setattr("twicc.providers.codex.agent.manager.command_processes_may_run",
                        lambda pid, since: False)
    return manager


def _probed_agent(state, hold):
    agent = MagicMock()
    agent.state = state
    agent.in_subagent_hold.return_value = hold
    agent.has_live_shells.return_value = True
    agent.shell_probe.return_value = (123, 0.0)
    agent.live_shell_keys.return_value = {("t1", "2")}
    agent.drop_gone_shells.return_value = False
    return agent


def test_reconciliation_runs_in_user_turn_and_in_the_hold_with_a_snapshot(monkeypatch):
    manager = _probe_manager(monkeypatch)
    for state, hold in ((AgentState.USER_TURN, False), (AgentState.ASSISTANT_TURN, True)):
        agent = _probed_agent(state, hold)
        asyncio.run(manager._check_agent_timeout(agent, time.time()))
        assert agent.drop_gone_shells.call_args.kwargs == {"keys": {("t1", "2")}}


def test_reconciliation_skips_a_working_turn(monkeypatch):
    manager = _probe_manager(monkeypatch)
    agent = _probed_agent(AgentState.ASSISTANT_TURN, False)
    asyncio.run(manager._check_agent_timeout(agent, time.time()))
    agent.drop_gone_shells.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_ws_send_ack_follows_the_codex_delivery_result():
    """Spec §10: the WS ack is sent exactly when the Codex send delivered."""
    async def scenario():
        agent = make_agent()
        agent._notify_state_change = AsyncMock()
        agent._schedule_turn = lambda text, images: None
        agent.agent_settings = AgentSettings()
        agent.apply_agent_settings = AsyncMock()
        manager = CodexAgentManager.__new__(CodexAgentManager)
        manager._agents = {agent.session_id: agent}
        manager._lock = asyncio.Lock()
        # A live process: the settings-only branch below reaches the manager.
        manager.get_agent_info = lambda session_id: object()
        helpers = SimpleNamespace(resolve_agent_settings=lambda s: s, enforce_agent_settings_consistency=lambda s: s)
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        with (
            patch("twicc.asgi.get_session_provider", new=AsyncMock(return_value="codex")),
            patch("twicc.asgi.ensure_provider_running"),
            patch("twicc.asgi.get_provider_helpers", return_value=helpers),
            patch("twicc.asgi.get_project_directory", new=AsyncMock(return_value="/tmp")),
            patch("twicc.asgi.get_agent_manager_registry", return_value=SimpleNamespace(get=lambda p: manager)),
            patch("twicc.asgi.run_under_db_write_lock", new=AsyncMock()),
        ):
            base = {"session_id": agent.session_id, "project_id": "p", "request_id": "r1"}
            # USER_TURN with text: CodexAgent.send returns True -> ack.
            assert await consumer._handle_send_message_admitted({**base, "text": "hello"}) is True
            consumer.send_json.assert_awaited_once_with(
                {"type": "send_ack", "request_id": "r1", "session_id": agent.session_id},
            )
            # Settings-only update: nothing delivered -> no ack.
            consumer.send_json.reset_mock()
            agent.state = AgentState.USER_TURN
            assert await consumer._handle_send_message_admitted({**base, "text": ""}) is False
            consumer.send_json.assert_not_awaited()

    asyncio.run(scenario())
