"""
Claude Code provider orchestrator.

Owns the lifecycle of every async/background task that belongs to the Claude
Code provider: initial JSONL sync, sessions watcher, background metadata
compute, auth/usage/statuspage/commands polling, model retirement,
cron restart, original file cache cleanup, and the ClaudeCodeAgentManager
that wraps the Claude Agent SDK.

The CLI server entry point (``twicc.cli.run``) instantiates this class and
delegates start/shutdown of all Claude Code tasks to it. Cross-provider tasks
(PyPI version check, OpenRouter price sync, search index lifecycle, global
startup search-indexing task) stay in the CLI module — pricing in particular
is shared across every provider that has declared an
``OPENROUTER_MODEL_PREFIX``, with a single fetch per cycle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.core.enums import Provider
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_compute_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.claude_code.agent import get_claude_code_agent_manager
from twicc.providers.claude_code.agent.original_file_cache import (
    start_cleanup_task as start_original_file_cache_cleanup,
    stop_cleanup_task as stop_original_file_cache_cleanup,
)
from twicc.core.models import Project, Session, SessionType
from twicc.providers.claude_code.cron_restart import restart_all_session_crons
from twicc.providers.claude_code.initial_sync import scan_projects, scan_sessions, sync_all
from twicc.providers.model_retirement_task import (
    start_model_retirement_task,
    stop_model_retirement_task,
)
from twicc.providers.claude_code.sessions_watcher import get_watcher
from twicc.providers.claude_code.plans_watcher import get_claude_code_plans_watcher
from twicc.providers.claude_code.commands_task import (
    start_commands_task,
    stop_commands_task,
)
from twicc.providers.statuspage_task import start_statuspage_task, stop_statuspage_task
from twicc.providers.claude_code.usage_task import start_usage_sync_task, stop_usage_sync_task
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)


def _count_total_sessions() -> int:
    """Filesystem-only count of session files across all projects (for progress reporting)."""
    total = 0
    for project_id in scan_projects():
        total += len(scan_sessions(project_id))
    return total


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        # The task had already finished with an error before we cancelled it.
        # shutdown() must not be derailed by it — log and carry on so the rest
        # of the teardown (notably the initial-sync drain marker) still runs.
        logger.exception("%s ended with an exception during shutdown", name)
    logger.info("%s stopped", name)


def _on_watcher_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Watcher task crashed with exception — file changes will no longer be detected!",
            exc_info=exc,
        )
    else:
        logger.warning(
            "Watcher task ended unexpectedly (no exception) — file changes will no longer be detected"
        )


class ClaudeCodeOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Claude Code provider tasks.

    Task dependency graph (started by ``start()``):

    - initial_sync, usage_sync, auth_check, statuspage, commands,
      original_file_cache_cleanup, model_retirement: start immediately.
    - watcher: starts after initial_sync **and** after the CLI has
      initialized the global search index (so the watcher can write into
      it as new items arrive).
    - cron_restart + background_compute: start after initial_sync.

    The cross-provider search-index init/shutdown and the global search
    indexing background task live in the CLI now — this orchestrator
    only signals its progress via :attr:`initial_sync_done` and
    :attr:`compute_done` (inherited from :class:`BaseOrchestrator`).

    Pricing is owned by the CLI (cross-provider, single OpenRouter fetch
    shared by every provider that has an ``OPENROUTER_MODEL_PREFIX``); the
    initial price sync is awaited by the CLI before instantiating this
    orchestrator, so prices are guaranteed to be in DB by the time the
    background compute task starts.
    """

    provider = Provider.CLAUDE_CODE

    def __init__(self) -> None:
        super().__init__()
        # This provider owns both phases — reset the inherited pre-set
        # events so the CLI actually waits for our broadcasts.
        self.initial_sync_done = asyncio.Event()
        self.compute_done = asyncio.Event()

        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()
        # Set by the host (CLI) when the server begins shutting down
        self._shutdown_event: asyncio.Event | None = None

        # Tasks started immediately
        self._sync_task: asyncio.Task | None = None
        # Future of the initial-sync producer thread (asyncio.to_thread).
        # shutdown() awaits it so the thread is really finished, not just
        # the wrapping coroutine cancelled.
        self._sync_thread_future: asyncio.Future | None = None
        self._orch_task: asyncio.Task | None = None
        self._usage_sync_task: asyncio.Task | None = None
        self._statuspage_task: asyncio.Task | None = None
        self._commands_task: asyncio.Task | None = None
        self._original_file_cache_task: asyncio.Task | None = None
        self._retirement_task: asyncio.Task | None = None
        self._plans_watcher_task: asyncio.Task | None = None

        # Tasks started by the internal orchestrator coroutine (may stay None
        # if shutdown is requested before their prerequisites complete).
        self._watcher_task: asyncio.Task | None = None
        self._compute_task: asyncio.Task | None = None
        self._compute_ctx: ComputeContext | None = None
        self._cron_restart_task: asyncio.Task | None = None

    def request_thread_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration. Async tasks listen for the shared
        ``shutdown_event`` passed to :meth:`start`; this hook is for the
        blocking thread only.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch all Claude Code tasks. Returns once tasks are scheduled.

        Every task goes through :meth:`BaseOrchestrator._create_task` so
        log records emitted from them (directly or via ``sync_to_async``
        / ``asyncio.to_thread`` calls they spawn) are tagged with this
        provider — no per-task ``current_provider.set()`` needed.
        """
        self._shutdown_event = shutdown_event
        self.search_index_ready = search_index_ready

        # Reset stateful events in case this is a hot-restart (provider was
        # toggled off via Settings, then back on). ``shutdown()`` set them
        # all so the previous run's awaiters could finish; without this
        # reset, the new tasks would observe the leftover ``set()`` and
        # exit on the first ``is_set()`` check.
        self._sync_stop_event.clear()
        self.initial_sync_done.clear()
        self.compute_done.clear()

        self._sync_task = self._create_task(self._initial_sync_task())
        self._orch_task = self._create_task(self._dependency_orchestrator())
        self._usage_sync_task = self._create_task(start_usage_sync_task())
        self._statuspage_task = self._create_task(start_statuspage_task(self.provider))
        self._commands_task = self._create_task(start_commands_task())
        self._original_file_cache_task = self._create_task(start_original_file_cache_cleanup())
        self._retirement_task = self._create_task(start_model_retirement_task(self.provider))
        # Plan files watcher — powers the session view's Plan tab. Filesystem
        # only (like the artifacts watcher), so it starts immediately with no
        # dependency on the initial JSONL sync or background compute.
        self._plans_watcher_task = self._create_task(get_claude_code_plans_watcher().start())

    async def shutdown(self) -> None:
        """Stop all Claude Code tasks in dependency-safe order."""
        # Make sure the initial sync thread cooperates if it's still running
        self._sync_stop_event.set()

        # Unblock the CLI in case it was awaiting either lifecycle event:
        # if a task was cancelled before reaching its natural ``set()``,
        # the CLI's ``wait_initial_sync_done`` / ``wait_compute_done``
        # would otherwise hang. Both calls are idempotent.
        self.initial_sync_done.set()
        self.compute_done.set()

        # Cancel the dependency orchestrator first, before any await below.
        # ``initial_sync_done.set()`` just unblocked _dependency_orchestrator's
        # ``await self.initial_sync_done.wait()``; awaiting anything else first
        # would let it resume and start the background compute and the watcher
        # *while the provider is shutting down*. ``_cancel_task`` runs
        # ``.cancel()`` synchronously — before this coroutine yields — so the
        # orchestrator is killed at its wait() and never runs its body.
        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Orchestrator task")

        # Cancel startup tasks (may already be done)
        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Initial sync task")
        # asyncio.to_thread does not kill the producer thread on cancel —
        # only awaiting its future proves the thread actually stopped (it
        # cooperates via _sync_stop_event, set above). Block here so the
        # provider does not reach the "stopped" phase with a live thread
        # still pushing onto the shared queue.
        if self._sync_thread_future is not None and not self._sync_thread_future.done():
            with suppress(Exception):
                await asyncio.shield(self._sync_thread_future)
            self._sync_thread_future = None
        # The producer thread is now stopped, so every initial-sync payload
        # it produced (CreateSession, UpdateSession, MarkSessionsStale, ...)
        # is enqueued. But a cancelled _initial_sync_task never pushed its
        # completion marker, so nothing yet proves those payloads have been
        # drained. Push the marker ourselves and await it: the queue is FIFO,
        # so the marker's done future resolving proves every payload of this
        # run has been applied. Without this, a queued payload could be
        # applied after the provider reaches the "stopped" phase and race the
        # next hot-start's producer (duplicate-row IntegrityError, or a stale
        # staleness write landing after the new producer read the DB). A
        # harmless no-op when _initial_sync_task ran to completion and already
        # drained its own marker. Pushed with no stop_event — _sync_stop_event
        # is set, but this marker is the drain proof and must not be dropped.
        if self._sync_task is not None:
            from twicc.providers.db_writer import (
                InitialSyncDoneMarker,
                put_thread_message,
            )
            with suppress(Exception):
                done_future = asyncio.get_running_loop().create_future()
                await put_thread_message(
                    InitialSyncDoneMarker(provider=self.provider, done_future=done_future)
                )
                await done_future

        # Watcher (may not have started yet)
        if self._watcher_task is not None:
            logger.info("Stopping watcher...")
            get_watcher().stop_watcher()
            await _cancel_task(self._watcher_task, "Watcher")
        else:
            logger.info("Watcher was not started, skipping")

        # Plans watcher (independent of the JSONL watcher above)
        if self._plans_watcher_task is not None:
            logger.info("Stopping plans watcher...")
            get_claude_code_plans_watcher().stop()
            await _cancel_task(self._plans_watcher_task, "Plans watcher")

        # Background compute (may not have started yet)
        if self._compute_task is not None:
            logger.info("Stopping background compute task...")
            # Abandon the compute run before stopping the worker: from here on,
            # every still-queued or still-incoming session_complete for this
            # run is skipped by the DB writer (untracked run), so a
            # shut-down provider's partial compute results never apply — not
            # during this teardown, and not racing the next hot-start. The
            # run's sessions are recomputed on the next start.
            from twicc.providers.db_writer import abandon_compute_run
            await abandon_compute_run(self._compute_ctx.run_id, self.provider)
            await stop_background_task(self._compute_ctx)
            await _cancel_task(self._compute_task, "Background compute task")
        else:
            logger.info("Background compute was not started, skipping")

        # Usage sync
        if self._usage_sync_task is not None:
            logger.info("Stopping usage sync task...")
            stop_usage_sync_task()
            await _cancel_task(self._usage_sync_task, "Usage sync task")

        # Statuspage
        if self._statuspage_task is not None:
            logger.info("Stopping statuspage task...")
            stop_statuspage_task(self.provider)
            await _cancel_task(self._statuspage_task, "Statuspage task")

        # Commands
        if self._commands_task is not None:
            logger.info("Stopping commands task...")
            stop_commands_task()
            await _cancel_task(self._commands_task, "Commands task")

        # Original file cache cleanup
        if self._original_file_cache_task is not None:
            stop_original_file_cache_cleanup()
            await _cancel_task(self._original_file_cache_task, "Original file cache cleanup")

        # Model retirement
        if self._retirement_task is not None:
            logger.info("Stopping model retirement task...")
            stop_model_retirement_task(self.provider)
            await _cancel_task(self._retirement_task, "Model retirement task")

        # Cron restart (may still be retrying)
        if self._cron_restart_task is not None:
            await _cancel_task(self._cron_restart_task, "Cron restart")

        # Claude Code agent manager (Claude Agent SDK)
        logger.info("Stopping Claude Code agent manager...")
        await get_claude_code_agent_manager().shutdown()
        logger.info("Claude Code agent manager stopped")

    # ------------------------------------------------------------------
    # Internal task coroutines
    # ------------------------------------------------------------------

    async def _initial_sync_task(self) -> None:
        """Exception-safe wrapper around :meth:`_run_initial_sync`.

        ``_run_initial_sync`` can raise — e.g. ``sync_all`` hitting an
        unexpected error. On any non-cancellation exception, log it and still
        set ``initial_sync_done`` in the ``finally``: otherwise
        ``_dependency_orchestrator`` (which awaits that event) would hang
        forever and the provider would never start its compute/watcher. The
        provider then runs degraded on whatever was synced before the failure,
        recovered on the next restart.
        """
        try:
            await self._run_initial_sync()
        except Exception:
            logger.exception("Initial sync task for %s failed", self.provider.value)
        finally:
            self.initial_sync_done.set()

    async def _run_initial_sync(self) -> None:
        """Run sync_all() in a thread, pushing payloads onto the shared queue.

        The producer thread does not write to DB directly: it pushes
        initial-sync payloads onto the process-wide shared queue, drained by
        the DB writer (:mod:`twicc.providers.db_writer`).

        A producer thread that keeps pushing after a cancel pushes onto a
        still-drained queue — no lost writes; the zombie-thread / overlap
        concern is handled by ``shutdown()`` blocking on
        ``_sync_thread_future``. Releasing ``initial_sync_done`` is handled by
        the :meth:`_initial_sync_task` wrapper; this method first pushes and
        drains the run's completion marker — even when the producer crashes —
        so ``initial_sync_done`` is never released mid-drain.
        """
        from twicc.providers.db_writer import (
            InitialSyncDoneMarker,
            get_thread_queue,
            put_thread_message,
        )

        loop = asyncio.get_running_loop()
        provider_value = self.provider.value

        total_sessions = await asyncio.to_thread(_count_total_sessions)

        await broadcast_startup_progress(
            "initial_sync", 0, total_sessions, provider=provider_value
        )

        progress = {"current": 0}

        def on_session_progress(session_id: str, idx: int, total: int):
            # idx/total are per-project; we track global progress ourselves
            progress["current"] += 1
            asyncio.run_coroutine_threadsafe(
                broadcast_startup_progress(
                    "initial_sync", progress["current"], total_sessions, provider=provider_value
                ),
                loop,
            )

        sync_queue = get_thread_queue()
        logger.info("Starting data synchronization...")

        # Keep an explicit reference to the producer thread future so
        # shutdown() can wait for the *real* thread end, not just this
        # coroutine being cancelled.
        self._sync_thread_future = asyncio.ensure_future(
            asyncio.to_thread(
                sync_all,
                sync_queue,
                on_session_progress=on_session_progress,
                stop_event=self._sync_stop_event,
            )
        )
        # Wait for the producer thread, capturing a crash rather than letting
        # it propagate yet: the thread has ended either way, so the marker
        # pushed below still sits last in this run and the DB writer's FIFO
        # drain of it still proves every payload applied. That proof must
        # complete before initial_sync_done is released (by the
        # _initial_sync_task wrapper) -- on the crash path too, or the compute
        # phase could start while the DB writer is still applying this run's
        # payloads.
        #
        # The await is shielded: shutdown() cancels _sync_task, and a bare
        # await would propagate that cancel to _sync_thread_future, mark it
        # done() and make shutdown()'s `not done()` guard skip the wait,
        # leaving the producer thread alive after shutdown. The shield keeps
        # the thread future uncancelled so shutdown() can await it. A
        # CancelledError (this coroutine cancelled) still propagates;
        # shutdown() then pushes its own drain marker.
        sync_error: Exception | None = None
        try:
            await asyncio.shield(self._sync_thread_future)
        except Exception as exc:
            sync_error = exc

        # Producer thread finished (cleanly or by crashing) — close the run
        # with the marker. It carries a Future the writer resolves with the
        # run's failure count once it has drained every payload this run
        # produced.
        done_future = loop.create_future()
        if not await put_thread_message(
            InitialSyncDoneMarker(provider=self.provider, done_future=done_future),
            self._sync_stop_event,
        ):
            # shutdown signalled — marker dropped (shutdown() pushes its own).
            if sync_error is not None:
                logger.error(
                    "Initial sync for %s crashed during shutdown",
                    provider_value, exc_info=sync_error,
                )
            return
        failed_payloads = await done_future

        if sync_error is not None:
            # Producer crashed; its payloads are now fully drained, so
            # initial_sync_done is safe to release — re-raise for the
            # _initial_sync_task wrapper to log.
            raise sync_error

        if failed_payloads:
            logger.error(
                "Initial sync for %s completed with %d payload(s) that failed "
                "to apply — affected sessions will be re-synced on the next start",
                provider_value, failed_payloads,
            )

        await broadcast_startup_progress(
            "initial_sync", total_sessions, total_sessions,
            provider=provider_value, completed=True,
        )

        projects_count = await sync_to_async(
            Project.objects.filter(
                stale=False, sessions__provider=Provider.CLAUDE_CODE,
            ).distinct().count
        )()
        sessions_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CLAUDE_CODE, stale=False, type=SessionType.SESSION, hidden=False,
            ).count
        )()
        subagents_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CLAUDE_CODE, stale=False, type=SessionType.SUBAGENT,
            ).count
        )()
        logger.info(
            "Data synchronized (%d projects, %d sessions, %d subagents)",
            projects_count,
            sessions_count,
            subagents_count,
        )

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start compute + cron restart + watcher.

        Tasks created here (background compute, cron restart, watcher)
        inherit the provider tag from this task's context.

        The CLI has already run the cross-provider initial price sync
        before this orchestrator was started, so prices are guaranteed
        to be in DB by the time background compute kicks in. The CLI
        also owns ``init_search_index()`` and signals readiness via
        :attr:`search_index_ready` — the watcher waits on it so it
        never writes to an uninitialized index. The background compute
        does not touch the index, so it starts as soon as the initial
        sync is done.
        """
        await self.initial_sync_done.wait()

        # Background compute is independent of the search index and can
        # start as soon as the initial sync is done. The done_callback
        # signals completion so the CLI's global search-indexing task
        # can fire — it must run on success, failure, **and** cancel,
        # otherwise the CLI would block forever.
        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.CLAUDE_CODE_COMPUTE_VERSION,
            compute_factory="twicc.providers.claude_code.compute:get_compute",
        )
        self._compute_task = self._create_task(
            start_background_compute_task(self._compute_ctx)
        )
        self._compute_task.add_done_callback(lambda _t: self.compute_done.set())
        logger.info("Background compute started (after initial sync)")

        # Restart cron jobs from previous process runs. Must run after
        # the watcher is up (below) so that JSONL writes from restarted
        # sessions are picked up — but we schedule it now so that
        # ``shutdown()`` can cancel it cleanly if it never gets a chance
        # to run.
        assert self._shutdown_event is not None
        self._cron_restart_task = self._create_task(
            restart_all_session_crons(stop_event=self._shutdown_event)
        )
        logger.info("Cron restart task launched")

        # Watcher writes new items into the global search index, so it
        # must wait until the CLI has called ``init_search_index()``.
        assert self.search_index_ready is not None, "search_index_ready must be set by start()"
        await self.search_index_ready.wait()
        self._watcher_task = self._create_task(get_watcher().start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Watcher started (after initial sync + search index ready)")
