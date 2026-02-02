"""
Session title management - writing custom-title entries to JSONL files.

This module handles writing custom-title entries to session JSONL files.
Reading is handled by sync.py which parses CUSTOM_TITLE items.
"""

import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Global dict for pending titles
# Used when a process is in starting/assistant_turn
_pending_titles: dict[str, str] = {}


def get_session_jsonl_path(session) -> Path:
    """Get the JSONL file path for a session."""
    return Path(settings.CLAUDE_PROJECTS_DIR) / session.project_id / f"{session.id}.jsonl"


def write_custom_title_to_jsonl(session_id: str, title: str) -> None:
    """Write a custom-title entry to the session's JSONL file.

    The entry format matches what Claude CLI uses:
    {"type": "custom-title", "customTitle": "...", "sessionId": "..."}
    """
    from twicc.core.models import Session

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("Cannot write title: session %s not found", session_id)
        return

    jsonl_path = get_session_jsonl_path(session)

    if not jsonl_path.exists():
        logger.warning("Cannot write title: JSONL file %s does not exist", jsonl_path)
        return

    entry = {"type": "custom-title", "customTitle": title, "sessionId": session_id}

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    logger.debug("Wrote custom-title to %s: %s", jsonl_path, title[:50])


def set_pending_title(session_id: str, title: str) -> None:
    """Store a title to be written when the process becomes safe."""
    _pending_titles[session_id] = title
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove a pending title for a session."""
    return _pending_titles.pop(session_id, None)


def flush_pending_title(session_id: str) -> None:
    """Write pending title to JSONL if one exists.

    Called by ProcessManager when process transitions to user_turn or dead.
    """
    title = pop_pending_title(session_id)
    if title:
        logger.debug("Flushing pending title for session %s", session_id)
        write_custom_title_to_jsonl(session_id, title)
