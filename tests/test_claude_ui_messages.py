"""Claude UI metadata preserves source text and explicit message provenance."""

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.agent.exceptions import SendDeliveryError
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.ui_messages import ui_message, ui_message_text
from twicc.providers.claude_code import bin as claude_bin
from twicc.providers.claude_code import commands
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.claude_code.agent.hybrid import tmux
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
from twicc.providers.claude_code.compute import get_compute
from twicc.providers.claude_code.helpers import ClaudeCodeHelpers
from twicc.providers.claude_code.title_control import canonicalize_claude_title
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler
from twicc.providers.helpers import AgentSettings
from twicc.providers.state import ProviderDisabledError


@pytest.fixture
def agent():
    return ClaudeCodeAgent(
        session_id="test-session", project_id="test-project", cwd="/tmp",
        settings=AgentSettings(), get_session_slug=AsyncMock(),
        on_cron_created=AsyncMock(), on_cron_deleted=AsyncMock(),
    )


@pytest.mark.parametrize("count", [1, 2])
def test_waiting_subagent_snapshot_and_broadcast_share_metadata(agent, count):
    agent._live_background_tasks = {str(i): "Provider task description" for i in range(count)}
    agent._live_monitor_tasks = {"monitor"}
    agent._broadcast_process_label = AsyncMock()
    asyncio.run(agent._refresh_waiting_label())

    expected = ui_message("backend.claude.waiting_subagents", {"count": count}, count=count)
    label = f"waiting for {count} subagent{'s' if count > 1 else ''}"
    agent._broadcast_process_label.assert_awaited_once_with(label, expected)
    assert agent.get_info().label == label
    assert agent.get_info().label_ui_message == expected


def test_waiting_monitor_wakeup_and_clear_metadata(agent, monkeypatch):
    agent._waiting_label_active = True
    agent._live_monitor_tasks = {"monitor"}
    monkeypatch.setattr(agent, "_has_pending_wakeup", lambda: True)
    monkeypatch.setattr(agent, "_format_wakeup_time", lambda: "14:32")
    assert agent.current_status_label() == "monitoring"
    assert agent.current_status_label_ui_message() == ui_message("backend.claude.monitoring")

    agent._live_monitor_tasks.clear()
    assert agent.current_status_label() == "waiting for scheduled wakeup (14:32)"
    assert agent.current_status_label_ui_message() == ui_message(
        "backend.claude.waiting_wakeup", {"time": "14:32"},
    )
    agent._broadcast_process_label = AsyncMock()
    asyncio.run(agent._clear_waiting_label())
    agent._broadcast_process_label.assert_awaited_once_with("")
    assert agent.get_info().label is None
    assert agent.get_info().label_ui_message is None


@pytest.mark.parametrize("known_source", [False, True])
def test_process_error_keeps_raw_text_and_prefers_known_context(agent, known_source):
    agent._transition_to_dead = AsyncMock()
    agent._shutdown_sdk_client = AsyncMock()
    presentation = ui_message("backend.claude.start_failed", {"diagnostic": "raw SDK detail"})
    diagnostic = ui_message_text("Process not started", "backend.claude.process_not_started")
    exc = RuntimeError(diagnostic) if known_source else OSError("raw SDK detail")
    raw = f"Failed to start process: {exc}"
    asyncio.run(agent._handle_error(raw, exc=exc, presentation=presentation))

    assert agent.error == raw
    assert agent.get_info().error == raw
    assert agent.get_info().error_ui_message == (diagnostic.ui_message if known_source else presentation)
    agent._transition_to_dead.assert_awaited_once()
    agent._shutdown_sdk_client.assert_awaited_once()


def test_send_guard_retains_runtime_error_and_english(agent):
    with pytest.raises(RuntimeError, match="Process not started") as caught:
        asyncio.run(agent.send("Hello"))
    assert type(caught.value) is RuntimeError
    assert caught.value.args[0].ui_message == ui_message("backend.claude.process_not_started")

    agent._client = object()
    agent.state = AgentState.STARTING
    with pytest.raises(RuntimeError, match="Cannot send message in state starting") as caught:
        asyncio.run(agent.send("Hello"))
    assert caught.value.args[0].ui_message == ui_message(
        "backend.claude.cannot_send_state", {"state": AgentState.STARTING},
    )


@pytest.mark.parametrize("content", ["", "  ", "<tool_use_error> </tool_use_error>", []])
def test_unknown_tool_error_fallback_has_metadata_in_live_and_batch(content):
    parsed = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": content},
    ]}}
    compute = get_compute()
    live = compute.extract_tool_result_info(parsed, session_id="s")
    batch = compute.analyze_content(parsed, session_id="s", tool_use_map={})
    assert live.error_text == batch.tool_result_error == "Unknown error"
    assert live.error_text.ui_message == batch.tool_result_error.ui_message == ui_message(
        "backend.claude.unknown_tool_error",
    )
    assert parsed["message"]["content"][0]["content"] == content


@pytest.mark.parametrize("content, expected", [
    ("Unknown error", "Unknown error"),
    ("<tool_use_error>Unknown error</tool_use_error>", "Unknown error"),
    ("Exit code 7\nProvider detail", "Exit code 7"),
    ("Provider diagnostic", "Provider diagnostic"),
])
def test_provider_tool_errors_keep_verbatim_text_without_metadata(content, expected):
    parsed = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": content},
    ]}}
    compute = get_compute()
    live = compute.extract_tool_result_info(parsed, session_id="s")
    batch = compute.analyze_content(parsed, session_id="s", tool_use_map={})
    assert live.error_text == batch.tool_result_error == expected
    assert getattr(live.error_text, "ui_message", None) is None
    assert getattr(batch.tool_result_error, "ui_message", None) is None


@pytest.mark.parametrize("kind, fallback", [("command", "Custom command"), ("skill", "Skill"), ("workflow", "Workflow")])
def test_command_description_defaults_remain_english(tmp_path, kind, fallback):
    if kind == "command":
        (tmp_path / "fallback.md").write_text("")
        (tmp_path / "external.md").write_text(fallback)
        discovered = commands._scan_commands_dir(tmp_path)
    elif kind == "skill":
        for name, body in [("fallback", ""), ("external", fallback)]:
            (tmp_path / name).mkdir()
            (tmp_path / name / "SKILL.md").write_text(body)
        discovered = commands._scan_skills_dir(tmp_path)
        single = commands._scan_single_skill_dir(tmp_path / "fallback", "test-plugin")
        assert single.description == fallback
    else:
        (tmp_path / "fallback.js").write_text("export const meta = {}")
        (tmp_path / "external.js").write_text(f"export const meta = {{description: '{fallback}'}}")
        discovered = commands._scan_workflows_dir(tmp_path)

    by_name = {command.name: command for command in discovered}
    generated = by_name["fallback"]
    external = by_name["external"]
    assert generated.description == external.description == fallback


def test_hybrid_attachment_errors_keep_codes_and_context(monkeypatch, tmp_path):
    content = SimpleNamespace(user_text="/help", manifest=SimpleNamespace(entries=[SimpleNamespace(mode="inline")]))
    with pytest.raises(SendDeliveryError) as caught:
        HybridClaudeAgent._refuse_command_with_content(content)
    assert str(caught.value) == "Attachments cannot be sent with a command"
    assert caught.value.code == "attachments_with_command"
    assert caught.value.ui_message == ui_message("backend.claude.attachments_with_command")

    directory = tmp_path / "has whitespace"
    monkeypatch.setattr("twicc.providers.claude_code.agent.hybrid.agent.get_session_hybrid_dir", lambda session_id: directory)
    with pytest.raises(SendDeliveryError) as caught:
        asyncio.run(HybridClaudeAgent._check_reference_dir(SimpleNamespace(session_id="s"), content))
    assert str(directory) in str(caught.value)
    assert caught.value.ui_message == ui_message("backend.claude.attachment_path_whitespace", {"path": str(directory)})


@pytest.mark.parametrize("failure", ["load", "paste"])
def test_tmux_errors_keep_raw_diagnostic_and_existing_exception(monkeypatch, failure):
    calls = []
    def run(args, **kwargs):
        calls.append(args[0])
        failed = args[0] == f"{failure}-buffer"
        return subprocess.CompletedProcess(args, int(failed), b"", b"raw tmux detail\n")
    monkeypatch.setattr(tmux, "_run", run)
    with pytest.raises(RuntimeError) as caught:
        tmux.paste_text("s", "user content")
    assert str(caught.value) == f"tmux {failure}-buffer failed: raw tmux detail\n"
    assert caught.value.args[0].ui_message == ui_message(
        f"backend.claude.tmux_{failure}_failed", {"diagnostic": "raw tmux detail\n"},
    )
    assert "send-keys" not in calls


def test_missing_tmux_and_binary_keep_context(monkeypatch, tmp_path):
    monkeypatch.setattr(tmux, "get_tmux_path", lambda: None)
    with pytest.raises(RuntimeError) as caught:
        tmux._tmux_base()
    assert caught.value.args[0].ui_message == ui_message("backend.claude.hybrid_tmux_required")

    monkeypatch.setattr(claude_bin, "_BIN_PATH", None)
    monkeypatch.setattr(claude_bin, "files", lambda name: tmp_path)
    with pytest.raises(FileNotFoundError) as caught:
        claude_bin.resolve_bundled_binary()
    path = tmp_path / "_bundled" / "claude"
    assert str(caught.value) == f"Bundled Claude binary not found at {path}"
    assert caught.value.args[0].ui_message == ui_message("backend.claude.binary_not_found", {"path": str(path)})


def test_validation_messages_preserve_english_and_parameters():
    valid, message = ClaudeCodeHelpers().validate_usage_file_payload({})
    assert valid is False
    assert message == "Missing required keys: five_hour, seven_day"
    assert message.ui_message == ui_message("backend.claude.usage_missing_keys", {"keys": "five_hour, seven_day"})
    valid, message = ClaudeCodeHelpers().validate_usage_file_payload({"five_hour": {}, "seven_day": {}})
    assert valid is True
    assert message == "Valid usage file"
    assert message.ui_message == ui_message("backend.claude.usage_file_valid")

    title = canonicalize_claude_title("Title\x00")
    assert title.title is None
    assert title.error == "Title cannot contain non-printable characters"
    assert title.error.ui_message == ui_message("backend.claude.title_non_printable")


def test_disabled_provider_browser_response_keeps_shared_metadata(monkeypatch):
    error = ProviderDisabledError(Provider.CLAUDE_CODE)
    monkeypatch.setattr("twicc.providers.claude_code.ws.ensure_provider_running", MagicMock(side_effect=error))
    consumer = SimpleNamespace(send_json=AsyncMock())
    handler = ClaudeCodeWSHandler(consumer)
    asyncio.run(handler._handle_pending_request_response({}))
    consumer.send_json.assert_awaited_once_with({
        "type": "error", "code": "provider_disabled", "provider": Provider.CLAUDE_CODE.value,
        "message": str(error), "ui_message": error.ui_message,
    })


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("source_changed, text, key", [
    (True, "Claude source changes during title drain", "backend.claude.title_source_changed"),
    (False, "Claude title drain is incomplete", "backend.claude.title_drain_incomplete"),
])
def test_title_drain_refusals_keep_metadata_at_browser_boundary(monkeypatch, tmp_path, source_changed, text, key):
    from tests.db_write_helpers import run_with_direct_writes
    from twicc.asgi import _exception_message, _ws_ui_messages
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code import sessions_watcher
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager

    project = Project.objects.create(id="drain-project")
    session = Session.objects.create(
        id="drain-session", project=project, provider=Provider.CLAUDE_CODE,
        file_path="drain.jsonl", storage_incarnation="source-incarnation",
    )
    (tmp_path / session.file_path).write_text('{"type":"custom-title","customTitle":"Title"}\n')
    monkeypatch.setattr("twicc.provider_homes.claude_projects_dir", lambda: tmp_path)

    async def process_path(path):
        if source_changed:
            await Session.objects.filter(pk=session.pk).aupdate(history_epoch=1)

    monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: SimpleNamespace(process_path=process_path))

    async def scenario():
        with pytest.raises(RuntimeError) as caught:
            await ClaudeCodeAgentManager()._ingest_title_available(session.id)
        assert type(caught.value) is RuntimeError
        assert str(caught.value) == text
        assert _ws_ui_messages({"message": _exception_message(caught.value)}) == {
            "message": text, "ui_message": {"key": key, "params": {}},
        }

    run_with_direct_writes(scenario)


def test_prior_process_drain_refusal_keeps_metadata_at_browser_boundary(monkeypatch):
    from twicc.asgi import _exception_message, _ws_ui_messages
    from twicc.providers.claude_code import title_publication
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.claude_code.title_sync import TitleSyncCoordinator

    monkeypatch.setattr(title_publication, "_coordinator", TitleSyncCoordinator())
    monkeypatch.setattr(title_publication, "_worker", None)
    manager = ClaudeCodeAgentManager()
    manager._title_processes = {
        "session": SimpleNamespace(_title_process_token="prior", _title_ready=False),
    }

    async def scenario():
        worker = manager._ensure_title_publications()
        try:
            with pytest.raises(RuntimeError) as caught:
                await manager._register_and_start(SimpleNamespace(session_id="session"), "Hello", resume=True)
            assert type(caught.value) is RuntimeError
            assert _ws_ui_messages({"message": _exception_message(caught.value)}) == {
                "message": "Prior Claude process final drain is incomplete",
                "ui_message": {"key": "backend.claude.prior_process_drain_incomplete", "params": {}},
            }
        finally:
            await worker.close_publications()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_final_title_drain_refusal_retains_route_and_browser_metadata(monkeypatch):
    from tests.db_write_helpers import run_with_direct_writes
    from twicc.asgi import _exception_message, _ws_ui_messages
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code import title_publication
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.providers.claude_code.title_sync import SourceCursor, TitleSyncCoordinator

    project = Project.objects.create(id="final-drain-project")
    session = Session.objects.create(id="final-drain-session", project=project, storage_incarnation="source")
    coordinator = TitleSyncCoordinator()
    monkeypatch.setattr(title_publication, "_coordinator", coordinator)
    monkeypatch.setattr(title_publication, "_worker", None)
    manager = ClaudeCodeAgentManager()
    boundary = SourceCursor(session.id, "source", 0, 1)
    monkeypatch.setattr(manager, "_ingest_title_available", AsyncMock(return_value=boundary))

    async def scenario():
        worker = manager._ensure_title_publications()
        try:
            await manager._title_lane(lambda: coordinator.attach_process(session.id, "process", hybrid=False))
            with pytest.raises(RuntimeError) as caught:
                await manager._finish_title_process(session.id, "process")
            assert type(caught.value) is RuntimeError
            assert coordinator.process_token(session.id) == "process"
            assert _ws_ui_messages({"message": _exception_message(caught.value)}) == {
                "message": "Final Claude title drain is incomplete",
                "ui_message": {"key": "backend.claude.final_title_drain_incomplete", "params": {}},
            }
        finally:
            await worker.close_publications()

    run_with_direct_writes(scenario)


@pytest.fixture
def mismatched_native_content():
    from twicc.core.services.attachments.types import AttachmentContent, AttachmentManifest, ManifestEntry

    entry = ManifestEntry(1, "image.png", "image", 1, 1, "inline", None)
    return AttachmentContent((), AttachmentManifest("session", None, (entry,)), "Hello")


@pytest.mark.parametrize("transport", ["sdk", "hybrid"])
def test_native_manifest_mismatch_keeps_value_error_and_source_metadata(agent, mismatched_native_content, transport):
    async def scenario():
        with pytest.raises(ValueError) as caught:
            if transport == "sdk":
                await agent._build_query_prompt("Hello", None, None, content=mismatched_native_content)
            else:
                await HybridClaudeAgent._materialize_content(SimpleNamespace(), mismatched_native_content)
        assert type(caught.value) is ValueError
        assert str(caught.value) == "The native parts do not match the inline manifest entries"
        assert caught.value.args[0].ui_message == {
            "key": "backend.claude.native_manifest_mismatch", "params": {},
        }

    asyncio.run(scenario())


def test_native_manifest_mismatch_reaches_sdk_process_error(agent, mismatched_native_content):
    agent._client = SimpleNamespace(query=AsyncMock())
    agent.state = AgentState.USER_TURN
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()
    agent._transition_to_dead = AsyncMock()
    agent._shutdown_sdk_client = AsyncMock()

    assert asyncio.run(agent.send("Hello", content=mismatched_native_content)) is False
    assert agent.get_info().error == "Failed to send message: The native parts do not match the inline manifest entries"
    assert agent.get_info().error_ui_message == {
        "key": "backend.claude.native_manifest_mismatch", "params": {},
    }


def test_native_manifest_mismatch_reaches_hybrid_first_paste_error(monkeypatch, mismatched_native_content):
    agent = HybridClaudeAgent("session", "project", "/tmp", AgentSettings())
    agent._transition_to_dead = AsyncMock()
    monkeypatch.setattr(tmux, "kill_session", lambda session_id: None)

    asyncio.run(agent._first_paste("Hello", None, None, mismatched_native_content))
    assert agent.get_info().error == "First paste failed: The native parts do not match the inline manifest entries"
    assert agent.get_info().error_ui_message == {
        "key": "backend.claude.native_manifest_mismatch", "params": {},
    }
