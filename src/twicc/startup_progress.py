"""
Startup progress tracking and broadcasting.

Maintains module-level state for each (provider, phase) tuple and broadcasts
progress updates via Django Channels to all connected WebSocket clients.

Provider-aware phases (initial_sync, background_compute) carry the provider
key emitting them so the frontend can aggregate totals across providers.
Provider-agnostic phases (e.g. search_index, which indexes sessions
regardless of the provider that produced them) pass ``provider=None``.

The state is also readable by UpdatesConsumer.connect() so clients joining
mid-startup receive the current progress immediately.
"""

from __future__ import annotations

import logging

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Module-level state: current startup progress for each (provider, phase) pair.
# Entries are created on first ``set_startup_progress`` call and kept after
# completion so clients connecting mid-startup can reconstruct the full picture.
_current_progress: dict[tuple[str | None, str], dict] = {}


def get_startup_progress() -> list[dict]:
    """Return the list of startup progress states for WS connection init.

    Called by UpdatesConsumer.connect() to send current progress to newly
    connected clients. Includes completed phases so reconnecting clients
    can show them as finished.
    """
    return list(_current_progress.values())


def set_startup_progress(
    phase: str,
    current: int,
    total: int,
    *,
    provider: str | None = None,
    completed: bool = False,
    detail: str | None = None,
) -> bool:
    """Update the module-level progress state for a (provider, phase) pair.

    ``detail`` is an optional free-text status the UI shows under the phase
    counter (e.g. the Codex rollout-migration tally). It is replaced on
    every accepted update, never accumulated.

    Monotonic by design: a stale ``completed=False`` message arriving after
    a ``completed=True`` for the same key, or a ``current`` that would move
    the counter backwards, is silently dropped. This protects against the
    race in ``_initial_sync_task`` where ``run_coroutine_threadsafe``
    schedules progress broadcasts from the sync thread that may execute on
    the asyncio loop *after* the orchestrator's final ``completed=True``
    broadcast has already updated the dict.

    Returns ``True`` when the call actually mutated the stored state,
    ``False`` when the update was rejected as stale. ``broadcast_startup_progress``
    relies on this to skip the matching WS broadcast when nothing changed —
    so stale messages don't reach connected clients either.
    """
    key = (provider, phase)
    existing = _current_progress.get(key)
    if existing is not None:
        if existing["completed"] and not completed:
            return False
        if current < existing["current"]:
            return False
    _current_progress[key] = {
        "type": "startup_progress",
        "provider": provider,
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
        "detail": detail,
    }
    return True


async def broadcast_startup_progress(
    phase: str,
    current: int,
    total: int,
    *,
    provider: str | None = None,
    completed: bool = False,
    detail: str | None = None,
) -> None:
    """Update state and broadcast progress via Django Channels.

    Updates the module-level state first (so new connections get the latest),
    then broadcasts to all connected WebSocket clients via the "updates" group.
    Stale messages rejected by :func:`set_startup_progress` (downgrade of
    ``completed`` or backwards ``current``) are also skipped on the wire so
    connected clients never see the regression.
    """
    if not set_startup_progress(phase, current, total, provider=provider, completed=completed, detail=detail):
        return

    message = {
        "type": "startup_progress",
        "provider": provider,
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
        "detail": detail,
    }

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )
