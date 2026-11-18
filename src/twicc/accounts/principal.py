"""Who is calling: the :class:`Principal` attached to every gated request.

Two kinds of identity exist:

- the **owner** — the built-in admin identity behind the instance password
  (``TWICC_PASSWORD_HASH``), the no-password local mode and the dev-worktree
  bypass. It has no ``User`` row and can never be locked out by account
  management: it is the break-glass admin of the instance.
- a **user** — one :class:`twicc.core.models.User` row, signed in with its own
  password or with Google. Its login session carries the account id and the
  account's ``auth_epoch``; a mismatch (password changed, account disabled,
  role changed, "sign out everywhere") drops the session at its next request.

``accounts_enabled()`` tells whether at least one account exists (active or
not). While none does, TwiCC behaves exactly as the single-user product it
always was.
"""

from __future__ import annotations

import time
from typing import NamedTuple

from asgiref.sync import sync_to_async

SESSION_USER_ID_KEY = "twicc_user_id"
SESSION_USER_EPOCH_KEY = "twicc_user_epoch"

KIND_OWNER = "owner"
KIND_USER = "user"


class Principal(NamedTuple):
    """The resolved caller of one request or WebSocket connection."""

    kind: str
    user_id: int | None
    email: str
    display_name: str
    role: str
    avatar_url: str = ""

    @property
    def is_admin(self) -> bool:
        from twicc.core.models import UserRole

        return self.role == UserRole.ADMIN

    @property
    def is_owner(self) -> bool:
        return self.kind == KIND_OWNER

    def to_payload(self) -> dict:
        """The JSON shape sent to the SPA (``/api/auth/check/``, ``/api/me/``)."""
        return {
            "kind": self.kind,
            "id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "is_admin": self.is_admin,
            "avatar_url": self.avatar_url,
        }


OWNER = Principal(kind=KIND_OWNER, user_id=None, email="", display_name="Owner", role="admin")


# --- accounts_enabled (cached) ----------------------------------------------

# Consulted on every gated request, so cached briefly. Accounts can be created
# from the CLI (another process), hence a TTL rather than a pure in-process
# invalidation; local mutations still invalidate at once.
_ENABLED_TTL_SECONDS = 5.0
_enabled_cache: tuple[float, bool] | None = None


def invalidate_accounts_enabled() -> None:
    global _enabled_cache
    _enabled_cache = None


def accounts_enabled_sync() -> bool:
    """Whether at least one account exists (sync, cached)."""
    global _enabled_cache
    now = time.monotonic()
    if _enabled_cache is not None and now - _enabled_cache[0] < _ENABLED_TTL_SECONDS:
        return _enabled_cache[1]
    from twicc.core.models import User

    # ANY row, active or not: disabling every account must never silently
    # fall back to the unprotected single-user mode. Only deleting them does.
    value = User.objects.exists()
    _enabled_cache = (now, value)
    return value


async def accounts_enabled() -> bool:
    """Async variant — the one gates call first, so it also refreshes the cache."""
    if _enabled_cache is not None and time.monotonic() - _enabled_cache[0] < _ENABLED_TTL_SECONDS:
        return _enabled_cache[1]
    return await sync_to_async(accounts_enabled_sync)()


def accounts_enabled_cached() -> bool:
    """Last known value, never touching the DB (safe from any context).

    Sync helpers reached from the event loop (``local_access``) read this;
    every gate refreshes it through :func:`accounts_enabled` before.
    """
    return bool(_enabled_cache and _enabled_cache[1])


# --- user principal ---------------------------------------------------------


def principal_for_user(user) -> Principal:
    return Principal(
        kind=KIND_USER,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name or user.email,
        role=user.role,
        avatar_url=user.avatar_url or "",
    )


def load_user_principal_sync(user_id, epoch) -> Principal | None:
    """The principal behind a session's ``(user_id, epoch)``, or None if stale.

    None when the account is gone, disabled, or its ``auth_epoch`` moved on
    since the session was opened.
    """
    from twicc.core.models import User

    if not isinstance(user_id, int) or not isinstance(epoch, int):
        return None
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.is_active or user.auth_epoch != epoch:
        return None
    return principal_for_user(user)


async def load_user_principal(user_id, epoch) -> Principal | None:
    if user_id is None:
        return None
    return await sync_to_async(load_user_principal_sync)(user_id, epoch)


def bind_user_session(session, user) -> None:
    """Mark a Django session as signed in as ``user`` (current epoch).

    Clears any owner marker so one cookie never carries two identities.
    """
    from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY

    session.pop(SESSION_AUTH_KEY, None)
    session.pop(SESSION_FINGERPRINT_KEY, None)
    session[SESSION_USER_ID_KEY] = user.id
    session[SESSION_USER_EPOCH_KEY] = user.auth_epoch
