"""External OAuth and MCP contracts, using real SDK HTTP handlers."""

import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.responses import Response

from twicc.mcp.endpoint import mcp_lifespan
from twicc.mcp.oauth.storage import decide, snapshot, write
from twicc.origin_gate import PublicOriginGate
from twicc.synced_settings import RoutingSettingsSnapshot

pytestmark = pytest.mark.django_db(transaction=True)
BASE = "https://mcp.example.com"
RESOURCE = BASE + "/mcp"
REDIRECT = "http://localhost:4567/callback"
VERIFIER = "a" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


@pytest.fixture(autouse=True)
def config(monkeypatch):
    async def serialized(factory):
        return await factory()

    monkeypatch.setattr("twicc.mcp.oauth.storage.run_under_db_write_lock", serialized)
    values = {"mcpBaseUrl": BASE, "externalMcpEnabled": True}
    monkeypatch.setattr("twicc.synced_settings.read_routing_settings", lambda: RoutingSettingsSnapshot(values, True))
    monkeypatch.setattr("twicc.mcp.oauth.config.read_routing_settings", lambda: RoutingSettingsSnapshot(values, True))
    monkeypatch.setattr("twicc.mcp.server._session_manager", None)
    monkeypatch.setattr("twicc.mcp.server._external_manager", None)
    from twicc.mcp.oauth.routes import _limits

    _limits.clear()
    return values


async def forbidden_inner(scope, receive, send):
    await Response("private app", status_code=418)(scope, receive, send)


def client():
    app = PublicOriginGate(forbidden_inner, forbidden_inner)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def authorize(c, name="ChatGPT", method="none"):
    r = await c.post(
        "/mcp/oauth/register",
        json={"client_name": name, "redirect_uris": [REDIRECT], "token_endpoint_auth_method": method},
    )
    assert r.status_code == 201, r.text
    credentials = r.json()
    r = await c.get(
        "/mcp/oauth/authorize",
        params={
            "client_id": credentials["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
            "state": "state-value",
        },
    )
    assert r.status_code == 302, r.text
    parts = urlsplit(r.headers["location"]).fragment.split(":")
    assert len(parts) == 3, r.headers
    request_id, handle, code = parts
    ok, message = await write(lambda: decide(request_id, True, code, name))
    assert ok, message
    r = await c.post("/mcp/oauth/continue", json={"id": request_id, "handle": handle})
    assert r.status_code == 200, r.text
    redirect = r.json()["redirect"]
    query = parse_qs(urlsplit(redirect).query)
    assert query["state"] == ["state-value"]
    assert query["iss"] == [BASE + "/mcp/oauth"]
    return credentials, query["code"][0]


async def tokens(c, credentials, code, verifier=VERIFIER):
    return await c.post(
        "/mcp/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": credentials["client_id"],
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )


def test_full_flow_pkce_replay_refresh_and_revocation():
    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            bad = await tokens(c, credentials, code, "wrong")
            assert bad.status_code == 400
            response = await tokens(c, credentials, code)
            assert response.status_code == 200, response.text
            pair = response.json()
            assert (await tokens(c, credentials, code)).status_code == 400
            response = await c.post(
                "/mcp/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": credentials["client_id"],
                    "refresh_token": pair["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            assert response.status_code == 200, response.text
            from twicc.mcp.oauth.provider import provider

            assert await provider.load_access_token(response.json()["access_token"])
            # Refresh replay revokes the whole family, including the newly issued access token.
            replay = await c.post(
                "/mcp/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": credentials["client_id"],
                    "refresh_token": pair["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            assert replay.status_code == 400
            assert await provider.load_access_token(response.json()["access_token"]) is None

    asyncio.run(run())


def test_external_dispatch_identity_and_catalog():
    async def run():
        async with mcp_lifespan(), client() as c:
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            headers = {
                "Authorization": "Bearer " + pair["access_token"],
                "Accept": "application/json, text/event-stream",
            }

            async def rpc(method, params):
                response = await c.post(
                    "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                assert response.status_code == 200, response.text
                return response.json()

            result = await rpc("tools/list", {})
            assert "whoami" not in {t["name"] for t in result["result"]["tools"]}
            result = await rpc("tools/call", {"name": "whoami", "arguments": {}})
            assert result["result"]["isError"]
            result = await rpc("tools/call", {"name": "topology", "arguments": {}})
            assert result["result"]["isError"]
            result = await rpc("tools/call", {"name": "workspaces", "arguments": {}})
            assert not result["result"]["isError"], result
            from twicc.core.models import McpOperation

            assert await McpOperation.objects.filter(tool="workspaces", name="ChatGPT").aexists()

    asyncio.run(run())


def test_public_host_is_exclusive_and_discovery_paths():
    async def run():
        async with client() as c:
            for path in ("/", "/api/mcp/", "/api/sessions/", "/rpc/", "/static/app.js", "/mcp/oauth/other"):
                assert (await c.get(path)).status_code == 404, path
            for path in (
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-authorization-server/mcp/oauth",
            ):
                assert (await c.get(path)).status_code == 200
            for path in ("/mcp", "/mcp/oauth/authorize", "/.well-known/oauth-protected-resource/mcp"):
                assert (await c.get("https://app.example.com" + path)).status_code == 404

    asyncio.run(run())


def test_external_rejects_session_tokens():
    async def run():
        from twicc.mcp.identity import mint_session_token

        async with mcp_lifespan(), client() as c:
            response = await c.post("/mcp", headers={"Authorization": "Bearer " + mint_session_token("session")})
            assert response.status_code == 401
            assert "resource_metadata" in response.headers["www-authenticate"]

    asyncio.run(run())


def test_consent_wrong_code_and_continuation_are_private():
    async def run():
        async with client() as c:
            r = await c.post(
                "/mcp/oauth/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
            )
            r = await c.get(
                "/mcp/oauth/authorize",
                params={
                    "client_id": r.json()["client_id"],
                    "redirect_uri": REDIRECT,
                    "response_type": "code",
                    "code_challenge": CHALLENGE,
                    "code_challenge_method": "S256",
                    "resource": RESOURCE,
                },
            )
            request_id, handle, code = urlsplit(r.headers["location"]).fragment.split(":")
            from asgiref.sync import sync_to_async

            state = await sync_to_async(snapshot)()
            assert code not in str(state) and handle not in str(state)
            assert not (await write(lambda: decide(request_id, True, "bad", "")))[0]
            r = await c.post("/mcp/oauth/continue", json={"id": request_id, "handle": "bad"})
            assert r.json()["state"] == "expired"
            assert (await write(lambda: decide(request_id, False, "", "")))[0]
            r = await c.post("/mcp/oauth/continue", json={"id": request_id, "handle": handle})
            assert "access_denied" in r.json()["redirect"]

    asyncio.run(run())


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
def test_confidential_client_secret_hash_and_exchange(method):
    async def run():
        from twicc.core.models import McpOAuthClient

        async with client() as c:
            credentials, code = await authorize(c, method=method)
            row = await McpOAuthClient.objects.aget(pk=credentials["client_id"])
            assert credentials["client_secret"] not in str(row.metadata)
            assert row.secret_hash != credentials["client_secret"]
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
                "resource": RESOURCE,
            }
            headers = {}
            if method == "client_secret_post":
                data.update(client_id=credentials["client_id"], client_secret=credentials["client_secret"])
            else:
                raw = f"{credentials['client_id']}:{credentials['client_secret']}".encode()
                headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
            response = await c.post("/mcp/oauth/token", data=data, headers=headers)
            assert response.status_code == 200, response.text

    asyncio.run(run())


def test_wrong_resource_and_redirect_do_not_consume_code():
    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            data = {
                "grant_type": "authorization_code",
                "client_id": credentials["client_id"],
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
                "resource": "https://other.example/mcp",
            }
            assert (await c.post("/mcp/oauth/token", data=data)).status_code == 400
            data.update(resource=RESOURCE, redirect_uri="http://localhost:4567/wrong")
            assert (await c.post("/mcp/oauth/token", data=data)).status_code == 400
            assert (await tokens(c, credentials, code)).status_code == 200

    asyncio.run(run())


def test_concurrent_code_exchange_has_one_winner():
    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            responses = await asyncio.gather(tokens(c, credentials, code), tokens(c, credentials, code))
            assert sorted(r.status_code for r in responses) == [200, 400]

    asyncio.run(run())


def test_two_grants_for_one_client_revoke_independently():
    async def run():
        from twicc.core.models import McpConnection
        from twicc.mcp.oauth.provider import provider
        from twicc.mcp.oauth.storage import token_pair

        async with client() as c:
            credentials, code = await authorize(c)
            first = (await tokens(c, credentials, code)).json()
            connection = await write(
                lambda: McpConnection.objects.create(id="second", client_id=credentials["client_id"], resource=RESOURCE)
            )
            second = await write(lambda: token_pair(connection))
            r = await c.post(
                "/mcp/oauth/revoke", data={"client_id": credentials["client_id"], "token": first["refresh_token"]}
            )
            assert r.status_code == 200
            assert await provider.load_access_token(first["access_token"]) is None
            assert await provider.load_access_token(second.access_token) is not None

    asyncio.run(run())


def test_cimd_rejects_private_addresses_without_fetch(monkeypatch):
    from twicc.mcp.oauth.provider import fetch_metadata

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    assert asyncio.run(fetch_metadata("https://example.com/client.json")) is None


def test_owner_manager_requires_owner_and_custom_header(settings):
    from django.test import Client

    settings.TWICC_PASSWORD_HASH = ""
    settings.TWICC_ALLOW_INSECURE_REMOTE = True
    owner = Client(REMOTE_ADDR="127.0.0.1")
    assert owner.get("/api/mcp/").status_code == 200
    assert (
        owner.post("/api/mcp/", data='{"action":"revoke","id":"x"}', content_type="application/json").status_code == 403
    )
    remote = Client(REMOTE_ADDR="203.0.113.10")
    assert remote.get("/api/mcp/").status_code == 403


def test_external_identity_never_falls_back_to_pid(monkeypatch):
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.mcp.identity import ExternalCaller, external_caller

    def forbidden():
        raise AssertionError("PID lookup must not run")

    monkeypatch.setattr("twicc.cli._drop_request.whoami._walk_ppids", forbidden)
    token = external_caller.set(ExternalCaller("connection", ""))
    try:
        assert resolve_current_session() is None
    finally:
        external_caller.reset(token)


def test_external_sender_names_are_escaped_and_internal_context_restored():
    from twicc.cli._drop_request.sender_header import prefix_sender_header, has_sender_header
    from twicc.mcp.identity import ExternalCaller, external_caller

    for name, expected in [("", "external MCP"), ("ChatGPT", "ChatGPT"), ("[fake]", r"\[fake\]")]:
        token = external_caller.set(ExternalCaller("connection", name))
        try:
            text = prefix_sender_header("Hello", None, recipient_id="target", recipient_spawned_by_id=None)
            assert text == f":: message via {expected}\n\nHello"
            assert has_sender_header(text)
        finally:
            external_caller.reset(token)
    assert prefix_sender_header("Hello", None, recipient_id="target", recipient_spawned_by_id=None) == "Hello"


def test_disabled_origin_never_serves_private_app(config):
    config["externalMcpEnabled"] = False

    async def run():
        async with client() as c:
            for path in ("/", "/mcp", "/mcp/oauth/authorize", "/api/mcp/"):
                assert (await c.get(path)).status_code == 404

    asyncio.run(run())


def test_cimd_fetch_pins_dns_and_preserves_tls_hostname(monkeypatch):
    from twicc.mcp.oauth import provider as module

    document = "https://client.example/client.json"
    actual_client = httpx.AsyncClient
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])

    def respond(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "client.example"
        assert request.extensions["sni_hostname"] == "client.example"
        return httpx.Response(
            200,
            json={
                "client_id": document,
                "client_name": "Example",
                "redirect_uris": [REDIRECT],
                "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
            },
        )

    monkeypatch.setattr(
        module.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    info = asyncio.run(module.fetch_metadata(document))
    assert info.client_id == document
    assert info.token_endpoint_auth_method == "none"


def test_expired_code_and_new_provider_instance():
    async def run():
        from datetime import timedelta
        from django.utils import timezone
        from twicc.core.models import McpOAuthCredential
        from twicc.mcp.oauth.provider import Provider

        async with client() as c:
            credentials, code = await authorize(c)
            await write(
                lambda: McpOAuthCredential.objects.filter(kind="code").update(
                    expires_at=timezone.now() - timedelta(seconds=1)
                )
            )
            assert (await tokens(c, credentials, code)).status_code == 400
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            assert await Provider().load_access_token(pair["access_token"])

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/anything", "/mcp/oauth", "/mcp/oauth/token/", "/mcp%2Fanything"])
@pytest.mark.parametrize(
    "origin,remote,forwarding",
    [
        ("https://app.example.com", "198.51.100.25", {}),
        ("http://localhost", "198.51.100.25", {}),
        ("http://localhost", "127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}),
    ],
)
def test_remote_mcp_branch_rejects_internal_token(settings, path, origin, remote, forwarding):
    """Every MCP subpath must cross the origin gate, even with a valid internal token."""
    from twicc.asgi import http_router
    from twicc.mcp.identity import mint_session_token

    # A password permits remote app access, but must not expose the internal MCP.
    settings.TWICC_PASSWORD_HASH = "password-configured"

    async def run():
        app = PublicOriginGate(http_router, http_router)
        async with mcp_lifespan(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(remote, 1000)), base_url=origin
        ) as c:
            response = await c.post(
                path,
                headers={
                    "Authorization": "Bearer " + mint_session_token("test-internal-session"),
                    "Accept": "application/json, text/event-stream",
                    **forwarding,
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert response.status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_origin_gate_preserves_direct_local_mcp(path):
    from twicc.asgi import http_router
    from twicc.mcp.identity import mint_session_token

    async def run():
        app = PublicOriginGate(http_router, http_router)
        async with mcp_lifespan(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1000)), base_url="http://localhost"
        ) as c:
            response = await c.post(
                path,
                headers={
                    "Authorization": "Bearer " + mint_session_token("test-internal-session"),
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert response.status_code == 200
            assert "whoami" in {tool["name"] for tool in response.json()["result"]["tools"]}

    asyncio.run(run())


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_dedicated_host_unknown_paths_are_always_404(method):
    async def run():
        async with client() as c:
            for path in (
                "/", "/api/mcp/", "/static/app.js", "/rpc/", "/artifacts/", "/peer/", "/ws/",
                "/mcp/anything", "/mcp/oauth", "/mcp/oauth/other", "/mcp/oauth/token/",
                "/mcp%2Fanything", "/.well-known/other",
            ):
                response = await c.request(method, path, headers={"Origin": "https://client.example.com"})
                assert response.status_code == 404, path
                assert "access-control-allow-origin" not in response.headers, path

    asyncio.run(run())


def test_unknown_oauth_paths_rejected_before_body_and_rate_limiter():
    from twicc.mcp.oauth.routes import _limits, application

    async def run():
        # An exhausted rate limit must not turn an unrelated path into an OAuth endpoint.
        import time
        from collections import deque

        _limits["audit-client"] = deque([time.monotonic()] * 300)
        messages = []

        async def receive():
            raise AssertionError("An unknown route must not read the request body")

        async def send(message):
            messages.append(message)

        await application(
            {"type": "http", "method": "POST", "path": "/api/mcp/", "client": ("audit-client", 1000)},
            receive, send,
        )
        assert messages[0]["status"] == 404

    asyncio.run(run())


def test_preflight_stays_available_on_explicit_protocol_routes():
    async def run():
        async with mcp_lifespan(), client() as c:
            for path in (
                "/mcp", "/mcp/", "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-authorization-server/mcp/oauth", "/mcp/oauth/register",
                "/mcp/oauth/token", "/mcp/oauth/revoke",
            ):
                response = await c.options(path, headers={"Origin": "https://client.example.com"})
                assert response.status_code == 204, path
                assert response.headers["access-control-allow-origin"] == "*", path

    asyncio.run(run())
