"""Runtime liveness tracking for Claude Code ``Monitor`` tools."""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import SystemMessage, UserMessage

from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent


def _monitor_started(task_id: str = "monitor-1") -> UserMessage:
    """The tool_result the CLI returns when a ``Monitor`` is armed."""
    return UserMessage(
        content=f"Monitor started (task {task_id}, timeout 600000ms).",
        tool_use_result={"taskId": task_id, "timeoutMs": 600000, "persistent": False},
    )


def _task_notification(task_id: str = "monitor-1", status: str = "completed") -> SystemMessage:
    """The system event the CLI emits when a task stops, whatever its type."""
    return SystemMessage(
        subtype="task_notification",
        data={
            "type": "system",
            "subtype": "task_notification",
            "task_id": task_id,
            "status": status,
            "summary": f'Monitor "wait for the suite" {status}',
        },
    )


def _agent() -> ClaudeCodeAgent:
    """Build the small in-memory slice used by monitor tracking."""
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "session-1"
    agent._live_monitor_tasks = set()
    agent._live_background_tasks = {}
    agent._live_shell_tasks = {}
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._broadcast_process_label = AsyncMock()
    return agent


def test_monitor_start_holds_liveness_until_terminal_notification() -> None:
    agent = _agent()

    asyncio.run(agent._update_live_monitor_tasks(_monitor_started()))
    assert agent._live_monitor_tasks == {"monitor-1"}

    # A Monitor is a local_bash task: it is never tracked as a background
    # agent, yet its terminal system event must still release it.
    asyncio.run(agent._update_live_tasks(_task_notification()))
    assert agent._live_monitor_tasks == set()
    assert agent._live_background_tasks == {}


def test_monitor_survives_a_non_terminal_task_update() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._update_live_tasks(SystemMessage(
        subtype="task_updated",
        data={"task_id": "monitor-1", "patch": {"status": "running"}},
    )))

    assert agent._live_monitor_tasks == {"monitor-1"}


def test_monitor_stopped_by_task_stop_releases_via_the_system_event_too() -> None:
    """``TaskStop`` reports ``status="stopped"`` — also terminal."""
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._update_live_tasks(_task_notification(status="stopped")))

    assert agent._live_monitor_tasks == set()


def test_terminal_event_for_an_unknown_task_is_harmless() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._update_live_tasks(_task_notification(task_id="monitor-9")))

    assert agent._live_monitor_tasks == {"monitor-1"}


def test_task_stop_result_ends_monitor_from_its_json_text_payload() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._update_live_monitor_tasks(UserMessage(
        content=(
            '{"message":"Successfully stopped task: monitor-1",'
            '"task_id":"monitor-1","task_type":"local_bash"}'
        ),
    )))

    assert agent._live_monitor_tasks == set()


def test_monitor_label_is_used_while_no_higher_priority_wait_exists() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")

    asyncio.run(agent._refresh_waiting_label())

    agent._broadcast_process_label.assert_awaited_once_with("monitoring")
    assert agent._waiting_label_active is True


def test_background_subagent_label_takes_priority_over_monitoring() -> None:
    agent = _agent()
    agent._live_monitor_tasks.add("monitor-1")
    agent._live_background_tasks["agent-1"] = "review"

    asyncio.run(agent._refresh_waiting_label())

    agent._broadcast_process_label.assert_awaited_once_with("waiting for 1 subagent")
