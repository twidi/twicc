"""Minimal Agent Client Protocol client over stdio for ``gemini --acp``.

ACP is JSON-RPC 2.0, one message per line, both ways: the client sends
requests (``initialize``, ``session/new``, ``session/load``,
``session/prompt``, ``session/set_model``, ``session/set_mode``) and
notifications (``session/cancel``); the agent sends notifications
(``session/update``) and requests (``session/request_permission``).

Only what TwiCC uses is implemented; no third-party dependency.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import orjson

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

# JSON-RPC error codes.
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# A ``session/load`` replays a whole conversation and file contents can travel
# in a single line: keep the reader limit generous.
_STREAM_LIMIT = 64 * 1024 * 1024
_STDERR_TAIL_LINES = 40

NotificationHandler = Callable[[str, dict], Awaitable[None]]
RequestHandler = Callable[[str, dict], Awaitable[Any]]


class AcpError(Exception):
    """A JSON-RPC error answer, or a request that cannot complete."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class AcpConnectionClosed(AcpError):
    """The agent process went away before answering."""


class AcpConnection:
    """One ``gemini --acp`` subprocess and its JSON-RPC channel."""

    def __init__(
        self,
        *,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
    ) -> None:
        self._on_notification = on_notification
        self._on_request = on_request
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=_STDERR_TAIL_LINES)
        self.closed = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, argv: Sequence[str], *, cwd: str, env: dict[str, str]) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
            start_new_session=True,
        )
        self._reader_task = asyncio.create_task(self._read_stdout(), name="gemini-acp-reader")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="gemini-acp-stderr")

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    async def close(self, timeout: float = 3.0) -> None:
        """Close stdin, wait for the process to exit, then terminate it."""
        process = self._process
        if process is None:
            self.closed.set()
            return
        if process.stdin is not None and not process.stdin.is_closing():
            with contextlib.suppress(Exception):
                process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=timeout)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for task in list(self._tasks):
            task.cancel()
        self._fail_pending(AcpConnectionClosed("Gemini CLI process closed"))
        self.closed.set()

    # ------------------------------------------------------------------
    # Outgoing
    # ------------------------------------------------------------------

    async def request(self, method: str, params: dict) -> Any:
        if self.closed.is_set():
            raise AcpConnectionClosed("Gemini CLI process closed")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict) -> None:
        if self.closed.is_set():
            return
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdin.is_closing():
            raise AcpConnectionClosed("Gemini CLI process closed")
        async with self._write_lock:
            try:
                process.stdin.write(orjson.dumps(message) + b"\n")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AcpConnectionClosed("Gemini CLI process closed") from exc

    # ------------------------------------------------------------------
    # Incoming
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        try:
            while True:
                try:
                    line = await stdout.readline()
                except ValueError:
                    logger.warning("Gemini ACP: dropped an oversized line")
                    continue
                if not line:
                    break
                try:
                    message = orjson.loads(line)
                except orjson.JSONDecodeError:
                    # The CLI may print stray diagnostics on stdout.
                    logger.debug("Gemini ACP: ignored non-JSON stdout line: %r", line[:200])
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            self._fail_pending(AcpConnectionClosed(self._closed_reason()))
            self.closed.set()

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stderr = self._process.stderr
        while True:
            try:
                line = await stderr.readline()
            except ValueError:
                continue
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)

    def _closed_reason(self) -> str:
        tail = self.stderr_tail().strip()
        return tail.splitlines()[-1] if tail else "Gemini CLI process exited"

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if "id" in message:
                self._spawn(self._answer_request(message["id"], method, params))
            else:
                self._spawn(self._deliver_notification(method, params))
            return
        request_id = message.get("id")
        future = self._pending.get(request_id) if isinstance(request_id, int) else None
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(AcpError(
                str(error.get("message") or "Gemini CLI error"), code=error.get("code"), data=error.get("data"),
            ))
        else:
            future.set_result(message.get("result"))

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver_notification(self, method: str, params: dict) -> None:
        try:
            await self._on_notification(method, params)
        except Exception:
            logger.exception("Gemini ACP: notification handler failed for %s", method)

    async def _answer_request(self, request_id: Any, method: str, params: dict) -> None:
        try:
            result = await self._on_request(method, params)
        except AcpError as exc:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": exc.code or INTERNAL_ERROR, "message": str(exc)}}
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Gemini ACP: request handler failed for %s", method)
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": INTERNAL_ERROR, "message": str(exc)}}
        else:
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        with contextlib.suppress(AcpConnectionClosed):
            await self._write(response)

    def _fail_pending(self, error: AcpError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
