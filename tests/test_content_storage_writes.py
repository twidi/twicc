"""Content publication writes preserve assignments and avoid redundant ownership writes."""

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from twicc.core.content_storage import references
from twicc.core.content_storage.codec import ContentIntegrityError
from twicc.core.models import SessionItem, SessionItemContent
from tests.test_content_references import session, item, bind, owners

pytestmark = pytest.mark.django_db


def test_owner_present_produces_no_update():
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['source'], 'codex')
        row = item(root, ids['source'])
        bind(row)
        initial_owner_ids = owners(ids['source'])
        updates = []
        def capture(execute, sql, params, many, context):
            if sql.startswith('UPDATE "core_sessionitemcontent"'):
                updates.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            bind(row)
    owner_update_count = len(updates)
    assert owner_update_count == 0
    assert owners(ids['source']) == initial_owner_ids


def test_owner_addition_preserves_other_owners():
    first, second = session('first'), session('second')
    with transaction.atomic():
        ids = references.intern_contents(['source'], 'codex')
        bind(item(first, ids['source']))
        bind(item(second, ids['source']))
    assert owners(ids['source']) == ['first', 'second']


@pytest.mark.parametrize('original_text', [None, 'original'])
def test_assignment_and_legacy_clear_share_one_update(original_text):
    from twicc.core.content_storage.writes import write_item_content_assignments
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['current', 'original'], 'codex')
        original_id = ids[original_text] if original_text is not None else None
        row = item(root, None, original_id)
        SessionItem.objects.filter(pk=row.pk).update(content='legacy')
        row.current_content_id, row.content = ids['current'], ''
        writes = []
        def capture(execute, sql, params, many, context):
            if sql.startswith('UPDATE'):
                writes.append((sql, params, many))
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            write_item_content_assignments([row, row])
    assert len(writes) == 1
    assert writes[0][2] is True
    assert len(writes[0][1]) == 1
    assert 'CASE' not in writes[0][0]
    row.refresh_from_db()
    assert (row.current_content_id, row.original_content_id, row.content) == (ids['current'], original_id, '')


def test_conflicting_assignments_reject_before_any_write():
    from twicc.core.content_storage.writes import write_item_content_assignments
    root = session('root')
    row = item(root, None)
    duplicate = SessionItem.objects.get(pk=row.pk)
    duplicate.content = 'different'
    with transaction.atomic(), pytest.raises(ContentIntegrityError), CaptureQueriesContext(connection) as queries:
        write_item_content_assignments([row, duplicate])
    assert not any(q['sql'].startswith('UPDATE') for q in queries)


def test_owner_json_matches_django_field_preparation():
    from twicc.core.content_storage.writes import write_content_owners
    with transaction.atomic():
        pk = references.intern_contents(['source'], 'codex')['source']
        row = SessionItemContent.objects.get(pk=pk)
        row.session_ids = ['quote"', 'é', 'line\n']
        write_content_owners([row])
        with connection.cursor() as cursor:
            cursor.execute('SELECT session_ids FROM core_sessionitemcontent WHERE id = %s', [pk])
            stored = cursor.fetchone()[0]
    expected = SessionItemContent._meta.get_field('session_ids').get_db_prep_save(row.session_ids, connection)
    assert stored == expected
    assert owners(pk) == ['quote"', 'é', 'line\n']


def test_assignment_rollback_restores_counts_and_owners():
    from twicc.core.content_storage.writes import write_item_content_assignments
    root = session('root')
    with transaction.atomic():
        ids = references.intern_contents(['old', 'new'], 'codex')
        row = item(root, ids['old'], ids['old'])
        bind(row)
    with references.session_reference_operation(root.id, item_count=500000, expected_batches=2) as operation:
        with pytest.raises(RuntimeError), transaction.atomic():
            with operation.capture_mutation([row.pk]):
                row.current_content_id = ids['new']
                write_item_content_assignments([row])
            bind(row)
            references.reconcile_session_memberships(root.id, [ids['old']])
            raise RuntimeError('rollback')
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT content_id, count FROM {operation.table} ORDER BY content_id')
            assert cursor.fetchall() == [(ids['old'], 2)]
    row.refresh_from_db()
    assert row.current_content_id == row.original_content_id == ids['old']
    assert owners(ids['old']) == ['root']
    assert owners(ids['new']) == []


def test_identical_override_produces_no_item_update():
    from twicc.core.content_storage.extraction import apply_override_slice
    from tests.test_content_extraction import make_session, message
    root = make_session()
    with transaction.atomic():
        pk = references.intern_contents(['same'], 'codex')['same']
        row = item(root, pk)
        bind(row)
    updates = []
    def capture(execute, sql, params, many, context):
        if sql.startswith('UPDATE "core_sessionitem"'):
            updates.append(sql)
        return execute(sql, params, many, context)
    with references.session_reference_operation(root.id, item_count=1, expected_batches=1) as operation:
        with connection.execute_wrapper(capture):
            apply_override_slice(message(root), [{'id': row.id, 'content': 'same'}], operation)
    assert updates == []
    row.refresh_from_db()
    assert row.current_content_id == pk


@pytest.mark.django_db(transaction=True)
def test_writers_require_caller_transaction():
    from twicc.core.content_storage.writes import write_content_owners, write_item_content_assignments
    for writer in (write_content_owners, write_item_content_assignments):
        with pytest.raises(transaction.TransactionManagementError):
            writer([])


def test_extraction_uses_fixed_assignments_and_preserves_original():
    from twicc.core.content_storage.extraction import extract_storage_slice
    from tests.test_content_extraction import make_session
    root = make_session()
    with transaction.atomic():
        original = references.intern_contents(['original'], 'codex')['original']
        row = item(root, None, original)
        SessionItem.objects.filter(pk=row.pk).update(content='legacy é')
    updates = []
    def capture(execute, sql, params, many, context):
        if sql.startswith('UPDATE "core_sessionitem"'):
            updates.append((sql, many))
        return execute(sql, params, many, context)
    with connection.execute_wrapper(capture):
        extract_storage_slice(root.id, root.storage_incarnation, 0, 10, -1, 256, 1024)
    assert len(updates) == 1
    assert updates[0][1] is True
    assert 'CASE' not in updates[0][0]
    row.refresh_from_db()
    assert row.original_content_id == original
    assert row.content == ''
    assert row.current_content.raw_size == len('legacy é'.encode())


def test_conflicting_overrides_reject_before_interning():
    from twicc.core.content_storage.extraction import apply_override_slice
    from tests.test_content_extraction import make_session, message
    root = make_session()
    row = SessionItem.objects.create(session=root, line_num=1, content='legacy')
    with references.session_reference_operation(root.id, item_count=1, expected_batches=1) as operation:
        with pytest.raises(ContentIntegrityError):
            apply_override_slice(message(root), [
                {'id': row.id, 'content': 'first'}, {'id': row.id, 'content': 'second'},
            ], operation)
    row.refresh_from_db()
    assert row.current_content_id is None
    assert row.content == 'legacy'
    assert SessionItemContent.objects.count() == 0
