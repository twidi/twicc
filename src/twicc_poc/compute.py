"""
Metadata computation for session items.

Provides functions to compute display level and group membership
for session items. Used by both the background task (full session)
and the watcher (single item).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from django.conf import settings

from twicc_poc.core.enums import ItemDisplayLevel, ItemKind
from twicc_poc.core.models import SessionItem

# Content types considered user-visible (for display_level and kind computation)
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')

if TYPE_CHECKING:
    from twicc_poc.core.models import Session

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions for Prefix/Suffix Detection
# =============================================================================


def _get_content_list(parsed_json: dict) -> list | None:
    """Extract the content list from a user or assistant message."""
    message = parsed_json.get('message', {})
    content = message.get('content')
    return content if isinstance(content, list) else None


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

    content = _get_content_list(parsed_json)
    if not content:
        return False, False

    return _has_collapsible_prefix(content), _has_collapsible_suffix(content)


def _has_visible_content(content: str | list) -> bool:
    """
    Check if message content contains user-visible content.

    User-visible content types are: text, document, image.

    Args:
        content: Message content (string or list of content items)

    Returns:
        True if content is a string or contains at least one visible content item
    """
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
    - COLLAPSIBLE (2): Meta messages, tool results, thinking/tool_use only,
                       summaries, file snapshots, custom titles
    - DEBUG_ONLY (3): System messages (except api_error), queue ops, progress

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

    entry_type = parsed_json.get('type')

    # DEBUG_ONLY: System messages (except api_error), queue operations, progress
    if entry_type == 'system':
        return ItemDisplayLevel.DEBUG_ONLY

    if entry_type in ('queue-operation', 'progress'):
        return ItemDisplayLevel.DEBUG_ONLY

    # Everything else is collapsible: meta messages, tool results, thinking/tool_use,
    # summaries, file snapshots, custom titles, etc.
    return ItemDisplayLevel.COLLAPSIBLE


def compute_item_kind(parsed_json: dict) -> ItemKind | None:
    """
    Determine the kind/category of an item based on its JSON content.

    Classification rules:
    - USER_MESSAGE: User messages with visible content (text, document, image), not meta
    - ASSISTANT_MESSAGE: Assistant messages with visible content (text, document, image)
    - API_ERROR: System messages with subtype 'api_error'

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        ItemKind enum value, or None if not a recognized kind

    Note:
        Any modification to this function's logic MUST increment
        CURRENT_COMPUTE_VERSION in settings.py to trigger recomputation.
    """
    entry_type = parsed_json.get('type')

    # API error
    if entry_type == 'system':
        subtype = parsed_json.get('subtype')
        if subtype == 'api_error':
            return ItemKind.API_ERROR
        return None

    # User messages
    if entry_type == 'user':
        # Meta messages are not user messages
        if parsed_json.get('isMeta') is True:
            return None

        message = parsed_json.get('message', {})
        content = message.get('content', [])

        # Only user messages with visible content count as USER_MESSAGE
        if _has_visible_content(content):
            return ItemKind.USER_MESSAGE

        return None

    # Assistant messages
    if entry_type == 'assistant':
        message = parsed_json.get('message', {})
        content = message.get('content', [])

        # Only assistant messages with visible content count as ASSISTANT_MESSAGE
        if _has_visible_content(content):
            return ItemKind.ASSISTANT_MESSAGE

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
# Batch Processing: compute_session_metadata
# =============================================================================


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
    connection.close()

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found for metadata computation")
        return

    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    state = GroupState()
    items_to_update: list[SessionItem] = []
    batch_size = 50

    for item in queryset.iterator(chunk_size=batch_size):
        try:
            parsed = json.loads(item.content)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in item {item.session_id}:{item.line_num}")
            parsed = {}

        # Compute display_level and kind
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

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

        # Bulk update when batch is full
        if len(items_to_update) >= batch_size:
            SessionItem.objects.bulk_update(
                items_to_update,
                ['display_level', 'group_head', 'group_tail', 'kind']
            )
            items_to_update = []

    # Finalize pending groups
    finalized = state.finalize()
    items_to_update.extend(finalized)

    # Final bulk update
    if items_to_update:
        SessionItem.objects.bulk_update(
            items_to_update,
            ['display_level', 'group_head', 'group_tail', 'kind']
        )

    session.compute_version = settings.CURRENT_COMPUTE_VERSION
    session.save(update_fields=['compute_version'])

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
            parsed = json.loads(previous.content)
            _, has_suffix = _detect_prefix_suffix(parsed, previous.kind)
            if has_suffix:
                return previous.line_num  # ALWAYS item is the head
        except json.JSONDecodeError:
            pass

    return None


def compute_item_metadata_live(session_id: str, item: SessionItem, content: str) -> None:
    """
    Compute metadata for a single item during live sync.

    Unlike batch processing, this queries the database for context.

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

    # Compute display_level and kind
    metadata = compute_item_metadata(parsed)
    item.display_level = metadata['display_level']
    item.kind = metadata['kind']

    # Initialize group fields
    item.group_head = None
    item.group_tail = None

    if item.display_level == ItemDisplayLevel.DEBUG_ONLY:
        return

    # Find if there's an open group before us
    open_group_head = _find_open_group_head(session_id, item.line_num)

    if item.display_level == ItemDisplayLevel.COLLAPSIBLE:
        if open_group_head is not None:
            # Join existing group
            item.group_head = open_group_head
            item.group_tail = item.line_num

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
