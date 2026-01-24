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

from twicc_poc.core.models import Project, Session, SessionItem
from twicc_poc.core.serializers import serialize_project, serialize_session, serialize_session_item
from twicc_poc.sync import check_file_has_content, sync_session_items

logger = logging.getLogger(__name__)


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
    """Update project sessions_count and mtime from its non-empty sessions."""
    # Only count sessions with at least 1 line (non-empty)
    sessions = Session.objects.filter(project=project, archived=False, last_line__gt=0)
    project.sessions_count = sessions.count()
    max_mtime = sessions.order_by("-mtime").values_list("mtime", flat=True).first()
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])


@sync_to_async
def get_project_by_id(project_id: str) -> Project | None:
    """Get a project by ID, or None if not found."""
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


@sync_to_async
def get_or_create_session(session_id: str, project: Project) -> tuple[Session, bool]:
    """Get or create a session in the database."""
    return Session.objects.get_or_create(id=session_id, defaults={"project": project})


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
def sync_session_items_async(session: Session, file_path: Path) -> int:
    """Synchronize session items from a JSONL file (async wrapper).

    The session must already be saved to the database.

    Returns:
        The number of new items added (int >= 0)
    """
    return sync_session_items(session, file_path)


@sync_to_async
def get_new_session_items(session: Session, start_line: int) -> list[dict]:
    """Get session items added after start_line."""
    items = SessionItem.objects.filter(
        session=session,
        line_num__gt=start_line,
    ).order_by("line_num")
    return [serialize_session_item(item) for item in items]


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
        # Project folder deleted - mark as archived
        project = await get_project_by_id(project_id)
        if project and not project.archived:
            project.archived = True
            await sync_to_async(project.save)(update_fields=["archived"])
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
    elif project.archived:
        # Project was archived but folder reappeared
        project.archived = False
        await sync_to_async(project.save)(update_fields=["archived"])
        await broadcast_message(channel_layer, {
            "type": "project_updated",
            "project": serialize_project(project),
        })


async def sync_and_broadcast(
    path: Path,
    change_type: Change,
    channel_layer,
) -> None:
    """
    Handle a session file change.

    Synchronizes the session with the database and broadcasts updates.
    Empty sessions (0 lines) are ignored and not created in the database.
    """
    projects_dir = Path(settings.CLAUDE_PROJECTS_DIR)

    # Extract project_id and session_id from path
    try:
        relative_path = path.relative_to(projects_dir)
        parts = relative_path.parts
        if len(parts) != 2:
            # Not a direct child of a project folder
            return
        project_id = parts[0]
        session_id = path.stem
    except ValueError:
        return

    if change_type == Change.deleted:
        # Session file deleted - mark as archived
        session = await get_session_by_id(session_id)
        if session and not session.archived:
            session.archived = True
            await sync_to_async(session.save)(update_fields=["archived"])
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })
            # Update project metadata
            project = await get_project_by_id(project_id)
            if project:
                await update_project_metadata(project)
                project = await refresh_project(project)
                await broadcast_message(channel_layer, {
                    "type": "project_updated",
                    "project": serialize_project(project),
                })
        return

    # Check if session already exists in DB
    existing_session = await get_session_by_id(session_id)

    if existing_session is None:
        # New session file - check if it has content before creating
        has_content = await check_file_has_content_async(path)
        if not has_content:
            # Empty file (0 lines) - ignore completely
            return

        # Ensure project exists first
        project, project_created = await get_or_create_project(project_id)
        if project_created:
            await broadcast_message(channel_layer, {
                "type": "project_added",
                "project": serialize_project(project),
            })

        # File has content, create and save the session first
        session = Session(id=session_id, project=project)
        await sync_to_async(session.save)()

        # Now sync items (session is saved, has valid PK)
        items_result = await sync_session_items_async(session, path)

        await broadcast_message(channel_layer, {
            "type": "session_added",
            "session": serialize_session(session),
        })

        if items_result > 0:
            # Refresh session to get updated values
            session = await refresh_session(session)

            # Broadcast session update
            await broadcast_message(channel_layer, {
                "type": "session_updated",
                "session": serialize_session(session),
            })

            # Broadcast new items
            new_items = await get_new_session_items(session, 0)
            if new_items:
                await broadcast_message(channel_layer, {
                    "type": "session_items_added",
                    "session_id": session_id,
                    "project_id": project_id,
                    "items": new_items,
                })

            # Update project metadata
            await update_project_metadata(project)
            project = await refresh_project(project)
            await broadcast_message(channel_layer, {
                "type": "project_updated",
                "project": serialize_project(project),
            })
        return

    # Session exists in DB - sync items
    session = existing_session
    project = await get_project_by_id(project_id)
    if project is None:
        # Project should exist if session exists, but handle gracefully
        project, _ = await get_or_create_project(project_id)

    last_line_before = session.last_line
    items_result = await sync_session_items_async(session, path)

    if items_result > 0:
        # Refresh session to get updated values
        session = await refresh_session(session)

        # Broadcast session update
        await broadcast_message(channel_layer, {
            "type": "session_updated",
            "session": serialize_session(session),
        })

        # Broadcast new items
        new_items = await get_new_session_items(session, last_line_before)
        if new_items:
            await broadcast_message(channel_layer, {
                "type": "session_items_added",
                "session_id": session_id,
                "project_id": project_id,
                "items": new_items,
            })

        # Update project metadata
        await update_project_metadata(project)
        project = await refresh_project(project)
        await broadcast_message(channel_layer, {
            "type": "project_updated",
            "project": serialize_project(project),
        })
    elif session.archived:
        # Session file reappeared - unarchive
        session.archived = False
        await sync_to_async(session.save)(update_fields=["archived"])
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

            # Skip agent files
            if "/agent-" in path_str or path.name.startswith("agent-"):
                continue

            # Only process files that are direct children of project folders
            try:
                relative = path.relative_to(projects_dir)
                if len(relative.parts) != 2:
                    continue
            except ValueError:
                continue

            # Sync and broadcast session changes
            await sync_and_broadcast(path, change_type, channel_layer)
