from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from twicc.core.content_storage import references
from twicc.core.content_storage.codec import ContentIntegrityError, decode_content, encode_cold
from twicc.core.content_storage.types import ContentBinding
from twicc.core.models import ContentDictionary, Project, Session, SessionItem, SessionItemContent

pytestmark = pytest.mark.django_db


def session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='references', defaults={'directory': '/references'})
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name, **kwargs)


def item(owner, current, original=None, line=1):
    return SessionItem.objects.create(session=owner, line_num=line, content='', current_content_id=current,
                                      original_content_id=original)


def bind(row):
    references.bind_persisted_items([ContentBinding(row.id, row.session_id, row.current_content_id,
                                                  row.original_content_id)])


def owners(content_id):
    return SessionItemContent.objects.get(pk=content_id).session_ids


def test_reuse_keeps_compressed_representation_and_provider(content_row_factory):
    text = 'exact source é\n' * 100
    stored = content_row_factory(text, True, session_ids=['owner'])
    before = bytes(stored.payload)
    with transaction.atomic():
        ids = references.intern_contents([text, text], 'claude')
    stored.refresh_from_db()
    assert ids == {text: stored.id}
    assert stored.codec == 'zstd' and bytes(stored.payload) == before
    assert stored.dictionary_provider == 'codex'
    assert stored.session_ids == ['owner']
    assert decode_content(stored) == text


def test_collision_is_not_silent(monkeypatch):
    with transaction.atomic():
        first = references.intern_contents(['first'], 'codex')
    real = references.encode_plain
    digest = bytes(SessionItemContent.objects.get(pk=first['first']).sha256)
    monkeypatch.setattr(references, 'encode_plain', lambda text: real(text)._replace(sha256=digest))
    with transaction.atomic(), pytest.raises(ContentIntegrityError):
        references.intern_contents(['second'], 'codex')


def test_shared_large_original_and_small_result_keep_both_owners():
    root, fork = session('root'), session('fork')
    large, small = 'source' * 1000, '{}'
    with transaction.atomic():
        ids = references.intern_contents([large, small], 'codex')
        bind(item(root, ids[small], ids[large]))
        bind(item(root, ids[large], ids[large], 2))
        bind(item(fork, ids[large]))
        references.reconcile_session_memberships(root.id, [ids[large]])
    assert owners(ids[large]) == ['fork', 'root']
    assert owners(ids[small]) == ['root']
    assert decode_content(SessionItemContent.objects.get(pk=ids[large])) == large
    assert decode_content(SessionItemContent.objects.get(pk=ids[small])) == small


def test_ignored_conflict_never_adds_phantom_owner():
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['kept', 'ignored'], 'codex')
        actual = item(root, ids['kept'])
        bind(actual)
        SessionItem.objects.bulk_create([SessionItem(session=root, line_num=1, content='',
                                                     current_content_id=ids['ignored'])], ignore_conflicts=True)
        with pytest.raises(ContentIntegrityError):
            references.bind_persisted_items([ContentBinding(actual.id, root.id, ids['ignored'], None)])
    assert owners(ids['ignored']) == []
    assert owners(ids['kept']) == ['root']


def test_hot_compressed_bind_invalidates_and_rollback_restores(content_row_factory):
    root = session('root', content_state_epoch=4, plaintext_verified_epoch=4)
    stored = content_row_factory('large' * 1000, True)
    row = SessionItem.objects.create(session=root, line_num=1, content='legacy')
    with pytest.raises(RuntimeError), transaction.atomic():
        row.current_content_id = stored.id
        row.content = ''
        row.save(update_fields=['current_content', 'content'])
        bind(row)
        root.refresh_from_db()
        assert root.content_state_epoch != root.plaintext_verified_epoch
        assert owners(stored.id) == ['root']
        raise RuntimeError('rollback')
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.current_content_id is None and row.content == 'legacy'
    assert owners(stored.id) == []
    assert root.content_state_epoch == root.plaintext_verified_epoch == 4


def test_prepared_requires_guard_and_exact_bytes():
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    text = 'compress me ' * 1000
    encoded, _ = encode_cold(text, dictionary.id, 'v1')
    with transaction.atomic(), pytest.raises(ContentIntegrityError):
        references.intern_contents([text], 'codex', {encoded.sha256: encoded})
    with transaction.atomic(), references.prepared_content_guard(lambda: True):
        ids = references.intern_contents([text], 'codex', {encoded.sha256: encoded})
    assert SessionItemContent.objects.get(pk=ids[text]).codec == 'zstd'
    bad = encoded._replace(raw_size=1)
    with transaction.atomic(), references.prepared_content_guard(lambda: True), pytest.raises(ContentIntegrityError):
        references.intern_contents([text], 'codex', {encoded.sha256: bad})


def test_count_operation_both_slots_savepoint_rollback_and_retry():
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['source', 'result'], 'codex')
        rows = [item(root, ids['source'], ids['source'], n) for n in (1, 2)]
        references.bind_persisted_items([ContentBinding(r.id, root.id, ids['source'], ids['source']) for r in rows])
    for attempt in range(2):
        with references.session_reference_operation(root.id, item_count=500000, expected_batches=2) as operation:
            assert operation.uses_counts
            with transaction.atomic():
                with operation.capture_mutation([rows[0].id]):
                    SessionItem.objects.filter(pk=rows[0].id).update(current_content_id=ids['result'])
                rows[0].refresh_from_db()
                bind(rows[0])
                references.reconcile_session_memberships(root.id, [ids['source']])
                assert owners(ids['source']) == ['root']
            if attempt == 0:
                with pytest.raises(RuntimeError), transaction.atomic():
                    with operation.capture_mutation([r.id for r in rows]):
                        SessionItem.objects.filter(session=root).update(current_content_id=ids['result'],
                                                                       original_content_id=None)
                    references.reconcile_session_memberships(root.id, [ids['source']])
                    assert owners(ids['source']) == []
                    raise RuntimeError('chunk rollback')
                with transaction.atomic():
                    references.reconcile_session_memberships(root.id, [ids['source']])
                    assert owners(ids['source']) == ['root']
            else:
                with transaction.atomic():
                    with operation.capture_mutation([r.id for r in rows]):
                        SessionItem.objects.filter(session=root).update(current_content_id=ids['result'],
                                                                       original_content_id=None)
                    references.reconcile_session_memberships(root.id, [ids['source']])
                assert owners(ids['source']) == []


def test_remove_is_exact_and_small_operation_uses_grouped_reconciliation():
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['source'], 'codex')
        row = item(root, ids['source'])
        bind(row)
        references.remove_session_memberships(root.id, iter([ids['source']]))
        assert owners(ids['source']) == ['root']
    with references.session_reference_operation(root.id, item_count=1, expected_batches=2) as operation:
        assert not operation.uses_counts
        with transaction.atomic(), operation.capture_mutation([row.id]):
            row.delete()
        with transaction.atomic():
            references.remove_session_memberships(root.id, [ids['source']])
    assert owners(ids['source']) == []


def test_count_capture_ignored_insert_and_superseded_guard():
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['actual', 'ignored'], 'codex')
        row = item(root, ids['actual'], ids['actual'])
        bind(row)
    with references.session_reference_operation(
        root.id, item_count=500000, expected_batches=2,
    ) as operation, transaction.atomic():
        with operation.capture_mutation(line_nums=[1, 2]):
            SessionItem.objects.bulk_create([
                SessionItem(session=root, line_num=1, content='', current_content_id=ids['ignored']),
                SessionItem(session=root, line_num=2, content='', current_content_id=ids['actual'],
                            original_content_id=ids['actual']),
            ], ignore_conflicts=True)
        with operation.capture_mutation([row.id]):
            assert SessionItem.objects.filter(pk=row.id, current_content_id=ids['ignored']).update(
                current_content_id=ids['ignored']) == 0
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT content_id, count FROM {operation.table} ORDER BY content_id')
            assert cursor.fetchall() == [(ids['actual'], 4)]
        references.reconcile_session_memberships(root.id, [ids['actual'], ids['ignored']])
        assert owners(ids['actual']) == ['root']
        assert owners(ids['ignored']) == []
    with connection.cursor() as cursor:
        cursor.execute('SELECT name FROM sqlite_temp_master WHERE name LIKE %s', ['content_reference_counts_%'])
        assert cursor.fetchall() == []


@pytest.mark.django_db(transaction=True)
def test_reference_writes_require_caller_transaction():
    with pytest.raises(transaction.TransactionManagementError):
        references.intern_contents(['text'], 'codex')
    with pytest.raises(transaction.TransactionManagementError):
        references.bind_persisted_items([])
    with pytest.raises(transaction.TransactionManagementError):
        references.reconcile_session_memberships('session', [])
    with pytest.raises(transaction.TransactionManagementError):
        references.remove_session_memberships('session', [])


def test_prepared_guard_rechecks_and_reuse_does_not_overwrite(content_row_factory):
    root = session('cold', archived=True, created_at=timezone.now() - timedelta(days=31))
    text = 'eligible source' * 1000
    dictionary = ContentDictionary.objects.get(provider='codex', version=1)
    encoded, _ = encode_cold(text, dictionary.id, 'v1')
    proof = lambda: Session.objects.filter(pk=root.id, archived=True).exists()
    with transaction.atomic(), references.prepared_content_guard(proof):
        Session.objects.filter(pk=root.id).update(archived=False)
        with pytest.raises(ContentIntegrityError):
            references.intern_contents([text], 'codex', {encoded.sha256: encoded})
    assert not SessionItemContent.objects.filter(sha256=encoded.sha256).exists()
    existing = content_row_factory(text, False)
    with transaction.atomic(), references.prepared_content_guard(proof):
        assert references.intern_contents([text], 'claude', {encoded.sha256: encoded}) == {text: existing.id}
    existing.refresh_from_db()
    assert existing.codec == 'plain' and existing.dictionary_provider == 'codex'


def test_binding_validates_whole_batch_before_ownership_write():
    root, other = session('root'), session('other')
    with transaction.atomic():
        ids = references.intern_contents(['source'], 'codex')
        row = item(root, ids['source'])
        with pytest.raises(ContentIntegrityError):
            references.bind_persisted_items([
                ContentBinding(row.id, root.id, ids['source'], None),
                ContentBinding(row.id, other.id, ids['source'], None),
            ])
        assert owners(ids['source']) == []


@pytest.mark.django_db(transaction=True)
def test_count_cleanup_preserves_database_error_during_outer_rollback():
    from django.db import IntegrityError
    root = session('root')
    SessionItem.objects.create(session=root, line_num=1, content='existing')
    with pytest.raises(IntegrityError), transaction.atomic(), references.session_reference_operation(
        root.id, item_count=500000, expected_batches=2,
    ):
        SessionItem.objects.create(session=root, line_num=1, content='duplicate')
    with connection.cursor() as cursor:
        cursor.execute('SELECT name FROM sqlite_temp_master WHERE name LIKE %s', ['content_reference_counts_%'])
        assert cursor.fetchall() == []


def test_count_validation_reads_only_bounded_delta_keys():
    from django.test.utils import CaptureQueriesContext
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['old', 'new'], 'codex')
        row = item(root, ids['old'])
    with references.session_reference_operation(
        root.id, item_count=500000, expected_batches=2,
    ) as operation, transaction.atomic(), CaptureQueriesContext(connection) as queries, operation.capture_mutation([row.id]):
        SessionItem.objects.filter(pk=row.id).update(current_content_id=ids['new'])
    validations = [query['sql'] for query in queries if 'count < 0' in query['sql']]
    assert len(validations) == 1
    assert 'content_id IN' in validations[0]


def test_reference_pages_are_bounded_guarded_and_count_equal_slots():
    from django.test.utils import CaptureQueriesContext
    root = session('pages')
    with transaction.atomic():
        ids = references.intern_contents(['same'], 'codex')
        for line in range(0, 5):
            item(root, ids['same'], ids['same'], line)
    operation = references.SessionReferenceOperation(root.id, 500000, 2)
    calls = []
    try:
        with CaptureQueriesContext(connection) as queries:
            assert not operation.initialize_page(row_budget=2, validate=lambda: calls.append(1) or True)
            assert not operation.initialize_page(row_budget=2, validate=lambda: calls.append(1) or True)
            assert operation.initialize_page(row_budget=2, validate=lambda: calls.append(1) or True)
        assert len(calls) == 3
        assert not any('UPDATE "main"' in q['sql'] or 'UNION ALL' in q['sql'] for q in queries)
        reads = [q['sql'] for q in queries if 'SELECT' in q['sql'] and 'core_sessionitem"' in q['sql']]
        assert len(reads) == 3 and all('LIMIT 2' in sql for sql in reads)
        assert references._reference_operation.get() is None
        with operation.activate(), transaction.atomic():
            assert references._referenced_ids(root.id, [ids['same']]) == {ids['same']}
        assert references._reference_operation.get() is None
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT count FROM {operation.table}')
            assert cursor.fetchone() == (10,)
    finally:
        operation.close()
        operation.close()


def test_reference_page_guard_rejects_without_refresh():
    operation = references.SessionReferenceOperation('gone', 500000, 2)
    try:
        with pytest.raises(ContentIntegrityError, match='observation'):
            operation.initialize_page(row_budget=1, validate=lambda: False)
        with pytest.raises(ContentIntegrityError):
            operation.initialize_page(row_budget=1, validate=lambda: True)
    finally:
        operation.close()


def test_reference_connection_replacement_aborts_before_temp_sql(monkeypatch):
    operation = references.SessionReferenceOperation('replace', 500000, 2)
    operation.initialize()
    physical = connection.connection
    try:
        monkeypatch.setattr(connection, 'connection', object())
        with pytest.raises(ContentIntegrityError, match='connection'):
            operation.validate_connection()
    finally:
        monkeypatch.setattr(connection, 'connection', physical)
        operation.close()


def test_reference_cached_reads_require_complete_pages_and_same_connection(monkeypatch):
    from django.test.utils import CaptureQueriesContext
    root = session('bounded-reads')
    with transaction.atomic():
        ids = references.intern_contents(['value'], 'codex')
        item(root, ids['value'])
    operation = references.SessionReferenceOperation(root.id, 500000, 2)
    try:
        assert not operation.initialize_page(row_budget=1, validate=lambda: True)
        with operation.activate(), pytest.raises(ContentIntegrityError, match='not initialized'):
            references._referenced_ids(root.id, [ids['value']])
        assert operation.initialize_page(row_budget=1, validate=lambda: True)
        with operation.activate(), CaptureQueriesContext(connection) as queries:
            for _ in range(3):
                assert references._referenced_ids(root.id, [ids['value']]) == {ids['value']}
        assert len(queries) == 3
        assert all('content_reference_counts_' in q['sql'] for q in queries)
        physical = connection.connection
        with operation.activate():
            monkeypatch.setattr(connection, 'connection', object())
            try:
                with pytest.raises(ContentIntegrityError, match='connection'):
                    references._referenced_ids(root.id, [ids['value']])
            finally:
                monkeypatch.setattr(connection, 'connection', physical)
    finally:
        operation.close()


def test_reference_page_failure_does_not_advance_cursor_or_damage_outer_transaction():
    root = session('page-rollback')
    operation = references.SessionReferenceOperation(root.id, 500000, 2)
    physical = connection.connection
    def failure():
        raise RuntimeError('guard failed')
    try:
        with pytest.raises(RuntimeError, match='guard failed'):
            operation.initialize_page(validate=failure)
        assert operation.initialization_cursor == -1
        assert not operation.initialized
    finally:
        operation.close()
    assert connection.connection is physical
    assert Session.objects.filter(pk=root.id).exists()
