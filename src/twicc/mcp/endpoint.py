"""Raw-ASGI MCP entry with separate internal and external authentication.

The origin gate marks requests from the dedicated public MCP host. Those
requests require OAuth. Direct local requests retain automatic session-token
or API-token access. Application cookies never authenticate MCP calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import orjson

from twicc.auth.local_access import scope_remote_access_blocked
from twicc.auth.tokens import verify_token
from twicc.mcp import mcp_enabled
from twicc.mcp.identity import TOKEN_PREFIX, resolve_session_token
from twicc.mcp.server import get_session_manager

logger = logging.getLogger(__name__)

_started = False


def _bearer(scope) -> str:
    for key, value in scope.get("headers") or ():
        if key == b"authorization":
            return value.decode("latin-1").removeprefix("Bearer ").strip()
    return ""


def _authorized(scope) -> bool:
    token = _bearer(scope)
    if token.startswith(TOKEN_PREFIX):
        return resolve_session_token(token) is not None
    if token:
        return verify_token(token) is not None
    return False


async def _plain_response(send, status: int, body: dict, *, headers=()) -> None:
    payload = orjson.dumps(body)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), *headers],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def handle_mcp(scope, receive, send) -> None:
    """ASGI handler for every /mcp request."""
    if scope["type"] != "http":  # pragma: no cover — router only sends http
        return
    if not mcp_enabled() or not _started:
        await _plain_response(send, 503, {"error": "MCP server not available."})
        return
    if scope.get("twicc_external_mcp"):
        from twicc.mcp.oauth.provider import provider
        from twicc.mcp.oauth.config import base_url, RESOURCE_METADATA
        from twicc.mcp.identity import external_caller, ExternalCaller
        from twicc.core.models import McpConnection
        from starlette.responses import Response

        if scope.get("method") == "OPTIONS":
            await Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version, MCP-Session-Id",
                },
            )(scope, receive, send)
            return

        async def cors_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"access-control-allow-origin", b"*"),
                        (b"access-control-expose-headers", b"WWW-Authenticate, MCP-Session-Id"),
                    ]
                )
            await send(message)

        access = await provider.load_access_token(_bearer(scope))
        if access is None:
            await _plain_response(
                cors_send,
                401,
                {"error": "OAuth authorization required."},
                headers=[
                    (
                        b"www-authenticate",
                        (
                            'Bearer resource_metadata="' + base_url() + RESOURCE_METADATA + '", scope="twicc:full"'
                        ).encode(),
                    ),
                ],
            )
            return
        connection = await McpConnection.objects.aget(pk=access.subject)
        token = external_caller.set(ExternalCaller(connection.id, connection.name))
        try:
            from twicc.mcp.server import get_external_session_manager

            await get_external_session_manager().handle_request(scope, receive, cors_send)
        finally:
            external_caller.reset(token)
        return
    if scope_remote_access_blocked(scope):
        await _plain_response(send, 403, {"error": "Remote access is disabled."})
        return
    # ``_authorized`` reads the token/secret files; keep that off the event loop,
    # matching the /rpc/ auth middleware's sync_to_async convention.
    if not await asyncio.to_thread(_authorized, scope):
        await _plain_response(
            send,
            401,
            {"error": "A TwiCC MCP session token or API token is required."},
            headers=[(b"www-authenticate", b"Bearer")],
        )
        return
    # The session manager expects to own the path; it treats the mount point
    # as the endpoint regardless of the exact path value.
    await get_session_manager().handle_request(scope, receive, send)


@contextlib.asynccontextmanager
async def mcp_lifespan():
    """Run the session manager's task group (call once, from run.py or tests)."""
    global _started
    manager = get_session_manager()
    from twicc.mcp.server import get_external_session_manager

    async with manager.run(), get_external_session_manager().run():
        _started = True
        logger.info("MCP server ready at /mcp")
        try:
            yield
        finally:
            _started = False


async def start_mcp_task(shutdown_event) -> None:
    """run.py background task: keep the session manager alive until shutdown."""
    if not mcp_enabled():
        logger.info("MCP server disabled (TWICC_NO_MCP)")
        return
    async with mcp_lifespan():
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=60)
            except TimeoutError:
                from twicc.mcp.oauth.storage import cleanup, changed

                try:
                    if await cleanup():
                        await changed()
                except Exception:
                    logger.exception("MCP OAuth cleanup failed")
