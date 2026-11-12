"""Prepared native source reads keep content work outside admitted writers."""

from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from tests import content_storage_helpers
from twicc.agent.completion_types import NativeMessageRef
from twicc.core.content_storage import readers
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem

content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db


@pytest.fixture(params=['claude_code', 'codex'])
def source(request, content_row_factory):
    session = Session.objects.create(id='prepared-source', provider=request.param,
        project=Project.objects.create(id='prepared-project'), storage_incarnation=str(uuid4()))

    def add(line, identity, *, group=None, legacy=False, user=False):
        if session.provider == 'claude_code':
            parsed = {'type': 'user' if user else 'assistant', 'uuid': identity,
                'message': {'id': group or identity, 'role': 'user' if user else 'assistant',
                    'content': [{'type': 'text', 'text': identity}], 'stop_reason': 'end_turn'}}
        else:
            parsed = {'type': 'event_msg', 'payload': {'type': 'item_completed',
                'thread_id': session.id, 'turn_id': 'turn', 'item': {'id': identity,
                    'type': 'UserMessage' if user else 'AgentMessage', 'phase': 'final_answer',
                    'content': [{'type': 'Text', 'text': identity}]}}}
        text = orjson.dumps(parsed).decode()
        content = None if legacy else content_row_factory(text, True, session.provider)
        return SessionItem.objects.create(session=session, line_num=line,
            kind=ItemKind.USER_MESSAGE if user else ItemKind.ASSISTANT_MESSAGE,
            message_id=group or identity if session.provider == 'claude_code' else None,
            timestamp=timezone.now(), content=text if legacy else '', current_content=content)

    def ref(identity):
        return NativeMessageRef('claude_uuid', identity) if session.provider == 'claude_code' else (
            NativeMessageRef('codex_item', identity, 'turn'))

    return SimpleNamespace(session=session, add=add, ref=ref)


def api():
    from twicc.core.services import timeline_source_reads
    return timeline_source_reads


def lines(groups):
    return [[item.line_num for item, _, _ in group] for group in groups]


def test_prepared_lookup_never_decodes_and_context_is_scoped(source, monkeypatch):
    source.add(1, 'answer')
    module = api()
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    assert module.current_source_reads(source.session.id) is None

    def forbidden(*args, **kwargs):
        pytest.fail('Prepared lookup or admission decodes source content')

    monkeypatch.setattr(readers, 'decode_content', forbidden)
    with module.prepared_source_reads(prepared):
        assert module.current_source_reads(source.session.id) is prepared
        assert module.current_source_reads('other-session') is None
        assert lines(prepared.resolve([source.ref('answer')])) == [[1]]
        assert prepared.resolve([source.ref('unprepared')]) == []
        assert prepared.is_current()
    assert module.current_source_reads(source.session.id) is None


def test_missing_ref_reuses_search_and_observes_late_low_line_insert(source, monkeypatch):
    source.add(1, 'old')
    source.add(10, 'newer')
    module = api()
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    request = [([source.ref('late')], 0, 10)]
    assert module.prepare_source_reads(source.session.id, request).resolve([source.ref('late')], ceiling=10) == []
    assert len(decoded) == 2
    decoded.clear()
    assert module.prepare_source_reads(source.session.id, request).resolve([source.ref('late')], ceiling=10) == []
    assert decoded == []
    late = source.add(5, 'late')
    prepared = module.prepare_source_reads(source.session.id, request)
    assert lines(prepared.resolve([source.ref('late')], ceiling=10)) == [[5]]
    assert set(decoded) == {late.current_content_id}


def test_initial_search_respects_floor(source, monkeypatch):
    source.add(1, 'old')
    source.add(20, 'answer')
    original = readers.decode_content
    decoded = []

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('missing')], 10, 30)])
    assert prepared.resolve([source.ref('missing')], floor=10, ceiling=30) == []
    assert len(decoded) == 1


def test_scope_rewrite_discards_missing_locator(source):
    item = source.add(1, 'old')
    module = api()
    request = [([source.ref('replacement')], 0, None)]
    old = module.prepare_source_reads(source.session.id, request)
    Session.objects.filter(pk=source.session.pk).update(history_epoch=1)
    SessionItem.objects.filter(pk=item.pk).delete()
    source.add(1, 'replacement')
    assert not old.is_current()
    prepared = module.prepare_source_reads(source.session.id, request)
    assert lines(prepared.resolve([source.ref('replacement')])) == [[1]]


@pytest.mark.parametrize('change', ['kind', 'timestamp', 'message_id', 'line_num', 'current_content'])
def test_admission_rejects_changed_used_metadata(source, change):
    item = source.add(1, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    value = {'kind': ItemKind.USER_MESSAGE, 'timestamp': timezone.now(), 'message_id': 'changed',
        'line_num': 2, 'current_content': None}[change]
    SessionItem.objects.filter(pk=item.pk).update(**{change: value})
    assert not prepared.is_current()


def test_admission_compares_legacy_plaintext(source):
    item = source.add(1, 'answer', legacy=True)
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    SessionItem.objects.filter(pk=item.pk).update(content='changed')
    assert not prepared.is_current()


def test_exact_claude_uuid_freezes_complete_group(source):
    if source.session.provider != 'claude_code':
        pytest.skip('Claude split groups')
    source.add(1, 'prefix', group='group')
    source.add(3, 'terminal', group='group')
    source.add(4, 'future', group='group')
    module = api()
    prepared = module.prepare_source_reads(source.session.id,
        [([source.ref('terminal')], 0, 10)])
    assert lines(prepared.resolve([source.ref('terminal')], ceiling=10)) == [[1, 3]]
    impostor = module.prepare_source_reads(source.session.id, [([source.ref('group')], 0, 10)])
    assert impostor.resolve([source.ref('group')], ceiling=10) == []
    source.add(5, 'unrelated')
    assert prepared.is_current()
    source.add(2, 'late-fragment', group='group')
    assert not prepared.is_current()


def test_existing_malformed_group_member_has_a_metadata_proof(source, content_row_factory):
    if source.session.provider != 'claude_code':
        pytest.skip('Claude split groups')
    source.add(1, 'prefix', group='group')
    malformed = SessionItem.objects.create(session=source.session, line_num=2, message_id='group',
        content='', current_content=content_row_factory('{"type":"assistant"}', True, 'claude_code'))
    source.add(3, 'terminal', group='group')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('terminal')], 0, None)])
    assert lines(prepared.resolve([source.ref('terminal')])) == [[1, 3]]
    assert prepared.is_current()
    SessionItem.objects.filter(pk=malformed.pk).update(current_content=None, content='changed')
    assert not prepared.is_current()


def test_batch_search_decodes_each_candidate_once(source, monkeypatch):
    source.add(1, 'first')
    source.add(2, 'second')
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = api().prepare_source_reads(source.session.id,
        [([source.ref('first')], 0, None), ([source.ref('second')], 0, None)])
    assert lines(prepared.resolve([source.ref('first')])) == [[1]]
    assert lines(prepared.resolve([source.ref('second')])) == [[2]]
    assert len(decoded) == len(set(decoded)) == 2


def test_prepared_groups_support_aliases_and_narrower_requests(source, monkeypatch):
    if source.session.provider != 'claude_code':
        pytest.skip('Claude split groups')
    source.add(1, 'prefix', group='group')
    source.add(3, 'terminal', group='group')
    prepared = api().prepare_source_reads(source.session.id,
        [([source.ref('terminal')], 0, None)])

    def forbidden(*args, **kwargs):
        pytest.fail('Derived source request reads content')

    monkeypatch.setattr(readers, 'decode_content', forbidden)
    assert lines(prepared.resolve([source.ref('terminal')], ceiling=3)) == [[1, 3]]
    assert lines(prepared.resolve([source.ref('prefix')], ceiling=3)) == [[1]]
    alias = NativeMessageRef('claude_message', 'group')
    assert lines(prepared.resolve([alias], ceiling=3)) == [[1, 3]]
    assert prepared.resolve([alias], ceiling=4) == []
    assert prepared.resolve([alias], floor=-1, ceiling=3) == []
    assert prepared.resolve([alias], floor=1, ceiling=3) == []


def test_prepared_superset_keeps_unknown_exact_refs_absent(source, monkeypatch):
    source.add(1, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])

    def forbidden(*args, **kwargs):
        pytest.fail('Superset lookup reads content')

    monkeypatch.setattr(readers, 'decode_content', forbidden)
    groups = prepared.resolve([source.ref('missing'), source.ref('answer')])
    assert lines(groups) == [[1]]
    assert all(source.ref('missing') not in aliases for group in groups for _, _, aliases in group)


@pytest.mark.parametrize('field,value', [('provider', 'changed'), ('storage_incarnation', 'replacement')])
def test_admission_rejects_changed_source_scope(source, field, value):
    source.add(1, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    Session.objects.filter(pk=source.session.pk).update(**{field: value})
    assert not prepared.is_current()


def test_admission_retries_new_candidates_for_an_unresolved_ref(source):
    source.add(10, 'existing')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('late')], 0, 10)])
    assert prepared.is_current()
    source.add(5, 'late')
    assert not prepared.is_current()


def test_new_terminal_ref_reuses_successful_interval_search(source, monkeypatch):
    source.add(1, 'old')
    source.add(2, 'first-answer')
    module = api()
    module.prepare_source_reads(source.session.id, [([source.ref('first-answer')], 0, None)])
    latest = source.add(3, 'next-answer')
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('next-answer')], 0, None)])
    assert lines(prepared.resolve([source.ref('next-answer')])) == [[3]]
    assert decoded == [latest.current_content_id]


def test_advancing_frozen_ceiling_reads_only_new_candidates(source, monkeypatch):
    source.add(1, 'old')
    source.add(2, 'first-answer')
    module = api()
    module.prepare_source_reads(source.session.id, [([source.ref('first-answer')], 0, 2)])
    latest = source.add(3, 'next-answer')
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('next-answer')], 0, 3)])
    assert lines(prepared.resolve([source.ref('next-answer')], ceiling=3)) == [[3]]
    assert decoded == [latest.current_content_id]


def test_locator_eviction_discards_its_negative_search_proof(source, monkeypatch):
    source.add(1, 'old')
    source.add(2, 'middle')
    source.add(3, 'answer')
    module = api()
    monkeypatch.setattr(module, '_ALIAS_LIMIT', 2)
    module.prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('old')], 0, None)])
    assert lines(prepared.resolve([source.ref('old')])) == [[1]]


def test_late_duplicate_exact_identity_invalidates_the_prepared_selection(source):
    source.add(3, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 0, None)])
    source.add(1, 'answer', group='other-group', legacy=True)
    assert not prepared.is_current()


def test_expanded_ceiling_finds_existing_pk_outside_the_old_interval(source):
    source.add(1, 'old')
    source.add(10, 'answer')
    module = api()
    module.prepare_source_reads(source.session.id, [([source.ref('answer')], 0, 5)])
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('answer')], 0, 10)])
    assert lines(prepared.resolve([source.ref('answer')], ceiling=10)) == [[10]]


def test_observed_context_rows_join_the_admission_proof(source):
    context = source.add(1, 'context')
    source.add(3, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 2, None)])
    readers.load_current([context])
    prepared.observe([context], floor=0, ceiling=2)
    source.add(4, 'future')
    assert prepared.is_current()
    SessionItem.objects.filter(pk=context.pk).update(current_content=None, content='changed')
    assert not prepared.is_current()


def test_observed_context_rejects_late_insertions_within_its_boundary(source):
    source.add(3, 'answer')
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('answer')], 2, None)])
    prepared.observe([], floor=0, ceiling=2)
    assert prepared.is_current()
    source.add(1, 'late-context')
    assert not prepared.is_current()


def test_first_exact_claude_uuid_lookup_decodes_each_split_block_once(source, monkeypatch):
    if source.session.provider != 'claude_code':
        pytest.skip('Claude split groups')
    prefix = source.add(1, 'prefix', group='group')
    terminal = source.add(2, 'terminal', group='group')
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = api().prepare_source_reads(source.session.id, [([source.ref('terminal')], 0, None)])
    assert lines(prepared.resolve([source.ref('terminal')])) == [[1, 2]]
    assert decoded.count(prefix.current_content_id) == 1
    assert decoded.count(terminal.current_content_id) == 1


@pytest.mark.parametrize('limit,value', [('_RECENT_ROW_LIMIT', 1), ('_RECENT_BYTE_LIMIT', 1)])
def test_scan_cache_discards_blocks_beyond_its_row_or_byte_budget(source, monkeypatch, limit, value):
    if source.session.provider != 'claude_code':
        pytest.skip('Claude split groups')
    prefix = source.add(1, 'prefix', group='group')
    terminal = source.add(2, 'terminal', group='group')
    module = api()
    monkeypatch.setattr(module, limit, value)
    decoded = []
    original = readers.decode_content

    def count(row):
        decoded.append(row.pk)
        return original(row)

    monkeypatch.setattr(readers, 'decode_content', count)
    prepared = module.prepare_source_reads(source.session.id, [([source.ref('terminal')], 0, None)])
    assert lines(prepared.resolve([source.ref('terminal')])) == [[1, 2]]
    assert decoded.count(prefix.current_content_id) == 2
    assert decoded.count(terminal.current_content_id) == 1
