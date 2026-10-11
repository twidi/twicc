"""Progressive compute extraction uses real commits and exact logical strings."""

from uuid import uuid4

import pytest
from django.db import transaction

from tests.content_storage_helpers import read_current
from twicc.core.models import Project, Session, SessionItem
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(**kwargs):
    project, _ = Project.objects.get_or_create(id='extraction-project')
    return Session.objects.create(id=kwargs.pop('id', 'extract'), project=project, provider='codex',
                                  storage_incarnation=str(uuid4()), file_path=str(uuid4()), last_line=10, **kwargs)


def message(session, **kwargs):
    return {'session_id': session.id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'session_fields': {'compute_version': 123}, **kwargs}


def test_small_result_extracts_unchanged_empty_and_malformed_strings():
    session = make_session()
    for line, text in enumerate(('', 'not json', '{"unicode":"é"}'), 1):
        SessionItem.objects.create(session=session, line_num=line, content=text)
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    for item, text in zip(SessionItem.objects.filter(session=session).order_by('line_num'),
                          ('', 'not json', '{"unicode":"é"}'), strict=True):
        assert item.current_content_id is not None
        assert item.original_content_id is None
        assert item.content == ''
        assert read_current(item) == text
        assert item.current_content.session_ids == [session.id]
    session.refresh_from_db()
    assert session.content_storage_version == 1


@pytest.mark.parametrize('change', ['epoch', 'incarnation'])
def test_equal_offsets_do_not_accept_stale_identity(change):
    session = make_session()
    old = message(session)
    if change == 'epoch':
        Session.objects.filter(pk=session.id).update(history_epoch=1)
    else:
        session.delete()
        session = make_session()
    assert BaseSessionCompute.apply_session_complete(old).outcome == 'superseded'
    session.refresh_from_db()
    assert session.compute_version != 123


def test_storage_slice_commits_and_resumes_without_duplicate_items():
    from twicc.core.content_storage.state import extract_storage_slice

    session = make_session()
    for line in range(1, 4):
        SessionItem.objects.create(session=session, line_num=line, content=f'legacy {line}')
    first = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, -1, 1, 1024)
    assert first.rows == 1
    assert not first.done
    assert not transaction.get_connection().in_atomic_block
    assert SessionItem.objects.get(session=session, line_num=1).current_content_id is not None
    cursor = first
    while not cursor.done:
        cursor = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, cursor.line_num, 1, 1024)
    assert SessionItem.objects.filter(session=session).count() == 3
    assert [read_current(item) for item in SessionItem.objects.filter(session=session).order_by('line_num')] == [
        'legacy 1', 'legacy 2', 'legacy 3',
    ]
    session.refresh_from_db()
    assert session.content_storage_version is None


def test_changed_override_checks_legacy_input_digest():
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='changed elsewhere')
    msg = message(session, content_overrides=[{'id': item.id, 'content': 'computed',
                                             'observed_current_id': None, 'observed_legacy_digest': '0' * 64}])
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'superseded'
    assert read_current(SessionItem.objects.get(pk=item.id)) == 'changed elsewhere'


def test_override_preserves_known_original_and_removes_displaced_membership():
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage.ingestion import insert_ingested_items
    from twicc.core.content_storage.references import intern_contents
    from twicc.core.models import SessionItemContent

    _public_dictionary('codex')
    session = make_session()
    item = SessionItem(session=session, line_num=1, content='source')
    with transaction.atomic():
        insert_ingested_items(session, [(item, 'source')])
        item = SessionItem.objects.get(session=session, line_num=1)
        old_current = intern_contents(['old enrichment'], 'codex')['old enrichment']
        SessionItem.objects.filter(pk=item.id).update(current_content_id=old_current)
        SessionItemContent.objects.filter(pk=old_current).update(session_ids=[session.id])
    original = item.original_content_id
    msg = message(session, content_overrides=[{'id': item.id, 'content': 'new enrichment',
                                             'observed_current_id': old_current}])
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    item.refresh_from_db()
    assert item.original_content_id == original
    assert read_current(item) == 'new enrichment'
    assert SessionItemContent.objects.get(pk=old_current).session_ids == []
    assert SessionItemContent.objects.get(pk=original).session_ids == [session.id]
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'


def test_real_partial_commit_failure_retries_with_fresh_reference_counts(monkeypatch):
    from django.db import DatabaseError, connection
    from twicc.core.content_storage import extraction, references
    from twicc.core.models import SessionItemContent

    session = make_session(last_offset=50)
    for line in range(1, 5):
        SessionItem.objects.create(session=session, line_num=line, content=f'old {line}')
    msg = message(session)
    monkeypatch.setattr(extraction, 'ROW_BUDGET', 1)
    monkeypatch.setattr(references, 'COUNT_SCAN_THRESHOLD', 1)
    original = connection.commit
    moved_before_failure = []

    def commit():
        moved = SessionItem.objects.filter(session=session, current_content_id__isnull=False).count()
        if moved == 2:
            moved_before_failure.append(moved)
            raise DatabaseError('injected second storage commit')
        return original()

    with monkeypatch.context() as patch:
        patch.setattr(connection, 'commit', commit)
        with pytest.raises(DatabaseError, match='second storage commit'):
            BaseSessionCompute.apply_session_complete(msg)
    assert moved_before_failure == [2]
    assert SessionItem.objects.filter(session=session, current_content_id__isnull=False).count() == 1
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.compute_version != 123
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    assert [read_current(item) for item in SessionItem.objects.filter(session=session).order_by('line_num')] == [
        f'old {line}' for line in range(1, 5)
    ]
    assert SessionItemContent.objects.count() == 4
    assert all(row.session_ids == [session.id] for row in SessionItemContent.objects.all())
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_temp_master WHERE name LIKE 'content_reference_counts_%'")
        assert cursor.fetchall() == []


def test_storage_slice_byte_budget_allows_one_oversized_row():
    from twicc.core.content_storage.state import extract_storage_slice

    session = make_session()
    SessionItem.objects.create(session=session, line_num=1, content='é' * 50)
    SessionItem.objects.create(session=session, line_num=2, content='small')
    first = extract_storage_slice(session.id, session.storage_incarnation, 0, 10, -1, 10, 10)
    assert (first.line_num, first.rows, first.bytes, first.done) == (1, 1, 100, False)
    assert SessionItem.objects.get(session=session, line_num=2).current_content_id is None


def test_initialize_legacy_identity_is_stable_before_dispatch():
    from twicc.core.content_storage.extraction import initialize_compute_session

    session = make_session()
    Session.objects.filter(pk=session.id).update(storage_incarnation=None)
    initialize_compute_session(session.id)
    session.refresh_from_db()
    first = session.storage_incarnation
    assert first
    initialize_compute_session(session.id)
    session.refresh_from_db()
    assert session.storage_incarnation == first
    assert session.content_storage_version is None


def test_worker_sends_only_changed_text_and_observed_input_identity():
    import queue

    import orjson

    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute

    session = make_session()
    text = orjson.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'unchanged'}}).decode()
    SessionItem.objects.create(session=session, line_num=1, content=text)
    results = queue.Queue()
    ClaudeCodeSessionCompute().compute_session_metadata(session.id, results, 1)
    result = next(orjson.loads(raw) for raw in list(results.queue) if orjson.loads(raw)['type'] == 'session_complete')
    assert result['observed_incarnation'] == session.storage_incarnation
    assert result['observed_epoch'] == session.history_epoch
    assert result['observed_upper_line'] == session.last_line
    assert result['content_overrides'] == []
    assert 'unchanged_contents' not in result
    assert text not in orjson.dumps(result).decode()


def test_final_apply_failure_preserves_committed_extraction_and_retries(monkeypatch):
    from twicc.providers import compute_base

    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='exact')
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('injected final facts')
        patch.setattr(compute_base, 'replace_history_facts', fail)
        with pytest.raises(RuntimeError, match='final facts'):
            BaseSessionCompute.apply_session_complete(message(session))
    item.refresh_from_db()
    assert read_current(item) == 'exact'
    assert item.content == ''
    session.refresh_from_db()
    assert session.content_storage_version is None
    assert session.compute_version != 123
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
