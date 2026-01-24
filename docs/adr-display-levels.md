# ADR: Display Levels and Collapsible Groups System

> **Implementation Note**: Code examples in this document are **pseudo-code illustrating logical approaches**. They are not production-ready and should be adapted during actual implementation. Do not copy them verbatim.

## Context

The application displays conversation items from JSONL files. Currently, all items are displayed as raw JSON (debug mode). The goal is to implement a system where:

1. Items can be filtered based on their importance/type
2. Users can choose a display granularity level
3. Filtered items can be grouped and revealed on demand (collapsible groups)

## The Core Problem

### Virtual Scroller and Indexing Mismatch

The application uses a virtual scroller for performance (lazy loading of conversation items). The virtual scroller works on indices: "show me items 20-30".

**The challenge**: We want to filter items based on their content, but we can only know if an item should be displayed *after* fetching and parsing its JSON content.

This creates an indexing mismatch:
- **Raw items space**: N items (indices 0 to N-1), includes items that won't be displayed
- **Visual items space**: X items (X < N), only what the user sees

The virtual scroller asks for "visual items 5-10", but we don't know which raw items correspond to visual items 5-10 until we've fetched and analyzed all preceding items.

### Additional Constraints

1. **Backend stores raw JSON**: The backend doesn't parse JSON, it stores it as a string. Parsing on every request would be expensive.

2. **Filtering logic may evolve**: Display rules will change over time. Any pre-computed data must handle version changes.

3. **Volume**: Conversations can have thousands of items, some weighing several MB.

4. **Cost calculation needed**: Future feature requires parsing all items anyway (to extract cost data).

5. **Dynamic filtering**: User can change display mode at any time (no server round-trip for mode changes).

## Alternatives Considered

### 1. Fetch Everything, Filter Client-Side

Fetch all items at session load, filter in the frontend.

- ✅ Simple: virtual scroller works on the filtered list
- ✅ Mode changes are instant (no API call)
- ❌ Heavy initial load (thousands of potentially large items)
- ❌ Memory issues with large sessions

**Verdict**: Rejected for performance reasons.

### 2. Double Indexing with Mapping

Maintain `visualIndex → rawIndex` mapping.

- ✅ Virtual scroller works normally on visual indices
- ❌ Mapping can only be built after fetching all items
- ❌ Complex mapping maintenance on filter changes

**Verdict**: Rejected - doesn't solve the core problem.

### 3. Height 0 for Hidden Items

Virtual scroller manages all N items. Hidden items render with height 0.

- ✅ Simple: index = line_num - 1
- ✅ On-demand fetching works naturally
- ❌ Virtual scrollers don't support height 0 items well (confirmed by research)
- ❌ Scroller does NOT automatically request more items to fill viewport
- ❌ Can cause crashes or render all DOM nodes in edge cases

**Verdict**: Rejected - virtual scrollers explicitly don't support zero-height items.

### 4. Progressive Fetch with Dynamic Mapping

Build the mapping incrementally as items are fetched.

- ✅ Progressive loading
- ❌ High complexity
- ❌ Virtual scroller must support unknown/growing size
- ❌ Scrollbar size changes as items are discovered

**Verdict**: Rejected for complexity.

### 5. Statistical Estimation

Estimate ratio of visible items, adjust dynamically.

- ✅ On-demand fetching
- ❌ Approximate: visual jumps when estimates are corrected
- ❌ Ratio varies significantly across sessions/filters

**Verdict**: Rejected for poor UX.

### 6. Server-Side Filtering

Server applies filter, returns only matching items.

- ✅ Virtual scroller works perfectly
- ❌ Duplicated logic (server + client for UI display)
- ❌ Network request on every filter change

**Verdict**: Rejected - want filter logic client-side only.

### 7. Lightweight Metadata Fetch

Fetch only filtering metadata initially, full content on demand.

- ✅ Light initial fetch
- ✅ Instant client-side filtering
- ✅ Virtual scroller works on filtered list
- ⚠️ Requires initial fetch of N metadata items (acceptable size)

**Verdict**: Chosen solution.

## Chosen Solution: Pre-computed Display Levels with Metadata-First Loading

### Principle

1. **Sync unchanged**: Initial and live sync store raw JSON (no parsing during sync)
2. **Background task**: Computes display levels, group bounds, costs for each session
3. **Versioning**: `compute_version` on sessions tracks rule changes
4. **Metadata-first loading**: Frontend fetches all item metadata (without content) on session load
5. **Visual index mapping**: Frontend computes which items to show based on mode and expanded groups
6. **Content on demand**: Full item content fetched only when needed by virtual scroller

### Three Display Modes

| Mode | Description |
|------|-------------|
| **Debug** | Everything displayed (development/troubleshooting) |
| **Normal** | Standard view, some items never shown (no groups) |
| **Simplified** | Priority items shown, others grouped under collapsible toggles |

**Default mode**: Normal

**UI**: Simple selector in the session header (implementation details left to the developer).

**Storage**: Global state in the store's `localState` (not per-session). The mode applies to whichever session is currently viewed. Not persisted to localStorage.

### Display Levels

| Level | Name | Debug Mode | Normal Mode | Simplified Mode |
|-------|------|------------|-------------|-----------------|
| 1 | Always | ✅ Shown | ✅ Shown | ✅ Shown |
| 2 | Collapsible | ✅ Shown | ✅ Shown | 🔽 In collapsible group |
| 3 | Debug Only | ✅ Shown | ❌ Hidden | ❌ Hidden |

### Collapsible Groups

In **Simplified mode**, consecutive items with `display_level > 1` form a group:

```
Item 21: level 1 (always)      → Shown            group_head=null, group_tail=null
Item 22: level 2 (collapsible) → Group head ───┐  group_head=22, group_tail=26
Item 23: level 2 (collapsible) → Hidden       │   group_head=22, group_tail=26
Item 24: level 3 (debug only)  → Hidden       │   group_head=null, group_tail=null  ← NO group info
Item 25: level 2 (collapsible) → Hidden       │   group_head=22, group_tail=26
Item 26: level 2 (collapsible) → Group tail ──┘   group_head=22, group_tail=26
Item 27: level 1 (always)      → Shown            group_head=null, group_tail=null
```

**Key rules**:
- A group ends only when a level 1 item is encountered
- Debug-only items (level 3) within a group **don't break it**, but they **don't have `group_head`/`group_tail` set**
- Only level 2 items have group bounds stored (since level 3 items are never shown in Simplified mode anyway)
- The group head is the first level 2 item, the tail is the last level 2 item (level 3 items in between are ignored for bounds)

### Database Schema Changes

#### Session Model

```python
class Session(models.Model):
    # ... existing fields ...
    compute_version = models.PositiveIntegerField(null=True)  # NULL = never computed
```

#### SessionItem Model

```python
from enum import IntEnum

class DisplayLevel(IntEnum):
    ALWAYS = 1       # Always shown in all modes
    COLLAPSIBLE = 2  # Shown in Normal, grouped in Simplified
    DEBUG_ONLY = 3   # Only shown in Debug mode

class SessionItem(models.Model):
    # ... existing fields ...
    display_level = models.PositiveSmallIntegerField(null=True)  # DisplayLevel enum value
    group_head = models.PositiveIntegerField(null=True)  # line_num of group start (level 2 only)
    group_tail = models.PositiveIntegerField(null=True)  # line_num of group end (level 2 only)
    # Future: cost = models.DecimalField(null=True)
    # Future: component_type = models.CharField(null=True)
```

**Important**: `group_head` and `group_tail` are only set for level 2 (COLLAPSIBLE) items. Level 3 (DEBUG_ONLY) items within a group don't have these fields set because they are never displayed when groups are shown (Simplified mode hides them entirely).

### Background Computation Task

> **Note**: The code examples below are **pseudo-code illustrating the logic**, not production-ready implementations. They should be adapted during actual implementation.

The computation is organized as a modular function that calls specialized sub-functions:

```python
CURRENT_COMPUTE_VERSION = 1

def compute_metadata(parsed_json):
    """
    Main entry point for computing all metadata fields from parsed JSON.
    Calls specialized functions for each computed field.
    Returns a dict with all computed metadata.
    """
    return {
        'display_level': compute_display_level(parsed_json),
        # Future: 'cost': compute_cost(parsed_json),
        # Future: 'component_type': compute_component_type(parsed_json),
    }

def compute_display_level(parsed_json):
    """
    Determine the display level for an item.
    For now, returns ALWAYS (1) for all items.
    Will be refined as display rules are defined.
    """
    return DisplayLevel.ALWAYS  # TODO: implement actual logic
```

#### Session Processing (Batch-based)

Sessions can have thousands of items, some very large. Loading all at once is not feasible. Use Django's `iterator()` with `chunk_size` to process in batches:

```python
# Pseudo-code for batch processing
def compute_session_metadata(session):
    queryset = SessionItem.objects.filter(session=session).order_by('line_num')

    current_group_head = None
    group_items = []  # Buffer for current group

    # Process items in batches without loading all into memory
    for item in queryset.iterator(chunk_size=500):
        parsed = json.loads(item.content)
        metadata = compute_metadata(parsed)
        item.display_level = metadata['display_level']

        if item.display_level == DisplayLevel.ALWAYS:
            # Close previous group if any
            if group_items:
                finalize_group(group_items, current_group_head)
                group_items = []
                current_group_head = None
            item.group_head = None
            item.group_tail = None
        elif item.display_level == DisplayLevel.COLLAPSIBLE:
            # Level 2: part of collapsible group
            if current_group_head is None:
                current_group_head = item.line_num
            group_items.append(item)
        else:
            # Level 3 (DEBUG_ONLY): no group info, but doesn't break the group
            item.group_head = None
            item.group_tail = None

        item.save()  # Or batch with bulk_update

    # Close last group
    if group_items:
        finalize_group(group_items, current_group_head)

    session.compute_version = CURRENT_COMPUTE_VERSION
    session.save()

def finalize_group(group_items, head_line_num):
    """Set group_head and group_tail for all items in the group."""
    tail_line_num = group_items[-1].line_num
    for item in group_items:
        item.group_head = head_line_num
        item.group_tail = tail_line_num
        item.save()  # Or batch
```

**Key point about groups**: When consecutive items have levels 2, 3, 2, 2, 3, 2, they form ONE group. The level 3 items are "inside" the group but don't have `group_head`/`group_tail` set because they're never displayed in Simplified mode. The group spans from the first level 2 item to the last level 2 item, regardless of level 3 items in between.

### Task Scheduling

The background task runs continuously:

```python
# Pseudo-code for background task loop
async def background_compute_task():
    while True:
        # Find next session needing computation (most recent first)
        # Includes NULL (never computed) and outdated versions
        session = Session.objects.exclude(
            compute_version=CURRENT_COMPUTE_VERSION
        ).order_by('-mtime').first()

        if session is None:
            # Nothing to compute, wait before checking again
            await asyncio.sleep(1)
            continue

        # Process this session
        await compute_session_metadata(session)

        # Notify frontend via WebSocket (same mechanism as watcher)
        await broadcast_session_updated(session)
```

**Scheduling**:
- Starts after the initial sync task completes (at app startup)
- Runs as an asyncio task (same pattern as the file watcher)
- Processes sessions ordered by `mtime` descending (most recent first)
- Sessions with `compute_version IS NULL` or `!= CURRENT_COMPUTE_VERSION` are processed
- User can only open a session once its computation is complete

**Notification**: After computing a session, the task broadcasts a `session_updated` WebSocket message (same mechanism as the watcher uses for file changes).

**Configuration**: `CURRENT_COMPUTE_VERSION` is defined in Django settings.

**CPU-intensive work**: The computation runs in a `ProcessPoolExecutor` to avoid blocking the asyncio event loop:

```python
# Pseudo-code
from concurrent.futures import ProcessPoolExecutor

executor = ProcessPoolExecutor(max_workers=1)

async def background_compute_task():
    loop = asyncio.get_event_loop()
    while True:
        session = get_next_session_to_compute()
        if session is None:
            await asyncio.sleep(1)
            continue

        # Run CPU-intensive work in separate process
        await loop.run_in_executor(executor, compute_session_metadata, session.id)

        # Back in main process: broadcast update
        await broadcast_session_updated(session)
```

**Graceful shutdown**: The executor must be shut down when the app exits (Ctrl+C). Use `executor.shutdown(wait=False)` for immediate stop. The interrupted computation is not a problem: `compute_version` is only updated at the end, so the session will be reprocessed on next startup. SQLite handles writes atomically.

### Live Sync (Watcher)

When new lines arrive via the file watcher, we compute metadata immediately:

> **Note**: This is **pseudo-code illustrating the logic**. Adapt during implementation.

```python
def on_new_line(session, line_content, line_num):
    parsed = json.loads(line_content)
    metadata = compute_metadata(parsed)  # Same function as background task

    item = SessionItem(
        session=session,
        line_num=line_num,
        content=line_content,
        display_level=metadata['display_level'],
    )

    # Get previous item to determine group membership
    previous = SessionItem.objects.filter(
        session=session,
        line_num=line_num - 1
    ).first()

    if item.display_level == DisplayLevel.ALWAYS:
        item.group_head = None
        item.group_tail = None
    elif item.display_level == DisplayLevel.COLLAPSIBLE:
        # Check if previous item was part of a group (level 2 or 3)
        if previous and previous.display_level in (DisplayLevel.COLLAPSIBLE, DisplayLevel.DEBUG_ONLY):
            # Continue existing group - find the actual group_head
            # Previous might be level 3 (no group_head), so look for nearest level 2
            existing_head = find_current_group_head(session, line_num - 1)
            if existing_head:
                item.group_head = existing_head
                item.group_tail = item.line_num
                # Update tail for all level 2 items in group
                SessionItem.objects.filter(
                    session=session,
                    group_head=existing_head
                ).update(group_tail=item.line_num)
            else:
                # Start new group
                item.group_head = item.line_num
                item.group_tail = item.line_num
        else:
            # Start new group
            item.group_head = item.line_num
            item.group_tail = item.line_num
    else:
        # Level 3 (DEBUG_ONLY): no group info
        item.group_head = None
        item.group_tail = None

    item.save()
```

**Note**: When a level 2 item arrives after level 3 items (which don't break groups), we need to find the `group_head` of the current open group by looking back at previous level 2 items.

### Serialization

Two serialization functions are needed:

1. **Full serialization** (`serialize_item`): Returns all fields including `content`. Used for the items endpoint and WebSocket messages.
2. **Metadata serialization** (`serialize_item_metadata`): Returns all fields EXCEPT `content`. Used for the metadata endpoint.

```python
# Pseudo-code
def serialize_item(item):
    return {
        'line_num': item.line_num,
        'display_level': item.display_level,
        'group_head': item.group_head,
        'group_tail': item.group_tail,
        'content': item.content,
    }

def serialize_item_metadata(item):
    return {
        'line_num': item.line_num,
        'display_level': item.display_level,
        'group_head': item.group_head,
        'group_tail': item.group_tail,
        # NO content field
    }
```

**WebSocket item messages**: Use `serialize_item` (full serialization with all metadata fields). The frontend receives `display_level`, `group_head`, `group_tail` along with `content`.

### API Endpoints

#### GET /api/.../items/metadata/

Returns metadata for ALL items in a session (without content). Uses `serialize_item_metadata`:

```json
[
  { "line_num": 1, "display_level": 1, "group_head": null, "group_tail": null },
  { "line_num": 2, "display_level": 2, "group_head": 2, "group_tail": 5 },
  { "line_num": 3, "display_level": 2, "group_head": 2, "group_tail": 5 },
  ...
]
```

Estimated size: ~50 bytes per item → 250KB for 5000 items (acceptable).

#### GET /api/.../items/?range=...

Returns full items (with content) for requested ranges. Uses `serialize_item`. Automatically extends ranges to include complete groups.

### Frontend Architecture

#### Data Structures

```javascript
// Store: sessionItems - source of truth, indexed by line_num
sessionItems: {
  [sessionId]: [
    {
      line_num: 1,
      display_level: 1,
      group_head: null,
      group_tail: null,
      content: null  // fetched on demand
    },
    {
      line_num: 2,
      display_level: 2,
      group_head: 2,
      group_tail: 5,
      content: "{...}"  // fetched
    },
    ...
  ]
}

// Local UI state (in store.localState)
sessionItemsDisplayMode: 'normal',  // 'debug' | 'normal' | 'simplified' - global, not per-session

sessionExpandedGroups: {
  [sessionId]: Set([2, 17, ...])  // Set of group_head line_nums that are expanded
}

// Computed: sessionVisualItems - what the virtual scroller receives
sessionVisualItems: {
  [sessionId]: [
    { visualIndex: 0, lineNum: 1 },
    { visualIndex: 1, lineNum: 2, isGroupHead: true, isExpanded: false },
    { visualIndex: 2, lineNum: 6 },
    ...
  ]
}
```

**Notes on sessionExpandedGroups**:
- Stored per session (user may have different groups expanded in different sessions)
- Kept in store memory (survives navigation between sessions within the same app session)
- NOT persisted to localStorage (resets on page reload)
- When `sessionVisualItems` is recomputed, `sessionExpandedGroups` is read to determine `isExpanded` for each group head

**Accessing sessionItems by lineNum**: `sessionItems` is an array where index 0 corresponds to `line_num: 1`. To get an item by its `lineNum`, use `sessionItems[lineNum - 1]`. Consider creating a helper/getter to encapsulate this offset.

#### Visual Items Computation

```javascript
function computeSessionVisualItems(items, mode, sessionExpandedGroups) {
  const result = []
  let visualIndex = 0

  for (const item of items) {
    if (mode === 'debug') {
      result.push({ visualIndex: visualIndex++, lineNum: item.line_num })
      continue
    }

    if (mode === 'normal') {
      if (item.display_level !== 3) {
        result.push({ visualIndex: visualIndex++, lineNum: item.line_num })
      }
      // level 3: skip entirely
      continue
    }

    // mode === 'simplified'
    if (item.display_level === 1) {
      result.push({ visualIndex: visualIndex++, lineNum: item.line_num })
    } else {
      // Level 2 or 3
      const isHead = item.line_num === item.group_head
      const isExpanded = sessionExpandedGroups.has(item.group_head)

      if (isHead) {
        // Head always has a visual index (shows at least the toggle)
        result.push({
          visualIndex: visualIndex++,
          lineNum: item.line_num,
          isGroupHead: true,
          isExpanded: isExpanded
        })
      } else if (isExpanded && item.display_level !== 3) {
        // Group member, expanded, not debug-only
        result.push({ visualIndex: visualIndex++, lineNum: item.line_num })
      }
      // Otherwise: no visual index (hidden)
    }
  }

  return result
}
```

Complexity: O(N) for N items. Instant for 5000 items.

Recalculated when:
- New item arrives (via WebSocket)
- User changes display mode
- User toggles a group

#### Virtual Scroller Integration

```vue
<DynamicScroller
  :items="sessionVisualItems[sessionId]"
  key-field="lineNum"
  :min-item-size="80"
>
  <template #default="{ item, active }">
    <DynamicScrollerItem :item="item" :active="active">
      <div class="item-wrapper">
        <!-- Group toggle if this is a head -->
        <GroupToggle
          v-if="item.isGroupHead"
          :expanded="item.isExpanded"
          @toggle="toggleGroup(item.lineNum)"
        />
        <!-- Item content (if not head OR if expanded) -->
        <SessionItem
          v-if="!item.isGroupHead || item.isExpanded"
          :data="sessionItems[sessionId][item.lineNum - 1]"
        />
      </div>
    </DynamicScrollerItem>
  </template>
</DynamicScroller>
```

**Key decisions**:
- `key-field="lineNum"`: Stable key for Vue diffing. `lineNum` never changes for an item, while `visualIndex` shifts on every expand/collapse.
- Wrapper div contains both group toggle and item content
- `GroupToggle` is a separate small component. **Initial implementation**: simple ellipsis ("..."). Future improvements (item count, etc.) can be added later.
- `SessionItem` stays "pure" (doesn't know about group system)

#### Session Loading Flow

**Two blocking states with different UI:**

1. **Compute not ready** (`compute_version_up_to_date === false`):
   - Show a **callout** (like error state): "Session pas encore disponible, calcul en cours..."
   - No spinner, no retry button (will auto-update via WebSocket)
   - Wait for `session_updated` message with `compute_version_up_to_date: true`

2. **Fetching data** (compute ready, loading metadata + initial items):
   - Show **loading spinner** (existing behavior)
   - Both fetches happen in parallel

**Flow when user clicks on a session:**

1. Check `session.compute_version_up_to_date`
   - If `false`: show callout "Calcul en cours...", stop here, wait for WebSocket update
2. **In parallel** (Promise.all):
   - Fetch all metadata: `GET /api/projects/{projectId}/sessions/{sessionId}/items/metadata/`
   - Fetch initial content: `GET /api/projects/{projectId}/sessions/{sessionId}/items/?range=1:20&range=N-19:N`
3. When BOTH complete:
   - Store metadata in `sessionItems` (all items, with `content: null`)
   - Update `sessionItems` entries with fetched content
   - Compute `sessionVisualItems` based on current mode
4. Virtual scroller renders, requests more content as user scrolls

**Error handling**: If either fetch fails, show the existing error panel with retry button. On retry, both fetches are retried.

#### Session Serialization

The session serializer includes a computed boolean field:

```python
def serialize_session(session):
    return {
        # ... existing fields ...
        'compute_version_up_to_date': session.compute_version == CURRENT_COMPUTE_VERSION,
    }
```

The frontend doesn't need to know the actual `compute_version` value or `CURRENT_COMPUTE_VERSION`. It just needs to know if the session is ready or not.

#### WebSocket: session_updated

When the background task finishes computing a session, it triggers a `session_updated` WebSocket message (existing mechanism). The serialized session now includes `compute_version_up_to_date: true`.

**Frontend handling of `session_updated`:**

1. Update session in store (as usual)
2. If this is the currently displayed session AND we were blocked on compute:
   - `compute_version_up_to_date` changed from `false` to `true`
   - Trigger the normal session loading flow (parallel fetch of metadata + initial items)

#### WebSocket: New Item

When a new item arrives:
1. Parse the full item data (metadata + content, including `display_level`, `group_head`, `group_tail`)
2. Add to `sessionItems`
3. Recompute `sessionVisualItems`
4. Virtual scroller automatically updates

**Behavior with expanded groups**: If a new item belongs to an expanded group (same `group_head` as an already expanded group), it will automatically appear in the list since the group is expanded.

#### Critical Rule: sessionItems and sessionVisualItems Synchronization

**`sessionVisualItems` must ALWAYS be recomputed when `sessionItems` changes.**

This includes:
- Initial metadata fetch
- Content fetch (items updated with content)
- New item via WebSocket
- Any modification to the items array

The two structures must stay in sync. Never update `sessionItems` without immediately recomputing `sessionVisualItems` afterward.

## Why Height 0 Doesn't Work

Research on `vue-virtual-scroller` and other virtual scrolling libraries revealed:

1. **Zero-height items are explicitly unsupported** ([react-virtuoso discussion](https://github.com/petyosi/react-virtuoso/discussions/481))
2. **Scroller does NOT automatically fill viewport** - it calculates visible range based on scroll position + buffer, then stops
3. **Edge cases cause crashes** - can render all DOM nodes or throw "Rendered items limit reached" errors ([ngx-virtual-scroller issue](https://github.com/rintoj/ngx-virtual-scroller/issues/123))

The universal recommendation is to **filter items at the data level** before passing to the scroller, not hide them via CSS/height.

## Future Extensions

The `compute_version` system allows adding more computed fields:

1. **`cost`**: Token cost extracted from JSON (for billing display)
2. **`component_type`**: Vue component to use for rendering (centralized type logic)
3. **`summary`**: Pre-extracted text for search indexing

All computation logic stays in one place (backend), avoiding duplication across frontend/backend.

## Summary

| Aspect | Decision |
|--------|----------|
| Where is filter logic? | Backend (during computation) |
| When is computation done? | Background task + live sync |
| How to handle version changes? | `compute_version` triggers recomputation |
| Blocking on session open? | Two states: callout if compute pending, spinner if fetching |
| Initial fetch strategy | Metadata + initial items fetched **in parallel** |
| Session readiness exposed as | `compute_version_up_to_date` boolean (not raw version number) |
| Notification when compute done | Existing `session_updated` WebSocket message |
| Group bounds storage | `group_head`/`group_tail` for level 2 items only |
| API for metadata | New endpoint returning all items without content |
| API for content | Existing endpoint, auto-extends ranges for complete groups |
| Virtual scroller data | `sessionVisualItems` - computed filtered list for virtual scroller |
| Display mode storage | `sessionItemsDisplayMode` in localState (global, not per-session) |
| Expanded groups storage | `sessionExpandedGroups` in localState (per-session, not persisted) |
| Background task execution | `ProcessPoolExecutor` with graceful shutdown |
| Virtual scroller key | `lineNum` (stable across expand/collapse) |
| Group UI | Wrapper component with toggle + item content |
| Visual index recalculation | O(N) on mode change, expand/collapse, new item |
