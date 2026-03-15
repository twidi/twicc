"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import os
from decimal import Decimal

import orjson
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, NamedTuple

import xmltodict
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned
from django.db.models import Q

from twicc.core.enums import ItemDisplayLevel, ItemKind
from twicc.core.models import AgentLink, Project, Session, SessionItem, SessionType, ToolResultLink
from twicc.core.pricing import (
    calculate_line_cost,
    calculate_line_context_usage,
    extract_model_info,
)

class AgentLinkUpdate(NamedTuple):
    """Describes a new AgentLink creation to broadcast to the frontend."""
    parent_session_id: str
    agent_id: str
    tool_use_id: str
    is_background: bool
    started_at: datetime | None


class ToolResultUpdate(NamedTuple):
    """Describes a tool completion state change to broadcast to the frontend."""
    session_id: str
    tool_use_id: str
    result_count: int
    completed_at: datetime | None  # Timestamp of the latest tool_result
    extra: str | None = None  # Optional extra data (e.g. diff stats JSON for Edit tools)


class AgentStoppedUpdate(NamedTuple):
    """Describes a subagent session whose process has naturally finished."""
    agent_session_id: str
    stopped_at: datetime


# Tool names that spawn subagent sessions (Task is the legacy name, Agent is the new one)
AGENT_TOOL_NAMES = frozenset({'Task', 'Agent'})

# Tool names whose completion state is tracked via ToolResultUpdate.
# Also includes any tool whose name starts with 'mcp__' (MCP tools).
TRACKED_TOOL_NAMES = frozenset({'Bash', 'WebFetch', 'WebSearch', 'Computer', 'Edit'}) | AGENT_TOOL_NAMES


def is_tracked_tool(tool_name: str) -> bool:
    """Check if a tool's completion state should be tracked."""
    return tool_name in TRACKED_TOOL_NAMES or tool_name.startswith('mcp__')

# Content types considered user-visible (for display_level and kind computation)
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')

# XML prefixes for system messages
# These are user messages that should be treated as debug-only
_SYSTEM_XML_PREFIXES = (
    '<local-command-',
)

# Prefix for task notification XML (background agent results)
_TASK_NOTIFICATION_TAG = '<task-notification>'
_TASK_NOTIFICATION_CLOSE_TAG = '</task-notification>'

# Maximum length for extracted titles (before truncation)
TITLE_MAX_LENGTH = 200

logger = logging.getLogger(__name__)


# =============================================================================
# Project Directory Cache
# =============================================================================

# Module-level cache: project_id -> directory (can be None)
_project_directories: dict[str, str | None] = {}
# Module-level cache: project_id -> git_root (can be None)
_project_git_roots: dict[str, str | None] = {}


def load_project_directories() -> None:
    """
    Load all project directories into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_directories.clear()
    _project_directories.update(
        Project.objects.values_list('id', 'directory')
    )


def load_project_git_roots() -> None:
    """
    Load all project git_roots into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_git_roots.clear()
    _project_git_roots.update(
        Project.objects.values_list('id', 'git_root')
    )


def get_project_directory(project_id: str) -> str | None:
    """Get cached directory for a project."""
    return _project_directories.get(project_id)


def get_project_git_root(project_id: str) -> str | None:
    """Get cached git_root for a project."""
    return _project_git_roots.get(project_id)


def ensure_project_directory(project_id: str, cwd: str) -> None:
    """
    Ensure the project's directory is set and up-to-date.

    - If project not in cache: load from DB and cache
    - If directory differs from cwd: update DB and cache
    - If directory matches cwd: do nothing

    Args:
        project_id: The project ID
        cwd: The current working directory from a session line
    """
    # Load project into cache if not present
    if project_id not in _project_directories:
        try:
            directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
        except Project.DoesNotExist:
            # Project doesn't exist yet, skip (will be handled when project is created)
            return
        _project_directories[project_id] = directory

    # Check if update needed
    if _project_directories[project_id] == cwd:
        return

    # Update DB and cache, and set stale based on directory existence
    should_be_stale = not os.path.isdir(cwd)
    Project.objects.filter(id=project_id).update(directory=cwd, stale=should_be_stale)
    _project_directories[project_id] = cwd

    # Re-resolve git_root when directory changes
    ensure_project_git_root(project_id, cwd)


def ensure_project_git_root(project_id: str, directory: str | None = None) -> None:
    """
    Resolve and store git_root for a project.

    Called:
    - At sync_all (startup) for all projects with a directory
    - When project.directory changes (from ensure_project_directory)
    - When a session gets git info but project.git_root is still None

    Args:
        project_id: The project ID
        directory: The project directory to resolve from. If None, uses cached/DB value.
    """
    if directory is None:
        directory = _project_directories.get(project_id)
        if directory is None:
            try:
                directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
            except Project.DoesNotExist:
                return
        if not directory:
            return

    result = resolve_git_from_path(directory, use_cache=False)
    git_root = result[0] if result else None

    # Check if update needed
    if _project_git_roots.get(project_id) == git_root:
        return

    # Update DB and cache
    Project.objects.filter(id=project_id).update(git_root=git_root)
    _project_git_roots[project_id] = git_root


def update_project_total_cost(project_id: str) -> None:
    """
    Recalculate and save a project's total_cost.

    Uses Project.recalculate_total_cost() which sums total_cost from
    non-stale SESSION-type sessions.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    project.recalculate_total_cost()
    project.save(update_fields=["total_cost"])


def update_project_metadata(project_id: str) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    sessions = Session.objects.filter(
        project=project, type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0
    )
    project.sessions_count = sessions.count()
    max_mtime = sessions.order_by("-mtime").values_list("mtime", flat=True).first()
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])

    update_project_total_cost(project_id)


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

# Module-level cache for batch compute: directory path → (git_directory, git_branch) or None
_git_resolution_cache: dict[str, tuple[str, str] | None] = {}


def resolve_git_from_path(dir_path: str, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Walk up from dir_path to find a .git entry and resolve git directory and branch.

    Args:
        dir_path: An absolute directory path to start from
        use_cache: Whether to read from and write to the module-level resolution cache.
                   Set to False for live resolution where fresh results are needed.

    Returns:
        (git_directory, git_branch) tuple, or None if no .git found
    """
    traversed: list[str] = []
    current = dir_path

    while True:
        # Check cache for this directory
        if use_cache and current in _git_resolution_cache:
            result = _git_resolution_cache[current]
            # Cache all traversed intermediate paths
            for path in traversed:
                _git_resolution_cache[path] = result
            return result

        traversed.append(current)

        git_path = os.path.join(current, '.git')
        try:
            if os.path.isdir(git_path):
                # Main repo: .git is a directory
                branch = read_head_branch(os.path.join(git_path, 'HEAD'))
                result = (current, branch) if branch is not None else None
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

            elif os.path.isfile(git_path):
                # Worktree: .git is a file containing "gitdir: /path/to/.git/worktrees/name"
                result = _resolve_worktree_git(current, git_path)
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

        except OSError:
            # Permission error or other OS issue, skip this level
            pass

        # Move up one directory
        parent = os.path.dirname(current)
        if parent == current:
            # Reached filesystem root without finding .git
            if use_cache:
                for path in traversed:
                    _git_resolution_cache[path] = None
            return None
        current = parent


def _resolve_worktree_git(git_directory: str, git_file_path: str) -> tuple[str, str] | None:
    """
    Resolve git branch from a worktree's .git file.

    The .git file contains something like:
        gitdir: /home/user/project/.git/worktrees/worktree-name

    The HEAD file in that gitdir path contains the branch reference.

    Args:
        git_directory: The directory containing the .git file (the worktree root)
        git_file_path: Path to the .git file

    Returns:
        (git_directory, git_branch) tuple, or None on error
    """
    try:
        with open(git_file_path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return None

    if not content.startswith('gitdir: '):
        return None

    gitdir = content[len('gitdir: '):]
    head_path = os.path.join(gitdir, 'HEAD')
    branch = read_head_branch(head_path)
    if branch is None:
        return None
    return (git_directory, branch)


def read_head_branch(head_path: str) -> str | None:
    """
    Read a git HEAD file and extract the branch name or commit hash.

    HEAD contains either:
    - "ref: refs/heads/feature/upload" → branch = "feature/upload"
    - A raw commit hash (40 hex chars) → detached HEAD, return the hash

    Args:
        head_path: Path to the HEAD file

    Returns:
        Branch name or commit hash, or None on error
    """
    try:
        with open(head_path, 'r') as f:
            content = f.read().strip()
    except OSError:
        return None

    if content.startswith('ref: refs/heads/'):
        return content[len('ref: refs/heads/'):]
    elif content.startswith('ref: '):
        # Other ref (unlikely but handle it)
        return content[len('ref: '):]
    elif len(content) >= 7 and all(c in '0123456789abcdef' for c in content):
        # Detached HEAD: raw commit hash
        return content
    return None


def extract_paths_from_tool_uses(parsed_json: dict) -> list[str]:
    """
    Extract file/directory paths from tool_use blocks in an assistant message.

    Only considers Read, Edit, Write, Grep, and Glob tools.
    Only returns absolute paths (starting with /).

    Args:
        parsed_json: Parsed JSON content of an assistant message

    Returns:
        List of absolute paths found in tool_use inputs
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []

    paths = []
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


def resolve_git_for_item(parsed_json: dict, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Resolve git directory and branch for a session item.

    Extracts paths from tool_use blocks, resolves each to a git root,
    and returns the most common resolution.

    Args:
        parsed_json: Parsed JSON content of the item
        use_cache: Whether to use the module-level git resolution cache.
                   Set to False for live resolution where fresh results are needed.
                   Passed through to resolve_git_from_path.

    Returns:
        (git_directory, git_branch) tuple, or None if no paths or no git found
    """
    paths = extract_paths_from_tool_uses(parsed_json)
    if not paths:
        return None

    resolutions: list[tuple[str, str]] = []
    for path in paths:
        # Use the directory part of the path (for files)
        dir_path = os.path.dirname(path) if not os.path.isdir(path) else path
        result = resolve_git_from_path(dir_path, use_cache=use_cache)
        if result is not None:
            resolutions.append(result)

    if not resolutions:
        return None

    if len(resolutions) == 1:
        return resolutions[0]

    # Multiple resolutions: use the most frequent git_directory
    counter = Counter(r[0] for r in resolutions)
    most_common_dir = counter.most_common(1)[0][0]
    # Return the first resolution matching the most common directory
    for r in resolutions:
        if r[0] == most_common_dir:
            return r

    return resolutions[0]  # Fallback (shouldn't reach here)


# =============================================================================
# Title Extraction from User Messages
# =============================================================================


import re

# Regex patterns for stripping markdown
_MARKDOWN_PATTERNS = [
    (re.compile(r'^#{1,6}\s+', re.MULTILINE), ''),  # Headers: # ## ### etc.
    (re.compile(r'\*\*(.+?)\*\*'), r'\1'),  # Bold: **text**
    (re.compile(r'__(.+?)__'), r'\1'),  # Bold: __text__
    (re.compile(r'\*(.+?)\*'), r'\1'),  # Italic: *text*
    (re.compile(r'_(.+?)_'), r'\1'),  # Italic: _text_
    (re.compile(r'~~(.+?)~~'), r'\1'),  # Strikethrough: ~~text~~
    (re.compile(r'`(.+?)`'), r'\1'),  # Inline code: `text`
    (re.compile(r'^\s*[-*+]\s+', re.MULTILINE), ''),  # Unordered list markers
    (re.compile(r'^\s*\d+\.\s+', re.MULTILINE), ''),  # Ordered list markers
    (re.compile(r'^\s*>\s*', re.MULTILINE), ''),  # Blockquotes
    (re.compile(r'\[([^\]]+)\]\([^)]+\)'), r'\1'),  # Links: [text](url) -> text
]


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting from text."""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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


class ParsedCommand(NamedTuple):
    name: str
    message: str | None
    args: str | None


def extract_command(text: str) -> ParsedCommand | None:
    if not text.startswith("<command-"):
        return None
    xml_text = f"<root>{text}</root>"
    try:
        parsed = xmltodict.parse(xml_text)
    except Exception:
        return None
    if not (name := (root := parsed["root"]).get("command-name")):
        return None
    return ParsedCommand(
        name=name,
        message=root.get("command-message"),
        args=root.get("command-args"),
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


def transform_task_notification(parsed_json: dict) -> str | None:
    """
    Transform a task-notification user message into a synthetic tool_result format.

    Background agents deliver their results as user messages with XML content
    like ``<task-notification>...<tool-use-id>...</tool-use-id>...</task-notification>``
    instead of the normal tool_result content array format.

    This function detects such messages, parses the XML, and rewrites
    ``parsed_json`` **in place** so that downstream code sees a standard
    tool_result item (content list, toolUseResult with agentId, etc.).

    Args:
        parsed_json: The parsed JSONL line (mutated in place if transformed).

    Returns:
        The new serialised JSON string to store in DB if a transformation was
        performed, or ``None`` if the item was not a task-notification.
    """
    if parsed_json.get('type') != 'user':
        return None
    message = parsed_json.get('message')
    if not isinstance(message, dict):
        return None
    content = message.get('content')
    if not isinstance(content, str):
        return None
    stripped = content.lstrip()
    if not stripped.startswith(_TASK_NOTIFICATION_TAG):
        return None

    # Find the LAST closing tag to avoid issues if </task-notification> appears inside <result>
    close_idx = content.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
    if close_idx == -1:
        return None
    xml_str = content[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]

    try:
        notification = xmltodict.parse(xml_str)['task-notification']
        tool_use_id = notification.get('tool-use-id')
        task_id = notification.get('task-id')
        result_text = notification.get('result', '') or notification.get('summary', '')
    except Exception:
        # Fallback: xmltodict can fail when <result> contains unescaped XML-like text
        # (e.g. "<width>x<height>"). Extract fields manually.
        logger.info("xmltodict failed for task-notification, falling back to manual extraction")
        tool_use_id, task_id, result_text = _extract_task_notification_fields(xml_str)

    if not tool_use_id:
        return None

    # Preserve original content for debugging
    parsed_json['twiccOriginalContent'] = content

    # Rewrite message.content as a standard tool_result content array
    message['content'] = [{
        'type': 'tool_result',
        'tool_use_id': tool_use_id,
        'content': result_text,
    }]

    # Add toolUseResult with agentId so that get_tool_result_agent_info() works
    if task_id:
        parsed_json['toolUseResult'] = {'agentId': task_id}

    # Serialise and return the new content for DB storage
    return orjson.dumps(parsed_json).decode('utf-8')


# Regex to strip ANSI escape codes from local command output
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

# Local command output tags (stdout and stderr)
_LOCAL_COMMAND_TAGS = (
    ('<local-command-stdout>', '</local-command-stdout>'),
    ('<local-command-stderr>', '</local-command-stderr>'),
)

# Prefixes/suffixes that indicate a local command output should be filtered out (not displayed)
_LOCAL_COMMAND_FILTERED_PREFIXES = ('compacted',)
_LOCAL_COMMAND_FILTERED_SUFFIXES = ('dismissed', 'cancelled', 'no content')


def transform_local_command_output(parsed_json: dict) -> str | None:
    """
    Transform a local-command-stdout/stderr message into a synthetic assistant_message.

    Local command outputs appear in two formats in JSONL:
    1. ``type: "system", subtype: "local_command"`` with content containing
       ``<local-command-stdout>...</local-command-stdout>`` (or stderr variant)
    2. ``type: "user"`` with message.content (string or text block) containing
       ``<local-command-stdout>...</local-command-stdout>`` (or stderr variant)

    This function detects such messages, extracts the text from the XML tag,
    strips ANSI escape codes, and rewrites ``parsed_json`` **in place** so that
    downstream code sees a standard assistant_message item.

    Messages whose content is empty, starts with "compacted", or ends with
    "dismissed" or "cancelled" are filtered out (returns ``None``).

    Args:
        parsed_json: The parsed JSONL line (mutated in place if transformed).

    Returns:
        The new serialised JSON string to store in DB if a transformation was
        performed, or ``None`` if the item was not a local-command-stdout/stderr
        or was filtered out.
    """
    entry_type = parsed_json.get('type')
    raw_text = None

    # Format 1: type=system, subtype=local_command
    if entry_type == 'system' and parsed_json.get('subtype') == 'local_command':
        content = parsed_json.get('content', '')
        if isinstance(content, str):
            raw_text = _extract_local_command_text(content)

    # Format 2: type=user, message.content contains the tag
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

    # Strip ANSI escape codes and whitespace
    text = _ANSI_RE.sub('', raw_text).strip()

    # Filter out empty or non-interesting messages
    if not text:
        return None
    text_lower = text.lower()
    if any(text_lower.startswith(prefix) or text_lower.startswith("(" + prefix) for prefix in _LOCAL_COMMAND_FILTERED_PREFIXES):
        return None
    if any(text_lower.endswith(suffix) or text_lower.endswith(suffix + ")") for suffix in _LOCAL_COMMAND_FILTERED_SUFFIXES):
        return None

    # Preserve original content for debugging
    if entry_type == 'system':
        parsed_json['twiccOriginalContent'] = parsed_json.get('content')
    else:
        parsed_json['twiccOriginalContent'] = parsed_json.get('message', {}).get('content')

    # Rewrite as a standard assistant message
    parsed_json['type'] = 'assistant'
    parsed_json.pop('subtype', None)
    parsed_json['message'] = {
        'role': 'assistant',
        'content': [{'type': 'text', 'text': text}],
    }

    # Serialise and return the new content for DB storage
    return orjson.dumps(parsed_json).decode('utf-8')


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


def extract_title_from_user_message(parsed_json: dict) -> str | None:
    """
    Extract a title from a user message JSON.

    Extracts text content, strips markdown and whitespace,
    truncates to TITLE_MAX_LENGTH characters, and adds ellipsis if truncated.

    Args:
        parsed_json: Parsed JSON content of a user message item

    Returns:
        Cleaned title string, or None if no text content found
    """
    if (content := get_message_content(parsed_json)) is None:
        return None

    text = extract_text_from_content(content)
    if not text:
        return None

    if (command := extract_command(text)) is not None:
        # Use command name as title for command invocations
        cleaned = command.name
        if command.args:
            cleaned += f' {_strip_markdown(command.args)}'

    else:
        # Strip markdown and whitespace
        cleaned = _strip_markdown(text).strip()

    # Collapse multiple whitespace into single space
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if not cleaned:
        return None

    # Truncate if needed
    if len(cleaned) > TITLE_MAX_LENGTH:
        return cleaned[:TITLE_MAX_LENGTH] + '…'

    return cleaned


# =============================================================================
# Helper Functions for Prefix/Suffix Detection
# =============================================================================


def _has_collapsible_prefix(content: list) -> bool:
    """Check if content array starts with a collapsible element."""
    if not content:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get('type') not in VISIBLE_CONTENT_TYPES


def _has_collapsible_suffix(content: list) -> bool:
    """Check if content array ends with a collapsible element."""
    if not content:
        return False
    last = content[-1]
    return isinstance(last, dict) and last.get('type') not in VISIBLE_CONTENT_TYPES


def _detect_prefix_suffix(parsed_json: dict, kind: ItemKind | None) -> tuple[bool, bool]:
    """
    Detect if an ALWAYS item has collapsible prefix/suffix.

    Returns:
        (has_prefix, has_suffix) tuple
    """
    if kind not in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE):
        return False, False

    content = get_message_content_list(parsed_json)
    if not content:
        return False, False

    return _has_collapsible_prefix(content), _has_collapsible_suffix(content)


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


def get_tool_use_entries(parsed_json: dict) -> dict[str, str]:
    """
    Extract tool_use ID → name mapping from an assistant or content_items message.

    Returns a dict mapping tool_use_id to tool name (e.g. {"toolu_xxx": "Bash"}).
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return {}
    return {
        item['id']: item.get('name', '')
        for item in content
        if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('id')
    }


def is_bash_tool_use_background(parsed_json: dict, tool_use_id: str) -> bool:
    """
    Check if a Bash tool_use has run_in_background=true in its input.

    Args:
        parsed_json: The parsed JSON of the assistant message containing the tool_use.
        tool_use_id: The ID of the tool_use to check.

    Returns True if the tool_use has run_in_background=true, False otherwise.
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return False
    for item in content:
        if (isinstance(item, dict) and item.get('type') == 'tool_use'
                and item.get('id') == tool_use_id and item.get('name') == 'Bash'):
            inputs = item.get('input', {})
            return isinstance(inputs, dict) and bool(inputs.get('run_in_background'))
    return False


def get_tool_use_ids(parsed_json: dict) -> list[str]:
    """
    Extract tool_use IDs from an assistant or content_items message.

    Returns a list of tool_use IDs found in the message content array.
    """
    return list(get_tool_use_entries(parsed_json).keys())


def get_tool_result_id(parsed_json: dict) -> str | None:
    """
    Extract the tool_use_id from a tool_result item.

    Finds the first tool_result entry in the content array (may be bundled
    with other items like text).

    Returns the tool_use_id string, or None if no tool_result found.
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return None
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            return item.get('tool_use_id')
    return None


def is_tool_result_item(parsed_json: dict) -> bool:
    """
    Check if an item contains a tool_result.

    A tool_result item is a user message whose content array contains
    at least one entry of type "tool_result" (possibly bundled with
    other items like text).
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return False
    return any(isinstance(item, dict) and item.get('type') == 'tool_result' for item in content)


def compute_edit_diff_stats(parsed_json: dict) -> str | None:
    """
    Compute diff stats from an Edit tool_result's structuredPatch.

    Looks at the root-level ``toolUseResult`` key for a ``structuredPatch``
    list of unified-diff hunks.  Each hunk has a ``lines`` list where entries
    prefixed with ``"+"`` are additions and ``"-"`` are removals.

    Returns a JSON string like ``{"lines_added": 5, "lines_removed": 3}``
    (with an extra ``"hunks"`` key when there are multiple hunks), or *None*
    when the data is unavailable (error result, old format, non-Edit tool).
    """
    tool_use_result = parsed_json.get('toolUseResult')
    if not isinstance(tool_use_result, dict):
        return None

    structured_patch = tool_use_result.get('structuredPatch')
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


def get_task_tool_uses(parsed_json: dict) -> list[tuple[str, bool]]:
    """
    Extract tool_use IDs and background flag from agent tool calls in an assistant message.

    Returns a list of (tool_use_id, is_background) tuples for tool_use items
    where name is "Task" or "Agent".
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []
    results = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get('type') == 'tool_use'
            and item.get('name') in AGENT_TOOL_NAMES
            and item.get('id')
        ):
            is_background = bool(isinstance(item.get('input'), dict) and item['input'].get('run_in_background'))
            results.append((item['id'], is_background))
    return results


def get_tool_result_agent_info(parsed_json: dict) -> tuple[str, str] | None:
    """
    Extract (tool_use_id, agent_id) from a tool_result with agentId.

    Checks both the tool_result content and the root-level toolUseResult
    for the agent_id. Returns None if this is not a Task tool result with an agent.

    Args:
        parsed_json: Parsed JSONL line

    Returns:
        Tuple of (tool_use_id, agent_id) if found, None otherwise
    """
    # Must contain a tool_result item
    content = get_message_content_list(parsed_json, "user")
    if content is None:
        return None
    tool_result = None
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_result':
            tool_result = item
            break
    if tool_result is None:
        return None

    tool_use_id = tool_result.get('tool_use_id')
    if not tool_use_id:
        return None

    # Check for agentId in the root-level toolUseResult
    tool_use_result = parsed_json.get('toolUseResult')
    if not isinstance(tool_use_result, dict):
        return None

    agent_id = tool_use_result.get('agentId')
    if not agent_id:
        return None

    return tool_use_id, agent_id


def _is_system_xml_content(content: str | list | None) -> bool:
    """
    Check if content is a system XML message (command invocation or output).

    These are user messages containing only XML tags like:
    - <local-command-stdout>...</local-command-stdout> (command outputs)

    Args:
        content: Message content (string or list)

    Returns:
        True if the content is a system XML message
    """
    if isinstance(content, str):
        stripped = content.lstrip()
        return any(stripped.startswith(prefix) for prefix in _SYSTEM_XML_PREFIXES)
    return False


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


# =============================================================================
# Item Metadata Computation (display_level, kind)
# =============================================================================


def compute_item_display_level(parsed_json: dict, kind: ItemKind | None) -> int:
    """
    Determine the display level for an item based on its JSON content and kind.

    Classification rules:
    - ALWAYS (1): USER_MESSAGE, ASSISTANT_MESSAGE, API_ERROR kinds
    - COLLAPSIBLE (2): Meta messages, thinking/tool_use only,
                       summaries, file snapshots, custom titles
    - DEBUG_ONLY (3): SYSTEM kind, standalone tool_result items

    Args:
        parsed_json: Parsed JSON content of the item
        kind: The pre-computed ItemKind (or None)

    Returns:
        ItemDisplayLevel enum value (1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY)

    Note:
        Any modification to this function's logic MUST increment
        CURRENT_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    # These kinds are always visible
    if kind in (ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE, ItemKind.API_ERROR):
        return ItemDisplayLevel.ALWAYS

    # DEBUG_ONLY: SYSTEM kind (system messages, queue-operation, progress, XML commands)
    if kind == ItemKind.SYSTEM:
        return ItemDisplayLevel.DEBUG_ONLY

    # DEBUG_ONLY: Standalone tool_result items (their data is accessed via ToolResultLink)
    if is_tool_result_item(parsed_json):
        return ItemDisplayLevel.DEBUG_ONLY

    # Everything else is collapsible: meta messages, thinking/tool_use,
    # summaries, file snapshots, custom titles, etc.
    return ItemDisplayLevel.COLLAPSIBLE


def compute_item_kind(parsed_json: dict) -> ItemKind | None:
    """
    Determine the kind/category of an item based on its JSON content.

    Classification rules:
    - USER_MESSAGE: User messages with visible content (text, document, image), not meta
    - ASSISTANT_MESSAGE: Assistant messages with visible content (text, document, image)
    - API_ERROR: System messages with subtype 'api_error', or messages with isApiErrorMessage=true
    - SYSTEM: System messages (except api_error), queue-operation, progress, summary, file-history-snapshot, last-prompt
    - CUSTOM_TITLE: Items of type 'custom-title' (session title set by Claude)

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        ItemKind enum value, or None if not a recognized kind

    Note:
        Any modification to this function's logic MUST increment
        CURRENT_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    # "Bastard" API error format: type="assistant" but isApiErrorMessage=true
    # The error text is serialized in content[0].text as a raw string
    if parsed_json.get('isApiErrorMessage'):
        return ItemKind.API_ERROR

    entry_type = parsed_json.get('type')

    # Custom title (session title set by Claude)
    if entry_type == 'custom-title':
        return ItemKind.CUSTOM_TITLE

    # System types: system (except api_error), queue-operation, progress, etc.
    if entry_type in ('queue-operation', 'progress', 'summary', 'file-history-snapshot', 'last-prompt'):
        return ItemKind.SYSTEM

    if entry_type == 'system':
        subtype = parsed_json.get('subtype')
        if subtype == 'api_error':
            return ItemKind.API_ERROR
        return ItemKind.SYSTEM

    # User messages
    if entry_type == 'user':

        content = get_message_content(parsed_json)
        text = extract_text_from_content(content)

        # Commands are shown as user messages (except /clear which is system)
        if text is not None and (command := extract_command(text)):
            if command.name == '/clear':
                return ItemKind.SYSTEM
            return ItemKind.USER_MESSAGE

        # Meta messages are not user messages
        if parsed_json.get('isMeta'):
            return ItemKind.SYSTEM

        # System XML messages (commands, outputs) are SYSTEM kind
        if _is_system_xml_content(content):
            return ItemKind.SYSTEM

        # Tool results bundled with text (e.g. "Tool loaded.") are CONTENT_ITEMS, not user messages
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get('type') == 'tool_result' for item in content
        ):
            return ItemKind.CONTENT_ITEMS

        # Only user messages with visible content count as USER_MESSAGE
        if text or _has_visible_content(content):
            return ItemKind.USER_MESSAGE

        # Content array without visible items → CONTENT_ITEMS
        if isinstance(content, list):
            return ItemKind.CONTENT_ITEMS

        return None

    # Assistant messages
    if entry_type == 'assistant':
        content = get_message_content(parsed_json)

        # Only assistant messages with visible content count as ASSISTANT_MESSAGE
        if _has_visible_content(content):
            return ItemKind.ASSISTANT_MESSAGE

        # Content array without visible items → CONTENT_ITEMS
        if isinstance(content, list):
            return ItemKind.CONTENT_ITEMS

        return None

    return None


def compute_item_metadata(parsed_json: dict) -> dict:
    """
    Compute all metadata fields for a single item.

    Kind is computed first, then used to determine display_level.

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        Dict with computed metadata fields:
        - display_level: int (ItemDisplayLevel enum value)
        - kind: str | None (item category)
    """
    kind = compute_item_kind(parsed_json)
    return {
        'display_level': compute_item_display_level(parsed_json, kind),
        'kind': kind,
    }


# =============================================================================
# Group State Machine
# =============================================================================


class ItemGroupInfo(NamedTuple):
    """Group assignment for a single item."""
    group_head: int | None
    group_tail: int | None
    closed_items: list[Any] = []  # Items whose group was just closed


class GroupState:
    """
    Tracks group state during sequential item processing.

    A group is "open" when:
    - Previous item was COLLAPSIBLE, or
    - Previous ALWAYS item had a suffix (potential group start)

    Usage:
        state = GroupState()
        for item in items:
            info = state.process_item(item.line_num, display_level, has_prefix, has_suffix)
            item.group_head = info.group_head
            item.group_tail = info.group_tail
        state.finalize()  # Close any pending group
    """

    def __init__(self) -> None:
        # Current open group (COLLAPSIBLE items accumulating)
        self._group_head: int | None = None
        self._group_items: list[tuple[int, Any]] = []  # (line_num, item_ref)

        # Pending ALWAYS with suffix (might start a group)
        self._pending_suffix: tuple[int, Any] | None = None  # (line_num, item_ref)

    def has_open_group(self) -> bool:
        """Check if there's an open group that the next item could join."""
        return self._group_head is not None or self._pending_suffix is not None

    def get_current_head(self) -> int | None:
        """Get the head of the current open group."""
        if self._group_head is not None:
            return self._group_head
        if self._pending_suffix is not None:
            return self._pending_suffix[0]
        return None

    def process_item(
        self,
        line_num: int,
        display_level: ItemDisplayLevel,
        has_prefix: bool,
        has_suffix: bool,
        item_ref: Any = None,
    ) -> ItemGroupInfo:
        """
        Process a single item and return its group assignment.

        Args:
            line_num: The item's line number
            display_level: ALWAYS, COLLAPSIBLE, or DEBUG_ONLY
            has_prefix: True if ALWAYS item has collapsible prefix
            has_suffix: True if ALWAYS item has collapsible suffix
            item_ref: Reference to item object (for batch updates)

        Returns:
            ItemGroupInfo with group_head and group_tail assignments
        """
        if display_level == ItemDisplayLevel.DEBUG_ONLY:
            # DEBUG_ONLY: transparent to groups, no participation
            return ItemGroupInfo(group_head=None, group_tail=None)

        if display_level == ItemDisplayLevel.COLLAPSIBLE:
            return self._process_collapsible(line_num, item_ref)

        # ALWAYS
        return self._process_always(line_num, has_prefix, has_suffix, item_ref)

    def _process_collapsible(self, line_num: int, item_ref: Any) -> ItemGroupInfo:
        """Process a COLLAPSIBLE item."""
        # Check if we're connecting to a pending ALWAYS suffix
        if self._pending_suffix is not None:
            suffix_line, suffix_ref = self._pending_suffix
            self._pending_suffix = None

            # The ALWAYS suffix starts this group
            self._group_head = suffix_line
            self._group_items = [(suffix_line, suffix_ref), (line_num, item_ref)]
            return ItemGroupInfo(group_head=suffix_line, group_tail=None)

        # Join existing group or start new one
        if self._group_head is not None:
            # Continue existing group
            self._group_items.append((line_num, item_ref))
            return ItemGroupInfo(group_head=self._group_head, group_tail=None)
        else:
            # Start new group
            self._group_head = line_num
            self._group_items = [(line_num, item_ref)]
            return ItemGroupInfo(group_head=line_num, group_tail=None)

    def _process_always(
        self, line_num: int, has_prefix: bool, has_suffix: bool, item_ref: Any
    ) -> ItemGroupInfo:
        """Process an ALWAYS item."""
        result_head: int | None = None
        closed_items: list[Any] = []
        joined_via_prefix = False

        # Handle prefix: can join an open group
        if has_prefix and self.has_open_group():
            result_head = self.get_current_head()
            joined_via_prefix = True

            # Add to group items for tail update (but track that this is the joining ALWAYS)
            if self._pending_suffix is not None:
                # Connect pending suffix to this prefix
                suffix_line, suffix_ref = self._pending_suffix
                self._group_items = [(suffix_line, suffix_ref)]
                self._group_head = suffix_line
                self._pending_suffix = None
            # Don't add the current ALWAYS to _group_items - it joins but doesn't get group_tail

        # ALWAYS always terminates any group before it
        if self._group_items:
            # Determine tail: this item if it joined via prefix, else last item in group
            if joined_via_prefix:
                tail = line_num
            else:
                tail = self._group_items[-1][0]

            # Update all items in the group (not including current ALWAYS)
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    closed_items.append(ref)

            # Reset group state
            self._group_items = []
            self._group_head = None

        # Also close pending suffix if not joined by this item's prefix
        if self._pending_suffix is not None and not joined_via_prefix:
            # Pending suffix was not connected, close it as orphan
            suffix_line, suffix_ref = self._pending_suffix
            if suffix_ref is not None:
                # Suffix stays orphan (group_tail already None)
                closed_items.append(suffix_ref)
            self._pending_suffix = None

        # Handle suffix: might start a new group
        if has_suffix:
            self._pending_suffix = (line_num, item_ref)

        # ALWAYS item itself doesn't get group_tail from this operation
        # group_tail for ALWAYS is only set when its suffix connects to something later
        return ItemGroupInfo(group_head=result_head, group_tail=None, closed_items=closed_items)

    def finalize(self) -> list[Any]:
        """
        Finalize any open groups at end of processing.

        Returns:
            List of item references that were updated (for batch save)
        """
        updated = []

        # Close any open COLLAPSIBLE group
        if self._group_items:
            tail = self._group_items[-1][0]
            for _, ref in self._group_items:
                if ref is not None:
                    ref.group_tail = tail
                    updated.append(ref)
            self._group_items = []
            self._group_head = None

        # Pending ALWAYS suffix stays orphan (group_tail = None)
        if self._pending_suffix is not None:
            _, ref = self._pending_suffix
            if ref is not None:
                updated.append(ref)
            self._pending_suffix = None

        return updated


# =============================================================================
# Timestamp Parsing
# =============================================================================


def parse_timestamp_to_datetime(timestamp: str) -> datetime | None:
    """
    Parse an ISO timestamp string to a datetime object (UTC aware).

    Args:
        timestamp: ISO format timestamp (e.g., "2026-01-22T10:53:42.927Z")

    Returns:
        datetime object (UTC aware), or None if parsing fails.
    """
    if not timestamp:
        return None

    try:
        # Handle 'Z' suffix by converting to +00:00
        if timestamp.endswith("Z"):
            timestamp = timestamp[:-1] + "+00:00"
        return datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None


def extract_item_timestamp(parsed_json: dict) -> datetime | None:
    """
    Extract timestamp from parsed JSON.

    The timestamp is always present at the root level of JSONL lines.

    Args:
        parsed_json: The parsed JSON content of the item

    Returns:
        datetime object (UTC aware), or None if not found or parsing fails.
    """
    timestamp_str = parsed_json.get("timestamp")
    if timestamp_str:
        return parse_timestamp_to_datetime(timestamp_str)
    return None


# =============================================================================
# Cost and Context Usage Computation
# =============================================================================


def compute_item_cost_and_usage(
    item: SessionItem,
    parsed_json: dict,
    seen_message_ids: set[str],
) -> None:
    """
    Compute and assign cost, context_usage, and message_id on a SessionItem.

    This function handles deduplication: cost is only assigned if the message_id
    has not been seen before (Claude Code writes multiple JSONL lines for a single
    API call with the same message.id due to streaming).

    Modifies the item in place. Also modifies the seen_message_ids set.

    Args:
        item: The SessionItem to update (must have content already set)
        parsed_json: The parsed JSON content of the item
        seen_message_ids: Set of already-seen message IDs for deduplication
    """
    message = parsed_json.get("message", {})
    if not isinstance(message, dict):
        return
    usage = message.get("usage")

    if not usage:
        return

    # Extract and store message_id for deduplication tracking
    msg_id = message.get("id")
    if msg_id:
        item.message_id = msg_id

    # Context usage: always computed when usage data is present
    item.context_usage = calculate_line_context_usage(usage)

    # Cost: only computed if message_id not already seen (deduplication)
    if msg_id and msg_id not in seen_message_ids:
        seen_message_ids.add(msg_id)

        model_info = extract_model_info(message.get("model", ""))
        if model_info:
            model_id = f"anthropic/claude-{model_info.family}-{model_info.version}"
            if timestamp_str := parsed_json.get("timestamp"):
                if dt := parse_timestamp_to_datetime(timestamp_str):
                    item.cost = calculate_line_cost(usage, model_id, dt.date())


# =============================================================================
# Batch Processing: compute_session_metadata
# =============================================================================


def compute_session_metadata(session_id: str, result_queue) -> None:
    """
    Compute metadata for all items in a session.

    Sends all results via result_queue as a single message per session.
    Does NOT write to the database directly.
    The caller is responsible for consuming the queue and applying changes.

    In production, the main process consumes the queue and performs all DB writes,
    eliminating "database is locked" errors.

    For tests, pass a queue.Queue and either:
    - Inspect the queue contents directly
    - Call apply_compute_results() to apply changes to DB

    Args:
        session_id: The session ID
        result_queue: Queue to send results (multiprocessing.Queue or queue.Queue)
    """
    from decimal import Decimal
    from django.db import connection
    from django.db.models import Sum
    from twicc.core.models import Session

    # Ensure this process/thread has its own database connection
    connection.close()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found for metadata computation")
        result_queue.put(orjson.dumps({
            'type': 'error',
            'session_id': session_id,
            'error': 'Session not found',
        }))
        return

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    state = GroupState()
    items_to_update: list[SessionItem] = []
    tool_result_links_to_create: list[dict] = []
    agent_links_to_create: list[dict] = []
    all_item_updates: list[dict] = []  # Accumulate all item updates
    all_tool_result_links: list[dict] = []  # Accumulate all tool_result links
    all_agent_links: list[dict] = []  # Accumulate all agent links
    content_overrides: list[dict] = []  # Only items whose content was transformed
    batch_size = 50

    # Helper to serialize items
    def serialize_items(items: list[SessionItem]) -> list[dict]:
        return [
            {
                'id': item.id,
                'display_level': item.display_level,
                'group_head': item.group_head,
                'group_tail': item.group_tail,
                'kind': item.kind,
                'message_id': item.message_id,
                'cost': str(item.cost) if item.cost is not None else None,
                'context_usage': item.context_usage,
                'timestamp': item.timestamp.isoformat() if item.timestamp else None,
                'git_directory': item.git_directory,
                'git_branch': item.git_branch,
            }
            for item in items
        ]

    # Helper to flush item batch to accumulator
    def flush_items(items: list[SessionItem]) -> None:
        if items:
            all_item_updates.extend(serialize_items(items))

    # Helper to flush link batches to accumulators
    def flush_tool_result_links(links: list[dict]) -> None:
        if links:
            all_tool_result_links.extend(links)

    def flush_agent_links(links: list[dict]) -> None:
        if links:
            all_agent_links.extend(links)

    # Map tool_use_id → (line_num, tool_name) of the item containing the tool_use
    tool_use_map: dict[str, tuple[int, str]] = {}
    # Map tool_use_id → line_num for Task tool_uses (to link to agents)
    task_tool_use_map: dict[str, tuple[int, bool, datetime]] = {}

    # Track if we've set the initial title from first user message
    initial_title_set = False
    # Track sessions that need title updates (session_id -> title)
    session_titles: dict[str, str] = {}

    # Track user message count (message turns)
    user_message_count = 0

    # Track affected days for activity recalculation
    affected_days: set[str] = set()  # ISO date strings

    # Track cost and context usage (deduplication by message_id)
    seen_message_ids: set[str] = set()
    last_context_usage: int | None = None

    # Track first timestamp (for session.created_at)
    first_timestamp: datetime | None = None

    # Track lifecycle timestamps
    last_started_at: datetime | None = None  # Will be set to first_timestamp, then updated by SessionStart hookEvents
    last_updated_at: datetime | None = None  # Last item timestamp seen

    # Track runtime environment fields (last seen values)
    first_cwd: str | None = None  # First cwd = project directory
    last_cwd: str | None = None
    last_cwd_git_branch: str | None = None
    last_model: str | None = None

    # Track resolved git directory/branch (from tool_use paths)
    last_resolved_git_directory: str | None = None
    last_resolved_git_branch: str | None = None

    # Track agent completion: tool_use_id -> (result_count, last_timestamp)
    agent_tool_result_counts: dict[str, tuple[int, datetime | None]] = {}
    # Track agent stopped updates to include in session_complete message
    agent_stopped_list: list[dict] = []  # [{agent_session_id, stopped_at}, ...]

    for item in queryset.iterator(chunk_size=batch_size):
        try:
            parsed = orjson.loads(item.content)
        except orjson.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        # Transform task-notification XML into standard tool_result format
        new_content = transform_task_notification(parsed)
        if new_content is None:
            # Transform local-command-stdout into assistant_message format
            new_content = transform_local_command_output(parsed)

        if new_content is not None:
            item.content = new_content
            content_overrides.append({'id': item.id, 'content': new_content})

        # Compute display_level and kind
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)
        if first_timestamp is None and item.timestamp is not None:
            first_timestamp = item.timestamp
            last_started_at = first_timestamp  # Initialize to created_at
            # Track session creation day for activity recalculation
            affected_days.add(first_timestamp.date().isoformat())

        # Track lifecycle timestamps
        if item.timestamp is not None:
            last_updated_at = item.timestamp
        # Detect SessionStart hookEvent to update last_started_at
        if (
            item.timestamp is not None
            and parsed.get('type') == 'progress'
            and isinstance(parsed.get('data'), dict)
            and parsed['data'].get('hookEvent') == 'SessionStart'
        ):
            last_started_at = item.timestamp

        # Compute cost and context usage
        compute_item_cost_and_usage(item, parsed, seen_message_ids)
        if item.context_usage is not None:
            last_context_usage = item.context_usage

        # Extract runtime environment fields (keep last non-null value)
        if cwd := parsed.get('cwd'):
            if first_cwd is None:
                first_cwd = cwd
            last_cwd = cwd
        if cwd_git_branch := parsed.get('gitBranch'):
            last_cwd_git_branch = cwd_git_branch
        if (message := parsed.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                last_model = model

        # Resolve git directory/branch from tool_use paths
        if item.git_directory is not None:
            # Already resolved (recompute protection) — preserve and track
            last_resolved_git_directory = item.git_directory
            last_resolved_git_branch = item.git_branch
        else:
            # Attempt resolution from tool_use paths
            git_resolution = resolve_git_for_item(parsed)
            if git_resolution is not None:
                item.git_directory, item.git_branch = git_resolution
                last_resolved_git_directory = item.git_directory
                last_resolved_git_branch = item.git_branch

        # Handle title extraction
        if item.kind == ItemKind.USER_MESSAGE and not initial_title_set:
            # First user message: set initial title if not already defined
            title = extract_title_from_user_message(parsed)
            if title:
                session_titles[session_id] = title
                initial_title_set = True

        if item.kind == ItemKind.CUSTOM_TITLE:
            # Custom title: update the target session's title
            custom_title = parsed.get('customTitle')
            target_session_id = parsed.get('sessionId', session_id)
            if custom_title and isinstance(custom_title, str):
                session_titles[target_session_id] = custom_title

        # Track user message count (message turns)
        if item.kind == ItemKind.USER_MESSAGE:
            user_message_count += 1

        # Track affected days for activity recalculation (only for items that contribute)
        if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost):
            affected_days.add(item.timestamp.date().isoformat())

        # Track tool_use IDs from assistant/content_items messages
        tool_use_entries = get_tool_use_entries(parsed)
        for tu_id, tu_name in tool_use_entries.items():
            tool_use_map[tu_id] = (item.line_num, tu_name)

        # Track Task tool_use IDs (for agent links)
        task_tool_use_entries = get_task_tool_uses(parsed)
        for tu_id, is_background in task_tool_use_entries:
            task_tool_use_map[tu_id] = (item.line_num, is_background, item.timestamp)

        # Check if this is a tool_result and create link
        tool_result_ref = get_tool_result_id(parsed)
        if tool_result_ref and tool_result_ref in tool_use_map:
            tu_line_num, tu_name = tool_use_map[tool_result_ref]
            extra = compute_edit_diff_stats(parsed) if tu_name == 'Edit' else None
            tool_result_links_to_create.append({
                'session_id': session_id,
                'tool_use_line_num': tu_line_num,
                'tool_result_line_num': item.line_num,
                'tool_use_id': tool_result_ref,
                'tool_name': tu_name,
                'tool_result_at': item.timestamp,
                'extra': extra,
            })
            # Track result counts for Agent/Task tools to detect natural completion
            if tu_name in AGENT_TOOL_NAMES:
                prev_count, _ = agent_tool_result_counts.get(tool_result_ref, (0, None))
                agent_tool_result_counts[tool_result_ref] = (prev_count + 1, item.timestamp)

        # Check if this is a Task tool_result with agentId and create agent link
        if agent_info := get_tool_result_agent_info(parsed):
            tu_id, agent_id = agent_info
            if tu_id in task_tool_use_map:
                line_num, is_background, started_at = task_tool_use_map[tu_id]
                agent_links_to_create.append({
                    'session_id': session_id,
                    'tool_use_line_num': line_num,
                    'tool_use_id': tu_id,
                    'agent_id': agent_id,
                    'is_background': is_background,
                    'started_at': started_at,
                })
                # Remove from map to avoid duplicate links
                del task_tool_use_map[tu_id]

        # Detect prefix/suffix for ALWAYS items
        has_prefix, has_suffix = False, False
        if item.display_level == ItemDisplayLevel.ALWAYS:
            has_prefix, has_suffix = _detect_prefix_suffix(parsed, item.kind)

        # Process through group state machine
        info = state.process_item(
            line_num=item.line_num,
            display_level=item.display_level,
            has_prefix=has_prefix,
            has_suffix=has_suffix,
            item_ref=item,
        )
        item.group_head = info.group_head
        # group_tail is set by GroupState when group closes

        # Add any items whose groups were just closed
        items_to_update.extend(info.closed_items)

        # Collect current item for batch update (except items still in open group)
        if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
            items_to_update.append(item)
        elif item.display_level == ItemDisplayLevel.ALWAYS and not has_suffix:
            items_to_update.append(item)
        # COLLAPSIBLE and ALWAYS-with-suffix are added via closed_items when group closes

        # Flush items batch when full
        if len(items_to_update) >= batch_size:
            flush_items(items_to_update)
            items_to_update = []

        # Flush link batches when full
        if len(tool_result_links_to_create) >= batch_size:
            flush_tool_result_links(tool_result_links_to_create)
            tool_result_links_to_create = []
        if len(agent_links_to_create) >= batch_size:
            flush_agent_links(agent_links_to_create)
            agent_links_to_create = []

    # Finalize pending groups
    finalized = state.finalize()
    items_to_update.extend(finalized)

    # Final flush to accumulators
    flush_items(items_to_update)
    flush_tool_result_links(tool_result_links_to_create)
    flush_agent_links(agent_links_to_create)

    # user_message_count is already tracked as a simple counter (incremented for each USER_MESSAGE)

    # Determine which subagents naturally finished based on tool_result counts
    for tu_id, (result_count, last_ts) in agent_tool_result_counts.items():
        if last_ts is None:
            continue
        # Find agent_id for this tool_use_id from agent_links we're about to create
        for link in all_agent_links:
            if link['tool_use_id'] == tu_id:
                required = 2 if link.get('is_background') else 1
                if result_count >= required:
                    agent_stopped_list.append({
                        'agent_session_id': link['agent_id'],
                        'stopped_at': last_ts.isoformat(),
                    })
                break

    # Determine project directory update (for real sessions only)
    project_directory = first_cwd if first_cwd and session.type == SessionType.SESSION else None

    # Send ALL results as a single message (serialized with orjson for speed)
    # Note: costs (self_cost, subagents_cost, total_cost) are NOT included here.
    # They are recalculated from SessionItem data in the main process after items are written,
    # using Session.recalculate_costs(). This avoids order-of-processing issues with subagents.

    # Fallback: if no item provided git info, try resolving from the session's cwd.
    # This handles sessions where the agent only uses Bash (no tool_use with file paths).
    # Uses use_cache=True (default) since background compute benefits from caching across sessions.
    if not last_resolved_git_directory and last_cwd:
        cwd_git = resolve_git_from_path(last_cwd)
        if cwd_git:
            last_resolved_git_directory, last_resolved_git_branch = cwd_git

    result_queue.put(orjson.dumps({
        'type': 'session_complete',
        'session_id': session_id,
        'project_id': session.project_id,
        'item_updates': all_item_updates,
        'item_fields': ['display_level', 'group_head', 'group_tail', 'kind', 'message_id', 'cost', 'context_usage', 'timestamp', 'git_directory', 'git_branch'],
        'content_overrides': content_overrides,
        'tool_result_links': all_tool_result_links,
        'agent_links': all_agent_links,
        'session_fields': {
            'compute_version': settings.CURRENT_COMPUTE_VERSION,
            'user_message_count': user_message_count,
            'context_usage': last_context_usage,
            'cwd': last_cwd,
            'cwd_git_branch': last_cwd_git_branch,
            'git_directory': last_resolved_git_directory,
            'git_branch': last_resolved_git_branch,
            'model': last_model,
            'created_at': first_timestamp.isoformat() if first_timestamp else None,
            'last_started_at': last_started_at.isoformat() if last_started_at else None,
            'last_updated_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else (last_updated_at.isoformat() if last_updated_at else None),
            'last_stopped_at': datetime.fromtimestamp(session.mtime, tz=timezone.utc).isoformat() if session.mtime else None,
        },
        'titles': session_titles,
        'project_directory': project_directory,
        'affected_days': sorted(affected_days) if affected_days else None,
        'agent_stopped': agent_stopped_list or None,
    }))

    connection.close()


# =============================================================================
# Live Processing: compute_item_metadata_live
# =============================================================================


def _find_open_group_head(session_id: str, before_line_num: int) -> int | None:
    """
    Find the head of any open group before the given line number.

    Skips DEBUG_ONLY items. Returns None if no open group.
    """
    # Look at previous non-DEBUG_ONLY item
    previous = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=before_line_num,
    ).exclude(
        display_level=ItemDisplayLevel.DEBUG_ONLY
    ).order_by('-line_num').first()

    if not previous:
        return None

    # COLLAPSIBLE with group_head = group is open
    if previous.display_level == ItemDisplayLevel.COLLAPSIBLE and previous.group_head:
        return previous.group_head

    # ALWAYS with suffix = check if it has collapsible suffix
    if previous.display_level == ItemDisplayLevel.ALWAYS:
        try:
            parsed = orjson.loads(previous.content)
            _, has_suffix = _detect_prefix_suffix(parsed, previous.kind)
            if has_suffix:
                return previous.line_num  # ALWAYS item is the head
        except orjson.JSONDecodeError:
            pass

    return None


def create_tool_result_link_live(
    session_id: str, item: SessionItem, parsed_json: dict
) -> ToolResultUpdate | None:
    """
    Create a ToolResultLink for a tool_result item during live sync.

    Searches the session for the item containing the matching tool_use
    and creates the link entry.

    Returns a ToolResultUpdate if the tool is tracked (Bash/Task/Agent), None otherwise.
    """
    from twicc.core.models import ToolResultLink

    tool_use_id = get_tool_result_id(parsed_json)
    if not tool_use_id:
        return None

    # Find candidates by text search (LIKE), ordered most recent first.
    # The tool_use_id string could appear in text content (e.g. assistant mentioning it),
    # so we iterate candidates and verify each one until we find an actual tool_use match.
    candidates = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=item.line_num,
        content__contains=tool_use_id,
    ).order_by('-line_num')

    for candidate in candidates.iterator(chunk_size=10):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        tool_use_entries = get_tool_use_entries(candidate_parsed)
        if tool_use_id in tool_use_entries:
            tool_name = tool_use_entries[tool_use_id]
            extra = compute_edit_diff_stats(parsed_json) if tool_name == 'Edit' else None
            _, created = ToolResultLink.objects.get_or_create(
                session_id=session_id,
                tool_use_line_num=candidate.line_num,
                tool_result_line_num=item.line_num,
                tool_use_id=tool_use_id,
                defaults={'tool_name': tool_name, 'tool_result_at': item.timestamp, 'extra': extra},
            )
            if not created:
                return None

            # Emit ToolResultUpdate for tracked tools (Bash, Agent, WebFetch, Edit, MCP, etc.)
            if is_tracked_tool(tool_name):
                links = ToolResultLink.objects.filter(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                )
                result_count = links.count()
                max_timestamp = links.order_by('-tool_result_at').values_list('tool_result_at', flat=True).first()
                return ToolResultUpdate(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                    result_count=result_count,
                    completed_at=max_timestamp,
                    extra=extra,
                )

            return None

    return None


def check_agent_naturally_stopped(
    session_id: str, tool_result_update: ToolResultUpdate
) -> AgentStoppedUpdate | None:
    """Check if a Task/Agent tool_result indicates a subagent has naturally finished.

    For non-background agents, 1 tool_result means done.
    For background agents, 2 tool_results means done.

    If the agent is done, updates its last_stopped_at and last_updated_at.

    Returns an AgentStoppedUpdate if the agent stopped, None otherwise.
    """
    from twicc.core.models import AgentLink, Session

    # Find the AgentLink for this tool_use_id
    agent_link = AgentLink.objects.filter(
        session_id=session_id,
        tool_use_id=tool_result_update.tool_use_id,
    ).first()
    if agent_link is None:
        return None

    required_results = 2 if agent_link.is_background else 1
    if tool_result_update.result_count < required_results:
        return None

    stopped_at = tool_result_update.completed_at
    if stopped_at is None:
        return None

    # Find the agent session and update its lifecycle timestamps
    agent_session_id = agent_link.agent_id
    updated = Session.objects.filter(
        id=agent_session_id,
    ).update(last_stopped_at=stopped_at, last_updated_at=stopped_at)

    if updated:
        return AgentStoppedUpdate(
            agent_session_id=agent_session_id,
            stopped_at=stopped_at,
        )

    return None


def create_agent_link_from_tool_result(session_id: str, item: SessionItem, parsed_json: dict) -> AgentLinkUpdate | None:
    """
    Create an AgentLink for a Task tool_result with agentId during live sync.

    When a tool_result arrives with an agentId in toolUseResult, this function
    finds the corresponding Task tool_use and creates an agent link.

    Returns an AgentLinkUpdate if a link was created, None otherwise.
    """
    from twicc.core.models import AgentLink

    agent_info = get_tool_result_agent_info(parsed_json)
    if not agent_info:
        return None

    tool_use_id, agent_id = agent_info

    if is_agent_link_done(session_id, agent_id):
        return None

    # Check if we already have this agent link
    if AgentLink.objects.filter(
        session_id=session_id,
        agent_id=agent_id,
    ).exists():
        mark_agent_link_done(session_id, agent_id)
        return None

    # Find the Task tool_use by searching for the tool_use_id
    candidates = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lt=item.line_num,
        content__contains=tool_use_id,
    ).order_by('-line_num')

    for candidate in candidates.iterator(chunk_size=10):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        # Check if this candidate has a Task tool_use with this ID
        for tu_id, is_background in get_task_tool_uses(candidate_parsed):
            if tu_id != tool_use_id:
                continue
            try:
                obj, created = AgentLink.objects.get_or_create(
                    session_id=session_id,
                    tool_use_line_num=candidate.line_num,
                    tool_use_id=tool_use_id,
                    defaults={"agent_id": agent_id, "is_background": is_background, "started_at": candidate.timestamp},
                )
                mark_agent_link_done(session_id, agent_id)
                if created:
                    return AgentLinkUpdate(
                        parent_session_id=session_id,
                        agent_id=agent_id,
                        tool_use_id=tool_use_id,
                        is_background=is_background,
                        started_at=candidate.timestamp,
                    )
            except MultipleObjectsReturned:  # defensive mode
                pass
            return None
    return None


def _extract_task_tool_use_prompts(content: list) -> list[tuple[str, str, bool]]:
    """
    Extract (tool_use_id, prompt, is_background) triples from agent tool_use items in content.

    Args:
        content: The content array from an assistant message

    Returns:
        List of (tool_use_id, prompt, is_background) tuples for all agent tool_uses found (Task or Agent)
    """
    results = []
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


AGENTS_LINKS_DONE_CACHE: set[tuple[str, str]] = set()
AGENTS_PROMPT_CACHE: dict[tuple[str, str], str] = {}


def mark_agent_link_done(session_id: str, agent_id: str) -> None:
    """Mark that we've created the agent link for this subagent."""
    AGENTS_LINKS_DONE_CACHE.add((session_id, agent_id))
    uncache_agent_prompt(session_id, agent_id)


def is_agent_link_done(session_id: str, agent_id: str) -> bool:
    """Check if we've already created the agent link for this subagent."""
    return (session_id, agent_id) in AGENTS_LINKS_DONE_CACHE


def get_cached_agent_prompt(session_id: str, agent_id: str) -> str | None:
    """Get the prompt for a subagent from the cache."""
    return AGENTS_PROMPT_CACHE.get((session_id, agent_id))


def cache_agent_prompt(session_id: str, agent_id: str, prompt: str) -> None:
    """Set the prompt for a subagent in the cache."""
    AGENTS_PROMPT_CACHE[(session_id, agent_id)] = prompt


def uncache_agent_prompt(session_id: str, agent_id: str) -> None:
    """Clear the prompt for a subagent from the cache."""
    AGENTS_PROMPT_CACHE.pop((session_id, agent_id), None)


def create_agent_link_from_subagent(
    parent_session_id: str,
    agent_id: str,
    agent_prompt: str,
) -> AgentLinkUpdate | None:
    """
    Create an AgentLink for a subagent by matching its prompt to a Task tool_use.

    When a new subagent is detected, this function searches the parent session
    for a Task tool_use with a matching prompt and creates an agent link.

    This allows linking the tool_use to its agent before the tool_result arrives.

    Returns an AgentLinkUpdate if the link was created, None otherwise.
    """
    from twicc.core.models import AgentLink

    if is_agent_link_done(parent_session_id, agent_id):
        return None

    # Check if we already have this agent link
    if AgentLink.objects.filter(
        session_id=parent_session_id,
        agent_id=agent_id,
    ).exists():
        mark_agent_link_done(parent_session_id, agent_id)
        return None

    agent_prompt = agent_prompt.strip()

    # Search for agent tool_use items in parent session, most recent first
    # We look for items containing '"name":"Task"' or '"name":"Agent"' to narrow the search
    candidates = SessionItem.objects.filter(
        Q(content__contains='"name":"Task"') | Q(content__contains='"name":"Agent"'),
        session_id=parent_session_id,
    ).order_by('-line_num')

    for index, candidate in enumerate(candidates.iterator(chunk_size=20)):
        try:
            candidate_parsed = orjson.loads(candidate.content)
        except orjson.JSONDecodeError:
            continue

        # Get the content list from assistant message
        content = get_message_content_list(candidate_parsed, "assistant")
        if content is None:
            continue

        # Extract (tool_use_id, prompt, is_background) triples from Task tool_uses
        for tu_id, prompt, is_background in _extract_task_tool_use_prompts(content):
            if prompt.strip() == agent_prompt:
                try:
                    obj, created = AgentLink.objects.get_or_create(
                        session_id=parent_session_id,
                        tool_use_line_num=candidate.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": agent_id, "is_background": is_background, "started_at": candidate.timestamp},
                    )
                    if created:
                        mark_agent_link_done(parent_session_id, agent_id)
                        return AgentLinkUpdate(
                            parent_session_id=parent_session_id,
                            agent_id=agent_id,
                            tool_use_id=tu_id,
                            is_background=is_background,
                            started_at=candidate.timestamp,
                        )
                except MultipleObjectsReturned:  # defensive mode
                    continue

    return None


def create_agent_link_from_tool_use(
    session_id: str,
    item: SessionItem,
    parsed_json: dict,
) -> list[AgentLinkUpdate]:
    """
    Create AgentLink(s) for Task tool_use(s) by matching against existing subagents.

    When a new assistant message containing Task tool_use(s) is synced in the parent session,
    this function checks if any matching subagents already exist in the database.
    This handles the race condition where the subagent file is synced before the parent
    session's Task tool_use item exists: when the parent catches up, we create the link here.

    Returns a list of AgentLinkUpdates for each link created.
    """
    from twicc.core.models import AgentLink

    # Extract assistant message content
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []

    # Collect all (tool_use_id, prompt, is_background) triples from agent tool_uses in this message
    task_prompts = _extract_task_tool_use_prompts(content)
    # Normalize prompts for matching
    task_prompts = [(tu_id, prompt.strip(), is_bg) for tu_id, prompt, is_bg in task_prompts]

    if not task_prompts:
        return []

    updates: list[AgentLinkUpdate] = []

    # Get all subagents for this session that don't have a link yet
    subagents = Session.objects.filter(
        parent_session_id=session_id,
        type=SessionType.SUBAGENT,
    )

    # For each subagent, check if its prompt matches one of our Task tool_use prompts
    for subagent in subagents:
        if is_agent_link_done(session_id, subagent.id):
            continue

        # Check if link already exists
        if AgentLink.objects.filter(
            session_id=session_id,
            agent_id=subagent.id,
        ).exists():
            mark_agent_link_done(session_id, subagent.id)
            continue

        # Get the subagent's prompt from cache or DB
        subagent_prompt = get_cached_agent_prompt(session_id, subagent.id)
        if not subagent_prompt:
            # Try to get from the subagent's first user message
            first_user_message = SessionItem.objects.filter(
                session_id=subagent.id,
                kind=ItemKind.USER_MESSAGE,
            ).first()
            if first_user_message is None:
                continue
            try:
                first_parsed = orjson.loads(first_user_message.content)
            except orjson.JSONDecodeError:
                continue
            subagent_prompt = extract_text_from_content(get_message_content(first_parsed))
            if not subagent_prompt:
                continue
            subagent_prompt = subagent_prompt.strip()
            cache_agent_prompt(session_id, subagent.id, subagent_prompt)

        # Check if the subagent's prompt matches any Task tool_use prompt
        for tu_id, prompt, is_background in task_prompts:
            if prompt == subagent_prompt:
                try:
                    _, created = AgentLink.objects.get_or_create(
                        session_id=session_id,
                        tool_use_line_num=item.line_num,
                        tool_use_id=tu_id,
                        defaults={"agent_id": subagent.id, "is_background": is_background, "started_at": item.timestamp},
                    )
                    if created:
                        mark_agent_link_done(session_id, subagent.id)
                        updates.append(AgentLinkUpdate(
                            parent_session_id=session_id,
                            agent_id=subagent.id,
                            tool_use_id=tu_id,
                            is_background=is_background,
                            started_at=item.timestamp,
                        ))
                except MultipleObjectsReturned:
                    pass
                break

    return updates


def compute_item_metadata_live(session_id: str, item: SessionItem, parsed_json: dict) -> set[int]:
    """
    Compute metadata for a single item during live sync.

    Unlike batch processing, this queries the database for context.

    Args:
        session_id: The session ID
        item: The SessionItem object (already has line_num and content set)
        parsed_json: The already-parsed JSON content (possibly transformed)

    Returns:
        Set of line_nums of pre-existing items whose group_tail was updated
    """
    # Resolve git directory/branch from tool_use paths (no cache for live resolution)
    git_resolution = resolve_git_for_item(parsed_json, use_cache=False)
    if git_resolution is not None:
        item.git_directory, item.git_branch = git_resolution

    # Initialize group fields
    item.group_head = None
    item.group_tail = None

    if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
        return set()

    # Track which pre-existing items were modified
    modified_line_nums: set[int] = set()

    # Find if there's an open group before us
    open_group_head = _find_open_group_head(session_id, item.line_num)

    if item.display_level == ItemDisplayLevel.COLLAPSIBLE:
        if open_group_head is not None:
            # Join existing group
            item.group_head = open_group_head
            item.group_tail = item.line_num

            # Get line_nums of pre-existing items that will be updated
            affected_collapsibles = SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True)
            modified_line_nums.update(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).exists()
            if always_starter:
                modified_line_nums.add(open_group_head)

            # Update all items in group with new tail
            SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head
            ).update(group_tail=item.line_num)

            # Also update ALWAYS item if it started the group (via suffix)
            SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).update(group_tail=item.line_num)
        else:
            # Start new group
            item.group_head = item.line_num
            item.group_tail = item.line_num

    elif item.display_level == ItemDisplayLevel.ALWAYS:
        has_prefix, has_suffix = _detect_prefix_suffix(parsed_json, item.kind)

        # Handle prefix
        if has_prefix and open_group_head is not None:
            item.group_head = open_group_head

            # Get line_nums of pre-existing items that will be updated
            affected_collapsibles = SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True)
            modified_line_nums.update(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).exists()
            if always_starter:
                modified_line_nums.add(open_group_head)

            # Update all items in group with new tail (this item)
            SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head
            ).update(group_tail=item.line_num)

            # Also update ALWAYS item if it started the group
            SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).update(group_tail=item.line_num)

        # Suffix: group_tail stays null until next item arrives and connects
        # (will be updated by next item's compute_item_metadata_live)

    return modified_line_nums


def apply_session_complete(msg: dict) -> None:
    """
    Apply all results for a session in one go.

    This handles the new 'session_complete' message type that contains
    all updates for a session in a single message.
    """
    session_id = msg['session_id']

    # 1. Delete existing links
    ToolResultLink.objects.filter(session_id=session_id).delete()
    AgentLink.objects.filter(session_id=session_id).delete()

    # 2. Apply item updates
    item_updates = msg.get('item_updates', [])
    item_fields = msg.get('item_fields', [])
    if item_updates and item_fields:
        items = [
            SessionItem(id=upd['id'], **{
                field: Decimal(value) if (value := upd.get(field)) is not None and field == 'cost' else value
                for field in item_fields
            })
            for upd in item_updates
        ]
        SessionItem.objects.bulk_update(items, item_fields, 50)

    # 2b. Apply content overrides (rare: only transformed task-notification items)
    content_overrides = msg.get('content_overrides', [])
    if content_overrides:
        items = [
            SessionItem(id=ovr['id'], content=ovr['content'])
            for ovr in content_overrides
        ]
        SessionItem.objects.bulk_update(items, ['content'], 50)

    # 3. Create links
    tool_result_links_data = msg.get('tool_result_links', [])
    if tool_result_links_data:
        links = [ToolResultLink(**d) for d in tool_result_links_data]
        ToolResultLink.objects.bulk_create(links, ignore_conflicts=True)

    agent_links_data = msg.get('agent_links', [])
    if agent_links_data:
        links = [AgentLink(**d) for d in agent_links_data]
        AgentLink.objects.bulk_create(links, ignore_conflicts=True)

    # 4. Update session fields
    session_fields = msg.get('session_fields', {})
    if session_fields:
        # Handle datetime fields
        for dt_field in ('created_at', 'last_started_at', 'last_updated_at', 'last_stopped_at'):
            if dt_field in session_fields and session_fields[dt_field] is not None:
                session_fields[dt_field] = datetime.fromisoformat(session_fields[dt_field])
        Session.objects.filter(id=session_id).update(**session_fields)

    # 5. Recalculate session costs from SessionItem data (idempotent, order-independent)
    session = Session.objects.get(id=session_id)
    session.recalculate_costs()
    session.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 6. If this is a subagent, recalculate parent session costs too
    if session.parent_session_id:
        parent = Session.objects.get(id=session.parent_session_id)
        parent.recalculate_costs()
        parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])

    # 7. Update titles
    titles = msg.get('titles', {})
    for target_id, title in titles.items():
        Session.objects.filter(id=target_id).update(title=title)

    # 8. Update project directory
    project_id = msg.get('project_id')
    project_directory = msg.get('project_directory')
    if project_id and project_directory:
        ensure_project_directory(project_id, project_directory)

    # 9. Resolve project git_root if session has git info but project doesn't
    session_git_dir = session_fields.get('git_directory') if session_fields else None
    if session_git_dir and project_id and get_project_git_root(project_id) is None:
        ensure_project_git_root(project_id)

    # 10. Update last_stopped_at for subagents that naturally finished
    agent_stopped = msg.get('agent_stopped')
    if agent_stopped:
        for entry in agent_stopped:
            stopped_at = datetime.fromisoformat(entry['stopped_at'])
            Session.objects.filter(id=entry['agent_session_id']).update(
                last_stopped_at=stopped_at, last_updated_at=stopped_at
            )

    # 11. Update project metadata (sessions_count, mtime, total_cost)
    if project_id:
        update_project_metadata(project_id)
