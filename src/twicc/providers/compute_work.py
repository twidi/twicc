"""Event-loop-owned compute gates and committed source notifications."""

import asyncio
from collections.abc import Callable


class ComputeWorkState:
    def __init__(self):
        self.changed = asyncio.Event()
        self._pending: set[str] = set()
        self._source_wakeups: dict[str, tuple[asyncio.AbstractEventLoop, Callable]] = {}

    def set_pending(self, provider: str, pending: bool) -> None:
        if (provider in self._pending) == pending:
            return
        if pending:
            self._pending.add(provider)
        else:
            self._pending.discard(provider)
        self.changed.set()

    def has_pending(self) -> bool:
        return bool(self._pending)

    def register_source_wakeup(self, provider: str, callback) -> None:
        self._source_wakeups[provider] = (asyncio.get_running_loop(), callback)

    def unregister_source_wakeup(self, provider: str, callback) -> None:
        installed = self._source_wakeups.get(provider)
        if installed is not None and installed[1] is callback:
            self._source_wakeups.pop(provider)

    def notify_source_changed(self, provider: str, session_id: str) -> None:
        installed = self._source_wakeups.get(provider)
        if installed is None:
            return
        loop, callback = installed

        def dispatch():
            if self._source_wakeups.get(provider) is installed:
                callback(session_id)

        if not loop.is_closed():
            loop.call_soon_threadsafe(dispatch)


def notify_committed_source_changed(provider: str, session_id: str) -> None:
    """Route a committed writer change without constructing a new registry."""
    from twicc import orchestrator

    registry = orchestrator._registry
    if registry is not None:
        registry.compute_work.notify_source_changed(provider, session_id)
