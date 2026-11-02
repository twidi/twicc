"""Publication admission, commit, stale-state, and reentrant lifecycle contracts."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.utils import timezone

from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import LogicalTurn, ProcessRun, Project, Session, TimelineEvent, TimelineKind
from twicc.providers.helpers import AgentSettings


class Agent(BaseAgent):
    provider = Provider.CODEX

    def get_pid(self):
        return None


class Manager(BaseAgentManager):
    provider = Provider.CODEX


@pytest.fixture
def bridge(transactional_db, monkeypatch):
    from twicc.providers import db_writer

    monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    project = Project.objects.create(id="completion-p", directory="/tmp/completion")
    session = Session.objects.create(id="completion-s", project=project, provider="codex")
    now = timezone.now()
    agent = Agent(session.id, project.id, "/tmp", AgentSettings())
    agent.process_run = ProcessRun.objects.create(
        session_id=session.id, provider="codex", started_at=now,
        last_state_change_at=now, state=AgentState.ASSISTANT_TURN.value,
    )
    manager = Manager()
    manager._agents[session.id] = agent
    manager._flush_pending_title = AsyncMock()
    manager._broadcast_info = AsyncMock()
    manager._update_session_stopped_at = AsyncMock()
    return manager, agent


def open_candidate(manager, agent):
    manager._bind_completion_callbacks(agent)
    agent._completion_enabled = True
    agent._set_state(AgentState.ASSISTANT_TURN)
    controller = agent.completion_controller
    controller.activity(0.0)
    controller.provider_ended(0.0)
    return controller.candidate()


@pytest.mark.parametrize("ingress", ["activity", "input", "awaiting"])
def test_activity_before_writer_admission_suppresses_completion(bridge, ingress):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(factory, **kwargs):
            entered.set()
            await release.wait()
            return await factory()

        with patch("twicc.agent.base_manager.run_under_db_write_lock", side_effect=blocked):
            confirmation = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await asyncio.wait_for(entered.wait(), timeout=2)
            now = asyncio.get_running_loop().time()
            if ingress == "activity":
                agent.completion_controller.activity(now)
            elif ingress == "input":
                agent.completion_controller.input_pending("input", now)
            else:
                agent.completion_controller.set_awaiting(True, now)
            release.set()
            await confirmation
        assert agent.state == AgentState.ASSISTANT_TURN
        manager._broadcast_info.assert_not_awaited()
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())

    asyncio.run(scenario())


def test_valid_claim_commits_before_publication(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)

        async def broadcast(info):
            row = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
            process = await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)
            assert row.outcome == "normal"
            assert process.state == AgentState.USER_TURN.value
            assert info.state == AgentState.USER_TURN

        manager._broadcast_info.side_effect = broadcast
        await manager._confirm_completion(agent, ticket)
        manager._broadcast_info.assert_awaited_once()
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        assert agent.completion_controller.snapshot().outcome == "normal"

    asyncio.run(scenario())


def test_new_activity_after_claim_keeps_old_journal_but_suppresses_old_live_state(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        original = manager._persist_completion_admitted
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(agent_arg, snapshot, *, state=None):
            result = await original(agent_arg, snapshot, state=state)
            entered.set()
            await release.wait()
            return result

        with patch.object(manager, "_persist_completion_admitted", side_effect=blocked):
            confirmation = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await asyncio.wait_for(entered.wait(), timeout=2)
            agent._completion_activity()
            next_id = agent.completion_controller.snapshot().turn_id
            newer_publication = asyncio.create_task(agent._notify_state_change())
            release.set()
            await asyncio.gather(confirmation, newer_publication)
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert next_id != ticket.turn_id
        assert (await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)).outcome == "normal"
        assert agent.state == AgentState.ASSISTANT_TURN
        states = [call.args[0].state for call in manager._broadcast_info.await_args_list]
        assert states == [AgentState.ASSISTANT_TURN]

    asyncio.run(scenario())


def test_persistence_rejection_never_publishes_success(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        assert agent.state == AgentState.ASSISTANT_TURN
        manager._broadcast_info.assert_not_awaited()

    asyncio.run(scenario())


def test_completion_and_process_transition_roll_back_together(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=RuntimeError("write rejected")):
            await manager._confirm_completion(agent, ticket)
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        row = await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)
        assert row.state == AgentState.ASSISTANT_TURN.value
        manager._broadcast_info.assert_not_awaited()

    asyncio.run(scenario())


def test_completion_settings_restart_drains_dead_callback(bridge):
    from twicc.providers.claude_code.agent.manager import ClaudeCodeAgentManager

    async def scenario():
        _, agent = bridge
        manager = ClaudeCodeAgentManager()
        manager._agents[agent.session_id] = agent
        manager._broadcast_info = AsyncMock()
        manager._update_session_stopped_at = AsyncMock()
        manager._flush_pending_title = AsyncMock()
        agent._old_runs_purged = True
        ticket = open_candidate(manager, agent)

        async def apply_settings(current):
            current.kill_reason = "apply-settings"
            await current._transition_to_dead()
            await current.wait_for_dead()

        with patch.object(manager, "_apply_pending_settings", side_effect=apply_settings):
            await asyncio.wait_for(manager._confirm_completion(agent, ticket), timeout=2)
        assert agent.state == AgentState.DEAD
        assert agent._dead_callback_done_event.is_set()
        states = [call.args[0].state for call in manager._broadcast_info.await_args_list]
        assert states == [AgentState.USER_TURN, AgentState.DEAD]
        assert agent.process_run is None

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_candidate_only_interrupt_ends_without_sdk_call(provider):
    from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
    from twicc.providers.codex.agent.agent import CodexAgent

    async def scenario():
        cls = ClaudeCodeAgent if provider == "claude_code" else CodexAgent
        extra = (AsyncMock(), AsyncMock(), AsyncMock()) if provider == "claude_code" else (Mock(), None)
        agent = cls("interrupt", "p", "/tmp", AgentSettings(), *extra, ephemeral=True)
        agent._state_change_callback = AsyncMock()
        agent._completion_activity()
        agent._completion_provider_ended()
        candidate = agent.completion_controller.candidate()
        sdk_interrupt = AsyncMock()
        if provider == "claude_code":
            agent._client = type("Client", (), {"interrupt": sdk_interrupt})()
        else:
            agent._current_turn = None
        assert await agent.soft_interrupt() is True
        assert agent.state == AgentState.USER_TURN
        assert agent.completion_controller.snapshot().outcome == "ended"
        assert agent.completion_controller.claim_due(candidate, candidate.deadline + 1) is None
        sdk_interrupt.assert_not_awaited()
        assert agent._completion_timer_task is None
        agent._state_change_callback.assert_awaited_once()

    asyncio.run(scenario())


def test_direct_dead_transition_cancels_candidate():
    async def scenario():
        agent = Agent("dead", "p", "/tmp", AgentSettings(), ephemeral=True)
        agent._completion_activity()
        agent._completion_provider_ended()
        ticket = agent.completion_controller.candidate()
        agent._set_state(AgentState.DEAD)
        assert agent.completion_controller.snapshot().outcome == "ended"
        assert agent.completion_controller.claim_due(ticket, ticket.deadline + 1) is None
        assert agent._completion_timer_task is None
        closed = agent.completion_controller.snapshot()
        agent._completion_activity()
        assert agent.state == AgentState.DEAD
        assert agent.completion_controller.snapshot() == closed

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_actual_root_work_still_uses_sdk_interrupt(provider):
    from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
    from twicc.providers.codex.agent.agent import CodexAgent

    async def scenario():
        cls = ClaudeCodeAgent if provider == "claude_code" else CodexAgent
        extra = (AsyncMock(), AsyncMock(), AsyncMock()) if provider == "claude_code" else (Mock(), None)
        agent = cls("interrupt-work", "p", "/tmp", AgentSettings(), *extra, ephemeral=True)
        agent._completion_activity()
        sdk = Mock(interrupt=AsyncMock())
        if provider == "claude_code":
            agent._client = sdk
        else:
            agent._current_turn = sdk
        assert await agent.soft_interrupt() is True
        sdk.interrupt.assert_awaited_once()
        assert agent.completion_controller.snapshot().outcome is None
        assert agent.state == AgentState.ASSISTANT_TURN

    asyncio.run(scenario())


def test_journal_opening_scope_stays_immutable_after_initial_write(bridge):
    async def scenario():
        manager, agent = bridge
        manager._bind_completion_callbacks(agent)
        agent._completion_source_floor = 3
        agent._completion_activity()
        await asyncio.gather(*tuple(agent._completion_tasks))
        first = agent.completion_controller.snapshot()
        agent._completion_source_floor = 40
        agent._completion_storage_incarnation = "new-observation"
        agent._completion_provider_ended()
        await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=first.turn_id)
        assert row.source_floor == 3
        assert row.storage_incarnation is None
        assert row.revision == agent.completion_controller.snapshot().revision
        agent._cancel_completion_timer()

    asyncio.run(scenario())


def test_candidate_interrupt_commits_closed_journal_and_user_state_together(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        process_pk = agent.process_run.pk
        original = manager._persist_process_run_transition_admitted
        observed = []

        def check_transaction(current, state):
            from django.db import connection

            if state == AgentState.USER_TURN:
                observed.append(connection.in_atomic_block)
                assert LogicalTurn.objects.get(pk=ticket.turn_id).outcome == "ended"
            original(current, state)

        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=check_transaction):
            assert await agent._completion_interrupt_candidate() is True
        assert observed == [True]
        row = await asyncio.to_thread(ProcessRun.objects.get, pk=process_pk)
        assert row.state == AgentState.USER_TURN.value
        manager._broadcast_info.assert_awaited_once()

    asyncio.run(scenario())


def test_root_activity_burst_coalesces_persistence_and_preserves_terminal_order():
    async def scenario():
        manager = Manager()
        agent = Agent("burst", "p", "/tmp", AgentSettings(), ephemeral=True)
        manager._agents[agent.session_id] = agent
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def persist(current, snapshot):
            entered.set()
            await release.wait()
            writes.append(snapshot)
            return True

        manager._persist_completion = persist
        manager._bind_completion_callbacks(agent)
        agent._completion_activity()
        await asyncio.wait_for(entered.wait(), timeout=2)
        state_revision = agent._state_revision
        state_changed_at = agent.state_changed_at
        for _ in range(1000):
            agent._completion_activity()
        assert agent._state_revision == state_revision
        assert agent.state_changed_at == state_changed_at
        assert len(agent._completion_tasks) == 1
        closed = agent._completion_terminate("ended")
        agent._completion_activity()
        newest = agent.completion_controller.snapshot()
        assert len(agent._completion_tasks) == 1
        release.set()
        await asyncio.gather(*tuple(agent._completion_tasks))
        assert len(writes) <= 3
        assert writes[-2:] == [closed, newest]

    asyncio.run(scenario())


def test_death_sets_state_before_waiting_for_persistence_and_drains_on_cancel():
    async def scenario():
        agent = Agent("death-drain", "p", "/tmp", AgentSettings(), ephemeral=True)
        entered, release = asyncio.Event(), asyncio.Event()

        async def persist(current, snapshot):
            entered.set()
            await release.wait()
            return True

        agent._completion_persist_callback = persist
        agent._state_change_callback = AsyncMock()
        agent._completion_activity()
        await asyncio.wait_for(entered.wait(), timeout=2)
        death = asyncio.create_task(agent._transition_to_dead())
        try:
            await asyncio.wait_for(agent._dead_event.wait(), timeout=2)
            death.cancel()
            assert not agent._dead_callback_done_event.is_set()
        finally:
            release.set()
            try:
                await death
            except asyncio.CancelledError:
                pass
        assert agent._dead_callback_done_event.is_set()
        agent._state_change_callback.assert_awaited_once()
        assert agent.completion_controller.snapshot().outcome == "ended"

    asyncio.run(scenario())
