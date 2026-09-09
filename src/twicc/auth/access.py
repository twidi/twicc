"""The single answer to "is this caller allowed past the instance password?".

Every gate — HTTP middleware, ``/rpc/`` scoping, the two WebSocket consumers,
the MCP owner views, ``/api/auth/check/`` — goes through :func:`request_allowed`
or :func:`scope_allowed`. Routing them all here is the point: a bypass that one
call site forgot would either leak a surface or, worse, hand the SPA an
``authenticated: false`` while the API answers, which reads as a broken login.

Two ways in: a session bound to the current password hash
(:mod:`twicc.auth.session_auth`), or the dev-worktree local bypass below.

Dev-worktree local bypass
-------------------------
A worktree instance is thrown away with its checkout, and its login session
lives in its own database — so an agent that (re-)creates a worktree finds an
UI it cannot open, having no reason to know the user's password. The bypass
exists for exactly that, and only that.

Three conditions, ALL required:

1. ``settings.TWICC_DEV_LOCAL_BYPASS`` — the intent, set by devctl when it
   launches a worktree backend.
2. The data dir is REALLY a secondary git worktree (:func:`_data_dir_is_worktree`).
   A filesystem fact, so putting the variable in a ``.env`` — the one place a
   value can override the process environment — buys nothing: ``~/.twicc`` is
   not a worktree, and a released install has no ``.git`` at all.
3. The request is a DIRECT LOOPBACK one, per the fail-safe classifier of
   :mod:`twicc.auth.local_access`: loopback TCP peer **and** no forwarding
   header. Any proxy or tunnel in front adds one, so remote access keeps
   asking for the password — including the same worktree reached through its
   own tunnel.

Known limitation, inherited from that shared classifier and accepted: a tunnel
that adds NO forwarding header (a raw ``ssh -L`` port-forward) presents as
loopback. It is the same trust model the no-password remote gate already runs
on, and it is confined to a dev worktree.
"""

from __future__ import annotations

import logging

from django.conf import settings

from twicc.auth.local_access import request_is_local, scope_is_local
from twicc.auth.session_auth import is_session_authenticated
from twicc.paths import get_data_dir

logger = logging.getLogger(__name__)

# Resolved on first use and kept for the process: both conditions are launch
# properties, and this is consulted on every gated request.
_bypass_enabled: bool | None = None


def _data_dir_is_worktree() -> bool:
    """Whether the data dir is the root of a SECONDARY git worktree.

    A secondary worktree has a ``.git`` *file* holding ``gitdir:
    <repo>/.git/worktrees/<name>``; a normal clone has a ``.git`` directory,
    and a submodule's ``gitdir:`` has no ``worktrees`` segment. Read from the
    filesystem rather than ``git rev-parse`` so no subprocess and no git
    binary are needed.

    Deliberately NOT in ``twicc.paths``: that module resolves the data dir and
    must keep knowing nothing about worktrees (devctl alone decides that, by
    injecting ``TWICC_DATA_DIR``). This answers a different question, for the
    bypass only.
    """
    marker = get_data_dir() / ".git"
    if not marker.is_file():
        return False
    try:
        content = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("gitdir:"):
            return "worktrees" in line.split(":", 1)[1].strip().split("/")
    return False


def dev_local_bypass_enabled() -> bool:
    """Whether this process may grant the dev-worktree local bypass at all.

    The per-request locality check is separate — see :func:`request_allowed`.
    """
    global _bypass_enabled
    if _bypass_enabled is None:
        if not settings.TWICC_DEV_LOCAL_BYPASS:
            _bypass_enabled = False
        elif not _data_dir_is_worktree():
            _bypass_enabled = False
            logger.warning(
                "TWICC_DEV_LOCAL_BYPASS is set but %s is not a git worktree: bypass refused",
                get_data_dir(),
            )
        else:
            _bypass_enabled = True
            logger.info(
                "Dev worktree: direct loopback requests bypass the instance password "
                "(remote access still requires it)"
            )
    return _bypass_enabled


def reset_cache() -> None:
    """Forget the resolved verdict (tests)."""
    global _bypass_enabled
    _bypass_enabled = None


def request_allowed(request, session_auth, session_fingerprint) -> bool:
    """Whether an HTTP request may pass the instance-password gate.

    The session values are passed in already read (the callers are a mix of
    sync and async code, as for :func:`~twicc.auth.session_auth.is_session_authenticated`).
    """
    if is_session_authenticated(session_auth, session_fingerprint, settings.TWICC_PASSWORD_HASH):
        return True
    return dev_local_bypass_enabled() and request_is_local(request)


def scope_allowed(scope, session_auth, session_fingerprint) -> bool:
    """Whether an ASGI (WebSocket) connection may pass the instance-password gate."""
    if is_session_authenticated(session_auth, session_fingerprint, settings.TWICC_PASSWORD_HASH):
        return True
    return dev_local_bypass_enabled() and scope_is_local(scope)
