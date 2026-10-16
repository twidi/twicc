"""Queue notifications bound to one writer event-loop lifetime."""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Callable


class WriterSignals:
    """Wake dispatch and source observers without carrying authoritative state."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.dispatch = asyncio.Event()
        self.sources = asyncio.Event()
        self._loop = loop
        self._closed = False

    def _set(self, sources: bool) -> None:
        # Recheck inside the scheduled callback: close can precede its delivery.
        if self._closed:
            return
        if sources:
            self.sources.set()
        self.dispatch.set()

    def _notify(self, sources: bool) -> None:
        if self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._set(sources)
        else:
            try:
                self._loop.call_soon_threadsafe(self._set, sources)
            except RuntimeError:
                if not self._loop.is_closed():
                    raise

    def notify_dispatch(self) -> None:
        self._notify(False)

    def notify_sources(self) -> None:
        self._notify(True)

    def close(self) -> None:
        self._closed = True


class NotifyingQueue(queue.Queue):
    """Notify after successful insertion and after releasing the queue mutex."""

    def __init__(self, maxsize: int = 0, *, on_put: Callable[[], None]):
        super().__init__(maxsize)
        self._on_put = on_put

    def put(self, item, block=True, timeout=None):
        super().put(item, block=block, timeout=timeout)
        self._on_put()


class NotifyingAsyncQueue(asyncio.Queue):
    """Both put APIs notify once; asyncio.put delegates to put_nowait."""

    def __init__(self, *, on_put: Callable[[], None]):
        super().__init__()
        self._on_put = on_put

    def put_nowait(self, item):
        super().put_nowait(item)
        self._on_put()
