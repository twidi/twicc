"""Codex usage sync orchestration.

Owns the lifecycle of the Codex usage sync loop (start/stop event,
interval, error handling). Delegates the cross-provider building blocks
(broadcasting, latest-snapshot lookup) to :mod:`twicc.usage_task`.

Mirrors :mod:`twicc.providers.claude_code.usage_task` — same structure,
different fetcher.
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

from .credentials import credentials_path
from .helpers import CodexHelpers
from .usage import fetch_and_save_usage

logger = logging.getLogger(__name__)

_usage_sync_stop_event: asyncio.Event | None = None


def get_usage_sync_stop_event() -> asyncio.Event:
    """Get or create the stop event for the Codex usage sync task."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is None:
        _usage_sync_stop_event = asyncio.Event()
    return _usage_sync_stop_event


def stop_usage_sync_task() -> None:
    """Signal the Codex usage sync task to stop."""
    global _usage_sync_stop_event
    if _usage_sync_stop_event is not None:
        _usage_sync_stop_event.set()


async def start_usage_sync_task() -> None:
    """Periodically fetch and store Codex usage quotas.

    Runs until :func:`stop_usage_sync_task` is called:
    - Executes :func:`fetch_and_save_usage` immediately on startup (baseline)
    - Then, each cycle, fetches only when :func:`should_run_usage_cycle` says
      someone may read or need the data; otherwise pauses to spare the API call
    - Waits :attr:`CodexHelpers.USAGE_SYNC_INTERVAL` between cycles, or resumes
      early from a paused state when activity wakes the loop
    - Handles graceful shutdown via the stop event

    The fetch operation runs in a thread to avoid blocking the event
    loop, as it involves an HTTP request to ChatGPT's ``wham/usage``
    endpoint.
    """
    interval = CodexHelpers.USAGE_SYNC_INTERVAL
    stop_event = get_usage_sync_stop_event()
    wake_event = get_usage_wake_event()
    # Reset for hot-restart support: clear the stop event so a relaunched
    # task isn't killed by a stale set() left by the prior shutdown.
    stop_event.clear()

    logger.info("Codex usage sync task started")

    first_cycle = True  # always fetch once at startup for a baseline snapshot
    paused = False      # tracked only to log active<->pause transitions once

    while not stop_event.is_set():
        if first_cycle or should_run_usage_cycle():
            first_cycle = False
            if paused:
                logger.info("Codex usage sync resumed (activity detected)")
                paused = False
            # Consume any wake signal now that we are acting on it.
            wake_event.clear()

            success = False
            try:
                # On macOS, skip the OAuth token refresh only when Codex is in
                # keyring storage mode — there, refreshing makes the bundled codex
                # binary rewrite the "Codex Auth" Keychain item, resetting its ACL
                # and popping an unprompted authorization dialog (same issue as
                # Claude Code). The CLI deletes <codex home>/auth.json whenever it
                # switches to keyring, so the file's presence is a reliable, always-
                # current signal of where the binary writes: file present → refresh
                # rewrites the file (no prompt) → allow; file absent (keyring) →
                # refresh rewrites the Keychain → skip. Re-evaluated every cycle
                # because the mode can change at runtime (unlike Claude Code, which
                # is always Keychain on macOS). The token is then refreshed by real
                # sessions or on demand via the sidebar "Refresh now" button.
                allow_refresh = sys.platform != "darwin" or credentials_path().is_file()
                snapshot = await fetch_and_save_usage(allow_refresh=allow_refresh)
                if snapshot:
                    success = True
                    logger.info(
                        "Codex usage sync completed: 5h=%.1f%% (time: %.1f%%), 7d=%.1f%% (time: %.1f%%)",
                        snapshot.five_hour_utilization or 0,
                        snapshot.five_hour_temporal_pct or 0,
                        snapshot.seven_day_utilization or 0,
                        snapshot.seven_day_temporal_pct or 0,
                    )
                else:
                    logger.warning("Codex usage sync: no data (credentials missing or API error)")
            except Exception as e:
                logger.error("Codex usage sync failed: %s", e, exc_info=True)

            try:
                await broadcast_usage_updated(Provider.CODEX, success)
            except Exception as e:
                logger.error("Codex usage broadcast failed: %s", e, exc_info=True)

            # Active cadence: sleep the interval on the stop event only — wake
            # pings are ignored here so steady activity can't trigger a fetch
            # storm (we already fetch every interval while active).
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass
        else:
            if not paused:
                logger.info("Codex usage sync paused (idle, no active agent)")
                paused = True
            # Paused: resume early when activity wakes us, else re-check the gate
            # at the next interval.
            await wait_for_usage_resume(stop_event, interval)

    logger.info("Codex usage sync task stopped")
