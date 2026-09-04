from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import NamedTuple


class CanonicalImageGeneration(NamedTuple):
    id: str
    status: str
    revised_prompt: str | None
    result: str
    saved_path: str | None
    transparent_background: bool | None
    failure: object | None


def completed_item(record: dict) -> dict | None:
    if record.get("type") != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    return item if isinstance(item, dict) else None


def _item_of_type(record: dict, item_type: str) -> dict | None:
    item = completed_item(record)
    if item is None or item.get("type") != item_type:
        return None
    return item


def _content(item: dict | None) -> list[dict]:
    if item is None:
        return []
    content = item.get("content")
    if not isinstance(content, list):
        return []
    return [entry for entry in content if isinstance(entry, dict)]


def _joined_text(entries: list[dict], entry_type: str) -> str | None:
    """Concatenate the ``text`` of every ``entry_type`` entry, with no separator.

    Mirrors Codex's own ``UserMessageItem::message()`` / app-server history
    reducer. Whitespace-only text counts as no text — the legacy readers
    required a non-blank message, and TwiCC never renders a blank one — but
    the surrounding whitespace of a real message is preserved so callers
    that edit the text in place (``/plan`` prefixing, screenshot tags) see
    the exact persisted string.
    """
    parts = [
        entry.get("text")
        for entry in entries
        if entry.get("type") == entry_type and isinstance(entry.get("text"), str)
    ]
    text = "".join(parts)
    return text if text.strip() else None


def user_message_text(record: dict) -> str | None:
    return _joined_text(_content(_item_of_type(record, "UserMessage")), "text")


def user_message_is_visible(record: dict) -> bool:
    if user_message_text(record):
        return True
    return user_message_attachment_count(record) > 0


def user_message_attachment_count(record: dict) -> int:
    return sum(
        entry.get("type") in {"image", "local_image"}
        for entry in _content(_item_of_type(record, "UserMessage"))
    )


def agent_message_text(record: dict) -> str | None:
    return _joined_text(_content(_item_of_type(record, "AgentMessage")), "Text")


def canonical_result_item(record: dict) -> dict | None:
    item = completed_item(record)
    if item is None or item.get("type") not in {"FileChange", "McpToolCall"}:
        return None
    return item


def canonical_call_id(record: dict) -> str | None:
    item = canonical_result_item(record)
    if item is None:
        return None
    item_id = item.get("id")
    return item_id if isinstance(item_id, str) and item_id else None


def image_generation(record: dict) -> CanonicalImageGeneration | None:
    item = completed_item(record)
    if item is None:
        return None
    item_type = item.get("type")
    if item_type == "ImageGeneration":
        revised_prompt = item.get("revised_prompt")
        saved_path = item.get("saved_path")
        transparent_background = None
        failure = None
    elif item_type == "Extension" and item.get("kind") == "image_gen.generation":
        revised_prompt = item.get("revisedPrompt")
        saved_path = item.get("savedPath")
        transparent_background = item.get("transparentBackground")
        failure = item.get("failure")
    else:
        return None
    return CanonicalImageGeneration(
        id=item.get("id", ""),
        status=item.get("status", ""),
        revised_prompt=revised_prompt,
        result=item.get("result", ""),
        saved_path=saved_path,
        transparent_background=transparent_background,
        failure=failure,
    )


def _completed_at_ms(timestamp: object) -> int:
    if not isinstance(timestamp, str):
        return 0
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1000)


def _build_twicc_message(
    record: dict,
    *,
    session_id: str,
    line_num: int,
    item_type: str,
    content: list[dict],
) -> dict:
    built = deepcopy(record)
    built["twiccOriginalContent"] = deepcopy(record.get("payload"))
    built["type"] = "event_msg"
    built["payload"] = {
        "type": "item_completed",
        "thread_id": session_id,
        "turn_id": f"twicc-line-{line_num}",
        "item": {
            "type": item_type,
            "id": f"twicc-item-{line_num}",
            "content": content,
        },
        "completed_at_ms": _completed_at_ms(record.get("timestamp")),
    }
    return built


def build_twicc_user_message(
    record: dict,
    *,
    session_id: str,
    line_num: int,
    text: str,
) -> dict:
    return _build_twicc_message(
        record,
        session_id=session_id,
        line_num=line_num,
        item_type="UserMessage",
        content=[{"type": "text", "text": text, "text_elements": []}],
    )


def build_twicc_agent_message(
    record: dict,
    *,
    session_id: str,
    line_num: int,
    text: str,
) -> dict:
    return _build_twicc_message(
        record,
        session_id=session_id,
        line_num=line_num,
        item_type="AgentMessage",
        content=[{"type": "Text", "text": text}],
    )
