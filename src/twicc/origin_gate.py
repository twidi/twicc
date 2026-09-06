"""ASGI gate enforcing the common public-origin routing (peer-origin-routing
design §9-§11).

A single :class:`PublicOriginGate` replaces the former ``ShareHostGate``. It
wraps the application ABOVE BlackNoise, so it runs before static files, Django
and its SPA fallback, the raw ``/mcp`` endpoint, and application WebSockets.
Per request it reads the four origins from the active in-process cache, builds
the pure routing policy, classifies the request authority + path, and executes
the result:

  Share hostname                → ShareOnlyApp (existing Share-only policy)
  dedicated Peer authority      → only /peer/ HTTP; everything else 404/4404
  dedicated MCP authority       → only /mcp and explicit MCP OAuth/discovery routes
  shared External+Peer authority→ full app, /peer/ included
  every other authority         → full app, but never /peer/
  quarantined / invalid Host    → plain 404, WebSocket close 4404
  unavailable routing settings → plain 404, WebSocket close 4404

Rejections answer the plain ``404 Not found`` (or close ``4404``) without
calling the inner application and without revealing the configured addresses.
The gate never repairs or writes settings.
The received Host header is authoritative. Proxies must preserve the configured
hostname; the gate does not recover a rewritten host from forwarding headers.
"""

from __future__ import annotations

import logging

from twicc.core.services.origin_policy import (
    SHARE_ONLY_PREFIXES,
    classify_request,
    get_origin_policy,
    request_authority_from_scope,
)

logger = logging.getLogger(__name__)


def _share_only_allowed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SHARE_ONLY_PREFIXES) or path == "/favicon.ico"


async def _reply_404(send):
    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Not found"})


async def _reject_request(stype, send):
    if stype == "websocket":
        await send({"type": "websocket.close", "code": 4404})
        return
    await _reply_404(send)


async def _reply_204(send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _raw_host_header(scope) -> str:
    """The raw ``Host`` header value(s), for the operator log only."""
    values = [value for name, value in scope.get("headers") or () if name == b"host"]
    return ", ".join(value.decode("latin1", "replace") for value in values)


async def _reply_redirect(send, location: str):
    # 302 Found — TEMPORARY on purpose: the share-host root points at /share/ for now,
    # but a real homepage could live there later, so it must not be cached permanently.
    await send({"type": "http.response.start", "status": 302,
                "headers": [(b"location", location.encode("latin1")),
                            (b"cache-control", b"no-store"),
                            (b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b""})


class ShareOnlyApp:
    """Wrap an ASGI app, exposing ONLY the share surface (used on the share host)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        path = scope.get("path", "")
        if stype == "http":
            if path == "/":
                # Share-host root → the recent-shares homepage (temporary redirect).
                return await _reply_redirect(send, "/share/")
            if path == "/favicon.ico":
                return await _reply_204(send)
            if not _share_only_allowed(path):
                return await _reply_404(send)
            return await self.inner(scope, receive, send)
        if stype == "websocket":
            if not path.startswith("/ws/share/"):
                await send({"type": "websocket.close", "code": 4404})
                return
            return await self.inner(scope, receive, send)
        # lifespan et al. pass through.
        return await self.inner(scope, receive, send)


class PublicOriginGate:
    """Route every HTTP request and WebSocket by its request authority against
    the live External / Share / Peer / MCP settings."""

    def __init__(self, full_app, share_only_app):
        self.full_app = full_app
        self.share_only_app = share_only_app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket"):
            return await self.full_app(scope, receive, send)
        path = scope.get("path", "")
        authority = request_authority_from_scope(scope)
        if authority is None:
            # ``debug``, never ``warning``: this gate faces the internet, so one
            # record per rejected request would be a log-flood vector. The design
            # (§11) hides the reason from the RESPONSE, not from the operator's
            # own log — the reply below stays the plain 404 / close 4404.
            logger.debug("Origin gate: unusable Host header %r (%s %s)", _raw_host_header(scope), stype, path)
            return await _reject_request(stype, send)
        from twicc.synced_settings import read_routing_settings

        try:
            snapshot = read_routing_settings()
            if not snapshot.available:
                return await _reject_request(stype, send)
            policy = get_origin_policy(snapshot.settings)
        except Exception:
            logger.exception("Public-origin routing settings are unavailable")
            return await _reject_request(stype, send)
        # MCP owns a dedicated hostname, including when disabled or invalid.
        from twicc.mcp.oauth.config import base_url, protocol_path
        from twicc.core.services.origin_policy import recognize_authority
        from twicc.auth.local_access import scope_is_local
        mcp_host = recognize_authority(snapshot.settings.get("mcpBaseUrl", ""))
        if (mcp_host and authority.hostname == mcp_host.hostname
                and authority.authority != policy.external_authority):
            if stype != "http" or not base_url() or authority.authority != mcp_host.authority:
                return await _reject_request(stype, send)
            if path in ("/mcp", "/mcp/"):
                from twicc.mcp.endpoint import handle_mcp
                return await handle_mcp({**scope, "twicc_external_mcp": True}, receive, send)
            from twicc.mcp.oauth.routes import application as oauth_application
            return await oauth_application(scope, receive, send)
        if protocol_path(path):
            # Keep the agents' direct loopback route. A proxy is not a local caller.
            if (path not in ("/mcp", "/mcp/") or stype != "http" or not scope_is_local(scope)
                    or authority.hostname not in ("127.0.0.1", "localhost", "::1")):
                return await _reject_request(stype, send)
        surface = classify_request(policy, authority, path, stype)
        if surface == "share_surface":
            return await self.share_only_app(scope, receive, send)
        if surface == "inner_app":
            return await self.full_app(scope, receive, send)
        # Same reasoning as above: operator-only diagnostic, at debug level.
        logger.debug("Origin gate: rejected authority %s (%s %s)", authority.authority, stype, path)
        return await _reject_request(stype, send)
