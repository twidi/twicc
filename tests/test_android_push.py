"""Real registration storage and Android notification delivery rules."""

import asyncio
import base64
from importlib import import_module
from uuid import uuid4

import orjson
import pytest
from django.test import Client
from twicc.auth.session_auth import bind_session


@pytest.fixture
def push_server(settings, monkeypatch, tmp_path, db):
    from twicc.android_push import storage

    settings.TWICC_PASSWORD_HASH = "password"
    monkeypatch.setattr(storage, "get_data_dir", lambda: tmp_path)
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    bind_session(session, "password")
    session.save()
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    return client, tmp_path


def choices(**overrides):
    return {"user_turn": True, "pending_request": False, "extra_usage": False, "peer": False} | overrides


def registration():
    return {"device_id": str(uuid4()), "name": "Phone", "enabled": True, "preferences": choices()}


def subscription(token="sample"):
    return {
        "endpoint": f"https://fcm.googleapis.com/fcm/send/{token}",
        "keys": {
            "p256dh": base64.urlsafe_b64encode(b"\x04" + b"x" * 64).decode().rstrip("="),
            "auth": base64.urlsafe_b64encode(b"a" * 16).decode().rstrip("="),
        },
    }


def test_configuration_is_automatic_private_and_stable(push_server):
    client, root = push_server
    assert Client().get("/api/android-push/").status_code == 401
    first = client.get("/api/android-push/")
    second = client.get("/api/android-push/")
    assert first.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["public_key"]) == 87
    assert "no-store" in first["Cache-Control"]
    assert (root / "android-push/vapid-private.pem").stat().st_mode & 0o777 == 0o600


def test_grant_renews_endpoint_without_browser_cookies_and_revokes(push_server, settings):
    client, root = push_server
    body = registration()
    response = client.post("/api/android-push/", data=body, content_type="application/json")
    assert response.status_code == 200
    token = response.json()["token"]
    native = Client()
    update = {"device_id": body["device_id"], "subscription": subscription()}
    assert native.put("/api/auth/android-push/", data=update, content_type="application/json").status_code == 401
    assert (
        native.put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 200
    )
    path = root / "android-push/devices" / f"{body['device_id']}.json"
    saved = orjson.loads(path.read_bytes())
    assert token not in path.read_text()
    assert saved["subscription"] == subscription()
    assert path.stat().st_mode & 0o777 == 0o600
    settings.TWICC_PASSWORD_HASH = "changed"
    assert (
        native.put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 401
    )
    settings.TWICC_PASSWORD_HASH = "password"
    assert (
        native.delete(
            "/api/auth/android-push/",
            data={"device_id": body["device_id"]},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 200
    )
    assert not path.exists()
    assert (
        native.put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 401
    )


def test_registration_requires_boolean_choices_and_fcm_endpoint(push_server):
    client, _ = push_server
    body = registration()
    body["preferences"]["user_turn"] = "yes"
    assert client.post("/api/android-push/", data=body, content_type="application/json").status_code == 400
    body = registration()
    grant = client.post("/api/android-push/", data=body, content_type="application/json").json()["token"]
    update = {"device_id": body["device_id"], "subscription": subscription()}
    update["subscription"]["endpoint"] = "https://localhost/private"
    assert (
        Client()
        .put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {grant}",
        )
        .status_code
        == 400
    )


def test_android_does_not_suppress_push_but_browsers_do(monkeypatch):
    from twicc import presence

    monkeypatch.setattr(presence, "_last_active", {})
    monkeypatch.setattr(presence, "_browser_clients", {})
    clock = [1000.0]
    monkeypatch.setattr(presence.time, "monotonic", lambda: clock[0])
    presence.touch("mobile", "android", "phone")
    assert presence.is_user_present()
    assert not presence.is_browser_present()
    presence.touch("desktop", "browser", "computer")
    assert presence.is_browser_present()
    clock[0] += 301
    assert not presence.is_browser_present()
    # A late native handshake corrects this connection's earlier classification.
    presence.touch("mobile", "browser", "phone")
    presence.touch("mobile", "android", "phone")
    assert not presence.is_browser_present()


@pytest.mark.django_db(transaction=True)
def test_delivery_respects_choices_permission_and_browser_presence(push_server, monkeypatch):
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="p", directory="/tmp/android-push")
    Session.objects.create(id="s", project=project, provider="codex", file_path="s.jsonl")
    from twicc.android_push import storage, delivery
    from twicc import presence

    client, _ = push_server
    body = registration()
    grant = client.post("/api/android-push/", data=body, content_type="application/json").json()["token"]
    Client().put(
        "/api/auth/android-push/",
        data={"device_id": body["device_id"], "subscription": subscription()},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {grant}",
    )
    sent = []

    async def send(device, payload):
        sent.append(payload)

    monkeypatch.setattr(delivery, "_send_one", send)
    monkeypatch.setattr(presence, "is_browser_present", lambda: False)
    asyncio.run(delivery.send("pending_request", "Question", "Body"))
    assert sent == []
    asyncio.run(delivery.send("user_turn", "Finished", "Body", path="/project/p/session/s"))
    assert len(sent) == 1
    assert sent[0]["path"] == "/project/p/session/s"
    monkeypatch.setattr(presence, "is_browser_present", lambda: True)
    asyncio.run(delivery.send("user_turn", "Finished", "Body"))
    assert len(sent) == 1
    device = storage.read_device(body["device_id"])
    device["enabled"] = False
    storage.save_device(device)
    monkeypatch.setattr(presence, "is_browser_present", lambda: False)
    asyncio.run(delivery.send("user_turn", "Finished", "Body"))
    assert len(sent) == 1


def test_real_events_do_not_require_apprise_targets(monkeypatch):
    from types import SimpleNamespace
    from twicc import external_notifications as events
    from twicc.android_push import delivery
    from twicc.agent.states import AgentState

    sent = []
    monkeypatch.setattr(delivery, "dispatch", lambda *args, **kw: sent.append((args, kw)))
    monkeypatch.setattr(events, "read_synced_settings", dict)
    monkeypatch.setattr(events, "_last_seen", {})
    monkeypatch.setattr(events, "get_provider_helpers", lambda _: SimpleNamespace(LABEL="Codex"))
    info = SimpleNamespace(
        session_id="s",
        project_id="p",
        provider="codex",
        state=AgentState.USER_TURN,
        pending_requests=[],
        background_work_in_progress={},
    )
    events.notify_agent_event(info, "Session", "Project", None)
    events.notify_agent_event(info, "Session", "Project", None)
    assert len(sent) == 1
    assert sent[0][0][0] == "user_turn"
    assert sent[0][1]["path"] == "/project/p/session/s"
    info.pending_requests = [SimpleNamespace(request_type="ask_user_question")]
    events.notify_agent_event(info, "Session", "Project", None, True)
    assert sent[-1][0][0] == "pending_request"
    events.notify_extra_usage_started(
        "codex", SimpleNamespace(extra_usage_utilization=None, extra_usage_remaining_credits=20), {}
    )
    assert sent[-1][0][0] == "extra_usage"
    assert sent[-1][1]["action"] == "home"
    peer = SimpleNamespace(name="Alice", remote_display_name="Alice", base_url="https://peer.example")
    events.notify_peer_request(peer)
    assert sent[-1][1]["action"] == "peerRequests"
    events.notify_peer_message(
        SimpleNamespace(id=42, peer=peer, reply_to_message_id=None, title="Question", payload={"text": "Hello"})
    )
    assert sent[-1][1]["action"] == "peerInbox"
    assert sent[-1][1]["detail"] == "42"


@pytest.mark.django_db(transaction=True)
def test_real_encryption_and_stale_endpoint_retirement(push_server, monkeypatch):
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="p", directory="/tmp/android-push")
    Session.objects.create(id="s", project=project, provider="codex", file_path="s.jsonl")
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import http_ece
    import requests
    from twicc.android_push import delivery, storage

    client, _ = push_server
    body = registration()
    token = client.post("/api/android-push/", data=body, content_type="application/json").json()["token"]
    receiver = ec.generate_private_key(ec.SECP256R1())
    public = receiver.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    address = subscription()
    address["keys"]["p256dh"] = base64.urlsafe_b64encode(public).decode().rstrip("=")
    native = Client()
    update = {"device_id": body["device_id"], "subscription": address}
    assert (
        native.put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 200
    )
    captured = []

    def post(url, **kwargs):
        assert url == address["endpoint"]
        assert kwargs["headers"]["ttl"] == "300"
        assert kwargs["headers"]["urgency"] == "high"
        assert kwargs["headers"]["authorization"].startswith("vapid t=")
        assert kwargs["timeout"] == 15
        plaintext = http_ece.decrypt(kwargs["data"], private_key=receiver, auth_secret=b"a" * 16, version="aes128gcm")
        assert len(plaintext) <= 3900
        captured.append(orjson.loads(plaintext))
        result = requests.Response()
        result.status_code = 201
        result._content = b""
        return result

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(delivery.presence, "is_browser_present", lambda: False)
    asyncio.run(delivery.send("user_turn", "Finished", "😀" * 800, path="/project/p/session/s"))
    assert captured[0]["type"] == "twicc.notification"
    snapshot = storage.read_device(body["device_id"])
    # An old in-flight rejection cannot erase an endpoint that already rotated.
    update["subscription"] = subscription("rotated")
    native.put(
        "/api/auth/android-push/", data=update, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    storage.retire_subscription(snapshot)
    assert storage.read_device(body["device_id"])["subscription"] == update["subscription"]

    def expired(*args, **kwargs):
        result = requests.Response()
        result.status_code = 410
        result._content = b""
        return result

    monkeypatch.setattr(requests, "post", expired)
    # Supply a real receiver key, even for a rejected delivery.
    update["subscription"]["keys"]["p256dh"] = address["keys"]["p256dh"]
    native.put(
        "/api/auth/android-push/", data=update, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    delivery._post(storage.read_device(body["device_id"]), {"type": "test"})
    assert storage.read_device(body["device_id"])["subscription"] is None
    # A scoped grant still renews an expired subscription without browser cookies.
    assert (
        native.put(
            "/api/auth/android-push/",
            data=update,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        ).status_code
        == 200
    )


@pytest.mark.django_db(transaction=True)
def test_session_dismissals_ignore_browser_presence_and_notification_choices(push_server, monkeypatch):
    from twicc.android_push import delivery

    client, _ = push_server
    body = registration()
    body['preferences'] = choices(user_turn=False)
    token = client.post('/api/android-push/', data=body, content_type='application/json').json()['token']
    Client().put('/api/auth/android-push/', data={'device_id': body['device_id'], 'subscription': subscription()},
                 content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
    monkeypatch.setattr(delivery.presence, 'is_browser_present', lambda: True)
    sent = []

    async def capture(device, payload):
        sent.append(payload)

    monkeypatch.setattr(delivery, '_send_one', capture)
    ids = [f'session-{i:03d}-' + 'a' * 70 for i in range(75)]
    asyncio.run(delivery.send_dismissals(ids + [ids[0]]))
    assert {sid for payload in sent for sid in payload['session_ids']} == set(ids)
    assert sum(len(payload['session_ids']) for payload in sent) == len(ids)
    assert all(payload['type'] == 'twicc.dismiss_sessions' for payload in sent)
    assert all(len(orjson.dumps(payload)) <= 3900 for payload in sent)
    assert all('title' not in payload and 'body' not in payload for payload in sent)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('flag', ['archived', 'hidden'])
def test_notification_delivery_drops_sessions_that_are_no_longer_visible(push_server, monkeypatch, flag):
    from twicc.android_push import delivery
    from twicc.core.models import Project, Session

    client, _ = push_server
    project = Project.objects.create(id='p', directory='/tmp/android-push')
    session = Session.objects.create(id='s', project=project, provider='codex', file_path='s.jsonl', **{flag: True})
    body = registration()
    token = client.post('/api/android-push/', data=body, content_type='application/json').json()['token']
    Client().put('/api/auth/android-push/', data={'device_id': body['device_id'], 'subscription': subscription()},
                 content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
    monkeypatch.setattr(delivery.presence, 'is_browser_present', lambda: False)
    sent = []

    async def capture(device, payload):
        sent.append(payload)

    monkeypatch.setattr(delivery, '_send_one', capture)
    asyncio.run(delivery.send('user_turn', 'Finished', 'Body', path='/project/p/session/s'))
    assert sent == []
    setattr(session, flag, False)
    session.save(update_fields=[flag])
    asyncio.run(delivery.send('user_turn', 'Finished', 'Body', path='/project/p/session/s'))
    assert len(sent) == 1


def test_push_timestamps_describe_the_event_not_delayed_delivery(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from twicc.android_push import delivery

    event_time = datetime(2026, 11, 7, 12, tzinfo=UTC)
    clock = [event_time]

    class Clock:
        @staticmethod
        def now(tz):
            return clock[0]

    monkeypatch.setattr(delivery, 'datetime', Clock)
    monkeypatch.setattr(delivery.presence, 'is_browser_present', lambda: False)
    monkeypatch.setattr(delivery.storage, 'devices', lambda: [{'preferences': choices()}])
    sent = []

    async def capture(device, payload):
        sent.append(payload)

    monkeypatch.setattr(delivery, '_send_one', capture)

    async def scenario():
        delivery.dispatch('user_turn', 'Finished', 'Body')
        delivery.dismiss_sessions(['s'])
        clock[0] += timedelta(seconds=30)
        await asyncio.gather(*list(delivery._tasks))

    asyncio.run(scenario())
    assert {payload['type'] for payload in sent} == {'twicc.notification', 'twicc.dismiss_sessions'}
    assert all(payload['sent_at'] == event_time.isoformat() for payload in sent)
