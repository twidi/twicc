"""In-process execution of drop-request payloads (no files involved)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc import paths
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.drop_requests_watcher import execute_drop_payload


async def _passthrough_db_write(coro_factory):
    return await coro_factory()


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Reroute the data dir to a temp path and keep workspace mutations off the
    channel layer (None in tests). ``get_workspaces_path`` resolves through
    ``get_data_dir`` dynamically, so patching that one function reroutes the
    whole tree."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)

    async def _noop():
        return None

    monkeypatch.setattr("twicc.workspaces._broadcast_after_write", _noop)
    return data_dir


def test_execute_unknown_kind_returns_failed():
    status = asyncio.run(execute_drop_payload({"kind": "nope:nope"}, "nope:nope"))
    assert status["status"] == "failed"
    assert "Unknown payload kind" in status["error"]
    assert "failed_at" in status


@pytest.mark.django_db(transaction=True)
def test_execute_mute_on_user_turn_roundtrip():
    project = Project.objects.create(id="-mute-drop", directory="/tmp/mute-drop")
    session = Session.objects.create(
        id="mute-drop-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    payload = {
        "session_id": session.id,
        "mute_on_user_turn": True,
    }

    with patch(
        "twicc.core.services.session_update.get_channel_layer",
        return_value=layer,
    ), patch(
        "twicc.core.services.session_update.ensure_provider_running",
    ), patch(
        "twicc.core.services.session_update.run_under_db_write_lock",
        side_effect=_passthrough_db_write,
    ):
        status = asyncio.run(execute_drop_payload(
            payload, "session:update_mute_on_user_turn"
        ))

    assert status["status"] == "updated"
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    message = layer.group_send.await_args.args[1]["data"]
    assert message["session"]["mute_on_user_turn"] is True


@pytest.mark.django_db(transaction=True)
def test_execute_mute_rejects_a_missing_field():
    status = asyncio.run(execute_drop_payload(
        {"session_id": "mute-drop-missing"},
        "session:update_mute_on_user_turn",
    ))

    assert status["status"] == "rejected"
    assert status["errors"][0]["field"] == "mute_on_user_turn"
    assert status["errors"][0]["code"] == "missing"


@pytest.mark.django_db(transaction=True)
def test_execute_mute_rejects_a_non_boolean_value():
    project = Project.objects.create(
        id="-mute-drop-invalid", directory="/tmp/mute-drop-invalid"
    )
    session = Session.objects.create(
        id="mute-drop-invalid-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
    )

    status = asyncio.run(execute_drop_payload(
        {"session_id": session.id, "mute_on_user_turn": "true"},
        "session:update_mute_on_user_turn",
    ))

    assert status["status"] == "rejected"
    assert status["errors"][0]["code"] == "invalid_mute_on_user_turn"
    session.refresh_from_db()
    assert session.mute_on_user_turn is False


@pytest.mark.django_db(transaction=True)
def test_execute_mute_accepts_hidden_session_without_broadcasting():
    project = Project.objects.create(
        id="-mute-drop-hidden", directory="/tmp/mute-drop-hidden"
    )
    session = Session.objects.create(
        id="mute-drop-hidden-session",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        hidden=True,
    )
    layer = SimpleNamespace(group_send=AsyncMock())

    with patch(
        "twicc.core.services.session_update.get_channel_layer",
        return_value=layer,
    ), patch(
        "twicc.core.services.session_update.ensure_provider_running",
    ), patch(
        "twicc.core.services.session_update.run_under_db_write_lock",
        side_effect=_passthrough_db_write,
    ):
        status = asyncio.run(execute_drop_payload(
            {"session_id": session.id, "mute_on_user_turn": True},
            "session:update_mute_on_user_turn",
        ))

    assert status["status"] == "updated"
    session.refresh_from_db()
    assert session.mute_on_user_turn is True
    assert layer.group_send.await_count == 0


@pytest.mark.django_db(transaction=True)
def test_execute_tree_detached_roundtrip():
    project = Project.objects.create(id="-td-drop", directory="/tmp/td-drop")
    session = Session.objects.create(
        id="td-drop-session", project=project, provider=Provider.CODEX.value, type=SessionType.SESSION,
    )
    layer = SimpleNamespace(group_send=AsyncMock())
    with patch("twicc.core.services.session_update.get_channel_layer", return_value=layer), \
            patch("twicc.core.services.session_update.ensure_provider_running"), \
            patch("twicc.core.services.session_update.run_under_db_write_lock",
                  side_effect=_passthrough_db_write):
        status = asyncio.run(execute_drop_payload(
            {"session_id": session.id, "tree_detached": True}, "session:update_tree_detached"))

    assert status["status"] == "updated"
    session.refresh_from_db()
    assert session.tree_detached is True
    assert layer.group_send.await_args.args[1]["data"]["session"]["tree_detached"] is True


@pytest.mark.django_db(transaction=True)
def test_execute_workspace_create_roundtrip(isolated_data_dir):
    payload = {"kind": "workspace:create", "name": "mcp-test-ws"}
    status = asyncio.run(execute_drop_payload(payload, "workspace:create"))
    assert status["status"] == "created", status
    assert status["workspace_id"]
    assert "created_at" in status


from twicc.cli._drop_request import transport


def test_local_mode_still_uses_files(tmp_path, monkeypatch):
    # drop_file.py binds the symbol at import time — patch the consumer.
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: tmp_path,
    )
    sub = transport.submit({"name": "x"}, kind="workspace:create")
    assert (tmp_path / f"{sub.request_uuid}.json").exists()
    assert sub.poll() is None  # no watcher running → still pending
    sub.cleanup()
    assert not (tmp_path / f"{sub.request_uuid}.json").exists()


@pytest.mark.django_db(transaction=True)
def test_backend_mode_executes_without_files(tmp_path, monkeypatch, isolated_data_dir):
    drops = tmp_path / "drops"
    drops.mkdir()
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: drops,
    )

    def _cli_side():
        transport.ensure_server_available()          # no-op in backend mode
        sub = transport.submit({"name": "ws-inproc"}, kind="workspace:create")
        out = transport.wait(sub, timeout_seconds=10)
        sub.cleanup()
        return out

    async def scenario():
        loop = asyncio.get_running_loop()
        token = transport.backend_loop.set(loop)
        try:
            # The CLI side runs in a worker thread, like invoke() under /mcp.
            return await asyncio.to_thread(_cli_side)
        finally:
            transport.backend_loop.reset(token)

    outcome = asyncio.run(scenario())
    assert outcome.status == "created"
    assert outcome.data["workspace_id"]
    assert list(drops.iterdir()) == []               # zero drop files touched


def test_backend_mode_session_create_injects_uuid(monkeypatch):
    # session:create must mint request_uuid == session_id, like write_drop_file.
    captured = {}

    async def fake_execute(payload, kind):
        captured.update(payload)
        return {"status": "created", "session_id": payload["session_id"]}

    monkeypatch.setattr(
        "twicc.drop_requests_watcher.execute_drop_payload", fake_execute,
    )

    def _cli_side():
        sub = transport.submit({"prompt": "hi"}, kind="session:create")
        out = transport.wait(sub, timeout_seconds=5)
        return sub, out

    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(_cli_side)
        finally:
            transport.backend_loop.reset(token)

    sub, out = asyncio.run(scenario())
    assert captured["session_id"] == sub.request_uuid
    assert out.data["session_id"] == sub.request_uuid
