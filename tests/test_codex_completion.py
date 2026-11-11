"""Codex SDK ownership and confirmed logical completion."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.helpers import AgentSettings


def make_agent(*, ephemeral=True):
    agent = CodexAgent("completion-codex", "project", "/tmp", AgentSettings(), AsyncMock(), AsyncMock(),
                       work_dirs=[], ephemeral=ephemeral)
    agent._broadcast_stream_event = AsyncMock()
    agent._broadcast_process_label = AsyncMock()
    agent._broadcast_process_tools = AsyncMock()
    agent._link_async_question_turn = AsyncMock()
    agent._settle_async_questions = AsyncMock()
    agent._continue_async_questions = AsyncMock()
    agent._prune_finished_subagents = AsyncMock()
    agent._record_async_question_fact = AsyncMock()
    return agent


def event(method, *, item=None, status="completed", turn_id="physical-1", thread_id="completion-codex"):
    payload = SimpleNamespace(thread_id=thread_id, turn_id=turn_id)
    if item is not None:
        payload.item = SimpleNamespace(root=SimpleNamespace(**item))
    if method.startswith("turn/"):
        payload.turn = SimpleNamespace(id=turn_id, status=status)
    return SimpleNamespace(method=method, payload=payload)


def final(identity="answer", *, delivery=None, thread_id="completion-codex"):
    return event("item/completed", item={"type": "agentMessage", "id": identity, "text": "answer",
                                         "phase": "final_answer", "delivery": delivery}, thread_id=thread_id)


class Turn:
    id = "physical-1"

    def __init__(self, events):
        self.events = events
        self.steer = AsyncMock()

    async def stream(self):
        for value in self.events:
            yield value


async def run(agent, events):
    await agent._run_turn("", None, turn_handle=Turn(events))


def cleanup(agent):
    agent._cancel_completion_timer()
    agent._cancel_compaction_timeout()


def test_root_final_waits_one_second_and_retains_native_identity():
    async def scenario():
        agent = make_agent()
        await run(agent, [final(), event("turn/completed")])
        assert agent.state == AgentState.ASSISTANT_TURN
        ticket = agent.completion_controller.candidate()
        assert ticket is not None
        assert agent.completion_controller.claim_due(ticket, ticket.deadline - .001) is None
        closed = agent.completion_controller.claim_due(ticket, ticket.deadline)
        assert closed.final_refs == (NativeMessageRef("codex_item", "answer", "physical-1"),)
        assert not agent._has_actual_turn_work()
        cleanup(agent)
    asyncio.run(scenario())


def test_send_during_candidate_opens_fresh_sdk_turn():
    async def scenario():
        agent = make_agent()
        await run(agent, [final(), event("turn/completed")])
        interval = agent.completion_controller.snapshot().turn_id
        agent._schedule_turn = MagicMock()
        assert await agent.send("next") is True
        agent._schedule_turn.assert_called_once()
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.snapshot().turn_id == interval
        cleanup(agent)
    asyncio.run(scenario())


def test_active_turn_steers_and_opening_rpc_waits_for_handle():
    async def scenario():
        for opening in (False, True):
            agent = make_agent()
            agent._completion_activity()
            agent._actual_root_active = True
            handle = Turn([])
            agent._build_turn_input = AsyncMock(return_value=[])
            agent._steer_question_submission = AsyncMock()
            if not opening:
                agent._current_turn = handle
                agent._current_turn_ready.set()
            send = asyncio.create_task(agent.send("next", completion_token="input-id"))
            await asyncio.sleep(0)
            if opening:
                assert not send.done()
                agent._current_turn = handle
                agent._current_turn_ready.set()
            assert await send
            agent._steer_question_submission.assert_awaited_once_with(handle, [], None, completion_token="input-id")
            cleanup(agent)
    asyncio.run(scenario())


def test_async_final_and_child_final_do_not_replace_root_answer():
    async def scenario():
        agent = make_agent()
        await run(agent, [final("root"), final("async", delivery="async"),
                          final("child", thread_id="child"), event("turn/completed")])
        assert agent.ephemeral_final_text == "answer"
        assert agent.completion_controller.snapshot().final_refs == (
            NativeMessageRef("codex_item", "root", "physical-1"),)
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("status,outcome", [("interrupted", "ended"), ("failed", "provider_error")])
def test_exceptional_turn_bypasses_confirmation(status, outcome):
    async def scenario():
        agent = make_agent()
        await run(agent, [final(), event("turn/completed", status=status)])
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == outcome
        assert agent.completion_controller.candidate() is None
        cleanup(agent)
    asyncio.run(scenario())


def test_cold_plan_command_settles_without_logical_interval():
    async def scenario():
        agent = make_agent()
        agent._build_collaboration_mode = MagicMock(return_value=None)
        await agent.run_plan_command("")
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        assert agent.completion_controller.candidate() is None
        cleanup(agent)
    asyncio.run(scenario())


def test_command_failure_settles_without_interval():
    async def scenario():
        agent = make_agent()
        agent._thread.update_settings_with_policy.side_effect = RuntimeError("failure")
        agent._build_collaboration_mode = MagicMock(return_value=None)
        with pytest.raises(RuntimeError):
            await agent.run_plan_command("")
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        cleanup(agent)
    asyncio.run(scenario())


def test_goal_clear_without_continuation_settles():
    async def scenario():
        agent = make_agent()
        await agent.run_goal_command("clear")
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        cleanup(agent)
    asyncio.run(scenario())


def test_command_preserves_actual_root_ownership():
    async def scenario():
        agent = make_agent()
        agent._completion_activity()
        agent._actual_root_active = True
        await agent._settle_after_command(AgentState.USER_TURN, "plan_command_done")
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is None
        cleanup(agent)
    asyncio.run(scenario())


def test_manual_compact_does_not_open_logical_interval():
    async def scenario():
        agent = make_agent()
        await agent.compact()
        await agent.notify_compacted()
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        cleanup(agent)
    asyncio.run(scenario())


def test_child_resume_invalidates_candidate_before_deadline(monkeypatch):
    async def scenario():
        from twicc.providers.codex.agent import agent as module
        agent = make_agent(ephemeral=False)
        await run(agent, [final(), event("turn/completed")])
        candidate = agent.completion_controller.candidate()
        monkeypatch.setattr(module, "_running_first_level_subagent_ids", lambda *_: ["child"])
        await agent.notify_subagents_resumed([("child", "/root/task")])
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(candidate, candidate.deadline) is None
        assert agent.in_subagent_hold()
        cleanup(agent)
    asyncio.run(scenario())


def test_goal_owned_turns_keep_one_interval_until_goal_stops():
    async def scenario():
        agent = make_agent()
        monitor = SimpleNamespace(stream=lambda: Turn([
            event("turn/started"), final("goal-answer"), event("turn/completed")
        ]).stream(), close=MagicMock())
        agent._codex = MagicMock()
        agent._goal_monitor = monitor
        agent._goal_continuation_active = True
        await agent._run_goal_continuation(monitor)
        assert agent._goal_monitor is None
        assert not agent._has_actual_turn_work()
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is not None
        assert agent.completion_controller.snapshot().final_refs == (
            NativeMessageRef("codex_item", "goal-answer", "physical-1"),)
        cleanup(agent)
    asyncio.run(scenario())


def test_plan_question_blocks_confirmation_and_stay_ends_immediately():
    async def scenario():
        agent = make_agent()
        entered, answer = asyncio.Event(), asyncio.Event()
        async def question(request):
            agent._completion_awaiting_changed(True)
            entered.set()
            await answer.wait()
            agent._completion_awaiting_changed(False)
            return {"decision": "stay"}
        agent._await_pending_request = question
        task = asyncio.create_task(run(agent, [final(), event("item/completed", item={"type": "plan"}),
                                               event("turn/completed")]))
        await entered.wait()
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.is_open
        assert agent.state == AgentState.ASSISTANT_TURN
        answer.set()
        await task
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == "normal"
        cleanup(agent)
    asyncio.run(scenario())


def test_cleanup_await_cannot_confirm_before_root_settlement():
    async def scenario():
        agent = make_agent()
        async def settle(*args, **kwargs):
            assert not agent._has_actual_turn_work()
            assert agent.completion_controller.candidate() is None
        agent._settle_async_questions = settle
        await run(agent, [final(), event("turn/completed")])
        assert agent.completion_controller.candidate() is not None
        cleanup(agent)
    asyncio.run(scenario())


def test_manager_guards_title_hook_after_state_changes(monkeypatch):
    async def scenario():
        from twicc.agent.base_manager import BaseAgentManager
        from twicc.providers.codex.agent.manager import CodexAgentManager
        agent = make_agent(ephemeral=False)
        agent._completion_activity()
        agent._resumed = True
        manager = CodexAgentManager()
        manager._agents[agent.session_id] = agent
        manager._reassert_codex_title_after_resume = AsyncMock()
        async def changed(self, current):
            current._completion_provider_ended()
        monkeypatch.setattr(BaseAgentManager, "_on_state_change", changed)
        await manager._on_state_change(agent)
        manager._reassert_codex_title_after_resume.assert_not_awaited()
        cleanup(agent)
    asyncio.run(scenario())


def test_manager_reserves_input_before_first_delivery_await():
    async def scenario():
        from twicc.providers.codex.agent.manager import CodexAgentManager
        agent = make_agent()
        await run(agent, [final(), event("turn/completed")])
        manager = CodexAgentManager()
        manager._agents[agent.session_id] = agent
        async def deliver(*args, **kwargs):
            assert agent.completion_controller.candidate() is None
            assert kwargs["completion_token"] == "input-token"
            return True
        manager._send_to_session_under_gate = deliver
        assert await manager.send_to_session(agent.session_id, "project", "/tmp", "next", AgentSettings(),
                                             completion_token="input-token", completion_cursor=17)
        assert agent._completion_source_floor == 17
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,status", [("rejected", "rejected"), ("timeout", "uncertain"),
                                            ("preopen", "uncertain")])
def test_scheduled_input_waits_for_native_open_and_failure_never_confirms(failure, status):
    async def scenario():
        from openai_codex.errors import JsonRpcError
        agent = make_agent()
        opening = asyncio.Event()
        async def open_turn(*args, **kwargs):
            await opening.wait()
            if failure == "rejected":
                raise JsonRpcError(-32602, "rejected")
            raise TimeoutError("unknown delivery")
        agent._open_turn = open_turn
        agent._build_turn_input = AsyncMock(return_value=[])
        if failure == "preopen":
            agent._build_turn_input.side_effect = ValueError("bad input")
        settled = agent._completion_input_settled
        agent._completion_input_settled = MagicMock(wraps=settled)
        assert await agent.send("next", completion_token="native-input", completion_cursor=42)
        assert agent._has_actual_turn_work()
        agent._completion_input_settled.assert_not_called()
        opening.set()
        await agent._turn_task
        assert ("native-input", status) in [call.args for call in agent._completion_input_settled.call_args_list]
        assert agent.state == AgentState.DEAD
        assert agent.completion_controller.snapshot().outcome == "provider_error"
        assert agent.completion_controller.candidate() is None
        assert agent._completion_scheduled_tokens == set()
        cleanup(agent)
    asyncio.run(scenario())


def test_fresh_native_acceptance_settles_same_token():
    async def scenario():
        agent = make_agent()
        opened = asyncio.Event()
        release = asyncio.Event()
        async def open_turn(*args, **kwargs):
            opened.set()
            await release.wait()
            return Turn([final(), event("turn/completed")])
        agent._open_turn = open_turn
        agent._build_turn_input = AsyncMock(return_value=[])
        assert await agent.send("next", completion_token="native-input")
        await opened.wait()
        assert agent.completion_controller.candidate() is None
        assert "native-input" in agent._completion_scheduled_tokens
        release.set()
        await agent._turn_task
        assert not agent._completion_scheduled_tokens
        assert agent.completion_controller.candidate() is not None
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_native_finals_share_one_durable_interval_and_one_public_start(monkeypatch):
    from django.utils import timezone
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.models import LogicalTurn, ProcessRun, Project, Session, TimelineEvent
    from twicc.providers import db_writer
    project = Project.objects.create(id="codex-completion-project", directory="/tmp")
    session = Session.objects.create(id="completion-codex", project=project, provider="codex")
    process = ProcessRun.objects.create(session_id=session.id, provider="codex", started_at=timezone.now(),
                                        last_state_change_at=timezone.now(), state="starting")
    async def scenario():
        monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
        monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
        agent = make_agent(ephemeral=False)
        agent.get_pid = lambda: None
        agent.process_run = process
        agent.project_id = project.pk
        manager = BaseAgentManager()
        manager._agents[agent.session_id] = agent
        manager._broadcast_info = AsyncMock()
        manager._flush_pending_title = AsyncMock()
        manager._bind_completion_callbacks(agent)
        agent._state_change_callback = manager._on_state_change
        agent._completion_activity()
        await agent._notify_state_change()
        await run(agent, [final("intermediate"), event("turn/completed")])
        interval = agent.completion_controller.snapshot().turn_id
        agent._schedule_turn = MagicMock()
        await agent.send("continue", completion_token="2efc0667-3234-487d-bac1-93b6de135d82")
        agent._schedule_turn.assert_called_once()
        assert agent.completion_controller.snapshot().turn_id == interval
        agent.completion_controller.delay_seconds = 0
        await run(agent, [final("conclusion"), event("turn/completed")])
        ticket = agent.completion_controller.candidate()
        agent._cancel_completion_timer()
        await manager._confirm_completion(agent, ticket)
        await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=interval)
        assert row.outcome == "normal"
        assert [ref["native_id"] for ref in row.final_refs] == ["intermediate", "conclusion"]
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 1
        assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.order_by("id").values_list("kind", flat=True))) == [
            "turn.started", "turn.ended"]
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("command", ["plan", "plan-failed", "goal-clear"])
def test_command_only_settlement_has_no_durable_turn_boundaries(monkeypatch, command):
    from django.utils import timezone
    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.models import LogicalTurn, ProcessRun, Project, Session, TimelineEvent
    from twicc.providers import db_writer
    project = Project.objects.create(id="codex-command-project", directory="/tmp")
    Session.objects.create(id="completion-codex", project=project, provider="codex")
    process = ProcessRun.objects.create(session_id="completion-codex", provider="codex", started_at=timezone.now(),
                                        last_state_change_at=timezone.now(), state="starting")
    async def scenario():
        monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
        monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
        agent = make_agent(ephemeral=False)
        agent.get_pid = lambda: None
        agent.process_run = process
        agent.project_id = project.pk
        manager = BaseAgentManager()
        manager._agents[agent.session_id] = agent
        manager._broadcast_info = AsyncMock()
        manager._bind_completion_callbacks(agent)
        agent._state_change_callback = manager._on_state_change
        agent._build_collaboration_mode = MagicMock(return_value=None)
        if command == "plan-failed":
            agent._thread.update_settings_with_policy.side_effect = RuntimeError("failure")
            with pytest.raises(RuntimeError):
                await agent.run_plan_command("")
        elif command == "plan":
            await agent.run_plan_command("")
        else:
            await agent.run_goal_command("clear")
        assert agent.state == AgentState.USER_TURN
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert await asyncio.to_thread(TimelineEvent.objects.count) == 0
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=process.pk)).state == "user_turn"
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_manager_settings_failure_rejects_before_provider_delivery(monkeypatch):
    from twicc.providers.codex.agent.manager import CodexAgentManager
    from twicc.core.models import Project, Session
    project = Project.objects.create(id="project", directory="/tmp")
    Session.objects.create(id="completion-codex", project=project, provider="codex")
    async def scenario():
        async def write(factory, **kwargs):
            return await factory()
        monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", write)
        agent = make_agent()
        await run(agent, [final(), event("turn/completed")])
        manager = CodexAgentManager()
        manager._agents[agent.session_id] = agent
        agent.apply_agent_settings = AsyncMock(side_effect=RuntimeError("settings failure"))
        settled = agent._completion_input_settled
        agent._completion_input_settled = MagicMock(wraps=settled)
        with pytest.raises(RuntimeError, match="settings failure"):
            await manager.send_to_session(agent.session_id, "project", "/tmp", "next", AgentSettings(),
                                          completion_token="not-delivered")
        assert agent._completion_input_settled.call_args.args == ("not-delivered", "rejected")
        assert agent.completion_controller.candidate() is not None
        cleanup(agent)
    asyncio.run(scenario())


def test_goal_with_last_child_release_can_confirm(monkeypatch):
    async def scenario():
        agent = make_agent(ephemeral=False)
        agent._codex = MagicMock()
        monitor = SimpleNamespace(stream=lambda: Turn([event("turn/started"), final(),
                                                       event("turn/completed")]).stream(), close=MagicMock())
        agent._live_subagents["child"] = "/root/task"
        agent._goal_monitor = monitor
        agent._goal_continuation_active = True
        await agent._run_goal_continuation(monitor)
        assert agent.completion_controller.candidate() is None
        monkeypatch.setattr("twicc.providers.codex.agent.agent._stopped_subagent_ids", lambda *_: ["child"])
        await agent.notify_subagents_stopped(["child"])
        assert agent.completion_controller.candidate() is not None
        cleanup(agent)
    asyncio.run(scenario())


def test_send_after_stream_drain_opens_fresh_and_old_cleanup_cannot_end_it():
    async def scenario():
        agent = make_agent()
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(*args, **kwargs):
            entered.set()
            await release.wait()
        agent._settle_async_questions = settle
        old = asyncio.create_task(run(agent, [final("old"), event("turn/completed")]))
        agent._turn_task = old
        await entered.wait()
        agent._open_turn = AsyncMock(return_value=Turn([]))
        agent._build_turn_input = AsyncMock(return_value=[])
        try:
            assert await asyncio.wait_for(agent.send("new"), .2)
            newer = agent._turn_task
            assert newer is not old
            release.set()
            await old
            await newer
            assert agent.state == AgentState.ASSISTANT_TURN
            assert agent.completion_controller.candidate() is not None
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)
            cleanup(agent)
    asyncio.run(scenario())


def test_old_prune_cannot_arm_child_hold_over_new_actual_work():
    async def scenario():
        agent = make_agent()
        agent._completion_activity()
        agent._live_subagents["child"] = "/root/task"
        entered, release = asyncio.Event(), asyncio.Event()
        async def prune():
            entered.set()
            await release.wait()
        agent._prune_finished_subagents = prune
        old = asyncio.create_task(agent._try_arm_subagent_hold())
        await entered.wait()
        agent._actual_root_active = True
        release.set()
        assert await old is False
        assert not agent.in_subagent_hold()
        cleanup(agent)
    asyncio.run(scenario())


def test_command_cleanup_cannot_end_new_actual_work():
    async def scenario():
        agent = make_agent()
        agent._completion_activity()
        agent._completion_provider_ended()
        entered, release = asyncio.Event(), asyncio.Event()
        async def prune():
            entered.set()
            await release.wait()
        agent._prune_finished_subagents = prune
        command = asyncio.create_task(agent._settle_after_command(AgentState.USER_TURN, "plan_command_done"))
        await entered.wait()
        agent._actual_root_active = True
        agent._completion_activity()
        release.set()
        await command
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is None
        cleanup(agent)
    asyncio.run(scenario())


def test_plan_stay_cleanup_cannot_close_new_actual_work():
    async def scenario():
        agent = make_agent()
        agent._completion_activity()
        agent._completion_provider_ended()
        agent._await_pending_request = AsyncMock(return_value={"decision": "stay"})
        entered, release = asyncio.Event(), asyncio.Event()
        async def prune():
            entered.set()
            await release.wait()
        agent._prune_finished_subagents = prune
        plan = asyncio.create_task(agent._prompt_plan_implementation())
        await entered.wait()
        agent._actual_root_active = True
        agent._completion_activity()
        release.set()
        await plan
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.is_open
        cleanup(agent)
    asyncio.run(scenario())


def test_goal_settlement_routes_send_to_fresh_turn_before_cleanup_returns():
    async def scenario():
        agent = make_agent()
        agent._codex = MagicMock()
        monitor = SimpleNamespace(stream=lambda: Turn([event("turn/started"), final("goal"),
                                                       event("turn/completed")]).stream(), close=MagicMock())
        agent._goal_monitor = monitor
        agent._goal_continuation_active = True
        entered, release, native_opened, native_release = (asyncio.Event() for _ in range(4))
        async def settle(*args, **kwargs):
            entered.set()
            await release.wait()
        class FreshTurn:
            id = "fresh-turn"
            async def stream(self):
                native_opened.set()
                await native_release.wait()
                yield event("turn/completed", turn_id=self.id)
        agent._settle_async_questions = settle
        agent._open_turn = AsyncMock(return_value=FreshTurn())
        agent._build_turn_input = AsyncMock(return_value=[])
        goal = asyncio.create_task(agent._run_goal_continuation(monitor))
        agent._turn_task = goal
        try:
            await entered.wait()
            assert agent._goal_monitor is None
            assert agent._current_turn is None
            assert not agent._has_actual_turn_work()
            assert agent.completion_controller.candidate() is None
            assert await asyncio.wait_for(agent.send("next"), .2)
            await native_opened.wait()
            fresh = agent._turn_task
            assert fresh is not goal
            release.set()
            await goal
            assert agent._current_turn.id == "fresh-turn"
            assert agent.state == AgentState.ASSISTANT_TURN
            assert agent.completion_controller.is_open
            assert agent.completion_controller.candidate() is None
            agent._open_turn.assert_awaited_once()
            native_release.set()
            await fresh
        finally:
            release.set()
            native_release.set()
            await asyncio.gather(goal, agent._turn_task, return_exceptions=True)
            cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("status,outcome", [("interrupted", "ended"), ("failed", "provider_error")])
@pytest.mark.parametrize("verify_immediate", [True, False])
def test_exceptional_goal_closes_before_settlement_and_cannot_close_new_work(status, outcome, verify_immediate):
    async def scenario():
        agent = make_agent()
        agent._codex = MagicMock()
        monitor = SimpleNamespace(stream=lambda: Turn([event("turn/started"), final("goal"),
                                                       event("turn/completed", status=status)]).stream(),
                                  close=MagicMock())
        agent._goal_monitor = monitor
        agent._goal_continuation_active = True
        entered, release = asyncio.Event(), asyncio.Event()
        async def settle(*args, **kwargs):
            entered.set()
            await release.wait()
        agent._settle_async_questions = settle
        goal = asyncio.create_task(agent._run_goal_continuation(monitor))
        agent._turn_task = goal
        try:
            await entered.wait()
            closed = agent.completion_controller.snapshot()
            if verify_immediate:
                assert closed.outcome == outcome
                assert agent.state == AgentState.USER_TURN
            await agent.notify_goal_continuation_stopped()
            agent._schedule_turn = MagicMock()
            assert await agent.send("new work")
            current = agent.completion_controller.snapshot()
            if verify_immediate:
                assert current.turn_id != closed.turn_id
            assert agent._has_actual_turn_work()
            release.set()
            await goal
            assert agent._has_actual_turn_work()
            assert agent.state == AgentState.ASSISTANT_TURN
            assert agent.completion_controller.snapshot().turn_id == current.turn_id
            assert agent.completion_controller.is_open
            assert agent.completion_controller.candidate() is None
        finally:
            release.set()
            await asyncio.gather(goal, return_exceptions=True)
            cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize('earlier_answer', [False, True])
def test_empty_physical_final_retains_previous_relevant_final(earlier_answer):
    async def scenario():
        agent = make_agent()
        if earlier_answer:
            await run(agent, [final('earlier'), event('turn/completed')])
        await run(agent, [event('item/completed', item={'type': 'agentMessage', 'id': 'empty', 'text': '', 'phase': 'final_answer'}),
                          event('turn/completed')])
        refs = agent.completion_controller.snapshot().final_refs
        assert refs == ((NativeMessageRef('codex_item', 'earlier', 'physical-1'),) if earlier_answer else ())
        cleanup(agent)
    asyncio.run(scenario())


def test_reserved_client_identity_reaches_start_and_steer():
    async def scenario():
        agent = make_agent()
        agent._thread = SimpleNamespace(turn_with_policy=AsyncMock(return_value=Turn([])))
        agent._admit_async_question_owner = AsyncMock()
        agent._untrusted = False
        handle = await agent._open_turn([], completion_token="start-id")
        assert agent._thread.turn_with_policy.await_args.kwargs["client_user_message_id"] == "start-id"
        assert handle.id == "physical-1"
        from unittest.mock import patch
        with patch("twicc.providers.codex.agent.agent.steer_with_message_id", new=AsyncMock()) as steer:
            await agent._steer_question_submission(handle, [], None, completion_token="steer-id")
            steer.assert_awaited_once_with(handle, [], client_user_message_id="steer-id")
        cleanup(agent)
    asyncio.run(scenario())


def test_root_user_observation_reuses_reserved_client_alias():
    async def scenario():
        agent = make_agent()
        agent._completion_activity()
        agent._completion_input_pending("token")
        reserved = NativeMessageRef("codex_client", "client")
        agent._timeline_reserve_input("token", reserved)
        await agent._handle_stream_event(event("item/completed", item={
            "type": "userMessage", "id": "native", "client_id": "client", "delivery": None}))
        assert agent._timeline_refs_for_turn(agent.completion_controller.snapshot()) == [reserved._asdict()]
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("text", [":: notice from TwiCC: still running", "<twicc-resume>continue</twicc-resume>"])
def test_internal_user_observation_has_no_message_reference(text):
    async def scenario():
        agent = make_agent()
        await agent._handle_stream_event(event("item/completed", item={
            "type": "userMessage", "id": "internal", "delivery": None,
            "content": [SimpleNamespace(type="text", text=text)]}))
        assert agent._timeline_refs_for_turn(agent.completion_controller.snapshot()) == []
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["failed", "interrupted", "completed"])
@pytest.mark.parametrize("explicit_final", [False, True])
def test_goal_end_verifies_candidate_only_after_completed_status(status, explicit_final):
    async def scenario():
        agent = make_agent()
        agent._codex = MagicMock()
        answer = event("item/completed", item={"type": "agentMessage", "id": "goal-candidate", "text": "answer",
                       "phase": "final_answer" if explicit_final else None, "delivery": None})
        monitor = SimpleNamespace(stream=lambda: Turn([
            event("turn/started"), answer, event("turn/completed", status=status),
        ]).stream(), close=MagicMock())
        agent._goal_monitor = monitor
        agent._goal_continuation_active = True
        await agent._run_goal_continuation(monitor)
        snapshot = agent.completion_controller.snapshot()
        native_ref = NativeMessageRef("codex_item", "goal-candidate", "physical-1")
        assert snapshot.final_refs == (native_ref,)
        assert snapshot.outcome == {"failed": "provider_error", "interrupted": "ended", "completed": None}[status]
        assert agent._timeline_refs_for_turn(snapshot) == (
            [native_ref._asdict()] if explicit_final or status == "completed" else []
        )
        cleanup(agent)
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["failed", "interrupted", "completed"])
@pytest.mark.parametrize("explicit_final", [False, True])
def test_replaced_stream_end_verifies_candidate_only_after_completed_status(status, explicit_final):
    async def scenario():
        agent = make_agent()
        replacement = asyncio.create_task(asyncio.Event().wait())
        agent._turn_task = replacement
        answer = event("item/completed", item={"type": "agentMessage", "id": "replaced-candidate", "text": "answer",
                       "phase": "final_answer" if explicit_final else None, "delivery": None})
        try:
            await run(agent, [answer, event("turn/completed", status=status)])
            snapshot = agent.completion_controller.snapshot()
            native_ref = NativeMessageRef("codex_item", "replaced-candidate", "physical-1")
            assert snapshot.final_refs == (native_ref,)
            assert snapshot.outcome is None
            assert agent._turn_task is replacement
            assert agent.state == AgentState.ASSISTANT_TURN
            assert "physical-1" not in agent._completion_turn_outcomes
            assert agent._timeline_refs_for_turn(snapshot) == (
                [native_ref._asdict()] if explicit_final or status == "completed" else []
            )
        finally:
            replacement.cancel()
            await asyncio.gather(replacement, return_exceptions=True)
            cleanup(agent)
    asyncio.run(scenario())


def test_manager_copies_runtime_message_proof_before_worker_use():
    from tests.test_completion_publication import Manager
    async def scenario():
        agent, manager = make_agent(ephemeral=False), Manager()
        manager._agents[agent.session_id] = agent
        first = NativeMessageRef("codex_item", "verified-first", "physical-1")
        second = NativeMessageRef("codex_item", "verified-second", "physical-2")
        agent._completion_source_floor = 17
        agent._completion_storage_incarnation = "opening-source"
        agent._completion_history_epoch = 2
        agent._completion_activity()
        agent._timeline_observe_message(first, role="assistant")
        copied = manager.timeline_verified_message_refs(agent.session_id)
        agent._completion_source_floor = 30
        agent._completion_storage_incarnation = "latest-source"
        agent._completion_history_epoch = 3
        agent._timeline_observe_message(second, role="assistant")
        assert tuple(proof.ref for proof in copied) == (first,)
        assert copied[0].provider == "codex"
        assert copied[0].source_floor == 17
        assert copied[0].storage_incarnation == "opening-source"
        assert copied[0].history_epoch == 2
        assert tuple(proof.ref for proof in manager.timeline_verified_message_refs(agent.session_id)) == (first, second)
        assert manager.timeline_verified_message_refs("absent") is None
        cleanup(agent)
    asyncio.run(scenario())
