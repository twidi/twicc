from pathlib import Path

import pytest

from twicc.core.services.attachments.manifest import build_manifest, parse_manifest
from twicc.core.services.attachments.types import AttachmentManifest, ManifestEntry

DIRECTORY = Path("/home/u/.twicc/artifacts/dc41829c-21fa-4714-b397-3123404319c6/attachments")
DIR_LINE = "file = /home/u/.twicc/artifacts/dc41829c-21fa-4714-b397-3123404319c6/attachments/"
INTRO = "Files the user attached to this message, in the order they attached them."
SDK_INLINE = "inline = sent to you with this message; the inline files appear above, in this same order."
HYBRID_INLINE = "inline = attached to this message through the @ reference at the end of its line."

TRICKY_NAMES = [
    "plain.png",
    "notes (1).txt",
    "a (b) c.txt",
    "x): @y.txt",
    "a&b.txt",
    "<tag>.txt",
    ">x<.txt",
    "mail@host.txt",
    "@start.txt",
    "literal &#64; here.txt",
    "&amp;&lt;&gt;&#64;.txt",
    "a&#64;@<>.png",
    "end (inline).png",
    "z (image 1 of 1, inline)",
]


def entry(n, name, kind, rank, of, mode, artifact_name=None):
    return ManifestEntry(n, name, kind, rank, of, mode, artifact_name)


def manifest(*entries, directory=DIRECTORY):
    return AttachmentManifest("owner", directory, tuple(entries))


def test_all_inline_snapshot():
    block = build_manifest(manifest(entry(1, "a.png", "image", 1, 2, "inline"), entry(2, "b.png", "image", 2, 2, "inline"), directory=None))
    assert block == (
        "<twicc:attachments>\n"
        f"{INTRO}\n"
        f"{SDK_INLINE}\n"
        "1. a.png (image 1 of 2, inline)\n"
        "2. b.png (image 2 of 2, inline)\n"
        "</twicc:attachments>"
    )


def test_all_file_snapshot():
    block = build_manifest(manifest(entry(1, "c.mp4", "video", 1, 1, "file", "c (1).mp4")))
    assert block == (
        "<twicc:attachments>\n"
        f"{INTRO}\n"
        f"{DIR_LINE}\n"
        "1. c (1).mp4 (video 1 of 1, file)\n"
        "</twicc:attachments>"
    )


def test_mixed_snapshot_uses_final_name_for_files():
    block = build_manifest(
        manifest(
            entry(1, "login.png", "image", 1, 2, "inline"),
            entry(2, "capture.mp4", "video", 1, 1, "file", "capture.mp4"),
            entry(3, "spec.pdf", "PDF", 1, 1, "inline"),
            entry(4, "notes.txt", "text", 1, 1, "file", "notes (1).txt"),
            entry(5, "after.png", "image", 2, 2, "inline"),
        )
    )
    assert block == (
        "<twicc:attachments>\n"
        f"{INTRO}\n"
        f"{SDK_INLINE}\n"
        f"{DIR_LINE}\n"
        "1. login.png (image 1 of 2, inline)\n"
        "2. capture.mp4 (video 1 of 1, file)\n"
        "3. spec.pdf (PDF 1 of 1, inline)\n"
        "4. notes (1).txt (text 1 of 1, file)\n"
        "5. after.png (image 2 of 2, inline)\n"
        "</twicc:attachments>"
    )
    assert block.count("</twicc:attachments>") == 1


def test_hybrid_snapshot():
    m = manifest(
        entry(1, "a.png", "image", 1, 1, "inline"),
        entry(2, "d.zip", "other", 1, 1, "file", "d.zip"),
        entry(3, "n.txt", "text", 1, 1, "inline"),
    )
    block = build_manifest(m, hybrid_paths=("/d/hybrid/s/att_0123456789ab.png", None, "/d/hybrid/s/att_abcdef012345"))
    assert block == (
        "<twicc:attachments>\n"
        f"{INTRO}\n"
        f"{HYBRID_INLINE}\n"
        f"{DIR_LINE}\n"
        "1. a.png (image 1 of 1, inline): @/d/hybrid/s/att_0123456789ab.png\n"
        "2. d.zip (other 1 of 1, file)\n"
        "3. n.txt (text 1 of 1, inline): @/d/hybrid/s/att_abcdef012345\n"
        "</twicc:attachments>"
    )
    parsed = parse_manifest(block)
    assert parsed.hybrid is True
    assert parsed.hybrid_paths == ("/d/hybrid/s/att_0123456789ab.png", None, "/d/hybrid/s/att_abcdef012345")


def test_escaping_literal():
    block = build_manifest(manifest(entry(1, "a&#64;@<>.png", "image", 1, 1, "inline"), directory=None))
    assert "1. a&amp;#64;&#64;&lt;&gt;.png (image 1 of 1, inline)" in block
    assert block.startswith(f"<twicc:attachments>\n{INTRO}\n")
    assert parse_manifest(block).entries[0].name == "a&#64;@<>.png"


@pytest.mark.parametrize("name", TRICKY_NAMES)
@pytest.mark.parametrize("hybrid", [False, True])
def test_round_trip_tricky_names(name, hybrid):
    m = manifest(
        entry(1, name, "image", 1, 1, "inline"),
        entry(2, name, "text", 1, 1, "file", name),
    )
    paths = ("/d/hybrid/s/att_0123456789ab.png", None) if hybrid else None
    parsed = parse_manifest(build_manifest(m, hybrid_paths=paths))
    assert parsed is not None
    assert parsed.entries == (
        entry(1, name, "image", 1, 1, "inline"),
        entry(2, name, "text", 1, 1, "file", name),
    )
    assert parsed.directory == DIR_LINE.removeprefix("file = ")
    assert parsed.hybrid is hybrid
    assert parsed.hybrid_paths == (paths if hybrid else (None, None))


def test_parse_all_inline_has_no_directory():
    parsed = parse_manifest(build_manifest(manifest(entry(1, "a.png", "image", 1, 1, "inline"), directory=None)))
    assert parsed.directory is None
    assert parsed.hybrid is False
    assert parsed.hybrid_paths == (None,)


def test_parse_tolerates_surrounding_whitespace():
    block = build_manifest(manifest(entry(1, "a.png", "image", 1, 1, "inline"), directory=None))
    assert parse_manifest(f"\n  {block}\n\n") is not None


def test_file_entry_without_artifact_name_falls_back_to_name():
    block = build_manifest(manifest(entry(1, "a.zip", "other", 1, 1, "file")))
    assert "1. a.zip (other 1 of 1, file)" in block


def _good():
    return build_manifest(
        manifest(entry(1, "a.png", "image", 1, 1, "inline"), entry(2, "b.mp4", "video", 1, 1, "file", "b.mp4"))
    )


@pytest.mark.parametrize(
    "text",
    [
        "<twicc:attachments>\nbad\n</twicc:attachments>",
        "<twicc:attachments>\n</twicc:attachments>",
        "<twicc:attachments>\n\n</twicc:attachments>",
        f"<twicc:attachments>\n{INTRO}\n</twicc:attachments>",
        "no block at all",
        "",
    ],
)
def test_parse_rejects_malformed(text):
    assert parse_manifest(text) is None


def test_parse_rejects_wrong_headers_and_structure():
    good = _good()
    assert parse_manifest(good) is not None
    # wrong intro
    assert parse_manifest(good.replace(INTRO, "Files attached.")) is None
    # wrong inline header
    assert parse_manifest(good.replace(SDK_INLINE, "inline = something else")) is None
    # inline line missing while an inline entry exists
    assert parse_manifest(good.replace(SDK_INLINE + "\n", "")) is None
    # file line missing while a file entry exists
    assert parse_manifest(good.replace(DIR_LINE + "\n", "")) is None
    # relative directory
    assert parse_manifest(good.replace("file = /home", "file = home")) is None
    # directory without trailing slash
    assert parse_manifest(good.replace("attachments/\n", "attachments\n")) is None
    # extra line
    assert parse_manifest(good.replace("</twicc:attachments>", "extra\n</twicc:attachments>")) is None
    # blank line inside
    assert parse_manifest(good.replace(INTRO + "\n", INTRO + "\n\n")) is None
    # wrong order of header lines
    assert parse_manifest(good.replace(f"{SDK_INLINE}\n{DIR_LINE}", f"{DIR_LINE}\n{SDK_INLINE}")) is None
    # missing close or open tag
    assert parse_manifest(good.replace("\n</twicc:attachments>", "")) is None
    assert parse_manifest(good.replace("<twicc:attachments>\n", "", 1)) is None
    # trailing text after the block
    assert parse_manifest(good + "\ntrailing") is None
    # leading text before the block
    assert parse_manifest("lead\n" + good) is None


def test_parse_rejects_bad_entries():
    good = _good()
    assert parse_manifest(good.replace("1. a.png", "1 a.png")) is None
    assert parse_manifest(good.replace("(image 1 of 1", "(movie 1 of 1")) is None
    assert parse_manifest(good.replace("inline)", "maybe)", 1)) is None
    # non sequential numbering
    assert parse_manifest(good.replace("2. b.mp4", "3. b.mp4")) is None
    # rank beyond total
    assert parse_manifest(good.replace("image 1 of 1", "image 2 of 1")) is None
    # empty name
    assert parse_manifest(good.replace("1. a.png (", "1.  (")) is None


@pytest.mark.parametrize(
    "old, new",
    [
        ("1. a.png (image 1 of 1, inline)", "١. a.png (image ١ of 01, inline)"),
        ("1. a.png", "١. a.png"),
        ("image 1 of 1", "image ١ of 1"),
        ("image 1 of 1", "image 1 of ١"),
        ("1. a.png", "01. a.png"),
        ("image 1 of 1", "image 01 of 1"),
        ("image 1 of 1", "image 1 of 01"),
        ("image 1 of 1", "image 0 of 1"),
    ],
)
def test_parse_rejects_unicode_digits_and_leading_zeros(old, new):
    good = _good()
    assert parse_manifest(good) is not None
    assert parse_manifest(good.replace(old, new)) is None


def test_parse_keeps_unicode_whitespace_out_of_hybrid_paths():
    m = manifest(entry(1, "a.png", "image", 1, 1, "inline"), directory=None)
    hybrid = build_manifest(m, hybrid_paths=("/d/hybrid/s/att_0123456789ab.png",))
    assert parse_manifest(hybrid.replace("att_", "att ")) is None


def test_round_trip_multi_digit_numbers():
    entries = [entry(n, f"f{n}.txt", "text", n, 12, "file", f"f{n}.txt") for n in range(1, 13)]
    parsed = parse_manifest(build_manifest(manifest(*entries)))
    assert parsed is not None
    assert parsed.entries == tuple(entries)


def test_parse_rejects_hybrid_inconsistencies():
    m = manifest(entry(1, "a.png", "image", 1, 1, "inline"), directory=None)
    hybrid = build_manifest(m, hybrid_paths=("/d/hybrid/s/att_0123456789ab.png",))
    assert parse_manifest(hybrid) is not None
    # hybrid header but inline entry without reference
    assert parse_manifest(hybrid.replace(": @/d/hybrid/s/att_0123456789ab.png", "")) is None
    # sdk header but reference present
    assert parse_manifest(hybrid.replace(HYBRID_INLINE, SDK_INLINE)) is None
    # reference on a file entry
    file_block = build_manifest(manifest(entry(1, "b.mp4", "video", 1, 1, "file", "b.mp4")))
    assert parse_manifest(file_block.replace("file)", "file): @/x/y")) is None


def test_builder_rejects_inconsistent_hybrid_paths():
    m = manifest(entry(1, "a.png", "image", 1, 1, "inline"), entry(2, "b.mp4", "video", 1, 1, "file", "b.mp4"))
    with pytest.raises(ValueError):
        build_manifest(m, hybrid_paths=("/p/att_0123456789ab",))  # wrong length
    with pytest.raises(ValueError):
        build_manifest(m, hybrid_paths=(None, None))  # inline entry without path
    with pytest.raises(ValueError):
        build_manifest(m, hybrid_paths=("/p/att_0123456789ab", "/p/x"))  # file entry with path
    with pytest.raises(ValueError):
        build_manifest(m, hybrid_paths=("/p/with space/att_0123456789ab", None))


def test_builder_rejects_missing_directory_for_file_entry_and_empty():
    with pytest.raises(ValueError):
        build_manifest(manifest(entry(1, "b.mp4", "video", 1, 1, "file", "b.mp4"), directory=None))
    with pytest.raises(ValueError):
        build_manifest(manifest(directory=None))
