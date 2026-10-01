"""Codex multi-agent v2: the ASSISTANT_TURN hold on live children.

When the model calls ``spawn_agent`` WITHOUT ``wait_agent`` in the same
turn, Codex ends the turn as soon as the parent stops talking — children
or not. Settling to USER_TURN there fires every "finished working"
consumer (green check, browser notification, idle auto-stop) while the
subagent still runs. The hold keeps ASSISTANT_TURN at that idle boundary
instead, mirroring Claude Code's background-agents hold, and releases on
the watcher's end-of-child signal: the parent's SDK stream carries a
``completed`` item for most run ends, not for every one, so the watcher's
stop relay (re-checked against the run model) is the reliable source.

The in-turn ``wait_agent`` label is the other shape, covered by
``test_codex_subagent_wait_label.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.agent.states import AgentState
from twicc.providers.codex.agent import agent as agent_module
from twicc.providers.codex.agent.agent import CodexAgent

# The run model's view for the relay tests: these children are known and no
# longer running; any other id (e.g. a child of another run) is not known.
STOPPED_IN_RUN_MODEL = {"agent-1", "agent-2"}


def _activity(agent_thread_id: str, kind: str, agent_path: str = "/root/task") -> SimpleNamespace:
    """A ``SubAgentActivityThreadItem`` as the SDK hands it over."""
    return SimpleNamespace(
        type="subAgentActivity",
        id=f"call_{agent_thread_id}",
        agent_thread_id=agent_thread_id,
        agent_path=agent_path,
        kind=SimpleNamespace(value=kind),  # SDK enum
    )


def _agent(
    *,
    state: AgentState = AgentState.ASSISTANT_TURN,
    stopped: list[str] | None = None,
) -> CodexAgent:
    """The in-memory slice used by the hold, with a stubbed watcher view."""
    agent = CodexAgent.__new__(CodexAgent)
    agent.session_id = "session-parent"
    agent.state = state
    agent.previous_state = None
    agent.state_changed_at = 0.0
    agent.last_activity = 0.0
    agent._live_subagents = {}
    agent._subagent_set_lock = asyncio.Lock()
    agent._subagent_wait_label_active = False
    agent._subagent_hold_active = False
    agent._manual_compaction = False
    agent._goal_continuation_active = False
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._current_turn = None
    agent._pending_requests = {}
    agent._init_shell_notice_state()
    agent._init_codex_shell_notice_state()
    agent._broadcast_process_label = AsyncMock()
    agent._notify_state_change = AsyncMock()
    agent._set_state = MagicMock(
        side_effect=lambda new_state: setattr(agent, "state", new_state),
    )
    agent._prune_finished_subagents = AsyncMock(
        side_effect=lambda: [agent._live_subagents.pop(sid, None) for sid in (stopped or [])]
    )
    return agent


def _labels(agent: CodexAgent) -> list[str]:
    return [call.args[0] for call in agent._broadcast_process_label.await_args_list]


class TestArmingTheHold:
    def test_live_children_arm_the_hold(self) -> None:
        agent = _agent()
        agent._note_sub_agent_activity(_activity("agent-1", "started", "/root/impl"))

        armed = asyncio.run(agent._try_arm_subagent_hold())

        assert armed is True
        assert agent._subagent_hold_active is True
        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_awaited_once()
        # process_state first (it wipes any label on the frontend), then
        # the label on top.
        assert _labels(agent) == ["waiting for 1 subagent"]

    def test_nothing_live_declines_and_drops_the_flag(self) -> None:
        """A stale flag must not survive a decision that found nothing running."""
        agent = _agent(stopped=["agent-1"])
        agent._subagent_hold_active = True
        agent._note_sub_agent_activity(_activity("agent-1", "started"))

        armed = asyncio.run(agent._try_arm_subagent_hold())

        assert armed is False
        assert agent._subagent_hold_active is False
        agent._notify_state_change.assert_not_awaited()
        assert _labels(agent) == []

    def test_already_finished_children_never_hold(self) -> None:
        """The prune runs before the decision — the watcher's view wins."""
        agent = _agent(stopped=["agent-1"])
        agent._note_sub_agent_activity(_activity("agent-1", "started"))
        agent._note_sub_agent_activity(_activity("agent-2", "started"))

        armed = asyncio.run(agent._try_arm_subagent_hold())

        assert armed is True
        assert _labels(agent) == ["waiting for 1 subagent"]

    def test_a_completed_stream_item_removes_the_child(self) -> None:
        """The stream's ``completed`` item (most run ends carry one) removes the child."""
        agent = _agent()
        agent._note_sub_agent_activity(_activity("agent-1", "started"))
        agent._note_sub_agent_activity(_activity("agent-1", "completed"))

        assert agent._live_subagents == {}


class TestReleasingTheHold:
    @pytest.fixture(autouse=True)
    def _run_model(self, monkeypatch) -> None:
        """Stub the run-model read the stop relay makes for a watcher-backed agent."""
        monkeypatch.setattr(
            agent_module, "_stopped_subagent_ids",
            lambda root_id, ids: [agent_id for agent_id in ids if agent_id in STOPPED_IN_RUN_MODEL],
        )

    def _held_agent(self, children: dict[str, str]) -> CodexAgent:
        agent = _agent()
        agent._live_subagents = dict(children)
        agent._subagent_hold_active = True
        return agent

    def test_last_child_releases_to_user_turn(self) -> None:
        agent = self._held_agent({"agent-1": "/root/impl"})

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent._subagent_hold_active is False
        assert agent.state == AgentState.USER_TURN
        agent._notify_state_change.assert_awaited_once()

    def test_remaining_children_refresh_the_count(self) -> None:
        agent = self._held_agent({"agent-1": "/root/a", "agent-2": "/root/b"})

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent._subagent_hold_active is True
        assert agent.state == AgentState.ASSISTANT_TURN
        assert _labels(agent) == ["waiting for 1 subagent"]

    def test_unknown_ids_are_a_no_op(self) -> None:
        """A relay for children of another run must not touch the state."""
        agent = self._held_agent({"agent-1": "/root/a"})

        asyncio.run(agent.notify_subagents_stopped(["someone-else"]))

        assert agent._subagent_hold_active is True
        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_not_awaited()

    def test_without_a_hold_the_relay_only_prunes(self) -> None:
        """USER_TURN with a live set entry (e.g. relay raced the turn end)."""
        agent = _agent(state=AgentState.USER_TURN)
        agent._live_subagents = {"agent-1": "/root/a"}

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent._live_subagents == {}
        assert agent.state == AgentState.USER_TURN
        agent._notify_state_change.assert_not_awaited()

    def test_a_running_turn_owns_the_state(self) -> None:
        """Mid-``wait_agent``: refresh the in-turn label count, nothing else."""
        agent = _agent()
        agent._live_subagents = {"agent-1": "/root/a", "agent-2": "/root/b"}
        agent._subagent_wait_label_active = True
        agent._current_turn = object()

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_not_awaited()
        assert _labels(agent) == ["waiting for 1 subagent"]

    def test_a_manual_compaction_owns_the_settle(self) -> None:
        """The /compact conclusion re-runs the hold decision, not this relay."""
        agent = self._held_agent({"agent-1": "/root/a"})
        agent._manual_compaction = True

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_not_awaited()

    def test_a_goal_continuation_owns_the_settle(self) -> None:
        agent = self._held_agent({"agent-1": "/root/a"})
        agent._goal_continuation_active = True

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent.state == AgentState.ASSISTANT_TURN
        agent._notify_state_change.assert_not_awaited()

    def test_dead_agent_is_left_alone(self) -> None:
        agent = _agent(state=AgentState.DEAD)
        agent._live_subagents = {"agent-1": "/root/a"}
        agent._subagent_hold_active = True

        asyncio.run(agent.notify_subagents_stopped(["agent-1"]))

        assert agent.state == AgentState.DEAD
        agent._notify_state_change.assert_not_awaited()


class TestBreakingTheHoldWithAMessage:
    def test_a_send_during_the_hold_opens_a_real_turn(self) -> None:
        """No active turn to steer — the message breaks the hold instead."""
        agent = _agent()
        agent._live_subagents = {"agent-1": "/root/a"}
        agent._subagent_hold_active = True
        agent._schedule_turn = MagicMock()

        accepted = asyncio.run(agent.send("continue"))

        assert accepted is True
        assert agent._subagent_hold_active is False
        agent._schedule_turn.assert_called_once_with("continue", None)
        # The state doesn't change, so no process_state broadcast would
        # drop the label — send clears it explicitly.
        assert _labels(agent) == [""]
