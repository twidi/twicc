#!/usr/bin/env python
"""
Entry point for the TWICC POC application.

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
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc_poc.settings")

import django
django.setup()

# Now we can import Django-dependent modules
from django.core.management import call_command

from twicc_poc.core.models import Project, Session
from twicc_poc.sync import sync_all
from twicc_poc.watcher import start_watcher, stop_watcher
from twicc_poc.background import start_background_compute_task, stop_background_task


async def run_server(port: int):
    """Run the ASGI server with file watcher and background compute task."""
    import uvicorn
    from twicc_poc.asgi import application

    # Start watcher task
    watcher_task = asyncio.create_task(start_watcher())

    # Start background compute task (after initial sync completed in main())
    compute_task = asyncio.create_task(start_background_compute_task())

    # Configure uvicorn
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        # Clean shutdown of watcher
        stop_watcher()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

        # Clean shutdown of background compute task
        stop_background_task()
        compute_task.cancel()
        try:
            await compute_task
        except asyncio.CancelledError:
            pass


def main():
    print("TWICC POC Starting...")
    print("✓ Environment loaded")

    # Migrations auto
    call_command("migrate", verbosity=0)
    print("✓ Migrations applied")

    # Sync initial
    sync_all()
    projects_count = Project.objects.filter(archived=False).count()
    sessions_count = Session.objects.filter(archived=False).count()
    print(f"✓ Data synchronized ({projects_count} projects, {sessions_count} sessions)")

    # Parse port
    port = os.environ.get("TWICC_PORT", "3500")
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            raise ValueError()
    except ValueError:
        print(f"Error: Invalid port '{port}'. Must be a number between 1 and 65535.")
        sys.exit(1)

    print("✓ File watcher scheduled")
    print("✓ Background compute task scheduled")
    print(f"→ Server starting on http://0.0.0.0:{port_int}")

    # Run async server
    asyncio.run(run_server(port_int))


if __name__ == "__main__":
    main()
