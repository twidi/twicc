"""
Background task for computing session metadata.

Runs continuously, processing sessions that need metadata computation.
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

from twicc_poc.compute import compute_session_metadata
from twicc_poc.core.models import Session
from twicc_poc.core.serializers import serialize_session

logger = logging.getLogger(__name__)

# Single worker thread for computation
_executor: ThreadPoolExecutor | None = None

# Stop event for graceful shutdown
_stop_event: asyncio.Event | None = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create the ThreadPoolExecutor."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compute")
    return _executor


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_background_task() -> None:
    """Signal the background task to stop and shutdown executor."""
    global _executor, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


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


async def start_background_compute_task() -> None:
    """
    Background task that continuously processes sessions needing computation.

    Runs until stop event is set. Uses ProcessPoolExecutor for CPU-intensive work.
    """
    executor = get_executor()
    stop_event = get_stop_event()
    loop = asyncio.get_event_loop()

    logger.info("Background compute task started")

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

            logger.info(f"Computing metadata for session {session.id}")

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

            try:
                await broadcast_session_updated(session)
            except Exception as e:
                logger.error(f"Error in broadcast_session_updated for {session.id}: {e}")
                raise

            logger.info(f"Completed metadata computation for session {session.id}")

        except Exception as e:
            logger.error(f"Error in background compute task: {e}", exc_info=True)
            # Wait before retrying to avoid tight error loop
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    logger.info("Background compute task stopped")
