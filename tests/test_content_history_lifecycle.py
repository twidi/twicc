"""Destructive history boundaries retain content and reconcile exact owners."""

from tests.tree_aggregate_helpers import begin_history_replacement

from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from tests.content_storage_helpers import _public_dictionary, read_current
from twicc.core.content_storage.ingestion import initialize_ingestion_session, insert_ingested_items
from twicc.core.content_storage.state import snapshot_root_storage, verify_plaintext_root
from twicc.core.models import ContentDictionary, Project, Session, SessionItem, SessionItemContent, Share
from twicc.providers.codex import rollout_migration
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload

pytestmark = pytest.mark.django_db(transaction=True)


def session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='lifecycle-project')
    return Session.objects.create(id=name, project=project, provider='codex', file_path=str(uuid4()), **kwargs)


def ingest(owner, text, original=None):
    with transaction.atomic():
        initialize_ingestion_session(owner)
        insert_ingested_items(owner, [(SessionItem(session=owner, line_num=1, content=text),
                                       text if original is None else original)])
    return SessionItem.objects.get(session=owner)


def job(owner):
    return rollout_migration.ReplaceCodexHistoryJob('codex', owner.id, [(1, 'source'), (2, 'new source')],
                                                   200, 2, 1.0, None)


def test_replacement_interruptions_reconcile_both_slots_and_retry():
    owner = session('replace', content_storage_version=1, plaintext_verified_epoch=1)
    other = session('survivor')
    old = ingest(owner, 'enriched', 'source')
    ingest(other, 'source')
    current_id, original_id = old.current_content_id, old.original_content_id
    with transaction.atomic():
        snapshot = snapshot_root_storage(owner.id)
    replacement = job(owner)
    anchor = {'timestamp': '2026-08-31T10:00:00+00:00'}
    share = Share.objects.create(kind='session', session=owner, options={
        'mode': 'snapshot', 'frozen_at_line': 1, rollout_migration.SNAPSHOT_ANCHOR_KEY: anchor,
    })
    begin_history_replacement(replacement)
    owner.refresh_from_db()
    assert owner.last_offset == 0
    assert owner.history_epoch == 1
    assert owner.content_storage_version is None
    assert owner.plaintext_verified_epoch is None
    assert owner.content_state_epoch > snapshot.epoch
    assert SessionItemContent.objects.get(pk=current_id).session_ids == []
    assert SessionItemContent.objects.get(pk=original_id).session_ids == [other.id]
    rollout_migration._insert_replace_codex_history_chunk(owner.id, replacement.items[:1])
    assert read_current(SessionItem.objects.get(session=owner)) == 'source'
    owner.refresh_from_db()
    assert owner.last_offset == 0
    rollout_migration._apply_replace_codex_history_job(replacement)
    owner.refresh_from_db()
    assert owner.last_offset == 200
    assert owner.history_epoch == 2
    assert owner.compute_version is None
    for item in SessionItem.objects.filter(session=owner):
        assert item.content == ''
        assert item.current_content_id == item.original_content_id
        assert owner.id in item.current_content.session_ids
    assert SessionItemContent.objects.get(pk=current_id).session_ids == []
    assert SessionItemContent.objects.get(pk=original_id).session_ids == sorted([owner.id, other.id])
    share.refresh_from_db()
    assert share.options[rollout_migration.SNAPSHOT_ANCHOR_KEY] == anchor


def test_replacement_begin_rollback_restores_history_and_memberships(monkeypatch):
    owner = session('rollback')
    row = ingest(owner, 'current', 'original')
    from twicc.providers import live_aggregates

    def fail(*args, **kwargs):
        raise RuntimeError('interrupted')

    with monkeypatch.context() as patch:
        patch.setattr(live_aggregates, 'apply_contribution_changes', fail)
        with pytest.raises(RuntimeError):
            begin_history_replacement(job(owner))
    assert SessionItem.objects.filter(pk=row.pk).exists()
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == [owner.id]
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == [owner.id]
    owner.refresh_from_db()
    assert owner.history_epoch == 0
    begin_history_replacement(job(owner))
    assert not SessionItem.objects.filter(session=owner).exists()
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == []


def test_guardian_nested_cascade_preserves_shared_owner_and_invalidates_surviving_root():
    root = session('root')
    child = session('child', type='subagent', parent_session=root)
    leaf = session('leaf', type='subagent', parent_session=child)
    independent = session('independent')
    row = ingest(child, 'child current', 'shared')
    ingest(leaf, 'shared')
    ingest(independent, 'shared')
    with transaction.atomic():
        snapshot = snapshot_root_storage(root.id)
        other_snapshot = snapshot_root_storage(independent.id)
    with CaptureQueriesContext(connection) as queries:
        deleted = _apply_delete_sessions_payload(DeleteSessionsPayload('codex', [child.id]))
    assert deleted == ['child', 'leaf']
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == []
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == [independent.id]
    root.refresh_from_db()
    independent.refresh_from_db()
    assert root.content_state_epoch > snapshot.epoch
    assert independent.content_state_epoch == other_snapshot.epoch
    content_reads = [q['sql'] for q in queries if q['sql'].startswith('SELECT')
                     and 'core_sessionitemcontent' in q['sql']]
    assert content_reads
    assert all('"payload"' not in sql for sql in content_reads)


def test_delete_rollback_restores_descendant_ownership(monkeypatch):
    root = session('rollback-root')
    child = session('rollback-child', type='subagent', parent_session=root)
    row = ingest(child, 'current', 'original')
    from twicc.providers import live_aggregates

    def fail(*args, **kwargs):
        raise RuntimeError('interrupted')

    with monkeypatch.context() as patch:
        patch.setattr(live_aggregates, 'apply_contribution_changes', fail)
        with pytest.raises(RuntimeError):
            _apply_delete_sessions_payload(DeleteSessionsPayload('codex', [root.id]))
    assert SessionItem.objects.filter(pk=row.pk).exists()
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == [child.id]
    _apply_delete_sessions_payload(DeleteSessionsPayload('codex', [root.id]))
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == []


def test_reset_recreation_rejects_old_compute_and_promotion(monkeypatch):
    from twicc.core.management.commands import sync

    dictionary = _public_dictionary('codex')
    owner = session('recreated')
    row = ingest(owner, 'current', 'original')
    with transaction.atomic():
        snapshot = snapshot_root_storage(owner.id)
    observation = {
        'session_id': owner.id, 'observed_incarnation': owner.storage_incarnation,
        'observed_epoch': owner.history_epoch, 'observed_upper_line': owner.last_line,
        'observed_last_offset': owner.last_offset, 'session_fields': {'compute_version': 123}, 'history_facts': [],
    }
    monkeypatch.setattr(sync, 'sync_all_with_progress', lambda *args, **kwargs: None)
    sync.Command().handle(reset=True)
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == []
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == []
    assert ContentDictionary.objects.filter(pk=dictionary.id).exists()
    recreated = session(owner.id)
    ingest(recreated, 'new current')
    assert recreated.storage_incarnation != snapshot.incarnation
    with transaction.atomic():
        assert verify_plaintext_root(snapshot) is False
    assert BaseSessionCompute.apply_session_complete(observation).outcome == 'superseded'


@pytest.mark.parametrize('fail_sync', [False, True])
def test_sync_command_routes_queue_and_always_stops_writer(monkeypatch, fail_sync):
    from twicc.core.management.commands import sync
    from twicc.providers import db_writer

    events = []
    queue = object()
    command = sync.Command()

    def start():
        events.append('start')

    async def ready():
        events.append('ready')

    async def stop():
        events.append('stop')

    def synchronize(actual_queue, *, stream):
        assert actual_queue is queue
        assert stream is command.stdout
        events.append('sync')
        if fail_sync:
            raise RuntimeError('sync interrupted')

    monkeypatch.setattr(db_writer, 'start_db_writer', start)
    monkeypatch.setattr(db_writer, 'stop_db_writer', stop)
    monkeypatch.setattr(db_writer, 'wait_db_writer_ready', ready)
    monkeypatch.setattr(db_writer, 'get_thread_queue', lambda: queue)
    monkeypatch.setattr(sync, 'sync_all_with_progress', synchronize)
    if fail_sync:
        with pytest.raises(RuntimeError, match='sync interrupted'):
            command.handle(reset=False)
    else:
        command.handle(reset=False)
    assert events == ['start', 'ready', 'sync', 'stop']


def test_reset_rollback_restores_project_history_and_membership(monkeypatch):
    from django.db.models.query import QuerySet
    from twicc.core.management.commands import sync

    owner = session('reset-rollback')
    row = ingest(owner, 'current', 'original')
    delete = QuerySet.delete

    def fail_project_delete(queryset):
        if queryset.model is Project:
            raise RuntimeError('reset interrupted')
        return delete(queryset)

    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, 'delete', fail_project_delete)
        with pytest.raises(RuntimeError, match='reset interrupted'):
            sync.Command().handle(reset=True)
    assert SessionItem.objects.filter(pk=row.pk).exists()
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == [owner.id]
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == [owner.id]
    monkeypatch.setattr(sync, 'sync_all_with_progress', lambda *args, **kwargs: None)
    sync.Command().handle(reset=True)
    assert SessionItemContent.objects.get(pk=row.original_content_id).session_ids == []


def test_replacement_chunk_rollback_retries_with_known_originals(monkeypatch):
    from twicc.core.content_storage import ingestion

    owner = session('chunk-rollback')
    replacement = job(owner)
    begin_history_replacement(replacement)

    def fail(*args, **kwargs):
        raise RuntimeError('chunk interrupted')

    with monkeypatch.context() as patch:
        patch.setattr(ingestion, 'bind_persisted_items', fail)
        with pytest.raises(RuntimeError, match='chunk interrupted'):
            rollout_migration._insert_replace_codex_history_chunk(owner.id, replacement.items)
    assert not SessionItem.objects.filter(session=owner).exists()
    assert not SessionItemContent.objects.exists()
    owner.refresh_from_db()
    assert owner.last_offset == 0
    rollout_migration._insert_replace_codex_history_chunk(owner.id, replacement.items)
    assert SessionItem.objects.filter(session=owner).count() == 2
    assert all(item.current_content_id == item.original_content_id
               for item in SessionItem.objects.filter(session=owner))


def test_whole_history_removal_streams_distinct_both_slot_ids(monkeypatch):
    from twicc.core.content_storage import references

    owner = session('bounded')
    with transaction.atomic():
        initialize_ingestion_session(owner)
        insert_ingested_items(owner, [(SessionItem(session=owner, line_num=line, content=f'current {line}'),
                                       f'original {line}') for line in range(1, 8)])
    monkeypatch.setattr(references, 'MEMBERSHIP_BATCH_SIZE', 3)
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        references.remove_history_memberships(owner.id)
        SessionItem.objects.filter(session=owner).delete()
    assert all(row.session_ids == [] for row in SessionItemContent.objects.only('session_ids'))
    item_reads = [q['sql'] for q in queries if q['sql'].startswith('SELECT') and 'core_sessionitem"' in q['sql']]
    assert len(item_reads) == 1
    assert 'UNION' in item_reads[0]
    assert '"content"' not in item_reads[0]
    content_reads = [q['sql'] for q in queries if q['sql'].startswith('SELECT')
                     and 'core_sessionitemcontent' in q['sql']]
    assert len(content_reads) == 5
    assert all('"payload"' not in sql for sql in content_reads)


def test_deleted_orchestration_parent_preserves_linked_independent_owners():
    root = session('orchestration-root')
    independent = session('linked-independent', spawned_by=root, spawn_root=root)
    row = ingest(root, 'shared orchestration content')
    ingest(independent, 'shared orchestration content')
    assert _apply_delete_sessions_payload(DeleteSessionsPayload('codex', [root.id])) == [root.id]
    independent.refresh_from_db()
    assert independent.spawned_by_id is None and independent.spawn_root_id is None
    assert SessionItemContent.objects.get(pk=row.current_content_id).session_ids == [independent.id]
