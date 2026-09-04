"""Process-local coordination for Codex rollout migration."""

from __future__ import annotations

import asyncio


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
