"""Lifecycle manager for the Gemini CLI provider.

Tasks started by :meth:`GeminiOrchestrator.start`:

- background compute (generic, recomputes rows whose ``compute_version`` is
  outdated) and model retirement: immediately;
- the session-file watcher: once the search index is ready, followed by a
  catch-up pass that replays the session files already on disk through the
  watcher (Gemini sessions are few; no dedicated bulk initial sync).

Nothing here requires the Gemini CLI to be installed: without it the session
directory never appears and the watcher keeps polling for it.
"""

from __future__ import annotations

import asyncio
import logging

from django.conf import settings

from twicc.core.enums import Provider
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_compute_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.model_retirement_task import start_model_retirement_task, stop_model_retirement_task

from .agent import get_gemini_agent_manager
from .sessions_watcher import get_watcher

logger = logging.getLogger(__name__)


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("%s ended with an exception during shutdown", name)
    logger.info("%s stopped", name)


def _on_watcher_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Gemini watcher crashed — file changes will no longer be detected!", exc_info=exc)
    else:
        logger.warning("Gemini watcher ended unexpectedly — file changes will no longer be detected")


class GeminiOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Gemini CLI tasks.

    ``initial_sync_done`` / ``compute_done`` stay pre-set (inherited): the
    catch-up runs through the live watcher path, which creates sessions with
    an up-to-date ``compute_version``, so the CLI has nothing to wait for.
    """

    provider = Provider.GEMINI

    def __init__(self) -> None:
        super().__init__()
        self._shutdown_event: asyncio.Event | None = None
        self._orch_task: asyncio.Task | None = None
        self._watcher_task: asyncio.Task | None = None
        self._catch_up_task: asyncio.Task | None = None
        self._compute_task: asyncio.Task | None = None
        self._compute_ctx: ComputeContext | None = None
        self._retirement_task: asyncio.Task | None = None

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        self._shutdown_event = shutdown_event
        self.search_index_ready = search_index_ready

        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.GEMINI_COMPUTE_VERSION,
            compute_factory="twicc.providers.gemini.compute:get_compute",
        )
        self._compute_task = self._create_task(
            start_background_compute_task(
                self._compute_ctx, initial_done=asyncio.Event(), work_state=self.compute_work,
            )
        )
        self._retirement_task = self._create_task(start_model_retirement_task(self.provider))
        self._orch_task = self._create_task(self._start_watcher())

    async def _start_watcher(self) -> None:
        """Start the watcher once the search index is ready, then catch up."""
        assert self.search_index_ready is not None
        await self.search_index_ready.wait()
        watcher = get_watcher()
        self._watcher_task = self._create_task(watcher.start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Gemini watcher started")
        self._catch_up_task = self._create_task(self._catch_up())

    async def _catch_up(self) -> None:
        """Replay the session files already present when the watcher started.

        The base watcher only catches up a directory that appears after it
        started. When the directory already exists, files written while
        TwiCC was down are replayed here, one at a time (``process_path``
        waits for each file to drain, reading from the stored offset).
        """
        watcher = get_watcher()
        # ``start_watcher`` binds the channel layer before it can consume.
        while getattr(watcher, "_channel_layer", None) is None:
            if self._watcher_task is None or self._watcher_task.done():
                return
            await asyncio.sleep(0.05)
        if not watcher.projects_dir.is_dir():
            return
        paths = await asyncio.to_thread(watcher.enumerate_session_files, watcher.projects_dir)
        if paths:
            logger.info("Gemini: catching up %d existing session file(s)", len(paths))
        for path in paths:
            await watcher.process_path(path)

    async def shutdown(self) -> None:
        if self._compute_ctx is not None:
            self._compute_ctx.stop_event.set()
        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Gemini watcher starter")
        if self._catch_up_task is not None:
            await _cancel_task(self._catch_up_task, "Gemini catch-up")

        logger.info("Stopping Gemini agent manager...")
        await get_gemini_agent_manager().shutdown()

        if self._watcher_task is not None:
            get_watcher().stop_watcher()
            await _cancel_task(self._watcher_task, "Gemini watcher")

        if self._compute_task is not None:
            from twicc.providers.db_writer import abandon_compute_run

            await abandon_compute_run(self._compute_ctx.run_id, self.provider)
            await _cancel_task(self._compute_task, "Gemini background compute")
            await stop_background_task(self._compute_ctx)

        if self._retirement_task is not None:
            stop_model_retirement_task(self.provider)
            await _cancel_task(self._retirement_task, "Gemini model retirement task")

        self._orch_task = self._watcher_task = self._catch_up_task = None
        self._compute_task = self._retirement_task = None
        self._compute_ctx = None
