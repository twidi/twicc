"""Cross-layer round trip of composer attachments (design §6-§10, plan Task 19).

Staged mixed files go through the real planner, the real committer, the real provider input
builders, then come back as the JSONL user record the provider writes and through the real
ingestion (``transform_inline``). The transformed records are pinned in
``tests/fixtures/attachment_roundtrip_records.json``, which the frontend history-strip tests
(``frontend/src/utils/attachmentStrip.test.js``) read: both halves of the pipeline check the same
literal shapes.

Regenerate the fixture after an intended change with ``TWICC_UPDATE_FIXTURES=1``.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import orjson
import pytest

from twicc.core.enums import ItemKind
from twicc.core.services.attachments.committer import discard_prepared, finish_attachments, prepare_attachments
from twicc.core.services.attachments.planner import plan_attachments
from twicc.core.services.attachments.types import AttachmentContent, AttachmentRef, PlanTarget
from twicc.paths import get_artifacts_dir
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.claude_code.helpers import ClaudeCodeHelpers
from twicc.providers.codex.agent.agent import ImageInput, TextInput
from twicc.providers.codex.canonical import user_message_is_visible, user_message_text
from twicc.providers.codex.compute import CodexSessionCompute

from tests.test_attachment_manifest_ingestion import CLAUDE_SID, CODEX_SID, claude_user, codex_user_message
from tests.test_claude_attachment_delivery import _make_agent as make_claude_agent
from tests.test_claude_attachment_delivery import _prompt as claude_prompt
from tests.test_codex_attachment_delivery import _items as codex_items
from tests.test_codex_attachment_delivery import _make_agent as make_codex_agent
from tests.test_composer_attachment_planner import pdf, png
from tests.test_composer_attachments_staging import make_entry, root  # noqa: F401 - fixture

FIXTURE = Path(__file__).parent / "fixtures" / "attachment_roundtrip_records.json"

CLAUDE = PlanTarget("claude_code", False, False, "opus", False, "first_party")
CODEX = PlanTarget("codex", False, False, "gpt-sol", False, "first_party")

MIB = 1024 * 1024
# A "large" video: well over every native threshold, deterministic bytes.
VIDEO = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * (3 * MIB // 256)
FIRST_IMAGE = png(2, 2)
SECOND_IMAGE = png(3, 1)
SMALL_PDF = pdf(300)

MIXED = (
    ("login.png", FIRST_IMAGE),
    ("capture.mp4", VIDEO),
    ("spec.pdf", SMALL_PDF),
    ("after.png", SECOND_IMAGE),
)
FILE_ONLY = (("capture.mp4", VIDEO), ("archive.zip", b"PK\x03\x04" + b"\x00" * 64))


def stage_all(root, specs) -> tuple[AttachmentRef, ...]:  # noqa: F811 - fixture name
    return tuple(make_entry(root, filename=name, content=content)[0] for name, content in specs)


def commit(refs, target: PlanTarget, session_id: str, text: str) -> AttachmentContent:
    plan = plan_attachments(refs, target, text=text)
    prepared = prepare_attachments(plan, session_id=session_id)
    try:
        return finish_attachments(prepared, session_id=session_id, text=text)
    finally:
        discard_prepared(prepared)


def as_jsonl(record: dict) -> dict:
    """The record as the watcher reads it back from the provider JSONL."""
    return orjson.loads(orjson.dumps(record))


def claude_roundtrip(root, monkeypatch, specs, text: str):  # noqa: F811 - fixture name
    refs = stage_all(root, specs)
    content = commit(refs, CLAUDE, CLAUDE_SID, text)
    prompt = claude_prompt(make_claude_agent(monkeypatch), text, content)
    record = as_jsonl(claude_user(prompt))
    ClaudeCodeSessionCompute().transform_inline(record, session_id=CLAUDE_SID, line_num=5)
    return content, prompt, record


def codex_canonical_content(items) -> list[dict]:
    """The canonical ``UserMessage`` content Codex 0.160 records for a turn input."""
    content = []
    for item in items:
        if isinstance(item, ImageInput):
            content.append({"type": "image", "image_url": item.url})
        elif isinstance(item, TextInput):
            content.append({"type": "text", "text": item.text, "text_elements": []})
        else:  # pragma: no cover - the builder emits only these two
            raise TypeError(type(item).__name__)
    return content


def codex_roundtrip(root, monkeypatch, specs, text: str):  # noqa: F811 - fixture name
    refs = stage_all(root, specs)
    content = commit(refs, CODEX, CODEX_SID, text)
    items = codex_items(make_codex_agent(monkeypatch), text, content)
    record = as_jsonl(codex_user_message(codex_canonical_content(items)))
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    compute.transform_inline(record, session_id=CODEX_SID, line_num=9)
    return content, items, record


def data_url(media_type: str, data: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode()}"


def video_marker() -> str:
    """A slice of the video's base64 that any embedding of its bytes would contain."""
    return base64.b64encode(VIDEO[:3 * 1024]).decode()


# ----------------------------------------------------------------------
# Claude SDK: staged mixed files -> plan -> commit -> prompt -> record
# ----------------------------------------------------------------------


def test_claude_mixed_files_round_trip_to_ordered_metadata(root, monkeypatch):  # noqa: F811
    content, prompt, record = claude_roundtrip(root, monkeypatch, MIXED, "Please review")

    # Provider input: native blocks in manifest order, manifest, user text LAST.
    assert [block["type"] for block in prompt] == ["image", "document", "image", "text", "text"]
    assert prompt[-1]["text"] == "Please review"
    assert prompt[-2]["text"].startswith("<twicc:attachments>\n")

    # The large video became an artifact; its bytes never reach the provider input.
    final = get_artifacts_dir() / CLAUDE_SID / "attachments" / "capture.mp4"
    assert final.read_bytes() == VIDEO
    serialized_prompt = orjson.dumps(prompt)
    assert len(serialized_prompt) < 64 * 1024
    assert video_marker().encode() not in serialized_prompt

    # Ingestion: the manifest left the stored content and became metadata.
    assert record["twicc_attachments"] == {
        "owner": CLAUDE_SID,
        "entries": [
            {"n": 1, "name": "login.png", "kind": "image", "rank": 1, "of": 2, "mode": "inline", "artifact_name": None},
            {"n": 2, "name": "capture.mp4", "kind": "video", "rank": 1, "of": 1, "mode": "file",
             "artifact_name": "capture.mp4"},
            {"n": 3, "name": "spec.pdf", "kind": "PDF", "rank": 1, "of": 1, "mode": "inline", "artifact_name": None},
            {"n": 4, "name": "after.png", "kind": "image", "rank": 2, "of": 2, "mode": "inline", "artifact_name": None},
        ],
    }
    blocks = record["message"]["content"]
    assert [block["type"] for block in blocks] == ["image", "document", "image", "text"]
    assert blocks[0]["source"]["data"] == base64.b64encode(FIRST_IMAGE).decode()
    assert blocks[2]["source"]["data"] == base64.b64encode(SECOND_IMAGE).decode()
    assert blocks[3] == {"type": "text", "text": "Please review"}
    assert "<twicc:attachments>" not in orjson.dumps(record).decode()
    assert str(get_artifacts_dir()) not in orjson.dumps(record).decode(), "no absolute path is stored"

    compute = ClaudeCodeSessionCompute()
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE
    assert compute.extract_title_from_user_message(record) == "Please review"
    assert ClaudeCodeHelpers().extract_indexable_text_from_parsed(record) == "Please review"


def test_claude_file_only_follow_up_without_text_is_a_visible_user_message(root, monkeypatch):  # noqa: F811
    content, prompt, record = claude_roundtrip(root, monkeypatch, FILE_ONLY, "")

    assert content.native_parts == ()
    assert [block["type"] for block in prompt] == ["text"], "the manifest alone, no empty user text"
    assert record["message"]["content"] == []
    assert [entry["mode"] for entry in record["twicc_attachments"]["entries"]] == ["file", "file"]
    compute = ClaudeCodeSessionCompute()
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE
    assert compute.extract_title_from_user_message(record) is None
    assert video_marker().encode() not in orjson.dumps(prompt)


# ----------------------------------------------------------------------
# Codex: images inline, PDF and video as artifacts
# ----------------------------------------------------------------------


def test_codex_mixed_files_round_trip_to_ordered_metadata(root, monkeypatch):  # noqa: F811
    content, items, record = codex_roundtrip(root, monkeypatch, MIXED, "Please review")

    assert [type(item).__name__ for item in items] == ["ImageInput", "ImageInput", "TextInput", "TextInput"]
    assert items[0].url == data_url("image/png", FIRST_IMAGE)
    assert items[1].url == data_url("image/png", SECOND_IMAGE)
    serialized_items = orjson.dumps(codex_canonical_content(items))
    assert len(serialized_items) < 64 * 1024
    assert video_marker().encode() not in serialized_items
    attachments = get_artifacts_dir() / CODEX_SID / "attachments"
    assert sorted(path.name for path in attachments.iterdir()) == ["capture.mp4", "spec.pdf"]

    assert [(e["name"], e["mode"]) for e in record["twicc_attachments"]["entries"]] == [
        ("login.png", "inline"), ("capture.mp4", "file"), ("spec.pdf", "file"), ("after.png", "inline"),
    ]
    assert record["twicc_attachments"]["owner"] == CODEX_SID
    assert [entry["type"] for entry in record["payload"]["item"]["content"]] == ["image", "image", "text"]
    assert user_message_text(record) == "Please review"
    assert user_message_is_visible(record) is True
    assert CodexSessionCompute().compute_item_kind(record) == ItemKind.USER_MESSAGE


# ----------------------------------------------------------------------
# Shared fixture: the frontend strip tests read these exact records
# ----------------------------------------------------------------------


def _cases(root, monkeypatch) -> dict:  # noqa: F811 - fixture name
    _, _, claude_mixed = claude_roundtrip(root, monkeypatch, MIXED, "Please review")
    _, _, claude_file_only = claude_roundtrip(root, monkeypatch, FILE_ONLY, "")
    _, _, codex_mixed = codex_roundtrip(root, monkeypatch, MIXED, "Please review")
    return {
        "_comment": "Generated by tests/test_attachment_pipeline_roundtrip.py (TWICC_UPDATE_FIXTURES=1).",
        "claude_mixed": {
            "sent": {"text": "Please review", "attachmentCount": len(MIXED), "names": [n for n, _ in MIXED]},
            "record": claude_mixed,
        },
        "claude_file_only": {
            "sent": {"text": "", "attachmentCount": len(FILE_ONLY), "names": [n for n, _ in FILE_ONLY]},
            "record": claude_file_only,
        },
        "codex_mixed": {
            "sent": {"text": "Please review", "attachmentCount": len(MIXED), "names": [n for n, _ in MIXED]},
            "record": codex_mixed,
        },
    }


def test_shared_frontend_fixture_matches_the_backend_output(root, monkeypatch):  # noqa: F811
    cases = _cases(root, monkeypatch)
    rendered = orjson.dumps(cases, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    if os.environ.get("TWICC_UPDATE_FIXTURES") == "1":
        FIXTURE.write_bytes(rendered)
    assert FIXTURE.is_file(), "missing fixture: run this test once with TWICC_UPDATE_FIXTURES=1"
    assert orjson.loads(FIXTURE.read_bytes()) == orjson.loads(rendered), (
        "the transformed records changed: update the fixture with TWICC_UPDATE_FIXTURES=1 "
        "and re-run the frontend strip tests"
    )


@pytest.mark.parametrize("case", ["claude_mixed", "claude_file_only", "codex_mixed"])
def test_fixture_records_carry_no_large_bytes(case):
    record = orjson.loads(FIXTURE.read_bytes())[case]["record"]
    serialized = orjson.dumps(record)
    assert len(serialized) < 16 * 1024
    assert video_marker().encode() not in serialized
