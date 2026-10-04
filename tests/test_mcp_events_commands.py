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
