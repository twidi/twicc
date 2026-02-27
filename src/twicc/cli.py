"""
CLI entry point for the TWICC application.

Handles Django setup, migrations, and starts the server with all background
tasks running concurrently. The server is available immediately; initial sync
and background compute run as async tasks that broadcast progress via WebSocket.

Used by:
- ``uvx twicc`` / ``pip install twicc && twicc``  (via project.scripts)
- ``python -m twicc``  (via __main__.py)
- ``uv run run.py``  (dev wrapper at repo root)
"""

import asyncio
import logging
import os
import sys
import threading

from dotenv import load_dotenv

from twicc.paths import get_env_path

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env)
load_dotenv(get_env_path())

# Configure Django before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

import django  # noqa: E402

django.setup()

# Logger must be created AFTER django.setup() so LOGGING config is applied
logger = logging.getLogger("twicc.run")

# Add a temporary console handler for startup messages (just the text, no timestamp/level).
# It will be removed once the server is about to start, so only the file handler remains.
_startup_console = logging.StreamHandler()
_startup_console.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger("twicc").addHandler(_startup_console)

# Now we can import Django-dependent modules
from django.core.management import call_command  # noqa: E402

from twicc.agent import shutdown_process_manager  # noqa: E402
from twicc.background_task import ComputeContext, start_background_compute_task, stop_background_task  # noqa: E402
from twicc.core.models import Project, Session, SessionType  # noqa: E402
from twicc.initial_sync import scan_projects, scan_sessions, sync_all  # noqa: E402
from twicc.pricing_task import run_initial_price_sync, start_price_sync_task, stop_price_sync_task  # noqa: E402
from twicc.sessions_watcher import start_watcher, stop_watcher  # noqa: E402
from twicc.startup_progress import broadcast_startup_progress  # noqa: E402
from twicc.usage_task import start_usage_sync_task, stop_usage_sync_task  # noqa: E402


def _count_total_sessions() -> int:
    """Quick filesystem-only count of session files across all projects.

    This scans the projects directory without reading any file contents or
    touching the database. Used to provide a total for progress reporting
    before sync_all() starts.
    """
    total = 0
    for project_id in scan_projects():
        total += len(scan_sessions(project_id))
    return total


async def _cancel_task(task: asyncio.Task, name: str) -> None:
    """Cancel an asyncio task and wait for it to finish."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("%s stopped", name)


async def run_server(port: int):
    """Run the ASGI server with all background tasks.

    The server starts immediately. Initial sync and background compute run
    as concurrent async tasks that broadcast progress via WebSocket.

    Task dependency graph:
    - initial_sync, initial_price_sync, price_sync, usage_sync: start immediately
    - watcher: starts after initial_sync completes
    - background_compute: starts after both initial_sync AND initial_price_sync complete
    """
    import signal

    import uvicorn
    from asgiref.sync import sync_to_async

    from twicc.asgi import application

    # --- Dependency signaling events ---
    sync_done = asyncio.Event()
    price_done = asyncio.Event()

    # Threading event to cooperatively stop the initial sync thread on shutdown
    sync_stop_event = threading.Event()

    # --- Mutable container for tasks created by the orchestrator ---
    # These may still be None during shutdown if orchestrator hasn't started them yet.
    deferred = {
        "watcher_task": None,
        "compute_task": None,
        "compute_ctx": None,
        "price_sync_task": None,
    }

    # --- Initial sync task ---
    async def initial_sync_task():
        """Run sync_all() in a thread with progress broadcasting."""
        loop = asyncio.get_running_loop()

        # Quick filesystem scan to get total session count for progress bar
        total_sessions = await asyncio.to_thread(_count_total_sessions)

        # Broadcast initial state (0/N)
        await broadcast_startup_progress("initial_sync", 0, total_sessions)

        # Progress tracking — the callback is called from the sync thread,
        # so we use run_coroutine_threadsafe to post to the event loop.
        progress = {"current": 0}

        def on_session_progress(session_id: str, idx: int, total: int):
            # idx/total are per-project; we track global progress ourselves
            progress["current"] += 1
            asyncio.run_coroutine_threadsafe(
                broadcast_startup_progress("initial_sync", progress["current"], total_sessions),
                loop,
            )

        logger.info("Starting data synchronization...")
        await asyncio.to_thread(sync_all, on_session_progress=on_session_progress, stop_event=sync_stop_event)

        # Broadcast completion
        await broadcast_startup_progress("initial_sync", total_sessions, total_sessions, completed=True)

        # Log summary
        projects_count = await sync_to_async(Project.objects.filter(stale=False).count)()
        sessions_count = await sync_to_async(
            Session.objects.filter(stale=False, type=SessionType.SESSION).count
        )()
        subagents_count = await sync_to_async(
            Session.objects.filter(stale=False, type=SessionType.SUBAGENT).count
        )()
        logger.info(
            "Data synchronized (%d projects, %d sessions, %d subagents)",
            projects_count,
            sessions_count,
            subagents_count,
        )

        sync_done.set()

    # --- Initial price sync task ---
    async def initial_price_sync_task():
        """Run initial price sync then signal completion."""
        await run_initial_price_sync()
        price_done.set()

    # --- Orchestrator: starts dependent tasks after their prerequisites ---
    async def orchestrator_task():
        """Wait for dependencies and start watcher + background compute."""
        # Start watcher once initial sync is done
        await sync_done.wait()
        deferred["watcher_task"] = asyncio.create_task(start_watcher())
        logger.info("Watcher started (after initial sync)")

        # Start background compute and periodic price sync once initial price sync is done.
        # The periodic price sync task must wait for the initial price sync to avoid
        # a race condition: both call sync_model_prices() which uses non-atomic
        # read-then-create, causing UNIQUE constraint violations on concurrent inserts.
        await price_done.wait()
        deferred["price_sync_task"] = asyncio.create_task(start_price_sync_task())
        deferred["compute_ctx"] = ComputeContext()
        deferred["compute_task"] = asyncio.create_task(
            start_background_compute_task(deferred["compute_ctx"])
        )
        logger.info("Background compute started (after sync + price sync)")

    # --- Launch all tasks ---
    sync_task = asyncio.create_task(initial_sync_task())
    price_init_task = asyncio.create_task(initial_price_sync_task())
    orch_task = asyncio.create_task(orchestrator_task())
    usage_sync_task = asyncio.create_task(start_usage_sync_task())

    # Configure uvicorn
    # log_config=None prevents Uvicorn from installing its own StreamHandlers;
    # uvicorn loggers are handled by Django's LOGGING config instead.
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %s, initiating shutdown...", signum)
        sync_stop_event.set()
        shutdown_event.set()
        server.should_exit = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        await server.serve()
    finally:
        logger.info("Server shutdown initiated...")

        # Cancel startup tasks (may already be done)
        await _cancel_task(sync_task, "Initial sync task")
        await _cancel_task(price_init_task, "Initial price sync task")
        await _cancel_task(orch_task, "Orchestrator task")

        # Clean shutdown of watcher (may not have started yet)
        if deferred["watcher_task"] is not None:
            logger.info("Stopping watcher...")
            stop_watcher()
            await _cancel_task(deferred["watcher_task"], "Watcher")
        else:
            logger.info("Watcher was not started, skipping")

        # Clean shutdown of background compute task (may not have started yet)
        if deferred["compute_task"] is not None:
            logger.info("Stopping background compute task...")
            stop_background_task(deferred["compute_ctx"])
            await _cancel_task(deferred["compute_task"], "Background compute task")
        else:
            logger.info("Background compute was not started, skipping")

        # Clean shutdown of price sync task (may not have started yet)
        if deferred["price_sync_task"] is not None:
            logger.info("Stopping price sync task...")
            stop_price_sync_task()
            await _cancel_task(deferred["price_sync_task"], "Price sync task")
        else:
            logger.info("Price sync task was not started, skipping")

        # Clean shutdown of usage sync task
        logger.info("Stopping usage sync task...")
        stop_usage_sync_task()
        await _cancel_task(usage_sync_task, "Usage sync task")

        # Clean shutdown of Claude processes (also stops the internal timeout monitor)
        # This gracefully terminates any active Claude SDK processes
        logger.info("Stopping process manager...")
        await shutdown_process_manager()
        logger.info("Process manager stopped")

        logger.info("Server shutdown complete")


def main():
    logger.info("TWICC starting...")
    logger.info("Environment loaded")

    # Migrations auto
    call_command("migrate", verbosity=0)
    logger.info("Migrations applied")

    # Parse port
    port = os.environ.get("TWICC_PORT", "3500")
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError()
    except ValueError:
        logger.error("Invalid port '%s'. Must be a number between 1 and 65535.", port)
        sys.exit(1)

    logger.info("Server starting on http://0.0.0.0:%d", port_int)

    # Remove the startup console handler -- from now on, only the file handler remains
    logging.getLogger("twicc").removeHandler(_startup_console)

    # Run async server (initial sync runs as an async task inside run_server)
    asyncio.run(run_server(port_int))
