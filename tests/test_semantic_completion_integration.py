"""Controlled semantic ending races through the common completion bridge."""

import asyncio
from unittest.mock import patch

from tests.test_timeline_decisions import decision_fixture
from tests.test_timeline_messages import live_session, content_row_factory
from uuid import uuid4

import pytest

from tests.test_completion_publication import bridge, open_candidate
from twicc.agent.semantic_types import Classification
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import LogicalTurn, ProcessRun, Session, TimelineDecision, TimelineEvent, TimelineKind
from twicc.request_origin import RequestOrigin

# Export imported fixtures for pytest discovery.
__all__ = ['bridge', 'content_row_factory', 'decision_fixture', 'live_session']



@pytest.fixture(autouse=True)
def provider(bridge, request):
    manager, agent = bridge
    chosen = getattr(request, 'param', 'codex')
    agent.provider = manager.provider = Provider(chosen)
    incarnation = str(uuid4())
    Session.objects.filter(pk=agent.session_id).update(provider=chosen, storage_incarnation=incarnation, history_epoch=1, file_path='completion-s.jsonl')
    agent._completion_storage_incarnation = incarnation
    agent._completion_history_epoch = 1
    agent.process_run.provider = chosen
    agent.process_run.save(update_fields=['provider'])


def evidence(*args):
    return {'target': {'shells': [{'key': 'shell-1', 'owner': 'root', 'command': 'sleep 100',
            'description': None, 'started_at': None, 'start_line': None}]},
            'shell_refs': [{'key': 'shell-1', 'generation': 1, 'owner_ref': None, 'started_at': None}],
            'previous_messages': [{'role': 'assistant', 'text': 'Done'}], 'tool_evidence': []}


async def pending(manager, agent):
    ticket = open_candidate(manager, agent)
    agent._completion_capture_ending_evidence = evidence
    agent._completion_shells_are_current = lambda context: True
    await manager._confirm_completion(agent, ticket)
    if agent._completion_tasks:
        await asyncio.gather(*tuple(agent._completion_tasks))
    return ticket, agent._completion_pending_ending


@pytest.mark.parametrize('provider', ['codex', 'claude_code'], indirect=True)
def test_shell_decision_gates_general_completion_without_timeline_clients(bridge, provider):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        assert agent.completion_controller.is_open
        assert agent.state == AgentState.ASSISTANT_TURN
        assert (await asyncio.to_thread(ProcessRun.objects.get, pk=agent.process_run.pk)).state == 'assistant_turn'
        manager._broadcast_info.assert_not_awaited()
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
        await manager.apply_ending_decision(agent, context.id, classification=Classification('NO', 'Still working'))
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert agent.completion_controller.candidate() is None
        assert agent.completion_controller.is_open
        turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert turn.outcome is None
        from twicc.core.services.logical_turn_answers import resolve_logical_answer
        assert (await asyncio.to_thread(resolve_logical_answer, turn, cursor=0)).status == 'pending'
        manager._broadcast_info.assert_not_awaited()
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('provider', ['codex', 'claude_code'], indirect=True)
def test_user_send_claims_before_staging_and_binding(bridge, provider):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        token = str(uuid4())
        agent._timeline_input_origins[token] = RequestOrigin('user', 'ui', None, None)
        agent._completion_input_pending(token, staged=True)
        assert agent.completion_controller.snapshot().outcome == 'normal'
        assert ticket.turn_id in agent._completion_pending_publications
        agent._completion_input_pending(token)
        assert agent._completion_inputs[token].turn_id is None
        agent._completion_activity()
        new = agent.completion_controller.snapshot()
        assert new.turn_id != ticket.turn_id
        assert agent._completion_inputs[token].turn_id == new.turn_id
        agent._completion_input_settled(token, 'rejected')
        agent._cancel_completion_timer()
        await manager._retry_completion(agent, agent._completion_pending_publications[ticket.turn_id])
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        old = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert old.closed_at == context.occurred_at
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.status == 'accepted' and row.reason == 'user_input' and row.applied_at is not None
        assert not await manager.apply_ending_decision(agent, context.id, classification=Classification('NO', 'late'))
        assert agent.state == AgentState.ASSISTANT_TURN
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('winner', ['root', 'rejected'])
def test_root_or_rejection_before_user_preserves_open_ownership(bridge, winner):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        if winner == 'root':
            agent._completion_activity()
        else:
            await manager.apply_ending_decision(agent, context.id, classification=Classification('NO', 'blocked'))
        token = str(uuid4())
        agent._completion_input_pending(token)
        assert agent._completion_inputs[token].turn_id == ticket.turn_id
        assert not await manager.apply_ending_decision(agent, context.id, classification=Classification('YES', 'late'))
        assert agent.completion_controller.is_open
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
    asyncio.run(scenario())


def test_agent_origin_does_not_split(bridge):
    async def scenario():
        manager, agent = bridge
        ticket, _ = await pending(manager, agent)
        token = str(uuid4())
        agent._timeline_input_origins[token] = RequestOrigin('agent', 'mcp', None, None)
        agent._completion_input_pending(token)
        assert agent.completion_controller.is_open
        assert agent._completion_inputs[token].turn_id == ticket.turn_id
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
    asyncio.run(scenario())


@pytest.mark.parametrize('winner', ['user', 'root'])
@pytest.mark.parametrize('provider', ['codex', 'claude_code'], indirect=True)
def test_ingress_wins_before_initial_decision_insertion(bridge, provider, winner):
    async def scenario():
        manager, agent = bridge
        ticket = open_candidate(manager, agent)
        agent._completion_capture_ending_evidence = evidence
        entered, release = asyncio.Event(), asyncio.Event()
        from twicc.agent.base_manager import run_under_db_write_lock

        async def blocked(factory, **kwargs):
            entered.set()
            await release.wait()
            return await run_under_db_write_lock(factory, **kwargs)

        with patch('twicc.agent.base_manager.run_under_db_write_lock', side_effect=blocked):
            task = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await entered.wait()
            context = agent._completion_pending_ending
            if winner == 'user':
                token = str(uuid4())
                agent._completion_input_pending(token, staged=True)
                assert agent._completion_pending_publications[ticket.turn_id].outcome == 'normal'
            else:
                agent._completion_activity()
                assert agent.completion_controller.is_open
            release.set()
            await task
            if ticket.turn_id in agent._completion_pending_publications:
                await manager._retry_completion(agent, agent._completion_pending_publications[ticket.turn_id])
            agent._cancel_completion_timer()
            if agent._completion_tasks:
                await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.status == ('accepted' if winner == 'user' else 'canceled')
        assert row.reason == ('user_input' if winner == 'user' else 'root_activity')
        closed = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert closed.closed_at == (context.occurred_at if winner == 'user' else None)
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('winner', ['user', 'root'])
def test_ingress_wins_while_model_result_waits_for_writer(bridge, winner):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        entered, release = asyncio.Event(), asyncio.Event()
        from twicc.agent.base_manager import run_under_db_write_lock

        async def blocked(factory, **kwargs):
            entered.set()
            await release.wait()
            return await run_under_db_write_lock(factory, **kwargs)

        with patch('twicc.agent.base_manager.run_under_db_write_lock', side_effect=blocked):
            task = asyncio.create_task(manager.apply_ending_decision(agent, context.id,
                classification=Classification('YES', 'late verdict')))
            await entered.wait()
            if winner == 'user':
                agent._completion_input_pending(str(uuid4()), staged=True)
            else:
                agent._completion_activity()
            release.set()
            assert not await task
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        if winner == 'user':
            await manager._retry_completion(agent, agent._completion_pending_publications[ticket.turn_id])
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.reason == ('user_input' if winner == 'user' else 'root_activity')
        agent._cancel_completion_timer()
    asyncio.run(scenario())


def test_changed_shells_discard_approval_until_explicit_reconsideration(bridge):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        agent._completion_shells_are_current = lambda captured: False
        assert not await manager.apply_ending_decision(agent, context.id, classification=Classification('YES', 'obsolete'))
        assert agent.completion_controller.is_open
        assert agent.completion_controller.candidate() is None
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        assert (await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)).status == 'canceled'
        agent.completion_controller.reconsider(asyncio.get_running_loop().time())
        assert agent.completion_controller.candidate().revision != ticket.revision
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('stop', [False, True])
def test_exceptional_endings_cancel_shell_inference_without_forcing(bridge, stop):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        if stop:
            snapshot = agent._completion_terminate('ended')
            await manager._end_completion(agent, snapshot)
        else:
            agent._completion_awaiting_changed(True)
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.status == 'canceled' and not row.force_pending
        assert not await manager.apply_ending_decision(agent, context.id, classification=Classification('YES', 'late'))
    asyncio.run(scenario())


def test_accepted_shell_ending_uses_general_completion_path(bridge):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        assert await manager.apply_ending_decision(agent, context.id, classification=Classification('YES', 'Background server'))
        assert agent.state == AgentState.USER_TURN
        manager._broadcast_info.assert_awaited_once()
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.status == 'accepted' and row.reason == 'Background server'
        assert row.applied_at is not None and not row.force_pending
        ended = await asyncio.to_thread(lambda: TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED))
        assert ended.occurred_at == context.occurred_at
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('conflict', ['missing', 'closed', 'newer'])
def test_missing_turn_admission_exception_never_bypasses_existing_journal(decision_fixture, conflict):
    from tests.test_timeline_decisions import ending_request
    from twicc.core.services.logical_turns import store_turn_snapshot
    from twicc.core.services.timeline_decisions import admit_decision

    _, now, _ = decision_fixture
    request, snapshot = ending_request(decision_fixture)
    if conflict == 'missing':
        LogicalTurn.objects.filter(pk=snapshot.turn_id).delete()
    elif conflict == 'closed':
        store_turn_snapshot(snapshot._replace(revision=4, outcome='ended'), closed_at=now)
    else:
        store_turn_snapshot(snapshot._replace(revision=4), closed_at=None)
    first = admit_decision(request, identity_key='default', occurred_at=now, not_before=now)
    assert TimelineDecision.objects.get(pk=first).status == 'canceled'
    request = request._replace(id=str(uuid4()))
    admitted = admit_decision(request, identity_key='live-exception', occurred_at=now, not_before=now,
        allow_missing_turn=True)
    assert TimelineDecision.objects.get(pk=admitted).status == ('pending' if conflict == 'missing' else 'canceled')


def test_no_shell_completion_forces_exact_late_final_without_stale_ceiling(bridge, live_session):
    from twicc.agent.completion_types import NativeMessageRef

    manager, agent = bridge
    f = live_session
    f.append(f.message('progress', 'Working'))
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.id, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.id, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.id: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch

    async def scenario():
        open_candidate(manager, agent)
        ref = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=ref)
        ticket = agent.completion_controller.candidate()
        await manager._confirm_completion(agent, ticket)
        row = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind='ending'))
        assert row.status == 'accepted' and row.force_pending and row.applied_at is not None
        assert 'source_ceiling' not in row.payload
        assert agent.state == AgentState.USER_TURN
        await asyncio.to_thread(f.append, f.message('last', 'Exact final'))
        await asyncio.to_thread(row.refresh_from_db)
        assert not row.force_pending
        assert await asyncio.to_thread(f.texts) == (['Exact final'] if f.provider == 'claude_code' else ['Working', 'Exact final'])
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('unknown_scope', [False, True])
def test_result_needs_current_source_and_exact_proof_for_nullable_scope(bridge, unknown_scope):
    async def scenario():
        manager, agent = bridge
        if unknown_scope:
            agent._completion_storage_incarnation = None
            await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(storage_incarnation=None))
        ticket, context = await pending(manager, agent)
        if not unknown_scope:
            await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(storage_incarnation=str(uuid4())))
        assert not await manager.apply_ending_decision(agent, context.id, classification=Classification('YES', 'obsolete'))
        assert agent.completion_controller.is_open
        assert agent.completion_controller.candidate() is None
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        assert row.status == 'canceled' and not row.force_pending
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('claim_first', [False, True])
@pytest.mark.parametrize('continuation', ['idle', 'retry', 'newer_snapshot'])
def test_invalidated_local_claim_releases_later_input_journal(bridge, live_session, claim_first, continuation):
    from tests.test_completion_integration import drain
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.core.models import LogicalTurnInput
    from twicc.core.services import timeline_decisions as decisions

    manager, agent = bridge
    f = live_session
    f.append(f.message('last', 'Obsolete final'), capture=False)
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = f.session.storage_incarnation
    agent._completion_history_epoch = f.session.history_epoch

    async def scenario():
        open_candidate(manager, agent)
        initial = str(uuid4())
        agent._completion_input_pending(initial)
        agent._completion_input_settled(initial, 'accepted')
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        agent._completion_capture_ending_evidence = evidence
        agent._completion_shells_are_current = lambda context: True
        await manager._confirm_completion(agent, ticket)
        await drain(agent)
        context = agent._completion_pending_ending
        if claim_first:
            agent._completion_claim_ending(context, reason='user_input')
        await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(history_epoch=2))
        await asyncio.to_thread(decisions.invalidate_semantic_decisions, session_id=agent.session_id)
        token = str(uuid4())
        agent._timeline_input_origins[token] = RequestOrigin('user', 'ui', None, None)
        agent._completion_input_pending(token, staged=True)
        agent._completion_input_pending(token)
        old = agent._completion_pending_publications[ticket.turn_id]
        if continuation != 'idle':
            agent._completion_activity()
            new = agent.completion_controller.snapshot()
            agent._completion_input_settled(token, 'accepted')
        agent._cancel_completion_timer()
        if continuation == 'newer_snapshot':
            await manager._persist_completion(agent, agent.completion_controller.snapshot())
        else:
            assert not await manager._retry_completion(agent, old)
        assert ticket.turn_id not in agent._completion_pending_publications
        assert ticket.turn_id not in agent._completion_endings
        await drain(agent)
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=context.id)
        turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        initial_association = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=initial)
        assert str(initial_association.turn_id) == old.turn_id
        assert row.status == 'invalidated' and not row.force_pending
        assert turn.outcome == 'ended' and turn.closed_at == context.occurred_at
        if continuation != 'idle':
            association = await asyncio.to_thread(LogicalTurnInput.objects.get, pk=token)
            assert str(association.turn_id) == new.turn_id
            assert (await agent.wait_for_completion_input(token)).turn_id == new.turn_id
            latest = await asyncio.to_thread(LogicalTurn.objects.get, pk=new.turn_id)
            assert latest.revision == agent.completion_controller.snapshot().revision
        assert agent.state == AgentState.ASSISTANT_TURN
        manager._broadcast_info.assert_not_awaited()
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists())
        assert await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count()) == 1
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('shells', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_first_ending_binds_unknown_scope_with_exact_native_proof(bridge, live_session, shells, restart):
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.agent.semantic_types import SourceScope
    from twicc.core.services.timeline_decisions import recover_semantic_decisions
    from tests.test_completion_integration import drain

    manager, agent = bridge
    f = live_session
    f.append(f.message('last', 'Exact first final'), capture=False)
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = agent._completion_history_epoch = None
    if shells:
        agent._completion_capture_ending_evidence = evidence
        agent._completion_shells_are_current = lambda context: True

    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        await manager._confirm_completion(agent, ticket)
        await drain(agent)
        row = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind='ending'))
        turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        scope = SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch)
        assert (row.storage_incarnation, row.history_epoch) == scope[1:]
        assert (turn.storage_incarnation, turn.history_epoch) == scope[1:]
        assert row.status == ('pending' if shells else 'accepted')
        if shells:
            assert agent._completion_pending_ending.scope == scope
            assert agent._completion_endings[ticket.turn_id].scope == scope
            if restart:
                # Boot requires exact agreement after the existing native binder runs.
                await asyncio.to_thread(recover_semantic_decisions, now=row.expires_at)
            else:
                assert await manager.apply_ending_decision(agent, str(row.pk), classification=Classification('YES', 'server'))
            row = await asyncio.to_thread(TimelineDecision.objects.get, pk=row.pk)
            assert row.status == 'accepted' and row.applied_at is not None
        else:
            assert agent.state == AgentState.USER_TURN
            assert not agent._completion_pending_publications
        agent._cancel_completion_timer()
    asyncio.run(scenario())


@pytest.mark.parametrize('restart', [False, True])
def test_ending_binds_unknown_scope_after_late_native_indexing(bridge, live_session, restart):
    from tests.test_completion_integration import drain
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.core.services.timeline_decisions import recover_semantic_decisions

    manager, agent = bridge
    f = live_session
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = agent._completion_history_epoch = None
    agent._completion_capture_ending_evidence = evidence
    agent._completion_shells_are_current = lambda context: True

    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        await manager._confirm_completion(agent, ticket)
        await drain(agent)
        row = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind='ending'))
        assert row.status == 'pending' and row.storage_incarnation is None
        await asyncio.to_thread(f.append, f.message('last', 'Exact late final'), capture=False)
        turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert turn.storage_incarnation is not None
        if restart:
            await asyncio.to_thread(recover_semantic_decisions, now=row.expires_at)
        else:
            assert await manager.apply_ending_decision(agent, str(row.pk), classification=Classification('YES', 'server'))
            assert agent._completion_storage_incarnation == turn.storage_incarnation
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=row.pk)
        assert row.status == 'accepted' and row.applied_at is not None
        assert row.storage_incarnation == turn.storage_incarnation
        assert row.history_epoch == turn.history_epoch
    asyncio.run(scenario())


@pytest.mark.parametrize('foreign', ['incarnation', 'epoch', 'journal', 'newer'])
def test_unknown_ending_never_binds_known_foreign_scope(bridge, live_session, foreign):
    from tests.test_completion_integration import drain
    from twicc.agent.completion_types import NativeMessageRef

    manager, agent = bridge
    f = live_session
    f.append(f.message('last', 'Current final'), capture=False)
    f.session.refresh_from_db()
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = str(uuid4()) if foreign == 'incarnation' else None
    agent._completion_history_epoch = f.session.history_epoch + 1 if foreign == 'epoch' else None
    agent._completion_capture_ending_evidence = evidence
    agent._completion_shells_are_current = lambda context: True

    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        if foreign in ('journal', 'newer'):
            await manager._persist_completion(agent, agent.completion_controller.snapshot())
            await drain(agent)
            await asyncio.to_thread(lambda: LogicalTurn.objects.filter(pk=ticket.turn_id).update(
                **({'history_epoch': 99} if foreign == 'journal' else {'revision': ticket.revision + 1})))
        await manager._confirm_completion(agent, ticket)
        await drain(agent)
        row = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind='ending'))
        if foreign == 'newer':
            assert agent._completion_pending_ending is None
        elif foreign == 'incarnation':
            assert row.storage_incarnation != f.session.storage_incarnation
        elif foreign == 'epoch':
            assert row.history_epoch != f.session.history_epoch
        assert not await manager.apply_ending_decision(agent, str(row.pk), classification=Classification('YES', 'foreign'))
        await drain(agent)
        row = await asyncio.to_thread(TimelineDecision.objects.get, pk=row.pk)
        assert row.status == 'canceled' and not row.force_pending
        assert agent._completion_pending_ending is None
        assert agent.completion_controller.is_open
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
    asyncio.run(scenario())


@pytest.mark.parametrize('journal_state', ['newer', 'ended', 'normal'])
def test_invalidated_claim_preserves_newer_and_terminal_journals(bridge, journal_state):
    from tests.test_completion_integration import drain
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.core.services import timeline_decisions as decisions
    from twicc.core.services.logical_turns import store_turn_snapshot

    async def scenario():
        manager, agent = bridge
        open_candidate(manager, agent)
        agent.completion_controller.provider_ended(0, final_ref=NativeMessageRef('codex_item', 'late', 'native'))
        ticket = agent.completion_controller.candidate()
        agent._completion_capture_ending_evidence = evidence
        agent._completion_shells_are_current = lambda context: True
        await manager._confirm_completion(agent, ticket)
        await drain(agent)
        context = agent._completion_pending_ending
        old = agent._completion_claim_ending(context, reason='user_input')
        if journal_state == 'newer':
            await asyncio.to_thread(store_turn_snapshot, old._replace(outcome=None), closed_at=None)
        else:
            snapshot = old._replace(outcome=journal_state)
            assert await manager._persist_completion_admitted(agent, snapshot, state=AgentState.ASSISTANT_TURN)
        before = await asyncio.to_thread(lambda: LogicalTurn.objects.filter(pk=ticket.turn_id).values().get())
        events = await asyncio.to_thread(lambda: list(TimelineEvent.objects.values()))
        await asyncio.to_thread(lambda: Session.objects.filter(pk=agent.session_id).update(history_epoch=2))
        await asyncio.to_thread(decisions.invalidate_semantic_decisions, session_id=agent.session_id)
        assert not await manager._retry_completion(agent, old)
        await drain(agent)
        assert ticket.turn_id not in agent._completion_pending_publications
        after = await asyncio.to_thread(lambda: LogicalTurn.objects.filter(pk=ticket.turn_id).values().get())
        assert after == before
        assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.values())) == events
        manager._broadcast_info.assert_not_awaited()
    asyncio.run(scenario())


def test_source_binding_preserves_root_cancellation_cleanup(bridge, live_session):
    from tests.test_completion_integration import drain
    from twicc.agent.base_manager import run_under_db_write_lock
    from twicc.agent.completion_types import NativeMessageRef

    manager, agent = bridge
    f = live_session
    f.append(f.message('last', 'Captured final'), capture=False)
    agent.session_id, agent.project_id = f.session.pk, f.session.project_id
    agent.provider = manager.provider = Provider(f.provider)
    agent.process_run.session_id, agent.process_run.provider = f.session.pk, f.provider
    agent.process_run.save(update_fields=['session_id', 'provider'])
    manager._agents = {f.session.pk: agent}
    agent._completion_storage_incarnation = agent._completion_history_epoch = None
    agent._completion_capture_ending_evidence = evidence
    agent._completion_shells_are_current = lambda context: True

    async def scenario():
        open_candidate(manager, agent)
        native = NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', 'last',
            None if f.provider == 'claude_code' else 'native-turn')
        agent.completion_controller.provider_ended(0, final_ref=native)
        ticket = agent.completion_controller.candidate()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(factory, **kwargs):
            entered.set()
            await release.wait()
            return await run_under_db_write_lock(factory, **kwargs)

        with patch('twicc.agent.base_manager.run_under_db_write_lock', side_effect=blocked):
            confirmation = asyncio.create_task(manager._confirm_completion(agent, ticket))
            await entered.wait()
            agent._completion_activity()
            release.set()
            await confirmation
            await drain(agent)
        assert agent._completion_pending_ending is None
        assert ticket.turn_id not in agent._completion_endings
        row = await asyncio.to_thread(lambda: TimelineDecision.objects.get(kind='ending'))
        assert (row.status, row.reason) == ('canceled', 'root_activity')
        assert agent.completion_controller.is_open
    asyncio.run(scenario())


def test_failed_delivery_does_not_reopen_claimed_old_turn(bridge):
    async def scenario():
        manager, agent = bridge
        ticket, context = await pending(manager, agent)
        token = str(uuid4())
        agent._completion_input_pending(token, staged=True)
        closed = agent.completion_controller.snapshot()
        agent._completion_input_pending(token)
        agent._completion_input_settled(token, 'rejected')
        assert agent.completion_controller.snapshot() == closed
        assert agent._completion_inputs[token].turn_id is None
        assert await agent.wait_for_completion_input(token) is None
        await manager._retry_completion(agent, closed)
        turn = await asyncio.to_thread(LogicalTurn.objects.get, pk=ticket.turn_id)
        assert turn.outcome == 'normal' and turn.closed_at == context.occurred_at
        assert not agent.completion_controller.is_open
        agent._cancel_completion_timer()
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
    asyncio.run(scenario())
