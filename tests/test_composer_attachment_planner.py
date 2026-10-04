"""Ordered first-fit planning of composer attachments (spec §6.3-§6.6).

Every entry is staged through the Task 1 helpers. Exact limits are tested at the threshold and
one byte (or one item) above it, for every target.
"""

import builtins
import os
import struct
import zlib
from pathlib import Path

import pytest

from twicc.core.services.attachments import images, planner
from twicc.core.services.attachments.planner import AttachmentPlanError, plan_attachments
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import AttachmentRef, NativePart, PlanTarget
from twicc.providers.claude_code import helpers as claude_helpers
from twicc.providers.codex import helpers as codex_helpers
from twicc.providers.helpers import get_provider_helpers

from tests.test_composer_attachments_staging import make_entry, root, write_promoted  # noqa: F401 - fixture

KIB = 1024
MIB = 1024 * KIB

CLAUDE = PlanTarget("claude_code", False, False, "opus", False, "first_party")
CLAUDE_1M = PlanTarget("claude_code", False, False, "opus", True, "first_party")
CLAUDE_THIRD_PARTY = PlanTarget("claude_code", False, False, "opus", False, "third_party")
CLAUDE_HYBRID = PlanTarget("claude_code", True, False, "opus", False, "first_party")
CODEX = PlanTarget("codex", False, False, "gpt-sol", False, "first_party")
ALL_TARGETS = [CLAUDE, CLAUDE_1M, CLAUDE_THIRD_PARTY, CLAUDE_HYBRID, CODEX]
CLAUDE_SDK_TARGETS = [CLAUDE, CLAUDE_1M, CLAUDE_THIRD_PARTY]


def b64(n: int) -> int:
    return 4 * ((n + 2) // 3)


# ── Builders ──


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(width: int = 2, height: int = 2, pad: int = 0) -> bytes:
    """A still RGB PNG; *pad* bytes go in a private ancillary chunk (Pillow skips it)."""
    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if pad:
        data += _chunk(b"twIc", b"\x00" * pad)
    return data + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b"")


def png_of_size(size: int) -> bytes:
    base = len(png())
    data = png(pad=size - base - 12)
    assert len(data) == size
    return data


def pdf(size: int) -> bytes:
    head = b"%PDF-1.4\n"
    return head + b"0" * (size - len(head))


def text(size: int) -> bytes:
    return b"a" * size


def stage(root, name: str, content: bytes) -> AttachmentRef:  # noqa: F811 - fixture name
    ref, _entry, _source = make_entry(root, filename=name, content=content)
    return ref


def stage_many(root, specs) -> tuple[AttachmentRef, ...]:  # noqa: F811 - fixture name
    return tuple(stage(root, name, content) for name, content in specs)


def pdfs_filling(budget: int, *, last_extra: int = 0) -> list[tuple[str, bytes]]:
    """PDFs (each <= 512 KiB) whose base64 measures add up to exactly *budget*.

    *last_extra* adds bytes to the last PDF, which raises its measure above the remainder.
    """
    full = b64(512 * KIB)
    count, remainder = divmod(budget, full)
    assert remainder % 4 == 0
    sizes = [512 * KIB] * count
    if remainder:
        sizes.append(3 * (remainder // 4))
    sizes[-1] += last_extra
    return [(f"doc{i}.pdf", pdf(size)) for i, size in enumerate(sizes)]


class ReadTracker:
    """Shadows ``open`` in the planner and records the bytes read per path."""

    def __init__(self):
        self.read_bytes: dict[str, int] = {}

    def open(self, path, mode="r", *args, **kwargs):
        file = builtins.open(path, mode, *args, **kwargs)
        key = str(path)
        self.read_bytes.setdefault(key, 0)
        original_read = file.read
        tracker = self

        def read(n=-1):
            assert n is not None and n >= 0, "unbounded read"
            data = original_read(n)
            tracker.read_bytes[key] += len(data)
            return data

        file.read = read
        return file


def source_path(root, ref) -> Path:  # noqa: F811 - fixture name
    return Path(os.path.realpath(next((root / ref.bucket / ref.id / "file").iterdir())))


# ── Provider policy ──


def test_provider_policies_hold_the_exact_limits():
    claude = claude_helpers.ATTACHMENT_POLICY
    assert claude.native_kinds == frozenset({"image", "PDF", "text"})
    assert claude.hybrid_native_kinds == frozenset({"image"})
    assert claude.pdf_native_max_bytes == 512 * KIB
    assert claude.text_native_max_bytes == 50 * KIB
    assert (claude.max_native_items, claude.max_native_items_1m) == (80, 580)
    assert (claude.volume_budget, claude.volume_budget_third_party) == (16 * MIB, 12 * MIB)
    assert claude.image_long_edge == 2000
    assert (claude.per_image_base64_limit, claude.per_image_base64_limit_third_party) == (10_485_760, 5_242_880)

    codex = codex_helpers.ATTACHMENT_POLICY
    assert codex.native_kinds == frozenset({"image"})
    assert codex.hybrid_native_kinds == frozenset({"image"})
    assert (codex.max_native_items, codex.max_native_items_1m) == (1500, 1500)
    assert (codex.volume_budget, codex.volume_budget_third_party) == (16 * MIB, 16 * MIB)
    assert codex.image_long_edge == 2576
    assert (codex.per_image_base64_limit, codex.per_image_base64_limit_third_party) == (None, None)


def test_policy_is_exposed_by_the_provider_helpers_and_legacy_support_is_kept():
    assert get_provider_helpers("claude_code").get_attachment_policy() is claude_helpers.ATTACHMENT_POLICY
    assert get_provider_helpers("codex").get_attachment_policy() is codex_helpers.ATTACHMENT_POLICY
    assert claude_helpers.ATTACHMENT_SUPPORT["max_files_per_message"] == 100
    assert codex_helpers.ATTACHMENT_SUPPORT["documents"] is False


@pytest.mark.parametrize("target", ALL_TARGETS)
def test_image_normalization_limits_match_the_policy(target):
    policy = get_provider_helpers(target.provider).get_attachment_policy()
    assert images.long_edge(target) == policy.image_long_edge
    expected = (
        policy.per_image_base64_limit_third_party if target.platform == "third_party" else policy.per_image_base64_limit
    )
    assert images.per_image_limit(target) == expected


# ── Order, numbering and ranks ──


def test_mixed_kinds_keep_order_numbers_and_ranks(root):  # noqa: F811
    refs = stage_many(root, [("a.png", png()), ("clip.mp4", b"\x00\x01binary"), ("b.png", png(3, 3))])
    plan = plan_attachments(refs, CLAUDE, text="hello")
    assert plan.target == CLAUDE
    assert [e.mode for e in plan.entries] == ["inline", "file", "inline"]
    assert [e.n for e in plan.entries] == [1, 2, 3]
    assert [e.kind for e in plan.entries] == ["image", "video", "image"]
    assert [(e.rank, e.of) for e in plan.entries if e.kind == "image"] == [(1, 2), (2, 2)]
    assert [e.name for e in plan.entries] == ["a.png", "clip.mp4", "b.png"]
    assert [e.ref for e in plan.entries] == list(refs)
    assert plan.entries[1].native is None
    assert plan.entries[0].native == NativePart("image", "image/png", png())


def test_ranks_count_inline_and_file_entries_together(root):  # noqa: F811
    refs = stage_many(
        root,
        [("big.pdf", pdf(512 * KIB + 1)), ("small.pdf", pdf(100)), ("note.txt", text(5)), ("last.pdf", pdf(200))],
    )
    plan = plan_attachments(refs, CLAUDE, text="")
    assert [e.mode for e in plan.entries] == ["file", "inline", "inline", "inline"]
    assert [(e.kind, e.rank, e.of) for e in plan.entries] == [
        ("PDF", 1, 3), ("PDF", 2, 3), ("text", 1, 1), ("PDF", 3, 3),
    ]


def test_empty_refs_give_an_empty_plan():
    assert plan_attachments((), CLAUDE, text="hi").entries == ()


def test_plan_is_deterministic(root):  # noqa: F811
    refs = stage_many(root, [("a.png", png()), ("b.txt", text(10)), ("c.bin", b"\x00\x01")])
    assert plan_attachments(refs, CLAUDE, text="x") == plan_attachments(refs, CLAUDE, text="x")


# ── Capability policy per target ──

KIND_SAMPLES = {
    "image": ("pic.png", png()),
    "PDF": ("doc.pdf", pdf(1000)),
    "text": ("notes.txt", text(1000)),
    "video": ("clip.mp4", b"\x00video"),
    "audio": ("song.mp3", b"\x00audio"),
    "other": ("blob.bin", b"\x00other"),
}
EXPECTED_INLINE = {
    CLAUDE: {"image", "PDF", "text"},
    CLAUDE_1M: {"image", "PDF", "text"},
    CLAUDE_THIRD_PARTY: {"image", "PDF", "text"},
    CLAUDE_HYBRID: {"image"},
    CODEX: {"image"},
}


@pytest.mark.parametrize("target", ALL_TARGETS)
@pytest.mark.parametrize("kind", list(KIND_SAMPLES))
def test_capability_policy(root, target, kind):  # noqa: F811
    ref = stage(root, *KIND_SAMPLES[kind])
    entry = plan_attachments((ref,), target, text="").entries[0]
    assert entry.kind == kind
    assert entry.mode == ("inline" if kind in EXPECTED_INLINE[target] else "file")


@pytest.mark.parametrize("target", CLAUDE_SDK_TARGETS)
def test_pdf_threshold(root, target):  # noqa: F811
    at, above = stage(root, "at.pdf", pdf(512 * KIB)), stage(root, "above.pdf", pdf(512 * KIB + 1))
    plan = plan_attachments((at, above), target, text="")
    assert [e.mode for e in plan.entries] == ["inline", "file"]
    assert plan.entries[0].native == NativePart("PDF", "application/pdf", pdf(512 * KIB))


@pytest.mark.parametrize("target", CLAUDE_SDK_TARGETS)
def test_text_threshold(root, target):  # noqa: F811
    at, above = stage(root, "at.txt", text(50 * KIB)), stage(root, "above.txt", text(50 * KIB + 1))
    plan = plan_attachments((at, above), target, text="")
    assert [e.kind for e in plan.entries] == ["text", "text"]
    assert [e.mode for e in plan.entries] == ["inline", "file"]
    assert plan.entries[0].native == NativePart("text", "text/plain", "a" * 50 * KIB)


def test_native_text_is_decoded_text(root):  # noqa: F811
    content = "héllo wörld\n".encode()
    entry = plan_attachments((stage(root, "u.txt", content),), CLAUDE, text="").entries[0]
    assert entry.native == NativePart("text", "text/plain", "héllo wörld\n")


def test_native_text_must_decode_fully(root, monkeypatch):  # noqa: F811
    ref = stage(root, "bad.txt", b"abc\xff\xfe")
    monkeypatch.setattr(planner, "detect_kind", lambda path, name, size: "text")
    entry = plan_attachments((ref,), CLAUDE, text="").entries[0]
    assert (entry.kind, entry.mode, entry.native) == ("text", "file", None)


# ── Count quota ──

COUNT_CASES = [
    (CLAUDE, 80, "t.txt", text(1)),
    (CLAUDE_1M, 580, "t.txt", text(1)),
    (CLAUDE_THIRD_PARTY, 80, "t.txt", text(1)),
    (CLAUDE_HYBRID, 80, "p.png", png()),
    (CODEX, 1500, "p.png", png()),
]


@pytest.mark.parametrize(("target", "limit", "name", "content"), COUNT_CASES)
def test_count_quota_at_and_one_above(root, target, limit, name, content):  # noqa: F811
    refs = stage_many(root, [(name, content)] * (limit + 1))
    modes = [e.mode for e in plan_attachments(refs, target, text="").entries]
    assert modes == ["inline"] * limit + ["file"]


def test_claude_count_covers_images_pdfs_and_texts_together(root):  # noqa: F811
    refs = stage_many(root, [("t.txt", text(1))] * 78 + [("p.png", png()), ("d.pdf", pdf(50)), ("x.pdf", pdf(50))])
    plan = plan_attachments(refs, CLAUDE, text="")
    assert [e.mode for e in plan.entries[-3:]] == ["inline", "inline", "file"]


def test_file_entries_consume_no_quota(root):  # noqa: F811
    refs = stage_many(root, [("v.mp4", b"\x00v")] * 5 + [("t.txt", text(1))] * 80)
    modes = [e.mode for e in plan_attachments(refs, CLAUDE, text="").entries]
    assert modes == ["file"] * 5 + ["inline"] * 80


def test_count_exhaustion_reads_only_the_head(root, monkeypatch):  # noqa: F811
    refs = stage_many(
        root,
        [("t.txt", text(1))] * 80 + [("late.png", png_of_size(200 * KIB)), ("late.pdf", pdf(400 * KIB))],
    )
    tracker = ReadTracker()
    monkeypatch.setattr(planner, "open", tracker.open, raising=False)
    calls = []
    original = images.normalize_image
    monkeypatch.setattr(images, "normalize_image", lambda *a, **k: calls.append(a) or original(*a, **k))
    plan = plan_attachments(refs, CLAUDE, text="")
    assert [e.mode for e in plan.entries[-2:]] == ["file", "file"]
    assert [e.kind for e in plan.entries[-2:]] == ["image", "PDF"]
    assert calls == []
    for ref in refs[-2:]:
        assert tracker.read_bytes[str(source_path(root, ref))] <= planner.HEAD_BYTES


@pytest.mark.parametrize(
    ("name", "content"),
    [("big.pdf", pdf(600 * KIB)), ("big.txt", text(200 * KIB))],
)
def test_oversized_pdf_and_text_read_only_the_head(root, monkeypatch, name, content):  # noqa: F811
    ref = stage(root, name, content)
    tracker = ReadTracker()
    monkeypatch.setattr(planner, "open", tracker.open, raising=False)
    entry = plan_attachments((ref,), CLAUDE, text="").entries[0]
    assert entry.mode == "file"
    assert tracker.read_bytes[str(source_path(root, ref))] <= planner.HEAD_BYTES


def test_pdf_over_the_remaining_budget_reads_only_the_head(root, monkeypatch):  # noqa: F811
    specs = pdfs_filling(16 * MIB - b64(400 * KIB) + 4)  # leaves 4 bytes less than the last PDF needs
    refs = stage_many(root, specs) + (stage(root, "late.pdf", pdf(400 * KIB)),)
    tracker = ReadTracker()
    monkeypatch.setattr(planner, "open", tracker.open, raising=False)
    entry = plan_attachments(refs, CLAUDE, text="").entries[-1]
    assert entry.mode == "file"
    assert tracker.read_bytes[str(source_path(root, refs[-1]))] <= planner.HEAD_BYTES


# ── Volume budget ──

BUDGET_CASES = [(CLAUDE, 16 * MIB), (CLAUDE_1M, 16 * MIB), (CLAUDE_THIRD_PARTY, 12 * MIB)]


@pytest.mark.parametrize(("target", "budget"), BUDGET_CASES)
def test_claude_budget_at_the_limit(root, target, budget):  # noqa: F811
    refs = stage_many(root, pdfs_filling(budget))
    plan = plan_attachments(refs, target, text="")
    assert all(e.mode == "inline" for e in plan.entries)
    assert sum(b64(len(e.native.data)) for e in plan.entries) == budget


@pytest.mark.parametrize(("target", "budget"), BUDGET_CASES)
def test_claude_budget_one_byte_above(root, target, budget):  # noqa: F811
    refs = stage_many(root, pdfs_filling(budget, last_extra=1))
    modes = [e.mode for e in plan_attachments(refs, target, text="").entries]
    assert modes == ["inline"] * (len(refs) - 1) + ["file"]


@pytest.mark.parametrize(("size", "mode"), [(40_000, "inline"), (40_001, "file")])
def test_text_budget_measure_is_its_utf8_length(root, size, mode):  # noqa: F811
    refs = stage_many(root, pdfs_filling(16 * MIB - 40_000)) + (stage(root, "t.txt", text(size)),)
    assert plan_attachments(refs, CLAUDE, text="").entries[-1].mode == mode


@pytest.mark.parametrize(("size", "mode"), [(3 * (16 * MIB) // 4, "inline"), (3 * (16 * MIB) // 4 + 1, "file")])
def test_codex_budget(root, size, mode):  # noqa: F811
    ref = stage(root, "big.png", png_of_size(size))
    assert plan_attachments((ref,), CODEX, text="").entries[0].mode == mode


@pytest.mark.parametrize(
    ("target", "limit"),
    [(CLAUDE, 10_485_760), (CLAUDE_HYBRID, 10_485_760), (CLAUDE_THIRD_PARTY, 5_242_880)],
)
@pytest.mark.parametrize("extra", [0, 1])
def test_claude_per_image_limit(root, target, limit, extra):  # noqa: F811
    ref = stage(root, "big.png", png_of_size(3 * limit // 4 + extra))
    assert plan_attachments((ref,), target, text="").entries[0].mode == ("inline" if extra == 0 else "file")


def test_user_text_consumes_no_budget(root):  # noqa: F811
    refs = stage_many(root, pdfs_filling(16 * MIB))
    plan = plan_attachments(refs, CLAUDE, text="x" * (20 * MIB))
    assert all(e.mode == "inline" for e in plan.entries)


def test_first_fit_lets_a_later_small_entry_be_native(root):  # noqa: F811
    refs = stage_many(root, pdfs_filling(16 * MIB - 1000))
    refs += stage_many(root, [("big.pdf", pdf(10_000)), ("small.txt", text(900)), ("late.pdf", pdf(100))])
    modes = [e.mode for e in plan_attachments(refs, CLAUDE, text="").entries[-3:]]
    assert modes == ["file", "inline", "file"]  # the late PDF measures 136 > the 100 bytes left


# ── Entry errors and promoted entries ──


def test_missing_entry_directory_is_attachment_missing(root):  # noqa: F811
    ref = AttachmentRef("b1", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(AttachmentError) as exc:
        plan_attachments((ref,), CLAUDE, text="")
    assert exc.value.code == "attachment_missing"


def test_existing_unready_entry_is_attachment_not_ready(root):  # noqa: F811
    ready = stage(root, "ok.txt", text(3))
    ref, _entry, _source = make_entry(root, filename="up.txt", content=b"abc", ready=False)
    with pytest.raises(AttachmentError) as exc:
        plan_attachments((ready, ref), CLAUDE, text="")
    assert exc.value.code == "attachment_not_ready"


def test_promoted_entry_is_a_file_with_its_recorded_kind(root, tmp_path):  # noqa: F811
    ref, entry, source = make_entry(root, filename="notes.txt", content=b"hello")
    final = tmp_path / "artifacts" / "s1" / "attachments" / "notes (1).txt"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"hello")
    source.unlink()
    write_promoted(entry, final, kind="text", original_name="notes.txt", final_name=final.name)
    promoted_retry = plan_attachments((ref,), CLAUDE, text="")
    assert promoted_retry.entries[0].mode == "file"
    assert promoted_retry.entries[0].kind == "text"
    assert promoted_retry.entries[0].name == "notes.txt"
    assert promoted_retry.entries[0].source.promoted.final_path == final


def test_promoted_entry_with_a_missing_artifact_is_attachment_missing(root, tmp_path):  # noqa: F811
    ref, entry, source = make_entry(root)
    source.unlink()
    write_promoted(entry, tmp_path / "gone" / "doc.txt")
    with pytest.raises(AttachmentError) as exc:
        plan_attachments((ref,), CLAUDE, text="")
    assert exc.value.code == "attachment_missing"


# ── Ephemeral targets ──


def test_ephemeral_mixed_plan_requires_artifacts(root):  # noqa: F811
    refs = stage_many(root, [("a.png", png()), ("clip.mp4", b"\x00v"), ("big.pdf", pdf(600 * KIB))])
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    with pytest.raises(AttachmentPlanError) as exc:
        plan_attachments(refs, CLAUDE._replace(ephemeral=True), text="")
    assert exc.value.code == "attachment_requires_artifacts"
    assert exc.value.names == ("clip.mp4", "big.pdf")
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before


def test_ephemeral_all_native_plan_is_accepted(root):  # noqa: F811
    refs = stage_many(root, [("a.png", png()), ("n.txt", text(3))])
    plan = plan_attachments(refs, CLAUDE._replace(ephemeral=True), text="")
    assert [e.mode for e in plan.entries] == ["inline", "inline"]


# ── Commands ──


@pytest.mark.parametrize("message", ["/model sonnet", "   /compact", "\n\t/foo bar"])
def test_hybrid_slash_command_with_attachments_is_refused(root, message):  # noqa: F811
    ref = stage(root, "a.png", png())
    with pytest.raises(AttachmentPlanError) as exc:
        plan_attachments((ref,), CLAUDE_HYBRID, text=message)
    assert exc.value.code == "attachments_with_command"


def test_command_is_refused_before_any_entry_is_loaded():
    missing = AttachmentRef("b1", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(AttachmentPlanError) as exc:
        plan_attachments((missing,), CLAUDE_HYBRID, text="/model")
    assert exc.value.code == "attachments_with_command"


def test_slash_text_is_allowed_for_claude_sdk_and_without_attachments(root):  # noqa: F811
    ref = stage(root, "a.png", png())
    assert plan_attachments((ref,), CLAUDE, text="/review").entries[0].mode == "inline"
    assert plan_attachments((), CLAUDE_HYBRID, text="/model").entries == ()
    assert plan_attachments((ref,), CLAUDE_HYBRID, text="see a/b").entries[0].mode == "inline"


@pytest.mark.parametrize("message", ["/compact", "  /goal ship it", "/plan"])
def test_codex_hardcoded_command_with_attachments_is_refused(root, message):  # noqa: F811
    ref = stage(root, "a.png", png())
    with pytest.raises(AttachmentPlanError) as exc:
        plan_attachments((ref,), CODEX, text=message)
    assert exc.value.code == "attachments_with_command"


def test_codex_other_slash_text_is_allowed(root):  # noqa: F811
    ref = stage(root, "a.png", png())
    assert plan_attachments((ref,), CODEX, text="/some/path").entries[0].mode == "inline"
