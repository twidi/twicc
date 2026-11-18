"""Account and grant mutations, shared by the admin REST API and the CLI.

Every function is synchronous (ORM + PBKDF2) — async callers wrap it with
``sync_to_async``/``asyncio.to_thread``. Each one validates its input and
returns an :class:`AccountResult` instead of raising, so the REST layer and the
CLI render the same structured errors.

Every mutation that changes what someone may see ends with
:func:`_after_access_change`, which drops the access cache and pushes the
change to connected clients (``accounts_changed`` WS event — each connection
re-resolves its own principal and access).
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from twicc.accounts.principal import invalidate_accounts_enabled
from twicc.accounts.project_access import invalidate_access_cache
from twicc.auth.hashers import hash_password

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_DISPLAY_NAME_LENGTH = 100


class AccountError(NamedTuple):
    field: str
    code: str
    message: str


class AccountResult(NamedTuple):
    success: bool
    user: object | None = None
    errors: list[AccountError] | None = None


def _fail(field: str, code: str, message: str) -> AccountResult:
    return AccountResult(success=False, errors=[AccountError(field, code, message)])


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> AccountError | None:
    if not email or len(email) > 254 or not _EMAIL_RE.match(email):
        return AccountError("email", "invalid_email", "Invalid email address.")
    return None


def validate_password(password: str | None) -> AccountError | None:
    if password is None:
        return None
    if len(password) < MIN_PASSWORD_LENGTH:
        return AccountError(
            "password", "password_too_short", f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    return None


def serialize_user(user, *, with_grants: bool = False) -> dict:
    data = {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "has_password": bool(user.password_hash),
        "google_linked": bool(user.google_sub),
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
    if with_grants:
        data["project_grants"] = [
            {"project_id": g.project_id, "level": g.level} for g in user.project_grants.all().order_by("project_id")
        ]
        data["workspace_grants"] = [
            {"workspace_id": g.workspace_id, "level": g.level}
            for g in user.workspace_grants.all().order_by("workspace_id")
        ]
    return data


def _after_access_change() -> None:
    invalidate_access_cache()
    invalidate_accounts_enabled()
    from twicc.accounts.notify import schedule_accounts_changed

    schedule_accounts_changed()


def _active_admin_count(exclude_user_id: int | None = None) -> int:
    from twicc.core.models import User, UserRole

    qs = User.objects.filter(role=UserRole.ADMIN, is_active=True)
    if exclude_user_id is not None:
        qs = qs.exclude(pk=exclude_user_id)
    return qs.count()


# --- accounts ---------------------------------------------------------------


def create_user(
    *, email: str, display_name: str = "", role: str = "member", password: str | None = None
) -> AccountResult:
    from twicc.core.models import User, UserRole

    email = normalize_email(email)
    errors = [e for e in (validate_email(email), validate_password(password)) if e]
    if role not in UserRole.values:
        errors.append(AccountError("role", "invalid_role", "Role must be 'admin' or 'member'."))
    display_name = (display_name or "").strip()
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        errors.append(AccountError("display_name", "display_name_too_long", "Display name is too long."))
    if errors:
        return AccountResult(success=False, errors=errors)
    try:
        user = User.objects.create(
            email=email,
            display_name=display_name,
            role=role,
            password_hash=hash_password(password) if password else "",
        )
    except IntegrityError:
        return _fail("email", "duplicate_email", "An account with this email already exists.")
    logger.info("Account created: %s (%s)", email, role)
    _after_access_change()
    return AccountResult(success=True, user=user)


def update_user(
    user_id: int,
    *,
    email: str | None = None,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    clear_password: bool = False,
    unlink_google: bool = False,
) -> AccountResult:
    """Partial update. Any change that affects authority bumps ``auth_epoch``."""
    from twicc.core.models import User, UserRole

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _fail("id", "user_not_found", "Account not found.")

    bump_epoch = False
    if email is not None:
        email = normalize_email(email)
        if err := validate_email(email):
            return AccountResult(success=False, errors=[err])
        if email != user.email:
            if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                return _fail("email", "duplicate_email", "An account with this email already exists.")
            user.email = email
            bump_epoch = True
    if display_name is not None:
        display_name = display_name.strip()
        if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
            return _fail("display_name", "display_name_too_long", "Display name is too long.")
        user.display_name = display_name
    if role is not None and role != user.role:
        if role not in UserRole.values:
            return _fail("role", "invalid_role", "Role must be 'admin' or 'member'.")
        if user.role == UserRole.ADMIN and user.is_active and _active_admin_count(exclude_user_id=user.pk) == 0:
            # The owner identity still exists only when an instance password is
            # set — without one, demoting the last admin would lock everybody out.
            from django.conf import settings

            if not settings.TWICC_PASSWORD_HASH:
                return _fail("role", "last_admin", "Cannot demote the last admin while no instance password is set.")
        user.role = role
        bump_epoch = True
    if is_active is not None and is_active != user.is_active:
        if not is_active and user.role == UserRole.ADMIN and _active_admin_count(exclude_user_id=user.pk) == 0:
            from django.conf import settings

            if not settings.TWICC_PASSWORD_HASH:
                return _fail(
                    "is_active", "last_admin", "Cannot disable the last admin while no instance password is set."
                )
        user.is_active = is_active
        bump_epoch = True
    if password is not None:
        if err := validate_password(password):
            return AccountResult(success=False, errors=[err])
        user.password_hash = hash_password(password)
        bump_epoch = True
    elif clear_password:
        user.password_hash = ""
        bump_epoch = True
    if unlink_google and user.google_sub:
        user.google_sub = None
        bump_epoch = True
    if bump_epoch:
        user.auth_epoch = F("auth_epoch") + 1
    user.save()
    user.refresh_from_db()
    _after_access_change()
    return AccountResult(success=True, user=user)


def revoke_sessions(user_id: int) -> AccountResult:
    """Sign an account out everywhere (bumps its epoch)."""
    from twicc.core.models import User

    updated = User.objects.filter(pk=user_id).update(auth_epoch=F("auth_epoch") + 1)
    if not updated:
        return _fail("id", "user_not_found", "Account not found.")
    _after_access_change()
    return AccountResult(success=True, user=User.objects.get(pk=user_id))


def delete_user(user_id: int) -> AccountResult:
    from django.conf import settings

    from twicc.core.models import User, UserRole

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _fail("id", "user_not_found", "Account not found.")
    if (
        user.role == UserRole.ADMIN
        and user.is_active
        and _active_admin_count(exclude_user_id=user.pk) == 0
        and not settings.TWICC_PASSWORD_HASH
        and User.objects.exclude(pk=user.pk).filter(is_active=True).exists()
    ):
        return _fail("id", "last_admin", "Cannot delete the last admin while other accounts remain.")
    user.delete()
    logger.info("Account deleted: %s", user.email)
    _after_access_change()
    return AccountResult(success=True, user=None)


def record_login(user) -> None:
    from twicc.core.models import User

    now = timezone.now()
    User.objects.filter(pk=user.pk).update(last_login_at=now)
    user.last_login_at = now


# --- grants -----------------------------------------------------------------


def set_project_grant(user_id: int, project_id: str, level: str | None) -> AccountResult:
    """Create/update (``level``) or remove (``level=None``) a direct project grant."""
    from twicc.core.models import AccessLevel, Project, ProjectGrant, User

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _fail("user_id", "user_not_found", "Account not found.")
    if level is None:
        ProjectGrant.objects.filter(user=user, project_id=project_id).delete()
    else:
        if level not in AccessLevel.values:
            return _fail("level", "invalid_level", "Level must be 'view' or 'operate'.")
        if not Project.objects.filter(pk=project_id).exists():
            return _fail("project_id", "project_not_found", "Project not found.")
        ProjectGrant.objects.update_or_create(user=user, project_id=project_id, defaults={"level": level})
    _after_access_change()
    return AccountResult(success=True, user=user)


def set_workspace_grant(user_id: int, workspace_id: str, level: str | None) -> AccountResult:
    from twicc.core.models import AccessLevel, User, WorkspaceGrant
    from twicc.workspaces import read_workspaces

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _fail("user_id", "user_not_found", "Account not found.")
    if level is None:
        WorkspaceGrant.objects.filter(user=user, workspace_id=workspace_id).delete()
    else:
        if level not in AccessLevel.values:
            return _fail("level", "invalid_level", "Level must be 'view' or 'operate'.")
        known = {ws.get("id") for ws in read_workspaces().get("workspaces", []) if isinstance(ws, dict)}
        if workspace_id not in known:
            return _fail("workspace_id", "workspace_not_found", "Workspace not found.")
        WorkspaceGrant.objects.update_or_create(user=user, workspace_id=workspace_id, defaults={"level": level})
    _after_access_change()
    return AccountResult(success=True, user=user)


def replace_grants(user_id: int, *, project_grants: list[dict], workspace_grants: list[dict]) -> AccountResult:
    """Replace every grant of an account at once (the admin matrix "Save")."""
    from twicc.core.models import AccessLevel, Project, ProjectGrant, User, WorkspaceGrant
    from twicc.workspaces import read_workspaces

    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return _fail("user_id", "user_not_found", "Account not found.")

    known_ws = {ws.get("id") for ws in read_workspaces().get("workspaces", []) if isinstance(ws, dict)}
    p_rows: dict[str, str] = {}
    for g in project_grants:
        pid, level = g.get("project_id"), g.get("level")
        if level not in AccessLevel.values:
            return _fail("project_grants", "invalid_level", "Level must be 'view' or 'operate'.")
        p_rows[pid] = level
    existing_projects = set(Project.objects.filter(pk__in=list(p_rows)).values_list("id", flat=True))
    if missing := set(p_rows) - existing_projects:
        return _fail("project_grants", "project_not_found", f"Unknown project(s): {', '.join(sorted(missing))}")
    w_rows: dict[str, str] = {}
    for g in workspace_grants:
        wid, level = g.get("workspace_id"), g.get("level")
        if level not in AccessLevel.values:
            return _fail("workspace_grants", "invalid_level", "Level must be 'view' or 'operate'.")
        if wid not in known_ws:
            return _fail("workspace_grants", "workspace_not_found", f"Unknown workspace: {wid}")
        w_rows[wid] = level

    with transaction.atomic():
        ProjectGrant.objects.filter(user=user).delete()
        WorkspaceGrant.objects.filter(user=user).delete()
        ProjectGrant.objects.bulk_create(
            [ProjectGrant(user=user, project_id=pid, level=level) for pid, level in p_rows.items()]
        )
        WorkspaceGrant.objects.bulk_create(
            [WorkspaceGrant(user=user, workspace_id=wid, level=level) for wid, level in w_rows.items()]
        )
    _after_access_change()
    return AccountResult(success=True, user=user)


def project_members(project_id: str) -> list[dict]:
    """Who can reach a project, and how — for the project-level Access panel.

    Lists direct grants and workspace-derived grants separately so the UI can
    show *why* someone has access (and only edit the direct ones in place).
    """
    from twicc.core.models import Project, ProjectGrant, User, UserRole, WorkspaceGrant
    from twicc.workspaces import read_workspaces

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        return []
    # A worktree inherits from its main repository.
    anchor_id = project.worktree_of_id or project.id
    ws_ids = {
        ws.get("id"): ws.get("name")
        for ws in read_workspaces().get("workspaces", [])
        if isinstance(ws, dict) and anchor_id in ws.get("projectIds", [])
    }
    rows: dict[int, dict] = {}

    def _row(user) -> dict:
        return rows.setdefault(user.id, {
            "user": serialize_user(user),
            "direct_level": None,
            "via_workspaces": [],
            "is_admin": user.role == UserRole.ADMIN,
        })

    for user in User.objects.filter(role=UserRole.ADMIN):
        _row(user)
    for grant in ProjectGrant.objects.filter(project_id=anchor_id).select_related("user"):
        _row(grant.user)["direct_level"] = grant.level
    for grant in WorkspaceGrant.objects.filter(workspace_id__in=list(ws_ids)).select_related("user"):
        _row(grant.user)["via_workspaces"].append({
            "workspace_id": grant.workspace_id,
            "workspace_name": ws_ids.get(grant.workspace_id),
            "level": grant.level,
        })
    return sorted(rows.values(), key=lambda r: (not r["is_admin"], r["user"]["email"]))
