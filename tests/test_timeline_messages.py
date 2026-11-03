"""Live message snapshots preserve source text and exact native identities."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from tests import content_storage_helpers
from twicc.agent.completion_types import NativeMessageRef
from twicc.core.models import LogicalTurn, Project, Session, TimelineEvent
from twicc.providers.live_sync import LiveSyncLimits

content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db


@pytest.fixture(params=['claude_code', 'codex'])
def live_session(request, tmp_path):
    provider = request.param
    session = Session.objects.create(id='snapshot-session', project=Project.objects.create(id='snapshot-project'),
                                     provider=provider, file_path='snapshot.jsonl')
    path = tmp_path / 'snapshot.jsonl'
    path.touch()
    if provider == 'claude_code':
        from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
        compute = ClaudeCodeSessionCompute()
    else:
        from twicc.providers.codex.compute import CodexSessionCompute
        compute = CodexSessionCompute()
    boundary = timezone.now() - timedelta(seconds=1)
    timestamp = timezone.now().isoformat()

    def message(identity, text, *, user=False, final=True, message_id=None, **extra):
        if provider == 'claude_code':
            result = {'type': 'user' if user else 'assistant', 'uuid': identity, 'timestamp': timestamp,
                      'message': {'id': message_id or identity, 'role': 'user' if user else 'assistant',
                                  'content': [{'type': 'text', 'text': text}],
                                  'stop_reason': None if final is None else ('end_turn' if final else 'tool_use')}}
            result.update(extra)
            return result
        return {'type': 'event_msg', 'timestamp': timestamp, 'payload': {
            'type': 'item_completed', 'thread_id': session.id, 'turn_id': 'native-turn',
            'item': {'id': identity, 'type': 'UserMessage' if user else 'AgentMessage',
                     'content': [{'type': 'text' if user else 'Text', 'text': text}],
                     'phase': None if final is None else ('final_answer' if final else 'commentary'), **extra}}}

    def append(*records, capture=True, limit=500, since=None):
        with path.open('ab') as stream:
            for record in records:
                stream.write(orjson.dumps(record) + b'\n')
        while True:
            kwargs = {'timeline_since': boundary if since is None else since} if capture else {}
            result = compute.sync_session_slice(session.id, path, limits=LiveSyncLimits(limit), **kwargs)
            if not result.has_more:
                break

    def events():
        return list(TimelineEvent.objects.filter(session_id=session.id, kind__in=[
            'user_message.sent', 'assistant_message.sent']).order_by('id'))

    return SimpleNamespace(session=session, path=path, compute=compute, provider=provider, boundary=boundary,
                           message=message, append=append, events=events,
                           texts=lambda: [event.data['text'] for event in events()])


def test_records_all_users_and_finals_in_source_order(live_session):
    f = live_session
    f.append(f.message('u1', 'A', user=True), f.message('u2', 'B', user=True),
             f.message('a1', 'reply B'), f.message('a2', 'reply A'))
    assert f.texts() == ['A', 'B', 'reply B', 'reply A']
    assert [event.kind for event in f.events()] == [
        'user_message.sent', 'user_message.sent', 'assistant_message.sent', 'assistant_message.sent']
    assert all('role' not in event.data for event in f.events())
    assert [event.data['source_line'] for event in f.events()] == [1, 2, 3, 4]
    assert all(event.actor == 'agent' for event in f.events()[2:])
    assert all(event.via is None for event in f.events()[:2])


def test_replay_and_old_first_discovery_do_not_capture(live_session):
    f = live_session
    f.append(f.message('import', 'imported'), capture=False)
    old = f.message('old', 'historical')
    old['timestamp'] = (f.boundary - timedelta(days=1)).isoformat()
    f.append(old, f.message('new', 'new'))
    f.append(f.message('new', 'new'), f.message('other', 'new'))
    assert f.texts() == ['new', 'new']
    assert all(len(event.dedupe_key) < 100 for event in f.events())


def test_markdown_is_verbatim_and_empty_content_is_absent(live_session):
    f = live_session
    text = '    indented code\ntrailing spaces  \n**Markdown**'
    f.append(f.message('u', text, user=True), f.message('a', text), f.message('blank', '  '))
    assert f.texts() == [text, text]


def test_nonfinal_and_unknown_assistants_are_not_selected(live_session):
    f = live_session
    f.append(f.message('commentary', 'working', final=False), f.message('unknown', 'unknown', final=None))
    assert f.texts() == []


def test_claude_group_waits_for_terminal_block_and_joins_verbatim(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude group protocol')
    f.append(f.message('block-1', '**First**  ', message_id='api-message', final=None))
    assert f.texts() == []
    f.append(f.message('block-2', '    Second', message_id='api-message'))
    assert f.texts() == ['**First**  \n    Second']
    refs = f.events()[0].data['source_refs']
    assert {ref['native_id'] for ref in refs} == {'api-message', 'block-1', 'block-2'}


def test_claude_closing_nontext_block_completes_text(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude group protocol')
    first = f.message('text', 'answer', message_id='api-message', final=None)
    last = f.message('closing', '', message_id='api-message')
    last['message']['content'] = [{'type': 'thinking', 'thinking': 'private'}]
    f.append(first, last, limit=1)
    assert f.texts() == ['answer']


def test_verified_uuid_waits_until_exact_terminal_is_indexed(live_session):
    from twicc.core.services.timeline_messages import record_verified_messages
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude group protocol')
    f.append(f.message('prefix', 'first', message_id='api-message', final=None))
    record_verified_messages(f.session.id, refs=[NativeMessageRef('claude_message', 'api-message')],
                             timeline_since=f.boundary)
    record_verified_messages(f.session.id, refs=[NativeMessageRef('claude_uuid', 'terminal')],
                             timeline_since=f.boundary)
    assert f.texts() == []
    f.append(f.message('terminal', 'last', message_id='api-message', final=None))
    record_verified_messages(f.session.id, refs=[NativeMessageRef('claude_uuid', 'terminal')],
                             timeline_since=f.boundary)
    assert f.texts() == ['first\nlast']


def test_codex_async_question_and_client_alias(live_session):
    f = live_session
    if f.provider != 'codex':
        pytest.skip('Codex protocol')
    f.append(f.message('question', 'question?', delivery='async'), f.message('u', 'input', user=True, clientId='client'))
    assert f.texts() == ['input']
    assert {'kind': 'codex_client', 'native_id': 'client', 'turn_id': None} in f.events()[0].data['source_refs']


def test_recovery_reads_only_saved_live_references(live_session):
    from twicc.core.services.timeline_messages import recover_referenced_messages
    f = live_session
    f.append(f.message('old', 'unreferenced'), f.message('saved', 'retained'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=1, first_ref=None,
        final_refs=[NativeMessageRef(kind, 'saved', 'native-turn' if f.provider == 'codex' else None)._asdict()],
        opened_at=f.boundary)
    from twicc.core.services.session_timeline import record_event_sync
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={
        'message_refs': [NativeMessageRef(kind, 'saved', 'native-turn' if f.provider == 'codex' else None)._asdict()]})
    recover_referenced_messages(f.session.id)
    recover_referenced_messages(f.session.id)
    assert f.texts() == ['retained']


def test_claude_no_usage_prefix_does_not_suppress_later_cost(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude billing groups')
    prefix = f.message('prefix', 'first', message_id='api-message', final=None)
    terminal = f.message('terminal', 'last', message_id='api-message')
    terminal['message'].update(model='claude-sonnet-4-5-20250929', usage={'input_tokens': 100, 'output_tokens': 100})
    f.append(prefix)
    f.append(terminal)
    costs = list(f.session.items.order_by('line_num').values_list('cost', flat=True))
    assert costs[0] is None
    assert costs[1] is not None and costs[1] > 0
    duplicate = f.message('duplicate', 'last', message_id='api-message')
    duplicate['message'].update(terminal['message'])
    f.append(duplicate)
    assert f.session.items.get(line_num=3).cost is None
    from queue import Queue
    queue = Queue()
    f.compute.compute_session_metadata(f.session.id, queue, 1)
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    complete = next(message for message in messages if message['type'] == 'session_complete')
    assert f.compute.apply_session_complete(complete).outcome == 'applied'
    assert list(f.session.items.order_by('line_num').values_list('cost', flat=True)) == [*costs, None]
    assert f.texts() == ['first\nlast']


def test_malformed_source_does_not_hide_following_message(live_session):
    f = live_session
    malformed = {'type': 'assistant', 'uuid': 'broken', 'message': ['invalid']} if f.provider == 'claude_code' else {
        'type': 'event_msg', 'payload': ['invalid']}
    f.append(malformed, f.message('good', 'retained'))
    assert f.texts() == ['retained']


def test_old_logical_turn_without_timeline_refs_never_backfills(live_session):
    from twicc.core.services.timeline_messages import recover_referenced_messages
    f = live_session
    f.append(f.message('first', 'old final'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=0,
        first_ref=NativeMessageRef(kind, 'first', 'native-turn' if f.provider == 'codex' else None)._asdict(),
        final_refs=[], opened_at=f.boundary)
    recover_referenced_messages(f.session.id)
    assert f.texts() == []


def test_current_compressed_content_is_used(live_session, content_row_factory):
    from twicc.core.services.timeline_messages import record_indexed_messages
    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    f = live_session
    record = f.message('compressed', '    code  ')
    content = content_row_factory(orjson.dumps(record).decode(), True, f.provider)
    SessionItem.objects.create(session=f.session, line_num=1, kind=ItemKind.ASSISTANT_MESSAGE,
        timestamp=timezone.now(), message_id='compressed' if f.provider == 'claude_code' else None,
        content='stale', current_content=content)
    record_indexed_messages(f.session.id, new_line_nums=[1], timeline_since=f.boundary)
    assert f.texts() == ['    code  ']


def test_claude_excluded_material_and_slash_arguments(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude source shapes')
    command = '<command-name>/custom</command-name>\n<command-args>  **bold**  </command-args>'
    compaction = f.message('compact', 'summary', user=True, isCompactSummary=True)
    tool = f.message('tool', '', final=False)
    tool['message']['content'] = [{'type': 'tool_use', 'id': 'call', 'name': 'Read', 'input': {}}]
    thinking = f.message('thinking', '', final=None)
    thinking['message']['content'] = [{'type': 'thinking', 'thinking': 'private'}]
    pause = f.message('pause', 'pause')
    pause['message']['stop_reason'] = 'pause_turn'
    result = f.message('result', '', user=True)
    result['message']['content'] = [{'type': 'tool_result', 'tool_use_id': 'call', 'content': 'private'}]
    local = {'type': 'system', 'subtype': 'local_command', 'timestamp': timezone.now().isoformat(),
             'content': '<local-command-stdout>local output</local-command-stdout>'}
    f.append(compaction, tool, thinking, pause, result, local, f.message('command', command, user=True))
    assert f.texts() == [command]


def test_multiple_text_blocks_preserve_spaces(live_session):
    f = live_session
    record = f.message('blocks', 'first  ')
    if f.provider == 'claude_code':
        record['message']['content'].append({'type': 'text', 'text': '    second'})
        expected = 'first  \n    second'
    else:
        record['payload']['item']['content'].append({'type': 'Text', 'text': '    second'})
        expected = 'first      second'
    f.append(record)
    assert f.texts() == [expected]


def test_durable_turn_refs_recover_after_journal_expires(live_session):
    from twicc.core.services.session_timeline import record_event_sync
    from twicc.core.services.timeline_messages import recover_referenced_messages
    f = live_session
    f.append(f.message('durable', 'late text'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'durable', 'native-turn' if f.provider == 'codex' else None)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref._asdict()]})
    recover_referenced_messages(f.session.id)
    assert f.texts() == ['late text']


def test_verified_nonfinal_verdict_always_wins(live_session):
    from twicc.core.services.timeline_messages import record_verified_messages
    f = live_session
    f.append(f.message('nonfinal', 'working', final=False))
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    record_verified_messages(f.session.id,
        refs=[NativeMessageRef(kind, 'nonfinal', 'native-turn' if f.provider == 'codex' else None)],
        timeline_since=f.boundary)
    assert f.texts() == []


def test_saved_reference_keeps_verified_capture_eligible_after_restart(live_session, content_row_factory):
    from twicc.core.services.session_timeline import record_event_sync
    from twicc.core.services.timeline_messages import record_verified_messages
    f = live_session
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'late', 'native-turn' if f.provider == 'codex' else None)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref._asdict()]})
    # Index directly, without the publication recovery hook, to test the admitted
    # proof writer when the source predates its new watcher's capture boundary.
    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    content = content_row_factory(orjson.dumps(f.message('late', 'delayed')).decode(), True, f.provider)
    SessionItem.objects.create(session=f.session, line_num=1, kind=ItemKind.ASSISTANT_MESSAGE,
        timestamp=timezone.now(), message_id='late' if f.provider == 'claude_code' else None,
        content='', current_content=content)
    record_verified_messages(f.session.id, refs=[ref], timeline_since=timezone.now() + timedelta(days=1))
    assert f.texts() == ['delayed']


def test_claude_image_blocks_do_not_add_blank_text(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split image block')
    text = f.message('text', 'answer', message_id='api', final=None)
    image = f.message('image', '', message_id='api')
    image['message']['content'] = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}}]
    f.append(text, image)
    assert f.texts() == ['answer']


def test_recovery_does_not_read_source_without_pending_timeline_refs(live_session):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.core.services.timeline_messages import recover_referenced_messages
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    with CaptureQueriesContext(connection) as queries:
        recover_referenced_messages(f.session.id)
    assert not any('core_sessionitem' in query['sql'].lower() for query in queries)
    f.append(f.message('saved', 'complete'))
    refs = f.events()[0].data['source_refs']
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': refs})
    with CaptureQueriesContext(connection) as queries:
        recover_referenced_messages(f.session.id)
    assert not any('core_sessionitem' in query['sql'].lower() for query in queries)


def test_recovery_rejects_replaced_logical_turn_scope(live_session):
    from twicc.core.services.timeline_messages import recover_referenced_messages
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    f.append(f.message('saved', 'old source'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'saved', 'native-turn' if f.provider == 'codex' else None)._asdict()
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=0, first_ref=None, final_refs=[ref],
        storage_incarnation='old-incarnation', opened_at=f.boundary)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref]})
    recover_referenced_messages(f.session.id)
    assert f.texts() == []


def test_duplicate_native_records_in_one_slice_do_not_repeat_text(live_session):
    f = live_session
    user = f.message('u', 'input', user=True)
    assistant = f.message('a', 'answer')
    f.append(user, user, assistant, assistant)
    assert f.texts() == ['input', 'answer']


def test_live_slice_does_not_scan_history_for_a_missing_durable_reference(live_session):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    f.append(f.message('history', 'old content'), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'missing', 'native-turn' if f.provider == 'codex' else None)._asdict()
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref]})
    with CaptureQueriesContext(connection) as queries:
        f.append(f.message('new', 'new content'))
    assert f.texts() == ['new content']
    assert not any('"core_sessionitem"."line_num" > 0' in query['sql'] for query in queries)


def test_native_subagent_transcripts_do_not_create_timeline_messages(live_session):
    f = live_session
    Session.objects.filter(pk=f.session.id).update(type='subagent')
    f.append(f.message('user', 'agent prompt', user=True), f.message('assistant', 'agent reply'))
    assert f.texts() == []


def test_saved_uuid_recovers_a_new_live_group_after_restart(live_session):
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split completion proof')
    refs = [NativeMessageRef('claude_message', 'api')._asdict(), NativeMessageRef('claude_uuid', 'terminal')._asdict()]
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': refs})
    restarted_boundary = timezone.now() + timedelta(days=1)
    f.append(f.message('prefix', 'first', message_id='api', final=None), since=restarted_boundary)
    assert f.texts() == []
    f.append(f.message('terminal', 'last', message_id='api', final=None), since=restarted_boundary)
    assert f.texts() == ['first\nlast']


def test_saved_refs_cannot_admit_live_text_from_a_replaced_source(live_session):
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'old', 'native-turn' if f.provider == 'codex' else None)._asdict()
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=0, first_ref=None, final_refs=[ref],
        storage_incarnation='replaced', opened_at=f.boundary)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref]})
    f.append(f.message('old', 'replaced text'), since=timezone.now() + timedelta(days=1))
    assert f.texts() == []


def test_user_origin_requires_explicit_source_evidence(live_session):
    f = live_session
    record = f.message('agent-input', 'submitted by agent', user=True)
    record['_origin'] = {'actor': 'agent', 'via': 'cli', 'actor_ref': 'sender', 'actor_name': None}
    f.append(record, f.message('unknown-input', 'unknown sender', user=True))
    explicit, unknown = f.events()
    assert (explicit.actor, explicit.via, explicit.actor_ref) == ('agent', 'cli', 'sender')
    assert (unknown.actor, unknown.via) == ('external', None)


def test_attachment_images_do_not_enter_saved_text(live_session):
    f = live_session
    record = f.message('with-image', 'caption', user=True)
    if f.provider == 'claude_code':
        record['message']['content'].append({'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': 'AA=='}})
    else:
        record['payload']['item']['content'].append({'type': 'image', 'url': 'data:image/png;base64,AA=='})
    f.append(record)
    assert f.texts() == ['caption']


@pytest.mark.parametrize('publication', ['live', 'recovery'])
def test_additional_user_ref_rejects_the_full_invalid_source_group(live_session, publication):
    from twicc.core.services.session_timeline import record_event_sync
    from twicc.core.services.timeline_messages import recover_referenced_messages
    f = live_session
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    native_turn = 'native-turn' if f.provider == 'codex' else None
    first = NativeMessageRef(kind, 'first-user', native_turn)._asdict()
    extra = NativeMessageRef(kind, 'extra-user', native_turn)._asdict()
    expired = NativeMessageRef(kind, 'expired-user', native_turn)._asdict()
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider, run_id=uuid4(),
        ordinal=1, position=1, source_floor=0, first_ref=first, final_refs=[],
        storage_incarnation='replaced', opened_at=f.boundary)
    records = [f.message('extra-user', 'replaced text', user=True),
               f.message('expired-user', 'expired journal text', user=True)]
    if publication == 'recovery':
        f.append(*records, capture=False)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [first, extra]})
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [expired]})
    if publication == 'live':
        f.append(*records, since=timezone.now() + timedelta(days=1))
    else:
        recover_referenced_messages(f.session.id)
    assert f.texts() == ['expired journal text']


def test_delayed_claude_groups_keep_first_source_order(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split groups')
    f.append(f.message('a-prefix', 'A1', message_id='a', final=None),
             f.message('b-prefix', 'B1', message_id='b', final=None))
    assert f.texts() == []
    f.append(f.message('b-final', 'B2', message_id='b'),
             f.message('a-final', 'A2', message_id='a'))
    events = f.events()
    assert len({event.occurred_at for event in events}) == 1
    assert [event.data['source_line'] for event in events] == [1, 2]
    assert f.texts() == ['A1\nA2', 'B1\nB2']


def test_markerless_live_group_uses_open_journal_final_proof(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude terminal UUID proof')
    ref = NativeMessageRef('claude_uuid', 'terminal')
    LogicalTurn.objects.create(id=uuid4(), session_id=f.session.id, provider=f.provider,
        run_id=uuid4(), ordinal=1, position=1, source_floor=0, opened_at=f.boundary,
        final_refs=[ref._asdict()])
    f.append(f.message('first', 'first', message_id='api', final=None))
    assert f.texts() == []
    f.append(f.message('terminal', 'last', message_id='api', final=None))
    assert f.texts() == ['first\nlast']
    assert not TimelineEvent.objects.filter(kind='turn.ended').exists()


def test_startup_metadata_publication_recovers_only_saved_refs(live_session):
    from queue import Queue
    from twicc.core.services.session_timeline import record_event_sync
    f = live_session
    f.append(f.message('history', 'unreferenced'), f.message('late', 'delayed', final=None), capture=False)
    kind = 'claude_uuid' if f.provider == 'claude_code' else 'codex_item'
    ref = NativeMessageRef(kind, 'late', 'native-turn' if f.provider == 'codex' else None)
    record_event_sync('turn.ended', session_id=f.session.id, actor='agent', data={'message_refs': [ref._asdict()]})
    # Startup has no live agent or retained journal. Metadata publication makes
    # the existing exact sources readable and resolves only durable references.
    queue = Queue()
    f.compute.compute_session_metadata(f.session.id, queue, 1)
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    complete = next(message for message in messages if message['type'] == 'session_complete')
    assert f.compute.apply_session_complete(complete).outcome == 'applied'
    assert f.texts() == ['delayed']
    f.compute.compute_session_metadata(f.session.id, queue, 2)
    messages = [orjson.loads(queue.get()) for _ in range(queue.qsize())]
    complete = next(message for message in messages if message['type'] == 'session_complete')
    assert f.compute.apply_session_complete(complete).outcome == 'applied'
    assert f.texts() == ['delayed']


def test_persisted_shell_notice_does_not_create_user_snapshot(live_session):
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    f = live_session
    f.append(f.message('shell-notice', SHELL_NOTICE_HEADER + ': background shell(s) still running', user=True))
    assert f.texts() == []
