"""
CLI entry point for the TWICC application.

Handles Django setup, migrations, and starts the server. Each provider's
own background tasks (sync, watcher, compute, auth, usage, ...) are
owned by its :class:`BaseOrchestrator` subclass; the CLI just iterates
the :class:`OrchestratorRegistry` to start, signal, and shut them down.

The CLI itself owns the cross-provider tasks:
- PyPI version check
- OpenRouter price sync (one fetch shared across every provider that
  has declared an ``OPENROUTER_MODEL_PREFIX``)
- Tantivy search index lifecycle (``init_search_index`` /
  ``shutdown_search_index``) and the startup search-indexing task,
  gated on every provider's initial-sync / compute completion via the
  events on :class:`BaseOrchestrator`.

Used by:
- ``uvx twicc`` / ``pip install twicc && twicc``  (via project.scripts)
- ``python -m twicc``  (via __main__.py)
- ``uv run run.py``  (dev wrapper at repo root)
"""

import asyncio
import logging
import os
import sys

from twicc.paths import ensure_env_loaded, get_env_load_warnings

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env).
# Already done by the ``twicc.cli`` package import; idempotent, kept so the boot
# order of this module stays explicit.
ensure_env_loaded()

# Configure Django before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

import django  # noqa: E402

django.setup()

# Clean up provider-specific environment variables that may have been
# inherited from a parent process (e.g. ``CLAUDE_CODE_*`` when TwiCC is
# launched from within Claude Code). These would make subprocesses we
# spawn (login shell, tmux, the provider CLI itself) think they are
# already inside an SDK session. Each provider's helper purges its own
# markers; ordering after ``django.setup()`` is required because the
# helpers registry instantiates provider helpers that touch Django
# models on import. None of the variables we strip influence anything
# Django reads at startup, so the move is benign.
from twicc.providers.helpers import get_provider_helpers_registry  # noqa: E402

get_provider_helpers_registry().purge_env_vars(os.environ)

# Strip our own ``DJANGO_SETTINGS_MODULE`` from the environment the server
# process passes on to everything it spawns. Like the provider markers above,
# every agent / terminal / subprocess inherits this server's ``os.environ``;
# leaving ``DJANGO_SETTINGS_MODULE=twicc.settings`` in it would override the
# settings module of *another* Django project an agent might be working on
# (e.g. its ``manage.py`` would load twicc's settings instead of its own).
#
# Safe to drop here: ``django.setup()`` above has already cached the settings
# for this process, and the in-process uvicorn / migrations never re-read the
# env var. The one process that still needs it — the spawned compute worker
# (multiprocessing "spawn") — sets it itself before its own ``django.setup()``
# (see ``twicc.providers.background_compute_task.compute_worker_main``), so it
# no longer relies on inheriting it from here.
os.environ.pop("DJANGO_SETTINGS_MODULE", None)

# Logger must be created AFTER django.setup() so LOGGING config is applied
logger = logging.getLogger("twicc.run")

# Add a temporary console handler for startup messages (just the text, no timestamp/level).
# It will be removed once the server is about to start, so only the file handler remains.
_startup_console = logging.StreamHandler()
_startup_console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("twicc").addHandler(_startup_console)

# Now we can import Django-dependent modules
from django.core.management import call_command  # noqa: E402

from twicc.instance_lock import InstanceAlreadyRunning, InstanceLock  # noqa: E402
from twicc.orchestrator import get_orchestrator_registry  # noqa: E402
from twicc.paths import get_data_dir  # noqa: E402
from twicc.pricing_task import start_price_sync_task, sync_all_providers  # noqa: E402
from twicc.benchmarks_task import start_benchmark_sync_task  # noqa: E402
from twicc.quota_wakeup_task import start_quota_wakeup_task  # noqa: E402
from twicc.session_dirs_cleanup_task import start_session_dirs_cleanup_task  # noqa: E402
from twicc.peer_purge_task import start_peer_purge_task  # noqa: E402
from twicc.tmux_cleanup_task import start_tmux_cleanup_task  # noqa: E402
from twicc.auth.tokens import start_last_used_flush_task  # noqa: E402
from twicc.share.view_tracking import start_share_view_flush_task  # noqa: E402
from twicc.artifacts.denial_tracking import start_denial_flush_task  # noqa: E402
from twicc.telemetry import start_telemetry_task  # noqa: E402
from twicc.search import SearchIndexLockedError, init_search_index, shutdown_search_index  # noqa: E402
from twicc.search_indexing_task import (  # noqa: E402
    get_active_indexing_tasks,
    kick_off_search_indexing,
    stop_search_index_task,
)
from twicc.version_check_task import start_version_check_task, stop_version_check_task  # noqa: E402
from twicc.tips_manifest import init_manifest, start_tips_watcher_task  # noqa: E402
from twicc.help_manifest import init_manifest as init_help_manifest, start_help_watcher_task  # noqa: E402


async def _cancel_task(task: asyncio.Task | None, name: str) -> None:
    """Cancel an asyncio task and wait for it to finish."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


async def _orchestrate_global_search(
    orchestrators,
    shutdown_event: asyncio.Event,
    search_index_ready: asyncio.Event,
    request_shutdown,
) -> None:
    """Coordinate the cross-provider parts of the search lifecycle.

    Initializes the global Tantivy index once every provider's initial
    sync has reported completion, then signals ``search_index_ready``
    so provider watchers can start writing to it. After every
    provider's background compute has reported completion, fires the
    global search-indexing task via :func:`kick_off_search_indexing`,
    which also registers the task handle into the module-level list
    consulted by the shutdown path.

    Hot-toggle re-triggers happen separately from this coroutine: the
    orchestrator registry schedules its own ``kick_off_search_indexing``
    after each ``start_one`` once the provider's ``compute_done`` fires,
    and the run lock inside the indexing module serializes those runs
    against this boot pass.

    ``shutdown_event`` short-circuits both gates so a server stopping
    mid-startup doesn't leave dangling work. ``request_shutdown`` is the
    callback used to stop the server when initialization fails fatally
    (currently: Tantivy writer lock already held by another process).
    """
    await orchestrators.wait_initial_sync_done()
    if shutdown_event.is_set():
        return

    try:
        await asyncio.to_thread(init_search_index)
    except SearchIndexLockedError as exc:
        logger.error(
            "Cannot start TwiCC: the search index writer lock at %s is already held.\n"
            "This usually means a previous TwiCC process did not shut down cleanly and a\n"
            "subprocess (typically the background compute worker) is still running.\n"
            "Run `pkill -f twicc` to clean up stale processes, then start TwiCC again.\n"
            "If the issue persists, identify the holder with:\n"
            "  lsof %s/.tantivy-writer.lock",
            exc.search_dir, exc.search_dir,
        )
        request_shutdown()
        return

    logger.info("Search index initialized (after every provider's initial sync)")
    search_index_ready.set()

    await orchestrators.wait_compute_done()
    if shutdown_event.is_set():
        return

    await kick_off_search_indexing()
    logger.info("Background search indexing started (after every provider's compute)")


async def run_server(port: int):
    """Run the ASGI server with all background tasks."""
    import signal

    import uvicorn

    from twicc.asgi import application

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    # Start the DB writer FIRST, before any producer that writes to
    # the DB. Every periodic task that lands a write — initial price sync
    # below, commands_task / usage_task / model_retirement_task spawned by
    # the orchestrators, and the per-provider initial sync + compute they
    # drive — routes its writes through this DB writer's queues. Standing it
    # up before any of them guarantees the DB writer always exists by the
    # time a producer tries to submit. ``stop_db_writer`` is used at
    # the very end of this function's shutdown path, after every producer
    # has shut down.
    from twicc.providers.db_writer import start_db_writer, stop_db_writer
    start_db_writer()

    # Cross-provider initial price sync runs *before* per-provider orchestrators
    # so they can rely on prices being in DB by the time their compute paths run.
    # A single OpenRouter fetch covers every provider that has declared an
    # ``OPENROUTER_MODEL_PREFIX``; failure here is logged and non-fatal. The
    # actual ``ModelPrice`` inserts flow through the DB writer started
    # just above.
    await sync_all_providers()
    init_manifest()
    init_help_manifest()

    # When ``TWICC_AUTO_ENABLE_PROVIDERS=1`` (devctl worktree mode) seeds the
    # initial ``disabledProviders=[]`` choice if the file lacks one, so the
    # orchestrators below see every provider as enabled instead of the empty
    # set that gates the activation dialog. No-op once the user has made a
    # choice — toggles from Settings keep working normally.
    from twicc.providers.state import apply_auto_enable_providers_bootstrap
    apply_auto_enable_providers_bootstrap()

    # Cross-provider boot cleanup of stale ProcessRun rows from a previous
    # TwiCC instance. Runs after the DB writer is up but before any
    # provider orchestrator starts, so no live agent's freshly-created
    # row can be misclassified as stale and rewritten to DEAD. The
    # cleanup is idempotent (a re-run after a clean shutdown is a no-op).
    from twicc.agent.process_run_cleanup import cleanup_stale_process_runs
    try:
        await cleanup_stale_process_runs()
    except Exception as exc:
        logger.error(
            "Boot ProcessRun cleanup failed: %s", exc, exc_info=True,
        )

    # Cross-provider boot sweep of ``Project.stale``: re-stat every known
    # project directory. Runs after the DB writer is up but before any provider
    # orchestrator starts, so the flag is already correct when the first project
    # syncs, and no session's cwd write races it. Provider-agnostic on purpose —
    # it used to live inside claude_code's sync_all, which left a Codex-only
    # TwiCC with a flag that never refreshed.
    from twicc.projects import refresh_all_project_directory_states
    try:
        refreshed = await refresh_all_project_directory_states()
        if refreshed:
            logger.info("Boot directory sweep: %d project(s) changed stale state", refreshed)
    except Exception as exc:
        logger.error(
            "Boot project directory sweep failed: %s", exc, exc_info=True,
        )

    # Cross-provider search lifecycle event: set once ``init_search_index()``
    # has returned, so provider watchers know they can write into the
    # index. Created here, owned by ``_orchestrate_global_search``,
    # awaited by every ``BaseOrchestrator.start`` that owns a watcher.
    search_index_ready = asyncio.Event()

    # Per-provider orchestrators (started in parallel; each one is
    # responsible for its own task graph and dependency ordering).
    orchestrators = get_orchestrator_registry()

    await orchestrators.start_all(shutdown_event, search_index_ready)

    # Configure uvicorn
    # log_config=None prevents Uvicorn from installing its own StreamHandlers;
    # uvicorn loggers are handled by Django's LOGGING config instead.
    # The server is created up front so the search-lifecycle coordinator can
    # request a graceful shutdown via ``request_shutdown`` if it fails fatally
    # (e.g. another process holds the Tantivy writer lock).
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)

    def request_shutdown() -> None:
        """Trigger a graceful shutdown of every component.

        Used both by the OS signal handler and by background coroutines
        that encounter a non-recoverable startup error.
        """
        # Cooperative stop for any provider's blocking sync threads
        # (async tasks listen for ``shutdown_event`` directly).
        orchestrators.request_thread_stop_all()
        shutdown_event.set()
        server.should_exit = True

    # Cross-provider search-lifecycle coordinator. Runs in parallel to
    # the server so ``init_search_index`` doesn't gate uvicorn startup.
    # The background search-indexing task it spawns (and any hot-toggle
    # re-trigger) is tracked via ``get_active_indexing_tasks`` so we
    # can stop every live run cleanly below.
    search_orchestrator_task = asyncio.create_task(
        _orchestrate_global_search(orchestrators, shutdown_event, search_index_ready, request_shutdown)
    )

    # Cross-provider periodic tasks
    price_sync_task = asyncio.create_task(start_price_sync_task(shutdown_event))
    benchmark_sync_task = asyncio.create_task(start_benchmark_sync_task(shutdown_event))
    quota_wakeup_task = asyncio.create_task(start_quota_wakeup_task(shutdown_event))
    session_dirs_cleanup_task = asyncio.create_task(start_session_dirs_cleanup_task(shutdown_event))
    peer_purge_task = asyncio.create_task(start_peer_purge_task(shutdown_event))
    tmux_cleanup_task = asyncio.create_task(start_tmux_cleanup_task(shutdown_event))
    last_used_flush_task = asyncio.create_task(start_last_used_flush_task(shutdown_event))
    share_view_flush_task = asyncio.create_task(start_share_view_flush_task(shutdown_event))
    denial_flush_task = asyncio.create_task(start_denial_flush_task(shutdown_event))
    telemetry_task = asyncio.create_task(start_telemetry_task(shutdown_event))
    version_check_task = asyncio.create_task(start_version_check_task())

    # One-shot trust backfill: settle every not-yet-imported project's trust
    # from the provider configs (seed + projection + broadcast). Runs in the
    # server loop (NOT blocking boot) because each settled project may spawn
    # a short-lived Codex app-server for the config projection.
    from twicc.core.services.trust import backfill_unimported_trust
    trust_backfill_task = asyncio.create_task(backfill_unimported_trust())
    # One-shot project-icon discovery sweep (the "initial sync" of icons): every
    # project's anchor + repo favicon/logo, applied silently and broadcast live.
    # Runs in the server loop (not blocking boot) — icons appearing a moment
    # after startup is fine. Cheap after the first run (manifests short-circuit).
    from twicc.project_icons import discover_all_project_icons
    icon_discovery_task = asyncio.create_task(discover_all_project_icons())
    # Dev-only: re-scan the tips dir every 10 s and broadcast on change.
    # The task short-circuits to a no-op outside TWICC_DEBUG so this is a
    # zero-cost coroutine in production.
    tips_watcher_task = asyncio.create_task(start_tips_watcher_task(shutdown_event))
    help_watcher_task = asyncio.create_task(start_help_watcher_task(shutdown_event))

    # CLI drop-request plumbing (cf. docs/superpowers/specs/2026-05-17-cli-session-create-design.md)
    from twicc.heartbeat import heartbeat_loop
    from twicc.drop_requests_watcher import get_drop_requests_watcher

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    drop_watcher_task = asyncio.create_task(get_drop_requests_watcher().start())

    # TwiCC's own MCP server (/mcp): keeps the streamable-HTTP session manager
    # alive until shutdown. Disabled by TWICC_NO_MCP (the task returns early).
    from twicc.mcp.endpoint import start_mcp_task
    mcp_task = asyncio.create_task(start_mcp_task(shutdown_event))

    # Per-session artifacts presence tracking (powers the session's Artifacts
    # tab). Filesystem-only, so it starts immediately like the drop watcher —
    # no dependency on the initial JSONL sync.
    from twicc.artifacts_watcher import get_artifacts_watcher
    artifacts_watcher_task = asyncio.create_task(get_artifacts_watcher().start())

    # Hybrid CLI sessions: adopt tmux survivors FIRST (their claude outlives
    # TwiCC restarts), then start the hook-events watcher — its boot scan
    # must find the adopted agents so a leftover PermissionRequest of a
    # still-pending prompt reaches them (events for long-gone sessions are
    # dropped harmlessly).
    from django.conf import settings
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.enums import Provider
    from twicc.providers.claude_code.agent.hybrid.hooks_watcher import (
        get_hybrid_hooks_watcher,
    )

    # Hybrid CLI mode is gated behind TWICC_CLAUDE_HYBRID_ENABLED (default OFF).
    # While off, neither the boot adoption nor the hooks watcher run, and the
    # backend refuses to create or resume any hybrid session (see the guards in
    # the agent factory and the session-creation service).
    hybrid_hooks_watcher_task = None
    if settings.CLAUDE_HYBRID_ENABLED:
        try:
            await get_agent_manager_registry().get(Provider.CLAUDE_CODE).adopt_running_hybrid_sessions()
        except Exception:
            logger.exception("Hybrid boot adoption failed")
        hybrid_hooks_watcher_task = asyncio.create_task(get_hybrid_hooks_watcher().start())

    def handle_signal(signum, frame):
        logger.info("Received signal %s, initiating shutdown...", signum)
        request_shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        await server.serve()
    finally:
        logger.info("Server shutdown initiated...")

        # Stop cross-provider tasks first. The price sync loop watches
        # ``shutdown_event`` directly (set above by the signal handler),
        # so we just wait for it to finish.
        logger.info("Stopping price sync task...")
        await _cancel_task(price_sync_task, "Price sync task")

        logger.info("Stopping model benchmark sync task...")
        await _cancel_task(benchmark_sync_task, "Model benchmark sync task")

        logger.info("Stopping quota warm-up task...")
        await _cancel_task(quota_wakeup_task, "Quota warm-up task")

        # Watches ``shutdown_event`` directly too; cancel covers the disabled
        # no-op path (coroutine already returned) and the mid-sleep case.
        logger.info("Stopping session dirs cleanup task...")
        await _cancel_task(session_dirs_cleanup_task, "Session dirs cleanup task")
        await _cancel_task(peer_purge_task, "Peer attachment purge task")

        logger.info("Stopping tmux reaper task...")
        await _cancel_task(tmux_cleanup_task, "tmux reaper task")

        logger.info("Stopping token last-used flush task...")
        await _cancel_task(last_used_flush_task, "Token last-used flush task")

        logger.info("Stopping share view flush task...")
        await _cancel_task(share_view_flush_task, "Share view flush task")

        logger.info("Stopping artifact denial flush task...")
        await _cancel_task(denial_flush_task, "Artifact denial flush task")

        logger.info("Stopping telemetry task...")
        await _cancel_task(telemetry_task, "Telemetry task")

        logger.info("Stopping version check task...")
        stop_version_check_task()
        await _cancel_task(version_check_task, "Version check task")

        # One-shot; usually already finished — cancel covers an early shutdown.
        await _cancel_task(trust_backfill_task, "Trust backfill task")
        await _cancel_task(icon_discovery_task, "Project icon discovery task")

        # Tips watcher exits cleanly when shutdown_event fires (set above),
        # but we still cancel it explicitly to cover the no-op TWICC_DEBUG=
        # off path (coroutine already returned) and any awaited wait_for.
        logger.info("Stopping tips watcher task...")
        await _cancel_task(tips_watcher_task, "Tips watcher task")

        logger.info("Stopping help watcher task...")
        await _cancel_task(help_watcher_task, "Help watcher task")

        logger.info("Stopping heartbeat task...")
        await _cancel_task(heartbeat_task, "Heartbeat task")

        logger.info("Stopping drop-requests watcher task...")
        await _cancel_task(drop_watcher_task, "Drop-requests watcher task")

        logger.info("Stopping MCP server task...")
        await _cancel_task(mcp_task, "MCP server task")

        logger.info("Stopping artifacts watcher task...")
        await _cancel_task(artifacts_watcher_task, "Artifacts watcher task")

        logger.info("Stopping hybrid-hooks watcher task...")
        await _cancel_task(hybrid_hooks_watcher_task, "Hybrid-hooks watcher task")

        # Stop the global search-indexing task(s) (if any ever started)
        # and the coordinator that gated them. Order matters: cancel the
        # coordinator first so it doesn't spawn a new search task after
        # we've already stopped the running ones. The active list covers
        # both the boot pass and any hot-toggle re-trigger queued behind
        # it on the run lock.
        await _cancel_task(search_orchestrator_task, "Search lifecycle coordinator")
        active_indexing_tasks = get_active_indexing_tasks()
        if active_indexing_tasks:
            logger.info(
                "Stopping search index task(s) (%d active)...",
                len(active_indexing_tasks),
            )
            stop_search_index_task()
            for idx, task in enumerate(active_indexing_tasks, start=1):
                await _cancel_task(task, f"Search index task #{idx}")
        else:
            logger.info("Search index task was not started, skipping")

        # Then let every provider tear down its own tasks (in parallel).
        await orchestrators.shutdown_all()

        # Stop the DB writer. Done after every orchestrator has shut
        # down — their blocking shutdown() guarantees no producer thread or
        # subprocess is still alive, so nothing is left pushing onto the
        # shared queues.
        await stop_db_writer()

        # Finally tear down the search index itself. Done after the
        # providers' watchers are stopped so no late write races us.
        logger.info("Shutting down search index...")
        await asyncio.to_thread(shutdown_search_index)

        logger.info("Server shutdown complete")


def main():
    # Acquire the per-data-dir instance lock before doing anything that
    # touches state shared by every TwiCC process (DB migrations, Tantivy
    # writer, ports). The lock is a POSIX flock on <data_dir>/twicc.lock,
    # released automatically by the kernel on any kind of process death
    # (including SIGKILL or crash) — no stale-lock cleanup needed.
    instance_lock = InstanceLock(get_data_dir())
    try:
        instance_lock.acquire()
    except InstanceAlreadyRunning as exc:
        logger.error("%s", exc)
        sys.exit(1)

    try:
        logger.info("TWICC starting...")
        logger.info("Environment loaded")
        # Provider homes: what the .env dropped (already printed to stderr by
        # ``cli.main()``; repeated here so it lands in backend.log), then one
        # line per resolved location. Validation already passed in ``cli.main()``.
        from twicc.provider_homes import describe_provider_homes, ensure_codex_home

        for warning in get_env_load_warnings():
            logger.warning("%s", warning)
        for line in describe_provider_homes():
            logger.info("%s", line)
        # Codex refuses to start on a missing CODEX_HOME; create a configured one.
        ensure_codex_home()

        from django.conf import settings
        logger.info("TwiCC launch prefix: %s", settings.TWICC_LAUNCH_PREFIX)

        # Migrations auto
        call_command("migrate", verbosity=0)
        logger.info("Migrations applied")

        # Backfill Project.worktree_of from strong filesystem signals. Must run
        # AFTER migrate (the column ships in a migration) and is idempotent —
        # only projects without a link are considered. Each newly-created link is
        # logged by the helper (via logger.info); see twicc.worktree_backfill.
        from twicc.worktree_backfill import backfill_worktree_links
        _, _wt_linked, _wt_unresolved = asyncio.run(backfill_worktree_links())
        if _wt_linked or _wt_unresolved:
            logger.info(
                "Worktree backfill: %d link(s) created, %d look like a worktree but unlinked",
                _wt_linked, len(_wt_unresolved),
            )

        # Each provider tracks CLI authentication state — established on client
        # connect, flipped to "not authenticated" by a real auth error, and
        # re-confirmed via the UI "Check again" button — and broadcasts it to
        # connected clients. Sending messages is disabled in the UI when the
        # owning provider is not authenticated.

        # Parse port
        port = os.environ.get("TWICC_PORT", "3500")
        try:
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                raise ValueError()
        except ValueError:
            logger.error("Invalid port '%s'. Must be a number between 1 and 65535.", port)
            sys.exit(1)

        logger.info("Server starting on http://localhost:%d", port_int)

        # Now that the port is known, write the sidecar info file so a second
        # ``twicc`` invocation can show a helpful "Holder: PID X, port Y" line.
        instance_lock.write_info(port=port_int)

        # Remove the startup console handler -- from now on, only the file handler remains
        logging.getLogger("twicc").removeHandler(_startup_console)

        # Run async server (initial sync runs as an async task inside run_server)
        asyncio.run(run_server(port_int))
    finally:
        instance_lock.release()
