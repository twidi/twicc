"""Bounded image header probes and native normalization of composer attachments.

Every decision that can be taken from ``st_size`` and the image header comes before any decode:
dimensions and animation are read from bounded headers (PNG chunk walk, WebP RIFF header, GIF
logical screen descriptor, lazy Pillow open of the JPEG markers), then the pixel, CMYK, per-image
and budget checks run, and only then is the image decoded. Within the target long edge the original
bytes are sent unchanged; above it the image is transposed, resized once (Lanczos) and re-encoded in
its own family. Any failure means the entry becomes a file (``None``).
Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.3-§6.5.
"""

import io
import logging
import math
import os
import struct
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageOps

from twicc.core.services.attachments.types import NativePart, PlanTarget

logger = logging.getLogger(__name__)

__all__ = [
    "HEAD_BYTES",
    "IMAGE_MEDIA_TYPES",
    "ImageHeader",
    "base64_size",
    "head_opens_as_raster",
    "long_edge",
    "normalize_image",
    "per_image_limit",
    "probe_image_header",
    "sniff_image_format",
]

# Kind detection and the image header probes read at most this from the start of a file
# (the PNG chunk walk alone may seek past it).
HEAD_BYTES = 64 * 1024

PROVIDER_CODEX = "codex"
CLAUDE_LONG_EDGE = 2000
CODEX_LONG_EDGE = 2576
MAX_PIXELS = 100_000_000
# Base64 length limit of one image after normalization, per Claude platform. Codex has none.
CLAUDE_PER_IMAGE_LIMITS = {"first_party": 10_485_760, "third_party": 5_242_880}
PLATFORM_THIRD_PARTY = "third_party"

PNG_MAX_CHUNKS = 1000
PNG_MAX_CHUNK_LENGTH = 0x7FFFFFFF  # PNG spec: a chunk length never exceeds 2^31 - 1

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IMAGE_MAGIC = (
    (PNG_SIGNATURE, "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
)
IMAGE_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}

# Raster formats with a real signature that Pillow may open from the head bytes alone: such a file
# is an ``image`` for the manifest, but never native. Plugins without a signature check (TGA, …),
# stub plugins and text-based formats (EPS, XBM, XPM) are left out so they never claim arbitrary data.
_OTHER_RASTER_FORMATS = (
    "AVIF", "BLP", "BMP", "CUR", "DCX", "DDS", "DIB", "FLI", "ICNS", "ICO",
    "JPEG2000", "MSP", "PCX", "PPM", "PSD", "QOI", "SGI", "SUN", "TIFF",
)  # fmt: skip

# Modes that Pillow resizes with Lanczos and encodes in the output family without losing values
# (checked with Pillow 12: ``I;16`` / ``I;16B`` resize and save as 16-bit PNG).
_RESAMPLE_MODES = {
    "PNG": frozenset({"L", "LA", "RGB", "RGBA", "I;16", "I;16B"}),
    "GIF": frozenset({"L", "LA", "RGB", "RGBA"}),  # encoded as PNG
    "WEBP": frozenset({"L", "LA", "RGB", "RGBA"}),
    "JPEG": frozenset({"L", "RGB", "CMYK"}),
}

_TIFF_BYTE_ORDERS = {b"II*\x00": "<I", b"MM\x00*": ">I"}

_VP8_START_CODE = b"\x9d\x01\x2a"
_VP8L_SIGNATURE = 0x2F
_VP8X_ANIMATION_FLAG = 0x02


class ImageHeader(NamedTuple):
    """Header facts of a native-format image, read without decoding it."""

    format: str  # PNG, JPEG, GIF or WEBP (a Pillow MPO is reported as JPEG)
    width: int
    height: int
    # GIF: always False here, its animation probe runs only after the size and budget checks.
    animated: bool
    # Pillow mode, JPEG only (CMYK check); None otherwise.
    mode: str | None


# ── Limits ──


def long_edge(target: PlanTarget) -> int:
    return CODEX_LONG_EDGE if target.provider == PROVIDER_CODEX else CLAUDE_LONG_EDGE


def per_image_limit(target: PlanTarget) -> int | None:
    """Base64 size limit of one normalized image, or None when the target has none."""
    if target.provider == PROVIDER_CODEX:
        return None
    platform = PLATFORM_THIRD_PARTY if target.platform == PLATFORM_THIRD_PARTY else "first_party"
    return CLAUDE_PER_IMAGE_LIMITS[platform]


def base64_size(size: int) -> int:
    """The budget measure of binary native data (spec §6.5)."""
    return 4 * math.ceil(size / 3)


def _fits(size: int, target: PlanTarget, remaining_budget: int) -> bool:
    measure = base64_size(size)
    limit = per_image_limit(target)
    return measure <= remaining_budget and (limit is None or measure <= limit)


# ── Magic bytes ──


def sniff_image_format(head: bytes) -> str | None:
    """The native image format (PNG, JPEG, GIF, WEBP) recognized by magic bytes, or None."""
    for magic, image_format in _IMAGE_MAGIC:
        if head.startswith(magic):
            return image_format
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def head_opens_as_raster(head: bytes) -> bool:
    """True when Pillow opens another raster format (BMP, TIFF, …) from *head* alone."""
    if not head:
        return False
    tiff_offset_format = _TIFF_BYTE_ORDERS.get(head[:4])
    if tiff_offset_format is not None:
        # A TIFF whose first IFD (its 2-byte entry count at least) lies past the head cannot be
        # opened from the head: decided here, before Pillow parses (and warns about) a truncated IFD.
        if len(head) < 8 or struct.unpack(tiff_offset_format, head[4:8])[0] + 2 > len(head):
            return False
    try:
        with Image.open(io.BytesIO(head), formats=_OTHER_RASTER_FORMATS):
            return True
    except Image.DecompressionBombError:
        return True  # the header was read: a (huge) image
    except Exception:  # noqa: BLE001 - any Pillow failure means "not openable from the head"
        return False


# ── Header probes ──


def _probe_png(file, head: bytes) -> ImageHeader | None:
    """IHDR from the head, then a chunk-header walk for ``acTL`` before the first ``IDAT``."""
    if len(head) < 33 or head[12:16] != b"IHDR" or struct.unpack(">I", head[8:12])[0] != 13:
        return None
    width, height = struct.unpack(">II", head[16:24])
    offset = len(PNG_SIGNATURE)
    for _ in range(PNG_MAX_CHUNKS):
        file.seek(offset)
        chunk_header = file.read(8)
        if len(chunk_header) < 8:
            return None
        length, chunk_type = struct.unpack(">I", chunk_header[:4])[0], chunk_header[4:]
        if length > PNG_MAX_CHUNK_LENGTH:
            return None
        if chunk_type == b"IDAT":
            return ImageHeader("PNG", width, height, False, None)
        if chunk_type == b"acTL":
            return ImageHeader("PNG", width, height, True, None)
        if chunk_type == b"IEND":
            return None
        offset += 12 + length  # length + type + data + CRC
    return None


def _probe_webp(head: bytes) -> ImageHeader | None:
    """The first RIFF chunk: ``VP8X`` canvas and animation flag, else the ``VP8`` / ``VP8L`` frame header."""
    chunk_type = head[12:16]
    if chunk_type == b"VP8X" and len(head) >= 30:
        flags = head[20]
        width = int.from_bytes(head[24:27], "little") + 1
        height = int.from_bytes(head[27:30], "little") + 1
        return ImageHeader("WEBP", width, height, bool(flags & _VP8X_ANIMATION_FLAG), None)
    if chunk_type == b"VP8 " and len(head) >= 30 and head[23:26] == _VP8_START_CODE:
        width = struct.unpack("<H", head[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", head[28:30])[0] & 0x3FFF
        return ImageHeader("WEBP", width, height, False, None)
    if chunk_type == b"VP8L" and len(head) >= 25 and head[20] == _VP8L_SIGNATURE:
        bits = int.from_bytes(head[21:25], "little")
        return ImageHeader("WEBP", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, False, None)
    return None


def _probe_gif(head: bytes) -> ImageHeader | None:
    if len(head) < 10:
        return None
    width, height = struct.unpack("<HH", head[6:10])
    return ImageHeader("GIF", width, height, False, None)


def _probe_jpeg(path: Path) -> ImageHeader | None:
    """A lazy Pillow open: it reads the markers up to the start of scan, never the image data."""
    with _open_image(path, "JPEG") as image:
        width, height = image.size
        return ImageHeader("JPEG", width, height, False, image.mode)


def probe_image_header(path: Path) -> ImageHeader | None:
    """Header facts of a PNG, JPEG, GIF or WebP file, or None (other format, malformed or unreadable).

    Reads at most the first 64 KiB, plus the 8-byte chunk headers of the PNG walk.
    """
    try:
        with open(path, "rb") as file:
            head = file.read(HEAD_BYTES)
            image_format = sniff_image_format(head)
            if image_format == "PNG":
                header = _probe_png(file, head)
            elif image_format == "WEBP":
                header = _probe_webp(head)
            elif image_format == "GIF":
                header = _probe_gif(head)
            elif image_format == "JPEG":
                header = _probe_jpeg(path)
            else:
                return None
    except Exception:  # noqa: BLE001 - any I/O or Pillow failure makes the entry a file
        logger.debug("Image header probe failed for %s", path, exc_info=True)
        return None
    if header is None or header.width <= 0 or header.height <= 0:
        return None
    return header


# ── Decode and normalize ──


def _open_image(path: Path, image_format: str) -> Image.Image:
    """Open *path* lazily with the single Pillow plugin of *image_format* (a JPEG may come back as MPO).

    The plugin factory is called directly instead of ``Image.open``: its decompression-bomb check
    warns from 89.5 MP (``Image.MAX_IMAGE_PIXELS``), below our own 100 MP limit, and that threshold
    is process-global (other Pillow users rely on it), so it is never changed. Our limit is enforced
    instead: on the header facts before any open, and on the opened size before any decode
    (:func:`_decode_native`). Nothing is decoded here.
    """
    if image_format not in Image.OPEN:
        Image.init()
    factory, _accept = Image.OPEN[image_format]
    # A path argument: the image owns its file, and closes it with the image.
    return factory(os.fspath(path), None)


def _resized_size(width: int, height: int, edge: int) -> tuple[int, int]:
    if width >= height:
        return edge, max(1, round(height * edge / width))
    return max(1, round(width * edge / height)), edge


def _encode(image: Image.Image, image_format: str) -> tuple[bytes, str]:
    """Re-encode a resized image in its own family (spec §6.4): one encode, no quality loop."""
    options = {}
    icc_profile = image.info.get("icc_profile")
    if icc_profile:
        options["icc_profile"] = icc_profile
    if image_format == "JPEG":
        output_format, options["quality"], media_type = "JPEG", 92, "image/jpeg"
    elif image_format == "WEBP":
        output_format, options["lossless"], media_type = "WEBP", True, "image/webp"
    else:  # PNG, and a still GIF
        output_format, media_type = "PNG", "image/png"
    buffer = io.BytesIO()
    image.save(buffer, format=output_format, **options)
    return buffer.getvalue(), media_type


def _resample_ready(image: Image.Image, image_format: str) -> Image.Image:
    """The image in a mode that Lanczos resizes and its family encodes without losing values.

    Palette and bilevel images are converted (Pillow resizes them with nearest otherwise); a 32-bit
    ``I`` PNG becomes 16-bit ``I;16`` (the PNG maximum, clipped correctly). Any other mode would be
    damaged by a conversion (e.g. 16-bit values clipped to 255), so it raises ``ValueError`` and the
    entry becomes a file.
    """
    mode = image.mode
    if mode == "1":
        image = image.convert("L")
    elif mode in ("P", "PA"):
        has_alpha = mode == "PA" or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha else "RGB")
    elif mode == "I" and image_format == "PNG":
        image = image.convert("I;16")
    if image.mode not in _RESAMPLE_MODES[image_format]:
        raise ValueError(f"Unsupported {image_format} mode for resizing: {mode}")
    return image


def _decode_native(path: Path, header: ImageHeader, edge: int) -> tuple[bytes, str] | None:
    """Verify and decode the image; return the bytes to send and their media type, or None.

    Runs only after every header, size and budget check. Within *edge* the original bytes are
    returned; above it the image is transposed, resized and re-encoded.
    """
    with _open_image(path, header.format) as image:
        if image.width * image.height > MAX_PIXELS:
            return None  # the size Pillow opened, not only the probed header: never decode past the limit
        image.verify()
    with _open_image(path, header.format) as image:
        if image.width * image.height > MAX_PIXELS:
            return None
        # The GIF animation probe skips through the first frame's data: only now.
        if header.format == "GIF" and getattr(image, "is_animated", False):
            return None
        image.load()  # decode (an MPO loads its primary frame)
        if max(header.width, header.height) <= edge:
            return path.read_bytes(), IMAGE_MEDIA_TYPES[header.format]
        icc_profile = image.info.get("icc_profile")
        transposed = ImageOps.exif_transpose(image)
    transposed = _resample_ready(transposed, header.format)
    resized = transposed.resize(_resized_size(*transposed.size, edge), Image.Resampling.LANCZOS)
    if icc_profile:
        resized.info["icc_profile"] = icc_profile
    return _encode(resized, header.format)


def normalize_image(path: Path, *, target: PlanTarget, remaining_budget: int) -> NativePart | None:
    """The native part of an eligible image, or None when it must be sent as a file.

    Order (spec §6.4): header facts → 100 MP → animation (PNG, WebP) → Claude CMYK → size and budget
    of the original bytes when within the target dimensions (nothing decoded) → GIF animation probe
    and decode → budget and per-image limit of the bytes actually sent.
    """
    header = probe_image_header(path)
    if header is None or header.animated:
        return None
    if header.width * header.height > MAX_PIXELS:
        return None
    if header.format == "JPEG" and header.mode == "CMYK" and target.provider != PROVIDER_CODEX:
        return None  # the Claude CLI cannot decode a 4-component JPEG
    edge = long_edge(target)
    if max(header.width, header.height) <= edge:
        try:
            size = os.stat(path).st_size
        except OSError:
            return None
        if not _fits(size, target, remaining_budget):
            return None
    try:
        result = _decode_native(path, header, edge)
    except Exception:  # noqa: BLE001 - any Pillow error (open, verify, decode, encode) makes the entry a file
        logger.debug("Image normalization failed for %s", path, exc_info=True)
        return None
    if result is None:
        return None
    data, media_type = result
    if not _fits(len(data), target, remaining_budget):
        return None
    return NativePart("image", media_type, data)
