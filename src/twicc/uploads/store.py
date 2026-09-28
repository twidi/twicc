"""Staging area and metadata of the browser file uploads.

Design: docs/plans/2026-09-28-file-upload-design.md (§5.1, §5.2, §5.8).

For each upload ``<id>`` (``uuid4().hex``) the staging dir
(:func:`twicc.paths.get_uploads_dir`) holds:

- ``<id>.part`` — the bytes received so far. While the upload is ``active``,
  its size is the offset (the source of truth).
- ``<id>.json`` — the metadata, written with :func:`atomic_write_json`.

Every function here is blocking file work: an async caller runs it in a worker
thread (``asyncio.to_thread``), under the upload's lock when the spec asks for
one. Nothing here takes a lock or broadcasts: the caller broadcasts the record
a successful write returns (:mod:`twicc.uploads.broadcast`).

Metadata write rule (§5.2): ``version`` comes only from the metadata on disk.
A write reads it and writes ``version + 1``. A write that fails raises and
changes nothing on disk, so it uses no version number and the caller has
nothing to broadcast.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import get_uploads_dir

logger = logging.getLogger(__name__)


# ── States ────────────────────────────────────────────────────────────────────

STATE_ACTIVE = "active"
STATE_FINALIZING = "finalizing"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

STATES = frozenset({STATE_ACTIVE, STATE_FINALIZING, STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED})
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED})


def is_terminal(state: str) -> bool:
    """True for ``completed``, ``failed`` and ``cancelled``."""
    return state in TERMINAL_STATES


# ── Length caps of the metadata fields (§5.2, §5.3 check 1-2) ────────────────

CLIENT_ID_MAX_LENGTH = 64
FINGERPRINT_MAX_LENGTH = 64
ORIGIN_KEY_MAX_LENGTH = 1024
FILENAME_MAX_BYTES = 240
ORIGIN_PANELS = frozenset({"files", "artifacts"})

# Prefix of the cross-filesystem temp file in the target dir (§5.6); reserved.
TEMP_FILE_PREFIX = ".twicc-upload-"

# A terminal upload's ``<id>.json`` stays as a tombstone this long, counted
# from its ``updated_at`` (§5.2, §5.3 ``GET``, §5.9).
TOMBSTONE_LIFETIME = timedelta(hours=24)


# ── Error numbers (§5.1) ──────────────────────────────────────────────────────

DISK_FULL_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})

# Errors that repeat on every attempt because of the target itself. ENOENT and
# ENOTDIR count only when the target dir is gone (see is_target_refusal).
_TARGET_REFUSAL_ERRNOS = frozenset(
    {errno.EACCES, errno.EROFS, errno.EINVAL, errno.EILSEQ, errno.ENAMETOOLONG, errno.EFBIG}
)
_TARGET_GONE_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR})


def is_disk_full(exc: BaseException) -> bool:
    """True when *exc* is "disk full": ``ENOSPC`` or ``EDQUOT``."""
    return isinstance(exc, OSError) and exc.errno in DISK_FULL_ERRNOS


def is_target_refusal(exc: BaseException, target_dir: str) -> bool:
    """True when *exc* is "target refuses" (§5.1).

    ``EACCES``, ``EROFS``, ``EINVAL``, ``EILSEQ``, ``ENAMETOOLONG``, ``EFBIG``;
    ``ENOENT`` / ``ENOTDIR`` only when *target_dir* is no longer a directory.
    An ``ENOENT`` while *target_dir* still exists concerns the source: it is an
    unexpected error, not a refusal.
    """
    if not isinstance(exc, OSError):
        return False
    if exc.errno in _TARGET_REFUSAL_ERRNOS:
        return True
    return exc.errno in _TARGET_GONE_ERRNOS and not os.path.isdir(target_dir)


def failure_code(exc: BaseException) -> int:
    """HTTP code of a failed step: ``507`` for disk full, ``500`` otherwise."""
    return 507 if is_disk_full(exc) else 500


# ── Staging dir, ids and file names ──────────────────────────────────────────

_ID_RE = re.compile(r"[0-9a-f]{32}")
_STAGING_NAME_RE = re.compile(r"(?P<id>[0-9a-f]{32})(?:(?P<json>\.json)|(?P<part>\.part)|(?P<tmp>\.json\..+\.tmp))")

NAME_KIND_JSON = "json"
NAME_KIND_PART = "part"
NAME_KIND_JSON_TMP = "json_tmp"


class StagingName(NamedTuple):
    """A staging-dir entry name that passes the name filter (§5.1)."""

    kind: str  # NAME_KIND_JSON, NAME_KIND_PART or NAME_KIND_JSON_TMP
    upload_id: str


def classify_staging_name(name: str) -> StagingName | None:
    """Apply the name filter of §5.1 to one staging-dir entry name.

    Only ``<32 hex>.json``, ``<32 hex>.part`` and ``<32 hex>.json.*.tmp`` (the
    :func:`atomic_write_json` temp files) are upload files; every other name
    gives ``None`` and is never touched.
    """
    match = _STAGING_NAME_RE.fullmatch(name)
    if match is None:
        return None
    if match["json"]:
        kind = NAME_KIND_JSON
    elif match["part"]:
        kind = NAME_KIND_PART
    else:
        kind = NAME_KIND_JSON_TMP
    return StagingName(kind, match["id"])


def is_valid_upload_id(value: object) -> bool:
    """True when *value* is 32 lowercase hex characters."""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def new_upload_id() -> str:
    """A new upload id (``uuid4().hex``)."""
    return uuid.uuid4().hex


def get_staging_dir() -> Path:
    """The staging dir, created (mode ``0o700``) when absent."""
    path = get_uploads_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _checked_id(upload_id: str) -> str:
    if not is_valid_upload_id(upload_id):
        raise ValueError(f"invalid upload id: {upload_id!r}")
    return upload_id


def part_path(upload_id: str) -> Path:
    """Path of ``<id>.part`` (the staging dir is created when absent)."""
    return get_staging_dir() / f"{_checked_id(upload_id)}.part"


def metadata_path(upload_id: str) -> Path:
    """Path of ``<id>.json`` (the staging dir is created when absent)."""
    return get_staging_dir() / f"{_checked_id(upload_id)}.json"


def part_size(upload_id: str) -> int | None:
    """Size of ``<id>.part``, or ``None`` when it does not exist."""
    try:
        return part_path(upload_id).stat().st_size
    except FileNotFoundError:
        return None


def is_in_staging_dir(path: str) -> bool:
    """True when *path* is the staging dir or inside it (§5.1).

    Compares ``os.path.realpath`` values, so a symlink to the staging dir does
    not pass. This is the only upload check that uses ``realpath``.
    """
    staging = os.path.realpath(get_uploads_dir())
    real = os.path.realpath(path)
    return real == staging or real.startswith(staging + os.sep)


def remove_best_effort(path: str | os.PathLike) -> None:
    """Remove a file after a terminal write (§5.2).

    ``ENOENT`` is ignored, other errors are logged and swallowed: the janitor
    removes the leftovers later.
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Upload cleanup: cannot remove %s", path, exc_info=True)


# ── Metadata ─────────────────────────────────────────────────────────────────


class UnparsableMetadataError(Exception):
    """``<id>.json`` exists but is not a valid upload metadata object."""


class UploadNotFoundError(Exception):
    """``<id>.json`` does not exist."""


# Fields a metadata update may change. The others are set at creation (or are
# computed by the write itself: version, updated_at, offset).
_UPDATABLE_FIELDS = frozenset(
    {
        "state",
        "final_path",
        "final_method",
        "final_source",
        "error",
        "finalize_error_code",
        "finalize_failed_at",
        "last_transfer_at",
    }
)
# Set together, cleared together by the next state change (§5.2).
_FINALIZE_ERROR_FIELDS = ("error", "finalize_error_code", "finalize_failed_at")
# Fields every metadata object must hold (written by create_metadata, indexed
# by build_record and by the later steps). A file missing one is unparsable.
_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "client_id",
        "state",
        "version",
        "size",
        "offset",
        "filename",
        "target_dir",
        "scope",
        "origin",
        "fingerprint",
        "created_at",
        "updated_at",
        "last_transfer_at",
    }
)


def now_iso() -> str:
    """Current time, ISO 8601, UTC."""
    return datetime.now(UTC).isoformat()


def read_metadata(upload_id: str) -> dict | None:
    """Read ``<id>.json``.

    Returns ``None`` when the file does not exist (or disappears during the
    read). Raises :class:`UnparsableMetadataError` when it is not valid JSON,
    not an object, lacks a coherent ``id`` / ``version`` / ``state``, or lacks
    one of the fields every metadata object holds (a power loss can truncate a
    write, §8).
    """
    path = metadata_path(upload_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise UnparsableMetadataError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UnparsableMetadataError(f"{path}: not a JSON object")
    version = data.get("version")
    if (
        data.get("id") != upload_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or data.get("state") not in STATES
        or not _REQUIRED_FIELDS <= data.keys()
    ):
        raise UnparsableMetadataError(f"{path}: not an upload metadata object")
    return data


def scan_staging() -> Iterator[tuple[StagingName, os.DirEntry]]:
    """Yield ``(StagingName, DirEntry)`` for every staging-dir entry that
    passes the name filter of §5.1 (``.json``, ``.part``, ``.json.*.tmp``).

    Entries of any other name are never yielded. The entry type is not
    checked: the caller decides what to do with it.
    """
    with os.scandir(get_staging_dir()) as entries:
        for entry in entries:
            name = classify_staging_name(entry.name)
            if name is not None:
                yield name, entry


def list_metadata() -> list[dict]:
    """Every parsable ``<id>.json`` of the staging dir.

    Applies the name filter of §5.1, skips a ``.json`` that cannot be parsed or
    that disappears between the listing and the read.
    """
    records = []
    for name, entry in scan_staging():
        if name.kind != NAME_KIND_JSON:
            continue
        try:
            meta = read_metadata(name.upload_id)
        except UnparsableMetadataError:
            continue
        except OSError:
            logger.warning("Uploads: cannot read %s", entry.path, exc_info=True)
            continue
        if meta is not None:
            records.append(meta)
    return records


def create_metadata(
    upload_id: str,
    *,
    client_id: str,
    size: int,
    filename: str,
    target_dir: str,
    scope: dict,
    origin: dict,
    fingerprint: str,
) -> dict:
    """Write the first metadata of a new upload (``active``, version 1, offset 0).

    ``<id>.part`` must already exist (creation writes it first, §5.2) and
    ``<id>.json`` must not. Returns the persisted metadata. A failed write
    raises and leaves no ``<id>.json``.
    """
    meta_file = metadata_path(upload_id)
    if not part_path(upload_id).exists():
        raise FileNotFoundError(f"{upload_id}.part must exist before {upload_id}.json")
    if meta_file.exists():
        raise FileExistsError(f"{meta_file} already exists")
    now = now_iso()
    meta = {
        "id": upload_id,
        "client_id": client_id,
        "state": STATE_ACTIVE,
        "version": 1,
        "size": size,
        "offset": 0,
        "filename": filename,
        "target_dir": target_dir,
        "scope": scope,
        "origin": origin,
        "fingerprint": fingerprint,
        "final_path": None,
        "final_method": None,
        "final_source": None,
        "error": None,
        "finalize_error_code": None,
        "finalize_failed_at": None,
        "created_at": now,
        "updated_at": now,
        "last_transfer_at": now,
    }
    atomic_write_json(meta_file, meta)
    return meta


def create_upload(upload_id: str, **fields: object) -> dict:
    """Create a new upload on disk: ``<id>.part`` first, then ``<id>.json`` (§5.2).

    *fields* are the keyword arguments of :func:`create_metadata`. The part
    file is created with ``open(path, "xb")`` (mode set by the umask). A
    failure removes only what this call created: a ``.part`` that already
    exists (``FileExistsError``) is left untouched, and a failed
    :func:`create_metadata` never leaves a ``.json`` of its own (it refuses an
    existing one, and ``atomic_write_json`` leaves the path untouched on
    failure), so only this call's ``.part`` is removed. The error is
    re-raised. Returns the persisted metadata.
    """
    part = part_path(upload_id)
    with open(part, "xb"):
        pass
    try:
        return create_metadata(upload_id, **fields)
    except BaseException:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Upload creation: cannot remove %s", part, exc_info=True)
        raise


def update_metadata(upload_id: str, **changes: object) -> dict:
    """Write the metadata of an existing upload with *changes*; return it.

    The one rule for every write (§5.2):

    - the current metadata is read from disk; the new ``version`` is its
      ``version + 1`` and ``updated_at`` is now;
    - a change of ``state`` clears ``error``, ``finalize_error_code`` and
      ``finalize_failed_at``, unless *changes* sets them;
    - when the resulting state is not terminal, ``offset`` is set to the
      current size of ``<id>.part`` (kept as is when the file is missing); a
      terminal write keeps the last ``offset``.

    Only the fields of ``_UPDATABLE_FIELDS`` can be changed, and
    ``finalize_error_code`` / ``finalize_failed_at`` never exist without
    ``error`` (``ValueError`` otherwise, nothing written). Raises :class:`UploadNotFoundError` when ``<id>.json`` is
    absent, :class:`UnparsableMetadataError` when it is unparsable, and the
    ``OSError`` of a failed write — which leaves the file on disk unchanged,
    so the caller must not broadcast.
    """
    unknown = set(changes) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"not updatable upload metadata fields: {sorted(unknown)}")
    if "state" in changes and changes["state"] not in STATES:
        raise ValueError(f"invalid upload state: {changes['state']!r}")

    current = read_metadata(upload_id)
    if current is None:
        raise UploadNotFoundError(upload_id)

    meta = dict(current)
    if "state" in changes and changes["state"] != current["state"]:
        for field in _FINALIZE_ERROR_FIELDS:
            meta[field] = None
    meta.update(changes)
    if meta.get("error") is None and (
        meta.get("finalize_error_code") is not None or meta.get("finalize_failed_at") is not None
    ):
        raise ValueError("finalize_error_code and finalize_failed_at need error (set together, §5.2)")
    meta["version"] = current["version"] + 1
    meta["updated_at"] = now_iso()
    if not is_terminal(meta["state"]):
        size = part_size(upload_id)
        if size is not None:
            meta["offset"] = size

    atomic_write_json(metadata_path(upload_id), meta)
    return meta


# ── Record (§5.8) ─────────────────────────────────────────────────────────────


def build_record(meta: dict) -> dict:
    """The public record of an upload: the ``POST`` answer, each ``GET`` item
    and the ``upload_state`` payload.

    ``scope``, ``final_method`` and ``final_source`` are never sent;
    ``final_path`` is sent only for ``completed``.
    """
    return {
        "id": meta["id"],
        "client_id": meta["client_id"],
        "state": meta["state"],
        "version": meta["version"],
        "filename": meta["filename"],
        "target_dir": meta["target_dir"],
        "size": meta["size"],
        "offset": meta["offset"],
        "origin": meta["origin"],
        "fingerprint": meta["fingerprint"],
        "final_path": meta.get("final_path") if meta["state"] == STATE_COMPLETED else None,
        "error": meta.get("error"),
        "created_at": meta["created_at"],
        "updated_at": meta["updated_at"],
    }
