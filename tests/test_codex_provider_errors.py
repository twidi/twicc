"""Terminal Codex provider errors persist and render as recoverable items."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import orjson
import pytest

from openai_codex.generated.v2_all import ErrorNotification
from twicc.core.enums import ItemDisplayLevel, ItemKind
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent_runs import is_task_complete
from twicc.providers.codex.compute import get_compute
from twicc.providers.codex.provider_errors import (
    CodexProviderError,
    build_provider_error_marker,
    parse_provider_error_marker,
)


def _injected_rollout_line(marker: str) -> dict:
    return {
        "timestamp": "2026-07-16T04:02:15.157Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": marker}],
        },
    }


def test_provider_error_marker_round_trip():
    error = CodexProviderError(
        turn_id="turn-1",
        message="Selected model is at capacity. Please try a different model.",
        error_type="serverOverloaded",
    )
    marker = build_provider_error_marker(error)

    assert parse_provider_error_marker(marker) == error
    assert parse_provider_error_marker("<twicc-provider-error>{broken") is None
    assert parse_provider_error_marker("ordinary user text") is None


def test_injected_provider_error_becomes_visible_api_error():
    compute = get_compute()
    parsed = _injected_rollout_line(
        build_provider_error_marker(
            CodexProviderError(
                turn_id="turn-1",
                message="Selected model is at capacity. Please try a different model.",
                error_type="serverOverloaded",
            )
        )
    )

    transformed = compute.transform_inline(parsed, session_id="session-1", line_num=8)

    assert transformed == orjson.dumps(parsed).decode()
    assert parsed["type"] == "twicc_provider_error"
    assert parsed["provider"] == "codex"
    assert parsed["isApiErrorMessage"] is True
    assert parsed["error"] == {
        "type": "serverOverloaded",
        "message": "Selected model is at capacity. Please try a different model.",
    }
    assert compute.compute_item_kind(parsed) == ItemKind.API_ERROR
    assert (
        compute.compute_item_display_level(parsed, ItemKind.API_ERROR)
        == ItemDisplayLevel.ALWAYS
    )


def test_hidden_resume_instruction_is_not_a_user_message():
    compute = get_compute()
    parsed = {
        "type": "event_msg",
        "payload": {
            "type": "user_message",
            "message": "<twicc-resume>Continue the interrupted turn.</twicc-resume>",
        },
    }

    assert compute.compute_item_kind(parsed) == ItemKind.SYSTEM


@pytest.mark.parametrize("error_type", ["server_overloaded", {"http_connection_failed": {"http_status_code": 401}}])
def test_native_failed_turn_becomes_visible_api_error(error_type):
    compute = get_compute()
    payload = {
        "type": "task_complete",
        "turn_id": "failed-turn",
        "last_agent_message": None,
        "error": {"message": "Provider request failed", "codex_error_info": error_type},
    }
    parsed = {"type": "event_msg", "payload": payload.copy()}

    transformed = compute.transform_inline(parsed, session_id="native-error", line_num=10)

    assert transformed == orjson.dumps(parsed).decode()
    assert parsed["type"] == "event_msg"
    assert parsed["payload"] == payload
    assert is_task_complete(parsed)
    assert parsed["provider"] == "codex"
    assert parsed["isApiErrorMessage"] is True
    assert parsed["turnId"] == "failed-turn"
    assert parsed["error"] == {"type": error_type, "message": "Provider request failed"}
    assert compute.compute_item_kind(parsed) == ItemKind.API_ERROR
    assert compute.compute_item_display_level(parsed, ItemKind.API_ERROR) == ItemDisplayLevel.ALWAYS
    # Recompute must preserve both the error and the native turn-end evidence.
    compute.transform_inline(parsed, session_id="native-error", line_num=10)
    assert parsed["payload"] == payload
    assert compute.compute_item_kind(parsed) == ItemKind.API_ERROR


@pytest.mark.parametrize("error", [None, {}, {"message": ""}, {"message": 42}])
def test_task_complete_without_valid_error_stays_system(error):
    compute = get_compute()
    parsed = {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "ok", "error": error}}
    assert compute.transform_inline(parsed, session_id="successful-turn", line_num=10) is None
    assert compute.compute_item_kind(parsed) == ItemKind.SYSTEM


def test_terminal_notification_closes_without_injecting_error():
    async def scenario():
        agent = CodexAgent.__new__(CodexAgent)
        agent.session_id = "thread-1"
        agent.last_activity = 0
        agent.error = None
        agent.kill_reason = None
        agent._thread = SimpleNamespace(inject_user_message=AsyncMock())
        agent._codex = SimpleNamespace(close=AsyncMock())
        agent._cancel_all_pending_futures = Mock()
        agent._transition_to_dead = AsyncMock()
        agent._items_by_id = {}
        agent._user_terminated_tool_ids = {}

        payload = ErrorNotification.model_validate(
            {
                "error": {
                    "message": "Selected model is at capacity. Please try a different model.",
                    "codexErrorInfo": "serverOverloaded",
                },
                "threadId": "thread-1",
                "turnId": "turn-1",
                "willRetry": False,
            }
        )

        await agent._handle_stream_event(
            SimpleNamespace(method="error", payload=payload)
        )

        agent._thread.inject_user_message.assert_not_awaited()
        agent._transition_to_dead.assert_awaited_once()
        agent._codex.close.assert_awaited_once()
        assert agent.kill_reason == "error"

    asyncio.run(scenario())
