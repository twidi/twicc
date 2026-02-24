"""
Synchronization logic for JSONL files from Claude projects.

Scans CLAUDE_PROJECTS_DIR for projects and sessions, synchronizes them
with the database, and reads new lines from modified JSONL files.
"""

from __future__ import annotations

import logging

import orjson
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from twicc.compute import (
    cache_agent_prompt,
    create_agent_link_from_tool_result,
    create_agent_link_from_tool_use,
    create_agent_link_from_subagent,
    create_tool_result_link_live,
    compute_item_cost_and_usage,
    compute_item_metadata,
    compute_item_metadata_live,
    ensure_project_directory,
    ensure_project_git_root,
    get_project_git_root,
    extract_item_timestamp,
    extract_text_from_content,
    extract_title_from_user_message,
    get_cached_agent_prompt,
    get_message_content,
    is_agent_link_done,
    is_tool_result_item,
    update_project_total_cost,
)
from twicc.core.enums import ItemDisplayLevel, ItemKind
from twicc.core.models import Project, Session, SessionItem, SessionType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


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


def _sync_session_items_raw(session: Session, file_path: Path) -> list[int]:
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


def sync_session_items(session: Session, file_path: Path) -> tuple[list[int], list[int]]:
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
            items_to_create: list[tuple[SessionItem, dict]] = []
            current_line_num = session.last_line

            # Track title updates (session_id -> title)
            session_title_updates: dict[str, str] = {}
            # Track if we've already set initial title for this session (from first user message ever)
            initial_title_needs_set = session.title is None

            # Track first timestamp in this batch (for session.created_at)
            first_timestamp: datetime | None = None

            # Track last seen values for runtime environment fields
            first_cwd: str | None = None  # First cwd in this batch
            last_cwd: str | None = None
            last_cwd_git_branch: str | None = None
            last_model: str | None = None

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
                # Pre-compute display_level (no group info yet)
                try:
                    parsed = orjson.loads(line)
                except orjson.JSONDecodeError:
                    parsed = {}
                metadata = compute_item_metadata(parsed)
                item.display_level = metadata['display_level']
                item.kind = metadata['kind']

                # Extract timestamp
                item.timestamp = extract_item_timestamp(parsed)
                if first_timestamp is None and item.timestamp is not None:
                    first_timestamp = item.timestamp

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
                            if create_agent_link_from_subagent(
                                parent_session_id=session.parent_session_id,
                                agent_id=agent_id,
                                agent_prompt=prompt,
                            ):

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

            # Track line_nums of new items
            new_line_nums = {item.line_num for item in items_only}

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
                    item_modified_lines = compute_item_metadata_live(session.id, item, item.content)
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
                    create_tool_result_link_live(session.id, item, parsed)
                    # Also check for agent links (Task tool_result with agentId)
                    create_agent_link_from_tool_result(session.id, item, parsed)

                # For parent sessions: check if this assistant message contains Task tool_use(s)
                # and try to link them to existing subagents (handles the race condition where
                # the subagent was synced before this Task tool_use existed).
                # Note: Task tool_uses are often in CONTENT_ITEMS lines (streaming splits
                # the text and tool_use into separate lines, and tool_use-only lines have
                # no visible content so they're classified as CONTENT_ITEMS, not ASSISTANT_MESSAGE).
                if session.type == SessionType.SESSION and item.kind in (ItemKind.ASSISTANT_MESSAGE, ItemKind.CONTENT_ITEMS):
                    create_agent_link_from_tool_use(session.id, item, parsed)

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
            is_new_session = session.created_at is None and first_timestamp is not None
            if is_new_session:
                session.created_at = first_timestamp

            # Recalculate activity counters for affected days (only items that contribute)
            affected_days = {
                item.timestamp.date()
                for item, _ in items_to_create
                if item.timestamp and (item.kind == ItemKind.USER_MESSAGE or item.cost)
            }
            if is_new_session and session.type == SessionType.SESSION and first_timestamp:
                affected_days.add(first_timestamp.date())

        # Update offset to end of file
        session.last_offset = f.tell()
        session.mtime = file_mtime
        session.save(update_fields=["last_offset", "last_line", "mtime", "user_message_count", "context_usage", "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch", "model", "created_at"])

        # Recalculate activities after session.save (needs created_at in DB for session_count)
        if lines:
            from twicc.core.models import PeriodicActivity
            PeriodicActivity.recalculate_for_days(session.project_id, affected_days)

        # If this is a subagent, propagate cost to parent session
        if session.type == SessionType.SUBAGENT and session.parent_session_id:
            _update_parent_session_costs(session.parent_session_id)

    # Exclude new items from modified_line_nums
    return sorted(new_line_nums), sorted(modified_line_nums - new_line_nums)


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
            new_line_nums = _sync_session_items_raw(subagent, file_path)
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
            new_line_nums = _sync_session_items_raw(subagent, file_path)
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
            new_line_nums = _sync_session_items_raw(session, file_path)

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
        new_line_nums = _sync_session_items_raw(session, file_path)
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
