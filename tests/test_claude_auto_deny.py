"""Claude arming rule for the auto-deny and the setMode mirror.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md`` (§2.4, §4.4, §4.5).
"""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
import pytest

from twicc.agent import base_agent as base_agent_module
from twicc.agent.auto_deny import AUTO_DENY_MESSAGE
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.helpers import AgentSettings

DANGEROUS = {"command": "rm -rf /tmp/auto-deny-test"}


def _make_agent(monkeypatch, mode="bypassPermissions", *, untrusted=False) -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="opus", permission_mode=mode),
        AsyncMock(return_value=None),
        AsyncMock(),
        AsyncMock(),
    )
    monkeypatch.setattr(agent, "_resolve_untrusted_now", AsyncMock(return_value=untrusted))
    monkeypatch.setattr(agent, "get_permission_suggestions", lambda *args, **kwargs: None)
    return agent


async def _ask(agent, tool_name, tool_input):
    """Start the can_use_tool callback and return it with its registered request."""
    task = asyncio.create_task(agent._handle_pending_request(tool_name, tool_input, ToolPermissionContext()))
    for _ in range(50):
        if agent.pending_requests:
            break
        await asyncio.sleep(0)
    (pending,) = agent.pending_requests
    return task, pending


@pytest.mark.parametrize("tool_name,tool_input", [
    ("Bash", DANGEROUS),
    ("Monitor", DANGEROUS),
    ("Write", {"file_path": "/etc/auto-deny-test", "content": "x"}),
    ("Edit", {"file_path": "/etc/auto-deny-test", "old_string": "a", "new_string": "b"}),
])
def test_an_action_tool_is_armed_in_bypass(monkeypatch, tool_name, tool_input):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, tool_name, tool_input)
        assert pending.auto_deny_at is not None
        agent.resolve_pending_request(pending.request_id, PermissionResultAllow())
        await task

    asyncio.run(run())


@pytest.mark.parametrize("mode,tool_name,tool_input,untrusted", [
    ("bypassPermissions", "AskUserQuestion", {"questions": []}, False),
    ("bypassPermissions", "ExitPlanMode", {"plan": "the plan"}, False),
    ("bypassPermissions", "mcp__other__tool", {}, False),
    ("bypassPermissions", "WebFetch", {"url": "https://example.com"}, False),
    ("bypassPermissions", "Bash", DANGEROUS, True),
    ("default", "Bash", DANGEROUS, False),
    ("acceptEdits", "Write", {"file_path": "/etc/auto-deny-test", "content": "x"}, False),
    ("plan", "Bash", DANGEROUS, False),
])
def test_everything_else_is_never_armed(monkeypatch, mode, tool_name, tool_input, untrusted):
    agent = _make_agent(monkeypatch, mode, untrusted=untrusted)

    async def run():
        task, pending = await _ask(agent, tool_name, tool_input)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


def test_an_unanswered_prompt_is_denied_with_the_message(monkeypatch):
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)
    agent = _make_agent(monkeypatch)

    # wait_for: without the arming, nothing ever answers and the test must
    # fail on a timeout instead of hanging.
    result = asyncio.run(asyncio.wait_for(
        agent._handle_pending_request("Bash", DANGEROUS, ToolPermissionContext()), 2,
    ))

    assert isinstance(result, PermissionResultDeny)
    assert result.message == AUTO_DENY_MESSAGE
    assert result.interrupt is False
    assert agent.pending_requests == ()


def _allow_with_mode(mode, destination="session"):
    return PermissionResultAllow(
        updated_permissions=[PermissionUpdate(type="setMode", mode=mode, destination=destination)],
    )


@pytest.mark.parametrize("destination", ["session", None])
def test_an_allow_that_leaves_bypass_disarms_the_next_prompts(monkeypatch, destination):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("default", destination))
        await task
        assert agent.agent_settings.permission_mode == "default"

        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


def test_an_allow_that_enters_bypass_arms_the_next_prompts(monkeypatch):
    agent = _make_agent(monkeypatch, "default")

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("bypassPermissions"))
        await task
        assert agent.agent_settings.permission_mode == "bypassPermissions"

        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is not None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


@pytest.mark.parametrize("destination", ["userSettings", "projectSettings", "localSettings"])
def test_a_set_mode_for_another_destination_is_not_mirrored(monkeypatch, destination):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("default", destination))
        await task

    asyncio.run(run())
    assert agent.agent_settings.permission_mode == "bypassPermissions"
