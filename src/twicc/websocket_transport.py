"""Authenticated heartbeat transport around sequential Channels dispatch."""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable

import orjson

logger = logging.getLogger(__name__)

Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

MAX_HEARTBEAT_BYTES = 1024
MAX_QUEUED_EVENTS = 32
MAX_QUEUED_BYTES = 16 * 1024 * 1024


class HeartbeatTransport:
    """Own upstream reads, bounded business admission, and serialized writes.

    Only authenticated pings bypass the FIFO. Terminal state occupies no FIFO
    capacity, so a failed producer always wakes the consumer after admitted work.
    """

    def __init__(self, receive: Receive, send: Send, encode_json: Callable[[dict], Awaitable[str]]):
        self._upstream_receive = receive
        self._upstream_send = send
        self._encode_json = encode_json
        self._queue = deque()
        self._queued_bytes = 0
        self._changed = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._reader = None
        self._heartbeat_enabled = False
        self._closed = False
        self._terminal = None
        self._terminal_delivered = False
        self.failure: Exception | None = None
        self._termination_logged = False

    def enable_heartbeat(self) -> None:
        """Enable only after the consumer authorizes and completes accept."""
        if not self._closed and self._terminal is None:
            self._heartbeat_enabled = True

    def disable_heartbeat(self) -> None:
        self._heartbeat_enabled = False

    def _finish(self, event: dict) -> None:
        self.disable_heartbeat()
        if self._terminal is None:
            self._terminal = event
        self._changed.set()

    def _fail(self, exc: Exception) -> None:
        if self.failure is None:
            self.failure = exc
            self._log_termination(type(exc).__name__)
        # 1006 describes an abnormal received termination; it is never sent.
        self._finish({"type": "websocket.disconnect", "code": 1006})
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()

    def _log_termination(self, reason):
        if not self._termination_logged:
            self._termination_logged = True
            logger.warning("WebSocket transport terminated connection=%x reason=%s queued_events=%d queued_bytes=%d",
                           id(self), reason, len(self._queue), self._queued_bytes)

    async def _join_reader(self) -> None:
        if self._reader is None or self._reader is asyncio.current_task():
            return
        cancelled = False
        while not self._reader.done():
            try:
                await asyncio.shield(self._reader)
            except asyncio.CancelledError:
                cancelled |= bool(asyncio.current_task().cancelling())
        # The reader handles wire errors itself. Retrieve its cancellation.
        if not self._reader.cancelled():
            self._reader.result()
        if cancelled:
            raise asyncio.CancelledError

    async def run(self, application: Callable[[Receive, Send], Awaitable[None]]) -> None:
        """Drain Channels cleanup and admitted writes before leaving the scope."""
        self._reader = asyncio.create_task(self._read(), name="websocket-transport-reader")
        consumer = asyncio.create_task(application(self.receive, self.send), name="websocket-consumer")
        try:
            try:
                await asyncio.shield(consumer)
            except asyncio.CancelledError:
                self.disable_heartbeat()
                self._reader.cancel()
                # Deliver cancellation once. Repeated caller cancellations must
                # not interrupt Channels' own receive-task cleanup.
                consumer.cancel()
                while not consumer.done():
                    try:
                        await asyncio.shield(consumer)
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        break
                if not consumer.cancelled():
                    exc = consumer.exception()
                    if exc is not None:
                        logger.warning("WebSocket consumer failed during cancellation", exc_info=exc)
                raise
        finally:
            self.disable_heartbeat()
            self._closed = True
            self._reader.cancel()
            try:
                await self._join_reader()
            finally:
                self._queue.clear()
                self._queued_bytes = 0

    async def receive(self) -> dict:
        while True:
            if self._queue:
                event, size = self._queue.popleft()
                self._queued_bytes -= size
                self._changed.set()
                return event
            if self._terminal is not None and not self._terminal_delivered:
                self._terminal_delivered = True
                return self._terminal
            self._changed.clear()
            await self._changed.wait()

    async def send(self, event: dict) -> None:
        if event["type"] == "websocket.close":
            self.disable_heartbeat()
            self._closed = True
        async with self._send_lock:
            await self._send_raw(event)

    async def _send_raw(self, event: dict) -> None:
        try:
            if self.failure is not None or (self._closed and event["type"] != "websocket.close"):
                raise OSError("WebSocket transport is closed")
            await self._upstream_send(event)
        except Exception as exc:
            self._fail(exc)
            await self._join_reader()
            raise

    async def _pong(self) -> None:
        text = await self._encode_json({"type": "pong"})
        async with self._send_lock:
            if self._heartbeat_enabled:
                await self._send_raw({"type": "websocket.send", "text": text})

    async def _read(self) -> None:
        try:
            while self._terminal is None:
                event = await self._upstream_receive()
                if event["type"] == "websocket.disconnect":
                    code = event.get('code', 1000)
                    if code not in (1000, 1001):
                        self._log_termination(f'disconnect-{code}')
                    self._finish(event)
                    return
                text = event.get("text")
                size = len(text.encode("utf-8")) if text is not None else len(event.get("bytes") or b"")
                if (
                    self._heartbeat_enabled and event["type"] == "websocket.receive"
                    and text is not None and size <= MAX_HEARTBEAT_BYTES
                ):
                    try:
                        value = orjson.loads(text)
                    except orjson.JSONDecodeError:
                        value = None
                    if isinstance(value, dict) and value.get("type") == "ping":
                        await self._pong()
                        continue
                # One oversized frame is admitted only into an empty FIFO.
                # The ASGI server retains responsibility for its frame limit.
                while self._queue and (
                    len(self._queue) >= MAX_QUEUED_EVENTS or self._queued_bytes + size > MAX_QUEUED_BYTES
                ):
                    self._changed.clear()
                    await self._changed.wait()
                self._queue.append((event, size))
                self._queued_bytes += size
                self._changed.set()
        except Exception as exc:
            self._fail(exc)
