"""WebSocket send ordering: detached sends on per-session lanes, control barriers.

A fake planner (the admitted send body) blocks on ``asyncio.Event`` objects.
``_settle()`` only yields to the event loop; no test relies on timing sleeps.
"""

import asyncio
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from twicc import asgi
from twicc.agent import ephemeral, send_lanes
from twicc.asgi import WSConsumer

FRAME_TIMEOUT = 5


async def _settle(rounds: int = 30) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _drain_detached() -> None:
    while asgi._DETACHED_TASKS:
        await asyncio.gather(*list(asgi._DETACHED_TASKS), return_exceptions=True)


def _run(coro):
    ephemeral.clear()
    send_lanes._reset_for_tests()
    try:
        asyncio.run(coro)
    finally:
        ephemeral.clear()
        send_lanes._reset_for_tests()


def _error_codes(*consumers) -> list[str]:
    return [
        call.args[0].get("code")
        for consumer in consumers
        for call in consumer.send_json.call_args_list
        if call.args[0].get("type") == "error"
    ]


class FakePlanner:
    """Admitted send body: blocks per message until its gate opens.

    A creation send (one that carries an admission) registers its agent like
    the real manager does, so a later send to the same id is an ordinary
    follow-up instead of a second creation.
    """

    def __init__(self, *names: str) -> None:
        self.gates = {name: asyncio.Event() for name in names}
        self.started = {name: asyncio.Event() for name in names}
        self.delivered_order: list[str] = []
        self.on_admitted = None

    def open_all(self) -> None:
        for gate in self.gates.values():
            gate.set()

    async def __call__(self, content: dict, *, ephemeral_admission=None) -> bool:
        name = content["text"]
        self.started[name].set()
        if self.on_admitted is not None:
            self.on_admitted(content, ephemeral_admission)
        await self.gates[name].wait()
        if ephemeral_admission is not None:
            for sid in (content["session_id"], *ephemeral_admission_bound_ids(ephemeral_admission)):
                ephemeral.mark_registered(sid)
        self.delivered_order.append(name)
        return True


def ephemeral_admission_bound_ids(admission) -> list[str]:
    return [sid for sid, value in ephemeral._claims.items() if value is admission]


def _consumer(planner: FakePlanner) -> WSConsumer:
    consumer = WSConsumer()
    consumer.send_json = AsyncMock()
    consumer._handle_send_message_admitted = planner
    return consumer


def _send(session_id: str, text: str, *, refs: bool = False) -> dict:
    frame = {"type": "send_message", "session_id": session_id, "project_id": "p", "provider": "codex", "text": text}
    if refs:
        # A canonical UUID per message: the inline shape check rejects any other id.
        frame["attachments"] = [{"bucket": "b", "id": str(uuid.uuid5(uuid.NAMESPACE_URL, text))}]
    else:
        frame["images"] = []
    return frame


class FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.soft_kill_started = asyncio.Event()
        self.soft_kill_release = asyncio.Event()
        self.soft_kill_release.set()
        self.force_kill_called = asyncio.Event()
        self.interrupt_called = asyncio.Event()

    def set_broadcast_callback(self, callback) -> None:
        pass

    def get_active_agents(self):
        return []

    async def kill_agent(self, session_id: str, reason: str = "manual") -> bool:
        self.calls.append(f"kill:{session_id}")
        self.soft_kill_started.set()
        await self.soft_kill_release.wait()  # The grace window.
        return True

    async def hard_kill_agent(self, session_id: str, reason: str = "force") -> bool:
        self.calls.append(f"force:{session_id}")
        self.force_kill_called.set()
        return True

    async def interrupt_agent(self, session_id: str) -> bool:
        self.calls.append(f"interrupt:{session_id}")
        self.interrupt_called.set()
        return True


def _patch_control(registry: FakeRegistry) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("twicc.asgi.get_agent_manager_registry", return_value=registry))
    stack.enter_context(patch("twicc.asgi.get_session_provider", new=AsyncMock(return_value=None)))
    return stack


async def _frame(consumer: WSConsumer, frame: dict) -> None:
    """Dispatch one frame; the receive loop must never block on a send."""
    await asyncio.wait_for(consumer.receive_json(frame), timeout=FRAME_TIMEOUT)


def test_two_connections_mixed_refs_and_legacy_sends_keep_order_without_agent_starting():
    async def scenario():
        planner = FakePlanner("first", "second", "third")
        conn_a, conn_b = _consumer(planner), _consumer(planner)
        unrelated_frame_handled = asyncio.Event()

        with patch("twicc.presence.touch", side_effect=lambda *_: unrelated_frame_handled.set()):
            await _frame(conn_a, _send("draft", "first"))
            await planner.started["first"].wait()
            await _frame(conn_b, _send("draft", "second", refs=True))
            await _frame(conn_a, _send("draft", "third"))
            await _frame(conn_b, {"type": "presence", "device_class": "desktop"})
        assert unrelated_frame_handled.is_set()

        # Later gates open first; the lane still holds them behind "first".
        planner.gates["third"].set()
        planner.gates["second"].set()
        await _settle()
        assert planner.delivered_order == []
        assert not planner.started["second"].is_set()

        planner.gates["first"].set()
        await _drain_detached()
        assert planner.delivered_order == ["first", "second", "third"]
        assert "agent_starting" not in _error_codes(conn_a, conn_b)
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_three_rapid_sends_to_resumed_session_while_first_plan_waits():
    async def scenario():
        planner = FakePlanner("first", "second", "third")
        consumer = _consumer(planner)
        ephemeral_admission = ephemeral.reserve("resumed", "codex", "p", ephemeral=False)
        ephemeral.mark_registered("resumed")
        ephemeral.settle(ephemeral_admission)
        unrelated_frame_handled = asyncio.Event()

        with patch("twicc.presence.touch", side_effect=lambda *_: unrelated_frame_handled.set()):
            await _frame(consumer, _send("resumed", "first", refs=True))
            await _frame(consumer, _send("resumed", "second"))
            await _frame(consumer, _send("resumed", "third", refs=True))
            await planner.started["first"].wait()
            await _frame(consumer, {"type": "presence"})
        assert unrelated_frame_handled.is_set()
        assert not planner.started["second"].is_set()

        planner.open_all()
        await _drain_detached()
        assert planner.delivered_order == ["first", "second", "third"]
        assert _error_codes(consumer) == []
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_shape_error_is_answered_inline_without_a_lane():
    async def scenario():
        planner = FakePlanner()
        consumer = _consumer(planner)
        await _frame(consumer, {"type": "send_message", "project_id": "p", "text": "x", "request_id": "r1"})
        frame = consumer.send_json.call_args.args[0]
        assert frame["code"] == "invalid_request"
        assert frame["request_id"] == "r1"
        assert not asgi._DETACHED_TASKS
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_soft_stop_and_interrupt_wait_behind_earlier_sends_without_holding_the_lane():
    async def scenario():
        planner = FakePlanner("first", "after-stop")
        consumer = _consumer(planner)
        registry = FakeRegistry()
        registry.soft_kill_release.clear()

        with _patch_control(registry):
            await _frame(consumer, _send("sess", "first"))
            await planner.started["first"].wait()
            await _frame(consumer, {"type": "kill_process", "session_id": "sess"})
            await _frame(consumer, {"type": "interrupt_session", "session_id": "sess"})
            await _settle()
            assert registry.calls == []

            planner.gates["first"].set()
            await registry.soft_kill_started.wait()
            await registry.interrupt_called.wait()
            assert planner.delivered_order == ["first"]
            assert registry.calls == ["kill:sess", "interrupt:sess"]

            # The soft stop is still in its grace window, yet a new send runs:
            # the barrier never holds the lane while the control executes.
            planner.gates["after-stop"].set()
            await _frame(consumer, _send("sess", "after-stop"))
            await asyncio.wait_for(planner.started["after-stop"].wait(), timeout=FRAME_TIMEOUT)
            assert not registry.soft_kill_release.is_set()

            registry.soft_kill_release.set()
            await _drain_detached()
        assert planner.delivered_order == ["first", "after-stop"]
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_force_kill_bypasses_a_send_blocked_in_planning():
    async def scenario():
        planner = FakePlanner("first")
        consumer = _consumer(planner)
        registry = FakeRegistry()

        with _patch_control(registry):
            await _frame(consumer, _send("sess", "first", refs=True))
            await planner.started["first"].wait()
            await _frame(consumer, {"type": "kill_process", "session_id": "sess", "force": True})
            await asyncio.wait_for(registry.force_kill_called.wait(), timeout=FRAME_TIMEOUT)
            assert force_kill_called_before_delivery(planner, registry)

            # Planning resumes: the admitted send may still start an agent
            # under the existing process-only kill contract.
            planner.gates["first"].set()
            await _drain_detached()
        assert planner.delivered_order == ["first"]
        assert send_lanes._live_keys() == set()

    _run(scenario())


def force_kill_called_before_delivery(planner: FakePlanner, registry: FakeRegistry) -> bool:
    return registry.force_kill_called.is_set() and planner.delivered_order == []


def test_force_kill_bypasses_a_soft_stop_in_its_grace_window():
    async def scenario():
        planner = FakePlanner("first")
        consumer = _consumer(planner)
        registry = FakeRegistry()
        registry.soft_kill_release.clear()

        with _patch_control(registry):
            await _frame(consumer, {"type": "kill_process", "session_id": "sess"})
            await registry.soft_kill_started.wait()
            # A send queues behind nothing (the soft stop holds no lane) and
            # blocks in planning; the force kill still lands immediately.
            await _frame(consumer, _send("sess", "first"))
            await planner.started["first"].wait()
            await _frame(consumer, {"type": "kill_process", "session_id": "sess", "force": True})
            await asyncio.wait_for(registry.force_kill_called.wait(), timeout=FRAME_TIMEOUT)
            assert registry.calls == ["kill:sess", "force:sess"]

            registry.soft_kill_release.set()
            planner.gates["first"].set()
            await _drain_detached()
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_codex_bind_aliases_the_canonical_lane_for_sends_and_barriers():
    async def scenario():
        planner = FakePlanner("create", "to-canonical", "to-draft")
        consumer = _consumer(planner)
        registry = FakeRegistry()
        canonical_bound = asyncio.Event()

        def bind_on_creation(content, admission):
            if content["text"] == "create":
                # What ``_start_agent_with_admission`` does once Codex mints its id.
                ephemeral.bind(admission, "canonical")
                canonical_bound.set()

        planner.on_admitted = bind_on_creation

        with _patch_control(registry):
            await _frame(consumer, _send("draft", "create"))
            await canonical_bound.wait()
            await _frame(consumer, _send("canonical", "to-canonical", refs=True))
            await _frame(consumer, {"type": "interrupt_session", "session_id": "canonical"})
            await _frame(consumer, _send("draft", "to-draft"))
            await _frame(consumer, {"type": "kill_process", "session_id": "canonical"})
            await _settle()
            assert registry.calls == []
            assert not planner.started["to-canonical"].is_set()

            planner.gates["create"].set()
            await planner.started["to-canonical"].wait()
            # The draft's own send is over, but the alias stays while the
            # canonical send and the queued controls still hold references.
            assert send_lanes._live_keys() == {"draft", "canonical"}
            await _settle()
            assert registry.calls == []

            planner.open_all()
            await _drain_detached()

        assert planner.delivered_order == ["create", "to-canonical", "to-draft"]
        assert registry.calls == ["interrupt:canonical", "kill:canonical"]
        assert "agent_starting" not in _error_codes(consumer)
        assert send_lanes._live_keys() == set()

    _run(scenario())


def test_registry_clears_after_a_failed_send():
    async def scenario():
        consumer = WSConsumer()
        consumer.send_json = AsyncMock()
        consumer._handle_send_message_admitted = AsyncMock(side_effect=RuntimeError("boom"))
        await _frame(consumer, _send("sess", "first"))
        await _drain_detached()
        assert send_lanes._live_keys() == set()
        assert ephemeral.pending_snapshot() == []

    _run(scenario())


def test_cancelled_send_releases_lane_and_admission():
    async def scenario():
        planner = FakePlanner("first")
        consumer = _consumer(planner)
        await _frame(consumer, _send("draft", "first"))
        await planner.started["first"].wait()
        (task,) = list(asgi._DETACHED_TASKS)
        task.cancel()
        await _drain_detached()
        assert send_lanes._live_keys() == set()
        assert ephemeral.pending_snapshot() == []
        assert not ephemeral._claims

    _run(scenario())


def test_transport_keeps_dispatching_and_disconnect_never_cancels_an_admitted_send(
        transactional_db, settings, monkeypatch):
    # ``transactional_db``: Channels closes old DB connections around each
    # dispatched event, which needs database access once an earlier test
    # initialized a connection.
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr("twicc.asgi.scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: FakeRegistry())
    # Provider on-connect messages (auth, usage) are outside this test's scope.
    monkeypatch.setattr("twicc.asgi.is_provider_enabled", lambda provider: False)

    async def scenario():
        planner = FakePlanner("first")

        async def admitted(self, content, *, ephemeral_admission=None):
            return await planner(content, ephemeral_admission=ephemeral_admission)

        unrelated_frame_handled = asyncio.Event()
        outgoing_broadcast_sent = asyncio.Event()
        with (
            patch.object(WSConsumer, "_handle_send_message_admitted", new=admitted),
            patch("twicc.presence.touch", side_effect=lambda *_: unrelated_frame_handled.set()),
        ):
            comm = WebsocketCommunicator(WSConsumer.as_asgi(), "/ws/?subscribe=ordering_probe")
            connected, _ = await comm.connect()
            assert connected

            await comm.send_json_to(_send("draft", "first", refs=True))
            await asyncio.wait_for(planner.started["first"].wait(), timeout=FRAME_TIMEOUT)

            await comm.send_json_to({"type": "presence"})
            await asyncio.wait_for(unrelated_frame_handled.wait(), timeout=FRAME_TIMEOUT)

            await get_channel_layer().group_send(
                "updates", {"type": "broadcast", "data": {"type": "ordering_probe"}},
            )
            frame = await comm.receive_json_from(timeout=FRAME_TIMEOUT)
            assert frame == {"type": "ordering_probe"}
            outgoing_broadcast_sent.set()

            await comm.disconnect()
            assert planner.delivered_order == []
            planner.gates["first"].set()
            await _drain_detached()

        assert unrelated_frame_handled.is_set()
        assert outgoing_broadcast_sent.is_set()
        assert planner.delivered_order == ["first"]
        assert send_lanes._live_keys() == set()

    _run(scenario())


@pytest.fixture(autouse=True)
def _no_leftover_detached_tasks():
    yield
    asgi._DETACHED_TASKS.clear()
