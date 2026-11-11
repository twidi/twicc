"""Runtime liveness tracking for Claude Code ``Workflow`` runs.

The ``Workflow`` tool launches its script in the background and ends the
parent's turn while the run keeps working. The CLI streams the run as a
``local_workflow`` task: ``task_started`` at launch, ``task_progress`` while
its agents work, then ``task_updated`` (terminal status) and
``task_notification``. A live run holds ASSISTANT_TURN, like a background
subagent.
"""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import SystemMessage

from twicc.agent.completion import CompletionController
from twicc.core.ui_messages import ui_message
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent


def _system(subtype: str, task_id: str, **data) -> SystemMessage:
    return SystemMessage(
        subtype=subtype,
        data={"type": "system", "subtype": subtype, "task_id": task_id, **data},
    )


def _workflow_started(task_id: str = "w4oyzc1pu") -> SystemMessage:
    """The ``task_started`` the CLI emits when a ``Workflow`` run launches."""
    return _system(
        "task_started", task_id,
        tool_use_id="toolu_1",
        description="Apply the glossary, then review each language",
        task_type="local_workflow",
        workflow_name="normalize-i18n-glossary",
        prompt="export const meta = {name: 'normalize-i18n-glossary'}",
    )


def _workflow_progress(task_id: str = "w4oyzc1pu") -> SystemMessage:
    return _system(
        "task_progress", task_id,
        tool_use_id="toolu_1",
        description="Normalize: normalize:fr",
        last_tool_name="normalize:fr",
    )


def _agent() -> ClaudeCodeAgent:
    """Build the small in-memory slice used by task tracking."""
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "session-1"
    agent._live_monitor_tasks = set()
    agent._live_workflow_tasks = {}
    agent._ended_workflow_tasks = set()
    agent._live_background_tasks = {}
    agent._live_shell_tasks = {}
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._broadcast_process_label = AsyncMock()
    agent._pending_requests = {}
    # A closed turn: forgetting a shell task has no completion delivery to note.
    agent.completion_controller = CompletionController(run_id="run-1")
    agent._init_shell_notice_state()
    agent._init_claude_shell_notice_state()
    return agent


def test_a_started_workflow_holds_the_turn_and_is_reported() -> None:
    agent = _agent()

    asyncio.run(agent._update_live_tasks(_workflow_started()))

    assert agent._live_workflow_tasks == {"w4oyzc1pu": "normalize-i18n-glossary"}
    assert "workflow:w4oyzc1pu" in agent._completion_assistant_holds()
    assert agent.current_background_work()["workflows"] == 1
    # A workflow is neither a subagent nor a shell.
    assert agent._live_background_tasks == {}
    assert agent._live_shell_tasks == {}


def test_progress_and_non_terminal_updates_keep_it_live() -> None:
    agent = _agent()
    asyncio.run(agent._update_live_tasks(_workflow_started()))

    asyncio.run(agent._update_live_tasks(_workflow_progress()))
    asyncio.run(agent._update_live_tasks(_system("task_updated", "w4oyzc1pu", patch={"status": "running"})))

    assert set(agent._live_workflow_tasks) == {"w4oyzc1pu"}


def test_the_terminal_update_releases_it() -> None:
    agent = _agent()
    asyncio.run(agent._update_live_tasks(_workflow_started()))

    asyncio.run(agent._update_live_tasks(
        _system("task_updated", "w4oyzc1pu", patch={"status": "completed", "end_time": 1}),
    ))

    assert agent._live_workflow_tasks == {}
    assert agent._completion_assistant_holds() == frozenset()
    assert agent.current_background_work() is None
    assert agent.ended_workflow_task_ids() == frozenset({"w4oyzc1pu"})


def test_a_killed_run_is_released_by_its_notification() -> None:
    agent = _agent()
    asyncio.run(agent._update_live_tasks(_workflow_started()))

    asyncio.run(agent._update_live_tasks(_system("task_notification", "w4oyzc1pu", status="killed")))

    assert agent._live_workflow_tasks == {}


def test_the_release_refreshes_an_active_waiting_label() -> None:
    agent = _agent()
    asyncio.run(agent._update_live_tasks(_workflow_started("w1")))
    asyncio.run(agent._update_live_tasks(_workflow_started("w2")))

    asyncio.run(agent._refresh_waiting_label())
    agent._broadcast_process_label.assert_awaited_with(
        "waiting for 2 workflows",
        ui_message("backend.claude.waiting_workflows", {"count": 2}, count=2),
    )

    asyncio.run(agent._update_live_tasks(_system("task_notification", "w2", status="completed")))
    agent._broadcast_process_label.assert_awaited_with(
        "waiting for workflow",
        ui_message("backend.claude.waiting_workflows", {"count": 1}, count=1),
    )


def test_the_name_falls_back_to_the_description() -> None:
    agent = _agent()

    asyncio.run(agent._update_live_tasks(
        _system("task_started", "w1", task_type="local_workflow", description="Dynamic workflow"),
    ))

    assert agent._live_workflow_tasks == {"w1": "Dynamic workflow"}
