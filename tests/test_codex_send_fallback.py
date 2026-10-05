"""Rejected Codex steering falls back without duplicating accepted input."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from openai_codex.errors import JsonRpcError, TransportClosedError

from twicc.agent import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.helpers import AgentSettings


class Turn:
    def __init__(self, turn_id, deliveries, failures=()):
        self.id = turn_id
        self.deliveries = deliveries
        self.failures = list(failures)
        self.finished = asyncio.Event()
        self.events = []

    async def steer(self, items):
        if self.failures:
            raise self.failures.pop(0)
        self.deliveries.append((self.id, items))

    async def stream(self):
        await self.finished.wait()
        for event in self.events:
            yield event


class Thread:
    id = "thread"

    def __init__(self, deliveries):
        self.deliveries = deliveries
        self.failures = []
        self.opened = []
        self.on_open = None
        self.release_open = None

    async def turn_with_policy(self, items, **kwargs):
        if self.on_open:
            self.on_open()
        if self.release_open:
            await self.release_open.wait()
        if self.failures:
            raise self.failures.pop(0)
        turn = Turn("new", self.deliveries)
        self.opened.append(turn)
        self.deliveries.append((turn.id, items))
        return turn


def fixture():
    deliveries = []
    thread = Thread(deliveries)
    agent = CodexAgent(
        "session", "project", "/tmp", AgentSettings(permission_mode="yolo"),
        SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))), thread,
    )
    agent._set_state(AgentState.ASSISTANT_TURN)
    agent._current_turn = Turn("old", deliveries, [JsonRpcError(-32600, "no active turn to steer")])
    agent._current_turn_ready.set()
    agent._reconcile_context = AsyncMock()
    agent._broadcast_stream_event = AsyncMock()
    agent._notify_state_change = AsyncMock()
    agent._handle_error = AsyncMock()
    agent._try_arm_subagent_hold = AsyncMock(return_value=False)
    return agent, thread, deliveries


async def cleanup(agent):
    if agent._turn_task:
        agent._turn_task.cancel()
        await asyncio.gather(agent._turn_task, return_exceptions=True)


def test_rejected_steer_opens_turn_and_preserves_images_and_context():
    async def run():
        agent, thread, deliveries = fixture()
        image = {"source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}}
        try:
            with patch("twicc.providers.codex.agent.agent.apply_pending_context", return_value="context\nFollow-up") as context:
                assert await agent.send("Follow-up", images=[image]) is True
                await asyncio.sleep(0)
                assert context.call_count == 1
            assert len(deliveries) == 1
            assert deliveries[0][0] == "new"
            assert deliveries[0][1][0].url == "data:image/png;base64,YWJj"
            assert deliveries[0][1][1].text == "context\nFollow-up"
            assert agent._current_turn is thread.opened[0]
            assert agent.state == AgentState.ASSISTANT_TURN
            agent._handle_error.assert_not_awaited()
        finally:
            await cleanup(agent)
    asyncio.run(run())


@pytest.mark.parametrize("final_failure", [False, True])
def test_rejected_open_retries_current_turn_once(final_failure):
    async def run():
        agent, thread, deliveries = fixture()
        current = Turn("current", deliveries, [JsonRpcError(-32600, "final rejection")] if final_failure else [])
        thread.on_open = lambda: setattr(agent, "_current_turn", current)
        thread.failures = [JsonRpcError(-32600, "turn start rejected")]
        with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
            if final_failure:
                with pytest.raises(RuntimeError, match="final rejection"):
                    await agent.send("Follow-up")
                assert deliveries == []
            else:
                assert await agent.send("Follow-up") is True
                assert [(turn_id, items[0].text) for turn_id, items in deliveries] == [("current", "Follow-up")]
        assert agent._turn_task is None
        agent._handle_error.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["steer", "start"])
@pytest.mark.parametrize("failure", [TimeoutError("timeout"), TransportClosedError("closed"), JsonRpcError(-32603, "internal error")])
def test_ambiguous_failure_does_not_retry(stage, failure):
    async def run():
        agent, thread, deliveries = fixture()
        if stage == "steer":
            agent._current_turn.failures = [failure]
        else:
            thread.failures = [failure]
        with (
            patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text),
            pytest.raises((RuntimeError, TransportClosedError)),
        ):
            await agent.send("Follow-up")
        assert deliveries == []
        assert thread.opened == []
        assert agent._turn_task is None
    asyncio.run(run())


def test_fallback_waits_for_open_acceptance_before_acknowledging():
    async def run():
        agent, thread, deliveries = fixture()
        thread.release_open = asyncio.Event()
        with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
            send = asyncio.create_task(agent.send("Follow-up"))
            try:
                await asyncio.sleep(0.02)
                assert not send.done()
                assert deliveries == []
                thread.release_open.set()
                assert await send is True
            finally:
                send.cancel()
                await asyncio.gather(send, return_exceptions=True)
                await cleanup(agent)
    asyncio.run(run())


def test_replacement_stream_survives_previous_consumer_cleanup():
    async def run():
        agent, thread, deliveries = fixture()
        old = agent._current_turn
        old.events = [SimpleNamespace(method="turn/completed", payload=SimpleNamespace(thread_id="session"))]
        agent._active_tools["old-tool"] = {"name": "exec_command", "input": {}}
        logging_patch = patch("twicc.providers.codex.agent.agent.log_stream_event")
        logging_patch.start()
        previous_task = asyncio.create_task(agent._run_turn("", None, turn_handle=old))
        agent._turn_task = previous_task
        try:
            await asyncio.sleep(0)
            with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
                send = asyncio.create_task(agent.send("Follow-up"))
                await asyncio.sleep(0)
                old.finished.set()
                assert await send is True
            await previous_task
            await asyncio.sleep(0)
            assert not previous_task.cancelled()
            assert agent._active_tools == {}
            assert agent._current_turn is thread.opened[0]
            assert agent._current_turn_ready.is_set()
            assert agent.state == AgentState.ASSISTANT_TURN
            thread.opened[0].finished.set()
            await agent._turn_task
            assert agent.state == AgentState.USER_TURN
            assert agent._current_turn is None
            assert not agent._current_turn_ready.is_set()
            assert len(deliveries) == 1
        finally:
            await cleanup(agent)
            logging_patch.stop()
    asyncio.run(run())


def test_normal_send_keeps_settings_and_stream_lifecycle():
    async def run():
        agent, thread, deliveries = fixture()
        agent._set_state(AgentState.USER_TURN)
        agent._current_turn = None
        agent._current_turn_ready.clear()
        try:
            with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
                assert await agent.send("New turn") is True
                await asyncio.sleep(0.02)
            assert len(deliveries) == 1
            assert deliveries[0][1][0].text == "New turn"
            assert agent._current_turn is thread.opened[0]
            thread.opened[0].finished.set()
            await agent._turn_task
            assert agent.state == AgentState.USER_TURN
        finally:
            await cleanup(agent)
    asyncio.run(run())


@pytest.mark.parametrize("open_failure", [None, TimeoutError("ambiguous open")])
def test_old_completion_waits_for_pending_delivery(open_failure):
    async def run():
        agent, thread, deliveries = fixture()
        old = agent._current_turn
        previous_task = asyncio.create_task(agent._run_turn("", None, turn_handle=old))
        agent._turn_task = previous_task
        thread.release_open = asyncio.Event()
        if open_failure:
            thread.failures = [open_failure]
        try:
            await asyncio.sleep(0)
            with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
                send = asyncio.create_task(agent.send("Follow-up"))
                await asyncio.sleep(0.02)
                old.finished.set()
                await asyncio.sleep(0.02)
                assert agent.state == AgentState.ASSISTANT_TURN
                assert not previous_task.done()
                thread.release_open.set()
                if open_failure:
                    with pytest.raises(RuntimeError, match="ambiguous open"):
                        await send
                    await previous_task
                    assert agent.state == AgentState.USER_TURN
                    assert deliveries == []
                else:
                    assert await send is True
                    await previous_task
                    assert agent.state == AgentState.ASSISTANT_TURN
                    assert len(deliveries) == 1
        finally:
            await cleanup(agent)
    asyncio.run(run())


def test_shutdown_during_old_stream_drain_does_not_resurrect_agent():
    async def run():
        agent, thread, deliveries = fixture()
        old = agent._current_turn

        async def stream():
            await old.finished.wait()
            raise TransportClosedError("transport closed while draining")
            yield  # Keep this an async generator.

        old.stream = stream

        async def dead():
            agent._set_state(AgentState.DEAD)

        agent._transition_to_dead = dead
        previous_task = asyncio.create_task(agent._run_turn("", None, turn_handle=old))
        agent._turn_task = previous_task
        try:
            await asyncio.sleep(0)
            with patch("twicc.providers.codex.agent.agent.apply_pending_context", side_effect=lambda _, text: text):
                send = asyncio.create_task(agent.send("Follow-up"))
                await asyncio.sleep(0.02)
                assert len(deliveries) == 1  # turn/start already accepted.
                old.finished.set()
                with pytest.raises(TransportClosedError):
                    await send
            await previous_task
            assert agent.state == AgentState.DEAD
            assert agent._turn_task is previous_task
            assert agent._current_turn is None
        finally:
            await cleanup(agent)
    asyncio.run(run())
