"""
File watcher for JSONL files in Claude projects directory.

Uses watchfiles to monitor changes and broadcasts updates via WebSocket.
"""

import asyncio
import logging
from pathlib import Path

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from watchfiles import Change, awatch

from twicc.compute import load_project_directories, update_project_total_cost
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.core.serializers import (
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.sync import check_file_has_content, sync_session_items

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
    # Only count sessions (not subagents) with at least 1 line (non-empty)
    sessions = Session.objects.filter(
        project=project, stale=False, last_line__gt=0, type=SessionType.SESSION
    )
    project.sessions_count = sessions.count()
    max_mtime = sessions.order_by("-mtime").values_list("mtime", flat=True).first()
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])

    # Update total_cost
    update_project_total_cost(project.id)


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
) -> Session:
    """Create a session or subagent in the database.

    For subagents, parent_session must be provided.
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
        )
    else:
        return Session.objects.create(
            id=parsed.session_id,
            project=project,
        )


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
    return check_file_has_content(file_path)


@sync_to_async
def sync_session_items_async(session: Session, file_path: Path) -> tuple[list[int], list[int]]:
    """Synchronize session items from a JSONL file (async wrapper).

    The session must already be saved to the database.

    Returns:
        A tuple of:
        - List of line_nums of new items added (sorted)
        - List of line_nums of pre-existing items whose metadata was updated (sorted)
    """
    return sync_session_items(session, file_path)


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


async def sync_project_and_broadcast(
    path: Path,
    change_type: Change,
    channel_layer,
) -> None:
    """
    Handle a new project directory being created.

    Creates the project in the database and broadcasts project_added.
    """
    project_id = path.name

    if change_type == Change.deleted:
        # Project folder deleted - mark as stale
        project = await get_project_by_id(project_id)
        if project and not project.stale:
            project.stale = True
            await sync_to_async(project.save)(update_fields=["stale"])
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })
        return

    # New project folder
    project, created = await get_or_create_project(project_id)

    if created:
        await broadcast_message(channel_layer, {
            "type": "project_added",
            "project": serialize_project(project),
        })
    elif project.stale:
        # Project was stale but folder reappeared
        project.stale = False
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
    existing_session = await get_session_by_id(parsed.session_id)

    if existing_session is None:
        # New file - check if it has content before creating
        has_content = await check_file_has_content_async(path)
        if not has_content:
            # Empty file (0 lines) - ignore completely
            return

        # Ensure project exists first
        project, project_created = await get_or_create_project(parsed.project_id)
        if project_created:
            await broadcast_message(channel_layer, {
                "type": "project_added",
                "project": serialize_project(project),
            })

        # Create session (regular or subagent)
        session = await create_session(parsed, project, parent_session)

        # Sync items (session is saved, has valid PK)
        new_line_nums, modified_line_nums = await sync_session_items_async(session, path)

        # Refresh to get computed values
        session = await refresh_session(session)

        await broadcast_message(channel_layer, {
            "type": "session_added",
            "session": serialize_session(session),
        })

        if new_line_nums:
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
        return

    # Session exists in DB - sync items
    session = existing_session
    project = await get_project_by_id(parsed.project_id)
    if project is None:
        # Project should exist if session exists, but handle gracefully
        project, _ = await get_or_create_project(parsed.project_id)

    new_line_nums, modified_line_nums = await sync_session_items_async(session, path)

    if new_line_nums:
        # Refresh session to get updated values
        session = await refresh_session(session)

        # Broadcast session update
        await broadcast_message(channel_layer, {
            "type": "session_updated",
            "session": serialize_session(session),
        })

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

    # Load project directories cache at startup
    await sync_to_async(load_project_directories)()

    logger.info(f"Starting file watcher on: {projects_dir}")

    async for changes in awatch(projects_dir, stop_event=stop_event):
        for change_type, path_str in changes:
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
