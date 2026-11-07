"""Browser message metadata must preserve English helpers and technical values."""

from unittest.mock import AsyncMock

import orjson
import pytest

from twicc.core.ui_messages import ui_message, ui_message_text


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_shared_file_errors_keep_english_and_browser_identity(tmp_path):
    from twicc import file_content
    from twicc.views import _with_ui_message

    missing = tmp_path / "missing.txt"
    result = file_content.get_file_content(str(missing))
    assert result == {"error": "Cannot read file", "size": 0, "binary": False}
    assert "ui_message" not in orjson.loads(orjson.dumps(result))
    wire = _with_ui_message(result)
    assert wire["ui_message"] == ui_message("backend.file_content.read_failed")

    target = tmp_path / "existing.txt"
    target.write_text("user content")
    result = file_content.move_path(str(target), str(tmp_path))
    assert result["error"] == "Source is already in this directory"
    assert target.read_text() == "user content"
    assert result["error"].ui_message["key"] == "backend.file_content.same_directory"


def test_update_instructions_only_add_metadata_at_client_boundary():
    from twicc.installation import prepare_update_instructions_for_client, resolve_update_instructions

    for mode in ("uvx", "uv_tool", "pip", "source", "unknown"):
        raw = resolve_update_instructions(mode)._asdict()
        wire = prepare_update_instructions_for_client(raw)
        assert set(raw) == {"mode", "before", "command", "after"}
        assert all(wire[key] == value for key, value in raw.items())
        assert wire["before_ui_message"]["key"].startswith("backend.updates.")
        assert ("after_ui_message" in wire) == bool(raw["after"])


def test_git_tree_labels_and_errors_preserve_technical_content(monkeypatch):
    from twicc import git

    monkeypatch.setattr(git, "_parse_index_files", lambda directory: [{"path": "source.py", "unstaged_status": "modified"}])
    monkeypatch.setattr(git, "_attach_line_stats", lambda files, directory: None)
    index = git.get_index_files("/repo")
    assert index["tree"]["name"] == "Uncommitted files"
    assert index["tree"]["name_ui_message"] == ui_message("backend.git.uncommitted_files")
    assert index["tree"]["children"][0]["name"] == "source.py"

    monkeypatch.setattr(git, "_parse_commit_files", lambda *args: [{"path": "source.py", "status": "modified"}])
    monkeypatch.setattr(git, "_get_commit_numstat", lambda *args: {})
    commit = git.get_commit_files("/repo", "abc123456789")
    assert commit["tree"]["name"] == "Commit abc1234"
    assert commit["tree"]["name_ui_message"] == ui_message("backend.git.commit_files", {"hash": "abc1234"})

    with pytest.raises(git.GitError) as caught:
        git.validate_path_in_repo("/repo", "../private")
    assert str(caught.value) == "Path is outside the repository"
    assert caught.value.ui_message == ui_message("backend.git.path_outside_repository")


def test_ws_boundary_publishes_known_metadata_and_preserves_external_strings():
    from twicc.asgi import _exception_message, _ws_ui_messages

    message = ui_message_text("Known validation", "backend.example.validation", {"field": "name"})
    failure = RuntimeError(message)
    translated = _exception_message(failure)
    contextual = _exception_message(failure, ui_message_text("Failed to send message: Known validation", "backend.websocket.send_failed", {"detail": str(failure)}))
    assert contextual == "Failed to send message: Known validation"
    assert contextual.ui_message == message.ui_message
    wire = _ws_ui_messages({"type": "error", "message": translated, "errors": [{"message": message}]})
    assert wire["message"] == "Known validation"
    assert wire["ui_message"] == message.ui_message
    assert wire["errors"][0]["ui_message"] == message.ui_message
    assert _ws_ui_messages({"message": "Raw provider diagnostic"}) == {"message": "Raw provider diagnostic"}


@pytest.mark.anyio
async def test_validation_ws_sends_metadata_through_actual_boundary(monkeypatch, tmp_path):
    from channels.generic.websocket import AsyncJsonWebsocketConsumer
    from twicc.asgi import WSConsumer

    sent = AsyncMock()
    monkeypatch.setattr(AsyncJsonWebsocketConsumer, "send_json", sent)
    consumer = WSConsumer()
    await consumer._handle_validate_usage_dump_path({"file_path": str(tmp_path / "usage.json")})
    wire = sent.await_args.args[0]
    assert wire["valid"] is True
    assert wire["message"] == "Valid dump path"
    assert wire["ui_message"] == ui_message("backend.usage.valid_dump_path")
    await consumer._handle_kill_terminal({"terminal_context": "global", "terminal_index": 0})
    wire = sent.await_args.args[0]
    assert wire["message"] == "Cannot kill main terminal"
    assert wire["ui_message"] == ui_message("backend.websocket.main_terminal_protected")


@pytest.mark.anyio
async def test_startup_detail_metadata_survives_broadcast_and_reconnect(monkeypatch):
    from twicc import startup_progress

    monkeypatch.setattr(startup_progress, "_current_progress", {})
    layer = AsyncMock()
    monkeypatch.setattr(startup_progress, "get_channel_layer", lambda: layer)
    detail = ui_message_text("Migration 2/5", "backend.example.migration", {"current": 2, "total": 5})
    await startup_progress.broadcast_startup_progress("background_compute", 2, 5, provider="codex", detail=detail)
    [state] = startup_progress.get_startup_progress()
    assert state["detail"] == "Migration 2/5"
    assert state["detail_ui_message"] == detail.ui_message
    assert layer.group_send.await_args.args[1]["data"] == state
    startup_progress.set_startup_progress("background_compute", 3, 5, provider="codex")
    assert "detail_ui_message" not in startup_progress.get_startup_progress()[0]


def test_browser_probe_exposes_only_owned_reason_metadata():
    from twicc.browser_probe import _frame_verdict, _probe_response
    import httpx

    response = _probe_response({"reachable": False, "reason": ui_message_text("DNS lookup failed", "backend.browser_probe.dns_failed")})
    assert orjson.loads(response.content)["reason_ui_message"] == ui_message("backend.browser_probe.dns_failed")
    embeddable, reason = _frame_verdict(httpx.Headers({"X-Frame-Options": "deny"}))
    assert embeddable is False
    assert reason == "X-Frame-Options: deny"
    assert "reason_ui_message" not in orjson.loads(_probe_response({"reason": reason}).content)


def test_git_worktree_fallback_has_metadata_and_stderr_stays_verbatim(monkeypatch):
    from types import SimpleNamespace
    from twicc import git

    monkeypatch.setattr(git, "get_branches", lambda directory: [])
    result = SimpleNamespace(returncode=1, stderr="")
    monkeypatch.setattr(git.subprocess, "run", lambda *args, **kwargs: result)
    ok, message = git.create_worktree("/repo", "/worktree", "topic")
    assert ok is False
    assert message == "git worktree add failed"
    assert message.ui_message == ui_message("backend.git.worktree_add_failed")
    result.stderr = "external Git diagnostic\n"
    ok, message = git.create_worktree("/repo", "/worktree", "topic")
    assert ok is False
    assert message == "external Git diagnostic"
    assert not hasattr(message, "ui_message")


@pytest.mark.anyio
async def test_peer_message_snapshot_replays_error_metadata(transactional_db, monkeypatch, settings):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from twicc import asgi
    from twicc.core.models import Peer, PeerMessage

    descriptor = ui_message("backend.example.peer_error")
    peer = await Peer.objects.acreate(name="peer", base_url="https://peer.example", state="active")
    await PeerMessage.objects.acreate(peer=peer, message_id="pm_replay_error", thread_id="pm_replay_error", direction="out", status="failed", error="Known peer failure", error_ui_message=descriptor)
    consumer = asgi.WSConsumer()
    consumer.scope = {"query_string": b"subscribe=peer_messages_updated"}
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "replay-test"
    consumer.accept = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer._heartbeat_transport = SimpleNamespace(enable_heartbeat=Mock())
    consumer._provider_handlers = {}
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr(asgi, "scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr(asgi, "get_agent_manager_registry", lambda: SimpleNamespace(set_broadcast_callback=lambda callback: None))
    await consumer.connect()
    frame = consumer.send_json.await_args.args[0]
    assert frame["type"] == "peer_messages_updated"
    assert frame["messages"][0]["error"] == "Known peer failure"
    assert frame["messages"][0]["error_ui_message"] == descriptor


@pytest.mark.django_db
@pytest.mark.parametrize("missing,english,key", [
    ("project", "Project not found", "backend.projects.not_found"),
    ("session", "Session not found", "backend.file_tree.session_not_found"),
])
def test_file_scope_missing_resource_retains_http404_metadata(tmp_path, missing, english, key):
    from django.http import Http404
    from twicc.core.models import Project
    from twicc.file_tree import validate_path

    if missing == "session":
        Project.objects.create(id="scope-project", directory=str(tmp_path))
    with pytest.raises(Http404) as caught:
        validate_path("scope-project", str(tmp_path), session_id="missing-session")
    assert str(caught.value) == english
    assert caught.value.args[0].ui_message == ui_message(key)
    assert orjson.loads(orjson.dumps({"error": str(caught.value)})) == {"error": english}
