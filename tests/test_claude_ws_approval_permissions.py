"""The approval branch reads `updated_permissions` straight from the wire.

Two readers assume a list of dicts: the trust clamp, which is a security floor,
and the `setMode` persist. A frame carrying anything else — a stale bundle, a
replayed draft, a script speaking the protocol — raised out of the WebSocket
consumer, which has no guard above it: the browser loses its updates channel and
the approval stays pending with nothing left to resolve it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_sdk.types import PermissionResultAllow

from twicc.agent.states import PendingRequest
from twicc.providers.claude_code.ws import ClaudeCodeWSHandler


@pytest.fixture
def pending():
    return PendingRequest(
        request_id="req-1", request_type="tool_approval", tool_name="Bash",
        tool_input={"command": "ls"}, created_at=1_700_000_000.0,
    )


def _approve(pending, updated_permissions):
    """Run an allow frame through the handler; return the resolved response."""
    manager = MagicMock()
    manager.get_agent_info.return_value = MagicMock(pending_requests=[pending])
    manager.resolve_pending_request = AsyncMock(return_value=True)

    handler = ClaudeCodeWSHandler(consumer=None)
    content = {
        "session_id": "s-1", "request_id": "req-1", "request_type": "tool_approval",
        "decision": "allow", "updated_permissions": updated_permissions,
    }
    with patch("twicc.providers.claude_code.ws.ensure_provider_running"), \
         patch("twicc.providers.claude_code.ws.get_claude_code_agent_manager",
               return_value=manager), \
         patch("twicc.providers.claude_code.ws._clamp_setmode_permissions_for_trust",
               new=AsyncMock()), \
         patch("twicc.providers.claude_code.ws.update_session_permission_mode",
               new=AsyncMock()) as persist:
        asyncio.run(handler._handle_pending_request_response(content))

    manager.resolve_pending_request.assert_awaited_once()
    return manager.resolve_pending_request.await_args.args[2], persist


@pytest.mark.parametrize("updated_permissions", [
    42, "setMode", {"type": "setMode"}, ["setMode"], [42, None],
])
def test_a_malformed_permission_list_does_not_break_the_connection(pending, updated_permissions):
    response, persist = _approve(pending, updated_permissions)

    assert isinstance(response, PermissionResultAllow)
    assert response.updated_permissions is None
    persist.assert_not_awaited()


def test_a_well_formed_permission_list_still_persists_its_mode(pending):
    # The other direction: filtering must not swallow a real suggestion.
    response, persist = _approve(pending, [{"type": "setMode", "mode": "acceptEdits"}])

    assert response.updated_permissions is not None
    persist.assert_awaited_once_with("s-1", "acceptEdits")


def test_a_mixed_list_keeps_the_entries_that_are_readable(pending):
    response, persist = _approve(
        pending, ["garbage", {"type": "setMode", "mode": "acceptEdits"}])

    assert len(response.updated_permissions) == 1
    persist.assert_awaited_once_with("s-1", "acceptEdits")
