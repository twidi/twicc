# Session Items Metadata Propagation

## Overview

When new lines are added to a JSONL file and caught by the watcher, metadata is computed for the new items (kind, display_level, group_head, group_tail). However, the arrival of new items can affect the metadata of **previous items**, specifically their `group_tail` field.

Currently, only the new items are sent via WebSocket (`session_items_added`), but the modified existing items are not propagated to the frontend.

## Problem Statement

### Cases Where Previous Items Are Modified

1. **New COLLAPSIBLE joins existing group**
   - Previous COLLAPSIBLE items get their `group_tail` updated
   - ALWAYS item with suffix (that started the group) gets `group_tail` updated

2. **New ALWAYS with prefix joins existing group**
   - Previous COLLAPSIBLE items get their `group_tail` updated
   - ALWAYS item with suffix (that started the group) gets `group_tail` updated

### Example

```
BEFORE:
  Line 5: ALWAYS with suffix, group_tail=null (suffix treated as internal group)

AFTER (Line 6: COLLAPSIBLE arrives):
  Line 5: group_tail=6  ← MODIFIED in DB, but NOT sent to frontend
  Line 6: COLLAPSIBLE, group_head=5, group_tail=6  ← sent to frontend
```

## Solution

Enrich the `session_items_added` WebSocket message with metadata of existing items that were modified.

### Message Format

```json
{
    "type": "session_items_added",
    "session_id": "xxx",
    "project_id": "yyy",
    "items": [
        {"line_num": 6, "content": "...", "display_level": 2, "group_head": 5, "group_tail": 6, "kind": null}
    ],
    "updated_metadata": [
        {"line_num": 5, "display_level": 1, "group_head": null, "group_tail": 6, "kind": "assistant_message"}
    ]
}
```

- `items`: New items (full content + metadata) - existing behavior
- `updated_metadata`: Metadata-only for existing items that were modified (using `serialize_session_item_metadata`)

---

## Backend Changes

### 1. compute.py - Return Modified Items

Modify `compute_item_metadata_live()` to return the list of pre-existing items whose metadata was updated.

```python
def compute_item_metadata_live(session_id: str, item: SessionItem, content: str) -> list[int]:
    """
    Compute metadata for a single item during live sync.

    Unlike batch processing, this queries the database for context.

    Args:
        session_id: The session ID
        item: The SessionItem object (already has line_num and content set)
        content: The raw JSON content string

    Returns:
        List of line_nums of pre-existing items whose group_tail was updated
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
        return []

    # Track which pre-existing items were modified
    modified_line_nums = []

    # Find if there's an open group before us
    open_group_head = _find_open_group_head(session_id, item.line_num)

    if item.display_level == ItemDisplayLevel.COLLAPSIBLE:
        if open_group_head is not None:
            # Join existing group
            item.group_head = open_group_head
            item.group_tail = item.line_num

            # Get line_nums of items that will be updated (pre-existing only)
            affected_collapsibles = list(SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True))
            modified_line_nums.extend(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).first()
            if always_starter and open_group_head not in modified_line_nums:
                modified_line_nums.append(open_group_head)

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

            # Get line_nums of items that will be updated (pre-existing only)
            affected_collapsibles = list(SessionItem.objects.filter(
                session_id=session_id,
                group_head=open_group_head,
                line_num__lt=item.line_num
            ).values_list('line_num', flat=True))
            modified_line_nums.extend(affected_collapsibles)

            # Check if ALWAYS started this group
            always_starter = SessionItem.objects.filter(
                session_id=session_id,
                line_num=open_group_head,
                display_level=ItemDisplayLevel.ALWAYS
            ).first()
            if always_starter and open_group_head not in modified_line_nums:
                modified_line_nums.append(open_group_head)

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
```

### 2. sync.py - Accumulate and Merge Updates

Modify `sync_session_items()` to collect and merge modified item line numbers, then return them.

```python
def sync_session_items(session: Session, file_path: Path) -> tuple[int, list[int]]:
    """
    Synchronize session items from a JSONL file.

    Reads new lines from the file starting at last_offset.
    The session must already be saved to the database.

    Returns:
        Tuple of (new_items_count, modified_line_nums)
        - new_items_count: number of new items added
        - modified_line_nums: line numbers of pre-existing items whose metadata changed
    """
    if not file_path.exists():
        return 0, []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed, nothing to do
    if session.mtime == file_mtime:
        return 0, []

    new_items_count = 0
    # Track which pre-existing items were modified (set for uniqueness)
    modified_line_nums: set[int] = set()

    with open(file_path, "r", encoding="utf-8") as f:
        # Seek to last known position
        f.seek(session.last_offset)

        # Read remaining content
        new_content = f.read()
        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return 0, []

        # Split into lines (filter out empty lines)
        lines = [line for line in new_content.split("\n") if line.strip()]

        if lines:
            # Create SessionItem objects for bulk insert
            items_to_create = []
            current_line_num = session.last_line

            for line in lines:
                current_line_num += 1
                item = SessionItem(
                    session=session,
                    line_num=current_line_num,
                    content=line,
                )
                # Pre-compute display_level (no group info yet)
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    parsed = {}
                metadata = compute_item_metadata(parsed)
                item.display_level = metadata['display_level']
                item.kind = metadata['kind']
                items_to_create.append(item)

            # Bulk create all items
            SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
            new_items_count = len(items_to_create)

            # Second pass: compute group membership for COLLAPSIBLE and ALWAYS items
            for item in items_to_create:
                if item.display_level in (ItemDisplayLevel.COLLAPSIBLE, ItemDisplayLevel.ALWAYS):
                    modified = compute_item_metadata_live(session.id, item, item.content)
                    # Add to set (automatically handles uniqueness)
                    modified_line_nums.update(modified)
                    # Need to save the group info for the new item
                    SessionItem.objects.filter(
                        session=session,
                        line_num=item.line_num
                    ).update(
                        group_head=item.group_head,
                        group_tail=item.group_tail
                    )

            # Update session tracking fields
            session.last_line = current_line_num

        # Update offset to end of file
        session.last_offset = f.tell()
        session.mtime = file_mtime
        session.save(update_fields=["last_offset", "last_line", "mtime"])

    return new_items_count, list(modified_line_nums)
```

### 3. watcher.py - Propagate and Broadcast

Modify `sync_session_items_async()` and `sync_and_broadcast()` to propagate the modified line numbers.

```python
@sync_to_async
def sync_session_items_async(session: Session, file_path: Path) -> tuple[int, list[int]]:
    """Synchronize session items from a JSONL file (async wrapper).

    The session must already be saved to the database.

    Returns:
        Tuple of (new_items_count, modified_line_nums)
    """
    return sync_session_items(session, file_path)


@sync_to_async
def get_items_metadata(session: Session, line_nums: list[int]) -> list[dict]:
    """Get metadata (without content) for specific items."""
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).order_by("line_num")
    return [serialize_session_item_metadata(item) for item in items]
```

In `sync_and_broadcast()`, update the broadcast logic:

```python
# For new session case:
items_result, modified_line_nums = await sync_session_items_async(session, path)

if items_result > 0:
    # ... existing code ...

    # Broadcast new items
    new_items = await get_new_session_items(session, 0)
    if new_items:
        message = {
            "type": "session_items_added",
            "session_id": session_id,
            "project_id": project_id,
            "items": new_items,
        }
        # Add updated metadata if any pre-existing items were modified
        if modified_line_nums:
            updated_metadata = await get_items_metadata(session, modified_line_nums)
            if updated_metadata:
                message["updated_metadata"] = updated_metadata
        await broadcast_message(channel_layer, message)

# For existing session case:
last_line_before = session.last_line
items_result, modified_line_nums = await sync_session_items_async(session, path)

if items_result > 0:
    # ... existing code ...

    # Broadcast new items
    new_items = await get_new_session_items(session, last_line_before)
    if new_items:
        message = {
            "type": "session_items_added",
            "session_id": session_id,
            "project_id": project_id,
            "items": new_items,
        }
        # Add updated metadata if any pre-existing items were modified
        if modified_line_nums:
            updated_metadata = await get_items_metadata(session, modified_line_nums)
            if updated_metadata:
                message["updated_metadata"] = updated_metadata
        await broadcast_message(channel_layer, message)
```

### 4. serializers.py - Ensure serialize_session_item_metadata exists

This function should already exist:

```python
def serialize_session_item_metadata(item):
    """
    Serialize a SessionItem model to a dictionary WITHOUT content.
    """
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        "kind": item.kind,
    }
```

---

## Frontend Changes

### 1. stores/data.js - Handle Updated Metadata

Modify `addSessionItems()` to accept and apply metadata updates, including migrating internal expanded groups to external when an ALWAYS item acquires a `group_tail`.

```javascript
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility'

// In the store actions:

/**
 * Add or update session items in the array.
 * Items are placed at their correct index (line_num - 1).
 * If items arrive beyond current array size, extends with placeholders.
 * @param {string} sessionId
 * @param {Array<{line_num: number, content: string}>} newItems
 * @param {Array<{line_num: number, display_level: number, group_head: number|null, group_tail: number|null, kind: string|null}>} updatedMetadata - Optional metadata updates for existing items
 */
addSessionItems(sessionId, newItems, updatedMetadata = null) {
    if (!newItems?.length && !updatedMetadata?.length) return

    const items = this.sessionItems[sessionId]

    // First: apply metadata updates to existing items
    if (items && updatedMetadata?.length) {
        for (const update of updatedMetadata) {
            const index = update.line_num - 1
            const existingItem = items[index]

            if (existingItem) {
                // Check if an ALWAYS item is acquiring a group_tail (suffix becoming external)
                if (existingItem.kind === 'user_message' || existingItem.kind === 'assistant_message') {
                    const hadGroupTail = existingItem.group_tail != null
                    const willHaveGroupTail = update.group_tail != null
                    if (!hadGroupTail && willHaveGroupTail && existingItem.content) {
                        this._migrateInternalSuffixToExternal(sessionId, update.line_num, existingItem.content)
                    }
                }

                // Update all metadata fields
                existingItem.display_level = update.display_level
                existingItem.group_head = update.group_head
                existingItem.group_tail = update.group_tail
                existingItem.kind = update.kind
            }
        }
    }

    // Then: add new items
    if (newItems?.length) {
        if (!items) {
            // Not initialized yet - create array from the items we have
            const maxLineNum = Math.max(...newItems.map(item => item.line_num))
            this.sessionItems[sessionId] = Array.from(
                { length: maxLineNum },
                (_, index) => ({ line_num: index + 1 })
            )
        }

        const targetArray = this.sessionItems[sessionId]

        for (const item of newItems) {
            const index = item.line_num - 1

            // Extend array with placeholders if needed
            while (targetArray.length <= index) {
                targetArray.push({ line_num: targetArray.length + 1 })
            }

            // Place item at correct index
            targetArray[index] = item
        }
    }

    this.recomputeVisualItems(sessionId)
},

/**
 * Migrate expanded internal suffix group to external group.
 * Called when an ALWAYS item acquires a group_tail (suffix becomes external).
 *
 * When an ALWAYS item had its suffix treated as an internal group (group_tail was null)
 * and the user had expanded that internal suffix, we need to migrate that expansion
 * state to the external group system so the user still sees the content expanded.
 *
 * @param {string} sessionId
 * @param {number} lineNum - The line number of the ALWAYS item
 * @param {string} contentString - The raw JSON content of the item
 */
_migrateInternalSuffixToExternal(sessionId, lineNum, contentString) {
    // Parse the content JSON to extract message.content array
    let messageContent
    try {
        const parsed = JSON.parse(contentString)
        messageContent = parsed?.message?.content
    } catch {
        return
    }

    if (!Array.isArray(messageContent) || messageContent.length === 0) return

    // Use getPrefixSuffixBoundaries with group_tail=null (state BEFORE the update)
    // to find where the suffix was when it was still treated as internal
    const { suffixStartIndex } = getPrefixSuffixBoundaries(messageContent, null, null)

    if (suffixStartIndex === null) return  // No suffix exists

    // Check if this suffix was expanded as an internal group
    const internalGroups = this.localState.sessionInternalExpandedGroups[sessionId]
    if (!internalGroups?.[lineNum]) return

    const expandedIndexes = internalGroups[lineNum]
    if (!expandedIndexes.includes(suffixStartIndex)) return

    // Suffix was expanded internally → migrate to external group
    // Add lineNum to sessionExpandedGroups (the group_head for suffix group is the ALWAYS item's line_num)
    if (!this.localState.sessionExpandedGroups[sessionId]) {
        this.localState.sessionExpandedGroups[sessionId] = []
    }
    if (!this.localState.sessionExpandedGroups[sessionId].includes(lineNum)) {
        this.localState.sessionExpandedGroups[sessionId].push(lineNum)
    }

    // Remove suffixStartIndex from internal groups
    const idx = expandedIndexes.indexOf(suffixStartIndex)
    if (idx >= 0) {
        expandedIndexes.splice(idx, 1)
    }

    // Clean up if empty
    if (expandedIndexes.length === 0) {
        delete internalGroups[lineNum]
    }
}
```

### 2. composables/useWebSocket.js - Pass Updated Metadata

```javascript
case 'session_items_added':
    // Only add if we've fetched items for this session
    if (store.areSessionItemsFetched(msg.session_id)) {
        store.addSessionItems(msg.session_id, msg.items, msg.updated_metadata)
    }
    break
```

---

## Summary of Changes

### Backend Files

| File | Changes |
|------|---------|
| `compute.py` | `compute_item_metadata_live()` returns `list[int]` of modified line_nums |
| `sync.py` | `sync_session_items()` returns `tuple[int, list[int]]` with modified line_nums |
| `watcher.py` | `sync_session_items_async()` propagates tuple, `get_items_metadata()` helper, broadcast includes `updated_metadata` |
| `serializers.py` | Ensure `serialize_session_item_metadata()` exists (should already) |

### Frontend Files

| File | Changes |
|------|---------|
| `stores/data.js` | `addSessionItems()` accepts `updatedMetadata`, new `_migrateInternalSuffixToExternal()` helper |
| `composables/useWebSocket.js` | Pass `msg.updated_metadata` to `addSessionItems()` |

### No Changes Required

- `visualItems.js` - Logic already correct
- `contentVisibility.js` - Logic already correct
- Vue components - Already consume props correctly
