"""
Pending selected model storage for new sessions.

When creating a new session, the selected_model must be stored before the
file watcher creates the Session row in the database. This module provides
a simple in-memory store (like the pending permission mode pattern) that the
watcher reads when creating the session.
"""

import logging

logger = logging.getLogger(__name__)

# Global dict: session_id -> selected_model string
_pending_models: dict[str, str] = {}


def set_pending_selected_model(session_id: str, model: str) -> None:
    """Store a selected model to be applied when the session is created by the watcher."""
    _pending_models[session_id] = model
    logger.debug("Set pending selected_model for session %s: %s", session_id, model)


def pop_pending_selected_model(session_id: str) -> str | None:
    """Get and remove a pending selected model for a session."""
    return _pending_models.pop(session_id, None)
