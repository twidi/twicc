"""Create or adopt a git worktree of a project, registered as a linked Project.

Single orchestration point shared by:

- the HTTP endpoints ``POST /api/projects/<id>/worktrees/`` (create a new
  worktree) and ``POST /api/projects/<id>/worktrees/adopt`` (adopt an
  existing one) (:mod:`twicc.views`), and
- the CLI ``create-session`` flow — ``--worktree-branch`` (create) or
  ``--worktree-path`` alone (adopt) — where
  :func:`twicc.core.services.session_creation.create_session_from_payload`
  calls one of them before creating the session so it lands in the worktree.

Both entry points are transport-agnostic: they return a
:class:`twicc.projects.ProjectMutationResult` (``success`` flag, the new
worktree ``project_id`` + ``Project`` instance, or a list of structured
errors). Callers map the error ``code`` to their own surface — an HTTP
status for the view, a ``rejected`` drop-status for the watcher.

The actual ``git worktree add`` (:func:`twicc.git.create_worktree`) runs
OUTSIDE the DB write lock — the subprocess can take seconds; only the
get-or-create of the source project (when the caller knows it by path
only, e.g. the CLI) and of the new worktree project run under the lock,
mirroring the granularity the HTTP endpoint used before extraction.
Adoption runs no subprocess at all — the worktree already exists on disk.
"""

from __future__ import annotations

import asyncio
import os

from twicc.core.ui_messages import ui_message_text
from asgiref.sync import sync_to_async
from django.db import IntegrityError

from twicc.core.models import Project
from twicc.paths import path_to_project_id
from twicc.projects import (
    ProjectMutationError,
    ProjectMutationResult,
    register_project,
)
from twicc.providers.db_writer import run_under_db_write_lock


def _error(field: str, code: str, message: str) -> ProjectMutationResult:
    return ProjectMutationResult(False, None, None, [ProjectMutationError(field, code, message)])


async def _resolve_source_repo_root(
    source_project_id: str, source_directory: str | None
) -> tuple[str | None, ProjectMutationResult | None]:
    """Resolve the git repo root of the source project.

    Returns ``(repo_root, None)`` on success, or ``(None, error_result)`` with
    a ``project_not_found`` / ``not_git_repo`` code. ``source_directory`` lets
    a caller that only knows the source by path (the CLI) have the source
    Project get-or-created here — so its ``worktree_of`` FK resolves and it
    auto-adds to workspaces from the main process where broadcasts reach
    connected clients. The HTTP caller omits it: the source row already exists
    and a missing one is a ``project_not_found``.
    """
    from twicc.git import resolve_git_from_path

    source = await Project.objects.filter(id=source_project_id).afirst()
    if source is None:
        if not source_directory:
            return None, _error("project", "project_not_found",
                                ui_message_text(f"Project {source_project_id!r} not found.", 'backend.worktrees.project_not_found', {"project_id": repr(source_project_id)}))
        source, _ = await run_under_db_write_lock(
            lambda: register_project(source_project_id, directory=source_directory)
        )

    repo_root = source.git_root
    if not repo_root:
        # Freshly-registered rows have no computed git_root yet; resolve it
        # live from the directory so a never-synced repo still works.
        directory = source.directory or source_directory
        git = (
            await sync_to_async(resolve_git_from_path)(directory, use_cache=False)
            if directory else None
        )
        repo_root = git[0] if git else None
    if not repo_root:
        return None, _error("project", "not_git_repo",
                            ui_message_text(f"Project {source_project_id!r} is not a git repository.", 'backend.worktrees.not_git_repo', {"project_id": repr(source_project_id)}))
    return repo_root, None


async def _register_worktree_project(
    new_project_id: str, resolved: str, source_project_id: str
) -> ProjectMutationResult:
    """Register ``resolved`` as a Project linked to ``source_project_id`` via
    ``worktree_of`` (under the DB write lock). Shared by create + adopt.

    The explicit ``worktree_of_id`` sets the link at row creation: the
    ``project_added`` broadcast and the result both carry it, and the
    filesystem detection (``ensure_worktree_link``) is skipped. ``created`` is
    deliberately not checked: ``register_project`` get-or-creates (and patches
    the link onto a pre-existing directory-less row), so adoption is naturally
    idempotent and a lost race is benign — failing here would orphan a
    worktree that exists on disk.
    """
    try:
        new_project, _created = await run_under_db_write_lock(
            lambda: register_project(
                new_project_id, directory=resolved, worktree_of_id=source_project_id,
            )
        )
    except IntegrityError:
        return ProjectMutationResult(False, new_project_id, None, [
            ProjectMutationError("worktree_path", "project_already_exists",
                                 ui_message_text("A project already exists for this directory.", 'backend.worktrees.project_exists')),
        ])
    return ProjectMutationResult(True, new_project_id, new_project, None)


async def _unarchive_if_needed(project) -> None:
    """Opening a session in an adopted worktree should surface it: if its
    project row was archived (e.g. a worktree the user picks again after
    archiving it), unarchive it and broadcast ``project_updated`` so the new
    session isn't hidden behind the archived filter. No-op when already active.
    """
    if not project.archived:
        return
    project.archived = False
    await run_under_db_write_lock(
        lambda: project.asave(update_fields=["archived"])
    )
    from channels.layers import get_channel_layer
    from twicc.core.serializers import serialize_project
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "project_updated", "project": serialize_project(project)},
    })


async def create_worktree_from_source(
    *,
    source_project_id: str,
    path: str,
    branch: str,
    start_from: str | None = None,
    source_directory: str | None = None,
) -> ProjectMutationResult:
    """Create a git worktree of ``source_project_id`` at ``path`` on ``branch``.

    An existing local ``branch`` is checked out into the worktree
    (``start_from`` ignored); a new one is created with ``-b``, from
    ``start_from`` when given, else from the source repo's current HEAD.

    ``source_directory`` lets a caller that only knows the source by path
    (the CLI) have the source Project get-or-created here, so the
    worktree's ``worktree_of`` FK is valid. The HTTP caller omits it: the
    source row already exists and a missing one is a ``project_not_found``.

    Returns the registered worktree as a
    :class:`~twicc.projects.ProjectMutationResult`. Error codes:
    ``project_not_found``, ``not_git_repo``, ``invalid_path``,
    ``branch_required``, ``project_already_exists``, ``start_from_not_found``,
    ``git_error``.
    """
    from twicc.git import create_worktree, get_branches

    branch = (branch or "").strip()
    path = (path or "").strip()
    start_from = (start_from or "").strip() or None

    # --- resolve the source project + its repo root -------------------
    repo_root, error = await _resolve_source_repo_root(source_project_id, source_directory)
    if error is not None:
        return error

    # --- validate the requested worktree ------------------------------
    if not path or not os.path.isabs(path):
        return _error("worktree_path", "invalid_path", ui_message_text("Path must be an absolute path.", 'backend.worktrees.absolute_path'))
    if not branch:
        return _error("worktree_branch", "branch_required", ui_message_text("Branch is required.", 'backend.worktrees.branch_required'))

    resolved = await asyncio.to_thread(os.path.realpath, path)
    new_project_id = path_to_project_id(resolved)
    if await Project.objects.filter(id=new_project_id).aexists():
        return ProjectMutationResult(False, new_project_id, None, [
            ProjectMutationError("worktree_path", "project_already_exists",
                                 ui_message_text("A project already exists for this directory.", 'backend.worktrees.project_exists')),
        ])

    local_branches = await sync_to_async(get_branches)(repo_root)
    if branch in local_branches:
        start_from = None  # meaningless for an existing-branch checkout
    elif start_from and start_from not in local_branches:
        return _error("worktree_start_from", "start_from_not_found",
                      ui_message_text("Start-from branch does not exist.", 'backend.worktrees.start_from_missing'))

    # Whether the target path exists / is an empty directory is left to git:
    # ``git worktree add`` rejects a non-empty target with a clear message.
    # Run it OUTSIDE the DB write lock — the subprocess can take seconds.
    ok, git_error = await sync_to_async(create_worktree)(repo_root, resolved, branch, start_from)
    if not ok:
        return _error("worktree", "git_error", git_error)

    return await _register_worktree_project(new_project_id, resolved, source_project_id)


async def adopt_existing_worktree(
    *,
    source_project_id: str,
    path: str,
    source_directory: str | None = None,
) -> ProjectMutationResult:
    """Register an EXISTING git worktree of the source repo as a linked Project.

    Unlike :func:`create_worktree_from_source` this runs no ``git worktree
    add`` — the worktree already exists on disk. ``path`` must resolve to a
    real linked worktree of the source repository (verified against ``git
    worktree list``), never an arbitrary directory; the main checkout itself
    is rejected (it is the source repo, not a worktree of it). Powers the UI
    "Existing worktree" tab and the CLI ``create-session --worktree-path``
    (without ``--worktree-branch``).

    Idempotent: if a Project already exists for the path it is returned (and
    its ``worktree_of`` link patched if missing), so the caller can open a
    session on it either way.

    Error codes: ``project_not_found``, ``not_git_repo``, ``invalid_path``,
    ``not_a_worktree``.
    """
    from twicc.git import list_worktrees, resolve_worktree_main_repo

    path = (path or "").strip()

    repo_root, error = await _resolve_source_repo_root(source_project_id, source_directory)
    if error is not None:
        return error

    if not path or not os.path.isabs(path):
        return _error("worktree_path", "invalid_path", ui_message_text("Path must be an absolute path.", 'backend.worktrees.absolute_path'))

    resolved = await asyncio.to_thread(os.path.realpath, path)

    # The target must be a real linked worktree of the source repo — never an
    # arbitrary directory. Match by realpath against ``git worktree list``, and
    # reject the main checkout (the source repo itself). ``resolve_worktree_
    # main_repo`` yields the main root even when the source project is itself a
    # worktree, so the exclusion holds regardless of the entry point.
    worktrees = await sync_to_async(list_worktrees)(repo_root)
    main_root = await sync_to_async(resolve_worktree_main_repo)(repo_root) or repo_root
    main_root_real = await asyncio.to_thread(os.path.realpath, main_root)

    matched = None
    for wt in worktrees:
        wt_real = await asyncio.to_thread(os.path.realpath, wt.path)
        if wt_real == resolved:
            matched = wt
            break
    if matched is None or resolved == main_root_real:
        return _error("worktree_path", "not_a_worktree",
                      ui_message_text("Path is not a linked worktree of this repository.", 'backend.worktrees.not_linked'))
    if matched.is_prunable or not await asyncio.to_thread(os.path.isdir, resolved):
        return _error("worktree_path", "not_a_worktree",
                      ui_message_text("Worktree directory does not exist on disk.", 'backend.worktrees.directory_missing'))

    new_project_id = path_to_project_id(resolved)
    result = await _register_worktree_project(new_project_id, resolved, source_project_id)
    if result.success and result.project is not None:
        await _unarchive_if_needed(result.project)
    return result
