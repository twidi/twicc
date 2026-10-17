"""Watcher of the data dir root for the data dir backups (``twicc.data_backups``).

Started by ``run_server()`` and stopped by cancelling it. Watches the data dir
root only (not recursive) and takes a version of each covered file that
changes, once the writes to it go quiet. Two scans close the gaps around the
watch: a catch-up scan as soon as the watch is armed (the writes made between
the startup scan and the start of the watch), and a final scan when the
watcher is cancelled (a change still waiting for its quiet delay).

Design: docs/plans/2026-10-17-data-dir-file-backups-design.md.
"""

import asyncio
import logging
from pathlib import Path

from watchfiles import Change, awatch

from twicc.data_backups import is_covered, scan_all, take_version
from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)


def is_watched(path: str, data_dir: Path) -> bool:
    """Whether a watcher event path is a covered file directly in ``data_dir``.

    Compared on resolved paths: the default data dir is not resolved, and a
    platform may report events under the canonical path (macOS FSEvents), so a
    symlinked data dir would otherwise never match.
    """
    candidate = Path(path)
    return is_covered(candidate.name) and candidate.parent.resolve() == data_dir.resolve()


async def run_data_backups_watcher(*, step_ms: int = 1000, debounce_ms: int = 5000, rust_timeout_ms: int = 1000) -> None:
    """Back up each change to a covered file of the data dir root, until cancelled.

    ``step_ms`` is the quiet delay: a burst of writes that each create a new
    path (every backend writer replaces the file with a temp file) gives one
    version. ``debounce_ms`` caps a burst that never goes quiet. Never raises
    anything but the cancellation.
    """
    data_dir = get_data_dir().resolve()

    def watched(change: Change, path: str) -> bool:
        return is_watched(path, data_dir)

    # Private on purpose: ``awatch`` sets the event it is given when it is
    # cancelled, and the backend's shutdown event must not be set that way.
    stop_event = asyncio.Event()
    try:
        armed = False
        async for changes in awatch(
            data_dir,
            watch_filter=watched,
            recursive=False,
            step=step_ms,
            debounce=debounce_ms,
            rust_timeout=rust_timeout_ms,
            yield_on_timeout=True,
            stop_event=stop_event,
        ):
            if not armed:
                # The first yield (a change or the timeout) means the watch is
                # running: catch up on what was written before it started.
                armed = True
                await asyncio.to_thread(scan_all, reason="watch start")
            names = sorted({Path(path).name for change, path in changes if change in (Change.added, Change.modified)})
            for name in names:
                if await asyncio.to_thread(take_version, name):
                    logger.info("Backed up %s", name)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Data backups watcher stopped")
    finally:
        # A few small files: cheap enough to run on the loop at shutdown.
        scan_all(reason="shutdown")
