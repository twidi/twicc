# Specs: Partial Visibility for ALWAYS Items

> **Status**: Specification complete, implementation pending

## Context

The display levels system classifies entire messages (SessionItem) as ALWAYS, COLLAPSIBLE, or DEBUG_ONLY. However, a single message can contain mixed content:

```json
{
  "type": "user",
  "message": {
    "content": [
      { "type": "tool_result", ... },
      { "type": "text", "text": "Here's my question" },
      { "type": "tool_result", ... }
    ]
  }
}
```

In this example, the message is classified as ALWAYS (it has visible text), but it also contains `tool_result` items that should be collapsible.

## The Problem

Currently, if a message has ANY visible content (text, image, document), the ENTIRE message is shown in all modes. This exposes `tool_result`, `tool_use`, and `thinking` blocks that should be hidden by default.

### Content Types

**User messages** (`type: "user"`):
- Content can be a string (plain text) OR an array
- Array item types: `text`, `image`, `document`, `tool_result`

**Assistant messages** (`type: "assistant"`):
- Content is always an array
- Array item types: `text`, `thinking`, `tool_use`, `tool_result`

**Visible types** (ALWAYS shown): `text`, `image`, `document`
**Collapsible types**: Any type NOT in the visible list (the list of collapsible types is not fixed and may evolve)

## Solution Overview

### Key Insight

We don't need new database fields. We can reuse `group_head` and `group_tail` for ALWAYS items that have collapsible prefix/suffix that connects to adjacent groups.

### Terminology

For an ALWAYS item with array content:

- **Prefix**: collapsible elements at the START of content (before first visible element)
- **Suffix**: collapsible elements at the END of content (after last visible element)
- **Middle**: collapsible elements BETWEEN visible elements (handled at render time only)

```
content: [tool_result, tool_result, TEXT, tool_use, TEXT, tool_result, tool_result]
          └────── prefix ───────┘   │     middle    │    └────── suffix ───────┘
                                    └─── visible ───┘
```

## Core Rules

### Rule 1: COLLAPSIBLE Items

For a COLLAPSIBLE item:
- `group_head` = the head of the group it belongs to (itself if starting a new group, or the existing group's head if joining)
- `group_tail` = the tail of the group (itself initially, updated as group grows)

### Rule 2: ALWAYS Items - group_head

For an ALWAYS item, `group_head` is set **only if**:
1. The item has a collapsible prefix, AND
2. There is an open group before it (previous item was COLLAPSIBLE, or ALWAYS with suffix)

If the prefix is "orphan" (no preceding group), `group_head = null`.

### Rule 3: ALWAYS Items - group_tail

For an ALWAYS item, `group_tail` is set **only if**:
1. The item has a collapsible suffix, AND
2. A following item continues the group (next item is COLLAPSIBLE, or ALWAYS with prefix)

If the suffix is "orphan" (no following group), `group_tail = null`.

### Rule 4: Independence of group_head and group_tail

For ALWAYS items, `group_head` and `group_tail` are **completely independent**:
- `group_head` relates to the prefix and the group BEFORE
- `group_tail` relates to the suffix and the group AFTER
- They can reference DIFFERENT groups
- One can be set while the other is null

### Rule 5: ALWAYS Items Are Group Boundaries

An ALWAYS item always terminates any group it joins (via its prefix). A new group can only start from:
- A COLLAPSIBLE item
- An ALWAYS item's suffix (which becomes the head of the new group)

### Rule 6: Orphan Prefix/Suffix - No Database Marking

When a prefix or suffix doesn't connect to a group, we do NOT mark it in the database. The frontend detects orphans by analyzing the content and renders them as inline ellipses.

### Rule 7: DEBUG_ONLY Items Don't Affect Groups

DEBUG_ONLY items don't participate in groups and don't close them. They have `group_head = null` and `group_tail = null`.

## Exhaustive Case Analysis

Notation:
- `COLL` = COLLAPSIBLE item
- `ALWAYS` = ALWAYS item (with optional prefix `[p]` and/or suffix `[s]`)
- `gh` = group_head, `gt` = group_tail
- `null` = no value

---

### Case 1: A=COLL, B=COLL, C=COLL

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=A, gt=C`
- B: `gh=A, gt=C`
- C: `gh=A, gt=C`

→ One group A→C

---

### Case 2: A=COLL, B=COLL, C=ALWAYS (no prefix, no suffix)

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=A, gt=B` (unchanged, group terminated)
- B: `gh=A, gt=B` (unchanged)
- C: `gh=null, gt=null`

→ Group A→B, C isolated

---

### Case 3: A=COLL, B=COLL, C=ALWAYS[p] (prefix, no suffix)

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=A, gt=C`
- B: `gh=A, gt=C`
- C: `gh=A, gt=null` (prefix connected, no suffix)

→ Group A→C (includes prefix of C)

---

### Case 4: A=COLL, B=COLL, C=ALWAYS[s] (suffix, no prefix)

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=A, gt=B` (unchanged, group terminated because C has no prefix)
- B: `gh=A, gt=B` (unchanged)
- C: `gh=null, gt=null` (no prefix so no gh, suffix orphan so no gt)

→ Group A→B, C has orphan suffix (rendered inline)

---

### Case 5: A=COLL, B=COLL, C=ALWAYS[p,s] (prefix and suffix)

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=A, gt=C`
- B: `gh=A, gt=C`
- C: `gh=A, gt=null` (prefix connected, suffix orphan for now)

→ Group A→C (prefix of C), suffix of C orphan (will connect if D continues)

---

### Case 6: A=COLL, B=ALWAYS (no prefix, no suffix), C=COLL

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=A` (unchanged, group terminated)
- B: `gh=null, gt=null`

**After C arrives:**
- A: `gh=A, gt=A` (unchanged)
- B: `gh=null, gt=null`
- C: `gh=C, gt=C`

→ Group A alone, B isolated, group C→?

---

### Case 7: A=COLL, B=ALWAYS[p] (prefix, no suffix), C=COLL

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=null` (prefix connected, no suffix)

**After C arrives:**
- A: `gh=A, gt=B` (unchanged)
- B: `gh=A, gt=null` (unchanged)
- C: `gh=C, gt=C` (new group, B had no suffix)

→ Group A→B (includes prefix of B), C starts new group

---

### Case 8: A=COLL, B=ALWAYS[s] (suffix, no prefix), C=COLL

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=A` (unchanged, group terminated)
- B: `gh=null, gt=null` (no prefix, suffix orphan)

**After C arrives:**
- A: `gh=A, gt=A` (unchanged)
- B: `gh=null, gt=C` (suffix now connected to C)
- C: `gh=B, gt=C`

→ Group A alone, group B→C (suffix of B + C)

---

### Case 9: A=COLL, B=ALWAYS[p,s] (prefix and suffix), C=COLL

**After A arrives:**
- A: `gh=A, gt=A`

**After B arrives:**
- A: `gh=A, gt=B`
- B: `gh=A, gt=null` (prefix connected, suffix orphan)

**After C arrives:**
- A: `gh=A, gt=B` (unchanged)
- B: `gh=A, gt=C` (suffix now connected)
- C: `gh=B, gt=C`

→ Group A→B (prefix of B), group B→C (suffix of B + C)

---

### Case 10: A=ALWAYS[s] (suffix, no prefix), B=COLL, C=COLL

**After A arrives:**
- A: `gh=null, gt=null` (suffix orphan)

**After B arrives:**
- A: `gh=null, gt=B` (suffix now connected)
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=null, gt=C`
- B: `gh=A, gt=C`
- C: `gh=A, gt=C`

→ Group A→C (suffix of A + B + C)

---

### Case 11: A=ALWAYS[s], B=COLL, C=ALWAYS (no prefix)

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=null, gt=B` (unchanged)
- B: `gh=A, gt=B` (unchanged)
- C: `gh=null, gt=null`

→ Group A→B (suffix of A + B), C isolated

---

### Case 12: A=ALWAYS[s], B=COLL, C=ALWAYS[p] (prefix, no suffix)

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=B`
- B: `gh=A, gt=B`

**After C arrives:**
- A: `gh=null, gt=C`
- B: `gh=A, gt=C`
- C: `gh=A, gt=null` (prefix connected, no suffix)

→ Group A→C (suffix of A + B + prefix of C)

---

### Case 13: A=ALWAYS[s], B=ALWAYS[p] (prefix, no suffix), C=COLL

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=B` (suffix connected)
- B: `gh=A, gt=null` (prefix connected, no suffix)

**After C arrives:**
- A: `gh=null, gt=B` (unchanged)
- B: `gh=A, gt=null` (unchanged)
- C: `gh=C, gt=C` (new group)

→ Group A→B (suffix of A + prefix of B), C starts new group

---

### Case 14: A=ALWAYS[s], B=ALWAYS[p,s] (prefix and suffix), C=COLL

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=B`
- B: `gh=A, gt=null` (prefix connected, suffix orphan)

**After C arrives:**
- A: `gh=null, gt=B` (unchanged)
- B: `gh=A, gt=C` (suffix connected)
- C: `gh=B, gt=C`

→ Group A→B (suffix A + prefix B), group B→C (suffix B + C)

---

### Case 15: A=ALWAYS (no prefix, no suffix), B=COLL, C=COLL

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=null`
- B: `gh=B, gt=B`

**After C arrives:**
- A: `gh=null, gt=null`
- B: `gh=B, gt=C`
- C: `gh=B, gt=C`

→ A isolated, group B→C

---

### Case 16: A=ALWAYS (no suffix), B=ALWAYS[p] (orphan prefix, no suffix), C=COLL

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=null` (prefix orphan, no suffix)

**After C arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=null`
- C: `gh=C, gt=C`

→ A isolated, B isolated (orphan prefix rendered inline), C starts group

---

### Case 17: A=ALWAYS (no suffix), B=ALWAYS[s] (orphan suffix, no prefix), C=COLL

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=null` (no prefix, suffix orphan)

**After C arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=C` (suffix connected)
- C: `gh=B, gt=C`

→ A isolated, group B→C (suffix of B + C)

---

### Case 18: A=ALWAYS (no suffix), B=ALWAYS[s], C=ALWAYS (no prefix)

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=null` (suffix orphan)

**After C arrives:**
- A: `gh=null, gt=null`
- B: `gh=null, gt=null` (suffix stays orphan, C has no prefix)
- C: `gh=null, gt=null`

→ A isolated, B isolated (orphan suffix rendered inline), C isolated

---

### Case 19: A=ALWAYS[s], B=ALWAYS[p,s], C=ALWAYS[p] (prefix, no suffix)

**After A arrives:**
- A: `gh=null, gt=null`

**After B arrives:**
- A: `gh=null, gt=B`
- B: `gh=A, gt=null` (prefix connected, suffix orphan)

**After C arrives:**
- A: `gh=null, gt=B` (unchanged)
- B: `gh=A, gt=C` (suffix connected)
- C: `gh=B, gt=null` (prefix connected, no suffix)

→ Group A→B (suffix A + prefix B), group B→C (suffix B + prefix C)

---

## Summary of Rules

### For COLLAPSIBLE Items

| Field | Value |
|-------|-------|
| `group_head` | Head of the group (self if new, existing if joining) |
| `group_tail` | Tail of the group (self initially, updated as group grows) |

### For ALWAYS Items

| Field | Condition | Value |
|-------|-----------|-------|
| `group_head` | Has prefix AND preceding group exists | Head of the preceding group |
| `group_head` | Has prefix BUT no preceding group (orphan) | `null` |
| `group_head` | No prefix | `null` |
| `group_tail` | Has suffix AND following item continues group | Tail of the following group |
| `group_tail` | Has suffix BUT no following continuation (orphan) | `null` |
| `group_tail` | No suffix | `null` |

### Orphan Detection

A prefix/suffix is "orphan" when:
- **Orphan prefix**: Has prefix but `group_head = null`
- **Orphan suffix**: Has suffix but `group_tail = null`

The frontend detects orphans by:
1. Analyzing the content to find prefix/suffix
2. Checking if `group_head` / `group_tail` is null
3. Rendering inline ellipsis for orphans

---

## Backend Implementation

### Architecture

Two entry points share the same core logic:

1. **`compute_session_metadata`**: Processes entire sessions in batch (background task)
2. **`compute_item_metadata_live`**: Processes single items as they arrive (watcher)

The core group logic is centralized in a `GroupState` class that both entry points use.

### Helper Functions

```python
# In compute.py - already exists
VISIBLE_CONTENT_TYPES = ('text', 'document', 'image')


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
```

### Core Group Logic: `GroupState`

```python
from typing import Any, NamedTuple


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
            # group_tail is set by GroupState when group closes
            items_to_update.extend(info.closed_items)
        finalized = state.finalize()
        items_to_update.extend(finalized)
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
            ItemGroupInfo with group_head, group_tail, and closed_items
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

            # Connect to pending suffix if any
            if self._pending_suffix is not None:
                suffix_line, suffix_ref = self._pending_suffix
                self._group_items = [(suffix_line, suffix_ref)]
                self._group_head = suffix_line
                self._pending_suffix = None
            # Don't add current ALWAYS to _group_items - it joins but doesn't get group_tail

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

        # Close pending suffix if not joined by this item's prefix
        if self._pending_suffix is not None and not joined_via_prefix:
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
```

### Batch Processing: `compute_session_metadata`

```python
def compute_session_metadata(session_id: str) -> None:
    """Compute metadata for all items in a session."""
    from django.db import connection
    from twicc_poc.core.models import Session

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
```

### Live Processing: `compute_item_metadata_live`

```python
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
```

### Code to Remove

The function `_is_only_tool_result()` is no longer used and can be removed.

---

## Frontend Implementation (Logic Only)

### Virtual Scroller Constraint

Each SessionItem = one row in the virtual scroller. We cannot split an item into multiple rows.

### Ellipsis Placement

**For groups (multi-item)**:
- The ellipsis wraps the `group_head` item
- All other items in the group are hidden
- On expand: all items become visible rows

**For inline collapsibles (orphan prefix/suffix/middle)**:
- Within an ALWAYS item's render, the first sub-item of each collapsible sequence gets wrapped in an ellipsis
- This is handled at render time, not by the virtual scroller

### Render Logic for ALWAYS Items

When rendering an ALWAYS item (`kind` is `USER_MESSAGE` or `ASSISTANT_MESSAGE`):

1. **Analyze content array** to find collapsible sequences:
   - Prefix: `content[0..firstVisibleIndex-1]`
   - Middle: any collapsible items between visible items
   - Suffix: `content[lastVisibleIndex+1..end]`

2. **For prefix**:
   - If `group_head != null`: prefix is part of a group, render when that group is expanded
   - If `group_head == null` but has prefix: orphan prefix, render inline ellipsis

3. **For middle**: always render inline ellipsis on first collapsible sub-item of each sequence (in simplified mode)

4. **For suffix**:
   - If `group_tail != null`: suffix is part of a group, clicking the inline ellipsis expands the group
   - If `group_tail == null` but has suffix: orphan suffix, render inline ellipsis that only expands this suffix

### Click Behavior

**Group ellipsis** (on group_head item):
- Expands all items from `group_head` to `group_tail`
- This includes prefixes of ALWAYS items in the group

**Inline ellipsis with group_tail** (suffix starting a group):
- Expands items from this item to `group_tail`
- The suffix is rendered, plus any following COLLAPSIBLE items, plus any prefix of the tail item

**Inline ellipsis without group_tail** (orphan):
- Only expands the local collapsible content within this item
- No other items affected

---

## Version Increment

When implementing these changes, `CURRENT_COMPUTE_VERSION` in `settings.py` must be incremented to trigger recomputation of all sessions.

---

## Implementation Report

### Test Setup

**Configuration pytest-django** : La mise en place a été directe. Fichiers créés :
- `src/twicc_poc/settings_test.py` : Settings avec SQLite in-memory
- `tests/conftest.py` : Configuration pytest
- `pyproject.toml` : Ajout de `pytest` et `pytest-django` dans les dépendances optionnelles

Aucun problème particulier rencontré pour cette partie.

### Problèmes rencontrés lors de l'implémentation

#### Problème 1 : Items non persistés en batch mode

**Symptôme** : Après le premier run des tests, tous les tests batch échouaient (items avec `group_head=None, group_tail=None`) alors que les tests live passaient.

**Cause** : Dans `compute_session_metadata`, les items COLLAPSIBLE n'étaient jamais ajoutés à `items_to_update`. Ils étaient modifiés par `GroupState` (via `item_ref.group_tail = tail`) mais jamais inclus dans le `bulk_update`.

**Solution** : Ajout de `closed_items` dans `ItemGroupInfo`. Quand `GroupState` ferme un groupe, il retourne la liste des items modifiés. `compute_session_metadata` les ajoute alors à `items_to_update` :

```python
class ItemGroupInfo(NamedTuple):
    group_head: int | None
    group_tail: int | None
    closed_items: list[Any] = []  # Nouveau champ

# Dans compute_session_metadata :
items_to_update.extend(info.closed_items)
```

#### Problème 2 : group_tail incorrectement défini sur les items ALWAYS avec prefix

**Symptôme** : Les tests des cas 3, 5, 7, 12, 13, 19 échouaient. Un ALWAYS[p] (avec prefix) avait `group_tail` défini alors qu'il devrait être `None`.

**Cause** : Dans `_process_always`, quand un ALWAYS avec prefix rejoignait un groupe, il était ajouté à `_group_items`. Quand le groupe était fermé, tous les items (y compris l'ALWAYS) recevaient `group_tail`.

Mais selon les specs, un ALWAYS ne devrait avoir `group_tail` que si son **suffix** démarre un groupe qui se connecte plus tard. Le prefix permet de rejoindre un groupe, mais l'ALWAYS lui-même ne fait pas partie du groupe pour `group_tail`.

**Solution** : Ne pas ajouter l'item ALWAYS courant à `_group_items` quand il rejoint via son prefix :

```python
def _process_always(self, line_num, has_prefix, has_suffix, item_ref):
    joined_via_prefix = False

    if has_prefix and self.has_open_group():
        result_head = self.get_current_head()
        joined_via_prefix = True

        if self._pending_suffix is not None:
            # Connecte le pending suffix mais N'AJOUTE PAS l'ALWAYS courant
            suffix_line, suffix_ref = self._pending_suffix
            self._group_items = [(suffix_line, suffix_ref)]  # Sans (line_num, item_ref)
            ...
        # Ne pas ajouter l'ALWAYS aux _group_items
```

#### Problème 3 : Pending suffix non fermé quand ALWAYS sans prefix arrive

**Symptôme** : Après le fix du problème 2, certains tests échouaient encore car un pending suffix restait ouvert indéfiniment.

**Cause** : Quand un ALWAYS sans prefix arrivait, le pending suffix d'un ALWAYS précédent n'était pas fermé comme orphelin.

**Solution** : Ajout d'une condition pour fermer le pending suffix quand l'ALWAYS courant ne le rejoint pas :

```python
# Close pending suffix if not joined by this item's prefix
if self._pending_suffix is not None and not joined_via_prefix:
    suffix_line, suffix_ref = self._pending_suffix
    if suffix_ref is not None:
        closed_items.append(suffix_ref)  # Orphelin, group_tail reste None
    self._pending_suffix = None
```

### Leçons apprises

1. **Tester batch ET live séparément** : Les deux modes ont des logiques différentes (state machine vs queries DB). Un bug peut affecter l'un mais pas l'autre.

2. **Attention aux mutations silencieuses** : Modifier un objet via `item_ref.group_tail = x` ne suffit pas si l'objet n'est jamais persisté. Il faut tracker explicitement ce qui doit être sauvegardé.

3. **Distinguer "rejoindre un groupe" vs "faire partie du groupe"** : Un ALWAYS avec prefix rejoint conceptuellement un groupe (son prefix est affiché avec), mais l'item ALWAYS lui-même ne reçoit pas `group_tail` car ce champ est réservé à son suffix.

### Statistiques des tests

- **42 tests** au total
- **38 tests** pour les 19 cas documentés (batch + live)
- **4 tests** pour les items DEBUG_ONLY
- Temps d'exécution : ~0.27s

---

## Frontend Implementation

### Rappel du fonctionnement des groupes

Un groupe est défini par :
- `group_head` : numéro de ligne du premier item du groupe
- `group_tail` : numéro de ligne du dernier item du groupe

Tous les items d'un groupe partagent les mêmes valeurs `group_head` et `group_tail`.

### Principe actuel de l'ellipsis

Le concept fondamental : **le composant toggle est placé AVANT le premier élément d'un groupe collapsible**.

- Le `GroupToggle` est rendu **avant** le `group_head` (pas comme wrapper)
- Quand le groupe est collapsed, seul le toggle est visible (les items du groupe sont masqués)
- Quand le groupe est expanded, le toggle est affiché suivi de tous les items du groupe
- Les autres items du groupe ne sont pas rendus quand collapsed (filtrés par `visualItems`)

Ce même pattern s'applique aux groupes internes dans le contenu d'un ALWAYS : le toggle est placé **avant** le premier sous-élément du groupe, pas comme wrapper.

### Changements pour les ALWAYS avec prefix/suffix

#### Cas 1 : Le group_tail est un ALWAYS (groupe se terminant par un prefix)

Quand le groupe est masqué :
- Seul le `group_head` (COLLAPSIBLE) est affiché avec son ellipsis
- L'ALWAYS final n'affiche pas son prefix

Quand le groupe est étendu :
- Tous les items COLLAPSIBLE sont affichés
- L'ALWAYS affiche son **prefix** (qui était masqué)

#### Cas 2 : Un ALWAYS démarre un groupe avec son suffix

L'ALWAYS a `group_tail != null` qui pointe vers le dernier item du groupe (un COLLAPSIBLE, ou un autre ALWAYS avec prefix). L'ALWAYS lui-même n'a **pas** `group_head` pointant vers lui-même.

Les items COLLAPSIBLE qui suivent ont `group_head` pointant vers cet ALWAYS.

**Conséquence pour l'affichage :**
- L'ellipsis est **dans** le rendu de l'ALWAYS, sur le premier élément de son suffix
- Les COLLAPSIBLE suivants ne sont pas affichés (filtrés par `visualItems` car leur `group_head` pointe vers l'ALWAYS, pas vers eux-mêmes)

Exemple : Un ALWAYS (ligne 5) avec contenu `[text, tool_use, tool_result]`, suivi de COLLAPSIBLE (lignes 6, 7)
- L'ALWAYS a `group_head=null, group_tail=7`
- Les COLLAPSIBLE ont `group_head=5, group_tail=7`
- `text` est visible (affiché normalement)
- `tool_use` est le premier élément du suffix → **c'est lui qui est encapsulé dans l'ellipsis**
- `tool_result` fait partie du suffix → masqué
- Les COLLAPSIBLE 6 et 7 ne sont pas affichés

Au clic sur l'ellipsis :
- Le suffix de l'ALWAYS devient visible (`tool_use`, `tool_result`)
- Les COLLAPSIBLE 6 et 7 sont affichés
- Si le `group_tail` (7) est un ALWAYS avec prefix, son prefix est aussi affiché

#### Cas 3 : Deux groupes distincts (prefix et suffix sur le même ALWAYS)

Un ALWAYS peut avoir :
- `group_head` pointant vers un groupe précédent (son prefix y participe)
- `group_tail` pointant vers un groupe suivant (son suffix le démarre)

Ce sont deux groupes distincts avec deux ellipsis distincts :
- L'ellipsis du premier groupe est sur le `group_head` de ce groupe (un COLLAPSIBLE ou le suffix d'un ALWAYS précédent)
- L'ellipsis du second groupe est sur le premier élément du suffix de cet ALWAYS

### Détection des limites prefix/suffix

Le frontend doit déterminer où commence le suffix et où finit le prefix dans le contenu d'un ALWAYS.

#### Optimisation par les métadonnées

Avant de parcourir le tableau `content`, on peut utiliser les métadonnées :
- Si `group_head == null` → pas de prefix connecté (peut être orphelin)
- Si `group_tail == null` → pas de suffix connecté (peut être orphelin)

#### Fonction utilitaire

```javascript
const VISIBLE_CONTENT_TYPES = ['text', 'document', 'image']

/**
 * Determine prefix and suffix boundaries in an ALWAYS item's content.
 *
 * @param {Array} content - The content array from the message
 * @param {number|null} groupHead - The item's group_head metadata
 * @param {number|null} groupTail - The item's group_tail metadata
 * @returns {{ prefixEndIndex: number|null, suffixStartIndex: number|null }}
 *          - prefixEndIndex: last index of prefix (null if no prefix)
 *          - suffixStartIndex: first index of suffix (null if no suffix)
 */
function getPrefixSuffixBoundaries(content, groupHead, groupTail) {
  const hasPrefix = groupHead != null
  const hasSuffix = groupTail != null

  // Early exit if no prefix/suffix based on metadata
  if ((!hasPrefix && !hasSuffix) || !Array.isArray(content) || content.length === 0) {
    return { prefixEndIndex: null, suffixStartIndex: null }
  }

  let prefixEndIndex = null
  let suffixStartIndex = null

  if (hasPrefix) {
    // Find first visible element, prefix ends just before
    for (let i = 0; i < content.length; i++) {
      const item = content[i]
      if (typeof item === 'object' && VISIBLE_CONTENT_TYPES.includes(item.type)) {
        prefixEndIndex = i > 0 ? i - 1 : null
        break
      }
    }
  }

  if (hasSuffix) {
    // Find last visible element, suffix starts just after
    for (let i = content.length - 1; i >= 0; i--) {
      const item = content[i]
      if (typeof item === 'object' && VISIBLE_CONTENT_TYPES.includes(item.type)) {
        suffixStartIndex = i < content.length - 1 ? i + 1 : null
        break
      }
    }
  }

  return { prefixEndIndex, suffixStartIndex }
}
```

#### Exemples

| Content | groupHead | groupTail | Result |
|---------|-----------|-----------|--------|
| `[tool_use, text, tool_result]` | 5 | 10 | `{ prefixEndIndex: 0, suffixStartIndex: 2 }` |
| `[text, tool_use, tool_result]` | null | 10 | `{ prefixEndIndex: null, suffixStartIndex: 1 }` |
| `[tool_use, tool_result, text]` | 5 | null | `{ prefixEndIndex: 1, suffixStartIndex: null }` |
| `[text, image, text]` | null | null | `{ prefixEndIndex: null, suffixStartIndex: null }` |
| `[tool_use, text]` | null | null | `{ prefixEndIndex: null, suffixStartIndex: null }` (orphelins, traités inline) |

### Gestion des orphelins

Un prefix ou suffix est "orphelin" quand il existe dans le contenu mais n'est pas connecté à un groupe :
- **Orphan prefix** : premier élément est collapsible mais `group_head == null`
- **Orphan suffix** : dernier élément est collapsible mais `group_tail == null`

Les orphelins sont rendus avec une ellipsis **inline** qui n'affecte que le contenu local de l'item, sans impacter les autres items de la session.

### Modifications à apporter

#### 1. `visualItems.js`

Adapter la logique de filtrage pour :
- Quand le `group_head` est un ALWAYS, identifier que c'est le suffix qui démarre le groupe
- Inclure le calcul de `prefixEndIndex` et `suffixStartIndex` dans les visual items pour les ALWAYS

#### 2. Composant de rendu des messages (SessionItem.vue ou équivalent)

Pour les items ALWAYS (`user_message`, `assistant_message`) :
- Utiliser `getPrefixSuffixBoundaries()` pour déterminer les zones
- Encapsuler le premier élément du suffix dans l'ellipsis quand applicable
- Afficher/masquer le prefix selon l'état d'expansion du groupe auquel il appartient

#### 3. Gestion des groupes étendus

Le store `sessionExpandedGroups` doit continuer à fonctionner :
- Une entrée par `group_head`
- L'expansion d'un groupe dévoile tout de `group_head` à `group_tail`
- Cela inclut automatiquement les prefix/suffix des ALWAYS aux extrémités

### Comportement au clic

| Type d'ellipsis | Où | Action au clic |
|-----------------|-----|----------------|
| Group ellipsis | Sur le `group_head` (COLLAPSIBLE) | Affiche tous les items de `group_head` à `group_tail`, y compris les prefix des ALWAYS |
| Suffix ellipsis | Sur le premier élément du suffix d'un ALWAYS | Affiche le suffix + tous les items jusqu'au `group_tail` + le prefix du tail si c'est un ALWAYS |
| Orphan ellipsis | Sur un prefix/suffix non connecté | Affiche uniquement le contenu local (prefix ou suffix de cet item) |

### Groupes collapsibles internes

Un ALWAYS peut contenir des **séquences d'éléments non visibles** qui forment des "pseudo-groupes" collapsibles internes. Ces groupes sont gérés localement au sein de l'item ALWAYS, sans affecter les autres items de la session.

#### Règle de détection

Parcourir le contenu d'un ALWAYS et identifier toutes les séquences consécutives d'éléments non visibles, **en excluant** :
- Le prefix (indices 0 à N) si `group_head != null` (connecté à un groupe externe)
- Le suffix (indices N à fin) si `group_tail != null` (connecté à un groupe externe)

Les prefix/suffix orphelins (non connectés) sont traités comme des groupes internes.

#### Comportement

Pour chaque groupe interne :
- Le **premier élément** de la séquence est encapsulé dans un composant ellipsis
- Les autres éléments de la séquence sont masqués
- Au clic sur l'ellipsis, toute la séquence devient visible

#### Fonction utilitaire

```javascript
const VISIBLE_CONTENT_TYPES = ['text', 'document', 'image']

/**
 * Check if a content item is visible (not collapsible).
 *
 * @param {any} item - A content item
 * @returns {boolean} - True if visible
 */
function isVisibleItem(item) {
  return typeof item === 'object' && VISIBLE_CONTENT_TYPES.includes(item.type)
}

/**
 * Find all internal collapsible groups within an ALWAYS item's content.
 *
 * A group is a consecutive sequence of non-visible elements.
 * Prefix/suffix connected to external groups are excluded.
 *
 * @param {Array} content - The content array from the message
 * @param {number|null} groupHead - If not null, prefix is connected to external group (exclude it)
 * @param {number|null} groupTail - If not null, suffix is connected to external group (exclude it)
 * @returns {Array<{startIndex: number, endIndex: number}>} - List of internal collapsible groups
 */
function getInternalCollapsibleGroups(content, groupHead, groupTail) {
  if (!Array.isArray(content) || content.length === 0) {
    return []
  }

  // Determine the range to scan (exclude connected prefix/suffix)
  let scanStart = 0
  let scanEnd = content.length - 1

  // If prefix is connected to external group, find where it ends and skip it
  if (groupHead != null) {
    for (let i = 0; i < content.length; i++) {
      if (isVisibleItem(content[i])) {
        scanStart = i  // Start scanning from first visible element
        break
      }
    }
  }

  // If suffix is connected to external group, find where it starts and skip it
  if (groupTail != null) {
    for (let i = content.length - 1; i >= 0; i--) {
      if (isVisibleItem(content[i])) {
        scanEnd = i  // Stop scanning at last visible element
        break
      }
    }
  }

  // Scan the range and collect consecutive non-visible sequences
  const groups = []
  let currentGroup = null

  for (let i = scanStart; i <= scanEnd; i++) {
    const item = content[i]

    if (!isVisibleItem(item)) {
      // Non-visible: start or continue a group
      if (currentGroup === null) {
        currentGroup = { startIndex: i, endIndex: i }
      } else {
        currentGroup.endIndex = i
      }
    } else {
      // Visible: close current group if any
      if (currentGroup !== null) {
        groups.push(currentGroup)
        currentGroup = null
      }
    }
  }

  // Close last group if still open
  if (currentGroup !== null) {
    groups.push(currentGroup)
  }

  // Handle orphan prefix (not connected but exists)
  if (groupHead == null && scanStart === 0) {
    // Check if there's a prefix (first element is non-visible)
    if (!isVisibleItem(content[0])) {
      // Find where prefix ends
      let prefixEnd = 0
      for (let i = 0; i < content.length; i++) {
        if (isVisibleItem(content[i])) {
          prefixEnd = i - 1
          break
        }
        prefixEnd = i  // All non-visible
      }
      // Add as group if not already captured (edge case: entire content is non-visible)
      const alreadyCaptured = groups.some(g => g.startIndex === 0)
      if (!alreadyCaptured && prefixEnd >= 0) {
        groups.unshift({ startIndex: 0, endIndex: prefixEnd })
      }
    }
  }

  // Handle orphan suffix (not connected but exists)
  if (groupTail == null && scanEnd === content.length - 1) {
    // Check if there's a suffix (last element is non-visible)
    if (!isVisibleItem(content[content.length - 1])) {
      // Find where suffix starts
      let suffixStart = content.length - 1
      for (let i = content.length - 1; i >= 0; i--) {
        if (isVisibleItem(content[i])) {
          suffixStart = i + 1
          break
        }
        suffixStart = i  // All non-visible
      }
      // Add as group if not already captured
      const alreadyCaptured = groups.some(g => g.endIndex === content.length - 1)
      if (!alreadyCaptured && suffixStart < content.length) {
        groups.push({ startIndex: suffixStart, endIndex: content.length - 1 })
      }
    }
  }

  return groups
}
```

#### Exemples

**Cas 1 : Groupe interne au milieu**
```
content: [text, tool_use, tool_result, text]
groupHead: null, groupTail: null
```
→ Résultat : `[{ startIndex: 1, endIndex: 2 }]`
→ `tool_use` encapsulé dans ellipsis, `tool_result` masqué

**Cas 2 : Prefix et suffix orphelins**
```
content: [tool_result, tool_result, text, tool_use]
groupHead: null, groupTail: null
```
→ Résultat : `[{ startIndex: 0, endIndex: 1 }, { startIndex: 3, endIndex: 3 }]`
→ Deux groupes internes : le prefix orphelin et le suffix orphelin

**Cas 3 : Prefix connecté, middle et suffix orphelin**
```
content: [tool_result, text, tool_use, tool_use, text, tool_result]
groupHead: 5, groupTail: null
```
→ Résultat : `[{ startIndex: 2, endIndex: 3 }, { startIndex: 5, endIndex: 5 }]`
→ Prefix ignoré (connecté), middle et suffix orphelin sont des groupes internes

**Cas 4 : Tout connecté, seulement middle**
```
content: [tool_result, text, tool_use, text, tool_result]
groupHead: 5, groupTail: 10
```
→ Résultat : `[{ startIndex: 2, endIndex: 2 }]`
→ Prefix et suffix ignorés (connectés), seul le middle forme un groupe interne

**Cas 5 : Aucun groupe interne**
```
content: [text, image, text]
groupHead: null, groupTail: null
```
→ Résultat : `[]`
→ Tous les éléments sont visibles

---

## Pré-implémentation Frontend

### Architecture actuelle

**Fichiers concernés :**
- `frontend/src/utils/visualItems.js` : Calcul de la liste des items à afficher
- `frontend/src/views/SessionView.vue` : Rendu du virtual scroller avec les items
- `frontend/src/components/SessionItem.vue` : Rendu d'un item individuel (message)
- `frontend/src/components/GroupToggle.vue` : Composant toggle (ellipsis "...")
- `frontend/src/components/items/content/ContentList.vue` : Rendu des sous-éléments d'un message

**Flux actuel :**
1. `computeVisualItems()` filtre les items selon le mode et les groupes étendus
2. `SessionView` rend chaque visual item via le `DynamicScroller`
3. Pour un group head COLLAPSIBLE, `GroupToggle` est affiché **avant** l'item
4. `SessionItem` rend le contenu du message via `ContentList`

**Pattern du toggle :**
Le composant `GroupToggle` est placé **avant** le premier élément d'un groupe, pas comme wrapper. Ce même pattern s'applique à tous les niveaux (session et contenu interne).

### Changements nécessaires

#### 1. Nouveau fichier : `frontend/src/utils/contentVisibility.js`

Centralise les fonctions de détection des zones collapsibles dans le contenu d'un ALWAYS.

```javascript
// frontend/src/utils/contentVisibility.js

/**
 * Content types that are considered visible (always shown).
 */
export const VISIBLE_CONTENT_TYPES = ['text', 'document', 'image']

/**
 * Check if a content item is visible (not collapsible).
 */
export function isVisibleItem(item) {
  return typeof item === 'object' && item !== null && VISIBLE_CONTENT_TYPES.includes(item.type)
}

/**
 * Determine prefix and suffix boundaries in an ALWAYS item's content.
 */
export function getPrefixSuffixBoundaries(content, groupHead, groupTail) {
  // ... (code inchangé, voir section précédente)
}

/**
 * Find all internal collapsible groups within an ALWAYS item's content.
 */
export function getInternalCollapsibleGroups(content, groupHead, groupTail) {
  // ... (code inchangé, voir section précédente)
}
```

#### 2. Modification de `visualItems.js`

**Rappel des règles backend :**
- Un ALWAYS n'a **jamais** `group_head == line_num` ni `group_tail == line_num`
- `group_head` pointe vers un item **précédent** (ou null si orphelin)
- `group_tail` pointe vers un item **suivant** (ou null si orphelin)

```javascript
// frontend/src/utils/visualItems.js

export function computeVisualItems(items, mode, expandedGroups = []) {
    // ... (code inchangé, voir section précédente)
    // Ajoute prefixGroupHead, prefixExpanded, suffixGroupHead, suffixExpanded aux ALWAYS
}
```

#### 3. Modification de `ContentList.vue`

Intégrer `GroupToggle` pour les groupes collapsibles internes. Le toggle est placé **avant** le premier élément de chaque groupe.

```vue
<!-- frontend/src/components/items/content/ContentList.vue -->
<script setup>
import { computed, ref } from 'vue'
import { getInternalCollapsibleGroups, isVisibleItem } from '@/utils/contentVisibility'
import GroupToggle from '@/components/GroupToggle.vue'
import TextContent from './TextContent.vue'
import ImageContent from './ImageContent.vue'
import DocumentContent from './DocumentContent.vue'
import UnknownEntry from '../UnknownEntry.vue'

const props = defineProps({
    items: { type: Array, required: true },
    role: { type: String, required: true },
    // Props pour les groupes externes (prefix/suffix connectés)
    groupHead: { type: Number, default: null },
    groupTail: { type: Number, default: null },
    prefixExpanded: { type: Boolean, default: false },
    suffixExpanded: { type: Boolean, default: false }
})

const emit = defineEmits(['toggle-suffix'])

// État local pour les groupes internes (non persisté)
const expandedInternalGroups = ref(new Set())

// Calcul des groupes collapsibles internes
const internalGroups = computed(() => {
    return getInternalCollapsibleGroups(props.items, props.groupHead, props.groupTail)
})

// Map startIndex -> group pour lookup rapide
const groupByStartIndex = computed(() => {
    const map = new Map()
    for (const group of internalGroups.value) {
        map.set(group.startIndex, group)
    }
    return map
})

// Calcul de la visibilité de chaque élément
const visibleItems = computed(() => {
    const result = []

    for (let i = 0; i < props.items.length; i++) {
        const item = props.items[i]
        const isVisible = isVisibleItem(item)
        const group = groupByStartIndex.value.get(i)
        const isGroupStart = group != null

        // Déterminer si cet élément doit être affiché
        let show = false
        let showToggleBefore = false

        if (isVisible) {
            show = true
        } else {
            // Trouver le groupe auquel appartient cet élément
            const containingGroup = findGroupContaining(i)
            if (containingGroup) {
                const isExpanded = expandedInternalGroups.value.has(containingGroup.startIndex)
                show = isExpanded
                // Afficher le toggle AVANT le premier élément du groupe si collapsed
                showToggleBefore = isGroupStart && !isExpanded
            }
        }

        result.push({
            index: i,
            item,
            show,
            showToggleBefore,
            groupStartIndex: group?.startIndex
        })
    }

    return result
})

function findGroupContaining(index) {
    for (const group of internalGroups.value) {
        if (index >= group.startIndex && index <= group.endIndex) {
            return group
        }
    }
    return null
}

function toggleInternalGroup(startIndex) {
    if (expandedInternalGroups.value.has(startIndex)) {
        expandedInternalGroups.value.delete(startIndex)
    } else {
        expandedInternalGroups.value.add(startIndex)
    }
}
</script>

<template>
    <div class="content-list">
        <template v-for="entry in visibleItems" :key="entry.index">
            <!-- Toggle AVANT le premier élément d'un groupe collapsed -->
            <GroupToggle
                v-if="entry.showToggleBefore"
                :expanded="false"
                @toggle="toggleInternalGroup(entry.groupStartIndex)"
            />

            <!-- Contenu de l'élément -->
            <template v-if="entry.show">
                <TextContent
                    v-if="entry.item.type === 'text'"
                    :text="entry.item.text"
                    :role="role"
                />
                <ImageContent
                    v-else-if="entry.item.type === 'image'"
                    :source="entry.item.source"
                    :media-type="entry.item.media_type"
                    :role="role"
                />
                <DocumentContent
                    v-else-if="entry.item.type === 'document'"
                    :source="entry.item.source"
                    :media-type="entry.item.media_type"
                    :title="entry.item.title"
                    :role="role"
                />
                <UnknownEntry
                    v-else
                    :type="entry.item.type"
                    :data="entry.item"
                />
            </template>
        </template>
    </div>
</template>

<style scoped>
.content-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
</style>
```

#### 4. Modification de `SessionView.vue`

Passer les nouvelles props et gérer les toggles de groupes.

```vue
<!-- SessionView.vue - template (extrait) -->

<!-- Group head COLLAPSIBLE (collapsed): toggle AVANT, item masqué -->
<GroupToggle
    v-else-if="item.isGroupHead && !item.isExpanded"
    :expanded="false"
    @toggle="toggleGroup(item.lineNum)"
/>

<!-- Group head COLLAPSIBLE (expanded): toggle AVANT, puis item -->
<div v-else-if="item.isGroupHead && item.isExpanded" class="group-expanded">
    <GroupToggle :expanded="true" @toggle="toggleGroup(item.lineNum)" />
    <SessionItem ... />
</div>

<!-- ALWAYS ou item régulier -->
<SessionItem
    v-else
    :content="getItemContent(item.lineNum)"
    :kind="getItemKind(item.lineNum)"
    :line-num="item.lineNum"
    :group-head="getItemGroupHead(item.lineNum)"
    :group-tail="getItemGroupTail(item.lineNum)"
    :prefix-expanded="item.prefixExpanded || false"
    :suffix-expanded="item.suffixExpanded || false"
    @toggle-suffix="toggleGroup(item.suffixGroupHead)"
/>
```

### Résumé des fichiers à créer/modifier

| Fichier | Action | Description |
|---------|--------|-------------|
| `frontend/src/utils/contentVisibility.js` | Créer | Fonctions de détection prefix/suffix/groupes internes |
| `frontend/src/utils/visualItems.js` | Modifier | Ajouter infos prefix/suffix aux visual items ALWAYS |
| `frontend/src/components/items/content/ContentList.vue` | Modifier | Intégrer GroupToggle pour groupes internes |
| `frontend/src/components/SessionItem.vue` | Modifier | Passer props de groupe à ContentList |
| `frontend/src/views/SessionView.vue` | Modifier | Passer group_head/tail aux items |

### Points d'attention

1. **Un seul composant toggle** : `GroupToggle` est utilisé partout (niveau session et niveau contenu), placé **avant** le premier élément d'un groupe.

2. **État des groupes internes** : Les groupes internes ont un état local dans `ContentList`. Ils ne sont pas persistés dans le store.

3. **Pattern uniforme** : Que ce soit au niveau session ou au niveau contenu, le toggle est toujours un élément block affiché avant le groupe, jamais inline au milieu du texte.

---

## Rapport d'implémentation Frontend

### Problèmes rencontrés

#### Problème 1 : Alias `@/` non configuré dans Vite

**Symptôme** : Erreur `Failed to resolve import "@/utils/contentVisibility"` lors de la compilation.

**Cause** : L'alias `@` n'est pas configuré dans `vite.config.js`. Les imports comme `@/utils/...` ou `@/components/...` ne sont pas résolus.

**Solution** : Utiliser des imports relatifs au lieu des alias :
```javascript
// Avant (ne fonctionne pas)
import { getInternalCollapsibleGroups } from '@/utils/contentVisibility'
import GroupToggle from '@/components/GroupToggle.vue'

// Après (fonctionne)
import { getInternalCollapsibleGroups } from '../../../utils/contentVisibility'
import GroupToggle from '../../GroupToggle.vue'
```

#### Problème 2 : Propagation des props à travers plusieurs niveaux

**Constat** : La chaîne de composants `SessionView → SessionItem → UserMessage/AssistantMessage → ContentList` nécessite de propager les props de groupe à travers chaque niveau.

**Solution** : Ajouter les mêmes props (`groupHead`, `groupTail`, `prefixExpanded`, `suffixExpanded`) et l'event `toggle-suffix` à chaque composant intermédiaire.

Fichiers modifiés :
- `SessionItem.vue` : Ajout des props et event
- `UserMessage.vue` : Ajout des props et event, passage à ContentList
- `AssistantMessage.vue` : Ajout des props et event, passage à ContentList

### Fichiers créés/modifiés

| Fichier | Action | Lignes ajoutées/modifiées |
|---------|--------|---------------------------|
| `frontend/src/utils/contentVisibility.js` | Créé | ~150 lignes |
| `frontend/src/utils/visualItems.js` | Modifié | +50 lignes |
| `frontend/src/components/items/content/ContentList.vue` | Modifié | +100 lignes |
| `frontend/src/components/SessionItem.vue` | Modifié | +20 lignes |
| `frontend/src/components/items/UserMessage.vue` | Modifié | +25 lignes |
| `frontend/src/components/items/AssistantMessage.vue` | Modifié | +25 lignes |
| `frontend/src/views/SessionView.vue` | Modifié | +30 lignes |

### Tests manuels effectués

- Serveurs démarrés sans erreur
- Compilation frontend réussie
- API backend répond correctement
