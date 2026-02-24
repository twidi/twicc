"""
Background tasks for usage quota synchronization and broadcasting.

Provides periodic fetching of Claude usage quotas from the Anthropic API,
and WebSocket broadcasting of usage updates to connected clients.
"""

from __future__ import annotations

import asyncio
import logging

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.models import UsageSnapshot
from twicc.core.serializers import serialize_usage_snapshot
from twicc.core.usage import compute_period_costs, fetch_and_save_usage, has_oauth_credentials

logger = logging.getLogger(__name__)

# Stop event for usage sync task
_usage_sync_stop_event: asyncio.Event | None = None

# Interval for usage sync: 5 minutes in seconds
USAGE_SYNC_INTERVAL = 5 * 60


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_usage_sync_task() -> None:
    """Signal the usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


@sync_to_async
def _get_latest_usage_snapshot() -> UsageSnapshot | None:
    """Get the most recent usage snapshot from the database."""
    return UsageSnapshot.objects.first()  # ordered by -fetched_at


def _build_usage_message(
    success: bool, reason: str, has_oauth: bool, snapshot: UsageSnapshot | None
) -> dict:
    """
    Build a usage_updated message payload.

    If has_oauth is False, usage data is omitted even if a snapshot exists
    in the database (the user may have switched from OAuth to API mode).

    Includes period cost data (spent, estimated_period, estimated_monthly)
    for the 5-hour and 7-day windows when a snapshot is available.
    """
    if has_oauth and snapshot:
        period_costs = compute_period_costs(snapshot)
        usage = serialize_usage_snapshot(snapshot, period_costs=period_costs)
    else:
        usage = None

    return {
        "type": "usage_updated",
        "success": success,
        "reason": reason,  # "sync" = after API fetch, "connection" = on WS connect
        "has_oauth": has_oauth,
        "usage": usage,
    }


@sync_to_async
def _build_usage_message_sync(
    success: bool, reason: str, has_oauth: bool, snapshot: UsageSnapshot | None
) -> dict:
    """
    sync_to_async wrapper for _build_usage_message.

    Required because compute_period_costs() performs database queries
    that cannot run in an async context.
    """
    return _build_usage_message(success, reason, has_oauth, snapshot)


async def broadcast_usage_updated(success: bool) -> None:
    """
    Broadcast usage_updated message via WebSocket to all connected clients.

    Always sends the latest snapshot from the database (not necessarily
    the one just fetched), plus a success flag indicating whether the
    last fetch succeeded. If OAuth is not configured, sends has_oauth=False
    with no usage data.
    """
    oauth = await asyncio.to_thread(has_oauth_credentials)
    snapshot = await _get_latest_usage_snapshot() if oauth else None
    data = await _build_usage_message_sync(success, reason="sync", has_oauth=oauth, snapshot=snapshot)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": data,
        },
    )


async def get_usage_message_for_connection() -> dict:
    """
    Build a usage_updated message to send to a single client on WS connect.

    Returns the latest snapshot from the database with reason="connection".
    If OAuth is not configured, returns has_oauth=False with no usage data.
    """
    oauth = await asyncio.to_thread(has_oauth_credentials)
    snapshot = await _get_latest_usage_snapshot() if oauth else None
    return await _build_usage_message_sync(success=True, reason="connection", has_oauth=oauth, snapshot=snapshot)


async def start_usage_sync_task() -> None:
    """
    Background task that periodically fetches and stores Claude usage quotas.

    Runs until stop event is set:
    - Executes fetch_and_save_usage() immediately on startup
    - Then waits USAGE_SYNC_INTERVAL before the next fetch
    - Handles graceful shutdown via stop event

    The fetch operation runs in a thread to avoid blocking the event loop,
    as it involves an HTTP request to the Anthropic API.
    """
    stop_event = get_usage_sync_stop_event()

    logger.info("Usage sync task started")

    while not stop_event.is_set():
        success = False
        try:
            snapshot = await asyncio.to_thread(fetch_and_save_usage)
            if snapshot:
                success = True
                logger.info(
                    "Usage sync completed: 5h=%.1f%% (time: %.1f%%), 7d=%.1f%% (time: %.1f%%)",
                    snapshot.five_hour_utilization or 0,
                    snapshot.five_hour_temporal_pct or 0,
                    snapshot.seven_day_utilization or 0,
                    snapshot.seven_day_temporal_pct or 0,
                )
            else:
                logger.warning("Usage sync: no data (credentials missing or API error)")
        except Exception as e:
            logger.error("Usage sync failed: %s", e, exc_info=True)

        # Broadcast to frontend (always sends latest snapshot from DB + success flag)
        try:
            await broadcast_usage_updated(success)
        except Exception as e:
            logger.error("Usage broadcast failed: %s", e, exc_info=True)

        # Wait for the next sync interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=USAGE_SYNC_INTERVAL)
        except asyncio.TimeoutError:
            # Timeout means it's time to sync again
            pass

    logger.info("Usage sync task stopped")
