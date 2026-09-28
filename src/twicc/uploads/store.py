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
import shutil
import stat
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
    except (FileNotFoundError, NotADirectoryError):
        # NotADirectoryError: a parent is gone (a removed target dir), so the
        # file is gone too.
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


def peek_metadata(upload_id: str) -> dict | None:
    """Lock-free read of an ``<id>`` route (§5.3): the metadata, or ``None``.

    ``None`` for an invalid id, a missing ``<id>.json`` or an unparsable one:
    the route answers ``404`` before any lock or path work. The staging dir is
    never created here.
    """
    if not is_valid_upload_id(upload_id):
        return None
    if not (get_uploads_dir() / f"{upload_id}.json").is_file():
        return None
    try:
        return read_metadata(upload_id)
    except UnparsableMetadataError:
        return None


# ── Steps of the tus routes (§5.3) ───────────────────────────────────────────
#
# Blocking work of one guarded operation (§5.4): the caller holds the upload's
# lock, runs the function in a worker thread and broadcasts the metadata it
# returns. Every failure rule runs here, inside the thread.


class WriteOutcome(NamedTuple):
    """Result of a step that writes the metadata."""

    meta: dict | None  # the persisted metadata to broadcast (None: nothing written)
    code: int  # the HTTP answer of the step


# Size of one read of the request body during an append.
APPEND_BLOCK_SIZE = 1024 * 1024

APPEND_OK = "ok"  # the body was fully appended
APPEND_EXCESS = "excess"  # the body holds more than ``size - offset``: truncated back, 400
APPEND_READ_ERROR = "read_error"  # reading the body failed (closed after a disconnect, short read)
APPEND_DISK_FULL = "disk_full"  # writing failed: disk full
APPEND_WRITE_ERROR = "write_error"  # writing failed: any other error


class AppendResult(NamedTuple):
    """Result of :func:`append_chunk`."""

    outcome: str  # one of the APPEND_* values
    part_size: int | None  # real size of ``<id>.part`` after the append (None: missing)
    meta: dict | None  # metadata persisted by the offset sync (None: no write, or a failed one)


def _write_block(file, data: bytes) -> None:
    """Write all of *data* to an unbuffered binary file."""
    view = memoryview(data)
    while view:
        written = file.write(view)
        view = view[written:]


def append_chunk(
    upload_id: str,
    read,
    *,
    start_offset: int,
    size: int,
    meta_offset: int,
    declared_length: int | None,
) -> AppendResult:
    """Append one ``PATCH`` body to ``<id>.part``, then sync the metadata (§5.3).

    *read* is the request's ``read(n)``: the body is read in blocks, never as a
    whole. At most ``size - start_offset`` bytes are accepted, whatever
    *declared_length* (``Content-Length``) says: when the body holds more,
    ``<id>.part`` is truncated back to *start_offset* (:data:`APPEND_EXCESS`).
    A read error (``ValueError`` / ``OSError``: Django closed the body file
    after a disconnect) or a body shorter than *declared_length* stops the
    append; the bytes already written stay.

    Then the metadata ``offset`` is synced to the real size of ``<id>.part``
    when it differs from *meta_offset* (the offset on disk before the append),
    with ``last_transfer_at`` when bytes were added. A failure of this sync is
    logged and ignored: the ``.part`` size stays the truth.
    """
    remaining = size - start_offset
    accepted = 0
    received = 0
    outcome = APPEND_OK
    part = part_path(upload_id)
    try:
        file = open(part, "ab", buffering=0)
    except OSError as exc:
        logger.warning("Upload %s: cannot open %s for append", upload_id, part, exc_info=True)
        outcome = APPEND_DISK_FULL if is_disk_full(exc) else APPEND_WRITE_ERROR
    else:
        with file:
            while True:
                want = remaining - accepted
                try:
                    # Once every accepted byte is in, read one more byte: any
                    # further byte is an excess.
                    block = read(min(APPEND_BLOCK_SIZE, want) if want > 0 else 1)
                except (ValueError, OSError):
                    logger.info("Upload %s: request body unreadable after %d bytes", upload_id, received)
                    outcome = APPEND_READ_ERROR
                    break
                if not block:
                    if declared_length is not None and received < declared_length:
                        logger.info(
                            "Upload %s: short request body (%d of %d bytes)", upload_id, received, declared_length
                        )
                        outcome = APPEND_READ_ERROR
                    break
                received += len(block)
                if want <= 0:
                    outcome = APPEND_EXCESS
                    break
                try:
                    _write_block(file, block)
                except OSError as exc:
                    logger.warning("Upload %s: append failed", upload_id, exc_info=True)
                    outcome = APPEND_DISK_FULL if is_disk_full(exc) else APPEND_WRITE_ERROR
                    break
                accepted += len(block)

    if outcome == APPEND_EXCESS:
        try:
            os.truncate(part, start_offset)
        except OSError:
            logger.warning("Upload %s: cannot truncate %s back to %d", upload_id, part, start_offset, exc_info=True)

    real_size = part_size(upload_id)
    meta = None
    if real_size is not None and real_size != meta_offset:
        changes = {"last_transfer_at": now_iso()} if real_size > start_offset else {}
        try:
            meta = update_metadata(upload_id, **changes)
        except Exception:
            logger.warning("Upload %s: offset sync after append failed", upload_id, exc_info=True)
    return AppendResult(outcome, real_size, meta)


def mark_staging_lost(upload_id: str) -> WriteOutcome:
    """``active`` whose ``<id>.part`` is missing → ``failed`` (``"staging file lost"``).

    Answer ``410`` after the write; a failed write answers its own code
    (``507`` for disk full, else ``500``) and writes nothing.
    """
    try:
        meta = update_metadata(upload_id, state=STATE_FAILED, error="staging file lost")
    except Exception as exc:
        logger.warning("Upload %s: cannot write the 'failed' state", upload_id, exc_info=True)
        return WriteOutcome(None, failure_code(exc))
    return WriteOutcome(meta, 410)


def cancel_upload(upload_id: str) -> WriteOutcome:
    """tus termination of a non-terminal, non-``finalizing`` upload (§5.3 ``DELETE``).

    Writes ``cancelled`` first, then removes ``<id>.part`` (best effort):
    ``204``. On a disk-full write: removes ``<id>.part`` first, then writes
    ``cancelled`` again; if that write fails too, nothing to broadcast and
    ``507``, whatever the second error (§5.3). Any other failed first write:
    ``500``, nothing removed.
    """
    try:
        meta = update_metadata(upload_id, state=STATE_CANCELLED)
    except Exception as exc:
        logger.warning("Upload %s: cannot write the 'cancelled' state", upload_id, exc_info=True)
        if not is_disk_full(exc):
            return WriteOutcome(None, failure_code(exc))
        remove_best_effort(part_path(upload_id))
        try:
            meta = update_metadata(upload_id, state=STATE_CANCELLED)
        except Exception:
            logger.warning("Upload %s: cannot write the 'cancelled' state again", upload_id, exc_info=True)
            return WriteOutcome(None, 507)
        return WriteOutcome(meta, 204)
    remove_best_effort(part_path(upload_id))
    return WriteOutcome(meta, 204)


# ── Finalization (§5.6) ──────────────────────────────────────────────────────
#
# The worker-thread part of the finalization step: every file and metadata
# step of §5.6 step 4, and every failure rule. The coroutine side (the
# re-validation, the ``finalizing`` write, the "finalizing now" set and the
# broadcasts) lives in :mod:`twicc.uploads.views`.

FINAL_METHOD_LINK = "link"
FINAL_METHOD_REPLACE = "replace"
FINAL_SOURCE_PART = "part"
FINAL_SOURCE_TMP = "tmp"

# Candidates after the requested name: ``stem (1).ext`` … ``stem (999).ext``.
MAX_NAME_SUFFIX = 999
# Free space a finalization needs for its metadata writes, and for a link.
FINALIZE_SPACE_MARGIN = 1024 * 1024
COPY_BLOCK_SIZE = 1024 * 1024

# ``os.link`` errors of a filesystem without hard-link support (§5.6 step 4.4).
_NO_HARD_LINK_ERRNOS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS})
# Mode argument of the temp file and of the reservation: the umask applies, so
# they end with the same mode as ``<id>.part`` (created with ``open(.., "xb")``).
_NEW_FILE_MODE = 0o666
_NEW_FILE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY

ERROR_STAGING_LOST = "staging file lost"


def temp_file_path(upload_id: str, target_dir: str) -> str:
    """``<target_dir>/.twicc-upload-<id>.tmp``: the cross-filesystem copy (§5.6)."""
    return os.path.join(target_dir, f"{TEMP_FILE_PREFIX}{_checked_id(upload_id)}.tmp")


def is_upload_temp_name(name: str) -> bool:
    """True for a base name matching ``.twicc-upload-*.tmp``."""
    return name.startswith(TEMP_FILE_PREFIX) and name.endswith(".tmp")


def candidate_names(filename: str) -> Iterator[str]:
    """The free-name candidates: *filename*, then ``stem (1).ext`` … ``stem (999).ext``.

    ``ext`` is the last suffix only (``archive.tar.gz`` → ``archive.tar (1).gz``);
    a dot-file without another dot has no extension (``.env`` → ``.env (1)``).
    """
    yield filename
    dot = filename.rfind(".")
    stem, ext = (filename[:dot], filename[dot:]) if dot > 0 else (filename, "")
    for number in range(1, MAX_NAME_SUFFIX + 1):
        yield f"{stem} ({number}){ext}"


def st_dev(path: str | os.PathLike) -> int:
    """``st_dev`` of *path*: the one filesystem-identity check of the uploads
    (creation check 5, finalization, the ``HEAD`` gate); tests fake it."""
    return os.stat(path).st_dev


def _free_bytes(path: str | os.PathLike) -> int:
    """Free bytes of the filesystem of *path*."""
    return shutil.disk_usage(path).free


def _file_size(path: str) -> int | None:
    """``st_size`` of *path*, or ``None`` when it (or a parent) does not exist."""
    try:
        return os.stat(path).st_size
    except (FileNotFoundError, NotADirectoryError):
        return None


def _remove_if_empty(path: str) -> None:
    """Remove *path* when it is an empty regular file (our reservation), best effort."""
    try:
        st = os.lstat(path)
    except OSError:
        return
    if st.st_size == 0 and stat.S_ISREG(st.st_mode):
        remove_best_effort(path)


def _os_error_message(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.errno is not None:
        return os.strerror(exc.errno)
    return "Unexpected error"


class FinalizeResult(NamedTuple):
    """Result of the worker-thread part of a finalization step."""

    meta: dict  # the metadata on disk after the step
    code: int  # 204 completed, 422 failed, else 507 (disk full) / 500
    broadcast: bool  # *meta* was just persisted with a new state: broadcast it


class _PreCommitFailure(Exception):
    """A failure before the commit point (§5.6): no link and no reservation exist."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _mapped_failure(exc: BaseException, target_dir: str) -> _PreCommitFailure:
    """Map an error of the copy, the link loop or the reserve loop (§5.6 step 4.4)."""
    if is_target_refusal(exc, target_dir):
        if not os.path.isdir(target_dir):
            return _PreCommitFailure(422, "The target directory no longer exists")
        return _PreCommitFailure(422, f"The target directory refuses the file: {_os_error_message(exc)}")
    if is_disk_full(exc):
        return _PreCommitFailure(507, "Not enough disk space in the target directory")
    return _PreCommitFailure(500, f"Unexpected error: {_os_error_message(exc)}")


class _Placement(NamedTuple):
    final_path: str
    method: str  # FINAL_METHOD_LINK or FINAL_METHOD_REPLACE
    source_kind: str  # FINAL_SOURCE_PART or FINAL_SOURCE_TMP
    source: str  # path of the source file


class _CrossDevice(Exception):
    """``os.link`` raised ``EXDEV``."""


class _NoHardLinks(Exception):
    """``os.link`` raised an error of a filesystem without hard links."""


def _copy_to_temp(part: str, tmp: str, target_dir: str, size: int) -> None:
    """Step 4.3, other filesystem: copy ``<id>.part`` into a new temp file, ``fsync`` it."""
    try:
        free = _free_bytes(target_dir)
    except OSError as exc:
        raise _mapped_failure(exc, target_dir) from exc
    if free < size:
        raise _PreCommitFailure(507, "Not enough disk space in the target directory")
    # Never write through a leftover: it may be hard-linked to a final name.
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _mapped_failure(exc, target_dir) from exc
    try:
        source = open(part, "rb")
    except OSError as exc:
        # The source, not the target: disk full or unexpected, never a refusal.
        raise _PreCommitFailure(failure_code(exc), f"Cannot read the staging file: {_os_error_message(exc)}") from exc
    with source:
        try:
            fd = os.open(tmp, _NEW_FILE_FLAGS, _NEW_FILE_MODE)
        except OSError as exc:
            raise _mapped_failure(exc, target_dir) from exc
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(source, out, COPY_BLOCK_SIZE)
                out.flush()
                os.fsync(out.fileno())
        except OSError as exc:
            raise _mapped_failure(exc, target_dir) from exc


def _link_loop(source: str, target_dir: str, names: list[str]) -> str:
    """Step 4.4: ``os.link`` the source to the first free candidate; return its path."""
    for name in names:
        candidate = os.path.join(target_dir, name)
        try:
            os.link(source, candidate)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise _CrossDevice from exc
            if exc.errno in _NO_HARD_LINK_ERRNOS:
                raise _NoHardLinks from exc
            raise _mapped_failure(exc, target_dir) from exc
        return candidate
    raise _PreCommitFailure(422, "No free file name in the target directory")


def _reserve_loop(target_dir: str, names: list[str]) -> str:
    """Step 4.4, no hard links: create the first free candidate empty; return its path."""
    for name in names:
        candidate = os.path.join(target_dir, name)
        try:
            fd = os.open(candidate, _NEW_FILE_FLAGS, _NEW_FILE_MODE)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno == errno.EPERM:
                raise _PreCommitFailure(422, "The target directory refuses the file") from exc
            raise _mapped_failure(exc, target_dir) from exc
        try:
            os.close(fd)
        except OSError:
            logger.warning("Upload finalization: cannot close the reservation %s", candidate, exc_info=True)
        return candidate
    raise _PreCommitFailure(422, "No free file name in the target directory")


def _place(upload_id: str, meta: dict) -> _Placement:
    """Steps 4.1-4.4: ``fsync``, candidates, source, link or reserve loop.

    Returns the placement once a link or a reservation exists. Raises
    :class:`_PreCommitFailure` before that; then no link and no reservation
    exist.
    """
    target_dir = meta["target_dir"]
    size = meta["size"]
    part = str(part_path(upload_id))
    tmp = temp_file_path(upload_id, target_dir)

    try:
        fd = os.open(part, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise _PreCommitFailure(failure_code(exc), f"Cannot sync the staging file: {_os_error_message(exc)}") from exc

    names = list(candidate_names(meta["filename"]))
    try:
        same_filesystem = st_dev(part) == st_dev(target_dir)
    except OSError as exc:
        raise _mapped_failure(exc, target_dir) from exc

    if same_filesystem:
        source, source_kind = part, FINAL_SOURCE_PART
    else:
        _copy_to_temp(part, tmp, target_dir, size)
        source, source_kind = tmp, FINAL_SOURCE_TMP

    while True:
        try:
            final_path = _link_loop(source, target_dir, names)
        except _CrossDevice:
            if source_kind == FINAL_SOURCE_TMP:
                raise _PreCommitFailure(500, "Unexpected error: cross-device link inside the target directory")
            # Two mounts of one filesystem can share ``st_dev``: copy instead.
            _copy_to_temp(part, tmp, target_dir, size)
            source, source_kind = tmp, FINAL_SOURCE_TMP
            continue
        except _NoHardLinks:
            final_path = _reserve_loop(target_dir, names)
            return _Placement(final_path, FINAL_METHOD_REPLACE, source_kind, source)
        return _Placement(final_path, FINAL_METHOD_LINK, source_kind, source)


def finalize_precommit_failure(upload_id: str, current: dict, code: int, message: str) -> FinalizeResult:
    """The pre-commit failure rule of §5.6 (no link and no reservation exist).

    Removes the temp file. Then:

    - *code* ``422`` → writes ``failed`` with ``error``, removes ``<id>.part``;
    - ``507`` or ``500`` → keeps ``<id>.part``, writes ``active`` with
      ``error``, ``finalize_error_code`` and ``finalize_failed_at``.

    A failed write leaves the state on disk as it was (*current*) and answers
    ``507`` when the write or the original failure is disk full, else ``500``
    (a ``422`` whose ``failed`` write fails answers the write's own code).
    """
    remove_best_effort(temp_file_path(upload_id, current["target_dir"]))
    if code == 422:
        try:
            meta = update_metadata(upload_id, state=STATE_FAILED, error=message)
        except Exception as exc:
            logger.warning("Upload %s: cannot write the 'failed' state", upload_id, exc_info=True)
            return FinalizeResult(current, failure_code(exc), False)
        remove_best_effort(part_path(upload_id))
        return FinalizeResult(meta, 422, True)
    try:
        meta = update_metadata(
            upload_id,
            state=STATE_ACTIVE,
            error=message,
            finalize_error_code=code,
            finalize_failed_at=now_iso(),
        )
    except Exception as exc:
        logger.warning("Upload %s: cannot write the finalization failure", upload_id, exc_info=True)
        return FinalizeResult(current, 507 if code == 507 or is_disk_full(exc) else 500, False)
    return FinalizeResult(meta, code, True)


def finalize_files(upload_id: str, current: dict) -> FinalizeResult:
    """Worker-thread part of the finalization (§5.6 step 4), with its failure rules.

    *current* is the ``finalizing`` metadata the coroutine just wrote. The
    caller holds the upload's lock and broadcasts the result when
    ``broadcast`` is true.
    """
    try:
        placement = _place(upload_id, current)
    except _PreCommitFailure as failure:
        logger.warning("Upload %s: finalization failed before the commit point: %s", upload_id, failure.message)
        return finalize_precommit_failure(upload_id, current, failure.code, failure.message)
    except Exception as exc:
        logger.exception("Upload %s: unexpected finalization error", upload_id)
        return finalize_precommit_failure(upload_id, current, 500, f"Unexpected error: {exc}")

    # 4.5 Commit point: the final name exists.
    changes = {"final_path": placement.final_path, "final_method": placement.method}
    if placement.method == FINAL_METHOD_REPLACE:
        changes["final_source"] = placement.source_kind
    try:
        current = update_metadata(upload_id, **changes)
    except Exception as exc:
        logger.warning("Upload %s: commit write failed", upload_id, exc_info=True)
        code = failure_code(exc)
        if placement.method == FINAL_METHOD_LINK:
            # Keep the link: the state stays ``finalizing``; recovery finds it by inode.
            return FinalizeResult(current, code, False)
        # The reservation name is known here: remove it, then the pre-commit rule.
        remove_best_effort(placement.final_path)
        message = "Not enough disk space" if code == 507 else f"Unexpected error: {_os_error_message(exc)}"
        return finalize_precommit_failure(upload_id, current, code, message)

    # 4.6 ``replace`` only.
    if placement.method == FINAL_METHOD_REPLACE:
        try:
            os.replace(placement.source, placement.final_path)
        except OSError as exc:
            logger.warning("Upload %s: replace into %s failed", upload_id, placement.final_path, exc_info=True)
            return FinalizeResult(current, failure_code(exc), False)

    # 4.7 ``completed``, then the best-effort removals.
    try:
        current = update_metadata(upload_id, state=STATE_COMPLETED)
    except Exception as exc:
        logger.warning("Upload %s: cannot write the 'completed' state", upload_id, exc_info=True)
        return FinalizeResult(current, failure_code(exc), False)
    remove_best_effort(part_path(upload_id))
    remove_best_effort(temp_file_path(upload_id, current["target_dir"]))
    return FinalizeResult(current, 204, True)


def finalize_space_available(meta: dict) -> bool:
    """The ``HEAD`` ``507`` gate (§5.3): enough free space to finalize again?

    1 MiB on the staging filesystem (metadata writes), plus ``size`` bytes on
    the target filesystem when a copy is needed (other filesystem), or 1 MiB
    on it for a link. When ``target_dir`` (or ``<id>.part``) cannot be
    stat'ed, the gate is skipped (``True``): recovery settles the upload.
    """
    target_dir = meta["target_dir"]
    # The target first: when it cannot be stat'ed, skip the whole gate.
    try:
        target_dev = st_dev(target_dir)
        target_free = _free_bytes(target_dir)
        same_filesystem = st_dev(part_path(meta["id"])) == target_dev
    except OSError:
        return True
    if _free_bytes(get_staging_dir()) < FINALIZE_SPACE_MARGIN:
        return False
    return target_free >= (FINALIZE_SPACE_MARGIN if same_filesystem else meta["size"])


# ── Recovery (§5.7) ──────────────────────────────────────────────────────────

RECOVERY_DONE = "done"  # settled; broadcast ``meta`` when ``broadcast`` is true
RECOVERY_ANSWER = "answer"  # not settled: answer ``code``
RECOVERY_FINALIZE = "finalize"  # the coroutine runs finalization (§5.6) from its step 1


class RecoveryVerdict(NamedTuple):
    """Result of the worker-thread part of a recovery step."""

    kind: str  # RECOVERY_DONE, RECOVERY_ANSWER or RECOVERY_FINALIZE
    meta: dict | None  # the metadata on disk after the thread work (None: absent)
    code: int  # the answer for RECOVERY_ANSWER (404, 507, 500); 200 otherwise
    broadcast: bool  # *meta* was just persisted with a new state: broadcast it


def _settle(upload_id: str, current: dict, cleanup: tuple[str, ...], **changes: object) -> RecoveryVerdict:
    """Write *changes*; on success remove *cleanup* (best effort) and answer *done*."""
    try:
        meta = update_metadata(upload_id, **changes)
    except Exception as exc:
        logger.warning("Upload %s: recovery write failed", upload_id, exc_info=True)
        return RecoveryVerdict(RECOVERY_ANSWER, current, failure_code(exc), False)
    for path in cleanup:
        remove_best_effort(path)
    return RecoveryVerdict(RECOVERY_DONE, meta, 200, True)


def _scan_for_inode(target_dir: str, inodes: set[tuple[int, int]]) -> str | None:
    """The entry of *target_dir* with one of *inodes*, skipping ``.twicc-upload-*.tmp``.

    A scan error (the target is gone or unreadable) counts as "not found".
    """
    try:
        with os.scandir(target_dir) as entries:
            for entry in entries:
                if is_upload_temp_name(entry.name):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) in inodes:
                    return os.path.join(target_dir, entry.name)
    except OSError:
        return None
    return None


def _recover_committed(upload_id: str, meta: dict, part: str, tmp: str) -> RecoveryVerdict:
    """Recovery case 2: ``final_path`` is set."""
    final_path = meta["final_path"]
    size = meta["size"]
    cleanup = (part, tmp)
    if meta.get("final_method") == FINAL_METHOD_LINK:
        return _settle(upload_id, meta, cleanup, state=STATE_COMPLETED)

    # ``replace``. ``lstat``: only a regular file at ``final_path`` counts
    # (a symlink placed at the reserved name is never taken for ours).
    try:
        final_st = os.lstat(final_path)
    except (FileNotFoundError, NotADirectoryError):
        final_st = None
    final_is_file = final_st is not None and stat.S_ISREG(final_st.st_mode)
    if final_is_file and final_st.st_size == size:
        return _settle(upload_id, meta, cleanup, state=STATE_COMPLETED)
    source = tmp if meta.get("final_source") == FINAL_SOURCE_TMP else part
    if _file_size(source) != size:
        _remove_if_empty(final_path)
        return _settle(upload_id, meta, cleanup, state=STATE_FAILED, error=ERROR_STAGING_LOST)
    # ``final_path`` absent, or empty (our reservation).
    if final_st is None or (final_is_file and final_st.st_size == 0):
        try:
            os.replace(source, final_path)
        except OSError as exc:
            logger.warning("Upload %s: recovery replace into %s failed", upload_id, final_path, exc_info=True)
            if not is_target_refusal(exc, meta["target_dir"]):
                return RecoveryVerdict(RECOVERY_ANSWER, meta, failure_code(exc), False)
            verdict = _settle(
                upload_id,
                meta,
                cleanup,
                state=STATE_FAILED,
                error=f"The target directory refuses the file: {_os_error_message(exc)}",
            )
            if verdict.kind == RECOVERY_DONE:
                _remove_if_empty(final_path)
            return verdict
        return _settle(upload_id, meta, cleanup, state=STATE_COMPLETED)

    # ``final_path`` holds other content: never overwrite it; finalize again
    # from ``<id>.part``, which picks a new free name.
    try:
        meta = update_metadata(upload_id, final_path=None, final_method=None, final_source=None)
    except Exception as exc:
        logger.warning("Upload %s: cannot clear the final path", upload_id, exc_info=True)
        return RecoveryVerdict(RECOVERY_ANSWER, meta, failure_code(exc), False)
    remove_best_effort(tmp)
    return RecoveryVerdict(RECOVERY_FINALIZE, meta, 200, False)


def _recover_uncommitted(upload_id: str, meta: dict, part: str, tmp: str) -> RecoveryVerdict:
    """Recovery case 3: ``final_path`` is not set."""
    target_dir = meta["target_dir"]
    inodes = set()
    for path in (part, tmp):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_nlink > 1:
            inodes.add((st.st_dev, st.st_ino))
    if inodes:
        found = _scan_for_inode(target_dir, inodes)
        if found is not None:
            return _settle(
                upload_id,
                meta,
                (part, tmp),
                state=STATE_COMPLETED,
                final_path=found,
                final_method=FINAL_METHOD_LINK,
            )

    remove_best_effort(tmp)
    received = part_size(upload_id)
    if received is None:
        return _settle(upload_id, meta, (), state=STATE_FAILED, error=ERROR_STAGING_LOST)
    if received >= meta["size"]:
        return RecoveryVerdict(RECOVERY_FINALIZE, meta, 200, False)
    if meta["state"] == STATE_FINALIZING:
        return _settle(upload_id, meta, (), state=STATE_ACTIVE)
    return RecoveryVerdict(RECOVERY_DONE, meta, 200, False)


def recover_files(upload_id: str) -> RecoveryVerdict:
    """Worker-thread part of the recovery step (§5.7).

    Re-reads the metadata (the caller holds the upload's lock), runs every
    file and metadata step of the matching case and returns a verdict. For
    :data:`RECOVERY_FINALIZE`, every metadata write the verdict needs is
    already done. An unexpected exception answers ``507`` / ``500`` and leaves
    the state as it is.
    """
    meta = peek_metadata(upload_id)
    if meta is None:
        return RecoveryVerdict(RECOVERY_ANSWER, None, 404, False)
    try:
        part = str(part_path(upload_id))
        tmp = temp_file_path(upload_id, meta["target_dir"])
        if is_terminal(meta["state"]):
            remove_best_effort(part)
            remove_best_effort(tmp)
            return RecoveryVerdict(RECOVERY_DONE, meta, 200, False)
        if meta.get("final_path"):
            return _recover_committed(upload_id, meta, part, tmp)
        return _recover_uncommitted(upload_id, meta, part, tmp)
    except Exception as exc:
        logger.exception("Upload %s: unexpected recovery error", upload_id)
        return RecoveryVerdict(RECOVERY_ANSWER, meta, failure_code(exc), False)


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
