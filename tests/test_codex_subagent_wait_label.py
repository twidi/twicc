"""Codex multi-agent v2: the "waiting for N subagents" status label.

While the parent blocks *inside* the turn on ``wait_agent``, no
``turn/completed`` fires and the session stays busy on its own. What it
lacks is the *reason*: Codex streams no tool activity, so the frontend
would show a bare "thinking" for the whole wait (18 minutes in one real
5-subagent session). (A turn that ends WITHOUT waiting on its live
children is the other shape — covered by the subagent hold, see
``test_codex_subagent_hold.py``.)

The label is driven by two thread items Codex routes on the parent's
stream: ``subAgentActivity`` (which children are alive) and
``collabAgentToolCall`` with ``tool == "wait"`` (the parent is blocked).
Not every run end reaches that stream as a ``completed`` item, so the live
set is pruned against the run model (``agent_run_states``). With nothing
live the label keeps the line and drops the count — a bare "waiting".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twicc.core.enums import Provider
from twicc.core.models import AgentLink, Project, Session, ToolResultLink
from twicc.providers.codex.agent.agent import CodexAgent, _is_collab_wait_call


def _activity(agent_thread_id: str, kind: str, agent_path: str = "/root/task") -> SimpleNamespace:
    """A ``SubAgentActivityThreadItem`` as the SDK hands it over."""
    return SimpleNamespace(
        type="subAgentActivity",
        id=f"call_{agent_thread_id}",
        agent_thread_id=agent_thread_id,
        agent_path=agent_path,
        kind=SimpleNamespace(value=kind),  # SDK enum
    )


def _collab_call(tool: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="collabAgentToolCall",
        id="call_collab",
        tool=SimpleNamespace(value=tool),  # SDK enum
        status=SimpleNamespace(value="inProgress"),
    )


def _agent(stopped: list[str] | None = None) -> CodexAgent:
    """The in-memory slice used by the wait label, with a stubbed watcher view."""
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "session-parent"
    agent._live_subagents = {}
    agent._subagent_set_lock = asyncio.Lock()
    agent._subagent_wait_label_active = False
    agent._subagent_hold_active = False
    # A manual /compact owns the status line while it runs, so the label
    # composition consults it (see ``current_status_label``).
    agent._manual_compaction = False
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._broadcast_process_label = AsyncMock()
    agent._prune_finished_subagents = AsyncMock(
        side_effect=lambda: [agent._live_subagents.pop(sid, None) for sid in (stopped or [])]
    )
    return agent


def _labels(agent: CodexAgent) -> list[str]:
    return [call.args[0] for call in agent._broadcast_process_label.await_args_list]


class TestCollabWaitDetection:
    def test_only_the_wait_tool_counts(self) -> None:
        assert _is_collab_wait_call(_collab_call("wait")) is True
        assert _is_collab_wait_call(_collab_call("spawnAgent")) is False
        assert _is_collab_wait_call(_collab_call("closeAgent")) is False
        assert _is_collab_wait_call(_activity("a", "started")) is False


class TestLiveSubagentTracking:
    def test_started_adds_interrupted_removes_interacted_is_neutral(self) -> None:
        agent = _agent()

        agent._note_sub_agent_activity(_activity("agent-1", "started", "/root/impl"))
        assert agent._live_subagents == {"agent-1": "/root/impl"}

        # A message to the agent must not change the set…
        agent._note_sub_agent_activity(_activity("agent-1", "interacted", "/root/impl"))
        assert agent._live_subagents == {"agent-1": "/root/impl"}

        # …and the SDK repeats the same item on item/completed.
        agent._note_sub_agent_activity(_activity("agent-1", "started", "/root/impl"))
        assert agent._live_subagents == {"agent-1": "/root/impl"}

        agent._note_sub_agent_activity(_activity("agent-1", "interrupted", "/root/impl"))
        assert agent._live_subagents == {}


class TestWaitLabel:
    def test_label_counts_the_live_children(self) -> None:
        agent = _agent()
        agent._note_sub_agent_activity(_activity("agent-1", "started"))
        agent._note_sub_agent_activity(_activity("agent-2", "started"))

        asyncio.run(agent._refresh_subagent_wait_label())

        assert _labels(agent) == ["waiting for 2 subagents"]
        assert agent._subagent_wait_label_active is True

    def test_singular_wording(self) -> None:
        agent = _agent()
        agent._note_sub_agent_activity(_activity("agent-1", "started"))

        asyncio.run(agent._refresh_subagent_wait_label())

        assert _labels(agent) == ["waiting for 1 subagent"]

    def test_finished_children_do_not_accumulate(self) -> None:
        """The sequential pattern (spawn → wait → spawn → wait) must not drift.

        Real sessions spawn one agent per step; without pruning against the
        watcher, the fifth wait would claim "5 subagents".
        """
        agent = _agent(stopped=["agent-1"])
        agent._note_sub_agent_activity(_activity("agent-1", "started"))
        agent._note_sub_agent_activity(_activity("agent-2", "started"))

        asyncio.run(agent._refresh_subagent_wait_label())

        assert _labels(agent) == ["waiting for 1 subagent"]
        assert agent._live_subagents == {"agent-2": "/root/task"}

    def test_no_live_child_drops_the_count_not_the_label(self) -> None:
        """A wait with nothing left alive still says the turn is a wait."""
        agent = _agent(stopped=["agent-1"])
        agent._note_sub_agent_activity(_activity("agent-1", "started"))

        asyncio.run(agent._refresh_subagent_wait_label())

        assert _labels(agent) == ["waiting"]
        assert agent._subagent_wait_label_active is True

    def test_a_wait_with_no_child_at_all_says_waiting(self) -> None:
        """The model using ``wait_agent`` as a sleep — no spawn ever happened.

        TwiCC's own orchestration spawns sessions through MCP, never Codex
        collab agents, so this is the shape a mistaken wait takes: nothing
        can wake it before its ``timeout_ms``, and a bare "thinking" would
        hide a turn that is doing nothing for up to an hour.
        """
        agent = _agent()

        asyncio.run(agent._refresh_subagent_wait_label())

        assert _labels(agent) == ["waiting"]
        assert agent._subagent_wait_label_active is True

    def test_completion_clears_the_label_once(self) -> None:
        agent = _agent()
        agent._note_sub_agent_activity(_activity("agent-1", "started"))
        asyncio.run(agent._refresh_subagent_wait_label())

        asyncio.run(agent._clear_subagent_wait_label())
        asyncio.run(agent._clear_subagent_wait_label())  # idempotent

        assert _labels(agent) == ["waiting for 1 subagent", ""]
        assert agent._subagent_wait_label_active is False

    def test_clear_without_label_is_a_no_op(self) -> None:
        agent = _agent()

        asyncio.run(agent._clear_subagent_wait_label())

        assert _labels(agent) == []


@pytest.mark.django_db(transaction=True)
class TestPruningAgainstTheWatcher:
    """The real prune — against the run model, not the stream."""

    def test_only_stopped_children_are_dropped(self) -> None:
        project = Project.objects.create(id="test-project-wait-label")
        parent = Session.objects.create(
            id="session-parent-wait-label", project=project, provider=Provider.CODEX,
        )
        for index, suffix in enumerate(("done", "live")):
            child_id = f"child-{suffix}"
            Session.objects.create(
                id=child_id,
                project=project,
                provider=Provider.CODEX,
                type="subagent",
                parent_session=parent,
                file_path=f"2026/08/15/rollout-child-{suffix}.jsonl",
            )
            # A root-owned background spawn: its run closes on ack + FINAL_ANSWER.
            AgentLink.objects.create(
                session=parent, tool_use_line_num=index + 1, tool_use_id=f"call-{suffix}",
                agent_id=child_id, is_background=True, started_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
            ToolResultLink.objects.create(  # the ack
                session=parent, tool_use_line_num=index + 1, tool_result_line_num=10 + index,
                tool_use_id=f"call-{suffix}", tool_result_at=datetime(2025, 1, 1, 0, 0, 1, tzinfo=UTC),
            )
        ToolResultLink.objects.create(  # the done child's FINAL_ANSWER
            session=parent, tool_use_line_num=1, tool_result_line_num=20,
            tool_use_id="call-done", tool_result_at=datetime(2025, 1, 1, 0, 5, tzinfo=UTC),
        )

        agent = CodexAgent.__new__(CodexAgent)
        agent.session_id = parent.id
        agent._live_subagents = {"child-done": "/root/a", "child-live": "/root/b"}
        agent._subagent_set_lock = asyncio.Lock()
        agent._subagent_wait_label_active = False
        agent._subagent_hold_active = False
        agent._manual_compaction = False
        agent._background_work_refresh_task = None
        agent._background_work_dirty = False
        agent._published_background_work = None
        agent._background_work_callback = None
        agent._broadcast_process_label = AsyncMock()

        asyncio.run(agent._refresh_subagent_wait_label())

        assert agent._live_subagents == {"child-live": "/root/b"}
        assert _labels(agent) == ["waiting for 1 subagent"]
