"""
File watcher for JSONL files in Claude projects directory.

Uses watchfiles to monitor changes and broadcasts updates via WebSocket.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from watchfiles import Change, awatch

import twicc.search as search
from twicc.compute import AgentLinkUpdate, AgentStoppedUpdate, ToolResultUpdate, cache_agent_prompt, \
    check_agent_naturally_stopped, compute_item_cost_and_usage, \
    compute_item_metadata, \
    compute_item_metadata_live, create_agent_link_from_subagent, create_agent_link_from_tool_result, \
    create_agent_link_from_tool_use, create_tool_result_link_live, ensure_project_directory, ensure_project_git_root, \
    extract_item_timestamp, \
    extract_text_from_content, extract_title_from_user_message, get_cached_agent_prompt, get_message_content, \
    get_project_directory, get_project_git_root, is_agent_link_done, \
    is_tool_result_item, load_project_directories, \
    load_project_git_roots, read_head_branch, resolve_git_from_path, \
    transform_local_command_output, transform_task_notification, \
    update_project_metadata as _update_project_metadata_sync
from twicc.core.enums import ItemDisplayLevel, ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)

logger = logging.getLogger(__name__)


class ParsedPath:
    """Result of parsing a JSONL file path."""
    __slots__ = ('project_id', 'session_id', 'type', 'parent_session_id')

    def __init__(
        self,
        project_id: str,
        session_id: str,
        type: SessionType,
        parent_session_id: str | None = None,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.type = type
        self.parent_session_id = parent_session_id


def parse_jsonl_path(path: Path, projects_dir: Path) -> ParsedPath | None:
    """
    Parse a JSONL file path and determine its type.

    Valid paths:
    - project_id/session_id.jsonl -> Session
    - project_id/session_id/subagents/agent-xxx.jsonl -> Subagent

    Invalid paths (returns None):
    - project_id/agent-*.jsonl (old format, ignored)
    - Any other structure
    """
    try:
        relative = path.relative_to(projects_dir)
    except ValueError:
        return None

    parts = relative.parts

    if len(parts) == 2:
        # Format: project_id/xxx.jsonl
        project_id, filename = parts
        if filename.startswith("agent-"):
            # Old format agents at project level - ignore
            return None
        if filename.endswith(".jsonl"):
            session_id = filename.removesuffix(".jsonl")
            return ParsedPath(project_id, session_id, SessionType.SESSION)

    elif len(parts) == 4:
        # Format: project_id/session_id/subagents/agent-xxx.jsonl
        project_id, parent_session_id, subdir, filename = parts
        if (
            subdir == "subagents"
            and filename.startswith("agent-")
            and filename.endswith(".jsonl")
        ):
            agent_id = filename.removeprefix("agent-").removesuffix(".jsonl")
            return ParsedPath(
                project_id,
                agent_id,
                SessionType.SUBAGENT,
                parent_session_id,
            )

    return None


async def broadcast_message(channel_layer, message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": message,
        },
    )


@sync_to_async
def get_or_create_project(project_id: str) -> tuple[Project, bool]:
    """Get or create a project in the database."""
    return Project.objects.get_or_create(id=project_id)


@sync_to_async
def update_project_metadata(project: Project) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    _update_project_metadata_sync(project.id)


@sync_to_async
def get_project_by_id(project_id: str) -> Project | None:
    """Get a project by ID, or None if not found."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


@sync_to_async
def create_session(
    parsed: ParsedPath,
    project: Project,
    parent_session: Session | None = None,
    permission_mode: str | None = None,
    selected_model: str | None = None,
    effort: str | None = None,
    thinking_enabled: bool | None = None,
    claude_in_chrome: bool | None = None,
    context_max: int | None = None,
) -> Session:
    """Create a session or subagent in the database.

    For subagents, parent_session must be provided.
    If permission_mode, selected_model, effort, thinking_enabled, claude_in_chrome,
    or context_max is provided, it overrides the default.
    Returns the created session.
    """
    if parsed.type == SessionType.SUBAGENT:
        if parent_session is None:
            raise ValueError("parent_session is required for subagents")
        return Session.objects.create(
            id=parsed.session_id,
            project=project,
            type=SessionType.SUBAGENT,
            parent_session=parent_session,
            agent_id=parsed.session_id,
            compute_version=settings.CURRENT_COMPUTE_VERSION,
        )
    else:
        kwargs = dict(
            id=parsed.session_id,
            project=project,
            compute_version=settings.CURRENT_COMPUTE_VERSION,
        )
        if permission_mode is not None:
            kwargs["permission_mode"] = permission_mode
        if selected_model is not None:
            kwargs["selected_model"] = selected_model
        if effort is not None:
            kwargs["effort"] = effort
        if thinking_enabled is not None:
            kwargs["thinking_enabled"] = thinking_enabled
        if claude_in_chrome is not None:
            kwargs["claude_in_chrome"] = claude_in_chrome
        if context_max is not None:
            kwargs["context_max"] = context_max
        return Session.objects.create(**kwargs)


@sync_to_async
def get_session_by_id(session_id: str) -> Session | None:
    """Get a session by ID, or None if not found."""
    try:
        return Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        return None


@sync_to_async
def check_file_has_content_async(file_path: Path) -> bool:
    """Check if a JSONL file has any valid lines (async wrapper)."""
    if not file_path.exists():
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return True
    return False


@sync_to_async
def get_session_items(session: Session, line_nums: list[int]) -> list[dict]:
    """Get full session items (with content) by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).order_by("line_num")
    return [serialize_session_item(item) for item in items]


@sync_to_async
def get_items_metadata(session: Session, line_nums: list[int]) -> list[dict]:
    """Get metadata (without content) for specific items by line_nums."""
    if not line_nums:
        return []
    items = SessionItem.objects.filter(
        session=session,
        line_num__in=line_nums,
    ).defer('content').order_by("line_num")
    return [serialize_session_item_metadata(item) for item in items]


@sync_to_async
def refresh_session(session: Session) -> Session:
    """Refresh session from database."""
    session.refresh_from_db()
    return session


@sync_to_async
def refresh_project(project: Project) -> Project:
    """Refresh project from database."""
    project.refresh_from_db()
    return project


async def _index_new_items_for_search(session: Session, line_nums: list[int]) -> None:
    """Index new session items for full-text search.

    Only indexes user_message and assistant_message items.
    Errors are caught and logged to never crash the watcher.
    """
    try:
        if not search.is_initialized():
            return

        items = await sync_to_async(
            lambda: list(
                SessionItem.objects.filter(
                    session=session,
                    line_num__in=line_nums,
                    kind__in=[ItemKind.USER_MESSAGE, ItemKind.ASSISTANT_MESSAGE],
                )
            )
        )()

        indexed_count = 0
        for item in items:
            parsed = orjson.loads(item.content)
            content = get_message_content(parsed)
            text = search.extract_indexable_text(content)
            if text:
                await asyncio.to_thread(
                    search.index_document,
                    session.id,
                    session.project_id,
                    item.line_num,
                    text,
                    "user" if item.kind == ItemKind.USER_MESSAGE else "assistant",
                    item.timestamp,
                    session.archived,
                )
                indexed_count += 1

        if indexed_count > 0:
            await asyncio.to_thread(search.commit)
    except Exception:
        logger.exception("Error indexing session items for search (session=%s)", session.id)


async def sync_project_and_broadcast(
    path: Path,
    change_type: Change,
    channel_layer,
) -> None:
    """
    Handle a project directory being created or deleted.

    Projects are NOT created eagerly here. They are created lazily when the
    first session with content appears (in sync_and_broadcast). This avoids
    polluting the project list with empty folders (e.g. folders left behind
    after Claude sublimates old sessions).

    This handler only updates the stale flag on existing projects.
    Stale is based on working directory existence, not Claude folder.
    """
    project = await get_project_by_id(path.name)
    if project is None:
        return

    should_be_stale = project.directory is not None and not os.path.isdir(project.directory)
    if project.stale != should_be_stale:
        project.stale = should_be_stale
        await sync_to_async(project.save)(update_fields=["stale"])
        await broadcast_message(channel_layer, {
            "type": "project_updated",
            "project": serialize_project(project),
        })


async def sync_and_broadcast(
    path: Path,
    parsed: ParsedPath,
    change_type: Change,
    channel_layer,
) -> None:
    """
    Handle a session or subagent file change.

    Synchronizes with the database and broadcasts updates via WebSocket.
    Empty files (0 lines) are ignored and not created in the database.
    """
    is_subagent = parsed.type == SessionType.SUBAGENT

    # For subagents, verify parent session exists
    parent_session: Session | None = None
    if is_subagent:
        parent_session = await get_session_by_id(parsed.parent_session_id)
        if parent_session is None:
            # Parent session not yet synced, skip for now
            logger.debug(
                f"Skipping subagent {parsed.session_id}: "
                f"parent session {parsed.parent_session_id} not found"
            )
            return

    if change_type == Change.deleted:
        # File deleted - mark as stale
        session = await get_session_by_id(parsed.session_id)
        if session and not session.stale:
            session.stale = True
            await sync_to_async(session.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })
            # Update project metadata (includes total_cost which changes for subagents too)
            project = await get_project_by_id(parsed.project_id)
            if project:
                await update_project_metadata(project)
                project = await refresh_project(project)
                await broadcast_message(channel_layer, {
                    "type": "project_updated",
                    "project": serialize_project(project),
                })
        return

    # Check if session already exists in DB
    session = await get_session_by_id(parsed.session_id)

    # Ensure project exists first
    project, project_created = await get_or_create_project(parsed.project_id)
    if project_created:
        await broadcast_message(channel_layer, {
            "type": "project_added",
            "project": serialize_project(project),
        })

    # Track whether this session was just created via TwiCC (had pending settings).
    # Used below to broadcast an early session_updated even before the user message
    # appears in the JSONL, so the frontend can drop the draft flag immediately.
    pending = {}

    if session is None:
        # New file - check if it has content before creating
        has_content = await check_file_has_content_async(path)
        if not has_content:
            # Empty file (0 lines) - ignore completely
            return

        # Create session (regular or subagent)
        # Pop any pending settings set by the WS handler for new sessions
        from twicc.pending_settings import pop_pending

        pending = pop_pending(parsed.session_id)
        session = await create_session(
            parsed, project, parent_session,
            permission_mode=pending.get("permission_mode"),
            selected_model=pending.get("selected_model"),
            effort=pending.get("effort"),
            thinking_enabled=pending.get("thinking_enabled"),
            claude_in_chrome=pending.get("claude_in_chrome"),
            context_max=pending.get("context_max"),
        )

    old_title = session.title
    new_line_nums, modified_line_nums, agent_link_updates, tool_result_updates, agent_stopped_updates = await sync_session_items(session, path)
    title_changed = session.title != old_title

    if new_line_nums:
        # Refresh session to get computed values
        session = await refresh_session(session)

        # Only broadcast if session has user messages — empty sessions (e.g. just
        # system/metadata lines) stay silent in DB until a user message arrives.
        # Exception: TwiCC-initiated sessions (identified by having had pending
        # settings) get an early session_updated so the frontend drops the draft
        # flag immediately, without waiting for the user message to appear in JSONL.
        if session.user_message_count > 0 or pending:
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

        if session.user_message_count > 0:
            # Broadcast new items (with updated metadata of pre-existing items if any)
            new_items = await get_session_items(session, new_line_nums)
            if new_items:
                message = {
                    "type": "session_items_added",
                    "session_id": parsed.session_id,
                    "project_id": parsed.project_id,
                    "parent_session_id": parsed.parent_session_id,
                    "items": new_items,
                }
                if modified_line_nums:
                    updated_metadata = await get_items_metadata(session, modified_line_nums)
                    if updated_metadata:
                        message["updated_metadata"] = updated_metadata
                await broadcast_message(channel_layer, message)

            # For subagents, broadcast parent session update (costs have changed)
            if is_subagent and parent_session:
                parent_session = await refresh_session(parent_session)
                await broadcast_message(channel_layer, {
                    "type": "session_updated",
                    "session": serialize_session(parent_session),
                })

            # Update project metadata (includes total_cost which changes for subagents too)
            await update_project_metadata(project)
            project = await refresh_project(project)
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })

            # Broadcast agent link state changes (subagent linked)
            for update in agent_link_updates:
                await broadcast_message(channel_layer, {
                    "type": "agent_link_created",
                    "parent_session_id": update.parent_session_id,
                    "agent_session_id": update.agent_id,
                    "tool_use_id": update.tool_use_id,
                    "is_background": update.is_background,
                    "started_at": update.started_at.isoformat() if update.started_at else None,
                    "project_id": parsed.project_id,
                })

            # Broadcast tool result state changes
            for update in tool_result_updates:
                await broadcast_message(channel_layer, {
                    "type": "tool_state",
                    "session_id": update.session_id,
                    "tool_use_id": update.tool_use_id,
                    "result_count": update.result_count,
                    "completed_at": update.completed_at.isoformat() if update.completed_at else None,
                    "extra": update.extra,
                    "error": update.error,
                })

            # Broadcast session_updated for subagents that naturally finished
            for stopped in agent_stopped_updates:
                stopped_session = await get_session_by_id(stopped.agent_session_id)
                if stopped_session:
                    await broadcast_message(channel_layer, {
                        "type": "session_updated",
                        "session": serialize_session(stopped_session),
                    })

            # Index for full-text search (sessions only, not subagents)
            if not is_subagent:
                if title_changed:
                    # Title changed — full session re-index (Tantivy can only delete by session_id,
                    # not by session_id + from_role, so we must re-index everything)
                    try:
                        await asyncio.to_thread(search.reindex_session, session.id)
                    except Exception:
                        logger.exception("Error re-indexing session for search after title change (session=%s)", session.id)
                else:
                    await _index_new_items_for_search(session, new_line_nums)

    elif session.stale:
        # File reappeared - unstale
        session.stale = False
        await sync_to_async(session.save)(update_fields=["stale"])
        await broadcast_message(channel_layer, {
            "type": "session_updated",
            "session": serialize_session(session),
        })


# Global stop event for clean shutdown
_stop_event: asyncio.Event | None = None


def get_stop_event() -> asyncio.Event:
    """Get or create the global stop event."""
    global _stop_event
    if _stop_event is None:
        _stop_event = asyncio.Event()
    return _stop_event


def stop_watcher() -> None:
    """Signal the watcher to stop."""
    if _stop_event is not None:
        _stop_event.set()


async def start_watcher() -> None:
    """
    Start the file watcher for Claude projects directory.

    Monitors all changes recursively and dispatches to appropriate handlers.
    """
    channel_layer = get_channel_layer()
    projects_dir = Path(settings.CLAUDE_PROJECTS_DIR)
    stop_event = get_stop_event()

    if not projects_dir.exists():
        logger.warning(f"Projects directory does not exist: {projects_dir}")
        return

    # Load project caches at startup
    await sync_to_async(load_project_directories)()
    await sync_to_async(load_project_git_roots)()

    logger.info(f"Starting file watcher on: {projects_dir}")

    async for changes in awatch(projects_dir, stop_event=stop_event):
        for change_type, path_str in changes:
            try:
                path = Path(path_str)

                # Handle project directories (direct children of projects_dir)
                if path.parent == projects_dir and (path.is_dir() or change_type == Change.deleted):
                    await sync_project_and_broadcast(path, change_type, channel_layer)
                    continue

                # Skip non-jsonl files
                if not path_str.endswith(".jsonl"):
                    continue

                # Parse path to determine type (session or subagent)
                parsed = parse_jsonl_path(path, projects_dir)
                if parsed is None:
                    # Invalid path (e.g., old-style agent-*.jsonl at project level)
                    continue

                # Sync and broadcast (works for both sessions and subagents)
                await sync_and_broadcast(path, parsed, change_type, channel_layer)
            except Exception:
                logger.exception("Error processing watcher change %s on %s", change_type, path_str)


@sync_to_async
def sync_session_items(
    session: Session, file_path: Path
) -> tuple[list[int], list[int], list[AgentLinkUpdate], list[ToolResultUpdate], list[AgentStoppedUpdate]]:
    """
    Synchronize session items from a JSONL file.

    Reads new lines from the file starting at last_offset.
    The session must already be saved to the database.

    Also handles session title updates:
    - First USER_MESSAGE sets initial title if not already set
    - CUSTOM_TITLE items update the title of their target session

    Returns:
        A tuple of:
        - List of line_nums of new items added (sorted)
        - List of line_nums of pre-existing items whose metadata was updated (sorted)
        - List of AgentLinkUpdate for agent state changes to broadcast
        - List of ToolResultUpdate for tool completion state changes to broadcast
        - List of AgentStoppedUpdate for subagents that naturally finished
    """
    if not file_path.exists():
        return [], [], [], [], []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed and no new data appended, nothing to do.
    # Check file size too: mtime has ~1s resolution, so two writes within the same second
    # share the same mtime. Without the size check, the second write would be silently skipped.
    if session.mtime == file_mtime and session.last_offset >= stat.st_size:
        return [], [], [], [], []

    with open(file_path, "r", encoding="utf-8") as f:
        # Seek to last known position
        f.seek(session.last_offset)

        # Read remaining content
        new_content = f.read()
        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return [], [], [], [], []

        # Split into lines (filter out empty lines)
        lines = [line for line in new_content.split("\n") if line.strip()]

        # Capture file position and save offset+mtime immediately to release the file
        new_offset = f.tell()

    session.last_offset = new_offset
    session.mtime = file_mtime

    if not lines:
        session.save(update_fields=["last_offset", "mtime"])
        return [], [], [], [], []

    # Create SessionItem objects for bulk insert
    items_to_create: list[tuple[SessionItem, dict]] = []
    current_line_num = session.last_line

    # Track title updates (session_id -> title)
    session_title_updates: dict[str, str] = {}
    # Track if we've already set initial title for this session (from first user message ever)
    initial_title_needs_set = session.title is None

    # Track first timestamp in this batch (for session.created_at)
    first_timestamp: datetime | None = None

    # Track lifecycle timestamps for this batch
    last_started_at_update: datetime | None = None  # Set if a SessionStart hookEvent is found
    last_updated_at: datetime | None = None  # Last item timestamp in this batch
    last_new_content_at: datetime | None = None  # Last assistant message timestamp in this batch

    # Track last seen values for runtime environment fields
    first_cwd: str | None = None  # First cwd in this batch
    last_cwd: str | None = None
    last_cwd_git_branch: str | None = None
    last_model: str | None = None
    last_slug: str | None = None

    # Track agent link updates to broadcast after processing
    agent_link_updates: list[AgentLinkUpdate] = []
    # Track tool result updates to broadcast after processing
    tool_result_updates: list[ToolResultUpdate] = []
    # Track subagents that naturally finished
    agent_stopped_updates: list[AgentStoppedUpdate] = []

    # For subagents: track if we need to create the link between the agent and the parent session tool use
    subagent_needs_link = (
        session.type == SessionType.SUBAGENT
        and session.parent_session_id
        and not is_agent_link_done(session.parent_session_id, session.id)
    )

    # Load existing message_ids for deduplication of cost computation
    seen_message_ids: set[str] = set(
        SessionItem.objects.filter(
            session_id=session.id,
            message_id__isnull=False,
        ).values_list('message_id', flat=True)
    )

    for line in lines:
        line = line.strip()
        if not line:
            line = "{}"
        current_line_num += 1
        item = SessionItem(
            session=session,
            line_num=current_line_num,
            content=line,
        )
        try:
            parsed = orjson.loads(line)
        except orjson.JSONDecodeError:
            parsed = {}

        # Transform task-notification XML into standard tool_result format
        new_content = transform_task_notification(parsed)
        if new_content is None:
            # Transform local-command-stdout into assistant_message format
            new_content = transform_local_command_output(parsed)

        if new_content is not None:
            item.content = new_content

        # Pre-compute display_level (no group info yet)
        metadata = compute_item_metadata(parsed)
        item.display_level = metadata['display_level']
        item.kind = metadata['kind']

        # Extract timestamp
        item.timestamp = extract_item_timestamp(parsed)
        if first_timestamp is None and item.timestamp is not None:
            first_timestamp = item.timestamp

        # Track lifecycle timestamps
        if item.timestamp is not None:
            last_updated_at = item.timestamp
        if item.timestamp is not None and item.kind == ItemKind.ASSISTANT_MESSAGE:
            last_new_content_at = item.timestamp
        # Detect SessionStart hookEvent to update last_started_at
        if (
            item.timestamp is not None
            and parsed.get('type') == 'progress'
            and isinstance(parsed.get('data'), dict)
            and parsed['data'].get('hookEvent') == 'SessionStart'
        ):
            last_started_at_update = item.timestamp

        # Compute cost and context usage (with deduplication)
        compute_item_cost_and_usage(item, parsed, seen_message_ids)

        items_to_create.append((item, parsed))

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
        if item_slug := parsed.get('slug'):
            last_slug = item_slug

        # Handle title extraction
        if item.kind == ItemKind.USER_MESSAGE and initial_title_needs_set:
            # First user message in this batch: set initial title
            title = extract_title_from_user_message(parsed)
            if title:
                session_title_updates[session.id] = title
                initial_title_needs_set = False

        # For subagents: create agent link from first user_message
        if subagent_needs_link and (agent_id := parsed.get('agentId')):
            prompt = get_cached_agent_prompt(session.parent_session_id, agent_id)
            if not prompt:
                # try to get from db
                if (first_user_message := session.items.filter(kind=ItemKind.USER_MESSAGE).first()) is not None:
                    try:
                        first_user_message_parsed = orjson.loads(first_user_message.content)
                    except orjson.JSONDecodeError:
                        pass
                    else:
                        prompt = extract_text_from_content(get_message_content(first_user_message_parsed))
                if not prompt:
                    # not in db so we may be the first one
                    if item.kind == ItemKind.USER_MESSAGE:
                        content = get_message_content(parsed)
                        prompt = extract_text_from_content(content)

                if prompt:
                    cache_agent_prompt(session.parent_session_id, agent_id, prompt)
                    agent_update = create_agent_link_from_subagent(
                        parent_session_id=session.parent_session_id,
                        agent_id=agent_id,
                        agent_prompt=prompt,
                    )
                    if agent_update:
                        agent_link_updates.append(agent_update)
                        subagent_needs_link = False

        if item.kind == ItemKind.CUSTOM_TITLE:
            # Custom title: update the target session's title
            custom_title = parsed.get('customTitle')
            target_session_id = parsed.get('sessionId', session.id)
            if custom_title and isinstance(custom_title, str):
                session_title_updates[target_session_id] = custom_title

    # Bulk create all items
    items_only = [item for item, _ in items_to_create]
    SessionItem.objects.bulk_create(items_only, ignore_conflicts=True)

    # Track line_nums of new and updated items
    new_line_nums: set[int] = {item.line_num for item in items_only}
    modified_line_nums: set[int] = set()

    # Second pass: compute group membership, tool_result links, and update cost/usage/timestamp fields
    for item, parsed in items_to_create:
        # Build the update dict for this item (includes cost/usage/timestamp fields)
        update_fields = {
            'message_id': item.message_id,
            'cost': item.cost,
            'context_usage': item.context_usage,
            'timestamp': item.timestamp,
        }

        # Group membership for COLLAPSIBLE and ALWAYS items
        if item.display_level in (ItemDisplayLevel.COLLAPSIBLE, ItemDisplayLevel.ALWAYS):
            item_modified_lines = compute_item_metadata_live(session.id, item, parsed)
            modified_line_nums.update(item_modified_lines)
            update_fields['group_head'] = item.group_head
            update_fields['group_tail'] = item.group_tail
            update_fields['git_directory'] = item.git_directory
            update_fields['git_branch'] = item.git_branch

        # Update the item in DB with all computed fields
        SessionItem.objects.filter(
            session=session,
            line_num=item.line_num
        ).update(**update_fields)

        # Tool result links (tool_result items are DEBUG_ONLY)
        if is_tool_result_item(parsed):
            tool_result_update = create_tool_result_link_live(session.id, item, parsed)
            if tool_result_update:
                tool_result_updates.append(tool_result_update)
                # Check if this completes a subagent naturally
                if stopped := check_agent_naturally_stopped(session.id, tool_result_update):
                    agent_stopped_updates.append(stopped)
            # Also check for agent links (Task tool_result with agentId)
            if update := create_agent_link_from_tool_result(session.id, item, parsed):
                agent_link_updates.append(update)

        # For parent sessions: check if this assistant message contains Task tool_use(s)
        # and try to link them to existing subagents (handles the race condition where
        # the subagent was synced before this Task tool_use existed).
        # Note: Task tool_uses are often in CONTENT_ITEMS lines (streaming splits
        # the text and tool_use into separate lines, and tool_use-only lines have
        # no visible content so they're classified as CONTENT_ITEMS, not ASSISTANT_MESSAGE).
        if session.type == SessionType.SESSION and item.kind in (ItemKind.ASSISTANT_MESSAGE, ItemKind.CONTENT_ITEMS):
            agent_link_updates.extend(create_agent_link_from_tool_use(session.id, item, parsed))

    # Check if project needs git_root resolution
    # (a session item resolved git info but project has no git_root yet)
    if any(item.git_directory for item, _ in items_to_create) and get_project_git_root(session.project_id) is None:
        ensure_project_git_root(session.project_id)

    # Apply title updates
    for target_session_id, title in session_title_updates.items():
        Session.objects.filter(id=target_session_id).update(title=title)
        # If updating the current session, update the object too
        if target_session_id == session.id:
            session.title = title

    # Update session tracking fields
    session.last_line = current_line_num

    # Recompute user_message_count using the optimized index
    session.user_message_count = SessionItem.objects.filter(
        session=session,
        kind=ItemKind.USER_MESSAGE
    ).count()

    # Update session cost and context usage from the new items
    # Find last context_usage among new items (most recent non-null value)
    for item, _ in reversed(items_to_create):
        if item.context_usage is not None:
            session.context_usage = item.context_usage
            break

    # Recalculate costs from DB (idempotent)
    session.recalculate_costs()

    # Update runtime environment fields if changed
    if last_cwd and last_cwd != session.cwd:
        # Update project directory only on first sync (when session.cwd was None)
        # The first cwd of a session is the project directory (where Claude Code was launched)
        # Only for real sessions, not subagents (which may be launched from a different directory)
        if session.cwd is None and first_cwd and session.type == SessionType.SESSION:
            ensure_project_directory(session.project_id, first_cwd)
        session.cwd = last_cwd
    if last_cwd_git_branch and last_cwd_git_branch != session.cwd_git_branch:
        session.cwd_git_branch = last_cwd_git_branch
    if last_model and last_model != session.model:
        session.model = last_model
    if last_slug and last_slug != session.slug:
        session.slug = last_slug

    # Update resolved git directory/branch from the latest item that has one
    # (items are processed in order, so the last one wins)
    for item, _ in reversed(items_to_create):
        if item.git_directory:
            if item.git_directory != session.git_directory or item.git_branch != session.git_branch:
                session.git_directory = item.git_directory
                session.git_branch = item.git_branch
            break

    # Fallback: if no item provided git info, try resolving from the session's cwd.
    # This handles sessions where the agent only uses Bash (no tool_use with file paths),
    # so resolve_git_for_item has nothing to work with.
    if not session.git_directory and session.cwd:
        cwd_git = resolve_git_from_path(session.cwd, use_cache=False)
        if cwd_git:
            session.git_directory, session.git_branch = cwd_git

    # Validate git state: verify git_directory still exists on disk and refresh branch.
    # This catches Bash commands that modify git state (git checkout, worktree deletion, etc.).
    if session.git_directory:
        if os.path.isdir(session.git_directory):
            # Directory exists: refresh branch from HEAD in case of git checkout
            head_path = os.path.join(session.git_directory, '.git', 'HEAD')
            if not os.path.isfile(head_path):
                # Worktree: .git is a file, read gitdir path to find HEAD
                git_file = os.path.join(session.git_directory, '.git')
                if os.path.isfile(git_file):
                    try:
                        with open(git_file, 'r') as f:
                            content = f.read().strip()
                        if content.startswith('gitdir: '):
                            head_path = os.path.join(content[len('gitdir: '):], 'HEAD')
                    except OSError:
                        head_path = None
                else:
                    head_path = None
            if head_path:
                branch = read_head_branch(head_path)
                if branch and branch != session.git_branch:
                    session.git_branch = branch
        else:
            # git_directory no longer exists: re-resolve through fallback chain
            resolved = None
            if session.cwd and os.path.isdir(session.cwd):
                resolved = resolve_git_from_path(session.cwd, use_cache=False)
            if not resolved:
                project_git_root = get_project_git_root(session.project_id)
                if project_git_root and os.path.isdir(project_git_root):
                    # Already a resolved git root, re-read branch from it
                    head_path = os.path.join(project_git_root, '.git', 'HEAD')
                    branch = read_head_branch(head_path)
                    if branch:
                        resolved = (project_git_root, branch)
            if not resolved:
                project_directory = get_project_directory(session.project_id)
                if project_directory and os.path.isdir(project_directory):
                    resolved = resolve_git_from_path(project_directory, use_cache=False)
            if resolved:
                session.git_directory, session.git_branch = resolved
            else:
                session.git_directory = None
                session.git_branch = None

    is_new_session = session.created_at is None and first_timestamp is not None
    if is_new_session:
        session.created_at = first_timestamp

    # Update lifecycle timestamps
    if last_started_at_update is not None:
        session.last_started_at = last_started_at_update
    elif is_new_session:
        # First sync: initialize last_started_at to created_at
        session.last_started_at = first_timestamp
    if last_updated_at is not None:
        session.last_updated_at = last_updated_at
    if last_new_content_at is not None:
        session.last_new_content_at = last_new_content_at

    # Recalculate activity counters for affected days (only items that contribute)
    affected_days = {
        item.timestamp.date()
        for item, _ in items_to_create
        if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost)
    }
    if is_new_session and session.type == SessionType.SESSION and first_timestamp:
        affected_days.add(first_timestamp.date())

    session.save(update_fields=["last_offset", "last_line", "mtime", "user_message_count", "context_usage", "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch", "git_directory", "git_branch", "model", "slug", "created_at", "last_started_at", "last_updated_at", "last_new_content_at"])

    # Recalculate activities after session.save (needs created_at in DB for session_count)
    from twicc.core.models import PeriodicActivity
    PeriodicActivity.recalculate_for_days(session.project_id, affected_days)

    # If this is a subagent, propagate cost to parent session
    if session.type == SessionType.SUBAGENT and session.parent_session_id:
        _update_parent_session_costs(session.parent_session_id)

    # Exclude new items from modified_line_nums
    return sorted(new_line_nums), sorted(modified_line_nums - new_line_nums), agent_link_updates, tool_result_updates, agent_stopped_updates


def _update_parent_session_costs(parent_session_id: str) -> None:
    """
    Recalculate the parent session's costs from SessionItem data.

    Called when a subagent's cost changes. Uses recalculate_costs()
    which sums SessionItem.cost for both the session and its subagents.
    """
    try:
        parent = Session.objects.get(id=parent_session_id)
    except Session.DoesNotExist:
        return
    parent.recalculate_costs()
    parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])
