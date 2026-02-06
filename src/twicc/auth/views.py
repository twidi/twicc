"""Authentication views for password-based login.

Provides login, logout, and auth status check endpoints.
All endpoints are under /api/auth/ and always accessible (no auth required).
"""

import hmac
import logging

import orjson
from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def auth_check(request):
    """GET /api/auth/check/ - Check if user is authenticated.

    Returns:
        - {"authenticated": true, "password_required": true} if authenticated
        - {"authenticated": true, "password_required": false} if no password configured
        - {"authenticated": false, "password_required": true} if not authenticated
    """
    password_required = bool(settings.TWICC_PASSWORD)
    if not password_required:
        return JsonResponse({"authenticated": True, "password_required": False})

    authenticated = request.session.get("authenticated", False)
    return JsonResponse({
        "authenticated": authenticated,
        "password_required": True,
    })


def login(request):
    """POST /api/auth/login/ - Authenticate with password.

    Body: {"password": "the_password"}

    On success, sets session["authenticated"] = True and returns 200.
    On failure, returns 401.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    if not settings.TWICC_PASSWORD:
        return JsonResponse({"error": "No password configured"}, status=400)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    password = data.get("password", "")

    # Constant-time comparison to prevent timing attacks
    if hmac.compare_digest(password, settings.TWICC_PASSWORD):
        request.session["authenticated"] = True
        logger.info("Successful login from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"authenticated": True})
    else:
        logger.warning("Failed login attempt from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"error": "Invalid password"}, status=401)


def logout(request):
    """POST /api/auth/logout/ - Clear authentication.

    Flushes the session entirely.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    request.session.flush()
    return JsonResponse({"authenticated": False})
