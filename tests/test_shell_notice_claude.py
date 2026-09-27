"""Claude Code bookkeeping for the background shell notice (spec §3.1, §3.2, §5.2)."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, UserMessage
from claude_agent_sdk.types import StreamEvent, ToolResultBlock, ToolUseBlock

from tests.shell_notice_helpers import run_async
from twicc.agent.base_agent import BaseAgent
from twicc.agent.shell_notice import OwnerFacts, ShellOwner
from twicc.agent.states import AgentState
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent


def make_agent():
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent.session_id = "root-1"
    agent.state = AgentState.USER_TURN
    agent._live_background_tasks = {}
    agent._live_monitor_tasks = set()
    agent._live_shell_tasks = {}
    agent._listed_background_tasks = set()
    agent._wakeup_refresh_handle = None
    agent._pending_wakeup_at = None
    agent._waiting_label_active = False
    agent._pending_requests = {}
    agent._broadcast_process_label = AsyncMock()
    agent._background_work_refresh_task = None
    agent._background_work_dirty = False
    agent._published_background_work = None
    agent._background_work_callback = None
    agent._background_work_broadcast_failures = 0
    agent._dead_event = asyncio.Event()
    agent._init_shell_notice_state()
    agent._init_claude_shell_notice_state()
    return agent


def system(subtype, **data):
    return SystemMessage(subtype=subtype, data={"subtype": subtype, **data})


def bash_use(tool_use_id, command, parent=None):
    return AssistantMessage(content=[ToolUseBlock(id=tool_use_id, name="Bash", input={"command": command})],
                            model="m", parent_tool_use_id=parent)


async def feed(agent, *messages):
    for msg in messages:
        agent._note_shell_notice_stream(msg)
        if isinstance(msg, SystemMessage):
            await agent._update_live_tasks(msg)


@run_async
async def test_main_agent_shell():
    agent = make_agent()
    await feed(agent,
               bash_use("toolu_b", "sleep 999"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      description="Sleep", is_backgrounded=True))
    [shell] = agent.shell_notice_state().shells
    assert (shell.owner, shell.command, shell.description, shell.tool_use_id) == (
        ShellOwner.MAIN, "sleep 999", "Sleep", "toolu_b",
    )


@run_async
async def test_first_level_subagent_shell_at_spawn_and_after_resume():
    agent = make_agent()
    await feed(agent,
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="toolu_agent",
                      description="Implement"),
               bash_use("toolu_b", "sleep 1", parent="toolu_agent"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      is_backgrounded=True, owned_by_subagent=True),
               # SendMessage resume: new tool_use id, messages still carry the Agent id.
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="toolu_send",
                      description="Implement"),
               bash_use("toolu_b2", "sleep 2", parent="toolu_agent"),
               system("task_started", task_id="b2", task_type="local_bash", tool_use_id="toolu_b2",
                      is_backgrounded=True, owned_by_subagent=True))
    shells = {s.key: s for s in agent.shell_notice_state().shells}
    assert shells["b1"].owner is ShellOwner.SUBAGENT and shells["b1"].owner_ref == "a1"
    assert shells["b2"].owner is ShellOwner.SUBAGENT and shells["b2"].owner_ref == "a1"
    assert shells["b1"].owner_running is True
    assert shells["b1"].owner_label == "Implement"


@run_async
async def test_nested_subagent_shell_is_unresolved_then_placed():
    agent = make_agent()
    await feed(agent,
               # A nested resume has no tool_use_id key: skipped for the map, kept for labels.
               system("task_started", task_id="a79858f65df89eb0a", task_type="local_agent",
                      description="Prepare review copy"),
               system("task_started", task_id="b5lhayncg", task_type="local_bash",
                      tool_use_id="toolu_01Tojv846z1a6TkUh8k7H8jT", is_backgrounded=True,
                      owned_by_subagent=True))
    assert agent._agent_by_tool_use == {}
    [raw] = agent.shell_notice_state().shells
    assert raw.owner is ShellOwner.UNRESOLVED
    [lookup] = agent.shell_notice_lookups({"b5lhayncg"}, time.time())
    assert (lookup.tool_use_id, lookup.codex) == ("toolu_01Tojv846z1a6TkUh8k7H8jT", False)
    agent.store_shell_resolutions([OwnerFacts("b5lhayncg", "a79858f65df89eb0a", "Bash", "a663305d8fe755548",
                                              "title", None, None, None)], time.time())
    [placed] = agent.shell_notice_state().shells
    assert (placed.owner, placed.owner_ref, placed.owner_label, placed.owner_running) == (
        ShellOwner.SUBAGENT, "a79858f65df89eb0a", "Prepare review copy", True,
    )
    assert agent.shell_notice_lookups({"b5lhayncg"}, time.time()) == []


@run_async
async def test_foreground_shells_are_left_out_until_backgrounded():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=False))
    assert agent.shell_notice_state().shells == []
    await feed(agent, system("task_updated", task_id="b1", patch={"is_backgrounded": True}))
    assert [s.key for s in agent.shell_notice_state().shells] == ["b1"]


@run_async
async def test_output_path_from_the_tool_result():
    agent = make_agent()
    await feed(agent,
               bash_use("toolu_b", "sleep 1"),
               system("task_started", task_id="b1", task_type="local_bash", tool_use_id="toolu_b",
                      is_backgrounded=True))
    agent._note_shell_notice_stream(UserMessage(content=[ToolResultBlock(
        tool_use_id="toolu_b",
        content="Command running in background with ID: b1. Output is being written to: /tmp/x/b1.output. You will",
    )]))
    assert agent.shell_notice_state().shells[0].output_path == "/tmp/x/b1.output"


def test_malformed_tool_result_text_part_does_not_raise():
    agent = make_agent()
    agent._note_shell_notice_stream(UserMessage(content=[ToolResultBlock(
        tool_use_id="toolu_b", content=[{"type": "text", "text": None}, {"type": "text", "text": 3}],
    )]))
    assert agent.shell_notice_state().shells == []


def test_bash_tool_use_with_a_non_dict_input_does_not_raise():
    agent = make_agent()
    agent._note_shell_notice_stream(AssistantMessage(
        content=[ToolUseBlock(id="toolu_b", name="Bash", input="sleep 1")], model="m", parent_tool_use_id=None,
    ))
    assert agent._tool_use_parents["toolu_b"] == (None, None)


@run_async
async def test_every_removal_site_drops_the_notice_state():
    agent = make_agent()
    for task_id in ("b1", "b2", "b3"):
        await feed(agent, system("task_started", task_id=task_id, task_type="local_bash",
                                 tool_use_id=f"t{task_id}", is_backgrounded=True))
    agent.mark_shells_noticed(["b1", "b2", "b3"])
    await feed(agent, system("task_notification", task_id="b1"))
    await feed(agent, system("background_tasks_changed", tasks=[{"task_id": "b2"}, {"task_id": "b3"}]))
    await feed(agent, system("background_tasks_changed", tasks=[{"task_id": "b3"}]))
    assert agent._shell_notice_notified == {"b3"}
    # Third site: a Monitor start removes its local_bash record.
    await agent._update_live_monitor_tasks(UserMessage(
        content=[ToolResultBlock(tool_use_id="tb3", content="Monitor started (task b3, timeout 600s)")],
        tool_use_result={"taskId": "b3"},
    ))
    assert "b3" not in agent._live_shell_tasks
    assert agent._shell_notice_notified == set()


@run_async
async def test_mark_shells_noticed_keeps_only_live_shells():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1", "gone"])
    assert agent._shell_notice_notified == {"b1"}


@run_async
async def test_subagent_run_end_is_recorded_at_both_sites():
    agent = make_agent()
    agent._client = AsyncMock()
    await feed(agent,
               system("task_started", task_id="a1", task_type="local_agent", tool_use_id="x", description="d"),
               system("task_started", task_id="a2", task_type="local_agent", tool_use_id="y", description="d"),
               system("task_notification", task_id="a1"))
    await agent.stop_subagent("a2")
    assert set(agent._subagent_run_ended_at) == {"a1", "a2"}
    assert agent.shell_notice_state().last_subagent_run_end == max(agent._subagent_run_ended_at.values())


@run_async
async def test_main_turn_tracking_and_idle_predicate():
    agent = make_agent()
    assert agent.shell_notice_state().idle is True
    agent._shell_notice_idle_since = 1.0
    await feed(agent, system("init"))
    assert agent._main_turn_open is True and agent._shell_notice_idle_since is None
    assert agent.shell_notice_state().idle is False
    agent._note_shell_notice_stream(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                                  is_error=False, num_turns=1, session_id="root-1"))
    assert agent.shell_notice_state().idle is True
    # A subagent's message does not open a main turn.
    agent._note_shell_notice_stream(bash_use("t", "x", parent="toolu_agent"))
    assert agent._main_turn_open is False
    agent._note_shell_notice_stream(StreamEvent(uuid="u", session_id="root-1", event={}))
    assert agent._main_turn_open is True


@run_async
async def test_monitor_wakeup_and_pending_request_are_not_idle():
    agent = make_agent()
    agent._live_monitor_tasks = {"m1"}
    assert agent.shell_notice_state().idle is False
    agent._live_monitor_tasks = set()
    agent._pending_wakeup_at = time.time() + 600
    assert agent.shell_notice_state().idle is False
    agent._pending_wakeup_at = None
    agent._pending_requests = {"r": SimpleNamespace(created_at=0.0)}
    assert agent.shell_notice_state().idle is False
    agent._pending_requests = {}
    agent.state = AgentState.STARTING
    assert agent.shell_notice_state().idle is False


def test_dead_transition_closes_the_main_turn():
    agent = make_agent()
    agent._main_turn_open = True
    agent._set_state(AgentState.DEAD)
    assert agent._main_turn_open is False


def test_hybrid_agents_keep_the_default_hooks():
    assert HybridClaudeAgent.shell_notice_state is BaseAgent.shell_notice_state
    assert HybridClaudeAgent.shell_notice_lookups is BaseAgent.shell_notice_lookups


@run_async
async def test_external_send_restarts_the_delay():
    agent = make_agent()
    agent._client = AsyncMock()
    agent._build_query_prompt = AsyncMock(return_value="prompt")
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()
    agent._shell_notice_idle_since = 1.0
    await agent.send("ok")
    agent._note_shell_notice_stream(ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                                  is_error=False, num_turns=1, session_id="root-1"))
    # The short turn closed and reset the delay: the next tick starts it again from now (Task 5).
    assert agent._main_turn_open is False
    assert agent._shell_notice_idle_since is None


@run_async
async def test_external_send_clears_the_set_and_the_notice_send_does_not():
    agent = make_agent()
    agent._client = AsyncMock()
    agent._build_query_prompt = AsyncMock(return_value="prompt")
    agent._notify_state_change = AsyncMock()
    agent._clear_waiting_label = AsyncMock()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1"])
    assert await agent.send("notice", shell_notice=True) is True
    assert agent._shell_notice_notified == {"b1"}
    agent.state = AgentState.USER_TURN
    assert await agent.send("hello") is True
    assert agent._shell_notice_notified == set()


@run_async
async def test_cli_auto_turn_does_not_clear_the_set():
    agent = make_agent()
    await feed(agent, system("task_started", task_id="b1", task_type="local_bash", tool_use_id="t",
                             is_backgrounded=True))
    agent.mark_shells_noticed(["b1"])
    await feed(agent, system("init"))
    agent._note_shell_notice_stream(StreamEvent(uuid="u", session_id="root-1", event={}))
    assert agent._shell_notice_notified == {"b1"}


@run_async
async def test_resolution_is_not_stored_for_a_shell_that_ended():
    agent = make_agent()
    agent.store_shell_resolutions([OwnerFacts("gone", "a1", "Bash", None, None, None, None, None)], time.time())
    assert agent._shell_notice_resolutions == {}


def test_dead_or_ephemeral_agent_has_no_state():
    agent = make_agent()
    agent.state = AgentState.DEAD
    assert agent.shell_notice_state() is None
    agent.state = AgentState.USER_TURN
    agent.ephemeral = True
    assert agent.shell_notice_state() is None
