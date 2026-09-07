"""Ephemeral admission and lifecycle contracts, without provider subprocesses."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.exceptions import SendDeliveryError
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings


def test_registry_owns_ids_until_shutdown_and_releases_persistent_collision():
    from twicc.agent import ephemeral

    ephemeral.clear()
    try:
        admission = ephemeral.reserve("draft", "codex", "project")
        assert ephemeral.pending_snapshot() == [
            {"draft_session_id": "draft", "provider": "codex", "project_id": "project"}
        ]
        with pytest.raises(SendDeliveryError):
            ephemeral.reserve("draft", "claude_code", "project")
        with pytest.raises(SendDeliveryError):
            ephemeral.check_readonly("draft", object())
        ephemeral.check_readonly("draft", admission)
        ephemeral.bind(admission, "canonical")
        ephemeral.settle(admission)
        assert ephemeral.pending_snapshot() == []
        for sid in ("draft", "canonical"):
            with pytest.raises(SendDeliveryError):
                ephemeral.check_readonly(sid)
        ephemeral.release(admission)
        assert not ephemeral.is_known("draft")
        assert not ephemeral.is_known("canonical")
    finally:
        ephemeral.clear()


class Agent(BaseAgent):
    provider = Provider.CLAUDE_CODE

    def get_pid(self):
        return None

    async def start(self, text, callback, **kwargs):
        from twicc.pending_session_attributes import get_pending_session_attributes
        from twicc.pending_titles import get_pending_title

        self._state_change_callback = callback
        assert get_pending_title(self.session_id) is None
        assert get_pending_session_attributes(self.session_id).ephemeral is True
        self._set_state(AgentState.ASSISTANT_TURN)
        await callback(self)

    async def interrupt_or_kill(self, reason="manual"):
        self.kill_reason = reason
        await self._transition_to_dead()


class Manager(BaseAgentManager):
    provider = Provider.CLAUDE_CODE

    async def _create_agent(self, session_id, project_id, cwd, *, settings, **kwargs):
        return Agent("canonical", project_id, cwd, settings, ephemeral=kwargs.get("ephemeral", False))


def test_ephemeral_start_and_result_never_write_db_and_emit_once():
    async def scenario():
        from twicc.agent import ephemeral
        from twicc.pending_session_attributes import set_pending_session_attributes, get_pending_session_attributes
        from twicc.pending_titles import set_pending_title
        from twicc.pending_agent_settings import set_pending_agent_settings, pop_pending_agent_settings

        ephemeral.clear()
        manager = Manager()
        manager.notify_session_bound = AsyncMock()
        manager._ensure_timeout_monitor_running = Mock()
        manager._broadcast_info = AsyncMock()
        manager._persist_process_run_transition = AsyncMock(side_effect=AssertionError("DB transition"))
        manager._update_session_stopped_at = AsyncMock(side_effect=AssertionError("DB stopped"))
        manager._flush_pending_title = AsyncMock(side_effect=AssertionError("title flush"))
        set_pending_title("draft", "private title")
        set_pending_agent_settings("draft", AgentSettings())
        set_pending_session_attributes("draft", ephemeral=True, system_prompt_addendum="ephemeral context")
        layer = Mock(group_send=AsyncMock())
        with patch("channels.layers.get_channel_layer", return_value=layer):
            sid = await manager._start_agent(
                "draft", "project", "/tmp", "prompt", False, settings=AgentSettings(), ephemeral=True
            )
            agent = manager._agents[sid]
            assert agent.process_run is None
            assert agent.get_info().extra == {"ephemeral": True, "ephemeral_draft_id": "draft"}
            for key in ("draft", "canonical"):
                assert get_pending_session_attributes(key) is None
                assert pop_pending_agent_settings(key) is None
            agent.ephemeral_final_text = "answer"
            agent._set_state(AgentState.USER_TURN)
            await manager._on_state_change(agent)
            assert agent.state == AgentState.USER_TURN
            await asyncio.sleep(0)
            await asyncio.gather(*manager._ephemeral_cleanup_tasks)
            frames = [call.args[1] for call in layer.group_send.call_args_list]
            results = [f for f in frames if f["type"] == "broadcast" and f["data"]["type"] == "ephemeral_result"]
            assert len(results) == 1
            assert results[0]["data"]["text"] == "answer"
            assert results[0]["data"]["status"] == "done"
            assert agent.state == AgentState.DEAD
            with pytest.raises(SendDeliveryError):
                manager._check_ephemeral_readonly("draft")
        ephemeral.clear()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state,reason,soft,status",
    [
        (AgentState.USER_TURN, None, True, "stopped"),
        (AgentState.DEAD, "manual", False, "stopped"),
        (AgentState.DEAD, "force", False, "stopped"),
        (AgentState.DEAD, "error", False, "error"),
        (AgentState.DEAD, "shutdown", False, None),
    ],
)
def test_terminal_status_and_exact_once(state, reason, soft, status):
    async def scenario():
        manager = Manager()
        manager._broadcast_info = AsyncMock()
        manager.kill_agent = AsyncMock()
        agent = Agent("status-id", "p", "/tmp", AgentSettings(), ephemeral=True)
        agent.state = state
        agent.kill_reason = reason
        agent.ephemeral_soft_interrupted = soft
        layer = Mock(group_send=AsyncMock())
        with patch("channels.layers.get_channel_layer", return_value=layer):
            await manager._on_state_change(agent)
            await manager._on_state_change(agent)
            await asyncio.gather(*manager._ephemeral_cleanup_tasks)
        frames = [c.args[1]["data"] for c in layer.group_send.call_args_list]
        assert len(frames) == (0 if status is None else 1)
        if status:
            assert frames[0]["status"] == status

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["factory", "bind", "start", "cancel"])
def test_start_failure_drains_buffers_and_settles_admission(stage):
    async def scenario():
        from twicc.agent import ephemeral
        from twicc.pending_session_attributes import set_pending_session_attributes, get_pending_session_attributes
        from twicc.pending_titles import set_pending_title, get_pending_title

        ephemeral.clear()
        manager = Manager()
        manager._ensure_timeout_monitor_running = Mock()
        manager._broadcast_info = AsyncMock()
        manager.notify_session_bound = AsyncMock()
        set_pending_title("draft", "private")
        set_pending_session_attributes("draft", ephemeral=True)
        if stage in ("factory", "cancel"):
            manager._create_agent = AsyncMock(
                side_effect=asyncio.CancelledError() if stage == "cancel" else RuntimeError("factory")
            )
        elif stage == "bind":
            manager.notify_session_bound.side_effect = RuntimeError("bind")
        else:
            manager._register_and_start = AsyncMock(side_effect=RuntimeError("start"))
        layer = Mock(group_send=AsyncMock())
        with (
            patch("channels.layers.get_channel_layer", return_value=layer),
            pytest.raises((RuntimeError, asyncio.CancelledError)),
        ):
            await manager._start_agent("draft", "p", "/tmp", "prompt", False, settings=AgentSettings(), ephemeral=True)
        assert ephemeral.pending_snapshot() == []
        assert not manager._agents
        for sid in ("draft", "canonical"):
            assert get_pending_title(sid) is None
            assert get_pending_session_attributes(sid) is None
        assert ephemeral.is_known("draft")
        frames = [c.args[1]["data"] for c in layer.group_send.call_args_list]
        assert len([frame for frame in frames if frame["type"] == "ephemeral_admission_failed"]) == 1
        ephemeral.clear()

    asyncio.run(scenario())


def test_shutdown_drains_ephemeral_tasks_and_clears_only_its_provider():
    async def scenario():
        from twicc.agent import ephemeral

        ephemeral.clear()
        admission = ephemeral.reserve("shutdown", "claude_code", "p")
        other = ephemeral.reserve("other", "codex", "p")
        manager = Manager()
        manager._broadcast_info = AsyncMock()
        agent = Agent("shutdown", "p", "/tmp", AgentSettings(), ephemeral=True)
        agent._state_change_callback = manager._on_state_change
        manager._agents[agent.session_id] = agent
        ephemeral.settle(admission)
        await manager.shutdown(timeout=1)
        assert agent.state == AgentState.DEAD
        assert not manager._agents
        assert not manager._ephemeral_cleanup_tasks
        assert not ephemeral.is_known("shutdown")
        assert ephemeral.is_known(other.draft_session_id)
        ephemeral.clear()

    asyncio.run(scenario())


def test_normal_running_claim_blocks_creation_but_allows_followup_until_dead():
    from twicc.agent import ephemeral

    ephemeral.clear()
    try:
        admission = ephemeral.reserve("normal", "claude_code", "p", ephemeral=False)
        ephemeral.bind(admission, "canonical-normal")
        ephemeral.mark_registered("canonical-normal")
        ephemeral.settle(admission)
        assert ephemeral.pending_snapshot() == []
        assert not ephemeral.is_known("normal")
        for sid in ("normal", "canonical-normal"):
            ephemeral.check_readonly(sid)  # Normal send/resume checks remain valid.
            with pytest.raises(SendDeliveryError) as error:
                ephemeral.reserve(sid, "codex", "p")
            assert error.value.code == "agent_starting"
        ephemeral.agent_ended("canonical-normal")
        second = ephemeral.reserve("normal", "codex", "p")
        assert second.ephemeral
    finally:
        ephemeral.clear()


def test_direct_normal_factory_claim_blocks_other_provider_ephemeral_start():
    async def scenario():
        from twicc.agent import ephemeral

        ephemeral.clear()
        entered = asyncio.Event()
        release = asyncio.Event()
        normal = Manager()
        normal.notify_session_bound = AsyncMock()
        normal._register_and_start = AsyncMock()
        other = Manager()
        other.provider = Provider.CODEX
        other._create_agent = AsyncMock()

        async def factory(*args, **kwargs):
            entered.set()
            await release.wait()
            return Agent("normal-canonical", "p", "/tmp", AgentSettings())

        normal._create_agent = AsyncMock(side_effect=factory)
        task = asyncio.create_task(
            normal._start_agent("mixed-direct", "p", "/tmp", "normal", False, settings=AgentSettings())
        )
        await entered.wait()
        with pytest.raises(SendDeliveryError) as error:
            await other._start_agent(
                "mixed-direct", "p", "/tmp", "ephemeral", False, settings=AgentSettings(), ephemeral=True
            )
        assert error.value.code == "agent_starting"
        other._create_agent.assert_not_called()
        release.set()
        await task
        assert not ephemeral.is_known("mixed-direct")
        ephemeral.clear()

    asyncio.run(scenario())
