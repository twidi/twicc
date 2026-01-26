"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.conf import settings

from twicc_poc.core.models import DisplayLevel, SessionItem



if TYPE_CHECKING:
    from twicc_poc.core.models import Session

logger = logging.getLogger(__name__)


def find_current_group_head(session_id: str, from_line_num: int) -> int | None:
    """
    Find the group_head of the current open group.

    Looks backward from from_line_num to find the nearest level 2 item.
    Returns its group_head, or None if no group is open.

    Args:
        session_id: The session ID
        from_line_num: Line number to search backward from

    Returns:
        group_head line_num if in a group, None otherwise
    """
    # Look for the most recent level 2 item
    recent_level2 = SessionItem.objects.filter(
        session_id=session_id,
        line_num__lte=from_line_num,
        display_level=DisplayLevel.COLLAPSIBLE
    ).order_by('-line_num').first()

    if recent_level2 and recent_level2.group_head:
        return recent_level2.group_head

    return None


def compute_item_metadata_live(session_id: str, item: SessionItem, content: str) -> None:
    """
    Compute metadata for a single item during live sync.

    Unlike compute_session_metadata which processes entire sessions,
    this function processes one item at a time and handles group
    membership by querying the database.

    Args:
        session_id: The session ID
        item: The SessionItem object (already has line_num and content set)
        content: The raw JSON content string
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in item {session_id}:{item.line_num}")
        parsed = {}

    metadata = compute_metadata(parsed)
    item.display_level = metadata['display_level']

    if item.display_level == DisplayLevel.ALWAYS:
        # Level 1: no group membership
        item.group_head = None
        item.group_tail = None

    elif item.display_level == DisplayLevel.COLLAPSIBLE:
        # Level 2: might join or start a group
        # Check previous item
        previous = SessionItem.objects.filter(
            session_id=session_id,
            line_num=item.line_num - 1
        ).first()

        if previous and previous.display_level in (DisplayLevel.COLLAPSIBLE, DisplayLevel.DEBUG_ONLY):
            # Previous item was level 2 or 3 → might be continuing a group
            existing_head = find_current_group_head(session_id, item.line_num - 1)
            if existing_head:
                # Continuing existing group
                item.group_head = existing_head
                item.group_tail = item.line_num
                # Update tail for all level 2 items in this group
                SessionItem.objects.filter(
                    session_id=session_id,
                    group_head=existing_head
                ).update(group_tail=item.line_num)
            else:
                # Previous was level 3 with no group → start new group
                item.group_head = item.line_num
                item.group_tail = item.line_num
        else:
            # Previous was level 1 or doesn't exist → start new group
            item.group_head = item.line_num
            item.group_tail = item.line_num

    else:
        # Level 3 (DEBUG_ONLY): no group membership
        item.group_head = None
        item.group_tail = None


def _is_only_tool_result(content: str | list) -> bool:
    """
    Check if message content contains only tool_result items.

    Args:
        content: Message content (string or list of content items)

    Returns:
        True if content is exclusively tool_result items
    """
    if isinstance(content, str):
        return False

    if not isinstance(content, list) or not content:
        return False

    for item in content:
        if isinstance(item, str):
            return False
        if isinstance(item, dict):
            if item.get('type') != 'tool_result':
                return False

    return True


def _has_text_content(content: list) -> bool:
    """
    Check if assistant message content contains visible text.

    Args:
        content: List of content items from assistant message

    Returns:
        True if at least one text content item exists
    """
    if not isinstance(content, list):
        return False

    for item in content:
        if isinstance(item, dict) and item.get('type') == 'text':
            return True

    return False


def compute_display_level(parsed_json: dict) -> int:
    """
    Determine the display level for an item based on its JSON content.

    Classification rules:
    - ALWAYS (1): Real user messages, assistant messages with text, API errors
    - COLLAPSIBLE (2): Meta messages, tool results, thinking/tool_use only,
                       summaries, file snapshots, custom titles
    - DEBUG_ONLY (3): System messages (except api_error), queue ops, progress

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        DisplayLevel enum value (1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY)

    Note:
        Any modification to this function's logic MUST increment
        CURRENT_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    entry_type = parsed_json.get('type')

    # DEBUG_ONLY: System messages (except api_error), queue operations, progress
    if entry_type == 'system':
        subtype = parsed_json.get('subtype')
        if subtype == 'api_error':
            return DisplayLevel.ALWAYS
        return DisplayLevel.DEBUG_ONLY

    if entry_type in ('queue-operation', 'progress'):
        return DisplayLevel.DEBUG_ONLY

    # User messages
    if entry_type == 'user':
        # Meta messages are collapsible
        if parsed_json.get('isMeta') is True:
            return DisplayLevel.COLLAPSIBLE

        # Messages containing only tool_result are collapsible
        message = parsed_json.get('message', {})
        content = message.get('content', [])
        if _is_only_tool_result(content):
            return DisplayLevel.COLLAPSIBLE

        # Real user messages are always visible
        return DisplayLevel.ALWAYS

    # Assistant messages
    if entry_type == 'assistant':
        message = parsed_json.get('message', {})
        content = message.get('content', [])

        # Assistant messages with text content are always visible
        if _has_text_content(content):
            return DisplayLevel.ALWAYS

        # Messages with only thinking/tool_use are collapsible
        return DisplayLevel.COLLAPSIBLE

    # Everything else is collapsible: summary, file-history-snapshot, custom-title
    return DisplayLevel.COLLAPSIBLE


def compute_metadata(parsed_json: dict) -> dict:
    """
    Compute all metadata fields for a single item.

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        Dict with computed metadata fields:
        - display_level: int (DisplayLevel enum value)
        - (future) cost: float
        - (future) component_type: str
    """
    return {
        'display_level': compute_display_level(parsed_json),
        # Future fields can be added here:
        # 'cost': compute_cost(parsed_json),
        # 'component_type': compute_component_type(parsed_json),
    }


def _finalize_group(group_items: list[SessionItem], head_line_num: int, tail_line_num: int) -> None:
    """
    Set group_head and group_tail for all level 2 items in a completed group.

    Args:
        group_items: List of SessionItem objects (level 2) in the group
        head_line_num: line_num of the first level 2 item
        tail_line_num: line_num of the last level 2 item
    """
    for item in group_items:
        item.group_head = head_line_num
        item.group_tail = tail_line_num




def compute_session_metadata(session_id: str) -> None:
    """
    Compute metadata for all items in a session.

    This function runs in a ThreadPoolExecutor.
    It processes items in batches using iterator() to avoid memory issues.

    Args:
        session_id: The session ID
    """
    from django.db import connection
    from twicc_poc.core.models import Session

    # Ensure this thread has its own database connection
    # and close any stale connection from a previous run
    connection.close()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found for metadata computation")
        return

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    current_group_head: int | None = None
    group_items: list[SessionItem] = []  # Buffer for current group's level 2 items
    items_to_update: list[SessionItem] = []  # Accumulate for bulk_update
    batch_size = 50

    for item in queryset.iterator(chunk_size=batch_size):
        try:
            parsed = json.loads(item.content)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        metadata = compute_metadata(parsed)
        item.display_level = metadata['display_level']

        if item.display_level == DisplayLevel.ALWAYS:
            # Level 1: close previous group if any
            if group_items:
                tail = group_items[-1].line_num
                _finalize_group(group_items, current_group_head, tail)
                items_to_update.extend(group_items)
                group_items = []
                current_group_head = None
            item.group_head = None
            item.group_tail = None

        elif item.display_level == DisplayLevel.COLLAPSIBLE:
            # Level 2: part of collapsible group
            if current_group_head is None:
                current_group_head = item.line_num
            group_items.append(item)
            continue  # Don't add to items_to_update yet, will be done in finalize

        else:
            # Level 3 (DEBUG_ONLY): no group info, but doesn't break the group
            item.group_head = None
            item.group_tail = None

        items_to_update.append(item)

        # Bulk update when batch is full
        if len(items_to_update) >= batch_size:
            SessionItem.objects.bulk_update(
                items_to_update,
                ['display_level', 'group_head', 'group_tail']
            )
            items_to_update = []

    # Close last group if session ends mid-group
    if group_items:
        tail = group_items[-1].line_num
        _finalize_group(group_items, current_group_head, tail)
        items_to_update.extend(group_items)

    # Final bulk update for remaining items
    if items_to_update:
        SessionItem.objects.bulk_update(
            items_to_update,
            ['display_level', 'group_head', 'group_tail']
        )

    # Mark session as computed with current version
    session.compute_version = settings.CURRENT_COMPUTE_VERSION
    session.save(update_fields=['compute_version'])

    # Close the connection to release any locks
    from django.db import connection
    connection.close()
