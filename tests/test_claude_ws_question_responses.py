"""Lock the wire output of the Claude ``AskUserQuestion`` answer path.

``ClaudeCodeWSHandler._handle_pending_request_response`` turns the widget's
payload into the ``PermissionResult`` the SDK returns to the agent. A later
refactor moves that translation into a shared module; these tests are the
baseline it must reproduce byte for byte, captured while the original code is
still in place.

Unlike the Codex builders this one is not a pure function: it looks the pending
request up through the manager, which the handler reaches through a module-level
factory at call time. So the factory is patched and the response is read off the
mock's ``resolve_pending_request`` call.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from twicc.agent.states import PendingRequest
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler


QUESTIONS = [
    {"question": "Which database?", "header": "Database",
     "options": [{"label": "PostgreSQL"}, {"label": "SQLite"}]},
    {"question": "Which cache?", "header": "Cache",
     "options": [{"label": "Redis"}, {"label": "Memcached"}]},
]


@pytest.fixture
def pending():
    return PendingRequest(
        request_id="req-1",
        request_type="ask_user_question",
        tool_name="AskUserQuestion",
        tool_input={"questions": QUESTIONS},
        created_at=1_700_000_000.0,
    )


def _answer(pending, content_extra: dict):
    """Run the handler over one widget payload; return the resolved response."""
    manager = MagicMock()
    manager.get_agent_info.return_value = MagicMock(pending_requests=[pending])
    manager.resolve_pending_request = AsyncMock(return_value=True)

    handler = ClaudeCodeWSHandler(consumer=None)
    content = {
        "session_id": "s-1",
        "request_id": "req-1",
        "request_type": "ask_user_question",
        **content_extra,
    }
    # The provider gate reads the DB; it is not what these tests lock.
    with patch("twicc.providers.claude_code.ws.ensure_provider_running"), \
         patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager",
               return_value=manager):
        asyncio.run(handler._handle_pending_request_response(content))

    manager.resolve_pending_request.assert_awaited_once()
    return manager.resolve_pending_request.await_args.args[2]


def test_submit_allows_with_the_stored_questions(pending):
    answers = {"Which database?": "PostgreSQL", "Which cache?": "Redis"}

    response = _answer(pending, {"action": "submit", "answers": answers})

    assert isinstance(response, PermissionResultAllow)
    # The questions are rebuilt from the pending request, never from the caller.
    assert response.updated_input == {"questions": QUESTIONS, "answers": answers}
    assert response.updated_permissions is None


def test_a_missing_action_defaults_to_submit(pending):
    response = _answer(pending, {"answers": {"Which database?": "SQLite"}})

    assert isinstance(response, PermissionResultAllow)


def test_partial_denies_with_the_native_clarify_text(pending):
    response = _answer(pending, {
        "action": "partial",
        "answers": {"Which database?": "PostgreSQL"},
    })

    assert isinstance(response, PermissionResultDeny)
    assert response.message == (
        "The user wants to clarify these questions.\n"
        "    This means they may have additional information, context or questions for you.\n"
        "    Take their response into account and then reformulate the questions if appropriate.\n"
        "    Start by asking them what they would like to clarify.\n"
        "\n"
        "    Questions asked:\n"
        '- "Which database?"\n'
        "  Answer: PostgreSQL\n"
        '- "Which cache?"\n'
        "  (No answer provided)"
    )


def test_cancel_denies_with_the_fixed_decline_text(pending):
    response = _answer(pending, {"action": "cancel", "answers": {}})

    assert isinstance(response, PermissionResultDeny)
    assert response.message == (
        "The user chose not to answer these questions. Acknowledge this briefly "
        "and ask them how they would like to proceed."
    )


@pytest.mark.parametrize("action", [["submit"], {"a": 1}, 42, None])
def test_a_malformed_action_does_not_break_the_connection(pending, action):
    """Same failure mode as a malformed ``answers``, one line up.

    The known-actions set is a frozenset, so testing membership of an
    unhashable value raises before the handler ever looks at it.
    """
    response = _answer(pending, {"action": action, "answers": {}})

    assert isinstance(response, PermissionResultAllow)


def test_a_malformed_answers_payload_does_not_break_the_connection(pending):
    """A frame this handler cannot read must not tear the WebSocket down.

    An exception raised here propagates out of the consumer: the browser loses
    its updates channel, and the pending request stays unresolved, so the agent
    is blocked with nothing left to unblock it. The old branch never inspected
    the payload and so could not raise; the shared translator must keep that
    property.
    """
    response = _answer(pending, {"action": "submit", "answers": ["PostgreSQL"]})

    assert isinstance(response, PermissionResultAllow)
    assert response.updated_input == {"questions": QUESTIONS, "answers": {}}


@pytest.mark.parametrize("tool_name", [["elicitationForm"], {"a": 1}])
def test_a_malformed_tool_name_does_not_break_the_connection(pending, tool_name):
    """The elicitation dispatch tests a frozenset, so it raises on this too.

    Same class as the malformed `action` and `answers` above: an exception here
    leaves the consumer, the browser loses its updates channel, and the request
    stays pending with nothing left to resolve it.
    """
    response = _answer(pending, {"tool_name": tool_name, "action": "cancel",
                                 "answers": {}})

    # The frame falls through to the question branch, which is what its
    # ``request_type`` says it is.
    assert isinstance(response, PermissionResultDeny)


@pytest.mark.parametrize("tool_name", ["elicitationForm", "elicitationUrl"])
def test_an_elicitation_frame_still_takes_the_elicitation_branch(tool_name):
    """The other half of the `tool_name` gate: it must not refuse too much.

    This dispatch is what routes an MCP elicitation answer. Disabled, a
    well-formed frame falls through to the "missing request_type" warning and
    the elicitation hangs forever — a failure no negative test would see.
    """
    pending = PendingRequest(
        request_id="req-1", request_type="ask_user_question", tool_name=tool_name,
        tool_input={}, created_at=1_700_000_000.0,
    )
    response = _answer(pending, {"tool_name": tool_name, "action": "decline"})

    # The elicitation bridge takes a raw wire dict, not a PermissionResult.
    assert response == {"action": "decline"}
