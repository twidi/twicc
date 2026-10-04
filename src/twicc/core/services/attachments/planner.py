"""Planning of composer attachments: kind detection, then ordered first-fit decisions.

Kind detection never reads a whole file: images by magic bytes (then a Pillow open of the head bytes
alone, for formats that are never native), PDF by its signature, and the text and NUL rules on at most
the first 64 KiB.

Planning walks the refs in add order. Each entry is decided from its kind, the provider policy and
the quotas still available (native items, volume budget) before any byte past the head is read; only
an entry that will really be native is read or normalized. A later small entry can be native after a
larger one became a file. Ranks count every entry of a kind, native and file alike.
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.3-§6.6.
"""

import asyncio
import mimetypes
from pathlib import Path

from twicc.core.services.attachments import images
from twicc.core.services.attachments.images import HEAD_BYTES, base64_size, head_opens_as_raster, sniff_image_format
from twicc.core.services.attachments.staging import ERROR_MISSING, AttachmentError, load_entry, validate_ref
from twicc.core.services.attachments.types import (
    AttachmentPlan,
    AttachmentRef,
    NativePart,
    PlannedEntry,
    PlanTarget,
    StagedEntry,
)

__all__ = [
    "ERROR_INVALID_ATTACHMENTS",
    "ERROR_REQUIRES_ARTIFACTS",
    "ERROR_WITH_COMMAND",
    "HEAD_BYTES",
    "KIND_AUDIO",
    "KIND_IMAGE",
    "KIND_OTHER",
    "KIND_PDF",
    "KIND_TEXT",
    "KIND_VIDEO",
    "MODE_FILE",
    "MODE_INLINE",
    "AttachmentPlanError",
    "describe_attachment_error",
    "detect_kind",
    "detect_kind_from_head",
    "is_hybrid_command",
    "is_utf8_text",
    "plan_attachments",
    "plan_attachments_off_loop",
    "read_head",
    "validate_attachment_frame",
]

KIND_IMAGE = "image"
KIND_PDF = "PDF"
KIND_TEXT = "text"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_OTHER = "other"

MODE_INLINE = "inline"
MODE_FILE = "file"

ERROR_INVALID_ATTACHMENTS = "invalid_attachments"
ERROR_REQUIRES_ARTIFACTS = "attachment_requires_artifacts"
ERROR_WITH_COMMAND = "attachments_with_command"

PROVIDER_CODEX = "codex"
PLATFORM_THIRD_PARTY = "third_party"

PDF_MEDIA_TYPE = "application/pdf"
TEXT_MEDIA_TYPE = "text/plain"

PDF_MAGIC = b"%PDF-"
# Longest UTF-8 sequence minus one: the most bytes a cut sequence can leave at the end of the head.
_MAX_CUT_SEQUENCE = 3


def read_head(path: Path) -> bytes:
    """At most the first 64 KiB of *path*."""
    with open(path, "rb") as file:
        return file.read(HEAD_BYTES)


def is_utf8_text(head: bytes, size: int) -> bool:
    """The text rule on a bounded head: non-empty, no NUL byte, valid UTF-8.

    An incomplete multi-byte sequence is tolerated only at the end of a head cut by the 64 KiB
    boundary (the file is longer than the head); anywhere else, invalid UTF-8 is not text.
    """
    if size <= 0 or not head or b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (
            len(head) == HEAD_BYTES
            and size > len(head)
            and exc.reason == "unexpected end of data"
            and exc.start >= len(head) - _MAX_CUT_SEQUENCE
        )
    return True


def detect_kind_from_head(head: bytes, name: str, size: int) -> str:
    """The kind of a file from its bounded *head*, its *name* and its *size* (spec §6.4)."""
    if sniff_image_format(head) is not None or head_opens_as_raster(head):
        return KIND_IMAGE
    if head.startswith(PDF_MAGIC):
        return KIND_PDF
    if is_utf8_text(head, size):
        return KIND_TEXT
    media_type, _ = mimetypes.guess_type(name, strict=False)
    top_level = media_type.partition("/")[0] if media_type else ""
    if top_level in (KIND_VIDEO, KIND_AUDIO):
        return top_level
    return KIND_OTHER


def detect_kind(path: Path, name: str, size: int) -> str:
    """The kind of the file at *path* (``image``, ``PDF``, ``text``, ``video``, ``audio`` or ``other``).

    Reads at most the first 64 KiB; *name* only matters for the video / audio fallback.
    """
    if size <= 0:
        return KIND_OTHER
    return detect_kind_from_head(read_head(path), name, size)


# ── Planning ──


class AttachmentPlanError(AttachmentError):
    """A business refusal of a whole plan; *names* lists the entries concerned, in order."""

    def __init__(self, code: str, message: str | None = None, *, names: tuple[str, ...] = ()):
        super().__init__(code, message)
        self.names = names


def describe_attachment_error(exc: AttachmentError) -> tuple[str, str, tuple[str, ...]]:
    """``(code, message, names)`` of a staging or plan error, for an error frame or result.

    The names of the entries concerned (``attachment_requires_artifacts``) are appended to the
    message, so a caller that only shows the message still lists them.
    """
    names = tuple(getattr(exc, "names", ()) or ())
    message = str(exc)
    if names:
        message = f"{message}: {', '.join(names)}"
    return exc.code, message, names


def _policy(target: PlanTarget):
    # Imported here: the provider helpers load the whole provider package.
    from twicc.providers.helpers import get_provider_helpers

    return get_provider_helpers(target.provider).get_attachment_policy()


def _is_refused_command(target: PlanTarget, text: str) -> bool:
    """True when *text* is a command whose path cannot carry attachments (spec §6.6)."""
    if target.provider == PROVIDER_CODEX:
        from twicc.providers.codex.agent.hardcoded_commands import parse_hardcoded_command

        return parse_hardcoded_command(text) is not None
    return target.hybrid and is_hybrid_command(text)


def is_hybrid_command(text: str) -> bool:
    """True when the hybrid TUI would read *text* as a command (spec §6.6).

    A leading ``/`` makes the pasted text a slash command, whose arguments would swallow the
    manifest. A leading ``!`` switches the TUI input to bash mode: the bundled CLI (2.1.286) turns
    a paste starting with ``!`` into an empty composer into a shell command. Leading whitespace is
    ignored for both, as the planner and the hybrid agent share this one check.
    """
    return text.lstrip().startswith(("/", "!"))


def _read_exact(path: Path, size: int) -> bytes | None:
    """The *size* bytes of *path*, or None when the file no longer has that size."""
    with open(path, "rb") as file:
        data = file.read(size + 1)
    return data if len(data) == size else None


def _native_document(entry: StagedEntry, kind: str, max_bytes: int | None, remaining_budget: int) -> NativePart | None:
    """The native part of a PDF or text candidate, or None for a file.

    Size and budget are checked from ``st_size`` first (the §6.5 measure), so a candidate over
    them is never read beyond the kind-detection head. A text must decode fully as UTF-8.
    """
    size = entry.size
    if max_bytes is not None and size > max_bytes:
        return None
    measure = base64_size(size) if kind == KIND_PDF else size
    if measure > remaining_budget:
        return None
    try:
        data = _read_exact(entry.path, size)
    except OSError:
        return None
    if data is None:
        return None
    if kind == KIND_PDF:
        return NativePart(KIND_PDF, PDF_MEDIA_TYPE, data)
    try:
        return NativePart(KIND_TEXT, TEXT_MEDIA_TYPE, data.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def _native_measure(part: NativePart) -> int:
    """The volume budget measure of a native part (spec §6.5)."""
    if isinstance(part.data, str):
        return len(part.data.encode("utf-8"))
    return base64_size(len(part.data))


def plan_attachments(refs: tuple[AttachmentRef, ...], target: PlanTarget, *, text: str) -> AttachmentPlan:
    """Decide, in add order, which attachments go natively and which become files (spec §6.6).

    Synchronous and blocking (it reads and decodes files): call it through ``asyncio.to_thread``.
    Writes nothing. The user *text* only matters for the command checks; it consumes no budget.

    Raises :class:`AttachmentPlanError` (``attachments_with_command`` before any entry is loaded;
    ``attachment_requires_artifacts`` for an ephemeral target with at least one file entry) and
    :class:`AttachmentError` (``attachment_missing`` / ``attachment_not_ready``) from the staging store.
    """
    if not refs:
        return AttachmentPlan(target, ())
    if _is_refused_command(target, text):
        raise AttachmentPlanError(ERROR_WITH_COMMAND, "Attachments cannot be sent with a command")

    policy = _policy(target)
    third_party = target.platform == PLATFORM_THIRD_PARTY
    native_kinds = policy.hybrid_native_kinds if target.hybrid else policy.native_kinds
    remaining_items = policy.max_native_items_1m if target.context_1m else policy.max_native_items
    remaining_budget = policy.volume_budget_third_party if third_party else policy.volume_budget
    max_bytes = {KIND_PDF: policy.pdf_native_max_bytes, KIND_TEXT: policy.text_native_max_bytes}

    decided: list[tuple[StagedEntry, str, str, NativePart | None]] = []
    for ref in refs:
        entry = load_entry(ref)
        if entry.promoted is not None:
            # The bytes already are an artifact (a Retry after a promotion): always a file.
            decided.append((entry, entry.promoted.original_name, entry.promoted.kind, None))
            continue
        kind = detect_kind(entry.path, entry.filename, entry.size)
        native = None
        if kind in native_kinds and remaining_items > 0:
            if kind == KIND_IMAGE:
                native = images.normalize_image(entry.path, target=target, remaining_budget=remaining_budget)
            elif kind in max_bytes:
                native = _native_document(entry, kind, max_bytes[kind], remaining_budget)
        if native is not None:
            # Every producer already checked the part against the remaining budget.
            remaining_items -= 1
            remaining_budget -= _native_measure(native)
        decided.append((entry, entry.filename, kind, native))

    totals: dict[str, int] = {}
    for _entry, _name, kind, _native in decided:
        totals[kind] = totals.get(kind, 0) + 1
    ranks: dict[str, int] = {}
    planned = []
    for n, (entry, name, kind, native) in enumerate(decided, start=1):
        ranks[kind] = ranks.get(kind, 0) + 1
        mode = MODE_FILE if native is None else MODE_INLINE
        planned.append(PlannedEntry(entry.ref, n, name, kind, ranks[kind], totals[kind], mode, entry, native))

    if target.ephemeral:
        names = tuple(e.name for e in planned if e.mode == MODE_FILE)
        if names:
            raise AttachmentPlanError(
                ERROR_REQUIRES_ARTIFACTS, "Ephemeral sessions only accept native attachments", names=names
            )
    return AttachmentPlan(target, tuple(planned))


async def plan_attachments_off_loop(
    refs: tuple[AttachmentRef, ...], target: PlanTarget, *, text: str,
) -> AttachmentPlan:
    """:func:`plan_attachments` in a worker thread, so a slow decode never blocks the event loop.

    An ``OSError`` (the staged file vanished or became unreadable between the load of its entry
    and the read of its bytes) becomes ``attachment_missing``: the entry is no longer usable.
    """
    try:
        return await asyncio.to_thread(plan_attachments, refs, target, text=text)
    except OSError as exc:
        raise AttachmentError(ERROR_MISSING, "Attachment not found") from exc


# ── Frame shape (spec §6.6, §8) ──

_LEGACY_FIELDS = ("images", "documents")


def validate_attachment_frame(payload: dict) -> tuple[AttachmentRef, ...]:
    """The composer refs of a ``send_message`` payload, in add order, after the shape checks.

    Only the shape is checked (no disk access), so it can run inline in the receive loop. An absent,
    ``None`` or empty ``attachments`` means no attachment content. Raises :class:`AttachmentError`
    (``invalid_attachments``) for a value that is not a list, a malformed ref, an id that is not a
    canonical UUID, the same ``{bucket, id}`` twice, or refs together with a non-empty legacy
    ``images`` / ``documents`` field.
    """
    raw = payload.get("attachments")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise AttachmentError(ERROR_INVALID_ATTACHMENTS, "attachments must be a list")
    if not raw:
        return ()
    if any(payload.get(field) for field in _LEGACY_FIELDS):
        raise AttachmentError(ERROR_INVALID_ATTACHMENTS, "attachments cannot be combined with images or documents")
    refs: list[AttachmentRef] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AttachmentError(ERROR_INVALID_ATTACHMENTS, "Each attachment must be an object")
        try:
            ref = validate_ref(item)
        except AttachmentError as exc:
            raise AttachmentError(ERROR_INVALID_ATTACHMENTS, str(exc)) from None
        if ref in refs:
            raise AttachmentError(ERROR_INVALID_ATTACHMENTS, "The same attachment is listed twice")
        refs.append(ref)
    return tuple(refs)
