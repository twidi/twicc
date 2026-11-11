"""Durable Claude gates and exact final-source obligations."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from tests.test_timeline_messages import content_row_factory, live_session, verified
from tests.test_completion_publication import bridge
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.semantic_types import Classification
from twicc.core.models import Session, TimelineDecision, TimelineEvent
from twicc.core.services import timeline_decisions, timeline_messages

# Export imported fixtures for pytest discovery.
__all__ = ['bridge', 'content_row_factory', 'live_session']


pytestmark = pytest.mark.django_db


def decide(row, verdict='YES'):
    timeline_decisions.resolve_decision(decision_id=str(row.pk), classification=Classification(verdict, 'useful'),
                                       effective_route='provider', now=timezone.now())
    return timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))


def ending(f, refs, *, floor=0):
    f.session.refresh_from_db()
    return TimelineDecision.objects.create(id=uuid4(), session=f.session, provider=f.provider,
        storage_incarnation=f.session.storage_incarnation, history_epoch=f.session.history_epoch,
        kind='ending', identity_key=str(uuid4()), status='accepted', force_pending=True,
        occurred_at=timezone.now(), not_before=timezone.now(), expires_at=timezone.now() + timedelta(seconds=45),
        payload={'snapshot': {'final_refs': [ref._asdict() for ref in refs]}, 'source_floor': floor,
                 'message_refs': [ref._asdict() for ref in refs]})


def ref(f, name):
    return NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', name,
                            None if f.provider == 'claude_code' else 'native-turn')


@pytest.mark.parametrize('ingress', ['indexed', 'verified', 'recovery'])
def test_provider_gate_across_ingress(live_session, ingress):
    f = live_session
    f.append(f.message('answer', 'Useful mid-work answer'), capture=ingress == 'indexed')
    if ingress == 'verified':
        timeline_messages.record_verified_messages(f.session.id, refs=verified(f, ref(f, 'answer')),
                                                  timeline_since=f.boundary)
    elif ingress == 'recovery':
        from twicc.core.services.session_timeline import record_event_sync
        record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={
            'message_refs': [ref(f, 'answer')._asdict()]})
        timeline_messages.recover_referenced_messages(f.session.id)
    rows = list(TimelineDecision.objects.filter(kind='message'))
    if f.provider == 'codex':
        assert rows == []
        assert f.texts() == ['Useful mid-work answer']
        return
    assert len(rows) == 1
    assert f.texts() == []
    assert rows[0].payload['target'] == {'text': 'Useful mid-work answer'}
    assert decide(rows[0]) is not None
    assert f.texts() == ['Useful mid-work answer']


def test_rejection_survives_recovery_and_final_force(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude gate')
    f.append(f.message('progress', 'I am working'))
    row = TimelineDecision.objects.get(kind='message')
    assert decide(row, 'NO') is None
    e = ending(f, [ref(f, 'progress')])
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert f.texts() == ['I am working']
    assert not timeline_decisions.resolve_decision(decision_id=str(row.pk), classification=Classification('NO', 'late'),
        effective_route='provider', now=timezone.now())
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert len(f.events()) == 1


def test_final_forcing_waits_for_exact_late_group(live_session):
    f = live_session
    f.append(f.message('progress', 'progress'))
    e = ending(f, [ref(f, 'progress'), ref(f, 'last')])
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    e.refresh_from_db()
    assert e.force_pending
    f.append(f.message('last', 'final'))
    e.refresh_from_db()
    assert not e.force_pending
    assert f.texts() == (['final'] if f.provider == 'claude_code' else ['progress', 'final'])


def test_forcing_does_not_publish_all_progress_finals(live_session):
    f = live_session
    f.append(f.message('first', 'progress'), f.message('last', 'final'))
    e = ending(f, [ref(f, 'first'), ref(f, 'last')])
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert f.texts() == (['final'] if f.provider == 'claude_code' else ['progress', 'final'])


@pytest.mark.parametrize('thinking_final', [None, True])
def test_thinking_prefix_allows_message_classification_and_publication(live_session, thinking_final):
    from twicc.timeline_decision_task import _source_ready

    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split thinking block')
    thinking = f.message('thinking', '', message_id='answer-group', final=thinking_final)
    thinking['message']['content'] = [{'type': 'thinking', 'thinking': 'private reasoning'}]
    f.append(thinking)
    assert not TimelineDecision.objects.filter(kind='message').exists()
    f.append(f.message('answer', 'Useful answer', message_id='answer-group'))
    assert f.session.items.get(line_num=1).kind == 'content_items'
    row = TimelineDecision.objects.get(kind='message')
    assert row.payload['target'] == {'text': 'Useful answer'}
    assert _source_ready(row)
    assert f.texts() == []
    assert decide(row)
    assert f.texts() == ['Useful answer']

    f.append(f.message('next', 'Next answer'))
    next_row = TimelineDecision.objects.get(payload__target__text='Next answer')
    assert next_row.payload['previous_messages'] == [
        {'role': 'assistant', 'text': 'Useful answer', 'line': 1}]


@pytest.mark.parametrize('late_text', [False, True])
def test_final_with_thinking_prefix_does_not_force_rejected_progress(live_session, late_text):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split thinking block')
    progress = f.message('progress', 'Review 5 is running')
    progress['timestamp'] = (timezone.now() - timedelta(minutes=9)).isoformat()
    f.append(progress, since=f.boundary - timedelta(minutes=10))
    progress_row = TimelineDecision.objects.get(kind='message')
    assert decide(progress_row, 'NO') is None

    thinking = f.message('thinking', '', message_id='final-group')
    thinking['message']['content'] = [{'type': 'thinking', 'thinking': 'private reasoning'}]
    f.append(thinking)
    if late_text:
        e = ending(f, [ref(f, 'progress'), ref(f, 'final')])
        assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
        assert f.texts() == []
    final = f.message('final', 'Review 5 passes. Work is complete.', message_id='final-group')
    f.append(final)
    if not late_text:
        final_row = TimelineDecision.objects.get(payload__target__text='Review 5 passes. Work is complete.')
        assert decide(final_row, 'NO') is None
        e = ending(f, [ref(f, 'progress'), ref(f, 'final')])
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))

    assert f.texts() == ['Review 5 passes. Work is complete.']
    event = f.events()[0]
    assert event.occurred_at.isoformat() == final['timestamp']
    assert {source['native_id'] for source in event.data['source_refs']} == {
        'thinking', 'final-group', 'final'}
    progress_row.refresh_from_db()
    assert (progress_row.status, progress_row.forced, progress_row.published_event_id) == ('rejected', False, None)
    final_row = TimelineDecision.objects.get(payload__target__text='Review 5 passes. Work is complete.')
    assert final_row.forced and final_row.published_event_id == event.pk
    e.refresh_from_db()
    assert not e.force_pending


def test_new_turn_message_is_never_forced_for_old_ending(live_session):
    f = live_session
    e = ending(f, [ref(f, 'missing')])
    f.append(f.message('new', 'new turn'))
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert f.texts() == ([] if f.provider == 'claude_code' else ['new turn'])


@pytest.mark.parametrize('failure', ['swallowed', 'rollback', 'conflict'])
def test_publication_retries_after_insert_failure(live_session, failure):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude gate')
    f.append(f.message('a', 'answer'))
    row = TimelineDecision.objects.get()
    timeline_decisions.accept_fallback(decision_id=str(row.pk), reason='invalid_response', now=timezone.now())
    if failure == 'swallowed':
        with patch('twicc.core.services.timeline_messages.record_event_sync', return_value=None):
            assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk)) is None
    elif failure == 'rollback':
        with transaction.atomic():
            assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
            transaction.set_rollback(True)
    else:
        from twicc.core.services.session_timeline import record_event_sync
        record_event_sync('assistant_message.sent', session_id=f.session.id, actor='agent',
                          dedupe_key=row.payload['dedupe_key'], data={**row.payload['event_data'], 'text': 'conflict'})
        assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk)) is None
        TimelineEvent.objects.filter(dedupe_key=row.payload['dedupe_key']).delete()
    row.refresh_from_db()
    assert row.published_event_id is None
    assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
    assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
    assert f.texts() == ['answer']


def test_context_is_frozen_before_candidate_and_keeps_user_anchor(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude context')
    f.append(f.message('u', 'actual request', user=True), *[f.message(str(i), f'prior {i}') for i in range(5)],
             f.message('target', 'candidate'), f.message('future', 'future input', user=True))
    row = TimelineDecision.objects.get(payload__target__text='candidate')
    assert [entry['text'] for entry in row.payload['previous_messages']] == [
        'actual request', 'prior 1', 'prior 2', 'prior 3', 'prior 4']
    f.append(f.message('later', 'later input', user=True))
    row.refresh_from_db()
    assert 'later input' not in str(row.payload)


def test_outgoing_context_buffer_is_bounded_and_filters_origin(bridge):
    from twicc.request_origin import RequestOrigin, UI_ORIGIN, origin_scope
    _, agent = bridge
    for i in range(15):
        with origin_scope(UI_ORIGIN):
            agent._timeline_capture_input(str(i), f'input {i}')
    with origin_scope(RequestOrigin('agent', 'mcp_internal', 'sender')):
        agent._timeline_capture_input('agent', 'agent input')
    agent._timeline_capture_input('command', '/help')
    snapshots = agent._timeline_context_inputs()
    assert len(snapshots) == 12
    assert [entry['input_snapshot']['text'] for entry in snapshots] == [f'input {i}' for i in range(3, 15)]


def _assert_forced_message_log(live_session, prior_verdict, caplog, django_capture_on_commit_callbacks, monkeypatch):
    import logging
    monkeypatch.setattr(logging.getLogger("twicc.core.services.timeline_decisions"), "disabled", False)
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude diagnostics')
    f.append(f.message('answer', 'private candidate'))
    row = TimelineDecision.objects.get()
    with (
        caplog.at_level(logging.DEBUG, logger='twicc.core.services.timeline_decisions'),
        django_capture_on_commit_callbacks(execute=True),
    ):
        if prior_verdict:
            decide(row, prior_verdict)
        e = ending(f, [ref(f, 'answer')])
        assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
        assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    import orjson
    records = [orjson.loads(record.message.split('semantic_decision ', 1)[1]) for record in caplog.records
               if 'semantic_decision ' in record.message]
    assert [record['event'] for record in records] == (['resolved', 'forced'] if prior_verdict else ['forced'])
    assert records[-1]['verdict'] is None
    if prior_verdict:
        assert records[0]['verdict'] == 'NO'
    assert 'private candidate' not in caplog.text


def test_forced_message_log_preserves_prior_model_verdict(live_session, caplog, django_capture_on_commit_callbacks, monkeypatch):
    _assert_forced_message_log(live_session, 'NO', caplog, django_capture_on_commit_callbacks, monkeypatch)


def test_forced_before_inference_logs_without_fake_model_result(live_session, caplog, django_capture_on_commit_callbacks, monkeypatch):
    _assert_forced_message_log(live_session, None, caplog, django_capture_on_commit_callbacks, monkeypatch)


def test_message_with_unknown_scope_publishes_with_exact_source(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude gate')
    f.append(f.message('a', 'answer'))
    from twicc.core.models import Session
    Session.objects.filter(pk=f.session.id).update(storage_incarnation=None)
    TimelineDecision.objects.update(storage_incarnation=None)
    row = TimelineDecision.objects.get()
    row.payload['event_data']['storage_incarnation'] = None
    row.save(update_fields=['payload'])
    assert decide(row)
    assert f.texts() == ['answer']


@pytest.mark.parametrize('change', ['bind', 'missing', 'text', 'scope'])
def test_application_requires_current_exact_source(live_session, content_row_factory, change):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude gate')
    f.append(f.message('a', 'answer'))
    row = TimelineDecision.objects.get()
    timeline_decisions.accept_fallback(decision_id=str(row.pk), reason='generation_failed', now=timezone.now())
    if change == 'bind':
        TimelineDecision.objects.filter(pk=row.pk).update(storage_incarnation=None, history_epoch=None)
        row.payload['event_data']['storage_incarnation'] = None
        row.payload['event_data']['history_epoch'] = None
        row.save(update_fields=['payload'])
        timeline_messages.record_indexed_messages(f.session.id, new_line_nums=[1], timeline_since=f.boundary)
        assert f.texts() == ['answer']
        return
    if change == 'missing':
        f.session.items.all().delete()
    elif change == 'text':
        import orjson
        f.session.items.update(current_content=content_row_factory(orjson.dumps(f.message('a', 'changed')).decode(), False))
    else:
        from twicc.core.models import Session
        Session.objects.filter(pk=f.session.id).update(storage_incarnation='replaced')
    assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk)) is None
    row.refresh_from_db()
    assert row.published_event_id is None
    assert f.texts() == []


def test_final_group_identity_without_terminal_proof_stays_pending(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude groups')
    f.append(f.message('prefix', 'prefix', message_id='group', final=None))
    e = ending(f, [NativeMessageRef('claude_message', 'group')])
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    f.append(f.message('terminal', 'terminal', message_id='group'))
    e.refresh_from_db()
    assert not e.force_pending
    assert f.texts() == ['prefix\nterminal']


def test_gate_and_rollback_emit_no_signal_before_commit(live_session, django_capture_on_commit_callbacks):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude gate')
    with patch('twicc.core.services.timeline_signal.poke') as poke:
        with django_capture_on_commit_callbacks(execute=True):
            f.append(f.message('a', 'answer'))
        assert not poke.called
        row = TimelineDecision.objects.get()
        timeline_decisions.accept_fallback(decision_id=str(row.pk), reason='timeout', now=timezone.now())
        with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
            assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
            transaction.set_rollback(True)
        assert not poke.called
        with django_capture_on_commit_callbacks(execute=True):
            assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
            assert timeline_decisions.publish_message_if_ready(decision_id=str(row.pk))
        assert poke.call_count == 1


def test_context_merges_inputs_before_window_and_keeps_notice_labels(live_session):
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    from twicc.request_origin import UI_ORIGIN
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude context')
    notice = SHELL_NOTICE_HEADER + ': waiting'
    f.append(f.message('u', 'actual request', user=True), f.message('a', 'first'),
             f.message('notice', notice, user=True), f.message('b', 'second'),
             f.message('candidate', 'candidate'))
    f.session.refresh_from_db()
    captured = (timezone.now() - timedelta(seconds=1)).isoformat()
    def snapshot(token, text, floor, native_ref=None):
        return {'input_snapshot': {'token': token, 'text': text, 'origin': UI_ORIGIN._asdict(),
            'captured_at': captured, 'source_floor': floor, 'storage_incarnation': f.session.storage_incarnation,
            'history_epoch': f.session.history_epoch, 'ref': native_ref}}
    snapshots = [snapshot('u', 'actual request', 0, ref(f, 'u')._asdict()),
                 snapshot('mid', 'question during work', 2), snapshot('future', 'future input', 5)]
    history = timeline_messages.capture_message_context(f.session, before=5, occurred_at=timezone.now(),
                                                       input_origins=snapshots)
    assert [(entry['role'], entry['text']) for entry in history] == [
        ('assistant', 'first'), ('user', 'question during work'), ('system_notice', notice), ('assistant', 'second')]
    assert not TimelineEvent.objects.filter(data__text='question during work').exists()


def test_rejected_candidate_is_not_reclassified_by_referenced_recovery(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude rejection')
    f.append(f.message('a', 'routine progress'))
    row = TimelineDecision.objects.get()
    decide(row, 'NO')
    from twicc.core.services.session_timeline import record_event_sync
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={
        'message_refs': [ref(f, 'a')._asdict()]})
    timeline_messages.recover_referenced_messages(f.session.id)
    row.refresh_from_db()
    assert row.status == 'rejected'
    assert TimelineDecision.objects.count() == 1
    assert f.texts() == []


def test_exact_ineligible_final_settles_without_manufacturing_message(live_session):
    f = live_session
    f.append(f.message('not-final', 'tool activity', final=False))
    e = ending(f, [ref(f, 'not-final')])
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    e.refresh_from_db()
    assert not e.force_pending
    assert f.texts() == []
    assert not TimelineDecision.objects.filter(kind='message').exists()


def test_unknown_ending_scope_binds_only_after_exact_late_source(live_session):
    f = live_session
    e = ending(f, [ref(f, 'late')])
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    f.append(f.message('late', 'late answer'))
    e.refresh_from_db()
    f.session.refresh_from_db()
    assert e.storage_incarnation == f.session.storage_incarnation
    assert not e.force_pending
    assert f.texts() == ['late answer']


def test_conflicting_forced_unknown_collision_preserves_both_obligations(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude canonical decision')
    f.append(f.message('a', 'answer'))
    f.session.refresh_from_db()
    canonical = TimelineDecision.objects.get()
    duplicate = TimelineDecision.objects.get()
    duplicate.pk = uuid4()
    duplicate.storage_incarnation = None
    duplicate.forced, duplicate.status = True, 'accepted'
    duplicate.payload = {**duplicate.payload, 'target': {'text': 'different evidence'}}
    duplicate.save(force_insert=True)
    with pytest.raises(ValueError, match='Decision source scope binding collision'):
        timeline_messages._canonical_decision(f.session, identity_key=canonical.identity_key,
                                             observed_ref=NativeMessageRef('claude_message', 'a'))
    duplicate.refresh_from_db()
    canonical.refresh_from_db()
    assert duplicate.forced and duplicate.published_event_id is None
    assert canonical.status == 'pending'
    assert f.texts() == []


def test_bounded_context_collector_supports_both_provider_sources(live_session):
    f = live_session
    f.append(f.message('u', 'request', user=True), f.message('a', 'complete answer'),
             f.message('target', 'next answer'))
    f.session.refresh_from_db()
    context = timeline_messages.capture_message_context(f.session, before=3, occurred_at=timezone.now())
    assert [(entry['role'], entry['text']) for entry in context] == [
        ('user', 'request'), ('assistant', 'complete answer')]


@pytest.mark.parametrize('rollback', [False, True])
def test_forced_unknown_message_transfers_to_rejected_canonical(live_session, rollback):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude forcing transfer')
    f.append(f.message('a', 'answer'))
    canonical = TimelineDecision.objects.get()
    decide(canonical, 'NO')
    duplicate = TimelineDecision.objects.get()
    duplicate.pk = uuid4()
    duplicate.storage_incarnation = None
    duplicate.forced, duplicate.status = True, 'accepted'
    duplicate.save(force_insert=True)
    with transaction.atomic():
        assert timeline_decisions.publish_message_if_ready(decision_id=str(duplicate.pk))
        if rollback:
            transaction.set_rollback(True)
    duplicate.refresh_from_db()
    canonical.refresh_from_db()
    if rollback:
        assert duplicate.status == 'accepted' and duplicate.forced
        assert canonical.status == 'rejected' and not canonical.forced
        assert f.texts() == []
    assert timeline_decisions.publish_message_if_ready(decision_id=str(duplicate.pk))
    assert timeline_decisions.publish_message_if_ready(decision_id=str(duplicate.pk))
    duplicate.refresh_from_db()
    canonical.refresh_from_db()
    assert duplicate.status == 'invalidated'
    assert duplicate.payload['canonical_decision_id'] == str(canonical.pk)
    assert canonical.forced and canonical.status == 'accepted'
    assert duplicate.published_event_id is None
    assert f.texts() == ['answer']
    assert not timeline_decisions.resolve_decision(decision_id=str(duplicate.pk),
        classification=Classification('NO', 'late'), effective_route='provider', now=timezone.now())


def test_publication_uses_captured_group_boundary(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude grouping')
    f.append(f.message('first', 'original answer', message_id='group'))
    row = TimelineDecision.objects.get()
    f.append(f.message('later-copy', 'later copy', message_id='group'))
    assert decide(row)
    assert f.texts() == ['original answer']


def test_forcing_bounds_group_to_exact_frozen_terminal(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude exact terminal')
    f.append(f.message('old-final', 'old final', message_id='group'), capture=False)
    e = ending(f, [ref(f, 'old-final')])
    f.append(f.message('future-part', 'future content', message_id='group'), capture=False)
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert f.texts() == ['old final']


def test_old_ending_never_forces_a_conflicting_newer_group_snapshot(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude exact terminal')
    f.append(f.message('old-final', 'old final', message_id='group'), capture=False)
    f.append(f.message('future-part', 'future content', message_id='group'))
    e = ending(f, [ref(f, 'old-final')])
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    assert f.texts() == []
    e.refresh_from_db()
    assert e.force_pending


def test_outgoing_snapshot_keeps_native_identity_after_live_turn_cleanup(bridge):
    _, agent = bridge
    agent._timeline_capture_input('token', 'actual user input')
    native = NativeMessageRef('codex_client', 'native')
    agent._timeline_reserve_input('token', native)
    agent._timeline_input_refs.pop('token')
    assert agent._timeline_context_inputs()[0]['input_snapshot']['ref'] == native._asdict()


def test_redirect_rejects_replaced_scope(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude redirect')
    f.append(f.message('a', 'answer'))
    duplicate = TimelineDecision.objects.get()
    duplicate.pk = uuid4()
    duplicate.storage_incarnation = None
    duplicate.forced, duplicate.status = True, 'accepted'
    duplicate.save(force_insert=True)
    with patch('twicc.core.services.timeline_messages.record_event_sync', return_value=None):
        assert timeline_decisions.publish_message_if_ready(decision_id=str(duplicate.pk)) is None
    duplicate.refresh_from_db()
    assert duplicate.payload.get('canonical_decision_id')
    Session.objects.filter(pk=f.session.id).update(storage_incarnation='replacement')
    assert timeline_decisions.publish_message_if_ready(decision_id=str(duplicate.pk)) is None


def test_context_deduplicates_native_copies(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude context')
    f.append(f.message('u', 'request', user=True), f.message('a', 'answer'))
    f.append(f.message('a', 'answer'), f.message('next', 'next'))
    row = TimelineDecision.objects.get(payload__target__text='next')
    assert row.payload['previous_messages'] == [
        {'role': 'user', 'text': 'request', 'line': 1},
        {'role': 'assistant', 'text': 'answer', 'line': 2},
    ]


def test_missing_earlier_eligible_ref_keeps_obligation(live_session):
    f = live_session
    f.append(f.message('last', 'tool activity', final=False))
    e = ending(f, [ref(f, 'missing'), ref(f, 'last')])
    assert not timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    e.refresh_from_db()
    assert e.force_pending
    f.append(f.message('missing', 'late eligible answer'))
    e.refresh_from_db()
    assert not e.force_pending
    assert f.texts() == ['late eligible answer']



def test_eligible_last_ref_does_not_wait_for_irrelevant_earlier_ref(live_session):
    f = live_session
    f.append(f.message('last', 'final answer'))
    e = ending(f, [ref(f, 'missing'), ref(f, 'last')])
    assert timeline_decisions.fulfill_final_message_obligation(ending_id=str(e.pk))
    e.refresh_from_db()
    assert not e.force_pending
    assert f.texts() == ['final answer']
