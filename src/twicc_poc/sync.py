"""
Synchronization logic for JSONL files from Claude projects.

Scans CLAUDE_PROJECTS_DIR for projects and sessions, synchronizes them
with the database, and reads new lines from modified JSONL files.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from twicc_poc.compute import compute_item_metadata, compute_item_metadata_live
from twicc_poc.core.enums import ItemDisplayLevel
from twicc_poc.core.models import Project, Session, SessionItem

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid session file (*.jsonl but not agent-*.jsonl)."""
    return path.suffix == ".jsonl" and not path.name.startswith("agent-")


def get_projects_dir() -> Path:
    """Get the Claude projects directory from settings."""
    return Path(settings.CLAUDE_PROJECTS_DIR)


def scan_projects() -> set[str]:
    """Scan the projects directory and return the set of project folder names."""
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return set()
    return {d.name for d in projects_dir.iterdir() if d.is_dir()}


def scan_sessions(project_id: str) -> dict[str, Path]:
    """
    Scan a project folder and return session files.

    Returns a dict mapping session id (filename without extension) to Path.
    """
    project_dir = get_projects_dir() / project_id
    if not project_dir.exists():
        return {}
    return {
        f.stem: f
        for f in project_dir.iterdir()
        if f.is_file() and is_session_file(f)
    }


def check_file_has_content(file_path: Path) -> bool:
    """
    Check if a JSONL file has any valid lines (non-empty, non-whitespace).

    This function performs no database operations and is used to determine
    if a session should be created before saving it.
    """
    if not file_path.exists():
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return True
    return False


def sync_session_items(session: Session, file_path: Path) -> tuple[list[int], list[int]]:
    """
    Synchronize session items from a JSONL file.

    Reads new lines from the file starting at last_offset.
    The session must already be saved to the database.

    Returns:
        A tuple of:
        - List of line_nums of new items added (sorted)
        - List of line_nums of pre-existing items whose metadata was updated (sorted)
    """
    if not file_path.exists():
        return [], []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed, nothing to do
    if session.mtime == file_mtime:
        return [], []

    new_line_nums: set[int] = set()
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
            return [], []

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

            # Track line_nums of new items
            new_line_nums = {item.line_num for item in items_to_create}

            # Second pass: compute group membership for COLLAPSIBLE and ALWAYS items
            # - COLLAPSIBLE: need group_head to know which group they belong to
            # - ALWAYS: need group_head (prefix) and group_tail (suffix) for connected groups
            for item in items_to_create:
                if item.display_level in (ItemDisplayLevel.COLLAPSIBLE, ItemDisplayLevel.ALWAYS):
                    item_modified_lines = compute_item_metadata_live(session.id, item, item.content)
                    modified_line_nums.update(item_modified_lines)
                    # Need to save the group info
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

    # Exclude new items from modified_line_nums
    return sorted(new_line_nums), sorted(modified_line_nums - new_line_nums)


def sync_project(
    project_id: str,
    on_session_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, int]:
    """
    Synchronize a single project and its sessions.

    Args:
        project_id: The project folder name.
        on_session_progress: Optional callback called after each session sync
            with (session_id, current_index, total_sessions).

    Returns a dict with sync statistics:
        - sessions_created: number of new sessions
        - sessions_archived: number of sessions marked as archived
        - items_added: total number of new session items
    """
    stats = {
        "sessions_created": 0,
        "sessions_archived": 0,
        "items_added": 0,
    }

    # Get or create project
    project, _ = Project.objects.get_or_create(id=project_id)

    # Scan session files on disk
    session_files = scan_sessions(project_id)
    disk_session_ids = set(session_files.keys())

    # Get existing sessions from database
    db_sessions = {s.id: s for s in Session.objects.filter(project=project)}
    db_session_ids = set(db_sessions.keys())

    # Track which sessions are non-empty (for counting and mtime)
    non_empty_session_ids: set[str] = set()

    # Process sessions that exist on disk
    sessions_to_sync = list(disk_session_ids)
    total_sessions = len(sessions_to_sync)
    max_mtime = 0.0

    for idx, session_id in enumerate(sessions_to_sync, start=1):
        file_path = session_files[session_id]

        # Check if session exists in DB
        if session_id in db_session_ids:
            session = db_sessions[session_id]
        else:
            # Create session only if file has content
            if not check_file_has_content(file_path):
                # Empty file (0 lines) - skip creating session
                if on_session_progress:
                    on_session_progress(session_id, idx, total_sessions)
                continue

            # File has content, create and save the session first
            session = Session(id=session_id, project=project)
            session.save()
            stats["sessions_created"] += 1

            # Now sync items (session is saved, has valid PK)
            new_line_nums, _ = sync_session_items(session, file_path)

            # Track as non-empty and update stats
            non_empty_session_ids.add(session_id)
            stats["items_added"] += len(new_line_nums)

            # Track max mtime for project
            if session.mtime > max_mtime:
                max_mtime = session.mtime

            if on_session_progress:
                on_session_progress(session_id, idx, total_sessions)
            continue

        # Session exists in DB, sync items
        new_line_nums, _ = sync_session_items(session, file_path)
        stats["items_added"] += len(new_line_nums)

        # Track as non-empty if it has lines
        if session.last_line > 0:
            non_empty_session_ids.add(session_id)
            # Track max mtime for project (only for non-empty sessions)
            if session.mtime > max_mtime:
                max_mtime = session.mtime

        if on_session_progress:
            on_session_progress(session_id, idx, total_sessions)

    # Mark archived sessions (exist in DB but not on disk)
    archived_session_ids = db_session_ids - disk_session_ids
    if archived_session_ids:
        Session.objects.filter(id__in=archived_session_ids, archived=False).update(
            archived=True
        )
        stats["sessions_archived"] += len(archived_session_ids)

    # Update project metadata (only count non-empty sessions)
    project.sessions_count = len(non_empty_session_ids)
    project.mtime = max_mtime
    if project.archived:
        project.archived = False
    project.save(update_fields=["sessions_count", "mtime", "archived"])

    return stats


def sync_all(
    on_project_start: Callable[[str, int, int], None] | None = None,
    on_project_done: Callable[[str, dict[str, int]], None] | None = None,
    on_session_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, int]:
    """
    Synchronize all projects from CLAUDE_PROJECTS_DIR.

    Args:
        on_project_start: Callback called before syncing a project
            with (project_id, current_index, total_projects).
        on_project_done: Callback called after syncing a project
            with (project_id, project_stats).
        on_session_progress: Callback passed to sync_project.

    Returns aggregate statistics.
    """
    stats = {
        "projects_created": 0,
        "projects_archived": 0,
        "sessions_created": 0,
        "sessions_archived": 0,
        "items_added": 0,
    }

    # Scan project folders on disk
    disk_project_ids = scan_projects()

    # Get existing projects from database
    db_project_ids = set(Project.objects.values_list("id", flat=True))

    # Create missing projects
    new_project_ids = disk_project_ids - db_project_ids
    for project_id in new_project_ids:
        Project.objects.create(id=project_id)
        stats["projects_created"] += 1

    # Mark archived projects (exist in DB but not on disk)
    archived_project_ids = db_project_ids - disk_project_ids
    if archived_project_ids:
        Project.objects.filter(id__in=archived_project_ids, archived=False).update(
            archived=True
        )
        stats["projects_archived"] += len(archived_project_ids)

    # Sync each project on disk
    projects_to_sync = sorted(disk_project_ids)
    total_projects = len(projects_to_sync)

    for idx, project_id in enumerate(projects_to_sync, start=1):
        if on_project_start:
            on_project_start(project_id, idx, total_projects)

        project_stats = sync_project(project_id, on_session_progress=on_session_progress)

        # Aggregate stats
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["sessions_archived"] += project_stats["sessions_archived"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    return stats


class ProgressDisplay:
    """Console progress display for sync operations."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.start_time = time.time()
        self.current_project = ""
        self.project_index = 0
        self.total_projects = 0
        self.session_index = 0
        self.total_sessions = 0

    def _write(self, text: str, end: str = "\n") -> None:
        """Write text to the output stream."""
        self.stream.write(text + end)
        self.stream.flush()

    def _clear_line(self) -> None:
        """Clear the current line (for dynamic updates)."""
        self.stream.write("\r\033[K")
        self.stream.flush()

    def _format_time(self, seconds: float) -> str:
        """Format seconds as mm:ss or hh:mm:ss."""
        if seconds < 3600:
            return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
        return f"{int(seconds // 3600):02d}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"

    def _progress_bar(self, current: int, total: int, width: int = 30) -> str:
        """Generate a progress bar string."""
        if total == 0:
            return "[" + " " * width + "]"
        filled = int(width * current / total)
        return "[" + "=" * filled + " " * (width - filled) + "]"

    def on_project_start(self, project_id: str, index: int, total: int) -> None:
        """Called when starting to sync a project."""
        self.current_project = project_id
        self.project_index = index
        self.total_projects = total
        self.session_index = 0
        self.total_sessions = 0

        elapsed = time.time() - self.start_time
        if index > 1:
            # Estimate remaining time based on average time per project
            avg_time = elapsed / (index - 1)
            remaining = avg_time * (total - index + 1)
            time_info = f" | Elapsed: {self._format_time(elapsed)} | ETA: {self._format_time(remaining)}"
        else:
            time_info = ""

        self._clear_line()
        self._write(f"[{index}/{total}] Syncing: {project_id}{time_info}")

    def on_session_progress(self, session_id: str, index: int, total: int) -> None:
        """Called after syncing each session."""
        self.session_index = index
        self.total_sessions = total

        bar = self._progress_bar(index, total)
        self._clear_line()
        self._write(f"         {bar} {index}/{total} sessions", end="")

    def on_project_done(self, project_id: str, stats: dict[str, int]) -> None:
        """Called when a project sync is complete."""
        self._clear_line()
        sessions_info = []
        if stats["sessions_created"]:
            sessions_info.append(f"+{stats['sessions_created']} new")
        if stats["sessions_archived"]:
            sessions_info.append(f"-{stats['sessions_archived']} archived")
        if stats["items_added"]:
            sessions_info.append(f"+{stats['items_added']} items")

        info = ", ".join(sessions_info) if sessions_info else "no changes"
        self._write(f"  [done] {project_id}: {info}")

    def on_sync_complete(self, stats: dict[str, int]) -> None:
        """Called when the entire sync is complete."""
        elapsed = time.time() - self.start_time
        self._write("")
        self._write(f"Sync complete in {self._format_time(elapsed)}")
        self._write(f"  Projects: {stats['projects_created']} created, {stats['projects_archived']} archived")
        self._write(f"  Sessions: {stats['sessions_created']} created, {stats['sessions_archived']} archived")
        self._write(f"  Items: {stats['items_added']} added")


def sync_all_with_progress(stream=None) -> dict[str, int]:
    """
    Synchronize all projects with console progress display.

    This is the main entry point for interactive sync with visual feedback.
    """
    display = ProgressDisplay(stream)

    stats = sync_all(
        on_project_start=display.on_project_start,
        on_project_done=display.on_project_done,
        on_session_progress=display.on_session_progress,
    )

    display.on_sync_complete(stats)
    return stats
