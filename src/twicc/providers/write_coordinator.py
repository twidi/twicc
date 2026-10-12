"""Event-loop-local admission and writer resources with foreground precedence.

Requests reserve their complete resource set at once. Within each priority,
FIFO applies to eligible requests: a blocked admission request cannot prevent
its current holder from acquiring the writer. No asyncio waiter internals are
used. All methods run on the writer's event loop, including token release.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from enum import Enum, IntFlag, auto
from typing import NamedTuple


class WritePriority(Enum):
    FOREGROUND = auto()
    BACKGROUND = auto()


class WriteResources(IntFlag):
    ADMISSION = auto()
    WRITER = auto()


write_priority: ContextVar[WritePriority] = ContextVar('write_priority', default=WritePriority.FOREGROUND)
_ALL_RESOURCES = WriteResources.ADMISSION | WriteResources.WRITER


class ResourceLease:
    """One granted resource reservation. Repeated release is harmless."""

    def __init__(self, coordinator: WriterCoordinator, resources: WriteResources, priority: WritePriority):
        self.coordinator = coordinator
        self.resources = resources
        self.priority = priority
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.coordinator.holders.remove(self)
        self.coordinator._schedule()


class ForegroundDemand:
    """Pressure registered before foreground dispatch reaches resource acquisition."""

    def __init__(self, coordinator: WriterCoordinator):
        self.coordinator = coordinator
        self.released = False
        coordinator.demands.add(self)

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.coordinator.demands.remove(self)
        self.coordinator._schedule()


class _Request(NamedTuple):
    resources: WriteResources
    priority: WritePriority
    future: asyncio.Future[ResourceLease]


class WriterCoordinator:
    """Own resource state for one writer lifetime. Public state is read-only to callers."""

    def __init__(self) -> None:
        self.requests: list[_Request] = []
        self.holders: set[ResourceLease] = set()
        self.demands: set[ForegroundDemand] = set()
        self._idle = asyncio.Event()
        self._idle.set()

    def foreground_demand(self) -> ForegroundDemand:
        return ForegroundDemand(self)

    def foreground_lock(self, resources: WriteResources) -> ForegroundLock:
        return ForegroundLock(self, resources)

    def locked(self, resources: WriteResources) -> bool:
        return any(lease.resources & resources for lease in self.holders)

    def _foreground_pressure(self) -> bool:
        return bool(self.demands) or any(
            item.priority is WritePriority.FOREGROUND for item in (*self.requests, *self.holders)
        )

    def _grant(self, resources: WriteResources, priority: WritePriority) -> ResourceLease:
        lease = ResourceLease(self, resources, priority)
        self.holders.add(lease)
        self._idle.clear()
        return lease

    def _schedule(self) -> None:
        # Iterate a snapshot: grants synchronously remove requests and reserve
        # resources, before any newly woken coroutine can run or be cancelled.
        for priority in (WritePriority.FOREGROUND, WritePriority.BACKGROUND):
            if priority is WritePriority.BACKGROUND and self._foreground_pressure():
                break
            for request in tuple(self.requests):
                if request.priority is not priority or self.locked(request.resources):
                    continue
                self.requests.remove(request)
                if not request.future.cancelled():
                    request.future.set_result(self._grant(request.resources, priority))
        if not self.requests and not self.holders:
            self._idle.set()

    async def acquire(self, resources: WriteResources, priority: WritePriority) -> ResourceLease:
        if not resources or int(resources) & ~int(_ALL_RESOURCES):
            raise ValueError('Expected admission and/or writer resources')
        if not isinstance(priority, WritePriority):
            raise TypeError('Expected a write priority')
        future = asyncio.get_running_loop().create_future()
        request = _Request(resources, priority, future)
        self.requests.append(request)
        self._idle.clear()
        self._schedule()
        try:
            return await future
        except asyncio.CancelledError:
            if request in self.requests:
                self.requests.remove(request)
            elif not future.cancelled():
                # The grant won, but the waiting task has not resumed yet.
                future.result().release()
            self._schedule()
            raise

    def try_background_lease(self) -> ResourceLease | None:
        # Respect older background requests as well as foreground pressure.
        if self.requests or self._foreground_pressure() or self.locked(_ALL_RESOURCES):
            return None
        return self._grant(_ALL_RESOURCES, WritePriority.BACKGROUND)

    async def drain(self) -> None:
        """Wait for all requests and holders, including grants not yet consumed.

        Producers must stop first. Demand alone owns no resource and does not
        prevent drain; demand owners must release tokens to unblock their work.
        """
        while self.requests or self.holders:
            await self._idle.wait()


class ForegroundLock:
    """asyncio.Lock-compatible foreground facade over coordinated resources."""

    def __init__(self, coordinator: WriterCoordinator, resources: WriteResources):
        self.coordinator = coordinator
        self.resources = resources
        self._lease: ResourceLease | None = None

    async def acquire(self) -> bool:
        self._lease = await self.coordinator.acquire(self.resources, WritePriority.FOREGROUND)
        return True

    def release(self) -> None:
        if self._lease is None:
            raise RuntimeError('Lock is not acquired')
        lease, self._lease = self._lease, None
        lease.release()

    def locked(self) -> bool:
        return self.coordinator.locked(self.resources)

    async def __aenter__(self) -> None:
        await self.acquire()

    async def __aexit__(self, *exc_info) -> None:
        self.release()
