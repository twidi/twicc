"""Process-local coordination for Codex rollout migration.

Three small pieces of shared state, all in-memory and owned by the main
process:

- the per-session **gate** (:func:`gate_for`) — an ``asyncio.Lock`` the
  coordinator holds while it migrates and rebuilds one session, and that the
  agent manager takes around every send to an existing session, so a message
  never reaches Codex while TwiCC is rewriting that session's history;
- the **migrating set** (:func:`mark_migrating` / :func:`is_migrating`) —
  the non-blocking view of the same fact for the JSONL watcher: it skips the
  events of a session under migration instead of queueing behind the gate
  (which would freeze every other session's live updates), and the
  coordinator replays the file once the session is released;
- the **scheduler wake-up** (:func:`wake_migration_scheduler` /
  :func:`wait_for_migration_wake`) plus the **rebuild requests**
  (:func:`request_rebuild` / :func:`take_rebuild_requests`) the watcher files
  when it detects that a rollout was rewritten under TwiCC's feet.

Nothing here is durable: a restart rediscovers every pending migration from
the source/DB history modes and the stale ``compute_version``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager


class _SessionGate(asyncio.Lock):
    """An asyncio lock that removes itself after its last user leaves."""

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id
        self._users = 0

    async def acquire(self) -> bool:
        self._users += 1
        try:
            return await super().acquire()
        except BaseException:
            self._users -= 1
            self._remove_if_idle()
            raise

    def release(self) -> None:
        super().release()
        self._users -= 1
        self._remove_if_idle()

    def _remove_if_idle(self) -> None:
        if self._users == 0 and _session_gates.get(self.session_id) is self:
            _session_gates.pop(self.session_id, None)


_session_gates: dict[str, _SessionGate] = {}
_migrating: set[str] = set()
_rebuild_requests: set[str] = set()
_migration_wake_event: asyncio.Event | None = None
_migration_wake_loop: asyncio.AbstractEventLoop | None = None
_migration_wake_generation = 0
_migration_wake_consumed = 0


def gate_for(session_id: str) -> asyncio.Lock:
    """Return the shared process-local gate for one Codex session."""

    gate = _session_gates.get(session_id)
    if gate is None:
        gate = _SessionGate(session_id)
        _session_gates[session_id] = gate
    return gate


def mark_migrating(session_id: str) -> None:
    """Flag a session whose history TwiCC is migrating / rebuilding right now."""

    _migrating.add(session_id)


def unmark_migrating(session_id: str) -> None:
    _migrating.discard(session_id)


def is_migrating(session_id: str) -> bool:
    """Non-blocking check used by the watcher to skip a session under migration."""

    return session_id in _migrating


@contextmanager
def migrating(session_id: str) -> Iterator[None]:
    mark_migrating(session_id)
    try:
        yield
    finally:
        unmark_migrating(session_id)


def request_rebuild(session_id: str) -> None:
    """Ask the coordinator to rebuild a session from its rollout (rewrite detected).

    The caller also resets the session's ``compute_version`` through the DB
    writer so the coordinator's stale-session scan picks it up; this request
    only lifts the per-run ``failed_this_run`` / deferred exclusions and
    wakes the scheduler.
    """

    _rebuild_requests.add(session_id)
    wake_migration_scheduler()


def take_rebuild_requests() -> set[str]:
    """Drain the pending rebuild requests (coordinator side)."""

    global _rebuild_requests
    taken, _rebuild_requests = _rebuild_requests, set()
    return taken


def wake_migration_scheduler() -> None:
    """Wake the Codex migration scheduler after relevant external activity."""

    global _migration_wake_generation
    _migration_wake_generation += 1
    if _migration_wake_event is not None:
        _migration_wake_event.set()


async def wait_for_migration_wake(stop_event: asyncio.Event, timeout: float) -> None:
    """Wait until shutdown, an explicit wake, or the retry timeout."""

    global _migration_wake_event, _migration_wake_loop, _migration_wake_consumed

    loop = asyncio.get_running_loop()
    if _migration_wake_event is None or _migration_wake_loop is not loop:
        _migration_wake_event = asyncio.Event()
        _migration_wake_loop = loop
    if _migration_wake_consumed < _migration_wake_generation:
        _migration_wake_consumed = _migration_wake_generation
        _migration_wake_event.clear()
        return

    _migration_wake_event.clear()
    wake_task = asyncio.create_task(_migration_wake_event.wait())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        await asyncio.wait(
            (wake_task, stop_task),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if wake_task.done() and not wake_task.cancelled():
            observed_generation = _migration_wake_generation
            _migration_wake_event.clear()
            _migration_wake_consumed = observed_generation
            if _migration_wake_generation > observed_generation:
                _migration_wake_event.set()
    finally:
        for task in (wake_task, stop_task):
            task.cancel()
        await asyncio.gather(wake_task, stop_task, return_exceptions=True)
