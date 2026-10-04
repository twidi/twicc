"""Bounded kind detection of composer attachments (spec §6.4).

Detection reads at most the first 64 KiB of a file. Images are recognized by magic bytes, then by
Pillow opening the head bytes alone (never native); the text and NUL rules apply to the head only.
"""

from __future__ import annotations

import builtins
import io
import struct
from pathlib import Path

import pytest
from PIL import Image

from twicc.core.services.attachments import planner
from twicc.core.services.attachments.planner import detect_kind

HEAD = 64 * 1024


def image_bytes(fmt: str, size=(4, 3), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, 128).save(buffer, format=fmt)
    return buffer.getvalue()


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def kind_of(tmp_path: Path, name: str, data: bytes, *, stored_as: str = "stored") -> str:
    path = write(tmp_path, stored_as, data)
    return detect_kind(path, name, len(data))


class ReadTracker:
    """Shadows ``open`` in a module and records every byte read through it."""

    def __init__(self):
        self.read_bytes = 0
        self.opens = 0

    def open(self, path, mode="r", *args, **kwargs):
        self.opens += 1
        file = builtins.open(path, mode, *args, **kwargs)
        tracker = self
        original_read = file.read

        def read(n=-1):
            assert n is not None and n >= 0, "unbounded read"
            data = original_read(n)
            tracker.read_bytes += len(data)
            return data

        file.read = read
        return file


@pytest.fixture
def tracked_reads(monkeypatch):
    tracker = ReadTracker()
    monkeypatch.setattr(planner, "open", tracker.open, raising=False)
    return tracker


# ── Empty and text ────────────────────────────────────────────────────────────


def test_empty_file_is_other(tmp_path):
    empty_path = write(tmp_path, "empty.txt", b"")
    assert detect_kind(empty_path, "empty.txt", 0) == "other"


def test_utf8_text(tmp_path):
    assert kind_of(tmp_path, "notes.txt", "héllo wörld\n".encode()) == "text"
    assert kind_of(tmp_path, "noext", b"plain") == "text"


def test_multibyte_sequence_cut_at_the_head_boundary_is_text(tmp_path, tracked_reads):
    data = ("a" * (HEAD - 1) + "é").encode()  # the two-byte "é" straddles the 64 KiB boundary
    assert len(data) == 65537
    cut_utf8_path = write(tmp_path, "notes", data)
    assert detect_kind(cut_utf8_path, "notes", 65537) == "text"
    assert tracked_reads.read_bytes <= HEAD


def test_a_nul_byte_in_the_head_is_not_text(tmp_path):
    assert kind_of(tmp_path, "notes.txt", b"hello\x00world") == "other"


def test_a_nul_byte_after_the_head_is_ignored(tmp_path):
    assert kind_of(tmp_path, "notes.txt", b"a" * HEAD + b"\x00") == "text"


def test_invalid_utf8_away_from_the_cut_boundary_is_not_text(tmp_path):
    # Latin-1 byte in the middle of a long head.
    data = b"a" * 1000 + b"caf\xe9 " + b"b" * (HEAD * 2)
    assert kind_of(tmp_path, "notes.txt", data) == "other"


def test_a_truncated_sequence_at_the_end_of_a_short_file_is_not_text(tmp_path):
    # The cut tolerance applies only at the bounded-head boundary, never at the end of a whole file.
    assert kind_of(tmp_path, "notes.txt", "café".encode()[:-1]) == "other"


def test_invalid_byte_at_the_head_boundary_is_not_text(tmp_path):
    # A byte that can never start a sequence is not a "cut" sequence.
    data = b"a" * (HEAD - 1) + b"\xff" + b"a" * 10
    assert kind_of(tmp_path, "notes.txt", data) == "other"


# ── PDF ───────────────────────────────────────────────────────────────────────


def test_pdf_by_magic(tmp_path):
    assert kind_of(tmp_path, "doc.bin", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 10) == "PDF"


# ── Images by magic bytes ────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_standard_raster_magic(tmp_path, fmt):
    assert kind_of(tmp_path, "file.dat", image_bytes(fmt)) == "image"


def test_bmp_is_an_image(tmp_path):
    assert kind_of(tmp_path, "pic.bmp", image_bytes("BMP")) == "image"


def test_unsupported_heic_falls_through(tmp_path):
    heic = struct.pack(">I", 24) + b"ftypheic" + b"\x00\x00\x00\x00mif1heic" + b"\x00" * 200
    assert kind_of(tmp_path, "photo.heic", heic) == "other"


def tiff_with_ifd_at(offset: int) -> bytes:
    """A minimal little-endian 1x1 8-bit grayscale TIFF whose IFD starts at *offset*."""
    pixel_offset = 8
    entries = [
        (256, 3, 1, 1),  # ImageWidth
        (257, 3, 1, 1),  # ImageLength
        (258, 3, 1, 8),  # BitsPerSample
        (259, 3, 1, 1),  # Compression: none
        (262, 3, 1, 1),  # Photometric: BlackIsZero
        (273, 4, 1, pixel_offset),  # StripOffsets
        (277, 3, 1, 1),  # SamplesPerPixel
        (278, 3, 1, 1),  # RowsPerStrip
        (279, 4, 1, 1),  # StripByteCounts
    ]
    ifd = struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        packed = struct.pack("<HHI", tag, typ, count)
        packed += struct.pack("<HH", value, 0) if typ == 3 else struct.pack("<I", value)
        ifd += packed
    ifd += struct.pack("<I", 0)
    data = bytearray(b"II*\x00" + struct.pack("<I", offset) + b"\x80")
    data += b"\x00" * (offset - len(data))
    data += ifd
    return bytes(data)


def test_tiff_with_ifd_in_the_head_is_an_image(tmp_path):
    data = tiff_with_ifd_at(16)
    assert Image.open(io.BytesIO(data)).size == (1, 1)
    assert kind_of(tmp_path, "scan.tiff", data) == "image"


def test_tiff_with_ifd_beyond_64_kib_falls_through(tmp_path, tracked_reads):
    data = tiff_with_ifd_at(HEAD + 4096)
    assert Image.open(io.BytesIO(data)).size == (1, 1)  # a valid TIFF
    assert kind_of(tmp_path, "scan.tiff", data) == "other"
    assert tracked_reads.read_bytes <= HEAD


# ── Misleading extensions ─────────────────────────────────────────────────────


def test_text_named_as_an_image_is_text(tmp_path):
    assert kind_of(tmp_path, "photo.png", b"just some text") == "text"


def test_png_named_as_text_is_an_image(tmp_path):
    assert kind_of(tmp_path, "notes.txt", image_bytes("PNG")) == "image"


def test_pdf_named_as_an_image_is_a_pdf(tmp_path):
    assert kind_of(tmp_path, "photo.jpg", b"%PDF-1.4\n") == "PDF"


def test_text_named_as_audio_is_text(tmp_path):
    assert kind_of(tmp_path, "song.mp3", b"lyrics only") == "text"


@pytest.mark.parametrize(("name", "kind"), [("clip.mp4", "video"), ("song.mp3", "audio"), ("blob.bin", "other")])
def test_binary_falls_back_to_the_name(tmp_path, name, kind):
    assert kind_of(tmp_path, name, b"\x00\x01\x02\x03" * 100) == kind


# ── Bounded reads ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.4\n" + b"x" * (HEAD * 8),
        b"t" * (HEAD * 8),
        b"\x00\x01" * (HEAD * 4),
    ],
    ids=["pdf", "text", "other"],
)
def test_large_files_are_classified_from_the_head_only(tmp_path, tracked_reads, data):
    path = write(tmp_path, "big", data)
    detect_kind(path, "big", len(data))
    assert tracked_reads.opens == 1
    assert tracked_reads.read_bytes <= HEAD
