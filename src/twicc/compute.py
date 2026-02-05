"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import orjson
import logging
from datetime import datetime
from typing import Any, NamedTuple

import xmltodict
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned

from twicc.core.enums import ItemDisplayLevel, ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.pricing import (
    calculate_line_cost,
    calculate_line_context_usage,
    extract_model_info,
)

# Content types considered user-visible (for display_level and kind computation)
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')

# XML prefixes for system messages
# These are user messages that should be treated as debug-only
_SYSTEM_XML_PREFIXES = (
    '<local-command-',
)

# Maximum length for extracted titles (before truncation)
TITLE_MAX_LENGTH = 200

logger = logging.getLogger(__name__)


# =============================================================================
# Project Directory Cache
# =============================================================================

# Module-level cache: project_id -> directory (can be None)
_project_directories: dict[str, str | None] = {}


def load_project_directories() -> None:
    """
    Load all project directories into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_directories.clear()
    _project_directories.update(
        Project.objects.values_list('id', 'directory')
    )


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

    # Update DB and cache
    Project.objects.filter(id=project_id).update(directory=cwd)
    _project_directories[project_id] = cwd


def update_project_total_cost(project_id: str) -> None:
    """
    Update a project's total_cost by summing all its sessions' total_costs.

    Only sums costs from non-stale regular sessions (not subagents).
    Subagent costs are already included in their parent session's total_cost.
    """
    from decimal import Decimal
    from django.db.models import Sum

    total_cost = Session.objects.filter(
        project_id=project_id,
        stale=False,
        type=SessionType.SESSION,
        total_cost__isnull=False,
    ).aggregate(total=Sum('total_cost'))['total'] or Decimal(0)

    Project.objects.filter(id=project_id).update(
        total_cost=total_cost if total_cost > 0 else None
    )


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


def get_tool_use_ids(parsed_json: dict) -> list[str]:
    """
    Extract tool_use IDs from an assistant or content_items message.

    Returns a list of tool_use IDs found in the message content array.
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []
    return [
        item['id']
        for item in content
        if isinstance(item, dict) and item.get('type') == 'tool_use' and item.get('id')
    ]


def get_tool_result_id(parsed_json: dict) -> str | None:
    """
    Extract the tool_use_id from a standalone tool_result item.

    Returns the tool_use_id string, or None if not a tool_result item.
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None or len(content) != 1:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get('type') != 'tool_result':
        return None
    return first.get('tool_use_id')


def is_tool_result_item(parsed_json: dict) -> bool:
    """
    Check if an item is a standalone tool_result.

    A tool_result item is a user message whose content array contains
    a single entry of type "tool_result". These items also have
    a "toolUseResult" key at root level.
    """
    content = get_message_content_list(parsed_json, "user")
    if content is None or len(content) != 1:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get('type') == 'tool_result'


def get_task_tool_uses(parsed_json: dict) -> list[str]:
    """
    Extract tool_use IDs from "Task" tool calls in an assistant message.

    Returns a list of tool_use IDs for tool_use items where name="Task".
    """
    content = get_message_content_list(parsed_json, "assistant")
    if content is None:
        return []
    return [
        item['id']
        for item in content
        if isinstance(item, dict)
        and item.get('type') == 'tool_use'
        and item.get('name') == 'Task'
        and item.get('id')
    ]


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
    # Must be a tool_result item
    content = get_message_content_list(parsed_json, "user")
    if content is None or len(content) != 1:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get('type') != 'tool_result':
        return None

    tool_use_id = first.get('tool_use_id')
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

    # DEBUG_ONLY: Standalone tool_result items (their data is accessed via SessionItemLink)
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
    - SYSTEM: System messages (except api_error), queue-operation, progress
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

    # System types: system (except api_error), queue-operation, progress
    if entry_type in ('queue-operation', 'progress'):
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

        # Commands are shown as user messages
        if text is not None and extract_command(text):
            return ItemKind.USER_MESSAGE

        # Meta messages are not user messages
        if parsed_json.get('isMeta'):
            return ItemKind.SYSTEM

        # System XML messages (commands, outputs) are SYSTEM kind
        if _is_system_xml_content(content):
            return ItemKind.SYSTEM

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

    Sends all results via result_queue. Does NOT write to the database directly.
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
        result_queue.put({
            'type': 'error',
            'session_id': session_id,
            'error': 'Session not found',
        })
        return

    # Signal to delete existing links (will be done by consumer)
    result_queue.put({
        'type': 'delete_links',
        'session_id': session_id,
    })

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    state = GroupState()
    items_to_update: list[SessionItem] = []
    links_to_create: list[dict] = []
    batch_size = 50

    # Helper to serialize items for queue
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
            }
            for item in items
        ]

    # Helper to flush item batch
    def flush_items(items: list[SessionItem]) -> None:
        if not items:
            return
        result_queue.put({
            'type': 'batch',
            'session_id': session_id,
            'updates': serialize_items(items),
            'fields': ['display_level', 'group_head', 'group_tail', 'kind', 'message_id', 'cost', 'context_usage', 'timestamp'],
        })

    # Helper to flush links batch
    def flush_links(links: list[dict]) -> None:
        if not links:
            return
        result_queue.put({
            'type': 'links',
            'session_id': session_id,
            'links': links,
        })

    # Map tool_use_id → line_num of the item containing the tool_use
    tool_use_map: dict[str, int] = {}
    # Map tool_use_id → line_num for Task tool_uses (to link to agents)
    task_tool_use_map: dict[str, int] = {}

    # Track if we've set the initial title from first user message
    initial_title_set = False
    # Track sessions that need title updates (session_id -> title)
    session_titles: dict[str, str] = {}

    # Track message count
    user_message_count = 0
    last_relevant_kind: ItemKind | None = None

    # Track cost and context usage (deduplication by message_id)
    seen_message_ids: set[str] = set()
    last_context_usage: int | None = None

    # Track runtime environment fields (last seen values)
    first_cwd: str | None = None  # First cwd = project directory
    last_cwd: str | None = None
    last_git_branch: str | None = None
    last_model: str | None = None

    for item in queryset.iterator(chunk_size=batch_size):
        try:
            parsed = orjson.loads(item.content)
        except orjson.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        # Compute display_level and kind
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)

        # Compute cost and context usage
        compute_item_cost_and_usage(item, parsed, seen_message_ids)
        if item.context_usage is not None:
            last_context_usage = item.context_usage

        # Extract runtime environment fields (keep last non-null value)
        if cwd := parsed.get('cwd'):
            if first_cwd is None:
                first_cwd = cwd
            last_cwd = cwd
        if git_branch := parsed.get('gitBranch'):
            last_git_branch = git_branch
        if (message := parsed.get('message')) and isinstance(message, dict):
            if model := message.get('model'):
                last_model = model

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

        # Track message count: count user messages and track last relevant kind
        if item.kind == ItemKind.USER_MESSAGE:
            user_message_count += 1
            last_relevant_kind = ItemKind.USER_MESSAGE
        elif item.kind == ItemKind.ASSISTANT_MESSAGE:
            last_relevant_kind = ItemKind.ASSISTANT_MESSAGE

        # Track tool_use IDs from assistant/content_items messages
        tool_use_ids = get_tool_use_ids(parsed)
        for tu_id in tool_use_ids:
            tool_use_map[tu_id] = item.line_num

        # Track Task tool_use IDs (for agent links)
        task_tool_use_ids = get_task_tool_uses(parsed)
        for tu_id in task_tool_use_ids:
            task_tool_use_map[tu_id] = item.line_num

        # Check if this is a tool_result and create link
        tool_result_ref = get_tool_result_id(parsed)
        if tool_result_ref and tool_result_ref in tool_use_map:
            links_to_create.append({
                'session_id': session_id,
                'source_line_num': tool_use_map[tool_result_ref],
                'target_line_num': item.line_num,
                'link_type': 'tool_result',
                'reference': tool_result_ref,
            })

        # Check if this is a Task tool_result with agentId and create agent link
        agent_info = get_tool_result_agent_info(parsed)
        if agent_info:
            tu_id, agent_id = agent_info
            if tu_id in task_tool_use_map:
                links_to_create.append({
                    'session_id': session_id,
                    'source_line_num': task_tool_use_map[tu_id],
                    'target_line_num': None,
                    'link_type': 'agent',
                    'reference': agent_id,
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

        # Flush links batch when full
        if len(links_to_create) >= batch_size:
            flush_links(links_to_create)
            links_to_create = []

    # Finalize pending groups
    finalized = state.finalize()
    items_to_update.extend(finalized)

    # Final flush
    flush_items(items_to_update)
    flush_links(links_to_create)

    # Compute message count: user_count * 2 - 1 if last is user, else user_count * 2
    if user_message_count == 0:
        message_count = 0
    elif last_relevant_kind == ItemKind.USER_MESSAGE:
        message_count = user_message_count * 2 - 1
    else:
        message_count = user_message_count * 2

    # Sum all item costs for self_cost
    self_cost = SessionItem.objects.filter(
        session=session,
        cost__isnull=False
    ).aggregate(total=Sum('cost'))['total']

    # Sum all subagent total_costs for subagents_cost
    subagents_cost = Session.objects.filter(
        parent_session=session,
        total_cost__isnull=False
    ).aggregate(total=Sum('total_cost'))['total'] or Decimal(0)

    total_cost = (self_cost or Decimal(0)) + subagents_cost

    # Determine project directory update (for real sessions only)
    project_directory = first_cwd if first_cwd and session.type == SessionType.SESSION else None

    # Send session update via queue
    result_queue.put({
        'type': 'session_update',
        'session_id': session_id,
        'project_id': session.project_id,
        'fields': {
            'compute_version': settings.CURRENT_COMPUTE_VERSION,
            'message_count': message_count,
            'context_usage': last_context_usage,
            'self_cost': str(self_cost) if self_cost else None,
            'subagents_cost': str(subagents_cost) if subagents_cost else None,
            'total_cost': str(total_cost) if total_cost else None,
            'cwd': last_cwd,
            'git_branch': last_git_branch,
            'model': last_model,
        },
        'titles': session_titles,
        'project_directory': project_directory,
    })

    # Signal completion
    result_queue.put({
        'type': 'done',
        'session_id': session_id,
    })

    connection.close()


def apply_compute_results(result_queue) -> None:
    """
    Apply compute results from the queue to the database.

    Consumes all messages from the queue and applies corresponding DB operations.
    Used by tests to apply results after calling compute_session_metadata().

    Args:
        result_queue: Queue containing compute result messages
    """
    import queue as queue_module
    from decimal import Decimal
    from twicc.core.models import Session, SessionItem, SessionItemLink

    while True:
        try:
            msg = result_queue.get_nowait()
        except queue_module.Empty:
            break

        msg_type = msg.get('type')

        if msg_type == 'delete_links':
            SessionItemLink.objects.filter(session_id=msg['session_id']).delete()

        elif msg_type == 'batch':
            updates = msg['updates']
            fields = msg['fields']
            items = []
            for upd in updates:
                item = SessionItem(id=upd['id'])
                for field in fields:
                    value = upd.get(field)
                    if field == 'cost' and value is not None:
                        value = Decimal(value)
                    setattr(item, field, value)
                items.append(item)
            if items:
                SessionItem.objects.bulk_update(items, fields)

        elif msg_type == 'links':
            links = [SessionItemLink(**link_data) for link_data in msg['links']]
            if links:
                SessionItemLink.objects.bulk_create(links, ignore_conflicts=True)

        elif msg_type == 'session_update':
            session_id = msg['session_id']
            fields = msg['fields']

            # Handle Decimal fields
            for decimal_field in ('self_cost', 'subagents_cost', 'total_cost'):
                if decimal_field in fields and fields[decimal_field] is not None:
                    fields[decimal_field] = Decimal(fields[decimal_field])

            Session.objects.filter(id=session_id).update(**fields)

            # Update titles
            for target_id, title in msg.get('titles', {}).items():
                Session.objects.filter(id=target_id).update(title=title)

            # Update project directory
            project_id = msg.get('project_id')
            directory = msg.get('project_directory')
            if project_id and directory:
                ensure_project_directory(project_id, directory)

            # Update project total_cost
            if project_id:
                update_project_total_cost(project_id)

        elif msg_type == 'done':
            # Processing complete for this session
            pass

        elif msg_type == 'error':
            logger.error(f"Compute error for {msg['session_id']}: {msg['error']}")


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


def create_tool_result_link_live(session_id: str, item: SessionItem, parsed_json: dict) -> None:
    """
    Create a SessionItemLink for a tool_result item during live sync.

    Searches the session for the item containing the matching tool_use
    and creates the link entry.
    """
    from twicc.core.models import SessionItemLink

    tool_use_id = get_tool_result_id(parsed_json)
    if not tool_use_id:
        return

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

        if tool_use_id in get_tool_use_ids(candidate_parsed):
            SessionItemLink.objects.get_or_create(
                session_id=session_id,
                source_line_num=candidate.line_num,
                target_line_num=item.line_num,
                link_type='tool_result',
                reference=tool_use_id,
            )
            return


def create_agent_link_from_tool_result(session_id: str, item: SessionItem, parsed_json: dict) -> None:
    """
    Create a SessionItemLink for a Task tool_result with agentId during live sync.

    When a tool_result arrives with an agentId in toolUseResult, this function
    finds the corresponding Task tool_use and creates an 'agent' link.

    Args:
        session_id: The session ID
        item: The SessionItem containing the tool_result
        parsed_json: The parsed JSON content of the tool_result item
    """
    from twicc.core.models import SessionItemLink

    agent_info = get_tool_result_agent_info(parsed_json)
    if not agent_info:
        return

    tool_use_id, agent_id = agent_info

    if is_agent_link_done(session_id, agent_id):
        return

    # Check if we already have this agent link
    if SessionItemLink.objects.filter(
        session_id=session_id,
        link_type='agent',
        reference=agent_id,
    ).exists():
        mark_agent_link_done(session_id, agent_id)
        return

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
        if tool_use_id in get_task_tool_uses(candidate_parsed):
            try:
                SessionItemLink.objects.get_or_create(
                    session_id=session_id,
                    link_type='agent',
                    source_line_num=candidate.line_num,
                    defaults={
                        "reference": agent_id,
                        "target_line_num": None,  # agent links have no target line
                    }
                )
                mark_agent_link_done(session_id, agent_id)
            except MultipleObjectsReturned:  # defensive mode
                pass
            return


def _extract_task_tool_use_prompt(content: list) -> str | None:
    """
    Extract the prompt from a Task tool_use content item.

    Args:
        content: The content array from an assistant message

    Returns:
        The prompt string if found, None otherwise
    """
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get('type') != 'tool_use' or item.get('name') != 'Task':
            continue
        inputs = item.get('input', {})
        if isinstance(inputs, dict):
            prompt = inputs.get('prompt')
            if isinstance(prompt, str):
                return prompt
    return None


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
) -> bool:
    """
    Create a SessionItemLink for a subagent by matching its prompt to a Task tool_use.

    When a new subagent is detected, this function searches the parent session
    for a Task tool_use with a matching prompt and creates an 'agent' link.

    This allows linking the tool_use to its agent before the tool_result arrives.

    Args:
        parent_session_id: The parent session ID
        agent_id: The agent's ID (e.g., "a9785d3")
        agent_prompt: The prompt from the agent's first user message
        agent_timestamp: The timestamp from the agent's first message (ISO format)

    Returns:
        True if the link was created, False otherwise
    """
    from twicc.core.models import SessionItemLink

    if is_agent_link_done(parent_session_id, agent_id):
        return False

    # Check if we already have this agent link
    if SessionItemLink.objects.filter(
        session_id=parent_session_id,
        link_type='agent',
        reference=agent_id,
    ).exists():
        mark_agent_link_done(parent_session_id, agent_id)
        return False

    agent_prompt = agent_prompt.strip()

    # Search for Task tool_use items in parent session, most recent first
    # We look for items containing '"name":"Task"' to narrow the search
    candidates = SessionItem.objects.filter(
        session_id=parent_session_id,
        content__contains='"name":"Task"',
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

        # Extract the prompt from Task tool_use
        prompt = _extract_task_tool_use_prompt(content)
        if prompt and prompt.strip() == agent_prompt:
            try:
                obj, created = SessionItemLink.objects.get_or_create(
                    session_id=parent_session_id,
                    link_type='agent',
                    source_line_num=candidate.line_num,
                    defaults={
                        "reference": agent_id,
                        "target_line_num": None,  # agent links have no target line
                    }
                )
                if created:
                    mark_agent_link_done(parent_session_id, agent_id)
                    logger.debug(f"CREATE LINK FOR AGENT {agent_id} SESSION {parent_session_id}")
                    return True
            except MultipleObjectsReturned:  # defensive mode
                continue

    return False


def compute_item_metadata_live(session_id: str, item: SessionItem, content: str) -> set[int]:
    """
    Compute metadata for a single item during live sync.

    Unlike batch processing, this queries the database for context.

    Args:
        session_id: The session ID
        item: The SessionItem object (already has line_num and content set)
        content: The raw JSON content string

    Returns:
        Set of line_nums of pre-existing items whose group_tail was updated
    """
    try:
        parsed = orjson.loads(content)
    except orjson.JSONDecodeError:
        logger.warning(f"Invalid JSON in item {session_id}:{item.line_num}")
        parsed = {}

    # Compute display_level and kind
    metadata = compute_item_metadata(parsed)
    item.display_level = metadata['display_level']
    item.kind = metadata['kind']

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
        has_prefix, has_suffix = _detect_prefix_suffix(parsed, item.kind)

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
