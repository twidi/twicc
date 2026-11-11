"""Provider shell snapshots and completion delivery settlement."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tests.test_claude_completion import answer, make_agent as claude_agent, result
from tests.test_codex_completion import event, make_agent as codex_agent
from tests.test_shell_notice_claude import bash_use, feed, system
from twicc.agent.states import AgentState
from twicc.agent.completion_types import CompletionCandidate, CompletionSnapshot, NativeMessageRef
from twicc.agent.semantic_types import EndingContext, SourceScope
from tests.test_timeline_messages import content_row_factory, live_session


async def shell(agent, key="tests", command="uv run pytest", *, parent=None):
    await feed(agent, bash_use("tool-" + key, command, parent), system("task_started",
        task_id=key, task_type="local_bash", tool_use_id="tool-" + key, is_backgrounded=True))


def capture(agent, monkeypatch):
    if hasattr(agent, "_main_turn_open"):
        agent._main_turn_open = False
    agent._completion_activity()
    agent._completion_provider_ended()
    ticket = agent.completion_controller.candidate()
    monkeypatch.setattr(agent.completion_controller, "delay_seconds", 0)
    agent._cancel_completion_timer()
    # Move only eligibility, without changing the loop clock or source timestamp.
    agent.completion_controller._candidate = ticket._replace(deadline=asyncio.get_running_loop().time())
    return agent._completion_capture_ending(agent.completion_controller.candidate(), [])


@pytest.mark.parametrize("command", ["uv run pytest", "npm run dev"])
def test_snapshot_keeps_complete_required_work_and_persistent_server(command):
    async def scenario():
        agent = claude_agent()
        await shell(agent, command=command)
        snapshot = agent.completion_shell_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0]["command"] == command
        assert snapshot[0]["owner"] == "root"
        assert snapshot[0]["generation"] > 0
        assert snapshot[0]["start_line"] is None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_mixed_owned_shells_remain_in_snapshot_without_notice_delay():
    async def scenario():
        agent = claude_agent()
        agent._agent_by_tool_use["spawn"] = "child"
        await shell(agent, "server", "npm run dev")
        await shell(agent, "tests", "uv run pytest", parent="spawn")
        snapshot = agent.completion_shell_snapshot()
        assert [row["owner"] for row in snapshot] == ["root", "subagent"]
        assert snapshot[1]["owner_ref"] == "child"
        assert [row["command"] for row in snapshot] == ["npm run dev", "uv run pytest"]
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_shell_exits_during_pending_analysis_waits_for_root(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent)
        context = capture(agent, monkeypatch)
        canceled = []
        agent._completion_cancel_inference = canceled.append
        await feed(agent, system("task_updated", task_id="tests", patch={"status": "completed"}))
        assert agent._completion_pending_ending is None
        assert canceled == [context.id]
        assert not agent.completion_delivery_settled()
        assert agent.completion_controller.candidate() is None
        assert agent.state == AgentState.ASSISTANT_TURN
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_claude_terminal_then_notification_then_delayed_root_stays_one_turn(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent)
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        await feed(agent, system("task_updated", task_id="tests", patch={"status": "completed"}))
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        assert not agent.completion_delivery_settled()
        assert agent.completion_controller.candidate() is None
        agent._completion_observe_message(system("status", status="requesting"))
        assert agent.completion_controller.candidate() is None
        agent._completion_observe_message(answer("conclusion"))
        agent._completion_observe_message(result())
        assert agent.completion_delivery_settled()
        assert agent.completion_controller.candidate() is not None
        assert agent.completion_controller.snapshot().turn_id == context.snapshot.turn_id
        assert agent.completion_controller.snapshot().ordinal == 1
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_missing_delivery_evidence_reconsiders_after_45_seconds(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent)
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        await feed(agent, system("task_updated", task_id="tests", patch={"status": "completed"}))
        deadline = agent._completion_delivery_deadline
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        assert agent._completion_delivery_deadline == deadline
        monkeypatch.setattr(agent, "_completion_delivery_now", lambda: deadline - .001)
        agent._completion_reconsider_if_settled()
        assert agent.completion_controller.candidate() is None
        monkeypatch.setattr(agent, "_completion_delivery_now", lambda: deadline)
        agent._completion_reconsider_if_settled()
        assert agent.completion_controller.candidate() is not None
        assert agent._completion_pending_ending is None
        assert context.snapshot.turn_id not in agent._completion_pending_publications
        ticket = agent.completion_controller.candidate()
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        assert agent.completion_delivery_settled()
        assert agent.completion_controller.candidate() == ticket
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_codex_exit_waits_for_actual_work(monkeypatch):
    async def scenario():
        agent = codex_agent(ephemeral=False)
        agent._track_shell((agent.session_id, "process"), 10, "uv run pytest")
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        agent._actual_root_active = True
        await agent.notify_shells_exited(agent.session_id, ["process"])
        assert not agent.completion_delivery_settled()
        assert agent.completion_controller.candidate() is None
        agent._actual_root_active = False
        agent._completion_reconsider_if_settled()
        assert agent.completion_controller.candidate() is not None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_accepted_independent_shell_ending_is_not_reopened(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent, command="npm run dev")
        context = capture(agent, monkeypatch)
        assert agent._completion_claim_ending(context, reason="model") is not None
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        assert agent.completion_delivery_settled()
        assert agent.completion_controller.snapshot().outcome == "normal"
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_codex_root_and_owned_shell_generations_invalidate_approval(monkeypatch):
    async def scenario():
        agent = codex_agent(ephemeral=False)
        agent._track_shell((agent.session_id, "same"), 10, "npm run dev")
        agent._track_shell(("child", "same"), 10, "uv run pytest")
        context = capture(agent, monkeypatch)
        shells = agent.completion_shell_snapshot()
        assert [row["owner"] for row in shells] == ["root", "subagent"]
        assert len({row["key"] for row in shells}) == 2
        assert agent._completion_shells_are_current(context)
        agent._track_shell(("child", "same"), 11, "uv run pytest")
        assert not agent._completion_shells_are_current(context)
        assert not agent._completion_claim_ending(context, reason="model")
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_reused_shell_key_does_not_wait_for_rejected_old_generation(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent)
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        agent._completion_clear_shell_delivery()
        # Simulate an old rejection retained while this key now names a new generation.
        agent._completion_blocking_shells = tuple(context.payload["shell_refs"])
        await shell(agent)
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        assert not agent._completion_delivery_waits
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_mixed_shells_reconsider_remaining_server_once_delivery_settles(monkeypatch):
    async def scenario():
        agent = codex_agent(ephemeral=False)
        agent._track_shell((agent.session_id, "server"), 10, "npm run dev")
        agent._track_shell(("child", "tests"), 10, "uv run pytest")
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        agent._schedule_turn = lambda *args, **kwargs: pytest.fail("Settlement must not launch root work")
        await agent.notify_shells_exited("child", ["tests"])
        ticket = agent.completion_controller.candidate()
        assert ticket is not None
        assert agent.completion_shell_snapshot()[0]["command"] == "npm run dev"
        await agent.notify_shells_exited("child", ["tests"])
        assert agent.completion_controller.candidate() == ticket
        assert not agent._completion_claim_ending(context, reason="obsolete")
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_later_normal_result_preserves_previously_rejected_final_without_extra_root_task(monkeypatch):
    async def scenario():
        agent = claude_agent()
        await shell(agent)
        agent._completion_observe_message(answer("progress"))
        agent._completion_observe_message(result())
        context = capture(agent, monkeypatch)
        agent._completion_ending_rejected(context)
        await feed(agent, system("task_notification", task_id="tests", status="completed"))
        agent._completion_observe_message(system("status", status="requesting"))
        agent._completion_observe_message(result())
        candidate = agent.completion_controller.candidate()
        assert candidate is not None
        agent._cancel_completion_timer()
        closed = agent.completion_controller.claim_due(candidate, candidate.deadline)
        assert closed.final_refs == (NativeMessageRef("claude_uuid", "progress"),)
        assert closed.turn_id == context.snapshot.turn_id
        assert closed.outcome == "normal"
    asyncio.run(scenario())


def test_root_tool_refs_are_frozen_and_child_tools_are_excluded(monkeypatch):
    from claude_agent_sdk import ToolResultBlock, UserMessage

    async def scenario():
        agent = claude_agent()
        agent._completion_observe_message(UserMessage(uuid="root-output", content=[
            ToolResultBlock(tool_use_id="tool", content="10 passed")]))
        agent._completion_observe_message(UserMessage(uuid="child-output", parent_tool_use_id="child", content=[
            ToolResultBlock(tool_use_id="child-tool", content="child output")]))
        context = capture(agent, monkeypatch)
        assert context.payload["captured_tool_refs"] == [NativeMessageRef("claude_uuid", "root-output")._asdict()]
        agent._completion_observe_message(answer("new-final"))
        assert not agent._completion_tool_refs
        assert len(context.payload["captured_tool_refs"]) == 1
        agent._cancel_completion_timer()

        agent = codex_agent()
        await agent._handle_stream_event(event("item/completed", item=dict(type="commandExecution", id="tool",
            command="uv run pytest", process_id="12", status="completed")))
        await agent._handle_stream_event(event("item/completed", thread_id="child", item=dict(
            type="commandExecution", id="child-tool", command="echo child", process_id="13")))
        assert list(agent._completion_tool_refs) == [NativeMessageRef("codex_item", "tool", "physical-1")]
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.django_db
def test_admitted_evidence_freezes_exact_final_and_completed_tool_before_future_append(live_session):
    f = live_session
    if f.provider == "claude_code":
        use = f.message("call", "", final=False)
        use["message"]["content"] = [{"type": "tool_use", "id": "tool", "name": "Bash",
                                      "input": {"command": "uv run pytest"}}]
        output = f.message("output", "", user=True)
        output["message"]["content"] = [{"type": "tool_result", "tool_use_id": "tool", "content": "10 passed"}]
        ref = NativeMessageRef("claude_uuid", "final")
    else:
        use = {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
            "call_id": "tool", "arguments": '{"cmd":"uv run pytest"}'}}
        output = {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "tool",
                                                       "output": "10 passed"}}
        ref = NativeMessageRef("codex_item", "final", "native-turn")
    f.append(f.message("user", "Run tests", user=True), use, output, f.message("final", "Tests complete"), capture=False)
    f.session.refresh_from_db()
    factory = claude_agent if f.provider == "claude_code" else codex_agent
    agent = factory()
    agent.session_id = f.session.id
    snapshot = CompletionSnapshot("logical", "run", 1, 1, None, (ref,), None)
    context = EndingContext(str(uuid4()), CompletionCandidate("logical", 1, 0), snapshot, timezone.now(),
        SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch), {
            "message_refs": [ref._asdict()], "input_context": [], "source_floor": 0,
            "previous_messages": [], "tool_evidence": []})
    agent._completion_enrich_ending_evidence_admitted(context)
    assert context.payload["previous_messages"][-1]["text"] == "Tests complete"
    assert context.payload["tool_evidence"][-1]["result"] == "10 passed"
    assert "uv run pytest" in context.payload["tool_evidence"][-1]["command"]
    assert context.payload["tool_evidence"][-1]["line"] == 3
    frozen = repr(context.payload)
    f.append(f.message("future", "Unrelated later result"), capture=False)
    agent._completion_enrich_ending_evidence_admitted(context)
    assert repr(context.payload) == frozen


@pytest.mark.django_db
def test_group_only_reference_does_not_capture_future_complete_assistant(live_session):
    f = live_session
    if f.provider != "claude_code":
        pytest.skip("Claude group references")
    f.append(f.message("part", "Partial", final=None, message_id="group"), capture=False)
    f.session.refresh_from_db()
    agent = claude_agent()
    agent.session_id = f.session.id
    ref = NativeMessageRef("claude_message", "group")
    snapshot = CompletionSnapshot("logical", "run", 1, 1, None, (ref,), None)
    context = EndingContext(str(uuid4()), CompletionCandidate("logical", 1, 0), snapshot,
        timezone.now() - timedelta(seconds=1), SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch),
        {"message_refs": [ref._asdict()], "input_context": [], "source_floor": 0,
         "previous_messages": [], "tool_evidence": []})
    f.append(f.message("later", "Future", message_id="group"), capture=False)
    agent._completion_enrich_ending_evidence_admitted(context)
    assert context.payload["previous_messages"] == []
    assert "source_boundary" not in context.payload


@pytest.mark.django_db
def test_captured_tool_after_final_is_included_without_uncaptured_future_tools(live_session):
    f = live_session
    final_ref = NativeMessageRef("claude_uuid", "final") if f.provider == "claude_code" else (
        NativeMessageRef("codex_item", "final", "native-turn"))
    if f.provider == "claude_code":
        use = f.message("call", "", final=False)
        use["message"]["content"] = [{"type": "tool_use", "id": "tool", "name": "Bash",
                                      "input": {"command": "uv run pytest"}}]
        output = f.message("output", "", user=True)
        output["message"]["content"] = [{"type": "tool_result", "tool_use_id": "tool", "content": "10 passed"}]
        observed = NativeMessageRef("claude_uuid", "output")
    else:
        use = f.message("call", "", final=False)
        output = {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": f.session.id,
            "turn_id": "native-turn", "item": {"id": "output", "type": "CommandExecution",
                "command": "uv run pytest", "status": "completed", "aggregated_output": "10 passed"}}}
        observed = NativeMessageRef("codex_item", "output", "native-turn")
    f.append(f.message("user", "Run tests", user=True), f.message("final", "Waiting"), use, output, capture=False)
    f.session.refresh_from_db()
    agent = claude_agent() if f.provider == "claude_code" else codex_agent()
    agent.session_id = f.session.id
    snapshot = CompletionSnapshot("logical", "run", 1, 1, None, (final_ref,), None)
    context = EndingContext(str(uuid4()), CompletionCandidate("logical", 1, 0), snapshot, timezone.now(),
        SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch), {
            "message_refs": [final_ref._asdict()], "captured_tool_refs": [observed._asdict()],
            "input_context": [], "source_floor": 0, "previous_messages": [], "tool_evidence": []})
    future = f.message("future", "Future output", user=True)
    f.append(future, capture=False)
    agent._completion_enrich_ending_evidence_admitted(context)
    assert context.payload["tool_evidence"][-1]["result"] == "10 passed"
    assert context.payload["source_boundary"] == 4
    assert context.payload["source_ceiling"] == 2
    assert context.payload["previous_messages"][-1]["text"] == "Waiting"
    assert all(row.get("line", 0) <= 2 for row in context.payload["previous_messages"])


@pytest.mark.django_db
@pytest.mark.parametrize("tool_type", ["McpToolCall", "DynamicToolCall", "FileChange"])
def test_canonical_completed_tool_results_are_available_to_selector(live_session, tool_type):
    f = live_session
    if f.provider != "codex":
        pytest.skip("Codex completed tool shapes")
    from twicc.core.services.timeline_messages import capture_ending_context

    item = {"id": "tool", "type": tool_type, "tool": "exec_command", "arguments": {"cmd": "uv run pytest"},
            "status": "completed", "result": {"content": [{"type": "text", "text": "10 passed"}]},
            "stdout": "10 passed", "changes": {}}
    f.append({"type": "event_msg", "payload": {"type": "item_completed", "thread_id": f.session.id,
        "turn_id": "native-turn", "item": item}}, f.message("final", "Tests complete"), capture=False)
    f.session.refresh_from_db()
    captured = capture_ending_context(f.session, refs=[NativeMessageRef("codex_item", "final", "native-turn")],
        floor=0, occurred_at=timezone.now())
    assert len(captured["tool_evidence"]) == 1
    assert "10 passed" in captured["tool_evidence"][0]["result"]


@pytest.mark.django_db
def test_ineligible_captured_final_does_not_substitute_older_assistant(live_session):
    f = live_session
    from twicc.core.services.timeline_messages import capture_ending_context

    f.append(f.message("older", "Earlier complete answer"), f.message("not-final", "Still working", final=False), capture=False)
    f.session.refresh_from_db()
    ref = NativeMessageRef("claude_uuid", "not-final") if f.provider == "claude_code" else (
        NativeMessageRef("codex_item", "not-final", "native-turn"))
    assert capture_ending_context(f.session, refs=[ref], floor=0, occurred_at=timezone.now()) == {}


@pytest.mark.django_db
def test_unreadable_evidence_remains_context_incomplete_and_freezes_once(live_session, monkeypatch):
    from twicc.core.content_storage.codec import ContentIntegrityError
    from unittest.mock import Mock

    f = live_session
    agent = claude_agent() if f.provider == "claude_code" else codex_agent()
    agent.session_id = f.session.id
    snapshot = CompletionSnapshot("logical", "run", 1, 1, None, (), None)
    context = EndingContext(str(uuid4()), CompletionCandidate("logical", 1, 0), snapshot, timezone.now(),
        SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch), {
            "message_refs": [], "input_context": [], "source_floor": 0,
            "previous_messages": [], "tool_evidence": []})
    capture_evidence = Mock(side_effect=ContentIntegrityError("Invalid content bytes"))
    monkeypatch.setattr("twicc.core.services.timeline_messages.capture_ending_context", capture_evidence)
    agent._completion_enrich_ending_evidence_admitted(context)
    agent._completion_enrich_ending_evidence_admitted(context)
    assert context.payload["previous_messages"] == []
    assert context.payload["tool_evidence"] == []
    assert capture_evidence.call_count == 1


def test_root_tool_without_native_identity_marks_context_incomplete(monkeypatch):
    from claude_agent_sdk import ToolResultBlock, UserMessage

    async def scenario():
        agent = claude_agent()
        agent._completion_observe_message(UserMessage(content=[ToolResultBlock(tool_use_id="tool", content="10 passed")]))
        context = capture(agent, monkeypatch)
        assert context.payload["tool_context_incomplete"]
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("agent_scope", ["current", "foreign_incarnation", "foreign_epoch"])
def test_watcher_only_codex_exit_keeps_result_after_sdk_drain(live_session, monkeypatch, agent_scope):
    f = live_session
    if f.provider != "codex":
        pytest.skip("Codex watcher exit ingress")
    from tests.test_codex_completion import Turn
    from twicc.providers.codex.agent.manager import CodexAgentManager
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

    f.append(f.message("user", "Run tests", user=True), f.message("final", "Tests continue"), capture=False)
    f.session.refresh_from_db()

    def completed(identity, process_id, output):
        return {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": f.session.id,
            "turn_id": "native-turn", "item": {"id": identity, "type": "CommandExecution",
                "process_id": process_id, "command": "uv run pytest", "status": "completed",
                "aggregated_output": output, "exit_code": 0}}}

    async def scenario():
        agent = codex_agent(ephemeral=False)
        agent.session_id = f.session.id
        agent._completion_storage_incarnation = f.session.storage_incarnation
        agent._completion_history_epoch = f.session.history_epoch
        agent._track_shell((agent.session_id, "tests"), 10, "uv run pytest")
        agent._track_shell((agent.session_id, "server"), 10, "npm run dev")
        sdk_turn = Turn([
            event("item/completed", thread_id=f.session.id, turn_id="native-turn", item={
                "type": "agentMessage", "id": "final", "text": "Tests continue", "phase": "final_answer"}),
            event("turn/completed", thread_id=f.session.id, turn_id="native-turn"),
        ])
        sdk_turn.id = "native-turn"
        await agent._run_turn("", None, turn_handle=sdk_turn)
        assert not agent._has_actual_turn_work()
        assert not agent._completion_tool_refs
        rejected = capture(agent, monkeypatch)
        agent._completion_ending_rejected(rejected)
        manager = CodexAgentManager()
        manager._agents[agent.session_id] = agent
        other_agent = codex_agent(ephemeral=False)
        other_agent.session_id = "another-root"
        other_agent._completion_activity()
        other_agent._completion_storage_incarnation = f.session.storage_incarnation
        other_agent._completion_history_epoch = f.session.history_epoch
        manager._agents[other_agent.session_id] = other_agent
        if agent_scope == "foreign_incarnation":
            agent._completion_storage_incarnation = str(uuid4())
        elif agent_scope == "foreign_epoch":
            agent._completion_history_epoch += 1
        await asyncio.to_thread(f.append, completed("exit-tests", "tests", "10 passed"),
            completed("future-output", "other", "Uncaptured future output"), capture=False)
        await CodexSessionsWatcher()._relay_shell_events(manager, agent.session_id, None, [3])
        assert not other_agent._completion_tool_refs
        other_agent._cancel_completion_timer()
        if agent_scope != "current":
            assert not agent._completion_tool_refs
            assert (agent.session_id, "tests") in agent._live_shells
            assert agent.completion_controller.candidate() is None
            agent._cancel_completion_timer()
            return
        candidate = agent.completion_controller.candidate()
        assert candidate is not None
        fresh = agent._completion_capture_ending(candidate, [])
        agent._cancel_completion_timer()
        await asyncio.to_thread(agent._completion_enrich_ending_evidence_admitted, fresh)
        assert fresh.payload["captured_tool_refs"] == [NativeMessageRef("codex_item", "exit-tests", "native-turn")._asdict()]
        assert [row["command"] for row in fresh.payload["target"]["shells"]] == ["npm run dev"]
        assert fresh.payload["tool_evidence"] == [{"tool_id": "exit-tests", "line": 3,
            "command": "uv run pytest", "result": "10 passed"}]
        assert fresh.payload["previous_messages"][-1] == {"role": "assistant", "text": "Tests continue", "line": 2}
        assert fresh.payload["source_boundary"] == 3
        assert fresh.payload["source_ceiling"] == 2
        assert fresh.snapshot.final_refs == rejected.snapshot.final_refs
    asyncio.run(scenario())
