"""
Cache for original file contents captured before Edit/Write tools execute.

The PreToolUse hook reads file contents before the tool modifies them.
The watcher injects cached contents into tool_result items that lack originalFile.
This gives the frontend full-file diffs even when the SDK omits originalFile.

Thread safety: the agent writes on the event loop; live compute borrows on its
database worker. The shared cache locks capture, reservation, and cleanup operations.
"""

import asyncio

from twicc.providers.enrichment_cache import ENTRY_TTL, EnrichmentCache

_cleanup_stop_event: asyncio.Event | None = None

# Maximum file size to cache (bytes). Files larger than this are skipped.
MAX_FILE_SIZE = 100_000  # 100 KB

# The shared cache owns the lock, capture TTL, and exact-record retry reservations.
_cache: EnrichmentCache[str] = EnrichmentCache()


def cache_original_file(session_id: str, tool_use_id: str, content: str) -> None:
    """Store pre-execution contents without replacing a claimed capture."""
    _cache.put((session_id, tool_use_id), content)


def pop_original_file(session_id: str, tool_use_id: str) -> str | None:
    """Consume an unclaimed capture for a nontransactional caller."""
    return _cache.pop((session_id, tool_use_id))


def clear_session(session_id: str) -> None:
    """Invalidate current captures and retries, including outstanding borrows."""
    _cache.clear_session(session_id)


def cleanup_expired() -> None:
    """Remove expired captures and unused reservations; keep active borrows pinned."""
    _cache.cleanup_expired()


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
