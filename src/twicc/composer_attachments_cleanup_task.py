"""Daily retention of the composer attachments staging store and of the artifacts pre-copies.

Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.4 "Server-side retention".

One pass:

1. **Entries** (``<staging>/<bucket>/<attachment_id>/``): removed when the directory mtime (refreshed
   by uploads and by ``touch/``) is at least :data:`COMMITTED_ENTRY_AGE` old with ``committed.json``,
   or :data:`DRAFT_ENTRY_AGE` old without it. An entry with ``oneshot.json`` (CLI, RPC, MCP) is
   removed once :data:`ONESHOT_ENTRY_AGE` old; that rule wins. Never while a non-terminal upload
   targets the entry.
   The reaper never cancels an upload: the uploads janitor expires a stalled one.
2. **Release tombstones** (``<bucket>/.released/<attachment_id>``) at least
   :data:`RELEASE_TOMBSTONE_AGE` old, then an empty ``.released/``, then an empty bucket.
3. **Pre-copies**: only the top-level ``.twicc-upload-*.tmp`` regular files of ``<data>/artifacts/``
   at least :data:`PRECOPY_AGE` old. Gated by ``settings.SESSION_DIRS_CLEANUP_ENABLED``: in a worktree
   ``artifacts/`` is a symlink shared with the main instance, which alone sweeps it.

The selection outside the locks only chooses candidates. Each entry, tombstone and directory is
re-checked and removed under the upload creation lock (and, for an entry, the locks of the uploads
that target it), so the reaper never races a composer creation or a release. Symlinks are never
followed, and only names that are valid refs are considered. Every age is measured against an
injectable clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from twicc.core.services.attachments import lifecycle, staging
from twicc.core.services.attachments.types import AttachmentRef
from twicc.paths import get_artifacts_dir, get_composer_attachments_dir
from twicc.uploads import locks, store

logger = logging.getLogger(__name__)

# A first pass a few minutes after startup (so frequent restarts never starve it), then daily.
COMPOSER_CLEANUP_FIRST_DELAY = 10 * 60
COMPOSER_CLEANUP_INTERVAL = 24 * 60 * 60

# An entry used by a send that was not delivered: the in-flight snapshot lifetime.
COMMITTED_ENTRY_AGE = timedelta(days=7)
# A draft attachment no browser has referenced for this long.
DRAFT_ENTRY_AGE = timedelta(days=30)
# A one-shot entry (CLI, RPC, MCP): nobody can send it twice (phase 2 design §4.3.2).
ONESHOT_ENTRY_AGE = timedelta(hours=24)
RELEASE_TOMBSTONE_AGE = timedelta(hours=24)
PRECOPY_AGE = timedelta(hours=24)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class CleanupStats(NamedTuple):
    entries: int  # entry directories removed
    tombstones: int  # release tombstones removed
    directories: int  # empty ``.released/`` and bucket directories removed
    precopies: int  # artifacts pre-copies removed


# ── Filesystem helpers (blocking) ─────────────────────────────────────────────


def _lstat_mtime(path: Path, kind: int) -> datetime | None:
    """mtime of *path* when it is (without following a symlink) of the ``stat`` *kind*, else ``None``."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_IFMT(st.st_mode) != kind:
        return None
    return datetime.fromtimestamp(st.st_mtime, tz=UTC)


def _is_older(reference: datetime | None, age: timedelta, now: datetime) -> bool:
    """True when *reference* is known and at least *age* old (an undatable path is never old)."""
    return reference is not None and now - reference >= age


def _staging_root() -> Path:
    return Path(os.path.realpath(get_composer_attachments_dir()))


def _entry_is_expired(ref: AttachmentRef, now: datetime) -> bool:
    """A real entry directory (bucket and entry not symlinks) old enough for its retention rule."""
    entry = _staging_root() / ref.bucket / ref.id
    if _lstat_mtime(entry.parent, stat.S_IFDIR) is None:
        return False
    mtime = _lstat_mtime(entry, stat.S_IFDIR)
    if os.path.lexists(entry / staging.ONESHOT_MARKER):
        return _is_older(mtime, ONESHOT_ENTRY_AGE, now)
    committed = os.path.lexists(entry / staging.COMMITTED_MARKER)
    return _is_older(mtime, COMMITTED_ENTRY_AGE if committed else DRAFT_ENTRY_AGE, now)


def _tombstone_path(ref: AttachmentRef) -> Path:
    return _staging_root() / ref.bucket / staging.RELEASED_DIR / ref.id


def _tombstone_is_expired(ref: AttachmentRef, now: datetime) -> bool:
    released = _tombstone_path(ref).parent
    if _lstat_mtime(released.parent, stat.S_IFDIR) is None or _lstat_mtime(released, stat.S_IFDIR) is None:
        return False
    return _is_older(_lstat_mtime(_tombstone_path(ref), stat.S_IFREG), RELEASE_TOMBSTONE_AGE, now)


def _rmdir_if_empty(path: Path) -> bool:
    """``rmdir`` a real, empty directory (``rmdir`` itself refuses a non-empty one)."""
    if _lstat_mtime(path, stat.S_IFDIR) is None:
        return False
    try:
        os.rmdir(path)
    except OSError:
        return False
    return True


class _BucketScan(NamedTuple):
    bucket: str
    entries: list[AttachmentRef]  # entry candidates (old enough at selection)
    tombstones: list[AttachmentRef]  # tombstone candidates (old enough at selection)


def _as_ref(bucket: str, name: str) -> AttachmentRef | None:
    try:
        return staging.validate_ref({"bucket": bucket, "id": name})
    except staging.AttachmentError:
        return None


def _scan(now: datetime) -> list[_BucketScan]:
    """The buckets of the staging store with their candidates. Creates nothing."""
    root = _staging_root()
    try:
        with os.scandir(root) as it:
            buckets = [
                entry.name
                for entry in it
                if entry.is_dir(follow_symlinks=False) and staging.is_valid_bucket(entry.name)
            ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    scans = []
    for bucket in sorted(buckets):
        entries: list[AttachmentRef] = []
        tombstones: list[AttachmentRef] = []
        try:
            with os.scandir(root / bucket) as it:
                names = [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in it]
        except OSError:
            continue
        for name, is_dir in sorted(names):
            if name == staging.RELEASED_DIR and is_dir:
                try:
                    with os.scandir(root / bucket / name) as it:
                        released = sorted(entry.name for entry in it)
                except OSError:
                    released = []
                for att_id in released:
                    ref = _as_ref(bucket, att_id)
                    if ref is not None and _tombstone_is_expired(ref, now):
                        tombstones.append(ref)
                continue
            ref = _as_ref(bucket, name) if is_dir else None
            if ref is not None and _entry_is_expired(ref, now):
                entries.append(ref)
        scans.append(_BucketScan(bucket, entries, tombstones))
    return scans


def _remove_expired_entry(ref: AttachmentRef, now: datetime) -> bool:
    """Re-check (the caller holds the locks) and remove an entry; never follows a symlink."""
    if not _entry_is_expired(ref, now):
        return False
    shutil.rmtree(_staging_root() / ref.bucket / ref.id)
    return True


def _remove_expired_tombstone(ref: AttachmentRef, now: datetime) -> bool:
    if not _tombstone_is_expired(ref, now):
        return False
    _tombstone_path(ref).unlink(missing_ok=True)
    return True


def _remove_empty_dirs(bucket: str) -> int:
    """An empty ``.released/``, then an empty bucket directory."""
    bucket_dir = _staging_root() / bucket
    removed = int(_rmdir_if_empty(bucket_dir / staging.RELEASED_DIR))
    return removed + int(_rmdir_if_empty(bucket_dir))


# ── Locked operations ─────────────────────────────────────────────────────────


def _has_live_upload(upload_ids: list[str]) -> bool:
    for upload_id in upload_ids:
        meta = store.peek_metadata(upload_id)
        if meta is not None and not store.is_terminal(meta["state"]):
            return True
    return False


async def _reap_entry(ref: AttachmentRef, now: datetime) -> bool:
    """Remove an expired entry under the creation lock and the locks of its uploads.

    Under the creation lock no new upload of the entry can appear; the non-terminal uploads that
    target it are then re-read under their own locks (a running finalization holds its lock), and
    the entry is kept when any is still non-terminal. Nothing is ever cancelled.
    """

    async def operation() -> bool:
        async with lifecycle.composer_creation_guard():
            upload_ids = await asyncio.to_thread(lifecycle.entry_upload_ids, ref)
            async with contextlib.AsyncExitStack() as stack:
                for upload_id in sorted(upload_ids):
                    lock = locks.get_upload_lock(upload_id)
                    if lock is not None:
                        await stack.enter_async_context(lock)
                if await asyncio.to_thread(_has_live_upload, upload_ids):
                    return False
                return await asyncio.to_thread(_remove_expired_entry, ref, now)

    return await locks.run_guarded(operation(), label=f"composer-reap({ref.bucket}/{ref.id})")


async def _under_creation_lock(function, *args):
    async def operation():
        async with lifecycle.composer_creation_guard():
            return await asyncio.to_thread(function, *args)

    return await locks.run_guarded(operation(), label=f"composer-reap-{function.__name__}")


# ── Artifacts pre-copies ──────────────────────────────────────────────────────


def _sweep_precopies(now: datetime) -> int:
    """Remove the old top-level ``.twicc-upload-*.tmp`` regular files of ``artifacts/`` (blocking)."""
    root = get_artifacts_dir()
    try:
        with os.scandir(root) as it:
            names = [entry.name for entry in it if store.is_upload_temp_name(entry.name)]
    except (FileNotFoundError, NotADirectoryError):
        return 0
    removed = 0
    for name in sorted(names):
        path = root / name
        if not _is_older(_lstat_mtime(path, stat.S_IFREG), PRECOPY_AGE, now):
            continue
        try:
            os.unlink(path)
        except FileNotFoundError:
            continue
        removed += 1
    return removed


# ── One pass ──────────────────────────────────────────────────────────────────


async def run_cleanup_pass(*, now: Clock = utc_now) -> CleanupStats:
    """Run one retention pass; *now* is read once, so every re-check uses the same instant."""
    from django.conf import settings

    current = now()
    entries = tombstones = directories = 0
    for scan in await asyncio.to_thread(_scan, current):
        for ref in scan.entries:
            try:
                if await _reap_entry(ref, current):
                    entries += 1
            except Exception:
                logger.warning("Composer attachments cleanup: cannot remove %s/%s", ref.bucket, ref.id, exc_info=True)
        for ref in scan.tombstones:
            try:
                if await _under_creation_lock(_remove_expired_tombstone, ref, current):
                    tombstones += 1
            except Exception:
                logger.warning("Composer attachments cleanup: cannot remove the tombstone of %s", ref, exc_info=True)
        try:
            directories += await _under_creation_lock(_remove_empty_dirs, scan.bucket)
        except Exception:
            logger.warning("Composer attachments cleanup: cannot prune bucket %s", scan.bucket, exc_info=True)

    precopies = 0
    if settings.SESSION_DIRS_CLEANUP_ENABLED:
        try:
            precopies = await asyncio.to_thread(_sweep_precopies, current)
        except Exception:
            logger.warning("Composer attachments cleanup: cannot sweep the artifacts pre-copies", exc_info=True)

    stats = CleanupStats(entries, tombstones, directories, precopies)
    if any(stats):
        logger.info(
            "Composer attachments cleanup: %d entries, %d tombstones, %d directories, %d pre-copies removed",
            *stats,
        )
    return stats


async def start_composer_attachments_cleanup_task(stop_event: asyncio.Event) -> None:
    """Retention loop: a first pass after :data:`COMPOSER_CLEANUP_FIRST_DELAY` seconds, then one every
    :data:`COMPOSER_CLEANUP_INTERVAL` seconds, until ``stop_event`` (the shared shutdown event) is set."""
    logger.info("Composer attachments cleanup task started")
    delay = COMPOSER_CLEANUP_FIRST_DELAY
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass  # time for a pass
            else:
                break  # stop_event fired

            try:
                await run_cleanup_pass()
            except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.exception("Composer attachments cleanup pass failed")
            delay = COMPOSER_CLEANUP_INTERVAL
    finally:
        logger.info("Composer attachments cleanup task stopped")
