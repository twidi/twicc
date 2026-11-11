"""Crash recovery and retention preserve exact journal ownership."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tests.test_timeline_decisions import decision_fixture, ending_request, admit
from tests.test_completion_publication import bridge
from tests.test_timeline_messages import live_session, content_row_factory
from twicc.core.models import LogicalTurn, TimelineDecision, TimelineEvent, ProcessRun
from twicc.core.services import timeline_decisions as decisions

pytestmark = pytest.mark.django_db(transaction=True)


def test_boot_cancels_old_pending_ending_after_durable_root_revision(decision_fixture):
    request, snapshot = ending_request(decision_fixture)
    _, now, _ = decision_fixture
    key = admit(request, now)
    LogicalTurn.objects.filter(pk=snapshot.turn_id).update(revision=snapshot.revision + 1)
    assert decisions.recover_semantic_decisions(now=now) == 1
    row = TimelineDecision.objects.get(pk=key)
    assert row.status == 'canceled'
    assert not row.force_pending
    assert TimelineEvent.objects.count() == 0


def test_recovery_matches_exact_open_snapshot_and_original_time(decision_fixture, monkeypatch):
    request, snapshot = ending_request(decision_fixture)
    session, now, _ = decision_fixture
    key = admit(request, now)
    monkeypatch.setattr(decisions, 'fulfill_final_message_obligation', lambda **kw: False)
    assert decisions.recover_semantic_decisions(now=now + timedelta(minutes=1)) == 1
    row = TimelineDecision.objects.get(pk=key)
    turn = LogicalTurn.objects.get(pk=snapshot.turn_id)
    assert (row.status, row.reason, turn.outcome, turn.closed_at) == ('accepted', 'boot_recovery', 'normal', now)
    assert row.force_pending and row.applied_at
    assert TimelineEvent.objects.get(kind='turn.ended').occurred_at == now
    assert decisions.recover_semantic_decisions(now=now + timedelta(minutes=2)) == 0
    assert TimelineEvent.objects.filter(kind='turn.ended').count() == 1


def test_boot_order_uses_actual_lost_process_reconciliation(decision_fixture, monkeypatch):
    from twicc.core.services.timeline_boot import reconcile_at_boot
    request, snapshot = ending_request(decision_fixture)
    session, now, _ = decision_fixture
    run = ProcessRun.objects.create(session_id=session.pk, provider=session.provider,
        state='assistant_turn', started_at=now, last_state_change_at=now)
    LogicalTurn.objects.filter(pk=snapshot.turn_id).update(process_run_id=run.pk)
    admit(request, now)
    monkeypatch.setattr(decisions, 'fulfill_final_message_obligation', lambda **kw: False)
    reconcile_at_boot(now=now + timedelta(minutes=1), boot_id='test')
    assert TimelineEvent.objects.filter(kind='turn.ended').count() == 1
    assert TimelineEvent.objects.filter(kind='process.ended').count() == 1


def test_long_turn_keeps_rejected_message_until_closure(decision_fixture):
    session, now, request = decision_fixture
    key = admit(request, now)
    row = TimelineDecision.objects.get(pk=key)
    turn = LogicalTurn.objects.create(id=uuid4(), session_id=session.pk, provider=session.provider,
        run_id=uuid4(), ordinal=1, position=1, source_floor=0, opened_at=now - timedelta(days=10),
        storage_incarnation=session.storage_incarnation, history_epoch=session.history_epoch,
        final_refs=row.payload['message_refs'])
    TimelineDecision.objects.filter(pk=key).update(status='rejected', resolved_at=now - timedelta(days=5))
    assert decisions.purge_semantic_decisions(now=now) == 0
    row.refresh_from_db()
    assert row.turn_id == turn.pk
    turn.closed_at, turn.outcome = now, 'normal'
    turn.save()
    assert decisions.purge_semantic_decisions(now=now + timedelta(hours=47)) == 0
    assert decisions.purge_semantic_decisions(now=now + timedelta(hours=48)) == 1


def test_cleanup_preserves_pending_final_obligation(decision_fixture):
    from twicc.logical_turn_cleanup_task import purge_logical_turns
    request, snapshot = ending_request(decision_fixture)
    _, now, _ = decision_fixture
    key = admit(request, now)
    LogicalTurn.objects.filter(pk=snapshot.turn_id).update(
        opened_at=now - timedelta(days=10), closed_at=now - timedelta(days=5), outcome='normal')
    TimelineDecision.objects.filter(pk=key).update(status='accepted', force_pending=True, applied_at=now)
    assert purge_logical_turns(now=now) == 0
    assert decisions.purge_semantic_decisions(now=now + timedelta(days=10)) == 0


def test_source_replacement_invalidates_without_removing_events(decision_fixture):
    session, now, request = decision_fixture
    key = admit(request, now)
    session.history_epoch += 1
    session.save()
    assert decisions.invalidate_semantic_decisions(session_id=session.pk) == 1
    assert TimelineDecision.objects.get(pk=key).status == 'invalidated'


@pytest.mark.parametrize('crash', ['before_inference', 'after_local_claim', 'after_accepted_commit', 'after_event_before_ack'])
def test_boot_crash_points_are_idempotent(decision_fixture, monkeypatch, crash):
    from twicc.core.services.logical_turns import store_turn_snapshot
    from twicc.core.services.session_timeline import record_event_sync
    request, snapshot = ending_request(decision_fixture)
    session, now, _ = decision_fixture
    key = admit(request, now)
    if crash in ('after_accepted_commit', 'after_event_before_ack'):
        decisions.accept_fallback(decision_id=key, reason='user_input', now=now)
    if crash == 'after_event_before_ack':
        store_turn_snapshot(snapshot._replace(outcome='normal', revision=snapshot.revision + 1), closed_at=now)
        record_event_sync('turn.ended', session_id=session.pk, actor='agent', occurred_at=now,
            dedupe_key=f'turn:{snapshot.turn_id}:turn.ended', data={'message_refs': request.payload['message_refs']})
    # A local claim has no extra persisted fact. Recovery uses only the exact journal.
    monkeypatch.setattr(decisions, 'fulfill_final_message_obligation', lambda **kw: False)
    decisions.recover_semantic_decisions(now=now + timedelta(seconds=1))
    decisions.recover_semantic_decisions(now=now + timedelta(seconds=2))
    row = TimelineDecision.objects.get(pk=key)
    assert row.status == 'accepted' and row.applied_at and row.force_pending
    assert LogicalTurn.objects.get(pk=snapshot.turn_id).closed_at == now
    assert TimelineEvent.objects.filter(kind='turn.ended').count() == 1
    assert not ProcessRun.objects.exists()


@pytest.mark.parametrize('outcome', ['ended', 'provider_error', 'normal', 'missing'])
def test_boot_never_uses_live_missing_journal_exception(decision_fixture, outcome):
    request, snapshot = ending_request(decision_fixture)
    _, now, _ = decision_fixture
    key = admit(request, now)
    if outcome == 'missing':
        LogicalTurn.objects.filter(pk=snapshot.turn_id).delete()
    else:
        LogicalTurn.objects.filter(pk=snapshot.turn_id).update(outcome=outcome, closed_at=now)
    decisions.recover_semantic_decisions(now=now)
    assert TimelineDecision.objects.get(pk=key).status == 'canceled'
    assert not TimelineEvent.objects.exists()


def test_later_open_turn_keeps_exceptional_lost_boundary(decision_fixture, monkeypatch):
    from twicc.core.services.timeline_boot import reconcile_at_boot
    request, snapshot = ending_request(decision_fixture)
    session, now, _ = decision_fixture
    run = ProcessRun.objects.create(session_id=session.pk, provider=session.provider,
        state='assistant_turn', started_at=now, last_state_change_at=now)
    LogicalTurn.objects.filter(pk=snapshot.turn_id).update(process_run_id=run.pk)
    admit(request, now)
    LogicalTurn.objects.create(id=uuid4(), run_id=snapshot.run_id, session_id=session.pk,
        provider=session.provider, process_run_id=run.pk, ordinal=2, position=2, opened_at=now,
        source_floor=0)
    monkeypatch.setattr(decisions, 'fulfill_final_message_obligation', lambda **kw: False)
    reconcile_at_boot(now=now + timedelta(seconds=1), boot_id='test')
    assert TimelineEvent.objects.filter(kind='turn.ended').count() == 2
    assert TimelineEvent.objects.filter(kind='process.ended').count() == 1


def test_unknown_rejected_message_protects_journal_and_live_missing_journal(decision_fixture):
    from twicc.logical_turn_cleanup_task import purge_logical_turns
    session, now, request = decision_fixture
    key = admit(request, now)
    TimelineDecision.objects.filter(pk=key).update(status='rejected', resolved_at=now - timedelta(days=8))
    turn = LogicalTurn.objects.create(id=uuid4(), session_id=session.pk, provider=session.provider,
        run_id=uuid4(), ordinal=1, position=1, source_floor=0, opened_at=now - timedelta(days=10))
    assert purge_logical_turns(now=now) == 0
    assert decisions.purge_semantic_decisions(now=now) == 0
    LogicalTurn.objects.filter(pk=turn.pk).delete()
    ProcessRun.objects.create(session_id=session.pk, provider=session.provider, state='assistant_turn',
        started_at=now - timedelta(days=10), last_state_change_at=now)
    assert decisions.purge_semantic_decisions(now=now) == 0


def test_unmanaged_rejected_messages_have_bounded_retention(decision_fixture):
    _, now, request = decision_fixture
    key = admit(request, now)
    TimelineDecision.objects.filter(pk=key).update(status='rejected', resolved_at=now)
    assert decisions.purge_semantic_decisions(now=now + timedelta(hours=47)) == 0
    assert decisions.purge_semantic_decisions(now=now + timedelta(hours=48)) == 1


def test_explicit_missing_source_invalidates_pending_and_final_obligation(decision_fixture):
    from twicc.providers.db_writer import _apply_mark_sessions_stale_payload, MarkSessionsStalePayload
    from twicc.core.enums import Provider
    session, now, request = decision_fixture
    message = admit(request, now)
    request, _ = ending_request(decision_fixture)
    final = admit(request, now)
    decisions.accept_fallback(decision_id=final, reason='user_input', now=now)
    _apply_mark_sessions_stale_payload(MarkSessionsStalePayload(Provider(session.provider), [session.pk]))
    assert set(TimelineDecision.objects.filter(pk__in=[message, final]).values_list('status', flat=True)) == {'invalidated'}
    assert not TimelineDecision.objects.filter(force_pending=True).exists()


def test_codex_first_ending_admission_uses_native_id(bridge, monkeypatch):
    """The real factory constructs its controller only after thread/start."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from tests.test_codex_thread_work_dirs import _install_factory_fakes
    from tests.test_semantic_completion_integration import evidence
    from tests.test_completion_publication import open_candidate
    from twicc.providers.codex.agent import manager as codex_manager_module
    from twicc.providers.codex.agent.agent import CodexAgent
    from twicc.providers.codex.agent.manager import CodexAgentManager
    from twicc.providers.helpers import AgentSettings
    from twicc.core.models import Session, LogicalTurnInput

    codex, _ = _install_factory_fakes(monkeypatch, [])
    codex._client = MagicMock()
    monkeypatch.setattr(codex_manager_module, 'CodexAgent', CodexAgent)
    monkeypatch.setattr(CodexAgent, '_seed_context_baseline', AsyncMock())
    manager = CodexAgentManager()
    manager._broadcast_info = AsyncMock()
    session = Session.objects.create(id='canonical-id', project_id='completion-p', provider='codex',
        file_path='canonical-id.jsonl', storage_incarnation=str(uuid4()), history_epoch=1)
    bound = []
    async def notify(**kwargs):
        bound.append(kwargs)
        assert not await asyncio.to_thread(TimelineDecision.objects.exists)
    monkeypatch.setattr(manager, 'notify_session_bound', notify)
    token = str(uuid4())
    async def register(agent, text, resume, **kwargs):
        assert bound == [{'draft_session_id': 'draft-id', 'session_id': 'canonical-id'}]
        assert agent.session_id == 'canonical-id'
        manager._agents[agent.session_id] = agent
        agent._completion_storage_incarnation = session.storage_incarnation
        agent._completion_history_epoch = session.history_epoch
        open_candidate(manager, agent)
        agent._completion_input_pending(token)
        agent._completion_input_settled(token, 'accepted')
        agent.completion_controller.provider_ended(0)
        agent._completion_capture_ending_evidence = evidence
        agent._completion_shells_are_current = lambda context: True
        await manager._confirm_completion(agent, agent.completion_controller.candidate())
        if agent._completion_tasks:
            await asyncio.gather(*tuple(agent._completion_tasks))
        agent._cancel_completion_timer()
    monkeypatch.setattr(manager, '_register_and_start', register)
    async def scenario():
        await manager._start_agent_with_admission('draft-id', 'completion-p', '/tmp', 'hello', False,
            settings=AgentSettings(), ephemeral_ids=[])
    asyncio.run(scenario())
    assert list(TimelineDecision.objects.values_list('session_id', flat=True)) == ['canonical-id']
    assert LogicalTurnInput.objects.get(pk=token).turn.session_id == 'canonical-id'
    assert not LogicalTurn.objects.filter(session_id='draft-id').exists()


def test_boot_source_read_failure_retains_accepted_publication(decision_fixture, monkeypatch):
    _, now, request = decision_fixture
    key = admit(request, now)
    def unavailable(**kwargs):
        raise OSError('temporary source read error')
    monkeypatch.setattr(decisions, 'publish_message_if_ready', unavailable)
    assert decisions.recover_semantic_decisions(now=now) == 1
    row = TimelineDecision.objects.get(pk=key)
    assert row.status == 'accepted' and row.reason == 'boot_recovery'
    assert row.applied_at is None


def test_boot_source_read_failure_retains_exact_final_obligation(decision_fixture, monkeypatch):
    request, snapshot = ending_request(decision_fixture)
    _, now, _ = decision_fixture
    key = admit(request, now)
    def unavailable(**kwargs):
        raise OSError('temporary source read error')
    monkeypatch.setattr(decisions, 'fulfill_final_message_obligation', unavailable)
    assert decisions.recover_semantic_decisions(now=now) == 1
    row = TimelineDecision.objects.get(pk=key)
    assert row.status == 'accepted' and row.applied_at and row.force_pending
    assert LogicalTurn.objects.get(pk=snapshot.turn_id).closed_at == now


def test_permanent_source_deletion_wakes_owned_inference(decision_fixture):
    from twicc.providers.db_writer import _apply_delete_sessions_payload, DeleteSessionsPayload
    from twicc.core.enums import Provider
    session, now, request = decision_fixture
    admit(request, now)
    wakes = []
    decisions.register_decision_wakeup(lambda: wakes.append(True))
    _apply_delete_sessions_payload(DeleteSessionsPayload(Provider(session.provider), [session.pk]))
    assert wakes
    assert not TimelineDecision.objects.exists()


@pytest.mark.parametrize('accepted', [False, True])
@pytest.mark.parametrize('failure', ['database_error', 'failed_insert'])
@pytest.mark.parametrize('live_session', ['claude_code'], indirect=True)
def test_boot_cleanup_retains_normal_ending_after_boundary_failure(live_session, monkeypatch, accepted, failure):
    from django.db import OperationalError
    from twicc.agent.completion_types import NativeMessageRef, CompletionSnapshot
    from twicc.agent.semantic_types import Classification, DecisionRequest, SourceScope
    from twicc.agent.process_run_cleanup import _apply_cleanup_stale_process_runs_job
    from twicc.core.services import session_timeline
    from twicc.timeline_decision_task import _retry_effect

    f = live_session
    f.append(f.message('final', 'Exact final after crash'))
    f.session.refresh_from_db()
    message = TimelineDecision.objects.get(kind='message', session=f.session)
    now = timezone.now()
    decisions.resolve_decision(decision_id=str(message.pk), classification=Classification('NO', 'routine'),
        effective_route='provider', now=now)
    assert f.texts() == []
    run = ProcessRun.objects.create(session_id=f.session.pk, provider=f.provider, state='assistant_turn',
        started_at=now - timedelta(minutes=1), last_state_change_at=now)
    ref = NativeMessageRef('claude_uuid', 'final')
    snapshot = CompletionSnapshot(str(uuid4()), str(uuid4()), 1, 3, ref, (ref,), None)
    turn = LogicalTurn.objects.create(id=snapshot.turn_id, session_id=f.session.pk, provider=f.provider,
        run_id=snapshot.run_id, process_run_id=run.pk, ordinal=1, position=1, revision=snapshot.revision,
        source_floor=0, storage_incarnation=f.session.storage_incarnation, history_epoch=f.session.history_epoch,
        first_ref=ref._asdict(), final_refs=[ref._asdict()], opened_at=now - timedelta(minutes=1))
    request = DecisionRequest(str(uuid4()), 'ending', f.session.pk,
        SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch),
        {'run_id': snapshot.run_id, 'turn_id': snapshot.turn_id, 'source_floor': 0,
         'candidate': {'turn_id': snapshot.turn_id, 'revision': snapshot.revision, 'deadline': 10.0},
         'snapshot': {**snapshot._asdict(), 'first_ref': ref._asdict(), 'final_refs': [ref._asdict()]},
         'message_refs': [ref._asdict()]}, now + timedelta(seconds=45), 'provider')
    key = decisions.admit_decision(request, identity_key='crashed-ending', occurred_at=now, not_before=now)
    if accepted:
        decisions.accept_fallback(decision_id=key, reason='user_input', now=now)
    original = session_timeline.record_event_sync
    attempts = []
    def fail_boundary(kind, **kwargs):
        if kind == 'turn.ended':
            attempts.append(kind)
            if failure == 'database_error':
                raise OperationalError('boundary unavailable during boot')
            return None
        return original(kind, **kwargs)
    monkeypatch.setattr(session_timeline, 'record_event_sync', fail_boundary)

    _apply_cleanup_stale_process_runs_job(None)

    row = TimelineDecision.objects.get(pk=key)
    turn.refresh_from_db()
    assert attempts
    assert row.status == 'accepted' and row.applied_at is None and row.force_pending
    assert (turn.outcome, turn.closed_at, turn.revision) == (None, None, snapshot.revision)
    assert not ProcessRun.objects.filter(pk=run.pk).exists()
    assert TimelineEvent.objects.filter(session_id=f.session.pk, kind='process.ended').count() == 1
    assert not TimelineEvent.objects.filter(session_id=f.session.pk, kind='turn.ended').exists()
    assert f.texts() == []

    monkeypatch.setattr(session_timeline, 'record_event_sync', original)
    # The normal runtime's durable-effect retry must work with no live agent or process.
    assert _retry_effect(row, now + timedelta(seconds=2))
    assert _retry_effect(row, now + timedelta(seconds=3))
    row.refresh_from_db()
    turn.refresh_from_db()
    assert row.status == 'accepted' and row.applied_at and not row.force_pending
    assert (turn.outcome, turn.closed_at) == ('normal', now)
    boundary = TimelineEvent.objects.get(session_id=f.session.pk, kind='turn.ended')
    assert boundary.occurred_at == now
    assert boundary.dedupe_key == f'turn:{snapshot.turn_id}:turn.ended'
    assert f.texts() == ['Exact final after crash']
    message.refresh_from_db()
    assert message.forced and message.status == 'accepted'
    assert TimelineEvent.objects.filter(session_id=f.session.pk, kind='assistant_message.sent').count() == 1
    assert TimelineEvent.objects.filter(session_id=f.session.pk, kind='process.ended').count() == 1
    assert not ProcessRun.objects.filter(session_id=f.session.pk).exists()


@pytest.mark.parametrize('changed', ['later_open_turn', 'newer_revision', 'new_source'])
def test_boot_pending_normal_protection_requires_exact_journal(decision_fixture, changed):
    from twicc.core.services.logical_turns import settle_abandoned_turns
    from twicc.core.services.timeline_boot import _reconcile_lost_processes
    request, snapshot = ending_request(decision_fixture)
    session, now, _ = decision_fixture
    run = ProcessRun.objects.create(session_id=session.pk, provider=session.provider,
        state='assistant_turn', started_at=now, last_state_change_at=now)
    LogicalTurn.objects.filter(pk=snapshot.turn_id).update(process_run_id=run.pk)
    key = admit(request, now)
    decisions.accept_fallback(decision_id=key, reason='boot_recovery', now=now)
    if changed == 'later_open_turn':
        later = LogicalTurn.objects.create(id=uuid4(), session_id=session.pk, provider=session.provider,
            run_id=snapshot.run_id, process_run_id=run.pk, ordinal=2, position=2, source_floor=0, opened_at=now)
    elif changed == 'newer_revision':
        LogicalTurn.objects.filter(pk=snapshot.turn_id).update(revision=snapshot.revision + 1)
    else:
        session.history_epoch += 1
        session.save(update_fields=['history_epoch'])
    _reconcile_lost_processes(now)
    assert settle_abandoned_turns(at=now) == 1
    old = LogicalTurn.objects.get(pk=snapshot.turn_id)
    if changed == 'later_open_turn':
        assert old.outcome is None and old.revision == snapshot.revision
        later.refresh_from_db()
        assert later.outcome == 'ended'
    else:
        assert old.outcome == 'ended'
    assert TimelineEvent.objects.filter(kind='turn.ended', session_id=session.pk).count() == 1
    assert TimelineEvent.objects.filter(kind='process.ended', session_id=session.pk).count() == 1
