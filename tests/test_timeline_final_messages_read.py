"""Read exact saved final snapshots without source or decision access."""

import asyncio
from datetime import timedelta

import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.models import Project, Session, SessionItem, TimelineEvent, TimelineKind
from twicc.core.services import timeline_feed
from twicc.core.services.session_timeline import record_event_sync

pytestmark = pytest.mark.django_db(transaction=True)


def resolve(ids, **kwargs):
    from twicc.core.services.timeline_final_messages import resolve_final_messages
    return resolve_final_messages(ids, **kwargs)


@pytest.fixture
def session():
    project = Project.objects.create(id='final-read', directory='/tmp/final-read')
    return Session.objects.create(id='final-reader', project=project, provider='codex',
        file_path='final.jsonl', storage_incarnation='source-one', history_epoch=2, title='Saved title')


def event(session, kind, data, **kwargs):
    row = record_event_sync(kind, session_id=session.id, project_id=session.project_id,
        provider=session.provider, actor='agent', data=data, **kwargs)
    assert row is not None
    return row


def pair(session, state='ready', **metadata):
    scope = {'storage_incarnation': session.storage_incarnation, 'history_epoch': session.history_epoch}
    message = event(session, TimelineKind.ASSISTANT_MESSAGE_SENT,
        {'text': 'Exact saved final', 'source_refs': [], 'source_line': 1, **scope},
        occurred_at=timezone.now() - timedelta(days=1))
    final = {'state': state, **scope, **metadata}
    if state == 'ready':
        final.setdefault('event_id', message.pk)
    ending = event(session, TimelineKind.TURN_ENDED, {'final_message': final})
    return ending, message


def assert_unavailable(body, ending_id):
    assert body == {'items': [{'ending_id': ending_id, 'state': 'unavailable',
                              'occurred_at': None, 'message': None}], 'sessions': {}}


def test_resolve_batch_reads_saved_text_only(session):
    pairs = [pair(session, state=('ready', 'pending', 'none')[i % 3]) for i in range(100)]
    with CaptureQueriesContext(connection) as queries:
        body = resolve([ending.pk for ending, _ in pairs])
    selects = [q['sql'] for q in queries if q['sql'].startswith('SELECT')]
    assert len(selects) == 3
    assert not any('core_sessionitem' in sql or 'core_timelinedecision' in sql for sql in selects)
    assert [item['state'] for item in body['items']] == [('ready', 'pending', 'none')[i % 3] for i in range(100)]
    assert body['items'][0]['message']['id'] == pairs[0][1].pk
    assert body['items'][0]['message']['data']['text'] == 'Exact saved final'
    assert body['items'][0]['occurred_at'] > body['items'][0]['message']['occurred_at']
    assert body['sessions'][session.id]['title'] == 'Saved title'
    assert pairs[0][1].pk not in [row['id'] for row in timeline_feed.fetch_feed(
        timeline_feed.FeedRequest((), 1, None, None))['events']]


def test_empty_request_performs_no_queries():
    with CaptureQueriesContext(connection) as queries:
        assert resolve([]) == {'items': [], 'sessions': {}}
    assert len(queries) == 0


def test_duplicate_ids_keep_request_order(session):
    a, _ = pair(session, 'pending')
    b, _ = pair(session, 'none')
    with CaptureQueriesContext(connection) as queries:
        body = resolve([b.pk, a.pk, b.pk])
    assert [item['ending_id'] for item in body['items']] == [b.pk, a.pk]
    assert len(queries) == 2  # No linked messages exist in this batch.


@pytest.mark.parametrize('ids', [[0], [-1], [True], ['1'], [1.2], [2**63], list(range(1, 102))])
def test_invalid_ids_are_rejected_without_db_work(ids):
    with CaptureQueriesContext(connection) as queries, pytest.raises(timeline_feed.FeedError):
        resolve(ids)
    assert len(queries) == 0


@pytest.mark.parametrize('change', ['hidden', 'muted', 'deleted', 'provider', 'project', 'source', 'epoch'])
def test_current_session_scope_and_visibility_are_required(session, change):
    ending, _ = pair(session)
    if change == 'deleted':
        session.delete()
    else:
        changes = {'hidden': {'hidden': True}, 'muted': {'mute_on_user_turn': True},
            'provider': {'provider': 'claude_code'}, 'project': {'project': Project.objects.create(id='moved-project', directory='/tmp/moved-final')},
            'source': {'storage_incarnation': 'replacement'}, 'epoch': {'history_epoch': 3}}
        Session.objects.filter(pk=session.pk).update(**changes[change])
    assert_unavailable(resolve([ending.pk]), ending.pk)


def test_muted_opt_in_still_excludes_hidden(session):
    ending, _ = pair(session)
    Session.objects.filter(pk=session.pk).update(mute_on_user_turn=True)
    assert resolve([ending.pk], include_muted=True)['items'][0]['state'] == 'ready'
    Session.objects.filter(pk=session.pk).update(hidden=True)
    assert_unavailable(resolve([ending.pk], include_muted=True), ending.pk)


@pytest.mark.parametrize('change', ['deleted', 'kind', 'provider', 'session', 'project', 'source', 'epoch', 'empty'])
def test_invalid_link_never_selects_another_message(session, change):
    ending, message = pair(session)
    event(session, TimelineKind.ASSISTANT_MESSAGE_SENT,
        {'text': 'Never substitute this', 'source_refs': [], 'source_line': 2})
    if change == 'deleted':
        message.delete()
    else:
        changes = {'kind': {'kind': TimelineKind.USER_MESSAGE_SENT}, 'provider': {'provider': 'claude_code'},
            'session': {'session_id': 'other'}, 'project': {'project_id': None},
            'source': {'data': {**message.data, 'storage_incarnation': 'old'}},
            'epoch': {'data': {**message.data, 'history_epoch': 1}},
            'empty': {'data': {**message.data, 'text': '  '}}}
        TimelineEvent.objects.filter(pk=message.pk).update(**changes[change])
    assert_unavailable(resolve([ending.pk]), ending.pk)


@pytest.mark.parametrize('metadata', [None, {}, {'state': 'ready'}, {'state': 'bogus'}])
def test_unproven_metadata_is_unavailable(session, metadata):
    ending = event(session, TimelineKind.TURN_ENDED, {'final_message': metadata})
    assert_unavailable(resolve([ending.pk]), ending.pk)


def test_pending_unknown_scope_is_pending_but_cannot_supply_text(session):
    ending, _ = pair(session, 'pending', storage_incarnation=None, history_epoch=None)
    item = resolve([ending.pk])['items'][0]
    assert item['state'] == 'pending' and item['message'] is None


def test_pending_replaced_scope_is_unavailable_without_decisions(session):
    ending, _ = pair(session, 'pending')
    Session.objects.filter(pk=session.pk).update(storage_incarnation='replacement')
    with CaptureQueriesContext(connection) as queries:
        assert_unavailable(resolve([ending.pk]), ending.pk)
    assert len(queries) == 2
    assert not any('core_timelinedecision' in q['sql'] for q in queries)


def test_wrong_kind_missing_id_and_scope_departure_are_unavailable(session):
    ending, message = pair(session)
    for ids, projects in [([message.pk], ()), ([999999], ()), ([ending.pk], ('other-project',))]:
        assert_unavailable(resolve(ids, project_ids=projects), ids[0])


def test_http_resolves_saved_message_and_expands_project_scope(session):
    parent = Project.objects.create(id='main-final', directory='/tmp/main-final')
    Project.objects.filter(pk=session.project_id).update(worktree_of=parent)
    ending, message = pair(session)
    response = asyncio.run(AsyncClient().get('/api/timeline/final-messages/',
        {'ending_ids': str(ending.pk), 'projects': parent.pk}))
    assert response.status_code == 200
    assert response.json()['items'][0]['message']['id'] == message.pk


@pytest.mark.parametrize('params', [{'ending_ids': '0'}, {'ending_ids': 'no'},
    {'ending_ids': '9' * 5000}, {'include_muted': 'yes'},
    {'ending_ids': ','.join(str(i) for i in range(1, 102))}])
def test_http_bad_requests_have_localized_errors(params):
    response = asyncio.run(AsyncClient().get('/api/timeline/final-messages/', params))
    assert response.status_code == 400
    assert response.json()['ui_message']['key'].startswith('backend.timeline.')


def test_ordinary_feed_does_not_resolve_associations(session):
    pair(session)
    with CaptureQueriesContext(connection) as queries:
        timeline_feed.fetch_feed(timeline_feed.FeedRequest((), 50, None, None))
    assert not any('core_timelinedecision' in q['sql'] or 'core_sessionitem' in q['sql'] for q in queries)
    assert not any('"core_timelineevent"."id" IN' in q['sql'] for q in queries)


def test_query_count_does_not_grow_with_transcript_or_batch_size(session):
    pairs = [pair(session) for _ in range(100)]
    for count, transcript_length in [(1, 0), (100, 1000)]:
        if transcript_length:
            SessionItem.objects.bulk_create([SessionItem(session=session, line_num=i + 1, content='{}')
                                             for i in range(transcript_length)])
        with CaptureQueriesContext(connection) as queries:
            body = resolve([ending.pk for ending, _ in pairs[:count]])
        assert len(body['items']) == count
        assert len(queries) == 3
        assert not any('core_sessionitem' in q['sql'] or 'core_timelinedecision' in q['sql'] for q in queries)


def test_invalid_terminal_scope_types_cannot_authorize_text(session):
    Session.objects.filter(pk=session.pk).update(history_epoch=1)
    session.history_epoch = 1
    ending, _ = pair(session, history_epoch=True)
    assert_unavailable(resolve([ending.pk]), ending.pk)


def test_linked_message_must_have_agent_identity(session):
    ending, message = pair(session)
    TimelineEvent.objects.filter(pk=message.pk).update(actor='user')
    assert_unavailable(resolve([ending.pk]), ending.pk)


def test_ending_must_have_agent_identity(session):
    ending, _ = pair(session)
    TimelineEvent.objects.filter(pk=ending.pk).update(actor='user')
    assert_unavailable(resolve([ending.pk]), ending.pk)


def test_http_empty_request_and_method_contract():
    assert asyncio.run(AsyncClient().get('/api/timeline/final-messages/')).json() == {'items': [], 'sessions': {}}
    assert asyncio.run(AsyncClient().post('/api/timeline/final-messages/')).status_code == 405
