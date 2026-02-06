"""Password authentication middleware.

When TWICC_PASSWORD_HASH is set, all requests (except login, static files)
require an authenticated session. Unauthenticated requests get a 401 response.

When TWICC_PASSWORD_HASH is empty/unset, all requests pass through (no protection).
"""

import logging

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Paths that are always accessible (no auth required)
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
)


class PasswordAuthMiddleware:
    """Middleware that enforces password authentication via session.

    Checks request.session["authenticated"] for all requests except
    public paths. Returns 401 JSON for API requests, 401 JSON for
    SPA requests (frontend handles redirect to login).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.password_required = bool(settings.TWICC_PASSWORD_HASH)
        if self.password_required:
            logger.info("Password protection enabled")
        else:
            logger.info("Password protection disabled (TWICC_PASSWORD_HASH not set)")

    def __call__(self, request):
        # No password configured = no protection
        if not self.password_required:
            return self.get_response(request)

        # Allow public paths
        if any(request.path.startswith(p) for p in PUBLIC_PATHS):
            return self.get_response(request)

        # Allow non-API paths (SPA catch-all serves index.html which contains
        # no sensitive data; Vue Router handles the login redirect client-side)
        if not request.path.startswith("/api/"):
            return self.get_response(request)

        # Check session authentication for API requests
        if not request.session.get("authenticated"):
            return JsonResponse(
                {"error": "Authentication required"},
                status=401,
            )

        return self.get_response(request)
