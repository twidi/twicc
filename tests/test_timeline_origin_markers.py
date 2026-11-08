"""Automatic starts and stops carry the system marker (design sections 5, 7; verification 8e, 8g, 9)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.timeline_helpers import columns
from twicc.agent.base_agent import BaseAgent
from twicc.agent.states import AgentState
from twicc.agent.timeline_hooks import record_process_started
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, TimelineEvent
from twicc.providers.claude_code import cron_restart
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.request_origin import SYSTEM_ORIGIN, UI_ORIGIN, current_origin, mcp_session_origin, origin_scope

A = mcp_session_origin("sess-A")
LOW, HIGH = AgentSettings(effort="low"), AgentSettings(effort="high")


class _Agent(BaseAgent):
    provider = Provider.CODEX

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def new_agent():
    return _Agent(session_id="s1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-mark", directory="/tmp/tl-origin-mark")


# --- The state-change funnel ---------------------------------------------------------------------------------


def test_the_funnel_runs_the_callback_under_the_system_marker_and_restores_the_origin():
    agent = new_agent()
    seen = []

    async def callback(_agent):
        seen.append(current_origin())

    agent._state_change_callback = callback

    async def scenario():
        with origin_scope(UI_ORIGIN):
            await agent._notify_state_change()
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("carrier", [None, UI_ORIGIN, A], ids=["unset", "ui", "agent"])
def test_a_follow_up_start_launched_by_the_funnel_is_system_whatever_the_triggering_request(project, carrier):
    restart = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False, process_run=None,
        is_hybrid=False,
    )
    dying = new_agent()

    async def callback(_agent):  # stands for the cron / pending-settings restart the DEAD callback launches
        await asyncio.to_thread(record_process_started, restart, resume=True)

    dying._state_change_callback = callback

    async def scenario():
        with origin_scope(carrier):
            await dying._notify_state_change()

    asyncio.run(scenario())
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert columns(row) == ("system", None, None, None)


# --- apply-settings restarts ---------------------------------------------------------------------------------


def _idle_manager(seen, *, start_raises=False, between=None, after=None):
    """A Claude manager with one idle agent whose startup settings (effort) differ from ``HIGH``."""
    manager = ClaudeCodeAgentManager()
    agent = SimpleNamespace(
        session_id="s1", state=AgentState.USER_TURN, agent_settings=LOW, background_shell_count=lambda: 0,
        process_run=None, interrupt_or_kill=AsyncMock(), is_hybrid=False,
    )
    manager._agents = {"s1": agent}
    manager._lock = asyncio.Lock()
    manager._pending_after_restart = {}
    manager._cron_restart_tasks = {}
    manager._check_ephemeral_readonly = lambda *a, **k: None
    manager._check_session_writable = AsyncMock()  # no DB here: availability is tested elsewhere
    manager._cancel_cron_restart_task = lambda session_id: None

    async def broadcast(*args, **kwargs):
        if between is not None:
            between.append(current_origin())

    async def start(*args, **kwargs):
        seen.append(current_origin())
        if start_raises:
            raise RuntimeError("start failed")

    def parked_release(session_id, pending):
        if after is not None:
            after.append(current_origin())

    manager._broadcast_agent_state = broadcast
    manager._start_agent = start
    manager._parked_release_after_start = parked_release
    return manager, agent


def test_the_inline_apply_settings_restart_is_system_and_the_rest_of_the_send_keeps_the_user_origin():
    seen, between, after = [], [], []

    async def scenario():
        manager, _agent = _idle_manager(seen, between=between, after=after)
        with origin_scope(UI_ORIGIN):
            await manager.send_to_session("s1", "-p", "/tmp", "hello", HIGH)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]  # the restart is automatic
    assert between == [UI_ORIGIN] and after == [UI_ORIGIN]  # the send's other writes are still the user's (8g)


def test_marker_is_restored_when_the_inline_restart_raises():
    seen = []

    async def scenario():
        manager, _agent = _idle_manager(seen, start_raises=True)
        with origin_scope(UI_ORIGIN):
            with pytest.raises(RuntimeError, match="start failed"):
                await manager.send_to_session("s1", "-p", "/tmp", "hello", HIGH)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


def test_an_apply_settings_kill_without_restart_leaves_a_later_send_its_own_origin():
    seen = []

    async def scenario():
        manager, agent = _idle_manager(seen)
        with origin_scope(UI_ORIGIN):
            # Settings-only change in USER_TURN: no text, no crons -> the kill restarts nothing.
            assert await manager.send_to_session("s1", "-p", "/tmp", "", HIGH) is False
        agent.interrupt_or_kill.assert_awaited_once_with(reason="apply-settings")
        assert seen == []
        agent.state = AgentState.DEAD
        with origin_scope(A):  # a later, independent send by an agent
            await manager.send_to_session("s1", "-p", "/tmp", "hello again", HIGH)

    asyncio.run(scenario())
    assert seen == [A]


@pytest.mark.django_db(transaction=True)
def test_the_pending_settings_restart_that_re_sends_a_parked_text_is_system(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider=Provider.CLAUDE_CODE.value)
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    current = helpers.enforce_agent_settings_consistency(
        helpers.resolve_agent_settings(AgentSettings.from_session(session))
    )
    Session.objects.filter(id="s1").update(effort="max" if current.effort != "max" else "low")  # a startup change
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, cwd="/tmp", state=AgentState.USER_TURN, agent_settings=current,
        background_shell_count=lambda: 0, process_run=None, interrupt_or_kill=AsyncMock(), is_hybrid=False,
    )
    manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
    manager._pending_after_restart = {"s1": {"text": "parked", "images": None, "documents": None}}
    manager._deferred_completion_receipts = {}
    manager._broadcast_agent_state = AsyncMock()
    manager._check_session_writable = AsyncMock()  # no DB here: availability is tested elsewhere
    manager._parked_release_after_start = lambda session_id, pending: None
    seen = []

    async def start(*args, **kwargs):
        seen.append(current_origin())

    manager._start_agent = start

    async def scenario():
        with origin_scope(UI_ORIGIN):
            await manager._apply_pending_settings(agent)
            return current_origin()

    assert asyncio.run(scenario()) == UI_ORIGIN
    assert seen == [SYSTEM_ORIGIN]


# --- Cron restarts and hybrid boot adoption ---------------------------------------------------------------------


@pytest.mark.parametrize("carrier", [None, UI_ORIGIN], ids=["boot-unset", "runtime-ui"])
def test_a_cron_restart_sends_under_the_system_marker(monkeypatch, carrier):
    seen = []
    stop = asyncio.Event()

    class _FakeManager:
        _agents = {}

        async def _session_is_hybrid(self, session_id):
            return False

        async def send_to_session(self, **kwargs):
            seen.append(current_origin())
            stop.set()  # end the retry loop after this attempt

    monkeypatch.setattr(
        "twicc.providers.claude_code.agent.manager.get_claude_code_agent_manager", lambda: _FakeManager(),
    )
    monkeypatch.setattr(cron_restart, "_collect_restart_data", lambda session_id: {
        "session_id": session_id, "project_id": "-p", "cwd": "/tmp", "settings": LOW,
        "restored_crons": [], "crons_to_recreate": [],
    })
    monkeypatch.setattr(cron_restart, "_build_restart_message", lambda restored, to_recreate: "restart")

    async def scenario():
        with origin_scope(carrier):
            await cron_restart.restart_session_crons("s1", stop_event=stop, initial_delay=0)
            return current_origin()

    assert asyncio.run(scenario()) == carrier
    assert seen == [SYSTEM_ORIGIN]


@pytest.mark.django_db(transaction=True)
def test_hybrid_boot_adoption_registers_under_the_system_marker(project, monkeypatch):
    from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux

    Session.objects.create(id="h1", file_path="h1.jsonl", project=project, provider=Provider.CLAUDE_CODE.value, hybrid=True)
    monkeypatch.setattr(hybrid_tmux, "list_hybrid_sessions", lambda: [hybrid_tmux.HYBRID_SESSION_PREFIX + "h1"])
    monkeypatch.setattr(hybrid_tmux, "pane_status", lambda session_id: (123, False))

    class _FakeHybrid:
        def __init__(self, **kwargs):
            self.session_id = kwargs["session_id"]

    monkeypatch.setattr("twicc.providers.claude_code.agent.hybrid.agent.HybridClaudeAgent", _FakeHybrid)
    seen = []

    async def register(agent, text, *, resume, **kwargs):
        seen.append(current_origin())

    async def scenario():
        manager = ClaudeCodeAgentManager.__new__(ClaudeCodeAgentManager)
        manager._lock = asyncio.Lock()
        manager._agents = {}
        manager._register_and_start = register
        await manager.adopt_running_hybrid_sessions()

    asyncio.run(scenario())
    assert seen == [SYSTEM_ORIGIN]
