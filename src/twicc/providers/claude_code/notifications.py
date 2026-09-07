"""Dependency-free parsing of persisted Claude task notifications."""
from __future__ import annotations

import logging
import re
from typing import NamedTuple

import xmltodict

logger = logging.getLogger(__name__)
_TASK_NOTIFICATION_TAG = '<task-notification>'
_TASK_NOTIFICATION_CLOSE_TAG = '</task-notification>'

_RESULT_OPEN_TAG = '<result>'
_RESULT_CLOSE_TAG = '</result>'
_SUMMARY_OPEN_TAG = '<summary>'
_SUMMARY_CLOSE_TAG = '</summary>'
_RE_TASK_ID = re.compile(r'<task-id>([^<]+)</task-id>')
_RE_TOOL_USE_ID = re.compile(r'<tool-use-id>([^<]+)</tool-use-id>')


def _extract_task_notification_fields(xml_str: str) -> tuple[str | None, str | None, str]:
    """
    Manually extract task-notification fields when xmltodict fails.

    Uses regex for simple single-value tags (task-id, tool-use-id) and
    positional extraction for <result> (opening tag to last closing tag)
    since result content may contain unescaped XML-like text.

    Returns:
        (tool_use_id, task_id, result_text)
    """
    m_tool_use = _RE_TOOL_USE_ID.search(xml_str)
    tool_use_id = m_tool_use.group(1).strip() if m_tool_use else None

    m_task = _RE_TASK_ID.search(xml_str)
    task_id = m_task.group(1).strip() if m_task else None

    result_text = ''
    open_idx = xml_str.find(_RESULT_OPEN_TAG)
    if open_idx != -1:
        close_idx = xml_str.rfind(_RESULT_CLOSE_TAG)
        if close_idx != -1 and close_idx > open_idx:
            result_text = xml_str[open_idx + len(_RESULT_OPEN_TAG):close_idx]

    # Fallback to <summary> if no <result> content
    if not result_text:
        open_idx = xml_str.find(_SUMMARY_OPEN_TAG)
        if open_idx != -1:
            close_idx = xml_str.rfind(_SUMMARY_CLOSE_TAG)
            if close_idx != -1 and close_idx > open_idx:
                result_text = xml_str[open_idx + len(_SUMMARY_OPEN_TAG):close_idx]

    return tool_use_id, task_id, result_text


class ParsedTaskNotification(NamedTuple):
    """A ``<task-notification>`` parsed and routed.

    ``is_task_result`` is the routing decision shared by the user-message
    and attachment rewrite branches: True means "completion of a spawned
    task (agent, workflow, …) whose payload belongs on the launching
    tool_use as a regular tool_result row"; False leaves the notification
    to the Monitor/background-command terminal handling.
    """
    tool_use_id: str | None
    task_id: str | None
    result_text: str
    status: str | None
    event: str | None
    is_task_result: bool


def _parse_task_notification(xml_str: str) -> ParsedTaskNotification:
    """Parse a ``<task-notification>`` XML string and decide its routing.

    The notification format changed over CLI versions:

    - old agent/workflow completions: ``<tool-use-id>`` + ``<result>``/
      ``<summary>``, no ``<status>``;
    - newer CLIs (async-by-default agents, ~2.1.18x+): agent/workflow
      completions carry ``<status>`` too, plus ``<result>`` and ``<usage>``;
    - Monitor terminals and background-command (Bash) completions:
      ``<tool-use-id>`` + ``<status>`` + ``<summary>``, but never a
      ``<result>``/``<usage>`` payload.

    So "has a ``<result>`` or ``<usage>`` payload, or predates ``<status>``"
    is the discriminator for task completions; the presence of ``<status>``
    alone is NOT (that was the old discriminator, and it misrouted the new
    agent/workflow completions to the Monitor-terminal rewrite).
    """
    try:
        notification = xmltodict.parse(xml_str)['task-notification']
        tool_use_id = notification.get('tool-use-id')
        task_id = notification.get('task-id')
        result_text = (
            notification.get('result', '')
            or notification.get('summary', '')
        )
        event_text = notification.get('event')
        status_text = notification.get('status')
        has_payload = 'result' in notification or 'usage' in notification
    except Exception:
        logger.info(
            "xmltodict failed for task-notification, "
            "falling back to manual extraction"
        )
        # Manual fallback covers only tool_use_id/task_id/result; <event>
        # and <status> stay None so malformed XML keeps routing to the
        # task-result rewrite (the historical behaviour).
        tool_use_id, task_id, result_text = _extract_task_notification_fields(xml_str)
        return ParsedTaskNotification(
            tool_use_id=tool_use_id,
            task_id=task_id,
            result_text=result_text,
            status=None,
            event=None,
            is_task_result=bool(tool_use_id),
        )

    # Validate types BEFORE the routing decision: a malformed notification
    # (e.g. a repeated tag makes xmltodict return a list) must not route to
    # the task-result rewrite with a non-string tool_use_id.
    tool_use_id = tool_use_id if isinstance(tool_use_id, str) else None
    task_id = task_id if isinstance(task_id, str) else None
    status_text = status_text if isinstance(status_text, str) else None
    event_text = event_text if isinstance(event_text, str) else None
    return ParsedTaskNotification(
        tool_use_id=tool_use_id,
        task_id=task_id,
        result_text=result_text,
        status=status_text,
        event=event_text,
        is_task_result=bool(tool_use_id) and (has_payload or not status_text),
    )


def _is_misrouted_task_result(stripped_xml: str) -> bool:
    """True when a notification stored as a Monitor terminal re-routes to a task result.

    Used by the repair pass in ``_transform_inline_provider``: ``stripped_xml``
    is a preserved original (already ``lstrip``-ped, starts with the
    ``<task-notification>`` tag) of an item previously rewritten as a Monitor
    terminal; re-run the routing to decide whether it must be restored.
    """
    close_idx = stripped_xml.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
    if close_idx == -1:
        return False
    xml_str = stripped_xml[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
    return _parse_task_notification(xml_str).is_task_result


class QueueCompletion(NamedTuple):
    """Terminal queue evidence, independent of compute and database modules."""
    task_id: str
    tool_use_id: str
    status: str | None


def parse_queue_completion(parsed_json: dict) -> QueueCompletion | None:
    """Read terminal enqueues, including old payload-only completion XML."""
    if parsed_json.get("type") != "queue-operation" or parsed_json.get("operation") != "enqueue":
        return None
    content = parsed_json.get("content")
    if not isinstance(content, str) or not content.lstrip().startswith(_TASK_NOTIFICATION_TAG):
        return None
    content = content.lstrip()
    end = content.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
    if end < 0:
        return None
    xml = content[:end + len(_TASK_NOTIFICATION_CLOSE_TAG)]
    note = _parse_task_notification(xml)
    # The legacy fallback deliberately omits status for rewrite compatibility.
    # Recover it here so malformed payload XML cannot turn a running event terminal.
    status_match = re.search(r"<status>([^<]+)</status>", xml)
    status = note.status or (status_match.group(1).strip() if status_match else None)
    if status is not None and status not in {"completed", "failed", "stopped", "cancelled", "canceled"}:
        return None
    if not note.task_id or not note.tool_use_id or not (status or note.is_task_result):
        return None
    return QueueCompletion(note.task_id, note.tool_use_id, status)
