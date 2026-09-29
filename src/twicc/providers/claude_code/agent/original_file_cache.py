"""
Cache for original file contents captured before Edit/Write tools execute.

The PreToolUse hook reads file contents before the tool modifies them.
The watcher injects cached contents into tool_result items that lack originalFile.
This gives the frontend full-file diffs even when the SDK omits originalFile.

Thread safety: the agent writes on the event loop; live compute pops on its
database worker. A lock protects compound cleanup and concurrent writes.
"""

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

_cleanup_stop_event: asyncio.Event | None = None

# Maximum file size to cache (bytes). Files larger than this are skipped.
MAX_FILE_SIZE = 100_000  # 100 KB

# TTL for cache entries (seconds). Entries older than this are cleaned up
# to avoid unbounded growth from orphaned entries (e.g. process crash).
ENTRY_TTL = 300  # 5 minutes

# Cache: (session_id, tool_use_id) → (file_content, timestamp)
_cache: dict[tuple[str, str], tuple[str, float]] = {}
_cache_lock = threading.Lock()


def cache_original_file(session_id: str, tool_use_id: str, content: str) -> None:
    """Store file content captured before a tool execution."""
    with _cache_lock:
        _cache[(session_id, tool_use_id)] = (content, time.monotonic())


def pop_original_file(session_id: str, tool_use_id: str) -> str | None:
    """Retrieve and remove cached file content for a tool execution.

    Returns the file content if found, None otherwise.
    """
    with _cache_lock:
        entry = _cache.pop((session_id, tool_use_id), None)
    if entry is None:
        return None
    content, ts = entry
    # Check TTL
    if time.monotonic() - ts > ENTRY_TTL:
        return None
    return content


def cleanup_expired() -> None:
    """Remove all expired entries."""
    with _cache_lock:
        if not _cache:
            return
        now = time.monotonic()
        expired = [key for key, (_, ts) in _cache.items() if now - ts > ENTRY_TTL]
        for key in expired:
            del _cache[key]
    if expired:
        logger.debug("original_file_cache: cleaned up %d expired entries", len(expired))


async def start_cleanup_task() -> None:
    """Periodic cleanup task for expired cache entries. Runs every ENTRY_TTL seconds."""
    global _cleanup_stop_event
    _cleanup_stop_event = asyncio.Event()

    while not _cleanup_stop_event.is_set():
        try:
            await asyncio.wait_for(_cleanup_stop_event.wait(), timeout=ENTRY_TTL)
            break  # stop event was set
        except TimeoutError:
            pass  # timeout expired, do cleanup
        cleanup_expired()


def stop_cleanup_task() -> None:
    """Signal the cleanup task to stop."""
    if _cleanup_stop_event is not None:
        _cleanup_stop_event.set()
