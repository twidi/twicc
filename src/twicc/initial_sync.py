"""
Synchronization logic for JSONL files from Claude projects.

Scans CLAUDE_PROJECTS_DIR for projects and sessions, synchronizes them
with the database, and reads new lines from modified JSONL files.
"""

from __future__ import annotations

import logging

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from twicc.compute import (
    ensure_project_git_root,
    update_project_total_cost,
)
from twicc.core.models import Project, Session, SessionItem, SessionType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid session file (*.jsonl but not agent-*.jsonl)."""
    return path.suffix == ".jsonl" and not path.name.startswith("agent-")


def is_subagent_file(path: Path) -> bool:
    """Check if a path is a valid subagent file in the correct location.

    Valid subagent files:
    - Must be in a 'subagents' directory
    - Must start with 'agent-' and end with '.jsonl'
    """
    return (
        path.suffix == ".jsonl"
        and path.name.startswith("agent-")
        and path.parent.name == "subagents"
    )


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


def scan_subagents(project_id: str, session_id: str) -> dict[str, Path]:
    """
    Scan a session's subagents folder and return subagent files.

    Returns a dict mapping agent_id (e.g., "a6c7d21") to Path.
    """
    subagents_dir = get_projects_dir() / project_id / session_id / "subagents"
    if not subagents_dir.exists():
        return {}
    return {
        f.stem.removeprefix("agent-"): f
        for f in subagents_dir.iterdir()
        if f.is_file() and is_subagent_file(f)
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


def _sync_session_items(session: Session, file_path: Path) -> list[int]:
    """
    Read new lines from a JSONL file and insert them as raw SessionItem rows.

    No JSON parsing is done. All metadata computation is deferred to the
    background compute task (compute_session_metadata).

    Used by sync_project/sync_all for the initial sync where speed matters
    and metadata will be computed in background anyway.

    Args:
        session: The session (must already be saved to the database)
        file_path: Path to the JSONL file

    Returns:
        List of line_nums of new items added
    """
    if not file_path.exists():
        return []

    stat = file_path.stat()
    file_mtime = stat.st_mtime

    # If mtime hasn't changed, nothing to do
    if session.mtime == file_mtime:
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        # Seek to last known position
        f.seek(session.last_offset)

        # Read remaining content
        new_content = f.read()
        if not new_content:
            # Update mtime even if no new content (file may have been touched)
            session.mtime = file_mtime
            session.save(update_fields=["mtime"])
            return []

        # Split into lines (filter out empty lines)
        lines = [line for line in new_content.split("\n") if line.strip()]

        if lines:
            # Create SessionItem objects for bulk insert (raw content only)
            current_line_num = session.last_line
            items_to_create: list[SessionItem] = []

            for line in lines:
                line = line.strip()
                if not line:
                    line = "{}"
                current_line_num += 1
                items_to_create.append(SessionItem(
                    session=session,
                    line_num=current_line_num,
                    content=line,
                ))

            # Bulk create all items
            SessionItem.objects.bulk_create(items_to_create, ignore_conflicts=True)

            # Update session tracking fields
            session.last_line = current_line_num

        # Update offset to end of file
        session.last_offset = f.tell()
        session.mtime = file_mtime
        session.save(update_fields=["last_offset", "last_line", "mtime"])

    return [item.line_num for item in items_to_create] if lines else []


def _sync_session_subagents(
    project: Project,
    session: Session,
    stats: dict[str, int],
) -> None:
    """
    Synchronize subagents for a given session.

    Scans the session's subagents folder and syncs each subagent file.
    Updates stats in place.
    """
    subagent_files = scan_subagents(project.id, session.id)
    if not subagent_files:
        return

    # Get existing subagents from database for this session
    db_subagents = {
        s.agent_id: s
        for s in Session.objects.filter(
            project=project,
            type=SessionType.SUBAGENT,
            parent_session=session,
        )
    }

    for agent_id, file_path in subagent_files.items():
        if agent_id in db_subagents:
            # Subagent exists in DB, sync items (raw insert only)
            subagent = db_subagents[agent_id]
            new_line_nums = _sync_session_items(subagent, file_path)
            stats["items_added"] += len(new_line_nums)

            # If new items were added, reset compute_version to trigger background recompute
            if new_line_nums and subagent.compute_version is not None:
                subagent.compute_version = None
                subagent.save(update_fields=["compute_version"])
        else:
            # New subagent - check if file has content
            if not check_file_has_content(file_path):
                continue

            # Create subagent entry (use agent_id as the primary key)
            subagent = Session(
                id=agent_id,
                project=project,
                type=SessionType.SUBAGENT,
                parent_session=session,
                agent_id=agent_id,
            )
            subagent.save()
            stats["sessions_created"] += 1

            # Sync items (raw insert only, compute_version is already NULL for new sessions)
            new_line_nums = _sync_session_items(subagent, file_path)
            stats["items_added"] += len(new_line_nums)

    # Mark stale subagents (exist in DB but not on disk)
    disk_agent_ids = set(subagent_files.keys())
    for agent_id, subagent in db_subagents.items():
        if agent_id not in disk_agent_ids and not subagent.stale:
            subagent.stale = True
            subagent.save(update_fields=["stale"])
            stats["sessions_stale"] += 1


def sync_project(
    project_id: str,
    on_session_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, int]:
    """
    Synchronize a single project, its sessions, and their subagents.

    Args:
        project_id: The project folder name.
        on_session_progress: Optional callback called after each session sync
            with (session_id, current_index, total_sessions).

    Returns a dict with sync statistics:
        - sessions_created: number of new sessions (including subagents)
        - sessions_stale: number of sessions marked as stale (including subagents)
        - items_added: total number of new session items (including subagent items)
    """
    project_start = time.monotonic()

    stats = {
        "sessions_created": 0,
        "sessions_stale": 0,
        "items_added": 0,
    }

    # Get or create project
    project, _ = Project.objects.get_or_create(id=project_id)

    # Scan session files on disk
    session_files = scan_sessions(project_id)
    disk_session_ids = set(session_files.keys())

    # Get existing sessions from database (only main sessions, not subagents)
    db_sessions = {
        s.id: s
        for s in Session.objects.filter(project=project, type=SessionType.SESSION)
    }
    db_session_ids = set(db_sessions.keys())

    # Process sessions that exist on disk
    sessions_to_sync = list(disk_session_ids)
    total_sessions = len(sessions_to_sync)
    max_mtime = 0.0

    logger.info(f"  Syncing project {project_id} ({total_sessions} sessions)")

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
            session = Session(id=session_id, project=project, type=SessionType.SESSION)
            session.save()
            stats["sessions_created"] += 1

            # Sync items (raw insert only, compute_version is already NULL for new sessions)
            new_line_nums = _sync_session_items(session, file_path)

            stats["items_added"] += len(new_line_nums)

            # Track max mtime for project
            if session.mtime > max_mtime:
                max_mtime = session.mtime

            # Sync subagents for this session
            _sync_session_subagents(project, session, stats)

            if on_session_progress:
                on_session_progress(session_id, idx, total_sessions)
            continue

        # Session exists in DB, sync items (raw insert only)
        new_line_nums = _sync_session_items(session, file_path)
        stats["items_added"] += len(new_line_nums)

        # If new items were added, reset compute_version to trigger background recompute
        if new_line_nums and session.compute_version is not None:
            session.compute_version = None
            session.save(update_fields=["compute_version"])

        if session.last_line > 0:
            if session.mtime > max_mtime:
                max_mtime = session.mtime

        # Sync subagents for this session
        _sync_session_subagents(project, session, stats)

        if on_session_progress:
            on_session_progress(session_id, idx, total_sessions)

    # Mark stale sessions (exist in DB but not on disk)
    stale_session_ids = db_session_ids - disk_session_ids
    if stale_session_ids:
        Session.objects.filter(id__in=stale_session_ids, stale=False).update(
            stale=True
        )
        stats["sessions_stale"] += len(stale_session_ids)

    # Update project metadata (count sessions with created_at, including stale ones)
    project.sessions_count = Session.objects.filter(
        project=project,
        type=SessionType.SESSION,
        created_at__isnull=False,
    ).count()
    project.mtime = max_mtime
    if project.stale:
        project.stale = False
    project.save(update_fields=["sessions_count", "mtime", "stale"])

    # Update project total_cost
    update_project_total_cost(project.id)

    elapsed = time.monotonic() - project_start
    logger.info(
        f"  ✓ Project {project_id} done in {elapsed:.1f}s — "
        f"{stats['items_added']} items, {stats['sessions_created']} new sessions"
    )

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
    sync_start = time.monotonic()

    stats = {
        "projects_created": 0,
        "projects_stale": 0,
        "sessions_created": 0,
        "sessions_stale": 0,
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

    # Mark stale projects (exist in DB but not on disk)
    stale_project_ids = db_project_ids - disk_project_ids
    if stale_project_ids:
        Project.objects.filter(id__in=stale_project_ids, stale=False).update(
            stale=True
        )
        stats["projects_stale"] += len(stale_project_ids)

    # Sync each project on disk
    projects_to_sync = sorted(disk_project_ids)
    total_projects = len(projects_to_sync)

    logger.info(f"Sync started — {total_projects} projects found")

    for idx, project_id in enumerate(projects_to_sync, start=1):
        if on_project_start:
            on_project_start(project_id, idx, total_projects)

        project_stats = sync_project(project_id, on_session_progress=on_session_progress)

        # Aggregate stats
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["sessions_stale"] += project_stats["sessions_stale"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    # Resolve git_root for all projects with a directory
    for project in Project.objects.filter(directory__isnull=False, stale=False):
        ensure_project_git_root(project.id, project.directory)

    elapsed = time.monotonic() - sync_start
    logger.info(
        f"✓ Sync complete in {elapsed:.1f}s — "
        f"{stats['sessions_created']} sessions created, "
        f"{stats['items_added']} items added"
    )

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
        if stats["sessions_stale"]:
            sessions_info.append(f"-{stats['sessions_stale']} stale")
        if stats["items_added"]:
            sessions_info.append(f"+{stats['items_added']} items")

        info = ", ".join(sessions_info) if sessions_info else "no changes"
        self._write(f"  [done] {project_id}: {info}")

    def on_sync_complete(self, stats: dict[str, int]) -> None:
        """Called when the entire sync is complete."""
        elapsed = time.time() - self.start_time
        self._write("")
        self._write(f"Sync complete in {self._format_time(elapsed)}")
        self._write(f"  Projects: {stats['projects_created']} created, {stats['projects_stale']} stale")
        self._write(f"  Sessions: {stats['sessions_created']} created, {stats['sessions_stale']} stale")
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
