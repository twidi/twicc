"""Official endings retain the exact final-publication result, once."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.test_timeline_messages import live_session
from tests.timeline_decision_helpers import accept_legacy_message_decisions
from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
from twicc.agent.semantic_types import DecisionRequest, SourceScope
from twicc.core.models import Session, TimelineDecision, TimelineEvent, TimelineKind
from twicc.core.services.logical_turns import open_logical_turn
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_decisions import (
    accept_fallback, admit_decision, apply_accepted_ending, fulfill_final_message_obligation,
)

__all__ = ['live_session', 'accept_legacy_message_decisions']
pytestmark = pytest.mark.django_db


def native(f, identity):
    return NativeMessageRef('claude_uuid' if f.provider == 'claude_code' else 'codex_item', identity,
                            None if f.provider == 'claude_code' else 'native-turn')


def ending(f, refs):
    f.session.refresh_from_db()
    session, now = f.session, timezone.now()
    turn_id, run_id = str(uuid4()), str(uuid4())
    open_logical_turn(turn_id=turn_id, session_id=session.id, provider=session.provider, run_id=run_id,
        process_run_id=None, ordinal=1, source_floor=0, storage_incarnation=session.storage_incarnation,
        history_epoch=session.history_epoch, opened_at=now)
    snapshot = CompletionSnapshot(turn_id, run_id, 1, 1, refs[0] if refs else None, tuple(refs), None)
    payload = {'run_id': run_id, 'turn_id': turn_id, 'source_floor': 0,
        'candidate': {'turn_id': turn_id, 'revision': 1, 'deadline': 10.0},
        'snapshot': {**snapshot._asdict(), 'first_ref': refs[0]._asdict() if refs else None,
                     'final_refs': [ref._asdict() for ref in refs]},
        'message_refs': [ref._asdict() for ref in refs], 'background_work': {'shells': 1}}
    request = DecisionRequest(str(uuid4()), 'ending', session.id,
        SourceScope(session.provider, session.storage_incarnation, session.history_epoch),
        payload, now + timedelta(seconds=45), 'provider')
    key = admit_decision(request, identity_key='ending:' + turn_id, occurred_at=now, not_before=now)
    assert accept_fallback(decision_id=key, reason='normal', now=now)
    return key


def apply(key):
    assert apply_accepted_ending(decision_id=key, now=timezone.now())
    return TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)


def resolution(event, value, **overrides):
    return record_event_sync(TimelineKind.TURN_ENDED, session_id=event.session_id,
        project_id=event.project_id, provider=event.provider, occurred_at=event.occurred_at,
        actor='agent', dedupe_key=event.dedupe_key, final_message_resolution={**value, **overrides})


def test_final_link_uses_authoritative_selection(live_session, accept_legacy_message_decisions):
    f = live_session
    f.append(f.message('progress', 'Progress A'), f.message('final', 'Final B'))
    key = ending(f, [native(f, 'progress'), native(f, 'final')])
    event = apply(key)
    final = f.events()[1]
    assert event.data['final_message'] == {'state': 'ready', 'event_id': final.pk,
        'storage_incarnation': final.data['storage_incarnation'], 'history_epoch': final.data['history_epoch']}
    assert fulfill_final_message_obligation(ending_id=key) is True
    assert f.texts() == ['Progress A', 'Final B']


def test_late_thinking_terminal_links_existing_message(live_session, accept_legacy_message_decisions):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude thinking terminal protocol')
    f.append(f.message('final', 'Final B'))
    key = ending(f, [native(f, 'final'), native(f, 'thinking')])
    event = apply(key)
    assert event.data['final_message']['state'] == 'pending'
    final_id = f.events()[0].pk
    thinking = f.message('thinking', '')
    thinking['message']['content'] = [{'type': 'thinking', 'thinking': 'private'}]
    f.append(thinking, capture=False)
    assert fulfill_final_message_obligation(ending_id=key) is True
    event.refresh_from_db()
    assert event.data['final_message']['event_id'] == final_id
    assert len(f.events()) == 1
    assert TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count() == 1


def test_empty_final_resolves_none(live_session, accept_legacy_message_decisions):
    f = live_session
    f.append(f.message('progress', 'Previous progress'), f.message('empty', '   '))
    event = apply(ending(f, [native(f, 'empty')]))
    assert event.data['final_message'] == {'state': 'none',
        'storage_incarnation': f.session.storage_incarnation, 'history_epoch': f.session.history_epoch}
    assert f.texts() == ['Previous progress']


@pytest.mark.parametrize('terminal', ['ready', 'none'])
def test_final_link_is_one_way(live_session, accept_legacy_message_decisions, monkeypatch, terminal):
    from twicc.core.services import timeline_signal
    f = live_session
    f.append(f.message('final', 'Final B'))
    key = ending(f, [native(f, 'missing')])
    event = apply(key)
    original = dict(event.data)
    original_columns = TimelineEvent.objects.filter(pk=event.pk).values().get()
    target = f.events()[0]
    value = {'state': terminal, 'storage_incarnation': f.session.storage_incarnation,
             'history_epoch': f.session.history_epoch}
    if terminal == 'ready':
        value['event_id'] = target.pk
    signals = []
    monkeypatch.setattr(timeline_signal, 'poke_on_commit', lambda **kwargs: signals.append(kwargs))
    with CaptureQueriesContext(connection) as queries:
        assert resolution(event, value).pk == event.pk
        assert resolution(event, value).pk == event.pk
    updates = [q['sql'] for q in queries if q['sql'].startswith('UPDATE "core_timelineevent"')]
    assert len(updates) == 1
    assert signals == [{'finalized_ending_ids': [event.pk]}]
    assert not any('core_sessionitem' in q['sql'] for q in queries)
    assert resolution(event, value, state='none' if terminal == 'ready' else 'ready', event_id=target.pk) is None
    assert resolution(event, value, history_epoch=f.session.history_epoch + 1) is None
    event.refresh_from_db()
    assert event.data == {**original, 'final_message': value}
    assert TimelineEvent.objects.filter(pk=event.pk).values().get() == {**original_columns, 'data': event.data}


def test_final_link_rejects_other_session_and_changed_scope(live_session, accept_legacy_message_decisions):
    f = live_session
    f.append(f.message('final', 'Final B'))
    event = apply(ending(f, [native(f, 'missing')]))
    target = f.events()[0]
    value = {'state': 'ready', 'event_id': target.pk, 'storage_incarnation': f.session.storage_incarnation,
             'history_epoch': f.session.history_epoch}
    other = Session.objects.create(id='other', project=f.session.project, provider=f.provider)
    wrong = record_event_sync(TimelineKind.ASSISTANT_MESSAGE_SENT, session_id=other.pk,
        project_id=other.project_id, provider=other.provider, actor='agent', data=target.data)
    assert resolution(event, value, event_id=wrong.pk) is None
    Session.objects.filter(pk=f.session.pk).update(history_epoch=f.session.history_epoch + 1)
    assert resolution(event, value) is None
    event.refresh_from_db()
    assert event.data['final_message']['state'] == 'pending'


def test_final_link_survives_decision_cleanup(live_session, accept_legacy_message_decisions):
    f = live_session
    f.append(f.message('final', 'Final B'))
    event = apply(ending(f, [native(f, 'final')]))
    TimelineDecision.objects.all().delete()
    event.refresh_from_db()
    assert event.data['final_message']['event_id'] == f.events()[0].pk


def test_final_link_storage_failure_retains_retry(live_session):
    f = live_session
    f.append(f.message('empty', '   '))
    key = ending(f, [native(f, 'empty')])
    def fail_resolution(execute, sql, params, many, context):
        if sql.startswith('UPDATE "core_timelineevent"'):
            raise OperationalError('Injected ending metadata storage failure')
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_resolution):
        event = apply(key)
    assert TimelineDecision.objects.get(pk=key).force_pending is True
    assert event.data['final_message']['state'] == 'pending'
    assert fulfill_final_message_obligation(ending_id=key) is True
    assert TimelineDecision.objects.get(pk=key).force_pending is False


@pytest.mark.parametrize('record_exists', [False, True])
def test_legacy_final_publication_clears_obligation(live_session, record_exists):
    f = live_session
    f.append(f.message('final', 'Final B'), capture=False)
    key = ending(f, [native(f, 'final')])
    row = TimelineDecision.objects.get(pk=key)
    if record_exists:
        event = record_event_sync(TimelineKind.TURN_ENDED, session_id=f.session.pk, actor='agent',
            occurred_at=row.occurred_at, dedupe_key=f'turn:{row.turn_id}:{TimelineKind.TURN_ENDED}', data={})
    assert fulfill_final_message_obligation(ending_id=key) is True
    assert TimelineDecision.objects.get(pk=key).force_pending is False
    assert f.texts() == ['Final B']
    if record_exists:
        event.refresh_from_db()
        assert event.data.get('final_message') is None


@pytest.mark.parametrize('outcome', ['normal', 'ended', 'provider_error'])
@pytest.mark.parametrize('has_ending', [False, True])
def test_live_boundary_initializes_only_normal_obligation(live_session, outcome, has_ending):
    from twicc.agent.timeline_hooks import record_logical_turn_boundary
    f = live_session
    f.append(f.message('empty', '   '), capture=False)
    f.session.refresh_from_db()
    turn_id, run_id = str(uuid4()), str(uuid4())
    snapshot = CompletionSnapshot(turn_id, run_id, 1, 2, None, (), outcome)
    scope = SourceScope(f.provider, f.session.storage_incarnation, f.session.history_epoch)
    context = SimpleNamespace(scope=scope, payload={'boundary_refs': []})
    agent = SimpleNamespace(session_id=f.session.pk, project_id=f.session.project_id, provider=f.provider,
        _completion_endings={turn_id: context} if has_ending else {},
        _timeline_refs_for_turn=lambda *args, **kwargs: [], _timeline_origins_for_refs=lambda refs: [])
    event_id = record_logical_turn_boundary(agent=agent, snapshot=snapshot, kind=TimelineKind.TURN_ENDED,
        occurred_at=timezone.now(), background_work=None)
    event = TimelineEvent.objects.get(pk=event_id)
    if has_ending and outcome == 'normal':
        assert event.data['final_message'] == {'state': 'pending',
            'storage_incarnation': f.session.storage_incarnation, 'history_epoch': f.session.history_epoch}
    else:
        assert event.data.get('final_message') is None
