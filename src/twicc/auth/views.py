"""Authentication views for password-based login and API token management.

Provides login, logout, auth status check, and API token endpoints.
All endpoints are under /api/auth/ and always accessible (no middleware auth).

The password is never stored in clear text. TWICC_PASSWORD_HASH contains a
SHA-256 hex digest. On login, the submitted password is hashed and compared
to the stored hash using constant-time comparison.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from collections import defaultdict

import orjson
from django.conf import settings
from django.http import JsonResponse

from twicc.paths import get_env_path

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


def _hash_password(password: str) -> str:
    """Hash a password with SHA-256 and return its hex digest."""
    return hashlib.sha256(password.encode()).hexdigest()


def auth_check(request):
    """GET /api/auth/check/ - Check if user is authenticated.

    Returns:
        - {"authenticated": true, "password_required": true} if authenticated
        - {"authenticated": true, "password_required": false} if no password configured
        - {"authenticated": false, "password_required": true} if not authenticated
    """
    password_required = bool(settings.TWICC_PASSWORD_HASH)
    has_api_token = bool(settings.TWICC_API_TOKEN)
    if not password_required:
        return JsonResponse({
            "authenticated": True,
            "password_required": False,
            "has_api_token": has_api_token,
        })

    authenticated = request.session.get("authenticated", False)
    return JsonResponse({
        "authenticated": authenticated,
        "password_required": True,
        "has_api_token": has_api_token,
    })


def login(request):
    """POST /api/auth/login/ - Authenticate with password.

    Body: {"password": "the_password"}

    The password is hashed with SHA-256 and compared to the stored hash
    using constant-time comparison to prevent timing attacks.

    On success, sets session["authenticated"] = True and returns 200.
    On failure, returns 401.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not settings.TWICC_PASSWORD_HASH:
        return JsonResponse({"error": "No password configured"}, status=400)

    ip = _get_client_ip(request)

    # Rate limit check
    wait = _check_rate_limit(ip)
    if wait is not None:
        logger.warning("Login rate-limited for %s (%ds remaining)", ip, wait)
        return JsonResponse(
            {"error": f"Too many attempts. Try again in {wait}s."},
            status=429,
        )

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    password = data.get("password", "")
    password_hash = _hash_password(password)

    # Constant-time comparison to prevent timing attacks
    if hmac.compare_digest(password_hash, settings.TWICC_PASSWORD_HASH):
        request.session["authenticated"] = True
        # Clear failed attempts on success
        _login_attempts.pop(ip, None)
        logger.info("Successful login from %s", ip)
        return JsonResponse({"authenticated": True})
    else:
        _record_failed_attempt(ip)
        remaining = _MAX_ATTEMPTS - len(_login_attempts[ip])
        logger.warning("Failed login attempt from %s (%d attempts left)", ip, max(0, remaining))
        return JsonResponse({"error": "Invalid password"}, status=401)


def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    request.session.flush()
    return JsonResponse({"authenticated": False})


# ── API Token Management ───────────────────────────────────────────────


def api_token(request):
    """GET /api/auth/token/ - Return the current API token.

    Requires session authentication (not token auth) to prevent
    token-to-token escalation. Returns 404 if no token configured.
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # Require session auth — /api/auth/ is in PUBLIC_PATHS so middleware
    # doesn't enforce auth here; we must check explicitly.
    if settings.TWICC_PASSWORD_HASH and not request.session.get("authenticated"):
        return JsonResponse({"error": "Session authentication required"}, status=401)

    token = settings.TWICC_API_TOKEN
    if not token:
        return JsonResponse({"error": "No API token configured"}, status=404)

    return JsonResponse({"token": token})


def api_token_regenerate(request):
    """POST /api/auth/token/regenerate/ - Generate a new API token.

    Writes the new token to .env and updates the runtime setting.
    Existing connections using the old token will fail on next request.
    Requires session authentication.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if settings.TWICC_PASSWORD_HASH and not request.session.get("authenticated"):
        return JsonResponse({"error": "Session authentication required"}, status=401)

    new_token = secrets.token_hex(32)

    # Update .env file
    _update_env_token(new_token)

    # Update runtime setting
    settings.TWICC_API_TOKEN = new_token
    os.environ["TWICC_API_TOKEN"] = new_token

    logger.info("API token regenerated by %s", request.META.get("REMOTE_ADDR"))
    return JsonResponse({"token": new_token})


def _update_env_token(new_token: str) -> None:
    """Update or append TWICC_API_TOKEN in the .env file."""
    env_path = get_env_path()
    try:
        content = env_path.read_text() if env_path.exists() else ""
    except OSError:
        content = ""

    new_line = f"TWICC_API_TOKEN={new_token}"
    pattern = r"^TWICC_API_TOKEN=.*$"
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n{new_line}\n"

    env_path.write_text(content)
