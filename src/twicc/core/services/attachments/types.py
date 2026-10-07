"""Immutable contracts shared by the composer attachments modules.

``kind`` is ``image``, ``PDF``, ``text``, ``video``, ``audio`` or ``other``.
``mode`` is ``inline`` or ``file``. ``provider`` and ``platform`` are literal strings.
``UserTextSlot.format`` is ``claude``, ``codex_response``, ``codex_canonical`` or ``hybrid``.
"""

from pathlib import Path
from typing import NamedTuple


class AttachmentRef(NamedTuple):
    """Identity of one staged attachment: ``<bucket>/<id>``."""

    bucket: str
    id: str


class PlanTarget(NamedTuple):
    provider: str
    hybrid: bool
    ephemeral: bool
    model: str
    context_1m: bool
    platform: str


class PromotedEntry(NamedTuple):
    session_id: str
    final_path: Path
    final_name: str
    kind: str
    original_name: str
    size: int


class StagedEntry(NamedTuple):
    ref: AttachmentRef
    filename: str
    size: int
    path: Path | None
    promoted: PromotedEntry | None


class NativePart(NamedTuple):
    kind: str
    media_type: str
    # Normalized binary data for images/PDFs, decoded text for text documents.
    data: bytes | str


class PlannedEntry(NamedTuple):
    ref: AttachmentRef
    n: int
    name: str
    kind: str
    rank: int
    of: int
    mode: str
    source: StagedEntry
    native: NativePart | None


class AttachmentPlan(NamedTuple):
    target: PlanTarget
    entries: tuple[PlannedEntry, ...]


class ManifestEntry(NamedTuple):
    n: int
    name: str
    kind: str
    rank: int
    of: int
    mode: str
    artifact_name: str | None


class AttachmentManifest(NamedTuple):
    owner: str
    # Absent when no entry is a file.
    directory: Path | None
    entries: tuple[ManifestEntry, ...]


class PreparedEntry(NamedTuple):
    entry: PlannedEntry
    precopy: Path | None


class PreparedAttachments(NamedTuple):
    plan: AttachmentPlan
    entries: tuple[PreparedEntry, ...]


class AttachmentContent(NamedTuple):
    # Inline entries only, in entry order.
    native_parts: tuple[NativePart, ...]
    manifest: AttachmentManifest
    user_text: str


class ParsedManifest(NamedTuple):
    entries: tuple[ManifestEntry, ...]
    directory: str | None
    hybrid_paths: tuple[str | None, ...]
    hybrid: bool


class UserTextSlot(NamedTuple):
    parent: dict
    key: str
    format: str
