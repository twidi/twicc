"""Password authentication middleware.

When TWICC_PASSWORD_HASH is set, all requests (except login, static files)
require an authenticated session. Unauthenticated requests get a 401 response.

When TWICC_PASSWORD_HASH is empty/unset, all requests pass through (no protection).
"""

import logging
from urllib.parse import urlencode

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse

from twicc.artifacts import NOT_AUTHENTICATED_SVG
from twicc.auth import tokens as api_tokens
from twicc.auth.access import request_allowed
from twicc.auth.local_access import remote_access_blocked
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY
from twicc.rpc.permissions import RPC_SCOPE_FULL, RPC_SCOPE_READ

logger = logging.getLogger(__name__)

# Paths that are always accessible (no auth required). ``/artifacts/auth`` is the
# standalone password page that gates direct artifact access — it must be reachable
# while unauthenticated, otherwise the redirect below would loop.
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
    "/artifacts/auth",
    # Public share surface (O1: the token is the credential; per-link password
    # gating lives inside the share views). ``/_twicc/share/`` is the built
    # viewer bundle (no data). Both exempt from the instance-password gate AND
    # the local-only remote gate — ``_is_data_path`` returns False for them.
    "/share/",
    "/_twicc/share/",
    # Peer messaging inbound API — Bearer-auth in views (design 2026-07-24).
    # Exempt from the instance-password gate AND the local-only remote gate.
    "/peer/",
)

# Non-API URL prefixes whose responses leak data and therefore require an
# authenticated session. Anything not in this list and not under ``/api/``
# falls through to the SPA catch-all, which only serves ``index.html`` (no
# sensitive content) and lets the frontend redirect to login.
PROTECTED_NON_API_PREFIXES: tuple[str, ...] = (
    "/artifacts/",
    "/project-icons/",
)


def _is_data_path(request) -> bool:
    """Whether the path serves sensitive data (must be gated) rather than a
    public endpoint or the SPA shell (which stay open so the frontend can load
    and render the access-blocked screen)."""
    if any(request.path.startswith(p) for p in PUBLIC_PATHS):
        return False
    if any(request.path.startswith(prefix) for prefix in PROTECTED_NON_API_PREFIXES):
        return True
    return request.path.startswith("/api/")


class PasswordAuthMiddleware:
    """Middleware that enforces password authentication via session.

    Checks request.session["authenticated"] for all requests except
    public paths. Returns 401 JSON for API requests, 401 JSON for
    SPA requests (frontend handles redirect to login).
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        self.password_required = bool(settings.TWICC_PASSWORD_HASH)
        # Django/asgiref detect coroutine middleware via iscoroutinefunction
        # on the *instance*, not on cls.__call__. A class with ``async def
        # __call__`` is not enough — mark the instance explicitly.
        markcoroutinefunction(self)
        if self.password_required:
            logger.info("Password protection enabled")
        else:
            logger.info("Password protection disabled (TWICC_PASSWORD_HASH not set)")

    async def __call__(self, request):
        # No password configured: nothing to authenticate against. Refuse
        # data-bearing endpoints for non-local requests (unless the operator
        # opted out via TWICC_ALLOW_INSECURE_REMOTE) so a forgotten password
        # doesn't leave the instance silently reachable over the network. Public
        # paths and the SPA shell stay open so the frontend can load and render
        # the "set a password" screen (driven by /api/auth/check/).
        if not self.password_required:
            if _is_data_path(request) and remote_access_blocked(request):
                return JsonResponse(
                    {"error": "remote_access_requires_password"},
                    status=403,
                )
            return await self.get_response(request)

        # Allow public paths
        if any(request.path.startswith(p) for p in PUBLIC_PATHS):
            return await self.get_response(request)

        # Allow non-API paths (SPA catch-all serves index.html which contains
        # no sensitive data; Vue Router handles the login redirect client-side).
        # Non-API URLs that DO leak data (artifact serving, ...) are listed
        # in ``PROTECTED_NON_API_PREFIXES`` and fall through to the auth check.
        is_protected_non_api = any(
            request.path.startswith(prefix) for prefix in PROTECTED_NON_API_PREFIXES
        )
        if not request.path.startswith("/api/") and not is_protected_non_api:
            return await self.get_response(request)

        # Check authentication for API requests. A session that was logged in
        # against an older password hash (rotated since) is treated as
        # unauthenticated, and its server-side row is dropped so it can't be
        # re-used by a parallel request.
        session = request.session
        auth_value = await session.aget(SESSION_AUTH_KEY)
        fingerprint = await session.aget(SESSION_FINGERPRINT_KEY)
        if not request_allowed(request, auth_value, fingerprint):
            if auth_value:
                await session.aflush()
            if is_protected_non_api:
                # An inline <img> (image rendered inside a message) can't follow an
                # HTML redirect — it would just show a broken image — so hand image
                # requests a small "not authenticated" placeholder image. A top-level
                # artifact navigation instead goes to the standalone password page,
                # which redirects back here after a successful login.
                dest = request.headers.get("Sec-Fetch-Dest", "")
                accept = request.headers.get("Accept", "")
                if dest == "image" or accept.startswith("image/"):
                    response = HttpResponse(NOT_AUTHENTICATED_SVG, content_type="image/svg+xml")
                    response["Cache-Control"] = "no-store"
                    return response
                query = urlencode({"redirect": request.get_full_path()})
                return HttpResponseRedirect(f"/artifacts/auth?{query}")
            return JsonResponse(
                {"error": "Authentication required"},
                status=401,
            )

        return await self.get_response(request)


class RpcTokenAuthMiddleware:
    """Authentication gate for ``/rpc/`` — tags the request with an auth scope.

    Protection posture:
      - **Neither** ``TWICC_PASSWORD_HASH`` **nor** any token configured →
        ``/rpc/`` is open (operator opted out of protection; local-only use).
      - **A password is set OR at least one token exists** → the request must
        carry either a valid Bearer token or a valid SPA session cookie. The
        password never itself authenticates RPC; it only forces gating on (so
        the existing "set a password for remote access" recommendation also
        closes /rpc/ automatically).

    Two ways to authenticate, two scopes (the *authorization* of a scope against
    the actual command happens in the dispatch view, see
    ``twicc.rpc.permissions``):
      - **Bearer token** (a held secret = capability) → ``RPC_SCOPE_FULL``.
      - **Session cookie** (ambient authority — same-origin artifact pages carry
        it automatically) → ``RPC_SCOPE_READ``, read-only commands only. A
        cross-site POST can't ride the cookie at all (``SESSION_COOKIE_SAMESITE``
        is ``"Lax"``), so this is the same CSRF posture as ``/api/``.

    The scope is stamped on ``request.rpc_scope``; the dispatch view defaults a
    missing attribute to full access (covers the unprotected pass-through).
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        if not request.path.startswith("/rpc/"):
            return await self.get_response(request)

        password_set = bool(settings.TWICC_PASSWORD_HASH)
        has_tokens = await sync_to_async(api_tokens.has_tokens)()

        if not password_set and not has_tokens:
            # Unprotected by choice — but still refuse non-local callers, for the
            # same reason as PasswordAuthMiddleware: with neither a password nor a
            # token there's nothing to authenticate, so /rpc/ must not be open
            # over the network (a valid token below stays accepted remotely).
            if remote_access_blocked(request):
                return JsonResponse(
                    {"error": "Remote access requires a password or an API token."},
                    status=403,
                )
            request.rpc_scope = RPC_SCOPE_FULL
            return await self.get_response(request)

        provided = self._bearer(request)
        record = await sync_to_async(api_tokens.verify_token)(provided) if provided else None
        if record is not None:
            # Record last_used in memory only — no I/O on the request path. A
            # burst of authenticated /rpc/ calls is coalesced into at most one
            # api-tokens.json write per interval by
            # ``twicc.auth.tokens.start_last_used_flush_task``.
            api_tokens.note_used(record.id)
            request.rpc_scope = RPC_SCOPE_FULL
            return await self.get_response(request)

        # No valid token: fall back to the SPA's session cookie. A logged-in
        # user's same-origin artifact page reaches /rpc/ without a token, but at
        # read-only scope — the dispatch view enforces the command allowlist.
        if await self._request_allowed(request):
            request.rpc_scope = RPC_SCOPE_READ
            return await self.get_response(request)

        msg = (
            "API token required. Create one with `twicc token create`."
            if not has_tokens
            else "Invalid or missing API token."
        )
        return JsonResponse({"error": msg}, status=401)

    @staticmethod
    async def _request_allowed(request) -> bool:
        """Whether ambient authority lets this request through, at read scope.

        Mirrors ``PasswordAuthMiddleware``: read the auth markers async (works
        on the event loop) and hand them to ``twicc.auth.access``, which
        validates them against the live ``TWICC_PASSWORD_HASH`` (a rotated hash
        invalidates the session) and also grants the dev-worktree local bypass.
        With no password configured there is no authenticated-session concept,
        so outside a bypassing worktree a token is the only way in.
        """
        session = request.session
        auth_value = await session.aget(SESSION_AUTH_KEY)
        fingerprint = await session.aget(SESSION_FINGERPRINT_KEY)
        return request_allowed(request, auth_value, fingerprint)

    @staticmethod
    def _bearer(request):
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[len("Bearer "):].strip()
        return None
