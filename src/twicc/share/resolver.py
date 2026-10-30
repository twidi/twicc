"""Resolve a share token to a request context, applying the uniform-404 policy
and per-link password gate (design §6.1/§7)."""

from __future__ import annotations

from typing import NamedTuple

from django.http import Http404, HttpResponseRedirect, JsonResponse
from urllib.parse import urlencode

from twicc.core.services.share_tokens import aresolve_share, password_fingerprint
from twicc.core.session_availability import asession_unavailable_reason

SHARE_GRANTS_SESSION_KEY = "share_grants"


class ShareContext(NamedTuple):
    share: object
    session: object | None
    bookmark: object | None
    options: dict


class SharePasswordRequired(Exception):
    """Raised when a per-link password is set and the viewer has no valid grant.
    Carried up to the view, which renders the redirect (HTML) or 401 (API)."""

    def __init__(self, token: str):
        self.token = token


async def resolve_or_404(request, token: str) -> ShareContext:
    """Resolve ``token`` → active share, enforcing revoked/expired (uniform 404)
    and the per-link password grant. Raises ``Http404`` or ``SharePasswordRequired``."""
    from asgiref.sync import sync_to_async

    share = await aresolve_share(token)
    if share is None or not share.is_active():
        raise Http404("This link is not available.")

    # Hydrate the related target (select_related equivalent for async).
    if share.kind == "session":
        session = await sync_to_async(lambda: share.session)()
        bookmark = None
        if session is None or session.stale or await asession_unavailable_reason(session) is not None:
            raise Http404("This link is not available.")
    else:
        session = None
        bookmark = await sync_to_async(lambda: share.artifact_bookmark)()
        if bookmark is None:
            raise Http404("This link is not available.")

    # Per-link password grant (design §7.2).
    if share.password_hash:
        grants = await request.session.aget(SHARE_GRANTS_SESSION_KEY) or {}
        if grants.get(share.id) != password_fingerprint(share.password_hash):
            raise SharePasswordRequired(token)

    return ShareContext(share=share, session=session, bookmark=bookmark, options=share.options or {})


def password_required_response(request, token: str):
    """Render the response for a missing password grant: redirect to the share
    password page for a document navigation, 401 JSON for an API/asset request."""
    dest = request.headers.get("Sec-Fetch-Dest", "")
    accept = request.headers.get("Accept", "")
    wants_html = dest in ("document", "iframe") or "text/html" in accept
    if wants_html:
        query = urlencode({"redirect": request.get_full_path()})
        return HttpResponseRedirect(f"/share/{token}/auth?{query}")
    return JsonResponse({"error": "share_password_required"}, status=401)
