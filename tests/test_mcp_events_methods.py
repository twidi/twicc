"""External subscription contract with real transactions and controlled callbacks."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

from asgiref.sync import sync_to_async
import httpx
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
import orjson
import pytest

from tests.mcp_events_helpers import FakeClock, FakeRegistry, append_assistant
from twicc.core.enums import ItemKind
from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient, McpOperation, Project, Session
from twicc.mcp.events import catalog
from twicc.mcp.events.delivery import VerificationError, VerificationService
from twicc.mcp.events.methods import (
    EventMethods, ListEventsParams, SubscribeParams, UnsubscribeParams,
    discovery_capability, register_event_methods,
)
from twicc.mcp.identity import ExternalCaller, external_caller
from twicc.mcp.oauth import storage
from twicc.mcp.pinned_https import PinnedResponse

pytestmark = pytest.mark.django_db(transaction=True)
URL = "https://receiver.example/events"
SECRET = "whsec_" + base64.b64encode(b"s" * 32).decode()
NEW_SECRET = "whsec_" + base64.b64encode(b"n" * 32).decode()


class Runtime:
    def __init__(self):
        self.adds = []
        self.removes = []

    def add(self, snapshot):
        self.adds.append(snapshot)

    def remove(self, identity, generation):
        self.removes.append((identity, generation))


class Verification:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Queue()
        self.release = asyncio.Event()
        self.release.set()
        self.error = None

    async def verify(self, *args):
        self.calls.append(args)
        self.entered.put_nowait(None)
        await self.release.wait()
        if self.error:
            raise self.error

    async def wait_calls(self, count):
        for _ in range(count):
            await asyncio.wait_for(self.entered.get(), 2)


def params(**changes):
    return SubscribeParams.model_validate({
        "name": catalog.EVENT_NAME, "arguments": {"session_id": "session"},
        "delivery": {"mode": "webhook", "url": URL, "secret": SECRET}, **changes,
    })


@pytest.fixture
def env(monkeypatch, tmp_path):
    # Exercise the real write-lock implementation without starting background workers.
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    clock = FakeClock()
    client = McpOAuthClient.objects.create(id="client")
    connection = McpConnection.objects.create(id="connection", client=client, resource="https://mcp.example/mcp")
    project = Project.objects.create(id="project", directory=str(tmp_path))
    session = Session.objects.create(
        id="session", project=project, provider="claude_code", created_at=clock.utcnow(),
        user_message_count=1, last_line=10, last_offset=100,
    )
    registry = FakeRegistry()
    registry.install(monkeypatch)
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set())
    runtime = Runtime()
    verification = Verification()
    methods = EventMethods(runtime, verification, clock=clock.clock, data_dir=str(tmp_path))
    token = external_caller.set(ExternalCaller(connection.id, "Test owner"))
    yield SimpleNamespace(clock=clock, connection=connection, session=session, registry=registry,
                          runtime=runtime, verification=verification, methods=methods)
    external_caller.reset(token)


async def subscribe(env, **changes):
    return await env.methods.subscribe(None, params(**changes))


async def row():
    return await sync_to_async(McpEventSubscription.objects.get)()


def test_create_result_snapshot_and_audit(env):
    async def run():
        result = await subscribe(env, _meta={"progressToken": "progress"}, futureKey=3, cursor="opaque")
        saved = await row()
        assert result == {"id": saved.id, "refreshBefore": (env.clock.utcnow() + timedelta(days=1)).isoformat(),
                          "cursor": None, "truncated": True}
        assert (saved.cursor_line, saved.initial_last_line, saved.turn_start_line, saved.numbering) == (10, 10, 10, 0)
        assert saved.cursor_at == env.clock.epoch()
        assert not saved.turn_open and saved.turn_started_at is None and saved.turn_opened_by == ""
        snapshot = env.runtime.adds[0]
        assert snapshot.generation == (saved.id, saved.created_at)
        assert snapshot.secret == SECRET
        with pytest.raises(TypeError):
            snapshot.arguments["session_id"] = "other"
        audit = await sync_to_async(McpOperation.objects.get)()
        assert audit.tool == "events/subscribe" and audit.name == "Test owner"
        assert audit.targets == {"session_id": "session", "subscription_id": saved.id}
        assert SECRET not in orjson.dumps(result).decode()
    asyncio.run(run())


@pytest.mark.parametrize("changes,code,data", [
    ({"name": None}, -32602, None), ({"name": 7}, -32602, None),
    ({"arguments": None}, -32602, None), ({"arguments": []}, -32602, None),
    ({"delivery": None}, -32602, None), ({"delivery": []}, -32602, None),
    ({"delivery": {}}, -32602, None),
    ({"name": "other"}, -32011, {"kind": "event"}),
    ({"delivery": {"mode": "sse"}}, -32014, {"feature": "deliveryMode", "value": "sse"}),
    ({"delivery": {"mode": None}}, -32014, {"feature": "deliveryMode", "value": None}),
    ({"arguments": {}}, -32602, None),
    ({"arguments": {"session_id": ""}}, -32602, None),
    ({"arguments": {"session_id": 1}}, -32602, None),
    ({"arguments": {"session_id": "session", "extra": 1}}, -32602, None),
    ({"arguments": {"session_id": "session", "wait_background": 1}}, -32602, None),
    *[({"arguments": {"session_id": "session", "since_line_num": value}}, -32602, None)
      for value in [True, -1, 3.5, 2147483648, "3", None]],
    *[({"ttlMs": value}, -32602, None) for value in [True, False, "5", [], {}, float("nan"), float("inf"), -float("inf")]],
    # Shape and TTL precede unknown-event, mode, schema, secret and URL checks.
    ({"name": "other", "ttlMs": True}, -32602, None),
    ({"name": "other", "delivery": {}}, -32602, None),
])
def test_subscribe_validation_order(env, changes, code, data):
    async def run():
        with pytest.raises(MCPError) as exc:
            await subscribe(env, **changes)
        assert exc.value.code == code
        assert exc.value.data == data if data is not None else isinstance(exc.value.data["reason"], str)
        assert env.verification.calls == []
        assert env.runtime.adds == []
    asyncio.run(run())
    assert not McpEventSubscription.objects.exists()


@pytest.mark.parametrize("secret", [None, 2, "", "wrong_" + SECRET, "whsec_%%%", "whsec_é",
                                        "whsec_" + base64.b64encode(b"a" * 23).decode(),
                                        "whsec_" + base64.b64encode(b"a" * 65).decode()])
def test_invalid_secret(env, secret):
    async def run():
        with pytest.raises(MCPError) as exc:
            await subscribe(env, delivery={"mode": "webhook", "url": URL, "secret": secret})
        assert exc.value.code == -32602 and "reason" in exc.value.data
        assert not env.verification.calls
    asyncio.run(run())


@pytest.mark.parametrize("url", [None, 3, "", "http://receiver.example/", "https:///x", "https://é.example/",
    "https://user@receiver.example/", "https://user:pass@receiver.example/", URL + "#fragment", URL + "#",
    "https://receiver.example:invalid/", "https://receiver.example:65536/", "https://[::1/x", URL + "a" * 2048])
def test_invalid_url(env, url):
    async def run():
        with pytest.raises(MCPError) as exc:
            await subscribe(env, delivery={"mode": "webhook", "url": url, "secret": SECRET})
        assert exc.value.code == -32602 and "reason" in exc.value.data
        assert not env.verification.calls
    asyncio.run(run())


@pytest.mark.parametrize("ttl,seconds", [(None, 86400), (0, 3600), (-100, 3600), (1, 3600),
                                        (3600500.5, 3600.5005), (10**400, 604800), (-10**400, 3600)])
def test_ttl_and_unpadded_secret_and_integral_float_cursor(env, ttl, seconds):
    async def run():
        result = await subscribe(env, ttlMs=ttl, arguments={"session_id": "session", "since_line_num": 3.0},
                                 delivery={"mode": "webhook", "url": URL, "secret": SECRET.rstrip("=")})
        saved = await row()
        assert saved.refresh_before == env.clock.utcnow() + timedelta(seconds=seconds)
        assert saved.cursor_line == 3 and type(saved.arguments["since_line_num"]) is int
        assert not result["truncated"]
    asyncio.run(run())


def test_refresh_preserves_every_detection_field_and_rotates_secrets(env):
    async def run():
        await subscribe(env)
        await storage.write(lambda: McpEventSubscription.objects.update(
            cursor_line=21, cursor_at=42.1, initial_last_line=19, numbering=5,
            turn_open=True, turn_started_at=33.2, turn_opened_by="transition", turn_start_line=20,
        ))
        before = await row()
        env.clock.advance(100)
        await subscribe(env, arguments={"session_id": "session", "since_line_num": 200},
                        delivery={"mode": "webhook", "url": URL, "secret": NEW_SECRET})
        after = await row()
        for field in ("created_at", "cursor_line", "cursor_at", "initial_last_line", "numbering",
                      "turn_open", "turn_started_at", "turn_opened_by", "turn_start_line"):
            assert getattr(after, field) == getattr(before, field), field
        assert after.secret == NEW_SECRET and after.previous_secret == SECRET
        assert after.previous_secret_until == env.clock.utcnow() + timedelta(minutes=5)
        assert after.arguments["since_line_num"] == 200
        previous_until = after.previous_secret_until
        env.clock.advance(10)
        await subscribe(env, delivery={"mode": "webhook", "url": URL, "secret": NEW_SECRET})
        assert (await row()).previous_secret_until == previous_until
        assert len(env.runtime.adds) == 3
    asyncio.run(run())
    assert McpOperation.objects.count() == 1


@pytest.mark.parametrize("code,data", [(-32015, {"reason": "challenge_failed"}),
                                        (-32013, {"limit": "concurrent_verifications", "max": 8})])
def test_verification_failure_changes_nothing(env, code, data):
    async def run():
        await subscribe(env)
        before = env.runtime.adds[0]
        env.verification.error = VerificationError(code, data)
        with pytest.raises(MCPError) as exc:
            await subscribe(env, delivery={"mode": "webhook", "url": URL, "secret": NEW_SECRET})
        assert exc.value.code == code and exc.value.data == data
        assert env.runtime.adds == [before]
        assert (await row()).secret == SECRET
        assert (await row()).refresh_before == before.refresh_before
    asyncio.run(run())
    assert McpOperation.objects.count() == 1


def test_real_verification_cache_is_shared_across_secret_rotation(env):
    async def run():
        calls = []
        async def send(url, *, headers, body):
            calls.append(headers)
            return PinnedResponse(200, body, False)
        service = VerificationService(clock=env.clock.clock, send=send)
        env.methods.verification = service
        try:
            await subscribe(env)
            await subscribe(env, delivery={"mode": "webhook", "url": URL, "secret": NEW_SECRET})
            assert len(calls) == 1 and (await row()).secret == NEW_SECRET
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("working", [False, True])
def test_initial_turn_uses_working_state_or_first_real_prompt(env, working):
    env.session.items.create(line_num=12, kind=ItemKind.USER_MESSAGE,
                            content=orjson.dumps({"message": {"content": "<command-name>/rename</command-name>"}}).decode())
    env.session.items.create(line_num=13, kind=ItemKind.USER_MESSAGE, timestamp=env.clock.utcnow(),
                            content=orjson.dumps({"message": {"content": "Prompt"}}).decode())
    env.session.items.create(line_num=14, kind=ItemKind.USER_MESSAGE, timestamp=env.clock.utcnow() + timedelta(seconds=1),
                            content=orjson.dumps({"message": {"content": "Second prompt"}}).decode())
    if working:
        env.registry.set_agent("session", at=env.clock.epoch() - 1)
    async def run():
        await subscribe(env)
        saved = await row()
        assert saved.turn_open
        assert saved.turn_opened_by == ("initial" if working else "history")
        assert saved.turn_started_at == env.clock.epoch() - (1 if working else 0)
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["reply", "prompt_crash"])
def test_verification_keeps_arrival_cursor_but_observes_insert_time_history(env, kind):
    async def run():
        env.verification.release.clear()
        task = asyncio.create_task(subscribe(env))
        await env.verification.wait_calls(1)
        env.clock.advance(10)
        def append():
            if kind == "reply":
                append_assistant(env.session, 11)
            else:
                env.session.items.create(line_num=11, kind=ItemKind.USER_MESSAGE,
                    content=orjson.dumps({"message": {"content": "New prompt"}}).decode())
                Session.objects.filter(pk="session").update(last_line=11)
        await storage.write(append)
        env.verification.release.set()
        await task
        saved = await row()
        assert (saved.cursor_line, saved.initial_last_line) == (10, 10)
        assert saved.refresh_before == env.clock.utcnow() + timedelta(days=1, seconds=-10)
        assert saved.cursor_at == env.clock.epoch()
        assert saved.turn_open == (kind == "prompt_crash")
        if kind == "prompt_crash":
            assert saved.turn_opened_by == "history" and saved.turn_started_at == 0
    asyncio.run(run())


@pytest.mark.parametrize("existing", ["none", "expired", "foreign"])
def test_concurrent_identical_inserts_reclassify_to_one_generation(env, existing):
    async def run():
        if existing != "none":
            await subscribe(env)
            await storage.write(lambda: McpEventSubscription.objects.update(
                data_dir="/foreign" if existing == "foreign" else env.methods.data_dir,
                refresh_before=env.clock.utcnow(),
            ))
            env.verification.entered = asyncio.Queue()
            env.runtime.adds.clear()
        env.verification.release.clear()
        tasks = [asyncio.create_task(subscribe(env)) for _ in range(2)]
        await env.verification.wait_calls(2)
        env.verification.release.set()
        results = await asyncio.gather(*tasks)
        assert results[0]["id"] == results[1]["id"]
        assert len(env.runtime.adds) == 2
        assert env.runtime.adds[0].generation == env.runtime.adds[1].generation
    asyncio.run(run())
    assert McpEventSubscription.objects.count() == 1
    assert McpOperation.objects.count() == (1 if existing == "none" else 2)


def seed_rows(env, count, *, connection=None, data_dir=None, expiry=None):
    McpEventSubscription.objects.bulk_create([
        McpEventSubscription(
            id=f"seed-{i}", connection=connection or env.connection, name=catalog.EVENT_NAME,
            arguments={"session_id": "session"}, session_id="session", callback_url=URL, secret=SECRET,
            cursor_line=0, cursor_at=0, initial_last_line=0, turn_open=False, turn_start_line=0,
            numbering=0, data_dir=data_dir or env.methods.data_dir,
            refresh_before=expiry or env.clock.utcnow() + timedelta(days=1),
        ) for i in range(count)
    ])


@pytest.mark.parametrize("total", [False, True])
def test_authoritative_limit_blocks_concurrent_new_identity(env, total):
    other = McpConnection.objects.create(id="other", client=env.connection.client, resource=env.connection.resource)
    seed_rows(env, 99 if total else 49, connection=other if total else None)
    async def run():
        env.verification.release.clear()
        tasks = [asyncio.create_task(subscribe(env, delivery={"mode": "webhook", "url": URL + str(i), "secret": SECRET}))
                 for i in range(2)]
        await env.verification.wait_calls(2)
        env.verification.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [value for value in results if isinstance(value, MCPError)]
        assert len(errors) == 1 and errors[0].code == -32013
        assert errors[0].data == {"limit": "subscriptions", "max": 100 if total else 50}
        assert len(env.runtime.adds) == 1
    asyncio.run(run())
    assert McpEventSubscription.objects.count() == (100 if total else 50)
    assert McpOperation.objects.count() == 1


def test_limit_precheck_skips_verification_and_foreign_rows_do_not_count(env):
    seed_rows(env, 100, data_dir="/another-instance")
    asyncio.run(subscribe(env))
    McpEventSubscription.objects.filter(id__startswith="seed").delete()
    seed_rows(env, 49)
    env.verification.calls.clear()
    async def run():
        with pytest.raises(MCPError) as exc:
            await subscribe(env, delivery={"mode": "webhook", "url": URL + "/other", "secret": SECRET})
        assert exc.value.code == -32013
        assert exc.value.data == {"limit": "subscriptions", "max": 50}
        assert not env.verification.calls
    asyncio.run(run())


@pytest.mark.parametrize("foreign", [False, True])
def test_expired_or_foreign_identity_replaced_at_limit(env, foreign):
    asyncio.run(subscribe(env))
    before = McpEventSubscription.objects.get()
    McpEventSubscription.objects.update(data_dir="/foreign" if foreign else env.methods.data_dir,
                                       refresh_before=env.clock.utcnow() if not foreign else before.refresh_before)
    seed_rows(env, 49)
    asyncio.run(subscribe(env, arguments={"session_id": "session", "since_line_num": 3}))
    after = McpEventSubscription.objects.get(pk=before.id)
    assert after.created_at != before.created_at and after.cursor_line == 3
    assert after.data_dir == env.methods.data_dir
    assert McpOperation.objects.count() == 2 and McpEventSubscription.objects.count() == 50


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_deleted_during_verification_refresh_rechecks_lookup_flag(env, lookup_fails):
    asyncio.run(subscribe(env))
    old = McpEventSubscription.objects.get()
    if lookup_fails:
        Session.objects.filter(pk="session").update(user_message_count=0)
    async def run():
        env.verification.entered = asyncio.Queue()
        env.verification.release.clear()
        task = asyncio.create_task(subscribe(env))
        await env.verification.wait_calls(1)
        await storage.write(lambda: McpEventSubscription.objects.all().delete())
        env.verification.release.set()
        if lookup_fails:
            with pytest.raises(MCPError) as exc:
                await task
            assert exc.value.code == -32011 and exc.value.data == {"reason": "unknown_session"}
            assert len(env.runtime.adds) == 1
        else:
            await task
            assert (await row()).created_at != old.created_at
    asyncio.run(run())
    assert McpEventSubscription.objects.count() == (0 if lookup_fails else 1)


def test_timely_refresh_uses_arrival_classification_after_expiry(env):
    asyncio.run(subscribe(env, ttlMs=1))
    old = McpEventSubscription.objects.get()
    env.clock.advance(3599)
    async def run():
        env.verification.entered = asyncio.Queue()
        env.verification.release.clear()
        task = asyncio.create_task(subscribe(env))
        await env.verification.wait_calls(1)
        env.clock.advance(15)
        # Match cleanup's margin: a recently expired row remains available.
        await storage.write(lambda: McpEventSubscription.objects.filter(
            refresh_before__lte=env.clock.utcnow() - timedelta(seconds=catalog.EXPIRY_CLEANUP_MARGIN_SECONDS),
        ).delete())
        env.verification.release.set()
        await task
        saved = await row()
        assert saved.created_at == old.created_at
        assert saved.refresh_before == env.clock.utcnow() + timedelta(days=1, seconds=-15)
    asyncio.run(run())
    assert McpOperation.objects.count() == 1


def test_lookup_failure_allows_live_refresh_but_rejects_new(env):
    asyncio.run(subscribe(env))
    Session.objects.filter(pk="session").update(user_message_count=0)
    asyncio.run(subscribe(env))
    async def run():
        with pytest.raises(MCPError) as exc:
            await subscribe(env, arguments={"session_id": "missing"})
        assert exc.value.code == -32011 and exc.value.data == {"reason": "unknown_session"}
        assert len(env.verification.calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize("epoch,offset,expected", [(0, 0, 0), (2, 0, None), (2, 100, 2)])
def test_arrival_numbering_and_epoch_acceptance(env, epoch, offset, expected):
    Session.objects.filter(pk="session").update(history_epoch=epoch, last_offset=offset,
                                               user_message_count=0 if epoch else 1)
    asyncio.run(subscribe(env))
    assert McpEventSubscription.objects.get().numbering == expected


def test_live_without_row_and_rejected_live_row_keep_arrival_metadata(env, monkeypatch):
    monkeypatch.setattr("twicc.cli.session._live_session_ids", lambda ids: set(ids))
    Session.objects.filter(pk="session").update(user_message_count=0)
    asyncio.run(subscribe(env))
    assert McpEventSubscription.objects.get().initial_last_line == 10
    asyncio.run(subscribe(env, arguments={"session_id": "missing"}))
    missing = McpEventSubscription.objects.get(session_id="missing")
    assert missing.initial_last_line == missing.cursor_line == missing.numbering == 0


def test_transaction_rolls_back_row_and_emits_no_command_when_audit_fails(env, monkeypatch):
    def fail(*args):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(env.methods, "_audit", fail)
    with pytest.raises(RuntimeError, match="audit failed"):
        asyncio.run(subscribe(env))
    assert not McpEventSubscription.objects.exists() and not env.runtime.adds


@pytest.mark.parametrize("delete", [False, True])
def test_committed_runtime_command_survives_request_cancellation(env, monkeypatch, delete):
    async def run():
        if delete:
            await subscribe(env)
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = Event()
        audit = env.methods._audit
        def held_audit(*args):
            audit(*args)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
        monkeypatch.setattr(env.methods, "_audit", held_audit)
        operation = env.methods.unsubscribe(None, UnsubscribeParams.model_validate({
            "name": catalog.EVENT_NAME, "arguments": {"session_id": "session"}, "delivery": {"url": URL},
        })) if delete else subscribe(env)
        task = asyncio.create_task(operation)
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        # Let cancellation reach the shielded database write before the commit.
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(env.runtime.removes if delete else env.runtime.adds) == 1
    asyncio.run(run())
    assert McpEventSubscription.objects.count() == (0 if delete else 1)


def test_unsubscribe_identity_scope_idempotence_and_generation(env):
    asyncio.run(subscribe(env))
    before = McpEventSubscription.objects.get()
    delete = UnsubscribeParams.model_validate({"name": catalog.EVENT_NAME,
        "arguments": {"session_id": "session", "since_line_num": "ignored", "unknown": True},
        "delivery": {"url": URL}, "_meta": {}, "extra": True})
    async def run():
        token = external_caller.set(ExternalCaller("different-connection", "Other owner"))
        try:
            assert await env.methods.unsubscribe(None, delete) == {}
        finally:
            external_caller.reset(token)
        assert not env.runtime.removes
        await storage.write(lambda: Session.objects.all().delete())
        assert await env.methods.unsubscribe(None, delete) == {}
        assert await env.methods.unsubscribe(None, delete) == {}
        assert env.runtime.removes == [(before.id, before.created_at)]
    asyncio.run(run())
    assert not McpEventSubscription.objects.exists()
    assert list(McpOperation.objects.values_list("tool", flat=True)) == ["events/subscribe", "events/unsubscribe"]


@pytest.mark.parametrize("changes", [{"name": None}, {"name": 1}, {"name": ""}, {"arguments": None},
    {"arguments": {}}, {"arguments": {"session_id": 1}},
    {"arguments": {"session_id": "session", "wait_background": 1}}, {"delivery": None}, {"delivery": {"url": 1}}])
def test_unsubscribe_validates_identity_types(env, changes):
    values = {"name": catalog.EVENT_NAME, "arguments": {"session_id": "session"}, "delivery": {"url": URL}, **changes}
    with pytest.raises(MCPError) as exc:
        asyncio.run(env.methods.unsubscribe(None, UnsubscribeParams.model_validate(values)))
    assert exc.value.code == -32602 and "reason" in exc.value.data
    assert not env.runtime.removes


@asynccontextmanager
async def wire_client(env):
    server = Server("events-test")
    register_event_methods(server, env.methods)
    register_event_methods(server, env.methods)
    assert server.middleware.count(discovery_capability) == 1
    manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    async with manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.handle_request), base_url="http://test",
            headers={"accept": "application/json, text/event-stream", "mcp-protocol-version": "2026-07-28"}) as client:
            yield client


def test_sdk_wire_discovery_list_and_field_errors(env):
    async def run():
        async with wire_client(env) as client:
            async def call(method, value):
                if isinstance(value, dict):
                    value = {**value, "_meta": {**(value.get("_meta") or {}),
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {}}}
                response = await client.post("/mcp", headers={"mcp-method": method},
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": value})
                body = response.json()
                assert response.status_code == (400 if "error" in body else 200), response.text
                return body
            discovered = (await call("server/discover", {}))["result"]
            assert discovered["capabilities"]["events"] == {}
            assert discovered["capabilities"]["extensions"][catalog.CAPABILITY_EXTENSION] == {}
            listed = (await call("events/list", {"_meta": {}, "future": 1}))["result"]
            assert listed["events"] == [catalog.EVENT_DEFINITION]
            assert listed["resultType"] == "complete" and "nextCursor" not in listed
            for key, value in [("name", 1), ("ttlMs", True), ("arguments", []), ("delivery", "bad")]:
                raw = params().model_dump(by_alias=True)
                raw[key] = value
                failure = (await call("events/subscribe", raw))["error"]
                assert failure["code"] == -32602 and "reason" in failure["data"]
            for scalar in [3, [], "bad"]:
                failure = (await call("events/subscribe", scalar))["error"]
                # The SDK rejects a non-object params envelope before handler validation.
                assert failure["code"] == -32600
    asyncio.run(run())


def test_middleware_preserves_capabilities_and_other_methods(env):
    async def run():
        result = {"capabilities": {"tools": {"listChanged": False}, "extensions": {"existing": {"yes": True}}}}
        async def call_next(ctx):
            return result
        assert await discovery_capability(SimpleNamespace(method="initialize"), call_next) is result
        assert "events" not in result["capabilities"]
        await discovery_capability(SimpleNamespace(method="server/discover"), call_next)
        assert result["capabilities"]["tools"] == {"listChanged": False}
        assert result["capabilities"]["extensions"]["existing"] == {"yes": True}
        # Definitions returned to callers never mutate the catalogue.
        listed = await env.methods.list_events(None, ListEventsParams())
        listed["events"][0]["name"] = "changed"
        assert catalog.EVENT_DEFINITION["name"] == "session.concluded"
    asyncio.run(run())
