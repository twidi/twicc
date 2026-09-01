"""
Codex provider orchestrator.

Owns the Codex initial JSONL sync, the background metadata compute,
the JSONL sessions watcher, the Codex CLI auth check task, the ChatGPT
usage sync task, the OpenAI statuspage poll, the periodic skill
catalogue sync (``skills/list`` → ``Command`` rows under the ``$``
prefix), the daily model retirement check, and the shutdown of the Codex
agent manager (its construction is lazy via the agent registry).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress

from asgiref.sync import sync_to_async
from django.conf import settings

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionType
from twicc.orchestrator import BaseOrchestrator
from twicc.providers.background_compute_task import (
    ComputeContext,
    start_background_compute_task,
    stop_background_task,
)
from twicc.providers.codex.agent import get_codex_agent_manager
from twicc.providers.codex.agent.original_files_cache import (
    start_cleanup_task as start_original_files_cache_cleanup,
    stop_cleanup_task as stop_original_files_cache_cleanup,
)
from twicc.providers.codex.commands_task import start_commands_task, stop_commands_task
from twicc.providers.codex.initial_sync import scan_session_files, sync_all
from twicc.providers.codex.plugin_install import ensure_twicc_plugin_installed
from twicc.providers.codex.sessions_watcher import get_watcher
from twicc.providers.statuspage_task import start_statuspage_task, stop_statuspage_task
from twicc.providers.codex.usage_task import start_usage_sync_task, stop_usage_sync_task
from twicc.providers.model_retirement_task import (
    start_model_retirement_task,
    stop_model_retirement_task,
)
from twicc.startup_progress import broadcast_startup_progress

logger = logging.getLogger(__name__)


def _count_total_sessions() -> int:
    """Filesystem-only count of Codex session files (for progress reporting)."""
    return len(scan_session_files())


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


class CodexOrchestrator(BaseOrchestrator):
    """Lifecycle manager for Codex provider tasks.

    Initial sync + auth check + usage sync + statuspage + model retirement.
    """

    provider = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        # This provider runs both initial sync and background compute —
        # reset the inherited pre-set events so the CLI actually waits
        # for our broadcasts.
        self.initial_sync_done = asyncio.Event()
        self.compute_done = asyncio.Event()

        # Cooperative stop event for the initial sync thread
        self._sync_stop_event = threading.Event()

        self._sync_task: asyncio.Task | None = None
        # Future of the initial-sync producer thread (asyncio.to_thread).
        # shutdown() awaits it so the thread is really finished, not just
        # the wrapping coroutine cancelled.
        self._sync_thread_future: asyncio.Future | None = None
        self._orch_task: asyncio.Task | None = None
        self._usage_sync_task: asyncio.Task | None = None
        self._statuspage_task: asyncio.Task | None = None
        self._commands_task: asyncio.Task | None = None
        self._original_files_cache_task: asyncio.Task | None = None
        self._retirement_task: asyncio.Task | None = None

        # Started by the dependency orchestrator coroutine after the
        # initial sync completes (compute) or after the search index is
        # also ready (watcher). Both stay None when shutdown is requested
        # before their prerequisites complete.
        self._compute_task: asyncio.Task | None = None
        self._compute_ctx: ComputeContext | None = None
        self._watcher_task: asyncio.Task | None = None

    def request_thread_stop(self) -> None:
        """Signal the cooperative stop event for the initial sync thread.

        Called from the CLI signal handler so that ``sync_all`` can return
        promptly even mid-iteration.
        """
        self._sync_stop_event.set()

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch the initial sync, dependency orchestrator, watcher, auth
        check, usage sync, and statuspage tasks.

        ``shutdown_event`` is the CLI-level SIGTERM signal, used by loops
        that do non-cancellable long-duration work and need to bail out
        gracefully before the eventual ``task.cancel()`` arrives — the
        canonical use is Claude's :func:`restart_all_session_crons`,
        which retries with exponential backoff. Codex has no such loop:
        its periodic tasks (auth, usage, statuspage, commands,
        original-files cache) and its watcher all suspend on
        ``wait_for(local_stop_event.wait(), timeout=...)``, and the
        ``task.cancel()`` issued by :meth:`shutdown` unblocks every
        ``wait_for`` instantly via ``CancelledError``. The parameter
        stays in the signature to honour :meth:`BaseOrchestrator.start`.

        ``search_index_ready`` is awaited by :meth:`_dependency_orchestrator`
        before launching the JSONL watcher, since the watcher writes new
        items into the global Tantivy index as they arrive.
        """
        self.search_index_ready = search_index_ready

        # Reset stateful events in case this is a hot-restart (provider was
        # toggled off via Settings, then back on). ``shutdown()`` set them
        # all so the previous run's awaiters could finish; without this
        # reset, the new tasks would observe the leftover ``set()`` and
        # exit on the first ``is_set()`` check.
        self._sync_stop_event.clear()
        self.initial_sync_done.clear()
        self.compute_done.clear()

        # Register the TwiCC marketplace and (re)install the plugin before
        # the commands sync task starts polling for skills. The call is
        # idempotent and best-effort — failures are logged inside.
        await ensure_twicc_plugin_installed()

        self._sync_task = self._create_task(self._initial_sync_task())
        self._orch_task = self._create_task(self._dependency_orchestrator())
        self._usage_sync_task = self._create_task(start_usage_sync_task())
        self._statuspage_task = self._create_task(start_statuspage_task(self.provider))
        self._commands_task = self._create_task(start_commands_task())
        self._original_files_cache_task = self._create_task(
            start_original_files_cache_cleanup()
        )
        self._retirement_task = self._create_task(start_model_retirement_task(self.provider))

    async def shutdown(self) -> None:
        """Stop the Codex tasks (sync + compute first, then the periodic ones)."""
        # Make sure the initial sync thread cooperates if it's still running
        self._sync_stop_event.set()

        # Unblock the CLI in case it was awaiting either lifecycle event
        # before our natural ``set()`` could run. Both calls are idempotent.
        self.initial_sync_done.set()
        self.compute_done.set()

        # Cancel the dependency orchestrator first, before any await below.
        # ``initial_sync_done.set()`` just unblocked _dependency_orchestrator's
        # ``await self.initial_sync_done.wait()``; awaiting anything else first
        # would let it resume and start the boot title sync, the background
        # compute and the watcher *while the provider is shutting down*.
        # ``_cancel_task`` runs ``.cancel()`` synchronously — before this
        # coroutine yields — so the orchestrator is killed at its wait() and
        # never runs its body.
        if self._orch_task is not None:
            await _cancel_task(self._orch_task, "Codex orchestrator task")

        if self._sync_task is not None:
            await _cancel_task(self._sync_task, "Codex initial sync task")
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

        # Watcher (may not have started yet — depends on initial sync + search index ready)
        if self._watcher_task is not None:
            logger.info("Stopping Codex watcher...")
            get_watcher().stop_watcher()
            await _cancel_task(self._watcher_task, "Codex watcher")
        else:
            logger.info("Codex watcher was not started, skipping")

        # Background compute (may not have started yet — depends on initial sync)
        if self._compute_task is not None:
            logger.info("Stopping Codex background compute task...")
            # Abandon the compute run before stopping the worker: from here on,
            # every still-queued or still-incoming session_complete for this
            # run is skipped by the DB writer (untracked run), so a
            # shut-down provider's partial compute results never apply — not
            # during this teardown, and not racing the next hot-start. The
            # run's sessions are recomputed on the next start.
            from twicc.providers.db_writer import abandon_compute_run
            await abandon_compute_run(self._compute_ctx.run_id, self.provider)
            await stop_background_task(self._compute_ctx)
            await _cancel_task(self._compute_task, "Codex background compute task")
        else:
            logger.info("Codex background compute was not started, skipping")

        if self._usage_sync_task is not None:
            logger.info("Stopping Codex usage sync task...")
            stop_usage_sync_task()
            await _cancel_task(self._usage_sync_task, "Codex usage sync task")

        if self._statuspage_task is not None:
            logger.info("Stopping Codex statuspage task...")
            stop_statuspage_task(self.provider)
            await _cancel_task(self._statuspage_task, "Codex statuspage task")

        if self._commands_task is not None:
            logger.info("Stopping Codex commands task...")
            stop_commands_task()
            await _cancel_task(self._commands_task, "Codex commands task")

        # Original files cache cleanup
        if self._original_files_cache_task is not None:
            stop_original_files_cache_cleanup()
            await _cancel_task(
                self._original_files_cache_task,
                "Codex original files cache cleanup",
            )

        # Model retirement
        if self._retirement_task is not None:
            logger.info("Stopping Codex model retirement task...")
            stop_model_retirement_task(self.provider)
            await _cancel_task(self._retirement_task, "Codex model retirement task")

        # Stop every live Codex agent. The manager itself is owned by the
        # AgentManagerRegistry singleton, so we just ask it to drain — its
        # internal timeout monitor, agent registry and locks are reset to a
        # fresh state, mirroring the shutdown happening on the Claude side.
        logger.info("Stopping Codex agent manager...")
        await get_codex_agent_manager().shutdown(timeout=5.0)
        logger.info("Codex agent manager stopped")

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
        logger.info("Starting Codex data synchronization...")

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

        sessions_count = await sync_to_async(
            Session.objects.filter(
                provider=Provider.CODEX, stale=False, type=SessionType.SESSION, hidden=False,
            ).count
        )()
        logger.info("Codex data synchronized (%d sessions)", sessions_count)

    async def _sync_titles_at_boot(self) -> None:
        """Import Codex Thread.name into Session.title for every known thread.

        Runs once between the initial JSONL sync and the background compute.
        The title bulk-update is routed through the DB writer (a
        :class:`SyncSessionTitlesJob` on the async queue, handled by
        :meth:`CodexHelpers.try_handle_async_job`) so it never writes to
        SQLite in parallel with the DB writer still draining other
        payloads — the very contention the DB writer exists to remove.

        ``submit_async_job`` awaits the job's future, which the helper
        resolves once the title rows have been **committed** to the DB.
        The ``session_updated`` WS broadcasts run as a best-effort
        post-apply side effect after the producer is already unblocked
        — they may finish before or after the compute phase starts, but
        the DB titles are guaranteed to be in place when we return.
        """
        from twicc.providers.codex.titles import (
            SyncSessionTitlesJob,
            bulk_sync_titles_from_codex,
        )
        from twicc.providers.db_writer import submit_async_job

        titles = await bulk_sync_titles_from_codex()
        if not titles:
            logger.info("Codex title sync at boot: no titles to import")
            return

        future = asyncio.get_running_loop().create_future()
        try:
            changed = await submit_async_job(SyncSessionTitlesJob(
                titles=titles, future=future,
            ))
        except Exception as e:
            logger.warning(
                "Codex title sync at boot failed via DB writer: %s", e,
            )
            return
        logger.info(
            "Codex title sync at boot: %d title(s) routed through the DB "
            "writer (%d changed)",
            len(titles), len(changed),
        )

    async def _dependency_orchestrator(self) -> None:
        """Wait for the initial sync, then start the background compute and
        the JSONL watcher. Also imports Codex Thread names into Session titles
        before compute begins, so clients see accurate titles from the first load.

        Background compute reads existing Codex sessions whose stored
        ``compute_version`` differs from
        :data:`settings.CODEX_COMPUTE_VERSION` and recomputes their
        kind / display_level. The done_callback signals
        :attr:`compute_done` so the CLI's global search-indexing task
        can fire — it must run on success, failure, **and** cancel,
        otherwise the CLI would block forever.

        The watcher writes new items into the global Tantivy index, so
        it must wait until the CLI has called ``init_search_index()``
        and signalled :attr:`search_index_ready`. Background compute
        does not touch the index, so it starts as soon as the initial
        sync is done.
        """
        await self.initial_sync_done.wait()

        # Pull thread names from the Codex state DB *before* the compute
        # task runs so newly-imported titles appear at the same time as
        # the rest of the initial UI state.
        try:
            await self._sync_titles_at_boot()
        except Exception as e:
            logger.warning("Codex title sync at boot failed: %s", e)

        self._compute_ctx = ComputeContext(
            provider=self.provider,
            compute_version=settings.CODEX_COMPUTE_VERSION,
            compute_factory="twicc.providers.codex.compute:get_compute",
        )
        self._compute_task = self._create_task(
            start_background_compute_task(self._compute_ctx)
        )
        self._compute_task.add_done_callback(lambda _t: self.compute_done.set())
        logger.info("Codex background compute started (after initial sync)")

        assert self.search_index_ready is not None, "search_index_ready must be set by start()"
        await self.search_index_ready.wait()
        self._watcher_task = self._create_task(get_watcher().start_watcher())
        self._watcher_task.add_done_callback(_on_watcher_done)
        logger.info("Codex watcher started (after initial sync + search index ready)")
