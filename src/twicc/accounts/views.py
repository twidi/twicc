"""REST API for the current principal and for account administration.

- ``GET  /api/me/``                                  — who am I, and what can I reach
- ``GET  /api/admin/users/`` · ``POST``              — list / create accounts
- ``GET|PATCH|DELETE /api/admin/users/<id>/``        — one account
- ``POST /api/admin/users/<id>/revoke-sessions/``    — sign out everywhere
- ``PUT  /api/admin/users/<id>/grants/``             — replace all grants (matrix save)
- ``GET  /api/admin/projects/<project_id>/access/``  — who reaches this project, and how
- ``PUT  /api/admin/projects/<project_id>/access/<user_id>/``   — set/remove a direct grant
- ``GET  /api/admin/workspaces/<workspace_id>/access/``         — workspace grants
- ``PUT  /api/admin/workspaces/<workspace_id>/access/<user_id>/`` — set/remove one

Everything under ``/api/admin/`` is admin-only, enforced here *and* by
``AccessControlMiddleware`` (defence in depth). Mutations run under the shared
DB write lock, like every other TwiCC write.
"""

from __future__ import annotations

import orjson
from asgiref.sync import sync_to_async
from django.http import JsonResponse

from twicc.accounts import service
from twicc.accounts.project_access import get_project_access_sync, visible_workspace_ids
from twicc.core.ui_messages import ui_error
from twicc.providers.db_writer import run_under_db_write_lock

_ERROR_STATUS = {
    "user_not_found": 404,
    "project_not_found": 404,
    "workspace_not_found": 404,
    "duplicate_email": 409,
    "last_admin": 409,
}


def _principal(request):
    return getattr(request, "twicc_principal", None)


def _forbidden():
    return JsonResponse(ui_error("Admin access required", "backend.accounts.adminRequired"), status=403)


def _method_not_allowed():
    return JsonResponse(ui_error("Method not allowed", "backend.common.methodNotAllowed"), status=405)


def _parse_body(request) -> dict | None:
    try:
        data = orjson.loads(request.body or b"{}")
    except orjson.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _bad_json():
    return JsonResponse(ui_error("Invalid JSON", "backend.common.invalidJson"), status=400)


def _result_response(result: service.AccountResult, *, payload=None, status: int = 200):
    if not result.success:
        errors = [e._asdict() for e in result.errors or []]
        code = errors[0]["code"] if errors else "error"
        body = ui_error(errors[0]["message"] if errors else "Error", f"backend.accounts.{code}")
        body["errors"] = errors
        return JsonResponse(body, status=_ERROR_STATUS.get(code, 400))
    return JsonResponse(payload if payload is not None else {"ok": True}, status=status)


async def _write(fn, *args, **kwargs):
    return await run_under_db_write_lock(lambda: sync_to_async(fn)(*args, **kwargs))


def _require_admin(request):
    principal = _principal(request)
    return principal is not None and principal.is_admin


# --- /api/me/ ----------------------------------------------------------------


def _me_payload(principal) -> dict:
    access = get_project_access_sync(principal)
    payload = {"principal": principal.to_payload(), "unrestricted": access.unrestricted}
    if not access.unrestricted:
        payload["projects"] = {
            pid: ("operate" if pid in access.operable else "view") for pid in sorted(access.viewable)
        }
        payload["workspace_ids"] = sorted(visible_workspace_ids(principal, access) or [])
    return payload


async def me(request):
    if request.method != "GET":
        return _method_not_allowed()
    principal = _principal(request)
    if principal is None:
        return JsonResponse(ui_error("Authentication required", "backend.common.authenticationRequired"), status=401)
    return JsonResponse(await sync_to_async(_me_payload)(principal))


# --- users -------------------------------------------------------------------


def _list_users() -> list[dict]:
    from twicc.core.models import User

    users = User.objects.prefetch_related("project_grants", "workspace_grants")
    return [service.serialize_user(u, with_grants=True) for u in users]


async def users(request):
    if not _require_admin(request):
        return _forbidden()
    if request.method == "GET":
        return JsonResponse({"users": await sync_to_async(_list_users)()})
    if request.method != "POST":
        return _method_not_allowed()
    data = _parse_body(request)
    if data is None:
        return _bad_json()
    password = data.get("password") or None
    result = await _write(
        service.create_user,
        email=str(data.get("email", "")),
        display_name=str(data.get("display_name", "") or ""),
        role=str(data.get("role", "member")),
        password=str(password) if password else None,
    )
    if result.success:
        payload = await sync_to_async(service.serialize_user)(result.user, with_grants=True)
        return _result_response(result, payload={"user": payload}, status=201)
    return _result_response(result)


def _get_user_payload(user_id: int) -> dict | None:
    from twicc.core.models import User

    user = User.objects.filter(pk=user_id).first()
    return service.serialize_user(user, with_grants=True) if user else None


async def user_detail(request, user_id: int):
    if not _require_admin(request):
        return _forbidden()
    if request.method == "GET":
        payload = await sync_to_async(_get_user_payload)(user_id)
        if payload is None:
            return JsonResponse(ui_error("Account not found", "backend.accounts.user_not_found"), status=404)
        return JsonResponse({"user": payload})
    if request.method == "DELETE":
        principal = _principal(request)
        if principal.user_id == user_id:
            return JsonResponse(
                ui_error("You cannot delete your own account", "backend.accounts.cannot_delete_self"), status=409
            )
        return _result_response(await _write(service.delete_user, user_id))
    if request.method != "PATCH":
        return _method_not_allowed()
    data = _parse_body(request)
    if data is None:
        return _bad_json()
    principal = _principal(request)
    if principal.user_id == user_id and (
        ("role" in data and data["role"] != "admin") or data.get("is_active") is False
    ):
        return JsonResponse(
            ui_error("You cannot demote or disable your own account", "backend.accounts.cannot_demote_self"),
            status=409,
        )
    kwargs = {}
    for key in ("email", "display_name", "role"):
        if key in data and data[key] is not None:
            kwargs[key] = str(data[key])
    if "is_active" in data:
        kwargs["is_active"] = bool(data["is_active"])
    if data.get("password"):
        kwargs["password"] = str(data["password"])
    if data.get("clear_password"):
        kwargs["clear_password"] = True
    if data.get("unlink_google"):
        kwargs["unlink_google"] = True
    result = await _write(service.update_user, user_id, **kwargs)
    if result.success:
        payload = await sync_to_async(service.serialize_user)(result.user, with_grants=True)
        return _result_response(result, payload={"user": payload})
    return _result_response(result)


async def user_revoke_sessions(request, user_id: int):
    if not _require_admin(request):
        return _forbidden()
    if request.method != "POST":
        return _method_not_allowed()
    return _result_response(await _write(service.revoke_sessions, user_id))


async def user_grants(request, user_id: int):
    if not _require_admin(request):
        return _forbidden()
    if request.method != "PUT":
        return _method_not_allowed()
    data = _parse_body(request)
    if data is None:
        return _bad_json()
    project_grants = data.get("project_grants") or []
    workspace_grants = data.get("workspace_grants") or []
    if not isinstance(project_grants, list) or not isinstance(workspace_grants, list):
        return _bad_json()
    result = await _write(
        service.replace_grants,
        user_id,
        project_grants=[g for g in project_grants if isinstance(g, dict)],
        workspace_grants=[g for g in workspace_grants if isinstance(g, dict)],
    )
    if result.success:
        payload = await sync_to_async(_get_user_payload)(user_id)
        return _result_response(result, payload={"user": payload})
    return _result_response(result)


# --- per-project / per-workspace access ------------------------------------


def _parse_level(data: dict):
    level = data.get("level")
    return None if level in (None, "", "none") else str(level)


async def project_access(request, project_id: str, user_id: int | None = None):
    if not _require_admin(request):
        return _forbidden()
    if user_id is None:
        if request.method != "GET":
            return _method_not_allowed()
        return JsonResponse({"members": await sync_to_async(service.project_members)(project_id)})
    if request.method != "PUT":
        return _method_not_allowed()
    data = _parse_body(request)
    if data is None:
        return _bad_json()
    result = await _write(service.set_project_grant, user_id, project_id, _parse_level(data))
    if result.success:
        return JsonResponse({"members": await sync_to_async(service.project_members)(project_id)})
    return _result_response(result)


def _workspace_members(workspace_id: str) -> list[dict]:
    from twicc.core.models import User, UserRole, WorkspaceGrant

    grants = {g.user_id: g.level for g in WorkspaceGrant.objects.filter(workspace_id=workspace_id)}
    rows = []
    for user in User.objects.all():
        if user.role == UserRole.ADMIN or user.id in grants:
            rows.append({
                "user": service.serialize_user(user),
                "level": grants.get(user.id),
                "is_admin": user.role == UserRole.ADMIN,
            })
    return sorted(rows, key=lambda r: (not r["is_admin"], r["user"]["email"]))


async def workspace_access(request, workspace_id: str, user_id: int | None = None):
    if not _require_admin(request):
        return _forbidden()
    if user_id is None:
        if request.method != "GET":
            return _method_not_allowed()
        return JsonResponse({"members": await sync_to_async(_workspace_members)(workspace_id)})
    if request.method != "PUT":
        return _method_not_allowed()
    data = _parse_body(request)
    if data is None:
        return _bad_json()
    result = await _write(service.set_workspace_grant, user_id, workspace_id, _parse_level(data))
    if result.success:
        return JsonResponse({"members": await sync_to_async(_workspace_members)(workspace_id)})
    return _result_response(result)
