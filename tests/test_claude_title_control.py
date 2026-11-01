"""Claude title control uses the active SDK without transcript fallbacks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from claude_agent_sdk import CLIConnectionError, ClaudeSDKClient, ProcessError
from claude_agent_sdk._internal.query import Query

from twicc.providers.claude_code.helpers import ClaudeCodeHelpers
from twicc.providers.claude_code.title_control import (
    TitleControlError,
    canonicalize_claude_title,
    rename_running_session,
)
from twicc.providers.codex.helpers import CodexHelpers


SESSION_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def no_file_fallback(monkeypatch):
    writer = Mock(side_effect=AssertionError("Active rename must not mutate the transcript"))
    monkeypatch.setattr("twicc.providers.claude_code.titles.rename_session_in_jsonl", writer)
    yield
    writer.assert_not_called()


def connected_client(monkeypatch, *, result=None, error=None):
    request = AsyncMock(return_value={} if result is None else result, side_effect=error)
    monkeypatch.setattr(Query, "_send_control_request", request)
    client = ClaudeSDKClient()
    # Construct only the Query shell. These tests never start a CLI process.
    client._query = object.__new__(Query)
    return client, request


def test_request_payload_and_default_timeout(monkeypatch):
    client, request = connected_client(monkeypatch)
    assert asyncio.run(rename_running_session(client, SESSION_ID, "New")) is None
    request.assert_awaited_once_with(
        {"subtype": "rename_session", "title": "New", "source": "host", "session_id": SESSION_ID},
        timeout=10.0,
    )


def test_explicit_timeout(monkeypatch):
    client, request = connected_client(monkeypatch)
    asyncio.run(rename_running_session(client, SESSION_ID, "New", timeout=0.25))
    assert request.await_args.kwargs == {"timeout": 0.25}


def test_missing_query_is_unavailable():
    with pytest.raises(TitleControlError) as caught:
        asyncio.run(rename_running_session(ClaudeSDKClient(), SESSION_ID, "New"))
    assert caught.value.reason == "unavailable"


def test_missing_control_method_is_unavailable():
    client = ClaudeSDKClient()
    client._query = SimpleNamespace()
    with pytest.raises(TitleControlError) as caught:
        asyncio.run(rename_running_session(client, SESSION_ID, "New"))
    assert caught.value.reason == "unavailable"


@pytest.mark.parametrize("wrapped", [False, True])
def test_timeout_is_uncertain_failure(monkeypatch, wrapped):
    error = TimeoutError("rename wait expired")
    if wrapped:
        try:
            raise Exception("Control request timeout: rename_session") from error  # noqa: TRY002 - SDK wraps its timeout in plain Exception.
        except Exception as sdk_error:
            error = sdk_error
    client, _ = connected_client(monkeypatch, error=error)
    with pytest.raises(TitleControlError) as caught:
        asyncio.run(rename_running_session(client, SESSION_ID, "New"))
    assert caught.value.reason == "timeout"
    assert caught.value.__cause__ is error


def test_rejection_is_not_success(monkeypatch):
    error = Exception("rename_session is not supported in this context")
    client, _ = connected_client(monkeypatch, error=error)
    with pytest.raises(TitleControlError) as caught:
        asyncio.run(rename_running_session(client, SESSION_ID, "New"))
    assert caught.value.reason == "rejected"
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("error", [
    CLIConnectionError("Disconnected"), ProcessError("Exited", exit_code=1),
    BrokenPipeError("Closed"), anyio.BrokenResourceError(), anyio.ClosedResourceError(),
])
def test_connection_failure_is_unavailable(monkeypatch, error):
    client, _ = connected_client(monkeypatch, error=error)
    with pytest.raises(TitleControlError) as caught:
        asyncio.run(rename_running_session(client, SESSION_ID, "New"))
    assert caught.value.reason == "unavailable"
    assert caught.value.__cause__ is error


def test_cancellation_propagates(monkeypatch):
    client, _ = connected_client(monkeypatch, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rename_running_session(client, SESSION_ID, "New"))


@pytest.mark.parametrize("title, expected", [
    ("  État\t du\nchat  ", "État du chat"),
    ("‘Résumé’ — déjà… 中文 🚀", "‘Résumé’ — déjà… 中文 🚀"),
    ("a\u00a0\u2003b", "a b"),
    ("x" * 200, "x" * 200),
    (" " * 200 + "ok" + " " * 200, "ok"),
])
def test_canonical_title(title, expected):
    result = canonicalize_claude_title(title)
    assert result.title == expected
    assert result.error is None
    assert ClaudeCodeHelpers().validate_title(title) == result


@pytest.mark.parametrize("title", [None, "", "\t \n", "x" * 201])
def test_invalid_title(title):
    result = canonicalize_claude_title(title)
    assert result.title is None
    assert result.error
    assert ClaudeCodeHelpers().validate_title(title) == result


@pytest.mark.parametrize("character", ["\x00", "\x1b", "\x7f", "\u200b", "\u200d", "\ufeff", "\u00ad"])
def test_non_whitespace_controls_are_rejected(character):
    result = canonicalize_claude_title(f"Before{character}After")
    assert result.title is None
    assert result.error


def test_codex_whitespace_validation_is_unchanged():
    result = CodexHelpers().validate_title("  État\t du\nchat  ")
    assert result.title == "État\t du\nchat"
    assert result.error is None
