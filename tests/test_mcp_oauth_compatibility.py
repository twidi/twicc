"""OAuth 2.1 token exchange and RFC 8252 loopback callbacks, without network I/O."""

import asyncio
import base64
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_mcp_external import CHALLENGE, REDIRECT, RESOURCE, VERIFIER, authorize, client, config
from twicc.mcp.oauth.storage import decide, write

pytestmark = pytest.mark.django_db(transaction=True)


async def exchange(c, credentials, code, *, redirect=None, verifier=VERIFIER, method="none"):
    data = {
        "grant_type": "authorization_code", "client_id": credentials["client_id"],
        "code": code, "code_verifier": verifier, "resource": RESOURCE,
    }
    if redirect is not None:
        data["redirect_uri"] = redirect
    headers = {}
    if method == "client_secret_post":
        data["client_secret"] = credentials["client_secret"]
    elif method == "client_secret_basic":
        data.pop("client_id")
        value = f"{credentials['client_id']}:{credentials['client_secret']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(value).decode()
    return await c.post("/mcp/oauth/token", data=data, headers=headers)


async def begin(c, registered, requested, *, method="GET"):
    response = await c.post("/mcp/oauth/register", json={
        "redirect_uris": [registered], "token_endpoint_auth_method": "none",
    })
    assert response.status_code == 201, response.text
    credentials = response.json()
    params = {
        "client_id": credentials["client_id"], "response_type": "code",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": RESOURCE,
    }
    if requested is not None:
        params["redirect_uri"] = requested
    response = (await c.get("/mcp/oauth/authorize", params=params) if method == "GET"
                else await c.post("/mcp/oauth/authorize", data=params))
    return credentials, response


async def finish(c, response):
    assert response.status_code == 302, response.text
    request_id, handle, verification = urlsplit(response.headers["location"]).fragment.split(":")
    ok, message = await write(lambda: decide(request_id, True, verification, "Compatibility test"))
    assert ok, message
    result = await c.post("/mcp/oauth/continue", json={"id": request_id, "handle": handle})
    assert result.status_code == 200, result.text
    callback = result.json()["redirect"]
    return callback, parse_qs(urlsplit(callback).query)["code"][0]


@pytest.mark.parametrize("method", ["none", "client_secret_post", "client_secret_basic"])
def test_token_exchange_without_redirect_keeps_pkce_and_single_use(config, method):
    async def run():
        async with client() as c:
            credentials, code = await authorize(c, method=method)
            bad = await exchange(c, credentials, code, verifier="wrong", method=method)
            assert bad.status_code == 400
            assert bad.json()["error"] == "invalid_grant"
            good = await exchange(c, credentials, code, method=method)
            assert good.status_code == 200, good.text
            assert (await exchange(c, credentials, code, method=method)).status_code == 400
    asyncio.run(run())


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_loopback_port_changes_only_at_authorization(config, host, method):
    async def run():
        async with client() as c:
            registered = f"http://{host}:45000/callback?app=one"
            requested = f"http://{host}:51234/callback?app=one"
            credentials, response = await begin(c, registered, requested, method=method)
            callback, code = await finish(c, response)
            assert callback.startswith(requested + "&")
            # The token exchange must use the selected port, not another allowed port.
            assert (await exchange(c, credentials, code, redirect=registered)).status_code == 400
            good = await exchange(c, credentials, code, redirect=requested)
            assert good.status_code == 200, good.text
    asyncio.run(run())


@pytest.mark.parametrize("requested", [
    "http://127.0.0.1:51234/other?app=one",
    "http://127.0.0.1:51234/callback?app=two",
    "http://127.0.0.2:51234/callback?app=one",
    "http://localhost:51234/callback?app=one",
    "http://[::1]:51234/callback?app=one",
    "https://127.0.0.1:51234/callback?app=one",
    "http://127.0.0.1.example.com:51234/callback?app=one",
    "http://user@127.0.0.1:51234/callback?app=one",
    "http://127.0.0.1:51234/callback?app=one#fragment",
    "http://127.0.0.1:51234/callback?app=one#",
])
def test_loopback_exception_never_changes_the_callback_target(config, requested):
    async def run():
        async with client() as c:
            _, response = await begin(c, "http://127.0.0.1:45000/callback?app=one", requested)
            assert response.status_code == 400, response.text
            assert "location" not in response.headers
    asyncio.run(run())


@pytest.mark.parametrize("registered,requested", [
    ("https://client.example.com:45000/callback", "https://client.example.com:51234/callback"),
    ("https://127.0.0.1:45000/callback", "https://127.0.0.1:51234/callback"),
    ("http://localhost:45000/callback", "http://localhost:51234/callback"),
    ("http://127.0.0.1:45000/callback", "http://127.0.0.1:51234/callback?"),
    ("http://127.0.0.1:45000/callback", "http://127.0.0.1:51234/callback#"),
])
def test_port_exception_preserves_other_callback_constraints(config, registered, requested):
    async def run():
        async with client() as c:
            _, response = await begin(c, registered, requested)
            assert response.status_code == 400
            assert "location" not in response.headers
    asyncio.run(run())


@pytest.mark.parametrize("redirect", [None, "https://client.example.com/callback"])
def test_implicit_authorization_redirect_accepts_optional_matching_token_redirect(config, redirect):
    async def run():
        async with client() as c:
            credentials, response = await begin(c, "https://client.example.com/callback", None)
            _, code = await finish(c, response)
            assert (await exchange(c, credentials, code, redirect="https://other.example.com/callback")).status_code == 400
            good = await exchange(c, credentials, code, redirect=redirect)
            assert good.status_code == 200, good.text
    asyncio.run(run())


@pytest.mark.parametrize("reason", ["expired", "revoked", "other_client", "unknown", "wrong_resource"])
def test_omitting_redirect_never_bypasses_grant_validation(config, reason):
    from datetime import timedelta
    from django.utils import timezone
    from twicc.core.models import McpConnection, McpOAuthCredential

    async def run():
        async with client() as c:
            credentials, code = await authorize(c)
            if reason == "expired":
                await write(lambda: McpOAuthCredential.objects.filter(kind="code").update(
                    expires_at=timezone.now() - timedelta(seconds=1),
                ))
            elif reason == "revoked":
                await write(lambda: McpConnection.objects.update(revoked_at=timezone.now()))
            elif reason == "other_client":
                response = await c.post("/mcp/oauth/register", json={
                    "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                })
                credentials = response.json()
            elif reason == "unknown":
                code = "unknown-code"
            elif reason == "wrong_resource":
                response = await c.post("/mcp/oauth/token", data={
                    "grant_type": "authorization_code", "client_id": credentials["client_id"],
                    "code": code, "code_verifier": VERIFIER, "resource": "https://other.example/mcp",
                })
                assert response.status_code == 400
                assert (await exchange(c, credentials, code)).status_code == 200
                return
            response = await exchange(c, credentials, code)
            assert response.status_code == 400, response.text
            assert response.json()["error"] == "invalid_grant"
    asyncio.run(run())


def test_optional_redirect_is_request_local_and_never_changes_stored_codes(config):
    from twicc.core.models import McpOAuthCredential

    async def run():
        async with client() as c:
            first, first_code = await authorize(c)
            second, second_code = await authorize(c)
            before = {row.pk: row.params async for row in McpOAuthCredential.objects.filter(kind="code")}
            responses = await asyncio.gather(
                exchange(c, first, first_code),
                exchange(c, second, second_code, redirect=REDIRECT),
            )
            assert [r.status_code for r in responses] == [200, 200]
            after = {row.pk: row.params async for row in McpOAuthCredential.objects.filter(kind="code")}
            assert before == after

            third, third_code = await authorize(c)
            competing = await asyncio.gather(
                exchange(c, third, third_code), exchange(c, third, third_code, redirect=REDIRECT),
            )
            assert sorted(r.status_code for r in competing) == [200, 400]
    asyncio.run(run())


def test_cimd_fresh_and_cached_clients_support_loopback_ports(config, monkeypatch):
    import httpx
    from twicc.mcp.oauth import provider as module
    from twicc.core.models import McpOAuthClient

    document = "https://client.example/client.json"
    registered = "http://127.0.0.1:45000/callback"
    actual_client = httpx.AsyncClient
    fetches = []

    def respond(request):
        fetches.append(str(request.url))
        return httpx.Response(200, json={
            "client_id": document, "redirect_uris": [registered],
            "token_endpoint_auth_method": "none",
        })

    async def run():
        async with client() as c:
            # Only the outbound metadata request uses MockTransport; no DNS or network I/O.
            monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
            monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: actual_client(
                transport=httpx.MockTransport(respond), **kw,
            ))
            for port in (51234, 52345):
                requested = f"http://127.0.0.1:{port}/callback"
                response = await c.get("/mcp/oauth/authorize", params={
                    "client_id": document, "redirect_uri": requested, "response_type": "code",
                    "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": RESOURCE,
                })
                callback, code = await finish(c, response)
                assert callback.startswith(requested + "?")
                result = await exchange(c, {"client_id": document}, code)
                assert result.status_code == 200, result.text
            assert len(fetches) == 1
            stored = await McpOAuthClient.objects.aget(pk=document)
            assert stored.metadata["redirect_uris"] == [registered]
    asyncio.run(run())
