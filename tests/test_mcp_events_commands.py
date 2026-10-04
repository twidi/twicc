"""Command predicates distinguish provider echoes from genuine prompts."""
import orjson
import pytest

from twicc.providers.helpers import get_provider_helpers


def stored(value):
    return orjson.dumps(value).decode()


@pytest.mark.parametrize("command", ["/rename", "/compact", "/goal clear", "/plan"])
def test_claude_echo(command):
    content = stored({"type": "user", "message": {"content":
        f"<command-name>{command}</command-name><command-message>{command}</command-message>"}})
    assert get_provider_helpers("claude_code").is_command_message(content)


@pytest.mark.parametrize("text", ["ordinary prompt", "Quote <command-name>/rename</command-name>", "/rename"])
def test_claude_prompt(text):
    assert not get_provider_helpers("claude_code").is_command_message(stored({"message": {"content": text}}))


@pytest.mark.parametrize("command", ["/compact", "/goal clear", "/plan"])
def test_codex_injected_and_rewritten(command):
    payload = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": command}]}
    helpers = get_provider_helpers("codex")
    assert helpers.is_command_message(stored({"type": "response_item", "payload": payload}))
    assert helpers.is_command_message(stored({"type": "event_msg", "payload": {
        "type": "user_message", "message": command}, "twiccOriginalContent": payload}))
    assert not helpers.is_command_message(stored({"type": "event_msg", "payload": {
        "type": "user_message", "message": command}}))


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
@pytest.mark.parametrize("content", ["{broken <command-name>", "null", "[]", "{}"])
def test_malformed_content(provider, content):
    assert not get_provider_helpers(provider).is_command_message(content)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind,text,signal", [
    ("user_message", "Work on this", "user_message"),
    ("user_message", "<command-name>/rename</command-name>", "command_message"),
    ("system", "[Request interrupted by user]", "turn_end"),
])
def test_hybrid_watcher_preserves_prompt_command_and_interruption_signals(monkeypatch, kind, text, signal):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from twicc.core.models import Project, Session, SessionItem
    from twicc.providers.claude_code.agent import manager
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher

    project = Project.objects.create(id="bridge", directory="/tmp/bridge")
    session = Session.objects.create(id="bridge", project=project, provider="claude_code", hybrid=True)
    SessionItem.objects.create(session=session, line_num=1, kind=kind,
        content=stored({"type": "user", "message": {"content": text}}))
    handler = AsyncMock()
    monkeypatch.setattr(manager, "get_claude_code_agent_manager", lambda: SimpleNamespace(handle_hybrid_jsonl_signals=handler))
    asyncio.run(ClaudeCodeSessionsWatcher()._bridge_hybrid_signals(session.id, [1], False))
    handler.assert_awaited_once()
    session_id, signals = handler.await_args.args
    assert session_id == session.id
    assert getattr(signals, signal)
    assert sum((signals.user_message, signals.command_message, signals.turn_end)) == 1
