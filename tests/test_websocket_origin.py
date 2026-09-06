"""Browser origin checks on every WebSocket, before consumers or PTYs run."""

import asyncio
from types import SimpleNamespace

import pytest
from channels.testing import WebsocketCommunicator

from twicc.origin_gate import PublicOriginGate, ShareOnlyApp


@pytest.fixture(autouse=True)
def routing(monkeypatch):
    # Import before patching so the module cannot retain this fixture's reader.
    from twicc.mcp.oauth import config

    values = {"publicBaseUrl": "https://app.example.com", "shareBaseUrl": "https://share.example.com"}

    def snapshot():
        return SimpleNamespace(settings=values, available=True)

    monkeypatch.setattr("twicc.synced_settings.read_routing_settings", snapshot)
    monkeypatch.setattr(config, "read_routing_settings", snapshot)
    return values


async def echo(scope, receive, send):
    assert (await receive())["type"] == "websocket.connect"
    await send({"type": "websocket.accept"})
    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            return
        await send({"type": "websocket.send", "text": message["text"]})


def communicator(app, host, origins, *, path="/ws/", scheme="ws", extra_headers=()):
    headers = [(b"host", host.encode()), *((b"origin", origin.encode()) for origin in origins), *extra_headers]
    comm = WebsocketCommunicator(app, path, headers=headers)
    comm.scope.update(scheme=scheme, client=("127.0.0.1", 43210), root_path="")
    return comm


@pytest.mark.parametrize("host,origin,scheme", [
    ("localhost:3500", "http://localhost:3500", "ws"),
    ("localhost:5173", "http://localhost:5173", "ws"),  # Vite preserves the browser's Host.
    ("127.0.0.1:3501", "http://127.0.0.1:3501", "ws"),
    ("[::1]:3501", "http://[::1]:3501", "ws"),
    ("192.168.1.10:3500", "http://192.168.1.10:3500", "ws"),
    ("twicc.local:3500", "http://twicc.local:3500", "ws"),
    ("app.example.com", "https://app.example.com", "wss"),
    ("app.example.com", "https://app.example.com", "ws"),  # TLS terminates at a proxy.
    ("app.example.com:443", "https://app.example.com", "ws"),
    ("app.example.com", "https://app.example.com:443", "wss"),
    ("localhost:80", "http://localhost", "ws"),
    ("APP.EXAMPLE.COM", "https://app.example.com", "wss"),
    ("app.example.com:8443", "https://app.example.com:8443", "ws"),
])
def test_matching_browser_origin_can_connect_and_reconnect(host, origin, scheme):
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        for _ in range(2):
            comm = communicator(gate, host, [origin], scheme=scheme)
            assert (await comm.connect())[0] is True
            await comm.send_to(text_data="normal traffic")
            assert await comm.receive_from() == "normal traffic"
            await comm.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("origins", [
    ["https://attacker.example"], ["null"], [""],
    ["http://localhost:3500.attacker.example"], ["http://localhost:3501"],
    ["http://127.0.0.1:3500"], ["http://localhost:3500/"],
    ["http://localhost:3500/path"], ["http://localhost:3500?x"],
    ["http://localhost:3500#x"], ["http://user@localhost:3500"],
    ["ws://localhost:3500"], ["file://localhost:3500"],
    ["http://localhost:3500", "http://localhost:3500"],
    ["http://localhost:3500 https://attacker.example"],
    [" http://localhost:3500"], ["http://localhost:3500\n"],
])
def test_cross_origin_opaque_and_malformed_origins_are_rejected(origins):
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        comm = communicator(gate, "localhost:3500", origins)
        assert await comm.connect() == (False, 4403)
        await comm.disconnect()

    asyncio.run(run())


def test_http_origin_cannot_use_a_known_secure_websocket():
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        comm = communicator(gate, "app.example.com", ["http://app.example.com"], scheme="wss")
        assert await comm.connect() == (False, 4403)
        await comm.disconnect()

    asyncio.run(run())


def test_forwarding_headers_cannot_authorize_a_foreign_origin():
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        comm = communicator(gate, "localhost:3500", ["https://attacker.example"], extra_headers=[
            (b"x-forwarded-host", b"attacker.example"), (b"x-forwarded-proto", b"https"),
        ])
        assert await comm.connect() == (False, 4403)
        await comm.disconnect()

    asyncio.run(run())


def test_native_client_without_origin_keeps_existing_access_checks():
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        comm = communicator(gate, "localhost:3500", [])
        assert (await comm.connect())[0] is True
        await comm.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("origin,allowed", [("https://share.example.com", True), ("https://app.example.com", False)])
def test_live_shares_require_their_own_browser_origin(origin, allowed):
    async def run():
        gate = PublicOriginGate(echo, ShareOnlyApp(echo))
        comm = communicator(gate, "share.example.com", [origin], path="/ws/share/token/")
        connected, code = await comm.connect()
        assert connected is allowed
        if not allowed:
            assert code == 4403
        await comm.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/ws/?subscribe=origin_probe", "/ws/terminal/0/"])
def test_real_application_rejects_foreign_origin_before_private_handlers(path, settings):
    from twicc.asgi import application

    settings.TWICC_PASSWORD_HASH = ""
    settings.TWICC_ALLOW_INSECURE_REMOTE = False

    async def run():
        comm = communicator(application, "localhost:3500", ["https://attacker.example"], path=path)
        assert await comm.connect() == (False, 4403)
        await comm.disconnect()

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("password", [False, True])
def test_real_ui_can_update_settings_and_reconnect(password, settings, tmp_path, monkeypatch):
    from django.contrib.sessions.backends.db import SessionStore
    from twicc.asgi import application
    from twicc.auth.session_auth import bind_session
    from twicc.core.enums import Provider
    import twicc.synced_settings as ss

    settings.TWICC_PASSWORD_HASH = "test-password" if password else ""
    settings.TWICC_ALLOW_INSECURE_REMOTE = False
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: tmp_path / "settings.json")
    # This serverless test has no provider processes or login-status probes.
    monkeypatch.setattr("twicc.asgi.is_provider_enabled", lambda _provider: False)
    ss._cache.clear()
    ss.write_synced_settings({**ss.read_synced_settings(), "disabledProviders": [provider.value for provider in Provider]})
    headers = []
    host = "app.example.com" if password else "localhost:5173"
    origin = f"https://{host}" if password else f"http://{host}"
    if password:
        session = SessionStore()
        bind_session(session, settings.TWICC_PASSWORD_HASH)
        session.save()
        headers = [(b"cookie", f"{settings.SESSION_COOKIE_NAME}={session.session_key}".encode()),
                   (b"x-forwarded-for", b"203.0.113.10")]

    async def run():
        for attempt in range(2):
            comm = communicator(application, host, [origin], extra_headers=headers,
                                path="/ws/?subscribe=synced_settings_updated")
            assert (await comm.connect())[0] is True
            snapshot = await comm.receive_json_from()
            assert snapshot["type"] == "synced_settings_updated"
            if attempt:
                assert snapshot["settings"]["autoUnpinOnArchive"] is False
            else:
                await comm.send_json_to({"type": "update_synced_settings", "request_id": "origin-test",
                                         "settings": {"autoUnpinOnArchive": False}})
                while True:
                    result = await comm.receive_json_from()
                    if result["type"] == "synced_settings_result":
                        assert result["status"] == "accepted"
                        break
            await comm.disconnect()
        # Cookies do not authorize a page from another origin.
        comm = communicator(application, host, ["https://attacker.example"], extra_headers=headers)
        assert await comm.connect() == (False, 4403)
        await comm.disconnect()

    try:
        asyncio.run(run())
        assert ss.read_synced_settings()["autoUnpinOnArchive"] is False
    finally:
        ss._cache.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["/ws/", "/ws/terminal/0/"])
@pytest.mark.parametrize("origins", [[], ["https://app.example.com"]])
@pytest.mark.parametrize("password", [False, True])
def test_origin_check_does_not_replace_remote_authentication(path, origins, password, settings):
    from twicc.asgi import application

    settings.TWICC_PASSWORD_HASH = "test-password" if password else ""
    settings.TWICC_ALLOW_INSECURE_REMOTE = False

    async def run():
        comm = communicator(application, "app.example.com", origins, path=path,
                            extra_headers=[(b"x-forwarded-for", b"203.0.113.10")])
        assert (await comm.connect())[0] is True  # Existing auth failure protocol accepts, then closes.
        assert await comm.receive_json_from() == {"type": "auth_failure"}
        assert (await comm.receive_output())["type"] == "websocket.close"
        await comm.disconnect()

    asyncio.run(run())
