"""Ordered hybrid transcript ingress and deferred first-paste admission."""

import asyncio
from unittest.mock import AsyncMock

import orjson
import pytest

from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager
from twicc.providers.helpers import AgentSettings


def make_agent():
    agent = HybridClaudeAgent("hybrid-completion", "project", "/tmp", AgentSettings())
    agent._notify_state_change = AsyncMock()
    return agent


def observation(kind, identity="", ref_kind="claude_uuid"):
    from twicc.providers.live_sync import CompletionObservation
    return CompletionObservation(kind, ref_kind if identity else "", identity)


def signals(*observations):
    from twicc.providers.claude_code.agent.hybrid.signals import HybridJsonlSignals
    return HybridJsonlSignals(observations=observations)


def test_ordered_end_then_start_does_not_close_new_root_activity():
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"),
            observation("end"), observation("user_message", "continued")))
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.snapshot().ordinal == 1
        assert agent.completion_controller.snapshot().first_ref == NativeMessageRef("claude_uuid", "user")
        assert agent.completion_controller.snapshot().final_refs == (NativeMessageRef("claude_uuid", "answer"),)
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_delayed_progress_cancels_final_candidate():
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"), observation("end")))
        stale = agent.completion_controller.candidate()
        assert stale is not None
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(observation("progress", "tool-result")))
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 5) is None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_local_command_ack_preserves_a_real_turn():
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("command"), observation("command_ack")))
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.snapshot().outcome is None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,outcome", [("interrupted", "ended"), ("provider_error", "provider_error")])
def test_exceptional_end_bypasses_confirmation(kind, outcome):
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"), observation(kind)))
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == outcome
        assert agent.completion_controller.candidate() is None
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_committed_ingress_invalidates_before_stalled_relay(monkeypatch):
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code import sessions_watcher, agent as agent_package
    from twicc.providers.live_sync import LiveSyncResult, LiveSyncUpdates
    session = Session.objects.create(id="hybrid-completion", project=Project.objects.create(id="project"),
                                     provider="claude_code", hybrid=True)

    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"), observation("end")))
        stale = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked():
            entered.set()
            await release.wait()
        agent._notify_state_change = blocked
        monkeypatch.setattr(agent_package.manager, "get_claude_code_agent_manager", lambda: manager)
        updates = LiveSyncUpdates.empty()._replace(completion_observations=(observation("activity", "continued"),))
        watcher = sessions_watcher.ClaudeCodeSessionsWatcher()
        watcher._after_live_slice_committed(session, LiveSyncResult(updates, False, 1, 100, 0))
        # No await occurs between committed ingress and candidate invalidation.
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 5) is None
        await asyncio.wait_for(entered.wait(), 2)
        release.set()
        await asyncio.sleep(0)
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_live_slice_carries_ordered_native_facts(tmp_path):
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code.compute import get_compute
    from twicc.providers.live_sync import LiveSyncLimits, merge_live_updates
    session = Session.objects.create(id="hybrid-completion", project=Project.objects.create(id="project"),
                                     provider="claude_code", hybrid=True)
    path = tmp_path / "transcript.jsonl"
    records = [
        {"type": "user", "uuid": "user", "message": {"role": "user", "content": "prompt"}},
        {"type": "assistant", "uuid": "answer", "message": {"id": "api", "role": "assistant",
            "content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn"}},
        {"type": "system", "subtype": "turn_duration"},
        {"type": "user", "uuid": "continued", "message": {"role": "user", "content": "continue"}},
        {"type": "user", "message": {"role": "user", "content": "[Request interrupted by user]"}},
    ]
    path.write_bytes(b"".join(orjson.dumps(record) + b"\n" for record in records))
    first = get_compute().sync_session_slice(session.id, path, limits=LiveSyncLimits(3))
    second = get_compute().sync_session_slice(session.id, path, limits=LiveSyncLimits(3))
    merged = merge_live_updates(first.updates, second.updates)
    assert [(event.kind, event.native_id) for event in merged.completion_observations] == [
        ("user_message", "user"), ("final", "answer"), ("end", ""),
        ("user_message", "continued"), ("interrupted", ""),
    ]


def test_hybrid_start_before_first_paste_stages_input(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from twicc.providers.claude_code.agent.hybrid import agent as module
    from twicc.providers.claude_code import sessions_watcher
    from twicc.core.services import trust

    async def scenario():
        agent = make_agent()
        ready, release = asyncio.Event(), asyncio.Event()
        async def composer():
            ready.set()
            await release.wait()
            return True
        agent._wait_until_composer_ready = composer
        agent._materialize_attachments = AsyncMock(return_value="prompt")
        agent._read_system_prompt_addendum = Mock(return_value=None)
        agent._resolve_temp_title = AsyncMock(return_value="test")
        agent._resolve_and_create_work_dirs = AsyncMock(return_value=[])
        agent._start_liveness_monitor = lambda: None
        monkeypatch.setattr(sessions_watcher, "get_watcher", lambda: Mock())
        monkeypatch.setattr(trust, "project_is_untrusted", lambda _: False)
        monkeypatch.setattr(module, "write_addendum_file", lambda *args: tmp_path / "addendum")
        monkeypatch.setattr(module, "get_session_hybrid_dir", lambda _: tmp_path)
        monkeypatch.setattr(module.hybrid_tmux, "session_exists", lambda _: False)
        monkeypatch.setattr(module.hybrid_tmux, "create_session", lambda *args: None)
        monkeypatch.setattr(module.hybrid_tmux, "pane_status", lambda _: (123, False))
        monkeypatch.setattr(module.hybrid_tmux, "paste_text", lambda *args: None)
        await agent.start("prompt", AsyncMock(), completion_token="staged", completion_cursor=23)
        assert agent.completion_controller.snapshot().ordinal == 0
        assert agent.completion_controller._inputs["staged"] == "pending"
        await asyncio.wait_for(ready.wait(), 2)
        release.set()
        await agent._first_paste_task
        assert agent.completion_controller.snapshot().ordinal == 0
        manager = ClaudeCodeAgentManager()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(observation("user_message", "native-user")))
        opening = agent.completion_controller.snapshot()
        assert opening.ordinal == 1
        assert opening.first_ref == NativeMessageRef("claude_uuid", "native-user")
        assert agent.completion_controller._inputs["staged"] == "accepted"
        assert agent._completion_openings[opening.turn_id][1] == 23
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(observation("activity", "model")))
        assert agent.completion_controller.snapshot().turn_id == opening.turn_id
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("compute_executor_started")
def test_watcher_records_root_before_stalled_session_refresh(provider_home, monkeypatch):
    from watchfiles import Change
    from twicc.core.models import Project, Session, SessionType
    from twicc.providers import sessions_watcher as base_module
    from twicc.providers.claude_code import sessions_watcher, agent as agent_package
    session = Session.objects.create(id="hybrid-completion", project=Project.objects.create(id="project"),
        provider="claude_code", hybrid=True, file_path="project/hybrid-completion.jsonl")
    path = provider_home.claude / "projects" / session.file_path
    path.parent.mkdir(parents=True)
    path.write_bytes(orjson.dumps({"type": "user", "uuid": "continued", "message": {
        "role": "user", "content": "continue"}}) + b"\n")

    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"), observation("end")))
        stale = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_refresh(current):
            entered.set()
            await release.wait()
            raise asyncio.CancelledError
        monkeypatch.setattr(base_module, "refresh_session", blocked_refresh)
        monkeypatch.setattr(agent_package.manager, "get_claude_code_agent_manager", lambda: manager)
        watcher = sessions_watcher.ClaudeCodeSessionsWatcher()
        parsed = base_module.ParsedSessionFile("project", session.id, SessionType.SESSION, session.file_path)
        task = asyncio.create_task(watcher.sync_and_broadcast(path, parsed, Change.modified, None))
        await asyncio.wait_for(entered.wait(), 5)
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 5) is None
        assert agent.completion_controller.snapshot().ordinal == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(*tuple(agent._completion_tasks))
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_completion_transport_import_does_not_load_agent_runtime(tmp_path):
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "-c", "import sys; import twicc.providers.live_sync; "
        "assert 'twicc.agent' not in sys.modules; assert 'twicc.providers.db_writer' not in sys.modules"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_permission_pending_blocks_a_previous_end_before_hook_await():
    async def scenario():
        agent = make_agent()
        agent.observe_jsonl_observations((observation("user_message", "user"),
            observation("final", "answer"), observation("end")))
        stale = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()
        async def stale_drop(_):
            entered.set()
            await release.wait()
            return False
        agent._is_stale_drop = stale_drop
        agent._schedule_gui_expiry = lambda _: None
        task = asyncio.create_task(agent.on_permission_request({
            "tool_name": "AskUserQuestion", "tool_input": {}}, "nonce"))
        await asyncio.wait_for(entered.wait(), 2)
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.claim_due(stale, stale.deadline + 5) is None
        release.set()
        assert await task is True
        assert "nonce" in agent._pending_requests
        agent._janitor_files = AsyncMock()
        agent._clear_all_pendings("reap")
        assert agent.completion_controller.candidate() is not None
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_manual_compaction_finishes_only_its_command_turn():
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("command"), observation("compaction")))
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().ordinal == 0
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("compaction"), observation("activity", "model")))
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.completion_controller.candidate() is None
    asyncio.run(scenario())


def test_candidate_interrupt_needs_no_tmux_control(monkeypatch):
    from twicc.providers.claude_code.agent.hybrid import agent as module
    async def scenario():
        agent = make_agent()
        agent.observe_jsonl_observations((observation("user_message", "user"),
            observation("final", "answer"), observation("end")))
        monkeypatch.setattr(module.hybrid_tmux, "composer_ready", lambda _: pytest.fail("candidate reaches tmux"))
        assert await agent.soft_interrupt() is True
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == "ended"
    asyncio.run(scenario())


def test_hybrid_publication_can_await_nested_death():
    from twicc.agent.base_agent import BaseAgent
    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        agent._notify_state_change = BaseAgent._notify_state_change.__get__(agent)
        async def changed(current):
            if current.state == AgentState.USER_TURN:
                # Avoid constructing the known cancellation cycle in the RED run.
                if asyncio.current_task() in current._completion_tasks:
                    return
                await current._transition_to_dead()
        agent._state_change_callback = changed
        manager.observe_hybrid_jsonl_signals(agent.session_id, signals(
            observation("command"), observation("command_ack")))
        await asyncio.sleep(0)
        assert agent.state == AgentState.DEAD
        await asyncio.sleep(0)
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind,outcome", [("interrupted", "ended"), ("provider_error", "provider_error")])
def test_exceptional_publication_persists_journal_with_process_state(monkeypatch, kind, outcome):
    from django.utils import timezone
    from twicc.core.models import LogicalTurn, ProcessRun, Project, Session
    from twicc.providers import db_writer
    session = Session.objects.create(id="hybrid-completion", project=Project.objects.create(id="project"),
                                     provider="claude_code", hybrid=True)
    now = timezone.now()
    process = ProcessRun.objects.create(session_id=session.id, provider="claude_code", started_at=now,
        state="assistant_turn", last_state_change_at=now)

    async def scenario():
        from twicc.agent.base_agent import BaseAgent
        monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
        monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        agent.process_run = process
        agent._old_runs_purged = True
        agent._notify_state_change = BaseAgent._notify_state_change.__get__(agent)
        agent._state_change_callback = manager._on_state_change
        manager._agents[agent.session_id] = agent
        manager._broadcast_info = AsyncMock()
        manager._flush_pending_title = AsyncMock()
        manager._apply_pending_settings = AsyncMock()
        manager._bind_completion_callbacks(agent)
        await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
            observation("user_message", "user"), observation("final", "answer"), observation(kind)))
        await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(LogicalTurn.objects.get, session_id=session.id)
        saved_process = await asyncio.to_thread(ProcessRun.objects.get, pk=process.pk)
        assert row.outcome == outcome
        assert row.first_ref == {"kind": "claude_uuid", "native_id": "user", "turn_id": None}
        assert row.final_refs == [{"kind": "claude_uuid", "native_id": "answer", "turn_id": None}]
        assert saved_process.state == "user_turn"
        assert agent.state == AgentState.USER_TURN
    asyncio.run(scenario())


def test_first_paste_does_not_replace_a_newer_confirmed_state(monkeypatch):
    from twicc.providers.claude_code.agent.hybrid import agent as module
    async def scenario():
        agent = make_agent()
        agent._materialize_attachments = AsyncMock(return_value="prompt")
        agent._wait_until_composer_ready = AsyncMock(return_value=True)
        monkeypatch.setattr(module.hybrid_tmux, "paste_text", lambda *args: None)
        async def delivered():
            agent.observe_jsonl_observations((observation("user_message", "user"),
                observation("final", "answer"), observation("end")))
            ticket = agent.completion_controller.candidate()
            agent.completion_controller.claim_due(ticket, ticket.deadline)
            agent._set_state(AgentState.USER_TURN)
        agent._notify_delivered = delivered
        await agent._first_paste("prompt", None, None)
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == "normal"
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("earlier_final", [False, True])
def test_assistant_command_output_prefix_keeps_its_native_final(earlier_final):
    from twicc.providers.claude_code.compute import get_compute

    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        records = [{"type": "user", "uuid": "prompt", "message": {
            "role": "user", "content": "Explain the command output tags."}}]
        if earlier_final:
            records.extend([
                {"type": "assistant", "uuid": "intermediate", "message": {
                    "role": "assistant", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "An earlier answer."}]}},
                {"type": "system", "subtype": "turn_duration"},
            ])
        records.extend([
            {"type": "assistant", "uuid": "conclusion", "message": {
                "role": "assistant", "stop_reason": "end_turn", "content": [{
                    "type": "text", "text": "<local-command-stdout>Example output</local-command-stdout>"}]}},
            {"type": "system", "subtype": "turn_duration"},
        ])
        for line, record in enumerate(records, 1):
            events = get_compute().extract_completion_observations(record, line)
            await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(*events))
        ticket = agent.completion_controller.candidate()
        assert ticket is not None
        closed = agent.completion_controller.claim_due(ticket, ticket.deadline)
        assert closed is not None
        assert closed.outcome == "normal"
        assert closed.final_refs[-1:] == (NativeMessageRef("claude_uuid", "conclusion"),)
        if earlier_final:
            assert closed.final_refs == (NativeMessageRef("claude_uuid", "intermediate"),
                                         NativeMessageRef("claude_uuid", "conclusion"))
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("during_root_turn", [False, True])
def test_native_user_command_ack_keeps_its_command_semantics(during_root_turn):
    from twicc.providers.claude_code.compute import get_compute

    async def scenario():
        manager, agent = ClaudeCodeAgentManager(), make_agent()
        manager._agents[agent.session_id] = agent
        if during_root_turn:
            await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(
                observation("user_message", "prompt")))
        records = [
            {"type": "user", "uuid": "command", "message": {
                "role": "user", "content": "<command-name>/rename</command-name>"}},
            {"type": "user", "uuid": "ack", "message": {
                "role": "user", "content": "<local-command-stdout>Session renamed to: Example</local-command-stdout>"}},
        ]
        for line, record in enumerate(records, 1):
            events = get_compute().extract_completion_observations(record, line)
            await manager.handle_hybrid_jsonl_signals(agent.session_id, signals(*events))
        expected = AgentState.ASSISTANT_TURN if during_root_turn else AgentState.USER_TURN
        assert agent.state == expected
        assert agent.completion_controller.snapshot().final_refs == ()
        assert agent.completion_controller.candidate() is None
    asyncio.run(scenario())
