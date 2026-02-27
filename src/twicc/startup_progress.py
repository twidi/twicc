"""
Startup progress tracking and broadcasting.

Maintains module-level state for each startup phase (initial_sync, background_compute)
and broadcasts progress updates via Django Channels to all connected WebSocket clients.

The state is also readable by UpdatesConsumer.connect() so clients joining mid-startup
receive the current progress immediately.
"""

from __future__ import annotations

import logging

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Module-level state: current startup progress for each phase.
# None means the phase hasn't started yet. A dict means the phase
# is active or completed — completed states are kept so clients
# connecting mid-startup can reconstruct the full picture.
_current_progress: dict[str, dict | None] = {
    "initial_sync": None,
    "background_compute": None,
}


def get_startup_progress() -> list[dict]:
    """Return list of startup progress states for WS connection init.

    Called by UpdatesConsumer.connect() to send current progress to newly
    connected clients. Includes completed phases so reconnecting clients
    can show them as finished.
    """
    return [state for state in _current_progress.values() if state is not None]


def set_startup_progress(phase: str, current: int, total: int, *, completed: bool = False) -> None:
    """Update the module-level progress state."""
    _current_progress[phase] = {
        "type": "startup_progress",
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
    }


async def broadcast_startup_progress(phase: str, current: int, total: int, *, completed: bool = False) -> None:
    """Update state and broadcast progress via Django Channels.

    Updates the module-level state first (so new connections get the latest),
    then broadcasts to all connected WebSocket clients via the "updates" group.
    """
    set_startup_progress(phase, current, total, completed=completed)

    message = {
        "type": "startup_progress",
        "phase": phase,
        "current": current,
        "total": total,
        "completed": completed,
    }

    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )
