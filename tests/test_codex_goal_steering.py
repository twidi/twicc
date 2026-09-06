"""Goal follow-ups must reach physical turns without interrupting or duplicating input."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai_codex import AsyncTurnHandle
from openai_codex._message_router import MessageRouter
from openai_codex.errors import JsonRpcError, TransportClosedError
from openai_codex.generated.v2_all import TurnStartedNotification, TurnCompletedNotification
from openai_codex.models import Notification

from twicc.agent import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.helpers import AgentSettings


def event(router, method, turn_id):
    model = TurnStartedNotification if method == "turn/started" else TurnCompletedNotification
    router.route_notification(
        Notification(
            method,
            model.model_validate(
                {
                    "threadId": "parent",
                    "turn": {
                        "id": turn_id,
                        "status": "inProgress" if method == "turn/started" else "completed",
                        "items": [],
                    },
                }
            ),
        )
    )


def agent_fixture():
    router = MessageRouter()
    client = SimpleNamespace(
        register_goal_operation=router.register_goal,
        unregister_goal_operation=router.unregister_goal,
        turn_steer=AsyncMock(return_value=SimpleNamespace(turn_id="a")),
    )
    codex = SimpleNamespace(_client=client, _ensure_initialized=AsyncMock())
    client._sync = SimpleNamespace(_approval_handler=None, _router=router)
    thread = SimpleNamespace(
        id="parent", goal_get=AsyncMock(return_value=None), goal_set=AsyncMock(), goal_clear=AsyncMock()
    )
    agent = CodexAgent("parent", "project", "/tmp", AgentSettings(permission_mode="auto"), codex, thread)
    agent._broadcast_stream_event = AsyncMock()
    agent._notify_state_change = AsyncMock()
    agent._try_arm_subagent_hold = AsyncMock(return_value=False)
    agent._handle_stream_event = AsyncMock()
    return agent, router, client


async def cleanup(agent):
    task = agent._turn_task
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_followup_during_autonomous_goal():
    async def run():
        agent, router, client = agent_fixture()

        async def activate(*args):
            event(router, "turn/started", "a")  # Notification precedes goal/set response.

        agent._thread.goal_set.side_effect = activate
        await agent.run_goal_command("Do the work")
        try:
            assert await asyncio.wait_for(agent.send("Focus on tests"), 0.3) is True
            assert client.turn_steer.await_args.args[:2] == ("parent", "a")
            assert client.turn_steer.await_args.args[2][0]["text"] == "Focus on tests"
        finally:
            await cleanup(agent)

    asyncio.run(run())


def test_followup_waits_across_continuation_boundary():
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        event(router, "turn/started", "a")
        event(router, "turn/completed", "a")
        pending = asyncio.create_task(agent.send("Next constraint"))
        try:
            await asyncio.sleep(0.02)
            assert not pending.done()
            event(router, "turn/started", "b")
            assert await asyncio.wait_for(pending, 0.5) is True
            assert client.turn_steer.await_args.args[:2] == ("parent", "b")
        finally:
            pending.cancel()
            await cleanup(agent)

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", [TransportClosedError("closed"), JsonRpcError(-32600, "cannot steer a compact turn")]
)
def test_delivery_failure_is_not_replayed(failure):
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        event(router, "turn/started", "a")
        client.turn_steer.side_effect = failure
        try:
            with pytest.raises((RuntimeError, TransportClosedError)):
                await asyncio.wait_for(agent.send("Only once"), 0.3)
            assert client.turn_steer.await_count == 1
        finally:
            await cleanup(agent)

    asyncio.run(run())


def test_ordinary_steering_still_uses_existing_handle():
    async def run():
        agent, router, client = agent_fixture()
        agent.state = AgentState.ASSISTANT_TURN
        agent._current_turn = AsyncTurnHandle(agent._codex, "parent", "ordinary")
        agent._current_turn_ready.set()
        assert await agent.send("Follow-up") is True
        assert client.turn_steer.await_args.args[:2] == ("parent", "ordinary")

    asyncio.run(run())


def goal_event(router, status=None):
    from openai_codex.generated.v2_all import ThreadGoalClearedNotification, ThreadGoalUpdatedNotification

    if status is None:
        payload = ThreadGoalClearedNotification.model_validate({"threadId": "parent"})
        method = "thread/goal/cleared"
    else:
        payload = ThreadGoalUpdatedNotification.model_validate(
            {
                "threadId": "parent",
                "goal": {
                    "objective": "Do the work",
                    "status": status,
                    "tokensUsed": 0,
                    "timeUsedSeconds": 0,
                    "threadId": "parent",
                    "createdAt": 0,
                    "updatedAt": 0,
                },
            }
        )
        method = "thread/goal/updated"
    router.route_notification(Notification(method, payload))


@pytest.mark.parametrize(
    "message",
    [
        "expected active turn id `a` but found `b`",
        "no active turn to steer",
    ],
)
def test_explicit_stale_turn_rejection_retries_next_turn_once(message):
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        event(router, "turn/started", "a")

        async def rollover(*args):
            if args[1] == "a":
                event(router, "turn/completed", "a")
                event(router, "turn/started", "b")
                raise JsonRpcError(-32600, message)
            return SimpleNamespace(turn_id="b")

        client.turn_steer.side_effect = rollover
        try:
            assert await agent.send("Follow-up") is True
            assert [call.args[1] for call in client.turn_steer.await_args_list] == ["a", "b"]
        finally:
            await cleanup(agent)

    asyncio.run(run())


@pytest.mark.parametrize("status", [None, "complete", "paused", "blocked"])
def test_terminal_goal_waits_for_active_turn_to_finish(status):
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        event(router, "turn/started", "a")
        goal_event(router, status)
        try:
            await asyncio.sleep(0.03)
            await agent.notify_goal_continuation_stopped()  # A fast rollout watcher must not settle early.
            assert agent.state == AgentState.ASSISTANT_TURN
            assert await agent.send("Final constraint") is True
            event(router, "turn/completed", "a")
            await asyncio.wait_for(agent._turn_task, 0.5)
            assert agent.state == AgentState.USER_TURN
            assert not router.has_goal("parent")
            assert agent._current_turn is None
        finally:
            await cleanup(agent)

    asyncio.run(run())


def test_goal_ends_while_followup_waits():
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        pending = asyncio.create_task(agent.send("Follow-up"))
        await asyncio.sleep(0.02)
        goal_event(router)
        try:
            with pytest.raises(RuntimeError, match="ended"):
                await asyncio.wait_for(pending, 0.5)
            assert client.turn_steer.await_count == 0
        finally:
            await cleanup(agent)

    asyncio.run(run())


def test_missing_next_turn_has_bounded_wait(monkeypatch):
    monkeypatch.setattr("twicc.providers.codex.agent.goal_continuation.GOAL_STEER_WAIT_SECONDS", 0.04)

    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("Do the work")
        try:
            with pytest.raises(RuntimeError, match="no active goal turn"):
                await asyncio.wait_for(agent.send("Follow-up"), 0.5)
            assert client.turn_steer.await_count == 0
        finally:
            await cleanup(agent)
        assert not router.has_goal("parent")

    asyncio.run(run())


def test_failed_goal_set_releases_route():
    async def run():
        agent, router, client = agent_fixture()
        agent._thread.goal_set.side_effect = RuntimeError("set failed")
        with pytest.raises(RuntimeError, match="set failed"):
            await agent.run_goal_command("Do the work")
        assert not router.has_goal("parent")
        assert agent.state == AgentState.USER_TURN

    asyncio.run(run())


def test_clear_does_not_restore_busy_state_after_turn_finishes_during_injection():
    async def run():
        agent, router, client = agent_fixture()
        await agent.run_goal_command("First")
        event(router, "turn/started", "a")

        async def clear():
            goal_event(router)

        async def inject(*args):
            event(router, "turn/completed", "a")
            await asyncio.sleep(0.04)

        agent._thread.goal_clear.side_effect = clear
        agent._thread.inject_user_message = inject
        try:
            await agent.run_goal_command("clear")
            await asyncio.wait_for(agent._turn_task, 0.5)
            assert agent.state == AgentState.USER_TURN
        finally:
            await cleanup(agent)

    asyncio.run(run())


def test_replacement_during_slow_stream_processing_keeps_new_goal_route():
    async def run():
        agent, router, client = agent_fixture()
        gate = asyncio.Event()

        async def slow_handler(ev):
            if ev.method == "turn/started" and ev.payload.turn.id == "a":
                await gate.wait()

        agent._handle_stream_event = slow_handler
        await agent.run_goal_command("First")
        event(router, "turn/started", "a")
        await asyncio.sleep(0.02)
        goal_event(router, "complete")
        event(router, "turn/completed", "a")
        from openai_codex.generated.v2_all import ThreadGoalStatus

        agent._thread.goal_get.return_value = SimpleNamespace(status=ThreadGoalStatus.complete)

        async def clear():
            goal_event(router)

        async def activate(*args):
            goal_event(router, "active")
            event(router, "turn/started", "b")

        agent._thread.goal_clear.side_effect = clear
        agent._thread.goal_set.side_effect = activate
        try:
            await agent.run_goal_command("Second")
            gate.set()
            await asyncio.sleep(0.04)
            assert agent.state == AgentState.ASSISTANT_TURN
            assert await asyncio.wait_for(agent.send("For the second goal"), 0.3) is True
            assert client.turn_steer.await_args.args[1] == "b"
        finally:
            gate.set()
            await cleanup(agent)

    asyncio.run(run())


def test_fast_replacement_drains_all_physical_turn_events():
    async def run():
        agent, router, client = agent_fixture()
        gate = asyncio.Event()
        consumed = []

        async def slow_handler(ev):
            if ev.method == "turn/started" and ev.payload.turn.id == "a":
                await gate.wait()
            if ev.method in ("turn/started", "turn/completed"):
                consumed.append((ev.method, ev.payload.turn.id))

        agent._handle_stream_event = slow_handler
        await agent.run_goal_command("First")
        event(router, "turn/started", "a")
        await asyncio.sleep(0.02)
        goal_event(router, "complete")
        event(router, "turn/completed", "a")

        async def activate(*args):
            goal_event(router, "active")
            event(router, "turn/started", "b")
            goal_event(router, "complete")
            event(router, "turn/completed", "b")

        agent._thread.goal_set.side_effect = activate
        try:
            await agent.run_goal_command("Second")
            gate.set()
            await asyncio.wait_for(agent._turn_task, 0.5)
            assert consumed == [
                ("turn/started", "a"),
                ("turn/completed", "a"),
                ("turn/started", "b"),
                ("turn/completed", "b"),
            ]
        finally:
            gate.set()
            await cleanup(agent)

    asyncio.run(run())


def test_transport_close_during_shutdown_preserves_shutdown_reason():
    async def run():
        agent, router, client = agent_fixture()
        agent.kill_reason = "shutdown"
        agent._codex.close = AsyncMock()
        agent._transition_to_dead = AsyncMock(side_effect=lambda: setattr(agent, "state", AgentState.DEAD))
        await agent.run_goal_command("Work")
        router.fail_all(TransportClosedError("closed"))
        await asyncio.wait_for(agent._turn_task, 0.5)
        assert agent.kill_reason == "shutdown"
        assert agent.error is None
        assert agent.state == AgentState.DEAD
        assert not router.has_goal("parent")

    asyncio.run(run())


def test_cancelled_command_settlement_keeps_goal_consumer():
    async def run():
        agent, router, client = agent_fixture()
        settling = asyncio.Event()
        gate = asyncio.Event()

        async def notify():
            settling.set()
            await gate.wait()

        agent._notify_state_change = notify
        command = asyncio.create_task(agent.run_goal_command("Work"))
        await asyncio.wait_for(settling.wait(), 0.5)
        command.cancel()
        await asyncio.gather(command, return_exceptions=True)
        try:
            assert agent._turn_task is not None
            event(router, "turn/started", "a")
            assert await agent.send("Still reachable") is True
        finally:
            await cleanup(agent)
        assert not router.has_goal("parent")

    asyncio.run(run())
