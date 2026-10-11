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

pytestmark = pytest.mark.django_db
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


def test_promotion_cursor_between_descendants_with_repeated_lines(content_row_factory):
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
    assert completed == ['a-child', 'b-leaf', 'z-root']
    for row in rows:
        row.refresh_from_db()
        assert row.codec == 'plain'


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
def test_missing_descendant_metadata_never_publishes_readiness(content_row_factory, problem):
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
    assert not batch.verified
    maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True)
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None


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
    assert len(batches) == 3


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
