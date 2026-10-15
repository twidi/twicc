"""
Per-provider orchestrators and their cross-provider registry.

Each provider owns the lifecycle of its own async/background tasks
(JSONL watcher, sync loops, agent manager, ...) inside a subclass of
:class:`BaseOrchestrator`. The CLI server entry point doesn't know which
tasks any given provider runs — it just calls
``start_all`` / ``request_thread_stop_all`` / ``shutdown_all`` on the
:class:`OrchestratorRegistry`, mirroring the registry pattern used for
helpers (``ProviderHelpersRegistry``), agent managers
(``AgentManagerRegistry``) and WS handlers
(``WSConsumer.PROVIDER_HANDLERS``).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Coroutine
from typing import Any, ClassVar, TypeVar

from twicc.core.enums import Provider
from twicc.logging_context import current_provider
from twicc.providers.state import (
    ProviderState,
    force_disable_after_failed_start,
    get_enabled_providers,
    is_provider_enabled,
    is_provider_running,
    set_provider_state,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseOrchestrator:
    """Lifecycle owner for one provider's async/background tasks.

    Subclasses must:

    - Set the :attr:`provider` ClassVar to the matching :class:`Provider`
      enum value.
    - Implement :meth:`start` (launch tasks; should return promptly once
      tasks are scheduled — ``run_server`` only awaits the call long
      enough to register them).
    - Implement :meth:`shutdown` (cancel tasks in the right order; should
      be best-effort and time-bounded).

    :meth:`request_thread_stop` has a no-op default — only providers that
    run blocking sync threads (e.g. the initial JSONL sync that scans a
    provider's on-disk store) need to override it to signal those threads
    cooperatively.
    """

    provider: ClassVar[Provider]

    def __init__(self) -> None:
        # Fail fast if the subclass forgot to set its provider key. Without
        # this guard, a missing attribute would only surface deep inside
        # the registry's first iteration as an opaque ``AttributeError``.
        # Mirrors the same guard in ``BaseAgent``.
        if not getattr(type(self), "provider", None):
            raise TypeError(
                f"{type(self).__name__}.provider must be set to a "
                "Provider enum value."
            )

        # Cross-provider lifecycle signals owned by the CLI.
        #
        # ``initial_sync_done`` and ``compute_done`` are pre-set by default
        # so providers without those phases (e.g. Codex today) don't make
        # the CLI wait forever on ``await asyncio.gather(*(o.<event>.wait()
        # for o in orchestrators))``. Providers that do run the phase reset
        # the event in their ``__init__`` and call ``.set()`` once their
        # corresponding ``broadcast_startup_progress(..., completed=True)``
        # has fired.
        self.initial_sync_done = asyncio.Event()
        self.initial_sync_done.set()
        self.compute_done = asyncio.Event()
        self.compute_done.set()

        # Set by the CLI after ``init_search_index()`` returns. Providers
        # that run a JSONL watcher writing to the index must ``await`` this
        # before their watcher starts; providers without a watcher ignore it.
        self.search_index_ready: asyncio.Event | None = None

    async def start(self, shutdown_event: asyncio.Event, search_index_ready: asyncio.Event) -> None:
        """Launch this provider's background tasks.

        ``shutdown_event`` is the shared CLI-level event set by the
        signal handler when the server begins to stop; long-running
        async loops that this orchestrator owns should watch it and
        bail out promptly.

        ``search_index_ready`` is a CLI-owned event set after the global
        ``init_search_index()`` returns. Providers whose watcher writes
        into the search index must ``await`` it before starting that
        watcher.
        """
        raise NotImplementedError

    def request_thread_stop(self) -> None:
        """Cooperative stop signal for blocking sync threads owned by this provider.

        Called from the CLI signal handler so blocking threads
        (e.g. an initial filesystem sync that walks disk inside
        ``asyncio.to_thread``) can exit promptly without waiting for
        their next chunk to finish.

        Async tasks listen for the shared ``shutdown_event`` passed to
        :meth:`start` instead — this hook is specifically for threads.

        Default: no-op. Providers without blocking sync threads don't
        need to override.
        """
        return

    async def shutdown(self) -> None:
        """Stop this provider's tasks in dependency-safe order.

        Best-effort and time-bounded: an exception here must not prevent
        other providers' shutdowns from running (the registry catches
        them via ``return_exceptions=True``).
        """
        raise NotImplementedError

    def _create_task(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        """Schedule ``coro`` as a task tagged with this orchestrator's provider.

        Wrapper around :func:`asyncio.create_task` that runs the new task
        in a context where :data:`twicc.logging_context.current_provider`
        is set to ``self.provider.value``. Any log record emitted from
        the task — directly or through a ``sync_to_async`` /
        ``asyncio.to_thread`` call it makes, both of which propagate the
        context — is stamped with the right provider tag automatically.

        The contextvar is set inside a fresh :func:`contextvars.copy_context`
        so the orchestrator's own caller (e.g. the CLI startup task) is
        not retagged when this method returns. Sub-tasks created from
        within ``coro`` (including :func:`asyncio.create_task` calls)
        inherit the tagged context, so providers only need to route every
        long-running task through this helper at the orchestrator level.
        """
        ctx = contextvars.copy_context()
        ctx.run(current_provider.set, self.provider.value)
        return asyncio.create_task(coro, name=name, context=ctx)


class OrchestratorRegistry:
    """Singleton holding one :class:`BaseOrchestrator` per provider.

    Instances are created eagerly when the registry is built, so a
    second call to ``start_all`` after a ``shutdown_all`` would reuse
    the same instances — providers must reset their internal state in
    ``shutdown`` if they want to support that.

    The current CLI lifecycle (start once, run server, shutdown once)
    doesn't exercise the reuse path, but keeping the contract explicit
    avoids surprises if a future host (e.g. a hot-reload harness)
    decides to restart.
    """

    PROVIDER_ORCHESTRATORS: ClassVar[dict[Provider, type[BaseOrchestrator]]]

    def __init__(self) -> None:
        # Imported here to avoid a circular import at module load time:
        # each provider orchestrator imports models / registries that
        # eventually import from this package.
        from twicc.providers.claude_code.orchestrator import ClaudeCodeOrchestrator
        from twicc.providers.codex.orchestrator import CodexOrchestrator

        self.PROVIDER_ORCHESTRATORS = {
            Provider.CLAUDE_CODE: ClaudeCodeOrchestrator,
            Provider.CODEX: CodexOrchestrator,
        }
        from twicc.providers.compute_work import ComputeWorkState

        self.compute_work = ComputeWorkState()
        self._orchestrators: dict[Provider, BaseOrchestrator] = {
            key: cls() for key, cls in self.PROVIDER_ORCHESTRATORS.items()
        }

        for orch in self._orchestrators.values():
            orch.compute_work = self.compute_work

        # Stashed by start_all() so start_one() / shutdown_one() can reuse them
        # on hot-toggle without the caller having to thread them through.
        self._shutdown_event: asyncio.Event | None = None
        self._search_index_ready: asyncio.Event | None = None

        # In-flight fire-and-forget hot-toggle tasks (finish_start /
        # finish_shutdown). Tracked so shutdown_all() can await them before
        # the caller stops the DB writer: a hot-toggled provider's
        # producer (initial-sync thread / compute worker) is alive until its
        # finish_shutdown completes, and the writer must outlive every
        # producer. Each task removes itself via a done-callback.
        self._inflight_hot_toggle_tasks: set[asyncio.Task[None]] = set()

        # Background task pre-downloading the Codex CLI runtime (see start_all).
        self._codex_runtime_task: asyncio.Task | None = None

    def get(self, provider: Provider) -> BaseOrchestrator:
        """Return the orchestrator for ``provider``."""
        return self._orchestrators[provider]

    def items(self) -> list[tuple[Provider, BaseOrchestrator]]:
        """Return ``(provider, orchestrator)`` pairs for every registered provider."""
        return list(self._orchestrators.items())

    def values(self) -> list[BaseOrchestrator]:
        """Return the orchestrator instances for every registered provider."""
        return list(self._orchestrators.values())

    # ------------------------------------------------------------------
    # Aggregate operations driven by the CLI lifecycle
    # ------------------------------------------------------------------

    async def start_all(
        self,
        shutdown_event: asyncio.Event,
        search_index_ready: asyncio.Event,
    ) -> None:
        """Start every enabled provider's orchestrator in parallel.

        Each :meth:`BaseOrchestrator.start` is non-blocking (it schedules
        tasks and returns), so ``gather`` mostly serves to overlap
        eventual init work. ``return_exceptions=True`` keeps a single
        provider's startup failure from cancelling the others — failures
        are logged so they don't go silent.

        ``search_index_ready`` is forwarded to every orchestrator so the
        ones that own a watcher (which writes to the global Tantivy
        index) can ``await`` it before starting that watcher.

        Only providers listed by :func:`twicc.providers.state.get_enabled_providers`
        are started. If no provider is enabled (no choice made yet, or everything
        disabled), nothing is started and the app keeps serving the initial
        provider-selection dialog.

        The two events are stashed on the registry so :meth:`start_one` /
        :meth:`shutdown_one` can reuse them later for hot-toggle.
        """
        # Stash so start_one() / shutdown_one() can reuse on hot-toggle.
        self._shutdown_event = shutdown_event
        self._search_index_ready = search_index_ready

        # Pre-download the Codex CLI runtime unconditionally, in the background.
        # NOT gated on Codex being enabled: the user can enable Codex live from
        # the UI and must not wait ~1 min for the binary at that point. Best-
        # effort — a failure here only means Codex features degrade until the
        # next attempt; TwiCC and Claude Code keep running.
        self._codex_runtime_task = asyncio.create_task(
            self._predownload_codex_runtime(),
            name="codex-runtime-predownload",
        )

        enabled = get_enabled_providers()
        if not enabled:
            return

        enabled_items = [(provider, orch) for provider, orch in self._orchestrators.items() if provider in enabled]
        tasks = [
            asyncio.create_task(
                self._start_with_state(provider),
                context=self._provider_context(provider),
            )
            for provider, _ in enabled_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (provider, _), result in zip(enabled_items, results):
            if isinstance(result, BaseException):
                logger.error(
                    "Orchestrator for %s failed to start: %s",
                    provider.value, result, exc_info=result,
                )

    async def _predownload_codex_runtime(self) -> None:
        """Fetch the Codex CLI runtime in the background (best-effort).

        Scheduled unconditionally by :meth:`start_all` so a later live enable
        of the Codex provider doesn't have to wait for the ~110 MB download.
        Any failure is logged and swallowed — the runtime is re-fetched on
        first real use via ``make_codex_config`` → ``ensure_codex_runtime``.
        """
        try:
            from twicc.providers.codex.runtime import ensure_codex_runtime
            await ensure_codex_runtime()
        except Exception:
            logger.exception(
                "Codex runtime pre-download failed; it will be retried on first use"
            )

    async def wait_initial_sync_done(self) -> None:
        """Block until every enabled provider's initial sync has reported completion.

        Providers without an initial sync inherit a pre-set event from
        :class:`BaseOrchestrator` so this returns instantly for them.

        Only waits on providers that were actually started (i.e. enabled ones).
        Waiting on an un-started provider's event would hang forever because
        ``start()`` is what triggers ``.set()`` on it.
        """
        enabled = get_enabled_providers()
        if not enabled:
            return
        await asyncio.gather(*(
            orch.initial_sync_done.wait()
            for provider, orch in self._orchestrators.items()
            if provider in enabled
        ))

    async def wait_compute_done(self) -> None:
        """Block until every enabled provider's background compute has reported completion.

        Same pre-set default as :meth:`wait_initial_sync_done` for
        providers without a compute phase. Only waits on enabled providers
        (same reasoning as :meth:`wait_initial_sync_done`).
        """
        enabled = get_enabled_providers()
        if not enabled:
            return
        await asyncio.gather(*(
            orch.compute_done.wait()
            for provider, orch in self._orchestrators.items()
            if provider in enabled
        ))

    async def begin_start(self, provider: Provider) -> None:
        """Synchronously broadcast the ``stopped → starting`` transition.

        Fast: the only work is a single ``set_provider_state`` (sync state
        update + ``group_send``). Must be followed by :meth:`finish_start`
        — either awaited directly (boot path) or scheduled as a background
        task (hot-toggle path) so the WS handler is not blocked on the slow
        ``orch.start()`` body.

        Splitting the start in two halves is what guarantees that
        ``provider_state_changed:starting`` reaches the client before any
        ``synced_settings_updated`` broadcast that the caller emits right
        after — the slow body used to race that broadcast back when the
        caller fire-and-forgot the whole start.

        Wrapped in ``asyncio.create_task(..., context=...)`` so the
        transition log line (``Provider state: stopped -> starting``) is
        stamped with the provider tag even when called from a context that
        has no tag (e.g. the WS handler).
        """
        if self._shutdown_event is None or self._search_index_ready is None:
            raise RuntimeError("OrchestratorRegistry.begin_start called before start_all")
        if provider not in self._orchestrators:
            return
        self.compute_work.set_pending(provider, True)
        await asyncio.create_task(
            set_provider_state(provider, ProviderState.STARTING),
            context=self._provider_context(provider),
        )

    async def finish_start(self, provider: Provider) -> None:
        """Run the slow ``orch.start()`` body and finalise the transition.

        Must be invoked **after** :meth:`begin_start` (which broadcasts the
        ``starting`` state). Flow:

        - ``await orch.start(...)``
        - On success: ``starting → running`` (broadcast)
        - On failure: roll back to ``stopped``, persist the provider into
          ``disabledProviders`` so the UI switch flips off, and re-raise
          so the caller's error handling (e.g. ``gather`` with
          ``return_exceptions=True``) sees the exception.

        Designed to be scheduled via :meth:`schedule_finish_start` for the
        hot-toggle path (fire-and-forget so the WS handler does not block
        on plugin install, app-server boot, …).
        """
        assert self._shutdown_event is not None
        assert self._search_index_ready is not None
        orch = self._orchestrators.get(provider)
        if orch is None:
            return
        try:
            await orch.start(self._shutdown_event, self._search_index_ready)
        except BaseException:
            logger.exception("Failed to start orchestrator for %s — forcing disable", provider.value)
            try:
                await orch.shutdown()
            except BaseException:
                logger.exception("Failed-start teardown for %s did not drain; retaining compute gate", provider.value)
            else:
                self.compute_work.set_pending(provider, False)
            await set_provider_state(provider, ProviderState.STOPPED)
            await force_disable_after_failed_start(provider)
            raise
        await set_provider_state(provider, ProviderState.RUNNING)

    def schedule_finish_start(
        self,
        provider: Provider,
        *,
        trigger_search_reindex: bool = True,
    ) -> asyncio.Task[None]:
        """Schedule :meth:`finish_start` as a tagged background task.

        Used by the WS hot-toggle handler so the caller does not need to
        know about :meth:`_provider_context`. Returns the ``finish_start``
        task so callers that want to await the full transition (e.g.
        :meth:`start_one`) can do so.

        When ``trigger_search_reindex`` is True (the default for hot-toggle),
        a follow-up :meth:`_kick_search_after_compute` task is scheduled
        only if ``finish_start`` completes successfully — a failed start
        rolls back to ``stopped`` and persists the provider into
        ``disabledProviders``, so kicking the search re-index would be
        meaningless (and would dangle on a ``compute_done`` that may
        never fire). See :meth:`_kick_search_after_compute` for why the
        re-index matters for historic sessions bulk-inserted by initial
        sync.
        """
        task = asyncio.create_task(
            self.finish_start(provider),
            context=self._provider_context(provider),
        )
        self._track_hot_toggle_task(task)
        task.add_done_callback(self._log_failure_done_callback(provider, "start"))
        if trigger_search_reindex:
            def _kick_on_success(t: asyncio.Task[None]) -> None:
                # Cancelled or raised → skip the re-index. ``t.exception()``
                # is idempotent here: the failure-logging callback added
                # above already retrieved it (so no asyncio warning will
                # fire), and a second call just returns the cached value.
                if t.cancelled() or t.exception() is not None:
                    return
                # Global shutdown began while finish_start was in flight
                # (round-3 #3 makes shutdown_all() await such in-flight
                # tasks, so this callback can fire during shutdown_all()).
                # run.py cancels the active search-indexing tasks just
                # before shutdown_all(), so scheduling a fresh kick now
                # would race the search index teardown — skip it.
                if self._shutdown_event is not None and self._shutdown_event.is_set():
                    return
                asyncio.create_task(
                    self._kick_search_after_compute(provider),
                    context=self._provider_context(provider),
                )
            task.add_done_callback(_kick_on_success)
        return task

    async def _start_with_state(self, provider: Provider) -> None:
        """Run the full ``stopped → starting → running`` cycle in a single task.

        Used by :meth:`start_all` where every provider is started in
        parallel via ``asyncio.gather`` and the caller wants to await the
        whole cycle (boot has no race with a subsequent
        ``synced_settings_updated`` broadcast — the client is not even
        connected yet).

        Equivalent to ``begin_start`` + ``finish_start`` chained.
        ``self._shutdown_event`` and ``self._search_index_ready`` must have
        been set by :meth:`start_all` already (the only caller).
        """
        await self.begin_start(provider)
        await self.finish_start(provider)

    async def begin_shutdown(self, provider: Provider) -> None:
        """Synchronously broadcast the ``running → stopping`` transition.

        Symmetric to :meth:`begin_start`: fast (single
        ``set_provider_state``), must be followed by
        :meth:`finish_shutdown` — either awaited directly (boot path,
        :meth:`shutdown_one`) or scheduled in the background (hot-toggle
        path) so the WS handler is not blocked on the slow
        ``orch.shutdown()`` body.
        """
        if provider not in self._orchestrators:
            return
        await asyncio.create_task(
            set_provider_state(provider, ProviderState.STOPPING),
            context=self._provider_context(provider),
        )

    async def finish_shutdown(self, provider: Provider) -> None:
        """Run the slow ``orch.shutdown()`` body and finalise the transition.

        Must be invoked **after** :meth:`begin_shutdown`. Ends in
        ``stopped`` unconditionally — even if shutdown raised. The task
        graph is gone either way; staying in ``stopping`` would leave the
        UI permanently busy.
        """
        orch = self._orchestrators.get(provider)
        if orch is None:
            return
        try:
            await orch.shutdown()
            self.compute_work.set_pending(provider, False)
        finally:
            await set_provider_state(provider, ProviderState.STOPPED)

    def schedule_finish_shutdown(self, provider: Provider) -> asyncio.Task[None]:
        """Schedule :meth:`finish_shutdown` as a tagged background task.

        Mirror of :meth:`schedule_finish_start` — used by the WS hot-toggle
        handler so the handler does not block on the slow shutdown body.
        Attaches a failure-logging done-callback so an unhandled
        ``orch.shutdown()`` exception lands in ``backend.log`` with the
        provider name, matching the per-provider log emitted by
        :meth:`shutdown_all` at teardown.
        """
        task = asyncio.create_task(
            self.finish_shutdown(provider),
            context=self._provider_context(provider),
        )
        self._track_hot_toggle_task(task)
        task.add_done_callback(self._log_failure_done_callback(provider, "shut down cleanly"))
        return task

    async def _shutdown_with_state(self, provider: Provider) -> None:
        """Run the full ``running → stopping → stopped`` cycle in a single task.

        Used by :meth:`shutdown_all` where every provider is shut down in
        parallel via ``asyncio.gather`` and the caller wants to await the
        whole cycle. Equivalent to ``begin_shutdown`` + ``finish_shutdown``
        chained.
        """
        await self.begin_shutdown(provider)
        await self.finish_shutdown(provider)

    def _track_hot_toggle_task(self, task: asyncio.Task[None]) -> None:
        """Register a fire-and-forget hot-toggle task so :meth:`shutdown_all`
        can await it before the caller stops the DB writer.

        A hot-toggled provider's producer (initial-sync thread / compute
        worker) is alive until its ``finish_start`` / ``finish_shutdown`` task
        completes; the writer must outlive every producer. The task removes
        itself from the set on completion.
        """
        self._inflight_hot_toggle_tasks.add(task)
        task.add_done_callback(self._inflight_hot_toggle_tasks.discard)

    @staticmethod
    def _log_failure_done_callback(provider: Provider, action: str):
        """Build an ``add_done_callback`` that logs per-provider failures.

        Used on the fire-and-forget tasks returned by
        :meth:`schedule_finish_start` / :meth:`schedule_finish_shutdown`
        so a failed hot-toggle leaves the same kind of trace in
        ``backend.log`` as the boot path's ``start_all`` / ``shutdown_all``
        already do via their ``gather(return_exceptions=True) + zip``
        loop. Without it, an unhandled task exception only surfaces as
        asyncio's generic "Task exception was never retrieved" warning,
        which loses the provider name and the per-provider intent.

        ``action`` is interpolated into the log line ("failed to {action}"),
        so callers should pass a short verb phrase that reads naturally
        — typically ``"start"`` for :meth:`schedule_finish_start` and
        ``"shut down cleanly"`` for :meth:`schedule_finish_shutdown` (the
        same phrasing as the matching log in :meth:`shutdown_all`).
        """
        def _cb(t: asyncio.Task[None]) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            logger.error(
                "Orchestrator for %s failed to %s: %s",
                provider.value, action, exc, exc_info=exc,
            )
        return _cb

    @staticmethod
    def _provider_context(provider: Provider) -> contextvars.Context:
        """Return a fresh Context with ``current_provider`` tagged for ``provider``.

        Mirrors :meth:`BaseOrchestrator._create_task` but for one-shot
        awaitables. ``start()`` and ``shutdown()`` themselves log synchronous
        lines (and ``start()`` even awaits sync work like
        ``ensure_twicc_plugin_installed``) before spawning the long-running
        tasks. Those synchronous log lines must carry the provider tag too,
        otherwise they fall back to ``"global"`` because the caller has no
        provider context (the WS handler for hot-toggle, the CLI startup
        task for ``start_all``).

        Returning a fresh ``Context`` lets us schedule the call via
        ``asyncio.create_task(..., context=...)``, which isolates the
        tag to the new task without touching the caller's context — safe
        with ``gather`` because each scheduled task gets its own context.
        """
        ctx = contextvars.copy_context()
        ctx.run(current_provider.set, provider.value)
        return ctx

    async def start_one(self, provider: Provider, *, trigger_search_reindex: bool = True) -> None:
        """Start a single provider end-to-end (synchronous wrt the caller).

        Convenience for callers that want to await the full
        ``stopped → starting → running`` cycle (CLI scripts, tests, …).
        The WS hot-toggle path does NOT use this — it splits the work so
        the handler can broadcast ``synced_settings_updated`` between
        ``begin_start`` and ``finish_start``, guaranteeing the
        ``provider_state_changed:starting`` arrives at the client first.

        Requires :meth:`start_all` to have been called first so the
        shutdown and search-index events are available; :meth:`begin_start`
        raises :exc:`RuntimeError` if that contract is violated.

        ``trigger_search_reindex`` (default True) schedules a background
        re-trigger of the cross-provider search indexing task after the
        provider's ``compute_done`` event fires. This catches the historic
        sessions that the initial sync bulk-inserts with
        ``search_version=NULL`` (the live JSONL watcher only indexes
        brand-new file writes, so without this rerun those rows would
        remain invisible to ``twicc search`` until the next process
        restart). The DB filter inside the indexing task naturally scopes
        the work to sessions that need it, and the task's internal lock
        keeps a concurrent boot pass from racing against this re-trigger.
        """
        await self.begin_start(provider)
        await self.schedule_finish_start(
            provider, trigger_search_reindex=trigger_search_reindex,
        )

    async def _kick_search_after_compute(self, provider: Provider) -> None:
        """Wait for ``provider``'s compute to finish, then re-trigger search indexing.

        Only scheduled after a *successful* :meth:`finish_start` (see the
        done-callback in :meth:`schedule_finish_start`), so we don't have
        to worry about waiting on a ``compute_done`` that will never fire
        normally — the event will be set by either the provider's
        compute-task done-callback (happy path) or by
        :meth:`BaseOrchestrator.shutdown` (which sets it idempotently
        during a later teardown). If the orchestrator is somehow gone
        from the registry by the time we run (shouldn't happen, but
        defensive), we silently no-op.
        """
        orch = self._orchestrators.get(provider)
        if orch is None:
            return
        await orch.compute_done.wait()
        # compute_done is also set idempotently by shutdown() during
        # teardown, so reaching here does not mean compute really finished.
        # If the global shutdown began while we were waiting, skip the
        # re-index: run.py has already cancelled the active search-indexing
        # tasks and a fresh kick now would race the search index teardown.
        if self._shutdown_event is not None and self._shutdown_event.is_set():
            return
        # A provider hot-toggle off also sets compute_done — shutdown() sets
        # it idempotently to release waiters — without touching the global
        # shutdown event the guard above checks. If this provider is no longer
        # running-and-enabled, the wait was unblocked by its teardown, not by
        # compute finishing: skip the re-index. The mark writes themselves go
        # through the DB writer now (R19's _MarkSessionsIndexedJob), so this
        # is no longer about SQLite write contention; the concern is that
        # kicking off a fresh sweep right as a provider is tearing down would
        # index a partial / inconsistent slice of its sessions and add an
        # asyncio task to the indexer pool that the teardown path then has
        # to chase down through ``get_active_indexing_tasks``.
        if not is_provider_running(provider) or not is_provider_enabled(provider):
            return
        # Lazy import to avoid pulling search_indexing_task at module
        # import time (it would in turn import Django models). The
        # orchestrator registry is imported very early in the CLI boot
        # and we want Django to be fully configured first.
        from twicc.search_indexing_task import kick_off_search_indexing

        await kick_off_search_indexing()
        logger.info(
            "Background search indexing re-triggered (after %s compute)",
            provider.value,
        )

    async def shutdown_one(self, provider: Provider) -> None:
        """Shutdown a single provider end-to-end (synchronous wrt the caller).

        Convenience for callers that want to await the full
        ``running → stopping → stopped`` cycle. The WS hot-toggle path
        does NOT use this — it splits the work via ``begin_shutdown`` +
        ``schedule_finish_shutdown`` so the handler is not blocked on
        ``orch.shutdown()`` (which can take several seconds when there
        are agents to drain).
        """
        await self.begin_shutdown(provider)
        await self.schedule_finish_shutdown(provider)

    def request_thread_stop_all(self) -> None:
        """Signal every provider's blocking sync threads to stop.

        Synchronous (called from the signal handler). Providers without
        blocking threads inherit the no-op default.
        """
        for orch in self._orchestrators.values():
            try:
                orch.request_thread_stop()
            except Exception as e:  # noqa: BLE001 — must not block other providers
                logger.error(
                    "Orchestrator %s.request_thread_stop failed: %s",
                    type(orch).__name__, e, exc_info=True,
                )

    async def shutdown_all(self) -> None:
        """Stop every enabled provider's tasks in parallel.

        First awaits any in-flight fire-and-forget hot-toggle task
        (``finish_start`` / ``finish_shutdown``) — see the body — so a
        provider mid hot-toggle is not missed by the per-enabled teardown
        below while its producer is still alive.

        Filters on the **currently enabled** providers (i.e. the present
        value of :func:`get_enabled_providers`). Under normal operation this
        is the right set: a provider's ``start()`` was either invoked by
        ``start_all`` at boot, or by ``start_one`` on a later hot-toggle.
        Calling ``shutdown()`` on an orchestrator whose ``start()`` was
        never invoked can raise ``AttributeError`` on attributes that are
        only created during startup (e.g. a watcher task).

        Edge case worth noting: if ``start_all`` raised for a specific
        provider (caught by ``return_exceptions=True``), that provider may
        still be in the "enabled" set without having been successfully
        started. In that situation, ``shutdown()`` may raise. The error is
        logged and does not affect the other providers' teardown.

        Each orchestrator owns its own task graph and teardown order, so
        parallel shutdown is safe — and faster when a provider has slow
        teardown work (waiting on a JSONL watcher to drain, killing SDK
        processes, ...). ``return_exceptions=True`` ensures a single
        failing provider doesn't leave the others' tasks dangling.
        """
        # A hot-toggled provider is brought up / torn down by a fire-and-forget
        # finish_start / finish_shutdown task and may not be in the current
        # enabled set, so the per-enabled teardown below would miss it — yet
        # its producer (initial-sync thread / compute worker) is alive until
        # that task completes, and the caller stops the DB writer
        # right after this returns. Await the in-flight ones first.
        inflight = list(self._inflight_hot_toggle_tasks)
        if inflight:
            logger.info(
                "shutdown_all: awaiting %d in-flight hot-toggle task(s)", len(inflight),
            )
            await asyncio.gather(*inflight, return_exceptions=True)

        # Cancel the background Codex runtime pre-download if it's still going
        # (scheduled unconditionally by start_all, independent of the enabled
        # set below).
        if self._codex_runtime_task is not None and not self._codex_runtime_task.done():
            self._codex_runtime_task.cancel()

        enabled = get_enabled_providers()
        if not enabled:
            return
        enabled_items = [(p, o) for p, o in self._orchestrators.items() if p in enabled]
        tasks = [
            asyncio.create_task(
                self._shutdown_with_state(provider),
                context=self._provider_context(provider),
            )
            for provider, _ in enabled_items
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (provider, _), result in zip(enabled_items, results):
            if isinstance(result, BaseException):
                logger.error(
                    "Orchestrator for %s failed to shut down cleanly: %s",
                    provider.value, result, exc_info=result,
                )


_registry: OrchestratorRegistry | None = None


def get_orchestrator_registry() -> OrchestratorRegistry:
    """Return the global :class:`OrchestratorRegistry` (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = OrchestratorRegistry()
    return _registry
