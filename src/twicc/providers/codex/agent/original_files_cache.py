"""
Cache for original file contents captured before an ``apply_patch`` runs.

Codex SDK has no ``PreToolUse`` hook the way Claude Code does, but it
streams an ``item/started`` notification carrying a ``FileChangeThreadItem``
**before** the patch hits disk. ``CodexAgent._handle_stream_event`` reads
the listed paths synchronously at that point and stores their contents
here, keyed by ``(session_id, call_id)``. The compute pass later borrows the
entry when it sees the matching canonical ``FileChange`` completion and
splices the contents into the persisted JSON so the frontend can render
full-file diffs even though Codex only persists a ``unified_diff``.

The value is a ``{abs_path: content}`` mapping because a single
``apply_patch`` can touch multiple files — one cache entry per call,
not per file.

Thread safety: the agent writes on the event loop; live compute borrows on its
database worker. The shared cache locks capture, reservation, and cleanup operations.
"""

import asyncio

from twicc.providers.enrichment_cache import ENTRY_TTL, EnrichmentCache

_cleanup_stop_event: asyncio.Event | None = None

# Maximum size per file (bytes). Files larger than this are skipped at
# capture time, matching Claude's per-file limit so memory pressure
# stays bounded even on a multi-file patch.
MAX_FILE_SIZE = 100_000  # 100 KB

# The shared cache owns the lock, capture TTL, and exact-record retry reservations.
_cache: EnrichmentCache[dict[str, str]] = EnrichmentCache()


def cache_original_files(session_id: str, call_id: str, files: dict[str, str]) -> None:
    """Store pre-execution contents without replacing a claimed capture."""
    if not files:
        return
    _cache.put((session_id, call_id), files)


def pop_original_files(session_id: str, call_id: str) -> dict[str, str] | None:
    """Consume an unclaimed capture for a nontransactional caller."""
    return _cache.pop((session_id, call_id))


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
