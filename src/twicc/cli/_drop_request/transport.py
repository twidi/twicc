"""Dual-mode drop-request transport.

Every mutating CLI command funnels its (payload, kind) through this seam:

- **Local mode** (a real ``twicc`` process talking to a separate backend):
  identical to the historical behavior — heartbeat preflight, atomic drop
  file, status-file polling, caller-side cleanup.
- **Backend mode** (the command runs *inside* the backend process — MCP tool
  calls, and optionally ``/rpc/``): no filesystem at all. The payload is
  executed by scheduling :func:`twicc.drop_requests_watcher.execute_drop_payload`
  on the backend's event loop; polling reads a concurrent Future.

Mode is selected by the ``backend_loop`` ContextVar: the in-backend dispatcher
sets it to its running loop before running the command in a worker thread
(ContextVars propagate through ``asyncio.to_thread``). CLI processes never set
it, so the default path is byte-for-byte the previous one.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import Future
from contextvars import ContextVar
from pathlib import Path

import orjson

from twicc.cli._drop_request.discovery import check_heartbeat
from twicc.cli._drop_request.drop_file import write_drop_file
from twicc.cli._drop_request.polling import FINAL_STATUSES, POLL_INTERVAL_SECONDS, PollOutcome

backend_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar(
    "twicc_backend_loop", default=None,
)


def _in_backend() -> bool:
    return backend_loop.get() is not None


def ensure_server_available() -> None:
    """Heartbeat preflight; a no-op in backend mode (we ARE the server).

    Raises :class:`twicc.cli._drop_request.discovery.ServerDownError` like
    ``check_heartbeat`` did — call sites keep their except clauses unchanged.
    """
    if not _in_backend():
        check_heartbeat()


class Submission:
    """One in-flight request; uniform poll/cleanup over both modes."""

    def __init__(self, request_uuid: str, *, status_path: Path | None = None,
                 drop_path: Path | None = None, future: Future | None = None) -> None:
        self.request_uuid = request_uuid
        self._status_path = status_path
        self._drop_path = drop_path
        self._future = future

    def poll(self) -> PollOutcome | None:
        """Non-blocking check. None while pending; PollOutcome when final."""
        if self._future is not None:
            if not self._future.done():
                return None
            try:
                data = dict(self._future.result())
            except Exception as e:  # scheduling failure (loop gone, ...)
                data = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
            data.setdefault("request_uuid", self.request_uuid)
            return PollOutcome(status=data.get("status"), data=data, received_seen=True)
        # Local mode: single-shot read of the status file.
        try:
            data = orjson.loads(self._status_path.read_bytes())
        except (FileNotFoundError, ValueError, OSError):
            return None
        status = data.get("status")
        if status in FINAL_STATUSES:
            return PollOutcome(status=status, data=data, received_seen=True)
        return None

    def was_received(self) -> bool:
        """Whether the request has reached at least the ``received`` stage.

        Backend mode: always True (submitting IS receipt). Local mode: reads the
        status file for a ``received`` marker. Used to keep the timeout wording
        accurate ("received but no response" vs "no confirmation") in the batch
        pollers, which never observe the intermediate via :meth:`poll`.
        """
        if self._future is not None:
            return True
        try:
            return orjson.loads(self._status_path.read_bytes()).get("status") == "received"
        except (FileNotFoundError, ValueError, OSError):
            return False

    def cleanup(self) -> None:
        """Delete the request/status files (local mode); no-op in backend mode."""
        if self._drop_path is not None:
            self._drop_path.unlink(missing_ok=True)
        if self._status_path is not None:
            self._status_path.unlink(missing_ok=True)


def submit(payload: dict, *, kind: str) -> Submission:
    """Submit one request in the active mode."""
    loop = backend_loop.get()
    if loop is None:
        drop = write_drop_file(payload, kind=kind)
        return Submission(
            drop.request_uuid,
            status_path=drop.path.with_name(f"{drop.request_uuid}.status.json"),
            drop_path=drop.path,
        )
    # Backend mode: replicate write_drop_file's envelope semantics without I/O.
    request_uuid = str(uuid.uuid4())
    full_payload = {**payload, "kind": kind}
    if kind == "session:create":
        full_payload["session_id"] = request_uuid
    from twicc.drop_requests_watcher import execute_drop_payload

    future = asyncio.run_coroutine_threadsafe(
        execute_drop_payload(full_payload, kind), loop,
    )
    return Submission(request_uuid, future=future)


def wait(submission: Submission, timeout_seconds: int) -> PollOutcome:
    """Block until final status or timeout (same contract as poll_status).

    Timeout returns ``data=None`` where the old ``poll_status`` returned the
    last partial read — deliberately: no caller reads ``.data`` on timeout
    (``build_final``'s timeout branch only uses ``received_seen``).
    """
    deadline = time.time() + timeout_seconds
    received_seen = _in_backend()  # backend mode: submission IS receipt
    while time.time() < deadline:
        outcome = submission.poll()
        if outcome is not None:
            return outcome
        received_seen = received_seen or submission.was_received()
        time.sleep(POLL_INTERVAL_SECONDS)
    return PollOutcome(status=None, data=None, received_seen=received_seen)
