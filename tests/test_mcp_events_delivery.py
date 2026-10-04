"""Delivery authority and retries use real SQL and a controlled network boundary."""

import asyncio
import base64
from datetime import datetime, timedelta
import ssl
from types import SimpleNamespace

from django.db import transaction
import pytest
from standardwebhooks import Webhook
from standardwebhooks.webhooks import WebhookVerificationError

from tests.mcp_events_helpers import FakeClock
from twicc.core.models import McpConnection, McpEventSubscription, McpOAuthClient
from twicc.mcp.events import delivery
from twicc.mcp.events.runtime import CursorWrite, Emission, EventsRuntime, RemoveCommand
from twicc.mcp.oauth import storage
from twicc.mcp.pinned_https import PinnedResponse

pytestmark = pytest.mark.django_db(transaction=True)
SECRET = base64.b64encode(b"s" * 32).decode()
NEW_SECRET = base64.b64encode(b"n" * 32).decode()
BODY = b'{"data":{"reply":{"text":"private reply"}}}'


@pytest.fixture
def env(monkeypatch, tmp_path):
    # Import consumers before patching config: routes/provider bind base_url.
    from twicc.mcp.oauth import routes  # noqa: F401

    monkeypatch.setattr("twicc.mcp.pinned_https.logger.disabled", False)
    monkeypatch.setattr("twicc.providers.db_writer._db_write_lock", asyncio.Lock())
    monkeypatch.setattr("twicc.providers.db_writer._db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr("twicc.mcp.oauth.config.base_url", lambda: "https://mcp.example")
    clock = FakeClock()
    client = McpOAuthClient.objects.create(id="client")
    connection = McpConnection.objects.create(id="connection", client=client, resource="https://mcp.example/mcp")
    row = McpEventSubscription.objects.create(
        id="subscription", connection=connection, name="session.concluded", arguments={"session_id": "session"},
        session_id="session", callback_url="https://callback.example/events", secret=SECRET,
        cursor_line=10, cursor_at=100, initial_last_line=10, numbering=2, data_dir=str(tmp_path),
        turn_open=False, turn_start_line=10,
        refresh_before=clock.utcnow() + timedelta(hours=1),
    )
    runtime = EventsRuntime(clock=clock.clock, data_dir=str(tmp_path))
    emission = Emission(row.id, row.created_at, "msg_frozen", BODY,
                        CursorWrite(row.id, row.created_at, 2, 20, 200))
    return SimpleNamespace(clock=clock, row=row, runtime=runtime, emission=emission)


@pytest.mark.parametrize("result,attempts", [
    (200, 1), (204, 1), (299, 1), (301, 1), (307, 1), (400, 1), (401, 1), (403, 1),
    (404, 1), (408, 3), (410, 1), (413, 1), (425, 3), (429, 3), (500, 3), (503, 3),
    (599, 3), (600, 1), (ConnectionRefusedError("refused"), 3), (TimeoutError("timeout"), 3),
    (ssl.SSLError("certificate"), 1),
])
def test_status_and_error_retry_matrix_keeps_subscription(env, result, attempts, caplog):
    async def run():
        calls, delays = [], []

        async def send(url, *, headers, body):
            calls.append((url, headers, body))
            if isinstance(result, Exception):
                raise result
            return PinnedResponse(result, b"private receiver response", False)

        async def sleep(delay):
            delays.append(delay)
            env.clock.advance(delay)

        service = delivery.DeliveryService(env.runtime, send=send, sleep=sleep)
        await service.deliver(env.emission)
        assert len(calls) == attempts
        assert delays == ([30, 120] if attempts == 3 else [])
        assert all(call[0] == env.row.callback_url and call[2] is BODY for call in calls)
        assert {call[1]["webhook-id"] for call in calls} == {"msg_frozen"}
        assert len({call[1]["webhook-timestamp"] for call in calls}) == attempts
        assert len({call[1]["webhook-signature"] for call in calls}) == attempts
        assert env.runtime.writes.get_nowait() == env.emission.cursor
        assert await McpEventSubscription.objects.filter(pk=env.row.pk).aexists()
    asyncio.run(run())
    assert SECRET not in caplog.text and "private reply" not in caplog.text
    assert "private receiver response" not in caplog.text


@pytest.mark.parametrize("change,deleted", [
    ("gone", True), ("recreated", False), ("expired", False), ("foreign", False),
    ("revoked", True), ("resource", True), ("unconfigured", False), ("revoked_unconfigured", True),
    ("foreign_revoked", False),
])
def test_authority_suppresses_and_only_deletes_revoked_or_resource_mismatch(env, monkeypatch, change, deleted):
    if change == "gone":
        env.row.delete()
    elif change == "recreated":
        McpEventSubscription.objects.update(created_at=env.row.created_at + timedelta(seconds=1))
    elif change == "expired":
        McpEventSubscription.objects.update(refresh_before=env.clock.utcnow())
    elif change in ("foreign", "foreign_revoked"):
        McpEventSubscription.objects.update(data_dir="/other-instance")
    if change in ("revoked", "revoked_unconfigured", "foreign_revoked"):
        McpConnection.objects.update(revoked_at=env.clock.utcnow())
    if change == "resource":
        McpConnection.objects.update(resource="https://old.example/mcp")
    if change in ("unconfigured", "revoked_unconfigured"):
        monkeypatch.setattr("twicc.mcp.oauth.config.base_url", lambda: "")

    async def run():
        async def forbidden(*args, **kwargs):
            pytest.fail("Suppressed authority must not contact the receiver")
        await delivery.DeliveryService(env.runtime, send=forbidden).deliver(env.emission)
        cursor = env.runtime.writes.get_nowait()
        assert cursor == env.emission.cursor
        if change in ("expired", "unconfigured"):
            await storage.write(lambda: env.runtime._apply_write(cursor))
            saved = await McpEventSubscription.objects.aget(pk="subscription")
            assert saved.cursor_line == 20  # Suppressed delivery is still consumed.
        assert await McpEventSubscription.objects.filter(pk="subscription").aexists() is not deleted
        if change in ("revoked", "resource", "revoked_unconfigured"):
            await asyncio.sleep(0)
            assert env.runtime.commands.get_nowait() == RemoveCommand(env.emission.id, env.emission.created_at)
        else:
            assert env.runtime.commands.empty()
    asyncio.run(run())


def test_rotation_between_attempts_uses_current_secrets_and_window(env, monkeypatch):
    class VerificationTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return env.clock.utcnow()
    monkeypatch.setattr("standardwebhooks.webhooks.datetime", VerificationTime)

    async def run():
        calls = []

        async def send(url, *, headers, body):
            calls.append((headers, body, env.clock.utcnow().replace(microsecond=0)))
            return PinnedResponse(503, b"", False)

        async def sleep(delay):
            env.clock.advance(delay)
            if len(calls) == 1:
                await storage.write(lambda: McpEventSubscription.objects.update(
                    secret=NEW_SECRET, previous_secret=SECRET,
                    previous_secret_until=env.clock.utcnow() + timedelta(seconds=60),
                ))

        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission)
        assert len(calls) == 3
        for (headers, body, signing_time), expected_secrets in zip(
            calls, ((SECRET,), (NEW_SECRET, SECRET), (NEW_SECRET,)), strict=True,
        ):
            assert body is BODY
            assert headers["webhook-id"] == "msg_frozen"
            assert headers["webhook-timestamp"] == str(int(signing_time.timestamp()))
            assert headers["webhook-signature"] == " ".join(
                Webhook(secret).sign("msg_frozen", signing_time, BODY.decode()) for secret in expected_secrets
            )
            for secret in expected_secrets:
                assert Webhook(secret).verify(body, headers) == {"data": {"reply": {"text": "private reply"}}}
        with pytest.raises(WebhookVerificationError, match="No matching signature"):
            Webhook(SECRET).verify(calls[2][1], calls[2][0])
    asyncio.run(run())


@pytest.mark.parametrize("change", ["unsubscribe", "recreate", "expire", "revoke"])
def test_retry_rechecks_authority_and_cannot_write_recreated_generation(env, change):
    async def run():
        calls = []

        async def send(*args, **kwargs):
            calls.append(kwargs)
            return PinnedResponse(503, b"", False)

        async def sleep(delay):
            env.clock.advance(delay)
            def mutate():
                if change == "unsubscribe":
                    McpEventSubscription.objects.all().delete()
                elif change == "recreate":
                    values = {field.attname: getattr(env.row, field.attname) for field in env.row._meta.fields
                              if field.name not in ("created_at", "updated_at")}
                    McpEventSubscription.objects.all().delete()
                    McpEventSubscription.objects.create(**values)
                elif change == "expire":
                    McpEventSubscription.objects.update(refresh_before=env.clock.utcnow())
                else:
                    McpConnection.objects.update(revoked_at=env.clock.utcnow())
            await storage.write(mutate)

        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission)
        assert len(calls) == 1
        cursor = env.runtime.writes.get_nowait()
        await storage.write(lambda: env.runtime._apply_write(cursor))
        if change == "recreate":
            row = await McpEventSubscription.objects.aget(pk="subscription")
            assert (row.cursor_line, row.cursor_at) == (10, 100)
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["send", "sleep"])
def test_cancelled_delivery_does_not_persist_cursor(env, phase):
    async def run():
        entered = asyncio.Event()
        async def block():
            entered.set()
            await asyncio.Future()
        async def send(*args, **kwargs):
            if phase == "send":
                await block()
            return PinnedResponse(503, b"", False)
        async def sleep(delay):
            await block()
        task = asyncio.create_task(delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert env.runtime.writes.empty()
    asyncio.run(run())


def test_delivery_tasks_run_on_loop_and_worker_posting_does_not_wait(env):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        env.runtime.loop = loop
        async def send(*args, **kwargs):
            assert asyncio.get_running_loop() is loop
            entered.set()
            await release.wait()
            return PinnedResponse(204, b"", False)
        env.runtime.delivery = delivery.DeliveryService(env.runtime, send=send)
        assert await asyncio.to_thread(env.runtime.post_emission, env.emission)
        await entered.wait()
        assert len(env.runtime.delivery_tasks) == 1
        tasks = tuple(env.runtime.delivery_tasks)
        release.set()
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert not env.runtime.delivery_tasks
        assert env.runtime.writes.get_nowait() == env.emission.cursor
    asyncio.run(run())


@pytest.mark.parametrize("move,expected", [("newer", (30, 300)), ("silent", (40, 200)), ("rebase", (5, 200))])
def test_slow_completion_preserves_newer_cursor_and_independent_time(env, move, expected):
    async def run():
        waiting, release = asyncio.Event(), asyncio.Event()
        async def send(*args, **kwargs):
            return PinnedResponse(503, b"", False)
        async def sleep(delay):
            waiting.set()
            await release.wait()
        service = delivery.DeliveryService(env.runtime, send=send, sleep=sleep)
        task = asyncio.create_task(service.deliver(env.emission))
        await waiting.wait()
        if move == "newer":
            newer = env.emission._replace(cursor=env.emission.cursor._replace(cursor_line=30, cursor_at=300))
            async def succeed(*args, **kwargs):
                return PinnedResponse(204, b"", False)
            await delivery.DeliveryService(env.runtime, send=succeed).deliver(newer)
        elif move == "silent":
            env.runtime.writes.put_nowait(env.emission.cursor._replace(cursor_line=40, cursor_at=None))
        else:
            await storage.write(lambda: McpEventSubscription.objects.update(numbering=3, cursor_line=5))
        writer = asyncio.create_task(env.runtime.run_writer())
        await env.runtime.writes.join()
        release.set()
        await task
        await env.runtime.writes.join()
        writer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await writer
        row = await McpEventSubscription.objects.aget(pk="subscription")
        assert (row.cursor_line, row.cursor_at) == expected
    asyncio.run(run())


@pytest.mark.parametrize("path", ["owner", "token", "refresh_reuse", "revoke_all"])
def test_every_existing_connection_revocation_path_stops_next_attempt(env, monkeypatch, settings, path):
    from django.test import RequestFactory
    from twicc.mcp import owner_views
    from twicc.mcp.oauth.provider import Provider

    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr(owner_views, "request_is_local", lambda request: True)

    async def run():
        calls = []
        async def send(*args, **kwargs):
            calls.append(kwargs)
            return PinnedResponse(503, b"", False)
        async def sleep(delay):
            if path == "owner":
                request = RequestFactory().post(
                    "/api/mcp/", data='{"action":"revoke","id":"connection"}',
                    content_type="application/json", HTTP_X_TWICC_MCP_OWNER="1",
                )
                assert (await owner_views.management(request)).status_code == 200
            elif path == "token":
                token = await storage.write(lambda: storage.issue(env.row.connection, "access", 900))
                await Provider().revoke_token(SimpleNamespace(token=token))
            elif path == "refresh_reuse":
                token = await storage.write(lambda: storage.issue(env.row.connection, "refresh", 900))
                assert await storage.write(lambda: storage.exchange(token, "refresh", "client")) is not None
                assert await storage.write(lambda: storage.exchange(token, "refresh", "client")) is None
            else:
                await storage.write(storage.revoke_all)
        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission)
        assert len(calls) == 1
        assert not await McpEventSubscription.objects.filter(pk="subscription").aexists()
        await asyncio.sleep(0)
        assert env.runtime.commands.get_nowait() == RemoveCommand(env.emission.id, env.emission.created_at)
    asyncio.run(run())


def test_access_token_expiry_is_not_subscription_authority(env):
    storage.issue(env.row.connection, "access", -1)
    async def run():
        calls = []
        async def send(*args, **kwargs):
            calls.append(kwargs)
            return PinnedResponse(204, b"", False)
        await delivery.DeliveryService(env.runtime, send=send).deliver(env.emission)
        assert len(calls) == 1
    asyncio.run(run())


def test_stop_between_thread_post_and_loop_callback_drops_emission(env):
    async def run():
        env.runtime.loop = asyncio.get_running_loop()
        assert env.runtime.post_emission(env.emission)
        env.runtime.request_stop()
        await asyncio.sleep(0)
        assert not env.runtime.delivery_tasks
        assert env.runtime.writes.empty()
        assert not env.runtime.post_emission(env.emission)
    asyncio.run(run())


def test_authority_delete_posts_remove_only_after_commit(env):
    McpConnection.objects.update(revoked_at=env.clock.utcnow())
    async def run():
        loop = asyncio.get_running_loop()
        service = delivery.DeliveryService(env.runtime)
        def rollback():
            with transaction.atomic():
                assert service._authority(env.emission, loop)[1] == "revoked"
                assert not McpEventSubscription.objects.exists()
                transaction.set_rollback(True)
        await storage.write(rollback)
        await asyncio.sleep(0)
        assert env.runtime.commands.empty()
        assert await McpEventSubscription.objects.filter(pk="subscription").aexists()
    asyncio.run(run())


def test_successful_retry_stops_and_awaiting_emission_has_no_cursor(env):
    async def run():
        calls, delays = [], []
        async def send(*args, **kwargs):
            calls.append(kwargs)
            return PinnedResponse(503 if len(calls) == 1 else 204, b"", False)
        async def sleep(delay):
            delays.append(delay)
        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission._replace(cursor=None))
        assert len(calls) == 2 and delays == [30]
        assert env.runtime.writes.empty()
    asyncio.run(run())


def test_unexpected_sender_failure_logs_frames_without_exception_message(env, caplog):
    async def run():
        async def send(*args, **kwargs):
            raise RuntimeError(SECRET + " private reply")
        async def sleep(delay):
            pass
        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission)
    asyncio.run(run())
    assert SECRET not in caplog.text and "private reply" not in caplog.text
    assert "RuntimeError" in caplog.text and "test_mcp_events_delivery.py" in caplog.text
    assert sum("Unexpected webhook send exception" in record.message for record in caplog.records) == 3


def test_configuration_becoming_empty_retains_subscription_between_attempts(env, monkeypatch):
    reads = []
    def base_url():
        reads.append(None)
        return "https://mcp.example" if len(reads) == 1 else ""
    monkeypatch.setattr("twicc.mcp.oauth.config.base_url", base_url)

    async def run():
        calls, delays = [], []
        async def send(url, *, headers, body):
            calls.append((headers, body))
            return PinnedResponse(503, b"", False)
        async def sleep(delay):
            delays.append(delay)
            env.clock.advance(delay)
        await delivery.DeliveryService(env.runtime, send=send, sleep=sleep).deliver(env.emission)
        assert await McpEventSubscription.objects.filter(pk="subscription").aexists()
        assert len(calls) == 1 and delays == [30]
        assert len(reads) == 2
        assert env.runtime.commands.empty()
        assert env.runtime.writes.get_nowait() == env.emission.cursor
    asyncio.run(run())


def test_same_path_restored_instance_can_deliver_the_same_event(env):
    async def run():
        calls = []
        async def send(*args, **kwargs):
            calls.append(kwargs["headers"]["webhook-id"])
            return PinnedResponse(204, b"", False)
        restored = EventsRuntime(clock=env.clock.clock, data_dir=env.runtime.data_dir)
        await delivery.DeliveryService(env.runtime, send=send).deliver(env.emission)
        await delivery.DeliveryService(restored, send=send).deliver(env.emission)
        assert calls == [env.emission.event_id, env.emission.event_id]
    asyncio.run(run())
