"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import os
import re

import orjson
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import ClassVar, NamedTuple

import xmltodict
from django.db.models import Q

from twicc.context_injection import INSTRUCTION_BLOCK_MARKER
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Session, SessionItem, SessionType
from twicc.paths import get_artifacts_dir
from twicc.pricing import calculate_line_context_usage
from twicc.provider_homes import claude_plans_dir, claude_projects_dir
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    INSERT_SCREENSHOT_TAG_RE,
    ToolResultInfo,
    is_base64_image,
    parse_timestamp_to_datetime,
    strip_markdown,
    substitute_insert_screenshot_tags,
)
from twicc.providers.goals import GOAL_STATE_ACTIVE, GOAL_STATE_COMPLETED, GoalEvent
from twicc.providers.plan_docs import DocEditEvent, extract_shell_write_targets, is_plan_doc_path
from .agent.original_file_cache import pop_original_file
from .pricing import extract_model_info, to_token_usage


# Tool names that spawn subagent sessions (Task is the legacy name, Agent is the new one)
AGENT_TOOL_NAMES = frozenset({'Task', 'Agent'})

MONITOR_TOOL_NAME = 'Monitor'

# Built-in task-tracking tools whose tool_use blocks get the
# ``twiccTasksTotal`` field written alongside ``twiccTaskData`` and
# ``twiccTasksData`` in :meth:`ClaudeCodeSessionCompute._enrich_task_tool_uses`.
# ``twiccTasksTotal`` exists so the summary header can render
# "<id>/<total>". TaskCreate and TaskList don't need it (their summary
# headers don't render the ratio).
_TASK_LOOKUP_BY_ID_TOOLS = frozenset({'TaskUpdate', 'TaskGet'})

# Content types considered user-visible (for display_level and kind computation)
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')

# XML prefixes for system messages
# These are user messages that should be treated as debug-only
_SYSTEM_XML_PREFIXES = (
    '<local-command-',
    '<twicc-',
)

# Built-in slash commands that are settings/control noise, not real user input:
# their <command-name> user line is classified SYSTEM (hidden in normal view,
# like /clear). /model, /effort and /fast change agent-settings (model / effort /
# fast_mode); their <local-command-stdout> acks are dropped via
# _LOCAL_COMMAND_FILTERED_PREFIXES. Custom and action commands stay USER_MESSAGE.
_SYSTEM_SLASH_COMMANDS = frozenset({'/clear', '/model', '/effort', '/fast'})

# Prefix for task notification XML (background agent results)
_TASK_NOTIFICATION_TAG = '<task-notification>'
_TASK_NOTIFICATION_CLOSE_TAG = '</task-notification>'

# Turn-abort breadcrumbs the CLI writes as user messages when a turn is
# interrupted: "[Request interrupted by user]", "[Request interrupted by
# user for tool use]". Also matched by the hybrid JSONL bridge (a turn that
# ends this way never writes a turn_duration line).
INTERRUPTION_MARKER_PREFIX = '[Request interrupted by user'

logger = logging.getLogger(__name__)


# =============================================================================
# Workflow detection
# =============================================================================

# Claude Code's Workflow tool writes a run artifact named ``wf_*.json`` at the
# root of the session's ``<session_id>/workflows/`` folder (alongside a
# ``scripts/`` subfolder we ignore). The presence of such a file is what flips
# ``Session.has_workflows``. Both the watcher (path-shape match) and the batch
# probe below key off the same ``wf_`` prefix + ``.json`` suffix.
WORKFLOW_FILE_PREFIX = "wf_"
WORKFLOW_FILE_SUFFIX = ".json"


def session_folder_has_workflow_json(session_folder: Path) -> bool:
    """True if ``session_folder/workflows/`` holds at least one ``wf_*.json``
    file directly at its root.

    ``session_folder`` is the directory sitting next to a top-level session's
    JSONL (``<projects_dir>/<project_id>/<session_id>/``). The scan is lazy and
    stops at the first match; a missing ``workflows/`` folder (the common case)
    returns ``False`` without raising. Subfolders (e.g. ``workflows/scripts/``)
    are not descended into — only root entries count.
    """
    workflows_dir = session_folder / "workflows"
    try:
        with os.scandir(workflows_dir) as entries:
            for entry in entries:
                name = entry.name
                if (
                    name.startswith(WORKFLOW_FILE_PREFIX)
                    and name.endswith(WORKFLOW_FILE_SUFFIX)
                    and entry.is_file()
                ):
                    return True
    except (FileNotFoundError, NotADirectoryError):
        pass
    return False


# =============================================================================
# Git Directory Resolution
# =============================================================================

# Tools whose input contains file paths for git resolution
_TOOL_PATH_FIELDS: dict[str, str] = {
    'Read': 'file_path',
    'Edit': 'file_path',
    'Write': 'file_path',
    'Grep': 'path',
    'Glob': 'path',
}

# File-editing tools inspected for plan-doc detection (``input.file_path``).
# Includes MultiEdit (absent from _TOOL_PATH_FIELDS, which serves git
# resolution only); NotebookEdit is deliberately out (.ipynb is not a doc).
_DOC_EDIT_FILE_TOOLS = frozenset({'Write', 'Edit', 'MultiEdit'})


# =============================================================================
# Title Extraction from User Messages
# =============================================================================


def extract_text_from_content(content: str | list | None) -> str | None:
    """
    Extract text content from a user message content field.

    Args:
        content: Either a string or a list of content items

    Returns:
        The extracted text, or None if no text found
    """
    if not content:
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                text = item.get('text')
                if isinstance(text, str):
                    return text.strip()

    return None


def is_interruption_marker(text: str) -> bool:
    """True for the CLI's turn-abort breadcrumb user messages."""
    stripped = text.strip()
    return stripped.startswith(INTERRUPTION_MARKER_PREFIX) and stripped.endswith(']')


class ParsedCommand(NamedTuple):
    name: str
    message: str | None
    args: str | None


_RE_COMMAND_NAME = re.compile(r'<command-name>(.*?)</command-name>', re.DOTALL)
_RE_COMMAND_MESSAGE = re.compile(r'<command-message>(.*?)</command-message>', re.DOTALL)
_RE_COMMAND_ARGS = re.compile(r'<command-args>(.*?)</command-args>', re.DOTALL)

# ``/goal`` arguments that clear the active goal (the CLI's clear aliases). A
# clear emits no ``goal_status`` attachment, so it's detected from the command.
_GOAL_CLEAR_ARGS = frozenset({"clear", "stop", "off", "reset", "none", "cancel"})


def _goal_args_from_command_text(text: str) -> str | None:
    """Args of a raw ``/goal <args>`` command as stored in a ``queued_command``.

    Strips any ``<twicc:instruction>`` block TwiCC appended to the command.
    Returns the argument string (``""`` for a bare ``/goal``), or ``None`` when
    the text is not a ``/goal`` command.
    """
    stripped = text.strip()
    marker = stripped.find(INSTRUCTION_BLOCK_MARKER)
    if marker != -1:
        stripped = stripped[:marker].rstrip()
    if stripped == "/goal":
        return ""
    if stripped.startswith("/goal") and stripped[5:6].isspace():
        return stripped[5:].strip()
    return None


def extract_command(text: str) -> ParsedCommand | None:
    if not text.startswith("<command-"):
        return None
    # The CLI writes the command fields verbatim, NOT XML-escaped, so the content
    # can carry bare ``&``/``<``/``>`` (e.g. ``/goal ... echo 'a' && echo 'b'``).
    # A strict XML parser rejects those and we'd lose the whole command; the flat
    # structure lets us pull each field with a regex instead (mirrors the frontend
    # ``extractCommand`` and the manual task-notification fallback below).
    name_match = _RE_COMMAND_NAME.search(text)
    if not name_match or not (name := name_match.group(1)):
        return None
    message_match = _RE_COMMAND_MESSAGE.search(text)
    args_match = _RE_COMMAND_ARGS.search(text)
    return ParsedCommand(
        name=name,
        message=message_match.group(1) if message_match else None,
        args=args_match.group(1) if args_match else None,
    )


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


def _agent_launch_tool_use_id_from_sidecar(session_id: str, task_id: str) -> str | None:
    """Resolve a subagent's launching tool_use_id from its ``.meta.json`` sidecar.

    Recent CLIs re-notify a resumable agent on every stop, and only the stops
    that follow a tool_use of this session carry a ``<tool-use-id>`` in the
    ``<task-notification>`` XML. When the agent re-woke on its own (its own
    background child finished, a scheduled wake-up fired, …) the notification
    has a ``<task-id>`` but no ``<tool-use-id>`` — the launching tool_use must
    be recovered from the ``subagents/agent-<task_id>.meta.json`` sidecar the
    CLI writes next to the subagent's JSONL (``{"toolUseId": ...}``).

    ``session_id`` is the session whose file carries the notification; its
    stored ``file_path`` (``<project>/<id>.jsonl`` for a top-level session,
    ``<project>/<top>/subagents/agent-<id>.jsonl`` for a subagent) anchors the
    sidecar lookup. Returns ``None`` when anything is missing (unknown
    session, absent sidecar, malformed JSON) — callers fall back to leaving
    the notification untouched.
    """
    file_path = (
        Session.objects.filter(id=session_id)
        .values_list('file_path', flat=True)
        .first()
    )
    if not file_path:
        return None
    session_file = claude_projects_dir() / file_path
    if session_file.parent.name == 'subagents':
        # Nested case: the notification sits in a subagent's own file; its
        # children's sidecars live in the same flat subagents/ directory.
        subagents_dir = session_file.parent
    else:
        subagents_dir = session_file.with_suffix('') / 'subagents'
    meta_path = subagents_dir / f'agent-{task_id}.meta.json'
    try:
        meta = orjson.loads(meta_path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    tool_use_id = meta.get('toolUseId') if isinstance(meta, dict) else None
    return tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None


# Regex to strip ANSI escape codes from local command output
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# Regex to drop a decorative lead-in (status glyph, punctuation, whitespace, an
# opening paren, …) before prefix-matching a local command ack. Some CLI acks
# carry a leading glyph — e.g. "↯ Fast mode ON · $10/$50 per Mtok" or
# "(Compacted …)" — that would otherwise defeat the settings-ack prefixes.
_ACK_LEAD_RE = re.compile(r'^[^0-9a-z]+')

# Local command output tags (stdout and stderr)
_LOCAL_COMMAND_TAGS = (
    ('<local-command-stdout>', '</local-command-stdout>'),
    ('<local-command-stderr>', '</local-command-stderr>'),
)

# Prefixes/suffixes that indicate a local command output should be filtered out (not displayed).
# The "set model to" / "set effort level to" / "fast mode " prefixes are the acks the CLI emits
# when an agent setting (model / effort / fast_mode) is changed — pure settings noise, paired with
# the matching slash command classified via _SYSTEM_SLASH_COMMANDS below. Filtered acks are not
# rewritten into assistant messages; they stay <local-command-stdout>… and fall under SYSTEM.
_LOCAL_COMMAND_FILTERED_PREFIXES = (
    'compacted',
    'set model to',
    'set effort level to',
    'fast mode ',
    'session renamed to',
)
_LOCAL_COMMAND_FILTERED_SUFFIXES = ('dismissed', 'cancelled', 'no content')


def _extract_local_command_text(text: str) -> str | None:
    """
    Extract the text content from a ``<local-command-stdout>`` or
    ``<local-command-stderr>`` tag.

    Uses rfind for the closing tag to avoid issues if the closing tag
    appears inside the content itself.

    Returns the inner text, or ``None`` if no tag is found.
    """
    stripped = text.lstrip()
    for open_tag, close_tag in _LOCAL_COMMAND_TAGS:
        start_idx = stripped.find(open_tag)
        if start_idx == -1:
            continue
        content_start = start_idx + len(open_tag)
        close_idx = stripped.rfind(close_tag)
        if close_idx == -1 or close_idx < content_start:
            continue
        return stripped[content_start:close_idx]
    return None


def get_message_content(parsed_json: dict) -> list | str | None:
    message = parsed_json.get('message', None)
    if not isinstance(message, dict):
        return None
    return message.get('content')


def get_message_content_list(parsed_json: dict, expected_type: str | None = None) -> list | None:
    """
    Extract the content array from a message of the expected type.
    """
    if expected_type is not None and parsed_json.get("type") != expected_type:
        return None
    content = get_message_content(parsed_json)
    if not isinstance(content, list):
        return None
    return content


def _is_system_xml_content(content: str | list | None) -> bool:
    """
    Check if content is a system XML message (command invocation or output).

    Matches user messages whose text starts with a system XML prefix
    (e.g., <local-command-stdout>, <twicc-cron-restart>).

    Handles both string content and list content with a single text entry.

    Args:
        content: Message content (string or list)

    Returns:
        True if the content is a system XML message
    """
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and len(content) == 1:
        item = content[0]
        if isinstance(item, dict) and item.get('type') == 'text':
            text = item.get('text')
    if text is None:
        return False
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)


def _has_visible_content(content: str | list | None) -> bool:
    """
    Check if message content contains user-visible content.

    User-visible content types are: text, document, image.

    Args:
        content: Message content (string or list of content items)

    Returns:
        True if content is a string or contains at least one visible content item
    """
    if not content:
        return False

    if isinstance(content, str):
        return True

    if not isinstance(content, list):
        return False

    for item in content:
        if isinstance(item, dict) and item.get('type') in VISIBLE_CONTENT_TYPES:
            return True

    return False


_TASK_TOOL_NAMES = frozenset({'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList'})

# The model sometimes spells a task tool's keys the "close but incorrect" way
# (``id`` / ``task_id`` for ``taskId``, ``active_form`` for ``activeForm``).
# Claude Code repairs those names before running the tool, but the JSONL records
# the raw input, so the aliases reach compute unrepaired — reading only the
# canonical spelling silently drops the call (measured on real transcripts:
# 39 of 556 TaskUpdate blocks, ~7%, spell the id ``task_id`` or ``id``).
# Order matters: the canonical spelling wins when several are present.
_TASK_ID_KEYS = ('taskId', 'id', 'task_id')


def _canonical_task_input(tool_input: dict) -> dict:
    """Return ``tool_input`` with the aliased key spellings canonicalised.

    Only ``activeForm`` is rewritten here: the id aliases are handled by each
    caller (TaskCreate drops them, TaskUpdate resolves the task through them).
    Canonicalising at ingestion keeps every stored task dict on the canonical
    shape, so readers — including the frontend's ``tasksDataToTodos`` mirror —
    need no alias awareness.
    """
    active_form = tool_input.get('active_form')
    if not isinstance(active_form, str) or 'activeForm' in tool_input:
        return tool_input
    canonical = dict(tool_input)
    canonical['activeForm'] = canonical.pop('active_form')
    return canonical


def _extract_tasks_snapshot(parsed_json: dict) -> list[dict] | None:
    """Return the **last** ``twiccTasksData`` list embedded in an assistant
    message's tool_use blocks. None when not found or malformed.

    A single assistant message can carry several task tool_use blocks
    (parallel tool calls). Each enriched block's ``twiccTasksData`` is
    the state right after that block was applied, so the last one is
    the most up-to-date snapshot for the whole message.
    """
    content = get_message_content_list(parsed_json, 'assistant')
    if content is None:
        return None
    last: list[dict] | None = None
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'tool_use':
            continue
        snapshot = block.get('twiccTasksData')
        if isinstance(snapshot, list):
            last = snapshot
    return last


def _iter_task_tool_use_blocks(parsed_json: dict):
    """Yield tool_use blocks whose name is one of the four task tools."""
    content = get_message_content_list(parsed_json, 'assistant')
    if content is None:
        return
    for block in content:
        if (
            isinstance(block, dict)
            and block.get('type') == 'tool_use'
            and block.get('name') in _TASK_TOOL_NAMES
        ):
            yield block


# Fields spliced into task tool_use blocks by _enrich_task_tool_uses.
_TASK_ENRICHMENT_KEYS = ('twiccTaskData', 'twiccTasksData', 'twiccTasksTotal')


class _SessionTaskState:
    """Per-session in-memory task-replay state (see ``_enrich_task_tool_uses``).

    * ``tasks`` — insertion-ordered ``task_id -> task dict`` (order of
      creation, mirrored into every ``twiccTasksData`` snapshot).
    * ``seen_tool_use_ids`` — ids of every task tool_use block already
      encountered for the session. Compaction re-appends the retained
      history lines verbatim to the JSONL (same ``uuid``, same tool_use
      ``id``), so a task block whose id was already seen is a duplicate of
      an earlier line and must never advance the state — replaying it would
      re-create every task (the "list shown N times" bug).
    * ``duplicates_seen`` — latched True at the first duplicate. Snapshots
      stored on blocks enriched *after* a duplicate polluted the state are
      corrupted; once the flag is set, already-enriched blocks are
      re-derived from their tool input instead of being kept immutable.
    """

    __slots__ = ('duplicates_seen', 'seen_tool_use_ids', 'tasks')

    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.seen_tool_use_ids: set[str] = set()
        self.duplicates_seen = False


# Legacy task tracking: older Claude Code sessions use the ``TodoWrite`` tool
# (a full-list replacement) rather than the incremental ``Task*`` tools. Only
# the frontend renders it; the backend keeps no state for it.
_TODO_WRITE_TOOL_NAME = 'TodoWrite'


def _normalize_todos(todos) -> list[dict] | None:
    """Normalise a ``TodoWrite.todos`` array to the cross-provider task shape.

    All-or-nothing (mirrors the frontend ``isValidTodos``): every entry must
    carry a string ``status`` plus at least one of ``content`` / ``activeForm``
    — a single bad entry invalidates the whole list (``None``). The array is
    already in the common shape, so this only validates and drops extra keys.
    """
    if not isinstance(todos, list) or not todos:
        return None
    normalized: list[dict] = []
    for todo in todos:
        if not isinstance(todo, dict):
            return None
        status = todo.get('status')
        content = todo.get('content')
        active_form = todo.get('activeForm')
        if not isinstance(status, str):
            return None
        if not isinstance(content, str) and not isinstance(active_form, str):
            return None
        item: dict = {'status': status}
        if isinstance(content, str):
            item['content'] = content
        if isinstance(active_form, str):
            item['activeForm'] = active_form
        normalized.append(item)
    return normalized


def _tasks_data_to_todos(tasks_data) -> list[dict] | None:
    """Map a spliced ``twiccTasksData`` snapshot to the common task shape.

    Mirrors the frontend ``tasksDataToTodos``: project each task's ``subject``
    onto ``content`` (and pass ``activeForm`` through), skipping malformed
    entries individually (not all-or-nothing). Returns ``None`` when nothing
    usable remains.
    """
    if not isinstance(tasks_data, list):
        return None
    normalized: list[dict] = []
    for task in tasks_data:
        if not isinstance(task, dict):
            continue
        status = task.get('status')
        if not isinstance(status, str):
            continue
        content = task.get('subject')
        active_form = task.get('activeForm')
        if not isinstance(content, str) and not isinstance(active_form, str):
            continue
        item: dict = {'status': status}
        if isinstance(content, str):
            item['content'] = content
        if isinstance(active_form, str):
            item['activeForm'] = active_form
        normalized.append(item)
    return normalized or None


# =============================================================================
# Live Sync — watcher entry point
# =============================================================================


# =============================================================================
# ClaudeCodeSessionCompute — concrete BaseSessionCompute for Claude Code
# =============================================================================


class ClaudeCodeSessionCompute(BaseSessionCompute):
    """
    Concrete compute pipeline for Claude Code sessions.

    The full :class:`BaseSessionCompute` surface — extraction, live
    machinery, batch (analyze_content + compute_session_metadata +
    apply_session_complete), and watcher live sync
    (sync_session_items_from_file) — is wired here. Each method
    delegates to a matching free function defined earlier in this file.

    Per-instance state held by this class:
      * ``_monitor_task_to_tool_use_id`` — per-session map for the Monitor
        tool aggregation (see :meth:`begin_session_compute`).
      * ``_session_task_states`` — per-session in-memory task state
        (:class:`_SessionTaskState`) used by
        :meth:`_enrich_task_tool_uses` to snapshot the task list at every
        task tool_use. Reconstructed lazily on first touch (see
        :meth:`_rebuild_state_if_missing`). Pruned per session in batch via
        :meth:`begin_session_compute` / :meth:`end_session_compute`; in the
        live watcher it grows monotonically over the process lifetime —
        bounded growth is acceptable for typical install scales (a few KB
        per session, dozens to low hundreds of sessions per long-running
        process).

    :func:`get_compute` returns a per-process singleton.
    """

    provider: ClassVar[Provider] = Provider.CLAUDE_CODE

    def __init__(self) -> None:
        super().__init__()
        self._monitor_task_to_tool_use_id: dict[str, dict[str, str]] = {}
        # Per-process in-memory task state, indexed by session_id (see
        # _SessionTaskState). Reconstructed lazily on the first
        # transform_inline that needs it (see _rebuild_state_if_missing).
        self._session_task_states: dict[str, _SessionTaskState] = {}

    def begin_session_compute(self, session_id: str) -> None:
        self._monitor_task_to_tool_use_id[session_id] = {}
        self._session_task_states.pop(session_id, None)

    def end_session_compute(self, session_id: str) -> None:
        self._monitor_task_to_tool_use_id.pop(session_id, None)
        self._session_task_states.pop(session_id, None)

    def extra_session_fields(self, session: Session) -> dict:
        # Detect whether this session has any workflow run (a ``wf_*.json`` at
        # the root of its ``<session_id>/workflows/`` folder). Only top-level
        # sessions own such a folder — subagents live under
        # ``<session_id>/subagents/`` and are explicitly out of scope.
        #
        # ``has_workflows`` is emitted ONLY when found, so a later recompute
        # never resets an already-True flag (one-way latch). The watcher latches
        # the same flag live when a ``wf_*.json`` first appears.
        if session.type != SessionType.SESSION or not session.file_path:
            return {}
        # ``file_path`` is ``<project_id>/<session_id>.jsonl`` relative to the
        # projects dir; dropping the ``.jsonl`` suffix yields the session's
        # sibling folder.
        session_folder = claude_projects_dir() / Path(session.file_path).with_suffix("")
        if session_folder_has_workflow_json(session_folder):
            return {"has_workflows": True}
        return {}

    def extract_doc_edit_events(self, parsed_json: dict, *, cwd: str | None) -> list[DocEditEvent]:
        # Plan-doc writes: ``Write``/``Edit``/``MultiEdit`` tool_use blocks
        # (``input.file_path``, absolute by tool contract) and ``Bash``
        # commands run through the shared shell heuristic. ``NotebookEdit``
        # is ignored (.ipynb is not a document).
        content = get_message_content_list(parsed_json, "assistant")
        if not content:
            return []
        events: list[DocEditEvent] = []
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            tool_input = block.get('input')
            if not isinstance(tool_input, dict):
                continue
            tool_name = block.get('name')
            if tool_name in _DOC_EDIT_FILE_TOOLS:
                file_path = tool_input.get('file_path')
                if isinstance(file_path, str) and file_path:
                    if not os.path.isabs(file_path):
                        if not cwd:
                            continue
                        file_path = os.path.join(cwd, file_path)
                    if is_plan_doc_path(file_path):
                        events.append(DocEditEvent(file_path, 'write'))
            elif tool_name == 'Bash':
                command = tool_input.get('command')
                if command:
                    for target, action in extract_shell_write_targets(command):
                        path = target if os.path.isabs(target) else (os.path.join(cwd, target) if cwd else None)
                        if path and is_plan_doc_path(path):
                            events.append(DocEditEvent(path, action))
        return events

    def extra_doc_edit_events(
        self, session: Session, *, last_slug: str | None,
    ) -> list[tuple[DocEditEvent, datetime | str | None]]:
        # The native plan-mode file is written by the Claude CLI itself (no
        # tool call in the JSONL): probe ``<claude home>/plans/<slug>.md`` so
        # the authoritative rebuild seeds/keeps its entry. The plans watcher
        # latches the same entry live.
        slug = last_slug or session.slug
        if not slug:
            return []
        plan_path = claude_plans_dir() / f"{slug}.md"
        try:
            mtime = plan_path.stat().st_mtime
        except OSError:
            return []
        timestamp = datetime.fromtimestamp(mtime, tz=UTC)
        return [(DocEditEvent(str(plan_path), 'write', 'claude_plan'), timestamp)]

    # ------------------------------------------------------------------
    # In-memory task state machinery
    # ------------------------------------------------------------------

    def _next_task_id(self, tasks: dict[str, dict]) -> str:
        """Sequential id allocator. First id is '1', then max(ids)+1."""
        if not tasks:
            return "1"
        return str(max(int(k) for k in tasks) + 1)

    def _apply_task_create(self, tasks: dict[str, dict], tool_input: dict) -> dict | None:
        """Add a new task to state. Returns the new task dict, or None
        when the input is malformed (missing subject).

        Note: nested mutable values from ``tool_input`` (e.g. lists in
        ``addBlocks``, dicts in ``metadata``) are stored by reference. The
        embedded snapshot at the call site uses ``dict(task)`` (shallow copy),
        so any later in-place mutation of those nested values would corrupt
        historical snapshots. Current code only ever reassigns task fields,
        never mutates them in place — preserve this invariant.
        """
        subject = tool_input.get('subject')
        if not isinstance(subject, str) or not subject:
            return None
        tool_input = _canonical_task_input(tool_input)
        new_id = self._next_task_id(tasks)
        # Merge all input fields as-is, then default status to 'pending'
        # and set our authoritative id. Any incoming id spelling is dropped
        # (TaskCreate input shouldn't carry one; defensive).
        task = {
            **{k: v for k, v in tool_input.items() if k not in _TASK_ID_KEYS},
            'status': 'pending',
            'id': new_id,
        }
        tasks[new_id] = task
        return task

    def _apply_task_update(self, tasks: dict[str, dict], tool_input: dict) -> dict | None:
        """Merge update fields into the existing task. Returns the updated
        task dict, or None when the task id is missing or unknown. The id is
        read through every spelling the model uses (see ``_TASK_ID_KEYS``).

        Mutation pattern: each input field reassigns the key on the existing
        task dict (``existing[k] = v``). Nested mutable values from the input
        are stored by reference. Do not mutate nested values in place (lists,
        dicts) — embedded snapshots in already-enriched blocks share the
        references via shallow ``dict(task)`` copies.
        """
        task_id = None
        for key in _TASK_ID_KEYS:
            candidate = tool_input.get(key)
            if isinstance(candidate, str) and candidate:
                task_id = candidate
                break
        if task_id is None:
            return None
        existing = tasks.get(task_id)
        if existing is None:
            return None
        for k, v in _canonical_task_input(tool_input).items():
            if k in _TASK_ID_KEYS:
                continue
            existing[k] = v
        return existing

    def _rebuild_state_if_missing(self, session_id: str, current_line_num: int) -> _SessionTaskState:
        """Ensure self._session_task_states[session_id] is populated
        consistently with the session's items already persisted in DB
        up to (but not including) current_line_num.

        Algorithm:
          1. If state already exists, return it.
          2. Initialise empty state.
          3. Find the latest SessionItem (line_num < current_line_num)
             whose content contains 'twiccTasksData'. Use that snapshot
             to seed the task dict.
          4. Walk every task tool_use item from the beginning of the
             session, registering each block's tool_use id in
             ``seen_tool_use_ids`` (so compaction-duplicated lines are
             recognised — see :class:`_SessionTaskState`). TaskCreate /
             TaskUpdate blocks after the snapshot (and not duplicated)
             also advance the task dict.
        """
        state = self._session_task_states.get(session_id)
        if state is not None:
            return state

        state = _SessionTaskState()
        self._session_task_states[session_id] = state

        # Pre-filter on the literal substring 'twiccTasksData' to avoid
        # scanning every item. False positives are rare (a tool_result
        # text could in theory mention the string) and benign:
        # _extract_tasks_snapshot returns None for items that don't carry
        # a real assistant tool_use snapshot, which falls back to
        # replay_after_line=0 — a slower but still correct rebuild.
        snapshot_item = (
            SessionItem.objects
            .filter(
                session_id=session_id,
                line_num__lt=current_line_num,
                content__contains='twiccTasksData',
            )
            .order_by('-line_num')
            .first()
        )

        replay_after_line = 0
        if snapshot_item is not None:
            try:
                parsed = orjson.loads(snapshot_item.content)
            except orjson.JSONDecodeError:
                parsed = None
            snapshot = _extract_tasks_snapshot(parsed) if parsed else None
            if snapshot is not None:
                for task in snapshot:
                    if not isinstance(task, dict):
                        continue
                    task_id = task.get('id')
                    if isinstance(task_id, str):
                        state.tasks[task_id] = dict(task)
                replay_after_line = snapshot_item.line_num

        # Same idea here: pre-filter on the literal tool_use name
        # substring. _iter_task_tool_use_blocks discriminates further
        # (block.type == 'tool_use' and block.name in _TASK_TOOL_NAMES),
        # so false positives from tool_results / user_messages mentioning
        # those strings are safely dropped. The walk starts at line 1 (not
        # after the snapshot): pre-snapshot blocks register their tool_use
        # ids in ``seen_tool_use_ids`` without touching the task dict, so
        # a compaction-duplicated line landing later is recognised.
        replay_items = (
            SessionItem.objects
            .filter(
                session_id=session_id,
                line_num__lt=current_line_num,
            )
            .filter(
                Q(content__contains='"name":"TaskCreate"')
                | Q(content__contains='"name":"TaskUpdate"')
                | Q(content__contains='"name":"TaskGet"')
                | Q(content__contains='"name":"TaskList"')
            )
            .order_by('line_num')
        )
        for item in replay_items:
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            for block in _iter_task_tool_use_blocks(parsed):
                block_id = block.get('id')
                if isinstance(block_id, str) and block_id:
                    if block_id in state.seen_tool_use_ids:
                        state.duplicates_seen = True
                        continue
                    state.seen_tool_use_ids.add(block_id)
                if item.line_num <= replay_after_line:
                    continue
                name = block.get('name')
                tool_input = block.get('input') or {}
                if name == 'TaskCreate':
                    self._apply_task_create(state.tasks, tool_input)
                elif name == 'TaskUpdate':
                    self._apply_task_update(state.tasks, tool_input)

        return state

    def _enrich_task_tool_uses(self, content: list, session_id: str, line_num: int) -> bool:
        """In-memory enrichment of the four task-tracking tool_use blocks.

        For each tool_use of name TaskCreate / TaskUpdate / TaskGet /
        TaskList in ``content``:
          * If the block's tool_use id was already seen for this session,
            the line is a compaction duplicate (compaction re-appends the
            retained history verbatim): the state is NOT advanced and any
            stored enrichment is stripped (a duplicate enriched before this
            dedup existed carries a corrupted snapshot).
          * If the block already carries ``twiccTasksData`` (TaskList path)
            and no duplicate was seen so far, the block is left untouched
            (immutability) and the in-memory state is reset from the
            snapshot so subsequent blocks remain consistent. Once a
            duplicate was seen, stored snapshots are no longer trusted:
            the enrichment is dropped and re-derived from the tool input
            (deterministic replay — identical on healthy blocks).
          * Blocks carrying ``twiccTaskData`` only (legacy disk-based
            by-id) stay immutable: the disk store could resolve tasks
            never created in this transcript, which a replay cannot.
          * Otherwise, the in-memory state is advanced and the block is
            enriched with ``twiccTaskData`` (when applicable),
            ``twiccTasksData`` (always), and ``twiccTasksTotal`` (only
            for by-id tools matching ``_TASK_LOOKUP_BY_ID_TOOLS``).

        Returns True if any block was mutated.
        """
        mutated = False
        state: _SessionTaskState | None = None

        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            name = block.get('name')
            if name not in _TASK_TOOL_NAMES:
                continue

            if state is None:
                state = self._rebuild_state_if_missing(session_id, line_num)

            block_id = block.get('id')
            has_block_id = isinstance(block_id, str) and bool(block_id)

            # --- Compaction-duplicate path ---
            if has_block_id and block_id in state.seen_tool_use_ids:
                state.duplicates_seen = True
                for key in _TASK_ENRICHMENT_KEYS:
                    if key in block:
                        del block[key]
                        mutated = True
                continue
            if has_block_id:
                state.seen_tool_use_ids.add(block_id)

            # --- Immutability paths ---
            if 'twiccTaskData' in block and 'twiccTasksData' not in block:
                # Legacy by-id block enriched with twiccTaskData only (no
                # twiccTasksData). Immutable, but we have no full snapshot
                # to restore state from. Skip; rely on the next snapshot
                # or reconstruction to recover state.
                continue

            if 'twiccTasksData' in block:
                if not state.duplicates_seen:
                    snapshot = block.get('twiccTasksData')
                    if isinstance(snapshot, list):
                        state.tasks.clear()
                        for task in snapshot:
                            if not isinstance(task, dict):
                                continue
                            task_id = task.get('id')
                            if isinstance(task_id, str):
                                state.tasks[task_id] = dict(task)
                    continue
                # A duplicate polluted the state before this block was
                # enriched: its stored snapshot is corrupted. Drop the
                # enrichment and fall through to re-derive it from the
                # tool input against the deduplicated replay state.
                for key in _TASK_ENRICHMENT_KEYS:
                    block.pop(key, None)
                mutated = True

            # --- Advance path ---
            tool_input = block.get('input') or {}

            if name == 'TaskCreate':
                task = self._apply_task_create(state.tasks, tool_input)
                if task is None:
                    continue
                block['twiccTaskData'] = dict(task)
            elif name == 'TaskUpdate':
                task = self._apply_task_update(state.tasks, tool_input)
                if task is None:
                    continue
                block['twiccTaskData'] = dict(task)
            elif name == 'TaskGet':
                task_id = tool_input.get('taskId')
                if isinstance(task_id, str) and task_id in state.tasks:
                    block['twiccTaskData'] = dict(state.tasks[task_id])
                # If taskId unknown, no twiccTaskData written. We still
                # attach the list snapshot + total below.

            # All four task tools reach this point in the "advance" path.
            # TaskCreate / TaskUpdate / TaskGet may have written
            # twiccTaskData above (or skipped via 'continue' on bad input);
            # TaskList simply falls through here — no state advance, just
            # the list snapshot attached below.
            block['twiccTasksData'] = [dict(t) for t in state.tasks.values()]

            if name in _TASK_LOOKUP_BY_ID_TOOLS:
                block['twiccTasksTotal'] = len(state.tasks)

            mutated = True

        return mutated

    def extract_tasks_payload(self, parsed_json: dict) -> dict | None:
        """Latest task/todo state on an assistant line, in the common shape.

        Two sources, both already materialised on the line by the time this
        runs (after :meth:`_transform_inline_provider`):

          * the incremental ``Task*`` tools — each enriched block carries a
            full ``twiccTasksData`` snapshot (see
            :meth:`_enrich_task_tool_uses`), mapped via :func:`_tasks_data_to_todos`;
          * the legacy ``TodoWrite`` tool — ``input.todos`` is already a full
            list, validated via :func:`_normalize_todos`.

        A single assistant message rarely mixes both; we walk the blocks in
        document order and keep the last task-bearing one, so the result is the
        message's final state. Returns ``None`` for non-assistant lines or
        lines without a valid task block.
        """
        content = get_message_content_list(parsed_json, 'assistant')
        if content is None:
            return None
        items: list[dict] | None = None
        source: str | None = None
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            name = block.get('name')
            if name in _TASK_TOOL_NAMES:
                snapshot = _tasks_data_to_todos(block.get('twiccTasksData'))
                if snapshot is not None:
                    items, source = snapshot, name
            elif name == _TODO_WRITE_TOOL_NAME:
                todos = _normalize_todos((block.get('input') or {}).get('todos'))
                if todos is not None:
                    items, source = todos, _TODO_WRITE_TOOL_NAME
        if items is None:
            return None
        return {'source': source, 'items': items, 'explanation': None}

    def extract_goal_event(self, parsed_json: dict) -> GoalEvent | None:
        """Goal lifecycle from a ``goal_status`` / ``queued_command`` attachment
        or a ``/goal clear`` command.

        ``/goal`` is a native CLI Stop hook: after each turn the evaluator writes
        an ``attachment`` of type ``goal_status`` carrying ``condition``, ``met``
        (bool) and — on the first line of a (re)stated goal — ``sentinel: true``.
        The sentinel line is the (re)definition signal (its ``condition`` is the
        objective); later non-sentinel lines only report progress (``met`` flips
        to ``true`` on completion).

        A ``/goal`` sent while the agent is busy is parked as a ``queued_command``
        attachment and does NOT emit a ``goal_status`` until it's dequeued and
        run — which can be a long time on a running goal. Reading the objective
        (or clear) straight from the queued command text reflects the change
        right away; the dedup in :func:`~twicc.providers.goals.apply_goal_event`
        absorbs the later ``goal_status`` re-statement, so no duplicate lands.

        A manual ``/goal clear`` (when not queued) writes no ``goal_status``, so
        it's read from the slash command instead.
        """
        if parsed_json.get('type') == 'attachment':
            attachment = parsed_json.get('attachment')
            if not isinstance(attachment, dict):
                return None
            atype = attachment.get('type')
            if atype == 'goal_status':
                met = bool(attachment.get('met'))
                condition = attachment.get('condition')
                restated = bool(attachment.get('sentinel')) and isinstance(condition, str) and bool(condition)
                return GoalEvent(
                    objective=condition if restated else None,
                    state=GOAL_STATE_COMPLETED if met else GOAL_STATE_ACTIVE,
                    raw_state='met' if met else 'unmet',
                )
            if atype == 'queued_command':
                prompt = attachment.get('prompt')
                if isinstance(prompt, list):
                    cmd_text = ''.join(
                        b.get('text', '') for b in prompt
                        if isinstance(b, dict) and b.get('type') == 'text'
                    )
                elif isinstance(prompt, str):
                    cmd_text = prompt
                else:
                    cmd_text = ''
                args = _goal_args_from_command_text(cmd_text)
                if args:
                    if args.lower() in _GOAL_CLEAR_ARGS:
                        return GoalEvent(cleared=True)
                    return GoalEvent(objective=args, state=GOAL_STATE_ACTIVE, raw_state='unmet')
            return None
        if parsed_json.get('type') == 'user':
            text = extract_text_from_content((parsed_json.get('message') or {}).get('content'))
            if text:
                command = extract_command(text)
                if (
                    command is not None
                    and command.name.lstrip('/') == 'goal'
                    and (command.args or '').strip().lower() in _GOAL_CLEAR_ARGS
                ):
                    return GoalEvent(cleared=True)
        return None

    def _substitute_screenshots_in_content(
        self,
        content: list,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None,
    ) -> bool:
        """Replace ``<twicc:insert-screenshot />`` tags in assistant text blocks.

        Walks ``content`` looking for ``{type: "text", text: ...}``
        blocks that carry at least one tag, and rewrites the text via
        :func:`substitute_insert_screenshot_tags`. The lookup of prior
        images is delegated to
        :meth:`BaseSessionCompute.iter_images_backward`, which combines
        the live-batch ``in_memory_items`` with a per-provider DB query.

        Returns ``True`` when at least one text block was modified.
        """
        mutated = False
        artifacts_dir: Path | None = None
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'text':
                continue
            text = block.get('text')
            if not isinstance(text, str) or not text:
                continue
            # Cheap fast-path: avoid building a callable / artifacts dir
            # when no tag is present.
            if not INSERT_SCREENSHOT_TAG_RE.search(text):
                continue
            if artifacts_dir is None:
                artifacts_dir = get_artifacts_dir()
            new_text = substitute_insert_screenshot_tags(
                text,
                session_id=session_id,
                images_provider=lambda needed: self.iter_images_backward(
                    session_id=session_id,
                    before_line_num=line_num,
                    images_needed=needed,
                    in_memory_items=in_memory_items,
                ),
                artifacts_dir=artifacts_dir,
            )
            if new_text != text:
                block['text'] = new_text
                mutated = True
        return mutated

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _transform_inline_provider(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> str | None:
        # Claude-Code-specific rewrites:
        #   1. enrich TaskCreate / TaskUpdate / TaskGet / TaskList tool_use
        #      blocks with twiccTaskData / twiccTasksData / twiccTasksTotal,
        #      computed from an in-memory per-session task state that's
        #      reconstructed from the tool_use inputs themselves (see
        #      _enrich_task_tool_uses + _rebuild_state_if_missing);
        #   2. substitute ``<twicc:insert-screenshot />`` tags in
        #      assistant text blocks with markdown image links pointing
        #      at base64 images extracted from prior tool_results
        #      (see :meth:`_substitute_screenshots_in_content`);
        #   3. populate the session-scoped Monitor task→tool_use_id map
        #      from each Monitor tool_result's ``toolUseResult.taskId``
        #      (side-effect only, no content rewrite);
        #   4. ``<task-notification>`` XML user messages — three flavours,
        #      routed by :func:`_parse_task_notification` (see its
        #      docstring for the format history): task result
        #      (agent/workflow completion), Monitor terminal /
        #      background-command completion (user_message variant,
        #      SDK ≥ 2.1.142), and Monitor task notification fragment
        #      (XML carries only ``<task-id>`` + ``<event>``,
        #      ``tool_use_id`` resolved via the session-scoped map);
        #   5. ``attachment`` (queued_command / task-notification)
        #      variants of the same notifications — task results rewritten
        #      like step 4, terminals into a synthetic tool_result
        #      carrying ``twiccMonitorTerminal=True``;
        #   6. CLI local command outputs wrapped in
        #      ``<local-command-stdout/stderr>`` tags.
        # Steps 4, 5, and 6 are normalised in place into the regular
        # tool_result / assistant message formats so the rest of the
        # pipeline doesn't need to care.

        # --- Repair pass ---
        # Task-notifications misrouted to the Monitor-terminal rewrite
        # before _parse_task_notification learned the new agent/workflow
        # completion shape (newer completions carry <status> too, which was
        # the old discriminator). The stored row is a
        # twiccMonitorTerminal tool_result whose content is just the status
        # string, but the original notification survives
        # (twiccOriginalContent for the user variant, twiccOriginalEntry
        # for the attachment variant): when it is a task result under the
        # current routing, restore the original in place and fall through
        # so the normal branches re-run. Live lines never carry these keys,
        # so this only fires on recompute.
        if parsed_json.get('twiccMonitorTerminal'):
            original_content = parsed_json.get('twiccOriginalContent')
            original_entry = parsed_json.get('twiccOriginalEntry')
            if isinstance(original_content, str):
                orig_stripped = original_content.lstrip()
                if (
                    orig_stripped.startswith(_TASK_NOTIFICATION_TAG)
                    and _is_misrouted_task_result(orig_stripped)
                ):
                    message = parsed_json.get('message')
                    if isinstance(message, dict):
                        message['content'] = original_content
                        parsed_json.pop('twiccOriginalContent', None)
                        parsed_json.pop('twiccMonitorTerminal', None)
            elif isinstance(original_entry, str):
                try:
                    restored = orjson.loads(original_entry)
                except orjson.JSONDecodeError:
                    restored = None
                restored_attachment = (
                    restored.get('attachment') if isinstance(restored, dict) else None
                )
                restored_prompt = (
                    restored_attachment.get('prompt')
                    if isinstance(restored_attachment, dict) else None
                )
                if (
                    isinstance(restored_prompt, str)
                    and restored_prompt.lstrip().startswith(_TASK_NOTIFICATION_TAG)
                    and _is_misrouted_task_result(restored_prompt.lstrip())
                ):
                    parsed_json.clear()
                    parsed_json.update(restored)

        entry_type = parsed_json.get('type')

        # --- Assistant-side rewrites: task enrichment + screenshot
        # substitution. Both apply to entry_type == 'assistant', and may
        # mutate ``parsed_json`` independently. Accumulate the flags so
        # that we return the rewritten payload once if either fired.
        if entry_type == 'assistant':
            mutated = False
            content = get_message_content_list(parsed_json, 'assistant')
            if content is not None:
                if self._enrich_task_tool_uses(content, session_id, line_num):
                    mutated = True
                if self._substitute_screenshots_in_content(
                    content,
                    session_id=session_id,
                    line_num=line_num,
                    in_memory_items=in_memory_items,
                ):
                    mutated = True
            if mutated:
                return orjson.dumps(parsed_json).decode('utf-8')

        # --- Monitor tool_result side-effect: index its taskId so later
        # task-notification user_messages can be rewritten as tool_results
        # attached to the original tool_use_id. No content rewrite here —
        # only the map is populated.
        if entry_type == 'user':
            session_id = parsed_json.get('sessionId')
            if isinstance(session_id, str) and session_id:
                tool_use_result = parsed_json.get('toolUseResult')
                if isinstance(tool_use_result, dict):
                    task_id = tool_use_result.get('taskId')
                    if isinstance(task_id, str) and task_id:
                        content = get_message_content_list(parsed_json, 'user')
                        if content:
                            for block in content:
                                if (
                                    isinstance(block, dict)
                                    and block.get('type') == 'tool_result'
                                    and isinstance(block.get('tool_use_id'), str)
                                ):
                                    self._monitor_task_to_tool_use_id.setdefault(
                                        session_id, {}
                                    )[task_id] = block['tool_use_id']
                                    break

        # --- task-notification XML (three flavours, dispatched by
        # _parse_task_notification — see its docstring for the format
        # history and the routing rules):
        #   - task result (agent/workflow completion): rewrite as a normal
        #     tool_result row on the launching tool_use, surface <task-id>
        #     as agentId (+ isAsync) so the subagent UI pairs up.
        #   - Monitor terminal / background-command completion
        #     (user_message variant, SDK ≥ 2.1.142): <tool-use-id> +
        #     <status> without a result payload → rewrite as a synthetic
        #     terminal tool_result with twiccMonitorTerminal flag,
        #     mirroring the legacy attachment-format terminal further below.
        #   - Monitor fragment: only <task-id> + <event> (no <tool-use-id>)
        #     → look up tool_use_id via the session-scoped map and rewrite
        #     as a regular tool_result row.
        if entry_type == 'user':
            message = parsed_json.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if isinstance(content, str):
                    stripped = content.lstrip()
                    if stripped.startswith(_TASK_NOTIFICATION_TAG):
                        close_idx = stripped.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
                        if close_idx != -1:
                            xml_str = stripped[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
                            note = _parse_task_notification(xml_str)

                            # --- Task result (agent/workflow completion) ---
                            if note.is_task_result:
                                parsed_json['twiccOriginalContent'] = content
                                block = {
                                    'type': 'tool_result',
                                    'tool_use_id': note.tool_use_id,
                                    'content': note.result_text,
                                }
                                # New-format notifications carry a status; a
                                # non-completed one (killed/failed agent) is
                                # an error result.
                                if note.status and note.status != 'completed':
                                    block['is_error'] = True
                                message['content'] = [block]
                                if note.task_id:
                                    # A notification only ever closes an
                                    # asynchronous launch, so isAsync here
                                    # backfills backgroundness when the ack
                                    # line didn't (older CLIs).
                                    parsed_json['toolUseResult'] = {
                                        'agentId': note.task_id,
                                        'isAsync': True,
                                    }
                                return orjson.dumps(parsed_json).decode('utf-8')

                            # --- Monitor terminal / background-command ---
                            if note.tool_use_id and note.status:
                                is_error = note.status != 'completed'
                                parsed_json['twiccOriginalContent'] = content
                                message['content'] = [{
                                    'type': 'tool_result',
                                    'tool_use_id': note.tool_use_id,
                                    'content': note.status,
                                    'is_error': is_error,
                                    'twiccMonitorTerminal': True,
                                }]
                                parsed_json['twiccMonitorTerminal'] = True
                                session_id = parsed_json.get('sessionId')
                                if isinstance(session_id, str) and note.task_id:
                                    self._monitor_task_to_tool_use_id.get(
                                        session_id, {}
                                    ).pop(note.task_id, None)
                                return orjson.dumps(parsed_json).decode('utf-8')

                            # --- Monitor task notification fragment ---
                            # No <tool-use-id> in the XML, but <task-id> resolvable
                            # via the session-scoped map and <event> present.
                            session_id = parsed_json.get('sessionId')
                            if (
                                isinstance(session_id, str)
                                and note.task_id
                                and note.event
                            ):
                                mapped = (
                                    self._monitor_task_to_tool_use_id
                                    .get(session_id, {})
                                    .get(note.task_id)
                                )
                                if mapped:
                                    parsed_json['twiccOriginalContent'] = content
                                    message['content'] = [{
                                        'type': 'tool_result',
                                        'tool_use_id': mapped,
                                        'content': note.event,
                                    }]
                                    return orjson.dumps(parsed_json).decode('utf-8')

                            # --- Orphan agent notification (no <tool-use-id>) ---
                            # A resumable agent that re-woke on its own (its own
                            # background child finished, …) stops again without a
                            # triggering tool_use in THIS session, so its terminal
                            # notification carries no <tool-use-id>. Recover the
                            # launching tool_use from the agent's .meta.json
                            # sidecar and rewrite as a regular task result.
                            # ``isAsync`` is deliberately NOT set here: this is
                            # never a launch ack, and flagging it would flip a
                            # foreground launching link to background (see
                            # create_agent_link_from_tool_result).
                            if note.task_id and not note.tool_use_id:
                                launch_tool_use_id = _agent_launch_tool_use_id_from_sidecar(
                                    session_id, note.task_id,
                                )
                                if launch_tool_use_id:
                                    parsed_json['twiccOriginalContent'] = content
                                    block = {
                                        'type': 'tool_result',
                                        'tool_use_id': launch_tool_use_id,
                                        'content': note.result_text,
                                    }
                                    if note.status and note.status != 'completed':
                                        block['is_error'] = True
                                    message['content'] = [block]
                                    parsed_json['toolUseResult'] = {
                                        'agentId': note.task_id,
                                    }
                                    return orjson.dumps(parsed_json).decode('utf-8')

                            # Fall through — no rewrite applied (an unresolved
                            # notification still classifies as SYSTEM via its
                            # origin.kind, see compute_item_kind).

        # --- attachment queued_command task-notification ---
        # Same notifications as the user_message variant above (a given
        # notification lands as exactly one of the two shapes, depending on
        # when it arrives), so the same two rewrites apply:
        #   - task result (agent/workflow completion) → synthetic
        #     user/tool_result row on the launching tool_use, with
        #     agentId/isAsync surfaced in toolUseResult;
        #   - Monitor / background-command terminal → synthetic terminal
        #     tool_result that compute_link_extra will flag with
        #     is_terminated:true; non-"completed" statuses surface as
        #     ToolResultLink.error through extract_tool_result_info.
        if entry_type == 'attachment':
            attachment = parsed_json.get('attachment')
            if (
                isinstance(attachment, dict)
                and attachment.get('type') == 'queued_command'
                and attachment.get('commandMode') == 'task-notification'
            ):
                prompt_text = attachment.get('prompt')
                if isinstance(prompt_text, str):
                    stripped = prompt_text.lstrip()
                    if stripped.startswith(_TASK_NOTIFICATION_TAG):
                        close_idx = stripped.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
                        if close_idx != -1:
                            xml_str = stripped[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
                            note = _parse_task_notification(xml_str)

                            # --- Task result (agent/workflow completion) ---
                            if note.is_task_result:
                                original_entry = orjson.dumps(parsed_json).decode('utf-8')
                                block = {
                                    'type': 'tool_result',
                                    'tool_use_id': note.tool_use_id,
                                    'content': note.result_text,
                                }
                                if note.status and note.status != 'completed':
                                    block['is_error'] = True
                                parsed_json['type'] = 'user'
                                parsed_json['message'] = {
                                    'role': 'user',
                                    'content': [block],
                                }
                                if note.task_id:
                                    parsed_json['toolUseResult'] = {
                                        'agentId': note.task_id,
                                        'isAsync': True,
                                    }
                                # Whole-entry snapshot — same rationale as the
                                # terminal rewrite below.
                                parsed_json['twiccOriginalEntry'] = original_entry
                                parsed_json.pop('attachment', None)
                                return orjson.dumps(parsed_json).decode('utf-8')

                            # --- Monitor / background-command terminal ---
                            if note.tool_use_id and note.status:
                                terminal_tool_use_id = note.tool_use_id
                                terminal_task_id = note.task_id
                                terminal_status = note.status
                                original_entry = orjson.dumps(parsed_json).decode('utf-8')
                                is_error = terminal_status != 'completed'
                                # Rewrite top-level shape into a synthetic user/tool_result
                                # entry compatible with extract_tool_result_info. The
                                # twiccMonitorTerminal flag is set in two places on purpose:
                                # the block-level copy lets the frontend aggregator skip the
                                # terminal chunk from the concatenated body (only
                                # ``message.content[0]`` is reachable via getParsedContent),
                                # while the top-level copy lets compute_link_extra flip
                                # ToolResultLink.extra to {"is_terminated": true} so the
                                # spinner stops without counting result rows.
                                parsed_json['type'] = 'user'
                                parsed_json['message'] = {
                                    'role': 'user',
                                    'content': [{
                                        'type': 'tool_result',
                                        'tool_use_id': terminal_tool_use_id,
                                        'content': terminal_status,
                                        'is_error': is_error,
                                        'twiccMonitorTerminal': True,
                                    }],
                                }
                                parsed_json['twiccMonitorTerminal'] = True
                                # Whole-entry snapshot (not a single content field) — the attachment
                                # has no single "content" field; the debug-worthy payload is the
                                # original parsed_json. Distinct key from twiccOriginalContent to
                                # signal the different shape to any future consumer.
                                parsed_json['twiccOriginalEntry'] = original_entry
                                # Drop the attachment key — the rewritten shape no longer
                                # carries one.
                                parsed_json.pop('attachment', None)

                                # Purge the per-task map entry: this Monitor's stream is
                                # complete, no more fragments will arrive.
                                session_id = parsed_json.get('sessionId')
                                if (
                                    isinstance(session_id, str)
                                    and isinstance(terminal_task_id, str)
                                ):
                                    self._monitor_task_to_tool_use_id.get(
                                        session_id, {}
                                    ).pop(terminal_task_id, None)

                                return orjson.dumps(parsed_json).decode('utf-8')

                            # --- Orphan agent notification (no <tool-use-id>) ---
                            # Same case as the user_message variant above: the
                            # agent re-woke on its own, so its stop notification
                            # has no launching tool_use to reference. Resolve it
                            # from the .meta.json sidecar; no ``isAsync`` (never
                            # a launch ack — see the user_message branch).
                            if note.task_id and not note.tool_use_id:
                                launch_tool_use_id = _agent_launch_tool_use_id_from_sidecar(
                                    session_id, note.task_id,
                                )
                                if launch_tool_use_id:
                                    original_entry = orjson.dumps(parsed_json).decode('utf-8')
                                    block = {
                                        'type': 'tool_result',
                                        'tool_use_id': launch_tool_use_id,
                                        'content': note.result_text,
                                    }
                                    if note.status and note.status != 'completed':
                                        block['is_error'] = True
                                    parsed_json['type'] = 'user'
                                    parsed_json['message'] = {
                                        'role': 'user',
                                        'content': [block],
                                    }
                                    parsed_json['toolUseResult'] = {
                                        'agentId': note.task_id,
                                    }
                                    # Whole-entry snapshot — same rationale as the
                                    # terminal rewrite above.
                                    parsed_json['twiccOriginalEntry'] = original_entry
                                    parsed_json.pop('attachment', None)
                                    return orjson.dumps(parsed_json).decode('utf-8')

        # --- local-command-stdout/stderr -> synthetic assistant_message ---
        raw_text: str | None = None
        if entry_type == 'system' and parsed_json.get('subtype') == 'local_command':
            content = parsed_json.get('content', '')
            if isinstance(content, str):
                raw_text = _extract_local_command_text(content)
        elif entry_type == 'user':
            message = parsed_json.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if isinstance(content, str):
                    raw_text = _extract_local_command_text(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            extracted = _extract_local_command_text(block.get('text', ''))
                            if extracted is not None:
                                raw_text = extracted
                                break

        if raw_text is None:
            return None

        # Strip ANSI escape codes and whitespace.
        text = _ANSI_RE.sub('', raw_text).strip()

        # Filter out empty or non-interesting messages.
        if not text:
            return None
        text_lower = text.lower()
        # Strip any decorative lead-in (glyph / "(" / whitespace) so acks like
        # "↯ Fast mode ON …" and "(Compacted …)" match the prefixes below.
        ack_lead_stripped = _ACK_LEAD_RE.sub('', text_lower)
        if any(
            ack_lead_stripped.startswith(prefix)
            for prefix in _LOCAL_COMMAND_FILTERED_PREFIXES
        ):
            return None
        if any(
            text_lower.endswith((suffix, suffix + ")"))
            for suffix in _LOCAL_COMMAND_FILTERED_SUFFIXES
        ):
            return None

        # Preserve original content for debugging.
        if entry_type == 'system':
            parsed_json['twiccOriginalContent'] = parsed_json.get('content')
        else:
            parsed_json['twiccOriginalContent'] = parsed_json.get('message', {}).get('content')

        # Rewrite as a standard assistant message.
        parsed_json['type'] = 'assistant'
        parsed_json.pop('subtype', None)
        parsed_json['message'] = {
            'role': 'assistant',
            'content': [{'type': 'text', 'text': text}],
        }

        return orjson.dumps(parsed_json).decode('utf-8')

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        # NOTE: any change to this classification MUST bump
        # CLAUDE_CODE_COMPUTE_VERSION so existing sessions are recomputed.

        # "Bastard" API error format: type="assistant" but isApiErrorMessage=true.
        # The error text is serialised in content[0].text as a raw string.
        if parsed_json.get('isApiErrorMessage'):
            return ItemKind.API_ERROR

        entry_type = parsed_json.get('type')

        # Top-level system-ish lines that the CLI sprinkles in (queue ops,
        # progress events, summaries, file snapshots, custom-title, etc.).
        # agent-name / agent-color are written by interactive CLI sessions
        # (hybrid mode brought them into TwiCC-watched files).
        # The "latch" family (atis-latch, isolation-latch, ...) restores
        # conversation state on --resume / fork; atis carries the opaque
        # experiment token the CLI sends as the `x-cc-atis` request header.
        # This list is deliberately NOT versioned: adding a type here without
        # bumping CLAUDE_CODE_COMPUTE_VERSION leaves already-stored lines as
        # COLLAPSIBLE and only fixes newly computed ones, which is enough for
        # noise this rare.
        if entry_type in (
            'queue-operation', 'progress', 'summary', 'file-history-snapshot',
            'last-prompt', 'attachment', 'permission-mode', 'custom-title',
            'pr-link', 'mode', 'ai-title', 'agent-name', 'agent-color',
            'agent-setting', 'tag', 'ended-by-model', 'relocated',
            'atis-latch', 'isolation-latch', 'worktree-state',
            'content-replacement', 'history-suppression',
            'attribution-snapshot', 'file-history-delta', 'frame-link',
            'artifact-comment-monitor', 'artifact-autoreact-ledger',
            'bridge-session', 'observer-ref', 'fork-context-ref',
            'marble-origami-commit', 'marble-origami-snapshot',
            'marble-origami-reset',
        ):
            return ItemKind.SYSTEM

        if entry_type == 'system':
            if parsed_json.get('subtype') == 'api_error':
                return ItemKind.API_ERROR
            return ItemKind.SYSTEM

        if entry_type == 'user':
            # Compact summary: user message with isCompactSummary flag (context compaction).
            if parsed_json.get('isCompactSummary'):
                return ItemKind.COMPACT_SUMMARY

            content = get_message_content(parsed_json)
            text = extract_text_from_content(content)

            # Slash commands surface as user messages, except settings/control
            # ones (/clear, /model, /effort, /fast) which are system noise.
            if text is not None and (command := extract_command(text)):
                if command.name in _SYSTEM_SLASH_COMMANDS:
                    return ItemKind.SYSTEM
                return ItemKind.USER_MESSAGE

            # Meta messages aren't user messages.
            if parsed_json.get('isMeta'):
                return ItemKind.SYSTEM

            # System XML messages (commands, outputs) are SYSTEM.
            if _is_system_xml_content(content):
                return ItemKind.SYSTEM

            # Tool results bundled with text (e.g. "Tool loaded.") are CONTENT_ITEMS.
            if isinstance(content, list) and any(
                isinstance(item, dict) and item.get('type') == 'tool_result'
                for item in content
            ):
                return ItemKind.CONTENT_ITEMS

            # Turn-abort breadcrumbs ("[Request interrupted by user]", "... for
            # tool use]") are CLI bookkeeping, not real user prompts.
            if text is not None and is_interruption_marker(text):
                return ItemKind.SYSTEM

            # CLI-injected task notifications that no rewrite matched (orphan
            # <task-notification> whose sidecar is missing, a subagent's own
            # "[SYSTEM NOTIFICATION - NOT USER INPUT]" wake-up) are system
            # noise, not something the human typed. Rewritten notifications
            # never reach this check: their content is a tool_result list,
            # classified CONTENT_ITEMS above.
            origin = parsed_json.get('origin')
            if isinstance(origin, dict) and origin.get('kind') == 'task-notification':
                return ItemKind.SYSTEM

            # Only user messages with visible content count as USER_MESSAGE.
            if text or _has_visible_content(content):
                return ItemKind.USER_MESSAGE

            # Content array without visible items -> CONTENT_ITEMS.
            if isinstance(content, list):
                return ItemKind.CONTENT_ITEMS

            return None

        if entry_type == 'assistant':
            content = get_message_content(parsed_json)

            # "No response requested." is a system-level message, not a real
            # assistant response.
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get('type') == 'text'
                and content[0].get('text') == 'No response requested.'
            ):
                return ItemKind.SYSTEM

            if _has_visible_content(content):
                return ItemKind.ASSISTANT_MESSAGE

            if isinstance(content, list):
                return ItemKind.CONTENT_ITEMS

            return None

        return None

    # compute_item_display_level + compute_item_metadata: inherited from base
    # (base implementation calls self.is_tool_result_item / self.compute_item_kind).

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        timestamp_str = parsed_json.get("timestamp")
        if timestamp_str:
            return parse_timestamp_to_datetime(timestamp_str)
        return None

    # extract_title_from_user_message: inherited from base
    # (base assembles raw text + format_command_for_title + truncation).

    def format_command_for_title(self, text: str) -> str | None:
        # Claude Code embeds slash commands as <command-name>/<command-args>
        # XML in the user message text; format them as "name [args]".
        command = extract_command(text)
        if command is None:
            return None
        formatted = command.name
        if command.args:
            formatted += f' {strip_markdown(command.args)}'
        return formatted

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        # Claude Code carries cwd / gitBranch at the JSONL root, model
        # inside message.model, and slug at the JSONL root.
        fields: dict = {
            'cwd': None,
            'cwd_git_branch': None,
            'model': None,
            'slug': None,
        }
        if cwd := parsed_json.get('cwd'):
            fields['cwd'] = cwd
        if branch := parsed_json.get('gitBranch'):
            fields['cwd_git_branch'] = branch
        if (message := parsed_json.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                fields['model'] = model
        if slug := parsed_json.get('slug'):
            fields['slug'] = slug
        return fields

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],
        current_model: str | None,  # noqa: ARG002 (model lives on the line itself)
    ) -> None:
        message = parsed_json.get("message", {})
        if not isinstance(message, dict):
            return
        usage = message.get("usage")
        if not usage:
            return

        # Skip entries whose ``usage`` payload sums to zero tokens. The CLI
        # writes these for SDK-emitted ``<synthetic>`` messages (closes
        # orphan assistant turns on ``--resume`` after a kill),
        # ``No response requested.`` markers, and ``api_error`` retries.
        # They don't reflect real context usage; treating them as
        # ``context_usage = 0`` would clobber the session's last real
        # value through the watcher's "last non-null context_usage wins"
        # rule and make the header ring briefly drop to 0% until the
        # next real assistant message lands.
        token_usage = to_token_usage(usage)
        context_usage = calculate_line_context_usage(token_usage)
        if context_usage == 0:
            return

        # Extract and store message_id for deduplication tracking
        msg_id = message.get("id")
        if msg_id:
            item.message_id = msg_id

        item.context_usage = context_usage

        # Cost: only computed if message_id not already seen (deduplication;
        # Claude Code writes multiple JSONL lines per API call when streaming).
        if msg_id and msg_id not in seen_message_ids:
            seen_message_ids.add(msg_id)
            model_info = extract_model_info(message.get("model", ""))
            if model_info:
                model_id = f"anthropic/claude-{model_info.family}-{model_info.version}"
                if (timestamp_str := parsed_json.get("timestamp")) and (
                    dt := parse_timestamp_to_datetime(timestamp_str)
                ):
                    from twicc.providers.helpers import get_provider_helpers
                    item.cost = get_provider_helpers(Provider.CLAUDE_CODE).calculate_line_cost(
                        token_usage, model_id, dt.date(),
                    )

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return False
        return any(
            isinstance(item, dict) and item.get('type') == 'tool_result'
            for item in content
        )

    def extract_tool_use_entries(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
    ) -> dict[str, str]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return {}
        return {
            item['id']: item.get('name', '')
            for item in content
            if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('id')
        }

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
        tool_use_map: dict | None = None,  # noqa: ARG002
    ) -> ToolResultInfo | None:
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return None
        # Find the first tool_result entry (may be bundled with text blocks).
        tool_result = next(
            (item for item in content if isinstance(item, dict) and item.get('type') == 'tool_result'),
            None,
        )
        if tool_result is None:
            return None
        tool_use_id = tool_result.get('tool_use_id')
        if not tool_use_id:
            return None

        error_text: str | None = None
        if tool_result.get('is_error'):
            error_content = tool_result.get('content', '')
            if isinstance(error_content, str):
                stripped = error_content.strip()
                if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                    error_text = (
                        stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip()
                        or 'Unknown error'
                    )
                elif stripped.startswith('Exit code '):
                    error_text = stripped.split('\n', 1)[0]
                else:
                    error_text = stripped or 'Unknown error'
            else:
                error_text = 'Unknown error'

        return ToolResultInfo(
            tool_use_id=tool_use_id,
            is_error=error_text is not None,
            error_text=error_text,
        )

    def iter_tool_result_image_refs(self, parsed_json):
        # Claude Code's user-message tool_results carry images in
        # ``content[].source = {type: "base64", media_type, data}``.
        # See :func:`twicc.providers.compute_base.is_base64_image` for the
        # shape, which mirrors the front-end's ``detectContentBlockSource``.
        # Inner content blocks are walked in REVERSE document order so
        # that, in a multi-image tool_result (e.g. an MCP browser_batch
        # capturing two screenshots in one call), the chronologically
        # later image is yielded first — matching the "offset=0 = most
        # recent" contract documented on the base hook.
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_result':
                continue
            tool_use_id = block.get('tool_use_id') or ''
            inner = block.get('content')
            if not isinstance(inner, list):
                continue
            for entry in reversed(inner):
                if not isinstance(entry, dict) or entry.get('type') != 'image':
                    continue
                detected = is_base64_image(entry.get('source'))
                if detected is None:
                    continue
                media_type, data = detected
                yield (tool_use_id, media_type, data)

    def image_candidate_queryset(self, session_id, before_line_num):
        # ``'"media_type":"image/'`` is the smallest invariant present in
        # every Claude Code tool_result carrying a base64 image (the
        # source dict's ``media_type`` always starts with ``image/``).
        # Cheap LIKE pre-filter; iter_tool_result_image_refs handles the
        # final shape check.
        return SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=before_line_num,
            content__contains='"media_type":"image/',
        ).order_by('-line_num')

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str, bool] | None:
        # Need both a tool_result block in the content (for tool_use_id) and
        # an agentId in the root-level toolUseResult. ``isAsync`` is set by
        # the async launch ack (async-by-default CLIs no longer put a
        # ``run_in_background`` flag in the tool_use input, so this ack is
        # the only reliable backgroundness signal) and re-set by our own
        # task-notification rewrite.
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return None
        tool_result = next(
            (item for item in content if isinstance(item, dict) and item.get('type') == 'tool_result'),
            None,
        )
        if tool_result is None:
            return None
        tool_use_id = tool_result.get('tool_use_id')
        if not tool_use_id:
            return None
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None
        agent_id = tool_use_result.get('agentId')
        if not agent_id:
            return None
        return tool_use_id, agent_id, bool(tool_use_result.get('isAsync'))

    def extract_workflow_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str] | None:
        # Mirror of extract_agent_info_from_tool_result, with runId instead of
        # agentId: a Workflow tool_result carries the launching tool_use_id in
        # its tool_result block and the run id in root-level
        # toolUseResult.runId (only for local runs; remote ones have no runId).
        content = get_message_content_list(parsed_json, "user")
        if content is None:
            return None
        tool_result = next(
            (item for item in content if isinstance(item, dict) and item.get('type') == 'tool_result'),
            None,
        )
        if tool_result is None:
            return None
        tool_use_id = tool_result.get('tool_use_id')
        if not tool_use_id:
            return None
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None
        run_id = tool_use_result.get('runId')
        if not run_id:
            return None
        return tool_use_id, run_id

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        results: list[tuple[str, bool]] = []
        for item in content:
            if (
                isinstance(item, dict)
                and item.get('type') == 'tool_use'
                and item.get('name') in AGENT_TOOL_NAMES
                and item.get('id')
            ):
                inputs = item.get('input')
                is_background = bool(isinstance(inputs, dict) and inputs.get('run_in_background'))
                results.append((item['id'], is_background))
        return results

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        results: list[tuple[str, str, bool]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get('type') != 'tool_use' or item.get('name') not in AGENT_TOOL_NAMES:
                continue
            tu_id = item.get('id')
            inputs = item.get('input', {})
            if isinstance(inputs, dict) and tu_id:
                prompt = inputs.get('prompt')
                if isinstance(prompt, str):
                    is_background = bool(inputs.get('run_in_background'))
                    results.append((tu_id, prompt, is_background))
        return results

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        # Only Read / Edit / Write / Grep / Glob tool_uses contribute paths
        # (their input field name varies — see _TOOL_PATH_FIELDS).
        content = get_message_content_list(parsed_json, "assistant")
        if content is None:
            return []
        paths: list[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get('type') != 'tool_use':
                continue
            tool_name = item.get('name')
            if tool_name not in _TOOL_PATH_FIELDS:
                continue
            field_name = _TOOL_PATH_FIELDS[tool_name]
            inputs = item.get('input')
            if not isinstance(inputs, dict):
                continue
            path = inputs.get(field_name)
            if isinstance(path, str) and path.startswith('/'):
                paths.append(path)
        return paths

    def compute_link_extra(
        self,
        parsed_json: dict,
        tool_name: str,
        *,
        session_id: str | None = None,  # noqa: ARG002 — kept for signature compat
    ) -> str | None:
        """Return the JSON ``ToolResultLink.extra`` payload for this result.

        Claude Code emits structured ``extra`` for two tools: ``Edit`` /
        ``Write`` carry diff stats; ``Monitor``'s synthetic terminal row
        carries ``{"is_terminated": True}`` so the frontend spinner can
        flip to done. Every other tool returns ``None`` and the inherited
        machinery stores ``ToolResultLink.extra = NULL`` for that link.
        Source of truth is the JSONL ``toolUseResult`` block.

        ``session_id`` is part of the base signature for Codex's spinner
        logic and ignored here — Claude Code's JSONL ``toolUseResult.is_error``
        already covers the deny case, so the spinner has no equivalent
        side-channel to consult.

        Output JSON shape (``orjson.dumps`` of the dict):

        - ``Write`` create (empty ``structuredPatch``, full new file
          content carried under ``content``)::

              {"lines_added": <int>}
              # ``lines_removed`` omitted — there's nothing to remove.

        - ``Edit`` or ``Write`` update (non-empty ``structuredPatch``)::

              {
                  "lines_added":   <int>,    # always present
                  "lines_removed": <int>,    # always present
                  # ``hunks`` only when more than one hunk was applied.
                  "hunks":         <int>,    # optional
              }

        - ``Monitor`` synthetic terminal (the ``attachment`` rewrite carrying
          ``parsed_json['twiccMonitorTerminal'] = True``)::

              {"is_terminated": True}

          Set by the closing chunk of the chain so ``isToolRunning`` on the
          frontend can stop the spinner without counting result rows (the
          Monitor stream emits a variable number of fragments).

        Counting rules: iterate ``structuredPatch[].lines`` and tally
        ``+`` / ``-`` prefixes; context lines (space prefix) and
        diff metadata lines are ignored.

        The frontend reads ``lines_added`` / ``lines_removed`` for the
        per-tool ``+N -M`` summary badge; ``hunks`` is informational
        and not consumed today.
        """
        if tool_name == MONITOR_TOOL_NAME:
            if parsed_json.get('twiccMonitorTerminal'):
                return orjson.dumps({'is_terminated': True}).decode()
            return None

        if tool_name not in ('Edit', 'Write'):
            return None
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None

        structured_patch = tool_use_result.get('structuredPatch')

        # Write creates: structuredPatch is empty, count lines from content.
        if isinstance(structured_patch, list) and not structured_patch:
            content = tool_use_result.get('content')
            if isinstance(content, str):
                lines_added = content.count('\n') + 1 if content else 0
                return orjson.dumps({'lines_added': lines_added}).decode()
            return None

        # Edit and Write updates: count +/- from structuredPatch hunks.
        if not isinstance(structured_patch, list) or not structured_patch:
            return None

        lines_added = 0
        lines_removed = 0
        for hunk in structured_patch:
            if not isinstance(hunk, dict):
                continue
            for line in hunk.get('lines', ()):
                if isinstance(line, str):
                    if line.startswith('+'):
                        lines_added += 1
                    elif line.startswith('-'):
                        lines_removed += 1

        stats: dict = {'lines_added': lines_added, 'lines_removed': lines_removed}
        if len(structured_patch) > 1:
            stats['hunks'] = len(structured_patch)

        return orjson.dumps(stats).decode()

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        # Prefix/suffix detection only matters for ALWAYS messages whose
        # content can mix visible blocks with thinking/tool_use blocks.
        if kind not in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
            return False, False
        content = get_message_content_list(parsed_json)
        if not content:
            return False, False
        first = content[0]
        last = content[-1]
        has_prefix = isinstance(first, dict) and first.get('type') not in VISIBLE_CONTENT_TYPES
        has_suffix = isinstance(last, dict) and last.get('type') not in VISIBLE_CONTENT_TYPES
        return has_prefix, has_suffix

    # resolve_git_for_item: inherited from base
    # (base walks self.extract_paths_from_tool_uses through resolve_git_from_path).

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        return extract_text_from_content(get_message_content(parsed_json))

    def agent_tool_candidates_query(self, parent_session_id: str):
        # Pre-filter on the textual marker of an agent-spawning tool_use to
        # avoid scanning every item of the parent session.
        return SessionItem.objects.filter(
            Q(content__contains='"name":"Task"') | Q(content__contains='"name":"Agent"'),
            session_id=parent_session_id,
        ).order_by('-line_num')

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        # Claude Code emits a `progress` line whose `data.hookEvent` is
        # `SessionStart` when the CLI re-attaches to a previously stored
        # session.
        if parsed_json.get('type') != 'progress':
            return False
        data = parsed_json.get('data')
        return isinstance(data, dict) and data.get('hookEvent') == 'SessionStart'

    def subagent_turn_boundary(self, parsed_json: dict) -> bool | None:
        """Map a Claude subagent's own lines to its running / idle state.

        Needed because the parent-side counting rule
        (:meth:`check_agent_naturally_stopped`) only knows how to say
        "stopped", never "working again" — and recent CLIs make background
        agents resumable: a finished agent re-wakes when its own background
        child completes, when the parent messages it, etc. Its file carries
        both boundaries:

        - an ``assistant`` line whose ``message.stop_reason`` is
          ``"end_turn"`` closes a turn (the CLI's parent-file
          ``<task-notification>`` consistently follows within ~1s) → idle;
        - a ``user`` line with *string* content and an ``origin.kind``
          (``"coordinator"`` = the parent's SendMessage, ``"task-notification"``
          = one of its own background children finishing) is a CLI-injected
          wake-up → working again. Regular tool_results (list content) and
          the initial task prompt (no ``origin``) are not boundaries.

        Only consulted on subagent files by the live path (see the base
        docstring) — batch recompute of imported sessions never calls it, so
        historical files keep the parent-side rule as their only source.
        """
        entry_type = parsed_json.get('type')
        message = parsed_json.get('message')
        if not isinstance(message, dict):
            return None
        if entry_type == 'assistant':
            if message.get('stop_reason') == 'end_turn':
                return True
            return None
        if entry_type == 'user':
            origin = parsed_json.get('origin')
            if (
                isinstance(message.get('content'), str)
                and isinstance(origin, dict)
                and origin.get('kind')
            ):
                return False
        return None

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        if parsed_json.get('type') != 'custom-title':
            return None
        custom_title = parsed_json.get('customTitle')
        if not isinstance(custom_title, str) or not custom_title:
            return None
        # When the line targets another session (the CLI dropped a custom
        # title entry into the wrong file), `sessionId` is set; otherwise
        # the directive applies to the current session — let the base
        # caller default to it.
        target = parsed_json.get('sessionId')
        return (target if isinstance(target, str) else '', custom_title)

    def transform_tool_result_with_cache(
        self, parsed_json: dict, session_id: str, line_num: int
    ) -> str | None:
        # Claude Code's PreToolUse hook captures file contents before
        # Edit/Write modifications; this splices them into the matching
        # tool_result so the front gets full-file diffs.
        tool_use_result = parsed_json.get('toolUseResult')
        if not isinstance(tool_use_result, dict):
            return None

        # Locate the tool_use_id from the first tool_result block.
        content = get_message_content_list(parsed_json, "user")
        tool_use_id = None
        if content is not None:
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'tool_result':
                    tool_use_id = item.get('tool_use_id')
                    break
        if not tool_use_id:
            return None

        # Always pop from the cache (consume the entry whether we use it or not).
        cached = pop_original_file(session_id, tool_use_id)
        if cached is None:
            return None

        # Already has originalFile — no injection needed.
        if tool_use_result.get('originalFile') is not None:
            return None

        tool_use_result['originalFile'] = cached
        logger.debug(
            "Injected cached originalFile into tool_result (session=%s, line=%d, tool_use_id=%s, size=%d)",
            session_id, line_num, tool_use_id, len(cached),
        )
        return orjson.dumps(parsed_json).decode('utf-8')

    def extract_subagent_marker(self, parsed_json: dict) -> str | None:
        agent_id = parsed_json.get('agentId')
        return agent_id if isinstance(agent_id, str) and agent_id else None

    def apply_session_title(self, target_session_id: str, title: str) -> bool:
        # Claude Code title persistence is gated by anti-stale-write
        # protection: the CLI may re-append the previous title from its
        # tail-scan after we updated it, and we must refuse those.
        from .titles import check_protected_title, rename_session_in_jsonl

        result = check_protected_title(target_session_id, title)
        if result.should_apply:
            Session.objects.filter(id=target_session_id).update(title=title)
            return True
        if result.correction:
            # CLI wrote a stale title — re-write the correct one.
            # This places the correct title at the end of the JSONL,
            # so the CLI's next tail-scan will absorb it.
            try:
                rename_session_in_jsonl(target_session_id, result.correction)
            except Exception:
                pass  # Will retry on next stale entry
        return False

    # ------------------------------------------------------------------
    # Live (watcher) machinery
    # ------------------------------------------------------------------

    # find_open_group_head + compute_item_metadata_live: inherited from base
    # (base implementation calls self.detect_prefix_suffix / self.resolve_git_for_item).
    # create_tool_result_link_live + check_agent_naturally_stopped +
    # create_agent_link_from_{tool_result,subagent,tool_use}: inherited from base
    # (the base algorithms call provider hooks for the parsing-only bits).

    # ------------------------------------------------------------------
    # Batch compute
    # ------------------------------------------------------------------

    def analyze_content(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility)
        tool_use_map: dict,  # noqa: ARG002
    ) -> ContentAnalysis:
        message = parsed_json.get('message')
        if not isinstance(message, dict):
            return _EMPTY_ANALYSIS

        content = message.get('content')
        entry_type = parsed_json.get('type')

        # --- String content (user messages can have string content) ---
        if isinstance(content, str):
            if not content:
                # Empty string: not visible, no text, not XML
                return _EMPTY_ANALYSIS
            # Non-empty string
            stripped_for_xml = content.lstrip()
            is_system_xml = any(stripped_for_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)
            return ContentAnalysis(
                has_visible_content=True,
                text_content=content.strip(),
                is_system_xml=is_system_xml,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=False,
                has_suffix=False,
                tool_result_agent_info=None,
            )

        # --- Not a list or empty list -> nothing to traverse ---
        if not isinstance(content, list) or not content:
            return _EMPTY_ANALYSIS

        # --- List content: single traversal ---

        # Prefix/suffix: check first and last items
        first_item = content[0]
        last_item = content[-1]
        has_prefix = isinstance(first_item, dict) and first_item.get('type') not in VISIBLE_CONTENT_TYPES
        has_suffix = isinstance(last_item, dict) and last_item.get('type') not in VISIBLE_CONTENT_TYPES

        # Common accumulators
        has_visible = False
        text_content: str | None = None

        if entry_type == 'assistant':
            # --- Assistant message: tool_use info + visibility ---
            tool_use_entries: dict[str, str] = {}
            task_tool_uses: list[tuple[str, bool]] = []
            file_paths: list[str] = []

            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('type')

                if item_type in VISIBLE_CONTENT_TYPES:
                    has_visible = True
                    # Extract text from first text block
                    if item_type == 'text' and text_content is None:
                        text_val = item.get('text')
                        if isinstance(text_val, str):
                            text_content = text_val.strip()

                elif item_type == 'tool_use':
                    tu_id = item.get('id')
                    tu_name = item.get('name', '')
                    if tu_id:
                        tool_use_entries[tu_id] = tu_name

                        # Task/Agent tool_uses
                        if tu_name in AGENT_TOOL_NAMES:
                            is_bg = bool(isinstance(item.get('input'), dict) and item['input'].get('run_in_background'))
                            task_tool_uses.append((tu_id, is_bg))

                        # File path extraction for git resolution
                        if tu_name in _TOOL_PATH_FIELDS:
                            field_name = _TOOL_PATH_FIELDS[tu_name]
                            inputs = item.get('input')
                            if isinstance(inputs, dict):
                                path = inputs.get(field_name)
                                if isinstance(path, str) and path.startswith('/'):
                                    file_paths.append(path)

            return ContentAnalysis(
                has_visible_content=has_visible,
                text_content=text_content,
                is_system_xml=False,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=tool_use_entries or _EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=task_tool_uses or _EMPTY_TASK_TOOL_USES,
                file_paths=file_paths or _EMPTY_FILE_PATHS,
                has_prefix=has_prefix,
                has_suffix=has_suffix,
                tool_result_agent_info=None,
            )

        if entry_type == 'user':
            # --- User message: tool_result info + visibility + text ---
            # Check for system XML in list content (single text entry starting with a system prefix)
            is_system_xml = False
            if len(content) == 1:
                only_item = content[0]
                if isinstance(only_item, dict) and only_item.get('type') == 'text':
                    text_val = only_item.get('text')
                    if isinstance(text_val, str):
                        stripped_xml = text_val.lstrip()
                        is_system_xml = any(stripped_xml.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)

            has_tool_result = False
            first_tool_result_id: str | None = None
            # Sentinel: ... means "first tool_result not found yet"
            first_tool_result_error: str | None | type(...) = ...

            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get('type')

                if item_type in VISIBLE_CONTENT_TYPES:
                    has_visible = True
                    # Extract text from first text block
                    if item_type == 'text' and text_content is None:
                        text_val = item.get('text')
                        if isinstance(text_val, str):
                            text_content = text_val.strip()

                elif item_type == 'tool_result':
                    if not has_tool_result:
                        # First tool_result: extract id and error
                        has_tool_result = True
                        first_tool_result_id = item.get('tool_use_id')

                        if not item.get('is_error'):
                            first_tool_result_error = None
                        else:
                            error_content = item.get('content', '')
                            if isinstance(error_content, str):
                                stripped = error_content.strip()
                                if stripped.startswith('<tool_use_error>') and stripped.endswith('</tool_use_error>'):
                                    first_tool_result_error = stripped[len('<tool_use_error>'):-len('</tool_use_error>')].strip() or 'Unknown error'
                                elif stripped.startswith('Exit code '):
                                    first_tool_result_error = stripped.split('\n', 1)[0]
                                else:
                                    first_tool_result_error = stripped or 'Unknown error'
                            else:
                                first_tool_result_error = 'Unknown error'

            # Resolve error sentinel
            tool_result_error = None if first_tool_result_error is ... else first_tool_result_error

            # Agent info: requires both tool_result_id and root-level
            # toolUseResult.agentId (isAsync flags an async launch ack — see
            # extract_agent_info_from_tool_result).
            agent_info = None
            if first_tool_result_id:
                tool_use_result = parsed_json.get('toolUseResult')
                if isinstance(tool_use_result, dict):
                    agent_id = tool_use_result.get('agentId')
                    if agent_id:
                        agent_info = (
                            first_tool_result_id,
                            agent_id,
                            bool(tool_use_result.get('isAsync')),
                        )

            return ContentAnalysis(
                has_visible_content=has_visible,
                text_content=text_content,
                is_system_xml=is_system_xml,
                has_tool_result=has_tool_result,
                tool_result_id=first_tool_result_id,
                tool_result_error=tool_result_error,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=has_prefix,
                has_suffix=has_suffix,
                tool_result_agent_info=agent_info,
            )

        # --- Other message types: just visibility + text + prefix/suffix ---
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            if item_type in VISIBLE_CONTENT_TYPES:
                has_visible = True
                if item_type == 'text' and text_content is None:
                    text_val = item.get('text')
                    if isinstance(text_val, str):
                        text_content = text_val.strip()

        return ContentAnalysis(
            has_visible_content=has_visible,
            text_content=text_content,
            is_system_xml=False,
            has_tool_result=False,
            tool_result_id=None,
            tool_result_error=None,
            tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
            task_tool_uses=_EMPTY_TASK_TOOL_USES,
            file_paths=_EMPTY_FILE_PATHS,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            tool_result_agent_info=None,
        )


    # compute_session_metadata + apply_session_complete: inherited from base
    # (the base orchestrates DB I/O and dispatches parsing through hooks).

    # ------------------------------------------------------------------
    # Watcher live sync
    # ------------------------------------------------------------------

    # sync_session_items_from_file: inherited from base
    # (the base orchestrates the file read, item creation, link wiring and
    # session-level updates; everything provider-specific is dispatched
    # through hooks declared above).


# =============================================================================
# Singleton accessor
# =============================================================================


_compute_instance: ClaudeCodeSessionCompute | None = None


def get_compute() -> ClaudeCodeSessionCompute:
    """
    Return the process-local :class:`ClaudeCodeSessionCompute` singleton.

    The class holds per-instance state (``_monitor_task_to_tool_use_id``
    and ``_session_task_states``); the singleton ensures the same instance
    is reused across all calls within the process, so the state persists
    naturally. Each multiprocessing worker gets its own instance because
    module globals are not shared across processes — that's exactly the
    behaviour we want for the batch worker.
    """
    global _compute_instance
    if _compute_instance is None:
        _compute_instance = ClaudeCodeSessionCompute()
    return _compute_instance
