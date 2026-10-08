"""Delayed daily cleanup of session artifacts and scratch directories.

Empty directories retain their existing 30-day cleanup policy. Scratch folders
are also removed recursively when every user is archived or hidden and has
been inactive for 30 days. Unreferenced orphan scratch folders are removed
without an age requirement. Artifacts are never removed recursively.

Shared scratch users include the folder's owner, spawn_root descendants, and
scratch_dir annotations (including paths inside a folder). Unknown timestamps
never authorize recursive removal. Active agents protect their work folders.

The first pass runs 30 minutes after startup; later passes run every 24 hours.
Filesystem work runs on a worker thread, with agent starts excluded during the
pass. Worktrees retain the existing disable flag because their roots may be
shared with the main instance.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

SESSION_DIRS_CLEANUP_INITIAL_DELAY = 30 * 60
SESSION_DIRS_CLEANUP_INTERVAL = 24 * 60 * 60
STALE_SESSION_DIR_AGE = timedelta(days=30)
_QUERY_CHUNK = 500


class SessionDirectoryState(NamedTuple):
    reference: datetime | None
    eligible: bool
    scratch_paths: tuple[Path, ...]


def _is_stale(reference: datetime | None, now: datetime) -> bool:
    return reference is not None and now - reference >= STALE_SESSION_DIR_AGE


def _dir_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _remove_if_empty(path: Path) -> bool:
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


_SESSION_FIELDS = (
    "id", "created_at", "last_started_at", "last_updated_at", "last_stopped_at",
    "archived", "hidden", "spawn_root_id", "annotations",
)


def _session_state(row: dict, scratch_root: Path, now: datetime) -> SessionDirectoryState:
    timestamps = [row[field] for field in _SESSION_FIELDS[1:5] if row[field] is not None]
    reference = max(timestamps) if timestamps else None
    shared = []
    if row["spawn_root_id"]:
        shared.append(scratch_root / row["spawn_root_id"])
    annotations = row["annotations"]
    annotated_path = annotations.get("scratch_dir") if isinstance(annotations, dict) else None
    if isinstance(annotated_path, str) and annotated_path and Path(annotated_path).is_absolute():
        shared.append(Path(annotated_path))
    return SessionDirectoryState(
        reference, bool(row["archived"] or row["hidden"]) and _is_stale(reference, now), tuple(shared),
    )


def _uses_folder(shared_path: Path, folder: Path) -> bool:
    # Resolve only for ownership comparison, never as a deletion target.
    # A nested annotation protects the whole containing session folder.
    try:
        for shared, candidate in (
            (Path(os.path.abspath(shared_path)), Path(os.path.abspath(folder))),
            (shared_path.resolve(), folder.resolve()),
        ):
            if shared.is_relative_to(candidate) or candidate.is_relative_to(shared):
                return True
        return False
    except (OSError, RuntimeError):
        return True  # An unreadable shared path must not authorize deletion.


def _scratch_users(folder: Path, scratch_root: Path, now: datetime) -> dict[str, SessionDirectoryState]:
    """Read current users immediately before removing a scratch folder."""
    from django.db.models import Q

    from twicc.core.models import Session

    users = {}
    rows = Session.objects.filter(
        Q(id=folder.name) | Q(spawn_root_id=folder.name) | Q(annotations__scratch_dir__isnull=False)
    ).values(*_SESSION_FIELDS)
    for row in rows.iterator(chunk_size=_QUERY_CHUNK):
        state = _session_state(row, scratch_root, now)
        if row["id"] == folder.name or any(_uses_folder(path, folder) for path in state.scratch_paths):
            users[row["id"]] = state
    return users


def _prune_stale_session_dirs() -> tuple[int, int]:
    """Return (artifact folders removed, scratch folders removed)."""
    from django.utils import timezone

    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import Session
    from twicc.paths import get_artifacts_dir, get_scratch_dir

    now = timezone.now()
    scratch_root = get_scratch_dir()
    roots = (("artifacts", get_artifacts_dir()), ("scratch", scratch_root))
    candidates = []
    for label, root in roots:
        try:
            with os.scandir(root) as entries:
                candidates.extend(
                    (label, entry.name, Path(entry.path))
                    for entry in entries if entry.is_dir(follow_symlinks=False)
                )
        except FileNotFoundError:
            continue

    if not candidates:
        return (0, 0)

    registry = get_agent_manager_registry()
    active_ids = {info.session_id for info in registry.get_active_agents()}
    active_paths = [Path(path) for path in registry.get_active_work_dirs()]
    states = {
        row["id"]: _session_state(row, scratch_root, now)
        for row in Session.objects.values(*_SESSION_FIELDS).iterator(chunk_size=_QUERY_CHUNK)
    }
    protected_shared = [
        path
        for session_id, state in states.items()
        if not state.eligible or session_id in active_ids
        for path in state.scratch_paths
    ]
    removed = {"artifacts": 0, "scratch": 0}
    for label, session_id, path in candidates:
        if session_id in active_ids or any(_uses_folder(active, path) for active in active_paths):
            continue
        state = states.get(session_id)
        if label == "scratch":
            if any(_uses_folder(shared, path) for shared in protected_shared):
                continue
            if state is None or state.eligible:
                # Re-read ownership and dates so unarchiving or new references
                # between the initial scan and this folder protect it.
                users = _scratch_users(path, scratch_root, now)
                if any(not user.eligible or sid in active_ids for sid, user in users.items()):
                    continue
                try:
                    # rmtree does not follow child symlinks and refuses a
                    # top-level symlink if the folder was replaced meanwhile.
                    shutil.rmtree(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    logger.warning("Session dirs cleanup: cannot remove %s", path, exc_info=True)
                    continue
                removed[label] += 1
                logger.info("Session dirs cleanup: removed scratch folder %s", path)
                continue
        reference = (state.reference if state else None) or _dir_mtime(path)
        if _is_stale(reference, now) and _remove_if_empty(path):
            removed[label] += 1

    if any(removed.values()):
        logger.info(
            "Session dirs cleanup: removed %d artifacts and %d scratch folders",
            removed["artifacts"], removed["scratch"],
        )
    return removed["artifacts"], removed["scratch"]


async def _run_cleanup_pass() -> None:
    from twicc.agent.registry import get_agent_manager_registry

    # Starts/resumes hold a provider manager lock while granting work dirs.
    # Holding all manager locks makes the runtime snapshot stable and prevents
    # an orphan-folder deletion racing a new session before its first DB row.
    async with get_agent_manager_registry().work_dirs_cleanup_guard():
        task = asyncio.create_task(asyncio.to_thread(_prune_stale_session_dirs))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop its worker. Keep the locks
            # until deletion finishes, including during server shutdown.
            await task
            raise


async def start_session_dirs_cleanup_task(stop_event: asyncio.Event) -> None:
    from django.conf import settings

    if not settings.SESSION_DIRS_CLEANUP_ENABLED:
        logger.info("Session dirs cleanup disabled (TWICC_NO_SESSION_DIRS_CLEANUP is set)")
        return

    logger.info("Session dirs cleanup task started")
    delay = SESSION_DIRS_CLEANUP_INITIAL_DELAY
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass
            else:
                break
            try:
                await _run_cleanup_pass()
            except Exception:
                logger.exception("Session dirs cleanup cycle failed")
            delay = SESSION_DIRS_CLEANUP_INTERVAL
    finally:
        logger.info("Session dirs cleanup task stopped")
