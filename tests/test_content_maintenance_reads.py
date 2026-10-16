"""Maintenance reads scale with selected owners and required payloads."""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests import content_storage_helpers
from tests.test_content_maintenance import cold, policy
from tests.test_content_root_state import session
from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage.cold_cache import cached_root_decisions
from twicc.core.content_storage.lifecycle import active_session_ids, update_lifecycle
from twicc.core.models import Session, SessionItem

content_row_factory = content_storage_helpers.content_row_factory
pytestmark = pytest.mark.django_db(transaction=True)


def payload_selects(queries):
    return [query['sql'] for query in queries if 'FROM "core_sessionitemcontent"' in query['sql']
            and '"core_sessionitemcontent"."payload"' in query['sql'].split(' FROM ')[0]
            and 'LENGTH(' not in query['sql'].split(' FROM ')[0]]


@pytest.mark.parametrize('representation', ['plain', 'zstd', 'no_gain'])
def test_shared_owners_use_batched_readiness_and_cached_lifecycle(content_row_factory, representation, monkeypatch):
    owners = [cold('first'), cold('second')]
    for index in range(100):
        row = content_row_factory(f'{index} content ' * 100, representation == 'zstd',
                                  session_ids=[owner.id for owner in owners])
        if representation == 'no_gain':
            row.compression_attempt_version = policy()
            row.compression_no_gain = True
            row.save(update_fields=['compression_attempt_version', 'compression_no_gain'])
    cached_root_decisions([owner.id for owner in owners], timezone.now(), frozenset())
    transcode = maintenance.transcode_content
    reads = []

    def record_read(row, *args, **kwargs):
        reads.append(row.id)
        return transcode(row, *args, **kwargs)

    monkeypatch.setattr(maintenance, 'transcode_content', record_read)
    for _ in range(2):
        reads.clear()
        with CaptureQueriesContext(connection) as queries:
            _, entries, done = maintenance.prepare_compression_batch(0)
        assert done
        ids = [entry.candidate.content_id for entry in entries]
        assert len(ids) == len(set(ids)) == (100 if representation == 'plain' else 0)
        owner_reads = [query['sql'] for query in queries if 'FROM "core_session"' in query['sql']]
        assert len(owner_reads) <= 2
        assert not any('"last_updated_at"' in sql.split(' FROM ')[0] for sql in owner_reads)
        assert len(payload_selects(queries)) == (1 if representation == 'plain' else 0)
        assert reads == ids


def test_plain_promotion_does_not_select_payload(content_row_factory):
    root = session('root', content_storage_version=1, storage_incarnation='root', content_state_epoch=0)
    row = content_row_factory('plain content', False, session_ids=[root.id])
    SessionItem.objects.create(session=root, line_num=1, content='', current_content=row, original_content=row)
    snapshot = maintenance._snapshot(root.id, frozenset())
    with CaptureQueriesContext(connection) as queries:
        batch = maintenance.prepare_promotion_batch(snapshot, maintenance.START_CURSOR)
    assert batch.verified and batch.cursor.done
    assert batch.entries == ()
    assert payload_selects(queries) == []


def test_identical_references_load_and_prepare_payload_once(content_row_factory):
    root = session('root', content_storage_version=1, storage_incarnation='root', content_state_epoch=0)
    row = content_row_factory('compressed content ' * 100, True, session_ids=[root.id])
    SessionItem.objects.create(session=root, line_num=1, content='', current_content=row, original_content=row)
    with CaptureQueriesContext(connection) as queries:
        batch = maintenance.prepare_promotion_batch(maintenance._snapshot(root.id, frozenset()),
                                                     maintenance.START_CURSOR)
    assert batch.verified and len(batch.entries) == 1
    assert len(payload_selects(queries)) == 1


def test_preparation_rechecks_active_and_committed_resume(content_row_factory):
    root = cold('root')
    child = session('child', type='subagent', parent_session=root)
    content_row_factory('content ' * 100, False, session_ids=[root.id])
    assert len(maintenance.prepare_compression_batch(0)[1]) == 1
    token = active_session_ids.set(frozenset({child.id}))
    try:
        assert maintenance.prepare_compression_batch(0)[1] == ()
    finally:
        active_session_ids.reset(token)
    _, entries, _ = maintenance.prepare_compression_batch(0)
    update_lifecycle(Session.objects.filter(pk=root.id), last_started_at=timezone.now())
    assert maintenance.prepare_compression_batch(0)[1] == ()
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 0


def test_preparation_rechecks_age_between_pages(content_row_factory, monkeypatch):
    now = timezone.now()
    root = cold('root')
    update_lifecycle(Session.objects.filter(pk=root.id), created_at=now - timedelta(days=15) + timedelta(seconds=1))
    first = content_row_factory('first ' * 100, False, session_ids=[root.id])
    second = content_row_factory('second ' * 100, False, session_ids=[root.id])
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now)
    cursor, entries, done = maintenance.prepare_compression_batch(0, row_budget=1)
    assert cursor == first.id and entries == () and not done
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now + timedelta(seconds=2))
    _, entries, done = maintenance.prepare_compression_batch(cursor, row_budget=1)
    assert done and [entry.candidate.content_id for entry in entries] == [second.id]


@pytest.mark.parametrize('invalid', ['dictionary', 'missing_current'])
def test_promotion_rejects_invalid_plain_metadata_without_payload_reads(content_row_factory, invalid):
    root = session('root', content_storage_version=1, storage_incarnation='root', content_state_epoch=0)
    row = content_row_factory('plain content', False, session_ids=[root.id])
    if invalid == 'dictionary':
        row.dictionary = content_storage_helpers._public_dictionary('codex')
        row.save(update_fields=['dictionary'])
    SessionItem.objects.create(session=root, line_num=1, content='',
                               current_content=None if invalid == 'missing_current' else row)
    snapshot = maintenance._snapshot(root.id, frozenset())
    with CaptureQueriesContext(connection) as queries:
        batch = maintenance.prepare_promotion_batch(snapshot, maintenance.START_CURSOR)
    assert not batch.verified and batch.entries == ()
    assert payload_selects(queries) == []


def test_readiness_changes_are_not_cached_between_preparations(content_row_factory):
    root = cold('root')
    content_row_factory('content ' * 100, False, session_ids=[root.id])
    assert len(maintenance.prepare_compression_batch(0)[1]) == 1
    Session.objects.filter(pk=root.id).update(compute_version=0)
    assert maintenance.prepare_compression_batch(0)[1] == ()


@pytest.mark.parametrize('ownership', ['duplicate', 'missing'])
def test_invalid_ownership_cannot_prepare_compression(content_row_factory, ownership):
    root = cold('root')
    row = content_row_factory('content ' * 100, False, session_ids=[root.id])
    row.session_ids = [root.id, root.id if ownership == 'duplicate' else 'missing']
    row.save(update_fields=['session_ids'])
    assert maintenance.prepare_compression_batch(0)[1] == ()


def owner_selects(queries):
    return [query['sql'] for query in queries if 'FROM "core_session"' in query['sql']]


@pytest.mark.parametrize('ownership', ['shared', 'overlapping', 'distinct'])
def test_publication_shared_owners_reuses_metadata_and_content_rows(content_row_factory, ownership):
    owners = [cold(f'owner-{index:03}') for index in range(100 if ownership == 'distinct' else 3)]
    ids = []
    for index in range(100):
        group = ([owners[index].id] if ownership == 'distinct' else
                 [owners[0].id, owners[1 + (index % 2 if ownership == 'overlapping' else 0)].id])
        ids.append(content_row_factory(f'{index} content ' * 100, False, session_ids=group).id)
    _, entries, _ = maintenance.prepare_compression_batch(0)
    # Roll back the reference publication so both captures observe identical inputs and cache state.
    with transaction.atomic():
        with CaptureQueriesContext(connection) as one_queries:
            assert maintenance.publish_prepared_batch(entries[:1], frozenset()) == 1
        transaction.set_rollback(True)
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        published = maintenance.publish_prepared_batch(entries, frozenset())
    assert published == 100
    full_content_fetches = payload_selects(queries)
    assert len(full_content_fetches) == 1
    assert f'IN ({", ".join(str(identity) for identity in ids)})' in full_content_fetches[0]
    if ownership == 'shared':
        assert owner_selects(queries) == owner_selects(one_queries)
    else:
        # All selected owners fit in one metadata page, regardless of content count.
        assert len(owner_selects(queries)) == len(owner_selects(one_queries))
    selects = [index for index, query in enumerate(queries) if query['sql'].startswith('SELECT')]
    guard = queries[0]['sql']
    assert guard.startswith('UPDATE "core_sessionitemcontent" SET "id" = "id" WHERE "id" = ')
    assert selects[0] > 0


def test_empty_publication_performs_no_queries():
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        assert maintenance.publish_prepared_batch((), frozenset()) == 0
    assert list(queries) == []


@pytest.mark.parametrize('change', ['active', 'resume', 'hot_owner', 'payload', 'codec', 'dictionary',
                                   'identity', 'missing_ancestry', 'deleted'])
def test_publication_rechecks_authoritative_observations(content_row_factory, change):
    owner = cold('owner')
    child = session('child', type='subagent', parent_session=owner,
                    content_storage_version=1, storage_incarnation='child')
    row = content_row_factory('content ' * 100, False, session_ids=[child.id])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    active = frozenset()
    if change == 'active':
        active = frozenset({owner.id})
    elif change == 'resume':
        update_lifecycle(Session.objects.filter(pk=owner.id), last_started_at=timezone.now())
    elif change == 'hot_owner':
        row.session_ids = [child.id, session('hot').id]
        row.save(update_fields=['session_ids'])
    elif change == 'missing_ancestry':
        child.parent_session = None
        child.save(update_fields=['parent_session'])
    elif change == 'deleted':
        row.delete()
    else:
        field, value = {'payload': ('payload', b'changed'), 'codec': ('codec', 'zstd'),
                        'dictionary': ('dictionary_id', content_storage_helpers._public_dictionary('codex').id),
                        'identity': ('sha256', b'x' * 32)}[change]
        type(row).objects.filter(pk=row.pk).update(**{field: value})
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, active) == 0


def test_invalid_candidate_does_not_discard_valid_peers(content_row_factory):
    owner = cold('owner')
    rows = [content_row_factory(f'{index} content ' * 100, False, session_ids=[owner.id]) for index in range(3)]
    _, entries, _ = maintenance.prepare_compression_batch(0)
    type(rows[1]).objects.filter(pk=rows[1].id).update(payload=b'changed')
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 2
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['zstd', 'plain', 'zstd']


def test_publication_rollback_discards_context_readiness(content_row_factory):
    owner = cold('owner')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 1
        transaction.set_rollback(True)
    row.refresh_from_db()
    assert row.codec == 'plain'
    update_lifecycle(Session.objects.filter(pk=owner.id), last_started_at=timezone.now())
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 0
    row.refresh_from_db()
    assert row.codec == 'plain'
