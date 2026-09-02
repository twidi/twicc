"""API views and SPA catch-all for serving the frontend."""

import asyncio
import hashlib
import logging
import os
import re
from bisect import bisect_left
from datetime import datetime, timedelta
from urllib.parse import unquote

from django.conf import settings
from django.db import IntegrityError
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.cache import get_conditional_response
from django.utils.http import quote_etag

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
import orjson

from twicc import search
from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import AgentLink, ArtifactBookmark, ArtifactNetworkDenial, Command, DailyActivity, ModelBenchmark, PinMode, Project, Session, SessionItem, SessionType, ToolResultLink, UsageSnapshot, WeeklyActivity, Workflow
from twicc.core.serializers import (
    serialize_artifact_bookmark,
    serialize_benchmark_row,
    serialize_network_denial,
    serialize_project,
    serialize_session,
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.core.session_queries import (
    aggregate_tool_states,
    parse_line_ranges,
    serialize_agent_links,
    tool_results_payload,
)
from twicc.core.text_filter import match_text_query
from twicc.core.services.project_mutation import clean_project_agent_defaults
from twicc.paths import path_to_project_id
from twicc.projects import register_project
from twicc.providers.db_writer import run_under_db_write_lock
from twicc.providers.sessions_watcher import mark_session_search_version_current
from twicc.providers.state import ProviderDisabledError, ensure_provider_running
from twicc.providers.helpers import get_provider_helpers, get_provider_helpers_registry
from twicc.terminal import kill_all_tmux_terminals
from twicc.workspaces import (
    add_project_to_workspaces,
    normalize_browser_url,
    normalize_browser_url_entries,
    read_workspaces,
    validate_browser_url,
)

logger = logging.getLogger(__name__)

# Number of sessions to return per page
# Set high (1000) to effectively load all sessions at once for most users,
# enabling instant client-side search/filtering without pagination complexity
SESSIONS_PAGE_SIZE = 1000

# Strong references to fire-and-forget tasks. asyncio.create_task only
# keeps a weak reference internally, so without holding the Task here the
# garbage collector can drop a still-running cleanup mid-flight. Tasks
# are added on creation and removed via ``discard`` from the
# ``add_done_callback`` so the set stays bounded.
_DETACHED_TASKS: set[asyncio.Task] = set()


async def _get_sessions_page(
    project_id: str | None,
    before_mtime: str | None,
    project_id_list: list[str] | None = None,
    pinned_only: bool = False,
    unread_only: bool = False,
    active_session_ids: list[str] | None = None,
) -> dict:
    """Get a page of sessions with pagination support.

    Args:
        project_id: Project ID to filter by, or None for all projects.
        before_mtime: Cursor for pagination - only return sessions with mtime < this value.
        project_id_list: List of project IDs to filter by (used when project_id is None).
        pinned_only: If True, include pinned sessions (any pin mode) in the union.
        unread_only: If True, include sessions with unread content (last_new_content_at
            set AND later than last_viewed_at, or last_viewed_at null) in the union.
        active_session_ids: If not None, include sessions whose id is in this list
            (typically the agent manager's active session ids) in the union.

    When more than one "sticky" flag (pinned_only / unread_only / active_session_ids)
    is set, the results are the UNION of the matching sessions — callers passing
    several flags get every session that matches at least one of them.

    Returns:
        Dict with "sessions" (list) and "has_more" (bool).
    """
    from django.db.models import F, Q

    sessions = Session.objects.filter(
        type=SessionType.SESSION,
        created_at__isnull=False,
        user_message_count__gt=0,
        hidden=False,
    )

    if project_id is not None:
        sessions = sessions.filter(project_id=project_id)
    elif project_id_list is not None:
        sessions = sessions.filter(project_id__in=project_id_list)

    sticky = Q()
    if pinned_only:
        sticky |= Q(pinned__isnull=False)
    if unread_only:
        sticky |= Q(last_new_content_at__isnull=False) & (
            Q(last_viewed_at__isnull=True) | Q(last_new_content_at__gt=F("last_viewed_at"))
        )
    if active_session_ids:
        sticky |= Q(id__in=active_session_ids)
    if pinned_only or unread_only or active_session_ids:
        sessions = sessions.filter(sticky)

    if before_mtime:
        sessions = sessions.filter(mtime__lt=float(before_mtime))

    # Fetch one extra to detect if there are more
    sessions = await sync_to_async(list)(
        sessions.order_by("-mtime")[: SESSIONS_PAGE_SIZE + 1]
    )

    has_more = len(sessions) > SESSIONS_PAGE_SIZE
    sessions = sessions[:SESSIONS_PAGE_SIZE]

    return {
        "sessions": [serialize_session(s) for s in sessions],
        "has_more": has_more,
    }


async def all_sessions(request):
    """GET /api/sessions/ - All sessions from all projects (paginated).

    Returns only regular sessions (not subagents).

    Query params (optional):
        before_mtime: Cursor for pagination - only return sessions older than this mtime.
        project_ids: Comma-separated list of project IDs to filter by.
        pinned: When "1"/"true", include pinned sessions (any pin mode) in the result.
        unread: When "1"/"true", include sessions with unread content in the result.
        has_process: When "1"/"true", include sessions that have an active Claude SDK
            process. Combined with pinned/unread via UNION when multiple flags are set.

    The pinned / unread / has_process flags are "sticky" filters used at app startup
    to preload cross-filter sessions that would otherwise be missing from the store
    (a single-project sidebar only loads its own project's sessions). When no such
    flag is set, the endpoint returns a regular paginated list.
    """
    before_mtime = request.GET.get("before_mtime")
    project_ids_param = request.GET.get("project_ids")
    project_id_list = project_ids_param.split(",") if project_ids_param else None
    pinned_only = request.GET.get("pinned", "").lower() in ("1", "true")
    unread_only = request.GET.get("unread", "").lower() in ("1", "true")
    has_process = request.GET.get("has_process", "").lower() in ("1", "true")

    active_session_ids: list[str] | None = None
    if has_process:
        from twicc.agent.registry import get_agent_manager_registry
        active_session_ids = [info.session_id for info in get_agent_manager_registry().get_active_agents()]

    return JsonResponse(await _get_sessions_page(
        None,
        before_mtime,
        project_id_list=project_id_list,
        pinned_only=pinned_only,
        unread_only=unread_only,
        active_session_ids=active_session_ids,
    ))


async def session_by_id(request, session_id):
    """GET /api/sessions/<session_id>/ - Fetch a single regular session by ID.

    Resolves a session when the caller does not know (or cannot trust) its
    project_id. Used by the frontend when rendering a cross-filter deep link
    (e.g. /project/A/session/sessionX where sessionX belongs to project B):
    the session view needs the session object to derive its real project,
    but the URL's project_id is the sidebar filter and may not match.

    Returns 404 if the session does not exist or is a subagent (subagents
    must be accessed via their parent's subagent route).
    """
    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    if session.parent_session_id is not None:
        raise Http404("Session not found")

    if session.hidden:
        raise Http404("Session not found")

    return JsonResponse(serialize_session(session))


async def session_plan_content(request, session_id):
    """GET /api/sessions/<session_id>/plan/ — the session's plan markdown.

    The plan file lives outside the project / artifacts roots that the generic
    ``file-content`` endpoint confines reads to (Claude Code stores it under
    ``<claude home>/plans/<slug>.md``), so the path is resolved server-side from the
    session's provider — the client passes only a session id, never a filesystem
    path. The provider helper returns ``None`` for providers with no plan concept
    (or a session with no slug), and a missing file 404s, so the frontend only
    fetches this when ``has_plan`` is true.
    """
    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    plan_path = get_provider_helpers(session.provider).resolve_plan_path(session)
    if plan_path is None:
        raise Http404("No plan for this session")

    try:
        content = await asyncio.to_thread(plan_path.read_text, encoding="utf-8")
    except OSError:
        raise Http404("Plan file not found")

    return JsonResponse({"content": content})


async def project_list(request):
    """GET /api/projects/ - List all projects.
    POST /api/projects/ - Create a new project from a directory path.
    """
    if request.method == "POST":
        return await _create_project(request)

    projects = await sync_to_async(list)(Project.objects.all())
    data = [serialize_project(p) for p in projects]
    return JsonResponse(data, safe=False)


async def _create_project(request):
    """Create a new project from a directory path.

    Body: {
        "directory": "/absolute/path",
        "name": "optional",
        "color": "optional",
        "workspace_ids": ["optional", "list", "of", "workspace", "ids"]
    }
    """
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # 1. Extract and validate directory
    directory = data.get("directory")
    if not directory or not isinstance(directory, str):
        return JsonResponse({"error": "directory is required"}, status=400)

    resolved = os.path.realpath(directory)
    if not os.path.isabs(resolved):
        return JsonResponse({"error": "Directory must be an absolute path"}, status=400)
    if not os.path.isdir(resolved):
        # If path exists but is not a directory (e.g. a file), reject outright
        if os.path.exists(resolved):
            return JsonResponse({"error": "Path exists but is not a directory"}, status=400)
        # Directory doesn't exist: create it if requested, otherwise ask the user
        if data.get("create_directory"):
            try:
                await asyncio.to_thread(os.makedirs, resolved, exist_ok=True)
            except OSError as e:
                return JsonResponse({"error": f"Failed to create directory: {e}"}, status=400)
        else:
            return JsonResponse(
                {"error": "Directory does not exist", "code": "directory_not_found"},
                status=400,
            )

    # 2. Generate project ID: all non-alphanumeric chars become dashes
    project_id = path_to_project_id(resolved)

    # 3. Check project doesn't already exist
    if await Project.objects.filter(id=project_id).aexists():
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    # 4. Validate optional name
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            name = None
        elif len(name) > 25:
            return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
        elif await Project.objects.filter(name=name).aexists():
            return JsonResponse({"error": "A project with this name already exists"}, status=400)

    # 5. Validate optional color
    color = data.get("color")
    if color is not None and not isinstance(color, str):
        color = None

    # 6. Create project — single entry point handles ``project_added``
    # broadcast and workspace auto-add. IntegrityError can still fire on a
    # ``name`` collision (the ``id`` collision path goes through the early
    # exists-check at step 3 and the get_or_create race window below).
    # ``register_project`` itself does the DB write, plus the channel-layer
    # broadcast and the workspace auto-add (which can also broadcast). The
    # whole call runs under the DB write lock; the broadcasts inside cost
    # nothing (in-process Channels) and keeping them under the lock
    # preserves the single-acquire shape of the existing helper.
    try:
        project, created = await run_under_db_write_lock(
            lambda: register_project(
                project_id,
                directory=resolved,
                name=name,
                color=color or None,
            )
        )
    except IntegrityError:
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)
    if not created:
        # Lost a race with another creator between the early exists-check
        # and now — same friendly 409 as the early-out.
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    # 7. Fold in the workspaces the user explicitly picked in the project
    # dialog. ``register_project`` already ran the pattern-based auto-add
    # (and broadcast it); this appends the manual picks under the same
    # ``_workspaces_lock`` (idempotent), so the two never clobber each other
    # — which a frontend whole-blob write right after creation could.
    workspace_ids = data.get("workspace_ids")
    if isinstance(workspace_ids, list):
        ids = [w for w in workspace_ids if isinstance(w, str) and w]
        if ids:
            await add_project_to_workspaces(project.id, ids)

    return JsonResponse(serialize_project(project), status=201)


async def project_branches(request, project_id):
    """GET /api/projects/<id>/branches/ - Local branches of the project's repo.

    Returns ``{"branches": [{"name": str, "checked_out": bool}, ...]}`` where
    ``checked_out`` flags branches already checked out in a worktree (including
    the main checkout). Order: current branch first, then alphabetical.
    """
    from twicc.git import get_branches, get_worktree_branches

    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)
    # Resolve the repo root live (git_root, or a fallback from the directory for
    # a never-synced / freshly git-init'd repo) — same fallback as the worktrees
    # GET/POST endpoints, so a stale ``git_root=None`` never wrongly 400s here.
    repo_root = await _resolve_repo_root(project)
    if not repo_root:
        return JsonResponse({"error": "Project is not a git repository"}, status=400)

    branches = await sync_to_async(get_branches)(repo_root)
    checked_out = await sync_to_async(get_worktree_branches)(repo_root)
    return JsonResponse(
        {"branches": [{"name": b, "checked_out": b in checked_out} for b in branches]}
    )


async def project_resolve_git(request, project_id):
    """POST /api/projects/<id>/resolve-git/ - Re-resolve the project's git_root live.

    The action-time verifier behind the worktree-creation affordances. The UI
    renders the "new session in a worktree" entry points (per-row button in the
    "New session" dropdown, the Command Palette command) from the cached
    ``git_root`` — it never blocks on it and never re-checks per render. When
    the user actually acts on a project whose ``git_root`` is ``None`` (a repo
    ``git init``-ed after the project was created), this re-resolves from the
    directory, persists, and broadcasts ``project_updated`` on change, so the
    affordance heals without a backend restart. Also heals the reverse (a
    project whose ``.git`` was removed → ``git_root`` back to ``None``).

    Returns ``{"git_root": str | null}`` (the freshly resolved value).
    """
    from twicc.projects import reresolve_project_git_root

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    found, git_root = await reresolve_project_git_root(project_id)
    if not found:
        return JsonResponse({"error": "Project not found"}, status=404)
    return JsonResponse({"git_root": git_root})


async def project_refresh_directory(request, project_id):
    """POST /api/projects/<id>/refresh-directory/ - Re-check the project directory live.

    ``Project.stale`` ("the working directory was gone last time we looked") is
    a stored observation: the UI renders it without ever re-checking per render.
    Nothing watches the working directories themselves, so a directory restored
    while TwiCC runs stays flagged until a restart. This is what the project
    dialog's "Re-check" button calls — it re-stats the directory, re-resolves
    ``git_root`` when the directory is back, persists, and broadcasts
    ``project_updated`` on change.

    Returns the refreshed serialised project.
    """
    from twicc.projects import refresh_project_directory_state

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    found, project = await refresh_project_directory_state(project_id)
    if not found:
        return JsonResponse({"error": "Project not found"}, status=404)
    return JsonResponse(serialize_project(project))


# Worktree-creation error code -> HTTP status. The service is
# transport-agnostic (it returns structured codes); the endpoint maps
# them here. Anything unmapped is a 400 (client-side input fault).
_WORKTREE_ERROR_STATUS: dict[str, int] = {
    "project_not_found": 404,
    "not_git_repo": 400,
    "invalid_path": 400,
    "branch_required": 400,
    "project_already_exists": 409,
    "start_from_not_found": 400,
    "not_a_worktree": 400,
    "git_error": 400,
}


def _serialize_worktrees(repo_root: str) -> list[dict]:
    """Build the enriched worktree list for ``GET .../worktrees/``.

    Lists every linked worktree of ``repo_root`` (the main checkout itself
    excluded), each annotated with: ``relative_path`` (relative to the main
    repo when underneath it, else absolute), the checked-out ``branch`` (or
    ``detached``), ``locked`` / ``prunable`` flags + reasons, a ``usable``
    flag (false when the directory is gone, prunable, or bare — those rows are
    shown disabled in the UI), and the TwiCC project link if one already
    exists (``project_id`` / ``sessions_count`` / ``archived``). Sorted
    usable-first, then by branch, then by path. Sync (subprocess + filesystem
    + DB reads) — call via ``sync_to_async``.
    """
    from twicc.git import list_worktrees, resolve_worktree_main_repo

    main_root = resolve_worktree_main_repo(repo_root) or repo_root
    main_root_real = os.path.realpath(main_root)

    out: list[dict] = []
    for wt in list_worktrees(repo_root):
        wt_real = os.path.realpath(wt.path)
        if wt_real == main_root_real:
            continue  # the main checkout is not a worktree-target
        if wt_real.startswith(main_root_real + os.sep):
            relative = os.path.relpath(wt_real, main_root_real)
        else:
            relative = wt.path
        exists = os.path.isdir(wt_real)
        proj = (
            Project.objects.filter(id=path_to_project_id(wt_real))
            .only("id", "sessions_count", "archived")
            .first()
        )
        out.append({
            "path": wt.path,
            "relative_path": relative,
            "branch": wt.branch,
            "detached": wt.is_detached,
            "locked": wt.is_locked,
            "locked_reason": wt.locked_reason,
            "prunable": wt.is_prunable,
            "prunable_reason": wt.prunable_reason,
            "usable": exists and not wt.is_prunable and not wt.is_bare,
            "project_id": proj.id if proj else None,
            "sessions_count": proj.sessions_count if proj else 0,
            "archived": bool(proj.archived) if proj else False,
        })

    out.sort(key=lambda w: (not w["usable"], (w["branch"] or "￿").lower(), w["path"]))
    return out


async def _resolve_repo_root(project) -> str | None:
    """Repo root of ``project``: its computed ``git_root``, or a live
    resolution from its directory for a never-synced repo. ``None`` when the
    directory backs no git repository."""
    repo_root = project.git_root
    if not repo_root and project.directory:
        from twicc.git import resolve_git_from_path
        git = await sync_to_async(resolve_git_from_path)(project.directory, use_cache=False)
        repo_root = git[0] if git else None
    return repo_root


async def project_worktrees(request, project_id):
    """GET  /api/projects/<id>/worktrees/ - List the git worktrees of the repo.
    POST /api/projects/<id>/worktrees/ - Create a git worktree of the project.

    GET returns ``{"worktrees": [...]}`` (see :func:`_serialize_worktrees`) so
    the UI can offer existing worktrees that have no session yet.

    POST body: {
        "path": "/absolute/path/of/the/new/worktree",
        "branch": "branch name (existing => checkout, new => created with -b)",
        "start_from": "optional existing branch the new branch starts from"
    }
    delegates to
    :func:`twicc.core.services.worktree_creation.create_worktree_from_source`
    — the same orchestration the CLI ``create-session --worktree-branch``
    flow uses — and serialises the new worktree project (201).
    """
    if request.method == "GET":
        try:
            project = await Project.objects.aget(id=project_id)
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found"}, status=404)
        repo_root = await _resolve_repo_root(project)
        if not repo_root:
            return JsonResponse({"error": "Project is not a git repository"}, status=400)
        worktrees = await sync_to_async(_serialize_worktrees)(repo_root)
        return JsonResponse({"worktrees": worktrees})

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    from twicc.core.services.worktree_creation import create_worktree_from_source

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    result = await create_worktree_from_source(
        source_project_id=project_id,
        path=data.get("path") or "",
        branch=data.get("branch") or "",
        start_from=data.get("start_from") or None,
    )
    if not result.success:
        err = result.errors[0]
        return JsonResponse({"error": err.message},
                            status=_WORKTREE_ERROR_STATUS.get(err.code, 400))

    return JsonResponse(serialize_project(result.project), status=201)


async def project_worktree_adopt(request, project_id):
    """POST /api/projects/<id>/worktrees/adopt/ - Adopt an existing worktree.

    Body: ``{"path": "/absolute/path/of/an/existing/worktree"}``.

    Registers a worktree that already exists on disk (but has no TwiCC project
    yet) as a Project linked to <id> via ``worktree_of`` — no ``git worktree
    add``. Delegates to
    :func:`twicc.core.services.worktree_creation.adopt_existing_worktree` and
    serialises the project (201). Idempotent: an already-registered worktree
    is returned as-is.
    """
    from twicc.core.services.worktree_creation import adopt_existing_worktree

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    result = await adopt_existing_worktree(
        source_project_id=project_id,
        path=data.get("path") or "",
    )
    if not result.success:
        err = result.errors[0]
        return JsonResponse({"error": err.message},
                            status=_WORKTREE_ERROR_STATUS.get(err.code, 400))

    return JsonResponse(serialize_project(result.project), status=201)


async def project_detail(request, project_id):
    """GET/PUT/PATCH /api/projects/<id>/ - Detail of a project, update name/color/agent defaults, or archive."""
    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Update allowed fields only
        if "name" in data:
            name = data["name"]
            if name is not None:
                name = name.strip()
                if not name:
                    # Empty after strip means no name
                    name = None
                elif len(name) > 25:
                    return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
                elif await Project.objects.filter(name=name).exclude(id=project_id).aexists():
                    return JsonResponse({"error": "A project with this name already exists"}, status=400)
            project.name = name
        if "color" in data:
            project.color = data["color"]
        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            project.archived = archived

        update_fields = ["name", "color", "archived"]
        if "worktree_directory" in data:
            worktree_directory = data["worktree_directory"]
            if worktree_directory is not None:
                if not isinstance(worktree_directory, str):
                    return JsonResponse({"error": "worktree_directory must be a string or null"}, status=400)
                worktree_directory = worktree_directory.strip() or None
            project.worktree_directory = worktree_directory
            update_fields.append("worktree_directory")
        if "default_provider" in data:
            clean_provider, _unused, err = clean_project_agent_defaults(data["default_provider"], None)
            if err is not None:
                return JsonResponse({"error": err}, status=400)
            project.default_provider = clean_provider
            update_fields.append("default_provider")
        if "default_agent_settings" in data:
            _unused, clean_settings, err = clean_project_agent_defaults(None, data["default_agent_settings"])
            if err is not None:
                return JsonResponse({"error": err}, status=400)
            project.default_agent_settings = clean_settings
            update_fields.append("default_agent_settings")
        if "default_layout_id" in data:
            # A named-layout id or "single-pane"; empty/None = inherit. Dangling ids are tolerated
            # (resolution falls back to inherit → global → single pane), so no existence check here.
            layout_id = data["default_layout_id"]
            if layout_id is not None and not isinstance(layout_id, str):
                return JsonResponse({"error": "default_layout_id must be a string or null"}, status=400)
            project.default_layout_id = layout_id or None
            update_fields.append("default_layout_id")
        if "browser_urls" in data:
            # Full-list replacement of the saved Browser-pane URLs. http(s)
            # only — the pane must never be pointed at javascript:/file:/data:
            # targets. [] or null = nothing saved (inherit).
            entries, entry_errors = normalize_browser_url_entries(
                data["browser_urls"] if data["browser_urls"] is not None else [],
                field="browser_urls",
            )
            if entry_errors:
                return JsonResponse({"error": entry_errors[0].message}, status=400)
            project.browser_urls = entries
            update_fields.append("browser_urls")
        await run_under_db_write_lock(
            lambda: project.asave(update_fields=update_fields)
        )

        # Broadcast project_updated via WebSocket (out of the DB lock).
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "project_updated",
                    "project": serialize_project(project),
                },
            },
        )

    elif request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            project.archived = archived
            await run_under_db_write_lock(
                lambda: project.asave(update_fields=["archived"])
            )

            # Broadcast project_updated via WebSocket
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )

    return JsonResponse(serialize_project(project))


_ICON_UPLOAD_MAX_BYTES = 5 * 1024 * 1024


def _decode_image_data_uri(spec) -> tuple[str, bytes] | None:
    """Return ``(mime, raw_bytes)`` from a ``data:`` image URI, or ``None``."""
    import base64
    import binascii

    if not isinstance(spec, str) or not spec.startswith("data:"):
        return None
    rest = spec[len("data:"):]
    header, sep, b64 = rest.partition(",")
    if not sep:
        return None
    params = header.split(";")
    mime = params[0].strip() or "application/octet-stream"
    if not any(p.strip().lower() == "base64" for p in params[1:]):
        return None
    try:
        raw = base64.b64decode("".join(b64.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    return (mime, raw) if raw else None


async def project_icon_manage(request, project_id):
    """POST /api/projects/<id>/icon/ — set/clear THIS project's icon, or scan.

    An icon is a per-project value that cascades to descendants by inheritance
    (resolved client-side), so there is no repo-level or "apply to all" action —
    setting a project's icon is enough. JSON body, action-dispatched:

    - ``{"action":"set","image":"<data-uri>"}`` — this project's own icon
      (cascades to inheriting descendants);
    - ``{"action":"none"}`` / ``{"action":"inherit"}`` — this project shows the
      color dot / follows the inherited icon again;
    - ``{"action":"scan"}`` — read-only: return the icon candidates found in the
      repo (each a normalized data-URI preview) for the user to pick from.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from twicc import project_icons as pi

    action = data.get("action")

    if action == "scan":
        anchor = project.icon_anchor or project.git_root
        if not anchor:
            return JsonResponse({"error": "Project is not in a git repository"}, status=400)
        candidates = await sync_to_async(pi.scan_repo_icons)(anchor)
        return JsonResponse({"candidates": candidates})

    if action == "set":
        decoded = _decode_image_data_uri(data.get("image"))
        if decoded is None:
            return JsonResponse({"error": "Invalid or missing image"}, status=400)
        mime, raw = decoded
        if len(raw) > _ICON_UPLOAD_MAX_BYTES:
            return JsonResponse({"error": "Image too large (max 5 MB)"}, status=400)
        ext = ".svg" if mime == "image/svg+xml" else ".png"
        token = await run_under_db_write_lock(
            lambda: sync_to_async(pi.set_project_icon_override_sync)(project_id, raw, ext)
        )
        if token is None:
            return JsonResponse({"error": "Unsupported or corrupt image"}, status=400)
    elif action in ("none", "inherit"):
        await run_under_db_write_lock(
            lambda: sync_to_async(pi.set_project_icon_state_sync)(project_id, action)
        )
    else:
        return JsonResponse({"error": "Unknown action"}, status=400)

    # Broadcast only the edited project; inheriting descendants re-resolve
    # reactively client-side from this update.
    from twicc.projects import _broadcast_project_updated

    await _broadcast_project_updated(project_id)

    project = await Project.objects.aget(id=project_id)
    return JsonResponse(serialize_project(project))


async def project_trust_resolve(request, project_id):
    """POST /api/projects/<id>/trust/resolve/ — resolve the project's effective trust.

    Resolves from the DB; if unresolved, seeds once from the provider configs
    (guarded by ``trust_imported``). Returns ``{state, via, source_id}`` where
    ``state`` is true / false / null (null → the caller should prompt the user).
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")
    from twicc.core.services.trust import resolve_project_trust

    try:
        return JsonResponse(await resolve_project_trust(project_id))
    except Exception:
        # Never 500 the trust gate: an unexpected failure (filesystem, git
        # subprocess, …) degrades to "unresolved" so the frontend keeps a
        # coherent state instead of guessing what the backend did.
        logger.exception("Trust resolve failed for project %s", project_id)
        return JsonResponse({"state": None, "via": "error", "source_id": None})


async def project_trust_decide(request, project_id):
    """POST /api/projects/<id>/trust/decide/ — record a trust decision.

    Body: ``{trusted: bool|null, propagation?: bool}``. ``trusted`` true/false is
    an explicit decision; ``null`` resets the project to inheritance. Persists the
    decision and projects it onto both providers' configs (``propagation``
    defaults to "the project is under git").
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if "trusted" not in data:
        return JsonResponse({"error": "'trusted' is required (true, false or null)"}, status=400)
    trusted = data.get("trusted")
    if not (trusted is None or isinstance(trusted, bool)):
        return JsonResponse({"error": "'trusted' must be true, false or null"}, status=400)
    propagation = data.get("propagation")
    if propagation is not None and not isinstance(propagation, bool):
        return JsonResponse({"error": "'propagation' must be a boolean"}, status=400)
    from twicc.core.services.trust import decide_project_trust

    try:
        result = await decide_project_trust(project_id, trusted, propagation)
    except Exception:
        logger.exception("Trust decide failed for project %s", project_id)
        return JsonResponse({"error": "decision_failed"}, status=500)
    if not result.get("ok"):
        return JsonResponse({"error": result.get("error", "decision_failed")}, status=400)
    return JsonResponse(result)


async def commands(request, project_id):
    """GET /api/projects/<id>/commands/?provider=<key>&activation_char=<char> — commands for a project.

    Returns global commands (``project=NULL``) and project-specific
    commands, sorted by name. Both ``provider`` and ``activation_char``
    query parameters are required: command sets are not interchangeable
    across backends (each provider's CLI has its own command vocabulary),
    and a single provider can expose multiple activation prefixes (e.g.
    Codex uses both ``/`` and ``$``), so there is no implicit default
    for either.
    """
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")

    provider_str = request.GET.get("provider")
    if not provider_str:
        return JsonResponse({"error": "provider is required."}, status=400)
    try:
        provider = Provider(provider_str)
    except ValueError:
        return JsonResponse({"error": f"Unknown provider {provider_str!r}."}, status=400)

    activation_char = request.GET.get("activation_char")
    if not activation_char:
        return JsonResponse({"error": "activation_char is required."}, status=400)
    if len(activation_char) != 1:
        return JsonResponse(
            {"error": "activation_char must be a single character."},
            status=400,
        )

    from django.db.models import Q

    from twicc.core.services.trust import project_is_untrusted

    qs = (
        Command.objects
        .filter(provider=provider.value, activation_char=activation_char)
        .order_by("name")
        .values("name", "plugin_name", "description", "argument_hint", "is_builtin", "is_workflow", "project_id")
    )
    # Untrusted (or unknown-trust) projects only get the global (user/managed)
    # commands — project-scoped ones are repo-controlled (trust design §13.4).
    if await sync_to_async(project_is_untrusted)(project_id):
        qs = qs.filter(project__isnull=True)
    else:
        qs = qs.filter(Q(project__isnull=True) | Q(project_id=project_id))
    cmds = await sync_to_async(list)(qs)

    return JsonResponse({
        "commands": [
            {
                "name": cmd["name"],
                "plugin_name": cmd["plugin_name"],
                "description": cmd["description"],
                "argument_hint": cmd["argument_hint"],
                "is_builtin": cmd["is_builtin"],
                "is_workflow": cmd["is_workflow"],
                "is_global": cmd["project_id"] is None,
            }
            for cmd in cmds
        ]
    })


async def user_messages(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/user-messages/ - User messages of a session.

    Returns all user messages for the given session, in chronological order (oldest first).
    Each entry includes line_num, timestamp, and the extracted text content.

    Messages another session sent to this one are left out: this feeds the
    composer's history picker, whose only purpose is to reuse something the
    human typed. An orchestrator session receives many reports from its
    children, and they all land as user messages -- they would bury the few
    real prompts. They stay in the full-text search, which indexes everything
    on purpose. The filter is applied here rather than in
    ``get_user_messages`` because that helper also serves the title suggestion
    and the workflow prompts, where the first message legitimately comes from
    another session.
    """
    from twicc.cli._drop_request.sender_header import has_sender_header

    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    items = await sync_to_async(list)(
        SessionItem.objects
        .filter(session=session, kind=ItemKind.USER_MESSAGE)
        .order_by("line_num")
    )
    messages = [
        {
            "line_num": msg.line_num,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "text": msg.text,
        }
        for msg in get_provider_helpers(session.provider).get_user_messages(items)
        if not has_sender_header(msg.text)
    ]

    return JsonResponse({"messages": messages})


async def project_sessions(request, project_id):
    """GET /api/projects/<id>/sessions/ - Sessions of a project AND its git
    worktrees (paginated).

    A git worktree's sessions belong to its main repository's whole (like a
    workspace aggregates its members, one level down), so filtering on a project
    always returns the project's own sessions PLUS those of every project whose
    ``worktree_of`` points at it -- in a single response, no second call. The
    full set of covered project ids is returned as ``scope_project_ids`` so the
    client can mark them all fetched (and thus accept their live
    ``session_updated`` pushes) without having to re-derive the worktree set
    itself -- which it cannot do reliably before the projects list has loaded.

    Returns only regular sessions (not subagents).
    Subagents are accessed via their parent session.

    Query params (optional):
        before_mtime: Cursor for pagination - only return sessions older than this mtime.
    """
    if not await Project.objects.filter(id=project_id).aexists():
        raise Http404("Project not found")

    # Scope = the project itself + every git worktree of it. Archived-blind, to
    # match the client's worktree scope (`getProjectScopeIds`): the session-level
    # archive filter hides archived sessions when "show archived" is off, so the
    # worktree set never needs an archived-aware refetch.
    worktree_ids = await sync_to_async(list)(
        Project.objects.filter(worktree_of_id=project_id).values_list("id", flat=True)
    )
    scope_ids = [project_id, *worktree_ids]

    before_mtime = request.GET.get("before_mtime")
    result = await _get_sessions_page(None, before_mtime, project_id_list=scope_ids)
    result["scope_project_ids"] = scope_ids
    return JsonResponse(result)


async def _resolve_session_or_404(session_id, project_id, parent_session_id):
    """Fetch the session for a session/subagent route, or raise Http404.

    Session route (``parent_session_id`` is None): the session must belong
    to the named project and must not be a subagent -- subagents are only
    reachable through the subagent route.

    Subagent route (``parent_session_id`` is set): the URL is
    ``/projects/<project_id>/sessions/<parent_session_id>/subagent/<session_id>/``.
    Its ``/projects/<project_id>/sessions/<parent_session_id>/`` prefix must
    name a real top-level session of that project -- the same constraint the
    session route enforces -- so ``project_id`` stays meaningful and is not a
    free parameter. The subagent itself is then resolved by its
    globally-unique ``session_id`` and checked to be a child of that parent;
    it is deliberately NOT scoped by ``project_id``, because a Codex subagent
    can run in a different project than its parent.
    """
    if parent_session_id is None:
        try:
            session = await Session.objects.aget(id=session_id, project_id=project_id)
        except Session.DoesNotExist:
            raise Http404("Session not found")
        if session.parent_session_id is not None:
            raise Http404("Session not found")
        if session.hidden:
            raise Http404("Session not found")
        return session

    # Subagent route: the parent must be a top-level session of project_id.
    if not await Session.objects.filter(
        id=parent_session_id,
        project_id=project_id,
        parent_session_id__isnull=True,
    ).aexists():
        raise Http404("Session not found")
    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Subagent not found for this parent session")
    if session.parent_session_id != parent_session_id:
        raise Http404("Subagent not found for this parent session")
    return session


async def session_detail(request, project_id, session_id, parent_session_id=None):
    """GET/PATCH /api/projects/<id>/sessions/<session_id>/ - Detail or rename session.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/

    When parent_session_id is provided, validates that session.parent_session_id matches.
    When accessing a subagent via the session endpoint (no parent_session_id in URL), returns 404.

    PATCH: Rename a session (not available for subagents).
        Body: {"title": "New title"}
        - Title is trimmed and must be non-empty
        - Max 200 characters
        - Writes custom-title entry to JSONL file (deferred if process is busy)
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    if request.method == "PATCH":
        # Reject subagents (cannot be modified)
        if session.type == SessionType.SUBAGENT:
            return JsonResponse({"error": "Subagents cannot be modified"}, status=400)

        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Handle title update
        if "title" in data:
            try:
                ensure_provider_running(session.provider)
            except ProviderDisabledError as e:
                return JsonResponse(
                    {"error": "provider_disabled", "provider": e.provider.value, "message": str(e)},
                    status=409,
                )
            provider_helpers = get_provider_helpers(session.provider)
            validation = provider_helpers.validate_title(data["title"])
            if validation.error:
                return JsonResponse({"error": validation.error}, status=400)
            title = validation.title

            # 1. Update DB immediately, under the shared DB write lock.
            session.title = title
            await run_under_db_write_lock(
                lambda: session.asave(update_fields=["title"])
            )

            # 2. Re-index for full-text search (title is a searchable document)
            if search.is_initialized():
                try:
                    await asyncio.to_thread(search.reindex_session, session_id)
                except Exception:
                    pass  # Non-critical: search will catch up on next startup

            # 3. Persist into the provider's session storage (also wires
            #    up any provider-specific anti-stale-write protection).
            try:
                await provider_helpers.rename_session(session_id, title)
            except Exception:
                pass  # Non-critical: DB is already updated, watcher will sync

        # Handle archived update
        needs_broadcast = False
        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            # Share the archive flow with ``twicc update-session archive``:
            # DB write + search reindex + agent/tmux teardown live in
            # ``apply_session_archived_change``. ``also_unpin=False`` here
            # because the auto-unpin decision is made by the frontend and
            # arrives as a separate ``pinned`` key in the same PATCH body
            # (handled by the dedicated ``pinned`` branch below). The
            # combined broadcast happens at the end of the handler.
            from twicc.core.services.session_update import apply_session_archived_change
            await apply_session_archived_change(session, archived, also_unpin=False)
            needs_broadcast = True

        # Handle pinned update: NULL (unpinned) or one of PinMode.values.
        if "pinned" in data:
            pinned = data["pinned"]
            if pinned is not None and pinned not in PinMode.values:
                return JsonResponse(
                    {"error": f"pinned must be null or one of {list(PinMode.values)}"},
                    status=400,
                )
            # Share the pin/unpin flow with ``twicc update-session
            # pin / unpin``: the helper owns the DB write so both surfaces
            # stay aligned. The combined broadcast happens at the end of
            # the handler.
            from twicc.core.services.session_update import apply_session_pinned_change
            await apply_session_pinned_change(session, pinned)
            needs_broadcast = True

        if "mute_on_user_turn" in data:
            mute_on_user_turn = data["mute_on_user_turn"]
            if not isinstance(mute_on_user_turn, bool):
                return JsonResponse(
                    {"error": "mute_on_user_turn must be a boolean"},
                    status=400,
                )
            from twicc.core.services.session_update import (
                apply_session_mute_on_user_turn_change,
            )
            await apply_session_mute_on_user_turn_change(session, mute_on_user_turn)
            needs_broadcast = True

        # Handle layout update: the per-session dockable-layout intention
        # (an object; {} = single pane). Persisted from the frontend via a
        # debounced PATCH as the user docks / resizes / loads layouts. Shares
        # the combined broadcast below.
        if "layout" in data:
            layout = data["layout"]
            if not isinstance(layout, dict):
                return JsonResponse({"error": "layout must be an object"}, status=400)
            from twicc.core.services.session_update import apply_session_layout_change
            await apply_session_layout_change(session, layout)
            needs_broadcast = True

        # Handle Browser-pane URL update: the last URL the session's Browser
        # tab was pointed at (UI state, restored on the tab's first activation
        # after a page reload). Persisted from the frontend via a debounced
        # PATCH on each toolbar navigation; null clears. Shares the combined
        # broadcast below.
        if "browser_url" in data:
            browser_url = data["browser_url"]
            if browser_url is not None and not isinstance(browser_url, str):
                return JsonResponse({"error": "browser_url must be a string or null"}, status=400)
            browser_url = normalize_browser_url(browser_url)
            url_errors = validate_browser_url(browser_url, field="browser_url")
            if url_errors:
                return JsonResponse({"error": url_errors[0].message}, status=400)
            from twicc.core.services.session_update import apply_session_browser_url_change
            await apply_session_browser_url_change(session, browser_url)
            needs_broadcast = True

        # Handle goal dismissal: hide the footer goal bar for the session's
        # latest goal. The value is the target goal's ``created_at`` (guards
        # against a newer goal having taken the last slot); only a closed
        # (completed or cleared) goal can be dismissed. One-way — there is no
        # un-dismiss. Shares the combined broadcast below.
        if "dismiss_goal" in data:
            created_at = data["dismiss_goal"]
            if not isinstance(created_at, str) or not created_at:
                return JsonResponse({"error": "dismiss_goal must be the goal's created_at"}, status=400)
            from twicc.core.services.session_update import apply_session_goal_dismissed_change
            error = await apply_session_goal_dismissed_change(session, created_at)
            if error:
                return JsonResponse({"error": error}, status=409)
            needs_broadcast = True

        # Handle plan-doc existence refresh: re-probe each plan_paths entry's
        # on-disk ``exists`` flag, live. Sent by the Plan tab on activation to
        # clear a stale ``missing`` flag (existence is otherwise only re-probed
        # by the rare full recompute). Only broadcasts when a flag flipped.
        if data.get("refresh_plan_existence"):
            from twicc.core.services.session_update import refresh_session_plan_existence
            if await refresh_session_plan_existence(session):
                needs_broadcast = True

        # Broadcast session_updated for archived/pinned changes.
        # Title changes don't need this: writing to JSONL triggers the
        # file watcher which broadcasts session_updated automatically.
        # Hidden sessions must not surface to the frontend via broadcast either.
        if needs_broadcast and not session.hidden:
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "session_updated",
                        "session": serialize_session(session),
                    },
                },
            )

    return JsonResponse(serialize_session(session))


async def bulk_archive_sessions(request):
    """POST /api/sessions/bulk-archive/ - Archive multiple sessions in one shot.

    Body:
        older_than (str, required): ISO timestamp. Sessions with mtime < this are eligible.
        scope (str, required): 'project' | 'workspace' | 'all'.
        project_id (str): required if scope == 'project'.
        workspace_id (str): required if scope == 'workspace'.
        title_query (str, optional): if non-empty, restrict to sessions whose
            ``title`` (falling back to ``id``) matches the query as a
            case-insensitive subsequence — same semantics as the sidebar
            filter input.
        include_archived_projects (bool, optional, default False): for
            workspace/all scopes, control whether sessions belonging to
            archived projects are eligible. Ignored for scope='project'
            (single-project scope is explicit about its target).
        dry_run (bool, optional, default False): if True, return only the count.

    Excludes: subagents, already-archived, pinned, sessions with an active agent,
    and sessions without user messages or without created_at (not visible in sidebar).

    Returns:
        {"count": N, "has_archived_in_scope": bool}. ``has_archived_in_scope``
        is True iff the (workspace/all) scope currently has at least one
        eligible session in an archived project — the frontend uses this to
        decide whether to surface the "Include archived projects" switch.
        Session IDs are not in the response — the frontend receives them via
        the ``sessions_bulk_archived`` WS broadcast.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    older_than_iso = data.get("older_than")
    scope_type = data.get("scope")
    project_id = data.get("project_id")
    workspace_id = data.get("workspace_id")
    title_query = (data.get("title_query") or "").strip()
    include_archived_projects = bool(data.get("include_archived_projects", False))
    dry_run = bool(data.get("dry_run", False))

    if not older_than_iso:
        return JsonResponse({"error": "older_than is required"}, status=400)
    if scope_type not in ("project", "workspace", "all"):
        return JsonResponse({"error": "Invalid scope"}, status=400)
    if scope_type == "project" and not project_id:
        return JsonResponse({"error": "project_id required for scope=project"}, status=400)
    if scope_type == "workspace" and not workspace_id:
        return JsonResponse({"error": "workspace_id required for scope=workspace"}, status=400)

    try:
        older_than_epoch = datetime.fromisoformat(
            older_than_iso
        ).timestamp()
    except (ValueError, AttributeError, TypeError):
        return JsonResponse({"error": "Invalid older_than format"}, status=400)

    active_ids = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }

    qs = Session.objects.filter(
        type=SessionType.SESSION,
        user_message_count__gt=0,
        created_at__isnull=False,
        archived=False,
        pinned__isnull=True,
        hidden=False,
        mtime__lt=older_than_epoch,
    ).exclude(id__in=active_ids)

    if scope_type == "project":
        qs = qs.filter(project_id=project_id)
    elif scope_type == "workspace":
        ws_data = await asyncio.to_thread(read_workspaces)
        ws = next(
            (w for w in ws_data.get("workspaces", []) if w["id"] == workspace_id),
            None,
        )
        if ws is None:
            return JsonResponse({"error": "Workspace not found"}, status=404)
        qs = qs.filter(project_id__in=ws.get("projectIds", []))
    # scope_type == "all": no additional filter

    # Materialize candidates with the fields needed for the Python-side
    # filters: title for subsequence matching, project__archived for the
    # ``include_archived_projects`` toggle AND for the has_archived_in_scope
    # signal sent back to the dialog. Fetching all three in one query keeps
    # the code path linear and the JOIN cost negligible at TwiCC scale.
    rows = await sync_to_async(list)(
        qs.values_list("id", "title", "project__archived")
    )

    if title_query:
        rows = [r for r in rows if match_text_query(title_query, r[1] or r[0])]

    # Used by the dialog to decide whether the "Include archived projects"
    # switch is meaningful. Only relevant for workspace/all — a project scope
    # always targets exactly one project regardless of its archived state.
    has_archived_in_scope = (
        scope_type in ("workspace", "all") and any(r[2] for r in rows)
    )

    if scope_type in ("workspace", "all") and not include_archived_projects:
        rows = [r for r in rows if not r[2]]

    ids = {r[0] for r in rows}

    if dry_run:
        return JsonResponse({
            "count": len(ids),
            "has_archived_in_scope": has_archived_in_scope,
        })

    # Re-check active_ids just before UPDATE to close the TOCTOU window.
    active_ids_now = {
        info.session_id
        for info in get_agent_manager_registry().get_active_agents()
    }
    ids -= active_ids_now

    # Atomic update under the DB write lock: flip ``archived`` AND reset
    # ``search_version`` to 0 in the same query. The reset guarantees that
    # any session whose Tantivy re-index is interrupted by shutdown (the
    # detached task below) will be picked up by the next-boot search
    # indexing sweep (which selects rows with ``search_version != CURRENT``).
    await run_under_db_write_lock(
        lambda: Session.objects.filter(id__in=ids).aupdate(
            archived=True, search_version=0,
        )
    )

    if ids:
        channel_layer = get_channel_layer()
        await channel_layer.group_send("updates", {
            "type": "broadcast",
            "data": {
                "type": "sessions_bulk_archived",
                "session_ids": list(ids),
            },
        })

        ids_snapshot = list(ids)

        async def post_archive_work():
            for sid in ids_snapshot:
                try:
                    if search.is_initialized():
                        await asyncio.to_thread(search.reindex_session, sid)
                        # Bump ``search_version`` back to CURRENT only once
                        # the Tantivy write has landed — shutdown between
                        # the reindex and this mark leaves the session at
                        # search_version=0 for the next-boot sweep to fix.
                        await run_under_db_write_lock(
                            lambda sid=sid: mark_session_search_version_current(sid)
                        )
                except Exception:
                    logger.exception("bulk archive: reindex failed for session %s", sid)
                try:
                    await asyncio.to_thread(kill_all_tmux_terminals, f"s:{sid}")
                except Exception:
                    logger.exception("bulk archive: tmux cleanup failed for session %s", sid)

        # Strong-reference the task in ``_DETACHED_TASKS`` so the GC can't
        # drop it mid-flight (asyncio only weakly references running tasks).
        # The done callback removes it once finished.
        cleanup_task = asyncio.create_task(post_archive_work(), name="bulk-archive-cleanup")
        _DETACHED_TASKS.add(cleanup_task)
        cleanup_task.add_done_callback(_DETACHED_TASKS.discard)

    return JsonResponse({"count": len(ids)})


async def session_items(request, project_id, session_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/ - Items of a session.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/

    When parent_session_id is provided, validates that session.parent_session_id matches.

    Query params (optional):
        range: one or more ranges (can be repeated)
            Formats:
            - "N" : exact line N
            - "min:max" : lines min to max (inclusive)
            - "min:" : all lines from min onwards
            - ":max" : all lines up to max

    Examples:
        ?range=5                -> line 5 only
        ?range=0:15             -> lines 0 to 15 inclusive
        ?range=10:              -> all lines from 10 onwards
        ?range=:10              -> all lines up to 10
        ?range=0:10&range=20:30&range=50:  -> multiple ranges combined
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    # Filter by line_num ranges (required — refusing to serve the whole session).
    ranges = request.GET.getlist("range")
    if not ranges:
        logger.warning(
            "session_items called without 'range' for session %s (project %s)",
            session_id,
            project_id,
        )
        return JsonResponse(
            {"error": "At least one 'range' query parameter is required"},
            status=400,
        )

    q_filter = parse_line_ranges(ranges)
    if q_filter is None:
        return JsonResponse(
            {"error": "No valid 'range' query parameter could be parsed"},
            status=400,
        )

    items = await sync_to_async(list)(session.items.filter(q_filter))
    data = [serialize_session_item(item) for item in items]
    return JsonResponse(data, safe=False)


async def session_items_metadata(request, project_id, session_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/metadata/ - Metadata of all items.

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/metadata/

    When parent_session_id is provided, validates that session.parent_session_id matches.

    Returns all items with metadata fields but WITHOUT content.
    Used for initial session load to build the visual items list.
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)

    items = await sync_to_async(list)(
        session.items.all().defer('content')  # Already ordered by line_num (see Meta.ordering)
    )
    data = [serialize_session_item_metadata(item) for item in items]
    return JsonResponse(data, safe=False)


async def tool_results(request, project_id, session_id, line_num, tool_id, parent_session_id=None):
    """GET /api/projects/<id>/sessions/<session_id>/items/<line_num>/tool-results/<tool_id>/

    Also handles subagent route:
    GET /api/projects/<id>/sessions/<parent_session_id>/subagent/<session_id>/items/<line_num>/tool-results/<tool_id>/

    Returns the tool_result content(s) for a specific tool_use.
    Uses ToolResultLink to find related tool_result items.
    """
    session = await _resolve_session_or_404(session_id, project_id, parent_session_id)
    payload = await sync_to_async(tool_results_payload)(session, line_num, tool_id)
    return JsonResponse(payload)


async def subagents_state(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/subagents/

    Returns the agent links for a session: tool_use_id → agent_id mappings.
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    if session.parent_session_id is not None:
        raise Http404("Session not found")

    links = await sync_to_async(list)(
        AgentLink.objects.filter(session=session).order_by("id")
    )
    result = await sync_to_async(serialize_agent_links)(links)
    return JsonResponse(result, safe=False)


async def tool_states(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/tool-states/

    Returns the completion state of all tool_use calls in the session:
    result_count, completed_at, error, and optional extra data.

    Response: {"tools": {"toolu_xxx": {"result_count": 2, "completed_at": "...", "error": null, "extra": "..."}, ...}}
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    tools = await sync_to_async(aggregate_tool_states)(
        ToolResultLink.objects.filter(session=session)
    )
    return JsonResponse({"tools": tools})


async def session_topology(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/topology/

    Return the spawned-session tree (``spawned_by`` links) containing this
    session, rooted at its top-level ancestor. Reuses ``build_topology`` — the
    same engine behind ``twicc topology`` — with full session payloads so the
    frontend can render each node's title, agent-settings summary, annotations,
    cost (own + cumulative subtree) and live process state.

    Not live: the Orchestration tab renders a snapshot and refetches on demand.
    ``twicc_pid=os.getpid()`` matches how the backend stamps ``ProcessRun`` rows
    (cf. ``base_manager``), so process state resolves without re-reading the
    instance status file.
    """
    try:
        session = await Session.objects.aget(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Orchestration trees follow ``spawned_by``; provider-internal subagents
    # (``parent_session_id`` set) are out of scope and never carry the tab.
    if session.parent_session_id is not None:
        raise Http404("Session not found")

    from twicc.cli.topology import build_topology

    data = await sync_to_async(build_topology)(
        session,
        include_processes=True,
        full_sessions=True,
        twicc_pid=os.getpid(),
    )
    return JsonResponse(data)


async def session_workflows(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/workflows/

    Return every Claude Code workflow run persisted for this session, newest
    first. Each entry is ``{run_id, updated_at, raw}`` where ``raw`` is the
    parsed ``wf_*.json`` envelope (the runtime's verbatim run state). Reads
    straight from the ``Workflow`` rows ingestion fills — no filesystem access —
    so a finished run stays available after Claude Code sublimates its files.
    """
    session = await _resolve_session_or_404(session_id, project_id, None)

    from twicc.providers.claude_code.workflow_synthesis import apply_orphan_status

    workflows = await sync_to_async(list)(
        Workflow.objects.filter(session_id=session_id)
    )
    cutoff = session.cutoff
    data = [
        {
            "run_id": w.run_id,
            "updated_at": w.updated_at.isoformat(),
            "cost": float(w.cost),
            "phases_cost": w.phases_cost,
            # An orphaned synthetic run (its session restarted or stopped without a
            # wf_*.json ever landing) is surfaced as interrupted at read time —
            # derived, not stored (no file event fires for a crash that writes nothing).
            "raw": apply_orphan_status(orjson.loads(w.raw_json), w.updated_at, cutoff),
        }
        for w in workflows
    ]
    data.sort(
        key=lambda d: (d["raw"].get("startTime") or 0) if isinstance(d["raw"], dict) else 0,
        reverse=True,
    )
    return JsonResponse(data, safe=False)


def _derive_workflow_links(session_id):
    """``[{tool_use_id, run_id}]`` for every ``Workflow`` tool_result of a session.

    Derived (not stored) from the launching tool_result's ``toolUseResult.runId``
    + the tool_result block's ``tool_use_id``. Reads from our persisted
    SessionItem rows, so it survives the JSONL being sublimated. Cheap: only
    items mentioning ``runId`` are scanned, then validated.
    """
    links = []
    seen = set()
    items = SessionItem.objects.filter(
        session_id=session_id, content__contains='"runId"'
    ).only("content")
    for it in items.iterator(chunk_size=50):
        try:
            parsed = orjson.loads(it.content)
        except orjson.JSONDecodeError:
            continue
        tool_use_result = parsed.get("toolUseResult")
        if not isinstance(tool_use_result, dict):
            continue
        run_id = tool_use_result.get("runId")
        if not run_id:
            continue
        message = parsed.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        tool_use_id = next(
            (b.get("tool_use_id") for b in content
             if isinstance(b, dict) and b.get("type") == "tool_result"),
            None,
        )
        if tool_use_id and tool_use_id not in seen:
            seen.add(tool_use_id)
            links.append({"tool_use_id": tool_use_id, "run_id": run_id})
    return links


async def workflow_links(request, project_id, session_id):
    """GET /api/projects/<id>/sessions/<session_id>/workflow-links/

    Lightweight ``[{tool_use_id, run_id}]`` list for the conversation: which
    ``Workflow`` tool_uses have a known run, so the chat can show "View
    Workflow" without loading the full envelopes. Mirrors ``/subagents/`` for
    agent links; the live counterpart is the ``workflow_link_created`` WS event.
    """
    await _resolve_session_or_404(session_id, project_id, None)
    links = await sync_to_async(_derive_workflow_links)(session_id)
    return JsonResponse(links, safe=False)


def _store_synthesis_and_build(session_id, run_id, meta, templates, script_hash, detection_unavailable=False):
    """Store a front's ``{meta, templates}`` on the run (guarded by
    ``script_hash``) and rebuild its STATE 1 envelope. Returns ``(status, payload)``:

    - ``("ok", raw_dict)`` — synthesis stored, STATE 1 (re)built;
    - ``("not_found", None)`` — no such run for this session;
    - ``("completed", None)`` — the real envelope already landed (STATE 2);
    - ``("stale_hash", current_hash)`` — the script changed since the front
      generated, so its templates are stale; the front should regenerate.
    """
    from twicc.providers.claude_code.workflow_synthesis import rebuild_state1

    try:
        workflow = Workflow.objects.get(run_id=run_id, session_id=session_id)
    except Workflow.DoesNotExist:
        return ("not_found", None)
    try:
        prior = orjson.loads(workflow.raw_json)
    except orjson.JSONDecodeError:
        prior = {}
    if not prior.get("synthetic"):
        return ("completed", None)
    if workflow.script_hash and workflow.script_hash != script_hash:
        return ("stale_hash", workflow.script_hash)
    workflow.synthesis = {"meta": meta, "templates": templates, "detectionUnavailable": detection_unavailable}
    workflow.save(update_fields=["synthesis", "updated_at"])
    if detection_unavailable:
        logger.warning(
            "[workflow] browser template generation failed for run %s — building a "
            "degraded running view (phases shown, agents Unassigned)", run_id,
        )
    return ("ok", rebuild_state1(run_id))


async def workflow_synthesis(request, project_id, session_id, run_id):
    """POST /api/projects/<id>/sessions/<sid>/workflows/<run_id>/synthesis/

    A viewing front, seeing a STATE 0 run (``synthetic`` with no phases yet),
    generates ``{meta, templates}`` from the launch script and POSTs them here.
    We store them (guarded by ``script_hash`` — a stale POST after a mid-run
    script change is rejected with 409), build the STATE 1 running view (phase
    detection over the live journal), broadcast ``workflow_changed``, and return
    the synthesized envelope. The browser owns template *generation* (eval); the
    back owns the string-matching detection.
    """
    session = await _resolve_session_or_404(session_id, project_id, None)
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    meta = data.get("meta")
    templates = data.get("templates")
    script_hash = data.get("script_hash")
    # The front sets this when it could extract meta but not execute the script to
    # derive templates — we still build a degraded view and flag detection.
    detection_unavailable = bool(data.get("detection_unavailable"))
    if not isinstance(meta, dict) or not isinstance(templates, list) or not isinstance(script_hash, str):
        return JsonResponse(
            {"error": "meta (object), templates (array) and script_hash (string) are required"},
            status=400,
        )

    status, payload = await run_under_db_write_lock(
        lambda: sync_to_async(_store_synthesis_and_build)(
            session_id, run_id, meta, templates, script_hash, detection_unavailable
        )
    )
    if status == "not_found":
        raise Http404("Workflow run not found")
    if status == "completed":
        return JsonResponse({"error": "Run already completed"}, status=409)
    if status == "stale_hash":
        return JsonResponse(
            {"error": "Script changed since generation; regenerate", "script_hash": payload},
            status=409,
        )
    if payload is None:  # defensive: synthesis stored but the build yielded nothing
        return JsonResponse({"error": "Failed to build running view"}, status=500)

    # Tell other open Workflows tabs on this session to refetch (the POSTing
    # front already has the payload below). Gated on hidden, like the watcher.
    if not session.hidden:
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "workflow_changed",
                "session_id": session_id,
                "project_id": project_id,
                "run_id": run_id,
            }},
        )
    return JsonResponse(payload, safe=False)


async def directory_tree(request, project_id, session_id=None):
    """GET directory tree listing.

    Works at project level (/api/projects/<id>/directory-tree/)
    or session level (/api/projects/<id>/sessions/<session_id>/directory-tree/).
    """
    from twicc.file_tree import get_directory_tree, validate_path

    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, request.GET.get("path"), session_id=session_id
    )
    if error:
        return error

    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") == "1"

    tree = await asyncio.to_thread(
        get_directory_tree, dir_path, show_hidden=show_hidden, show_ignored=show_ignored
    )
    return JsonResponse(tree)


async def file_search(request, project_id, session_id=None):
    """GET fuzzy file search.

    Works at project level (/api/projects/<id>/file-search/)
    or session level (/api/projects/<id>/sessions/<session_id>/file-search/).
    """
    from twicc.file_tree import search_files, validate_path

    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, request.GET.get("path"), session_id=session_id
    )
    if error:
        return error

    query = request.GET.get("q", "").strip()
    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") == "1"

    try:
        max_results = int(request.GET.get("limit", 50))
        max_results = max(1, min(max_results, 200))
    except (ValueError, TypeError):
        max_results = 50

    tree = await asyncio.to_thread(
        search_files,
        dir_path, query,
        max_results=max_results,
        show_hidden=show_hidden,
        show_ignored=show_ignored,
    )
    return JsonResponse(tree)


def validate_standalone_root(path, root):
    """Validate that path is within root directory. Returns error response or None."""
    if not root:
        return None  # No restriction
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    if path != root and not path.startswith(root + os.sep):
        return JsonResponse({"error": "Path is outside the allowed root directory"}, status=403)
    return None


async def standalone_directory_tree(request):
    """GET directory tree listing for any absolute directory path.

    Unlike the project-scoped directory-tree endpoint, this does not require
    a project and does not validate path ownership. The user can browse any
    directory accessible with their OS permissions — this is intentional since
    TwiCC is a local-only tool running on the user's own machine.

    Authentication is enforced by PasswordAuthMiddleware.

    Supports an optional ?root= parameter for server-side path restriction.

    If show_ignored is not explicitly passed, defaults to True so directories
    inside git repos are not hidden by .gitignore rules (the user needs to see
    all directories when picking a project root).
    """
    from twicc.file_tree import get_directory_tree

    dir_path = request.GET.get("path", "").strip()
    if not dir_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    if not os.path.isabs(dir_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    if not os.path.isdir(dir_path):
        return JsonResponse({"error": "Directory not found"}, status=404)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(dir_path, root)
    if error:
        return error

    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") != "0" if "show_ignored" in request.GET else True
    directories_only = request.GET.get("directories_only") == "1"

    tree = await asyncio.to_thread(
        get_directory_tree,
        dir_path, show_hidden=show_hidden, show_ignored=show_ignored, directories_only=directories_only,
    )
    return JsonResponse(tree)


async def home_directory(request):
    """GET the current user's home directory path."""
    return JsonResponse({"path": os.path.expanduser("~")})


async def standalone_file_search(request):
    """GET file search for any absolute directory path.

    Unlike the project-scoped file-search endpoint, this does not require
    a project. Supports an optional ?root= parameter for server-side path restriction.

    If show_ignored is not explicitly passed, defaults to True.
    """
    from twicc.file_tree import search_files

    dir_path = request.GET.get("path", "").strip()
    if not dir_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    dir_path = os.path.normpath(dir_path)

    if not os.path.isabs(dir_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    if not os.path.isdir(dir_path):
        return JsonResponse({"error": "Directory not found"}, status=404)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(dir_path, root)
    if error:
        return error

    query = request.GET.get("q", "").strip()
    show_hidden = request.GET.get("show_hidden") == "1"
    show_ignored = request.GET.get("show_ignored") != "0" if "show_ignored" in request.GET else True

    try:
        max_results = int(request.GET.get("limit", 50))
        max_results = max(1, min(max_results, 200))
    except (ValueError, TypeError):
        max_results = 50

    tree = await asyncio.to_thread(
        search_files,
        dir_path, query,
        max_results=max_results,
        show_hidden=show_hidden,
        show_ignored=show_ignored,
    )
    return JsonResponse(tree)


async def standalone_file_content(request):
    """GET/PUT file content for any absolute file path.

    Unlike the project-scoped file-content endpoint, this does not require
    a project. Supports an optional root parameter for server-side path restriction.
    """
    from twicc.file_content import get_file_content, get_file_meta, write_file_content

    # PUT: write file content
    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        content = data.get("content")
        if content is None:
            return JsonResponse({"error": "Missing 'content' field"}, status=400)

        file_path = (data.get("path") or "").strip()
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)

        file_path = os.path.normpath(file_path)

        if not os.path.isabs(file_path):
            return JsonResponse({"error": "Path must be absolute"}, status=400)

        root = (data.get("root") or "").strip()
        error = validate_standalone_root(file_path, root)
        if error:
            return error

        parent_dir = os.path.dirname(file_path)
        if not os.path.isdir(parent_dir):
            return JsonResponse({"error": "Parent directory not found"}, status=404)

        result = await asyncio.to_thread(write_file_content, file_path, content)
        if result.get("error"):
            return JsonResponse(result, status=400)

        return JsonResponse(result)

    # GET: read file content (default)
    file_path = request.GET.get("path", "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    file_path = os.path.normpath(file_path)

    if not os.path.isabs(file_path):
        return JsonResponse({"error": "Path must be absolute"}, status=400)

    root = request.GET.get("root", "").strip()
    error = validate_standalone_root(file_path, root)
    if error:
        return error

    if request.GET.get("meta_only"):
        result = await asyncio.to_thread(get_file_meta, file_path)
        if result.get("error"):
            return JsonResponse(result, status=404)
        return JsonResponse(result)

    if not os.path.isfile(file_path):
        return JsonResponse({"error": "File not found"}, status=404)

    result = await asyncio.to_thread(get_file_content, file_path)
    if result.get("error"):
        return JsonResponse(result, status=400)

    return JsonResponse(result)


# --- Raw file serving for HTML preview ----------------------------------
#
# The Files and Artifacts tabs can render an HTML file inside a sandboxed
# <iframe>. The iframe needs the file (and its relative CSS/JS/asset siblings)
# served as raw bytes with a real Content-Type — file-content returns JSON, and
# session_artifact only allows image extensions. These endpoints fill that gap.
#
# Crucially, the file path travels in the URL *path* (not a query parameter) so
# the browser resolves a page's relative references (``href="style.css"``) to
# sibling raw URLs. Confinement mirrors the JSON endpoints: project scope uses
# the project/session allowed dirs (validate_path); standalone scope carries a
# base64url-encoded confinement root as a path segment (so it, too, survives
# relative resolution) and reuses validate_standalone_root.

# Content types for the web asset extensions mimetypes may miss or map
# inconsistently across platforms; everything else falls back to mimetypes.
_RAW_CONTENT_TYPE_OVERRIDES: dict[str, str] = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    # Documents / media the Artifacts tab renders directly (PDF viewer,
    # <audio>/<video>). Pinned so the right type is served regardless of the
    # platform's mimetypes database.
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".weba": "audio/webm",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
}


def _guess_raw_content_type(path: str) -> str:
    import mimetypes

    ext = os.path.splitext(path)[1].lower()
    if ext in _RAW_CONTENT_TYPE_OVERRIDES:
        return _RAW_CONTENT_TYPE_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _raw_file_response(normalized_path: str, *, as_attachment: bool = False):
    """Return a FileResponse for a regular file, or ``None``.

    ``os.stat`` follows symlinks, so a symlinked target is served only when it
    resolves to a regular file. Callers are responsible for confinement (the
    path has already been validated against the allowed root/dirs).

    ``as_attachment`` turns the response into a download: Django adds a
    ``Content-Disposition: attachment`` header carrying the file's basename
    (RFC 5987-encoded when it is not ASCII).
    """
    import stat

    from django.http import FileResponse
    from django.utils.cache import add_never_cache_headers

    try:
        st = os.stat(normalized_path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    try:
        fp = open(normalized_path, "rb")
    except OSError:
        return None
    response = FileResponse(
        fp,
        content_type=_guess_raw_content_type(normalized_path),
        as_attachment=as_attachment,
    )
    # Render with the declared type only — never let the browser sniff a
    # served asset into something executable in a different context.
    response["X-Content-Type-Options"] = "nosniff"
    # Never cache raw-served files. These back the live HTML preview (an <iframe>
    # whose page pulls sibling CSS/JS/asset URLs from this same endpoint), and the
    # agent rewrites those files in place. A cached asset would survive even a hard
    # reload — the iframe's relative sub-resources don't carry the preview's
    # cache-bust query — leaving the preview running stale JS/CSS. ``no-store`` +
    # ``private`` (set by add_never_cache_headers) also stops a fronting CDN/tunnel
    # (e.g. Cloudflare) from edge-caching by file extension in its default mode.
    add_never_cache_headers(response)
    # Belt-and-suspenders for CDN tiers (RFC 9213, honored by Cloudflare/Fastly/
    # Akamai): a dedicated CDN-layer directive, in case an aggressive edge rule
    # (e.g. Cloudflare "Cache Everything") would otherwise ignore Cache-Control.
    response["CDN-Cache-Control"] = "no-store"
    return response


def _serve_artifact_file(normalized_path: str, *, as_document: bool):
    """Serve an artifact file, wrapping it with the network broker when it is the
    top-level HTML *document* (``as_document``) — shim injected + strict CSP
    (design §7/§8). Sub-assets and non-HTML keep streaming raw via
    :func:`_raw_file_response`. Returns ``None`` for a non-regular/unreadable
    file (→ 404). Sync (file I/O): call via ``asyncio.to_thread`` like the raw
    path."""
    import stat

    if as_document and _guess_raw_content_type(normalized_path) == "text/html":
        try:
            st = os.stat(normalized_path)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        try:
            with open(normalized_path, "rb") as fp:
                html = fp.read()
        except OSError:
            return None
        from twicc.artifacts.broker_html import artifact_html_response

        return artifact_html_response(html)
    return _raw_file_response(normalized_path)


async def _raw_serve_response(request, normalized_path: str):
    """Serve an already-validated raw path. Returns ``None`` for a
    non-regular/unreadable file (the caller turns that into a 404).

    ``?download=1`` streams the file as an attachment and **bypasses the
    artifact broker wrap**: an ``<a download>`` click is a navigation, so the
    browser sends ``Sec-Fetch-Dest: document`` and the HTML would otherwise
    arrive shim-injected + CSP-gated instead of as written on disk.
    """
    if request.GET.get("download") in ("1", "true"):
        return await asyncio.to_thread(_raw_file_response, normalized_path, as_attachment=True)

    from twicc.artifacts.broker_html import is_artifact_document_request

    as_document = is_artifact_document_request(request.headers.get("Sec-Fetch-Dest"))
    return await asyncio.to_thread(_serve_artifact_file, normalized_path, as_document=as_document)


def _normalize_raw_filepath(filepath: str) -> str:
    """Turn a ``<path:filepath>`` capture into a normalized absolute path."""
    return os.path.normpath("/" + filepath.lstrip("/"))


def _doc_dir_from_header(request, *, prefix: str) -> str | None:
    """Filesystem directory of the serving document, from the host-set
    ``X-Twicc-Artifact-Doc`` header (its URL pathname). ``None`` when absent
    or not under this route's own ``prefix`` — a doc served by another route
    (or a forged value) never authorizes a write here.

    The header is trusted because the broker host overwrites it before
    forwarding, so an artifact cannot forge it; a user forging it by hand gains
    nothing the standalone file-modify endpoints do not already grant (same
    reasoning as the broker design §6.4).
    """
    raw = request.headers.get("X-Twicc-Artifact-Doc")
    if not raw:
        return None
    doc_path = unquote(raw)
    if not doc_path.startswith(prefix):
        return None
    return os.path.dirname(_normalize_raw_filepath(doc_path[len(prefix):]))


def _dispatch_data_request(request, doc_dir: str, target: str):
    """Handle a ``data/`` store request on an artifact-serving route
    (design 2026-08-05 §3/§4): ``PUT``/``DELETE`` on a file, ``GET`` on the
    ``data/`` tree (the listing). Called only when the ``X-Twicc-Artifact-Doc``
    header checked out; ``doc_dir`` is the serving document's directory,
    already validated by the caller against the route's own confinement — this
    helper only enforces the ``data/`` boundary. ``target`` is the caller's
    already-normalized filesystem path for the request. Returns ``None`` when
    the request is a plain file ``GET``/``HEAD`` (caller keeps its existing
    raw-serving path). Sync (file I/O): call via ``asyncio.to_thread``.
    """
    from twicc.artifacts import data_store

    data_root = os.path.join(os.path.realpath(doc_dir), "data")
    if request.method in ("GET", "HEAD"):
        resolved = os.path.realpath(target)
        # The data/ root itself lists even when it does not exist yet (empty
        # store — the artifact probes before its first write); an existing
        # subdirectory under it lists its own subtree. Files stay raw-served.
        if resolved == data_root or (
            resolved.startswith(data_root + os.sep) and os.path.isdir(resolved)
        ):
            payload, status = data_store.list_data_dir(resolved)
            return JsonResponse(payload, status=status)
        return None
    resolved = data_store.resolve_data_target(doc_dir, target)
    if resolved is None:
        return JsonResponse({"error": "outside_data"}, status=403)
    if request.method == "PUT":
        payload, status = data_store.write_data_file(data_root, resolved, request.body)
    else:
        payload, status = data_store.delete_data_file(resolved)
    return JsonResponse(payload, status=status)


async def file_raw(request, project_id, filepath, session_id=None):
    """GET raw file bytes for HTML preview (project / session scope).

    Mounted at ``/api/projects/<id>/file-raw/<path:filepath>`` and the
    session-scoped variant. Confinement matches :func:`file_content`: the
    file's directory must be within the project/session allowed base dirs.

    ``?download=1`` serves the file as an attachment instead (Files / Git
    working-tree downloads) — see :func:`_raw_serve_response`.

    ``PUT``/``DELETE`` (and a ``GET`` on a directory) additionally serve the
    artifact data store when the host-set ``X-Twicc-Artifact-Doc`` header names
    a document this route serves — see :func:`_dispatch_data_request`.
    """
    from twicc.file_tree import validate_path

    if request.method not in ("GET", "HEAD", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "HEAD", "PUT", "DELETE"])

    normalized = _normalize_raw_filepath(filepath)

    # The data-store dispatch runs BEFORE the target's own validate_path: the
    # latter requires the target's directory to already exist, which the very
    # first write into a fresh ``data/`` tree cannot satisfy. Confinement is not
    # weakened — validate_path is applied to the *document's* directory, and the
    # dispatch then requires the target to live under ``<doc dir>/data/``, hence
    # transitively inside the same allowed base dirs.
    if session_id:
        prefix = f"/api/projects/{project_id}/sessions/{session_id}/file-raw/"
    else:
        prefix = f"/api/projects/{project_id}/file-raw/"
    doc_dir = _doc_dir_from_header(request, prefix=prefix)
    if request.method in ("PUT", "DELETE") and doc_dir is None:
        # Writes are only ever authorized by a document this route serves.
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if doc_dir is not None:
        _doc_session, _doc_dir_path, doc_error = await sync_to_async(validate_path)(
            project_id, doc_dir, session_id=session_id
        )
        if doc_error:
            return doc_error
        handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir, normalized)
        if handled is not None:
            return handled

    _session, _dir_path, error = await sync_to_async(validate_path)(
        project_id, os.path.dirname(normalized), session_id=session_id
    )
    if error:
        return error

    response = await _raw_serve_response(request, normalized)
    if response is None:
        raise Http404("File not found")
    return response


async def standalone_file_raw(request, root_b64, filepath):
    """GET raw file bytes for HTML preview (standalone / Artifacts scope).

    Mounted at ``/api/file-raw/<root_b64>/<path:filepath>``. ``root_b64`` is the
    base64url-encoded confinement root (the Artifacts tab passes the session's
    artifacts dir). Confinement mirrors :func:`standalone_file_content`.

    ``?download=1`` serves the file as an attachment instead (Artifacts tab
    downloads) — see :func:`_raw_serve_response`.

    ``PUT``/``DELETE`` (and a ``GET`` on a directory) additionally serve the
    artifact data store when the host-set ``X-Twicc-Artifact-Doc`` header names
    a document this route serves — see :func:`_dispatch_data_request`.
    """
    import base64

    if request.method not in ("GET", "HEAD", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "HEAD", "PUT", "DELETE"])

    try:
        padding = "=" * (-len(root_b64) % 4)
        root = base64.urlsafe_b64decode(root_b64 + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise Http404("Invalid preview root")
    root = os.path.normpath(root) if root else ""

    normalized = _normalize_raw_filepath(filepath)
    error = validate_standalone_root(normalized, root)
    if error:
        return error

    # Defense in depth: after symlink resolution the real path must still live
    # inside the confinement root.
    if root:
        resolved_root = os.path.realpath(root)
        resolved = os.path.realpath(normalized)
        if resolved != resolved_root and not resolved.startswith(resolved_root + os.sep):
            raise Http404("File not found")

    doc_dir = _doc_dir_from_header(request, prefix=f"/api/file-raw/{root_b64}/")
    if request.method in ("PUT", "DELETE") and doc_dir is None:
        # Writes are only ever authorized by a document this route serves.
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if doc_dir is not None:
        # The doc itself must satisfy the same confinement as the target.
        if validate_standalone_root(doc_dir, root) is not None:
            return JsonResponse({"error": "doc_outside_root"}, status=403)
        handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir, normalized)
        if handled is not None:
            return handled

    response = await _raw_serve_response(request, normalized)
    if response is None:
        raise Http404("File not found")
    return response


async def _standalone_file_modify(request, action):
    """Shared logic for standalone file rename, delete, move and create operations."""
    from twicc.file_content import create_path, delete_path, move_path, rename_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    root = (data.get("root") or "").strip()

    if action == "create":
        parent_dir = (data.get("parent_dir") or "").strip()
        if not parent_dir:
            return JsonResponse({"error": "Missing 'parent_dir' field"}, status=400)
        parent_dir = os.path.normpath(parent_dir)
        if not os.path.isabs(parent_dir):
            return JsonResponse({"error": "Path must be absolute"}, status=400)
        error = validate_standalone_root(parent_dir, root)
        if error:
            return error
        name = (data.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Missing 'name' field"}, status=400)
        kind = data.get("kind", "file")
        if kind not in ("file", "directory"):
            return JsonResponse({"error": "Invalid 'kind' field"}, status=400)
        result = await asyncio.to_thread(create_path, parent_dir, name, kind)
    else:
        file_path = (data.get("path") or "").strip()
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)
        file_path = os.path.normpath(file_path)
        if not os.path.isabs(file_path):
            return JsonResponse({"error": "Path must be absolute"}, status=400)
        error = validate_standalone_root(file_path, root)
        if error:
            return error

        if action == "rename":
            new_name = (data.get("new_name") or "").strip()
            if not new_name:
                return JsonResponse({"error": "Missing 'new_name' field"}, status=400)
            result = await asyncio.to_thread(rename_path, file_path, new_name)
        elif action == "move":
            destination_dir = (data.get("destination_dir") or "").strip()
            if not destination_dir:
                return JsonResponse({"error": "Missing 'destination_dir' field"}, status=400)
            destination_dir = os.path.normpath(destination_dir)
            if not os.path.isabs(destination_dir):
                return JsonResponse({"error": "Destination must be absolute"}, status=400)
            error = validate_standalone_root(destination_dir, root)
            if error:
                return error
            result = await asyncio.to_thread(move_path, file_path, destination_dir)
        else:
            result = await asyncio.to_thread(delete_path, file_path)

    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def standalone_file_rename(request):
    """POST: rename a file or directory (standalone)."""
    return await _standalone_file_modify(request, "rename")


async def standalone_file_delete(request):
    """POST: delete a file or directory (standalone)."""
    return await _standalone_file_modify(request, "delete")


async def standalone_file_move(request):
    """POST: move a file or directory (standalone)."""
    return await _standalone_file_modify(request, "move")


async def standalone_file_create(request):
    """POST: create a new file or directory (standalone)."""
    return await _standalone_file_modify(request, "create")


async def file_content(request, project_id, session_id=None):
    """GET/PUT file content.

    Works at project level (/api/projects/<id>/file-content/)
    or session level (/api/projects/<id>/sessions/<session_id>/file-content/).

    GET: read file content (existing behavior).
    PUT: write file content (full replacement).
    """
    from twicc.file_content import get_file_content, get_file_meta, write_file_content
    from twicc.file_tree import validate_path

    # PUT: write file content
    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        content = data.get("content")
        if content is None:
            return JsonResponse({"error": "Missing 'content' field"}, status=400)

        file_path = data.get("path")
        if not file_path:
            return JsonResponse({"error": "Missing 'path' field"}, status=400)

        # Validate that the file's directory is within allowed project/session paths
        dir_path = os.path.dirname(os.path.normpath(file_path))
        session, dir_path, error = await sync_to_async(validate_path)(
            project_id, dir_path, session_id=session_id
        )
        if error:
            return error

        normalized = os.path.normpath(file_path)
        result = await asyncio.to_thread(write_file_content, normalized, content)
        if result.get("error"):
            return JsonResponse(result, status=400)

        return JsonResponse(result)

    # GET: read file content (default)
    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    normalized = os.path.normpath(file_path)

    # meta_only: lightweight writable check for files and directories.
    # For directories, validate the path itself (not dirname) since the
    # directory may be an allowed root whose parent is outside scope.
    if request.GET.get("meta_only"):
        check_dir = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
        _session, _check_dir, error = await sync_to_async(validate_path)(project_id, check_dir, session_id=session_id)
        if error:
            return error
        result = await asyncio.to_thread(get_file_meta, normalized)
        if result.get("error"):
            return JsonResponse(result, status=404)
        return JsonResponse(result)

    # Validate that the file's directory is within allowed project/session paths
    dir_path = os.path.dirname(normalized)
    session, dir_path, error = await sync_to_async(validate_path)(
        project_id, dir_path, session_id=session_id
    )
    if error:
        return error

    # Now check the file itself exists
    if not os.path.isfile(normalized):
        return JsonResponse({"error": "File not found"}, status=404)

    result = await asyncio.to_thread(get_file_content, normalized)
    if result.get("error"):
        return JsonResponse(result, status=400)

    return JsonResponse(result)


async def _file_modify(request, project_id, session_id, action):
    """Shared logic for file rename, delete and move operations.

    Create is handled separately in file_create() because it validates the
    parent directory (not the path itself) and takes different parameters.
    """
    from twicc.file_content import delete_path, move_path, rename_path
    from twicc.file_tree import validate_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    file_path = (data.get("path") or "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' field"}, status=400)

    normalized = os.path.normpath(file_path)
    dir_path = os.path.dirname(normalized)
    _session, dir_path, error = await sync_to_async(validate_path)(project_id, dir_path, session_id=session_id)
    if error:
        return error

    if action == "rename":
        new_name = (data.get("new_name") or "").strip()
        if not new_name:
            return JsonResponse({"error": "Missing 'new_name' field"}, status=400)
        result = await asyncio.to_thread(rename_path, normalized, new_name)
    elif action == "move":
        destination_dir = (data.get("destination_dir") or "").strip()
        if not destination_dir:
            return JsonResponse({"error": "Missing 'destination_dir' field"}, status=400)
        dest_normalized = os.path.normpath(destination_dir)
        _session2, _dir_path2, error2 = await sync_to_async(validate_path)(project_id, dest_normalized, session_id=session_id)
        if error2:
            return error2
        result = await asyncio.to_thread(move_path, normalized, dest_normalized)
    else:
        result = await asyncio.to_thread(delete_path, normalized)

    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def file_rename(request, project_id, session_id=None):
    """POST: rename a file or directory."""
    return await _file_modify(request, project_id, session_id, "rename")


async def file_delete(request, project_id, session_id=None):
    """POST: delete a file or directory."""
    return await _file_modify(request, project_id, session_id, "delete")


async def file_move(request, project_id, session_id=None):
    """POST: move a file or directory to a different directory."""
    return await _file_modify(request, project_id, session_id, "move")


async def file_create(request, project_id, session_id=None):
    """POST: create a new file or directory."""
    from twicc.file_content import create_path
    from twicc.file_tree import validate_path

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    parent_dir = (data.get("parent_dir") or "").strip()
    if not parent_dir:
        return JsonResponse({"error": "Missing 'parent_dir' field"}, status=400)

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Missing 'name' field"}, status=400)

    kind = data.get("kind", "file")
    if kind not in ("file", "directory"):
        return JsonResponse({"error": "Invalid 'kind' field"}, status=400)

    normalized = os.path.normpath(parent_dir)
    _session, _dir_path, error = await sync_to_async(validate_path)(project_id, normalized, session_id=session_id)
    if error:
        return error

    result = await asyncio.to_thread(create_path, normalized, name, kind)
    if result.get("error"):
        return JsonResponse(result, status=400)
    return JsonResponse(result)


async def git_log(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-log/

    Returns git commit history for the session's git repository.

    Git directory resolution order:
    1. Session git_directory (from tool_use analysis)
    2. Project git_root (resolved from project directory walking up)

    When session_id is None (project-level, e.g. draft sessions), only the
    project git_root is used.

    The current branch is always resolved dynamically from the git directory
    at request time, ensuring it reflects the actual state.

    Returns 404 if no git context is available.

    Response:
        {
            "current_branch": "main",
            "has_more": true/false,
            "entries": [{ hash, branch, parents, message, committerDate, ... }],
            "index_files": {
                "stats": { "modified": 3, "added": 1, "deleted": 0 },
                "tree": { "name": "", "type": "directory", "loaded": true, "children": [...] }
            }
        }
    """
    from twicc.git import GitError, get_current_branch, get_git_log

    # Optional branch filter from query string
    branch_filter = request.GET.get("branch", "")

    # Resolve git directory using the shared helper.
    # An optional ?git_dir= query param lets the frontend request a specific root.
    requested_git_dir = request.GET.get("git_dir")
    try:
        git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    except Http404:
        return JsonResponse({"error": "No git repository found"}, status=404)

    # Always resolve branch dynamically from the git directory at request time,
    # so the branch selector reflects the actual state (handles worktrees too).
    current_branch = await asyncio.to_thread(get_current_branch, git_directory)

    try:
        result = await asyncio.to_thread(get_git_log, git_directory, branch=branch_filter or None)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    if current_branch:
        result["current_branch"] = current_branch
    return JsonResponse(result)


async def _resolve_session_git_directory(project_id, session_id=None, *, requested_git_dir=None):
    """Resolve the git directory for a session or project.

    Resolution order:
    1. If ``requested_git_dir`` is provided, validate it matches one of the
       known git roots (session git_directory, project git_root, or — for a
       worktree — the main repo's git root) and use it.
    2. Session git_directory (from tool_use analysis), if it still exists
    3. Project git_root (resolved from project directory walking up)
    4. Main repo git root, when the project is a worktree (last resort)

    When session_id is None (project-level, e.g. draft sessions), the session
    git_directory is not considered.

    Returns the git_directory path or raises Http404.
    """
    from twicc.roots import git_roots_for

    session = None
    if session_id:
        try:
            session = await Session.objects.aget(id=session_id, project_id=project_id)
        except Session.DoesNotExist:
            raise Http404("Session not found")

        # Only regular sessions (not subagents)
        if session.parent_session_id is not None:
            raise Http404("Session not found")

    try:
        project = await Project.objects.aget(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    # When the project is a git worktree, the main repo's git root is offered too.
    parent = None
    if project.worktree_of_id:
        try:
            parent = await Project.objects.aget(id=project.worktree_of_id)
        except Project.DoesNotExist:
            parent = None

    # Ordered candidate git roots (worktree first, main repo last), restricted to
    # those that still exist on disk.
    candidates = git_roots_for(project, session, parent)
    existing = [d for d in candidates if os.path.isdir(d)]

    if requested_git_dir:
        requested = os.path.normpath(requested_git_dir)
        if requested in existing:
            return requested
        # Requested directory is not among known/existing roots — reject with 404
        # so the frontend can mark it as missing and fall back to another root.
        raise Http404("No git repository found")

    if existing:
        return existing[0]

    raise Http404("No git repository found")


async def git_index_files(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-index-files/

    Returns stats and a file tree for uncommitted (index) changes.

    Response:
        {
            "stats": { "modified": 3, "added": 1, "deleted": 0 },
            "tree": { ... }
        }

    Returns ``null`` if there are no uncommitted changes.
    """
    from twicc.git import get_index_files

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_index_files, git_directory)

    return JsonResponse(result, safe=False)


async def git_commit_detail(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-detail/<commit_hash>/

    Returns detailed metadata for a single commit (hash, message, body,
    author, committer, dates).
    """
    from twicc.git import GitError, get_commit_detail

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    try:
        result = await asyncio.to_thread(get_commit_detail, git_directory, commit_hash)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse(result)


async def git_commit_files(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-files/<commit_hash>/

    Returns stats and a file tree for the files changed by a single commit.

    Response:
        {
            "stats": { "modified": 3, "added": 1, "deleted": 0 },
            "tree": {
                "name": "",
                "type": "directory",
                "loaded": true,
                "children": [...]
            }
        }
    """
    from twicc.git import GitError, get_commit_files

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    try:
        result = await asyncio.to_thread(get_commit_files, git_directory, commit_hash)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse(result)


async def git_index_file_diff(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-index-file-diff/

    Returns the original (HEAD) and modified (working tree) content of a file
    for display in the Monaco diff editor.

    Query params:
        path: File path relative to the git root.

    Response:
        {
            "original": "...",   # content at HEAD (null if new file)
            "modified": "...",   # content on disk (null if deleted)
            "binary": false,
            "error": null
        }
    """
    from twicc.git import get_index_file_diff

    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_index_file_diff, git_directory, file_path)

    if result.get("error"):
        return JsonResponse(result, status=500)

    return JsonResponse(result)


async def git_commit_file_diff(request, project_id, commit_hash, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-commit-file-diff/<commit_hash>/

    Returns the original (parent commit) and modified (commit) content of a file
    for display in the Monaco diff editor.

    Query params:
        path: File path relative to the git root.

    Response:
        {
            "original": "...",   # content at parent commit (null if added)
            "modified": "...",   # content at this commit (null if deleted)
            "binary": false,
            "error": null
        }
    """
    from twicc.git import get_commit_file_diff

    file_path = request.GET.get("path")
    if not file_path:
        return JsonResponse({"error": "Missing 'path' query parameter"}, status=400)

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)
    result = await asyncio.to_thread(get_commit_file_diff, git_directory, commit_hash, file_path)

    if result.get("error"):
        return JsonResponse(result, status=500)

    return JsonResponse(result)


async def _git_file_action(request, project_id, session_id, action):
    """Shared logic for git stage/unstage/discard operations."""
    from twicc.git import GitError, git_discard, git_stage, git_unstage

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    file_path = (data.get("path") or "").strip()
    if not file_path:
        return JsonResponse({"error": "Missing 'path' field"}, status=400)

    requested_git_dir = data.get("git_dir")
    git_directory = await _resolve_session_git_directory(project_id, session_id, requested_git_dir=requested_git_dir)

    fn = {"stage": git_stage, "unstage": git_unstage, "discard": git_discard}[action]

    try:
        await asyncio.to_thread(fn, git_directory, file_path)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"ok": True})


async def git_stage_file(request, project_id, session_id=None):
    """POST: stage a file (git add)."""
    return await _git_file_action(request, project_id, session_id, "stage")


async def git_unstage_file(request, project_id, session_id=None):
    """POST: unstage a file (git restore --staged)."""
    return await _git_file_action(request, project_id, session_id, "unstage")


async def git_discard_file(request, project_id, session_id=None):
    """POST: discard unstaged changes (git restore)."""
    return await _git_file_action(request, project_id, session_id, "discard")


# --- Git downloads ------------------------------------------------------
#
# The Git tab downloads a file as it stands at a given ref, or the patch that
# ref carries for it. Both stream (see twicc.git — no size cap, unlike the
# diff endpoints that feed the editor).


def _git_download_response(stream, filename: str, content_type: str):
    """Wrap a streamed git process into an attachment response."""
    from django.http import FileResponse
    from django.utils.cache import add_never_cache_headers

    response = FileResponse(
        stream,
        content_type=content_type,
        as_attachment=True,
        filename=filename,
    )
    response["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(response)
    response["CDN-Cache-Control"] = "no-store"
    return response


async def _resolve_git_download(request, project_id, session_id):
    """Validate the shared query params of the two download endpoints.

    Returns ``(git_directory, file_path, ref, None)`` or ``(None, None, None,
    error_response)``. ``ref`` is ``"index"`` or a commit hash — it lands in a
    revision spec, so anything else is refused.
    """
    from twicc.git import is_valid_commit_ref

    if request.method not in ("GET", "HEAD"):
        return None, None, None, HttpResponseNotAllowed(["GET", "HEAD"])

    file_path = request.GET.get("path")
    if not file_path:
        return None, None, None, JsonResponse({"error": "Missing 'path' query parameter"}, status=400)
    if "\n" in file_path or "\0" in file_path:
        # ``cat-file --batch-check`` reads one spec per line, so a newline in the
        # path would smuggle a second lookup past the confinement check.
        return None, None, None, JsonResponse({"error": "Invalid 'path' parameter"}, status=400)

    ref = request.GET.get("ref") or "index"
    if ref != "index" and not is_valid_commit_ref(ref):
        return None, None, None, JsonResponse({"error": "Invalid 'ref' parameter"}, status=400)

    requested_git_dir = request.GET.get("git_dir")
    git_directory = await _resolve_session_git_directory(
        project_id, session_id, requested_git_dir=requested_git_dir
    )
    return git_directory, file_path, ref, None


async def git_file_download(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-file-download/

    Download a file's content as it stands at ``ref``.

    Query params:
        path: File path relative to the git root.
        ref: ``index`` (default) or a commit hash.
        git_dir: Optional git root, validated against the session's known roots.

    At ``index`` the working-tree file is served, which is what the diff view
    shows as "modified"; a file deleted from the working tree falls back to its
    HEAD content. At a commit the blob comes from that commit, or from its
    parent when the commit deleted the file.
    """
    from twicc.git import GitError, blob_exists_at_ref, stream_blob_at_ref, validate_path_in_repo

    git_directory, file_path, ref, error = await _resolve_git_download(request, project_id, session_id)
    if error:
        return error

    def resolve():
        if ref == "index":
            abs_path = validate_path_in_repo(git_directory, file_path)
            if os.path.isfile(abs_path):
                return "disk", abs_path
            # Deleted from the working tree — serve the version it replaced.
            if blob_exists_at_ref(git_directory, "HEAD", file_path):
                return "git", "HEAD"
            return None, None
        if blob_exists_at_ref(git_directory, ref, file_path):
            return "git", ref
        # Deleted by that commit — its content only exists in the parent.
        if blob_exists_at_ref(git_directory, f"{ref}^", file_path):
            return "git", f"{ref}^"
        return None, None

    try:
        source, located = await asyncio.to_thread(resolve)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=400)

    filename = os.path.basename(file_path)
    if source == "disk":
        # validate_path is not involved here: _resolve_session_git_directory
        # already pinned the repo, and validate_path_in_repo (inside the git
        # helpers) confines the path to it. Serve the working-tree file itself.
        response = await asyncio.to_thread(_raw_file_response, located, as_attachment=True)
        if response is None:
            raise Http404("File not found")
        return response
    if source != "git":
        raise Http404("File not found at this revision")

    try:
        stream = await asyncio.to_thread(stream_blob_at_ref, git_directory, located, file_path)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return _git_download_response(stream, filename, _guess_raw_content_type(file_path))


async def git_diff_download(request, project_id, session_id=None):
    """GET /api/projects/<id>/[sessions/<session_id>/]git-diff-download/

    Download the unified patch for one file, as a ``.patch`` attachment.

    Query params:
        path: File path relative to the git root.
        ref: ``index`` (default, diff against HEAD) or a commit hash.
        git_dir: Optional git root, validated against the session's known roots.
    """
    from twicc.git import GitError, stream_commit_patch, stream_index_patch

    git_directory, file_path, ref, error = await _resolve_git_download(request, project_id, session_id)
    if error:
        return error

    try:
        if ref == "index":
            stream = await asyncio.to_thread(stream_index_patch, git_directory, file_path)
        else:
            stream = await asyncio.to_thread(stream_commit_patch, git_directory, ref, file_path)
    except GitError as e:
        return JsonResponse({"error": str(e)}, status=400)

    filename = f"{os.path.basename(file_path)}.patch"
    return _git_download_response(stream, filename, "text/x-patch")


_WEEKLY_ACTIVITY_MAX_WEEKS = 52


def _format_weekly_activity(rows, current_monday):
    """Format sparse WeeklyActivity rows into a dense list with zero-filling.

    Args:
        rows: Iterable of dicts with "date" (date object), "user_message_count",
            "session_count" and "cost" keys.
        current_monday: The Monday of the current week.

    Returns:
        List of dicts with "date" (ISO date string), "user_message_count",
        "session_count" and "cost" keys.
        Leading zero-activity weeks are trimmed, with up to 3 padding weeks.
    """
    data_by_week = {
        row["date"]: (row["user_message_count"], row.get("session_count", 0), row.get("cost", 0))
        for row in rows
    }

    start_monday = current_monday - timedelta(weeks=_WEEKLY_ACTIVITY_MAX_WEEKS - 1)

    # Find the first week with data to skip leading zeros
    first_active_monday = None
    for i in range(_WEEKLY_ACTIVITY_MAX_WEEKS):
        monday = start_monday + timedelta(weeks=i)
        user_message_count, session_count, cost = data_by_week.get(monday, (0, 0, 0))
        if user_message_count > 0 or session_count > 0 or cost:
            first_active_monday = monday
            break

    if first_active_monday is None:
        return []

    # Build result from first active week to current week
    result = []
    monday = first_active_monday
    while monday <= current_monday:
        user_message_count, session_count, cost = data_by_week.get(monday, (0, 0, 0))
        result.append({
            "date": monday.isoformat(),
            "user_message_count": user_message_count,
            "session_count": session_count,
            "cost": str(cost) if cost else "0",
        })
        monday += timedelta(weeks=1)

    # Pad with leading zero-activity weeks (up to 3) to approach max weeks
    padding = min(_WEEKLY_ACTIVITY_MAX_WEEKS - len(result), 3)
    for i in range(padding, 0, -1):
        result.insert(0, {
            "date": (first_active_monday - timedelta(weeks=i)).isoformat(),
            "user_message_count": 0,
            "session_count": 0,
            "cost": "0",
        })

    return result


async def home_data(request):
    """GET /api/home/ - Home page data: projects with weekly activity.

    Activity is summed across every provider — the home page shows a
    high-level overview, so per-provider filtering would not be
    meaningful here. The per-project detail pages use
    ``/api/daily-activity/`` instead, which exposes a ``provider``
    query param for that purpose.

    Returns:
        {
            "projects": [ { ...project..., "weekly_activity": [...] }, ... ],
            "global_weekly_activity": [ { "date": "...", "user_message_count": N, "session_count": N }, ... ]
        }
    """
    from django.db.models import Sum

    today = timezone.now().date()
    current_monday = today - timedelta(days=today.weekday())
    cutoff = current_monday - timedelta(weeks=_WEEKLY_ACTIVITY_MAX_WEEKS - 1)

    projects = await sync_to_async(list)(Project.objects.all())

    # Load all weekly activities in a single query (within the 52-week window).
    # Always aggregate per (project, date) so multi-provider rows collapse
    # into one entry per project/date.
    all_activities = await sync_to_async(list)(
        WeeklyActivity.objects
        .filter(date__gte=cutoff)
        .values("project_id", "date")
        .annotate(
            user_message_count=Sum("user_message_count"),
            session_count=Sum("session_count"),
            cost=Sum("cost"),
        )
    )

    # Group by project_id (None = global)
    from collections import defaultdict

    activities_by_project = defaultdict(list)
    for a in all_activities:
        activities_by_project[a["project_id"]].append(a)

    data = []
    for p in projects:
        d = serialize_project(p)
        d["weekly_activity"] = _format_weekly_activity(
            activities_by_project.get(p.id, []), current_monday
        )
        data.append(d)

    global_activity = _format_weekly_activity(
        activities_by_project.get(None, []), current_monday
    )

    return JsonResponse({
        "projects": data,
        "global_weekly_activity": global_activity,
    })


_DAILY_ACTIVITY_MAX_DAYS = 365


async def daily_activity(request, project_id=None):
    """GET /api/daily-activity/ or /api/projects/<id>/daily-activity/

    Returns daily activity data for the contribution graph, plus all-time totals.
    Sparse format: only days with activity are returned.
    The frontend heatmap component handles missing days as empty cells.

    If project_id is provided, returns per-project data.
    If project_id is omitted, returns global data (all projects).

    Query params (optional, only for /api/daily-activity/):
        project_ids: Comma-separated list of project IDs to filter by (e.g. workspace projects).
                     When provided, aggregates activity across the specified projects.
        provider: Optional. Backend provider key (e.g. ``claude_code``) to
                  scope the activity to a single provider. When omitted,
                  counts and costs are summed across every provider.

    Returns:
        {
            "daily_activity": [ { "date": "YYYY-MM-DD", "user_message_count": N, "session_count": N, "cost": "X.XX" }, ... ],
            "totals": { "user_message_count": N, "session_count": N, "cost": "X.XX" }
        }
    """
    from django.db.models import Sum

    provider_str = request.GET.get("provider")
    if provider_str:
        try:
            provider_filter = {"provider": Provider(provider_str).value}
        except ValueError:
            return JsonResponse({"error": f"Unknown provider: {provider_str!r}."}, status=400)
    else:
        provider_filter = {}

    today = timezone.now().date()
    cutoff = today - timedelta(days=_DAILY_ACTIVITY_MAX_DAYS - 1)

    # Determine filtering: specific project, set of projects (workspace), or global
    project_ids_param = request.GET.get("project_ids") if not project_id else None
    if project_id:
        project_filters = {"project_id": project_id}
    elif project_ids_param:
        project_filters = {"project_id__in": project_ids_param.split(",")}
    else:
        project_filters = {"project__isnull": True}

    # Always aggregate per date so multi-provider rows collapse into one
    # entry per day. With ``?provider=`` the sum runs over a single row
    # (still correct), or a single project's row in the per-project case.
    qs = DailyActivity.objects.filter(date__gte=cutoff, **project_filters, **provider_filter)
    rows = await sync_to_async(list)(
        qs.values("date").annotate(
            user_message_count=Sum("user_message_count"),
            session_count=Sum("session_count"),
            cost=Sum("cost"),
        ).order_by("date")
    )

    # All-time totals (no date filter)
    totals = await DailyActivity.objects.filter(**project_filters, **provider_filter).aaggregate(
        total_user_message_count=Sum("user_message_count"),
        total_session_count=Sum("session_count"),
        total_cost=Sum("cost"),
    )

    return JsonResponse({
        "daily_activity": [
            {
                "date": row["date"].isoformat(),
                "user_message_count": row["user_message_count"],
                "session_count": row["session_count"],
                "cost": str(row["cost"]) if row["cost"] else "0",
            }
            for row in rows
        ],
        "totals": {
            "user_message_count": totals["total_user_message_count"] or 0,
            "session_count": totals["total_session_count"] or 0,
            "cost": str(totals["total_cost"] or 0),
        },
    })


async def search_sessions(request):
    """GET /api/search/ - Full-text search across session messages."""
    if not search.is_initialized():
        return JsonResponse({"error": "Search index not ready"}, status=503)

    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"error": "Missing required parameter: q"}, status=400)

    project_id = request.GET.get("project_id")
    project_ids = request.GET.getlist("project_ids")
    session_id = request.GET.get("session_id")

    from_role = request.GET.get("from")
    if from_role is not None and from_role not in ("user", "assistant", "title"):
        return JsonResponse({"error": "Invalid 'from' parameter: must be 'user', 'assistant', or 'title'"}, status=400)

    after = None
    before = None
    try:
        if raw_after := request.GET.get("after"):
            after = datetime.fromisoformat(raw_after)
    except ValueError:
        return JsonResponse({"error": f"Invalid 'after' parameter: {raw_after!r} is not a valid ISO datetime"}, status=400)
    try:
        if raw_before := request.GET.get("before"):
            before = datetime.fromisoformat(raw_before)
    except ValueError:
        return JsonResponse({"error": f"Invalid 'before' parameter: {raw_before!r} is not a valid ISO datetime"}, status=400)

    include_archived = request.GET.get("include_archived", "").lower() in ("true", "1", "yes")

    try:
        limit = min(int(request.GET.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20
    try:
        offset = int(request.GET.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0

    try:
        results = await asyncio.to_thread(
            search.search,
            q,
            project_id=project_id,
            project_ids=project_ids or None,
            session_id=session_id,
            from_role=from_role,
            after=after,
            before=before,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return JsonResponse({"error": f"Search failed: {exc}"}, status=500)

    # Enrich results with session titles and project names from DB
    session_ids = [sr.session_id for sr in results.results]
    sessions_info = {}
    if session_ids:
        enriched_sessions = await sync_to_async(list)(
            Session.objects.filter(id__in=session_ids, hidden=False).select_related("project")
        )
        for s in enriched_sessions:
            sessions_info[s.id] = {
                "title": s.title or "",
                "project_id": s.project_id or "",
                "project_name": s.project.name if s.project else "",
                "archived": s.archived,
            }

    return JsonResponse({
        "query": q,
        "total_sessions": results.total_sessions,
        "results": [
            {
                "session_id": sr.session_id,
                "session_title": sessions_info.get(sr.session_id, {}).get("title", ""),
                "project_id": sessions_info.get(sr.session_id, {}).get("project_id", ""),
                "project_name": sessions_info.get(sr.session_id, {}).get("project_name", ""),
                "archived": sessions_info.get(sr.session_id, {}).get("archived", False),
                "score": sr.score,
                "matches": [
                    {
                        "line_num": m.line_num,
                        "from": m.from_role,
                        "snippet": m.snippet,
                        "score": m.score,
                        "timestamp": m.timestamp,
                    }
                    for m in sr.matches
                ],
            }
            for sr in results.results
        ],
    })


async def usage_history(request):
    """GET /api/usage-history/?provider=<key>&range_days=30&bucket_minutes=60&before=...

    Returns historical usage snapshots for charting utilization and burn rate over time.
    Both five_hour and seven_day data are returned in a single response.

    Parameters:
        provider: Required. Backend provider key (e.g. ``claude_code``). 400 when
            missing or unknown — there is no implicit default since multiple
            providers can track usage independently.
        range_days: Number of days to look back (default 30, 0.25–1825).
        bucket_minutes: Aggregation bucket size in minutes (default 0 = raw data).
            Allowed: 0, 30, 60, 300, 720, 1440.
            When > 0, snapshots are grouped into time buckets and the max value
            of each metric is kept per bucket.
        before: Optional ISO datetime. When present, the range ends at this time
            instead of now. Used for panning the usage history chart into the past.
    """
    ALLOWED_BUCKETS = {0, 30, 60, 300, 720, 1440}

    provider_str = request.GET.get("provider")
    if not provider_str:
        return JsonResponse({"error": "provider is required."}, status=400)
    try:
        provider = Provider(provider_str)
    except ValueError:
        return JsonResponse({"error": f"Unknown provider: {provider_str!r}."}, status=400)

    try:
        range_days = float(request.GET.get("range_days", "30"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "range_days must be a number."}, status=400)
    if range_days < 0.25 or range_days > 1825:
        return JsonResponse({"error": "range_days must be between 0.25 and 1825."}, status=400)

    try:
        bucket_minutes = int(request.GET.get("bucket_minutes", "0"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "bucket_minutes must be an integer."}, status=400)
    if bucket_minutes not in ALLOWED_BUCKETS:
        return JsonResponse({"error": f"bucket_minutes must be one of {sorted(ALLOWED_BUCKETS)}."}, status=400)

    before_str = request.GET.get("before")
    if before_str:
        try:
            end = datetime.fromisoformat(before_str)
            if end.tzinfo is None:
                end = timezone.make_aware(end)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid 'before' datetime format."}, status=400)
    else:
        end = timezone.now()

    cutoff = end - timedelta(days=range_days)

    snapshots = await sync_to_async(list)(
        UsageSnapshot.objects
        .filter(provider=provider.value)
        .filter(fetched_at__gte=cutoff, fetched_at__lte=end)
        .order_by("fetched_at")
    )

    FIVE_HOURS_S = 5 * 3600
    ONE_HOUR_S = 3600
    THIRTY_MIN_S = 1800
    SEVEN_DAYS_S = 7 * 86400
    ONE_DAY_S = 86400
    TWELVE_HOURS_S = 12 * 3600

    # Recent burn rate: compares current utilization with a reference snapshot
    # from ~lookback_seconds ago, measuring consumption rate over that recent interval.
    # Mirrors the frontend recentBurnRate() in utils/usage.js.
    # Formula: (delta_utilization) / ((delta_time / window) * 100)
    # When a reset boundary is crossed (delta_util < 0), computes a cross-period
    # rate by summing consumption from the old and new periods.
    def _compute_recent_rates(snaps, epochs, lookback_seconds, window_seconds, util_field, resets_at_field):
        n = len(snaps)
        rates = [None] * n
        for i in range(n):
            target = epochs[i] - lookback_seconds
            # Binary search for closest snapshot to target, only looking before i
            pos = bisect_left(epochs, target, 0, i)
            best_idx = None
            best_dist = float("inf")
            for candidate in (pos - 1, pos):
                if 0 <= candidate < i:
                    dist = abs(epochs[candidate] - target)
                    if dist < best_dist:
                        best_idx = candidate
                        best_dist = dist
            if best_idx is None or best_dist > lookback_seconds:
                continue
            current_util = getattr(snaps[i], util_field)
            ref_util = getattr(snaps[best_idx], util_field)
            if current_util is None or ref_util is None:
                continue
            delta_util = current_util - ref_util
            delta_seconds = epochs[i] - epochs[best_idx]
            if delta_seconds <= 0:
                continue
            delta_time_pct = (delta_seconds / window_seconds) * 100
            if delta_time_pct <= 0:
                continue

            if delta_util >= 0:
                # Normal intra-period
                rates[i] = delta_util / delta_time_pct
            else:
                # Cross-period: a reset happened between ref and current.
                # Find prev_end (last snapshot before the current window started)
                # and sum old-period + new-period consumption.
                resets_at = getattr(snaps[i], resets_at_field)
                if resets_at is None:
                    continue
                window_start_epoch = (resets_at - timedelta(seconds=window_seconds)).timestamp()
                # Last snapshot before the period boundary
                boundary_pos = bisect_left(epochs, window_start_epoch, best_idx, i)
                prev_end_idx = boundary_pos - 1
                if prev_end_idx < best_idx:
                    continue
                prev_end_util = getattr(snaps[prev_end_idx], util_field)
                if prev_end_util is None:
                    continue
                old_consumption = prev_end_util - ref_util
                if old_consumption < 0:
                    continue  # another reset between ref and prev_end
                total_consumption = old_consumption + current_util
                rates[i] = total_consumption / delta_time_pct
        return rates

    # Precompute epoch timestamps and recent rates for all 4 intervals
    epochs = [s.fetched_at.timestamp() for s in snapshots]
    fh_recent_long_rates = _compute_recent_rates(snapshots, epochs, ONE_HOUR_S, FIVE_HOURS_S, "five_hour_utilization", "five_hour_resets_at")
    fh_recent_short_rates = _compute_recent_rates(snapshots, epochs, THIRTY_MIN_S, FIVE_HOURS_S, "five_hour_utilization", "five_hour_resets_at")
    sd_recent_long_rates = _compute_recent_rates(snapshots, epochs, ONE_DAY_S, SEVEN_DAYS_S, "seven_day_utilization", "seven_day_resets_at")
    sd_recent_short_rates = _compute_recent_rates(snapshots, epochs, TWELVE_HOURS_S, SEVEN_DAYS_S, "seven_day_utilization", "seven_day_resets_at")

    # Extract raw data points with both periods
    raw = []
    for i, s in enumerate(snapshots):
        fh_burn = s.five_hour_burn_rate
        sd_burn = s.seven_day_burn_rate
        fh_rl = fh_recent_long_rates[i]
        fh_rs = fh_recent_short_rates[i]
        sd_rl = sd_recent_long_rates[i]
        sd_rs = sd_recent_short_rates[i]
        raw.append({
            "fetched_at": s.fetched_at,
            "fh_utilization": s.five_hour_utilization,
            "fh_burn_rate": round(fh_burn * 100, 1) if fh_burn is not None else None,
            "fh_recent_long": round(fh_rl * 100, 1) if fh_rl is not None else None,
            "fh_recent_short": round(fh_rs * 100, 1) if fh_rs is not None else None,
            "fh_temporal_pct": round(s.five_hour_temporal_pct, 1) if s.five_hour_temporal_pct is not None else None,
            "sd_utilization": s.seven_day_utilization,
            "sd_burn_rate": round(sd_burn * 100, 1) if sd_burn is not None else None,
            "sd_recent_long": round(sd_rl * 100, 1) if sd_rl is not None else None,
            "sd_recent_short": round(sd_rs * 100, 1) if sd_rs is not None else None,
            "sd_temporal_pct": round(s.seven_day_temporal_pct, 1) if s.seven_day_temporal_pct is not None else None,
        })

    # All metric keys (used for bucket aggregation and serialization)
    _METRIC_KEYS = (
        "fh_utilization", "fh_burn_rate",
        "fh_recent_long", "fh_recent_short", "fh_temporal_pct",
        "sd_utilization", "sd_burn_rate",
        "sd_recent_long", "sd_recent_short", "sd_temporal_pct",
    )

    # Aggregate into buckets if requested
    if bucket_minutes > 0 and raw:
        bucket_seconds = bucket_minutes * 60
        buckets = {}  # bucket_start_epoch -> aggregated values
        for point in raw:
            epoch = point["fetched_at"].timestamp()
            bucket_key = int(epoch // bucket_seconds) * bucket_seconds
            if bucket_key not in buckets:
                buckets[bucket_key] = {"epoch": bucket_key, **{k: point[k] for k in _METRIC_KEYS}}
            else:
                b = buckets[bucket_key]
                # Keep max of each metric (treating None as absent)
                for key in _METRIC_KEYS:
                    old_val = b[key]
                    new_val = point[key]
                    if new_val is not None:
                        b[key] = max(old_val, new_val) if old_val is not None else new_val

        # Convert back to sorted list with datetime
        aggregated = []
        for bucket_key in sorted(buckets):
            b = buckets[bucket_key]
            entry = {"fetched_at": datetime.fromtimestamp(b["epoch"], tz=timezone.get_current_timezone())}
            entry.update({k: b[k] for k in _METRIC_KEYS})
            aggregated.append(entry)
        raw = aggregated

    # Serialize
    data = []
    for point in raw:
        entry = {"fetched_at": point["fetched_at"].isoformat()}
        entry.update({k: point[k] for k in _METRIC_KEYS})
        data.append(entry)

    return JsonResponse({"snapshots": data})


async def bootstrap(request):
    """GET /api/bootstrap/ - All data needed before the app can mount.

    Returns synced settings (with defaults and categories), workspaces,
    terminal config, and message snippets in a single response so the
    frontend doesn't have to wait for the WebSocket connection.
    """
    from twicc.help_manifest import manifest_to_dict as help_manifest_to_dict
    from twicc.message_snippets import read_message_snippets_config
    from twicc.seen_help import read_seen_help
    from twicc.seen_tips import read_seen_tips
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS, prepare_settings_for_client, read_synced_settings
    from twicc.terminal_config import read_terminal_config
    from twicc.tips_manifest import manifest_to_dict
    from twicc.workspaces import read_workspaces

    # Bootstrap reads several user config / settings JSON files plus
    # workspace state — none of it is hot, but file I/O on the event loop
    # would still block ASGI. Hop into a worker thread for the lot.
    raw_settings = await asyncio.to_thread(read_synced_settings)
    clean_settings, version = prepare_settings_for_client(raw_settings)
    disabled_providers_present = "disabledProviders" in raw_settings
    disabled_providers = (raw_settings.get("disabledProviders") or []) if disabled_providers_present else []
    from twicc.providers.state import get_all_provider_states
    provider_states = get_all_provider_states()
    workspaces_data = await asyncio.to_thread(read_workspaces)
    terminal_config = await asyncio.to_thread(read_terminal_config)
    message_snippets = await asyncio.to_thread(read_message_snippets_config)
    seen_tips = await asyncio.to_thread(read_seen_tips)
    tips_manifest = await asyncio.to_thread(manifest_to_dict)
    seen_help = await asyncio.to_thread(read_seen_help)
    help_manifest = await asyncio.to_thread(help_manifest_to_dict)
    # ``helpers.get_bootstrap_data()`` does sync FS reads and sync ORM
    # work per provider — build the whole map in one worker thread hop.
    providers_data = await asyncio.to_thread(
        lambda: {
            provider.value: helpers.get_bootstrap_data()
            for provider, helpers in get_provider_helpers_registry().items()
        }
    )
    # Model benchmark rows (every provider/model/effort). The frontend needs the
    # COMPLETE set to normalise scores over the whole dataset, so ship them all.
    benchmarks = await asyncio.to_thread(lambda: list(ModelBenchmark.objects.all()))
    # Filter out agent-settings fields hidden from the frontend. ``get_bootstrap_data``
    # itself returns the full classification (so in-process consumers — notably the
    # CLI via ``load_local_bootstrap`` — can still see hidden fields as supported and
    # accept them as overrides); the trim happens here on the HTTP boundary, the only
    # path that ships ``agent_settings_categories`` to the frontend.
    from twicc.providers.helpers import AGENT_SETTINGS_HIDDEN_FROM_FRONTEND
    for provider_data in providers_data.values():
        categories = provider_data.get("agent_settings_categories")
        if not categories:
            continue
        provider_data["agent_settings_categories"] = {
            category: [k for k in keys if k not in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND]
            for category, keys in categories.items()
        }
    return JsonResponse({
        "settings": clean_settings,
        "settings_version": version,
        "default_settings": SYNCED_SETTINGS_DEFAULTS,
        "dev_mode": settings.DEV_MODE,
        "uvx_mode": settings.UVX_MODE,
        "twicc_launch_prefix": settings.TWICC_LAUNCH_PREFIX,
        "claudeHybridEnabled": settings.CLAUDE_HYBRID_ENABLED,
        "workspaces": workspaces_data.get("workspaces", []),
        "terminal_config": terminal_config,
        "message_snippets": message_snippets,
        "seen_tips": seen_tips,
        "tips_manifest": tips_manifest,
        "seen_help": seen_help,
        "help_manifest": help_manifest,
        "providers": providers_data,
        "benchmarks": [serialize_benchmark_row(b) for b in benchmarks],
        "disabledProvidersPresent": disabled_providers_present,
        "disabledProviders": disabled_providers,
        "providerStates": provider_states,
    })


async def changelog(request):
    """GET /api/changelog/ - Serve the local CHANGELOG.md (dev mode only)."""
    if not settings.DEV_MODE:
        raise Http404
    changelog_path = settings.PACKAGE_DIR.parent.parent / "CHANGELOG.md"
    if not changelog_path.is_file():
        raise Http404
    return HttpResponse(changelog_path.read_bytes(), content_type="text/plain; charset=utf-8")


# Image-only artifact serving. Extension → MIME type.
# Keep in sync with the list documented in the agent system prompt and the
# allowed_artifact_extensions block of any related skill.
ALLOWED_ARTIFACT_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# Filenames must start with an alphanumeric or underscore and may contain
# letters, digits, underscores, hyphens and dots after that. This rejects
# leading dots (hidden files, dot segments), spaces, slashes, backslashes
# and any other special character.
_ARTIFACT_FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def _classify_artifact_filename(filename: str) -> str | None:
    """Return the MIME type for a valid artifact filename, or ``None``.

    Rejects empty strings, ``.``/``..`` dot segments, paths containing
    slashes or backslashes, leading dots, and filenames whose extension is
    not in :data:`ALLOWED_ARTIFACT_EXTENSIONS`.
    """
    if not filename or "/" in filename or "\\" in filename:
        return None
    if filename in (".", ".."):
        return None
    if not _ARTIFACT_FILENAME_RE.match(filename):
        return None
    ext = os.path.splitext(filename)[1].lower()
    return ALLOWED_ARTIFACT_EXTENSIONS.get(ext)


def safe_open_artifact(artifacts_dir, filename):
    """Open ``artifacts_dir / filename`` for reading, or return ``None`` if it fails
    the security envelope: the resolved target must be a regular file living inside
    the resolved artifacts dir — no path traversal, no symlink escape, no
    directory/fifo/device/socket. Synchronous filesystem work; async callers offload
    it to a thread. Shared verbatim by :func:`session_artifact` and the public share
    media view so the guard can never diverge."""
    import stat

    target = artifacts_dir / filename
    try:
        # ``strict=True`` raises if the path doesn't exist; the dir itself may be a
        # symlink (e.g. worktree artifacts symlinked to the main data dir).
        resolved_dir = artifacts_dir.resolve(strict=True)
        resolved = target.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        return None
    # Defense in depth against symlinks pointing outside the artifacts dir.
    try:
        resolved.relative_to(resolved_dir)
    except ValueError:
        return None
    try:
        if not stat.S_ISREG(resolved.stat().st_mode):
            return None
        return resolved.open("rb")
    except OSError:
        return None


async def session_artifact(request, session_id, artifact_file_name):
    """Serve a session-scoped artifact image.

    Mounted at ``/artifacts/<session_id>/<artifact_file_name>``. Not under
    ``/api/`` (this serves media, not JSON) and not under any project URL
    (no project ownership is implied — artifacts can be surfaced anywhere
    in the UI). Files live on disk at
    ``<data_dir>/artifacts/<session_id>/<artifact_file_name>``. Only the
    images listed in :data:`ALLOWED_ARTIFACT_EXTENSIONS` are exposed.

    The security envelope:

    1. authentication is enforced by :class:`PasswordAuthMiddleware`, which
       lists ``/artifacts/`` alongside ``/api/`` as a protected path;
    2. the resolved file must live inside the session's artifacts directory
       (no symlink escapes, no path traversal);
    3. the file name must pass :func:`_classify_artifact_filename` (extension
       and shape).
    """
    from django.http import FileResponse

    from twicc.paths import get_session_artifacts_dir

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])

    content_type = _classify_artifact_filename(artifact_file_name)
    if content_type is None:
        raise Http404("Artifact not found")

    artifacts_dir = get_session_artifacts_dir(session_id)
    fp = await asyncio.to_thread(safe_open_artifact, artifacts_dir, artifact_file_name)
    if fp is None:
        raise Http404("Artifact not found")

    # ``as_attachment=False`` keeps the image inline so the browser renders
    # it directly inside the SPA (e.g. inside Markdown image tags).
    return FileResponse(fp, content_type=content_type, as_attachment=False)


# Project-icon buckets are opaque hashes: ``repo-<16 hex>`` (a repository's
# shared icon) or ``proj-<16 hex>`` (a per-project override). See
# twicc.project_icons and docs/plans/2026-07-17-project-icons-design.md.
_ICON_BUCKET_RE = re.compile(r"^(?:repo|proj)-[0-9a-f]{16}$")


async def project_icon(request, bucket, file_name):
    """Serve a project icon image.

    Mounted at ``/project-icons/<bucket>/<file_name>``. Like ``session_artifact``
    it serves media (not JSON) and is auth-gated by ``PasswordAuthMiddleware``
    via its protected non-API path list — served through Django, not BlackNoise,
    so the auth gate applies. Files live at
    ``<data_dir>/project-icons/<bucket>/<file_name>``. Same security envelope as
    artifacts: opaque bucket shape, extension+filename allowlist, and
    symlink/traversal confinement (:func:`safe_open_artifact`)."""
    from django.http import FileResponse

    from twicc.paths import get_project_icons_dir

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if not _ICON_BUCKET_RE.match(bucket):
        raise Http404("Project icon not found")
    content_type = _classify_artifact_filename(file_name)
    if content_type is None:
        raise Http404("Project icon not found")

    bucket_dir = get_project_icons_dir() / bucket
    fp = await asyncio.to_thread(safe_open_artifact, bucket_dir, file_name)
    if fp is None:
        raise Http404("Project icon not found")

    response = FileResponse(fp, content_type=content_type, as_attachment=False)
    response["X-Content-Type-Options"] = "nosniff"
    return response


async def external_notifications_test(request):
    """POST /api/external-notifications/test/ — send a test notification to Apprise URLs.

    Body: ``{"urls": ["<apprise url>", ...]}`` — the URL(s) as currently present
    in the settings form (not necessarily saved yet), so the user can verify a
    target before saving. Each URL is tested individually; the response reports
    per-URL results with privacy-masked URLs:
    ``{"results": [{"url_masked": ..., "ok": bool, "error": str|null}, ...]}``.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    urls = data.get("urls")
    if (
        not isinstance(urls, list)
        or not urls
        or not all(isinstance(u, str) and u.strip() for u in urls)
    ):
        return JsonResponse({"error": "'urls' must be a non-empty list of non-empty strings"}, status=400)
    from twicc.external_notifications import test_notification_urls

    results = await test_notification_urls([u.strip() for u in urls])
    return JsonResponse({"results": results})


async def artifact_bookmark_list(request):
    """GET /api/artifact-bookmarks/ — list all bookmarks.
    POST /api/artifact-bookmarks/ — create (or upsert) a bookmark."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        return await _create_artifact_bookmark(request)
    bookmarks = await sync_to_async(list)(ArtifactBookmark.objects.all())
    return JsonResponse({"bookmarks": [serialize_artifact_bookmark(b) for b in bookmarks]})


async def _create_artifact_bookmark(request):
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = (data.get("session_id") or "").strip()
    relative_path = (data.get("relative_path") or "").strip()
    name = (data.get("name") or "").strip()
    scope = data.get("scope")
    if not session_id or not relative_path or not name:
        return JsonResponse({"error": "session_id, relative_path and name are required"}, status=400)
    if scope not in PinMode.values:
        return JsonResponse({"error": "Invalid scope"}, status=400)

    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Confinement + existence (renderable-type is enforced client-side, design §4).
    # The write + broadcast live in the shared service so the CLI drop-request
    # path (kind="artifact_bookmark:upsert") stays byte-for-byte aligned.
    from twicc.core.services.artifact_bookmark_mutation import (
        confined_artifact_path,
        create_or_update_artifact_bookmark,
    )
    abs_path = confined_artifact_path(session_id, relative_path)
    if abs_path is None:
        return JsonResponse({"error": "Path escapes the artifacts directory"}, status=400)
    if not await sync_to_async(os.path.isfile)(abs_path):
        return JsonResponse({"error": "Artifact file not found"}, status=404)

    bookmark, created = await create_or_update_artifact_bookmark(
        session=session, relative_path=relative_path, name=name, scope=scope,
    )
    return JsonResponse(serialize_artifact_bookmark(bookmark), status=201 if created else 200)


async def artifact_bookmark_detail(request, bookmark_id):
    if request.method not in ("GET", "PATCH", "DELETE"):
        return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")

    # DELETE / PATCH writes + broadcasts live in the shared service (single
    # source of truth with the CLI drop-request path).
    from twicc.core.services.artifact_bookmark_mutation import (
        confined_artifact_path,
        delete_artifact_bookmark,
        patch_artifact_bookmark,
    )

    if request.method == "DELETE":
        await delete_artifact_bookmark(bookmark=bookmark)
        return JsonResponse({"ok": True})

    if request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        new_name = None
        new_scope = None
        if "name" in data:
            new_name = (data.get("name") or "").strip()
            if not new_name:
                return JsonResponse({"error": "name cannot be empty"}, status=400)
        if "scope" in data:
            if data["scope"] not in PinMode.values:
                return JsonResponse({"error": "Invalid scope"}, status=400)
            new_scope = data["scope"]
        await patch_artifact_bookmark(bookmark=bookmark, name=new_name, scope=new_scope)

    payload = serialize_artifact_bookmark(bookmark)
    if request.method == "GET":
        # Lazy availability check, on open (design §8/§9): stat the file now.
        # Kept OUT of the pure serializer; only the single-bookmark GET does I/O.
        abs_path = confined_artifact_path(bookmark.session_id, bookmark.relative_path)
        payload["available"] = bool(abs_path and await sync_to_async(os.path.isfile)(abs_path))
    return JsonResponse(payload)


async def artifact_bookmark_allowed_hosts(request, bookmark_id):
    """POST   /api/artifact-bookmarks/<id>/allowed-hosts/ — approve a host:port.
    DELETE /api/artifact-bookmarks/<id>/allowed-hosts/ — revoke one.

    Network-broker allowlist (design §6.4/§10). Body ``{"url": ...,
    "kind": "public"|"loopback"|"lan"}`` (``kind`` on POST only). The stored key
    is the normalized ``scheme://host:port``; ``metadata`` is never approvable.
    Browser-host only (human consent) — no CLI/drop-request surface. Returns the
    updated bookmark."""
    if request.method not in ("POST", "DELETE"):
        return HttpResponseNotAllowed(["POST", "DELETE"])
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    url = (data.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    from twicc.core.services.artifact_bookmark_mutation import (
        add_artifact_allowed_host,
        remove_artifact_allowed_host,
    )

    try:
        if request.method == "POST":
            kind = data.get("kind")
            if kind not in ("public", "loopback", "lan"):
                return JsonResponse({"error": "kind must be one of public/loopback/lan"}, status=400)
            await add_artifact_allowed_host(bookmark=bookmark, url=url, kind=kind)
        else:  # DELETE
            await remove_artifact_allowed_host(bookmark=bookmark, url=url)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(serialize_artifact_bookmark(bookmark))


async def artifact_bookmark_denied_hosts(request, bookmark_id):
    """POST   /api/artifact-bookmarks/<id>/denied-hosts/ — mark a host denied.
    DELETE /api/artifact-bookmarks/<id>/denied-hosts/ — un-deny one.

    The explicit owner "deny" decision (design N5), symmetric to allowed-hosts.
    Body {"url": ..., "kind": ...} on POST ({"url": ...} on DELETE); a stored
    host_key is a valid url value (normalize_host_key is idempotent on keys).
    Browser-host only (human decision) — no CLI/MCP surface. Returns the
    updated bookmark."""
    if request.method not in ("POST", "DELETE"):
        return HttpResponseNotAllowed(["POST", "DELETE"])
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    url = (data.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    from twicc.core.services.artifact_bookmark_mutation import (
        add_artifact_denied_host,
        remove_artifact_denied_host,
    )

    try:
        if request.method == "POST":
            kind = data.get("kind")
            if kind not in ("public", "loopback", "lan"):
                return JsonResponse({"error": "kind must be one of public/loopback/lan"}, status=400)
            await add_artifact_denied_host(bookmark=bookmark, url=url, kind=kind)
        else:  # DELETE
            await remove_artifact_denied_host(bookmark=bookmark, url=url)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(serialize_artifact_bookmark(bookmark))


async def artifact_bookmark_network_denials(request, bookmark_id):
    """GET  /api/artifact-bookmarks/<id>/network-denials/ — denial provenance
    rows (newest last_at first).
    POST /api/artifact-bookmarks/<id>/network-denials/ — record one owner-side
    preview denial event {"url", "kind"} (share=NULL, no ip/ua).

    Feeds the bookmark dialog's Network access list. Human-only; viewer-side
    rows are written by the share proxy, never here."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")

    if request.method == "GET":
        rows = await sync_to_async(lambda: list(
            ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark.id)
            .select_related("share").order_by("-last_at", "-id")
        ))()
        return JsonResponse({"denials": [serialize_network_denial(d) for d in rows]})

    # POST
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    url = (data.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)
    kind = data.get("kind")
    if kind not in ("public", "loopback", "lan"):
        return JsonResponse({"error": "kind must be one of public/loopback/lan"}, status=400)

    from twicc.artifacts.denial_tracking import record_owner_denial

    try:
        await record_owner_denial(bookmark=bookmark, url=url, kind=kind)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"ok": True})


async def artifact_redirect_to_slash(request, bookmark_id):
    """Redirect ``/artifacts/<id>`` → ``/artifacts/<id>/`` so the page's relative
    assets resolve under the bookmark's own directory rather than ``/artifacts/``."""
    return HttpResponseRedirect(f"/artifacts/{bookmark_id}/")


async def artifact_serve(request, bookmark_id, asset=""):
    """Serve a bookmarked artifact (and its sibling assets) for opening in a tab.

    ``/artifacts/<id>/``        → the bookmarked file itself (typically index.html)
    ``/artifacts/<id>/<asset>`` → a file resolved relative to the bookmark's
    directory, so the page's relative CSS/JS/image references load.

    Auth is enforced upstream by ``PasswordAuthMiddleware`` (it redirects
    unauthenticated requests to the standalone password page). Path confinement
    and byte serving reuse the same helpers as the file-raw endpoints, so nothing
    about rendering is duplicated here.

    ``PUT``/``DELETE`` (and a ``GET`` on a directory) additionally serve the
    artifact data store when the host-set ``X-Twicc-Artifact-Doc`` header names
    this bookmark's document — see :func:`_dispatch_data_request`.
    """
    if request.method not in ("GET", "HEAD", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "HEAD", "PUT", "DELETE"])
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")

    from twicc.artifacts.broker_html import ARTIFACT_INNER_DOC_PATH, artifact_shell_response
    from twicc.core.services.artifact_bookmark_mutation import confined_artifact_path

    abs_root = confined_artifact_path(bookmark.session_id, bookmark.relative_path)

    if request.method in ("PUT", "DELETE") or (
        request.method in ("GET", "HEAD") and request.headers.get("X-Twicc-Artifact-Doc")
    ):
        # The doc dir is intrinsic here (the bookmarked file's own directory);
        # the header only proves the request comes from THIS bookmark's document.
        doc_dir_fs = None
        if _doc_dir_from_header(request, prefix=f"/artifacts/{bookmark_id}/") is not None:
            doc_dir_fs = os.path.dirname(abs_root) if abs_root else None
        is_write = request.method in ("PUT", "DELETE")
        if is_write and doc_dir_fs is None:
            return HttpResponseNotAllowed(["GET", "HEAD"])
        if asset in ("", ARTIFACT_INNER_DOC_PATH):
            # The document itself, never part of the data store: a write here is
            # refused outright instead of falling through to the serving path.
            if is_write:
                return JsonResponse({"error": "outside_data"}, status=403)
        elif doc_dir_fs is not None:
            target = confined_artifact_path(
                bookmark.session_id, os.path.join(os.path.dirname(bookmark.relative_path), asset)
            )
            # confined_artifact_path realpaths a possibly-not-yet-existing file
            # fine; None here means escape from the session's artifacts dir.
            if is_write and target is None:
                return JsonResponse({"error": "outside_root"}, status=403)
            if target is not None:
                handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir_fs, target)
                if handled is not None:
                    return handled

    if asset == "":
        # Root request. An HTML artifact gets the trusted *shell* page (it iframes
        # the artifact's inner doc + mounts the broker), so the dedicated page
        # behaves exactly like the in-SPA preview (design §5/§9). A non-HTML
        # artifact has no broker need → served directly, unchanged.
        if abs_root is not None and _guess_raw_content_type(abs_root) == "text/html":
            return artifact_shell_response(
                bookmark_id=bookmark.id,
                allowed_hosts=bookmark.allowed_hosts,
                denied_hosts=bookmark.denied_hosts,
            )
        abs_path, as_document = abs_root, True
    elif asset == ARTIFACT_INNER_DOC_PATH:
        # The shell's iframe target: the artifact document itself, wrapped (shim
        # + strict CSP).
        abs_path, as_document = abs_root, True
    else:
        # A sibling asset, resolved relative to the bookmark's dir → streamed raw.
        rel = os.path.join(os.path.dirname(bookmark.relative_path), asset)
        abs_path, as_document = confined_artifact_path(bookmark.session_id, rel), False

    if abs_path is None:
        raise Http404("File not found")
    response = await asyncio.to_thread(_serve_artifact_file, abs_path, as_document=as_document)
    if response is None:
        raise Http404("File not found")
    return response


async def artifact_broker_shim(request):
    """Serve the built network-broker shim bundle (design §8), injected into
    artifact HTML documents at ``/_twicc/artifact-broker-shim.js``. Public
    (non-secret JS, same origin as the artifact); 404 until the frontend build
    has produced it (``npm run build``)."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    shim = settings.PACKAGE_DIR / "static" / "artifact-broker" / "shim.js"
    response = await asyncio.to_thread(_raw_file_response, str(shim))
    if response is None:
        raise Http404("Broker shim not built")
    return response


async def artifact_shell_asset(request, asset):
    """Serve the built artifact-*shell* bundle (phase 5) from
    ``static/artifact-shell/`` (``shell.js`` / ``shell.css``). Trusted same-origin
    TwiCC code — Vue + the shared broker composable/prompt — loaded by the
    dedicated artifact page. Public (non-secret); 404 until ``npm run build``
    produced it. The route captures a single path segment; confine defensively."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    base = (settings.PACKAGE_DIR / "static" / "artifact-shell").resolve()
    target = (base / asset).resolve()
    if not str(target).startswith(str(base) + os.sep):
        raise Http404("Not found")
    response = await asyncio.to_thread(_raw_file_response, str(target))
    if response is None:
        raise Http404("Artifact shell not built")
    return response


async def browser_companion_script(request):
    """Serve the browser-companion bundle (built by vite.config.companion.js).

    Loaded cross-origin by the user's OWN dev-server pages via a classic
    <script> tag, so it must stay reachable without TwiCC auth — like the
    broker shim, it relies on the middleware's non-API fallthrough (see
    auth/middleware.py); do not move it under /api/. 404 until ``npm run
    build`` produced it."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    script = settings.PACKAGE_DIR / "static" / "browser-companion" / "companion.js"
    response = await asyncio.to_thread(_raw_file_response, str(script))
    if response is None:
        raise Http404("Browser companion not built")
    return response


async def spa_index(request):
    """Catch-all for Vue Router - serves index.html."""
    index_path = settings.FRONTEND_DIST_DIR / "index.html"
    if not index_path.exists():
        raise Http404("Frontend not built. Run 'npm run build' in frontend/")
    content = index_path.read_bytes()
    etag = quote_etag(hashlib.sha256(content).hexdigest())
    response = HttpResponse(content, content_type="text/html")
    response["Cache-Control"] = "private, no-cache"
    response["ETag"] = etag
    return get_conditional_response(request, etag=etag, response=response)
