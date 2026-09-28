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
- The in-process **"finalizing now"** set (:func:`finalizing_now`,
  :func:`is_finalizing_now`), read without the lock by ``HEAD`` and ``DELETE``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import weakref
from collections.abc import Coroutine, Iterator
from typing import Any, TypeVar

from twicc.paths import get_uploads_dir
from twicc.uploads import store

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Strong references to the running guarded tasks (a bare create_task would let
# the event loop garbage-collect a task whose awaiting view was cancelled).
_GUARDED_TASKS: set[asyncio.Task] = set()

# Attribute set on an exception already logged by the done-callback, so the
# view decorator does not log it a second time.
_LOGGED_ATTR = "_twicc_upload_logged"


class _LoopLocks:
    """The locks of one event loop: the creation lock and one lock per upload."""

    def __init__(self) -> None:
        self.creation = asyncio.Lock()
        self.uploads: dict[str, asyncio.Lock] = {}


# An ``asyncio.Lock`` is bound to the event loop that first waits on it, so the
# locks are kept per loop. TwiCC runs one loop per process: there is one
# ``_LoopLocks``, never replaced while its loop lives (a waiter never finds a
# replaced lock, §5.4). A closed loop's entry goes away with the loop. Tests
# run one loop per ``asyncio.run`` and clear this mapping between tests.
_loop_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopLocks] = weakref.WeakKeyDictionary()


def _current_locks() -> _LoopLocks:
    loop = asyncio.get_running_loop()
    state = _loop_locks.get(loop)
    if state is None:
        state = _loop_locks[loop] = _LoopLocks()
    return state


def get_creation_lock() -> asyncio.Lock:
    """The module-level creation lock (§5.3 ``POST``) of the running loop."""
    return _current_locks().creation


def get_upload_lock(upload_id: str) -> asyncio.Lock | None:
    """The lock of one upload, or ``None`` when ``<id>.json`` does not exist.

    An entry is created only when the metadata file exists (one ``stat``; the
    staging dir is never created here); the caller answers ``404`` on
    ``None``. Raises ``ValueError`` for an invalid *upload_id*.
    """
    if not store.is_valid_upload_id(upload_id):
        raise ValueError(f"invalid upload id: {upload_id!r}")
    uploads = _current_locks().uploads
    lock = uploads.get(upload_id)
    if lock is None:
        if not (get_uploads_dir() / f"{upload_id}.json").exists():
            return None
        lock = uploads.setdefault(upload_id, asyncio.Lock())
    return lock


# Ids whose finalization runs in this process (§5.4). ``HEAD`` and ``DELETE``
# read it without the lock (§5.3); finalization adds its id before its
# ``finalizing`` write and removes it in a ``finally`` (:func:`finalizing_now`).
_FINALIZING_NOW: set[str] = set()


def is_finalizing_now(upload_id: str) -> bool:
    """True while the finalization of *upload_id* runs in this process."""
    return upload_id in _FINALIZING_NOW


@contextlib.contextmanager
def finalizing_now(upload_id: str) -> Iterator[None]:
    """Hold *upload_id* in the "finalizing now" set for the block.

    Used by finalization (§5.6 step 3) under the upload's lock: the id enters
    before the ``finalizing`` write and leaves in a ``finally`` that covers
    every later step and every exception.
    """
    _FINALIZING_NOW.add(upload_id)
    try:
        yield
    finally:
        _FINALIZING_NOW.discard(upload_id)


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
