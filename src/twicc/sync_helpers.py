"""
Cross-provider JSONL sync helpers.

Each provider stores session content as an append-only JSONL file. The
helpers in this module are the provider-agnostic, read-only plumbing every
initial-sync producer needs: a content probe before creating an empty
session, and an incremental reader that parses new JSONL lines into a
:class:`SessionItemsToInsert`. The producer turns that into a queue payload
(see :mod:`twicc.providers.db_writer`) — the actual DB writes happen in the
DB writer. Metadata computation (``kind``, ``display_level``,
costs, ...) stays in each provider's own compute path.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from twicc.core.models import Session, SessionItem

logger = logging.getLogger(__name__)


class SessionItemsToInsert(NamedTuple):
    """Result of parsing a session's JSONL file in the producer.

    The producer turns this into a ``CreateSessionPayload`` or
    ``UpdateSessionPayload`` (with ``items``, ``last_offset``, ``last_line``,
    ``mtime`` carried over) before pushing it onto the DB writer's
    queue. ``actually_new_count`` lets the caller decide whether to flag
    ``reset_compute_version`` on the payload.

    Producers receive ``None`` from :func:`read_session_items_from_file`
    when there is nothing to push — file missing, or unchanged since last
    sync (unless the session is still flagged ``stale``; see that function).
    """

    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    actually_new_count: int


class BackpressureSyncQueue:
    """A bounded-queue facade that blocks the producer instead of growing.

    The initial-sync producers run in ``asyncio.to_thread`` threads and parse
    JSONL far faster than the DB writer commits to SQLite. Pushing
    straight onto an unbounded queue would let the backlog — each payload
    carrying a whole session's lines — grow to gigabytes of RAM. ``sync_all``
    wraps the shared queue in this facade so every :meth:`put` blocks while the
    queue is full: the block *is* the backpressure, throttling the producer to
    the DB writer's write rate.

    The wait is stop-aware. ``shutdown()`` awaits the producer thread, so a
    plain blocking ``put`` on a full queue could deadlock it; :meth:`put` polls
    ``stop_event`` instead and returns once shutdown is signalled.
    """

    def __init__(self, sync_queue: queue.Queue, stop_event: threading.Event | None):
        self._queue = sync_queue
        self._stop_event = stop_event

    def put(self, payload: object) -> None:
        """Enqueue ``payload``, blocking while the queue is full.

        Drops the payload and returns if ``stop_event`` fires first — shutdown
        is underway and the permanent DB writer is going away regardless.
        """
        while True:
            if self._stop_event is not None and self._stop_event.is_set():
                return
            try:
                self._queue.put(payload, timeout=0.5)
                return
            except queue.Full:
                continue


def check_file_has_content(file_path: Path) -> bool:
    """
    Check if a JSONL file has any valid lines (non-empty, non-whitespace).

    This function performs no database operations and is used to determine
    if a session should be created before saving it.

    EAFP, not LBYL: the file is opened directly and any ``OSError`` (most
    often ``FileNotFoundError`` from the file vanishing between a scan and
    this call) is treated as "no content". A pre-flight ``exists()`` check
    would only reintroduce a TOCTOU race.

    Decoded with ``errors="replace"`` so invalid UTF-8 (a line cut
    mid-multibyte-character, or on-disk corruption) can't raise
    ``UnicodeDecodeError`` — a ``ValueError``, not an ``OSError``, so it would
    slip past the guard above and abort the caller's unguarded initial-sync
    loop. :func:`read_session_items_from_file` logs such files.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    return True
    except OSError:
        return False
    return False


def read_session_items_from_file(
    session: Session, file_path: Path, on_error: Callable[[OSError], None] | None = None,
) -> SessionItemsToInsert | None:
    """
    Read new lines from a JSONL file and return what should be inserted.

    Reads-only — performs the filesystem I/O and the ``pre_existing`` count
    (a DB read, safe under WAL) but does no writes. The caller (a producer
    inside ``asyncio.to_thread``) turns the result into a
    ``CreateSessionPayload`` or ``UpdateSessionPayload`` and pushes it onto
    the DB writer's queue.

    Returns ``None`` when there is nothing to do (file missing, or unchanged
    since last sync). Exception: an unchanged file whose session is still
    flagged ``stale`` yields an empty result (no items) instead of ``None``,
    so the caller can enqueue a stale-clearing ``UpdateSessionPayload``.
    ``on_error`` reports skipped stat/read failures without changing the
    return value. An unchanged file does not call it.
    """
    # EAFP, not LBYL: stat()/open() are attempted directly and any OSError
    # (typically FileNotFoundError from the file vanishing between a scan
    # and this call) returns None — there is nothing to sync. A pre-flight
    # exists() check would only reintroduce a TOCTOU race.
    try:
        stat = file_path.stat()
    except OSError as error:
        if on_error is not None:
            on_error(error)
        return None
    file_mtime = stat.st_mtime

    # If mtime hasn't changed and file hasn't grown beyond last_offset, nothing to do.
    # Check file size too: mtime has ~1s resolution, so two writes within the same second
    # share the same mtime. Without the size check, the second write would be silently skipped.
    if session.mtime == file_mtime and session.last_offset >= stat.st_size:
        if session.stale:
            # The file is back on disk unchanged, but the session is still
            # flagged stale from a run where the file was missing. There is no
            # new content, yet the caller must still enqueue an
            # UpdateSessionPayload so the DB writer's clear_stale path can run —
            # otherwise a deleted-then-restored session stays stale forever and
            # is wrongly excluded from the project mtime. Return an empty
            # result with the tracking fields unchanged. The stat() above
            # succeeded, so this never fires for a vanished file.
            return SessionItemsToInsert(
                items=[],
                last_offset=session.last_offset,
                last_line=session.last_line,
                mtime=file_mtime,
                actually_new_count=0,
            )
        return None

    # Read raw bytes, then decode leniently. A provider CLI can flush a line
    # cut mid-multibyte-character, or a file can be corrupted on disk; a strict
    # UTF-8 decode raises UnicodeDecodeError — a ValueError, not an OSError, so
    # it slips past the guards here and propagates through sync_project /
    # sync_all (neither guards its loop), aborting the whole provider's initial
    # sync. Replacing invalid bytes with U+FFFD confines the damage to its own
    # line (the per-line JSON parse downstream already tolerates it), so one
    # bad byte never blocks the rest of the file or the sync. Reading in binary
    # also keeps last_offset an unambiguous byte count, matching the
    # last_offset/st_size check above.
    try:
        with open(file_path, "rb") as f:
            f.seek(session.last_offset)
            raw = f.read()
            last_offset = f.tell()
    except OSError as error:
        if on_error is not None:
            on_error(error)
        return None

    try:
        new_content = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Invalid UTF-8 in %s — decoding with replacement", file_path)
        new_content = raw.decode("utf-8", errors="replace")

    if not new_content:
        # File was touched but no new content — caller still needs to persist
        # the new mtime (and the offset is unchanged).
        return SessionItemsToInsert(
            items=[],
            last_offset=last_offset,
            last_line=session.last_line,
            mtime=file_mtime,
            actually_new_count=0,
        )

    # Split into lines (filter out empty lines)
    lines = [line for line in new_content.split("\n") if line.strip()]

    if not lines:
        return SessionItemsToInsert(
            items=[],
            last_offset=last_offset,
            last_line=session.last_line,
            mtime=file_mtime,
            actually_new_count=0,
        )

    # Build (line_num, content) tuples; the DB writer creates the SessionItem
    # rows so that ``session`` references are resolved on the writer side
    # (matters for the create-session case where the row is being saved
    # inside the DB writer's transaction).
    current_line_num = session.last_line
    items: list[tuple[int, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            line = "{}"
        current_line_num += 1
        items.append((current_line_num, line))

    # Count how many of the new line_nums already exist in DB. Happens when
    # the watcher inserted items in a previous run but the session's
    # tracking fields weren't persisted before shutdown. The DB writer's
    # ``ignore_conflicts=True`` skips duplicates silently — the count here
    # lets the caller report accurate stats and decide whether to flag
    # ``reset_compute_version``.
    first_new_line = session.last_line + 1
    pre_existing = SessionItem.objects.filter(
        session=session,
        line_num__gte=first_new_line,
        line_num__lte=current_line_num,
    ).count()
    actually_new_count = len(items) - pre_existing

    return SessionItemsToInsert(
        items=items,
        last_offset=last_offset,
        last_line=current_line_num,
        mtime=file_mtime,
        actually_new_count=actually_new_count,
    )
