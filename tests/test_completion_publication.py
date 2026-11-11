"""Publication admission, commit, stale-state, and reentrant lifecycle contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import LogicalTurn, ProcessRun, Project, Session, SessionItem, TimelineEvent, TimelineKind
from twicc.providers.helpers import AgentSettings
from tests import content_storage_helpers

content_row_factory = content_storage_helpers.content_row_factory


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


@pytest.mark.parametrize("policy", ["visible", "muted", "hidden", "resync"])
def test_public_completion_sequence_waits_for_confirmation_and_exact_answer(bridge, content_row_factory, policy):
    from twicc import external_notifications
    from twicc.asgi import broadcast_process_state
    from twicc.core.services.logical_turn_answers import resolve_logical_answer

    manager, agent = bridge
    incarnation = str(uuid4())
    Session.objects.filter(pk=agent.session_id).update(
        storage_incarnation=incarnation, history_epoch=1,
        hidden=policy == "hidden", mute_on_user_turn=policy == "muted",
    )
    agent._completion_storage_incarnation = incarnation
    agent._completion_history_epoch = 1
    frames, notifications = [], []

    async def send(group, event):
        assert group == "updates"
        frames.append(event["data"])

    def index(identity, line, text):
        parsed = {"type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": agent.session_id, "turn_id": "native-turn",
            "item": {"type": "AgentMessage", "id": identity, "phase": "final_answer",
                     "content": [{"type": "Text", "text": text}]},
        }}
        content = content_row_factory(orjson.dumps(parsed).decode(), False)
        SessionItem.objects.create(
            session_id=agent.session_id, line_num=line, kind=ItemKind.ASSISTANT_MESSAGE,
            content="", current_content=content,
        )

    async def scenario():
        manager._broadcast_info = Manager._broadcast_info.__get__(manager)
        manager._broadcast_callback = broadcast_process_state
        open_candidate(manager, agent)
        controller = agent.completion_controller
        intermediate = NativeMessageRef("codex_item", "intermediate", "native-turn")
        final = NativeMessageRef("codex_item", "final", "native-turn")
        controller.activity(0.0, first_ref=intermediate)
        controller.provider_ended(0.0, final_ref=intermediate)
        stale = controller.candidate()
        await manager._broadcast_info(agent.get_info())
        controller.activity(0.2)
        controller.provider_ended(0.3, final_ref=final)
        ticket = controller.candidate()
        await manager._persist_completion(agent, controller.snapshot())

        # The first physical answer indexes late, while the logical turn stays open.
        await asyncio.to_thread(index, "intermediate", 2, "intermediate answer")
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert (await asyncio.to_thread(resolve_logical_answer, row, cursor=0)).status == "pending"
        await manager._confirm_completion(agent, stale)
        assert agent.state == AgentState.ASSISTANT_TURN
        assert all(frame["state"] != "user_turn" for frame in frames)
        assert notifications == []
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 0

        await manager._confirm_completion(agent, ticket)
        completion_frames = [frame for frame in frames if frame["state"] == "user_turn"]
        assert len(completion_frames) == (0 if policy == "hidden" else 1)
        if completion_frames:
            assert completion_frames[0]["mute_on_user_turn"] == (policy == "muted")
        assert len(notifications) == (0 if policy in {"hidden", "muted"} else 1)
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        await asyncio.to_thread(row.refresh_from_db)
        assert row.outcome == "normal"
        # Confirmation does not turn a missing final identity into an intermediate reply.
        assert (await asyncio.to_thread(resolve_logical_answer, row, cursor=0)).status == "pending"
        await asyncio.to_thread(index, "final", 3, "final answer")
        answer = await asyncio.to_thread(resolve_logical_answer, row, cursor=0)
        assert answer.status == "replied"
        assert answer.message.text == "final answer"

        if policy == "resync":
            await broadcast_process_state(agent.get_info(), resync=True)
            assert frames[-1]["resync"] is True
            assert len(notifications) == 1
            assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1

        completed = await asyncio.to_thread(lambda: list(TimelineEvent.objects.order_by("id").values()))
        agent._background_work_callback = manager._persist_background_work
        shell = {"subagents": 0, "shells": 1, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}
        for work in (shell, {**shell, "shells": 2}, None):
            agent.current_background_work = lambda: work
            await agent._publish_background_work()
            process = await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)
            assert process.background_work_in_progress == work
            assert process.state == "user_turn"
        assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.order_by("id").values())) == completed
        metadata_frames = [frame for frame in frames if frame["type"] == "process_background_work"]
        assert len(metadata_frames) == (0 if policy == "hidden" else 3)
        if metadata_frames:
            assert metadata_frames[-1]["background_work_in_progress"] is None
        assert len(notifications) == (0 if policy in {"hidden", "muted"} else 1)

    settings = {"externalNotificationTargets": [{
        "url": "json://example.test", "enabled": True, "tested": True, "awayOnly": False,
    }]}
    external_notifications._last_seen.pop(agent.session_id, None)
    try:
        with patch("twicc.asgi.get_channel_layer", return_value=SimpleNamespace(group_send=send)), \
                patch("twicc.agent.base_agent.get_channel_layer", return_value=SimpleNamespace(group_send=send)), \
                patch.object(external_notifications, "read_synced_settings", return_value=settings), \
                patch.object(external_notifications, "_send", new=lambda urls, title, body: (urls, title, body)), \
                patch.object(external_notifications, "_spawn", new=notifications.append):
            asyncio.run(scenario())
    finally:
        external_notifications._last_seen.pop(agent.session_id, None)


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

        def check_transaction(current, state, **kwargs):
            from django.db import connection

            if state == AgentState.USER_TURN:
                observed.append(connection.in_atomic_block)
                assert LogicalTurn.objects.get(pk=ticket.turn_id).outcome == "ended"
            original(current, state, **kwargs)

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


@pytest.mark.parametrize("outcome", ["normal", "ended"])
@pytest.mark.parametrize("failure", ["rejected", "rollback"])
def test_failed_completion_recovers_transactionally_after_another_fact(bridge, outcome, failure):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        process_pk = agent.process_run.pk
        if failure == "rejected":
            rejection = patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False))
        else:
            rejection = patch.object(manager, "_persist_process_run_transition_admitted", side_effect=RuntimeError("rollback"))
        with rejection:
            if outcome == "normal":
                await manager._confirm_completion(agent, ticket)
            else:
                assert await agent._completion_interrupt_candidate() is False
        claimed = agent.completion_controller.snapshot()
        assert claimed.outcome == outcome
        assert agent.completion_controller.candidate() is None
        assert agent.state == AgentState.ASSISTANT_TURN
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=process_pk)).state == AgentState.ASSISTANT_TURN.value
        manager._broadcast_info.assert_not_awaited()

        agent._completion_holds_changed(frozenset())
        assert agent._completion_timer_task is not None
        await asyncio.wait_for(agent._completion_timer_task, timeout=2)
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert agent.completion_controller.snapshot() == claimed
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert row.outcome == outcome
        assert row.revision == claimed.revision
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=process_pk)).state == AgentState.USER_TURN.value
        manager._broadcast_info.assert_awaited_once()
        assert manager._broadcast_info.await_args.args[0].state == AgentState.USER_TURN
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1

    asyncio.run(scenario())


def test_failed_confirmation_recovery_keeps_new_interval_active_and_records_both_boundaries(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        closed = agent.completion_controller.snapshot()
        agent._completion_activity()
        next_snapshot = agent.completion_controller.snapshot()
        assert next_snapshot.ordinal == closed.ordinal + 1
        await asyncio.wait_for(agent._completion_timer_task, timeout=2)
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        old_row = await asyncio.to_thread(LogicalTurn.objects.get, pk=closed.turn_id)
        new_row = await asyncio.to_thread(LogicalTurn.objects.get, pk=next_snapshot.turn_id)
        assert old_row.outcome == "normal"
        assert new_row.outcome is None
        assert old_row.position < new_row.position
        assert agent.completion_controller.snapshot() == next_snapshot
        assert agent.state == AgentState.ASSISTANT_TURN
        process = await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)
        assert process.state == AgentState.ASSISTANT_TURN.value
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED],
        ).order_by("id").values_list("kind", flat=True)))
        assert boundaries == [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED]
        manager._broadcast_info.assert_not_awaited()
        await agent._notify_state_change()
        states = [call.args[0].state for call in manager._broadcast_info.await_args_list]
        assert states == [AgentState.ASSISTANT_TURN]

    asyncio.run(scenario())


@pytest.mark.parametrize("initial_claim", [False, True])
def test_new_activity_does_not_cancel_an_admitted_completion_retry(bridge, initial_claim):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        if not initial_claim:
            with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
                await manager._confirm_completion(agent, ticket)
        entered, release = asyncio.Event(), asyncio.Event()
        original = manager._persist_completion_admitted

        async def blocked(current, snapshot, *, state=None):
            entered.set()
            await release.wait()
            return await original(current, snapshot, state=state)

        with patch.object(manager, "_persist_completion_admitted", side_effect=blocked):
            if initial_claim:
                agent._completion_timer_candidate = ticket
                agent._completion_timer_task = asyncio.create_task(agent._completion_wait(ticket))
            else:
                agent._completion_holds_changed(frozenset())
            retry = agent._completion_timer_task
            await asyncio.wait_for(entered.wait(), timeout=2)
            agent._completion_activity()
            latest = asyncio.create_task(agent._notify_state_change())
            try:
                assert retry.cancelling() == 0
            finally:
                release.set()
                await asyncio.gather(retry, latest, return_exceptions=True)
                if agent._completion_tasks:
                    await asyncio.gather(*tuple(agent._completion_tasks))
        assert not agent._completion_pending_publications
        assert (await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)).outcome == "normal"
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)).state == AgentState.ASSISTANT_TURN.value
        assert [call.args[0].state for call in manager._broadcast_info.await_args_list] == [AgentState.ASSISTANT_TURN]

    asyncio.run(scenario())


@pytest.mark.parametrize("initial_timer", [False, True])
def test_failed_confirmation_retries_without_another_provider_fact(bridge, initial_timer):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        delay_started, release_delay = asyncio.Event(), asyncio.Event()

        async def controlled_delay(seconds):
            if seconds == 0.0:
                return
            assert seconds == 1.0
            delay_started.set()
            await release_delay.wait()

        with patch("twicc.agent.base_agent.asyncio.sleep", side_effect=controlled_delay):
            with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
                if initial_timer:
                    agent._completion_timer_candidate = ticket
                    agent._completion_timer_task = asyncio.create_task(agent._completion_wait(ticket))
                    await agent._completion_timer_task
                else:
                    await manager._confirm_completion(agent, ticket)
            await asyncio.wait_for(delay_started.wait(), timeout=2)
            release_delay.set()
            await asyncio.wait_for(agent._completion_timer_task, timeout=2)
        assert agent.state == AgentState.USER_TURN
        assert not agent._completion_pending_publications
        assert (await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)).outcome == "normal"
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)).state == AgentState.USER_TURN.value
        manager._broadcast_info.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("new_activity", [False, True])
def test_failed_confirmation_then_stop_reconciles_claim_before_process_deletion(bridge, new_activity):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        process_pk = agent.process_run.pk
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        claimed = agent.completion_controller.snapshot()
        if new_activity:
            agent._completion_activity()
        latest = agent.completion_controller.snapshot()
        original = manager._persist_process_run_transition_admitted
        checks = []

        def check_before_delete(current, state, **kwargs):
            if state == AgentState.DEAD:
                from django.db import connection

                checks.append(connection.in_atomic_block)
                assert LogicalTurn.objects.get(pk=claimed.turn_id).outcome == "normal"
                if new_activity:
                    assert LogicalTurn.objects.get(pk=latest.turn_id).outcome == "ended"
            original(current, state, **kwargs)

        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=check_before_delete):
            agent.kill_reason = "manual"
            agent.mark_stopping()
            await asyncio.wait_for(agent._transition_to_dead(), timeout=2)
        assert checks == [True]
        old_row = await asyncio.to_thread(LogicalTurn.objects.get, pk=claimed.turn_id)
        assert old_row.outcome == "normal"
        if new_activity:
            new_row = await asyncio.to_thread(LogicalTurn.objects.get, pk=latest.turn_id)
            assert new_row.outcome == "ended"
            assert old_row.position < new_row.position
        assert not await asyncio.to_thread(ProcessRun.objects.filter(pk=process_pk).exists)
        assert not agent._completion_pending_publications
        assert agent.state == AgentState.DEAD
        assert agent._dead_callback_done_event.is_set()
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED],
        ).order_by("id").values_list("kind", flat=True)))
        expected = [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED]
        if new_activity:
            expected += [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED]
        assert boundaries == expected
        assert [call.args[0].state for call in manager._broadcast_info.await_args_list] == [AgentState.DEAD]

    asyncio.run(scenario())


@pytest.mark.parametrize("death", [False, True])
def test_recovery_rollback_restores_process_mirror_before_retry(bridge, death):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        agent._cancel_completion_timer()
        claimed = agent.completion_controller.snapshot()
        agent._completion_activity()
        agent._cancel_completion_timer()
        if death:
            agent.mark_stopping()
            agent._set_state(AgentState.DEAD)
        original = manager._persist_process_run_transition_admitted

        def reject_reconciliation_transition(current, state, **kwargs):
            if state == (AgentState.DEAD if death else AgentState.ASSISTANT_TURN):
                raise RuntimeError("reconciliation transition rolls back")
            original(current, state, **kwargs)

        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=reject_reconciliation_transition):
            if death:
                await manager._persist_process_run_transition(agent, AgentState.DEAD)
            else:
                await manager._retry_completion(agent, claimed)
        assert agent.process_run.state == AgentState.ASSISTANT_TURN.value
        row = await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)
        assert row.state == agent.process_run.state
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert await asyncio.to_thread(TimelineEvent.objects.count) == 0
        if death:
            await manager._persist_process_run_transition(agent, AgentState.DEAD)
        else:
            await manager._retry_completion(agent, claimed)
            if agent._completion_tasks:
                await asyncio.gather(*tuple(agent._completion_tasks))
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED],
        ).order_by("id").values_list("kind", flat=True)))
        expected = [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED]
        if death:
            expected.append(TimelineKind.TURN_ENDED)
        assert boundaries == expected
        assert (await asyncio.to_thread(LogicalTurn.objects.get, pk=claimed.turn_id)).outcome == "normal"
        agent._cancel_completion_timer()

    asyncio.run(scenario())


def test_cancelled_committed_retry_is_idempotent_and_unblocks_later_interval(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        entered, release = asyncio.Event(), asyncio.Event()
        original = manager._persist_completion_admitted

        async def committed_then_blocked(current, snapshot, *, state=None):
            result = await original(current, snapshot, state=state)
            entered.set()
            await release.wait()
            return result

        with patch.object(manager, "_persist_completion_admitted", side_effect=committed_then_blocked):
            agent._completion_holds_changed(frozenset())
            retry = agent._completion_timer_task
            await asyncio.wait_for(entered.wait(), timeout=2)
            retry.cancel()
            agent._completion_activity()
            latest = agent.completion_controller.snapshot()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await retry
        agent._completion_holds_changed(frozenset())
        await asyncio.wait_for(agent._completion_timer_task, timeout=2)
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert not agent._completion_pending_publications
        first = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        second = await asyncio.to_thread(LogicalTurn.objects.get, pk=latest.turn_id)
        assert first.outcome == "normal"
        assert second.outcome is None
        assert first.position < second.position
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)).state == AgentState.ASSISTANT_TURN.value
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED],
        ).order_by("id").values_list("kind", flat=True)))
        assert boundaries == [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED]
        manager._broadcast_info.assert_not_awaited()
        agent._cancel_completion_timer()

    asyncio.run(scenario())


@pytest.mark.parametrize("older_rejected_again", [False, True])
def test_candidate_interrupt_preserves_an_older_admitted_retry(bridge, older_rejected_again):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        with patch.object(manager, "_persist_completion_admitted", new=AsyncMock(return_value=False)):
            await manager._confirm_completion(agent, ticket)
        entered, release, interrupt_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = manager._persist_completion_admitted
        original_end = manager._end_completion
        attempts = 0

        async def committed_then_blocked(current, snapshot, *, state=None):
            nonlocal attempts
            if snapshot.turn_id == ticket.turn_id:
                attempts += 1
                if attempts == 1:
                    result = False if older_rejected_again else await original(current, snapshot, state=state)
                    entered.set()
                    await release.wait()
                    return result
            return await original(current, snapshot, state=state)

        async def end(current, snapshot):
            interrupt_entered.set()
            return await original_end(current, snapshot)

        agent._completion_end_callback = end
        with patch.object(manager, "_persist_completion_admitted", side_effect=committed_then_blocked):
            agent._completion_holds_changed(frozenset())
            retry = agent._completion_timer_task
            await asyncio.wait_for(entered.wait(), timeout=2)
            agent._completion_activity()
            latest = agent.completion_controller.snapshot()
            newer_publication = asyncio.create_task(agent._notify_state_change())
            agent._completion_provider_ended()
            interrupt = asyncio.create_task(agent._completion_interrupt_candidate())
            await asyncio.wait_for(interrupt_entered.wait(), timeout=2)
            try:
                assert retry.cancelling() == 0
            finally:
                release.set()
                results = await asyncio.gather(retry, newer_publication, interrupt, return_exceptions=True)
                if agent._completion_tasks:
                    await asyncio.gather(*tuple(agent._completion_tasks))
        assert results[-1] is True
        assert not agent._completion_pending_publications
        first = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        second = await asyncio.to_thread(LogicalTurn.objects.get, pk=latest.turn_id)
        assert first.outcome == "normal"
        assert second.outcome == "ended"
        assert first.position < second.position
        assert agent.state == AgentState.USER_TURN
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)).state == AgentState.USER_TURN.value
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind__in=[TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED],
        ).order_by("id").values_list("kind", flat=True)))
        assert boundaries == [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED]
        states = [call.args[0].state for call in manager._broadcast_info.await_args_list]
        assert states[-1] == AgentState.USER_TURN
        assert states.count(AgentState.USER_TURN) == 1
        assert all(state == AgentState.ASSISTANT_TURN for state in states[:-1])
        agent._cancel_completion_timer()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["normal", "ended", "provider_error"])
def test_expired_live_interval_still_completes_and_next_interval_persists(bridge, outcome):
    """Expiry must not suppress real completion or reopen its journal on stale retries."""
    from datetime import timedelta
    from twicc.logical_turn_cleanup_task import purge_logical_turns

    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        stale = agent.completion_controller.snapshot()
        assert await manager._persist_completion(agent, stale)
        now = timezone.now()
        await asyncio.to_thread(lambda: LogicalTurn.objects.filter(pk=stale.turn_id).update(opened_at=now - timedelta(days=8)))
        assert await asyncio.to_thread(purge_logical_turns, now=now) == 1
        assert not await manager._persist_completion(agent, stale)
        assert agent.state == AgentState.ASSISTANT_TURN
        if outcome == "normal":
            await manager._confirm_completion(agent, ticket)
        else:
            closed = agent.completion_controller.terminate(outcome, asyncio.get_running_loop().time())
            assert await manager._end_completion(agent, closed)
        assert agent.state == AgentState.USER_TURN
        assert not agent._completion_pending_publications
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        agent._completion_activity()
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        latest = agent.completion_controller.snapshot()
        assert latest.turn_id != stale.turn_id
        assert (await asyncio.to_thread(LogicalTurn.objects.get, pk=latest.turn_id)).outcome is None
        assert not await manager._persist_completion(agent, stale)
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 1

    asyncio.run(scenario())


def test_expired_interval_closure_rolls_back_then_retries_once(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        assert await manager._persist_completion(agent, agent.completion_controller.snapshot())
        await asyncio.to_thread(LogicalTurn.objects.all().delete)
        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=RuntimeError("rollback")):
            await manager._confirm_completion(agent, ticket)
        agent._cancel_completion_timer()
        assert agent.state == AgentState.ASSISTANT_TURN
        assert agent.process_run.state == AgentState.ASSISTANT_TURN.value
        assert await asyncio.to_thread(TimelineEvent.objects.count) == 1
        closed = agent.completion_controller.snapshot()
        assert await manager._retry_completion(agent, closed)
        assert agent.state == AgentState.USER_TURN
        assert await asyncio.to_thread(LogicalTurn.objects.count) == 0
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1

    asyncio.run(scenario())


def test_cancelled_committed_expired_closure_does_not_repeat_public_boundaries(bridge):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        assert await manager._persist_completion(agent, agent.completion_controller.snapshot())
        await asyncio.to_thread(LogicalTurn.objects.all().delete)
        original = manager._persist_completion_admitted

        entered, release = asyncio.Event(), asyncio.Event()

        async def committed_then_blocked(current, snapshot, *, state=None):
            result = await original(current, snapshot, state=state)
            entered.set()
            await release.wait()
            return result

        with patch.object(manager, "_persist_completion_admitted", side_effect=committed_then_blocked):
            confirmation = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await asyncio.wait_for(entered.wait(), timeout=2)
            confirmation.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await confirmation
        agent._cancel_completion_timer()
        claimed = agent.completion_controller.snapshot()
        agent._completion_activity()
        agent._cancel_completion_timer()
        assert not await manager._retry_completion(agent, claimed)
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        boundaries = await asyncio.to_thread(lambda: list(TimelineEvent.objects.order_by("id").values_list("kind", flat=True)))
        assert boundaries == [TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED, TimelineKind.TURN_STARTED]
        assert not agent._completion_pending_publications
        assert not await asyncio.to_thread(LogicalTurn.objects.filter(pk=claimed.turn_id).exists)
        manager._broadcast_info.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["ended", "provider_error"])
def test_exceptional_boundary_uses_termination_time_after_open_persistence(bridge, outcome):
    from datetime import timedelta

    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        opened_at = timezone.now()
        terminated_at = opened_at + timedelta(seconds=10)
        retried_at = terminated_at + timedelta(seconds=10)
        with patch("django.utils.timezone.now", return_value=opened_at):
            assert await manager._persist_completion(agent, agent.completion_controller.snapshot())
        with patch("django.utils.timezone.now", return_value=terminated_at):
            closed = agent._completion_terminate(outcome)
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        with patch.object(manager, "_persist_process_run_transition_admitted", side_effect=RuntimeError("rollback")):
            assert not await manager._end_completion(agent, closed)
        agent._cancel_completion_timer()
        with patch("django.utils.timezone.now", return_value=retried_at):
            assert await manager._retry_completion(agent, closed)
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        boundary = await asyncio.to_thread(lambda: TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED))
        assert row.closed_at == terminated_at
        assert boundary.occurred_at == terminated_at
        assert agent._completion_boundary_times[ticket.turn_id] == terminated_at
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize("winner", ["replacement", "stop", "controller_run", "run_id"])
def test_worker_entry_race_preserves_history_without_obsolete_process_write(bridge, winner):
    import threading
    from twicc.agent.completion import CompletionController
    from twicc.core.models import TimelineDecision
    from twicc.core.services.timeline_decisions import fulfill_final_message_obligation

    async def scenario():
        manager, agent = bridge
        incarnation = str(uuid4())
        agent._completion_storage_incarnation = incarnation
        agent._completion_history_epoch = 0
        await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(storage_incarnation=incarnation))
        ticket = open_candidate(manager, agent)
        process_id = agent.process_run.pk
        original_transition_time = agent.process_run.last_state_change_at
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def finish_final_before_process_write(*, ending_id):
            result = fulfill_final_message_obligation(ending_id=ending_id)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "Worker must resume after the controlled ingress"
            return result

        with patch("twicc.core.services.timeline_decisions.fulfill_final_message_obligation",
                   side_effect=finish_final_before_process_write):
            completion = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await asyncio.wait_for(entered.wait(), timeout=2)
            if winner == "replacement":
                manager._agents[agent.session_id] = object()
            elif winner == "stop":
                agent.mark_stopping()
            elif winner == "controller_run":
                agent.completion_controller = CompletionController(run_id=str(uuid4()))
            else:
                agent.completion_controller.run_id = str(uuid4())
            release.set()
            await completion
        process = await asyncio.to_thread(ProcessRun.objects.get, pk=process_id)
        assert process.state == AgentState.ASSISTANT_TURN.value
        assert process.last_state_change_at == original_transition_time
        row = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        decision = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind="ending"))
        assert row.outcome == "normal"
        assert decision.status == "accepted" and decision.applied_at is not None and not decision.force_pending
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        manager._broadcast_info.assert_not_awaited()
        if winner == "stop":
            agent.kill_reason = "manual"
            await agent._transition_to_dead()
            assert not await asyncio.to_thread(ProcessRun.objects.filter(pk=process_id).exists)
            assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.PROCESS_ENDED).count()) == 1
            assert [call.args[0].state for call in manager._broadcast_info.await_args_list] == [AgentState.DEAD]
        agent._cancel_completion_timer()
    asyncio.run(scenario())
