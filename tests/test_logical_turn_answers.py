"""Exact native answers belong to durable logical intervals, never adjacent turns."""

from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from tests import content_storage_helpers
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, LogicalTurnInput, Project, Session, SessionItem

content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db


@pytest.fixture(params=['claude_code', 'codex'])
def answer_fixture(request, content_row_factory):
    provider = request.param
    project = Project.objects.create(id='answers', directory='/tmp/answers')
    session = Session.objects.create(id='root', project=project, provider=provider,
                                     storage_incarnation=str(uuid4()), history_epoch=2)

    def ref(identity, *, turn='native-A', kind=None):
        return {'kind': kind or ('claude_uuid' if provider == 'claude_code' else 'codex_item'),
                    'native_id': identity, 'turn_id': turn if provider == 'codex' else None}

    def index(identity, line, text, *, child=False, delivery=None, message_id=None, sidechain=False, final=True):
        if provider == 'claude_code':
            parsed = {'type': 'assistant', 'uuid': identity, 'isSidechain': sidechain, 'parent_tool_use_id': 'child' if child else None,
                          'message': {'id': message_id, 'role': 'assistant', 'stop_reason': 'end_turn' if final else 'tool_use',
                                       'content': [{'type': 'text', 'text': text}]}}
        else:
            parsed = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': 'child' if child else session.id,
                          'turn_id': 'native-A', 'item': {'type': 'AgentMessage', 'id': identity, 'phase': 'final_answer' if final else 'commentary',
                          'delivery': delivery, 'content': [{'type': 'Text', 'text': text}]}}}
        if final is None:
            if provider == 'claude_code':
                parsed['message'].pop('stop_reason', None)
            else:
                parsed['payload']['item'].pop('phase', None)
        content = content_row_factory(orjson.dumps(parsed).decode(), True, provider)
        return SessionItem.objects.create(session=session, line_num=line, kind=ItemKind.ASSISTANT_MESSAGE,
                                          content='', current_content=content)

    def turn(*, refs=('A1', 'A2'), outcome='normal', position=1, floor=0, first='A1'):
        return LogicalTurn.objects.create(id=uuid4(), session_id=session.id, provider=provider, run_id=uuid4(),
            ordinal=1, position=position, source_floor=floor, storage_incarnation=session.storage_incarnation,
            history_epoch=session.history_epoch, opened_at=timezone.now(),
            closed_at=timezone.now() if outcome else None, outcome=outcome,
            first_ref=ref(first) if first else None, final_refs=[ref(i) for i in refs])
    return SimpleNamespace(session=session, ref=ref, index=index, turn=turn, provider=provider)


def test_last_native_answer_survives_later_interval(answer_fixture):
    from twicc.core.services.logical_turn_answers import NativeAnswerCache, resolve_logical_answer
    f = answer_fixture
    turn = f.turn()
    cache = NativeAnswerCache(f.session.id)
    f.index('A1', 2, 'intermediate')
    f.index('B1', 8, 'later')
    assert resolve_logical_answer(turn, cursor=0, cache=cache).status == 'pending'
    f.index('A2', 5, 'A2')
    assert resolve_logical_answer(turn, cursor=0, cache=cache).message.text == 'A2'
    assert resolve_logical_answer(turn, cursor=5, cache=cache).status == 'ended'


def test_open_interval_never_returns_intermediate(answer_fixture):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=('A1',), outcome=None)
    f.index('A1', 2, 'intermediate')
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


@pytest.mark.parametrize('outcome', ['ended', 'provider_error'])
def test_terminal_outcome_overrides_answer(answer_fixture, outcome):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=('A1',), outcome=outcome)
    f.index('A1', 2, 'intermediate')
    assert resolve_logical_answer(turn, cursor=0).status == outcome


def test_empty_and_missing_identity_are_distinct(answer_fixture):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=())
    assert resolve_logical_answer(turn, cursor=0).status == 'ended'
    turn.final_refs = [{'kind': 'unresolved', 'native_id': '', 'turn_id': None}]
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


def test_child_and_repeated_text_do_not_match_identity(answer_fixture):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=('A2',))
    f.index('A2', 2, 'same', child=True)
    f.index('different', 3, 'same')
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


def test_source_scope_change_invalidates_cache(answer_fixture):
    from twicc.core.services.logical_turn_answers import NativeAnswerCache, resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=('A1',))
    f.index('A1', 2, 'answer')
    cache = NativeAnswerCache(f.session.id)
    assert resolve_logical_answer(turn, cursor=0, cache=cache).status == 'replied'
    Session.objects.filter(pk=f.session.id).update(history_epoch=3)
    assert resolve_logical_answer(turn, cursor=0, cache=cache).status == 'invalidated'


def test_token_selects_accepted_interval_and_validates_session(answer_fixture):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    older = f.turn(refs=(), first=None)
    newer = f.turn(refs=('B1',), position=2)
    token = LogicalTurnInput.objects.create(id=uuid4(), turn=newer, admission_cursor=0, status='accepted')
    assert select_logical_turn(session_id=f.session.id, cursor=0, wait_token=None).pk == older.pk
    assert select_logical_turn(session_id=f.session.id, cursor=0, wait_token=str(token.pk)).pk == newer.pk
    with pytest.raises(ValueError):
        select_logical_turn(session_id='other', cursor=0, wait_token=str(token.pk))
    assert select_logical_turn(session_id=f.session.id, cursor=0, wait_token=None,
                               after_turn_id=str(older.pk)).pk == newer.pk


def test_claude_split_blocks_join_by_message_identity(answer_fixture):
    from twicc.core.services.logical_turn_answers import NativeAnswerCache, resolve_logical_answer, select_logical_turn
    f = answer_fixture
    if f.provider != 'claude_code':
        pytest.skip('Claude split records')
    turn = f.turn(refs=())
    turn.final_refs = [f.ref('api-id', kind='claude_message')]
    turn.save(update_fields=['final_refs'])
    cache = NativeAnswerCache(f.session.id)
    f.index('block-1', 2, 'first', message_id='api-id', final=None)
    assert resolve_logical_answer(turn, cursor=0, cache=cache).status == 'pending'
    assert resolve_logical_answer(turn, cursor=2, cache=cache).status == 'pending'
    assert select_logical_turn(session_id=f.session.id, cursor=2, wait_token=None, cache=cache).pk == turn.pk
    f.index('block-2', 3, 'second', message_id='api-id')
    answer = resolve_logical_answer(turn, cursor=0, cache=cache)
    assert answer.message.text == 'first\nsecond'
    assert answer.message.line_num == 3
    assert answer.message.is_final is True
    assert resolve_logical_answer(turn, cursor=3, cache=cache).status == 'ended'


def test_codex_async_delivery_cannot_resolve_root_answer(answer_fixture):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    if f.provider != 'codex':
        pytest.skip('Codex asynchronous delivery')
    turn = f.turn(refs=('A2',))
    f.index('A2', 2, 'notification', delivery='async')
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


@pytest.mark.parametrize('source_first', [False, True])
def test_unbound_source_binds_under_writer_admission_in_both_arrival_orders(answer_fixture, source_first):
    from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
    from twicc.core.services.logical_turn_answers import bind_indexed_turn_sources
    from twicc.core.services.logical_turns import store_turn_snapshot
    f = answer_fixture
    turn = f.turn(refs=(), first=None, outcome=None)
    turn.storage_incarnation = None
    turn.history_epoch = None
    turn.save()
    ref = NativeMessageRef(**f.ref('A2'))
    snapshot = CompletionSnapshot(str(turn.pk), str(turn.run_id), turn.ordinal, 1, ref, (ref,), 'normal')
    if source_first:
        f.index('A2', 5, 'answer')
        store_turn_snapshot(snapshot, closed_at=timezone.now())
    else:
        store_turn_snapshot(snapshot, closed_at=timezone.now())
        f.index('A2', 5, 'answer')
        bind_indexed_turn_sources(f.session.id)
    turn.refresh_from_db()
    assert turn.storage_incarnation == f.session.storage_incarnation
    assert turn.history_epoch == f.session.history_epoch


def test_unbound_source_requires_verified_native_proof(answer_fixture):
    from twicc.core.services.logical_turn_answers import bind_indexed_turn_sources, resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=('A2',))
    turn.storage_incarnation = None
    turn.history_epoch = None
    turn.save()
    f.index('unrelated', 5, 'same answer')
    bind_indexed_turn_sources(f.session.id)
    turn.refresh_from_db()
    assert turn.storage_incarnation is None
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


def test_indexed_earlier_final_cannot_hide_unindexed_last_final_after_cursor(answer_fixture):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    turn = f.turn()
    f.index('A1', 2, 'intermediate')
    assert select_logical_turn(session_id=f.session.id, cursor=2, wait_token=None).pk == turn.pk


def test_binding_cache_does_not_trust_rolled_back_native_proof(answer_fixture):
    from django.db import transaction
    from twicc.core.services.logical_turn_answers import bind_indexed_turn_sources
    f = answer_fixture
    turn = f.turn(refs=('A2',))
    turn.storage_incarnation = None
    turn.history_epoch = None
    turn.save()
    with transaction.atomic():
        f.index('A2', 5, 'rolled back answer')
        # Keep another unbound interval so the incremental cache remains live.
        other = f.turn(position=2, refs=('missing',), first='missing')
        other.storage_incarnation = None
        other.history_epoch = None
        other.save()
        bind_indexed_turn_sources(f.session.id)
        transaction.set_rollback(True)
    bind_indexed_turn_sources(f.session.id)
    turn.refresh_from_db()
    assert turn.storage_incarnation is None


@pytest.mark.parametrize('outcome', ['ended', 'provider_error', 'normal'])
def test_known_terminal_without_indexed_session_retains_durable_outcome(answer_fixture, outcome):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    turn = f.turn(refs=(), first=None, outcome=outcome)
    f.session.delete()
    assert resolve_logical_answer(turn, cursor=0).status == ('ended' if outcome == 'normal' else outcome)


def test_claude_sidechain_record_cannot_resolve_root_answer(answer_fixture):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    if f.provider != 'claude_code':
        pytest.skip('Claude sidechain source scope')
    turn = f.turn(refs=('A2',))
    f.index('A2', 2, 'child answer', sidechain=True)
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'


@pytest.mark.parametrize('outcome', ['normal', 'ended'])
@pytest.mark.parametrize('boundary', ['indexed', 'unindexed', 'wrong_scope'])
def test_later_native_opening_bounds_closed_empty_interval(answer_fixture, outcome, boundary):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    older = f.turn(refs=(), first='A0', outcome=outcome)
    f.index('A0', 2, 'earlier work', final=False)
    newer = f.turn(refs=('B1',), first='B0', position=2, floor=8)
    if boundary != 'unindexed':
        f.index('B0', 8, 'new work', final=False)
    if boundary == 'wrong_scope':
        newer.history_epoch += 1
        newer.save()
    f.index('B1', 9, 'new answer')
    selected = select_logical_turn(session_id=f.session.id, cursor=8, wait_token=None)
    assert selected.pk == (newer.pk if boundary == 'indexed' else older.pk)
    # A boundary after the cursor cannot establish that the old interval is past.
    assert select_logical_turn(session_id=f.session.id, cursor=7, wait_token=None).pk == older.pk
    token = LogicalTurnInput.objects.create(id=uuid4(), turn=older, admission_cursor=0, status='accepted')
    assert select_logical_turn(session_id=f.session.id, cursor=8, wait_token=str(token.pk)).pk == older.pk


def test_later_opening_does_not_replace_unindexed_final(answer_fixture):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    older = f.turn(refs=('missing',), first='A0')
    f.index('A0', 2, 'earlier work', final=False)
    f.turn(refs=('B1',), first='B0', position=2)
    f.index('B0', 8, 'new work', final=False)
    assert select_logical_turn(session_id=f.session.id, cursor=8, wait_token=None).pk == older.pk


@pytest.mark.parametrize('earlier_answer', [False, True])
def test_indexed_empty_codex_final_is_known_without_replacing_previous_answer(answer_fixture, earlier_answer):
    from twicc.core.services.logical_turn_answers import resolve_logical_answer
    f = answer_fixture
    if f.provider != 'codex':
        pytest.skip('Codex empty native final')
    turn = f.turn(refs=('A1', 'empty') if earlier_answer else ('empty',))
    if earlier_answer:
        f.index('A1', 2, 'earlier answer')
    # The final identity is authoritative until its source record is indexed.
    assert resolve_logical_answer(turn, cursor=0).status == 'pending'
    f.index('empty', 5, '')
    result = resolve_logical_answer(turn, cursor=0)
    assert result.status == ('replied' if earlier_answer else 'ended')
    if earlier_answer:
        assert result.message.text == 'earlier answer'
        assert resolve_logical_answer(turn, cursor=2).status == 'ended'


@pytest.mark.parametrize('outcome', ['normal', 'ended'])
@pytest.mark.parametrize('opening', ['user', 'assistant'])
def test_latest_user_default_uses_verified_native_user_opening(answer_fixture, content_row_factory, monkeypatch, outcome, opening):
    import time
    from twicc.cli import _wait_reply
    from twicc.providers.helpers import get_provider_helpers
    f = answer_fixture
    f.turn(refs=(), first='A0', outcome=outcome)
    f.index('A0', 2, 'earlier work', final=False)
    newer = f.turn(refs=('B1',), first='B-user' if opening == 'user' else 'B1', position=2)
    if f.provider == 'claude_code':
        parsed = {'type': 'user', 'uuid': 'B-user', 'message': {'role': 'user', 'content': 'next'}}
    else:
        parsed = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': f.session.id,
                  'turn_id': 'native-A', 'item': {'type': 'UserMessage', 'id': 'B-user',
                  'content': [{'type': 'Text', 'text': 'next'}]}}}
    content = content_row_factory(orjson.dumps(parsed).decode(), True, f.provider)
    SessionItem.objects.create(session=f.session, line_num=8, kind=ItemKind.USER_MESSAGE,
                               content='', current_content=content)
    f.index('B1', 9, 'new answer')
    f.session.compute_version = get_provider_helpers(f.provider).current_compute_version
    f.session.last_line = 9
    f.session.save(update_fields=['compute_version', 'last_line'])
    cursor = _wait_reply.default_wait_cursors([f.session])[f.session.id]
    assert cursor == 8
    monkeypatch.setattr(_wait_reply, '_agent_activity', lambda *_: _wait_reply._Activity(False, False, None, True))
    waiter = _wait_reply._SessionWait(f.session.id, cursor, started=time.monotonic(), twicc_pid=None, want_text=True)
    result = waiter.step()
    if opening == 'user':
        assert result['outcome'] == 'replied'
        assert result['text'] == 'new answer'
    else:
        # Proximity cannot associate the user line with the later assistant.
        assert result['outcome'] == 'ended'
    token = LogicalTurnInput.objects.create(id=uuid4(), turn=newer, admission_cursor=8, status='accepted')
    exact = _wait_reply._SessionWait(f.session.id, cursor, started=time.monotonic(), twicc_pid=None,
                                    want_text=True, wait_token=str(token.pk))
    assert exact.step()['text'] == 'new answer'


@pytest.mark.parametrize('outcome', ['ended', 'provider_error'])
def test_later_native_opening_bounds_interrupted_unindexed_final(answer_fixture, outcome):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    older = f.turn(refs=('unindexed-final',), first='A0', outcome=outcome)
    f.index('A0', 2, 'earlier work', final=False)
    newer = f.turn(refs=('B1',), first='B0', position=2)
    f.index('B0', 8, 'later work', final=False)
    assert select_logical_turn(session_id=f.session.id, cursor=8, wait_token=None).pk == newer.pk
    token = LogicalTurnInput.objects.create(id=uuid4(), turn=older, admission_cursor=0, status='accepted')
    assert select_logical_turn(session_id=f.session.id, cursor=8, wait_token=str(token.pk)).pk == older.pk


def test_unbound_source_cannot_prove_later_opening(answer_fixture):
    from twicc.core.services.logical_turn_answers import select_logical_turn
    f = answer_fixture
    older = f.turn(refs=(), first='A0')
    newer = f.turn(refs=(), first='B0', position=2)
    f.index('B0', 8, 'later work', final=False)
    Session.objects.filter(pk=f.session.id).update(storage_incarnation=None)
    LogicalTurn.objects.filter(pk=newer.pk).update(storage_incarnation=None)
    assert select_logical_turn(session_id=f.session.id, cursor=8, wait_token=None).pk == older.pk
