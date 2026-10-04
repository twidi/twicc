"""Staging store of composer attachments: identity, names, durable markers, ready loading, promotion.

Layout (``paths.get_composer_attachments_dir()``): ``<bucket>/<attachment_id>/`` holding
``file/<filename>``, ``ready.json``, ``committed.json`` and ``promoted.json``.
Every path is built from a validated ref and a sanitized file name, and real paths are
checked before any content access.
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.
"""

import errno
import logging
import os
import re
import stat
import uuid
from pathlib import Path

import orjson

from twicc.core.services.attachments.types import AttachmentRef, PreparedEntry, PromotedEntry, StagedEntry
from twicc.paths import get_artifacts_dir, get_composer_attachments_dir
from twicc.uploads.store import TEMP_FILE_PREFIX, candidate_names

logger = logging.getLogger(__name__)

__all__ = [
    "ERROR_COMMIT_FAILED",
    "ERROR_MISSING",
    "ERROR_NOT_READY",
    "NO_HARD_LINK_ERRNOS",
    "AttachmentError",
    "attachments_dir",
    "content_location",
    "content_media_type",
    "entry_dir",
    "get_composer_attachments_dir",
    "load_entry",
    "mark_committed",
    "normalize_filename",
    "on_upload_completed",
    "promote_entry",
    "validate_ref",
    "write_marker",
]

ERROR_INVALID_REF = "invalid_attachment_ref"
ERROR_NOT_READY = "attachment_not_ready"
ERROR_MISSING = "attachment_missing"
ERROR_COMMIT_FAILED = "attachment_commit_failed"

# ``os.link`` errors of a filesystem without hard-link support (same set as ``uploads.store``).
NO_HARD_LINK_ERRNOS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS})
ATTACHMENTS_SUBDIR = "attachments"

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


def is_valid_bucket(value: object) -> bool:
    """True when *value* is acceptable as the ``bucket`` of a ref."""
    try:
        _validate_key(value, "bucket")
    except AttachmentError:
        return False
    return True


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
    fsync_dir(entry)


def fsync_dir(directory: Path) -> None:
    """``fsync`` a directory, so the names created or removed in it are durable."""
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def mark_committed(ref: AttachmentRef, at: str) -> None:
    """Write ``committed.json = {at}`` on an existing entry; never recreates a released entry.

    Raises :class:`AttachmentError` (``attachment_missing``) when the entry directory is gone.
    """
    entry = _real_entry_dir(ref)
    if entry is None:
        raise AttachmentError(ERROR_MISSING, "Attachment not found")
    write_marker(entry, COMMITTED_MARKER, {"at": at})


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


# ── Content ──

OCTET_STREAM = "application/octet-stream"
PDF = "application/pdf"
TEXT_PLAIN = "text/plain; charset=utf-8"
# Text a browser could run or render as a document: never served inline, even as text/plain.
_ACTIVE_SUFFIXES = frozenset(
    {".htm", ".html", ".shtml", ".xht", ".xhtml", ".svg", ".svgz", ".xml", ".xsl", ".xslt", ".js", ".mjs", ".cjs"}
)


def content_location(entry: StagedEntry) -> Path:
    """The real path of the bytes of a loaded entry.

    A staged entry: its ``file/<filename>`` (already confined by :func:`load_entry`). A promoted
    entry: its ``final_path``, only when it resolves to a regular file directly inside
    ``artifacts/<session_id>/attachments/``. Raises :class:`AttachmentError` (``attachment_missing``)
    otherwise.
    """
    if entry.promoted is None:
        if entry.path is None:
            raise AttachmentError(ERROR_MISSING, "Attachment not found")
        return entry.path
    promoted = entry.promoted
    if not _is_plain_basename(promoted.session_id):
        raise AttachmentError(ERROR_MISSING, "Invalid promotion record")
    allowed = Path(os.path.realpath(attachments_dir(promoted.session_id)))
    real = Path(os.path.realpath(promoted.final_path))
    if real.parent != allowed or not real.is_file():
        raise AttachmentError(ERROR_MISSING, "The promoted file is outside its attachments directory")
    return real


def _looks_like_markup(head: bytes) -> bool:
    return head.removeprefix(b"\xef\xbb\xbf").lstrip()[:1] == b"<"


def content_media_type(entry: StagedEntry) -> tuple[str, bool]:
    """``(media type, inline)`` for the content endpoint, from at most the first 64 KiB of the bytes.

    The kind comes from the bounded detector of spec §6.4. Inline only for raster images (PNG, JPEG,
    GIF, WebP), PDF and UTF-8 plain text; everything else, including another raster format (BMP, …)
    and text that is markup or script (HTML, SVG, XML, JavaScript), is ``application/octet-stream``
    served as an attachment. The type always comes from the bytes: a file name can only refuse
    inline, never grant it.
    """
    # Imported here so the planner can import this module without an import cycle.
    from twicc.core.services.attachments import images, planner

    path = content_location(entry)
    # One open for the head and the size, so both describe the same file.
    with open(path, "rb") as file:
        head = file.read(planner.HEAD_BYTES)
        size = os.fstat(file.fileno()).st_size
    kind = planner.detect_kind_from_head(head, entry.filename, size)
    if kind == planner.KIND_IMAGE:
        image_format = images.sniff_image_format(head)
        if image_format is None:
            return OCTET_STREAM, False
        return images.IMAGE_MEDIA_TYPES[image_format], True
    if kind == planner.KIND_PDF:
        return PDF, True
    if (
        kind == planner.KIND_TEXT
        and not _looks_like_markup(head)
        and Path(entry.filename).suffix.lower() not in _ACTIVE_SUFFIXES
    ):
        return TEXT_PLAIN, True
    return OCTET_STREAM, False


# ── Upload completion hook ──


def on_upload_completed(meta: dict, final_path: str | Path) -> None:
    """Write ``ready.json`` for a completed composer upload.

    Called before the upload is persisted as ``completed``. Uses the final (possibly renamed) basename.
    A no-op when the upload is not a composer one, when the entry no longer exists (never recreated),
    or when *final_path* is not a file of the entry's ``file/`` directory with ``meta['size']`` bytes.
    Idempotent. May raise on an I/O error: the caller keeps the upload ``finalizing`` for recovery.
    A permanent condition (a malformed origin, an invalid entry key) is logged and never raises, so
    it can never keep an upload ``finalizing`` forever.
    """
    origin = meta.get("origin")
    if not isinstance(origin, dict) or origin.get("panel") != "composer":
        return
    key = origin.get("key")
    bucket, _, att_id = (key if isinstance(key, str) else "").partition("/")
    try:
        ref = validate_ref({"bucket": bucket, "id": att_id})
    except AttachmentError:
        logger.warning("Composer upload completed with an invalid entry key: %r", key)
        return
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


# ── Promotion ──


def attachments_dir(session_id: str) -> Path:
    """``artifacts/<session_id>/attachments/`` (not created, not resolved).

    Raises :class:`AttachmentError` (``attachment_commit_failed``) for an unsafe session id.
    """
    if not _is_plain_basename(session_id):
        raise AttachmentError(ERROR_COMMIT_FAILED, "Invalid session id")
    return get_artifacts_dir() / session_id / ATTACHMENTS_SUBDIR


def _remove_if_empty(path: Path) -> None:
    """Remove *path* when it is still our empty reservation (best effort)."""
    try:
        st = os.lstat(path)
        if st.st_size == 0 and stat.S_ISREG(st.st_mode):
            os.unlink(path)
    except OSError:
        logger.warning("Composer attachments: cannot remove the reservation %s", path, exc_info=True)


def _reserve_and_replace(source: Path, directory: Path, names: list[str]) -> Path:
    """No hard links: create the first free candidate exclusively, then replace it with *source*."""
    for name in names:
        candidate = directory / name
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AttachmentError(ERROR_COMMIT_FAILED, f"Cannot claim {name!r}: {exc}") from exc
        os.close(fd)
        try:
            # Only our own empty reservation is replaced: never another writer's file.
            os.replace(source, candidate)
        except OSError as exc:
            _remove_if_empty(candidate)
            raise AttachmentError(ERROR_COMMIT_FAILED, f"Cannot place {name!r}: {exc}") from exc
        return candidate
    raise AttachmentError(ERROR_COMMIT_FAILED, "No free file name in the attachments directory")


def _claim(source: Path, directory: Path, filename: str) -> Path:
    """Claim the first free candidate of *filename* in *directory* from the pre-copy *source*.

    An exclusive hard link per candidate (a name taken by a concurrent writer: next candidate);
    only a no-hard-link errno switches to an exclusive create followed by a replace. Never overwrites.
    """
    names = list(candidate_names(filename))
    for index, name in enumerate(names):
        candidate = directory / name
        try:
            os.link(source, candidate)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno in NO_HARD_LINK_ERRNOS:
                return _reserve_and_replace(source, directory, names[index:])
            raise AttachmentError(ERROR_COMMIT_FAILED, f"Cannot claim {name!r}: {exc}") from exc
        return candidate
    raise AttachmentError(ERROR_COMMIT_FAILED, "No free file name in the attachments directory")


def _remove_source(path: Path, what: str) -> None:
    """Remove a source after the tombstone; a failure only leaves a leftover (logged)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Composer attachments: cannot remove the %s %s", what, path, exc_info=True)


def _release_unrecorded_claim(staged: Path, final: Path) -> None:
    """Give back a claimed final name the tombstone does not record (a failure, not a crash).

    The claim is a hard link of the still-ready staged file: left behind, it would be an orphan
    artifact sharing that file's inode with the next promotion, possibly into another session
    (spec §7.1). A tombstone that already names *final* (its rename landed, a later sync failed)
    keeps it.
    """
    recorded = _read_json(staged / PROMOTED_MARKER)
    if recorded and recorded.get("final_path") == str(final):
        return
    try:
        os.unlink(final)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Composer attachments: cannot remove the unrecorded claim %s", final, exc_info=True)


def promote_entry(entry: PreparedEntry, session_id: str) -> PromotedEntry:
    """Promote one prepared file entry into ``artifacts/<session_id>/attachments/`` (spec §7.2).

    - a tombstone of the same session: its file must still exist, confined to that session's
      attachments directory (the marker is never trusted blindly), and is reused as is;
    - a staged file, or a tombstone of another session: the final name is claimed from the prepared
      pre-copy (:func:`_claim`), then ``promoted.json`` is written durably, and only then are the
      staged file and the pre-copy removed. A failure before the tombstone gives the claimed name
      back; a crash there leaves it as an orphan artifact. Either way the entry stays ready.

    Same-filesystem operations and small marker writes only. Raises :class:`AttachmentError`:
    ``attachment_missing`` for a vanished entry or promoted file, ``attachment_commit_failed`` for
    any other failure; :class:`OSError` may escape from the marker or directory writes.
    """
    planned = entry.entry
    source = planned.source
    previous = source.promoted
    if previous is not None and previous.session_id == session_id:
        real = content_location(source)
        return previous._replace(final_path=real)
    if entry.precopy is None:
        raise AttachmentError(ERROR_COMMIT_FAILED, "The entry has no prepared source")
    staged = _real_entry_dir(planned.ref)
    if staged is None:
        raise AttachmentError(ERROR_MISSING, "Attachment not found")

    target = attachments_dir(session_id)
    target.mkdir(parents=True, exist_ok=True)
    real_target = Path(os.path.realpath(target))
    original_name = previous.original_name if previous is not None else source.filename
    final = _claim(entry.precopy, real_target, original_name)
    try:
        fsync_dir(real_target)
        # The size of the bytes really promoted (another session may have edited its copy in place).
        size = os.stat(final).st_size
        promoted = PromotedEntry(session_id, final, final.name, planned.kind, original_name, size)
        write_marker(
            staged,
            PROMOTED_MARKER,
            {
                "session_id": promoted.session_id,
                "final_path": str(promoted.final_path),
                "final_name": promoted.final_name,
                "kind": promoted.kind,
                "original_name": promoted.original_name,
                "size": promoted.size,
            },
        )
    except Exception:
        _release_unrecorded_claim(staged, final)
        raise
    # The tombstone is durable: the sources can go. A crash from here on leaves a promoted entry.
    if previous is None and source.path is not None:
        _remove_source(source.path, "staged file")
        try:
            fsync_dir(source.path.parent)
        except OSError:
            logger.warning("Composer attachments: cannot sync %s", source.path.parent, exc_info=True)
    _remove_source(entry.precopy, "pre-copy")
    return promoted
