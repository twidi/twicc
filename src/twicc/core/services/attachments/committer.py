"""Commit of a composer attachment plan: off-loop preparation, bounded finish, pre-copy cleanup.

``prepare_attachments`` does everything slow, before any manager lock and off the event loop: it
gives every source a promotion will need a fsynced top-level pre-copy
``<data>/artifacts/.twicc-upload-<uuid>.tmp`` (a hard link of a staged file, or a real copy after
``EXDEV`` / a no-hard-link errno; always a real copy of another or unknown session's promoted file,
so two sessions never share an inode). ``finish_attachments`` then only does same-filesystem claims
and small marker writes: ``committed.json`` on every entry, then the promotions, then the structured
content. ``discard_prepared`` removes what is left of the pre-copies; callers own it in a
``try``/``finally``.

Every failure raises :class:`SendDeliveryError`: ``attachment_missing`` for a vanished entry or
promoted file, ``attachment_commit_failed`` for any other I/O failure. Staging stays retryable.
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §7.1-§7.2.
"""

import errno
import logging
import os
import shutil
import uuid
from pathlib import Path

from twicc.agent.exceptions import SendDeliveryError
from twicc.core.services.attachments import staging
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import (
    AttachmentContent,
    AttachmentManifest,
    AttachmentPlan,
    ManifestEntry,
    PlannedEntry,
    PreparedAttachments,
    PreparedEntry,
)
from twicc.paths import get_artifacts_dir
from twicc.uploads.store import COPY_BLOCK_SIZE, TEMP_FILE_PREFIX, now_iso

logger = logging.getLogger(__name__)

__all__ = ["discard_prepared", "finish_attachments", "prepare_attachments"]

MODE_INLINE = "inline"
MODE_FILE = "file"


def _error(code: str, message: str) -> SendDeliveryError:
    return SendDeliveryError(message, code=code)


def _delivery_error(exc: Exception) -> SendDeliveryError:
    """The :class:`SendDeliveryError` of a staging business error or of an I/O error."""
    if isinstance(exc, AttachmentError):
        code = exc.code if exc.code == staging.ERROR_MISSING else staging.ERROR_COMMIT_FAILED
        return _error(code, str(exc))
    return _error(staging.ERROR_COMMIT_FAILED, f"Cannot commit the attachments: {exc}")


# ── Pre-copies ──


def _new_precopy_path() -> Path:
    artifacts = get_artifacts_dir()
    artifacts.mkdir(parents=True, exist_ok=True)
    return Path(os.path.realpath(artifacts)) / f"{TEMP_FILE_PREFIX}{uuid.uuid4()}.tmp"


def _remove_precopy(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Composer attachments: cannot remove the pre-copy %s", path, exc_info=True)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy(source: Path, destination: Path) -> None:
    """A real, fsynced copy of *source* into the new file *destination*."""
    with open(source, "rb") as reader:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        with os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer, COPY_BLOCK_SIZE)
            writer.flush()
            os.fsync(writer.fileno())


def _make_precopy(source: Path, *, link: bool) -> Path:
    """Create a fsynced pre-copy of *source*; remove it again on any failure.

    *link*: try a hard link first, falling back to a copy on ``EXDEV`` or a no-hard-link errno
    (never decided from ``st_dev`` alone). The mtime is refreshed (a hard link keeps the source's),
    so the 24 h pre-copy reaper never removes it between prepare and finish.
    """
    destination = _new_precopy_path()
    try:
        linked = False
        if link:
            try:
                os.link(source, destination)
                linked = True
            except OSError as exc:
                if exc.errno != errno.EXDEV and exc.errno not in staging.NO_HARD_LINK_ERRNOS:
                    raise
        if not linked:
            _copy(source, destination)
        os.utime(destination)
        if linked:
            _fsync_file(destination)
        staging.fsync_dir(destination.parent)
    except BaseException:
        _remove_precopy(destination)
        raise
    return destination


def _prepare_entry(planned: PlannedEntry, session_id: str | None) -> Path | None:
    """The pre-copy one entry needs, or None (an inline entry, a same-session tombstone)."""
    if planned.mode != MODE_FILE:
        return None
    source = planned.source
    if source.promoted is None:
        if source.path is None:
            raise AttachmentError(staging.ERROR_COMMIT_FAILED, "The entry has no staged file")
        precopy = _make_precopy(source.path, link=True)
        if precopy.stat().st_size != source.size:
            _remove_precopy(precopy)
            raise AttachmentError(staging.ERROR_COMMIT_FAILED, "The staged file changed size")
        return precopy
    # Confined to its session's attachments directory: a tombstone is never trusted blindly.
    real = staging.content_location(source)
    if session_id is not None and source.promoted.session_id == session_id:
        return None
    try:
        return _make_precopy(real, link=False)
    except FileNotFoundError:
        raise AttachmentError(staging.ERROR_MISSING, "The promoted file no longer exists") from None


def prepare_attachments(plan: AttachmentPlan, *, session_id: str | None) -> PreparedAttachments:
    """Give every file entry a pre-copy on the artifacts filesystem (spec §7.1).

    Synchronous and blocking (links, copies, fsyncs): run it off the event loop, before any manager
    lock. *session_id* is ``None`` when the target session is not known yet (Codex new session).
    On failure, every pre-copy made so far is removed and :class:`SendDeliveryError` is raised.
    """
    prepared: list[PreparedEntry] = []
    try:
        for planned in plan.entries:
            prepared.append(PreparedEntry(planned, _prepare_entry(planned, session_id)))
    except (AttachmentError, OSError) as exc:
        discard_prepared(PreparedAttachments(plan, tuple(prepared)))
        raise _delivery_error(exc) from exc
    except BaseException:
        discard_prepared(PreparedAttachments(plan, tuple(prepared)))
        raise
    return PreparedAttachments(plan, tuple(prepared))


def discard_prepared(prepared: PreparedAttachments) -> None:
    """Remove the pre-copies still on disk (best effort, idempotent). Never touches staging."""
    for entry in prepared.entries:
        if entry.precopy is not None:
            _remove_precopy(entry.precopy)


# ── Finish ──


def _finish(prepared: PreparedAttachments, session_id: str, text: str) -> AttachmentContent:
    directory = Path(os.path.realpath(staging.attachments_dir(session_id)))
    at = now_iso()
    for entry in prepared.entries:
        staging.mark_committed(entry.entry.ref, at)

    native_parts = []
    manifest_entries = []
    has_file = False
    for entry in prepared.entries:
        planned = entry.entry
        if planned.mode == MODE_INLINE:
            if planned.native is None:
                raise AttachmentError(staging.ERROR_COMMIT_FAILED, "An inline entry has no native part")
            native_parts.append(planned.native)
            artifact_name = None
        else:
            # Through the module attribute: tests observe the commit order.
            artifact_name = staging.promote_entry(entry, session_id).final_name
            has_file = True
        manifest_entries.append(
            ManifestEntry(planned.n, planned.name, planned.kind, planned.rank, planned.of, planned.mode, artifact_name)
        )
    manifest = AttachmentManifest(session_id, directory if has_file else None, tuple(manifest_entries))
    return AttachmentContent(tuple(native_parts), manifest, text)


def finish_attachments(prepared: PreparedAttachments, *, session_id: str, text: str) -> AttachmentContent:
    """Commit the prepared entries for *session_id* and return the structured content (spec §7.1-§7.2).

    Writes ``committed.json`` on every entry (inline and file) first, then promotes the file entries
    in order. Only same-filesystem links/renames and small marker writes: safe under a lock. Returns
    the inline parts in entry order, the structured manifest (no text is built here) and the raw
    *text* (no context fold here). Always removes the remaining pre-copies; on failure raises
    :class:`SendDeliveryError` and leaves every entry retryable (an entry promoted before the
    failure keeps its tombstone, reused by a Retry of the same session).
    """
    try:
        return _finish(prepared, session_id, text)
    except (AttachmentError, OSError) as exc:
        raise _delivery_error(exc) from exc
    finally:
        discard_prepared(prepared)
