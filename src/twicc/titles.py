"""
Session title management - writing custom-title entries to JSONL files.

This module handles writing custom-title entries to session JSONL files.
Reading is handled by sync.py which parses CUSTOM_TITLE items.

Write reliability: Because the JSONL file is shared with the Claude CLI subprocess
(which also appends to it), concurrent writes can corrupt our custom-title line
(e.g. lines glued together without newline). To handle this, write_custom_title_to_jsonl
verifies the entry is intact after writing and retries if needed.
"""

import json
import logging
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Global dict for pending titles
# Used when a process is in starting/assistant_turn
_pending_titles: dict[str, str] = {}

# Max title length (matches frontend validation)
MAX_TITLE_LENGTH = 200


def validate_title(title: str | None) -> tuple[str | None, str | None]:
    """Validate and normalize a session title.

    Args:
        title: The title to validate (can be None or empty string)

    Returns:
        A tuple of (normalized_title, error_message).
        - If valid: (trimmed_title, None)
        - If invalid: (None, error_message)
    """
    if title is None:
        return None, "Title cannot be empty"

    title = title.strip()
    if not title:
        return None, "Title cannot be empty"

    if len(title) > MAX_TITLE_LENGTH:
        return None, f"Title must be {MAX_TITLE_LENGTH} characters or less"

    return title, None


def get_session_jsonl_path(session) -> Path:
    """Get the JSONL file path for a session."""
    return Path(settings.CLAUDE_PROJECTS_DIR) / session.project_id / f"{session.id}.jsonl"


# Max bytes to read from end of file when verifying a write.
# Starts at 4KB and grows exponentially up to this ceiling.
_VERIFY_MAX_READ_BYTES = 2 * 1024 * 1024  # 2 MB


def _verify_title_in_jsonl(jsonl_path: Path, entry_json: str) -> bool:
    """Verify that entry_json exists as a complete line in the JSONL file.

    Reads from the end of the file with an exponentially growing buffer
    (4KB -> 16KB -> 64KB -> ... -> 2MB) to find the entry without reading
    the entire file.

    Args:
        jsonl_path: Path to the JSONL file.
        entry_json: The exact JSON string (without trailing newline) to look for.

    Returns:
        True if the entry is found as a complete line, False otherwise.
    """
    try:
        file_size = jsonl_path.stat().st_size
    except OSError:
        return False

    chunk_size = 4096
    while chunk_size <= _VERIFY_MAX_READ_BYTES:
        seek_pos = max(0, file_size - chunk_size)
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                f.seek(seek_pos)
                tail = f.read()
        except OSError:
            return False

        # Check each line for an exact match (not just substring)
        for line in tail.split("\n"):
            if line.strip() == entry_json:
                return True

        # If we've read the whole file, no point growing the buffer
        if seek_pos == 0:
            return False

        chunk_size *= 4

    return False


def write_custom_title_to_jsonl(session_id: str, title: str, max_retries: int = 3) -> bool:
    """Write a custom-title entry to the session's JSONL file.

    The entry format matches what Claude CLI uses:
    {"type": "custom-title", "customTitle": "...", "sessionId": "..."}

    Because the JSONL file is shared with the Claude CLI subprocess, concurrent
    writes can corrupt our line. This function verifies the write succeeded and
    retries if the entry is not found intact in the file.

    Duplicate entries are harmless: sync.py stores custom-title updates in a dict
    keyed by session_id, so only the last one wins.

    Returns:
        True if the entry was successfully written and verified, False otherwise.
    """
    from twicc.core.models import Session

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("Cannot write title: session %s not found", session_id)
        return False

    jsonl_path = get_session_jsonl_path(session)

    if not jsonl_path.exists():
        logger.warning("Cannot write title: JSONL file %s does not exist", jsonl_path)
        return False

    entry = {"type": "custom-title", "customTitle": title, "sessionId": session_id}
    entry_json = json.dumps(entry)

    for attempt in range(max_retries):
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(entry_json + "\n")

        # Small delay to let any concurrent write (Claude CLI) flush its buffer,
        # which could corrupt our line if it was mid-write.
        time.sleep(0.05 * (attempt + 1))

        if _verify_title_in_jsonl(jsonl_path, entry_json):
            if attempt > 0:
                logger.info(
                    "Wrote custom-title to %s after %d retries: %s",
                    jsonl_path, attempt, title[:50],
                )
            else:
                logger.debug("Wrote custom-title to %s: %s", jsonl_path, title[:50])
            return True

        logger.warning(
            "Custom-title write verification failed for session %s "
            "(attempt %d/%d, file: %s)",
            session_id, attempt + 1, max_retries, jsonl_path,
        )

    logger.error(
        "Failed to write custom-title after %d attempts for session %s",
        max_retries, session_id,
    )
    return False


def set_pending_title(session_id: str, title: str) -> None:
    """Store a title to be written when the process becomes safe."""
    _pending_titles[session_id] = title
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Get a pending title for a session without removing it."""
    return _pending_titles.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove a pending title for a session."""
    return _pending_titles.pop(session_id, None)


def flush_pending_title(session_id: str) -> None:
    """Write pending title to JSONL if one exists.

    Called by ProcessManager when process transitions to user_turn or dead.
    Only removes the pending title from memory if the write succeeds.
    If it fails, the title stays pending and will be retried on the next
    state change.
    """
    title = get_pending_title(session_id)
    if title:
        logger.debug("Flushing pending title for session %s", session_id)
        success = write_custom_title_to_jsonl(session_id, title)
        if success:
            pop_pending_title(session_id)
        else:
            logger.error(
                "Keeping pending title for session %s for retry on next state change",
                session_id,
            )
