"""Coordination of composer staging entries with the tus uploads that fill them.

- :func:`composer_creation_guard`: the shared upload creation lock, held by a composer upload
  creation and by a release of an entry.
- :func:`settle_entry_uploads`: the settle rule, run for every live upload of an entry.
- :func:`reset_entry`, :func:`is_released`, :func:`touch_upload_entry`: the blocking entry work
  of a creation and of the transfer progress.
- :func:`release_refs`, :func:`status_refs`, :func:`touch_refs`: release, status and heartbeat of
  entries (the REST endpoints and the server release at delivery).

``twicc.uploads.views`` imports this module, so it is only imported here inside functions.
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.1 and §6.1.4.
"""

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from twicc.core.services.attachments import staging
from twicc.core.services.attachments.types import AttachmentRef
from twicc.uploads import locks, store

logger = logging.getLogger(__name__)

COMPOSER_PANEL = store.ORIGIN_PANEL_COMPOSER
SCOPE_KIND_COMPOSER = "composer"

# Markers removed when a new upload attempt resets an entry.
_RESET_MARKERS = (staging.READY_MARKER, staging.PROMOTED_MARKER, staging.COMMITTED_MARKER)
# HTTP answers of the settle operation that mean "settled".
_SETTLED_CODES = frozenset({204, 404})


class SettleError(Exception):
    """An upload of the entry could not be settled (its cancel write failed): nothing may be reset."""

    def __init__(self, upload_id: str, code: int):
        super().__init__(f"upload {upload_id} could not be settled ({code})")
        self.upload_id = upload_id
        self.code = code


@contextlib.asynccontextmanager
async def composer_creation_guard() -> AsyncIterator[None]:
    """Hold the upload creation lock (the one every upload creation holds) for the block."""
    async with locks.get_creation_lock():
        yield


# ── Identity ──


def origin_key(ref: AttachmentRef) -> str:
    """The ``origin.key`` of the uploads of an entry: ``<bucket>/<attachment_id>``."""
    return f"{ref.bucket}/{ref.id}"


def ref_from_origin_key(key: object) -> AttachmentRef:
    """Parse and validate an ``origin.key``; raises :class:`staging.AttachmentError` when invalid."""
    if not isinstance(key, str):
        raise staging.AttachmentError(staging.ERROR_INVALID_REF, "Attachment key must be a string")
    bucket, _, att_id = key.partition("/")
    return staging.validate_ref({"bucket": bucket, "id": att_id})


def composer_ref(meta: dict) -> AttachmentRef | None:
    """The entry an upload targets, or ``None`` for another origin or an invalid key."""
    origin = meta.get("origin")
    if not isinstance(origin, dict) or origin.get("panel") != COMPOSER_PANEL:
        return None
    try:
        return ref_from_origin_key(origin.get("key"))
    except staging.AttachmentError:
        return None


def upload_target_dir(ref: AttachmentRef) -> Path:
    """The tus target of an entry: ``<staging>/<bucket>/<attachment_id>/file``."""
    return staging.entry_dir(ref) / staging.FILE_DIR


def released_marker(ref: AttachmentRef) -> Path:
    """The release tombstone of an entry: ``<staging>/<bucket>/.released/<attachment_id>``."""
    return staging.entry_dir(ref).parent / staging.RELEASED_DIR / ref.id


# ── Blocking entry work ──


def is_released(ref: AttachmentRef) -> bool:
    """True when the entry has a release tombstone (a creation then answers 410)."""
    return os.path.lexists(released_marker(ref))


def _require_plain_dir(path: Path) -> None:
    """Refuse an existing *path* that is a symlink or not a directory."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise NotADirectoryError(f"{path} is not a plain directory")


def reset_entry(ref: AttachmentRef) -> Path:
    """Remove the entry markers, empty ``file/`` and create it (with its parents); return ``file/``.

    The markers go first, so a marker never names a file that was already removed.
    """
    entry = staging.entry_dir(ref)
    file_dir = entry / staging.FILE_DIR
    _require_plain_dir(entry)
    _require_plain_dir(file_dir)
    for name in _RESET_MARKERS:
        (entry / name).unlink(missing_ok=True)
    if file_dir.exists():
        shutil.rmtree(file_dir)
    file_dir.mkdir(parents=True)
    return file_dir


def touch_entry(ref: AttachmentRef) -> None:
    """Set an existing entry directory's mtime to now; never creates it, never touches its markers."""
    entry = staging.entry_dir(ref)
    try:
        if entry.parent.is_symlink() or entry.is_symlink() or not entry.is_dir():
            return
        os.utime(entry)
    except OSError:
        logger.warning("Composer attachments: cannot touch %s", entry, exc_info=True)


def touch_upload_entry(meta: dict) -> None:
    """:func:`touch_entry` for the entry of a composer upload; nothing for another origin."""
    ref = composer_ref(meta)
    if ref is not None:
        touch_entry(ref)


def entry_upload_ids(ref: AttachmentRef) -> list[str]:
    """Ids of the non-terminal uploads whose ``origin`` is this entry."""
    key = origin_key(ref)
    return [
        meta["id"]
        for meta in store.list_metadata()
        if not store.is_terminal(meta["state"])
        and isinstance(meta["origin"], dict)
        and meta["origin"].get("panel") == COMPOSER_PANEL
        and meta["origin"].get("key") == key
    ]


# ── Settle rule ──


async def settle_entry_uploads(ref: AttachmentRef) -> None:
    """Settle every live upload of an entry (spec §6.1.1 settle rule).

    The caller holds :func:`composer_creation_guard`, so no new upload of the entry appears meanwhile.
    Each upload settles under its own lock (``_run_locked``), which first waits for any running
    finalization or recovery. A ``finalizing`` upload is recovered; an upload still non-terminal
    afterwards is cancelled. Raises :class:`SettleError` when an upload could not be settled: the
    caller must then neither reset nor remove the entry.
    """
    from twicc.uploads import views as upload_views
    from twicc.uploads.broadcast import broadcast_upload_state

    async def settle(meta: dict):
        upload_id = meta["id"]
        if meta["state"] == store.STATE_FINALIZING:
            await upload_views.recover_upload(upload_id)
            meta = await asyncio.to_thread(store.peek_metadata, upload_id)
            if meta is None:
                return upload_views._status(404)
        if store.is_terminal(meta["state"]):
            return upload_views._status(204)
        outcome = await asyncio.to_thread(store.cancel_upload, upload_id)
        if outcome.meta is not None:
            await broadcast_upload_state(outcome.meta)
        return upload_views._status(outcome.code)

    for upload_id in await asyncio.to_thread(entry_upload_ids, ref):
        response = await upload_views._run_locked(upload_id, settle, label="settle")
        if response.status_code not in _SETTLED_CODES:
            raise SettleError(upload_id, response.status_code)


# ── Release (spec §6.1.4) ──


def _write_release_tombstone(ref: AttachmentRef) -> None:
    """Create ``<bucket>/.released/<attachment_id>`` (and its parents), refusing symlinked parents."""
    marker = released_marker(ref)
    bucket = marker.parent.parent
    _require_plain_dir(bucket)
    _require_plain_dir(marker.parent)
    marker.parent.mkdir(parents=True, exist_ok=True)
    _require_plain_dir(marker.parent)  # re-checked: created meanwhile by someone else
    marker.touch()


def remove_entry_dir(ref: AttachmentRef) -> None:
    """Remove the entry directory; a symlinked entry is unlinked, never followed. Absent: nothing."""
    entry = staging.entry_dir(ref)
    _require_plain_dir(entry.parent)
    if entry.is_symlink() or (entry.exists() and not entry.is_dir()):
        entry.unlink()
    elif entry.exists():
        shutil.rmtree(entry)


async def _release_one(ref: AttachmentRef) -> None:
    async with composer_creation_guard():
        # The tombstone first: even when the settle fails, no creation can recreate the entry.
        await asyncio.to_thread(_write_release_tombstone, ref)
        await settle_entry_uploads(ref)
        await asyncio.to_thread(remove_entry_dir, ref)


async def release_refs(refs: tuple[AttachmentRef, ...]) -> None:
    """Release each entry: tombstone, settle of its uploads, removal of its directory.

    Each entry is released under the creation lock (so no composer creation interleaves), its
    uploads under their own locks. An absent entry still gets its tombstone. A promoted file in
    ``artifacts/`` is never touched. Every ref is attempted; the first error (a
    :class:`SettleError` leaves its entry in place) is raised once all were tried.
    """
    first_error: BaseException | None = None
    for ref in refs:
        try:
            await _release_one(staging.validate_ref(ref))
        except Exception as exc:
            logger.warning("Composer attachments: cannot release %s", ref, exc_info=True)
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


# ── Status (spec §6.1.2) ──

STATE_PROMOTED = "promoted"
STATE_READY = "ready"
STATE_UPLOADING = "uploading"
STATE_MISSING = "missing"


def _live_uploads_by_key() -> dict[str, dict]:
    """The newest non-terminal composer upload of each entry key."""
    live: dict[str, dict] = {}
    for meta in store.list_metadata():
        origin = meta["origin"]
        if store.is_terminal(meta["state"]) or not isinstance(origin, dict):
            continue
        if origin.get("panel") != COMPOSER_PANEL or not isinstance(origin.get("key"), str):
            continue
        current = live.get(origin["key"])
        if current is None or str(meta["created_at"]) > str(current["created_at"]):
            live[origin["key"]] = meta
    return live


def _upload_offset(meta: dict) -> int:
    if meta["state"] == store.STATE_FINALIZING:
        return meta["size"]
    part = store.part_size(meta["id"])
    return min(part, meta["size"]) if part is not None else meta["offset"]


def _entry_state(ref: AttachmentRef) -> str | None:
    """``promoted`` / ``ready`` from the markers, ``missing`` for a promoted entry whose file is gone,
    ``None`` when the markers say nothing (the uploads decide)."""
    try:
        loaded = staging.load_entry(ref)
    except staging.AttachmentError:
        entry = staging.entry_dir(ref)
        if os.path.lexists(entry / staging.PROMOTED_MARKER):
            return STATE_MISSING  # a missing promoted target wins over an old ready.json
        return None
    if loaded.promoted is None:
        return STATE_READY
    try:
        staging.content_location(loaded)
    except staging.AttachmentError:
        return STATE_MISSING
    return STATE_PROMOTED


def status_refs(refs: tuple[AttachmentRef, ...]) -> list[dict]:
    """The state of each entry, in the requested order (blocking).

    First match wins: ``promoted`` (its promoted file still exists), ``ready``, ``uploading`` (a
    non-terminal upload targets it; with its ``client_id`` and ``offset``), ``missing``.
    """
    refs = tuple(staging.validate_ref(ref) for ref in refs)
    live = _live_uploads_by_key() if refs else {}
    statuses = []
    for ref in refs:
        item = {"bucket": ref.bucket, "id": ref.id}
        state = _entry_state(ref)
        meta = live.get(origin_key(ref))
        if state is None and meta is not None:
            item.update(state=STATE_UPLOADING, client_id=meta["client_id"], offset=_upload_offset(meta))
        else:
            item["state"] = state or STATE_MISSING
        statuses.append(item)
    return statuses


# ── Heartbeat (spec §6.1.2 ``touch/``) ──

HOLDER_DRAFT = "draft"
HOLDER_SNAPSHOT = "snapshot"
HOLDERS = frozenset({HOLDER_DRAFT, HOLDER_SNAPSHOT})


def touch_refs(refs: tuple[AttachmentRef, ...], *, holder: str) -> None:
    """Refresh the mtime of each existing entry (blocking); never creates one.

    ``holder="draft"`` also removes ``committed.json``: the entry is a draft attachment again and
    gets the 30-day retention. ``holder="snapshot"`` only touches. ``ValueError`` for another holder.
    """
    if holder not in HOLDERS:
        raise ValueError(f"invalid holder: {holder!r}")
    for ref in refs:
        ref = staging.validate_ref(ref)
        entry = staging.entry_dir(ref)
        if holder == HOLDER_DRAFT and not entry.parent.is_symlink() and not entry.is_symlink() and entry.is_dir():
            try:
                (entry / staging.COMMITTED_MARKER).unlink(missing_ok=True)
            except OSError:
                logger.warning("Composer attachments: cannot remove the commit marker of %s", entry, exc_info=True)
        touch_entry(ref)
