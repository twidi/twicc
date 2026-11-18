"""What a principal may see and operate: the per-project access resolver.

The single answer to "can this caller see / act on project X?". Every gate —
HTTP views, the ``/ws/`` broadcast filter, inbound WS actions, search,
timeline, artifacts — asks :func:`get_project_access` and then
:meth:`ProjectAccess.can_view` / :meth:`ProjectAccess.can_operate`.

Resolution for a member (admins and the owner see everything):

1. direct :class:`~twicc.core.models.ProjectGrant` rows;
2. every project listed in a workspace the member holds a
   :class:`~twicc.core.models.WorkspaceGrant` on (``workspaces.json``, read
   live, so workspace edits apply at once);
3. the worktrees of every project from 1–2 inherit its level (a worktree's
   sessions aggregate into its main repo, so hiding them would be incoherent).

When a project is reached through several paths, the strongest level wins.

Results are cached per account and dropped by :func:`invalidate_access_cache`
(grant/workspace/project mutations) or after a short TTL (the CLI writes from
another process).
"""

from __future__ import annotations

import threading
import time
from typing import NamedTuple

from asgiref.sync import sync_to_async

from twicc.accounts.principal import Principal

_CACHE_TTL_SECONDS = 10.0


class ProjectAccess(NamedTuple):
    """Resolved access of one principal. ``unrestricted`` short-circuits the sets."""

    unrestricted: bool
    viewable: frozenset[str]
    operable: frozenset[str]

    def can_view(self, project_id: str | None) -> bool:
        if self.unrestricted:
            return True
        return project_id is not None and project_id in self.viewable

    def can_operate(self, project_id: str | None) -> bool:
        if self.unrestricted:
            return True
        return project_id is not None and project_id in self.operable

    def filter_ids(self, project_ids):
        """Keep only the viewable ids of an iterable (order preserved)."""
        if self.unrestricted:
            return list(project_ids)
        return [pid for pid in project_ids if pid in self.viewable]


UNRESTRICTED = ProjectAccess(unrestricted=True, viewable=frozenset(), operable=frozenset())
NO_ACCESS = ProjectAccess(unrestricted=False, viewable=frozenset(), operable=frozenset())

_cache: dict[int, tuple[float, int, ProjectAccess]] = {}
_cache_lock = threading.Lock()
_generation = 0


def invalidate_access_cache() -> None:
    """Drop every cached resolution (call after any grant/workspace/project change)."""
    global _generation
    with _cache_lock:
        _generation += 1
        _cache.clear()


def _workspace_project_ids(workspace_ids: set[str]) -> dict[str, set[str]]:
    from twicc.workspaces import read_workspaces

    result: dict[str, set[str]] = {}
    for ws in read_workspaces().get("workspaces", []):
        if isinstance(ws, dict) and ws.get("id") in workspace_ids:
            result[ws["id"]] = {pid for pid in ws.get("projectIds", []) if isinstance(pid, str)}
    return result


def resolve_user_access_sync(user_id: int) -> ProjectAccess:
    """Compute (uncached) the access of a member account."""
    from twicc.core.models import AccessLevel, Project, ProjectGrant, WorkspaceGrant

    levels: dict[str, str] = {}

    def _merge(pid: str, level: str) -> None:
        if levels.get(pid) != AccessLevel.OPERATE:
            levels[pid] = level

    for pid, level in ProjectGrant.objects.filter(user_id=user_id).values_list("project_id", "level"):
        _merge(pid, level)

    ws_grants = dict(WorkspaceGrant.objects.filter(user_id=user_id).values_list("workspace_id", "level"))
    if ws_grants:
        for ws_id, pids in _workspace_project_ids(set(ws_grants)).items():
            for pid in pids:
                _merge(pid, ws_grants[ws_id])

    if levels:
        worktrees = Project.objects.filter(worktree_of_id__in=list(levels)).values_list("id", "worktree_of_id")
        for wt_id, main_id in worktrees:
            _merge(wt_id, levels[main_id])

    viewable = frozenset(levels)
    operable = frozenset(pid for pid, level in levels.items() if level == AccessLevel.OPERATE)
    return ProjectAccess(unrestricted=False, viewable=viewable, operable=operable)


def get_project_access_sync(principal: Principal | None) -> ProjectAccess:
    if principal is None:
        return NO_ACCESS
    if principal.is_admin or principal.user_id is None:
        return UNRESTRICTED
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(principal.user_id)
        generation = _generation
    if hit is not None and hit[1] == generation and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[2]
    access = resolve_user_access_sync(principal.user_id)
    with _cache_lock:
        if generation == _generation:
            _cache[principal.user_id] = (now, generation, access)
    return access


async def get_project_access(principal: Principal | None) -> ProjectAccess:
    if principal is None:
        return NO_ACCESS
    if principal.is_admin or principal.user_id is None:
        return UNRESTRICTED
    return await sync_to_async(get_project_access_sync)(principal)


def visible_workspace_ids(principal: Principal | None, access: ProjectAccess) -> set[str] | None:
    """Workspaces a member may see: granted ones, plus any holding a visible project.

    None means "all" (unrestricted). Only the ids — callers strip the
    invisible project ids out of each workspace themselves.
    """
    if access.unrestricted:
        return None
    from twicc.core.models import WorkspaceGrant
    from twicc.workspaces import read_workspaces

    ids: set[str] = set()
    if principal is not None and principal.user_id is not None:
        ids.update(WorkspaceGrant.objects.filter(user_id=principal.user_id).values_list("workspace_id", flat=True))
    for ws in read_workspaces().get("workspaces", []):
        if isinstance(ws, dict) and any(pid in access.viewable for pid in ws.get("projectIds", [])):
            ids.add(ws.get("id"))
    return ids


def filter_workspaces_payload(workspaces: list[dict], access: ProjectAccess, visible_ids: set[str] | None) -> list[dict]:
    """Strip a workspaces list down to what a member may see.

    Each kept workspace only lists the member's visible projects, and its
    auto-add patterns (which reveal directory layouts) are dropped.
    """
    if visible_ids is None:
        return workspaces
    result = []
    for ws in workspaces:
        if not isinstance(ws, dict) or ws.get("id") not in visible_ids:
            continue
        copy = dict(ws)
        copy["projectIds"] = [pid for pid in ws.get("projectIds", []) if access.can_view(pid)]
        copy["autoProjectPatterns"] = []
        result.append(copy)
    return result
