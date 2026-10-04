"""Staging store of composer attachments: identity, names, durable markers, ready loading.

Layout (``paths.get_composer_attachments_dir()``): ``<bucket>/<attachment_id>/`` holding
``file/<filename>``, ``ready.json``, ``committed.json`` and ``promoted.json``.
Every path is built from a validated ref and a sanitized file name, and real paths are
checked before any content access.
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.
"""

import logging
import os
import re
import uuid
from pathlib import Path

import orjson

from twicc.core.services.attachments.types import AttachmentRef, PromotedEntry, StagedEntry
from twicc.paths import get_composer_attachments_dir
from twicc.uploads.store import TEMP_FILE_PREFIX

logger = logging.getLogger(__name__)

__all__ = [
    "ERROR_MISSING",
    "ERROR_NOT_READY",
    "AttachmentError",
    "entry_dir",
    "get_composer_attachments_dir",
    "load_entry",
    "normalize_filename",
    "on_upload_completed",
    "validate_ref",
    "write_marker",
]

ERROR_INVALID_REF = "invalid_attachment_ref"
ERROR_NOT_READY = "attachment_not_ready"
ERROR_MISSING = "attachment_missing"

FALLBACK_NAME = "attachment"
FILE_DIR = "file"
READY_MARKER = "ready.json"
COMMITTED_MARKER = "committed.json"
PROMOTED_MARKER = "promoted.json"
RELEASED_DIR = ".released"

# Control characters (U+0000-U+001F, U+007F), line/paragraph separators, and the path separators.
_FORBIDDEN_CHARS = re.compile("[\x00-\x1f\x7f\x85  /\\\\]")


class AttachmentError(Exception):
    """A business error of the staging store, with a stable machine ``code``."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


# ── Identity ──


def _validate_key(value: object, what: str) -> str:
    """Same rules as the session ids of ``agent/work_dirs.py``."""
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise AttachmentError(ERROR_INVALID_REF, f"Invalid attachment {what}")
    return value


def validate_ref(raw: object) -> AttachmentRef:
    """Validate a client-provided ``{bucket, id}`` and return it as an :class:`AttachmentRef`.

    The id must be a UUID in canonical hyphenated lowercase form.
    """
    if isinstance(raw, AttachmentRef):
        raw = {"bucket": raw.bucket, "id": raw.id}
    if not isinstance(raw, dict):
        raise AttachmentError(ERROR_INVALID_REF, "Attachment ref must be an object")
    bucket = _validate_key(raw.get("bucket"), "bucket")
    att_id = _validate_key(raw.get("id"), "id")
    try:
        canonical = str(uuid.UUID(att_id))
    except ValueError:
        raise AttachmentError(ERROR_INVALID_REF, "Attachment id must be a UUID") from None
    if canonical != att_id:
        raise AttachmentError(ERROR_INVALID_REF, "Attachment id must be a canonical UUID")
    return AttachmentRef(bucket, att_id)


# ── File names ──


def _truncate(name: str, max_bytes: int) -> str:
    """Truncate *name* to *max_bytes* UTF-8 bytes, cutting the stem before the last extension."""
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    ext_bytes = len(ext.encode("utf-8"))
    if ext_bytes >= max_bytes:
        stem, ext = name, ""
        budget = max_bytes
    else:
        budget = max_bytes - ext_bytes
    return stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + ext


def normalize_filename(name: str, max_bytes: int) -> str:
    """Make any file name acceptable for the staging ``file/`` directory.

    Surrounding whitespace is stripped first (as the upload checks do), then forbidden characters become ``_``; a leading reserved upload prefix gets a ``_`` in front;
    an over-long name is truncated before its extension (on UTF-8 bytes); an empty, ``.`` or
    ``..`` name becomes ``attachment``.
    """
    result = _FORBIDDEN_CHARS.sub("_", name.strip())
    if result.startswith(TEMP_FILE_PREFIX):
        result = "_" + result
    result = _truncate(result, max_bytes).strip()
    if result in ("", ".", ".."):
        return FALLBACK_NAME
    return result


# ── Paths ──


def _staging_root() -> Path:
    return Path(os.path.realpath(get_composer_attachments_dir()))


def entry_dir(ref: AttachmentRef) -> Path:
    """The entry directory of a validated ref (not checked for existence)."""
    ref = validate_ref(ref)
    return _staging_root() / ref.bucket / ref.id


def _real_entry_dir(ref: AttachmentRef) -> Path | None:
    """The entry directory when it exists as a real directory inside the staging area, else None."""
    entry = entry_dir(ref)
    if not entry.is_dir() or Path(os.path.realpath(entry)) != entry:
        return None
    return entry


def _read_json(path: Path) -> dict | None:
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, orjson.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ── Durable markers ──


def write_marker(entry: Path, name: str, payload: dict) -> None:
    """Atomically and durably write ``<entry>/<name>`` (temp file, fsync, rename, fsync of the directory)."""
    final = entry / name
    tmp = entry / f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, orjson.dumps(payload))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(entry, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# ── Loading ──


def _load_promoted(data: dict) -> PromotedEntry:
    try:
        promoted = PromotedEntry(
            session_id=str(data["session_id"]),
            final_path=Path(data["final_path"]),
            final_name=str(data["final_name"]),
            kind=str(data["kind"]),
            original_name=str(data["original_name"]),
            size=int(data["size"]),
        )
    except (KeyError, TypeError, ValueError):
        raise AttachmentError(ERROR_MISSING, "Invalid promotion record") from None
    if not promoted.final_path.is_file():
        raise AttachmentError(ERROR_MISSING, "The promoted file no longer exists")
    return promoted


def _is_plain_basename(name: object) -> bool:
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
    )


def load_entry(ref: AttachmentRef) -> StagedEntry:
    """Load a ready (or promoted) entry.

    Raises :class:`AttachmentError` with ``attachment_missing`` (no entry, or a promoted file that
    vanished) or ``attachment_not_ready`` (no ready marker, or the file does not match it).
    """
    ref = validate_ref(ref)
    entry = _real_entry_dir(ref)
    if entry is None:
        raise AttachmentError(ERROR_MISSING, "Attachment not found")

    promoted_data = _read_json(entry / PROMOTED_MARKER)
    if promoted_data is not None:
        promoted = _load_promoted(promoted_data)
        return StagedEntry(ref, promoted.final_name, promoted.size, None, promoted)

    ready = _read_json(entry / READY_MARKER)
    if ready is None:
        raise AttachmentError(ERROR_NOT_READY, "Upload not complete")
    filename, size = ready.get("filename"), ready.get("size")
    if not _is_plain_basename(filename) or not isinstance(size, int) or isinstance(size, bool):
        raise AttachmentError(ERROR_NOT_READY, "Invalid ready marker")

    file_dir = entry / FILE_DIR
    path = file_dir / filename
    try:
        real = Path(os.path.realpath(path))
        if real != path or not real.is_file() or real.stat().st_size != size:
            raise AttachmentError(ERROR_NOT_READY, "The file does not match its ready marker")
    except OSError:
        raise AttachmentError(ERROR_NOT_READY, "The file is not readable") from None
    return StagedEntry(ref, filename, size, path, None)


# ── Upload completion hook ──


def on_upload_completed(meta: dict, final_path: str | Path) -> None:
    """Write ``ready.json`` for a completed composer upload.

    Called before the upload is persisted as ``completed``. Uses the final (possibly renamed) basename.
    A no-op when the upload is not a composer one, when the entry no longer exists (never recreated),
    or when *final_path* is not a file of the entry's ``file/`` directory with ``meta['size']`` bytes.
    Idempotent. May raise on an I/O error: the caller keeps the upload ``finalizing`` for recovery.
    """
    origin = meta.get("origin") or {}
    if origin.get("panel") != "composer":
        return
    bucket, _, att_id = str(origin.get("key", "")).partition("/")
    ref = validate_ref({"bucket": bucket, "id": att_id})
    entry = _real_entry_dir(ref)
    if entry is None:
        return
    final = Path(os.path.realpath(final_path))
    if final.parent != entry / FILE_DIR or not final.is_file():
        logger.warning("Composer upload completed outside its entry: %s", final_path)
        return
    size = final.stat().st_size
    if size != meta.get("size"):
        logger.warning("Composer upload size mismatch for %s/%s: %s != %s", bucket, att_id, size, meta.get("size"))
        return
    payload = {"filename": final.name, "size": size}
    if _read_json(entry / READY_MARKER) == payload:
        return
    write_marker(entry, READY_MARKER, payload)
