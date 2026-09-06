"""Passwordless startup suspends external access and preserves existing grants."""

from datetime import timedelta

import orjson
import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

import twicc.synced_settings as ss
from twicc.core.models import McpConnection, McpOAuthClient, McpOAuthRequest
from twicc.mcp.oauth import storage

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def startup_state(tmp_path, monkeypatch, settings):
    settings.TWICC_PASSWORD_HASH = ""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()

    async def direct(factory):
        return await factory()

    monkeypatch.setattr(storage, "run_under_db_write_lock", direct)
    ss.write_synced_settings({"externalMcpEnabled": True, "mcpBaseUrl": "https://mcp.example.com",
                              "_version": 7, "autoUnpinOnArchive": False})
    client = McpOAuthClient.objects.create(id="client", metadata={})
    connection = McpConnection.objects.create(id="grant", client=client, resource="https://mcp.example.com/mcp")
    for state in ("pending", "approved"):
        McpOAuthRequest.objects.create(id=state, client=client, connection=connection, state=state,
                                      expires_at=timezone.now() + timedelta(minutes=10))
    yield path, connection
    ss._cache.clear()


@pytest.mark.parametrize("origin", ["https://mcp.example.com", "not a valid origin"])
def test_startup_persists_disabled_and_preserves_grants(startup_state, settings, origin):
    path, connection = startup_state
    saved = ss.read_synced_settings()
    saved["mcpBaseUrl"] = origin
    ss.write_synced_settings(saved)

    assert async_to_sync(storage.enforce_password_requirement)() is True
    persisted = orjson.loads(path.read_bytes())
    assert persisted == {**saved, "externalMcpEnabled": False, "_version": 8}
    connection.refresh_from_db()
    assert connection.revoked_at is None
    assert set(McpOAuthRequest.objects.values_list("state", flat=True)) == {"pending", "approved"}

    # Another passwordless boot is idempotent. Restoring the password does not re-enable access.
    assert async_to_sync(storage.enforce_password_requirement)() is False
    settings.TWICC_PASSWORD_HASH = "configured-password"
    assert async_to_sync(storage.enforce_password_requirement)() is False
    assert orjson.loads(path.read_bytes()) == persisted
    connection.refresh_from_db()
    assert connection.revoked_at is None


def test_password_startup_preserves_enabled_access(startup_state, settings):
    path, connection = startup_state
    before = path.read_bytes()
    settings.TWICC_PASSWORD_HASH = "configured-password"
    assert async_to_sync(storage.enforce_password_requirement)() is False
    assert path.read_bytes() == before
    connection.refresh_from_db()
    assert connection.revoked_at is None


def test_existing_tokens_resume_only_after_manual_reactivation(startup_state, settings):
    from twicc.core.services.settings_mutation import update_synced_settings
    from twicc.mcp.oauth.provider import provider

    _, connection = startup_state
    pair = storage.token_pair(connection)
    assert async_to_sync(storage.enforce_password_requirement)() is True
    assert async_to_sync(provider.load_access_token)(pair.access_token) is None

    settings.TWICC_PASSWORD_HASH = "configured-password"
    assert async_to_sync(storage.enforce_password_requirement)() is False
    assert async_to_sync(provider.load_access_token)(pair.access_token) is None

    result = async_to_sync(update_synced_settings)({"externalMcpEnabled": True}, broadcast=False)
    assert result.status == "accepted"
    assert async_to_sync(provider.load_access_token)(pair.access_token) is not None
    renewed = storage.exchange(pair.refresh_token, "refresh", connection.client_id)
    assert renewed is not None
    assert async_to_sync(provider.load_access_token)(renewed.access_token) is not None


@pytest.mark.parametrize("reason", ["expired", "revoked"])
def test_suspension_preserves_expiration_and_explicit_revocation(startup_state, settings, reason):
    from twicc.core.models import McpOAuthCredential
    from twicc.core.services.settings_mutation import update_synced_settings
    from twicc.mcp.oauth.provider import provider

    _, connection = startup_state
    pair = storage.token_pair(connection)
    if reason == "expired":
        McpOAuthCredential.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        connection.revoked_at = timezone.now()
        connection.save(update_fields=["revoked_at"])
    assert async_to_sync(storage.enforce_password_requirement)() is True
    settings.TWICC_PASSWORD_HASH = "configured-password"
    result = async_to_sync(update_synced_settings)({"externalMcpEnabled": True}, broadcast=False)
    assert result.status == "accepted"
    connection.refresh_from_db()
    assert (connection.revoked_at is not None) == (reason == "revoked")
    assert async_to_sync(provider.load_access_token)(pair.access_token) is None
    assert storage.exchange(pair.refresh_token, "refresh", connection.client_id) is None


def test_passwordless_startup_preserves_unreadable_settings(startup_state):
    path, connection = startup_state
    path.write_bytes(b'{"broken"')
    ss._cache.clear()
    assert async_to_sync(storage.enforce_password_requirement)() is False
    assert path.read_bytes() == b'{"broken"'
    connection.refresh_from_db()
    assert connection.revoked_at is None


@pytest.mark.parametrize("kill_switch", ["0", "1"])
def test_server_disables_external_access_before_network_startup(startup_state, monkeypatch, kill_switch):
    from importlib import import_module

    run = import_module("twicc.cli.run")

    path, connection = startup_state
    monkeypatch.setenv("TWICC_NO_MCP", kill_switch)
    monkeypatch.setattr("twicc.providers.db_writer.start_db_writer", lambda: None)

    class StopBeforeNetwork(Exception):
        pass

    async def stop_before_network():
        assert orjson.loads(path.read_bytes())["externalMcpEnabled"] is False
        raise StopBeforeNetwork

    # Stop at the first outbound call. Do not launch providers or bind a socket.
    monkeypatch.setattr(run, "sync_all_providers", stop_before_network)
    with pytest.raises(StopBeforeNetwork):
        async_to_sync(run.run_server)(0)
    connection.refresh_from_db()
    assert connection.revoked_at is None


def test_failed_settings_write_preserves_grants(startup_state, monkeypatch, settings):
    _, connection = startup_state

    def fail_write(_data):
        raise OSError("disk full")

    monkeypatch.setattr(ss, "write_synced_settings", fail_write)
    with pytest.raises(OSError, match="disk full"):
        async_to_sync(storage.enforce_password_requirement)()
    # A failed settings write must not remove existing authorizations.
    settings.TWICC_PASSWORD_HASH = "configured-password"
    assert async_to_sync(storage.enforce_password_requirement)() is False
    connection.refresh_from_db()
    assert connection.revoked_at is None
