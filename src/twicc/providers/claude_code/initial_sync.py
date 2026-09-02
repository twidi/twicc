"""
Synchronization logic for JSONL files from Claude Code projects.

Scans the Claude Code projects dir (``<claude home>/projects/``, resolved by
``twicc.provider_homes.claude_projects_dir()``) for projects and sessions and
pushes initial-sync payloads onto the DB writer queue (which performs
every DB write inside a single serialised coroutine). Producers in this
module are strictly read-only on the DB side: they parse JSONL files, run
their existence/diff checks via ``Session.objects.filter`` reads, and emit
``CreateSessionPayload`` / ``UpdateSessionPayload`` / ``MarkSessionsStalePayload``
/ ``UpdateProjectMetadataPayload`` instances for the DB writer to apply.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType, Workflow
from twicc.providers.db_writer import (
    CreateSessionPayload,
    MarkSessionsStalePayload,
    ResolveProjectGitRootsPayload,
    UpdateProjectMetadataPayload,
    UpdateSessionPayload,
    UpsertWorkflowPayload,
)
from twicc.provider_homes import claude_projects_dir
from twicc.sync_helpers import BackpressureSyncQueue, check_file_has_content, read_session_items_from_file

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid session file (*.jsonl but not agent-*.jsonl)."""
    return path.suffix == ".jsonl" and not path.name.startswith("agent-")


# Real subagent files are named agent-a<hex>.jsonl (e.g. agent-a6c7d21.jsonl).
# The subagents/ directory also contains sidechain files like agent-acompact-<hex>.jsonl
# (context compaction) and agent-aprompt_suggestion-<hex>.jsonl (title suggestions)
# which are not real subagents and should be ignored.
_REAL_SUBAGENT_RE = re.compile(r"^agent-a[0-9a-f]+\.jsonl$")


def is_subagent_file(path: Path) -> bool:
    """Check if a path is a valid subagent file in the correct location.

    Valid subagent files:
    - Must be in a 'subagents' directory
    - Must match agent-a<hex>.jsonl (excludes sidechain files like acompact, aprompt_suggestion)
    """
    return (
        path.parent.name == "subagents"
        and _REAL_SUBAGENT_RE.match(path.name) is not None
    )


def scan_projects() -> set[str]:
    """Scan the projects directory and return the set of project folder names."""
    projects_dir = claude_projects_dir()
    if not projects_dir.exists():
        return set()
    return {d.name for d in projects_dir.iterdir() if d.is_dir()}


def scan_sessions(project_id: str) -> dict[str, Path]:
    """
    Scan a project folder and return session files.

    Returns a dict mapping session id (filename without extension) to Path.
    """
    project_dir = claude_projects_dir() / project_id
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

    Returns a dict mapping the subagent's ``Session.id`` to its Path:
    - normal subagents → the bare agent_id (e.g. ``"a6c7d21"``);
    - workflow subagents (one level deeper, under
      ``subagents/workflows/<run_id>/``) → the composite ``"<run_id>:<agent_id>"``.
    Keying by the eventual ``Session.id`` lets the caller diff against the DB
    rows uniformly, whatever their kind.
    """
    subagents_dir = claude_projects_dir() / project_id / session_id / "subagents"
    if not subagents_dir.exists():
        return {}
    result = {
        f.stem.removeprefix("agent-"): f
        for f in subagents_dir.iterdir()
        if f.is_file() and is_subagent_file(f)
    }
    # Workflow subagents: subagents/workflows/<run_id>/agent-a<hex>.jsonl.
    # is_subagent_file() (parent.name == "subagents") excludes them above, so
    # there is no double counting.
    workflows_dir = subagents_dir / "workflows"
    if workflows_dir.is_dir():
        for run_dir in workflows_dir.iterdir():
            if not run_dir.is_dir():
                continue
            for f in run_dir.iterdir():
                if f.is_file() and _REAL_SUBAGENT_RE.match(f.name) is not None:
                    agent_id = f.stem.removeprefix("agent-")
                    result[f"{run_dir.name}:{agent_id}"] = f
    return result


def scan_workflow_files(project_id: str, session_id: str) -> dict[str, Path]:
    """Scan a session's ``workflows/`` folder and return run files.

    Returns a dict mapping ``run_id`` (the filename stem, e.g.
    ``"wf_cd590ff1-f54"``) to the ``wf_*.json`` Path. The ``workflows/scripts/``
    subdirectory is skipped (only files at the root, ``is_file()``).
    """
    workflows_dir = claude_projects_dir() / project_id / session_id / "workflows"
    if not workflows_dir.is_dir():
        return {}
    return {
        f.stem: f
        for f in workflows_dir.iterdir()
        if f.is_file() and f.name.startswith("wf_") and f.suffix == ".json"
    }


def _sync_session_subagents(
    project: Project,
    session: Session,
    stats: dict[str, int],
    sync_queue: BackpressureSyncQueue,
) -> None:
    """Push initial-sync payloads for a session's subagents.

    Producer-side: reads filesystem + DB but performs no writes. Every write
    is delegated to the DB writer via :class:`CreateSessionPayload`,
    :class:`UpdateSessionPayload`, and :class:`MarkSessionsStalePayload`.
    """
    subagent_files = scan_subagents(project.id, session.id)
    if not subagent_files:
        return

    db_subagents = {
        s.id: s
        for s in Session.objects.filter(
            project=project,
            type=SessionType.SUBAGENT,
            parent_session=session,
        )
    }

    for agent_id, file_path in subagent_files.items():
        if agent_id in db_subagents:
            # Subagent already in DB — push an update payload if items changed.
            subagent = db_subagents[agent_id]
            to_insert = read_session_items_from_file(subagent, file_path)
            if to_insert is None:
                continue
            stats["items_added"] += to_insert.actually_new_count
            sync_queue.put(UpdateSessionPayload(
                provider=Provider.CLAUDE_CODE,
                session=subagent,
                items=to_insert.items,
                last_offset=to_insert.last_offset,
                last_line=to_insert.last_line,
                mtime=to_insert.mtime,
                reset_compute_version=(
                    to_insert.actually_new_count > 0
                    and subagent.compute_version is not None
                ),
                # Subagent stale is deliberately left set: the stale-clear fix
                # targets top-level sessions only. read_session_items_from_file
                # may now return an empty result for a stale subagent whose
                # file is back unchanged; with clear_stale=False that payload
                # is a harmless DB writer-side no-op.
                clear_stale=False,
            ))
        else:
            # New subagent.
            if not check_file_has_content(file_path):
                continue

            subagent = Session(
                id=agent_id,
                project_id=project.id,  # FK by id — DB writer ensures project exists first
                provider=Provider.CLAUDE_CODE,
                file_path=str(file_path.relative_to(claude_projects_dir())),
                type=SessionType.SUBAGENT,
                parent_session_id=session.id,
            )
            to_insert = read_session_items_from_file(subagent, file_path)
            if to_insert is None:
                # File vanished between scan and read.
                continue
            stats["items_added"] += to_insert.actually_new_count
            stats["sessions_created"] += 1
            sync_queue.put(CreateSessionPayload(
                provider=Provider.CLAUDE_CODE,
                project_id=project.id,
                # Subagents never carry a new directory; the parent session's
                # CreateSessionPayload already handled project creation when
                # needed.
                new_project_directory=None,
                new_project_stale=False,
                session=subagent,
                items=to_insert.items,
                last_offset=to_insert.last_offset,
                last_line=to_insert.last_line,
                mtime=to_insert.mtime,
            ))

    # Mark stale subagents (exist in DB but not on disk).
    disk_agent_ids = set(subagent_files.keys())
    stale_subagent_ids = [
        s.id for agent_id, s in db_subagents.items()
        if agent_id not in disk_agent_ids and not s.stale
    ]
    if stale_subagent_ids:
        sync_queue.put(MarkSessionsStalePayload(
            provider=Provider.CLAUDE_CODE,
            session_ids=stale_subagent_ids,
        ))
        stats["sessions_stale"] += len(stale_subagent_ids)


def _sync_session_workflows(
    session: Session,
    sync_queue: BackpressureSyncQueue,
) -> None:
    """Push ``UpsertWorkflowPayload``s for a session's not-yet-persisted runs.

    Backfill for runs that finished before this sync: the live watcher mirrors
    every ``wf_*.json`` write, but a file not rewritten after boot would never
    reach the DB otherwise. Read-only on the write side. Only runs whose
    ``run_id`` is not already stored are read + emitted, so large completed
    envelopes are not re-read on every sync (the live watcher keeps a stored
    run fresh while it is still being written).
    """
    wf_files = scan_workflow_files(session.project_id, session.id)
    if not wf_files:
        return
    existing_run_ids = set(
        Workflow.objects.filter(session_id=session.id).values_list("run_id", flat=True)
    )
    for run_id, file_path in wf_files.items():
        if run_id in existing_run_ids:
            continue
        try:
            raw = file_path.read_text(encoding="utf-8")
            orjson.loads(raw)  # validate — skip a partial write caught mid-flush
        except (OSError, orjson.JSONDecodeError):
            continue
        sync_queue.put(UpsertWorkflowPayload(
            provider=Provider.CLAUDE_CODE,
            session_id=session.id,
            run_id=run_id,
            raw_json=raw,
        ))


def sync_project(
    project_id: str,
    sync_queue: BackpressureSyncQueue,
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize a single project by pushing payloads to the DB writer.

    Args:
        project_id: The project folder name.
        sync_queue: The queue connected to the DB writer for this provider.
        on_session_progress: Optional callback called after each session sync
            with (session_id, current_index, total_sessions).
        stop_event: Optional threading event; when set, sync stops early.

    Returns a dict with sync statistics:
        - sessions_created: number of new sessions (including subagents)
        - sessions_stale: number of sessions marked as stale (including subagents)
        - items_added: total number of new session items (including subagent items)
        - project_created (optional): 1 if the project row will be created by the DB writer
    """
    project_start = time.monotonic()

    stats = {
        "sessions_created": 0,
        "sessions_stale": 0,
        "items_added": 0,
    }

    # Try to get existing project (read-only).
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        project = None

    session_files = scan_sessions(project_id)
    disk_session_ids = set(session_files.keys())

    if project is not None:
        db_sessions = {
            s.id: s
            for s in Session.objects.filter(
                project=project,
                type=SessionType.SESSION,
                provider=Provider.CLAUDE_CODE,
            )
        }
    else:
        db_sessions = {}
    db_session_ids = set(db_sessions.keys())

    sessions_to_sync = list(disk_session_ids)
    total_sessions = len(sessions_to_sync)
    project_will_be_created = False

    logger.info(f"  Syncing project {project_id} ({total_sessions} sessions)")

    for idx, session_id in enumerate(sessions_to_sync, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        file_path = session_files[session_id]

        if session_id in db_session_ids:
            # Existing session — push an update payload if items changed.
            session = db_sessions[session_id]
            to_insert = read_session_items_from_file(session, file_path)

            if to_insert is not None:
                stats["items_added"] += to_insert.actually_new_count

                sync_queue.put(UpdateSessionPayload(
                    provider=Provider.CLAUDE_CODE,
                    session=session,
                    items=to_insert.items,
                    last_offset=to_insert.last_offset,
                    last_line=to_insert.last_line,
                    mtime=to_insert.mtime,
                    reset_compute_version=(
                        to_insert.actually_new_count > 0
                        and session.compute_version is not None
                    ),
                    clear_stale=session.stale,
                ))

            _sync_session_subagents(project, session, stats, sync_queue)
            _sync_session_workflows(session, sync_queue)

            if on_session_progress:
                on_session_progress(session_id, idx, total_sessions)
            continue

        # New session.
        if not check_file_has_content(file_path):
            # Empty file (0 lines) — skip creating session.
            if on_session_progress:
                on_session_progress(session_id, idx, total_sessions)
            continue

        # File has content. Build the unsaved Session row (the DB writer will
        # ensure the project exists and then ``session.save()`` it). Claude
        # Code stores cwd inside the JSONL body, so initial sync cannot
        # provide a directory here; it gets filled in later by background
        # compute, which also re-runs workspace auto-add at that point.
        session = Session(
            id=session_id,
            project_id=project_id,
            provider=Provider.CLAUDE_CODE,
            file_path=str(file_path.relative_to(claude_projects_dir())),
            type=SessionType.SESSION,
        )
        to_insert = read_session_items_from_file(session, file_path)
        if to_insert is None:
            # File vanished between scan and read.
            if on_session_progress:
                on_session_progress(session_id, idx, total_sessions)
            continue

        stats["items_added"] += to_insert.actually_new_count
        stats["sessions_created"] += 1

        if project is None and not project_will_be_created:
            project_will_be_created = True
            stats["project_created"] = 1

        sync_queue.put(CreateSessionPayload(
            provider=Provider.CLAUDE_CODE,
            project_id=project_id,
            new_project_directory=None,
            new_project_stale=False,
            session=session,
            items=to_insert.items,
            last_offset=to_insert.last_offset,
            last_line=to_insert.last_line,
            mtime=to_insert.mtime,
        ))

        # Subagent sync also needs a project reference for its filter on
        # ``parent project``. If the project row is being created on the fly
        # by the above payload, use a transient Project instance carrying
        # just the id — ``_sync_session_subagents`` only reads ``project.id``,
        # never anything else from the object.
        _sync_session_subagents(
            project if project is not None else Project(id=project_id),
            session, stats, sync_queue,
        )
        _sync_session_workflows(session, sync_queue)

        if on_session_progress:
            on_session_progress(session_id, idx, total_sessions)

    # Skip end-of-project metadata if no session was ever created/found.
    if project is None and not project_will_be_created:
        elapsed = time.monotonic() - project_start
        logger.info(
            f"  ⊘ Project {project_id} skipped in {elapsed:.1f}s — no sessions with content"
        )
        return stats

    # Mark stale sessions (DB but not on disk).
    stale_session_ids = list(db_session_ids - disk_session_ids)
    if stale_session_ids:
        sync_queue.put(MarkSessionsStalePayload(
            provider=Provider.CLAUDE_CODE,
            session_ids=stale_session_ids,
        ))
        stats["sessions_stale"] += len(stale_session_ids)

    # Decide new_stale:
    # - existing project with directory → recompute from os.path.isdir
    # - existing project without directory but stale=True → clear stale (legacy)
    # - newly created project → leave alone (DB writer's default stale=False)
    new_stale: bool | None
    if project is not None and project.directory is not None:
        new_stale = not os.path.isdir(project.directory)
    elif project is not None and project.stale:
        new_stale = False
    else:
        new_stale = None

    sync_queue.put(UpdateProjectMetadataPayload(
        provider=Provider.CLAUDE_CODE,
        project_id=project_id,
        recalc_sessions_count=True,
        recalc_mtime=True,
        new_stale=new_stale,
        recalc_total_cost=True,
        # git_root is resolved at the end of ``sync_all`` once every project
        # has settled (the DB writer drains all per-project payloads first).
        resolve_git_root=False,
        git_root_directory=None,
    ))

    elapsed = time.monotonic() - project_start
    logger.info(
        f"  ✓ Project {project_id} done in {elapsed:.1f}s — "
        f"{stats['items_added']} items, {stats['sessions_created']} new sessions"
    )

    return stats


def sync_all(
    sync_queue: queue.Queue,
    on_project_start: Callable[[str, int, int], None] | None = None,
    on_project_done: Callable[[str, dict[str, int]], None] | None = None,
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize all projects from the Claude Code projects dir (``claude_projects_dir()``).

    Pushes payloads onto ``sync_queue`` (the DB writer drains them).
    Producer-side reads are safe under WAL even while the DB writer is
    writing earlier payloads.

    Args:
        sync_queue: The queue connected to the DB writer for this provider.
        on_project_start: Callback called before syncing a project
            with (project_id, current_index, total_projects).
        on_project_done: Callback called after syncing a project
            with (project_id, project_stats).
        on_session_progress: Callback passed to sync_project.
        stop_event: Optional threading event; when set, sync stops early.

    Returns aggregate statistics.
    """
    sync_start = time.monotonic()

    # Throttle the producer to the DB writer's write rate: a full bounded
    # queue blocks .put() instead of letting the backlog grow unbounded.
    sync_queue = BackpressureSyncQueue(sync_queue, stop_event)

    stats = {
        "projects_created": 0,
        "sessions_created": 0,
        "sessions_stale": 0,
        "items_added": 0,
    }

    disk_project_ids = scan_projects()

    # Note: ``project.stale`` is NOT recomputed here. It only tracks whether a
    # project's working directory exists on disk — nothing provider-specific —
    # so the sweep lives in the boot sequence
    # (``twicc.projects.refresh_all_project_directory_states``, called before
    # any orchestrator starts). Doing it here left a Codex-only TwiCC with a
    # flag that never refreshed.

    # Note: projects are NOT created eagerly here. They are created lazily
    # by ``sync_project`` only when they contain at least one session with
    # content. This avoids polluting the project list with empty project
    # folders (e.g. folders left behind after Claude sublimates old sessions).

    projects_to_sync = sorted(disk_project_ids)
    total_projects = len(projects_to_sync)

    logger.info(f"Sync started — {total_projects} projects found")

    for idx, project_id in enumerate(projects_to_sync, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info("Sync interrupted (after %d/%d projects)", idx - 1, total_projects)
            break

        if on_project_start:
            on_project_start(project_id, idx, total_projects)

        project_stats = sync_project(
            project_id,
            sync_queue,
            on_session_progress=on_session_progress,
            stop_event=stop_event,
        )

        stats["projects_created"] += project_stats.get("project_created", 0)
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["sessions_stale"] += project_stats["sessions_stale"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    interrupted = stop_event is not None and stop_event.is_set()

    # Resolve git_root for every project with a directory. Enqueued as a
    # single marker, not a producer-side query + one payload per project:
    # the DB writer runs the Project query when it drains the marker, after
    # every prior FIFO project payload of this run has committed — so a
    # project being un-staled earlier in the same sync is not wrongly
    # skipped by a stale=False filter evaluated against a pre-commit view.
    if not interrupted:
        sync_queue.put(ResolveProjectGitRootsPayload(provider=Provider.CLAUDE_CODE))

    elapsed = time.monotonic() - sync_start
    if interrupted:
        logger.info(
            f"⚠ Sync interrupted after {elapsed:.1f}s — "
            f"{stats['sessions_created']} sessions created, "
            f"{stats['items_added']} items added"
        )
    else:
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
        self._write(f"  Projects: {stats['projects_created']} created")
        self._write(f"  Sessions: {stats['sessions_created']} created, {stats['sessions_stale']} stale")
        self._write(f"  Items: {stats['items_added']} added")


def sync_all_with_progress(
    sync_queue: queue.Queue,
    stream=None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize all projects with console progress display.

    This is the main entry point for interactive sync with visual feedback.
    """
    display = ProgressDisplay(stream)

    stats = sync_all(
        sync_queue,
        on_project_start=display.on_project_start,
        on_project_done=display.on_project_done,
        on_session_progress=display.on_session_progress,
        stop_event=stop_event,
    )

    display.on_sync_complete(stats)
    return stats
