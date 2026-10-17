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
from twicc.core.content_storage.lifecycle import active_session_ids, update_lifecycle
from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
from twicc.core.models import Session, SessionItem

content_row_factory = content_storage_helpers.content_row_factory
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def session_cache():
    cache = MaintenanceSessionCache()
    yield cache
    cache.close()


def payload_selects(queries):
    return [query['sql'] for query in queries if 'FROM "core_sessionitemcontent"' in query['sql']
            and '"core_sessionitemcontent"."payload"' in query['sql'].split(' FROM ')[0]
            and 'LENGTH(' not in query['sql'].split(' FROM ')[0]]


@pytest.mark.parametrize('representation', ['plain', 'zstd', 'no_gain'])
def test_shared_owners_use_batched_readiness_and_cached_lifecycle(content_row_factory, representation, monkeypatch, session_cache):
    owners = [cold('first'), cold('second')]
    for index in range(100):
        row = content_row_factory(f'{index} content ' * 100, representation == 'zstd',
                                  session_ids=[owner.id for owner in owners])
        if representation == 'no_gain':
            row.compression_attempt_version = policy()
            row.compression_no_gain = True
            row.save(update_fields=['compression_attempt_version', 'compression_no_gain'])
    transcode = maintenance.transcode_content
    reads = []

    def record_read(row, *args, **kwargs):
        reads.append(row.id)
        return transcode(row, *args, **kwargs)

    monkeypatch.setattr(maintenance, 'transcode_content', record_read)
    for iteration in range(2):
        reads.clear()
        with CaptureQueriesContext(connection) as queries:
            _, entries, done, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
        assert done
        ids = [entry.candidate.content_id for entry in entries]
        assert len(ids) == len(set(ids)) == (100 if representation == 'plain' else 0)
        owner_reads = [query['sql'] for query in queries if 'FROM "core_session"' in query['sql']]
        assert len(owner_reads) == (1 if representation == 'plain' and iteration == 0 else 0)
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


def test_preparation_rechecks_active_and_committed_resume(content_row_factory, session_cache):
    root = cold('root')
    content_row_factory('content ' * 100, False, session_ids=[root.id])
    assert len(maintenance.prepare_compression_batch(0, session_cache=session_cache)[1]) == 1
    token = active_session_ids.set(frozenset({root.id}))
    try:
        assert maintenance.prepare_compression_batch(0, session_cache=session_cache)[1] == ()
    finally:
        active_session_ids.reset(token)
    _, entries, _, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    update_lifecycle(Session.objects.filter(pk=root.id), last_started_at=timezone.now())
    assert maintenance.prepare_compression_batch(0, session_cache=session_cache)[1] == ()
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset(), session_cache=session_cache).changed == 0


def test_preparation_rechecks_age_between_pages(content_row_factory, monkeypatch, session_cache):
    now = timezone.now()
    root = cold('root')
    update_lifecycle(Session.objects.filter(pk=root.id), created_at=now - timedelta(days=15) + timedelta(seconds=1))
    first = content_row_factory('first ' * 100, False, session_ids=[root.id])
    second = content_row_factory('second ' * 100, False, session_ids=[root.id])
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now)
    cursor, entries, done, _ = maintenance.prepare_compression_batch(0, row_budget=1, session_cache=session_cache)
    assert cursor == first.id and entries == () and not done
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now + timedelta(seconds=2))
    _, entries, done, _ = maintenance.prepare_compression_batch(cursor, row_budget=1, session_cache=session_cache)
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


def test_lifecycle_invalidation_refreshes_cached_preparation(content_row_factory, session_cache):
    root = cold('root')
    content_row_factory('content ' * 100, False, session_ids=[root.id])
    assert len(maintenance.prepare_compression_batch(0, session_cache=session_cache)[1]) == 1
    update_lifecycle(Session.objects.filter(pk=root.id), last_stopped_at=timezone.now())
    assert maintenance.prepare_compression_batch(0, session_cache=session_cache)[1] == ()


@pytest.mark.parametrize('ownership', ['duplicate', 'missing'])
def test_invalid_ownership_cannot_prepare_compression(content_row_factory, ownership, session_cache):
    root = cold('root')
    row = content_row_factory('content ' * 100, False, session_ids=[root.id])
    row.session_ids = [root.id, root.id if ownership == 'duplicate' else 'missing']
    row.save(update_fields=['session_ids'])
    assert maintenance.prepare_compression_batch(0, session_cache=session_cache)[1] == ()


def owner_selects(queries):
    return [query['sql'] for query in queries if 'FROM "core_session"' in query['sql']]


@pytest.mark.parametrize('ownership', ['shared', 'overlapping', 'distinct'])
def test_publication_shared_owners_reuses_metadata_and_content_rows(content_row_factory, ownership, session_cache):
    owners = [cold(f'owner-{index:03}') for index in range(100 if ownership == 'distinct' else 3)]
    ids = []
    for index in range(100):
        group = ([owners[index].id] if ownership == 'distinct' else
                 [owners[0].id, owners[1 + (index % 2 if ownership == 'overlapping' else 0)].id])
        ids.append(content_row_factory(f'{index} content ' * 100, False, session_ids=group).id)
    _, entries, _, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    # Roll back the reference publication so both captures observe identical inputs and cache state.
    with transaction.atomic():
        with CaptureQueriesContext(connection) as one_queries:
            assert maintenance.publish_prepared_batch(entries[:1], frozenset(), session_cache=session_cache).changed == 1
        transaction.set_rollback(True)
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        published = maintenance.publish_prepared_batch(entries, frozenset(), session_cache=session_cache).changed
    assert published == 100
    assert payload_selects(queries) == []
    metadata_fetches = [query['sql'] for query in queries if 'FROM "core_sessionitemcontent"' in query['sql']]
    assert len(metadata_fetches) == 1
    assert f'IN ({", ".join(str(identity) for identity in ids)})' in metadata_fetches[0]
    assert owner_selects(queries) == owner_selects(one_queries) == []
    if ownership == 'shared':
        assert owner_selects(queries) == owner_selects(one_queries)
    else:
        # All selected owners fit in one metadata page, regardless of content count.
        assert len(owner_selects(queries)) == len(owner_selects(one_queries))
    selects = [index for index, query in enumerate(queries) if query['sql'].startswith('SELECT')]
    assert selects[0] == 0
    assert not any('SET "id" = "id"' in query['sql'] for query in queries)


def test_empty_publication_performs_no_queries(session_cache):
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        assert maintenance.publish_prepared_batch((), frozenset(), session_cache=session_cache).changed == 0
    assert list(queries) == []


@pytest.mark.parametrize('change', ['active', 'resume', 'hot_owner', 'raw_size', 'codec', 'dictionary',
                                   'identity', 'policy', 'deleted'])
def test_publication_rechecks_authoritative_observations(content_row_factory, change, session_cache):
    owner = cold('owner')
    child = cold('child')
    child.type = 'subagent'
    child.parent_session = owner
    child.save(update_fields=['type', 'parent_session'])
    row = content_row_factory('content ' * 100, False, session_ids=[child.id])
    _, entries, _, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    active = frozenset()
    if change == 'active':
        active = frozenset({child.id})
    elif change == 'resume':
        update_lifecycle(Session.objects.filter(pk=child.id), last_started_at=timezone.now())
    elif change == 'hot_owner':
        row.session_ids = [child.id, session('hot').id]
        row.save(update_fields=['session_ids'])
    elif change == 'policy':
        row.compression_attempt_version = 'concurrent-policy'
        row.save(update_fields=['compression_attempt_version'])
    elif change == 'deleted':
        row.delete()
    else:
        field, value = {'raw_size': ('raw_size', row.raw_size + 1), 'codec': ('codec', 'zstd'),
                        'dictionary': ('dictionary_id', content_storage_helpers._public_dictionary('codex').id),
                        'identity': ('sha256', b'x' * 32)}[change]
        type(row).objects.filter(pk=row.pk).update(**{field: value})
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, active, session_cache=session_cache).changed == 0


def test_invalid_candidate_does_not_discard_valid_peers(content_row_factory, session_cache):
    owner = cold('owner')
    rows = [content_row_factory(f'{index} content ' * 100, False, session_ids=[owner.id]) for index in range(3)]
    _, entries, _, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    type(rows[1]).objects.filter(pk=rows[1].id).update(sha256=b'x' * 32)
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset(), session_cache=session_cache).changed == 2
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['zstd', 'plain', 'zstd']


def test_publication_rollback_discards_context_readiness(content_row_factory, session_cache):
    owner = cold('owner')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    _, entries, _, _ = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset(), session_cache=session_cache).changed == 1
        transaction.set_rollback(True)
    row.refresh_from_db()
    assert row.codec == 'plain'
    update_lifecycle(Session.objects.filter(pk=owner.id), last_started_at=timezone.now())
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset(), session_cache=session_cache).changed == 0
    row.refresh_from_db()
    assert row.codec == 'plain'


def test_only_candidates_load_payloads_and_publish_metadata(content_row_factory, monkeypatch):
    owner = cold('owner')
    for index in range(97):
        content_row_factory(f'conformant {index} ' * 100, True, session_ids=['never-loaded'])
    candidates = [content_row_factory(f'candidate {index} ' * 100, False, session_ids=[owner.id])
                  for index in range(3)]
    read_ids = []
    transcode = maintenance.transcode_content

    def record_payload(row, *args, **kwargs):
        read_ids.append(row.id)
        return transcode(row, *args, **kwargs)

    monkeypatch.setattr(maintenance, 'transcode_content', record_payload)
    cache = MaintenanceSessionCache()
    try:
        with CaptureQueriesContext(connection) as preparation:
            page = maintenance.prepare_compression_batch(0, session_cache=cache)
        assert page.done and page.counts.scanned == 100
        assert page.counts.already_compressed == 97 and page.counts.prepared == 3
        assert read_ids == [row.id for row in candidates]
        assert len(payload_selects(preparation)) == 1
        assert f'IN ({", ".join(str(row.id) for row in candidates)})' in payload_selects(preparation)[0]
        assert cache.statistics['ids_loaded'] == 1
        candidates[-1].session_ids = [owner.id, session('hot').id]
        candidates[-1].save(update_fields=['session_ids'])
        with CaptureQueriesContext(connection) as publication:
            result = maintenance._publish(page.entries, frozenset(), session_cache=cache)
        assert result.changed == 2 and result.stale == 1
        assert payload_selects(publication) == []
        metadata_selects = [query['sql'] for query in publication
                            if 'FROM "core_sessionitemcontent"' in query['sql']]
        assert len(metadata_selects) == 1
        assert f'IN ({", ".join(str(row.id) for row in candidates)})' in metadata_selects[0]
        assert len(owner_selects(publication)) == 1
        assert "'hot'" in owner_selects(publication)[0] and "'owner'" not in owner_selects(publication)[0]
    finally:
        cache.close()


@pytest.mark.parametrize('representation', ['zstd', 'no_gain'])
def test_conformant_page_needs_no_owner_payload_or_publication_reads(
    content_row_factory, session_cache, representation,
):
    row = content_row_factory('conformant ' * 100, representation == 'zstd', session_ids=['unresolved-owner'])
    if representation == 'no_gain':
        row.compression_attempt_version = policy()
        row.compression_no_gain = True
        row.save(update_fields=['compression_attempt_version', 'compression_no_gain'])
    with CaptureQueriesContext(connection) as queries:
        page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    assert page.done and page.entries == ()
    assert page.counts.scanned == 1
    assert page.counts.already_compressed == (representation == 'zstd')
    assert page.counts.no_gain_skips == (representation == 'no_gain')
    assert page.counts.payload_bytes_loaded == 0
    assert payload_selects(queries) == owner_selects(queries) == []
    with CaptureQueriesContext(connection) as publication:
        assert maintenance._publish(page.entries, frozenset(), session_cache=session_cache) == maintenance.PublicationResult()
    assert list(publication) == []


def test_concurrent_conformant_publication_does_not_update(content_row_factory, session_cache):
    owner = cold('owner')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    first = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert first.changed == 1
    assert first.bytes_saved == row.raw_size - len(page.entries[0].candidate.encoded.payload)
    with CaptureQueriesContext(connection) as queries:
        second = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert second == maintenance.PublicationResult(already_conformant=1)
    assert payload_selects(queries) == owner_selects(queries) == []
    assert not any(query['sql'].startswith('UPDATE') for query in queries)


@pytest.mark.parametrize('previous_no_gain', [False, True])
def test_no_gain_publication_updates_only_attempt_metadata(content_row_factory, session_cache, previous_no_gain):
    owner = cold('owner')
    row = content_row_factory('x', False, session_ids=[owner.id])
    if previous_no_gain:
        row.compression_no_gain = True
        row.compression_attempt_version = 'old-policy'
        row.save(update_fields=['compression_no_gain', 'compression_attempt_version'])
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    assert page.counts.payload_bytes_loaded == page.counts.candidate_raw_bytes == page.counts.candidate_stored_bytes == 1
    with CaptureQueriesContext(connection) as queries:
        result = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert result == maintenance.PublicationResult(no_gain_recorded=1)
    updates = [query['sql'] for query in queries if query['sql'].startswith('UPDATE')]
    assert len(updates) == 1
    assert '"payload"' not in updates[0] and '"session_ids"' not in updates[0]
    assert ('"compression_no_gain"' in updates[0]) is not previous_no_gain
    row.refresh_from_db()
    assert row.codec == 'plain' and bytes(row.payload) == b'x'
    assert row.compression_no_gain and row.compression_attempt_version == policy()
    assert maintenance.prepare_compression_batch(0, session_cache=session_cache).counts.no_gain_skips == 1
    with CaptureQueriesContext(connection) as repeated:
        result = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert result == maintenance.PublicationResult(already_conformant=1)
    assert not any(query['sql'].startswith('UPDATE') for query in repeated)


def test_new_cold_owner_is_preserved_during_publication(content_row_factory, session_cache):
    owner = cold('owner')
    other = cold('other')
    row = content_row_factory('content ' * 100, False, session_ids=[owner.id])
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    row.session_ids = [other.id, owner.id]
    row.save(update_fields=['session_ids'])
    with CaptureQueriesContext(connection) as queries:
        result = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert result.changed == 1
    row.refresh_from_db()
    assert row.session_ids == [other.id, owner.id]
    assert len(owner_selects(queries)) == 1
    assert "'other'" in owner_selects(queries)[0] and "'owner'" not in owner_selects(queries)[0]
    assert payload_selects(queries) == []


def test_committed_invalidations_reload_only_changed_owner(content_row_factory, session_cache):
    owners = [cold(f'owner-{index}') for index in range(3)]
    rows = [content_row_factory(f'content {index} ' * 100, False, session_ids=[owner.id])
            for index, owner in enumerate(owners)]
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    update_lifecycle(Session.objects.filter(pk=owners[1].id), last_started_at=timezone.now())
    with CaptureQueriesContext(connection) as queries:
        result = maintenance._publish(page.entries, frozenset(), session_cache=session_cache)
    assert result.changed == 2 and result.stale == 1
    assert len(owner_selects(queries)) == 1
    assert "'owner-1'" in owner_selects(queries)[0]
    assert "'owner-0'" not in owner_selects(queries)[0] and "'owner-2'" not in owner_selects(queries)[0]
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['zstd', 'plain', 'zstd']


@pytest.mark.parametrize('change', ['compress', 'restore', 'corrupt'])
def test_payload_selection_uses_coherent_representation(
    content_row_factory, session_cache, monkeypatch, change,
):
    from twicc.core.content_storage.codec import ContentIntegrityError, transcode_content
    from twicc.core.models import SessionItemContent

    owner = cold('owner')
    # A different provider dictionary makes the compressed input a recompression candidate.
    stale = content_row_factory('changing content ' * 100, change == 'restore', session_ids=[owner.id])
    peer = content_row_factory('unaffected content ' * 100, False, session_ids=[owner.id])
    if change == 'restore':
        dictionary = content_storage_helpers._public_dictionary('claude_code')
        encoded, _ = transcode_content(stale, dictionary.id)
        SessionItemContent.objects.filter(pk=stale.id).update(
            payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id,
        )
    decisions = session_cache.decisions

    def concurrent_change(*args, **kwargs):
        result = decisions(*args, **kwargs)
        stale.refresh_from_db()
        if change == 'corrupt':
            SessionItemContent.objects.filter(pk=stale.id).update(payload=b'corrupt')
        else:
            dictionary_id = None if change == 'restore' else content_storage_helpers._public_dictionary('codex').id
            encoded, _ = transcode_content(stale, dictionary_id)
            SessionItemContent.objects.filter(pk=stale.id).update(
                payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id,
            )
        return result

    monkeypatch.setattr(session_cache, 'decisions', concurrent_change)
    if change == 'corrupt':
        with pytest.raises(ContentIntegrityError):
            maintenance.prepare_compression_batch(0, session_cache=session_cache)
        return
    page = maintenance.prepare_compression_batch(0, session_cache=session_cache)
    assert page.done and [entry.candidate.content_id for entry in page.entries] == [peer.id]
    monkeypatch.setattr(session_cache, 'decisions', decisions)
    assert maintenance._publish(page.entries, frozenset(), session_cache=session_cache).changed == 1
    peer.refresh_from_db()
    assert peer.codec == 'zstd'


def test_root_selection_metadata_is_loaded_once_per_pass(content_row_factory, session_cache):
    roots = [session(f'root-{index}', content_storage_version=1, storage_incarnation=f'inc-{index}')
             for index in range(2)]
    for root in roots:
        row = content_row_factory(f'{root.id} text ' * 1000, True, session_ids=[root.id])
        SessionItem.objects.create(session=root, line_num=1, content='', current_content=row)
    with CaptureQueriesContext(connection) as selection:
        ids = maintenance.periodic_hot_root_page(None, timezone.now())
    assert len(selection) == 1
    assert selection[0]['sql'].split(' FROM ')[0] == 'SELECT "core_session"."id" AS "id"'
    with CaptureQueriesContext(connection) as recovery:
        assert maintenance.recovery_promotion_targets(ids, session_cache) == set(ids)
    lifecycle = [sql for sql in owner_selects(recovery) if '"created_at"' in sql.split(' FROM ')[0]]
    assert len(lifecycle) == 1
    assert all(f"'{root.id}'" in lifecycle[0] for root in roots)
    with CaptureQueriesContext(connection) as publication:
        for root in roots:
            snapshot = maintenance._snapshot(root.id, frozenset(), recovery=True, session_cache=session_cache)
            batch = maintenance.prepare_promotion_batch(snapshot, maintenance.START_CURSOR, session_cache=session_cache)
            assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True,
                                                     session_cache=session_cache).done
        maintenance.prepare_compression_batch(0, session_cache=session_cache)
    assert not [sql for sql in owner_selects(publication) if '"created_at"' in sql.split(' FROM ')[0]]
