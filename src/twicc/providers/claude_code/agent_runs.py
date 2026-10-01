"""Pure parsers for the Claude agent-run signals.

Read by the Claude ``collect_agent_run_signals`` (batch) and
``apply_agent_run_signals`` (live) hooks, which turn them into
``AgentInteraction`` / ``AgentRunEnd`` rows. No database access here. Design:
``docs/plans/2026-09-26-subagent-runs-and-control-tools-design.md`` §4.1
(shapes), §5.1 (interactions), §5.2 (run ends) and §6.1 (Claude signals).
"""
from __future__ import annotations

from typing import NamedTuple

import orjson

from .notifications import _TASK_NOTIFICATION_TAG, read_task_notification_text

# Unlike ``parse_queue_completion`` (which keeps its own set for the link
# recovery and the batch stamping), ``killed`` is terminal here: it is the
# status of a ``TaskStop``'d agent (design §5.2).
TERMINAL_NOTIFICATION_STATUSES = frozenset(
    {"completed", "failed", "stopped", "killed", "cancelled", "canceled"})

# Control tools -> (interaction kind, input field holding the target).
SEND_MESSAGE_TOOL = "SendMessage"

_CONTROL_TOOLS = {
    SEND_MESSAGE_TOOL: ("message", "to"),
    "TaskStop": ("stop", "task_id"),
    "TaskOutput": ("output", "task_id"),
}

_QUEUED_MESSAGE_PREFIX = "Message queued"

PLAIN_INTERRUPT_MARKER = "[Request interrupted by user]"


class ControlCall(NamedTuple):
    tool_use_id: str
    kind: str  # "message" | "stop" | "output"
    target: str  # SendMessage input.to ; TaskStop/TaskOutput input.task_id


class RunEndNotification(NamedTuple):
    task_id: str
    tool_use_id: str
    status: str | None


def _message_content(parsed: dict):
    message = parsed.get("message")
    return message.get("content") if isinstance(message, dict) else None


def control_calls(parsed: dict) -> list[ControlCall]:
    """Every control-tool ``tool_use`` block of an assistant line, in block order.

    The target is not shape-checked: the read-time tree rule sorts agents
    from shells, workflows, ``"main"`` or peers (design §4.1, §5.1).
    """
    if parsed.get("type") != "assistant":
        return []
    content = _message_content(parsed)
    if not isinstance(content, list):
        return []
    found = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        control = _CONTROL_TOOLS.get(block.get("name"))
        tool_use_id = block.get("id")
        tool_input = block.get("input")
        if control is None or not isinstance(tool_use_id, str) or not tool_use_id or not isinstance(tool_input, dict):
            continue
        kind, field = control
        target = tool_input.get(field)
        if isinstance(target, str) and target:
            found.append(ControlCall(tool_use_id, kind, target))
    return found


def _tool_result_json(parsed: dict, tool_use_id: str) -> dict | None:
    """The JSON object in the text of the line's ``tool_result`` block for ``tool_use_id``."""
    content = _message_content(parsed)
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("tool_use_id") != tool_use_id:
            continue
        text = block.get("content")
        if isinstance(text, list):
            text = next((part.get("text") for part in text
                         if isinstance(part, dict) and part.get("type") == "text"), None)
        if not isinstance(text, str):
            return None
        try:
            data = orjson.loads(text)
        except orjson.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def send_message_opens_run(parsed: dict, tool_use_id: str) -> bool:
    """True when this ``SendMessage`` result is the resumed shape (design §6.1).

    Reads ``toolUseResult`` (root side), else the JSON text of the
    ``tool_result`` block (subagent side, sometimes without
    ``resumedAgentId``). A queued message, a failure, a ``tool_use_error``
    or unparsable text is not a resume.
    """
    data = parsed.get("toolUseResult")
    if not isinstance(data, dict):
        data = _tool_result_json(parsed, tool_use_id)
    if data is None:
        return False
    if "resumedAgentId" in data:
        return True
    message = data.get("message")
    return data.get("success") is True and not (
        isinstance(message, str) and message.startswith(_QUEUED_MESSAGE_PREFIX))


def _is_notification_text(value) -> bool:
    return isinstance(value, str) and value.lstrip().startswith(_TASK_NOTIFICATION_TAG)


def _attachment_prompt(entry: dict) -> str | None:
    attachment = entry.get("attachment")
    if (
        entry.get("type") == "attachment"
        and isinstance(attachment, dict)
        and attachment.get("type") == "queued_command"
        and attachment.get("commandMode") == "task-notification"
    ):
        return attachment.get("prompt")
    return None


def _notification_text(parsed: dict) -> str | None:
    """The original notification text of the line, in any of its three forms.

    The user and attachment forms are rewritten at ingest (and the dict the
    hooks see is the rewritten one), so the preserved original is read
    first. ``twiccOriginalContent`` is also set by the local-command rewrite:
    only a ``<task-notification>`` text counts.
    """
    entry_type = parsed.get("type")
    if entry_type == "queue-operation":
        content = parsed.get("content")
        return content if parsed.get("operation") == "enqueue" and _is_notification_text(content) else None
    original_entry = parsed.get("twiccOriginalEntry")
    if isinstance(original_entry, str):
        try:
            original = orjson.loads(original_entry)
        except orjson.JSONDecodeError:
            original = None
        prompt = _attachment_prompt(original) if isinstance(original, dict) else None
        return prompt if _is_notification_text(prompt) else None
    if entry_type == "attachment":
        prompt = _attachment_prompt(parsed)
        return prompt if _is_notification_text(prompt) else None
    if entry_type == "user":
        original_content = parsed.get("twiccOriginalContent")
        if _is_notification_text(original_content):
            return original_content
        content = _message_content(parsed)
        return content if _is_notification_text(content) else None
    return None


def carries_task_notification(parsed: dict) -> bool:
    """True when the line is a ``<task-notification>`` in any form, rewritten or not.

    The ingest rewrite turns a notification into a ``tool_result`` on its
    call: such a line is never a ``SendMessage`` first result.
    """
    return _notification_text(parsed) is not None


def run_end_notification(parsed: dict) -> RunEndNotification | None:
    """The run end a ``<task-notification>`` line carries (design §5.2), or ``None``.

    Written when it names a task and a tool_use, and its status is terminal
    or absent on a task result (old payload-only completions). A
    ``running`` notification, a Monitor fragment without ``<tool-use-id>``
    or a ``queue-operation`` ``remove`` line is none.
    """
    text = _notification_text(parsed)
    if text is None:
        return None
    note = read_task_notification_text(text)
    if note is None or not note.task_id or not note.tool_use_id:
        return None
    if note.status is not None:
        if note.status not in TERMINAL_NOTIFICATION_STATUSES:
            return None
    elif not note.is_task_result:
        return None
    return RunEndNotification(note.task_id, note.tool_use_id, note.status)


def is_plain_interrupt_marker(parsed: dict) -> bool:
    """True for a user line that is exactly ``[Request interrupted by user]`` (design §5.2, §6.1).

    Not ``isMeta``, no ``origin``; a string or a single text block. The
    ``… for tool use]`` variant and quoted text do not match.
    """
    if parsed.get("type") != "user" or parsed.get("isMeta") or parsed.get("origin") is not None:
        return False
    content = _message_content(parsed)
    if isinstance(content, list):
        if len(content) != 1 or not isinstance(content[0], dict) or content[0].get("type") != "text":
            return False
        content = content[0].get("text")
    return content == PLAIN_INTERRUPT_MARKER
