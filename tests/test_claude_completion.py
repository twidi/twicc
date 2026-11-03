"""Claude root ingress, native finals, and logical completion boundaries."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock, UserMessage

from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.helpers import AgentSettings


def make_agent():
    agent = ClaudeCodeAgent("completion-claude", "project", "/tmp", AgentSettings(),
                            AsyncMock(), AsyncMock(), AsyncMock(), ephemeral=True)
    agent._broadcast_stream_event = AsyncMock()
    agent._broadcast_process_label = AsyncMock()
    agent._broadcast_process_tools = AsyncMock()
    return agent


def answer(identity="final", *, parent=None, message_id=None):
    return AssistantMessage(content=[TextBlock(text="answer")], model="claude-opus-5",
                            uuid=identity, message_id=message_id, stop_reason="end_turn",
                            parent_tool_use_id=parent)


def result(*, error=False):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                         is_error=error, num_turns=1, session_id="completion-claude")


async def feed(agent, messages):
    class Client:
        async def receive_messages(self):
            for message in messages:
                yield message

    agent._client = Client()
    await agent._run_message_loop()


def finish(agent, now):
    ticket = agent.completion_controller.candidate()
    assert ticket is not None
    closed = agent.completion_controller.claim_due(ticket, now)
    assert closed is not None
    agent._set_state(AgentState.USER_TURN)
    return closed


def test_requesting_bridges_late_first_tokens(monkeypatch):
    async def scenario():
        agent = make_agent()
        transitions = []
        async def changed(_):
            state = agent.state.value
            if not transitions or transitions[-1] != state:
                transitions.append(state)
        agent._state_change_callback = changed
        clock = [0.0]
        loop = asyncio.get_running_loop()
        original = loop.time
        monkeypatch.setattr(loop, "time", lambda: clock[0])
        try:
            await feed(agent, [answer("intermediate"), result()])
            ticket = agent.completion_controller.candidate()
            assert agent.state == AgentState.ASSISTANT_TURN
            assert not agent._first_turn_done_event.is_set()
            assert not agent._first_user_turn_reached
            clock[0] = 0.02
            await feed(agent, [SystemMessage(subtype="status", data={"status": "requesting"})])
            assert agent.completion_controller.candidate() is None
            assert agent.completion_controller.claim_due(ticket, 2.0) is None
            clock[0] = 3.0
            await feed(agent, [StreamEvent(uuid="stream", session_id=agent.session_id,
                                          event={"type": "message_start"})])
            clock[0] = 4.0
            await feed(agent, [answer("conclusion"), result()])
            assert agent.completion_controller.candidate().deadline == 5.0
            closed = finish(agent, 5.0)
            await agent._notify_state_change()
            assert agent._first_turn_done_event.is_set()
            assert agent._first_user_turn_reached
            assert closed.final_refs == (NativeMessageRef("claude_uuid", "intermediate"),
                                         NativeMessageRef("claude_uuid", "conclusion"))
            assert transitions == ["assistant_turn", "user_turn"]
        finally:
            agent._cancel_completion_timer()
            monkeypatch.setattr(loop, "time", original)
    asyncio.run(scenario())


def test_root_ingress_invalidates_before_stream_broadcast_await():
    async def scenario():
        agent = make_agent()
        await feed(agent, [answer(), result()])
        before = agent.completion_controller.candidate()
        assert before is not None
        async def broadcast(_):
            assert agent.completion_controller.candidate() is None
        agent._broadcast_stream_event = broadcast
        await feed(agent, [StreamEvent(uuid="stream", session_id=agent.session_id, event={
            "type": "message_start", "message": {"type": "message", "role": "assistant", "id": "api"}}),
            StreamEvent(uuid="stream2", session_id=agent.session_id, event={
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}})])
        assert agent.completion_controller.candidate() is None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("parent_first", [True, False])
def test_last_child_release_needs_no_second_parent_result(parent_first):
    async def scenario():
        agent = make_agent()
        started = SystemMessage(subtype="task_started", data={
            "task_id": "child", "task_type": "local_agent"})
        ended = SystemMessage(subtype="task_notification", data={
            "task_id": "child", "status": "completed"})
        await feed(agent, [answer(), started])
        if parent_first:
            await feed(agent, [result()])
            assert agent.completion_controller.candidate() is None
            await feed(agent, [ended])
        else:
            await feed(agent, [ended, result()])
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is not None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_stream_consumed_assistant_still_captures_api_identity():
    async def scenario():
        agent = make_agent()
        await feed(agent, [StreamEvent(uuid="stream", session_id=agent.session_id, event={
            "type": "message_start", "message": {"type": "message", "role": "assistant", "id": "api"}}),
            StreamEvent(uuid="stream2", session_id=agent.session_id, event={
                "type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
            answer(None, message_id="api"), result()])
        assert agent.completion_controller.snapshot().final_refs == (NativeMessageRef("claude_message", "api"),)
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("message", [
    SystemMessage(subtype="commands_changed", data={}),
    SystemMessage(subtype="hook_started", data={}),
    SystemMessage(subtype="status", data={"status": None, "permissionMode": "acceptEdits"}),
    UserMessage(content="<local-command-stdout>Set model to opus</local-command-stdout>"),
    answer("child-output", parent="tool-child"),
    StreamEvent(uuid="child-stream", session_id="completion-claude", event={"type": "message_start"},
                parent_tool_use_id="tool-child"),
])
def test_non_root_noise_does_not_open_interval(message):
    async def scenario():
        agent = make_agent()
        agent._set_state(AgentState.USER_TURN)
        await feed(agent, [message])
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_send_blocks_candidate_before_prompt_build_await():
    async def scenario():
        agent = make_agent()
        await feed(agent, [answer(), result()])
        stale = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        async def prompt(*args, **kwargs):
            entered.set()
            await release.wait()
            return "input"
        agent._build_query_prompt = prompt
        agent._client.query = AsyncMock()
        delivery = asyncio.create_task(agent.send("continue", completion_token="token", completion_cursor=41))
        await asyncio.wait_for(entered.wait(), 2)
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 2) is None
        release.set()
        assert await delivery is True
        assert agent.completion_controller._inputs["token"] == "accepted"
        assert agent._completion_source_floor == 41
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_monitor_release_and_wakeup_expiry_restart_confirmation(monkeypatch):
    async def scenario():
        agent = make_agent()
        await feed(agent, [UserMessage(content="Monitor started (task monitor, timeout 600000ms).",
                                       tool_use_result={"taskId": "monitor"}), answer(), result()])
        assert agent.completion_controller.candidate() is None
        await feed(agent, [SystemMessage(subtype="task_notification", data={"task_id": "monitor"})])
        assert agent.completion_controller.candidate() is not None
        await feed(agent, [UserMessage(content="wakeup", tool_use_result={"scheduledFor": 2000000000000}), result()])
        assert agent.completion_controller.candidate() is None
        monkeypatch.setattr("twicc.providers.claude_code.agent.agent.time.time", lambda: 2000000001)
        agent._completion_wakeup_due()
        assert agent.completion_controller.candidate() is not None
        agent._cancel_completion_timer()
        if agent._wakeup_refresh_handle:
            agent._wakeup_refresh_handle.cancel()
    asyncio.run(scenario())


def test_soft_interrupt_records_ended_without_confirmation():
    async def scenario():
        agent = make_agent()
        await feed(agent, [answer()])
        agent._soft_interrupting = True
        await feed(agent, [result(error=True)])
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == "ended"
        assert agent.completion_controller.candidate() is None
    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", [False, True])
def test_manager_does_not_apply_stale_settings_after_base_publication(monkeypatch, replacement):
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        agent.ephemeral = False
        agent._completion_activity()
        agent._set_state(AgentState.USER_TURN)
        agent._old_runs_purged = True
        manager._agents[agent.session_id] = agent
        applied = []
        async def apply(_):
            applied.append("obsolete")
        manager._apply_pending_settings = apply
        async def publication(_, current):
            if replacement:
                manager._agents[current.session_id] = make_agent()
            else:
                current._completion_activity()
        monkeypatch.setattr(BaseAgentManager, "_on_state_change", publication)
        await manager._on_state_change(agent)
        assert applied == []
    asyncio.run(scenario())


def test_manager_admits_input_before_live_settings_await():
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        agent.ephemeral = False
        agent._state_change_callback = AsyncMock()
        await feed(agent, [answer(), result()])
        stale = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        async def permission(_):
            entered.set()
            await release.wait()
        agent.set_permission_mode = permission
        agent._build_query_prompt = AsyncMock(return_value="continue")
        agent._client.query = AsyncMock()
        manager._agents[agent.session_id] = agent
        task = asyncio.create_task(manager.send_to_session(
            agent.session_id, agent.project_id, agent.cwd, "continue",
            agent.agent_settings._replace(permission_mode="acceptEdits"),
            completion_token="manager-input", completion_cursor=62))
        await asyncio.wait_for(entered.wait(), 2)
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 2) is None
        release.set()
        assert await task is True
        assert agent.completion_controller._inputs["manager-input"] == "accepted"
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_result_without_output_confirms_a_known_empty_answer():
    async def scenario():
        agent = make_agent()
        agent._open_main_turn()
        await feed(agent, [result()])
        ticket = agent.completion_controller.candidate()
        assert ticket is not None
        assert agent.completion_controller.claim_due(ticket, ticket.deadline).final_refs == ()
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_authentication_error_bypasses_a_matching_stream_branch(monkeypatch):
    from twicc.providers.claude_code import auth
    async def scenario():
        agent = make_agent()
        agent._shutdown_sdk_client = AsyncMock()
        monkeypatch.setattr(auth, "mark_unauthenticated_and_broadcast", AsyncMock())
        denied = answer("auth-error", message_id="api")
        denied.error = "authentication_failed"
        await feed(agent, [StreamEvent(uuid="stream", session_id=agent.session_id, event={
            "type": "message_start", "message": {"type": "message", "role": "assistant", "id": "api"}}),
            StreamEvent(uuid="stream2", session_id=agent.session_id, event={
                "type": "content_block_start", "index": 0, "content_block": {"type": "text"}}), denied])
        assert agent.state == AgentState.DEAD
        assert agent.completion_controller.snapshot().outcome == "provider_error"
        assert agent.completion_controller.candidate() is None
    asyncio.run(scenario())


def test_deferred_settings_restart_keeps_the_admission_token(monkeypatch):
    from twicc.providers.claude_code import cron_restart
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
    from twicc.request_origin import RequestOrigin, origin_scope
    async def scenario():
        manager, original = ClaudeCodeAgentManager(), make_agent()
        original.ephemeral = False
        original._set_state(AgentState.USER_TURN)
        original._state_change_callback = AsyncMock()
        manager._agents[original.session_id] = original
        manager._session_has_crons = AsyncMock(return_value=True)
        manager._check_session_writable = AsyncMock()
        manager._broadcast_agent_state = AsyncMock()
        original.interrupt_or_kill = AsyncMock()
        origin = RequestOrigin("agent", "cli", "sending-session")
        with origin_scope(origin):
            accepted = await manager.send_to_session(original.session_id, original.project_id, original.cwd, "continue",
                original.agent_settings._replace(effort="max"), completion_token="deferred", completion_cursor=81)
        assert accepted is False
        assert manager._pending_after_restart[original.session_id]["completion_token"] == "deferred"
        replacement = make_agent()
        replacement.ephemeral = False
        replacement._set_state(AgentState.USER_TURN)
        replacement._build_query_prompt = AsyncMock(return_value="continue")
        replacement._state_change_callback = AsyncMock()
        replacement._client = type("Client", (), {"query": AsyncMock()})()
        manager._agents[original.session_id] = replacement
        monkeypatch.setattr(cron_restart, "restart_session_crons", AsyncMock())
        await manager._restart_crons_for_session(original.session_id)
        assert replacement.completion_controller._inputs["deferred"] == "accepted"
        assert replacement._completion_source_floor == 81
        assert replacement._timeline_input_origins["deferred"] == origin
    asyncio.run(scenario())


def test_held_result_does_not_signal_first_user_turn_but_death_unblocks():
    async def scenario():
        agent = make_agent()
        await feed(agent, [answer(), SystemMessage(subtype="task_started", data={
            "task_id": "child", "task_type": "local_agent"}), result()])
        assert not agent._first_turn_done_event.is_set()
        assert not agent._first_user_turn_reached
        await agent._transition_to_dead()
        assert agent._first_turn_done_event.is_set()
        assert not agent._first_user_turn_reached
    asyncio.run(scenario())


def test_reserved_sdk_uuids_link_inputs_without_user_echoes():
    from types import SimpleNamespace
    async def scenario():
        agent = make_agent()
        agent._set_state(AgentState.USER_TURN)
        agent._notify_state_change = AsyncMock()
        agent._reconcile_context = AsyncMock()
        sent = []
        async def query(stream):
            sent.extend([message async for message in stream])
        agent._client = SimpleNamespace(query=query)
        assert await agent.send("A", completion_token="exact-A")
        assert agent._timeline_process_refs(AgentState.ASSISTANT_TURN) == [NativeMessageRef("claude_uuid", "exact-A")._asdict()]
        agent._completion_observe_message(answer("answer-A"))
        assert await agent.send("B", completion_token="exact-B")
        agent._completion_observe_message(result())
        snapshot = agent.completion_controller.snapshot()
        assert [message["uuid"] for message in sent] == ["exact-A", "exact-B"]
        assert agent._timeline_refs_for_turn(snapshot, start=True) == [NativeMessageRef("claude_uuid", "exact-A")._asdict()]
        assert agent._timeline_refs_for_turn(snapshot) == [NativeMessageRef("claude_uuid", identity)._asdict()
                                                        for identity in ("exact-A", "answer-A", "exact-B")]
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_internal_sdk_user_messages_do_not_become_turn_message_refs():
    from claude_agent_sdk import ToolResultBlock
    async def scenario():
        agent = make_agent()
        agent._completion_observe_message(UserMessage(uuid="tool-result", content=[
            ToolResultBlock(tool_use_id="tool", content="result"), TextBlock(text="loaded")]))
        agent._completion_observe_message(UserMessage(uuid="notification", content="wake up",
            origin={"kind": "task-notification"}))
        agent._completion_observe_message(UserMessage(uuid="child", content="child", parent_tool_use_id="tool"))
        assert agent._timeline_refs_for_turn(agent.completion_controller.snapshot()) == []
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_sdk_shell_notice_has_no_user_reference():
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    async def scenario():
        agent = make_agent()
        agent._completion_observe_message(UserMessage(uuid="notice", content=SHELL_NOTICE_HEADER + ': still running'))
        assert agent._timeline_refs_for_turn(agent.completion_controller.snapshot()) == []
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_explicit_sdk_final_is_retained_before_result_or_stop():
    async def scenario():
        agent = make_agent()
        agent._completion_observe_message(answer("explicit"))
        closed = agent.completion_controller.terminate("ended", asyncio.get_running_loop().time())
        assert agent._timeline_refs_for_turn(closed) == [NativeMessageRef("claude_uuid", "explicit")._asdict()]
        agent._cancel_completion_timer()
    asyncio.run(scenario())
