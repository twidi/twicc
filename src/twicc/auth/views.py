"""Authentication views for password-based login.

Provides login, logout, and auth status check endpoints.
All endpoints are under /api/auth/ and always accessible (no auth required).

Password storage and verification (PBKDF2-SHA256, with legacy SHA-256
support for hashes set before the upgrade) lives in ``twicc.auth.hashers``.
"""

import logging
import time
from collections import defaultdict
from urllib.parse import parse_qs

import asyncio

import orjson
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render

from twicc.accounts.principal import (
    OWNER,
    SESSION_USER_EPOCH_KEY,
    SESSION_USER_ID_KEY,
    bind_user_session,
)
from twicc.auth.access import auth_required, resolve_request_principal
from twicc.auth.hashers import hash_password, verify_password
from twicc.auth.local_access import remote_access_blocked
from twicc.auth.session_auth import (
    SESSION_AUTH_KEY,
    bind_session,
)
from twicc.core.ui_messages import ui_error

logger = logging.getLogger(__name__)


# ── Rate limiter for login brute-force protection ─────────────────────────

# Per-IP tracking of failed login attempts
_login_attempts: dict[str, list[float]] = defaultdict(list)

# Max failed attempts before throttling
_MAX_ATTEMPTS = 5
# Time window in seconds (failed attempts older than this are forgotten)
_WINDOW_SECONDS = 300  # 5 minutes
# Lockout duration after exceeding max attempts
_LOCKOUT_SECONDS = 60


def _check_rate_limit(ip: str) -> int | None:
    """Check if an IP is rate-limited.

    Returns None if allowed, or the number of seconds to wait if blocked.
    """
    now = time.monotonic()
    attempts = _login_attempts[ip]

    # Prune old attempts outside the window
    _login_attempts[ip] = [t for t in attempts if now - t < _WINDOW_SECONDS]
    attempts = _login_attempts[ip]

    if len(attempts) >= _MAX_ATTEMPTS:
        last_attempt = attempts[-1]
        wait = int(_LOCKOUT_SECONDS - (now - last_attempt))
        if wait > 0:
            return wait
        # Lockout expired — clear and allow
        _login_attempts[ip] = []

    return None


def _record_failed_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _login_attempts[ip].append(time.monotonic())


def _get_client_ip(request) -> str:
    """Extract the real client IP from the request.

    Checks proxy/tunnel headers in priority order before falling back to REMOTE_ADDR.
    Only the leftmost (client) IP is used from X-Forwarded-For to avoid spoofing
    via appended values.

    Header priority:
        1. CF-Connecting-IP  (Cloudflare Tunnel)
        2. Fly-Client-IP     (Fly.io)
        3. X-Real-IP         (Nginx convention)
        4. X-Forwarded-For   (standard proxy header, first IP only)
        5. REMOTE_ADDR       (direct connection fallback)
    """
    # Tunnel/proxy-specific headers (single IP, most trustworthy when present)
    for header in ("HTTP_CF_CONNECTING_IP", "HTTP_FLY_CLIENT_IP", "HTTP_X_REAL_IP"):
        ip = request.META.get(header)
        if ip:
            return ip.strip()

    # Standard proxy header — take the first (leftmost) IP only
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "unknown")


async def auth_check(request):
    """GET /api/auth/check/ - Check if user is authenticated.

    Every response includes the running server version for the login screen,
    plus the sign-in options the login screen offers:

    - ``accounts_enabled`` — at least one account exists (email + password form)
    - ``google_enabled`` — Google sign-in is configured
    - ``owner_password`` — the instance password is set (owner sign-in)

    Returns:
        - {"authenticated": true, "password_required": true, "principal": {...}} if authenticated
        - {"authenticated": true, "password_required": false, "principal": owner} if nothing is configured
        - {"authenticated": false, "password_required": true} if not authenticated
        - {"authenticated": false, "password_required": false, "access_denied": ...,
          "set_password_command": ...} when nothing is configured and the
          request is non-local (the frontend renders an access-blocked screen
          telling the operator which command sets a password). Always reachable
          (this endpoint is public) so the SPA can learn it's blocked before
          fetching any protected data.
    """
    from twicc.accounts import google
    from twicc.accounts.principal import accounts_enabled

    enabled = await accounts_enabled()
    options = {
        "version": settings.APP_VERSION,
        "accounts_enabled": enabled,
        "google_enabled": google.is_enabled(),
        "owner_password": bool(settings.TWICC_PASSWORD_HASH),
    }
    if not auth_required():
        if remote_access_blocked(request):
            return JsonResponse({
                "authenticated": False,
                "password_required": False,
                **options,
                "access_denied": "remote_no_password",
                "set_password_command": f"{settings.TWICC_LAUNCH_PREFIX} password set",
            })
        return JsonResponse({
            "authenticated": True, "password_required": False, **options, "principal": OWNER.to_payload(),
        })

    principal = await resolve_request_principal(request)
    session = request.session
    # Drop a stale session so the next request doesn't keep retrying it.
    if principal is None and (await session.aget(SESSION_AUTH_KEY) or await session.aget(SESSION_USER_ID_KEY)):
        await session.aflush()
    google_error = await session.aget(google.SESSION_ERROR_KEY) if principal is None else None
    if google_error:
        await session.apop(google.SESSION_ERROR_KEY)

    return JsonResponse({
        "authenticated": principal is not None,
        "password_required": True,
        **options,
        "principal": principal.to_payload() if principal else None,
        **({"google_error": google_error} if google_error else {}),
    })


async def login(request):
    """POST /api/auth/login/ - Authenticate with password.

    Body: {"password": "the_password"}

    Verification (constant-time, dispatched per format) is delegated to
    ``verify_password`` so both legacy SHA-256 and current PBKDF2 hashes
    are accepted.

    On success, sets session["authenticated"] = True and returns 200.
    On failure, returns 401.
    """
    if request.method != "POST":
        return JsonResponse(ui_error("Method not allowed", 'backend.common.methodNotAllowed'), status=405)

    ip = _get_client_ip(request)

    # Rate limit check
    wait = _check_rate_limit(ip)
    if wait is not None:
        logger.warning("Login rate-limited for %s (%ds remaining)", ip, wait)
        return JsonResponse(
            ui_error(f"Too many attempts. Try again in {wait}s.", 'backend.auth.rateLimited', {"seconds": wait}),
            status=429,
        )

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse(ui_error("Invalid JSON", 'backend.common.invalidJson'), status=400)

    password = data.get("password", "")
    if not isinstance(password, str):
        password = ""
    email = data.get("email")

    # Account sign-in (email + password).
    if isinstance(email, str) and email.strip():
        user = await asyncio.to_thread(_verify_account_password, email, password)
        if user is not None:
            await request.session.aget(SESSION_AUTH_KEY)
            # New session key on sign-in: no fixation of a pre-login cookie.
            await request.session.acycle_key()
            bind_user_session(request.session, user)
            await _record_login(user)
            _login_attempts.pop(ip, None)
            logger.info("Successful account login (%s) from %s", user.email, ip)
            return JsonResponse({"authenticated": True})
        _record_failed_attempt(ip)
        logger.warning("Failed account login from %s", ip)
        return JsonResponse(ui_error("Invalid email or password", 'backend.auth.invalidCredentials'), status=401)

    if not settings.TWICC_PASSWORD_HASH:
        return JsonResponse(ui_error("No password configured", 'backend.auth.noPassword'), status=400)

    # PBKDF2 verification is intentionally CPU-heavy — running it on the
    # event loop would stall unrelated async work for the entire request.
    # ``asyncio.to_thread`` ships it to a fresh worker thread; using
    # ``sync_to_async(...)`` here would default to ``thread_sensitive=True``
    # which routes through asgiref's single shared executor, serialising
    # all PBKDF2 verifications back-to-back.
    if await asyncio.to_thread(verify_password, password, settings.TWICC_PASSWORD_HASH):
        # ``bind_session`` mutates the session dict (``session[key] = ...``)
        # synchronously. Touching the session for the first time would
        # otherwise trigger a sync ORM load — async-pre-load it via
        # ``aget`` so the dict writes that follow are pure in-memory.
        # SessionMiddleware persists the modified session at response time
        # (SESSION_SAVE_EVERY_REQUEST=True).
        await request.session.aget(SESSION_AUTH_KEY)
        request.session.pop(SESSION_USER_ID_KEY, None)
        request.session.pop(SESSION_USER_EPOCH_KEY, None)
        bind_session(request.session, settings.TWICC_PASSWORD_HASH)
        # Clear failed attempts on success
        _login_attempts.pop(ip, None)
        logger.info("Successful login from %s", ip)
        return JsonResponse({"authenticated": True})
    else:
        _record_failed_attempt(ip)
        remaining = _MAX_ATTEMPTS - len(_login_attempts[ip])
        logger.warning("Failed login attempt from %s (%d attempts left)", ip, max(0, remaining))
        return JsonResponse(ui_error("Invalid password", 'backend.auth.invalidPassword'), status=401)


# Burned on unknown emails so a wrong address costs the same as a wrong
# password (no account enumeration through response timing).
_DUMMY_HASH = hash_password("twicc-timing-equalizer")


def _verify_account_password(email: str, password: str):
    """The active account matching ``email`` + ``password``, or None (sync, slow)."""
    from twicc.accounts.service import normalize_email
    from twicc.core.models import User

    user = User.objects.filter(email=normalize_email(email)).first()
    if user is None or not user.password_hash:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    return user


async def _record_login(user) -> None:
    from asgiref.sync import sync_to_async

    from twicc.accounts.service import record_login
    from twicc.providers.db_writer import run_under_db_write_lock

    await run_under_db_write_lock(lambda: sync_to_async(record_login)(user))


async def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse(ui_error("Method not allowed", 'backend.common.methodNotAllowed'), status=405)

    await request.session.aflush()
    return JsonResponse({"authenticated": False})


# ── Standalone artifact password page ─────────────────────────────────────
# A direct artifact URL (/artifacts/<id>/…) opened in a fresh tab may hit an
# unauthenticated session; PasswordAuthMiddleware redirects it here. Plain
# server-rendered web on purpose: no SPA, and no JS/API for the auth itself —
# just a form POST. The page is a Django template (twicc.artifacts/templates/
# artifact_auth.html); its only script is a tiny inline colour-scheme picker.


def _safe_artifact_redirect(value: str) -> str:
    """Confine the post-login redirect to a local artifact path — no open-redirect,
    no loop back to the auth page."""
    if (
        value
        and value.startswith("/artifacts/")
        and not value.startswith("//")
        and not value.startswith("/artifacts/auth")
    ):
        return value
    return "/"


async def artifact_auth(request):
    """GET/POST /artifacts/auth — standalone sign-in page for direct artifact access.

    PasswordAuthMiddleware redirects unauthenticated ``/artifacts/*`` navigations
    here with ``?redirect=<target>``. Accepts the instance password, or an
    account's email + password when accounts exist. On success it binds the
    session (sets the cookie) and 302s back to the target, where the usual
    per-project checks apply. Shares the login brute-force rate limiter.
    """
    from twicc.accounts.principal import accounts_enabled

    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)

    enabled = await accounts_enabled()

    def _page(target, error, status=200):
        context = {"redirect": target, "error": error, "accounts_enabled": enabled}
        return render(request, "artifact_auth.html", context, status=status)

    if request.method == "GET":
        target = _safe_artifact_redirect(request.GET.get("redirect", ""))
        # Nothing configured → nothing to gate; go straight through.
        if not auth_required():
            return HttpResponseRedirect(target)
        return _page(target, False)

    # POST — validate and (on success) authenticate.
    body = parse_qs(request.body.decode("utf-8", "replace"))
    target = _safe_artifact_redirect((body.get("redirect") or [""])[0])
    if not auth_required():
        return HttpResponseRedirect(target)

    ip = _get_client_ip(request)
    wait = _check_rate_limit(ip)
    if wait is not None:
        logger.warning("Artifact-auth rate-limited for %s (%ds remaining)", ip, wait)
        return _page(target, True, status=429)

    password = (body.get("password") or [""])[0]
    email = (body.get("email") or [""])[0].strip()
    if email:
        user = await asyncio.to_thread(_verify_account_password, email, password)
        if user is not None:
            await request.session.aget(SESSION_AUTH_KEY)
            await request.session.acycle_key()
            bind_user_session(request.session, user)
            await _record_login(user)
            _login_attempts.pop(ip, None)
            logger.info("Successful artifact-auth account login (%s) from %s", user.email, ip)
            return HttpResponseRedirect(target)
    elif settings.TWICC_PASSWORD_HASH and await asyncio.to_thread(
        verify_password, password, settings.TWICC_PASSWORD_HASH
    ):
        await request.session.aget(SESSION_AUTH_KEY)
        request.session.pop(SESSION_USER_ID_KEY, None)
        request.session.pop(SESSION_USER_EPOCH_KEY, None)
        bind_session(request.session, settings.TWICC_PASSWORD_HASH)
        _login_attempts.pop(ip, None)
        logger.info("Successful artifact-auth login from %s", ip)
        return HttpResponseRedirect(target)

    _record_failed_attempt(ip)
    return _page(target, True, status=401)


# ── Google sign-in ─────────────────────────────────────────────────────────


def _safe_next(value: str) -> str:
    """Confine the post-sign-in redirect to a local SPA path (no open redirect)."""
    if value and value.startswith("/") and not value.startswith("//") and not value.startswith("/api/"):
        return value
    return "/"


async def google_start(request):
    """GET /api/auth/google/start/?next=<path> — redirect to Google's consent screen."""
    from twicc.accounts import google

    config = google.get_config()
    if config is None:
        return JsonResponse(ui_error("Google sign-in is not configured", 'backend.auth.googleDisabled'), status=404)
    next_path = _safe_next(request.GET.get("next", ""))
    url, flow = google.start_flow(config, google.redirect_uri_for(config, request), next_path)
    await request.session.aget(google.SESSION_FLOW_KEY)
    await request.session.aset(google.SESSION_FLOW_KEY, flow)
    return HttpResponseRedirect(url)


async def google_callback(request):
    """GET /api/auth/google/callback/ — Google redirects here with ``code`` + ``state``.

    Always ends in a redirect to the SPA: on success to the requested page,
    on refusal to the login page, which reads the refusal code from
    ``/api/auth/check/`` (kept in the session, single-use).
    """
    import secrets as _secrets
    import time as _time

    from asgiref.sync import sync_to_async

    from twicc.accounts import google
    from twicc.providers.db_writer import run_under_db_write_lock

    config = google.get_config()
    if config is None:
        return HttpResponseRedirect("/login")
    session = request.session
    flow = await session.apop(google.SESSION_FLOW_KEY, None)

    async def _refuse(code: str):
        await session.aset(google.SESSION_ERROR_KEY, code)
        return HttpResponseRedirect("/login")

    ip = _get_client_ip(request)
    if (wait := _check_rate_limit(ip)) is not None:
        logger.warning("Google sign-in rate-limited for %s (%ds remaining)", ip, wait)
        return await _refuse("rate_limited")
    if request.GET.get("error"):
        return await _refuse("cancelled")
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    if (
        not isinstance(flow, dict)
        or not state
        or not code
        or not _secrets.compare_digest(str(flow.get("state", "")), state)
        or _time.time() - float(flow.get("started", 0)) > google.FLOW_MAX_AGE_SECONDS
    ):
        _record_failed_attempt(ip)
        return await _refuse("invalid_state")
    try:
        identity = await google.exchange_code(config, flow, code)
        user = await run_under_db_write_lock(
            lambda: sync_to_async(google.match_or_create_account)(identity, config)
        )
    except google.GoogleError as exc:
        _record_failed_attempt(ip)
        logger.warning("Google sign-in refused (%s) from %s", exc.code, ip)
        return await _refuse(exc.code)

    await session.acycle_key()
    bind_user_session(session, user)
    await _record_login(user)
    _login_attempts.pop(ip, None)
    logger.info("Successful Google login (%s) from %s", user.email, ip)
    return HttpResponseRedirect(_safe_next(flow.get("next", "")))
