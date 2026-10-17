"""Committed metadata reuse must preserve current native-root policy."""

from tests.tree_aggregate_helpers import begin_history_replacement
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.content_storage import state
from twicc.core.models import Project, Session

pytestmark = pytest.mark.django_db(transaction=True)


def make_session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='cold-cache', defaults={'directory': '/cold-cache'})
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name, **kwargs)


def decisions(ids, now=None, active=frozenset()):
    from twicc.core.content_storage.cold_cache import cached_root_decisions
    return cached_root_decisions(ids, now or timezone.now(), active)


def test_repeated_contents_reuse_root_metadata():
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    with CaptureQueriesContext(connection) as queries:
        for _ in range(5):
            assert decisions(['root'])['root'].is_cold
    assert len(queries) == 0


def test_reference_epoch_does_not_evict_lifecycle():
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    decisions(['root'])
    with transaction.atomic():
        state.invalidate_roots(['root'])
    with CaptureQueriesContext(connection) as queries:
        assert decisions(['root'])['root'].is_cold
    assert len(queries) == 0


def test_subagent_uses_root_policy():
    root = make_session('root', created_at=timezone.now() - timedelta(days=31))
    child = make_session('child', type='subagent', parent_session=root, created_at=timezone.now())
    leaf = make_session('leaf', type='subagent', parent_session=child)
    assert decisions(['child'])['child'].is_cold
    assert not decisions(['root'], active=frozenset({leaf.id}))['root'].is_cold


@pytest.mark.parametrize('archived,days', [(True, 15), (False, 30)])
def test_clock_crosses_threshold_in_both_directions(archived, days):
    now = timezone.now()
    make_session('root', archived=archived, created_at=now - timedelta(days=days))
    assert decisions(['root'], now)['root'].is_cold
    assert not decisions(['root'], now - timedelta(microseconds=1))['root'].is_cold
    assert decisions(['root'], now + timedelta(microseconds=1))['root'].is_cold


def test_reparent_invalidates_both_roots_after_commit():
    from twicc.core.content_storage.lifecycle import update_lifecycle
    old = make_session('old', created_at=timezone.now() - timedelta(days=31))
    new = make_session('new', created_at=timezone.now())
    make_session('child', type='subagent', parent_session=old)
    decisions(['old', 'new', 'child'])
    update_lifecycle(Session.objects.filter(pk='child'), parent_session_id=new.id)
    with CaptureQueriesContext(connection) as old_queries:
        assert decisions(['old'])['old'].is_cold
    with CaptureQueriesContext(connection) as new_queries:
        assert not decisions(['new'])['new'].is_cold
    assert len(old_queries) == len(new_queries) == 1
    result = decisions(['old', 'new', 'child'])
    assert result['child'].root_id == 'new'
    assert not result['child'].is_cold
    assert result['old'].is_cold


def test_rollback_does_not_publish_lifecycle_metadata():
    from twicc.core.content_storage.lifecycle import update_lifecycle
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    decisions(['root'])
    with pytest.raises(RuntimeError), transaction.atomic():
        update_lifecycle(Session.objects.filter(pk='root'), last_updated_at=timezone.now())
        raise RuntimeError('rollback')
    assert decisions(['root'])['root'].is_cold


def test_invalidated_inflight_fill_cannot_install_old_metadata(monkeypatch):
    from twicc.core.content_storage import cold_cache
    from twicc.core.content_storage.lifecycle import update_lifecycle
    from tests.content_storage_helpers import _public_dictionary
    from tests.test_content_extraction import message
    from twicc.core.models import SessionItem, SessionItemContent
    from twicc.providers.compute_base import BaseSessionCompute

    _public_dictionary('codex')
    session = make_session('root', created_at=timezone.now() - timedelta(days=31), last_line=1)
    SessionItem.objects.create(session=session, line_num=1, content='repeat ' * 1000)
    original = cold_cache._read_metadata
    changed = False

    def read_then_commit(ids):
        nonlocal changed
        rows = original(ids)
        if not changed:
            changed = True
            update_lifecycle(Session.objects.filter(pk='root'), last_updated_at=timezone.now())
        return rows

    monkeypatch.setattr(cold_cache, '_read_metadata', read_then_commit)
    assert not decisions(['root'])['root'].is_cold
    assert not decisions(['root'])['root'].is_cold
    session.refresh_from_db()
    assert BaseSessionCompute.apply_session_complete(message(session)).outcome == 'applied'
    assert SessionItemContent.objects.filter(codec='zstd').count() == 0


def test_database_identity_replacement_discards_old_metadata(monkeypatch):
    from twicc.core.content_storage import cold_cache
    identity = ['first']
    monkeypatch.setattr(cold_cache, '_database_identity', lambda: identity[0])
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    Session.objects.filter(pk='root').update(last_updated_at=timezone.now())
    identity[0] = 'replacement'
    assert not decisions(['root'])['root'].is_cold


def test_late_parent_creation_resolves_previously_missing_root():
    from twicc.core.content_storage.lifecycle import save_lifecycle
    assert not decisions(['parent'])['parent'].resolved
    project, _ = Project.objects.get_or_create(id='cold-cache', defaults={'directory': '/cold-cache'})
    parent = Session(id='parent', project=project, provider='codex', file_path='parent',
                     created_at=timezone.now() - timedelta(days=31))
    save_lifecycle(parent)
    assert decisions(['parent'])['parent'].is_cold


def test_atomic_cache_miss_cannot_publish_rolled_back_metadata():
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    with pytest.raises(RuntimeError), transaction.atomic():
        Session.objects.filter(pk='root').update(last_updated_at=timezone.now())
        assert not decisions(['root'])['root'].is_cold
        raise RuntimeError('rollback')
    assert decisions(['root'])['root'].is_cold


def test_publication_reuses_policy_within_one_writer_lease():
    from twicc.core.content_storage import extraction
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    decisions(['root'])
    # A shared owner uses committed session metadata rather than pending target fields.
    placement = {'pending_session_id': 'different-target', 'active_session_ids': frozenset(),
                 'now': timezone.now()}

    token = extraction._placement.set(placement)
    try:
        with transaction.atomic(), extraction._publication_decisions():
            with CaptureQueriesContext(connection) as first:
                assert extraction.cold_placement('root')
            with CaptureQueriesContext(connection) as repeated:
                assert extraction.cold_placement('root')
            assert len(first) == 1
            assert len(repeated) == 0
    finally:
        extraction._placement.reset(token)


def test_uncommitted_reparent_reads_local_mapping_without_global_publication():
    from twicc.core.content_storage.lifecycle import update_lifecycle
    old = make_session('old', created_at=timezone.now() - timedelta(days=31))
    new = make_session('new', created_at=timezone.now())
    make_session('child', type='subagent', parent_session=old)
    assert decisions(['child'])['child'].is_cold
    with pytest.raises(RuntimeError), transaction.atomic():
        update_lifecycle(Session.objects.filter(pk='child'), parent_session_id=new.id)
        assert decisions(['child'])['child'].root_id == 'new'
        assert not decisions(['child'])['child'].is_cold
        raise RuntimeError('rollback')
    assert decisions(['child'])['child'].root_id == 'old'
    assert decisions(['child'])['child'].is_cold


def test_savepoint_rollback_restores_committed_cache_reuse():
    from twicc.core.content_storage.lifecycle import update_lifecycle
    make_session('root', created_at=timezone.now())
    assert not decisions(['root'])['root'].is_cold
    with transaction.atomic():
        with pytest.raises(RuntimeError), transaction.atomic():
            update_lifecycle(Session.objects.filter(pk='root'), created_at=timezone.now() - timedelta(days=31))
            assert decisions(['root'])['root'].is_cold
            raise RuntimeError('savepoint rollback')
        with CaptureQueriesContext(connection) as queries:
            assert not decisions(['root'])['root'].is_cold
        assert len(queries) == 0
    assert not decisions(['root'])['root'].is_cold


def test_invalidated_fill_under_writer_stays_conservative(monkeypatch):
    from twicc.core.content_storage import cold_cache
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    original = cold_cache._read_metadata
    invalidated = False

    def invalidate_after_read(ids):
        nonlocal invalidated
        rows = original(ids)
        if not invalidated:
            invalidated = True
            cold_cache.invalidate_cold_metadata(ids)
        return rows

    monkeypatch.setattr(cold_cache, '_read_metadata', invalidate_after_read)
    with transaction.atomic():
        decision = decisions(['root'])['root']
        assert not decision.resolved
        assert not decision.is_cold
    assert decisions(['root'])['root'].is_cold


def test_inflight_batch_cannot_return_invalidated_cache_hit(monkeypatch):
    from twicc.core.content_storage import cold_cache
    from twicc.core.content_storage.lifecycle import update_lifecycle
    make_session('root', created_at=timezone.now() - timedelta(days=31))
    make_session('other')
    assert decisions(['root'])['root'].is_cold
    original = cold_cache._read_metadata
    changed = False

    def read_then_change_cached_root(ids):
        nonlocal changed
        rows = original(ids)
        if not changed:
            changed = True
            update_lifecycle(Session.objects.filter(pk='root'), last_updated_at=timezone.now())
        return rows

    monkeypatch.setattr(cold_cache, '_read_metadata', read_then_change_cached_root)
    assert not decisions(['root', 'other'])['root'].is_cold


def test_reference_only_ingestion_invalidation_reuses_lifecycle_metadata():
    from twicc.core.content_storage.ingestion import initialize_ingestion_session
    root = make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    with transaction.atomic():
        initialize_ingestion_session(root, invalidate=True)
    with CaptureQueriesContext(connection) as queries:
        assert decisions(['root'])['root'].is_cold
    assert len(queries) == 0


def test_history_replacement_keeps_committed_lifecycle_cache():
    from tests.test_content_history_lifecycle import job
    root = make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    begin_history_replacement(job(root))
    with CaptureQueriesContext(connection) as queries:
        assert decisions(['root'])['root'].is_cold
    assert len(queries) == 0


def test_watcher_creation_discards_previous_identity_mapping():
    from twicc.core.models import SessionType
    from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
    from twicc.providers.sessions_watcher import ParsedSessionFile
    root = make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    # Reproduce an existing cache entry for an identity recreated by source ingestion.
    root.delete()
    watcher = ClaudeCodeSessionsWatcher()
    project = Project.objects.get(pk='cold-cache')
    watcher.create_session_sync(ParsedSessionFile(project.id, 'root', SessionType.SESSION, '/root'), project)
    assert decisions(['root'])['root'].root_id == 'root'
    assert not decisions(['root'])['root'].is_cold


def test_root_deletion_discards_cached_descendant_mapping():
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
    root = make_session('root', created_at=timezone.now() - timedelta(days=31))
    make_session('child', type='subagent', parent_session=root)
    assert decisions(['child'])['child'].is_cold
    assert _apply_delete_sessions_payload(DeleteSessionsPayload('codex', ['root'])) == ['child', 'root']
    result = decisions(['root', 'child'])
    assert not result['root'].resolved
    assert not result['child'].resolved


def test_lifecycle_metadata_accessor_cannot_mutate_shared_cache():
    from twicc.core.content_storage.cold_cache import cached_lifecycle_metadata

    make_session('root', created_at=timezone.now() - timedelta(days=31))
    assert decisions(['root'])['root'].is_cold
    metadata = cached_lifecycle_metadata(['root'])
    metadata['root']['created_at'] = timezone.now()
    with CaptureQueriesContext(connection) as queries:
        assert decisions(['root'])['root'].is_cold
    assert len(queries) == 0
