from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from tests import content_storage_helpers
from tests.test_content_root_state import session
from twicc.core.content_storage import state
from twicc.core.content_storage.types import RootPromotionCursor
from twicc.core.models import SessionItem
from twicc.content_maintenance_task import promote_root_slice

content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db(transaction=True)
START = RootPromotionCursor(None, -1, 1, 0, 0, False)


def item(owner, current, original=None):
    return SessionItem.objects.create(session=owner, line_num=1, content='', current_content=current,
                                      original_content=original)


def snapshot(root):
    with transaction.atomic():
        return state.snapshot_root_storage(root.id)


def test_promotion_cursor_between_current_and_original(content_row_factory):
    root = session('root', storage_incarnation='i', content_storage_version=1)
    current = content_row_factory('current' * 100, True, session_ids=[root.id])
    original = content_row_factory('original' * 100, True, session_ids=[root.id])
    item(root, current, original)
    observation = snapshot(root)
    cursor = promote_root_slice(observation, START, 1, 8 * 1024 * 1024)
    assert cursor.fk_slot == 0
    current.refresh_from_db()
    original.refresh_from_db()
    assert current.codec == 'plain' and original.codec == 'zstd'
    cursor = promote_root_slice(observation, cursor, 1, 8 * 1024 * 1024)
    assert cursor.fk_slot == 1 and cursor.done
    original.refresh_from_db()
    root.refresh_from_db()
    assert original.codec == 'plain'
    assert root.plaintext_verified_epoch is None
    # A resumed public cursor carries no whole-pass readiness proof.
    with transaction.atomic():
        assert state.verify_plaintext_root(observation)


def test_promotion_cursor_does_not_visit_descendants_with_repeated_lines(content_row_factory):
    root = session('z-root', storage_incarnation='r', content_storage_version=1)
    child = session('a-child', type='subagent', parent_session=root,
                    storage_incarnation='c', content_storage_version=1)
    leaf = session('b-leaf', type='subagent', parent_session=child,
                   storage_incarnation='l', content_storage_version=1)
    rows = []
    for owner in (child, leaf, root):
        row = content_row_factory(owner.id * 100, True, session_ids=[owner.id])
        item(owner, row)
        rows.append(row)
    observation = snapshot(root)
    cursor = START
    completed = []
    while not cursor.done:
        cursor = promote_root_slice(observation, cursor, 1, 1)
        completed.append(cursor.session_id)
    assert completed == ['z-root']
    for owner, row in zip((child, leaf, root), rows):
        row.refresh_from_db()
        assert row.codec == ('plain' if owner == root else 'zstd')


def test_epoch_change_rejects_prepared_promotion(content_row_factory):
    root = session('root', storage_incarnation='r', content_storage_version=1)
    row = content_row_factory('text' * 200, True, session_ids=[root.id])
    item(root, row)
    observation = snapshot(root)
    with transaction.atomic():
        state.invalidate_roots([root.id])
    cursor = promote_root_slice(observation, START, 1, 1)
    row.refresh_from_db()
    assert not cursor.done and cursor.session_id is None
    assert row.codec == 'zstd'


def test_cold_root_does_not_promote(content_row_factory):
    root = session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    row = content_row_factory('text' * 200, True, session_ids=[root.id])
    item(root, row)
    promote_root_slice(snapshot(root), START, 1, 1)
    row.refresh_from_db()
    assert row.codec == 'zstd'


def test_repeated_both_slots_advance_without_repeated_writes(content_row_factory):
    from twicc import content_maintenance_task as maintenance

    root = session('root', storage_incarnation='i', content_storage_version=1)
    row = content_row_factory('same' * 100, True, session_ids=[root.id])
    item(root, row, row)
    batch = maintenance.prepare_promotion_batch(snapshot(root), START, 500, 8000000)
    assert batch.cursor.fk_slot == 1 and batch.cursor.rows == 2
    assert len(batch.entries) == 1
    cursor = maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True)
    assert cursor.done
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.codec == 'plain' and root.plaintext_verified_epoch == root.content_state_epoch


@pytest.mark.parametrize('problem', ['version', 'identity', 'current', 'legacy', 'non_native'])
def test_missing_descendant_metadata_does_not_block_root_readiness(content_row_factory, problem):
    from twicc import content_maintenance_task as maintenance

    root = session('a-root', storage_incarnation='i', content_storage_version=1)
    child = session('b-child', type='subagent', parent_session=root,
                    storage_incarnation=None if problem == 'identity' else 'c',
                    content_storage_version=None if problem == 'version' else 1)
    row = content_row_factory('same' * 100, True, session_ids=[child.id])
    SessionItem.objects.create(session=child, line_num=1, content='legacy' if problem == 'legacy' else '',
                               current_content=None if problem == 'current' else row)
    if problem == 'non_native':
        child.type = 'session'
        child.save(update_fields=['type'])
    batch = maintenance.prepare_promotion_batch(snapshot(root), START, 500, 8000000)
    assert batch.verified
    maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True)
    root.refresh_from_db()
    row.refresh_from_db()
    assert root.plaintext_verified_epoch == root.content_state_epoch
    assert row.codec == 'zstd'


def test_new_child_invalidates_final_batch_and_restarts(content_row_factory):
    from twicc import content_maintenance_task as maintenance

    root = session('a-root', storage_incarnation='i', content_storage_version=1)
    row = content_row_factory('same' * 100, True, session_ids=[root.id])
    item(root, row)
    observation = snapshot(root)
    batch = maintenance.prepare_promotion_batch(observation, START, 500, 8000000)
    with transaction.atomic():
        session('b-child', type='subagent', parent_session=root)
        state.invalidate_roots([root.id])
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None


def test_promotion_counts_empty_session_metadata_within_budget():
    from twicc import content_maintenance_task as maintenance

    root = session('a-root', storage_incarnation='i', content_storage_version=1)
    for index in range(5):
        session(f'child-{index}', type='subagent', parent_session=root,
                storage_incarnation=f'c{index}', content_storage_version=1)
    cursor = START
    observation = snapshot(root)
    batches = []
    while not cursor.done:
        batch = maintenance.prepare_promotion_batch(observation, cursor, 2, 100)
        assert batch.cursor.rows <= 2
        batches.append(batch)
        cursor = maintenance.apply_promotion_batch(batch, frozenset())
    assert len(batches) == 1
    assert batches[0].cursor.session_id == root.id


def test_promotion_budget_includes_prepared_plaintext_for_compressible_large_rows(content_row_factory):
    from twicc import content_maintenance_task as maintenance

    root = session('root', storage_incarnation='i', content_storage_version=1)
    for line in range(1, 4):
        row = content_row_factory(str(line) * (5 * 1024 * 1024), True, session_ids=[root.id])
        SessionItem.objects.create(session=root, line_num=line, content='', current_content=row)
    batch = maintenance.prepare_promotion_batch(snapshot(root), START, 500, 8 * 1024 * 1024)
    assert len(batch.entries) == 1
    assert batch.cursor.bytes >= 5 * 1024 * 1024
    assert not batch.cursor.done


def test_promotion_avoids_compression_eligibility_proofs(content_row_factory, monkeypatch):
    from twicc import content_maintenance_task as maintenance

    root = session('root', storage_incarnation='i', content_storage_version=1)
    shared = session('shared', archived=True, created_at=timezone.now() - timedelta(days=31))
    row = content_row_factory('shared' * 200, True, session_ids=[root.id, shared.id])
    item(root, row, row)

    def forbidden(*args, **kwargs):
        pytest.fail('Promotion performs compression eligibility proof work')

    monkeypatch.setattr(maintenance, 'publish_prepared_batch', forbidden)
    monkeypatch.setattr(maintenance, '_owners_cold', forbidden)
    batch = maintenance.prepare_promotion_batch(snapshot(root), START)
    # Unrelated shared-owner lifecycle changes do not invalidate exact decompression.
    shared.archived = False
    shared.save(update_fields=['archived'])
    cursor = maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True)
    row.refresh_from_db()
    root.refresh_from_db()
    assert cursor.done and row.codec == 'plain'
    assert row.session_ids == ['root', 'shared']
    assert root.plaintext_verified_epoch == root.content_state_epoch


@pytest.mark.parametrize('change', ['sha256', 'raw_size', 'codec', 'dictionary_id', 'delete'])
def test_promotion_rejects_changed_source_representation(content_row_factory, change):
    from twicc import content_maintenance_task as maintenance
    from twicc.core.models import SessionItemContent

    root = session('root', storage_incarnation='i', content_storage_version=1)
    row = content_row_factory('exact' * 200, True, session_ids=[root.id])
    item(root, row)
    batch = maintenance.prepare_promotion_batch(snapshot(root), START)
    changes = {'payload': b'changed', 'sha256': b'x' * 32, 'raw_size': 1,
               'codec': 'plain', 'dictionary_id': None}
    if change == 'delete':
        SessionItem.objects.filter(session=root).delete()
        row.delete()
    else:
        SessionItemContent.objects.filter(pk=row.id).update(**{change: changes[change]})
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None


@pytest.mark.parametrize('change', ['incarnation', 'epoch', 'replacement', 'reference'])
def test_promotion_rejects_root_or_reference_generation_changes(content_row_factory, change):
    from twicc import content_maintenance_task as maintenance
    from twicc.core.models import Session

    root = session('root', storage_incarnation='i', content_storage_version=1)
    row = content_row_factory('exact' * 200, True, session_ids=[root.id])
    ref = item(root, row)
    observation = snapshot(root)
    batch = maintenance.prepare_promotion_batch(observation, START)
    if change == 'incarnation':
        Session.objects.filter(pk=root.id).update(storage_incarnation='new')
    elif change == 'replacement':
        root.delete()
        session('root', storage_incarnation='new', content_storage_version=1,
                content_state_epoch=observation.epoch)
    else:
        with transaction.atomic():
            if change == 'reference':
                replacement = content_row_factory('replacement' * 100, True, session_ids=[root.id])
                SessionItem.objects.filter(pk=ref.pk).update(current_content=replacement)
            state.invalidate_roots([root.id])
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    row.refresh_from_db()
    assert row.codec == 'zstd'
    assert Session.objects.get(pk='root').plaintext_verified_epoch is None


def test_already_promoted_representation_retries_without_stale_publication(content_row_factory):
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.codec import encode_plain
    from twicc.core.models import SessionItemContent

    root = session('root', storage_incarnation='i', content_storage_version=1)
    text = 'same' * 200
    row = content_row_factory(text, True, session_ids=[root.id])
    item(root, row, row)
    observation = snapshot(root)
    batch = maintenance.prepare_promotion_batch(observation, START)
    SessionItemContent.objects.filter(pk=row.pk).update(payload=encode_plain(text).payload, codec='plain', dictionary=None)
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    retry = maintenance.prepare_promotion_batch(observation, START)
    assert maintenance.apply_promotion_batch(retry, frozenset(), pass_verified=True).done
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == observation.epoch


@pytest.mark.django_db(transaction=True)
def test_preparation_byte_accounting_survives_separate_connection_promotion(content_row_factory, monkeypatch, tmp_path):
    import sqlite3
    from django.db import connections
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage.codec import encode_plain
    from twicc.core.models import SessionItemContent

    root = session('root', storage_incarnation='i', content_storage_version=1)
    row = content_row_factory('large' * 10000, True, session_ids=[root.id])
    item(root, row)
    stored = bytes(row.payload)
    plain = encode_plain('large' * 10000)
    # Copy only this tiny synthetic pytest database into a WAL fixture.
    source = connections['default']
    path = tmp_path / 'promotion-race.sqlite'
    with sqlite3.connect(path) as target:
        source.connection.backup(target)
        target.execute('PRAGMA journal_mode=WAL')
    reader = source.copy()
    reader.settings_dict['NAME'] = str(path)
    connections['default'] = reader
    independent = reader.copy()
    original = SessionItemContent.objects.in_bulk
    attempted = []

    def concurrent_promotion(ids):
        # The writer commits after metadata reads. The reader must retain
        # its compressed snapshot while preparation performs codec work.
        with independent.cursor() as cursor:
            cursor.execute('UPDATE core_sessionitemcontent SET payload = %s, codec = %s, dictionary_id = NULL '
                           'WHERE id = %s', [plain.payload, 'plain', row.pk])
        attempted.append('committed')
        return original(ids)

    monkeypatch.setattr(SessionItemContent.objects, 'in_bulk', concurrent_promotion)
    try:
        batch = maintenance.prepare_promotion_batch(snapshot(root), START)
    finally:
        independent.close()
        reader.close()
        connections['default'] = source
    assert attempted == ['committed']
    assert len(batch.entries) == 1
    assert batch.entries[0].observation.stored_size == len(stored)
    assert batch.cursor.bytes >= plain.raw_size + len(stored)


def test_rejected_source_keeps_other_prepared_content_compressed(content_row_factory):
    from twicc import content_maintenance_task as maintenance
    from twicc.core.models import SessionItemContent

    root = session('root', storage_incarnation='i', content_storage_version=1)
    current = content_row_factory('current' * 200, True, session_ids=[root.id])
    original = content_row_factory('original' * 200, True, session_ids=[root.id])
    item(root, current, original)
    batch = maintenance.prepare_promotion_batch(snapshot(root), START)
    SessionItemContent.objects.filter(pk=original.pk).update(sha256=b'x' * 32)
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    current.refresh_from_db()
    assert current.codec == 'zstd'
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None
