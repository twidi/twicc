"""
Pending session settings storage for new sessions.

When creating a new session, settings (permission_mode, selected_model, effort,
thinking_enabled) must be stored before the file watcher creates the Session row
in the database. This module provides a simple in-memory store that the watcher
reads when creating the session.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Global dict: session_id -> {field_name: value}
_pending: dict[str, dict[str, Any]] = {}


def set_pending(session_id: str, **kwargs: Any) -> None:
    """Store pending settings to be applied when the session is created by the watcher.

    Args:
        session_id: The session identifier
        **kwargs: Field name/value pairs (e.g., permission_mode="default", effort="high")
    """
    entry = _pending.setdefault(session_id, {})
    entry.update(kwargs)
    logger.debug("Set pending settings for session %s: %s", session_id, kwargs)


def pop_pending(session_id: str) -> dict[str, Any]:
    """Get and remove all pending settings for a session.

    Returns:
        Dict of field name/value pairs, or empty dict if none pending.
    """
    return _pending.pop(session_id, {})
