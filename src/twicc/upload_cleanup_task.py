"""Janitor of the browser file uploads staging area.

Design: docs/plans/2026-09-28-file-upload-design.md (§5.9, §5.7 "Recovery
runs", §5.1 unparsable ``.json``).

A first pass runs shortly after startup, then one every
:data:`UPLOAD_CLEANUP_INTERVAL`. One pass, in this order:

1. **Recovery** (§5.7). First pass: every non-terminal upload except an
   ``active`` one with ``error`` set (it waits for the client's *Retry*).
   Later passes: every ``finalizing`` upload not in the "finalizing now" set,
   every ``active`` upload whose ``<id>.part`` is missing, and every ``active``
   upload whose ``<id>.part`` is complete and whose ``error`` is not set.
2. **Expiry**: a non-terminal upload whose ``last_transfer_at`` is older than
   :data:`UPLOAD_EXPIRY` — ``active`` → ``failed`` (``"expired"``, the
   ``DELETE`` disk-full ordering); ``finalizing`` → ``failed``
   (``"recovery failed"``).
3. **Leftovers of terminal uploads**: ``<id>.part`` and the target temp file.
4. **Tombstones** older than 24 h (from ``updated_at``): ``<id>.json``, then
   the lock entry. No broadcast.
5. **Unparsable** ``<id>.json`` still unparsable 24 h after its mtime: removed
   with its ``<id>.part``, then the lock entry.
6. **Orphans** older than 1 h: ``<id>.part`` without ``<id>.json``, and the
   ``atomic_write_json`` temp files.

Recovery runs before expiry, so a complete transfer left by a crash during a
long backend stop is finalized, not expired.

Every action on one upload (1-5) is a guarded operation
(:func:`twicc.uploads.locks.run_guarded`) that takes the upload's lock,
re-reads the metadata and **re-checks its selection conditions** (state,
``error``, ``.part`` size, ``last_transfer_at``, "finalizing now") before it
acts: an upload that a concurrent request just changed is skipped. The
selection outside the lock only chooses the candidates.

Every age is measured against an injectable clock (``now``), so tests control
ages without sleeping. Only the name filter of §5.1 is ever considered: other
entries of the staging dir are never touched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from twicc.paths import get_uploads_dir
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views
from twicc.uploads.broadcast import broadcast_upload_state

logger = logging.getLogger(__name__)


# A first pass shortly after startup (recovery of what a crash or a stop left),
# then one every 6 h (§5.9).
UPLOAD_CLEANUP_FIRST_DELAY = 30
UPLOAD_CLEANUP_INTERVAL = 6 * 60 * 60

# A non-terminal upload with no transfer for this long expires (§5.9).
UPLOAD_EXPIRY = timedelta(days=7)
# A ``.part`` without ``.json``, or an ``atomic_write_json`` temp file, is an
# orphan after this long (§5.9).
ORPHAN_AGE = timedelta(hours=1)
# An unparsable ``.json`` is removed this long after its mtime (§5.1).
UNPARSABLE_AGE = timedelta(hours=24)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The default clock: current time, aware, UTC."""
    return datetime.now(UTC)


class PassStats(NamedTuple):
    """What one janitor pass did (counts of uploads or files)."""

    recovered: int  # recovery steps run
    expired: int  # uploads written ``failed`` by expiry
    leftovers: int  # terminal uploads whose leftovers were removed
    tombstones: int  # tombstones removed
    unparsable: int  # unparsable ``.json`` removed
    orphans: int  # orphan files removed


# ── Time helpers ──────────────────────────────────────────────────────────────


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _is_older(reference: datetime | None, age: timedelta, now: datetime) -> bool:
    """True when *reference* is known and at least *age* old.

    An unknown reference is never old: the janitor never removes what it
    cannot date.
    """
    return reference is not None and now - reference >= age


def _mtime(path: str | os.PathLike) -> datetime | None:
    """mtime of a regular file (``lstat``), aware UTC; ``None`` when absent or not a file."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return datetime.fromtimestamp(st.st_mtime, tz=UTC)


# ── Selection (re-checked under the lock) ────────────────────────────────────


def needs_recovery(meta: dict, part_size: int | None, *, first_pass: bool) -> bool:
    """The recovery selection of §5.7 "Recovery runs" / §5.9.

    Never for a terminal upload, nor for an id in the "finalizing now" set.
    First pass: every other non-terminal upload except an ``active`` one with
    ``error`` set. Later passes: ``finalizing``; ``active`` without ``.part``;
    ``active`` with a complete ``.part`` and no ``error``.
    """
    state = meta["state"]
    if store.is_terminal(state) or locks.is_finalizing_now(meta["id"]):
        return False
    has_error = meta.get("error") is not None
    if first_pass:
        # An ``active`` upload with ``error`` waits for the client's Retry,
        # which goes through the HEAD gates (§5.3).
        return not (state == store.STATE_ACTIVE and has_error)
    if state == store.STATE_FINALIZING:
        return True
    # ``active``: ``.part`` missing, or complete without ``error`` (a crash
    # left it; a failed finalization waits for the client's Retry).
    return part_size is None or (part_size >= meta["size"] and not has_error)


def is_expired(meta: dict, now: datetime) -> bool:
    """The expiry selection of §5.9: non-terminal, not "finalizing now",
    ``last_transfer_at`` older than :data:`UPLOAD_EXPIRY`."""
    if store.is_terminal(meta["state"]) or locks.is_finalizing_now(meta["id"]):
        return False
    return _is_older(_parse_iso(meta.get("last_transfer_at")), UPLOAD_EXPIRY, now)


def is_old_tombstone(meta: dict, now: datetime) -> bool:
    """A terminal upload whose ``updated_at`` is at least 24 h old."""
    if not store.is_terminal(meta["state"]):
        return False
    return _is_older(_parse_iso(meta.get("updated_at")), store.TOMBSTONE_LIFETIME, now)


def _terminal_leftovers(meta: dict) -> list[str]:
    """The files a terminal upload should not have any more (present ones only)."""
    paths = [str(store.part_path(meta["id"])), store.temp_file_path(meta["id"], meta["target_dir"])]
    return [path for path in paths if os.path.lexists(path)]


# ── Snapshot (lock-free: only chooses the candidates) ────────────────────────


class Snapshot(NamedTuple):
    """The staging dir at the start of a phase, filtered by the §5.1 name rule."""

    metas: dict[str, dict]  # parsable ``<id>.json`` by id
    unparsable: set[str]  # ids whose ``<id>.json`` does not parse
    json_ids: set[str]  # ids with an ``<id>.json`` of any content
    parts: set[str]  # ids with an ``<id>.part``
    json_temps: list[str]  # paths of the ``atomic_write_json`` temp files


def take_snapshot() -> Snapshot | None:
    """Scan the staging dir (blocking). ``None`` when it does not exist (never created here)."""
    if not get_uploads_dir().is_dir():
        return None
    metas: dict[str, dict] = {}
    unparsable: set[str] = set()
    json_ids: set[str] = set()
    parts: set[str] = set()
    json_temps: list[str] = []
    for name, entry in store.scan_staging():
        if name.kind == store.NAME_KIND_PART:
            parts.add(name.upload_id)
        elif name.kind == store.NAME_KIND_JSON_TMP:
            json_temps.append(entry.path)
        else:
            json_ids.add(name.upload_id)
            try:
                meta = store.read_metadata(name.upload_id)
            except store.UnparsableMetadataError:
                unparsable.add(name.upload_id)
                continue
            except OSError:
                logger.warning("Upload cleanup: cannot read %s", entry.path, exc_info=True)
                continue
            if meta is not None:
                metas[name.upload_id] = meta
    return Snapshot(metas, unparsable, json_ids, parts, json_temps)


# ── Guarded actions ───────────────────────────────────────────────────────────


async def _guarded(upload_id: str, action: Callable[[], Awaitable[bool]], *, label: str) -> bool:
    """Run *action* as a guarded operation of one upload; ``True`` when it acted.

    The lock is taken inside the shielded task (§5.4). No lock (the ``.json``
    is gone) → nothing to do. An exception is logged (once, by the runner) and
    counts as "did not act": the next action or pass goes on.
    """
    lock = locks.get_upload_lock(upload_id)
    if lock is None:
        return False

    async def operation() -> bool:
        async with lock:
            return await action()

    try:
        return await locks.run_guarded(operation(), label=f"cleanup-{label}({upload_id})")
    except Exception as exc:
        if not locks.was_logged(exc):  # pragma: no cover - the runner logs it
            logger.exception("Upload cleanup: %s of %s failed", label, upload_id)
        return False


def _reread(upload_id: str) -> tuple[dict | None, int | None]:
    """The metadata re-read under the lock (``None``: absent or unparsable) and the ``.part`` size."""
    return store.peek_metadata(upload_id), store.part_size(upload_id)


async def _recover(upload_id: str, *, first_pass: bool) -> bool:
    async def action() -> bool:
        meta, part = await asyncio.to_thread(_reread, upload_id)
        if meta is None or not needs_recovery(meta, part, first_pass=first_pass):
            return False
        # Resolved at call time, so tests can replace it.
        await upload_views.recover_upload(upload_id)
        return True

    return await _guarded(upload_id, action, label="recovery")


async def _expire(upload_id: str, now: datetime) -> bool:
    async def action() -> bool:
        meta, _part = await asyncio.to_thread(_reread, upload_id)
        if meta is None or not is_expired(meta, now):
            return False
        if meta["state"] == store.STATE_ACTIVE:
            outcome = await asyncio.to_thread(store.expire_active_upload, upload_id)
        else:
            outcome = await asyncio.to_thread(store.expire_finalizing_upload, upload_id, meta)
        if outcome.meta is None:
            return False
        await broadcast_upload_state(outcome.meta)
        return True

    return await _guarded(upload_id, action, label="expiry")


def _remove_leftovers(upload_id: str) -> bool:
    meta = store.peek_metadata(upload_id)
    if meta is None or not store.is_terminal(meta["state"]):
        return False
    leftovers = _terminal_leftovers(meta)
    for path in leftovers:
        store.remove_best_effort(path)
    return bool(leftovers)


async def _clean_terminal(upload_id: str) -> bool:
    async def action() -> bool:
        return await asyncio.to_thread(_remove_leftovers, upload_id)

    return await _guarded(upload_id, action, label="leftovers")


def _unlink_metadata(upload_id: str) -> bool:
    """Remove ``<id>.json``; ``True`` when it is gone (``ENOENT`` included)."""
    try:
        os.unlink(store.metadata_path(upload_id))
    except FileNotFoundError:
        return True
    except OSError:
        logger.warning("Upload cleanup: cannot remove the metadata of %s", upload_id, exc_info=True)
        return False
    return True


def _remove_tombstone(upload_id: str, now: datetime) -> bool:
    meta = store.peek_metadata(upload_id)
    if meta is None or not is_old_tombstone(meta, now):
        return False
    return _unlink_metadata(upload_id)


async def _drop_tombstone(upload_id: str, now: datetime) -> bool:
    async def action() -> bool:
        removed = await asyncio.to_thread(_remove_tombstone, upload_id, now)
        if removed:
            locks.forget_upload_lock(upload_id)
        return removed

    return await _guarded(upload_id, action, label="tombstone")


def _remove_unparsable(upload_id: str, now: datetime) -> bool:
    try:
        store.read_metadata(upload_id)
    except store.UnparsableMetadataError:
        pass
    else:
        return False  # rewritten meanwhile, or gone
    if not _is_older(_mtime(store.metadata_path(upload_id)), UNPARSABLE_AGE, now):
        return False
    if not _unlink_metadata(upload_id):
        return False
    store.remove_best_effort(store.part_path(upload_id))
    return True


async def _drop_unparsable(upload_id: str, now: datetime) -> bool:
    async def action() -> bool:
        removed = await asyncio.to_thread(_remove_unparsable, upload_id, now)
        if removed:
            locks.forget_upload_lock(upload_id)
        return removed

    return await _guarded(upload_id, action, label="unparsable")


def _remove_orphans(snapshot: Snapshot, now: datetime) -> int:
    """Remove the orphans older than :data:`ORPHAN_AGE` (blocking).

    No lock exists for them (no ``<id>.json``); the conditions are re-checked
    just before each removal. A new upload's ``.part`` is never old: creation
    writes its ``.json`` right after it, under the creation lock.
    """
    removed = 0
    for upload_id in sorted(snapshot.parts - snapshot.json_ids):
        part = store.part_path(upload_id)
        if store.metadata_path(upload_id).exists():
            continue
        if _is_older(_mtime(part), ORPHAN_AGE, now):
            store.remove_best_effort(part)
            removed += 1
    for path in snapshot.json_temps:
        if _is_older(_mtime(path), ORPHAN_AGE, now):
            store.remove_best_effort(path)
            removed += 1
    return removed


# ── One pass ──────────────────────────────────────────────────────────────────


async def run_cleanup_pass(*, first_pass: bool, now: Clock = utc_now) -> PassStats:
    """Run one janitor pass (§5.9); *now* is the clock every age is measured with."""
    snapshot = await asyncio.to_thread(take_snapshot)
    if snapshot is None:
        return PassStats(0, 0, 0, 0, 0, 0)

    # 1. Recovery, before expiry.
    recovered = 0
    for upload_id, meta in sorted(snapshot.metas.items()):
        if store.is_terminal(meta["state"]):
            continue
        part = await asyncio.to_thread(store.part_size, upload_id)
        if needs_recovery(meta, part, first_pass=first_pass) and await _recover(upload_id, first_pass=first_pass):
            recovered += 1

    # 2. Expiry. The candidates come from the snapshot taken before recovery;
    # the re-read under the lock sees what recovery did.
    expired = 0
    for upload_id, meta in sorted(snapshot.metas.items()):
        if is_expired(meta, now()) and await _expire(upload_id, now()):
            expired += 1

    # 3-6 work on a fresh view of the staging dir.
    snapshot = await asyncio.to_thread(take_snapshot)
    if snapshot is None:  # pragma: no cover - removed during the pass
        return PassStats(recovered, expired, 0, 0, 0, 0)

    leftovers = tombstones = 0
    for upload_id, meta in sorted(snapshot.metas.items()):
        if not store.is_terminal(meta["state"]):
            continue
        if await asyncio.to_thread(_terminal_leftovers, meta) and await _clean_terminal(upload_id):
            leftovers += 1
        if is_old_tombstone(meta, now()) and await _drop_tombstone(upload_id, now()):
            tombstones += 1

    unparsable = 0
    for upload_id in sorted(snapshot.unparsable):
        if await _drop_unparsable(upload_id, now()):
            unparsable += 1

    orphans = await asyncio.to_thread(_remove_orphans, snapshot, now())

    stats = PassStats(recovered, expired, leftovers, tombstones, unparsable, orphans)
    if any(stats):
        logger.info(
            "Upload cleanup: %d recovered, %d expired, %d with leftovers, %d tombstones, %d unparsable, %d orphans",
            *stats,
        )
    return stats


async def start_upload_cleanup_task(stop_event: asyncio.Event) -> None:
    """Periodic janitor loop of the uploads staging area (§5.9).

    Waits :data:`UPLOAD_CLEANUP_FIRST_DELAY` seconds, runs the first pass
    (startup recovery), then one pass every :data:`UPLOAD_CLEANUP_INTERVAL`
    seconds, until ``stop_event`` is set (the shared shutdown event).
    """
    logger.info("Upload cleanup task started")
    first_pass = True
    delay = UPLOAD_CLEANUP_FIRST_DELAY
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass  # time for a pass
            else:
                break  # stop_event fired

            try:
                await run_cleanup_pass(first_pass=first_pass)
            except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
                logger.exception("Upload cleanup pass failed")
            first_pass = False
            delay = UPLOAD_CLEANUP_INTERVAL
    finally:
        logger.info("Upload cleanup task stopped")
