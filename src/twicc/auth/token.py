"""API token verification utility.

Provides constant-time token comparison for Bearer token authentication.
Used by both HTTP middleware and WebSocket authentication.
"""

import hmac

from django.conf import settings


def verify_api_token(token: str) -> bool:
    """Check if the given token matches the configured API token.

    Uses hmac.compare_digest for constant-time comparison to prevent
    timing attacks. Returns False if no API token is configured.
    """
    configured_token = settings.TWICC_API_TOKEN
    if not configured_token or not token:
        return False
    return hmac.compare_digest(token, configured_token)


def extract_bearer_token(request) -> str | None:
    """Extract Bearer token from the Authorization header.

    Returns the token string if present, None otherwise.
    """
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None
