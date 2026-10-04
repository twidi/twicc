"""Authenticated SDK wire, lifespan ownership, and committed subscription cleanup."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

from asgiref.sync import sync_to_async
import anyio
from django.db import transaction
from django.utils import timezone
import httpx
import orjson
import pytest

from tests.mcp_events_helpers import FakeRegistry, append_assistant
from tests.test_mcp_external import authorize, client, config, tokens  # noqa: F401
from tests.test_mcp_events_methods import SECRET, URL, params
from tests.test_mcp_events_runtime import clone, env  # noqa: F401
from twicc.core.models import McpConnection, McpEventSubscription, McpOperation, Project, Session
from twicc.mcp import endpoint, events, server
from twicc.mcp.events import catalog
from twicc.mcp.events.delivery import VerificationService
from twicc.mcp.events.methods import discovery_capability
from twicc.mcp.events.runtime import EventsRuntime
from twicc.mcp.identity import mint_session_token
from twicc.mcp.oauth import storage
from twicc.mcp.pinned_https import PinnedResponse

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def real_write_lock(config, monkeypatch):  # noqa: F811 — shared pytest fixture
    from twicc.providers import db_writer

    monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr(storage, "run_under_db_write_lock", db_writer.run_under_db_write_lock)


async def authenticated(c):
    credentials, code = await authorize(c, name="Events owner")
    pair = (await tokens(c, credentials, code)).json()
    return {"Authorization": "Bearer " + pair["access_token"]}


async def rpc(c, headers, method, arguments=None, *, version="2026-07-28"):
    arguments = dict(arguments or {})
    if version == "2026-07-28":
        arguments["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    response = await c.post("/mcp", headers={
        **headers, "accept": "application/json, text/event-stream", "mcp-protocol-version": version,
        "mcp-method": method,
        **({"mcp-name": arguments["name"]} if method == "tools/call" else {}),
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": arguments})
    body = response.json()
    expected_status = {-32601: 404, -32602: 400}.get(body.get("error", {}).get("code"), 200)
    assert response.status_code == expected_status, response.text
    assert response.headers["content-type"].startswith("application/json")
    return body


@pytest.fixture
def wire_session(tmp_path, monkeypatch):
    project = Project.objects.create(id="events-project", directory=str(tmp_path))
    session = Session.objects.create(id="session", project=project, provider="claude_code",
                                     user_message_count=1, last_offset=100, created_at=timezone.now())
    FakeRegistry().install(monkeypatch)
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set())
    monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: SimpleNamespace(pid=1234))
    return session


def test_authenticated_discovery_custom_results_legacy_and_internal_isolation():
    async def run():
        async with endpoint.mcp_lifespan(), client() as c:
            headers = await authenticated(c)
            discovered = (await rpc(c, headers, "server/discover"))["result"]
            assert discovered["capabilities"]["events"] == {}
            assert discovered["capabilities"]["extensions"][catalog.CAPABILITY_EXTENSION] == {}
            assert "tools" in discovered["capabilities"]
            listed = (await rpc(c, headers, "events/list"))["result"]
            assert listed["events"] == [catalog.EVENT_DEFINITION] and listed["resultType"] == "complete"
            assert "nextCursor" not in listed
            legacy = (await rpc(c, headers, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            }, version="2025-06-18"))["result"]
            assert "events" not in legacy["capabilities"]
            assert catalog.CAPABILITY_EXTENSION not in legacy["capabilities"].get("extensions", {})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=endpoint.handle_mcp),
                                         base_url="http://localhost") as internal:
                local_headers = {"Authorization": "Bearer " + mint_session_token("session")}
                internal_discovery = (await rpc(internal, local_headers, "server/discover"))["result"]
                assert "events" not in internal_discovery["capabilities"]
                assert catalog.CAPABILITY_EXTENSION not in internal_discovery["capabilities"].get("extensions", {})
                for method in ("events/list", "events/subscribe", "events/unsubscribe"):
                    assert (await rpc(internal, local_headers, method))["error"]["code"] == -32601
            assert server._external_server.middleware.count(discovery_capability) == 1
            assert discovery_capability not in server._server.middleware
    asyncio.run(run())


def test_real_handlers_deliver_without_browser_audit_and_preserve_tools(wire_session, monkeypatch):
    append_assistant(wire_session, 1, "Delivered without a browser")

    async def run():
        delivered = asyncio.Event()
        messages = []

        async def receiver(url, *, headers, body):
            message = orjson.loads(body)
            messages.append((headers, message))
            if message.get("type") == "verification":
                return PinnedResponse(200, orjson.dumps({"challenge": message["challenge"]}), False)
            delivered.set()
            return PinnedResponse(204, b"", False)

        monkeypatch.setattr("twicc.mcp.pinned_https.post_webhook", receiver)
        async with endpoint.mcp_lifespan(), client() as c:
            headers = await authenticated(c)
            arguments = params(arguments={"session_id": "session", "since_line_num": 0}).model_dump(by_alias=True)
            created = (await rpc(c, headers, "events/subscribe", arguments))["result"]
            assert created["resultType"] == "complete"
            assert created["cursor"] is None and created["truncated"] is False
            await asyncio.wait_for(delivered.wait(), 5)
            occurrence = messages[-1][1]
            assert occurrence["data"]["reply"]["text"] == "Delivered without a browser"
            assert occurrence["data"]["reply"]["outcome"] == "replied"
            refreshed = (await rpc(c, headers, "events/subscribe", arguments))["result"]
            assert refreshed["id"] == created["id"]
            assert await McpOperation.objects.filter(tool="events/subscribe").acount() == 1
            owner = await sync_to_async(storage.snapshot)()
            assert set(owner) == {"connections", "requests"}
            assert SECRET not in str(owner) and created["id"] not in str(owner)
            tool = (await rpc(c, headers, "tools/call", {"name": "workspaces", "arguments": {}}))["result"]
            assert not tool["isError"] and tool["structuredContent"]["exit_code"] == 0
            batch = (await rpc(c, headers, "tools/call", {"name": "batch_read", "arguments": {
                "calls": [{"id": "one", "name": "workspaces", "arguments": {}}],
            }}))["result"]
            assert batch["structuredContent"]["ok"]
            assert batch["structuredContent"]["results"][0]["response"] == tool["structuredContent"]
            for _ in range(2):
                removed = (await rpc(c, headers, "events/unsubscribe", arguments))["result"]
                assert {key: value for key, value in removed.items() if key != "_meta"} == {"resultType": "complete"}
            assert not await McpEventSubscription.objects.aexists()
            audits = [row async for row in McpOperation.objects.filter(tool__startswith="events/").order_by("id")]
            assert sorted(row.tool for row in audits) == ["events/subscribe", "events/unsubscribe"]
            connection = await McpConnection.objects.aget(name="Events owner")
            for row in audits:
                assert row.connection_id == connection.id and row.name == connection.name
                assert row.targets == {"session_id": "session", "subscription_id": created["id"]}
                assert SECRET not in str(row.targets) and URL not in str(row.targets)
    asyncio.run(run())


def test_registered_handlers_follow_fresh_runtime_across_lifespans(wire_session, monkeypatch):
    runtimes = []
    handlers = dict(server._external_server._request_handlers)

    async def verify(self, *args):
        return None

    monkeypatch.setattr(VerificationService, "verify", verify)

    async def run():
        async with endpoint.mcp_lifespan(), client() as c:
            runtime = events.get_runtime()
            assert runtime not in runtimes
            runtimes.append(runtime)
            adds = []
            original = runtime.add

            def capture(snapshot):
                adds.append(snapshot)
                original(snapshot)

            monkeypatch.setattr(runtime, "add", capture)
            headers = await authenticated(c)
            result = await rpc(c, headers, "events/subscribe", params().model_dump(by_alias=True))
            assert adds[0].id == result["result"]["id"]
            assert server._external_server._request_handlers == handlers
            assert server._external_server.middleware.count(discovery_capability) == 1
        assert events.get_runtime() is None and server._event_methods is None and server._batch_runtime is None
        assert runtime.stop_requested.is_set() and not runtime.thread.is_alive()
        assert runtime.writer_task.done() and runtime.supervisor_task.done()

    asyncio.run(run())
    server._session_manager = None
    server._external_manager = None
    asyncio.run(run())


@pytest.fixture
def lifecycle(monkeypatch):
    @asynccontextmanager
    async def manager_run():
        yield

    manager = SimpleNamespace(run=manager_run)
    monkeypatch.setattr(endpoint, "get_session_manager", lambda: manager)
    monkeypatch.setattr(server, "get_external_session_manager", lambda: manager)
    return manager


@pytest.mark.parametrize("failure", ["start", "events_close", "verification_close", "batch_close", "cancel"])
def test_lifespan_cleans_partial_start_and_all_close_failures(lifecycle, monkeypatch, failure):
    calls = []
    real_start = EventsRuntime.start
    real_close = EventsRuntime.close
    runtime = None

    async def start(self):
        nonlocal runtime
        runtime = self
        assert not endpoint._started
        assert server._batch_runtime is not None and events.get_runtime() is self
        await real_start(self)
        calls.append("start")
        if failure == "start":
            raise RuntimeError("test start failure")

    async def close(self):
        assert not endpoint._started
        await real_close(self)
        calls.append("events_close")
        if failure == "events_close":
            raise RuntimeError("test events_close failure")

    async def verification_close(self):
        await anyio.lowlevel.checkpoint()
        calls.append("verification_close")
        if failure == "verification_close":
            raise RuntimeError("test verification_close failure")

    async def batch_close(self):
        await anyio.lowlevel.checkpoint()
        calls.append("batch_close")
        if failure == "batch_close":
            raise RuntimeError("test batch_close failure")

    monkeypatch.setattr(EventsRuntime, "start", start)
    monkeypatch.setattr(EventsRuntime, "close", close)
    monkeypatch.setattr(VerificationService, "aclose", verification_close)
    monkeypatch.setattr(server.BatchRuntime, "close", batch_close)

    async def run():
        with anyio.CancelScope() as scope:
            async with endpoint.mcp_lifespan():
                assert endpoint._started
                if failure == "cancel":
                    scope.cancel()
        assert failure == "cancel"

    if failure == "cancel":
        asyncio.run(run())
    else:
        with pytest.raises(RuntimeError, match=f"test {failure} failure"):
            asyncio.run(run())
    assert calls == ["start", "events_close", "verification_close", "batch_close"]
    assert events.get_runtime() is None and server._event_methods is None and server._batch_runtime is None
    assert not endpoint._started and not runtime.thread.is_alive()
    assert runtime.writer_task.done() and runtime.supervisor_task.done()


def test_lifespan_closes_shared_inflight_verification(lifecycle, monkeypatch):
    async def run():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        monkeypatch.setattr("twicc.mcp.pinned_https.post_webhook", blocked)
        async with endpoint.mcp_lifespan():
            service = server._event_methods.verification
            caller = asyncio.create_task(service.verify("connection", URL, "subscription", SECRET))
            await asyncio.wait_for(entered.wait(), 2)
        assert cancelled.is_set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not service._inflight
    asyncio.run(run())


def test_cleanup_current_directory_margin_revocation_and_generation(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(events, "_runtime", env.runtime)
    now = env.clock.utcnow()
    expired = clone(env.row, "expired", refresh_before=now - timedelta(seconds=61))
    clone(env.row, "boundary", refresh_before=now - timedelta(seconds=60))
    clone(env.row, "margin", refresh_before=now - timedelta(seconds=1))
    revoked_connection = McpConnection.objects.create(id="revoked", client=env.row.connection.client,
                                                     revoked_at=now, resource=env.row.connection.resource)
    revoked = clone(env.row, "revoked", connection_id=revoked_connection.id)
    clone(env.row, "foreign", data_dir="/foreign", refresh_before=now - timedelta(days=1))
    clone(env.row, "foreign-revoked", data_dir="/foreign", connection_id=revoked_connection.id)
    removes = []

    def remove(identity, created_at):
        removes.append((identity, created_at))

    monkeypatch.setattr(env.runtime, "remove", remove)

    async def run():
        assert await storage.cleanup_event_subscriptions() == 2
        await asyncio.sleep(0)
        assert set(removes) == {(expired.id, expired.created_at), (revoked.id, revoked.created_at)}
        assert {key async for key in McpEventSubscription.objects.values_list("id", flat=True)} == {
            "subscription", "boundary", "margin", "foreign", "foreign-revoked",
        }
        assert not await McpOperation.objects.aexists()
        assert await storage.cleanup_event_subscriptions() == 0
    asyncio.run(run())


def test_cleanup_rollback_preserves_rows_and_posts_no_remove(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(events, "_runtime", env.runtime)
    McpEventSubscription.objects.update(refresh_before=env.clock.utcnow() - timedelta(seconds=61))
    original_write = storage.write

    async def rollback(fn):
        def wrapped():
            with transaction.atomic():
                fn()
                raise RuntimeError("rollback")
        return await original_write(wrapped)

    monkeypatch.setattr(storage, "write", rollback)

    async def run():
        with pytest.raises(RuntimeError, match="rollback"):
            await storage.cleanup_event_subscriptions()
        await asyncio.sleep(0)
        assert await McpEventSubscription.objects.filter(pk=env.row.pk).aexists()
        assert env.runtime.commands.empty()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["oauth", "events", "changed", None])
def test_periodic_cleanup_uses_sixty_seconds_and_independent_error_containment(lifecycle, monkeypatch, failure):
    calls = []

    async def cleanup():
        calls.append("oauth")
        if failure == "oauth":
            raise RuntimeError("oauth failure")
        return 1

    async def changed():
        calls.append("changed")
        if failure == "changed":
            raise RuntimeError("broadcast failure")

    async def cleanup_events():
        calls.append("events")
        if failure == "events":
            raise RuntimeError("events failure")

    monkeypatch.setattr(storage, "cleanup", cleanup)
    monkeypatch.setattr(storage, "changed", changed)
    monkeypatch.setattr(storage, "cleanup_event_subscriptions", cleanup_events)

    class Shutdown:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            return self.calls >= 2

        async def wait(self):
            self.calls += 1
            raise TimeoutError

    original_wait = asyncio.wait_for

    async def timed(awaitable, timeout):
        # Runtime close uses other timeouts; record only the recurring wait.
        if timeout == 60:
            calls.append("60s")
        return await original_wait(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", timed)
    asyncio.run(endpoint.start_mcp_task(Shutdown()))
    expected = ["60s", "oauth"] + ([] if failure == "oauth" else ["changed"]) + ["events"]
    assert calls == expected * 2


@pytest.mark.parametrize("path", ["owner", "token", "refresh_reuse", "revoke_all"])
def test_real_subscription_cleanup_after_each_revocation_path(wire_session, monkeypatch, settings, path):
    from django.test import RequestFactory
    from tests.test_mcp_external import RESOURCE
    from twicc.mcp import owner_views

    async def receiver(url, *, headers, body):
        challenge = orjson.loads(body)["challenge"]
        return PinnedResponse(200, orjson.dumps({"challenge": challenge}), False)

    monkeypatch.setattr("twicc.mcp.pinned_https.post_webhook", receiver)

    async def run():
        async with endpoint.mcp_lifespan(), client() as c:
            credentials, code = await authorize(c)
            pair = (await tokens(c, credentials, code)).json()
            headers = {"Authorization": "Bearer " + pair["access_token"]}
            result = (await rpc(c, headers, "events/subscribe", params().model_dump(by_alias=True)))["result"]
            row = await McpEventSubscription.objects.aget(pk=result["id"])
            runtime = events.get_runtime()
            removes = []
            original_remove = runtime.remove

            def remove(identity, created_at):
                removes.append((identity, created_at))
                original_remove(identity, created_at)

            monkeypatch.setattr(runtime, "remove", remove)
            if path == "owner":
                settings.TWICC_PASSWORD_HASH = ""
                monkeypatch.setattr(owner_views, "request_is_local", lambda request: True)
                request = RequestFactory().post("/api/mcp/", content_type="application/json",
                    data=orjson.dumps({"action": "revoke", "id": row.connection_id}), HTTP_X_TWICC_MCP_OWNER="1")
                assert (await owner_views.management(request)).status_code == 200
            elif path == "token":
                response = await c.post("/mcp/oauth/revoke", data={
                    "client_id": credentials["client_id"], "token": pair["refresh_token"],
                })
                assert response.status_code == 200
            elif path == "refresh_reuse":
                data = {"grant_type": "refresh_token", "client_id": credentials["client_id"],
                        "refresh_token": pair["refresh_token"], "resource": RESOURCE}
                assert (await c.post("/mcp/oauth/token", data=data)).status_code == 200
                assert (await c.post("/mcp/oauth/token", data=data)).status_code == 400
            else:
                await storage.write(storage.revoke_all)
            assert await storage.cleanup_event_subscriptions() == 1
            await asyncio.sleep(0)
            assert removes == [(row.id, row.created_at)]
            assert not await McpEventSubscription.objects.aexists()
            assert await McpOperation.objects.filter(tool="events/subscribe").acount() == 1
            assert not await McpOperation.objects.filter(tool="events/unsubscribe").aexists()
    asyncio.run(run())
