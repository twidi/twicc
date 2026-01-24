# Tasks: Display Levels and Collapsible Groups

Implementation tasks for [ADR: Display Levels and Collapsible Groups System](./adr-display-levels.md).

---

## Backend Tasks

### 1. Models and Configuration
Migration: add `compute_version` to Session, `display_level`/`group_head`/`group_tail` to SessionItem. Create `DisplayLevel` enum. Add `CURRENT_COMPUTE_VERSION` constant in settings.

#### Why

The system needs to track:
- Whether a session's metadata has been computed (and with which version of the rules)
- For each item: its display level and group membership

The `compute_version` on Session enables re-processing when display rules change (bump the version → all sessions get recomputed).

#### Files to Modify/Create

| Action | File | Description |
|--------|------|-------------|
| Modify | `src/twicc_poc/core/models.py` | Add fields to Session and SessionItem, add DisplayLevel enum |
| Modify | `src/twicc_poc/settings.py` | Add CURRENT_COMPUTE_VERSION constant |
| Create | `src/twicc_poc/core/migrations/0002_display_levels.py` | Auto-generated migration |

#### Detailed Changes

##### 1. `src/twicc_poc/core/models.py`

Add the `DisplayLevel` enum at the top of the file (after the imports):

```python
from enum import IntEnum

class DisplayLevel(IntEnum):
    """Display level for session items, determining visibility in different modes."""
    ALWAYS = 1       # Always shown in all modes
    COLLAPSIBLE = 2  # Shown in Normal, grouped in Simplified
    DEBUG_ONLY = 3   # Only shown in Debug mode
```

Modify the `Session` model (currently at line 19-37) to add the `compute_version` field:

```python
class Session(models.Model):
    """A session corresponds to a *.jsonl file (excluding agent-*.jsonl)"""

    id = models.CharField(max_length=255, primary_key=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    last_offset = models.PositiveBigIntegerField(default=0)
    last_line = models.PositiveIntegerField(default=0)
    mtime = models.FloatField(default=0)
    archived = models.BooleanField(default=False)
    compute_version = models.PositiveIntegerField(null=True, blank=True)  # NEW: NULL = never computed

    class Meta:
        ordering = ["-mtime"]

    def __str__(self):
        return self.id
```

Modify the `SessionItem` model (currently at line 40-62) to add the metadata fields:

```python
class SessionItem(models.Model):
    """A session item corresponds to a single line in a JSONL file"""

    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="items",
    )
    line_num = models.PositiveIntegerField()  # 1-based (first line = 1)
    content = models.TextField()

    # NEW: Display metadata fields
    display_level = models.PositiveSmallIntegerField(null=True, blank=True)  # DisplayLevel enum value
    group_head = models.PositiveIntegerField(null=True, blank=True, db_index=True)  # line_num of group start, indexed for watcher queries
    group_tail = models.PositiveIntegerField(null=True, blank=True)  # line_num of group end

    class Meta:
        ordering = ["line_num"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "line_num"],
                name="unique_session_line",
            )
        ]

    def __str__(self):
        return f"{self.session_id}:{self.line_num}"
```

##### 2. `src/twicc_poc/settings.py`

Add at the end of the file (after line 66):

```python
# Display levels computation
CURRENT_COMPUTE_VERSION = 1  # Bump when display rules change to trigger recomputation
```

##### 3. Generate Migration

Run the following command to generate the migration file:

```bash
uv run python -m django makemigrations core --name display_levels
```

This will create `src/twicc_poc/core/migrations/0002_display_levels.py` automatically.

#### Field Details

| Model | Field | Type | Nullable | Description |
|-------|-------|------|----------|-------------|
| Session | compute_version | PositiveIntegerField | Yes | NULL = never computed, compare to CURRENT_COMPUTE_VERSION |
| SessionItem | display_level | PositiveSmallIntegerField | Yes | 1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY |
| SessionItem | group_head | PositiveIntegerField | Yes | line_num of first level-2 item in group |
| SessionItem | group_tail | PositiveIntegerField | Yes | line_num of last level-2 item in group |

#### Migration Notes

- All existing sessions will have `compute_version = NULL` after migration
- All existing items will have `display_level = NULL`, `group_head = NULL`, `group_tail = NULL`
- The background task (Task 3) will process them on first startup

#### Group Bounds Logic (for reference)

When consecutive items have levels like: 2, 3, 2, 2, 3, 2:
- They form ONE group (level 3 doesn't break groups)
- Only the level 2 items have `group_head`/`group_tail` set
- Level 3 items keep `NULL` for both fields
- The group spans from the first level 2 to the last level 2

### 2. Metadata Computation Functions
Modular `compute_metadata` + `compute_session_metadata` with batch processing (iterator + group management).

#### Why

Sessions can have thousands of items, some weighing several MB. We need:
- Modular computation (easy to add new computed fields later: cost, component_type, etc.)
- Batch processing to avoid loading all items into memory at once
- Proper group boundary tracking across items

#### Files to Create

| Action | File | Description |
|--------|------|-------------|
| Create | `src/twicc_poc/compute.py` | New module for metadata computation functions |

#### Detailed Implementation

##### 1. Create `src/twicc_poc/compute.py`

This new file contains all metadata computation logic:

```python
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


def compute_display_level(parsed_json: dict) -> int:
    """
    Determine the display level for an item based on its JSON content.

    Args:
        parsed_json: Parsed JSON content of the item

    Returns:
        DisplayLevel enum value (1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY)

    Current implementation: Returns ALWAYS (1) for all items.
    TODO: Implement actual classification logic based on message types.
    """
    # Placeholder: all items are level 1 for now
    # Future: inspect parsed_json['type'] or other fields to determine level
    return DisplayLevel.ALWAYS


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

    This function is designed to run in a ProcessPoolExecutor (CPU-intensive).
    It processes items in batches using iterator() to avoid memory issues.

    Args:
        session_id: The session ID (not the object, for pickling compatibility)
    """
    # Import here to avoid issues with ProcessPoolExecutor pickling
    from twicc_poc.core.models import Session

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

    logger.info(f"Computed metadata for session {session_id}")
```

#### Architecture

Two levels of functions:

| Function | Scope | Input | Output | Used By |
|----------|-------|-------|--------|---------|
| `compute_display_level(parsed_json)` | Single field | Parsed JSON dict | int (DisplayLevel) | compute_metadata |
| `compute_metadata(parsed_json)` | Single item | Parsed JSON dict | dict with all metadata | Both |
| `compute_session_metadata(session_id)` | Full session | Session ID string | None (updates DB) | Background task |

#### Group Logic Example

Items with levels: 1, 2, 3, 2, 2, 3, 2, 1

| line_num | level | group_head | group_tail | Notes                                |
|----------|-------|------------|------------|--------------------------------------|
| 1        | 1     | NULL       | NULL       | Level 1 = no group                   |
| 2        | 2     | 2          | 7          | Group head                           |
| 3        | 3     | NULL       | NULL       | Inside group but no fields (level 3) |
| 4        | 2     | 2          | 7          | Group member                         |
| 5        | 2     | 2          | 7          | Group member                         |
| 6        | 3     | NULL       | NULL       | Inside group but no fields (level 3) |
| 7        | 2     | 2          | 7          | Group tail                           |
| 8        | 1     | NULL       | NULL       | Level 1 closes the group             |

#### Performance Considerations

- `iterator(chunk_size=500)` prevents loading all items into memory
- `bulk_update` is used instead of individual `save()` calls for better DB performance
- The function receives `session_id` (string) instead of Session object for ProcessPoolExecutor compatibility
- The function runs in a ProcessPoolExecutor (Task 3), so blocking is acceptable

### 3. Background Task
Asyncio loop + ProcessPoolExecutor + graceful shutdown. Start after initial sync completes.

#### Why

- Sessions need metadata computed before they can be displayed properly
- Computation is CPU-intensive (JSON parsing of potentially large items)
- Must not block the asyncio event loop (would freeze WebSocket, HTTP, etc.)
- Existing sessions (after migration) all need processing

#### Files to Create/Modify

| Action | File | Description |
|--------|------|-------------|
| Create | `src/twicc_poc/background.py` | New module for background compute task |
| Modify | `run.py` | Start the background task alongside the watcher |

#### Detailed Implementation

##### 1. Create `src/twicc_poc/background.py`

```python
"""
Background task for computing session metadata.

Runs continuously, processing sessions that need metadata computation.
Uses ProcessPoolExecutor to offload CPU-intensive work.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from twicc_poc.compute import compute_session_metadata
from twicc_poc.core.models import Session
from twicc_poc.core.serializers import serialize_session

logger = logging.getLogger(__name__)

# Single worker process for computation
_executor: ProcessPoolExecutor | None = None

# Stop event for graceful shutdown
_stop_event: asyncio.Event | None = None


def get_executor() -> ProcessPoolExecutor:
    """Get or create the ProcessPoolExecutor."""
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=1)
    return _executor


def get_stop_event() -> asyncio.Event:
    """Get or create the stop event."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_background_task() -> None:
    """Signal the background task to stop and shutdown executor."""
    global _executor, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _executor is not None:
        _executor.shutdown(wait=False)  # Don't wait for current computation
        _executor = None


@sync_to_async
def get_next_session_to_compute() -> Session | None:
    """
    Find the next session needing metadata computation.

    Returns sessions where:
    - compute_version IS NULL (never computed)
    - compute_version != CURRENT_COMPUTE_VERSION (outdated)

    Ordered by mtime descending (most recent first).
    """
    return Session.objects.exclude(
        compute_version=settings.CURRENT_COMPUTE_VERSION
    ).order_by('-mtime').first()


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


async def broadcast_session_updated(session: Session) -> None:
    """Broadcast session_updated message via WebSocket."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "session_updated",
                "session": serialize_session(session),
            },
        },
    )


async def start_background_compute_task() -> None:
    """
    Background task that continuously processes sessions needing computation.

    Runs until stop event is set. Uses ProcessPoolExecutor for CPU-intensive work.
    """
    executor = get_executor()
    stop_event = get_stop_event()
    loop = asyncio.get_event_loop()

    logger.info("Background compute task started")

    while not stop_event.is_set():
        try:
            # Find next session needing computation
            session = await get_next_session_to_compute()

            if session is None:
                # Nothing to compute, wait before checking again
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue

            logger.info(f"Computing metadata for session {session.id}")

            # Run CPU-intensive work in separate process
            # Pass session.id (string), not the object (can't pickle Django models)
            await loop.run_in_executor(
                executor,
                compute_session_metadata,
                session.id
            )

            # Back in main process: reload session and broadcast update
            session = await refresh_session(session)
            await broadcast_session_updated(session)

            logger.info(f"Completed metadata computation for session {session.id}")

        except Exception as e:
            logger.error(f"Error in background compute task: {e}", exc_info=True)
            # Wait before retrying to avoid tight error loop
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    logger.info("Background compute task stopped")
```

##### 2. Modify `run.py`

Add imports at line 33 (after the watcher imports):

```python
from twicc_poc.watcher import start_watcher, stop_watcher
from twicc_poc.background import start_background_compute_task, stop_background_task  # NEW
```

Modify the `run_server` async function (currently at line 36-62):

```python
async def run_server(port: int):
    """Run the ASGI server with file watcher and background compute task."""
    import uvicorn
    from twicc_poc.asgi import application

    # Start watcher task
    watcher_task = asyncio.create_task(start_watcher())

    # Start background compute task (after initial sync completed in main())
    compute_task = asyncio.create_task(start_background_compute_task())

    # Configure uvicorn
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        # Clean shutdown of watcher
        stop_watcher()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

        # Clean shutdown of background compute task
        stop_background_task()
        compute_task.cancel()
        try:
            await compute_task
        except asyncio.CancelledError:
            pass
```

Update the startup message in `main()` (around line 89):

```python
    print("✓ File watcher scheduled")
    print("✓ Background compute task scheduled")  # NEW
    print(f"→ Server starting on http://0.0.0.0:{port_int}")
```

#### Startup Sequence

The current `run.py` already has the correct sequence:

1. **`main()`**: Django setup, migrations, `sync_all()`
2. **`run_server()`**: Starts watcher and compute task concurrently
3. Both tasks run during server lifetime
4. On shutdown: both tasks are stopped gracefully

The background task starts **after** the initial sync completes (sync_all is called in main() before run_server()).

#### Query Logic

```python
Session.objects.exclude(compute_version=CURRENT_COMPUTE_VERSION)
```

This matches:
- `compute_version IS NULL` (never computed)
- `compute_version != CURRENT_COMPUTE_VERSION` (computed with old rules)

Sessions are processed in `mtime` descending order (most recently modified first).

#### Graceful Shutdown

- `stop_background_task()` sets the stop event and calls `executor.shutdown(wait=False)`
- `wait=False` because computation can take a long time for large sessions
- Interrupted computation is safe: `compute_version` is only updated at the very end
- The session will be reprocessed on next startup

#### Integration with Existing Code

The broadcast mechanism reuses the existing pattern from `watcher.py`:
- Uses `channel_layer.group_send()` to the "updates" group
- Uses `serialize_session()` which will include `compute_version_up_to_date` (Task 5)
- WebSocket consumer's `broadcast()` method sends to clients

### 4. Watcher (Live Sync)
Compute metadata on each new line, handle extension of existing groups.

#### Why

When new lines arrive via the file watcher, we need to:
- Compute metadata immediately (so the item is ready for display)
- Handle group membership (new item might extend an existing group)
- Broadcast the complete item (with metadata) via WebSocket

This is different from the background task which processes entire sessions. The watcher processes one line at a time, in real-time.

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `src/twicc_poc/sync.py` | Add metadata computation to `sync_session_items()` |
| Modify | `src/twicc_poc/compute.py` | Add `compute_item_metadata_live()` helper function |

#### Detailed Implementation

##### 1. Add to `src/twicc_poc/compute.py`

Add these functions for live (single-item) metadata computation:

```python
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
```

##### 2. Modify `src/twicc_poc/sync.py`

The `sync_session_items()` function (lines 74-138) creates `SessionItem` objects. Modify the item creation loop (around line 116-124):

**Current code:**
```python
for line in lines:
    current_line_num += 1
    items_to_create.append(
        SessionItem(
            session=session,
            line_num=current_line_num,
            content=line,
        )
    )

# Bulk create with ignore_conflicts in case of duplicate line_num
SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
```

**New code:**
```python
from twicc_poc.compute import compute_item_metadata_live

for line in lines:
    current_line_num += 1
    item = SessionItem(
        session=session,
        line_num=current_line_num,
        content=line,
    )
    # Compute metadata for this item (sets display_level, group_head, group_tail)
    # Note: This needs to be done AFTER previous items are saved if they might
    # affect group membership. Since we bulk_create at the end, we need to
    # save items one by one if they're level 2 or check the last level.

    items_to_create.append(item)

# For live sync, we need to process items one by one to handle groups correctly
# Bulk create first (without metadata), then compute metadata
SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True)

# Compute metadata for each new item (in order)
for item in items_to_create:
    # Reload item from DB to get the saved instance
    saved_item = SessionItem.objects.get(session=session, line_num=item.line_num)
    compute_item_metadata_live(session.id, saved_item, saved_item.content)
    saved_item.save(update_fields=['display_level', 'group_head', 'group_tail'])
```

**Alternative approach** (more efficient for large batches):

If all items are level 1 (ALWAYS), we can use bulk_update. For mixed levels with groups, we need individual processing:

```python
from twicc_poc.compute import compute_metadata

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
    metadata = compute_metadata(parsed)
    item.display_level = metadata['display_level']
    items_to_create.append(item)

# Bulk create all items
SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True)

# Second pass: compute group membership for level 2 items
for item in items_to_create:
    if item.display_level == DisplayLevel.COLLAPSIBLE:
        compute_item_metadata_live(session.id, item, item.content)
        # Need to save the group info
        SessionItem.objects.filter(
            session=session,
            line_num=item.line_num
        ).update(
            group_head=item.group_head,
            group_tail=item.group_tail
        )
```

#### Key Edge Cases

1. **Level 2 after level 3**: Level 3 items don't have group_head, so we must look back to find the actual group head from the nearest level 2 item.

2. **First item in session**: No previous item → if level 2, it starts a new group.

3. **Sequence: 1, 3, 2**: The level 3 is standalone (no group). The level 2 starts a new group (not joining anything).

4. **Sequence: 2, 3, 2**: All three are in the same group. The first 2 is head, the second 2 becomes tail, and we update the first 2's tail.

#### Group Tail Updates

When a new level 2 item extends a group, we must update `group_tail` for ALL level 2 items in that group:

```python
SessionItem.objects.filter(
    session_id=session_id,
    group_head=existing_head
).update(group_tail=item.line_num)
```

This is a single DB query, efficient even for large groups.

#### Difference from Background Task

| Aspect         | Background Task             | Watcher             |
|----------------|-----------------------------|---------------------|
| Processes      | Entire session              | One line (or batch) |
| Runs in        | ProcessPoolExecutor         | Main thread (sync)  |
| Group tracking | In-memory buffer            | DB queries          |
| Updates tail   | At finalize_group           | On each new level 2 |
| When           | On startup / version change | Real-time           |

### 5. Serialization
Add `compute_version_up_to_date` to serialize_session. Add new fields to serialize_item. Create new serialize_item_metadata function.

#### Why

Three serialization changes are needed:

1. **Session**: Frontend needs to know if a session is ready (computed) without knowing version details
2. **Item (full)**: WebSocket messages and item fetches need metadata fields
3. **Item (metadata only)**: New endpoint needs items without content (lighter payload)

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `src/twicc_poc/core/serializers.py` | Update all three serialization functions |

#### Detailed Implementation

##### Modify `src/twicc_poc/core/serializers.py`

**Current file (lines 1-37):**
```python
def serialize_project(project):
    ...

def serialize_session(session):
    return {
        "id": session.id,
        "project_id": session.project_id,
        "last_line": session.last_line,
        "mtime": session.mtime,
        "archived": session.archived,
    }

def serialize_session_item(item):
    return {
        "line_num": item.line_num,
        "content": item.content,
    }
```

**New file:**
```python
"""
Simple JSON serializers for core models.

Note: These serializers only access model attributes that are already loaded
in memory (no lazy-loaded relationships, no database queries). This makes them
safe to call from async contexts without sync_to_async wrapping, as long as
the model instance was already fetched from the database.
"""

from django.conf import settings


def serialize_project(project):
    """Serialize a Project model to a dictionary."""
    return {
        "id": project.id,
        "sessions_count": project.sessions_count,
        "mtime": project.mtime,
        "archived": project.archived,
    }


def serialize_session(session):
    """
    Serialize a Session model to a dictionary.

    Includes compute_version_up_to_date boolean to indicate if the session's
    metadata has been computed with the current version of rules.
    """
    return {
        "id": session.id,
        "project_id": session.project_id,
        "last_line": session.last_line,
        "mtime": session.mtime,
        "archived": session.archived,
        # NEW: Boolean indicating if session metadata is up-to-date
        "compute_version_up_to_date": session.compute_version == settings.CURRENT_COMPUTE_VERSION,
    }


def serialize_session_item(item):
    """
    Serialize a SessionItem model to a dictionary with full content.

    Used by:
    - GET /api/.../items/?range=... endpoint
    - WebSocket item_created messages
    """
    return {
        "line_num": item.line_num,
        "content": item.content,
        # NEW: Display metadata fields
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
    }


def serialize_session_item_metadata(item):
    """
    Serialize a SessionItem model to a dictionary WITHOUT content.

    Used by:
    - GET /api/.../items/metadata/ endpoint

    This is a lightweight serialization for loading all item metadata
    without the potentially large content field.
    """
    return {
        "line_num": item.line_num,
        "display_level": item.display_level,
        "group_head": item.group_head,
        "group_tail": item.group_tail,
        # NO content field - that's the whole point
    }
```

#### Summary of Changes

| Function | Changes |
|----------|---------|
| `serialize_project` | No changes |
| `serialize_session` | Add `compute_version_up_to_date` field |
| `serialize_session_item` | Add `display_level`, `group_head`, `group_tail` fields |
| `serialize_session_item_metadata` | **NEW** - same as above but without `content` |

#### Field Details

**serialize_session:**

| Field | Type | Description |
|-------|------|-------------|
| `compute_version_up_to_date` | boolean | `True` if computed with current rules, `False` otherwise |

**serialize_session_item / serialize_session_item_metadata:**

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `display_level` | int | Yes | 1=ALWAYS, 2=COLLAPSIBLE, 3=DEBUG_ONLY |
| `group_head` | int | Yes | line_num of group start (level 2 only) |
| `group_tail` | int | Yes | line_num of group end (level 2 only) |

#### Payload Size Comparison

For a session with 5000 items:

| Serialization | Per Item | Total (5000 items) |
|---------------|----------|-------------------|
| Metadata only | ~50 bytes | ~250 KB |
| Full (with content) | Variable (KB to MB) | Potentially huge |

The metadata-only endpoint is essential for the initial load strategy.

### 6. API and WebSocket
New endpoint `/items/metadata/`. Include metadata fields in WebSocket item messages.

#### Why

Two changes needed:
1. **New endpoint**: Frontend needs to fetch all item metadata (without content) on session load
2. **WebSocket update**: Real-time item messages must include metadata fields

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `src/twicc_poc/views.py` | Add `session_items_metadata()` view function |
| Modify | `src/twicc_poc/urls.py` | Add URL route for new endpoint |
| Verify | `src/twicc_poc/watcher.py` | Confirm it uses updated `serialize_session_item` |

#### Detailed Implementation

##### 1. Add view function to `src/twicc_poc/views.py`

Add this new function after `session_items()` (around line 107):

```python
def session_items_metadata(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/items/metadata/ - Metadata of all items.

    Returns all items with metadata fields but WITHOUT content.
    Used for initial session load to build the visual items list.
    """
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    items = session.items.all()  # Already ordered by line_num (see Meta.ordering)
    data = [serialize_session_item_metadata(item) for item in items]
    return JsonResponse(data, safe=False)
```

Also update the imports at the top of the file:

```python
from twicc_poc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,  # NEW
)
```

##### 2. Add URL route to `src/twicc_poc/urls.py`

Add the new route after the existing `session_items` route (around line 12):

**Current:**
```python
urlpatterns = [
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    # Catch-all for Vue Router (must be last)
    re_path(r"^(?!api/|static/|ws/).*$", views.spa_index),
]
```

**New:**
```python
urlpatterns = [
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/metadata/", views.session_items_metadata),  # NEW
    # Catch-all for Vue Router (must be last)
    re_path(r"^(?!api/|static/|ws/).*$", views.spa_index),
]
```

##### 3. Verify WebSocket item messages

The `watcher.py` file already uses `serialize_session_item` in the `get_new_session_items` function (line 94-100):

```python
@sync_to_async
def get_new_session_items(session: Session, start_line: int) -> list[dict]:
    """Get session items added after start_line."""
    items = SessionItem.objects.filter(
        session=session,
        line_num__gt=start_line,
    ).order_by("line_num")
    return [serialize_session_item(item) for item in items]
```

Since we updated `serialize_session_item` in Task 5 to include `display_level`, `group_head`, `group_tail`, **no changes are needed here**. The WebSocket messages will automatically include the new fields.

The message format will now be:

```json
{
  "type": "session_items_added",
  "session_id": "abc123",
  "project_id": "my-project",
  "items": [
    {
      "line_num": 42,
      "content": "{...}",
      "display_level": 2,
      "group_head": 40,
      "group_tail": 42
    }
  ]
}
```

#### API Endpoints Summary

| Endpoint | Method | Returns | Uses |
|----------|--------|---------|------|
| `/api/.../items/` | GET | Items with content + metadata | `serialize_session_item` |
| `/api/.../items/metadata/` | GET | Items metadata only (no content) | `serialize_session_item_metadata` |

#### Response Format: `/items/metadata/`

```json
[
  { "line_num": 1, "display_level": 1, "group_head": null, "group_tail": null },
  { "line_num": 2, "display_level": 2, "group_head": 2, "group_tail": 5 },
  { "line_num": 3, "display_level": 2, "group_head": 2, "group_tail": 5 },
  { "line_num": 4, "display_level": 3, "group_head": null, "group_tail": null },
  { "line_num": 5, "display_level": 2, "group_head": 2, "group_tail": 5 }
]
```

**Notes:**
- No pagination needed (metadata is small, ~50 bytes/item)
- Returns ALL items in line_num order
- Used for initial session load to build the visual items list

---

## Frontend Tasks

### 7. Store: Structures and Helpers
Add `sessionItemsDisplayMode`, `sessionExpandedGroups` to localState. Create helper for lineNum-based access.

#### Why

The frontend needs:
- Global display mode state (applies to whichever session is viewed)
- Per-session expanded groups tracking (survives navigation between sessions)
- Easy access to items by lineNum (array index offset)

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `frontend/src/stores/data.js` | Add new state, getters, and actions |
| Modify | `frontend/src/constants.js` | Add display mode constants |

#### Detailed Implementation

##### 1. Add constants to `frontend/src/constants.js`

Add after the existing `INITIAL_ITEMS_COUNT` constant:

```javascript
/**
 * Display mode values for session items.
 * - debug: Show all items (levels 1, 2, 3)
 * - normal: Show levels 1 and 2, hide level 3
 * - simplified: Show level 1, collapse level 2 groups, hide level 3
 */
export const DISPLAY_MODES = {
    DEBUG: 'debug',
    NORMAL: 'normal',
    SIMPLIFIED: 'simplified',
}

export const DEFAULT_DISPLAY_MODE = DISPLAY_MODES.NORMAL
```

##### 2. Modify `frontend/src/stores/data.js`

**2a.** Add to imports (top of file)

```javascript
import { DEFAULT_DISPLAY_MODE } from '../constants'
```

**2b.** Add new fields to `localState` (around line 13-20)

**Current:**
```javascript
localState: {
    projectsList: {
        loading: false,
        loadingError: false
    },
    projects: {},   // { projectId: { sessionsFetched, sessionsLoading, sessionsLoadingError } }
    sessions: {}    // { sessionId: { itemsFetched, itemsLoading, itemsLoadingError } }
}
```

**New:**
```javascript
localState: {
    projectsList: {
        loading: false,
        loadingError: false
    },
    projects: {},   // { projectId: { sessionsFetched, sessionsLoading, sessionsLoadingError } }
    sessions: {},   // { sessionId: { itemsFetched, itemsLoading, itemsLoadingError } }

    // NEW: Display mode - global, not per-session
    // Values: 'debug' | 'normal' | 'simplified'
    sessionItemsDisplayMode: DEFAULT_DISPLAY_MODE,

    // NEW: Expanded groups - per session
    // { sessionId: [groupHeadLineNum, ...] }
    // Using array instead of Set for Vue reactivity
    sessionExpandedGroups: {}
}
```

**2c.** Add new getters (after existing getters, around line 52)

```javascript
// NEW: Display mode getter
getDisplayMode: (state) => state.localState.sessionItemsDisplayMode,

// NEW: Get expanded groups for a session (returns array)
getExpandedGroups: (state) => (sessionId) =>
    state.localState.sessionExpandedGroups[sessionId] || [],

// NEW: Check if a group is expanded
isGroupExpanded: (state) => (sessionId, groupHeadLineNum) => {
    const groups = state.localState.sessionExpandedGroups[sessionId]
    return groups ? groups.includes(groupHeadLineNum) : false
},

// NEW: Get a single item by lineNum (handles 1-based to 0-based conversion)
getSessionItem: (state) => (sessionId, lineNum) => {
    const items = state.sessionItems[sessionId]
    if (!items || lineNum < 1) return null
    return items[lineNum - 1] || null
},
```

**2d.** Add new actions (after existing actions, around line 378)

```javascript
// NEW: Set display mode
setDisplayMode(mode) {
    this.localState.sessionItemsDisplayMode = mode
},

// NEW: Toggle expanded state of a group
toggleExpandedGroup(sessionId, groupHeadLineNum) {
    // Ensure array exists for this session
    if (!this.localState.sessionExpandedGroups[sessionId]) {
        this.localState.sessionExpandedGroups[sessionId] = []
    }

    const groups = this.localState.sessionExpandedGroups[sessionId]
    const index = groups.indexOf(groupHeadLineNum)

    if (index >= 0) {
        // Collapse: remove from array
        groups.splice(index, 1)
    } else {
        // Expand: add to array
        groups.push(groupHeadLineNum)
    }
},

// NEW: Expand a group (idempotent)
expandGroup(sessionId, groupHeadLineNum) {
    if (!this.localState.sessionExpandedGroups[sessionId]) {
        this.localState.sessionExpandedGroups[sessionId] = []
    }
    const groups = this.localState.sessionExpandedGroups[sessionId]
    if (!groups.includes(groupHeadLineNum)) {
        groups.push(groupHeadLineNum)
    }
},

// NEW: Collapse a group (idempotent)
collapseGroup(sessionId, groupHeadLineNum) {
    const groups = this.localState.sessionExpandedGroups[sessionId]
    if (groups) {
        const index = groups.indexOf(groupHeadLineNum)
        if (index >= 0) {
            groups.splice(index, 1)
        }
    }
},

// NEW: Collapse all groups for a session
collapseAllGroups(sessionId) {
    this.localState.sessionExpandedGroups[sessionId] = []
},
```

#### State Structure Summary

| State | Location | Type | Default | Persisted |
|-------|----------|------|---------|-----------|
| `sessionItemsDisplayMode` | `localState` | string | `'normal'` | No |
| `sessionExpandedGroups` | `localState` | `{[sessionId]: number[]}` | `{}` | No |

#### Reactivity Notes

We use **arrays instead of Sets** for `sessionExpandedGroups` because:
- Vue 3 reactivity doesn't track Set mutations (`add`, `delete`) automatically
- Arrays with `splice` and `push` are fully reactive
- `includes()` is O(n) but acceptable for small group counts per session

#### Helper Usage Examples

```javascript
// In components:
const store = useDataStore()

// Get display mode
const mode = store.getDisplayMode  // 'debug' | 'normal' | 'simplified'

// Set display mode
store.setDisplayMode('simplified')

// Check if a group is expanded
const isExpanded = store.isGroupExpanded(sessionId, 5)  // true/false

// Toggle a group
store.toggleExpandedGroup(sessionId, 5)

// Get a session item by line number
const item = store.getSessionItem(sessionId, 42)  // item at line 42
```

### 8. computeSessionVisualItems
Filtering function based on mode + expanded groups. Automatic recomputation when sessionItems changes.

#### Why

The virtual scroller needs a filtered list of items to display. This list depends on:
- Current display mode (debug/normal/simplified)
- Which groups are expanded (in simplified mode)

`sessionVisualItems` is the computed list that maps visual positions to actual items.

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `frontend/src/stores/data.js` | Add `sessionVisualItems` state and `computeVisualItems` action |
| Create | `frontend/src/utils/visualItems.js` | Pure function for computing visual items |

#### Detailed Implementation

##### 1. Create `frontend/src/utils/visualItems.js`

Create a new file with the pure computation function:

```javascript
/**
 * Compute visual items list from session items based on display mode and expanded groups.
 *
 * @param {Array} items - Array of session items with metadata
 * @param {string} mode - Display mode: 'debug' | 'normal' | 'simplified'
 * @param {Array} expandedGroups - Array of expanded group_head line numbers
 * @returns {Array} Array of visual items: { lineNum, isGroupHead?, isExpanded? }
 */
export function computeVisualItems(items, mode, expandedGroups = []) {
    if (!items || items.length === 0) return []

    const result = []
    const expandedSet = new Set(expandedGroups)  // Convert to Set for O(1) lookup

    for (const item of items) {
        // Skip items without display_level (not yet computed)
        if (item.display_level == null) {
            // In debug mode, show anyway; in other modes, skip
            if (mode === 'debug') {
                result.push({ lineNum: item.line_num })
            }
            continue
        }

        // Debug mode: show everything
        if (mode === 'debug') {
            result.push({ lineNum: item.line_num })
            continue
        }

        // Normal mode: show levels 1 and 2, hide level 3
        if (mode === 'normal') {
            if (item.display_level !== 3) {
                result.push({ lineNum: item.line_num })
            }
            // level 3: skip entirely
            continue
        }

        // Simplified mode: groups are collapsible
        if (item.display_level === 1) {
            // Level 1: always visible
            result.push({ lineNum: item.line_num })
        } else if (item.display_level === 2) {
            // Level 2: check if head or member
            const isHead = item.line_num === item.group_head
            const isExpanded = expandedSet.has(item.group_head)

            if (isHead) {
                // Group head: always has a visual entry (shows toggle)
                result.push({
                    lineNum: item.line_num,
                    isGroupHead: true,
                    isExpanded: isExpanded
                })
            } else if (isExpanded) {
                // Group member, group is expanded: visible
                result.push({ lineNum: item.line_num })
            }
            // else: group member, group collapsed: no visual entry
        }
        // Level 3 in simplified mode: never visible (skip)
    }

    return result
}
```

##### 2. Modify `frontend/src/stores/data.js`

**2a.** Add import (top of file)

```javascript
import { computeVisualItems } from '../utils/visualItems'
```

**2b.** Add `sessionVisualItems` to state (after `sessionItems`)

```javascript
state: () => ({
    // Server data
    projects: {},
    sessions: {},
    sessionItems: {},   // { sessionId: [{ line_num, content, display_level, ... }, ...] }
    sessionVisualItems: {},  // NEW: { sessionId: [{ lineNum, isGroupHead?, isExpanded? }, ...] }

    // Local UI state
    localState: {
        // ... existing fields ...
    }
}),
```

**2c.** Add getter (after existing getters)

```javascript
// NEW: Get visual items for a session
getSessionVisualItems: (state) => (sessionId) =>
    state.sessionVisualItems[sessionId] || [],
```

**2d.** Add action to recompute visual items

Add this action after the existing actions:

```javascript
/**
 * Recompute visual items for a session based on current mode and expanded groups.
 * Should be called after:
 * - sessionItems changes (metadata loaded, content loaded, new item via WebSocket)
 * - Display mode changes
 * - Group is toggled
 *
 * @param {string} sessionId
 */
recomputeVisualItems(sessionId) {
    const items = this.sessionItems[sessionId]
    if (!items) {
        this.sessionVisualItems[sessionId] = []
        return
    }

    const mode = this.localState.sessionItemsDisplayMode
    const expandedGroups = this.localState.sessionExpandedGroups[sessionId] || []

    this.sessionVisualItems[sessionId] = computeVisualItems(items, mode, expandedGroups)
},

/**
 * Recompute visual items for ALL sessions.
 * Called when display mode changes (affects all sessions).
 */
recomputeAllVisualItems() {
    for (const sessionId of Object.keys(this.sessionItems)) {
        this.recomputeVisualItems(sessionId)
    }
},
```

**2e.** Update existing actions to call recomputeVisualItems

**Update `setDisplayMode`:**
```javascript
setDisplayMode(mode) {
    this.localState.sessionItemsDisplayMode = mode
    this.recomputeAllVisualItems()  // NEW: Recompute for all sessions
},
```

**Update `toggleExpandedGroup`:**
```javascript
toggleExpandedGroup(sessionId, groupHeadLineNum) {
    // ... existing toggle logic ...

    this.recomputeVisualItems(sessionId)  // NEW: Recompute after toggle
},
```

**Update `addSessionItems`:**
```javascript
addSessionItems(sessionId, newItems) {
    // ... existing logic ...

    this.recomputeVisualItems(sessionId)  // NEW: Recompute after adding items
},
```

#### Data Structure

```javascript
// sessionVisualItems[sessionId] is an array of:
{
  lineNum: 1,           // Actual line number in sessionItems (1-based)
  isGroupHead: false,   // true if this is a collapsible group head (simplified mode)
  isExpanded: false     // true if group is expanded (only for group heads)
}
```

#### Complexity

- **Time**: O(N) for N items
- **Instant** for sessions with 5000 items (~1ms)

#### When to Recompute

| Trigger | Method to Call |
|---------|----------------|
| Initial metadata fetch | `recomputeVisualItems(sessionId)` |
| Content fetch | Already handled by `addSessionItems` |
| New item via WebSocket | Already handled by `addSessionItems` |
| Display mode change | Already handled by `setDisplayMode` |
| Group toggle | Already handled by `toggleExpandedGroup` |

#### Synchronization Rule

The store actions are designed to automatically maintain synchronization. **Never modify `sessionItems` directly** - always use the provided actions which will call `recomputeVisualItems`.

### 9. SessionView: Complete Refactoring
Compute pending state + parallel fetch (metadata + initial items) + sessionVisualItems integration.

This task combines three interconnected changes to SessionView that must be implemented together.

#### Overview

| Feature | Description |
|---------|-------------|
| Compute Pending State | Show callout when session metadata hasn't been computed yet |
| Parallel Fetch | Load metadata and initial items concurrently via `Promise.all` |
| VisualItems Integration | Use filtered `sessionVisualItems` instead of raw items |

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `frontend/src/stores/data.js` | Add `loadSessionMetadata`, `initSessionItemsFromMetadata`, `updateSessionItemsContent` |
| Modify | `frontend/src/views/SessionView.vue` | Complete refactoring: new function, computed props, watchers, template |

---

#### Part A: Store Actions

Add three new actions to `frontend/src/stores/data.js` (after the existing actions):

```javascript
/**
 * Load metadata for all items in a session (without content).
 * @param {string} projectId
 * @param {string} sessionId
 * @returns {Promise<Array|null>} Array of metadata objects or null on error
 */
async loadSessionMetadata(projectId, sessionId) {
    try {
        const res = await fetch(
            `/api/projects/${projectId}/sessions/${sessionId}/items/metadata/`
        )
        if (!res.ok) {
            console.error('Failed to load session metadata:', res.status, res.statusText)
            return null
        }
        return await res.json()
    } catch (error) {
        console.error('Failed to load session metadata:', error)
        return null
    }
},

/**
 * Initialize sessionItems array from metadata (no content).
 * @param {string} sessionId
 * @param {Array} metadata - Array of { line_num, display_level, group_head, group_tail }
 */
initSessionItemsFromMetadata(sessionId, metadata) {
    this.sessionItems[sessionId] = metadata.map(m => ({
        line_num: m.line_num,
        display_level: m.display_level,
        group_head: m.group_head,
        group_tail: m.group_tail,
        content: null  // Will be filled by content fetch
    }))

    // Compute visual items after initialization
    this.recomputeVisualItems(sessionId)
},

/**
 * Update existing session items with fetched content.
 * @param {string} sessionId
 * @param {Array} items - Array of { line_num, content, display_level, group_head, group_tail }
 */
updateSessionItemsContent(sessionId, items) {
    const sessionItemsArray = this.sessionItems[sessionId]
    if (!sessionItemsArray) return

    for (const item of items) {
        const index = item.line_num - 1  // line_num is 1-based
        if (sessionItemsArray[index]) {
            // Update content
            sessionItemsArray[index].content = item.content
            // Also update metadata in case it was computed after initial load
            if (item.display_level != null) {
                sessionItemsArray[index].display_level = item.display_level
            }
            if (item.group_head != null) {
                sessionItemsArray[index].group_head = item.group_head
            }
            if (item.group_tail != null) {
                sessionItemsArray[index].group_tail = item.group_tail
            }
        }
    }

    // Recompute visual items in case metadata changed
    this.recomputeVisualItems(sessionId)
},
```

---

#### Part B: SessionView.vue Modifications

All changes below are in `frontend/src/views/SessionView.vue`.

##### 1. Add computed properties

Add in the `<script setup>` section (after existing computed properties, around line 32-39):

```javascript
// Session items (raw, with metadata + content)
const items = computed(() => store.getSessionItems(sessionId.value))

// Visual items (filtered by display mode and expanded groups)
const visualItems = computed(() => store.getSessionVisualItems(sessionId.value))

// Check if session computation is pending
const isComputePending = computed(() => {
    const session = store.getSession(sessionId.value)
    return session && session.compute_version_up_to_date === false
})
```

##### 2. Replace `loadInitialItems` with `loadSessionData`

Replace the existing `loadInitialItems` function (lines 45-67) with:

```javascript
/**
 * Load session data: metadata (all items) + initial content (first N and last N).
 * Fetches both in parallel for faster loading.
 */
async function loadSessionData(pId, sId, lastLine) {
    // Mark as fetched first (before async operations to avoid race conditions)
    if (!store.localState.sessions[sId]) {
        store.localState.sessions[sId] = {}
    }
    store.localState.sessions[sId].itemsFetched = true
    store.localState.sessions[sId].itemsLoading = true

    try {
        // Build ranges for initial content
        const ranges = []
        if (lastLine <= INITIAL_ITEMS_COUNT * 2) {
            // Small session: load everything
            ranges.push([1, lastLine])
        } else {
            // Large session: load first N and last N
            ranges.push([1, INITIAL_ITEMS_COUNT])
            ranges.push([lastLine - INITIAL_ITEMS_COUNT + 1, lastLine])
        }

        // Build range params for items endpoint
        const params = new URLSearchParams()
        for (const [min, max] of ranges) {
            params.append('range', `${min}:${max}`)
        }

        // Fetch BOTH in parallel
        const [metadataResult, itemsResult] = await Promise.all([
            store.loadSessionMetadata(pId, sId),
            fetch(`/api/projects/${pId}/sessions/${sId}/items/?${params}`)
                .then(res => res.ok ? res.json() : null)
                .catch(() => null)
        ])

        // Check for errors
        if (!metadataResult || !itemsResult) {
            store.localState.sessions[sId].itemsLoadingError = true
            return
        }

        // Process results
        store.initSessionItemsFromMetadata(sId, metadataResult)
        store.updateSessionItemsContent(sId, itemsResult)

        // Success
        store.localState.sessions[sId].itemsLoadingError = false

    } catch (error) {
        console.error('Failed to load session data:', error)
        store.localState.sessions[sId].itemsLoadingError = true
    } finally {
        store.localState.sessions[sId].itemsLoading = false
    }
}
```

##### 3. Add helper functions

Add after the existing helpers (around line 223-233):

```javascript
/**
 * Get the content of an item by its line number.
 * Used by the scroller template and size-dependencies.
 */
function getItemContent(lineNum) {
    const item = store.getSessionItem(sessionId.value, lineNum)
    return item?.content
}

/**
 * Convert an array of line numbers to ranges for API calls.
 * e.g., [1, 2, 3, 5, 6, 10] → [[1, 3], [5, 6], [10, 10]]
 */
function lineNumsToRanges(lineNums) {
    if (lineNums.length === 0) return []

    const sorted = [...lineNums].sort((a, b) => a - b)
    const ranges = []
    let rangeStart = sorted[0]
    let rangeEnd = sorted[0]

    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === rangeEnd + 1) {
            rangeEnd = sorted[i]
        } else {
            ranges.push([rangeStart, rangeEnd])
            rangeStart = sorted[i]
            rangeEnd = sorted[i]
        }
    }

    ranges.push([rangeStart, rangeEnd])
    return ranges
}
```

##### 4. Update the main watcher

Replace the existing watch (lines 70-84):

```javascript
// Load session data when session changes
watch([projectId, sessionId, session], async ([newProjectId, newSessionId, newSession]) => {
    if (!newProjectId || !newSessionId || !newSession) return

    // Don't load if computation is pending
    if (newSession.compute_version_up_to_date === false) {
        return
    }

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    if (!store.areSessionItemsFetched(newSessionId)) {
        await loadSessionData(newProjectId, newSessionId, lastLine)
    }

    // Always scroll to end of session (with retry until stable)
    await nextTick()
    scrollToBottomUntilStable()
}, { immediate: true })
```

##### 5. Update `handleRetry` function

Replace the existing function (around line 87-105):

```javascript
// Retry loading session data after error
async function handleRetry() {
    if (!projectId.value || !sessionId.value || !session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    // Reset fetched state to allow reload
    if (store.localState.sessions[sessionId.value]) {
        store.localState.sessions[sessionId.value].itemsFetched = false
    }
    // Clear existing items
    delete store.sessionItems[sessionId.value]
    delete store.sessionVisualItems[sessionId.value]

    await loadSessionData(projectId.value, sessionId.value, lastLine)

    // Scroll to bottom after successful load
    await nextTick()
    scrollToBottomUntilStable()
}
```

##### 6. Add compute completion handler

Add after `handleRetry`:

```javascript
/**
 * Called when session becomes ready (compute completed).
 * Triggered by watching compute_version_up_to_date transition.
 */
async function onComputeCompleted() {
    if (!projectId.value || !sessionId.value || !session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    await loadSessionData(projectId.value, sessionId.value, lastLine)

    await nextTick()
    scrollToBottomUntilStable()
}

// Watch for session compute completion
watch(() => session.value?.compute_version_up_to_date, (newValue, oldValue) => {
    // Transition from false (or undefined) to true
    if (newValue === true && oldValue !== true) {
        onComputeCompleted()
    }
})
```

##### 7. Update `onScrollerUpdate` function

Replace the existing function (around line 208-220):

```javascript
/**
 * Handle scroller update event - triggers lazy loading for visible items.
 * Works with visualItems (filtered list) and maps to actual line numbers.
 */
function onScrollerUpdate(startIndex, endIndex, visibleStartIndex, visibleEndIndex) {
    const visItems = visualItems.value
    if (!visItems || visItems.length === 0) return

    // Add buffer around visible range
    const bufferedStart = Math.max(0, visibleStartIndex - LOAD_BUFFER)
    const bufferedEnd = Math.min(visItems.length - 1, visibleEndIndex + LOAD_BUFFER)

    // Collect line numbers that need content loading
    const lineNumsToLoad = []
    for (let i = bufferedStart; i <= bufferedEnd; i++) {
        const visualItem = visItems[i]
        if (visualItem) {
            const sessionItem = store.getSessionItem(sessionId.value, visualItem.lineNum)
            if (sessionItem && !sessionItem.content) {
                lineNumsToLoad.push(visualItem.lineNum)
            }
        }
    }

    if (lineNumsToLoad.length > 0) {
        pendingLoadRange.value = { lineNums: lineNumsToLoad }
        debouncedLoad()
    }
}
```

##### 8. Update `executePendingLoad` function

Replace the existing function (around line 171-199):

```javascript
/**
 * Execute the pending load - called after debounce.
 * Loads specific line numbers instead of ranges of indices.
 */
async function executePendingLoad() {
    const range = pendingLoadRange.value
    if (!range || !range.lineNums || range.lineNums.length === 0) return

    pendingLoadRange.value = null

    const ranges = lineNumsToRanges(range.lineNums)

    if (ranges.length > 0 && projectId.value && sessionId.value) {
        const el = scrollerRef.value?.$el
        const wasAtBottom = el
            ? (el.scrollHeight - el.scrollTop - el.clientHeight) <= 20
            : false

        await store.loadSessionItemsRanges(projectId.value, sessionId.value, ranges)

        if (el && wasAtBottom) {
            const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
            if (distanceFromBottom > 5) {
                await nextTick()
                scrollToBottomUntilStable()
            }
        }
    }
}
```

---

#### Part C: Template Updates

##### 1. Complete template structure

Replace the template section (around line 257-311):

```vue
<template>
    <div class="session-view">
        <!-- Header -->
        <header class="session-header" v-if="session">
            <!-- ... existing header content ... -->
        </header>

        <wa-divider></wa-divider>

        <!-- Compute pending state -->
        <div v-if="isComputePending" class="compute-pending-state">
            <wa-callout variant="warning">
                <wa-icon slot="icon" name="hourglass"></wa-icon>
                <span>Session en cours de préparation, veuillez patienter...</span>
            </wa-callout>
        </div>

        <!-- Error state -->
        <FetchErrorPanel
            v-else-if="hasError"
            :loading="isLoading"
            @retry="handleRetry"
        >
            Failed to load session content
        </FetchErrorPanel>

        <!-- Loading state -->
        <div v-else-if="isLoading" class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading...</span>
        </div>

        <!-- Items list (virtualized) -->
        <DynamicScroller
            :key="sessionId"
            ref="scrollerRef"
            v-else-if="visualItems.length > 0"
            :items="visualItems"
            :min-item-size="80"
            :buffer="200"
            key-field="lineNum"
            class="session-items"
            :emit-update="true"
            @update="onScrollerUpdate"
        >
            <template #default="{ item, index, active }">
                <DynamicScrollerItem
                    :item="item"
                    :active="active"
                    :size-dependencies="[getItemContent(item.lineNum), item.isExpanded]"
                    :data-index="index"
                    class="item-wrapper"
                >
                    <!-- Placeholder (no content loaded yet) -->
                    <div v-if="!getItemContent(item.lineNum)" class="item-placeholder">
                        <div class="line-number">{{ item.lineNum }}</div>
                        <wa-skeleton effect="sheen"></wa-skeleton>
                    </div>
                    <!-- Real item (Task 11 will add group toggle handling) -->
                    <SessionItem
                        v-else
                        :content="getItemContent(item.lineNum)"
                        :line-num="item.lineNum"
                    />
                </DynamicScrollerItem>
            </template>
        </DynamicScroller>

        <!-- Filtered empty state (all items hidden by current mode) -->
        <div v-else-if="visualItems.length === 0 && items.length > 0" class="empty-state">
            <wa-icon name="filter"></wa-icon>
            <span>Aucun élément à afficher dans ce mode</span>
        </div>

        <!-- Empty state -->
        <div v-else class="empty-state">
            No items in this session
        </div>
    </div>
</template>
```

##### 2. Add CSS

Add to `<style scoped>`:

```css
.compute-pending-state {
    padding: var(--wa-space-l);
}

.compute-pending-state wa-callout {
    max-width: 500px;
    margin: 0 auto;
}
```

---

#### Reference: Data Flow

```
Session navigation
    │
    ├── isComputePending === true?
    │       └── Show callout, wait for WebSocket update
    │
    └── isComputePending === false
            │
            └── loadSessionData(projectId, sessionId, lastLine)
                    │
                    ├── Promise.all([
                    │       loadSessionMetadata() → GET /items/metadata/
                    │       fetch() → GET /items/?range=...
                    │   ])
                    │
                    ├── initSessionItemsFromMetadata()
                    │       → Creates sessionItems with metadata, content=null
                    │       → Calls recomputeVisualItems()
                    │
                    └── updateSessionItemsContent()
                            → Updates content for fetched items
```

#### Reference: Template Condition Order

1. `isComputePending` - Computation not done yet
2. `hasError` - Fetch failed
3. `isLoading` - Currently fetching
4. `visualItems.length > 0` - Has content to display
5. `visualItems.length === 0 && items.length > 0` - All filtered out
6. else - Empty session

#### Reference: Key Changes

| Before | After |
|--------|-------|
| `loadInitialItems()` | `loadSessionData()` |
| `:items="items"` | `:items="visualItems"` |
| `key-field="line_num"` | `key-field="lineNum"` |
| `item.content` | `getItemContent(item.lineNum)` |
| Range-based lazy loading | Line-number-based lazy loading |

### 10. Mode Selector
UI in session header to switch between Debug/Normal/Simplified.

#### Why

Users need to switch between display modes:
- **Debug**: See everything (development, troubleshooting)
- **Normal**: Standard view, hide debug-only items
- **Simplified**: Priority items only, others grouped

#### Files to Modify

| Action | File | Description |
|--------|------|-------------|
| Modify | `frontend/src/views/SessionView.vue` | Add mode selector to header |
| Modify | `frontend/src/constants.js` | Already done in Task 7 (DISPLAY_MODES) |

#### Detailed Implementation

##### 1. Modify `frontend/src/views/SessionView.vue`

**1a.** Add imports

Add to the imports at the top of `<script setup>`:

```javascript
import { DISPLAY_MODES } from '../constants'
```

**1b.** Add computed property for display mode

Add after the other computed properties:

```javascript
// Display mode (global, from store)
const displayMode = computed(() => store.getDisplayMode)
```

**1c.** Add mode change handler

Add after the other functions:

```javascript
/**
 * Handle display mode change from the selector.
 */
function onModeChange(event) {
    const newMode = event.target.value
    store.setDisplayMode(newMode)
}
```

**1d.** Update the header template

Modify the session header (around lines 239-253) to include the mode selector:

```vue
<!-- Header -->
<header class="session-header" v-if="session">
    <div class="session-title">
        <h2 :title="sessionId">{{ truncateId(sessionId) }}</h2>
    </div>

    <!-- NEW: Mode selector -->
    <div class="session-controls">
        <wa-select
            :value="displayMode"
            @wa-change="onModeChange"
            size="small"
            class="mode-selector"
        >
            <wa-option :value="DISPLAY_MODES.DEBUG">Debug</wa-option>
            <wa-option :value="DISPLAY_MODES.NORMAL">Normal</wa-option>
            <wa-option :value="DISPLAY_MODES.SIMPLIFIED">Simplifié</wa-option>
        </wa-select>
    </div>

    <div class="session-meta">
        <span class="meta-item">
            <wa-icon name="list-numbers"></wa-icon>
            {{ session.last_line }} lines
        </span>
        <span class="meta-item">
            <wa-icon name="clock"></wa-icon>
            {{ formatDate(session.mtime) }}
        </span>
    </div>
</header>
```

**1e.** Add CSS for mode selector

Add to the `<style scoped>` section:

```css
.session-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
}

.session-title {
    flex: 1;
    min-width: 0;  /* Allow text truncation */
}

.session-controls {
    flex-shrink: 0;
}

.mode-selector {
    min-width: 120px;
}

.session-meta {
    width: 100%;
    display: flex;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
}
```

#### Display Mode Options

| Value | Label | Description |
|-------|-------|-------------|
| `debug` | Debug | Show all items (levels 1, 2, 3) |
| `normal` | Normal | Show levels 1 and 2, hide level 3 |
| `simplified` | Simplifié | Show level 1, collapse level 2 groups, hide level 3 |

#### Behavior

- Mode change is instant (no server request)
- `sessionVisualItems` recomputes automatically via `setDisplayMode` action
- Virtual scroller updates to show/hide items
- Mode is global (applies to all sessions)
- Mode is not persisted (resets on page reload)

#### Alternative: Button Group

If you prefer a more visual toggle instead of a dropdown, use `wa-button-group`:

```vue
<div class="session-controls">
    <wa-button-group>
        <wa-button
            :variant="displayMode === DISPLAY_MODES.DEBUG ? 'primary' : 'default'"
            @click="store.setDisplayMode(DISPLAY_MODES.DEBUG)"
            size="small"
        >
            Debug
        </wa-button>
        <wa-button
            :variant="displayMode === DISPLAY_MODES.NORMAL ? 'primary' : 'default'"
            @click="store.setDisplayMode(DISPLAY_MODES.NORMAL)"
            size="small"
        >
            Normal
        </wa-button>
        <wa-button
            :variant="displayMode === DISPLAY_MODES.SIMPLIFIED ? 'primary' : 'default'"
            @click="store.setDisplayMode(DISPLAY_MODES.SIMPLIFIED)"
            size="small"
        >
            Simplifié
        </wa-button>
    </wa-button-group>
</div>
```

This takes more horizontal space but provides clearer visual feedback.

#### UX Considerations

- Default mode is "Normal" (most common use case)
- Selector is positioned on the right of the header
- Using French labels ("Simplifié") to match the UI language

### 11. GroupToggle Component and Expand Logic
Ellipsis "..." component + modify scroller template for wrapper with toggle + item + store expand logic.

This task combines the UI component (GroupToggle) with the expand/collapse logic that was prepared in previous tasks.

#### Why

In simplified mode, collapsed groups show a toggle ("...") that users can click to expand. The toggle replaces the group's content when collapsed.

#### Files to Create/Modify

| Action | File | Description |
|--------|------|-------------|
| Create | `frontend/src/components/GroupToggle.vue` | New component for group expand/collapse toggle |
| Modify | `frontend/src/views/SessionView.vue` | Update scroller template to use GroupToggle |

---

#### Part A: GroupToggle Component

##### 1. Create `frontend/src/components/GroupToggle.vue`

```vue
<script setup>
/**
 * GroupToggle - Clickable toggle for expanding/collapsing item groups.
 *
 * Shows "..." when collapsed, with visual feedback on hover.
 * In simplified mode, this replaces collapsed group content.
 */
defineProps({
    /**
     * Whether the group is currently expanded.
     */
    expanded: {
        type: Boolean,
        default: false
    },
    /**
     * Number of items in the group (optional, for future display).
     */
    itemCount: {
        type: Number,
        default: 0
    }
})

const emit = defineEmits(['toggle'])

function handleClick() {
    emit('toggle')
}

function handleKeydown(event) {
    // Support Enter and Space for keyboard accessibility
    if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        emit('toggle')
    }
}
</script>

<template>
    <div
        class="group-toggle"
        :class="{ 'group-toggle--expanded': expanded }"
        role="button"
        tabindex="0"
        :aria-expanded="expanded"
        :aria-label="expanded ? 'Réduire le groupe' : 'Développer le groupe'"
        @click="handleClick"
        @keydown="handleKeydown"
    >
        <span class="toggle-indicator">...</span>
        <!-- Future: show item count -->
        <!-- <span v-if="itemCount > 0" class="toggle-count">({{ itemCount }})</span> -->
    </div>
</template>

<style scoped>
.group-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) var(--wa-space-m);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-radius-m);
    cursor: pointer;
    user-select: none;
    transition: background-color 0.15s ease;
}

.group-toggle:hover {
    background: var(--wa-color-surface-hover, rgba(0, 0, 0, 0.05));
}

.group-toggle:focus {
    outline: 2px solid var(--wa-color-primary);
    outline-offset: 2px;
}

.group-toggle--expanded {
    background: var(--wa-color-surface-alt);
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}

.toggle-indicator {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-subtle);
    letter-spacing: 0.1em;
}

.toggle-count {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
}
</style>
```

##### 2. Modify `frontend/src/views/SessionView.vue`

**2a.** Add import

Add to the imports at the top of `<script setup>`:

```javascript
import GroupToggle from '../components/GroupToggle.vue'
```

**2b.** Add toggleGroup function

Add after the existing helper functions:

```javascript
/**
 * Toggle a group's expanded state.
 * Called when clicking on a GroupToggle component.
 */
function toggleGroup(groupHeadLineNum) {
    store.toggleExpandedGroup(sessionId.value, groupHeadLineNum)
}
```

**2c.** Update the DynamicScroller template

Replace the content inside the `<template #default>` slot:

```vue
<DynamicScroller
    :key="sessionId"
    ref="scrollerRef"
    v-else-if="visualItems.length > 0"
    :items="visualItems"
    :min-item-size="80"
    :buffer="200"
    key-field="lineNum"
    class="session-items"
    :emit-update="true"
    @update="onScrollerUpdate"
>
    <template #default="{ item, index, active }">
        <DynamicScrollerItem
            :item="item"
            :active="active"
            :size-dependencies="[getItemContent(item.lineNum), item.isExpanded]"
            :data-index="index"
            class="item-wrapper"
        >
            <!-- Placeholder (no content loaded yet) -->
            <div v-if="!getItemContent(item.lineNum)" class="item-placeholder">
                <div class="line-number">{{ item.lineNum }}</div>
                <wa-skeleton effect="sheen"></wa-skeleton>
            </div>

            <!-- Group head (collapsed): show toggle only -->
            <GroupToggle
                v-else-if="item.isGroupHead && !item.isExpanded"
                :expanded="false"
                @toggle="toggleGroup(item.lineNum)"
            />

            <!-- Group head (expanded): show toggle + item content -->
            <div v-else-if="item.isGroupHead && item.isExpanded" class="group-expanded">
                <GroupToggle
                    :expanded="true"
                    @toggle="toggleGroup(item.lineNum)"
                />
                <SessionItem
                    :content="getItemContent(item.lineNum)"
                    :line-num="item.lineNum"
                />
            </div>

            <!-- Regular item: show item content -->
            <SessionItem
                v-else
                :content="getItemContent(item.lineNum)"
                :line-num="item.lineNum"
            />
        </DynamicScrollerItem>
    </template>
</DynamicScroller>
```

**2d.** Add CSS for expanded groups

Add to the `<style scoped>` section:

```css
.group-expanded {
    display: flex;
    flex-direction: column;
}

.group-expanded .group-toggle {
    margin-bottom: 0;
}

.group-expanded .session-item {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
}
```

##### Rendering Logic Summary

| Condition | Renders |
|-----------|---------|
| No content loaded | Placeholder with skeleton |
| isGroupHead && !isExpanded | GroupToggle only (collapsed) |
| isGroupHead && isExpanded | GroupToggle + SessionItem (expanded) |
| Regular item | SessionItem only |

##### Size Dependencies

```vue
:size-dependencies="[getItemContent(item.lineNum), item.isExpanded]"
```

Two dependencies:
1. `content`: Size changes when content loads
2. `isExpanded`: Size changes when group expands (toggle only → toggle + content)

##### SessionItem Stays Pure

The `SessionItem` component (at `frontend/src/components/SessionItem.vue`) doesn't need any changes. It just renders content:

```vue
<SessionItem
    :content="getItemContent(item.lineNum)"
    :line-num="item.lineNum"
/>
```

##### Accessibility

- `role="button"` for screen readers
- `tabindex="0"` for keyboard focus
- `aria-expanded` indicates current state
- `aria-label` provides context
- Keyboard support: Enter and Space trigger toggle

---

#### Part B: Expand Logic Verification

The expand/collapse logic was already implemented in Tasks 7 and 8. This section verifies the integration.

##### Files Already Modified

| File | Content |
|------|---------|
| `frontend/src/stores/data.js` | `toggleExpandedGroup` action (Task 7) |
| `frontend/src/stores/data.js` | `recomputeVisualItems` call in toggle (Task 8) |
| `frontend/src/views/SessionView.vue` | `toggleGroup` function and GroupToggle usage (this task) |

#### Verification Checklist

Verify that the following is in place:

##### 1. In `frontend/src/stores/data.js`

The `toggleExpandedGroup` action should:

```javascript
toggleExpandedGroup(sessionId, groupHeadLineNum) {
    // Ensure array exists for this session
    if (!this.localState.sessionExpandedGroups[sessionId]) {
        this.localState.sessionExpandedGroups[sessionId] = []
    }

    const groups = this.localState.sessionExpandedGroups[sessionId]
    const index = groups.indexOf(groupHeadLineNum)

    if (index >= 0) {
        // Collapse: remove from array
        groups.splice(index, 1)
    } else {
        // Expand: add to array
        groups.push(groupHeadLineNum)
    }

    // IMPORTANT: Recompute visual items after toggle
    this.recomputeVisualItems(sessionId)
},
```

##### 2. In `frontend/src/views/SessionView.vue`

The `toggleGroup` function:

```javascript
function toggleGroup(groupHeadLineNum) {
    store.toggleExpandedGroup(sessionId.value, groupHeadLineNum)
}
```

Usage in template:

```vue
<GroupToggle
    v-else-if="item.isGroupHead && !item.isExpanded"
    :expanded="false"
    @toggle="toggleGroup(item.lineNum)"
/>
```

#### What Happens on Toggle

```
User clicks GroupToggle
    │
    └── toggleGroup(lineNum) called
            │
            └── store.toggleExpandedGroup(sessionId, lineNum)
                    │
                    ├── Updates localState.sessionExpandedGroups[sessionId]
                    │
                    └── Calls recomputeVisualItems(sessionId)
                            │
                            └── Updates sessionVisualItems[sessionId]
                                    │
                                    └── Vue reactivity triggers re-render
                                            │
                                            └── DynamicScroller shows/hides items
```

#### Lazy Loading Consideration

When a group expands, its members become visible. They might not have content loaded yet:
- The items already have metadata (from initial metadata fetch)
- Content will be loaded on demand via `onScrollerUpdate` as they scroll into view
- No special preloading needed - the existing lazy loading handles it

#### Optional Enhancement: Preload Group Content

If you want immediate loading when expanding (smoother UX), add to `toggleExpandedGroup`:

```javascript
toggleExpandedGroup(sessionId, groupHeadLineNum) {
    // ... existing toggle logic ...

    const isExpanding = this.localState.sessionExpandedGroups[sessionId].includes(groupHeadLineNum)

    this.recomputeVisualItems(sessionId)

    // Optional: Preload content for newly visible items
    if (isExpanding) {
        this._preloadGroupContent(sessionId, groupHeadLineNum)
    }
},

// Private helper for preloading
async _preloadGroupContent(sessionId, groupHeadLineNum) {
    const items = this.sessionItems[sessionId]
    if (!items) return

    // Find items in this group without content
    const lineNumsToLoad = items
        .filter(item =>
            item.group_head === groupHeadLineNum &&
            item.display_level === 2 &&
            !item.content
        )
        .map(item => item.line_num)

    if (lineNumsToLoad.length > 0) {
        // Convert to ranges and load (reuse existing method pattern)
        const ranges = this._lineNumsToRanges(lineNumsToLoad)
        // Note: Need projectId from somewhere, or store it in sessionItems
    }
},
```

This is optional - the scroll-based lazy loading already works well.

#### Behavior with New Items

If a new item arrives via WebSocket and belongs to an expanded group:
1. `addSessionItems` adds the item to `sessionItems[sessionId]`
2. `addSessionItems` calls `recomputeVisualItems(sessionId)`
3. `computeVisualItems` checks if `item.group_head` is in `expandedGroups`
4. If yes, the item appears in `visualItems`
5. Vue re-renders and the item is visible

No special handling needed - the reactive chain handles it automatically.

---

## Tasks Tracking

- [x] #1 Models and Configuration
- [x] #2 Metadata Computation Functions
- [x] #3 Background Task
- [x] #4 Watcher (Live Sync)
- [x] #5 Serialization
- [x] #6 API and WebSocket
- [x] #7 Store: Structures and Helpers
- [x] #8 computeSessionVisualItems
- [x] #9 SessionView: Complete Refactoring
- [x] #10 Mode Selector
- [x] #11 GroupToggle Component and Expand Logic
- [x] Issue #1: Translate all French texts to English
- [x] Issue #2: Move sessionVisualItems to localState

## Status: COMPLETED

All 11 tasks have been implemented and reviewed successfully.

---

## Decisions made during implementation

(None for Task #1 - implementation followed the spec exactly)

(None for Task #2 - implementation followed the spec exactly)

(None for Task #3 - implementation followed the spec exactly)

(None for Task #5 - implementation followed the spec exactly)

(None for Task #6 - implementation followed the spec exactly)

(None for Task #7 - implementation followed the spec exactly)

(None for Task #8 - implementation followed the spec exactly)

---

## Resolved questions and doubts

(None for Task #1)

(None for Task #3)

---

## Issues

### Issue #1: All application texts must be in English

All UI strings, labels, messages, and aria-labels in the application code must be in English. Only documentation files (*.md) may contain French.

### Issue #2: sessionVisualItems should be in localState

`sessionVisualItems` was added directly to the store root state, but it should be in `localState` like `sessionItemsDisplayMode` and `sessionExpandedGroups` for consistency and proper persistence.
