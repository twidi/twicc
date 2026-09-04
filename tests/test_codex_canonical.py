from __future__ import annotations

from twicc.core.enums import ItemKind
from twicc.providers.codex.canonical import (
    agent_message_text,
    build_twicc_agent_message,
    build_twicc_user_message,
    canonical_call_id,
    canonical_result_item,
    completed_item,
    image_generation,
    user_message_attachment_count,
    user_message_is_visible,
    user_message_text,
)
from twicc.providers.codex.compute import CodexSessionCompute


def _completed(item: dict) -> dict:
    return {
        "timestamp": "2026-08-31T10:00:00.000Z",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "item": item,
            "completed_at_ms": 1_788_170_400_000,
        },
    }


def test_completed_item_accepts_only_the_item_completed_wrapper():
    item = {"type": "UserMessage", "id": "u1", "content": []}
    assert completed_item(_completed(item)) is item
    assert completed_item({"type": "event_msg", "payload": {"type": "item_started", "item": item}}) is None
    assert completed_item({"type": "response_item", "payload": {"item": item}}) is None


def test_user_message_contract_preserves_raw_rich_entries_but_filters_semantics():
    record = _completed({
        "type": "UserMessage",
        "id": "u1",
        "content": [
            {"type": "text", "text": "hello", "text_elements": []},
            {"type": "skill", "name": "review", "path": "/skills/review/SKILL.md"},
            {"type": "text", "text": " world", "text_elements": []},
            {"type": "mention", "name": "calendar", "path": "app://calendar"},
            {"type": "image", "image_url": "data:image/png;base64,AA"},
            {"type": "local_image", "path": "/tmp/a.png"},
            {"type": "audio", "audio_url": "data:audio/wav;base64,AA"},
        ],
    })

    assert user_message_text(record) == "hello world"
    assert user_message_attachment_count(record) == 2
    assert user_message_is_visible(record) is True


def test_user_message_visibility_rejects_structured_metadata_only():
    metadata_only = _completed({
        "type": "UserMessage",
        "id": "u1",
        "content": [
            {"type": "skill", "name": "review", "path": "/skills/review/SKILL.md"},
            {"type": "mention", "name": "calendar", "path": "app://calendar"},
        ],
    })
    image_only = _completed({
        "type": "UserMessage",
        "id": "u2",
        "content": [{"type": "local_image", "path": "/tmp/a.png"}],
    })

    assert user_message_text(metadata_only) is None
    assert user_message_is_visible(metadata_only) is False
    assert user_message_is_visible(image_only) is True


def test_agent_message_joins_only_text_content():
    record = _completed({
        "type": "AgentMessage",
        "id": "a1",
        "content": [
            {"type": "Text", "text": "first"},
            {"type": "Unknown", "text": "ignored"},
            {"type": "Text", "text": " second"},
        ],
    })
    assert agent_message_text(record) == "first second"


def test_result_item_and_call_id_accept_only_supported_structured_results():
    file_change = _completed({
        "type": "FileChange",
        "id": "patch-1",
        "changes": {},
        "status": "completed",
    })
    mcp = _completed({
        "type": "McpToolCall",
        "id": "mcp-1",
        "server": "demo",
        "tool": "read",
        "arguments": {},
        "status": "failed",
        "error": {"message": "transport failed"},
    })
    unsupported = _completed({"type": "WebSearch", "id": "web-1"})

    assert canonical_result_item(file_change)["status"] == "completed"
    assert canonical_call_id(file_change) == "patch-1"
    assert canonical_result_item(mcp)["error"]["message"] == "transport failed"
    assert canonical_call_id(mcp) == "mcp-1"
    assert canonical_result_item(unsupported) is None
    assert canonical_call_id(unsupported) is None


def test_image_generation_normalizes_native_and_extension_items():
    native = image_generation(_completed({
        "type": "ImageGeneration",
        "id": "ig-1",
        "status": "completed",
        "revised_prompt": "blue square",
        "result": "cG5n",
        "saved_path": "/tmp/native.png",
    }))
    extension = image_generation(_completed({
        "type": "Extension",
        "kind": "image_gen.generation",
        "id": "ig-2",
        "status": "failed",
        "revisedPrompt": "red square",
        "result": "",
        "transparentBackground": True,
        "failure": {"type": "usageLimitExceeded", "limitId": "images"},
        "savedPath": "/tmp/extension.png",
    }))

    assert native == ("ig-1", "completed", "blue square", "cG5n", "/tmp/native.png", None, None)
    assert extension == (
        "ig-2",
        "failed",
        "red square",
        "",
        "/tmp/extension.png",
        True,
        {"type": "usageLimitExceeded", "limitId": "images"},
    )
    assert image_generation(_completed({"type": "Extension", "kind": "clock.sleep", "id": "s1"})) is None


def test_private_user_builder_emits_schema_valid_canonical_wrapper():
    source = {"timestamp": "2026-08-31T10:00:00Z", "type": "response_item", "payload": {"role": "user"}}
    built = build_twicc_user_message(source, session_id="thread-1", line_num=7, text="/compact")

    assert built == {
        "timestamp": "2026-08-31T10:00:00Z",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "thread_id": "thread-1",
            "turn_id": "twicc-line-7",
            "item": {
                "type": "UserMessage",
                "id": "twicc-item-7",
                "content": [{"type": "text", "text": "/compact", "text_elements": []}],
            },
            "completed_at_ms": 1_788_170_400_000,
        },
        "twiccOriginalContent": {"role": "user"},
    }


def test_private_agent_builder_preserves_outer_fields_and_uses_zero_for_bad_timestamp():
    source = {
        "timestamp": "not-a-date",
        "type": "response_item",
        "ordinal": 8,
        "payload": {"role": "assistant", "content": "old"},
    }
    built = build_twicc_agent_message(source, session_id="thread-1", line_num=8, text="answer")

    assert built["ordinal"] == 8
    assert built["twiccOriginalContent"] == source["payload"]
    assert built["payload"] == {
        "type": "item_completed",
        "thread_id": "thread-1",
        "turn_id": "twicc-line-8",
        "item": {
            "type": "AgentMessage",
            "id": "twicc-item-8",
            "content": [{"type": "Text", "text": "answer"}],
        },
        "completed_at_ms": 0,
    }


def test_compute_classifies_only_supported_canonical_completed_items():
    compute = CodexSessionCompute()

    assert compute.compute_item_kind(_completed({
        "type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": "hello"}],
    })) == ItemKind.USER_MESSAGE
    assert compute.compute_item_kind(_completed({
        "type": "AgentMessage", "id": "a1", "content": [{"type": "Text", "text": "answer"}],
    })) == ItemKind.ASSISTANT_MESSAGE
    assert compute.compute_item_kind(_completed({
        "type": "ImageGeneration", "id": "i1", "status": "completed", "result": "png",
    })) == ItemKind.IMAGE
    assert compute.compute_item_kind(_completed({"type": "Plan", "id": "p1", "text": "ignored"})) == ItemKind.SYSTEM


def test_compute_reads_canonical_file_change_as_a_structured_tool_result():
    compute = CodexSessionCompute()
    record = _completed({
        "type": "FileChange",
        "id": "patch-1",
        "status": "failed",
        "stderr": "cannot apply",
        "changes": {"/repo/docs/plan.md": {"type": "update", "unified_diff": "+new\n-old"}},
    })

    assert compute.is_tool_result_item(record) is True
    result = compute.extract_tool_result_info(record, session_id="thread-1")
    assert result.tool_use_id == "patch-1"
    assert result.is_error is True
    assert result.error_text == "cannot apply"
    assert compute.extract_paths_from_tool_uses(record) == ["/repo/docs/plan.md"]


def test_compute_reads_canonical_mcp_result_errors():
    compute = CodexSessionCompute()
    transport_error = _completed({
        "type": "McpToolCall", "id": "m1", "server": "demo", "tool": "read",
        "arguments": {}, "status": "failed", "error": {"message": "offline"},
    })
    tool_error = _completed({
        "type": "McpToolCall", "id": "m2", "server": "demo", "tool": "read",
        "arguments": {}, "status": "completed", "result": {"isError": True, "content": []},
    })

    assert compute.extract_tool_result_info(transport_error, session_id="thread-1").error_text == "offline"
    assert compute.extract_tool_result_info(tool_error, session_id="thread-1").error_text == "Tool error"


def test_compute_reads_canonical_subagent_started_item():
    compute = CodexSessionCompute()
    record = _completed({
        "type": "SubAgentActivity",
        "id": "spawn-call",
        "kind": "started",
        "agent_thread_id": "child-thread",
        "agent_path": "/root/child",
    })

    analysis = compute.analyze_content(record, session_id="thread-1", tool_use_map={})
    assert analysis.tool_result_agent_info == ("spawn-call", "child-thread", True)


def test_blank_text_counts_as_no_text():
    blank_user = _completed({
        "type": "UserMessage", "id": "u1",
        "content": [{"type": "text", "text": " \n\t", "text_elements": []}],
    })
    blank_with_image = _completed({
        "type": "UserMessage", "id": "u2",
        "content": [
            {"type": "text", "text": " ", "text_elements": []},
            {"type": "image", "image_url": "data:image/png;base64,AA"},
        ],
    })
    padded = _completed({
        "type": "UserMessage", "id": "u3",
        "content": [{"type": "text", "text": "  hi \n", "text_elements": []}],
    })
    blank_agent = _completed({"type": "AgentMessage", "id": "a1", "content": [{"type": "Text", "text": "  "}]})

    assert user_message_text(blank_user) is None
    assert user_message_is_visible(blank_user) is False
    assert user_message_is_visible(blank_with_image) is True
    # Surrounding whitespace of a real message is preserved: in-place
    # editors (``/plan`` prefixing, screenshot tags) see the persisted string.
    assert user_message_text(padded) == "  hi \n"
    assert agent_message_text(blank_agent) is None


def test_compute_keeps_an_empty_agent_message_as_an_assistant_message():
    # The frontend renders the "empty response" notice for it, as it did for
    # the legacy ``agent_message`` event. Only its visibility is text-driven.
    compute = CodexSessionCompute()
    empty = _completed({"type": "AgentMessage", "id": "a1", "content": []})

    assert compute.compute_item_kind(empty) == ItemKind.ASSISTANT_MESSAGE
    analysis = compute.analyze_content(empty, session_id="thread-1", tool_use_map={})
    assert analysis.has_visible_content is False
    assert analysis.text_content is None


def test_compute_strips_message_text_content():
    compute = CodexSessionCompute()
    record = _completed({
        "type": "UserMessage", "id": "u1",
        "content": [{"type": "text", "text": "  hello \n", "text_elements": []}],
    })
    analysis = compute.analyze_content(record, session_id="thread-1", tool_use_map={})
    assert analysis.has_visible_content is True
    assert analysis.text_content == "hello"
