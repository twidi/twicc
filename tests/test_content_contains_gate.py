"""Public substring search requires one complete, verified hot-root snapshot."""

from datetime import timedelta

import orjson
import pytest
import typer
from django.db import transaction
from django.utils import timezone

from tests.content_storage_helpers import prepare_plaintext_search
from twicc.cli import session as cli_session
from twicc.core.content_storage import state
from twicc.core.content_storage.lifecycle import active_session_ids
from twicc.core.enums import ItemKind
from twicc.core.models import Project, Session, SessionItem
from twicc.rpc.invoker import invoke

pytestmark = pytest.mark.django_db


def make_session(name='root', **fields):
    project, _ = Project.objects.get_or_create(id='gate-project', defaults={'directory': '/gate'})
    return Session.objects.create(id=name, project=project, provider='claude_code', file_path=name,
                                  created_at=timezone.now(), user_message_count=1, **fields)


def add_message(session, line, text, *, row=None):
    payload = {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}
    return SessionItem.objects.create(session=session, line_num=line, kind=ItemKind.USER_MESSAGE,
                                      content=orjson.dumps(payload).decode() if row is None else '',
                                      current_content=row)


@pytest.mark.parametrize('reason', ['cold', 'warming', 'unresolved_ancestry'])
@pytest.mark.parametrize('command', [cli_session.content, cli_session.messages])
def test_public_contains_rejects_root_state(reason, command, capsysbinary):
    session = make_session(type='subagent' if reason == 'unresolved_ancestry' else 'session')
    add_message(session, 1, 'needle')
    if reason == 'cold':
        Session.objects.filter(pk=session.pk).update(archived=True, created_at=timezone.now() - timedelta(days=31))
    with transaction.atomic(), pytest.raises(state.PlaintextSearchUnavailable) as error:
        state.require_plaintext_search(session.id)
    assert error.value.reason == reason
    with pytest.raises(typer.Exit):
        command(session.id, contains=['needle'], range_str='1')
    assert reason in capsysbinary.readouterr().err.decode()


def test_deep_native_root_cold_inheritance_and_no_active_override():
    root = make_session()
    prepare_plaintext_search(root)
    Session.objects.filter(pk=root.pk).update(hidden=True, created_at=timezone.now() - timedelta(days=31))
    child = make_session('child', type='subagent', parent_session=root)
    leaf = make_session('leaf', type='subagent', parent_session=child)
    token = active_session_ids.set(frozenset({leaf.id}))
    try:
        with transaction.atomic(), pytest.raises(state.PlaintextSearchUnavailable) as error:
            state.require_plaintext_search(leaf.id)
        assert error.value.reason == 'cold'
    finally:
        active_session_ids.reset(token)


def test_unknown_identity_is_warming_but_plain_root_uses_metadata_fallback():
    session = make_session(content_storage_version=1, content_state_epoch=2, plaintext_verified_epoch=2)
    with transaction.atomic(), pytest.raises(state.PlaintextSearchUnavailable) as error:
        state.require_plaintext_search(session.id)
    assert error.value.reason == 'warming'
    prepare_plaintext_search(session)
    Session.objects.filter(pk=session.pk).update(content_state_epoch=3)
    with transaction.atomic():
        assert state.require_plaintext_search(session.id).epoch == 3
    session.refresh_from_db()
    assert session.plaintext_verified_epoch != session.content_state_epoch


@pytest.mark.parametrize('terms', [['NEEDLE', '100%_'], ['é'], ['É'], ['\\'], ['needle\x00'], ['missing']])
def test_ready_content_preserves_legacy_sql_matching(terms, capsysbinary):
    session = make_session()
    for line, text in enumerate(['needle 100%_ é \\', 'NEEDLE É', 'nothing'], 1):
        add_message(session, line, text)
    legacy = SessionItem.objects.filter(session=session).order_by('line_num')
    for term in terms:
        legacy = legacy.filter(content__icontains=term)
    expected = list(legacy.values_list('line_num', flat=True))
    prepare_plaintext_search(session)
    cli_session.content(session.id, contains=terms, limit=1, offset=1)
    result = orjson.loads(capsysbinary.readouterr().out)
    assert result['pagination']['total'] == len(expected)
    assert [item['line_num'] for item in result['items']] == expected[1:2]


def test_ready_messages_match_extracted_text_with_unicode_lower(capsysbinary):
    session = make_session()
    add_message(session, 1, 'É needle\nnext')
    add_message(session, 2, 'needle')
    prepare_plaintext_search(session)
    cli_session.messages(session.id, contains=['é', 'needle\nnext'], tail=1)
    result = orjson.loads(capsysbinary.readouterr().out)
    assert result['pagination']['total'] == 1
    assert [item['line_num'] for item in result['items']] == [1]
    cli_session.messages(session.id, contains=['"type"'])
    assert orjson.loads(capsysbinary.readouterr().out)['items'] == []


@pytest.mark.parametrize('reason', ['cold', 'warming'])
@pytest.mark.parametrize('command', [cli_session.content, cli_session.messages])
def test_cold_range_decodes_only_selected_rows(reason, command, content_row_factory, monkeypatch, capsysbinary):
    session = make_session(archived=reason == 'cold')
    Session.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=31))
    rows = []
    for line in range(1, 4):
        payload = {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': str(line)}]}}
        row = content_row_factory(orjson.dumps(payload).decode(), True, provider='claude_code')
        rows.append(row)
        add_message(session, line, '', row=row)
    from twicc.core.content_storage import readers
    decoded = []
    original = readers.decode_content
    def record(row):
        decoded.append(row.pk)
        return original(row)
    monkeypatch.setattr(readers, 'decode_content', record)
    command(session.id, range_str='2')
    result = orjson.loads(capsysbinary.readouterr().out)
    assert [item['line_num'] for item in result['items']] == [2]
    assert decoded == [rows[1].pk]
    assert all(row.codec == 'zstd' for row in type(rows[0]).objects.all())


def test_rpc_gate_uses_existing_error_envelope():
    session = make_session()
    result = invoke(['session', session.id, 'content', '--contains', 'needle'])
    assert result.exit_code == 1 and result.result is None
    assert 'warming' in result.error


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('command', [cli_session.content, cli_session.messages])
def test_contains_snapshot_cannot_mix_ready_epoch_and_new_hot_reference(command, tmp_path, monkeypatch, capsysbinary):
    """A WAL writer commits between the gate and count; the reader retains its epoch."""
    import sqlite3
    from copy import deepcopy

    from django.core.management import call_command
    from django.db import connections
    from django.db.backends.sqlite3.base import DatabaseWrapper

    # Build only a new synthetic test database. No instance database is copied.
    original_connection = connections['default']
    config = deepcopy(original_connection.settings_dict)
    database = tmp_path / 'snapshot.sqlite'
    config['NAME'] = str(database)
    isolated = DatabaseWrapper(config, alias='default')
    connections._connections.default = isolated
    try:
        call_command('migrate', verbosity=0, interactive=False)
        with isolated.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL')
        session = make_session()
        add_message(session, 1, 'needle')
        prepare_plaintext_search(session)
        with transaction.atomic():
            verified = state.require_plaintext_search(session.id)
        observed = []
        original_guard = cli_session._require_plaintext_search

        def concurrent_commit(session_id):
            original_guard(session_id)
            observed.append(state.require_plaintext_search(session_id))
            with sqlite3.connect(database, timeout=5) as writer:
                columns = [row[1] for row in writer.execute('PRAGMA table_info(core_sessionitem)')]
                selected = ['NULL' if column in ('id', 'current_content_id')
                            else '2' if column == 'line_num' else f'"{column}"'
                            for column in columns]
                names = ', '.join(f'"{column}"' for column in columns)
                writer.execute(f'INSERT INTO core_sessionitem ({names}) SELECT {", ".join(selected)} '
                               'FROM core_sessionitem WHERE session_id=? AND line_num=1', [session_id])
                writer.execute('UPDATE core_session SET content_state_epoch=content_state_epoch+1 WHERE id=?',
                               [session_id])
        monkeypatch.setattr(cli_session, '_require_plaintext_search', concurrent_commit)
        command(session.id, contains=['needle'])
        result = orjson.loads(capsysbinary.readouterr().out)
        assert observed == [verified]
        assert result['pagination']['total'] == 1
        assert [item['line_num'] for item in result['items']] == [1]
        assert SessionItem.objects.filter(session=session).count() == 2
        with transaction.atomic(), pytest.raises(state.PlaintextSearchUnavailable) as error:
            state.require_plaintext_search(session.id)
        assert error.value.reason == 'warming'
    finally:
        isolated.close()
        connections._connections.default = original_connection


def test_tantivy_cold_hits_remain_unchanged_and_hit_read_decodes_one_row(
    tmp_path, monkeypatch, content_row_factory, capsysbinary,
):
    from twicc import paths, search
    from twicc.core.content_storage import readers

    monkeypatch.setattr(paths, 'get_search_dir', lambda: tmp_path / 'search-index')
    search.init_search_index()
    try:
        session = make_session(archived=True)
        Session.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=31))
        items = [add_message(session, line, text) for line, text in enumerate(['needle', 'other', 'noise'], 1)]
        search.index_document(session_id=session.id, project_id=session.project_id, line_num=1,
                              body='needle', from_role='user', timestamp=timezone.now(), archived=True)
        search.commit()
        before = search.raw_search('needle', to_json=False)
        for item in items:
            row = content_row_factory(item.content, True, provider=session.provider, session_ids=[session.id])
            SessionItem.objects.filter(pk=item.pk).update(content='', current_content=row)
        after = search.raw_search('needle', to_json=False)
        assert after == before
        assert after['total_hits'] == 1
        decoded = []
        original_decode = readers.decode_content

        def record(row):
            decoded.append(row.pk)
            return original_decode(row)
        monkeypatch.setattr(readers, 'decode_content', record)
        cli_session.content(session.id, range_str='1')
        assert [item['line_num'] for item in orjson.loads(capsysbinary.readouterr().out)['items']] == [1]
        assert len(decoded) == 1
    finally:
        search.shutdown_search_index()


@pytest.mark.django_db(transaction=True)
def test_gate_requires_read_transaction():
    # A standalone guard would allow its caller to accidentally mix snapshots.
    with pytest.raises(transaction.TransactionManagementError):
        state.require_plaintext_search('unknown')



def test_ready_deep_native_descendant_uses_root_snapshot():
    root = make_session(content_storage_version=1)
    child = make_session('child', type='subagent', parent_session=root, content_storage_version=1)
    leaf = make_session('leaf', type='subagent', parent_session=child, content_storage_version=1)
    with transaction.atomic():
        for owner in (root, child, leaf):
            state.ensure_storage_incarnation(owner.id)
        snapshot = state.snapshot_root_storage(root.id)
        assert state.verify_plaintext_root(snapshot)
        assert state.require_plaintext_search(leaf.id) == snapshot


def test_ready_content_range_and_and_terms_precede_tail(capsysbinary):
    session = make_session()
    for line, text in enumerate(['needle match', 'needle only', 'needle MATCH', 'needle match', 'needle match'], 1):
        add_message(session, line, text)
    prepare_plaintext_search(session)
    cli_session.content(session.id, range_str='1-4', contains=['NEEDLE', 'match'], tail=2)
    result = orjson.loads(capsysbinary.readouterr().out)
    assert [item['line_num'] for item in result['items']] == [3, 4]
    assert result['pagination'] == {'limit': 2, 'offset': 1, 'total': 3, 'has_more': True}
