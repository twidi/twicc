"""
Cache for original file contents captured before an ``apply_patch`` runs.

Codex SDK has no ``PreToolUse`` hook the way Claude Code does, but it
streams an ``item/started`` notification carrying a ``FileChangeThreadItem``
**before** the patch hits disk. ``CodexAgent._handle_stream_event`` reads
the listed paths synchronously at that point and stores their contents
here, keyed by ``(session_id, call_id)``. The compute pass later pops the
entry when it sees the matching ``event_msg.patch_apply_end`` line and
splices the contents into the persisted JSON so the frontend can render
full-file diffs even though Codex only persists a ``unified_diff``.

The value is a ``{abs_path: content}`` mapping because a single
``apply_patch`` can touch multiple files — one cache entry per call,
not per file.

Thread safety: the agent writes on the event loop; live compute pops on its
database worker. A lock protects compound cleanup and concurrent writes.
"""

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

_cleanup_stop_event: asyncio.Event | None = None

# Maximum size per file (bytes). Files larger than this are skipped at
# capture time, matching Claude's per-file limit so memory pressure
# stays bounded even on a multi-file patch.
MAX_FILE_SIZE = 100_000  # 100 KB

# TTL for cache entries (seconds). Entries older than this are cleaned up
# to avoid unbounded growth from orphaned entries (e.g. patch refused by
# the user, agent crash between ``item/started`` and ``patch_apply_end``).
ENTRY_TTL = 300  # 5 minutes

# Cache: (session_id, call_id) → ({abs_path: content}, timestamp)
_cache: dict[tuple[str, str], tuple[dict[str, str], float]] = {}
_cache_lock = threading.Lock()


def cache_original_files(
    session_id: str, call_id: str, files: dict[str, str],
) -> None:
    """Store file contents captured before an apply_patch execution.

    Overwrites any existing entry for the same ``(session_id, call_id)``
    — duplicate ``item/started`` notifications for the same call are not
    expected from the SDK, but if they ever happen the latest wins.
    No-op when ``files`` is empty (e.g. the patch only adds new files,
    nothing to capture).
    """
    if not files:
        return
    with _cache_lock:
        _cache[(session_id, call_id)] = (files, time.monotonic())


def pop_original_files(
    session_id: str, call_id: str,
) -> dict[str, str] | None:
    """Retrieve and remove cached contents for an apply_patch execution.

    Returns the ``{abs_path: content}`` mapping if found and not expired,
    ``None`` otherwise. Always consumes the entry whether it's used or
    not (so a stale entry never lingers past its first lookup).
    """
    with _cache_lock:
        entry = _cache.pop((session_id, call_id), None)
    if entry is None:
        return None
    files, ts = entry
    if time.monotonic() - ts > ENTRY_TTL:
        return None
    return files


def clear_session(session_id: str) -> None:
    """Drop every entry belonging to ``session_id``.

    Called from ``CodexAgent.interrupt_or_kill`` so an interrupted turn
    doesn't leave its in-flight captures sitting in memory until the TTL
    expires. The session id is the first part of the key tuple, hence
    the linear scan — there are at most a handful of in-flight calls per
    session, so the cost is negligible.
    """
    with _cache_lock:
        keys = [k for k in _cache if k[0] == session_id]
        for k in keys:
            del _cache[k]
    if keys:
        logger.debug(
            "original_files_cache: cleared %d entries for session %s",
            len(keys), session_id,
        )


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
        logger.debug(
            "original_files_cache: cleaned up %d expired entries", len(expired),
        )


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
