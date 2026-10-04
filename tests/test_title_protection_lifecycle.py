"""Claude title protection follows the manager's current live entry."""

import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc.agent import AgentState, BaseAgentManager
from twicc.providers.claude_code import helpers, titles
from twicc.providers.claude_code.agent import manager as manager_module


@pytest.fixture
def rename_context(monkeypatch):
    monkeypatch.setattr(titles, "_protected_titles", {})
    manager = SimpleNamespace(_agents={}, rename_hybrid_if_live=AsyncMock(return_value=False))
    monkeypatch.setattr(manager_module, "get_claude_code_agent_manager", lambda: manager)
    writes = []

    def write(session_id, title):
        writes.append((session_id, title))

    monkeypatch.setattr(helpers, "rename_session_in_jsonl", write)
    return manager, writes


@pytest.mark.parametrize("state", [AgentState.STARTING, AgentState.ASSISTANT_TURN, AgentState.USER_TURN,
                                   AgentState.DEAD, None])
@pytest.mark.parametrize("write_fails", [False, True])
def test_only_current_live_entries_keep_protection(rename_context, monkeypatch, state, write_fails):
    manager, writes = rename_context
    if state is not None:
        manager._agents["s"] = SimpleNamespace(state=state)
    titles.protect_title("s", "Old")
    if write_fails:
        def fail(session_id, title):
            raise OSError("provider write failed")
        monkeypatch.setattr(helpers, "rename_session_in_jsonl", fail)
        with pytest.raises(OSError, match="provider write failed"):
            asyncio.run(helpers.ClaudeCodeHelpers().rename_session("s", "New"))
    else:
        asyncio.run(helpers.ClaudeCodeHelpers().rename_session("s", "New"))
        assert writes == [("s", "New")]
    expected = "New" if state not in (None, AgentState.DEAD) else None
    assert titles.get_protected_title("s") == expected
    if expected is None:
        assert titles.check_protected_title("s", "External").should_apply


@pytest.mark.parametrize("final_state", [AgentState.DEAD, None, AgentState.STARTING])
def test_protection_uses_entry_after_awaited_write(rename_context, monkeypatch, final_state):
    manager, _ = rename_context
    manager._agents["s"] = SimpleNamespace(state=AgentState.ASSISTANT_TURN)
    titles.protect_title("s", "Old")

    async def write_and_change_entry(function, session_id, title):
        function(session_id, title)
        if final_state is None:
            manager._agents.pop(session_id)
        else:
            manager._agents[session_id] = SimpleNamespace(state=final_state)

    monkeypatch.setattr(asyncio, "to_thread", write_and_change_entry)
    asyncio.run(helpers.ClaudeCodeHelpers().rename_session("s", "New"))
    assert titles.get_protected_title("s") == ("New" if final_state == AgentState.STARTING else None)


def test_live_hybrid_paste_keeps_protection_without_direct_append(rename_context):
    manager, writes = rename_context
    manager._agents["s"] = SimpleNamespace(state=AgentState.USER_TURN)
    manager.rename_hybrid_if_live.return_value = True
    asyncio.run(helpers.ClaudeCodeHelpers().rename_session("s", "New"))
    assert writes == []
    assert titles.get_protected_title("s") == "New"


def test_dead_rename_removes_old_protection_before_dead_handler_reads_it(rename_context, monkeypatch):
    _, writes = rename_context
    manager = manager_module.ClaudeCodeAgentManager()
    agent = SimpleNamespace(session_id="s", state=AgentState.DEAD, process_run=None,
                            get_info=lambda: SimpleNamespace(session_id="s", state=AgentState.DEAD))
    manager._agents["s"] = agent
    monkeypatch.setattr(manager_module, "get_claude_code_agent_manager", lambda: manager)
    monkeypatch.setattr(manager, "rename_hybrid_if_live", AsyncMock(return_value=False))
    monkeypatch.setattr(titles, "rename_session_in_jsonl", lambda session_id, title: writes.append((session_id, title)))
    titles.protect_title("s", "Old")

    async def scenario():
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def paused_base_state_change(self, current_agent):
            entered.set()
            await resume.wait()
            self._agents.pop(current_agent.session_id, None)

        monkeypatch.setattr(BaseAgentManager, "_on_state_change", paused_base_state_change)
        death = asyncio.create_task(manager._on_state_change(agent))
        await entered.wait()
        try:
            await helpers.ClaudeCodeHelpers().rename_session("s", "New")
        finally:
            resume.set()
            await death

    asyncio.run(scenario())
    assert writes == [("s", "New")]
    assert titles.check_protected_title("s", "External").should_apply


def test_explicit_provider_push_leaves_automatic_echo_record(rename_context, monkeypatch):
    echoes = import_module("twicc.title_echo")
    monkeypatch.setattr(echoes, "_automatic_title_echoes", {})
    echoes.record_automatic_title_push("s", "A")
    asyncio.run(helpers.ClaudeCodeHelpers().rename_session("s", "U"))
    assert echoes.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")
