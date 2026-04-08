"""
Background tasks for usage quota synchronization and broadcasting.

Provides periodic fetching of Claude usage quotas from the Anthropic API,
and WebSocket broadcasting of usage updates to connected clients.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

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


def _build_reference_snapshots(snapshot: UsageSnapshot) -> dict | None:
    """
    Query and serialize reference snapshots for recent burn rate computation.

    Looks up historical snapshots at four lookback targets:
    - ~1 hour and ~30 minutes ago (for computing recent rates on the 5-hour window)
    - ~24 hours and ~12 hours ago (for computing recent rates on all 7-day windows)

    Intra-period references are constrained to the **current quota window** (after
    its start time) to avoid crossing a reset boundary.

    When the current window is younger than a lookback interval, also provides
    **cross-period** references (``cross_fh_long``, ``cross_fh_short``,
    ``cross_sd_long``, ``cross_sd_short``), each with ``prev_ref`` and ``prev_end``
    snapshots from the previous period. The frontend uses these to compute a
    meaningful recent burn rate even in the early minutes of a new period.

    Returns a dict with reference keys, or None if no references are available.
    """
    if not snapshot or not snapshot.fetched_at:
        return None

    now = snapshot.fetched_at
    refs: dict = {}

    def _fmt_dt(dt):
        return dt.isoformat() if dt else None

    def _find_ref(target_delta: timedelta, window_start: datetime | None) -> UsageSnapshot | None:
        """Find the oldest snapshot within the lookback target and current window.

        The floor is the later of (now - target_delta) and window_start,
        so the delta never exceeds the target and never crosses a reset.
        """
        floor = now - target_delta
        if window_start and window_start > floor:
            floor = window_start
        return (
            UsageSnapshot.objects
            .exclude(pk=snapshot.pk)
            .filter(fetched_at__gte=floor)
            .order_by("fetched_at")
            .first()
        )

    # 5h window start (from resets_at - 5h)
    fh_window_start = (
        snapshot.five_hour_resets_at - timedelta(hours=5)
        if snapshot.five_hour_resets_at
        else None
    )

    # 7d window start (from resets_at - 7d)
    sd_window_start = (
        snapshot.seven_day_resets_at - timedelta(days=7)
        if snapshot.seven_day_resets_at
        else None
    )

    def _serialize_fh_ref(key: str, ref: UsageSnapshot | None) -> None:
        if ref:
            refs[key] = {
                "fetched_at": _fmt_dt(ref.fetched_at),
                "five_hour_utilization": ref.five_hour_utilization,
            }

    def _serialize_sd_ref(key: str, ref: UsageSnapshot | None) -> None:
        if ref:
            refs[key] = {
                "fetched_at": _fmt_dt(ref.fetched_at),
                "seven_day_utilization": ref.seven_day_utilization,
                "seven_day_opus_utilization": ref.seven_day_opus_utilization,
                "seven_day_sonnet_utilization": ref.seven_day_sonnet_utilization,
                "seven_day_oauth_apps_utilization": ref.seven_day_oauth_apps_utilization,
                "seven_day_cowork_utilization": ref.seven_day_cowork_utilization,
            }

    # References for 5h window: 1h and 30min lookbacks
    _serialize_fh_ref("one_hour", _find_ref(timedelta(hours=1), fh_window_start))
    _serialize_fh_ref("thirty_min", _find_ref(timedelta(minutes=30), fh_window_start))

    # References for 7d windows: 24h and 12h lookbacks
    _serialize_sd_ref("one_day", _find_ref(timedelta(hours=24), sd_window_start))
    _serialize_sd_ref("twelve_hour", _find_ref(timedelta(hours=12), sd_window_start))

    # Cross-period references: when the current window is younger than the lookback,
    # we look into the previous period to compute a meaningful recent burn rate.
    # For each lookback, we provide two snapshots from the previous period:
    #   prev_ref: closest to (now - lookback), to measure old-period consumption
    #   prev_end: last snapshot before the current window started
    def _find_cross_period(window_start, lookback):
        if window_start is None:
            return None
        elapsed = (now - window_start).total_seconds()
        if elapsed >= lookback.total_seconds():
            return None  # enough intra-period data

        target = now - lookback
        # Oldest snapshot in [target, window_start)
        prev_ref = (
            UsageSnapshot.objects
            .filter(fetched_at__gte=target, fetched_at__lt=window_start)
            .order_by("fetched_at")
            .first()
        )
        if not prev_ref:
            return None

        # Most recent snapshot before window_start
        prev_end = (
            UsageSnapshot.objects
            .filter(fetched_at__lt=window_start)
            .order_by("-fetched_at")
            .first()
        )
        if not prev_end:
            return None

        return prev_ref, prev_end

    def _serialize_fh_cross(key, window_start, lookback):
        result = _find_cross_period(window_start, lookback)
        if result:
            prev_ref, prev_end = result
            refs[key] = {
                "prev_ref": {"fetched_at": _fmt_dt(prev_ref.fetched_at), "five_hour_utilization": prev_ref.five_hour_utilization},
                "prev_end": {"fetched_at": _fmt_dt(prev_end.fetched_at), "five_hour_utilization": prev_end.five_hour_utilization},
            }

    def _serialize_sd_cross(key, window_start, lookback):
        result = _find_cross_period(window_start, lookback)
        if result:
            prev_ref, prev_end = result
            def _sd_fields(snap):
                return {
                    "fetched_at": _fmt_dt(snap.fetched_at),
                    "seven_day_utilization": snap.seven_day_utilization,
                    "seven_day_opus_utilization": snap.seven_day_opus_utilization,
                    "seven_day_sonnet_utilization": snap.seven_day_sonnet_utilization,
                    "seven_day_oauth_apps_utilization": snap.seven_day_oauth_apps_utilization,
                    "seven_day_cowork_utilization": snap.seven_day_cowork_utilization,
                }
            refs[key] = {"prev_ref": _sd_fields(prev_ref), "prev_end": _sd_fields(prev_end)}

    _serialize_fh_cross("cross_fh_long", fh_window_start, timedelta(hours=1))
    _serialize_fh_cross("cross_fh_short", fh_window_start, timedelta(minutes=30))
    _serialize_sd_cross("cross_sd_long", sd_window_start, timedelta(hours=24))
    _serialize_sd_cross("cross_sd_short", sd_window_start, timedelta(hours=12))

    return refs if refs else None


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
        references = _build_reference_snapshots(snapshot)
        usage = serialize_usage_snapshot(snapshot, period_costs=period_costs, references=references)
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
