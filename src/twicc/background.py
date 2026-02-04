"""
Background tasks for the TWICC application.

This module provides background tasks that run continuously:
- Compute task: Processes sessions that need metadata computation
- Price sync task: Synchronizes model prices from OpenRouter API

Uses ThreadPoolExecutor to offload work without blocking the event loop.

Note: We use ThreadPoolExecutor instead of ProcessPoolExecutor because:
- ProcessPoolExecutor spawns a separate process with its own SQLite connection
- That connection doesn't inherit Django's timeout/WAL settings properly
- This causes "database is locked" errors that don't respect busy_timeout
- ThreadPoolExecutor stays in the same process, sharing the DB connection config
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from twicc.compute import compute_session_metadata, load_project_directories
from twicc.core.models import Project, Session
from twicc.core.pricing import sync_model_prices
from twicc.core.serializers import serialize_project, serialize_session

logger = logging.getLogger(__name__)

# Single worker thread for computation
_executor: ThreadPoolExecutor | None = None

# Stop event for graceful shutdown (compute task)
_stop_event: asyncio.Event | None = None

# Stop event for price sync task
_price_sync_stop_event: asyncio.Event | None = None

# Progress tracking for initial computation
_initial_total: int | None = None
_processed_count: int = 0
_last_logged_percent: int = 0
_initial_phase_complete: bool = False


def get_executor() -> ThreadPoolExecutor:
    """Get or create the ThreadPoolExecutor."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compute")
    return _executor


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event for the compute task."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def get_price_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the price sync task."""
    global _price_sync_stop_event
    if _price_sync_stop_event is None:
        _price_sync_stop_event = asyncio.Event()
    return _price_sync_stop_event


def stop_background_task() -> None:
    """Signal the background compute task to stop and shutdown executor."""
    global _executor, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def stop_price_sync_task() -> None:
    """Signal the price sync task to stop."""
    global _price_sync_stop_event
    if _price_sync_stop_event is not None:
        _price_sync_stop_event.set()


async def run_initial_price_sync() -> None:
    """
    Run the initial price sync before starting other background tasks.

    This ensures fresh prices are available before the background compute task
    starts processing sessions. Must be awaited at startup.

    If sync fails, logs a warning but continues - existing prices in DB or
    DEFAULT_FAMILY_PRICES will be used as fallback.
    """
    logger.info("Running initial price sync...")
    try:
        stats = await asyncio.to_thread(sync_model_prices)
        logger.info(
            f"Initial price sync completed: {stats['created']} created, {stats['unchanged']} unchanged"
        )
    except Exception as e:
        logger.warning(
            f"Initial price sync failed: {e}. Will use existing DB prices or default family prices."
        )


def _reset_progress_tracking() -> None:
    """Reset progress tracking state for a new run."""
    global _initial_total, _processed_count, _last_logged_percent, _initial_phase_complete
    _initial_total = None
    _processed_count = 0
    _last_logged_percent = 0
    _initial_phase_complete = False


@sync_to_async
def _count_sessions_to_compute() -> int:
    """Count the number of sessions needing computation."""
    return Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).count()


@sync_to_async
def get_next_session_to_compute() -> Session | None:
    """
    Find the next session needing metadata computation.

    Returns sessions where:
    - compute_version IS NULL (never computed)
    - compute_version != CURRENT_COMPUTE_VERSION (outdated)

    Ordered by mtime descending (most recent first).
    """
    return Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).order_by('-mtime').first()


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


@sync_to_async
def get_project(project_id: str) -> Project | None:
    """Get a project by ID."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


async def broadcast_session_updated(session: Session) -> None:
    """Broadcast session_updated message via WebSocket."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )


async def broadcast_project_updated(project: Project) -> None:
    """Broadcast project_updated message via WebSocket."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "project_updated",
                "project": serialize_project(project),
            },
        },
    )


async def start_background_compute_task() -> None:
    """
    Background task that continuously processes sessions needing computation.

    Runs until stop event is set. Uses ProcessPoolExecutor for CPU-intensive work.

    Progress logging: During initial computation of existing sessions, logs
    progress at 10% intervals. Once all initial sessions are processed (100%),
    stops logging even if new sessions arrive later.
    """
    global _initial_total, _processed_count, _last_logged_percent, _initial_phase_complete

    executor = get_executor()
    stop_event = get_stop_event()
    loop = asyncio.get_event_loop()

    # Reset and initialize progress tracking
    _reset_progress_tracking()
    _initial_total = await _count_sessions_to_compute()

    # Load project directories cache at startup
    await sync_to_async(load_project_directories)()

    logger.info(f"Background compute task started ({_initial_total} sessions to process)")

    while not stop_event.is_set():
        try:
            # Find next session needing computation
            session = await get_next_session_to_compute()

            if session is None:
                # Nothing to compute, wait before checking again
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue

            # Run CPU-intensive work in separate process
            # Pass session.id (string), not the object (can't pickle Django models)
            try:
                await loop.run_in_executor(
                    executor,
                    compute_session_metadata,
                    session.id
                )
            except Exception as e:
                logger.error(f"Error in compute_session_metadata for {session.id}: {e}")
                raise

            # Back in main process: reload session and broadcast update
            try:
                session = await refresh_session(session)
            except Exception as e:
                logger.error(f"Error in refresh_session for {session.id}: {e}")
                raise

            # Broadcast for both regular sessions and subagents
            try:
                await broadcast_session_updated(session)
            except Exception as e:
                logger.error(f"Error in broadcast_session_updated for {session.id}: {e}")
                raise

            # Broadcast project update (total_cost has changed)
            try:
                project = await get_project(session.project_id)
                if project:
                    await broadcast_project_updated(project)
            except Exception as e:
                logger.error(f"Error in broadcast_project_updated for project {session.project_id}: {e}")
                raise

            # Progress logging during initial phase only
            if not _initial_phase_complete and _initial_total and _initial_total > 0:
                _processed_count += 1
                current_percent = (_processed_count * 100) // _initial_total

                # Log at 10% intervals
                if current_percent >= _last_logged_percent + 10:
                    _last_logged_percent = (current_percent // 10) * 10
                    logger.info(f"Background compute progress: {_last_logged_percent}% ({_processed_count}/{_initial_total})")

                # Mark initial phase complete at 100%
                if _processed_count >= _initial_total:
                    _initial_phase_complete = True
                    logger.info(f"Background compute: initial processing complete ({_initial_total} sessions)")

        except Exception as e:
            logger.error(f"Error in background compute task: {e}", exc_info=True)
            # Wait before retrying to avoid tight error loop
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    logger.info("Background compute task stopped")


# Interval for price sync: 24 hours in seconds
PRICE_SYNC_INTERVAL = 24 * 60 * 60


async def start_price_sync_task() -> None:
    """
    Background task that periodically synchronizes model prices from OpenRouter.

    Runs until stop event is set:
    - Executes sync_model_prices() immediately on startup
    - Then waits 24 hours before the next sync
    - Handles graceful shutdown via stop event

    The sync operation runs in a thread to avoid blocking the event loop,
    as it involves HTTP requests to the OpenRouter API.
    """
    stop_event = get_price_sync_stop_event()

    logger.info("Price sync task started")

    while not stop_event.is_set():
        try:
            # Run price sync in a thread to avoid blocking the event loop
            stats = await asyncio.to_thread(sync_model_prices)
            logger.info(
                f"Price sync completed: {stats['created']} created, {stats['unchanged']} unchanged"
            )
        except Exception as e:
            logger.error(f"Price sync failed: {e}", exc_info=True)

        # Wait for the next sync interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PRICE_SYNC_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to sync again
            pass

    logger.info("Price sync task stopped")
