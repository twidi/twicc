"""Upload locks and the guarded-operation runner.

Design: docs/plans/2026-09-28-file-upload-design.md (§5.4).

- One ``asyncio.Lock`` per upload (single process). The getter creates an entry
  only when ``<id>.json`` exists. An entry lives until the janitor removes the
  tombstone, so a waiter never finds a replaced lock. The lock is **not**
  re-entrant: finalization and recovery run under a lock already held by their
  caller and never take it themselves.
- One module-level **creation lock**, held by the whole creation task
  (idempotency lookup, checks, file creation, zero-byte finalization).
- The **runner** (:func:`run_guarded`) starts a guarded operation as a task in
  a fresh :class:`contextvars.Context` (so it does not inherit the request's
  asgiref ``ThreadSensitiveContext``, whose executor Django shuts down when the
  request ends), keeps a strong reference until it is done, logs its
  exception, and awaits it through :func:`asyncio.shield`. A client disconnect
  cancels only the awaiting view, never the operation: the operation always
  ends under its lock, and no cleanup code runs in parallel with its worker
  thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

from twicc.uploads import store

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Strong references to the running guarded tasks (a bare create_task would let
# the event loop garbage-collect a task whose awaiting view was cancelled).
_GUARDED_TASKS: set[asyncio.Task] = set()

# Attribute set on an exception already logged by the done-callback, so the
# view decorator does not log it a second time.
_LOGGED_ATTR = "_twicc_upload_logged"

# The locks are bound to the event loop that first waits on them. TwiCC runs
# one loop per process; a new loop (only in tests, one ``asyncio.run`` per
# test) gets fresh locks instead of locks bound to a closed loop.
_locks_loop: asyncio.AbstractEventLoop | None = None
_creation_lock: asyncio.Lock | None = None
_upload_locks: dict[str, asyncio.Lock] = {}


def _ensure_loop_state() -> None:
    global _locks_loop, _creation_lock
    loop = asyncio.get_running_loop()
    if loop is not _locks_loop:
        _locks_loop = loop
        _creation_lock = asyncio.Lock()
        _upload_locks.clear()


def get_creation_lock() -> asyncio.Lock:
    """The module-level creation lock (§5.3 ``POST``)."""
    _ensure_loop_state()
    return _creation_lock


def get_upload_lock(upload_id: str) -> asyncio.Lock | None:
    """The lock of one upload, or ``None`` when ``<id>.json`` does not exist.

    An entry is created only when the metadata file exists (a cheap ``stat``);
    the caller answers ``404`` on ``None``. *upload_id* must be a valid id.
    """
    _ensure_loop_state()
    lock = _upload_locks.get(upload_id)
    if lock is None:
        if not store.metadata_path(upload_id).exists():
            return None
        lock = _upload_locks.setdefault(upload_id, asyncio.Lock())
    return lock


def was_logged(exc: BaseException) -> bool:
    """True when *exc* was already logged by a guarded task's done-callback."""
    return getattr(exc, _LOGGED_ATTR, False)


async def run_guarded(coro: Coroutine[Any, Any, T], *, label: str) -> T:
    """Run a guarded operation to its end, whatever happens to the caller.

    The coroutine runs as a task in a fresh context, referenced until done,
    its exception logged once. The caller awaits it through ``asyncio.shield``:
    a cancellation of the caller (a client disconnect) propagates to the caller
    only; the task goes on. Its result is returned, its exception re-raised.
    """
    task = asyncio.create_task(coro, context=contextvars.Context())
    _GUARDED_TASKS.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _GUARDED_TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("Upload operation %s failed", label, exc_info=exc)
            try:
                setattr(exc, _LOGGED_ATTR, True)
            except AttributeError:  # pragma: no cover - exceptions accept attributes
                pass

    task.add_done_callback(_on_done)
    return await asyncio.shield(task)
