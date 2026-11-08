"""The status-line override must survive a reconnect — and never go stale.

``WorkingAssistantMessage`` shows "waiting for 2 subagents" (or "monitoring",
"compacting", …) instead of a bare "thinking" while a turn is held open by
something the agent waits on. That text used to travel only in the one-shot
``process_label`` message, so any client arriving afterwards — a reconnect, a
second tab, a plain page refresh — saw a session working on nothing with no
explanation.

It now rides along in every agent snapshot, recomputed on each read rather
than stored: a run that started with two background agents and has one left
must announce one, not two.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from twicc.agent.states import AgentInfo, AgentState, serialize_agent_info
from twicc.core.enums import Provider
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.codex.agent.agent import CodexAgent


def _claude_agent() -> ClaudeCodeAgent:
    """The in-memory slice the waiting label is computed from."""
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "session-1"
    agent._live_background_tasks = {}
    agent._live_monitor_tasks = set()
    agent._live_workflow_tasks = {}
    agent._ended_workflow_tasks = set()
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._broadcast_process_label = AsyncMock()
    return agent


def _codex_agent() -> CodexAgent:
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "session-2"
    agent._live_subagents = {}
    agent._subagent_wait_label_active = False
    agent._subagent_hold_active = False
    agent._manual_compaction = False
    return agent


class TestClaudeStatusLabel:
    def test_nothing_while_the_agent_works(self):
        """Live activity is the truth — the hold reason must not override it."""
        agent = _claude_agent()
        agent._live_background_tasks = {"task-1": "Extract findings"}

        assert agent.current_status_label() is None

    def test_the_count_is_recomputed_not_remembered(self):
        """Two agents at the hold, one left later → the snapshot says one."""
        agent = _claude_agent()
        agent._live_background_tasks = {"task-1": "Inventory", "task-2": "Dispositions"}
        agent._waiting_label_active = True
        assert agent.current_status_label() == "waiting for 2 subagents"

        del agent._live_background_tasks["task-2"]

        assert agent.current_status_label() == "waiting for 1 subagent"

    def test_hold_reasons_have_a_priority(self):
        agent = _claude_agent()
        agent._waiting_label_active = True
        agent._pending_wakeup_at = time.time() + 300
        assert agent.current_status_label().startswith("waiting for scheduled wakeup")

        agent._live_monitor_tasks = {"monitor-1"}
        assert agent.current_status_label() == "monitoring"

        agent._live_workflow_tasks = {"w1": "review"}
        assert agent.current_status_label() == "waiting for workflow"

        agent._live_workflow_tasks = {"w1": "review", "w2": "spec"}
        assert agent.current_status_label() == "waiting for 2 workflows"

        agent._live_background_tasks = {"task-1": "Inventory"}
        assert agent.current_status_label() == "waiting for 1 subagent"

    def test_nothing_left_to_wait_on(self):
        agent = _claude_agent()
        agent._waiting_label_active = True

        assert agent.current_status_label() is None


class TestCodexStatusLabel:
    def test_only_while_blocked_on_wait_agent(self):
        agent = _codex_agent()
        agent._live_subagents = {"thread-1": "/root/impl"}
        assert agent.current_status_label() is None

        agent._subagent_wait_label_active = True
        assert agent.current_status_label() == "waiting for 1 subagent"

    def test_the_subagent_hold_labels_without_a_wait(self):
        """A turn ended held on live children labels like an in-turn wait."""
        agent = _codex_agent()
        agent._live_subagents = {"thread-1": "/root/impl", "thread-2": "/root/qa"}
        agent._subagent_hold_active = True

        assert agent.current_status_label() == "waiting for 2 subagents"

    def test_a_wait_with_nothing_live_still_says_waiting(self):
        """Zero children drops the count, not the line (Codex holds the turn)."""
        agent = _codex_agent()
        agent._subagent_wait_label_active = True

        assert agent.current_status_label() == "waiting"

    def test_a_manual_compaction_owns_the_line(self):
        agent = _codex_agent()
        agent._manual_compaction = True

        assert agent.current_status_label() == "compacting"


class TestSnapshotSerialization:
    def _info(self, **overrides) -> AgentInfo:
        return AgentInfo(
            session_id="s",
            project_id="p",
            provider=Provider.CLAUDE_CODE,
            state=AgentState.ASSISTANT_TURN,
            previous_state=None,
            started_at=0.0,
            state_changed_at=0.0,
            last_activity=0.0,
            **overrides,
        )

    def test_the_label_travels_with_the_snapshot(self):
        payload = serialize_agent_info(self._info(label="waiting for 2 subagents"))

        assert payload["label"] == "waiting for 2 subagents"

    def test_no_key_when_there_is_no_label(self):
        assert "label" not in serialize_agent_info(self._info())


class TestDefaultAgentHasNoLabel:
    def test_providers_without_holdable_turns(self):
        """The base class answers None, so nothing to override for them."""
        agent = CodexAgent.__new__(CodexAgent)
        agent._live_subagents = {}
        agent._subagent_wait_label_active = False
        agent._subagent_hold_active = False
        agent._manual_compaction = False

        assert agent.current_status_label() is None
