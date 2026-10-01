"""Heartbeat overtaking, bounded admission, and real Channels teardown."""

import asyncio
import threading
from contextlib import asynccontextmanager, suppress

import pytest
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import InMemoryChannelLayer

from twicc.providers.db_writer import (
    get_db_write_lock, run_under_db_write_lock, start_db_writer, stop_db_writer,
)


PING = {"type": "websocket.receive", "text": '{"type":"ping"}'}
PONG = {"type": "websocket.send", "text": '{"type": "pong"}'}
DISCONNECT = {"type": "websocket.disconnect", "code": 1000}


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@asynccontextmanager
async def writer_running():
    start_db_writer()
    try:
        yield
    finally:
        await stop_db_writer()


class Wire:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.read_count = 0
        self.receiving = 0
        self.send_error = None

    async def receive(self):
        self.receiving += 1
        try:
            event = await self.incoming.get()
            self.read_count += 1
            if isinstance(event, Exception):
                raise event
            return event
        finally:
            self.receiving -= 1

    async def send(self, event):
        if self.send_error:
            raise self.send_error
        await self.outgoing.put(event)

    def put(self, *events):
        for event in events:
            self.incoming.put_nowait(event)

    async def output(self):
        return await asyncio.wait_for(self.outgoing.get(), 2)


@asynccontextmanager
async def transport_app(application, wire=None):
    from twicc.websocket_transport import HeartbeatTransport

    wire = wire or Wire()
    transport = HeartbeatTransport(wire.receive, wire.send, AsyncJsonWebsocketConsumer.encode_json)
    task = asyncio.create_task(transport.run(lambda receive, send: application(transport, receive, send)))
    try:
        yield wire, transport, task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert wire.receiving == 0
        assert transport._reader.done()


def test_ping_overtakes_blocked_session_viewed():
    @writer_running()
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        handled = []

        async def app(transport, receive, send):
            await send({"type": "websocket.accept"})
            transport.enable_heartbeat()
            while True:
                event = await receive()
                if event == DISCONNECT:
                    return
                started.set()
                await run_under_db_write_lock(lambda: release.wait())
                handled.append(event["text"])

        async with transport_app(app) as (wire, transport, task):
            await wire.output()
            lock = get_db_write_lock()
            await lock.acquire()
            try:
                wire.put({"type": "websocket.receive", "text": "session_viewed"})
                await started.wait()
                wire.put(PING, {"type": "websocket.receive", "text": "next"})
                assert await wire.output() == PONG
                assert not handled
            finally:
                release.set()
                lock.release()
            wire.put(DISCONNECT)
            await asyncio.wait_for(task, 2)
            assert handled == ["session_viewed", "next"]

    asyncio.run(run())


def test_ping_during_initial_snapshot():
    async def run():
        release = asyncio.Event()

        async def app(transport, receive, send):
            await send({"type": "websocket.accept"})
            transport.enable_heartbeat()
            await send({"type": "websocket.send", "text": "snapshot-1"})
            await release.wait()
            await send({"type": "websocket.send", "text": "snapshot-2"})
            assert (await receive())["text"] == "command"
            await send({"type": "websocket.send", "text": "command-result"})

        async with transport_app(app) as (wire, transport, task):
            await wire.output()
            assert (await wire.output())["text"] == "snapshot-1"
            wire.put({"type": "websocket.receive", "text": "command"}, PING)
            assert await wire.output() == PONG
            release.set()
            assert (await wire.output())["text"] == "snapshot-2"
            assert (await wire.output())["text"] == "command-result"
            await task

    asyncio.run(run())


@pytest.mark.parametrize("event", [
    {"type": "websocket.receive", "text": "{"},
    {"type": "websocket.receive", "text": "[]"},
    {"type": "websocket.receive", "text": "null"},
    {"type": "websocket.receive", "bytes": b'{"type":"ping"}'},
    {"type": "websocket.receive", "text": '{"type":"ping","padding":"' + "x" * 1024 + '"}'},
    {"type": "websocket.receive", "text": '{"type":"ping","padding":"' + "é" * 510 + '"}'},
])
def test_non_fast_path_frames_are_forwarded_unchanged(event):
    async def run():
        async def app(transport, receive, send):
            transport.enable_heartbeat()
            assert await receive() is event
        async with transport_app(app) as (wire, transport, task):
            wire.put(event)
            await asyncio.wait_for(task, 2)
            assert wire.outgoing.empty()
    asyncio.run(run())


@pytest.mark.parametrize(
    "payload,count",
    [("x", 32), ("x" * (8 * 1024 * 1024), 2), ("é" * (4 * 1024 * 1024), 2)],
    ids=["event-count", "byte-count", "unicode-byte-count"],
)
def test_fifo_bounds_hold_one_pending_upstream_event(payload, count):
    async def run():
        release = asyncio.Event()
        seen = []
        events = [{"type": "websocket.receive", "text": payload, "sequence": i} for i in range(count + 3)]
        async def app(transport, receive, send):
            await release.wait()
            for _ in events:
                seen.append((await receive())["sequence"])
        async with transport_app(app) as (wire, transport, task):
            wire.put(*events)
            await eventually(lambda: wire.read_count >= count + 1)
            await asyncio.sleep(0.01)
            assert wire.read_count == count + 1
            release.set()
            await asyncio.wait_for(task, 2)
            assert seen == list(range(count + 3))
    asyncio.run(run())


def test_oversized_event_is_admitted_only_when_fifo_empty():
    async def run():
        release = asyncio.Event()
        oversized = {"type": "websocket.receive", "bytes": b"x" * (16 * 1024 * 1024 + 1)}
        async def app(transport, receive, send):
            await release.wait()
            assert (await receive())["text"] == "first"
            assert await receive() is oversized
            assert (await receive())["text"] == "last"
        async with transport_app(app) as (wire, transport, task):
            wire.put({"type": "websocket.receive", "text": "first"}, oversized,
                     {"type": "websocket.receive", "text": "last"})
            await eventually(lambda: wire.read_count == 2)
            await asyncio.sleep(0.01)
            assert wire.read_count == 2
            release.set()
            await asyncio.wait_for(task, 2)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["receive", "send", "pong"])
def test_transport_failure_delivers_one_disconnect_after_admitted_events(failure):
    async def run():
        release = asyncio.Event()
        seen = []
        error = OSError("wire failed")
        async def app(transport, receive, send):
            transport.enable_heartbeat()
            await release.wait()
            if failure == "send":
                with suppress(OSError):
                    await send({"type": "websocket.send", "text": "business"})
            seen.append(await receive())
            seen.append(await receive())
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(receive(), 0.02)
        async with transport_app(app) as (wire, transport, task):
            event = {"type": "websocket.receive", "text": "admitted"}
            wire.put(event)
            await eventually(lambda: wire.read_count == 1)
            if failure == "receive":
                wire.put(error)
            else:
                wire.send_error = error
                if failure == "pong":
                    wire.put(PING)
            release.set()
            await asyncio.wait_for(task, 2)
            assert seen == [event, {"type": "websocket.disconnect", "code": 1006}]
            assert transport.failure is error
            assert wire.outgoing.empty()  # 1006 is never sent as a close frame.
            assert wire.receiving == 0
    asyncio.run(run())


def test_close_disables_queued_pong_before_send_lock_is_available():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        wire = Wire()
        raw_send = wire.send
        async def blocked_send(event):
            if event["type"] == "websocket.accept":
                entered.set()
                await release.wait()
            await raw_send(event)
        wire.send = blocked_send
        async def app(transport, receive, send):
            transport.enable_heartbeat()
            accept = asyncio.create_task(send({"type": "websocket.accept"}))
            await entered.wait()
            wire.put(PING)
            await eventually(lambda: wire.read_count == 1)
            close = asyncio.create_task(send({"type": "websocket.close", "code": 1000}))
            await asyncio.sleep(0)
            release.set()
            await accept
            await close
        async with transport_app(app, wire) as (_, transport, task):
            await asyncio.wait_for(task, 2)
            assert (await wire.output())["type"] == "websocket.accept"
            assert (await wire.output())["type"] == "websocket.close"
            assert wire.outgoing.empty()
    asyncio.run(run())


def test_disconnect_disables_gate_before_fifo_drains():
    async def run():
        release = asyncio.Event()
        async def app(transport, receive, send):
            transport.enable_heartbeat()
            await release.wait()
            assert (await receive())["text"] == "command"
            assert await receive() == DISCONNECT
        async with transport_app(app) as (wire, transport, task):
            wire.put({"type": "websocket.receive", "text": "command"}, DISCONNECT, PING)
            await eventually(lambda: wire.read_count == 2)
            release.set()
            await task
            assert wire.read_count == 2
            assert wire.outgoing.empty()
    asyncio.run(run())


class TrackingLayer(InMemoryChannelLayer):
    def __init__(self):
        super().__init__()
        self.receivers = set()
        self.discard_started = asyncio.Event()
        self.discard_release = asyncio.Event()
        self.discard_release.set()

    async def receive(self, channel):
        task = asyncio.current_task()
        self.receivers.add(task)
        try:
            return await super().receive(channel)
        finally:
            self.receivers.remove(task)

    async def group_discard(self, group, channel):
        self.discard_started.set()
        await self.discard_release.wait()
        await super().group_discard(group, channel)


@asynccontextmanager
async def real_consumer(monkeypatch, settings, *, remote=False, send=None, subscribe=b"test_only"):
    from twicc.asgi import WSConsumer

    settings.TWICC_PASSWORD_HASH = ""
    settings.TWICC_ALLOW_INSECURE_REMOTE = False
    monkeypatch.setattr("twicc.asgi.is_provider_enabled", lambda provider: False)
    layer, wire = TrackingLayer(), Wire()
    monkeypatch.setattr("channels.consumer.get_channel_layer", lambda alias: layer)
    consumer = WSConsumer()
    scope = {"type": "websocket", "path": "/ws/", "query_string": b"subscribe=" + subscribe,
             "headers": [], "client": ("203.0.113.1" if remote else "127.0.0.1", 1234)}
    task = asyncio.create_task(consumer(scope, wire.receive, send or wire.send))
    wire.put({"type": "websocket.connect"})
    try:
        yield consumer, wire, layer, task
    finally:
        layer.discard_release.set()
        task.cancel()
        with suppress(asyncio.CancelledError, OSError):
            await task
        assert not layer.groups.get("updates")
        assert not layer.receivers
        assert wire.receiving == 0
        assert consumer._heartbeat_transport._reader.done()


@pytest.mark.django_db(transaction=True)
def test_auth_failure_accept_never_enables_heartbeat(monkeypatch, settings):
    async def run():
        async with real_consumer(monkeypatch, settings, remote=True) as (consumer, wire, layer, task):
            assert (await wire.output())["type"] == "websocket.accept"
            wire.put(PING)
            assert (await wire.output())["text"] == '{"type": "auth_failure"}'
            assert (await wire.output())["type"] == "websocket.close"
            wire.put(DISCONNECT)
            await asyncio.wait_for(task, 2)
            assert wire.outgoing.empty()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ending", ["receive_failure", "swallowed_send_failure", "disconnect", "partial_connect"])
def test_channels_cleanup_on_transport_and_connection_end(monkeypatch, settings, ending):
    async def run():
        async with real_consumer(monkeypatch, settings) as (consumer, wire, layer, task):
            if ending == "partial_connect":
                wire.send_error = OSError("accept failed after group_add")
                with pytest.raises(OSError):
                    await asyncio.wait_for(task, 2)
                return
            assert (await wire.output())["type"] == "websocket.accept"
            if ending == "receive_failure":
                wire.put(OSError("receive failed"))
            elif ending == "swallowed_send_failure":
                wire.send_error = OSError("send failed")
                await consumer.send_json({"type": "test"})
            else:
                wire.put(DISCONNECT)
            await asyncio.wait_for(task, 2)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("admitted", [False, True])
def test_channels_repeated_cancellation_drains_write_and_cleanup(monkeypatch, settings, admitted):
    @writer_running()
    async def run():
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with real_consumer(monkeypatch, settings) as (consumer, wire, layer, task):
            await wire.output()
            lock = get_db_write_lock()
            if not admitted:
                await lock.acquire()
            async def write():
                started.set()
                await release.wait()
                finished.set()
            async def viewed(content):
                if not admitted:
                    started.set()
                await run_under_db_write_lock(write)
            consumer._handle_session_viewed = viewed
            layer.discard_release.clear()
            wire.put({"type": "websocket.receive", "text": '{"type":"session_viewed"}'})
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
                if admitted:
                    assert lock.locked()
                    assert not finished.is_set()
                release.set()
                await asyncio.wait_for(layer.discard_started.wait(), 2)
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
                layer.discard_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert finished.is_set() is admitted
            finally:
                release.set()
                if not admitted:
                    lock.release()
    asyncio.run(run())


def test_send_failure_wakes_full_fifo_and_discards_only_unadmitted_event():
    async def run():
        release = asyncio.Event()
        seen = []
        async def app(transport, receive, send):
            await release.wait()
            for _ in range(33):
                seen.append(await receive())
        async with transport_app(app) as (wire, transport, task):
            events = [{"type": "websocket.receive", "text": str(i)} for i in range(34)]
            wire.put(*events)
            await eventually(lambda: wire.read_count == 33)
            wire.send_error = OSError("failed while FIFO full")
            with pytest.raises(OSError):
                await transport.send({"type": "websocket.send", "text": "notification"})
            release.set()
            await asyncio.wait_for(task, 2)
            assert seen == [*events[:32], {"type": "websocket.disconnect", "code": 1006}]
            assert wire.read_count == 33
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("ending", ["receive_failure", "swallowed_send_failure", "disconnect"])
def test_channels_transport_end_drains_admitted_write(monkeypatch, settings, ending):
    @writer_running()
    async def run():
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with real_consumer(monkeypatch, settings) as (consumer, wire, layer, task):
            await wire.output()
            async def write():
                started.set()
                await release.wait()
                finished.set()
            async def viewed(content):
                await run_under_db_write_lock(write)
            consumer._handle_session_viewed = viewed
            wire.put({"type": "websocket.receive", "text": '{"type":"session_viewed"}'})
            try:
                await asyncio.wait_for(started.wait(), 2)
                if ending == "swallowed_send_failure":
                    wire.send_error = OSError("send failed during admitted write")
                    await consumer.send_json({"type": "test"})
                elif ending == "receive_failure":
                    wire.put(OSError("receive failed during admitted write"))
                else:
                    wire.put(DISCONNECT)
                await asyncio.sleep(0.01)
                assert not task.done()
                assert get_db_write_lock().locked()
                assert not finished.is_set()
                release.set()
                await asyncio.wait_for(task, 2)
                assert finished.is_set()
                assert not get_db_write_lock().locked()
            finally:
                release.set()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_successful_accept_must_complete_before_fast_heartbeat(monkeypatch, settings):
    async def run():
        accept_entered, accept_release = asyncio.Event(), asyncio.Event()
        snapshot_started, snapshot_done = threading.Event(), threading.Event()

        async def blocked_send(event):
            if event["type"] == "websocket.accept":
                accept_entered.set()
                await accept_release.wait()
            await wire.send(event)

        def read_snapshot():
            snapshot_started.set()
            assert snapshot_done.wait(3)
            return None, None, False

        monkeypatch.setattr("twicc.asgi._resolve_changelog_versions", read_snapshot)
        async with real_consumer(monkeypatch, settings, send=blocked_send, subscribe=b"server_version") as (
            consumer, wire, layer, task,
        ):
            try:
                await asyncio.wait_for(accept_entered.wait(), 2)
                wire.put(PING)
                await eventually(lambda: wire.read_count == 2)
                assert wire.outgoing.empty()
                accept_release.set()
                await wire.output()
                assert await asyncio.to_thread(snapshot_started.wait, 2)
                wire.put(PING)
                assert await wire.output() == PONG
                snapshot_done.set()
                assert '"type": "server_version"' in (await wire.output())["text"]
                assert await wire.output() == PONG  # The pre-accept ping stays in FIFO.
                wire.put(DISCONNECT)
                await asyncio.wait_for(task, 2)
            finally:
                accept_release.set()
                snapshot_done.set()
    asyncio.run(run())
