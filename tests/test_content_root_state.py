import hashlib
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.content_storage import state
from twicc.core.models import Project, Session, SessionItem, SessionItemContent

pytestmark = pytest.mark.django_db


def session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='p', defaults={'directory': '/p'})
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name, **kwargs)


def test_resolver_nested_active_and_bad_ancestry():
    root = session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    child = session('child', type='subagent', parent_session=root)
    leaf = session('leaf', type='subagent', parent_session=child)
    decision = state.resolve_root_decision(leaf.id, timezone.now(), frozenset())
    assert decision.resolved and decision.is_cold and decision.root_id == root.id
    assert state.resolve_root_decision(leaf.id, timezone.now(), frozenset({root.id})).active
    assert not state.resolve_root_decision(leaf.id, timezone.now(), frozenset({root.id})).is_cold
    assert not state.resolve_root_decision('missing', timezone.now(), frozenset()).resolved
    assert not state.resolve_root_decision(session('orphan', type='subagent').id, timezone.now(), frozenset()).resolved
    root.type = 'subagent'
    root.parent_session = leaf
    root.save(update_fields=['type', 'parent_session'])
    assert not state.resolve_root_decision(leaf.id, timezone.now(), frozenset()).resolved
    assert list(state.iter_native_tree_session_pages(root.id, None, 2)) == []


def test_native_membership_is_stable_paged_and_resumable():
    root = session('m-root')
    child = session('a-child', type='subagent', parent_session=root)
    session('z-leaf', type='subagent', parent_session=child)
    session('spawned', spawned_by=root)
    assert list(state.iter_native_tree_session_pages(root.id, None, 2)) == [['a-child', 'm-root'], ['z-leaf']]
    assert list(state.iter_native_tree_session_pages(root.id, 'm-root', 2)) == [['z-leaf']]


def test_stale_promotion_epoch_cannot_mark_ready():
    root = session('root')
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        state.invalidate_roots([root.id, root.id])
        assert not state.verify_plaintext_root(snapshot)
    root.refresh_from_db()
    assert root.content_state_epoch == snapshot.epoch + 1
    assert root.plaintext_verified_epoch is None


def test_tree_change_invalidates_both_roots():
    old = session('old')
    new = session('new')
    child = session('child', type='subagent', parent_session=old)
    with transaction.atomic():
        snapshots = [state.snapshot_root_storage(root.id) for root in (old, new)]
        child.parent_session = new
        child.save(update_fields=['parent_session'])
        state.invalidate_roots([old.id, new.id])
        assert all(not state.verify_plaintext_root(snapshot) for snapshot in snapshots)


def test_same_id_new_incarnation_rejects_old_promotion():
    root = session('root')
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        root.delete()
        recreated = session('root', content_state_epoch=snapshot.epoch)
        state.ensure_storage_incarnation(recreated.id)
        recreated.refresh_from_db()
        assert recreated.storage_incarnation != snapshot.incarnation
        assert not state.verify_plaintext_root(snapshot)


def test_verification_checks_both_slots_and_legacy_extraction():
    root = session('root', content_storage_version=1)
    raw = b'content'
    plain = SessionItemContent.objects.create(sha256=hashlib.sha256(raw).digest(), payload=raw, raw_size=len(raw))
    original = SessionItemContent.objects.create(sha256=hashlib.sha256(b'original').digest(), payload=b'x',
                                                 raw_size=8, codec='zstd')
    item = SessionItem.objects.create(session=root, line_num=1, content='', current_content=plain,
                                     original_content=original)
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        assert not state.verify_plaintext_root(snapshot)
        original.codec = 'plain'
        original.save(update_fields=['codec'])
        assert state.verify_plaintext_root(snapshot)
        root.refresh_from_db()
        assert root.plaintext_verified_epoch == snapshot.epoch
        SessionItem.objects.create(session=root, line_num=2, content='legacy')
        state.invalidate_roots([root.id])
        snapshot = state.snapshot_root_storage(root.id)
        assert not state.verify_plaintext_root(snapshot)
        assert SessionItem.objects.get(pk=item.pk).current_content_id == plain.pk


@pytest.mark.django_db(transaction=True)
def test_writer_helpers_require_caller_transaction():
    root = session('root')
    with pytest.raises(transaction.TransactionManagementError):
        state.ensure_storage_incarnation(root.id)
    with pytest.raises(transaction.TransactionManagementError):
        state.invalidate_roots([root.id])


def test_deep_native_tree_membership_does_not_expand_root_verification():
    root = session('root', content_storage_version=1)
    parent = root
    with transaction.atomic():
        state.ensure_storage_incarnation(root.id)
        for index in range(140):
            parent = session(f'child-{index:03}', type='subagent', parent_session=parent, content_storage_version=1)
            state.ensure_storage_incarnation(parent.id)
        empty = SessionItemContent.objects.create(sha256=hashlib.sha256(b'').digest(), payload=b'', raw_size=0)
        SessionItem.objects.create(session=parent, line_num=1, content='', current_content=empty)
        snapshot = state.snapshot_root_storage(root.id)
        assert sum(map(len, state.iter_native_tree_session_pages(root.id, None, 17))) == 141
        assert state.verify_plaintext_root(snapshot)
        parent.content_storage_version = None
        parent.save(update_fields=['content_storage_version'])
        state.invalidate_roots([root.id])
        assert state.verify_plaintext_root(state.snapshot_root_storage(root.id))
        assert not state.session_plaintext_metadata(parent.id)


def test_verification_never_selects_payload_or_inline_text():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    root = session('root', content_storage_version=1)
    raw = b'x' * 2_000_000
    plain = SessionItemContent.objects.create(sha256=hashlib.sha256(raw).digest(), payload=raw, raw_size=len(raw))
    SessionItem.objects.create(session=root, line_num=1, content='', current_content=plain)
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        with CaptureQueriesContext(connection) as queries:
            assert state.verify_plaintext_root(snapshot)
    selections = [entry['sql'] for entry in queries.captured_queries if entry['sql'].startswith('SELECT')]
    assert all('"payload"' not in sql for sql in selections)
    assert all('"content",' not in sql for sql in selections)


def test_unknown_identity_cannot_publish_readiness():
    from twicc.core.content_storage.types import RootStorageSnapshot

    root = session('root', content_storage_version=1, content_state_epoch=0)
    with transaction.atomic():
        assert not state.verify_plaintext_root(RootStorageSnapshot(root.id, 'unknown', 0))
    root.refresh_from_db()
    assert root.storage_incarnation is None
    assert root.plaintext_verified_epoch is None


def test_snapshot_policy_transition_rechecks_cold_and_active_root():
    root = session('root', content_storage_version=1, created_at=timezone.now() - timedelta(days=40))
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        root.archived = True
        root.save(update_fields=['archived'])
        assert not state.verify_plaintext_root(snapshot)
        assert state.verify_plaintext_root(snapshot, active_root_ids=frozenset({root.id}))


def test_snapshot_ancestry_transition_rejects_former_root():
    root = session('root', content_storage_version=1)
    new = session('new', content_storage_version=1)
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        root.type = 'subagent'
        root.parent_session = new
        root.save(update_fields=['type', 'parent_session'])
        assert not state.verify_plaintext_root(snapshot)


def test_dateless_resolved_root_can_publish_actual_plaintext_readiness():
    root = session('root', archived=True, content_storage_version=1)
    plain = SessionItemContent.objects.create(sha256=hashlib.sha256(b'').digest(), payload=b'', raw_size=0)
    SessionItem.objects.create(session=root, line_num=1, content='', current_content=plain)
    decision = state.resolve_root_decision(root.id, timezone.now(), frozenset())
    assert decision.resolved
    assert not decision.is_cold
    with transaction.atomic():
        snapshot = state.snapshot_root_storage(root.id)
        assert state.verify_plaintext_root(snapshot)
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == root.content_state_epoch
