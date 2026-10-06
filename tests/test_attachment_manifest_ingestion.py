"""Ingestion of the ``<twicc:attachments>`` manifest (design §10.1).

The block reaches the provider JSONL, but TwiCC stores it as structured
``twicc_attachments`` metadata and removes it from the user's own message —
only in validated user-message slots, at the exact position TwiCC writes it.
Every other record, and every block that fails a check, stays untouched.

Manifests are built with the shared builder (§7.4) and wrapped in the record
shapes the providers really write (Claude Code 2.1.286, Codex 0.160).
"""

from __future__ import annotations

import asyncio
import copy
import queue
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient

from tests.live_sync_helpers import drain_live_sync
from twicc.context_injection import extract_attachments_block
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.services.attachments.manifest import build_manifest
from twicc.core.services.attachments.types import AttachmentManifest, ManifestEntry, UserTextSlot
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.claude_code.helpers import ClaudeCodeHelpers
from twicc.providers.codex.canonical import (
    user_message_attachment_count,
    user_message_is_visible,
    user_message_text,
)
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.codex.helpers import CodexHelpers

CLAUDE_SID = "6f1d2c3b-4a59-4e8f-9a0b-1c2d3e4f5a6b"
CODEX_SID = "01a106b9-2844-7091-831f-c8a10f1f6bd1"
FORK_PARENT = "01a0fbe6-b6ef-7430-8299-2fa633db7cfd"
SPAWN_PARENT = "01a0aaaa-0000-7000-8000-000000000001"
OTHER_SID = "99999999-9999-4999-8999-999999999999"
DATA = "/home/u/.twicc"
PNG = {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}
IMAGE_BLOCK = {"type": "image", "source": PNG}
DOCUMENT_BLOCK = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="}}
DATA_URL = "data:image/png;base64,iVBORw0KGgo="


# ---------------------------------------------------------------------------
# Manifest builders (Task 6 builder, real protocol text)
# ---------------------------------------------------------------------------


def entry(n, name, kind, rank, of, mode, artifact_name=None):
    return ManifestEntry(n, name, kind, rank, of, mode, artifact_name)


def directory(owner: str) -> Path:
    return Path(f"{DATA}/artifacts/{owner}/attachments")


def hybrid_path(owner: str, hex12: str = "0123456789ab", ext: str = ".png") -> str:
    return f"{DATA}/hybrid/{owner}/att_{hex12}{ext}"


def sdk_block(owner: str, *entries: ManifestEntry) -> str:
    has_file = any(e.mode == "file" for e in entries)
    return build_manifest(AttachmentManifest(owner, directory(owner) if has_file else None, entries))


def hybrid_block(owner: str, *entries: ManifestEntry, paths=None) -> str:
    has_file = any(e.mode == "file" for e in entries)
    if paths is None:
        paths = tuple(hybrid_path(owner, f"{index:012x}") if e.mode == "inline" else None
                      for index, e in enumerate(entries))
    if not any(e.mode == "inline" for e in entries):
        return build_manifest(AttachmentManifest(owner, directory(owner), entries))
    return build_manifest(
        AttachmentManifest(owner, directory(owner) if has_file else None, entries), hybrid_paths=tuple(paths),
    )


MIXED = (
    entry(1, "login.png", "image", 1, 1, "inline"),
    entry(2, "movie.mp4", "video", 1, 1, "file", "movie.mp4"),
    entry(3, "spec.pdf", "PDF", 1, 1, "inline"),
)
ALL_FILE = (entry(1, "movie.mp4", "video", 1, 1, "file", "movie.mp4"),)


def expected_entries(*entries: ManifestEntry) -> list[dict]:
    return [e._asdict() for e in entries]


def expected_hybrid_entries(*entries: ManifestEntry) -> list[dict]:
    """Hybrid entries: each inline one also keeps the basename of its ``@`` reference (``hybrid_block`` defaults)."""
    return [
        {**e._asdict(), "reference": f"att_{index:012x}.png"} if e.mode == "inline" else e._asdict()
        for index, e in enumerate(entries)
    ]


# ---------------------------------------------------------------------------
# Real record shapes
# ---------------------------------------------------------------------------


def claude_user(content, *, session_id=CLAUDE_SID, **extra) -> dict:
    return {
        "parentUuid": "9d1d884d-175b-4ae5-b8c5-b4b16abe9866",
        "isSidechain": False,
        "promptId": "b0a1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d",
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": "41facacc-b8b1-45df-a3ea-62eaf3aae76f",
        "timestamp": "2026-10-04T11:13:19.093Z",
        "userType": "external",
        "entrypoint": "sdk-py",
        "cwd": "/home/u/project",
        "sessionId": session_id,
        "version": "2.1.286",
        "gitBranch": "main",
        **extra,
    }


def claude_queued(prompt, *, command_mode="prompt", session_id=CLAUDE_SID) -> dict:
    return {
        "parentUuid": "9d1d884d-175b-4ae5-b8c5-b4b16abe9866",
        "isSidechain": False,
        "attachment": {
            "type": "queued_command",
            "prompt": prompt,
            "source_uuid": "90232453-518b-41f0-8c11-13f79f54d3ad",
            "commandMode": command_mode,
            "timestamp": "2026-10-04T11:13:19.093Z",
        },
        "type": "attachment",
        "uuid": "41facacc-b8b1-45df-a3ea-62eaf3aae76f",
        "timestamp": "2026-10-04T11:13:19.093Z",
        "rendered": [{"content": [{"type": "text", "text": "<system-reminder>\nThe user sent a new message"}]}],
        "renderedRole": "system",
        "userType": "external",
        "entrypoint": "sdk-py",
        "cwd": "/home/u/project",
        "sessionId": session_id,
        "version": "2.1.286",
        "gitBranch": "main",
    }


def codex_response(content, *, ordinal=8) -> dict:
    return {
        "timestamp": "2026-10-04T11:42:43.014Z",
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {
            "type": "message",
            "id": "msg_01a106b9-3345-77d2-a9c8-66c604096dfa",
            "role": "user",
            "content": content,
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "01a106b9-2aec-7bb3-9c3b-ed97da04c51f",
                "create_time": 1791114163.013897,
                "content_item_kinds": ["user.image", "user.text"],
            },
        },
    }


def codex_user_message(content, *, ordinal=9, thread_id=CODEX_SID) -> dict:
    return {
        "timestamp": "2026-10-04T11:42:43.017Z",
        "ordinal": ordinal,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "thread_id": thread_id,
            "turn_id": "01a106b9-2aec-7bb3-9c3b-ed97da04c51f",
            "item": {"type": "UserMessage", "id": "01a106b9-3345-77d2-a9c8-66d0dc4be380", "content": content},
            "started_at_ms": 1791114163016,
            "completed_at_ms": 1791114163017,
        },
    }


def codex_session_meta(session_id: str, *, forked_from_id=None, parent_thread_id=None, ordinal=0) -> dict:
    payload = {
        "session_id": forked_from_id or session_id,
        "id": session_id,
        "timestamp": "2026-10-04T11:42:40.000Z",
        "cwd": "/home/u/project",
        "originator": "codex_vscode",
        "cli_version": "0.160.0",
        "source": "vscode",
        "model_provider": "openai",
    }
    if forked_from_id is not None:
        payload["forked_from_id"] = forked_from_id
        payload["subagent_history_start_ordinal"] = 20
    if parent_thread_id is not None:
        payload["parent_thread_id"] = parent_thread_id
    return {"timestamp": "2026-10-04T11:42:40.000Z", "ordinal": ordinal, "type": "session_meta", "payload": payload}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def canonical_text(value: str) -> dict:
    return {"type": "text", "text": value, "text_elements": []}


def input_text(value: str) -> dict:
    return {"type": "input_text", "text": value}


def transform(compute, record: dict, *, session_id: str, line_num: int = 5, in_memory_items=None):
    return compute.transform_inline(record, session_id=session_id, line_num=line_num, in_memory_items=in_memory_items)


# ---------------------------------------------------------------------------
# Claude SDK, hybrid, queued_command
# ---------------------------------------------------------------------------


def test_claude_sdk_array_extracts_the_block_after_native_media():
    compute = ClaudeCodeSessionCompute()
    record = claude_user([IMAGE_BLOCK, DOCUMENT_BLOCK, text(sdk_block(CLAUDE_SID, *MIXED)), text("Look at these")])

    serialized = transform(compute, record, session_id=CLAUDE_SID)

    assert serialized is not None and orjson.loads(serialized) == record
    assert record["twicc_attachments"] == {"owner": CLAUDE_SID, "entries": expected_entries(*MIXED)}
    assert record["twicc_attachments"]["entries"][1]["artifact_name"] == "movie.mp4"
    assert record["message"]["content"] == [IMAGE_BLOCK, DOCUMENT_BLOCK, text("Look at these")]
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE
    # A recompute re-runs on the cleaned stored copy: same result, nothing rewritten.
    recomputed = orjson.loads(serialized)
    assert transform(compute, recomputed, session_id=CLAUDE_SID) is None
    assert recomputed["twicc_attachments"] == record["twicc_attachments"]


def test_claude_all_inline_block_names_no_owner_and_uses_the_record_session():
    compute = ClaudeCodeSessionCompute()
    inline = (entry(1, "a.png", "image", 1, 1, "inline"),)
    record = claude_user([IMAGE_BLOCK, text(sdk_block(OTHER_SID, *inline)), text("hi")])

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["twicc_attachments"]["owner"] == CLAUDE_SID


def test_claude_image_placeholder_counts_as_a_media_slot():
    compute = ClaudeCodeSessionCompute()
    placeholder = text("[Image could not be processed: the image exceeds the maximum size]")
    record = claude_user([placeholder, DOCUMENT_BLOCK, text(sdk_block(CLAUDE_SID, *MIXED)), text("hi")])

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == [placeholder, DOCUMENT_BLOCK, text("hi")]
    assert "twicc_attachments" in record


def test_claude_file_only_message_without_text_stays_a_user_message():
    compute = ClaudeCodeSessionCompute()
    helpers = ClaudeCodeHelpers()
    record = claude_user([text(sdk_block(CLAUDE_SID, *ALL_FILE))])

    assert transform(compute, record, session_id=CLAUDE_SID) is not None

    assert record["message"]["content"] == []
    assert record["twicc_attachments"]["entries"] == expected_entries(*ALL_FILE)
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE
    assert compute.extract_title_from_user_message(record) is None
    assert helpers.extract_indexable_text_from_parsed(record) == ""


def test_claude_hybrid_string_with_text_strips_the_trailing_block():
    compute = ClaudeCodeSessionCompute()
    entries = (
        entry(1, "shot.png", "image", 1, 1, "inline"),
        entry(2, "movie.mp4", "video", 1, 1, "file", "movie.mp4"),
    )
    record = claude_user(f"Look at this\n\n{hybrid_block(CLAUDE_SID, *entries)}")

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "Look at this"
    assert record["twicc_attachments"] == {"owner": CLAUDE_SID, "entries": expected_hybrid_entries(*entries)}


def test_claude_hybrid_string_without_text_becomes_an_empty_user_message():
    compute = ClaudeCodeSessionCompute()
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"),)
    record = claude_user(hybrid_block(CLAUDE_SID, *entries))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == ""
    assert record["twicc_attachments"]["owner"] == CLAUDE_SID
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE


def test_claude_hybrid_all_file_block_parses_without_the_inline_header():
    """An all-file hybrid block has no hybrid header; the slot type selects the target."""
    compute = ClaudeCodeSessionCompute()
    record = claude_user(f"Here\n\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}")

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "Here"
    assert record["twicc_attachments"]["entries"] == expected_entries(*ALL_FILE)


def cli_paste_wrapper(inner: str, paste_id: str = "f79f") -> str:
    """The shape the bundled CLI (2.1.286) stores for a whole multi-line paste: the closing tag repeats the id."""
    return f'\n\n<pasted_content id="{paste_id}">\n{inner}\n</pasted_content id="{paste_id}">\n'


def test_hybrid_string_wrapped_by_the_cli_paste_tag_is_extracted_and_unwrapped():
    """Observed on a real hybrid session: the manifest is not at the end of the stored string."""
    compute = ClaudeCodeSessionCompute()
    entries = (
        entry(1, "shot.png", "image", 1, 1, "inline"),
        entry(2, "notes.txt", "text", 1, 1, "file", "notes.txt"),
    )
    record = claude_user(cli_paste_wrapper(f"Look at this\n\n{hybrid_block(CLAUDE_SID, *entries)}"))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "Look at this"
    assert record["twicc_attachments"] == {"owner": CLAUDE_SID, "entries": expected_hybrid_entries(*entries)}


def test_wrapped_hybrid_string_without_text_becomes_an_empty_user_message():
    compute = ClaudeCodeSessionCompute()
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"),)
    record = claude_user(cli_paste_wrapper(hybrid_block(CLAUDE_SID, *entries)))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == ""
    assert record["twicc_attachments"]["owner"] == CLAUDE_SID
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE


def test_wrapped_all_file_hybrid_block_is_extracted():
    compute = ClaudeCodeSessionCompute()
    record = claude_user(cli_paste_wrapper(f"Here\n\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}", "a1b2"))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "Here"
    assert record["twicc_attachments"]["entries"] == expected_entries(*ALL_FILE)


def test_a_wrapper_without_the_paste_id_is_also_unwrapped():
    compute = ClaudeCodeSessionCompute()
    record = claude_user(f"<pasted_content>\nHere\n\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}\n</pasted_content>")

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "Here"
    assert "twicc_attachments" in record


def test_a_paste_wrapper_goes_even_without_any_manifest():
    """For TwiCC the message is the pasted text, attachments or not (the wrapper is the CLI's bookkeeping)."""
    compute = ClaudeCodeSessionCompute()
    record = claude_user(cli_paste_wrapper("line one\nline two"))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == "line one\nline two"
    assert "twicc_attachments" not in record


def test_wrapped_block_with_a_wrong_owner_loses_the_wrapper_but_keeps_the_text():
    """No metadata is extracted (the block is not ours), but the wrapper is still the CLI's, not the user's."""
    compute = ClaudeCodeSessionCompute()
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"),)
    inner = f"hi\n\n{hybrid_block(OTHER_SID, *entries)}"
    record = claude_user(cli_paste_wrapper(inner))

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == inner
    assert "twicc_attachments" not in record


def test_text_outside_the_paste_wrapper_is_not_unwrapped():
    """Only a wrapper that is the whole string counts: typed text around it means a different message."""
    compute = ClaudeCodeSessionCompute()
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"),)
    content = f"typed before {cli_paste_wrapper(hybrid_block(CLAUDE_SID, *entries))}"
    record = claude_user(content)

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == content
    assert "twicc_attachments" not in record


def test_hybrid_owner_comes_from_the_reference_when_no_file_line():
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"),)
    message = {"content": f"x\n\n{hybrid_block(FORK_PARENT, *entries)}"}

    result = extract_attachments_block(
        (UserTextSlot(message, "content", "hybrid"),), {CODEX_SID, FORK_PARENT}, session_id=CODEX_SID,
    )

    assert result == {"owner": FORK_PARENT, "entries": expected_hybrid_entries(*entries)}


def test_hybrid_reference_keeps_the_basename_only():
    entries = (entry(1, "shot.png", "image", 1, 1, "inline"), entry(2, "scan", "image", 2, 2, "inline"))
    paths = (hybrid_path(CLAUDE_SID, "aaaaaaaaaaaa"), hybrid_path(CLAUDE_SID, "bbbbbbbbbbbb", ""))
    message = {"content": f"x\n\n{hybrid_block(CLAUDE_SID, *entries, paths=paths)}"}

    result = extract_attachments_block((UserTextSlot(message, "content", "hybrid"),), {CLAUDE_SID}, session_id=CLAUDE_SID)

    assert [e["reference"] for e in result["entries"]] == ["att_aaaaaaaaaaaa.png", "att_bbbbbbbbbbbb"]


def test_file_directory_owner_wins_over_the_hybrid_reference_owner():
    entries = (
        entry(1, "shot.png", "image", 1, 1, "inline"),
        entry(2, "movie.mp4", "video", 1, 1, "file", "movie.mp4"),
    )
    block = build_manifest(
        AttachmentManifest(FORK_PARENT, directory(FORK_PARENT), entries),
        hybrid_paths=(hybrid_path(CODEX_SID), None),
    )
    message = {"content": block}

    result = extract_attachments_block(
        (UserTextSlot(message, "content", "hybrid"),), {CODEX_SID, FORK_PARENT}, session_id=CODEX_SID,
    )

    assert result["owner"] == FORK_PARENT


def test_claude_queued_command_prompt_array_is_extracted():
    compute = ClaudeCodeSessionCompute()
    record = claude_queued([IMAGE_BLOCK, DOCUMENT_BLOCK, text(sdk_block(CLAUDE_SID, *MIXED)), text("also this")])
    rendered = copy.deepcopy(record["rendered"])

    assert transform(compute, record, session_id=CLAUDE_SID) is not None

    assert record["attachment"]["prompt"] == [IMAGE_BLOCK, DOCUMENT_BLOCK, text("also this")]
    assert record["twicc_attachments"]["entries"] == expected_entries(*MIXED)
    assert record["rendered"] == rendered


def test_claude_queued_command_in_another_mode_is_untouched():
    compute = ClaudeCodeSessionCompute()
    record = claude_queued([text(sdk_block(CLAUDE_SID, *ALL_FILE))], command_mode="bash")
    original = copy.deepcopy(record)

    assert transform(compute, record, session_id=CLAUDE_SID) is None
    assert record == original


# ---------------------------------------------------------------------------
# Codex response_item and canonical UserMessage
# ---------------------------------------------------------------------------


CODEX_ENTRIES = (
    entry(1, "a.png", "image", 1, 2, "inline"),
    entry(2, "movie.mp4", "video", 1, 1, "file", "movie.mp4"),
    entry(3, "b.png", "image", 2, 2, "inline"),
)


def test_codex_response_item_user_content_is_extracted():
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    image = {"type": "input_image", "image_url": DATA_URL, "detail": "high"}
    record = codex_response([image, image, input_text(sdk_block(CODEX_SID, *CODEX_ENTRIES)), input_text("hello")])

    assert transform(compute, record, session_id=CODEX_SID) is not None

    assert record["payload"]["content"] == [image, image, input_text("hello")]
    assert record["twicc_attachments"] == {"owner": CODEX_SID, "entries": expected_entries(*CODEX_ENTRIES)}


def test_codex_canonical_user_message_is_extracted_and_keeps_its_text():
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    helpers = CodexHelpers()
    image = {"type": "image", "image_url": DATA_URL}
    record = codex_user_message([
        image, image, canonical_text(sdk_block(CODEX_SID, *CODEX_ENTRIES)), canonical_text("hello"),
    ])

    transform(compute, record, session_id=CODEX_SID)

    assert record["payload"]["item"]["content"] == [image, image, canonical_text("hello")]
    assert user_message_text(record) == "hello"
    assert user_message_attachment_count(record) == 3
    assert helpers.extract_indexable_text_from_parsed(record) == "hello"
    assert compute.extract_title_from_user_message(record) == "hello"
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE


def test_codex_file_only_message_without_text_stays_a_user_message():
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    record = codex_user_message([canonical_text(sdk_block(CODEX_SID, *ALL_FILE))])

    transform(compute, record, session_id=CODEX_SID)

    assert record["payload"]["item"]["content"] == []
    assert user_message_text(record) is None
    assert user_message_is_visible(record) is True
    assert user_message_attachment_count(record) == 1
    assert compute.compute_item_kind(record) == ItemKind.USER_MESSAGE


# ---------------------------------------------------------------------------
# Rejections: invalid blocks stay content, no key is set
# ---------------------------------------------------------------------------


def _sdk_with(block: str, *, media=(IMAGE_BLOCK, DOCUMENT_BLOCK)) -> dict:
    return claude_user([*media, text(block), text("hi")])


GOOD = sdk_block(CLAUDE_SID, *MIXED)
INVALID_RECORDS = {
    "count_mismatch": lambda: _sdk_with(GOOD, media=(IMAGE_BLOCK,)),
    "too_many_media": lambda: _sdk_with(GOOD, media=(IMAGE_BLOCK, IMAGE_BLOCK, DOCUMENT_BLOCK)),
    "missing_intro": lambda: _sdk_with(GOOD.replace("Files the user attached to this message, in the order they attached them.\n", "")),
    "missing_inline_header": lambda: _sdk_with(GOOD.replace(
        "inline = sent to you with this message; the inline files appear above, in this same order.\n", "")),
    "missing_file_header": lambda: _sdk_with(GOOD.replace(f"file = {directory(CLAUDE_SID)}/\n", "")),
    "wrong_owner": lambda: _sdk_with(sdk_block(OTHER_SID, *MIXED)),
    "directory_not_attachments": lambda: _sdk_with(GOOD.replace("/attachments/", "/files/")),
    "misplaced_after_text": lambda: claude_user([IMAGE_BLOCK, DOCUMENT_BLOCK, text("hi"), text(GOOD)]),
    "misplaced_before_media": lambda: claude_user([text(GOOD), IMAGE_BLOCK, DOCUMENT_BLOCK, text("hi")]),
    "extra_block": lambda: claude_user([IMAGE_BLOCK, DOCUMENT_BLOCK, text(GOOD), text(f"hi\n\n{GOOD}")]),
    "crlf": lambda: _sdk_with(GOOD.replace("\n", "\r\n")),
    "hybrid_header_in_array": lambda: _sdk_with(hybrid_block(CLAUDE_SID, *MIXED)),
    "block_shares_its_entry": lambda: claude_user([IMAGE_BLOCK, DOCUMENT_BLOCK, text(f"{GOOD}\nhi")]),
    "hybrid_wrong_reference_owner": lambda: claude_user(
        f"hi\n\n{hybrid_block(CLAUDE_SID, *MIXED[:1], paths=(hybrid_path(OTHER_SID),))}"),
    "hybrid_wrong_reference_name": lambda: claude_user(
        f"hi\n\n{hybrid_block(CLAUDE_SID, *MIXED[:1], paths=(f'{DATA}/hybrid/{CLAUDE_SID}/shot.png',))}"),
    "hybrid_wrong_reference_hex": lambda: claude_user(
        f"hi\n\n{hybrid_block(CLAUDE_SID, *MIXED[:1], paths=(hybrid_path(CLAUDE_SID, '0123456789AB'),))}"),
    "hybrid_wrong_reference_folder": lambda: claude_user(
        f"hi\n\n{hybrid_block(CLAUDE_SID, *MIXED[:1], paths=(f'{DATA}/other/{CLAUDE_SID}/att_0123456789ab.png',))}"),
    "hybrid_sdk_header": lambda: claude_user(f"hi\n\n{sdk_block(CLAUDE_SID, *MIXED[:1])}"),
    "hybrid_no_blank_line": lambda: claude_user(f"hi\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}"),
    "hybrid_block_not_last": lambda: claude_user(f"hi\n\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}\n\nmore"),
    "hybrid_two_blocks": lambda: claude_user(
        f"{hybrid_block(CLAUDE_SID, *ALL_FILE)}\n\n{hybrid_block(CLAUDE_SID, *ALL_FILE)}"),
    "bad_entry_numbering": lambda: _sdk_with(GOOD.replace("3. spec.pdf", "4. spec.pdf")),
}


@pytest.mark.parametrize("name", sorted(INVALID_RECORDS))
def test_invalid_blocks_are_left_untouched(name):
    compute = ClaudeCodeSessionCompute()
    invalid_record = INVALID_RECORDS[name]()
    original_invalid_record = copy.deepcopy(invalid_record)

    assert transform(compute, invalid_record, session_id=CLAUDE_SID) is None

    assert invalid_record == original_invalid_record


def test_codex_count_mismatch_is_left_untouched():
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    record = codex_user_message([
        {"type": "image", "image_url": DATA_URL}, canonical_text(sdk_block(CODEX_SID, *CODEX_ENTRIES)),
    ])
    original = copy.deepcopy(record)

    assert transform(compute, record, session_id=CODEX_SID) is None
    assert record == original


def test_an_exact_pasted_all_file_block_with_the_current_owner_is_accepted():
    """Protocol limit (design §16): an exact pasted block passes the same checks."""
    compute = ClaudeCodeSessionCompute()
    record = claude_user([text(sdk_block(CLAUDE_SID, *ALL_FILE)), text("pasted")])

    transform(compute, record, session_id=CLAUDE_SID)

    assert record["message"]["content"] == [text("pasted")]
    assert "twicc_attachments" in record


def test_a_previously_extracted_key_is_preserved():
    compute = ClaudeCodeSessionCompute()
    previous = {"owner": CLAUDE_SID, "entries": expected_entries(*MIXED)}
    record = claude_user([text(sdk_block(CLAUDE_SID, *ALL_FILE)), text("hi")], twicc_attachments=previous)
    original = copy.deepcopy(record)

    assert transform(compute, record, session_id=CLAUDE_SID) is None
    assert record == original


# ---------------------------------------------------------------------------
# Records that are never user-message slots
# ---------------------------------------------------------------------------


BLOCK = sdk_block(CLAUDE_SID, *ALL_FILE)
UNTOUCHED_CLAUDE = {
    "tool_result": lambda: claude_user([{"type": "tool_result", "tool_use_id": "toolu_1", "content": BLOCK}]),
    "tool_result_text_list": lambda: claude_user([
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [text(BLOCK)]}, text(BLOCK),
    ]),
    "write_input": lambda: {
        **claude_user([]), "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Write", "input": {"file_path": "/x.md", "content": BLOCK}},
        ]},
    },
    "assistant_text": lambda: {
        **claude_user([]), "type": "assistant", "message": {"role": "assistant", "content": [text(BLOCK)]},
    },
    "sidechain": lambda: claude_user([text(BLOCK)], isSidechain=True),
    "meta": lambda: claude_user([text(BLOCK)], isMeta=True),
    "compact_summary": lambda: claude_user([text(BLOCK)], isCompactSummary=True),
    "last_prompt": lambda: {"type": "last-prompt", "lastPrompt": BLOCK, "sessionId": CLAUDE_SID},
}


@pytest.mark.parametrize("name", sorted(UNTOUCHED_CLAUDE))
def test_non_slot_claude_records_are_untouched(name):
    compute = ClaudeCodeSessionCompute()
    record = UNTOUCHED_CLAUDE[name]()
    original = copy.deepcopy(record)

    transform(compute, record, session_id=CLAUDE_SID)

    assert record == original


CODEX_BLOCK = sdk_block(CODEX_SID, *ALL_FILE)
UNTOUCHED_CODEX = {
    "compacted": lambda: {
        "timestamp": "2026-10-04T12:00:00.000Z", "type": "compacted",
        "payload": {"message": "", "replacement_history": [
            {"type": "message", "role": "user", "content": [input_text(CODEX_BLOCK)]},
        ]},
    },
    "function_call_output": lambda: {
        "timestamp": "2026-10-04T12:00:00.000Z", "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "call_1", "output": CODEX_BLOCK},
    },
    "assistant_message": lambda: {
        "timestamp": "2026-10-04T12:00:00.000Z", "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": CODEX_BLOCK}]},
    },
    "agent_message": lambda: {
        "timestamp": "2026-10-04T12:00:00.000Z", "type": "event_msg",
        "payload": {"type": "item_completed", "thread_id": CODEX_SID, "turn_id": "t",
                    "item": {"type": "AgentMessage", "id": "a", "content": [{"type": "Text", "text": CODEX_BLOCK}]}},
    },
}


@pytest.mark.parametrize("name", sorted(UNTOUCHED_CODEX))
def test_non_slot_codex_records_are_untouched(name):
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    record = UNTOUCHED_CODEX[name]()
    original = copy.deepcopy(record)

    transform(compute, record, session_id=CODEX_SID)

    assert record == original


# ---------------------------------------------------------------------------
# Codex fork owners: full recompute, live ingestion, caches
# ---------------------------------------------------------------------------


def _fork_lines(owner: str) -> list[dict]:
    """A forked rollout: own session_meta, the parent's copied session_meta, a copied user record."""
    return [
        codex_session_meta(CODEX_SID, forked_from_id=FORK_PARENT, parent_thread_id=SPAWN_PARENT, ordinal=0),
        codex_session_meta(FORK_PARENT, ordinal=1),
        codex_user_message([canonical_text(sdk_block(owner, *ALL_FILE)), canonical_text("copied")], ordinal=2,
                           thread_id=FORK_PARENT),
    ]


@pytest.fixture
def codex_fork_session(db, tmp_path):
    project = Project.objects.create(id="-tmp-attachments-ingestion")
    spawn_parent = Session.objects.create(id=SPAWN_PARENT, project=project, provider=Provider.CODEX)
    path = tmp_path / f"{CODEX_SID}.jsonl"
    path.touch()
    return Session.objects.create(
        id=CODEX_SID, project=project, provider=Provider.CODEX, file_path=str(path),
        type=SessionType.SUBAGENT, parent_session=spawn_parent,
    )


def _drain(result_queue, compute) -> None:
    while True:
        try:
            raw = result_queue.get_nowait()
        except queue.Empty:
            return
        message = orjson.loads(raw)
        if message.get("type") == "session_complete":
            compute.apply_session_complete(message)


def _stored(session_id: str, line_num: int) -> dict:
    return orjson.loads(SessionItem.objects.get(session_id=session_id, line_num=line_num).content)


@pytest.mark.parametrize("owner, accepted", [(FORK_PARENT, True), (CODEX_SID, True), (SPAWN_PARENT, False)])
def test_full_recompute_accepts_only_the_actual_fork_owner(codex_fork_session, owner, accepted):
    for line_num, record in enumerate(_fork_lines(owner), start=1):
        SessionItem.objects.create(session=codex_fork_session, line_num=line_num, content=orjson.dumps(record).decode())
    compute = CodexSessionCompute()
    result_queue = queue.Queue()

    compute.compute_session_metadata(CODEX_SID, result_queue, run_id=0)
    _drain(result_queue, compute)

    stored = _stored(CODEX_SID, 3)
    if accepted:
        assert stored["twicc_attachments"]["owner"] == owner
        assert stored["payload"]["item"]["content"] == [canonical_text("copied")]
    else:
        assert "twicc_attachments" not in stored
        assert stored == _fork_lines(owner)[2]
    assert CODEX_SID not in compute._fork_fields


def _append(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(f"{orjson.dumps(record).decode()}\n" for record in records)


def test_live_batch_latches_session_meta_before_the_copied_user_record(codex_fork_session):
    compute = CodexSessionCompute()
    path = Path(codex_fork_session.file_path)
    _append(path, _fork_lines(FORK_PARENT))

    drain_live_sync(compute, codex_fork_session, path)

    stored = _stored(CODEX_SID, 3)
    assert stored["twicc_attachments"]["owner"] == FORK_PARENT
    item = SessionItem.objects.get(session_id=CODEX_SID, line_num=3)
    assert item.kind == ItemKind.USER_MESSAGE


def test_live_ingestion_reads_line_one_already_stored(codex_fork_session):
    path = Path(codex_fork_session.file_path)
    lines = _fork_lines(FORK_PARENT)
    _append(path, lines[:2])
    drain_live_sync(CodexSessionCompute(), codex_fork_session, path)

    fresh = CodexSessionCompute()  # a restarted process: nothing cached
    _append(path, lines[2:])
    drain_live_sync(fresh, codex_fork_session, path)

    assert _stored(CODEX_SID, 3)["twicc_attachments"]["owner"] == FORK_PARENT


@pytest.mark.django_db
def test_live_seed_from_earlier_in_memory_line_one():
    compute = CodexSessionCompute()
    meta = codex_session_meta(CODEX_SID, forked_from_id=FORK_PARENT)
    user = _fork_lines(FORK_PARENT)[2]

    transform(compute, user, session_id=CODEX_SID, line_num=3, in_memory_items=[(1, None, meta)])

    assert user["twicc_attachments"]["owner"] == FORK_PARENT


@pytest.mark.django_db
def test_a_negative_live_seed_is_replaced_by_later_session_meta():
    compute = CodexSessionCompute()
    early = _fork_lines(FORK_PARENT)[2]
    original = copy.deepcopy(early)

    transform(compute, early, session_id=CODEX_SID, line_num=3, in_memory_items=[])
    assert early == original  # no DB seed: the fork owner is unknown

    transform(compute, codex_session_meta(CODEX_SID, forked_from_id=FORK_PARENT), session_id=CODEX_SID, line_num=1)
    later = _fork_lines(FORK_PARENT)[2]
    transform(compute, later, session_id=CODEX_SID, line_num=3, in_memory_items=[])

    assert later["twicc_attachments"]["owner"] == FORK_PARENT


def test_the_copied_parent_session_meta_does_not_replace_the_fork_owner():
    compute = CodexSessionCompute()
    compute.begin_session_compute(CODEX_SID)
    for line_num, record in enumerate(_fork_lines(FORK_PARENT), start=1):
        transform(compute, record, session_id=CODEX_SID, line_num=line_num)
        if line_num == 3:
            assert record["twicc_attachments"]["owner"] == FORK_PARENT


def test_one_compute_instance_keeps_fork_owners_per_session():
    compute = CodexSessionCompute()
    other_child, other_parent = "01a0cccc-0000-7000-8000-000000000003", "01a0dddd-0000-7000-8000-000000000004"
    compute.begin_session_compute(CODEX_SID)
    compute.begin_session_compute(other_child)
    transform(compute, codex_session_meta(CODEX_SID, forked_from_id=FORK_PARENT), session_id=CODEX_SID, line_num=1)
    transform(compute, codex_session_meta(other_child, forked_from_id=other_parent), session_id=other_child, line_num=1)

    crossed_a = codex_user_message([canonical_text(sdk_block(other_parent, *ALL_FILE))])
    crossed_b = codex_user_message([canonical_text(sdk_block(FORK_PARENT, *ALL_FILE))])
    own_a = codex_user_message([canonical_text(sdk_block(FORK_PARENT, *ALL_FILE))])
    transform(compute, crossed_a, session_id=CODEX_SID)
    transform(compute, crossed_b, session_id=other_child)
    transform(compute, own_a, session_id=CODEX_SID)

    assert "twicc_attachments" not in crossed_a
    assert "twicc_attachments" not in crossed_b
    assert own_a["twicc_attachments"]["owner"] == FORK_PARENT


# ---------------------------------------------------------------------------
# Readers of user text on actually transformed items
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("provider", ["claude", "hybrid", "codex"])
def test_user_text_readers_never_see_the_block(tmp_path, settings, provider):
    settings.TWICC_PASSWORD_HASH = ""
    project = Project.objects.create(id="-tmp-attachments-readers", directory=str(tmp_path))
    if provider == "codex":
        session_id, provider_value = CODEX_SID, Provider.CODEX
        records = [
            codex_session_meta(CODEX_SID),
            codex_user_message([{"type": "image", "image_url": DATA_URL},
                                canonical_text(sdk_block(CODEX_SID, *MIXED[:2])), canonical_text("Please review")]),
            codex_user_message([canonical_text(sdk_block(CODEX_SID, *ALL_FILE))], ordinal=12),
        ]
        compute, helpers = CodexSessionCompute(), CodexHelpers()
    else:
        session_id, provider_value = CLAUDE_SID, Provider.CLAUDE_CODE
        if provider == "claude":
            first = claude_user([IMAGE_BLOCK, text(sdk_block(CLAUDE_SID, *MIXED[:2])), text("Please review")])
            second = claude_user([text(sdk_block(CLAUDE_SID, *ALL_FILE))])
        else:
            first = claude_user(f"Please review\n\n{hybrid_block(CLAUDE_SID, *MIXED[:2])}")
            second = claude_user(hybrid_block(CLAUDE_SID, *ALL_FILE))
        records = [first, second]
        compute, helpers = ClaudeCodeSessionCompute(), ClaudeCodeHelpers()
    session = Session.objects.create(id=session_id, project=project, provider=provider_value)
    for line_num, record in enumerate(records, start=1):
        SessionItem.objects.create(session=session, line_num=line_num, content=orjson.dumps(record).decode())

    result_queue = queue.Queue()
    compute.compute_session_metadata(session_id, result_queue, run_id=0)
    _drain(result_queue, compute)

    items = list(SessionItem.objects.filter(session=session, kind=ItemKind.USER_MESSAGE).order_by("line_num"))
    assert len(items) == 2
    for item in items:
        assert "<twicc:attachments>" not in item.content
        assert "twicc_attachments" in orjson.loads(item.content)
    first_parsed = orjson.loads(items[0].content)
    assert compute.extract_title_from_user_message(first_parsed) == "Please review"
    assert helpers.extract_indexable_text_from_parsed(first_parsed) == "Please review"
    assert helpers.get_first_user_message(session_id) == "Please review"
    assert helpers.get_title_source(session_id) == "Please review"

    response = asyncio.run(AsyncClient().get(f"/api/projects/{project.id}/sessions/{session_id}/user-messages/"))
    assert response.status_code == 200
    assert [message["text"] for message in orjson.loads(response.content)["messages"]] == ["Please review"]
