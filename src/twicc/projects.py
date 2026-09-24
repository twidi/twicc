"""
Cross-provider project metadata helpers.

A :class:`Project` row is shared across every provider that owns a session
in the same working directory (its ``id`` is derived from the cwd via
:func:`twicc.paths.path_to_project_id`). The functions in this module own
the project-level fields (``directory``, ``git_root``, ``stale``,
``sessions_count``, ``mtime``, ``total_cost``) and the in-process caches
that back them. Provider-specific code (sync, watcher, compute) calls in
here whenever it learns something new about a project from its own data
stream.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction

from twicc.core.models import Project, Session, SessionType
from twicc.core.serializers import serialize_project
from twicc.git import resolve_git_from_path, resolve_worktree_main_repo
from twicc.paths import path_to_project_id
from twicc.project_color import color_for_project
from twicc.workspaces import (
    add_browser_url_entry,
    auto_add_project_to_workspaces,
    remove_browser_url_entry,
    set_default_browser_url_entry,
)

logger = logging.getLogger(__name__)


# Mirrors ``Project.name``'s ``max_length=25`` (see core/models.py). Surfaced
# here as a constant so the CLI / service / UI all use the same wording.
MAX_PROJECT_NAME_LENGTH = 25


# ---------------------------------------------------------------------------
# Mutation result types (shared between the atomic ops and the service layer)
# ---------------------------------------------------------------------------


class ProjectMutationError(NamedTuple):
    """One structured error returned by a project mutation."""
    field: str
    code: str
    message: str


class ProjectMutationResult(NamedTuple):
    """Outcome of a project mutation (create / update).

    On success: ``project_id`` is set; ``project`` is the final ``Project``
    instance (useful so the watcher / caller can serialise it for the
    status payload). On failure: ``errors`` is non-empty.
    """
    success: bool
    project_id: str | None
    project: Project | None
    errors: list[ProjectMutationError] | None


# =============================================================================
# Project Directory / Git Root Caches
# =============================================================================

# Module-level cache: project_id -> directory (can be None)
_project_directories: dict[str, str | None] = {}
# Module-level cache: project_id -> git_root (can be None)
_project_git_roots: dict[str, str | None] = {}


def load_project_directories() -> None:
    """
    Load all project directories into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_directories.clear()
    _project_directories.update(
        Project.objects.values_list('id', 'directory')
    )


def load_project_git_roots() -> None:
    """
    Load all project git_roots into the cache.

    Should be called once at process startup (watcher or compute background task).
    """
    _project_git_roots.clear()
    _project_git_roots.update(
        Project.objects.values_list('id', 'git_root')
    )


def get_project_directory(project_id: str) -> str | None:
    """Get cached directory for a project."""
    return _project_directories.get(project_id)


def project_directories_cached(project_ids: Iterable[str]) -> dict[str, str | None]:
    """Directory of each project, read through the module cache.

    A cached key answers without a query, a stored ``None`` included: the test
    is key membership, never ``.get()``, which cannot tell a miss from a hit.
    The misses cost ONE query, and each row found is stored with a single-key
    assignment (atomic under the GIL, never ``clear()``), so a concurrent
    ``load_project_directories`` rebuild costs at most one extra read of the
    same value. An id with no ``Project`` row maps to ``None`` and is not stored.
    """
    found: dict[str, str | None] = {}
    misses: list[str] = []
    for project_id in dict.fromkeys(project_ids):
        try:
            found[project_id] = _project_directories[project_id]
        except KeyError:
            misses.append(project_id)
    if misses:
        for project_id, directory in Project.objects.filter(id__in=misses).values_list("id", "directory"):
            _project_directories[project_id] = directory
            found[project_id] = directory
        for project_id in misses:
            found.setdefault(project_id, None)
    return found


def project_directory_cached(project_id: str) -> str | None:
    """One-id form of :func:`project_directories_cached`."""
    return project_directories_cached([project_id])[project_id]


def get_project_git_root(project_id: str) -> str | None:
    """Get cached git_root for a project."""
    return _project_git_roots.get(project_id)


def compute_project_stale(directory: str | None) -> bool:
    """Whether a project pointing at *directory* must be flagged ``stale``.

    ``Project.stale`` means "the working directory was gone the last time TwiCC
    looked": a stored observation, never re-checked at render time. A project
    whose directory is still unknown (``None``) is never stale.

    Single source of truth for the three sites that recompute the flag: the
    startup recompute (claude_code ``initial_sync``), the provider-folder
    watcher, and the action-time :func:`refresh_project_directory_state`.
    """
    return directory is not None and not os.path.isdir(directory)


def ensure_project_directory(project_id: str, cwd: str) -> None:
    """
    Ensure the project's directory is set and up-to-date.

    - If project not in cache: load from DB and cache
    - If directory differs from cwd: update DB and cache
    - If directory matches cwd: do nothing

    Args:
        project_id: The project ID
        cwd: The current working directory from a session line
    """
    # Load project into cache if not present
    if project_id not in _project_directories:
        try:
            directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
        except Project.DoesNotExist:
            # Project doesn't exist yet, skip (will be handled when project is created)
            return
        _project_directories[project_id] = directory

    # Check if update needed
    if _project_directories[project_id] == cwd:
        return

    # Update DB now; refresh the cache only once the surrounding transaction
    # commits. apply_session_complete and the DB writer call this
    # inside transaction.atomic — a rollback there would otherwise leave the
    # cache ahead of the DB, and the `== cwd` check above would then suppress
    # the corrective write forever. on_commit runs immediately when there is
    # no active transaction, so non-transactional callers are unaffected.
    should_be_stale = compute_project_stale(cwd)
    Project.objects.filter(id=project_id).update(directory=cwd, stale=should_be_stale)
    transaction.on_commit(lambda: _project_directories.update({project_id: cwd}))

    # Re-resolve git_root when directory changes
    ensure_project_git_root(project_id, cwd)


def ensure_project_git_root(project_id: str, directory: str | None = None) -> str | None:
    """
    Resolve and store git_root for a project.

    Called:
    - At sync_all (startup) for all projects with a directory
    - At project registration, once a directory is known (register_project_db_only)
    - When project.directory changes (from ensure_project_directory)
    - When a session gets git info but project.git_root is still None

    Returns the resolved git_root (``None`` when the directory backs no git
    repository, or is unknown) — even when no DB write was needed — so a caller
    holding the ``Project`` instance can mirror it onto the in-memory row: the
    write here is a bare ``UPDATE`` that does not touch the instance, so a
    serialiser running off the returned object would otherwise report a stale
    ``git_root``.

    Args:
        project_id: The project ID
        directory: The project directory to resolve from. If None, uses cached/DB value.
    """
    if directory is None:
        directory = _project_directories.get(project_id)
        if directory is None:
            try:
                directory = Project.objects.values_list('directory', flat=True).get(id=project_id)
            except Project.DoesNotExist:
                return None
        if not directory:
            return None

    result = resolve_git_from_path(directory, use_cache=False)
    git_root = result[0] if result else None

    # Check if update needed
    if _project_git_roots.get(project_id) == git_root:
        return git_root

    # Update DB now; refresh the cache only once the surrounding transaction
    # commits, so a rollback never leaves the cache ahead of the DB (the
    # `== git_root` check above would otherwise suppress the corrective
    # write). on_commit runs immediately when there is no active transaction.
    Project.objects.filter(id=project_id).update(git_root=git_root)
    transaction.on_commit(lambda: _project_git_roots.update({project_id: git_root}))
    return git_root


async def reresolve_project_git_root(project_id: str) -> tuple[bool, str | None]:
    """Action-time git_root verifier behind the worktree-creation affordances.

    Re-resolves *project_id*'s git_root live from its directory (a fresh
    filesystem walk, cache bypassed), persists it, and broadcasts
    ``project_updated`` when it changed — in BOTH directions: a repo that was
    ``git init``-ed after the project was created, or one whose ``.git`` was
    removed. The UI renders the "new session in a worktree" affordances from
    the cached ``git_root`` (never blocking on it, never re-checking per
    render); this is what those affordances call when the user actually acts,
    so the stale flag heals without a backend restart.

    Returns ``(found, git_root)``. ``found=False`` means the project row does
    not exist (caller maps to 404). ``git_root`` is the freshly resolved value
    (``None`` when the directory backs no git repository, or the project has no
    directory to resolve from).
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return (False, None)
    directory = project.directory
    if not directory:
        return (True, project.git_root)

    old = project.git_root
    new = await run_under_db_write_lock(
        lambda: sync_to_async(ensure_project_git_root)(project_id, directory)
    )
    if new != old:
        await _broadcast_project_updated(project_id)
    return (True, new)


async def refresh_project_directory_state(project_id: str) -> tuple[bool, Project | None]:
    """Action-time directory verifier behind the project dialog's "Re-check" button.

    ``Project.stale`` is a STORED observation (see :func:`compute_project_stale`),
    refreshed at startup by the claude_code initial sync and by the watcher when
    a *provider* project folder appears or disappears. Nothing watches the
    working directories themselves, so a directory deleted — or restored — while
    TwiCC runs keeps the stored flag until the next restart. This re-stats it
    live, persists, and broadcasts ``project_updated`` on change, so the flag
    heals without a backend restart. Both directions, like
    :func:`reresolve_project_git_root`.

    ``git_root`` is re-resolved in the same pass, but ONLY when the directory is
    there: a restored repository would otherwise keep whatever value it had when
    it went away (typically ``None`` for a project first seen while its
    directory was already gone). Resolving from a MISSING directory can only
    yield ``None``, which would destroy the last known value for no gain — so a
    stale project keeps its ``git_root`` untouched.

    Returns ``(found, project)``. ``found=False`` means the project row does not
    exist (caller maps to 404) and ``project`` is ``None``. Otherwise *project*
    is the refreshed row, with the fresh values mirrored onto the in-memory
    instance (the writes below are bare ``UPDATE``s) so it is safe to serialise.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return (False, None)

    old_stale = project.stale
    old_git_root = project.git_root
    new_stale = compute_project_stale(project.directory)
    directory = project.directory

    async def _apply() -> str | None:
        if new_stale != old_stale:
            await sync_to_async(Project.objects.filter(id=project_id).update)(stale=new_stale)
        if not new_stale and directory:
            return await sync_to_async(ensure_project_git_root)(project_id, directory)
        return old_git_root

    new_git_root = await run_under_db_write_lock(_apply)

    project.stale = new_stale
    project.git_root = new_git_root
    if new_stale != old_stale or new_git_root != old_git_root:
        await _broadcast_project_updated(project_id)
    return (True, project)


async def refresh_all_project_directory_states() -> int:
    """Cross-provider boot sweep: recompute ``stale`` for every known project.

    ``Project.stale`` only tracks whether ``Project.directory`` exists on disk;
    nothing about it is provider-specific. It used to be recomputed inside
    claude_code's ``sync_all``, so a TwiCC running Codex alone never refreshed
    it at all — a project flagged once stayed flagged for good. This is that
    same pass, owned by nobody in particular.

    Call it from the boot sequence BEFORE any provider orchestrator starts: the
    flag is then already correct when the first project syncs, and there is no
    race with :func:`ensure_project_directory` writing ``directory`` + ``stale``
    together from a session's cwd.

    The ``isdir`` calls run in a thread — a project on a dead network mount can
    block for seconds. Returns the number of rows whose flag changed.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    def _collect_changed() -> list[tuple[str, bool]]:
        changed = []
        for project in Project.objects.only("id", "directory", "stale").iterator():
            should_be_stale = compute_project_stale(project.directory)
            if project.stale != should_be_stale:
                changed.append((project.id, should_be_stale))
        return changed

    changed = await sync_to_async(_collect_changed)()
    if not changed:
        return 0

    def _apply() -> None:
        for project_id, should_be_stale in changed:
            Project.objects.filter(id=project_id).update(stale=should_be_stale)

    await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    # No-op at boot (nothing is connected yet, and the client reads fresh rows
    # on connect), but it keeps this callable at runtime without silently
    # leaving clients behind.
    for project_id, _ in changed:
        await _broadcast_project_updated(project_id)
    return len(changed)


# =============================================================================
# Project creation — single entry point
# =============================================================================
#
# Every code path that creates a ``Project`` row goes through ``register_project``
# (async). It wraps ``Project.objects.get_or_create`` and runs the
# after-creation hooks atomically: WS broadcast of ``project_added`` and
# workspace auto-add (when the directory is already known).
#
# For callers that learn the directory only later (the watcher between
# ``parse_session_file`` and ``sync_session_items_from_file``, and the
# claude_code initial sync where the cwd is in the JSONL body), pass
# ``directory=None`` here and call :func:`auto_add_project_to_workspaces`
# once the directory has been resolved. That second call is idempotent —
# a workspace that already lists the project is left untouched and no
# broadcast fires.


def _create_or_get_project(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
    worktree_of_id: str | None = None,
) -> tuple[Project, bool]:
    """Pure DB operation: get-or-create a Project row.

    Returns ``(project, was_just_created)``. No broadcasts, no auto-add —
    callers should go through ``register_project`` (async) instead, which
    runs the post-creation hooks.
    """
    defaults: dict = {}
    if directory:
        defaults["directory"] = directory
    if name is not None:
        defaults["name"] = name
    if color is not None:
        defaults["color"] = color
    if stale is not None:
        defaults["stale"] = stale
    if worktree_of_id is not None:
        defaults["worktree_of_id"] = worktree_of_id
    project, created = Project.objects.get_or_create(id=project_id, defaults=defaults)
    if created and directory:
        # Mirror the cache update that ``ensure_project_directory`` would do
        # so subsequent reads don't have to round-trip the DB. Deferred to
        # post-commit so a rolled-back transaction never leaves the cache
        # ahead of the DB (on_commit runs immediately outside a transaction).
        transaction.on_commit(lambda: _project_directories.update({project_id: directory}))
    return project, created


async def _broadcast_project_added(project: Project) -> None:
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "project_added", "project": serialize_project(project)},
    })


async def _broadcast_project_updated(project_id: str) -> None:
    """Re-read *project_id* from the DB and broadcast ``project_updated``.

    Unlike :func:`_broadcast_project_added`, this takes an id and re-fetches:
    the only caller (:func:`link_worktree_to_repo`) patched the row with a bare
    ``UPDATE`` (:func:`_set_worktree_of`), so there is no up-to-date in-memory
    instance to serialise. Silent no-op if the row vanished. A broadcast to a
    channel layer with no subscribers (e.g. worktree backfill before any WS
    client connects) is itself a no-op.
    """
    project = await sync_to_async(Project.objects.filter(id=project_id).first)()
    if project is None:
        return
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "project_updated", "project": serialize_project(project)},
    })


def _adopt_directory_sync(project: Project, directory: str) -> None:
    """Patch ``project.directory`` in DB + cache (sync). Caller checks that
    the project actually lacks a directory before invoking. The cache refresh
    is deferred to post-commit so a rolled-back transaction never leaves the
    cache ahead of the DB (on_commit runs immediately outside a transaction)."""
    project.directory = directory
    project.save(update_fields=["directory"])
    project_id = project.id
    transaction.on_commit(lambda: _project_directories.update({project_id: directory}))


def register_project_db_only(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
    worktree_of_id: str | None = None,
) -> tuple[Project, bool, bool]:
    """DB-only half of project registration: get-or-create + directory adoption.

    ``worktree_of_id`` sets the worktree link explicitly at creation; callers
    that already know the parent (e.g. the worktree-creation endpoint) use it
    instead of the filesystem detection of :func:`ensure_worktree_link`.

    Returns ``(project, was_just_created, adopted_directory)`` and runs **no**
    async side effects — no ``project_added`` broadcast, no workspace auto-add
    — so it is safe to call from inside a ``transaction.atomic()`` block. It
    does resolve ``git_root`` when the directory is first established here (a
    plain DB write, like the directory adoption below, mirrored onto the
    returned instance) so a never-synced git repo registered via the RPC/API
    drop-request exposes its repo root immediately, instead of staying
    ``git_root=None`` until the next startup sync re-resolves it — the
    worktree-creation affordance and the Git tab both gate on ``git_root``.

    Callers that need the side effects must run them themselves once the
    surrounding transaction has committed: broadcast ``project_added`` when
    ``created`` is true, and call
    :func:`twicc.workspaces.auto_add_project_to_workspaces` only when the
    project was just created or just adopted a directory
    (``created or adopted_directory``) — never on every call, since an
    existing project's workspace membership cannot change just because another
    of its sessions was synced. :func:`register_project` is the async wrapper
    that does exactly that; the DB writer
    (:mod:`twicc.providers.db_writer`) calls this directly so a project is
    never announced from inside — or despite a rollback of — its transaction.
    """
    project, created = _create_or_get_project(
        project_id, directory=directory, name=name, color=color, stale=stale,
        worktree_of_id=worktree_of_id,
    )
    adopted = False
    if not created and directory and not project.directory:
        _adopt_directory_sync(project, directory)
        adopted = True
    if worktree_of_id is not None and not created and project.worktree_of_id != worktree_of_id:
        # get_or_create defaults don't apply to a pre-existing row (lost race).
        project.worktree_of_id = worktree_of_id
        project.save(update_fields=["worktree_of"])
    # Resolve git_root exactly when the directory is first established (row
    # created, or a directory-less row just adopted one) — not on every
    # re-registration of an existing project, whose git_root only changes when
    # its directory does (handled by ensure_project_directory). The bare UPDATE
    # in ensure_project_git_root leaves the instance untouched, so mirror the
    # resolved value back onto it for the caller's broadcast / response.
    if directory and (created or adopted):
        project.git_root = ensure_project_git_root(project_id, directory)
    return project, created, adopted


async def register_project(
    project_id: str,
    *,
    directory: str | None = None,
    name: str | None = None,
    color: str | None = None,
    stale: bool | None = None,
    worktree_of_id: str | None = None,
) -> tuple[Project, bool]:
    """Single entry point for Project creation. Returns ``(project, was_just_created)``.

    On creation:

    1. Broadcasts ``project_added`` via the channel layer.
    2. If ``directory`` is provided, runs workspace auto-add (which
       broadcasts ``workspaces_updated`` on hit).

    If the project already exists *without* a directory and one is provided
    here, the directory is adopted and workspace auto-add is re-evaluated.
    Common when ``claude_code/initial_sync.py`` created the row before the
    background compute pass filled the cwd in.

    Pass ``directory=None`` for flows that resolve the directory only after
    creation (the file watcher between the bare ``get_or_create`` and the
    JSONL sync that fills in ``cwd``); call
    :func:`twicc.workspaces.auto_add_project_to_workspaces` once the
    directory has been set.

    Broadcasts targeting a channel layer with no subscribers (e.g.
    initial sync before any WS client is connected) are silent no-ops.
    """
    project, created, adopted = await sync_to_async(register_project_db_only)(
        project_id,
        directory=directory, name=name, color=color, stale=stale,
        worktree_of_id=worktree_of_id,
    )
    if created:
        await _broadcast_project_added(project)
    if (created or adopted) and project.directory:
        await auto_add_project_to_workspaces(project.id, project.directory)
        if worktree_of_id is None:
            # Explicit link already set at creation — skip filesystem detection.
            detected_parent_id = await ensure_worktree_link(project.id, project.directory)
            if detected_parent_id is not None:
                # ensure_worktree_link patched ``worktree_of`` in the DB only
                # (bare UPDATE via _set_worktree_of). Mirror it onto the
                # in-memory instance so a caller serialising this object —
                # notably the POST /api/projects/ 201 response — reports the
                # worktree link. Without this the response carries a stale
                # ``worktree_of = None`` that the frontend writes over the
                # correct ``project_updated`` broadcast, leaving the worktree
                # unmarked in the live UI until a reload.
                project.worktree_of_id = detected_parent_id
        # Auto-color runs after worktree detection so a worktree is never
        # colored (it inherits its main repo's color). Mirror the assigned
        # color onto the instance for the caller's serialization (e.g. the
        # POST /api/projects/ 201 response).
        new_color = await ensure_project_color(project.id, project.directory)
        if new_color:
            project.color = new_color
        # Project icon: resolve the anchor (worktree main repo / umbrella /
        # own git root) and auto-discover the repo's favicon/logo. Runs after
        # worktree detection + color so the anchor and inheritance are settled.
        # Never touches Project.icon; broadcasts project_updated on discovery.
        from twicc.project_icons import ensure_project_icon
        await ensure_project_icon(project)

    return project, created


def _set_worktree_of(project_id: str, parent_id: str) -> None:
    """Point ``project_id.worktree_of`` at ``parent_id`` (sync DB write)."""
    Project.objects.filter(id=project_id).update(worktree_of_id=parent_id)


def _find_project_for_repo(main_repo: str) -> str | None:
    """Return the id of an existing Project whose directory resolves to
    *main_repo* (already an absolute realpath), or None.

    Prefers the canonical id ``path_to_project_id(main_repo)`` so the common
    case needs no scan. Falls back to matching by ``realpath(directory)`` so a
    main repo already stored under a non-canonical path — e.g. its cwd was a
    symlink and the codex/watcher flows store it unresolved, unlike the
    realpath'd API/CLI flow — is reused instead of being duplicated.
    """
    canonical_id = path_to_project_id(main_repo)
    if Project.objects.filter(id=canonical_id).exists():
        return canonical_id
    for pid, pdir in Project.objects.exclude(directory__isnull=True).values_list("id", "directory"):
        if pdir and os.path.realpath(pdir) == main_repo:
            return pid
    return None


async def link_worktree_to_repo(project_id: str, main_repo: str) -> str | None:
    """Point *project_id*'s ``worktree_of`` at the project of *main_repo*.

    *main_repo* is the absolute realpath of a main repository root. Reuses an
    existing project that already points there (see :func:`_find_project_for_repo`),
    under any id, so a symlinked/unresolved main-repo path is not duplicated;
    otherwise falls back to the canonical id and **registers it unconditionally**
    (idempotent: creates the parent if absent, completes it — directory adoption
    + workspace auto-add — if it was still in the transient ``directory=None``
    state, no-op if already complete). Returns the parent id, or None when it
    would be a self-link (a repository is never its own worktree).

    Shared by live detection (:func:`ensure_worktree_link`) and the worktree
    backfill (:mod:`twicc.worktree_backfill`).
    """
    parent_id = await sync_to_async(_find_project_for_repo)(main_repo)
    if parent_id is None:
        parent_id = path_to_project_id(main_repo)
    if parent_id == project_id:
        return None
    await register_project(parent_id, directory=main_repo)
    await sync_to_async(_set_worktree_of)(project_id, parent_id)
    # The link is now in the DB, but the child's ``project_added`` already went
    # out with ``worktree_of = None`` (the link is resolved *after* creation,
    # see :func:`register_project`). Re-broadcast the child so connected clients
    # pick up the worktree link immediately, instead of only when some unrelated
    # event (e.g. its first session syncing) next re-serialises the project.
    await _broadcast_project_updated(project_id)
    return parent_id


async def ensure_worktree_link(project_id: str, directory: str) -> str | None:
    """Link *project_id* to its main repository when *directory* is a live git
    worktree (its ``.git`` pointer file is readable).

    Returns the parent (main repository) project id when a link is established,
    else ``None`` — so a caller holding the freshly-created ``Project`` instance
    can mirror the link onto it (``_set_worktree_of`` only patches the DB).

    No-op when *directory* is not inside a linked git worktree (or its ``.git``
    is unreadable because the folder is gone). Otherwise resolves the main
    repository and delegates to :func:`link_worktree_to_repo`.

    Async because it goes through ``register_project`` (broadcast + workspace
    auto-add need the event loop). Call it right after the workspace auto-add
    of *project_id*, in every context that performs one. Registering a parent
    re-enters this helper for it, but a main repository is not a worktree, so
    the recursion stops on the first call.
    """
    main_repo = resolve_worktree_main_repo(directory)
    if not main_repo:
        return None
    return await link_worktree_to_repo(project_id, os.path.realpath(main_repo))


def _ensure_project_color_sync(project_id: str, directory: str | None) -> str | None:
    """Assign a stable auto-generated color to a project that lacks one (sync).

    No-op (returns ``None``) when the project already has a color, is a git
    worktree (``worktree_of`` set → it inherits its main repository's color), is
    absent, or has neither a name nor a directory to derive a label from.
    Otherwise computes the color from the project name (or its final directory
    segment) and persists it with a ``color IS NULL`` guarded UPDATE so a
    concurrent write is never clobbered. Returns the color only when this call
    is the one that wrote it, so the async wrapper broadcasts exactly once.
    """
    row = (
        Project.objects.filter(id=project_id)
        .values("name", "color", "worktree_of_id", "directory")
        .first()
    )
    if row is None or row["color"] or row["worktree_of_id"]:
        return None
    color = color_for_project(row["name"], directory or row["directory"])
    if color is None:
        return None
    updated = Project.objects.filter(id=project_id, color__isnull=True).update(color=color)
    return color if updated else None


async def ensure_project_color(project_id: str, directory: str | None) -> str | None:
    """Give *project_id* an auto-generated color when it has none.

    Idempotent and safe to call from every project-creation choke point (right
    after :func:`ensure_worktree_link`, so the worktree status is already known).
    Broadcasts ``project_updated`` when — and only when — a color was written, so
    the freshly created project (announced by ``project_added`` before it had a
    color) lights up live. Returns the color it assigned, else ``None``.
    """
    color = await sync_to_async(_ensure_project_color_sync)(project_id, directory)
    if color is not None:
        await _broadcast_project_updated(project_id)
    return color


# ---------------------------------------------------------------------------
# Worktree scope helpers (sync reads — mirror the frontend store getters)
# ---------------------------------------------------------------------------
#
# A git worktree's sessions/cost/activity belong to its main repository's
# whole: viewing a main repo aggregates its worktrees' sessions, like a
# workspace aggregates its members one level down. These helpers are the
# backend equivalents of the Pinia getters that drive that aggregation in the
# UI (``getProjectScopeIds`` / ``getAllProjectIds`` / ``getWorktreesOf``), so
# the CLI/API report the same scope the UI shows. All are sync ORM reads,
# meant to be called after ``django.setup()`` from the CLI command bodies.


def worktree_child_ids(main_project_id: str) -> list[str]:
    """Ids of the git worktrees whose main repository is *main_project_id*.

    Mirrors the frontend ``getWorktreesOf`` getter: every project whose
    ``worktree_of`` points at *main_project_id*, most-recently-active first.
    Returns ``[]`` when the project has no worktrees (or does not exist).
    """
    return list(
        Project.objects.filter(worktree_of_id=main_project_id)
        .order_by("-mtime")
        .values_list("id", flat=True)
    )


def worktree_children_by_main(main_project_ids: Iterable[str]) -> dict[str, list[str]]:
    """Batch form of :func:`worktree_child_ids` for serializing a page of projects.

    Returns a ``{main_repo_id: [worktree_child_id, ...]}`` map (each list in
    ``-mtime`` order). Main ids with no worktrees are simply absent from the
    map. One query whatever the page size.
    """
    children: dict[str, list[str]] = {}
    for child_id, main_id in (
        Project.objects.filter(worktree_of_id__in=list(main_project_ids))
        .order_by("-mtime")
        .values_list("id", "worktree_of_id")
    ):
        children.setdefault(main_id, []).append(child_id)
    return children


def project_scope_ids(project_id: str) -> list[str]:
    """The session scope of *project_id*: itself plus its own git worktrees.

    Mirrors the frontend ``getProjectScopeIds`` exactly: a normal project folds
    in its worktrees (whose sessions belong to its main repository's whole),
    while a worktree — having no worktrees of its own — scopes to just its own
    sessions. The expansion is strictly downward: passing a worktree id does
    **not** pull in its main repository or its sibling worktrees.

    *project_id* comes first; its worktree child ids follow, most-recently-
    active first. An unknown id degrades to ``[project_id]``.
    """
    return [project_id, *worktree_child_ids(project_id)]


def expand_project_ids_with_worktrees(project_ids: Iterable[str]) -> list[str]:
    """Expand a set of project ids with each project's git worktrees.

    Mirrors the frontend workspace getter ``getAllProjectIds``: a workspace's
    scope is every member plus each member's worktrees. The input order is
    preserved, each member immediately followed by its worktrees; duplicates
    are dropped. Archived state is ignored (archived-blind), like the UI's
    whole-workspace aggregation — the session-level archived filter handles
    hiding.
    """
    ids = list(project_ids)
    if not ids:
        return []
    children = worktree_children_by_main(ids)
    result: list[str] = []
    seen: set[str] = set()
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            result.append(pid)
        for child_id in children.get(pid, []):
            if child_id not in seen:
                seen.add(child_id)
                result.append(child_id)
    return result


def update_project_total_cost(project_id: str) -> None:
    """
    Recalculate and save a project's total_cost.

    Uses Project.recalculate_total_cost() which sums total_cost from
    non-stale SESSION-type sessions.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    project.recalculate_total_cost()
    project.save(update_fields=["total_cost"])


# ---------------------------------------------------------------------------
# Name validation (pure helpers shared by CLI pre-flight + service)
# ---------------------------------------------------------------------------


def validate_project_name_format(
    name: str | None,
    *,
    field: str = "name",
) -> list[ProjectMutationError]:
    """Validate a project name's format: trim, ≤ MAX length.

    ``None`` and empty (after trim) are both OK — the name field is
    nullable and an empty string is normalised to None at write time.
    Uniqueness is checked separately by callers that can do an async DB
    query (the helper stays pure).
    """
    if name is None:
        return []
    trimmed = name.strip()
    if not trimmed:
        return []
    if len(trimmed) > MAX_PROJECT_NAME_LENGTH:
        return [ProjectMutationError(
            field, "invalid_name",
            f"Name must be ≤ {MAX_PROJECT_NAME_LENGTH} characters (got {len(trimmed)}).",
        )]
    return []


# ---------------------------------------------------------------------------
# Atomic update — read-modify-write under the DB write lock
# ---------------------------------------------------------------------------


async def update_project_atomic(
    project_id: str,
    *,
    new_name: str | None = None,
    unset_name: bool = False,
    color: str | None = None,
    unset_color: bool = False,
    archived: bool | None = None,
    default_provider: str | None = None,
    unset_default_provider: bool = False,
    worktree_directory: str | None = None,
    unset_worktree_directory: bool = False,
    add_browser_url: dict | None = None,
    remove_browser_url: str | None = None,
    set_default_browser_url: str | None = None,
    clear_browser_urls: bool = False,
) -> ProjectMutationResult:
    """Atomically apply a patch to an existing project.

    Mutually-exclusive flags (``new_name`` vs ``unset_name``, ``color`` vs
    ``unset_color``, ``default_provider`` vs ``unset_default_provider``,
    ``worktree_directory`` vs ``unset_worktree_directory``) must be enforced
    by the caller before invocation — this function trusts its inputs (the
    ``unset`` wins if both are set). ``default_provider`` is assumed already
    validated as a registered provider value; ``worktree_directory`` already
    trimmed and non-empty.

    Saved browser URLs are patched through ops on the ``browser_urls`` entry
    list, applied in order remove → add → set-default (same semantics as the
    workspace ops in :mod:`twicc.workspaces`):

    - ``add_browser_url``: ``{"url": str, "label": str|None, "set_default": bool}``
      with the URL already normalized/validated (idempotent on a listed URL).
    - ``remove_browser_url`` / ``clear_browser_urls``: idempotent removals.
    - ``set_default_browser_url``: fails with ``url_not_found`` when the URL
      is not in the list.

    Runs under :func:`run_under_db_write_lock` and broadcasts
    ``project_updated`` out of the lock on success. On
    ``Project.DoesNotExist`` returns a failed result with
    ``project_not_found``; on a DB ``IntegrityError`` (name collision)
    returns a failed result with ``duplicate_name``.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    def _do_update() -> ProjectMutationResult:
        with transaction.atomic():
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                return ProjectMutationResult(False, None, None, [
                    ProjectMutationError("PROJECT_ID", "project_not_found",
                                          f"Project {project_id!r} not found."),
                ])

            update_fields: list[str] = []

            if unset_name:
                if project.name is not None:
                    project.name = None
                    update_fields.append("name")
            elif new_name is not None:
                trimmed = new_name.strip()
                normalized = trimmed if trimmed else None
                if project.name != normalized:
                    project.name = normalized
                    update_fields.append("name")

            if unset_color:
                if project.color is not None:
                    project.color = None
                    update_fields.append("color")
            elif color is not None:
                if project.color != color:
                    project.color = color
                    update_fields.append("color")

            if archived is not None:
                if project.archived != bool(archived):
                    project.archived = bool(archived)
                    update_fields.append("archived")

            if unset_default_provider:
                if project.default_provider is not None:
                    project.default_provider = None
                    update_fields.append("default_provider")
            elif default_provider is not None:
                if project.default_provider != default_provider:
                    project.default_provider = default_provider
                    update_fields.append("default_provider")

            if unset_worktree_directory:
                if project.worktree_directory is not None:
                    project.worktree_directory = None
                    update_fields.append("worktree_directory")
            elif worktree_directory is not None:
                if project.worktree_directory != worktree_directory:
                    project.worktree_directory = worktree_directory
                    update_fields.append("worktree_directory")

            if clear_browser_urls or remove_browser_url or add_browser_url or set_default_browser_url:
                entries = list(project.browser_urls or [])
                if clear_browser_urls:
                    entries = []
                if remove_browser_url:
                    entries = remove_browser_url_entry(entries, remove_browser_url)
                if add_browser_url:
                    entries = add_browser_url_entry(
                        entries,
                        add_browser_url["url"],
                        label=add_browser_url.get("label"),
                        set_default=bool(add_browser_url.get("set_default")),
                    )
                if set_default_browser_url:
                    entries, found = set_default_browser_url_entry(entries, set_default_browser_url)
                    if not found:
                        return ProjectMutationResult(False, project_id, None, [
                            ProjectMutationError(
                                "--set-default-browser-url", "url_not_found",
                                f"URL {set_default_browser_url!r} is not in the project's "
                                "saved browser URLs."),
                        ])
                if entries != (project.browser_urls or []):
                    project.browser_urls = entries
                    update_fields.append("browser_urls")

            if not update_fields:
                # No-op write: the row already matches the patch. Treat as
                # success with the existing project — the CLI's no_op
                # pre-check usually catches this, so the path is rare.
                return ProjectMutationResult(True, project_id, project, None)

            try:
                project.save(update_fields=update_fields)
            except IntegrityError:
                return ProjectMutationResult(False, project_id, None, [
                    ProjectMutationError("--name", "duplicate_name",
                                          "Another project already uses this name."),
                ])
            return ProjectMutationResult(True, project_id, project, None)

    result = await run_under_db_write_lock(lambda: sync_to_async(_do_update)())

    if result.success and result.project is not None:
        channel_layer = get_channel_layer()
        await channel_layer.group_send("updates", {
            "type": "broadcast",
            "data": {
                "type": "project_updated",
                "project": serialize_project(result.project),
            },
        })
    return result


async def update_project_agent_defaults_atomic(
    project_id: str,
    *,
    provider: str,
    updates: dict,
) -> ProjectMutationResult:
    """Atomically patch one provider's bundle in ``default_agent_settings``.

    ``updates`` maps field names to values: a non-``None`` value sets the
    field, ``None`` removes it (back to inherit). Other fields of the bundle
    — and the other providers' bundles — are untouched. Empty bundles are
    dropped so storage stays sparse (``{}`` collapses to ``NULL``).

    The caller is responsible for validation (field names, value choices,
    ``permission_mode_if_untrusted`` in the untrusted-allowed set) — see
    ``clean_project_agent_defaults`` in
    :mod:`twicc.core.services.project_mutation`.

    Runs under :func:`run_under_db_write_lock` and broadcasts
    ``project_updated`` out of the lock on success.
    """
    from twicc.providers.db_writer import run_under_db_write_lock

    def _do_update() -> ProjectMutationResult:
        with transaction.atomic():
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                return ProjectMutationResult(False, None, None, [
                    ProjectMutationError("PROJECT_ID", "project_not_found",
                                          f"Project {project_id!r} not found."),
                ])

            current = project.default_agent_settings or {}
            bundle = dict(current.get(provider) or {})
            for field, value in updates.items():
                if value is None:
                    bundle.pop(field, None)
                else:
                    bundle[field] = value

            new_settings = {k: v for k, v in current.items() if k != provider}
            if bundle:
                new_settings[provider] = bundle
            normalized = new_settings or None

            if normalized == (project.default_agent_settings or None):
                # No-op write: the bundle already matches the patch.
                return ProjectMutationResult(True, project_id, project, None)

            project.default_agent_settings = normalized
            project.save(update_fields=["default_agent_settings"])
            return ProjectMutationResult(True, project_id, project, None)

    result = await run_under_db_write_lock(lambda: sync_to_async(_do_update)())

    if result.success and result.project is not None:
        channel_layer = get_channel_layer()
        await channel_layer.group_send("updates", {
            "type": "broadcast",
            "data": {
                "type": "project_updated",
                "project": serialize_project(result.project),
            },
        })
    return result


@transaction.atomic
def update_project_metadata(project_id: str) -> None:
    """Update project sessions_count, mtime, and total_cost from its sessions."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return
    sessions = Session.objects.filter(
        project=project, type=SessionType.SESSION, created_at__isnull=False, user_message_count__gt=0, hidden=False
    )
    project.sessions_count = sessions.count()
    # mtime excludes stale sessions (JSONL gone from disk): a stale session
    # must not keep the project's mtime -- and thus its sort position -- high.
    # sessions_count above intentionally keeps stale sessions (matching
    # recalc_sessions_count in the DB writer); only this mtime
    # aggregate filters them out. Keep this filter aligned with
    # _compute_project_mtime() in twicc.providers.db_writer.
    max_mtime = (
        sessions.filter(stale=False).order_by("-mtime").values_list("mtime", flat=True).first()
    )
    project.mtime = max_mtime or 0
    project.save(update_fields=["sessions_count", "mtime"])

    update_project_total_cost(project_id)
