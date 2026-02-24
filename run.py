#!/usr/bin/env -S uv run
"""
Entry point for the TWICC application.

Handles Django setup, migrations, initial sync, and starts the server
with file watcher running concurrently.
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src/ directory to Python path
src_dir = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(src_dir))

# Configure Django before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")

import django
django.setup()

# Now we can import Django-dependent modules
from django.core.management import call_command

from twicc.core.models import Project, Session, SessionType
from twicc.initial_sync import sync_all
from twicc.sessions_watcher import start_watcher, stop_watcher
from twicc.agent import shutdown_process_manager
from twicc.background_task import ComputeContext, start_background_compute_task, stop_background_task
from twicc.pricing_task import run_initial_price_sync, start_price_sync_task, stop_price_sync_task
from twicc.usage_task import start_usage_sync_task, stop_usage_sync_task


async def run_server(port: int):
    """Run the ASGI server with file watcher and background tasks."""
    import signal
    import uvicorn
    from twicc.asgi import application

    # Run initial price sync before starting background tasks
    # This ensures prices are available for cost calculation
    await run_initial_price_sync()

    # Start watcher task
    watcher_task = asyncio.create_task(start_watcher())

    # Start background compute task (prices are now available)
    compute_ctx = ComputeContext()
    compute_task = asyncio.create_task(start_background_compute_task(compute_ctx))

    # Start price sync task (periodic sync every 24h)
    price_sync_task = asyncio.create_task(start_price_sync_task())

    # Start usage sync task (periodic fetch every 5 minutes)
    usage_sync_task = asyncio.create_task(start_usage_sync_task())

    # Configure uvicorn
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # Set up signal handlers to ensure clean shutdown
    shutdown_event = asyncio.Event()

    def handle_signal(signum, frame):
        print(f"\n→ Received signal {signum}, initiating shutdown...")
        shutdown_event.set()
        server.should_exit = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        await server.serve()
    finally:
        print("→ Server shutdown initiated...")

        # Clean shutdown of watcher
        print("  Stopping watcher...")
        stop_watcher()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        print("  ✓ Watcher stopped")

        # Clean shutdown of background compute task
        print("  Stopping background compute task...")
        stop_background_task(compute_ctx)
        compute_task.cancel()
        try:
            await compute_task
        except asyncio.CancelledError:
            pass
        print("  ✓ Background compute task stopped")

        # Clean shutdown of price sync task
        print("  Stopping price sync task...")
        stop_price_sync_task()
        price_sync_task.cancel()
        try:
            await price_sync_task
        except asyncio.CancelledError:
            pass
        print("  ✓ Price sync task stopped")

        # Clean shutdown of usage sync task
        print("  Stopping usage sync task...")
        stop_usage_sync_task()
        usage_sync_task.cancel()
        try:
            await usage_sync_task
        except asyncio.CancelledError:
            pass
        print("  ✓ Usage sync task stopped")

        # Clean shutdown of Claude processes (also stops the internal timeout monitor)
        # This gracefully terminates any active Claude SDK processes
        print("  Stopping process manager...")
        await shutdown_process_manager()
        print("  ✓ Process manager stopped")

        print("→ Server shutdown complete")


def main():
    print("TWICC Starting...")
    print("✓ Environment loaded")

    # Migrations auto
    call_command("migrate", verbosity=0)
    print("✓ Migrations applied")

    # Sync initial (can take several minutes on first run or with a fresh database)
    logger.info("Starting data synchronization (may take a while on first run)...")
    sync_all()
    projects_count = Project.objects.filter(stale=False).count()
    sessions_count = Session.objects.filter(stale=False, type=SessionType.SESSION).count()
    subagents_count = Session.objects.filter(stale=False, type=SessionType.SUBAGENT).count()
    print(f"✓ Data synchronized ({projects_count} projects, {sessions_count} sessions, {subagents_count} subagents)")

    # Parse port
    port = os.environ.get("TWICC_PORT", "3500")
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError()
    except ValueError:
        print(f"Error: Invalid port '{port}'. Must be a number between 1 and 65535.")
        sys.exit(1)

    print(f"→ Server starting on http://0.0.0.0:{port_int}")

    # Run async server
    asyncio.run(run_server(port_int))


if __name__ == "__main__":
    main()
