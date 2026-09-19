"""A hybrid pending request stores a dict, whatever the hook wrote.

The hook drop file is written by the Claude CLI's hook, not by TwiCC, so
``tool_input`` is only a dict by convention. Every reader indexes it — the
question normalizer, the CLI's ``--raw`` output, the permission serializer — and
a reader that runs inside the WebSocket consumer turns an `AttributeError` there
into a dropped connection.

The old ``payload.get("tool_input") or {}`` rescued a falsy value and nothing
else: a JSON string or a non-empty list was stored verbatim.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent


def make_agent() -> HybridClaudeAgent:
    """The method under test touches five attributes; give it those five.

    Building a real agent means a real session, a tmux server and a transcript;
    ``on_permission_request`` needs none of them.
    """
    agent = object.__new__(HybridClaudeAgent)
    agent.session_id = "s-hybrid"
    agent.cwd = "/tmp"
    agent._pending_requests = {}
    agent._is_stale_drop = AsyncMock(return_value=False)
    agent._schedule_gui_expiry = lambda nonce: None
    agent._notify_state_change = AsyncMock()
    agent.last_activity = 0.0
    return agent


@pytest.mark.parametrize("tool_input", [
    '{"questions": []}',   # a JSON string the hook forgot to decode
    ["questions"],         # a non-empty list
    42,
    None,
    {},
])
def test_the_stored_tool_input_is_always_a_dict(tool_input):
    agent = make_agent()

    registered = asyncio.run(agent.on_permission_request(
        {"tool_name": "AskUserQuestion", "tool_input": tool_input}, "nonce-1",
    ))

    assert registered is True
    assert agent._pending_requests["nonce-1"].tool_input == (
        tool_input if isinstance(tool_input, dict) else {}
    )


def test_a_well_formed_payload_is_stored_unchanged():
    agent = make_agent()
    questions = [{"question": "Which database?", "options": [{"label": "SQLite"}]}]

    asyncio.run(agent.on_permission_request(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": questions}},
        "nonce-1",
    ))

    assert agent._pending_requests["nonce-1"].tool_input == {"questions": questions}
