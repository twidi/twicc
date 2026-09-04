"""
Synchronization logic for JSONL files from Codex sessions.

Walks the Codex sessions dir (``twicc.provider_homes.codex_sessions_dir()``,
``<codex home>/sessions/YYYY/MM/DD/``),
groups files by project (resolved from the first JSONL line's
``payload.cwd``), and pushes initial-sync payloads onto the DB writer's
thread queue. Producer-side reads only — every DB write is delegated to
the DB writer via ``CreateSessionPayload`` / ``UpdateSessionPayload`` /
``DeleteSessionsPayload`` / ``MarkSessionsStalePayload`` /
``UpdateProjectMetadataPayload``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import orjson

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.paths import path_to_project_id
from twicc.projects import compute_project_stale
from twicc.provider_homes import codex_sessions_dir
from twicc.providers.db_writer import (
    CreateSessionPayload,
    DeleteSessionsPayload,
    MarkSessionsStalePayload,
    UpdateProjectMetadataPayload,
    UpdateSessionPayload,
)
from twicc.sync_helpers import BackpressureSyncQueue, check_file_has_content, read_session_items_from_file
from .rollout_migration import HistoryMode, history_mode_from_record

logger = logging.getLogger(__name__)

_AUTO_REVIEW_MODEL = "codex-auto-review"
_GUARDIAN_SUBAGENT_KIND = "guardian"

if TYPE_CHECKING:
    from collections.abc import Callable


def is_session_file(path: Path) -> bool:
    """Check if a path is a valid Codex session file (rollout-*.jsonl)."""
    return path.suffix == ".jsonl" and path.name.startswith("rollout-")


class SessionMeta(NamedTuple):
    """First-line metadata extracted from a Codex JSONL file.

    ``parent_session_id`` is set when the file is a subagent rollout
    (Codex marks the spawn through ``payload.source.subagent.thread_spawn``);
    ``None`` for top-level sessions. ``ignored`` identifies provider-internal
    rollouts that must never become TwiCC sessions.
    """
    session_id: str
    cwd: str
    parent_session_id: str | None = None
    ignored: bool = False
    history_mode: HistoryMode = HistoryMode.LEGACY


def extract_session_meta(file_path: Path) -> SessionMeta | None:
    """
    Read the first line of a Codex JSONL file and pull session_id + cwd
    (and a parent session id when the file represents a subagent rollout).

    Codex writes a ``session_meta`` event as the first line of every
    rollout file, with the session UUID at ``payload.id`` and the working
    directory at ``payload.cwd``. The session UUID is the canonical
    session id (the filename also contains it but we trust the payload).

    For user-visible subagent rollouts the ``payload.source`` field carries
    ``{"subagent": {"thread_spawn": {"parent_thread_id": "...", ...}}}``
    — we surface ``parent_thread_id`` so :func:`_sync_subagents` can wire
    the subagent to its parent ``Session`` row. Codex supports nested
    subagents (a subagent can itself spawn subagents) but
    ``parent_thread_id`` always points to the *direct* parent, so a
    single field is enough to reconstruct the chain. Auto-review creates
    internal reviewer rollouts marked as
    ``{"subagent": {"other": "guardian"}}``; these are returned with
    ``ignored=True`` so every ingestion path can discard them before creating
    a TwiCC session.

    Returns ``None`` if the file is empty, unreadable, or missing either
    ``id`` or ``cwd`` — such files are skipped by the sync.
    """
    if not file_path.exists():
        return None

    try:
        with open(file_path, "rb") as f:
            first_line = f.readline()
    except OSError:
        return None

    if not first_line.strip():
        return None

    try:
        parsed = orjson.loads(first_line)
    except orjson.JSONDecodeError:
        return None

    payload = parsed.get("payload") if isinstance(parsed, dict) else None
    if not isinstance(payload, dict):
        return None

    session_id = payload.get("id")
    cwd = payload.get("cwd")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(cwd, str) or not cwd:
        return None

    parent_session_id: str | None = None
    ignored = False
    source = payload.get("source")
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            ignored = subagent.get("other") == _GUARDIAN_SUBAGENT_KIND
            thread_spawn = subagent.get("thread_spawn")
            if isinstance(thread_spawn, dict):
                candidate = thread_spawn.get("parent_thread_id")
                if isinstance(candidate, str) and candidate:
                    parent_session_id = candidate

    return SessionMeta(
        session_id=session_id,
        cwd=cwd,
        parent_session_id=parent_session_id,
        ignored=ignored,
        history_mode=history_mode_from_record(parsed),
    )


def _is_ignored_existing_session(session: Session, file_path: Path) -> bool:
    """Confirm that a legacy DB row is an internal Guardian rollout.

    New Guardian files are rejected from their first-line metadata before a
    row exists. The model/compute prefilter limits first-line reads to known
    Auto-review rows and sessions whose metadata was never computed; the
    source marker remains the authoritative check before deletion.
    """
    if session.model != _AUTO_REVIEW_MODEL and session.compute_version is not None:
        return False
    meta = extract_session_meta(file_path)
    return meta is not None and meta.ignored


def scan_session_files() -> list[Path]:
    """
    Walk the Codex sessions dir recursively and return every Codex session file.

    Codex stores files under ``YYYY/MM/DD/rollout-*.jsonl``. Returns an
    empty list if the directory doesn't exist yet (fresh install).
    """
    sessions_dir = codex_sessions_dir()
    if not sessions_dir.exists():
        return []

    return [
        path
        for path in sessions_dir.rglob("rollout-*.jsonl")
        if path.is_file() and is_session_file(path)
    ]


class _NewEntry(NamedTuple):
    """A disk file with no matching DB row yet (first line was parsed).

    ``parent_session_id`` is non-``None`` for subagent rollouts; the
    sync resolves it to the parent ``Session`` row before creating the
    subagent (multi-pass topological order — see :func:`_sync_subagents`).
    """
    file_path: Path
    session_id: str
    cwd: str
    parent_session_id: str | None = None


class _ExistingEntry(NamedTuple):
    """A disk file already represented by a DB session row."""
    file_path: Path
    session: Session


def _build_create_payload(
    entry: _NewEntry,
    project_id: str,
    is_subagent: bool,
    stats: dict[str, int],
) -> CreateSessionPayload | None:
    """Build the CreateSessionPayload for a new entry, or None on parse failure.

    Reads the entry's JSONL file (producer-side read); when it has content,
    bumps ``stats['items_added']`` and ``stats['sessions_created']``.

    Codex stores the cwd on the first JSONL line, so every payload carries
    ``new_project_directory``: the DB writer's ``register_project`` is
    idempotent (no-op when the project already exists), and a cross-project
    subagent may be the first session the DB writer ever sees for its own
    project.
    """
    relative_path = entry.file_path.relative_to(codex_sessions_dir())
    if is_subagent:
        session = Session(
            id=entry.session_id,
            project_id=project_id,
            provider=Provider.CODEX,
            file_path=str(relative_path),
            type=SessionType.SUBAGENT,
            parent_session_id=entry.parent_session_id,
        )
    else:
        session = Session(
            id=entry.session_id,
            project_id=project_id,
            provider=Provider.CODEX,
            file_path=str(relative_path),
            type=SessionType.SESSION,
        )

    to_insert = read_session_items_from_file(session, entry.file_path)
    if to_insert is None:
        return None

    stats["items_added"] += to_insert.actually_new_count
    stats["sessions_created"] += 1

    return CreateSessionPayload(
        provider=Provider.CODEX,
        project_id=project_id,
        new_project_directory=entry.cwd,
        new_project_stale=compute_project_stale(entry.cwd),
        session=session,
        items=to_insert.items,
        last_offset=to_insert.last_offset,
        last_line=to_insert.last_line,
        mtime=to_insert.mtime,
    )


def _sync_subagents(
    subagents: list[_NewEntry],
    resolvable_parent_ids: set[str],
    sync_queue: BackpressureSyncQueue,
    stats: dict[str, int],
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Push CreateSessionPayloads for every new Codex subagent, resolved globally.

    Runs once, after every project's top-level sessions have been pushed, so
    parent resolution spans all projects: a Codex subagent can run in a
    different cwd — hence a different project — than its parent, so its
    parent ``Session`` may belong to another project. Resolving per project
    (as an earlier revision of this module did) silently dropped such a
    subagent as an orphan.

    ``resolvable_parent_ids`` is the process-wide set of session ids the
    DB writer will have created (existing DB rows plus everything pushed in
    this run). Multi-pass: each sweep pushes every subagent whose parent is
    already resolvable and records its own id, so a deeper subagent resolves
    on a later sweep; the pass count is bounded by the spawn-tree depth. A
    subagent still unresolved once a full sweep makes no progress is a
    genuine orphan (its parent is on disk nowhere) — logged and skipped; the
    watcher retries it on the next file change.
    """
    total = len(subagents)
    done = 0
    remaining = list(subagents)
    while remaining:
        progressed = False
        next_remaining: list[_NewEntry] = []
        for entry in remaining:
            if stop_event is not None and stop_event.is_set():
                logger.info(
                    "  Codex subagent sync interrupted (after %d/%d subagents)",
                    done, total,
                )
                return

            # Defensive: the first line parsed (extract_session_meta), but
            # the rest of the file might be empty after a failed write.
            if not check_file_has_content(entry.file_path):
                done += 1
                if on_session_progress:
                    on_session_progress(entry.session_id, done, total)
                continue

            if entry.parent_session_id not in resolvable_parent_ids:
                # Parent isn't resolvable yet — retry on the next sweep
                # (it may itself be a subagent resolved later in this pass).
                next_remaining.append(entry)
                continue

            payload = _build_create_payload(
                entry, path_to_project_id(entry.cwd), True, stats
            )
            if payload is None:
                done += 1
                if on_session_progress:
                    on_session_progress(entry.session_id, done, total)
                continue

            sync_queue.put(payload)
            resolvable_parent_ids.add(entry.session_id)
            done += 1
            if on_session_progress:
                on_session_progress(entry.session_id, done, total)
            progressed = True

        if not progressed:
            for entry in next_remaining:
                logger.warning(
                    "  Skipping orphan Codex subagent %s: parent %s not found "
                    "(file %s)",
                    entry.session_id, entry.parent_session_id, entry.file_path,
                )
            break
        remaining = next_remaining


def sync_project(
    project_id: str,
    new_entries: list[_NewEntry],
    existing_entries: list[_ExistingEntry],
    sync_queue: BackpressureSyncQueue,
    resolvable_parent_ids: set[str],
    subagents_out: list[_NewEntry],
    on_session_progress: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """
    Synchronize one project's top-level Codex sessions by pushing payloads
    on ``sync_queue``.

    Producer-side reads only. The DB writer applies every write
    inside ``transaction.atomic`` from a single coroutine, so two
    providers (Claude Code + Codex) and two phases (initial sync +
    compute) never race on the SQLite write lock.

    Handles this project's existing-session updates and new top-level
    sessions. New subagents are appended to ``subagents_out`` and resolved
    globally by :func:`_sync_subagents` once *every* project's top-level
    sessions have been pushed — a Codex subagent's parent may belong to
    another project. Every session id pushed here is recorded in the shared
    ``resolvable_parent_ids`` so that later subagent resolution can see it.
    """
    project_start = time.monotonic()

    stats = {
        "sessions_created": 0,
        "items_added": 0,
    }

    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        project = None

    # Split new entries by topology. Top-level sessions have no parent
    # dependency and are created here; subagents are handed to
    # _sync_subagents for one global, cross-project resolution pass.
    regular_new = [e for e in new_entries if e.parent_session_id is None]
    subagent_new = [e for e in new_entries if e.parent_session_id is not None]
    # Hand subagents off now, so they are collected even when this project
    # has no top-level session and returns early below.
    subagents_out.extend(subagent_new)

    total_sessions = len(existing_entries) + len(regular_new)
    logger.info(
        f"  Syncing project {project_id} ({total_sessions} top-level Codex "
        f"sessions, {len(subagent_new)} subagents)"
    )

    idx = 0

    # Pass 1 — existing entries (UPDATEs only).
    for entry in existing_entries:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        to_insert = read_session_items_from_file(entry.session, entry.file_path)
        if to_insert is not None:
            stats["items_added"] += to_insert.actually_new_count
            sync_queue.put(UpdateSessionPayload(
                provider=Provider.CODEX,
                session=entry.session,
                items=to_insert.items,
                last_offset=to_insert.last_offset,
                last_line=to_insert.last_line,
                mtime=to_insert.mtime,
                reset_compute_version=(
                    to_insert.actually_new_count > 0
                    and entry.session.compute_version is not None
                ),
                clear_stale=entry.session.stale,
            ))

        if on_session_progress:
            on_session_progress(entry.session.id, idx, total_sessions)

    # Pass 2 — new top-level sessions (CREATEs). Subagents are resolved
    # later, globally, by _sync_subagents.
    created_a_regular = False
    for entry in regular_new:
        idx += 1
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "  Sync interrupted for project %s (after %d/%d sessions)",
                project_id, idx - 1, total_sessions,
            )
            return stats

        # Defensive: extract_session_meta already proved the first line
        # is parseable, but the rest of the file might be empty after a
        # failed write — skip without pushing.
        if not check_file_has_content(entry.file_path):
            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            continue

        payload = _build_create_payload(entry, project_id, False, stats)
        if payload is None:
            if on_session_progress:
                on_session_progress(entry.session_id, idx, total_sessions)
            continue

        # The first session of a not-yet-existing project: the DB writer
        # creates the Project row from this payload's cwd.
        if project is None and not created_a_regular:
            stats["project_created"] = 1
        created_a_regular = True

        sync_queue.put(payload)
        resolvable_parent_ids.add(entry.session_id)

        if on_session_progress:
            on_session_progress(entry.session_id, idx, total_sessions)

    if project is None and not created_a_regular:
        # No top-level Codex session with content for this project this
        # run. Any subagents were handed to _sync_subagents (which also
        # creates their project row from the payload's cwd); a
        # subagent-only project's metadata would recompute to 0/0
        # (subagents are excluded from sessions_count and mtime), so the
        # UpdateProjectMetadataPayload is correctly skipped here.
        elapsed = time.monotonic() - project_start
        logger.info(
            f"  ⊘ Project {project_id} skipped in {elapsed:.1f}s — "
            f"no top-level Codex sessions with content"
        )
        return stats

    # ``sessions_count`` and ``mtime`` are recomputed by the DB writer
    # (recalc_sessions_count / recalc_mtime): both are cross-provider, so a
    # value counted in this producer could clobber — or be clobbered by — a
    # fresher value another provider's compute writes, and reading them here
    # would also race the DB writer applying this run's session payloads.
    sync_queue.put(UpdateProjectMetadataPayload(
        provider=Provider.CODEX,
        project_id=project_id,
        recalc_sessions_count=True,
        recalc_mtime=True,
        # ``project.stale`` is left alone here: it is not provider state, and
        # the boot sweep (twicc.projects.refresh_all_project_directory_states)
        # has already recomputed it for every project before this run started.
        new_stale=None,
        recalc_total_cost=False,
        resolve_git_root=False,
        git_root_directory=None,
    ))

    elapsed = time.monotonic() - project_start
    logger.info(
        f"  ✓ Project {project_id} done in {elapsed:.1f}s — "
        f"{stats['items_added']} items, {stats['sessions_created']} new "
        f"top-level Codex sessions"
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
    Synchronize all Codex sessions from the Codex sessions dir (``codex_sessions_dir()``).

    Pushes payloads onto ``sync_queue`` (the DB writer drains them).
    Every project's top-level sessions are pushed first; subagents are then
    resolved in one global pass (:func:`_sync_subagents`), so a subagent
    whose parent lives in another project is still linked correctly.
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

    sessions_dir = codex_sessions_dir()
    if not sessions_dir.exists():
        logger.info(f"Codex sessions dir not found: {sessions_dir}")
        return stats

    disk_files = scan_session_files()
    disk_files_by_relative_path = {
        str(p.relative_to(sessions_dir)): p for p in disk_files
    }

    # Match disk files to existing DB rows by ``file_path`` (unique in DB),
    # so we don't have to read the first JSON line for sessions we already
    # know about. Only brand-new files require ``extract_session_meta``.
    # Include both top-level sessions and subagents — the latter share the
    # same ``rollout-*.jsonl`` layout under ``YYYY/MM/DD/`` and their
    # ``Session`` rows must be re-matched on subsequent syncs the same
    # way regular sessions are.
    db_sessions_by_path = {
        s.file_path: s
        for s in Session.objects.filter(
            provider=Provider.CODEX,
            type__in=(SessionType.SESSION, SessionType.SUBAGENT),
        )
    }

    ignored_existing_paths = {
        rel_path
        for rel_path, session in db_sessions_by_path.items()
        if (file_path := disk_files_by_relative_path.get(rel_path)) is not None
        and _is_ignored_existing_session(session, file_path)
    }
    if ignored_existing_paths:
        sync_queue.put(DeleteSessionsPayload(
            provider=Provider.CODEX,
            session_ids=[db_sessions_by_path[path].id for path in ignored_existing_paths],
        ))
        db_sessions_by_path = {
            path: session
            for path, session in db_sessions_by_path.items()
            if path not in ignored_existing_paths
        }

    new_by_project: dict[str, list[_NewEntry]] = {}
    existing_by_project: dict[str, list[_ExistingEntry]] = {}

    for rel_path, file_path in disk_files_by_relative_path.items():
        if rel_path in db_sessions_by_path:
            sess = db_sessions_by_path[rel_path]
            existing_by_project.setdefault(sess.project_id, []).append(
                _ExistingEntry(file_path=file_path, session=sess)
            )
        else:
            meta = extract_session_meta(file_path)
            if meta is None or meta.ignored:
                continue
            project_id = path_to_project_id(meta.cwd)
            new_by_project.setdefault(project_id, []).append(
                _NewEntry(
                    file_path=file_path,
                    session_id=meta.session_id,
                    cwd=meta.cwd,
                    parent_session_id=meta.parent_session_id,
                )
            )

    all_project_ids = sorted(set(new_by_project) | set(existing_by_project))
    total_projects = len(all_project_ids)

    logger.info(
        f"Codex sync started — {total_projects} projects, "
        f"{len(disk_files_by_relative_path)} session files"
    )

    # Subagent parents are resolved globally (see _sync_subagents): a Codex
    # subagent can run in a different cwd — hence project — than its parent.
    # Seed with every existing Codex session id; sync_project adds each
    # top-level session it pushes, and _sync_subagents adds each subagent.
    resolvable_parent_ids: set[str] = {s.id for s in db_sessions_by_path.values()}
    all_subagents: list[_NewEntry] = []

    for idx, project_id in enumerate(all_project_ids, start=1):
        if stop_event is not None and stop_event.is_set():
            logger.info(
                "Codex sync interrupted (after %d/%d projects)",
                idx - 1, total_projects,
            )
            break

        if on_project_start:
            on_project_start(project_id, idx, total_projects)

        project_stats = sync_project(
            project_id,
            new_entries=new_by_project.get(project_id, []),
            existing_entries=existing_by_project.get(project_id, []),
            sync_queue=sync_queue,
            resolvable_parent_ids=resolvable_parent_ids,
            subagents_out=all_subagents,
            on_session_progress=on_session_progress,
            stop_event=stop_event,
        )

        stats["projects_created"] += project_stats.get("project_created", 0)
        stats["sessions_created"] += project_stats["sessions_created"]
        stats["items_added"] += project_stats["items_added"]

        if on_project_done:
            on_project_done(project_id, project_stats)

    interrupted = stop_event is not None and stop_event.is_set()

    # Subagent topology pass — global, after every project's top-level
    # sessions have been pushed, so a subagent's parent is resolvable even
    # when it belongs to another project.
    if not interrupted:
        _sync_subagents(
            all_subagents,
            resolvable_parent_ids,
            sync_queue,
            stats,
            on_session_progress=on_session_progress,
            stop_event=stop_event,
        )
        interrupted = stop_event is not None and stop_event.is_set()

    # Mark stale sessions (on DB but no longer on disk). Pushed as a
    # single batch payload so the DB writer issues exactly one bulk UPDATE.
    if not interrupted:
        stale_paths = (
            set(db_sessions_by_path.keys()) - set(disk_files_by_relative_path.keys())
        )
        stale_session_ids = [
            db_sessions_by_path[p].id for p in stale_paths
            if not db_sessions_by_path[p].stale
        ]
        if stale_session_ids:
            sync_queue.put(MarkSessionsStalePayload(
                provider=Provider.CODEX,
                session_ids=stale_session_ids,
            ))
            stats["sessions_stale"] = len(stale_session_ids)

    elapsed = time.monotonic() - sync_start
    if interrupted:
        logger.info(
            f"⚠ Codex sync interrupted after {elapsed:.1f}s — "
            f"{stats['sessions_created']} sessions created, "
            f"{stats['items_added']} items added"
        )
    else:
        logger.info(
            f"✓ Codex sync complete in {elapsed:.1f}s — "
            f"{stats['sessions_created']} sessions created, "
            f"{stats['items_added']} items added"
        )

    return stats
