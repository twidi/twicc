"""Helpers for views that list instance-wide data: narrow it to the caller.

``AccessControlMiddleware`` only decides whether a member may *call* a global
route (:data:`~twicc.accounts.middleware.MEMBER_GLOBAL_ROUTES`); the view then
filters its own output with these helpers. Admins and the owner get
:data:`~twicc.accounts.project_access.UNRESTRICTED`, for which every helper is
the identity, so single-user behaviour is unchanged.
"""

from __future__ import annotations

from twicc.accounts.project_access import (
    UNRESTRICTED,
    ProjectAccess,
    get_project_access,
    get_project_access_sync,
)

# A project id no project can have: restricting to "nothing" through APIs
# whose empty list means "everything" (timeline, final messages, sessions).
NO_PROJECT_SENTINEL = "\x00no-project"


def _principal(request):
    return getattr(request, "twicc_principal", None)


async def request_access(request) -> ProjectAccess:
    principal = _principal(request)
    if principal is None:
        # Unauthenticated callers never reach a gated view; public views that
        # call this get the historical behaviour.
        return UNRESTRICTED
    return await get_project_access(principal)


def request_access_sync(request) -> ProjectAccess:
    principal = _principal(request)
    if principal is None:
        return UNRESTRICTED
    return get_project_access_sync(principal)


def restrict_project_ids(access: ProjectAccess, requested) -> list[str] | None:
    """Narrow an optional requested project-id list to the viewable ones.

    ``requested=None`` (no filter asked) → None when unrestricted, else every
    viewable id (or the sentinel when there is none, so "empty means all"
    APIs return nothing rather than everything).
    """
    if access.unrestricted:
        return None if requested is None else list(requested)
    if requested is None:
        ids = sorted(access.viewable)
    else:
        ids = [pid for pid in requested if pid in access.viewable]
    return ids or [NO_PROJECT_SENTINEL]


# Synced settings a member must not read: notification targets embed provider
# tokens in their Apprise URLs, and the others reveal host paths/endpoints.
MEMBER_HIDDEN_SETTINGS = frozenset({
    "externalNotificationTargets",
    "terminalTmuxConfigPath",
    "peerBaseUrl",
    "mcpBaseUrl",
})


def strip_member_settings(settings_payload: dict) -> dict:
    """A copy of the synced settings without the keys members may not read."""
    if not isinstance(settings_payload, dict):
        return settings_payload
    return {k: v for k, v in settings_payload.items() if k not in MEMBER_HIDDEN_SETTINGS}
