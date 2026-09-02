"""Claude Code usage sync orchestration.

Owns the lifecycle of the Claude Code usage sync loop (start/stop event,
interval, error handling). Delegates the building blocks (fetch latest,
build message, broadcast) to :mod:`twicc.usage_task`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from twicc.core.enums import Provider
from twicc.usage_task import (
    broadcast_usage_updated,
    get_usage_wake_event,
    should_run_usage_cycle,
    wait_for_usage_resume,
)
from .helpers import ClaudeCodeHelpers
from .usage import fetch_and_save_usage

logger = logging.getLogger(__name__)

# Stop event for usage sync task
_usage_sync_stop_event: asyncio.Event | None = None


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the Claude Code usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_usage_sync_task() -> None:
    """Signal the Claude Code usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


async def start_usage_sync_task() -> None:
    """Periodically fetch and store Claude Code usage quotas.

    Runs until :func:`stop_usage_sync_task` is called:
    - Executes :func:`fetch_and_save_usage` immediately on startup (baseline)
    - Then, each cycle, fetches only when :func:`should_run_usage_cycle` says
      someone may read or need the data; otherwise pauses to spare the API call
    - Waits :attr:`ClaudeCodeHelpers.USAGE_SYNC_INTERVAL` between cycles, or
      resumes early from a paused state when activity wakes the loop
    - Handles graceful shutdown via the stop event

    The fetch operation runs in a thread to avoid blocking the event
    loop, as it involves an HTTP request to the Anthropic API.
    """
    interval = ClaudeCodeHelpers.USAGE_SYNC_INTERVAL
    stop_event = get_usage_sync_stop_event()
    wake_event = get_usage_wake_event()
    # Reset for hot-restart support: clear the stop event so a relaunched
    # task isn't killed by a stale set() left by the prior shutdown.
    stop_event.clear()

    # On macOS, never auto-refresh the OAuth token from this background loop.
    # Refreshing makes the bundled ``claude`` CLI rewrite the
    # "Claude Code-credentials" Keychain item (hash-suffixed under a relocated
    # home, see provider_homes.claude_keychain_service), which resets its ACL and pops a
    # macOS authorization prompt at an unpredictable time (no active session) —
    # the exact symptom users complain about. The token is instead refreshed by
    # real agent sessions, or on demand via the sidebar "Refresh now" button
    # (``claude_code:check_usage``). Other platforms have no Keychain prompt, so
    # they keep auto-refreshing for always-fresh usage data.
    allow_refresh = sys.platform != "darwin"

    logger.info("Usage sync task started")

    first_cycle = True  # always fetch once at startup for a baseline snapshot
    paused = False      # tracked only to log active<->pause transitions once

    while not stop_event.is_set():
        if first_cycle or should_run_usage_cycle():
            first_cycle = False
            if paused:
                logger.info("Usage sync resumed (activity detected)")
                paused = False
            # Consume any wake signal now that we are acting on it.
            wake_event.clear()

            success = False
            try:
                snapshot = await fetch_and_save_usage(allow_refresh=allow_refresh)
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
                await broadcast_usage_updated(Provider.CLAUDE_CODE, success)
            except Exception as e:
                logger.error("Usage broadcast failed: %s", e, exc_info=True)

            # Active cadence: sleep the interval on the stop event only — wake
            # pings are ignored here so steady activity can't trigger a fetch
            # storm (we already fetch every interval while active).
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                # Timeout means it's time to sync again
                pass
        else:
            if not paused:
                logger.info("Usage sync paused (idle, no active agent)")
                paused = True
            # Paused: resume early when activity wakes us, else re-check the gate
            # at the next interval.
            await wait_for_usage_resume(stop_event, interval)

    logger.info("Usage sync task stopped")
