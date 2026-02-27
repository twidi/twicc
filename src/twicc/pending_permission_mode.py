"""
Pending permission mode storage for new sessions.

When creating a new session, the permission_mode must be stored before the
file watcher creates the Session row in the database. This module provides
a simple in-memory store (like titles.py's pending title pattern) that the
watcher reads when creating the session.
"""

import logging

logger = logging.getLogger(__name__)

# Global dict: session_id -> permission_mode string
_pending_modes: dict[str, str] = {}


def set_pending_permission_mode(session_id: str, mode: str) -> None:
    """Store a permission mode to be applied when the session is created by the watcher."""
    _pending_modes[session_id] = mode
    logger.debug("Set pending permission_mode for session %s: %s", session_id, mode)


def pop_pending_permission_mode(session_id: str) -> str | None:
    """Get and remove a pending permission mode for a session."""
    return _pending_modes.pop(session_id, None)
