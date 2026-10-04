"""Bounded image header probes and native normalization of composer attachments (spec §6.3-§6.5).

``normalize_image`` returns the native part of an eligible image, or ``None`` (file fallback).
Header, size and budget checks come before any decode; within the target dimensions the original
bytes are sent unchanged; above them the image is resized once and re-encoded in its own family.
"""

from __future__ import annotations

import builtins
import io
import math
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from twicc.core.services.attachments import images
from twicc.core.services.attachments.images import normalize_image, probe_image_header
from twicc.core.services.attachments.types import PlanTarget

BUDGET = 16 * 1024**2
HEAD = 64 * 1024

claude = PlanTarget("claude_code", False, False, "opus", False, "first_party")
claude_third_party = PlanTarget("claude_code", False, False, "opus", False, "third_party")
claude_hybrid = PlanTarget("claude_code", True, False, "opus", False, "first_party")
codex = PlanTarget("codex", False, False, "gpt", False, "first_party")


def b64_len(n: int) -> int:
    return 4 * math.ceil(n / 3)


# ── Fixture builders ──────────────────────────────────────────────────────────


def gradient(size, mode="RGB") -> Image.Image:
    """A smooth image (compresses well) whose content differs per pixel row."""
    width, height = size
    base = Image.linear_gradient("L").resize((width, height))
    if mode == "L":
        return base
    return Image.merge("RGB", (base, base.transpose(Image.Transpose.FLIP_LEFT_RIGHT), base)).convert(mode)


def encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunks.append((data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
    return chunks


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def build_png(chunks: list[tuple[bytes, bytes]]) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunk(kind, body) for kind, body in chunks)


def with_chunks_after_ihdr(data: bytes, extra: list[tuple[bytes, bytes]]) -> bytes:
    chunks = png_chunks(data)
    return build_png(chunks[:1] + extra + chunks[1:])


def orientation_exif(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    return exif.tobytes()


def ihdr(width: int, height: int) -> bytes:
    return struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)


@pytest.fixture
def small_png(tmp_path) -> Path:
    return write(tmp_path, "small.png", encode(gradient((40, 30)), "PNG"))


@pytest.fixture
def animated_gif(tmp_path) -> Path:
    frames = [Image.new("RGB", (20, 20), (255, 0, 0)), Image.new("RGB", (20, 20), (0, 0, 255))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    assert Image.open(io.BytesIO(buffer.getvalue())).is_animated
    return write(tmp_path, "anim.gif", buffer.getvalue())


class OpenSpy:
    """Records the calls to ``images._open_image`` (every Pillow open of the module)."""

    def __init__(self, monkeypatch, fail=False):
        self.calls = []
        self.fail = fail
        self.original = images._open_image
        monkeypatch.setattr(images, "_open_image", self)

    def __call__(self, path, image_format):
        self.calls.append(image_format)
        if self.fail:
            raise AssertionError("Pillow must not open this file")
        return self.original(path, image_format)


class DecodeSpy:
    def __init__(self, monkeypatch):
        self.calls = 0
        original = images._decode_native

        def spy(*args, **kwargs):
            self.calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(images, "_decode_native", spy)


class SaveSpy:
    """Records every ``Image.save`` call (format + options)."""

    def __init__(self, monkeypatch, fail=False):
        self.calls = []
        original = Image.Image.save

        def save(image, fp, format=None, **params):
            self.calls.append((format, params))
            if fail:
                raise OSError("encoder failure")
            return original(image, fp, format=format, **params)

        monkeypatch.setattr(Image.Image, "save", save)


class ReadTracker:
    """Shadows ``open`` in the images module and records the bytes read through it."""

    def __init__(self, monkeypatch):
        self.read_bytes = 0
        monkeypatch.setattr(images, "open", self.open, raising=False)

    def open(self, path, mode="r", *args, **kwargs):
        file = builtins.open(path, mode, *args, **kwargs)
        original_read = file.read
        tracker = self

        def read(n=-1):
            assert n is not None and n >= 0, "unbounded read"
            data = original_read(n)
            tracker.read_bytes += len(data)
            return data

        file.read = read
        return file


def decoded(part) -> Image.Image:
    image = Image.open(io.BytesIO(part.data))
    image.load()
    return image


# ── Within the target: original bytes ────────────────────────────────────────


def test_small_png_is_sent_unchanged(small_png):
    part = normalize_image(small_png, target=claude, remaining_budget=16 * 1024**2)
    assert part.data == small_png.read_bytes()
    assert part.kind == "image"
    assert part.media_type == "image/png"


@pytest.mark.parametrize(
    ("fmt", "media_type", "options"),
    [
        ("JPEG", "image/jpeg", {}),
        ("GIF", "image/gif", {}),
        ("WEBP", "image/webp", {}),
        ("WEBP", "image/webp", {"lossless": True}),
    ],
)
@pytest.mark.parametrize("target", [claude, claude_third_party, claude_hybrid, codex], ids=lambda t: t.platform)
def test_within_target_images_keep_their_bytes(tmp_path, fmt, media_type, options, target):
    path = write(tmp_path, "img", encode(gradient((64, 48)), fmt, **options))
    part = normalize_image(path, target=target, remaining_budget=BUDGET)
    assert part.data == path.read_bytes()
    assert part.media_type == media_type


def test_exactly_at_the_long_edge_is_within_target(tmp_path):
    path = write(tmp_path, "a.png", encode(gradient((2000, 10)), "PNG"))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET).data == path.read_bytes()
    path = write(tmp_path, "b.png", encode(gradient((10, 2576)), "PNG"))
    assert normalize_image(path, target=codex, remaining_budget=BUDGET).data == path.read_bytes()


def test_non_native_raster_is_a_file(tmp_path):
    path = write(tmp_path, "pic.bmp", encode(gradient((8, 8)), "BMP"))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_non_image_is_a_file(tmp_path):
    path = write(tmp_path, "notes.png", b"just text")
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


# ── Animation ─────────────────────────────────────────────────────────────────


def test_animated_gif_is_a_file(animated_gif):
    assert normalize_image(animated_gif, target=claude, remaining_budget=16 * 1024**2) is None
    assert normalize_image(animated_gif, target=codex, remaining_budget=BUDGET) is None


def apng_bytes() -> bytes:
    frames = [gradient((20, 20)), Image.new("RGB", (20, 20), (255, 0, 0))]
    data = encode(frames[0], "PNG", save_all=True, append_images=frames[1:], duration=50)
    kinds = [kind for kind, _ in png_chunks(data)]
    assert kinds.index(b"acTL") < kinds.index(b"IDAT")
    return data


def test_apng_is_a_file_without_pillow(tmp_path, monkeypatch):
    path = write(tmp_path, "anim.png", apng_bytes())
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None
    assert spy.calls == []


def test_acTL_found_past_the_64_kib_head_by_a_chunk_walk(tmp_path, monkeypatch):
    big = (b"tEXt", b"comment\x00" + b"x" * (3 * HEAD))
    path = write(tmp_path, "anim.png", with_chunks_after_ihdr(apng_bytes(), [big]))
    tracker = ReadTracker(monkeypatch)
    assert probe_image_header(path).animated is True
    assert tracker.read_bytes < HEAD + 1024  # the big chunk data is seeked past, never read
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_still_png_with_a_large_chunk_before_IDAT(tmp_path):
    big = (b"tEXt", b"comment\x00" + b"x" * (3 * HEAD))
    path = write(tmp_path, "still.png", with_chunks_after_ihdr(encode(gradient((30, 20)), "PNG"), [big]))
    header = probe_image_header(path)
    assert (header.format, header.width, header.height, header.animated) == ("PNG", 30, 20, False)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET).data == path.read_bytes()


def test_acTL_after_IDAT_is_not_animation(tmp_path):
    chunks = png_chunks(encode(gradient((30, 20)), "PNG"))
    actl = (b"acTL", struct.pack(">II", 1, 0))
    data = build_png(chunks[:-1] + [actl] + chunks[-1:])
    path = write(tmp_path, "still.png", data)
    assert probe_image_header(path).animated is False


def test_png_without_IDAT_is_a_file(tmp_path):
    path = write(tmp_path, "empty.png", build_png([(b"IHDR", ihdr(10, 10)), (b"IEND", b"")]))
    assert probe_image_header(path) is None
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_png_chunk_walk_stops_at_1000_chunks(tmp_path):
    base = encode(gradient((10, 10)), "PNG")
    assert [kind for kind, _ in png_chunks(base)] == [b"IHDR", b"IDAT", b"IEND"]
    fits = with_chunks_after_ihdr(base, [(b"tEXt", b"k\x00v")] * 998)  # IHDR + 998 + IDAT = 1000 chunks
    path = write(tmp_path, "fits.png", fits)
    assert probe_image_header(path) is not None
    assert normalize_image(path, target=claude, remaining_budget=BUDGET).data == fits
    over = with_chunks_after_ihdr(base, [(b"tEXt", b"k\x00v")] * 999)
    path = write(tmp_path, "over.png", over)
    assert probe_image_header(path) is None
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_png_oversized_chunk_length_is_a_file(tmp_path):
    chunks = png_chunks(encode(gradient((10, 10)), "PNG"))
    data = build_png(chunks[:1]) + struct.pack(">I", 0x80000000) + b"tEXt" + b"\x00" * 64
    path = write(tmp_path, "bad.png", data)
    assert probe_image_header(path) is None
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_png_with_a_bad_IHDR_is_a_file(tmp_path):
    path = write(tmp_path, "bad.png", build_png([(b"IHDR", ihdr(0, 10)), (b"IDAT", b""), (b"IEND", b"")]))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


# ── WebP header probe (never through Pillow) ─────────────────────────────────


def riff(chunks: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


def vp8x(width: int, height: int, flags: int) -> bytes:
    body = bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"VP8X" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize(
    ("options", "chunk_type"),
    [({}, b"VP8 "), ({"lossless": True}, b"VP8L"), ({"exif": orientation_exif(1)}, b"VP8X")],
)
def test_webp_header_probe(tmp_path, monkeypatch, options, chunk_type):
    data = encode(gradient((123, 45)), "WEBP", **options)
    assert data[12:16] == chunk_type
    path = write(tmp_path, "img.webp", data)
    spy = OpenSpy(monkeypatch, fail=True)
    header = probe_image_header(path)
    assert (header.format, header.width, header.height, header.animated) == ("WEBP", 123, 45, False)
    # Within the dimensions but over the budget: decided without Pillow.
    assert normalize_image(path, target=claude, remaining_budget=b64_len(len(data)) - 1) is None
    assert spy.calls == []


def test_animated_webp_is_a_file_without_pillow(tmp_path, monkeypatch):
    path = write(tmp_path, "anim.webp", riff(vp8x(20, 20, 0x02) + b"ANIM" + struct.pack("<I", 6) + b"\x00" * 6))
    spy = OpenSpy(monkeypatch, fail=True)
    assert probe_image_header(path).animated is True
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert spy.calls == []


def test_webp_over_100_megapixels_is_a_file_without_pillow(tmp_path, monkeypatch):
    path = write(tmp_path, "huge.webp", riff(vp8x(12000, 9000, 0)))
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert spy.calls == []


# ── JPEG ──────────────────────────────────────────────────────────────────────


def test_jpeg_with_a_large_icc_profile(tmp_path):
    data = encode(gradient((64, 48)), "JPEG", icc_profile=b"\x01" * (3 * HEAD))
    path = write(tmp_path, "icc.jpg", data)
    header = probe_image_header(path)
    assert (header.format, header.width, header.height) == ("JPEG", 64, 48)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET).data == data


def mpo_bytes(size) -> bytes:
    primary, second = gradient(size), Image.new("RGB", size, (0, 0, 255))
    data = encode(primary, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(data)).format == "MPO"
    return data


def test_mpo_within_target_is_a_plain_jpeg(tmp_path):
    path = write(tmp_path, "photo.jpg", mpo_bytes((64, 48)))
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    assert part.media_type == "image/jpeg"
    assert part.data == path.read_bytes()


def test_mpo_above_target_sends_the_resized_primary_frame(tmp_path):
    path = write(tmp_path, "photo.jpg", mpo_bytes((3000, 1500)))
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    image = decoded(part)
    assert (part.media_type, image.format, image.size) == ("image/jpeg", "JPEG", (2000, 1000))
    assert image.getpixel((1000, 500))[2] < 250  # the primary frame, not the blue second one


def test_cmyk_jpeg_depends_on_the_provider(tmp_path):
    path = write(tmp_path, "cmyk.jpg", encode(Image.new("CMYK", (32, 32), (0, 50, 100, 0)), "JPEG"))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None
    assert normalize_image(path, target=claude_hybrid, remaining_budget=BUDGET) is None
    assert normalize_image(path, target=codex, remaining_budget=BUDGET).data == path.read_bytes()


def test_cmyk_jpeg_above_target_stays_cmyk_for_codex(tmp_path):
    path = write(tmp_path, "cmyk.jpg", encode(Image.new("CMYK", (3000, 100), (0, 50, 100, 0)), "JPEG"))
    image = decoded(normalize_image(path, target=codex, remaining_budget=BUDGET))
    assert (image.mode, image.size) == ("CMYK", (2576, 86))


def patch_jpeg_size(data: bytes, width: int, height: int) -> bytes:
    sof = data.index(b"\xff\xc0")
    return data[: sof + 5] + struct.pack(">HH", height, width) + data[sof + 9 :]


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_jpeg_over_100_megapixels_is_a_file_before_decode(tmp_path, monkeypatch):
    path = write(tmp_path, "huge.jpg", patch_jpeg_size(encode(gradient((16, 16)), "JPEG"), 12000, 9000))
    assert probe_image_header(path)[1:3] == (12000, 9000)
    decode = DecodeSpy(monkeypatch)
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert decode.calls == 0


def test_png_over_100_megapixels_is_a_file_without_pillow(tmp_path, monkeypatch):
    data = with_chunks_after_ihdr(encode(gradient((10, 10)), "PNG"), [])
    chunks = png_chunks(data)
    path = write(tmp_path, "huge.png", build_png([(b"IHDR", ihdr(10001, 10000))] + chunks[1:]))
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert spy.calls == []


def test_exactly_100_megapixels_passes_the_pixel_check(tmp_path, monkeypatch):
    data = encode(gradient((10, 10)), "PNG")
    path = write(tmp_path, "edge.png", build_png([(b"IHDR", ihdr(10000, 10000))] + png_chunks(data)[1:]))
    calls = []
    # Stub the decode stage: a real 100 MP decode would allocate hundreds of MB.
    monkeypatch.setattr(images, "_decode_native", lambda *args, **kwargs: calls.append(args) or None)
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert len(calls) == 1


def test_gif_over_100_megapixels_is_a_file_without_pillow(tmp_path, monkeypatch):
    data = bytearray(encode(Image.new("P", (10, 10)), "GIF"))
    data[6:10] = struct.pack("<HH", 65535, 65535)
    path = write(tmp_path, "huge.gif", bytes(data))
    assert probe_image_header(path)[1:3] == (65535, 65535)
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is None
    assert spy.calls == []


# ── Budget and per-image limit before any decode ─────────────────────────────


@pytest.mark.parametrize("fmt", ["PNG", "GIF", "WEBP"])
def test_within_dimension_over_budget_never_opens_pillow(tmp_path, monkeypatch, fmt):
    data = encode(gradient((64, 48)), fmt)
    path = write(tmp_path, "img", data)
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=claude, remaining_budget=b64_len(len(data)) - 1) is None
    assert spy.calls == []


def test_within_dimension_over_budget_animated_gif_skips_the_animation_probe(animated_gif, monkeypatch):
    spy = OpenSpy(monkeypatch, fail=True)
    assert normalize_image(animated_gif, target=claude, remaining_budget=10) is None
    assert spy.calls == []


def test_within_dimension_over_budget_jpeg_is_not_decoded(tmp_path, monkeypatch):
    data = encode(gradient((64, 48)), "JPEG")
    path = write(tmp_path, "img.jpg", data)
    decode = DecodeSpy(monkeypatch)
    assert normalize_image(path, target=claude, remaining_budget=b64_len(len(data)) - 1) is None
    assert decode.calls == 0


def test_budget_equality_fits(small_png):
    measure = b64_len(small_png.stat().st_size)
    assert normalize_image(small_png, target=claude, remaining_budget=measure) is not None
    assert normalize_image(small_png, target=claude, remaining_budget=measure - 1) is None


def test_per_image_limits():
    assert images.per_image_limit(claude) == 10_485_760
    assert images.per_image_limit(claude_hybrid) == 10_485_760
    assert images.per_image_limit(claude_third_party) == 5_242_880
    assert images.per_image_limit(codex) is None
    assert images.long_edge(claude) == 2000
    assert images.long_edge(claude_hybrid) == 2000
    assert images.long_edge(codex) == 2576


def test_within_dimension_over_the_per_image_limit_is_not_decoded(small_png, monkeypatch):
    measure = b64_len(small_png.stat().st_size)
    monkeypatch.setitem(images.CLAUDE_PER_IMAGE_LIMITS, "third_party", measure - 1)
    monkeypatch.setitem(images.CLAUDE_PER_IMAGE_LIMITS, "first_party", measure)
    spy = OpenSpy(monkeypatch)
    assert normalize_image(small_png, target=claude_third_party, remaining_budget=BUDGET) is None
    assert spy.calls == []
    assert normalize_image(small_png, target=claude, remaining_budget=BUDGET) is not None
    assert normalize_image(small_png, target=codex, remaining_budget=BUDGET) is not None


# ── Above the target: resize once, same family ───────────────────────────────


@pytest.mark.parametrize(("target", "expected"), [(claude, (2000, 1000)), (codex, (2576, 1288))])
def test_png_resize_keeps_aspect_and_family(tmp_path, monkeypatch, target, expected):
    path = write(tmp_path, "big.png", encode(gradient((3000, 1500)), "PNG"))
    saves = SaveSpy(monkeypatch)
    part = normalize_image(path, target=target, remaining_budget=BUDGET)
    image = decoded(part)
    assert (part.media_type, image.format, image.size) == ("image/png", "PNG", expected)
    assert [fmt for fmt, _ in saves.calls] == ["PNG"]


def test_portrait_resize(tmp_path):
    path = write(tmp_path, "tall.png", encode(gradient((1000, 3000)), "PNG"))
    assert decoded(normalize_image(path, target=claude, remaining_budget=BUDGET)).size == (667, 2000)


def test_webp_resizes_to_lossless_webp(tmp_path, monkeypatch):
    path = write(tmp_path, "big.webp", encode(gradient((3000, 1500)), "WEBP", quality=80))
    saves = SaveSpy(monkeypatch)
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    assert part.media_type == "image/webp"
    assert part.data[12:16] == b"VP8L"
    assert decoded(part).size == (2000, 1000)
    assert saves.calls == [("WEBP", {"lossless": True})]


def test_jpeg_resizes_to_jpeg_quality_92_once(tmp_path, monkeypatch):
    path = write(tmp_path, "big.jpg", encode(gradient((3000, 1500)), "JPEG", quality=70))
    saves = SaveSpy(monkeypatch)
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    assert part.media_type == "image/jpeg"
    assert decoded(part).size == (2000, 1000)
    assert saves.calls == [("JPEG", {"quality": 92})]


def test_still_gif_resizes_to_png(tmp_path):
    path = write(tmp_path, "big.gif", encode(gradient((3000, 300)).convert("P"), "GIF"))
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    image = decoded(part)
    assert (part.media_type, image.format, image.size) == ("image/png", "PNG", (2000, 200))


def test_16_bit_grayscale_png_keeps_its_depth_when_resized(tmp_path):
    data = encode(Image.new("I;16", (3000, 100), 30000), "PNG")
    path = write(tmp_path, "deep.png", data)
    assert Image.open(path).mode == "I;16"
    part = normalize_image(path, target=claude, remaining_budget=BUDGET)
    image = decoded(part)
    assert (part.media_type, image.mode, image.size) == ("image/png", "I;16", (2000, 67))
    assert abs(image.getpixel((1000, 33)) - 30000) <= 1  # mid-gray, not clipped to white


@pytest.mark.parametrize(
    ("mode", "image_format", "value", "expected_mode", "expected"),
    [
        ("I;16", "PNG", 30000, "I;16", 30000),
        ("I;16B", "PNG", 30000, "I;16B", 30000),
        ("I", "PNG", 30000, "I;16", 30000),
        ("1", "PNG", 1, "L", 255),
        ("L", "JPEG", 100, "L", 100),
        ("CMYK", "JPEG", (1, 2, 3, 4), "CMYK", (1, 2, 3, 4)),
    ],
)
def test_resample_modes_preserve_values(mode, image_format, value, expected_mode, expected):
    image = images._resample_ready(Image.new(mode, (4, 4), value), image_format)
    assert (image.mode, image.getpixel((1, 1))) == (expected_mode, expected)


@pytest.mark.parametrize(
    ("mode", "image_format"),
    [("F", "PNG"), ("I;16L", "PNG"), ("I", "WEBP"), ("I;16", "GIF"), ("I;16", "JPEG"), ("RGBA", "JPEG")],
)
def test_resample_refuses_modes_it_would_corrupt(mode, image_format):
    with pytest.raises(ValueError):
        images._resample_ready(Image.new(mode, (4, 4)), image_format)


def test_unsupported_mode_above_target_is_a_file(tmp_path, monkeypatch):
    path = write(tmp_path, "big.png", encode(gradient((3000, 1500)), "PNG"))
    original = images._resample_ready
    monkeypatch.setattr(images, "_resample_ready", lambda image, fmt: original(image.convert("F"), fmt))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_exif_transpose_happens_before_resize(tmp_path):
    # Orientation 6: rotate 90° clockwise to display.
    path = write(tmp_path, "rotated.jpg", encode(gradient((3000, 1000)), "JPEG", exif=orientation_exif(6)))
    image = decoded(normalize_image(path, target=claude, remaining_budget=BUDGET))
    assert image.size == (667, 2000)


def test_within_target_jpeg_keeps_its_exif_orientation_bytes(tmp_path):
    path = write(tmp_path, "rotated.jpg", encode(gradient((300, 100)), "JPEG", exif=orientation_exif(6)))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET).data == path.read_bytes()


def test_no_byte_saving_loop_when_resized_bytes_exceed_the_budget(tmp_path, monkeypatch):
    path = write(tmp_path, "big.jpg", encode(gradient((3000, 1500)), "JPEG"))
    saves = SaveSpy(monkeypatch)
    assert normalize_image(path, target=claude, remaining_budget=100) is None
    assert len(saves.calls) == 1


def test_post_resize_per_image_limit_is_enforced(tmp_path, monkeypatch):
    path = write(tmp_path, "big.png", encode(gradient((3000, 1500)), "PNG"))
    monkeypatch.setitem(images.CLAUDE_PER_IMAGE_LIMITS, "first_party", 100)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None
    assert normalize_image(path, target=codex, remaining_budget=BUDGET) is not None


# ── Pillow errors become file fallback ───────────────────────────────────────


def test_open_error_is_a_file(tmp_path):
    path = write(tmp_path, "bad.jpg", b"\xff\xd8\xff" + b"garbage" * 10)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_verify_error_is_a_file(tmp_path):
    chunks = png_chunks(encode(gradient((20, 20)), "PNG"))
    data = bytearray(build_png(chunks))
    idat = data.index(b"IDAT")
    data[idat + 4] ^= 0xFF  # corrupt the compressed data: the CRC no longer matches
    path = write(tmp_path, "crc.png", bytes(data))
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_decode_error_is_a_file(tmp_path):
    data = encode(gradient((200, 200)), "JPEG")
    path = write(tmp_path, "cut.jpg", data[: len(data) // 2])
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_encode_error_is_a_file(tmp_path, monkeypatch):
    path = write(tmp_path, "big.png", encode(gradient((3000, 1500)), "PNG"))
    SaveSpy(monkeypatch, fail=True)
    assert normalize_image(path, target=claude, remaining_budget=BUDGET) is None


def test_missing_file_is_a_file(tmp_path):
    assert normalize_image(tmp_path / "gone.png", target=claude, remaining_budget=BUDGET) is None
