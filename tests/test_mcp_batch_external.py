"""Trusted external batch grants, using isolated configuration and real ORM queries."""

import asyncio
from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.models import McpConnection, McpOAuthClient, McpOAuthCredential
from twicc.mcp import identity
from twicc.mcp.oauth import provider as oauth_provider
from twicc.mcp.oauth.storage import digest
from twicc.synced_settings import RoutingSettingsSnapshot

BASE = "https://mcp.example.com"
RESOURCE = BASE + "/mcp"


@pytest.fixture
def config(monkeypatch, settings):
    from twicc.mcp.oauth import config as oauth_config

    settings.TWICC_PASSWORD_HASH = "test-password"
    monkeypatch.delenv("TWICC_NO_MCP", raising=False)
    values = {"mcpBaseUrl": BASE, "externalMcpEnabled": True}
    monkeypatch.setattr(oauth_config, "read_routing_settings", lambda: RoutingSettingsSnapshot(values, True))

    async def serialized(factory):
        return await factory()

    monkeypatch.setattr("twicc.mcp.oauth.storage.run_under_db_write_lock", serialized)
    return values


@pytest.fixture
def connection(config):
    client = McpOAuthClient.objects.create(id="batch-test-client")
    return McpConnection.objects.create(id="batch-test-connection", client=client, resource=RESOURCE)


def test_batch_contexts_default_to_none_and_remain_task_local():
    assert identity.external_grant.get() is None
    assert identity.batch_correlation.get() is None

    async def run():
        ready = asyncio.Event()
        entered = 0

        async def child(index):
            nonlocal entered
            grant = identity.ExternalGrant(str(index), RESOURCE, 12345)
            correlation = identity.BatchCorrelation(f"batch-{index}", "call", index)
            grant_token = identity.external_grant.set(grant)
            correlation_token = identity.batch_correlation.set(correlation)
            try:
                entered += 1
                if entered == 2:
                    ready.set()
                await ready.wait()
                assert identity.external_grant.get() == grant
                assert identity.batch_correlation.get() == correlation
            finally:
                identity.batch_correlation.reset(correlation_token)
                identity.external_grant.reset(grant_token)
            assert identity.external_grant.get() is None
            assert identity.batch_correlation.get() is None

        await asyncio.gather(child(0), child(1))
        assert identity.external_grant.get() is None
        assert identity.batch_correlation.get() is None

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", [
    "expired", "revoked", "disabled", "resource_changed", "connection_resource_changed", "missing", "kill_switch",
])
def test_live_grant_changes_invalidate_admission(connection, config, monkeypatch, change):
    expiry = int((timezone.now() + timedelta(hours=1)).timestamp())
    grant = identity.ExternalGrant(connection.id, RESOURCE, expiry)
    assert asyncio.run(oauth_provider.batch_grant_valid(grant)) is True
    connection.refresh_from_db()
    assert connection.last_used_at is None

    if change == "expired":
        grant = grant._replace(expires_at=int(timezone.now().timestamp()))
    elif change == "revoked":
        McpConnection.objects.filter(pk=connection.id).update(revoked_at=timezone.now())
    elif change == "disabled":
        config["externalMcpEnabled"] = False
    elif change == "resource_changed":
        config["mcpBaseUrl"] = "https://new.example.com"
    elif change == "connection_resource_changed":
        McpConnection.objects.filter(pk=connection.id).update(resource="https://other.example.com/mcp")
    elif change == "missing":
        connection.delete()
    elif change == "kill_switch":
        monkeypatch.setenv("TWICC_NO_MCP", "1")

    assert asyncio.run(oauth_provider.batch_grant_valid(grant)) is False


@pytest.mark.django_db(transaction=True)
def test_grant_lookup_failure_propagates_as_unavailable(connection, monkeypatch):
    from django.db import OperationalError
    from django.db.models.query import QuerySet

    async def unavailable(self):
        raise OperationalError("isolated database failure")

    monkeypatch.setattr(QuerySet, "aexists", unavailable)
    grant = identity.ExternalGrant(connection.id, RESOURCE, int(timezone.now().timestamp()) + 60)
    with pytest.raises(OperationalError):
        asyncio.run(oauth_provider.batch_grant_valid(grant))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["none", "revoked", "resource", "expired", "kind", "digest"])
def test_access_token_loading_preserves_credential_checks(connection, change):
    token = "synthetic-test-access-token"
    credential = McpOAuthCredential.objects.create(
        digest=digest(token), connection=connection, kind="access", expires_at=timezone.now() + timedelta(hours=1),
    )
    if change == "revoked":
        McpConnection.objects.filter(pk=connection.id).update(revoked_at=timezone.now())
    elif change == "resource":
        McpConnection.objects.filter(pk=connection.id).update(resource="https://other.example.com/mcp")
    elif change == "expired":
        McpOAuthCredential.objects.filter(pk=credential.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    elif change == "kind":
        McpOAuthCredential.objects.filter(pk=credential.pk).update(kind="refresh")
    elif change == "digest":
        token = "different-synthetic-token"

    loaded = asyncio.run(oauth_provider.provider.load_access_token(token))
    connection.refresh_from_db()
    if change == "none":
        assert loaded.subject == connection.id
        assert loaded.resource == RESOURCE
        assert loaded.expires_at == int(credential.expires_at.timestamp())
        assert connection.last_used_at is not None
    else:
        assert loaded is None
        assert connection.last_used_at is None


@pytest.fixture
def http_config(config, monkeypatch):
    from twicc.mcp import server
    from twicc.mcp.oauth import protection

    monkeypatch.setattr("twicc.synced_settings.read_routing_settings", lambda: RoutingSettingsSnapshot(config, True))
    monkeypatch.setattr(server, "_session_manager", None)
    monkeypatch.setattr(server, "_external_manager", None)
    monkeypatch.setattr(protection, "protection", protection.Protection())
    return config


def external_client():
    import httpx
    from starlette.responses import Response
    from twicc.origin_gate import PublicOriginGate

    async def private_app(scope, receive, send):
        await Response("private test app", status_code=418)(scope, receive, send)

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=PublicOriginGate(private_app, private_app)), base_url=BASE,
    )


async def authenticated_headers(client, name="Batch test client"):
    """Exercise registration, owner consent, PKCE exchange and bearer validation."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    from twicc.mcp.oauth.storage import decide, write

    redirect = "http://localhost:4567/callback"
    verifier = "a" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = await client.post("/mcp/oauth/register", json={
        "client_name": name, "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
    })
    assert response.status_code == 201, response.text
    client_id = response.json()["client_id"]
    response = await client.get("/mcp/oauth/authorize", params={
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": RESOURCE,
    })
    assert response.status_code == 302, response.text
    request_id, handle, verification = urlsplit(response.headers["location"]).fragment.split(":")
    ok, message = await write(lambda: decide(request_id, True, verification, name))
    assert ok, message
    response = await client.post("/mcp/oauth/continue", json={"id": request_id, "handle": handle})
    assert response.status_code == 200, response.text
    code = parse_qs(urlsplit(response.json()["redirect"]).query)["code"][0]
    response = await client.post("/mcp/oauth/token", data={
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "redirect_uri": redirect, "code_verifier": verifier, "resource": RESOURCE,
    })
    assert response.status_code == 200, response.text
    return {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Accept": "application/json, text/event-stream",
    }


async def rpc(client, headers, method, params):
    response = await client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    })
    assert response.status_code == 200, response.text
    assert "result" in response.json(), response.text
    return response.json()["result"]


async def call_batch(client, headers, name="batch", **arguments):
    return await rpc(client, headers, "tools/call", {"name": name, "arguments": arguments})


def assert_batch_output(result, schema):
    import jsonschema
    import orjson

    payload = result["structuredContent"]
    jsonschema.validate(payload, schema)
    assert orjson.loads(result["content"][0]["text"]) == payload
    return payload


@pytest.mark.django_db(transaction=True)
def test_authenticated_batch_grant_and_exact_provenance(http_config, monkeypatch):
    from twicc.core.models import McpOperation
    from twicc.mcp import server
    from twicc.mcp.endpoint import mcp_lifespan
    from twicc.cli._drop_request.whoami import forced_session_id

    original = server.execute_prepared
    observed = []

    async def capture(prepared, *, session_id, on_start=None):
        observed.append((identity.external_caller.get(), identity.external_grant.get(),
                         identity.batch_correlation.get(), session_id, forced_session_id.get()))
        return await original(prepared, session_id=session_id, on_start=on_start)

    monkeypatch.setattr(server, "execute_prepared", capture)

    async def run():
        async with mcp_lifespan(), external_client() as client:
            headers = await authenticated_headers(client)
            catalog = await rpc(client, headers, "tools/list", {})
            schemas = {tool["name"]: tool["outputSchema"] for tool in catalog["tools"]
                       if tool["name"] in {"batch", "batch_read"}}
            assert set(schemas) == {"batch", "batch_read"}
            response = await call_batch(client, headers, calls=[
                {"id": "listed", "name": "sessions", "arguments": {}},
                {"id": "missing", "name": "session", "arguments": {"session_id": "missing-test-session"}},
            ])
            payload = assert_batch_output(response, schemas["batch"])
            assert response["isError"] is False
            assert payload["summary"] == {"total": 2, "succeeded": 1, "failed": 1, "skipped": 0}
            assert len(observed) == 2
            connection = await McpConnection.objects.aget(name="Batch test client")
            credential = await McpOAuthCredential.objects.aget(connection=connection, kind="access")
            for index, (caller, grant, correlation, session_id, forced_id) in enumerate(observed):
                assert caller == identity.ExternalCaller(connection.id, "Batch test client")
                assert grant == identity.ExternalGrant(connection.id, RESOURCE, int(credential.expires_at.timestamp()))
                assert correlation == identity.BatchCorrelation(payload["batch_id"], ("listed", "missing")[index], index)
                assert session_id is None
                assert forced_id is None
            operations = [row async for row in McpOperation.objects.order_by("id")]
            assert [(row.tool, row.connection_id) for row in operations] == [
                ("sessions", connection.id), ("session", connection.id),
            ]
            assert operations[0].targets == {"_batch": {"id": payload["batch_id"], "call_id": "listed", "index": 0}}
            assert operations[1].targets == {
                "session_id": "missing-test-session",
                "_batch": {"id": payload["batch_id"], "call_id": "missing", "index": 1},
            }
            assert identity.external_caller.get() is None
            assert identity.external_grant.get() is None
            assert identity.batch_correlation.get() is None
            ordinary = await rpc(client, headers, "tools/call", {"name": "sessions", "arguments": {}})
            assert ordinary["isError"] is False
            assert (await McpOperation.objects.order_by("-id").afirst()).targets == {}

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("child", [
    {"id": "self", "name": "session", "arguments": {"session_id": "self"}},
    {"id": "who", "name": "whoami", "arguments": {}},
    {"id": "implicit", "name": "topology", "arguments": {}},
])
def test_authenticated_batch_rejects_external_identity_before_execution(http_config, child, monkeypatch):
    from twicc.core.models import McpOperation
    from twicc.mcp import server
    from twicc.mcp.endpoint import mcp_lifespan

    started = []
    original = server.execute_prepared

    async def capture(prepared, *, session_id, on_start=None):
        started.append(prepared.name)
        return await original(prepared, session_id=session_id, on_start=on_start)

    monkeypatch.setattr(server, "execute_prepared", capture)

    async def run():
        async with mcp_lifespan(), external_client() as client:
            headers = await authenticated_headers(client)
            catalog = await rpc(client, headers, "tools/list", {})
            schema = next(tool["outputSchema"] for tool in catalog["tools"] if tool["name"] == "batch")
            response = await call_batch(client, headers, calls=[
                {"id": "first", "name": "sessions", "arguments": {}}, child,
            ])
            payload = assert_batch_output(response, schema)
            assert response["isError"] is True
            assert payload["status"] == "rejected"
            assert payload["executed"] == 0
            assert payload["errors"][0]["index"] == 1
            assert started == []
            assert await McpOperation.objects.acount() == 0

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", ["batch", "batch_read"])
@pytest.mark.parametrize("failure", ["revoked", "lookup_error"])
def test_authenticated_batch_stops_new_children_after_authority_change(http_config, monkeypatch, name, failure):
    from django.db import OperationalError
    from twicc.core.models import McpOperation
    from twicc.mcp import server
    from twicc.mcp.endpoint import mcp_lifespan

    original = server.execute_prepared
    started = []

    async def capture(prepared, *, session_id, on_start=None):
        started.append(prepared.name)
        result = await original(prepared, session_id=session_id, on_start=on_start)
        if failure == "revoked":
            await McpConnection.objects.filter(pk=identity.external_grant.get().connection_id).aupdate(
                revoked_at=timezone.now(),
            )
        return result

    monkeypatch.setattr(server, "execute_prepared", capture)

    async def run():
        async with mcp_lifespan(), external_client() as client:
            headers = await authenticated_headers(client)
            # One local permit makes parallel-read admission order deterministic.
            server._batch_runtime.per_batch = 1
            check_grant = server._batch_runtime.check_grant

            async def checked(grant):
                if failure == "lookup_error" and started:
                    raise OperationalError("secret database error must not reach output")
                return await check_grant(grant)

            server._batch_runtime.check_grant = checked
            response = await call_batch(client, headers, name=name, on_error="continue", calls=[
                {"id": str(index), "name": "sessions", "arguments": {}} for index in range(3)
            ])
            payload = response["structuredContent"]
            assert response["isError"] is False
            assert payload["ok"] is False
            assert payload["summary"] == {"total": 3, "succeeded": 1, "failed": 0, "skipped": 2}
            code = "authorization_changed" if failure == "revoked" else "authorization_unavailable"
            assert payload["results"][0]["status"] == "success"
            for record in payload["results"][1:]:
                assert record["status"] == "skipped"
                assert record["error"]["code"] == code
                assert record["error"]["caused_by"] is None
                assert record["response"] is None
                assert record["outcome_unknown"] is False
                assert record["response_omitted"] is False
            assert "secret database" not in str(response)
            assert started == ["sessions"]
            assert await McpOperation.objects.acount() == 1

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_concurrent_authenticated_batches_keep_distinct_caller_contexts(http_config, monkeypatch):
    from twicc.core.models import McpOperation
    from twicc.mcp import server
    from twicc.mcp.endpoint import mcp_lifespan

    original = server.execute_prepared

    async def run():
        ready = asyncio.Event()
        seen = []

        async def capture(prepared, *, session_id, on_start=None):
            before = (identity.external_caller.get(), identity.external_grant.get(), identity.batch_correlation.get())
            seen.append(before)
            if len(seen) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
            assert before == (identity.external_caller.get(), identity.external_grant.get(), identity.batch_correlation.get())
            return await original(prepared, session_id=session_id, on_start=on_start)

        monkeypatch.setattr(server, "execute_prepared", capture)
        async with mcp_lifespan(), external_client() as client:
            headers = [await authenticated_headers(client, name) for name in ("First client", "Second client")]
            results = await asyncio.gather(*(
                call_batch(client, auth, name="batch_read", calls=[
                    {"id": "same-child-id", "name": "sessions", "arguments": {}},
                ]) for auth in headers
            ))
            assert all(result["structuredContent"]["ok"] for result in results)
            assert {caller.name for caller, _, _ in seen} == {"First client", "Second client"}
            assert len({grant.connection_id for _, grant, _ in seen}) == 2
            assert len({correlation.batch_id for _, _, correlation in seen}) == 2
            for caller, grant, correlation in seen:
                assert caller.connection_id == grant.connection_id
                operation = await McpOperation.objects.aget(connection_id=caller.connection_id)
                assert operation.name == caller.name
                assert operation.targets["_batch"] == {
                    "id": correlation.batch_id, "call_id": "same-child-id", "index": 0,
                }
            assert identity.external_caller.get() is None
            assert identity.external_grant.get() is None
            assert identity.batch_correlation.get() is None

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["expired", "disabled", "resource"])
def test_authority_is_rechecked_after_database_wait(connection, config, monkeypatch, change):
    from django.db.models.query import QuerySet

    now = timezone.now()
    grant = identity.ExternalGrant(connection.id, RESOURCE, int(now.timestamp()) + 60)
    async def delayed(self):
        if change == "expired":
            monkeypatch.setattr(oauth_provider.timezone, "now", lambda: now + timedelta(seconds=61))
        elif change == "disabled":
            config["externalMcpEnabled"] = False
        else:
            config["mcpBaseUrl"] = "https://changed.example.com"
        return True
    monkeypatch.setattr(QuerySet, "aexists", delayed)
    assert asyncio.run(oauth_provider.batch_grant_valid(grant)) is False
