"""
Background tasks for model price synchronization.

Provides periodic synchronization of Anthropic model prices from the
OpenRouter API. Runs as an async task alongside the main event loop.
"""

from __future__ import annotations

import asyncio
import logging

from twicc.core.pricing import sync_model_prices

logger = logging.getLogger(__name__)

# Stop event for price sync task
_price_sync_stop_event: asyncio.Event | None = None

# Interval for price sync: 24 hours in seconds
PRICE_SYNC_INTERVAL = 24 * 60 * 60


def get_price_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the price sync task."""
    global _price_sync_stop_event
    if _price_sync_stop_event is None:
        _price_sync_stop_event = asyncio.Event()
    return _price_sync_stop_event


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
