"""A fork subagent opens with its prompt as a user message.

The CLI (2.1.302+, ``subagent_type: "fork"``) starts a fork's file with a copy of the
parent's launching ``Agent`` tool_use, then a user line made of its ``tool_result`` ack
and a ``<fork-boilerplate>`` text block. Left alone, the prompt shows only inside a tool
block, where a classic subagent shows it as the first user message.
"""

from __future__ import annotations

import orjson

from twicc.core.enums import ItemKind
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute

SID = "a9047329-3805-4b88-bfd9-c12187b165ad"
PROMPT = "Reply with the single word FORKED.\n\nThen stop."


def _tool_use_line(*, parent_uuid=None, subagent_type="fork", prompt=PROMPT) -> dict:
    return {
        "type": "assistant",
        "isSidechain": True,
        "parentUuid": parent_uuid,
        "sessionId": SID,
        "message": {
            "model": "claude-sonnet-5-5",
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Agent",
                "input": {"description": "Fork diagnostic", "prompt": prompt, "subagent_type": subagent_type},
            }],
        },
    }


def _ack_line() -> dict:
    return {
        "type": "user",
        "isSidechain": True,
        "parentUuid": "uuid-1",
        "sessionId": SID,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": [{"type": "text", "text": "Fork started — that fork is you."}]},
                {"type": "text", "text": "<fork-boilerplate>\nYou are a worker fork.\n</fork-boilerplate>\n\nYour directive: x"},
            ],
        },
    }


def _transform(record: dict) -> str | None:
    return ClaudeCodeSessionCompute().transform_inline(record, session_id=SID, line_num=2)


def _kind(record: dict):
    return ClaudeCodeSessionCompute().compute_item_kind(record)


def test_the_launch_tool_use_becomes_the_user_prompt() -> None:
    record = _tool_use_line()
    rewritten = orjson.loads(_transform(record))
    assert rewritten["type"] == "user"
    assert rewritten["message"] == {"role": "user", "content": PROMPT}
    assert _kind(rewritten) == ItemKind.USER_MESSAGE


def test_the_original_tool_use_is_kept_for_debugging() -> None:
    record = _tool_use_line()
    original = record["message"]
    _transform(record)
    assert record["twiccOriginalMessage"] == original


def test_the_ack_becomes_a_system_line_without_a_dangling_tool_result() -> None:
    record = _ack_line()
    rewritten = orjson.loads(_transform(record))
    assert _kind(rewritten) == ItemKind.SYSTEM
    assert not ClaudeCodeSessionCompute().is_tool_result_item(rewritten)


def test_the_raw_shapes_were_not_a_prompt() -> None:
    # The defect this fixes: neither raw line is a user message.
    assert _kind(_tool_use_line()) != ItemKind.USER_MESSAGE
    assert _kind(_ack_line()) != ItemKind.USER_MESSAGE


def test_a_fork_launched_mid_conversation_is_left_alone() -> None:
    # A subagent that itself launches a fork: the tool_use is mid-chain (parentUuid set).
    assert _transform(_tool_use_line(parent_uuid="uuid-0")) is None


def test_a_non_fork_launch_is_left_alone() -> None:
    assert _transform(_tool_use_line(subagent_type="general-purpose")) is None


def test_a_line_outside_a_sidechain_is_left_alone() -> None:
    record = _tool_use_line()
    record["isSidechain"] = False
    assert _transform(record) is None


def test_a_plain_tool_result_with_text_is_left_alone() -> None:
    record = _ack_line()
    record["message"]["content"][1]["text"] = "Tool loaded."
    assert _transform(record) is None
