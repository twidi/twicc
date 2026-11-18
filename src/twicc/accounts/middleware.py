"""Per-project authorization for HTTP: deny by default for members.

Runs after ``PasswordAuthMiddleware`` (which resolved ``request.twicc_principal``)
and acts in ``process_view``, once the URL is resolved, so it sees the route
pattern and its kwargs. Admins (and the owner) pass through untouched.

For a **member** the rules are, in order:

1. ``/api/admin/…`` → refused.
2. The route carries a scope kwarg — ``project_id``, ``session_id`` /
   ``parent_session_id``, or ``bookmark_id`` — → the scoped project must be
   *viewable*; an unsafe method (POST/PUT/PATCH/DELETE) requires *operate*;
   a handful of project routes are admin-only whatever the grant
   (:data:`PROJECT_ADMIN_ROUTES`).
3. Otherwise the route must be listed in :data:`MEMBER_GLOBAL_ROUTES`
   (instance-wide views whose handler filters its own output by project);
   anything else — settings, peers, MCP, shares, standalone file access,
   usage, the artifact network proxy… — is refused.

Deny-by-default is the point: a route added later is admin-only until
someone decides how members may use it, instead of leaking silently.
"""

from __future__ import annotations

import logging

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.http import JsonResponse

from twicc.accounts.project_access import get_project_access
from twicc.core.ui_messages import ui_error

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Instance-wide routes a member may call; each view filters by project itself.
# Value = allowed methods (None = any).
MEMBER_GLOBAL_ROUTES: dict[str, frozenset[str] | None] = {
    "api/me/": SAFE_METHODS,
    "api/bootstrap/": SAFE_METHODS,
    "api/changelog/": SAFE_METHODS,
    "api/home/": SAFE_METHODS,
    "api/daily-activity/": SAFE_METHODS,
    "api/timeline/": SAFE_METHODS,
    "api/timeline/final-messages/": None,  # POST-for-read (session ids in the body, filtered)
    "api/sessions/": SAFE_METHODS,
    "api/search/": SAFE_METHODS,
    "api/projects/": SAFE_METHODS,  # list only; creating a project is admin-only
    "api/artifact-bookmarks/": SAFE_METHODS,
    "api/async-questions/existence/": None,
    # Composer attachments: random-id staging buckets the member's own composer fills.
    "api/composer-attachments/status/": None,
    "api/composer-attachments/touch/": None,
    "api/composer-attachments/<str:bucket>/<str:attachment_id>/": None,
    # Uploads: the view refuses a member's standalone (non-project) target.
    "api/uploads/": None,
    "api/uploads/<str:upload_id>/": None,
    "project-icons/<str:bucket>/<str:file_name>": SAFE_METHODS,
}

# Project routes reserved to admins whatever the member's grant: they change
# how agents run (trust), touch the filesystem outside the project files, or
# edit project-level metadata/defaults.
PROJECT_ADMIN_ROUTES: dict[str, frozenset[str] | None] = {
    "api/projects/<str:project_id>/": frozenset({"PUT", "PATCH", "DELETE", "POST"}),
    "api/projects/<str:project_id>/icon/": frozenset({"PUT", "PATCH", "DELETE", "POST"}),
    "api/projects/<str:project_id>/trust/decide/": None,
    "api/projects/<str:project_id>/recreate-directory/": None,
    "api/projects/<str:project_id>/refresh-directory/": None,
    "api/projects/<str:project_id>/worktrees/adopt/": None,
    "api/projects/<str:project_id>/worktrees/": frozenset({"POST", "PUT", "PATCH", "DELETE"}),
    "api/projects/<str:project_id>/sessions/<str:session_id>/archive-hidden-descendants/": None,
    # Native speech: ADMIN-only (see twicc.tts.native_consumer).
    "api/tts/native/<uuid:session_id>/audio/<str:audio_id>/": None,
    "api/tts/native/<uuid:session_id>/audio/<str:audio_id>/ending/": None,
}

# Session-scoped routes a member may READ but never write (whatever the grant).
_SESSION_READ_ONLY_ROUTES = frozenset({
    "api/sessions/<str:session_id>/",
})


def _forbidden():
    return JsonResponse(ui_error("Access denied", "backend.accounts.accessDenied"), status=403)


def _not_found():
    # Same answer as a missing object: never confirm that an invisible project exists.
    return JsonResponse(ui_error("Not found", "backend.common.notFound"), status=404)


def _session_project_ids(session_id: str) -> tuple[str | None, str | None] | None:
    from twicc.core.models import Session

    row = (
        Session.objects.filter(pk=session_id)
        .values_list("project_id", "parent_session__project_id")
        .first()
    )
    return row


def _bookmark_project_id(bookmark_id: int) -> str | None:
    from twicc.core.models import ArtifactBookmark

    return ArtifactBookmark.objects.filter(pk=bookmark_id).values_list("project_id", flat=True).first()


def _route_matches(table: dict, route: str, method: str) -> bool:
    if route not in table:
        return False
    methods = table[route]
    return methods is None or method in methods


class AccessControlMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        return await self.get_response(request)

    async def process_view(self, request, view_func, view_args, view_kwargs):
        principal = getattr(request, "twicc_principal", None)
        if principal is None or principal.is_admin:
            # None = a public path / the SPA shell (PasswordAuthMiddleware
            # already refused unauthenticated data paths).
            return None
        match = getattr(request, "resolver_match", None)
        route = match.route if match is not None else ""
        method = request.method.upper()

        if route.startswith("api/admin/"):
            return _forbidden()

        # Projects reached through the session/bookmark kwargs (any one of
        # them visible is enough); the URL project is checked on its own.
        project_ids: list[str | None] = []
        scoped = "project_id" in view_kwargs
        for key in ("session_id", "parent_session_id"):
            if key in view_kwargs:
                scoped = True
                row = await sync_to_async(_session_project_ids)(str(view_kwargs[key]))
                if row is None:
                    # Let the view answer its own 404 only when a project
                    # scope already vouched for the caller; else hide it.
                    if "project_id" not in view_kwargs:
                        return _not_found()
                    continue
                own, parent = row
                project_ids.append(own if own is not None else parent)
                if parent is not None and parent != own:
                    # A Codex subagent may live in another project than its
                    # parent: reachable through either.
                    project_ids.append(parent)
        if "bookmark_id" in view_kwargs:
            scoped = True
            project_ids.append(await sync_to_async(_bookmark_project_id)(view_kwargs["bookmark_id"]))

        if not scoped:
            if _route_matches(MEMBER_GLOBAL_ROUTES, route, method):
                return None
            return _forbidden()

        if _route_matches(PROJECT_ADMIN_ROUTES, route, method):
            return _forbidden()

        access = await get_project_access(principal)
        # Session routes inside a project URL check BOTH: the URL project and
        # the session's own project must be visible (no pairing a visible
        # project id with a foreign session id).
        url_project = view_kwargs.get("project_id")
        session_projects = project_ids
        if url_project is not None and not access.can_view(url_project):
            return _not_found()
        if session_projects and not any(access.can_view(pid) for pid in session_projects):
            return _not_found()

        if method not in SAFE_METHODS:
            if route in _SESSION_READ_ONLY_ROUTES:
                return _forbidden()
            targets = [url_project] if url_project is not None else session_projects
            if not any(access.can_operate(pid) for pid in targets):
                return _forbidden()
        return None
