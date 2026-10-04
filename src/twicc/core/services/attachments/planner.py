"""Planning of composer attachments: kind detection (this part), then ordered first-fit decisions.

Kind detection never reads a whole file: images by magic bytes (then a Pillow open of the head bytes
alone, for formats that are never native), PDF by its signature, and the text and NUL rules on at most
the first 64 KiB. Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.4.
"""

import mimetypes
from pathlib import Path

from twicc.core.services.attachments.images import HEAD_BYTES, head_opens_as_raster, sniff_image_format

__all__ = [
    "HEAD_BYTES",
    "KIND_AUDIO",
    "KIND_IMAGE",
    "KIND_OTHER",
    "KIND_PDF",
    "KIND_TEXT",
    "KIND_VIDEO",
    "detect_kind",
    "detect_kind_from_head",
    "is_utf8_text",
    "read_head",
]

KIND_IMAGE = "image"
KIND_PDF = "PDF"
KIND_TEXT = "text"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_OTHER = "other"

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
