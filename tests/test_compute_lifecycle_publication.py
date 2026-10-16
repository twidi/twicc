"""Historical stop projection is grouped, bounded, and guarded before mutation."""
import asyncio
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.test_content_extraction import make_session, message, _advance_storage_operation
from tests.test_content_storage_admission import writer_state  # noqa: F401
from twicc.core.content_storage import extraction, lifecycle
from twicc.core.models import Session
from twicc.providers.compute_base import BaseSessionCompute

pytestmark = pytest.mark.django_db(transaction=True)


def stops(child, *times):
    return [{'agent_session_id': child.id, 'stopped_at': value.isoformat()} for value in times]


def agent_updates(queries):
    return [query for query in queries if 'UPDATE "core_session" SET "last_stopped_at"' in query['sql']]


def test_repeated_stops_publish_latest_once():
    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root, compute_version=123)
    latest = timezone.now()
    with CaptureQueriesContext(connection) as queries:
        prepared = extraction.prepare_compute_result(message(root, agent_stopped=stops(
            child, latest - timedelta(seconds=2), latest, latest - timedelta(seconds=1))))
        result = BaseSessionCompute._apply_session_complete_final(prepared)
    child.refresh_from_db()
    assert result.outcome == 'applied'
    assert child.last_stopped_at == child.last_updated_at == latest
    assert len(agent_updates(queries)) == 1


def test_equal_stops_do_not_write_or_invalidate(monkeypatch):
    from twicc.providers.compute_lifecycle import normalize_agent_stops, publish_agent_stops

    root = make_session(content_state_epoch=4)
    now = timezone.now()
    child = make_session(id='child', type='subagent', parent_session=root,
                         last_stopped_at=now, last_updated_at=now)
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kw: requests.append(target))
    monkeypatch.setattr('twicc.core.content_storage.cold_cache.schedule_cold_invalidation',
                        lambda *args, **kwargs: pytest.fail('No-op invalidates cache'))
    with CaptureQueriesContext(connection) as queries, transaction.atomic():
        assert publish_agent_stops({}, normalize_agent_stops(stops(child, now))) == ()
    root.refresh_from_db()
    assert not agent_updates(queries)
    assert root.content_state_epoch == 4
    assert requests == []


def stopped_operation(root, entries):
    operation = extraction.begin_compute_application(message(root, agent_stopped=entries))
    _advance_storage_operation(operation, 'stopped_agents')
    return operation


def test_live_resume_between_preparation_and_publication_wins():
    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root)
    now = timezone.now()
    operation = stopped_operation(root, stops(child, now))
    try:
        batch = extraction.prepare_compute_batch(operation)
        resumed = now + timedelta(seconds=1)
        Session.objects.filter(pk=child.id).update(last_updated_at=resumed)
        extraction.publish_compute_batch(operation, batch, frozenset())
        child.refresh_from_db()
        assert child.last_updated_at == resumed
        assert child.last_stopped_at is None
    finally:
        extraction.close_compute_application(operation)


def test_external_epoch_is_checked_before_grouped_mutation():
    from twicc.core.content_storage.ingestion import insert_ingested_items
    from twicc.core.models import SessionItem

    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root)
    operation = stopped_operation(root, stops(child, timezone.now()))
    try:
        batch = extraction.prepare_compute_batch(operation)
        expected = operation.placement['root_epoch']
        with transaction.atomic():
            insert_ingested_items(root, [(SessionItem(session=root, line_num=1), 'external content')])
        with pytest.raises(extraction.SupersededStorageResult):
            extraction.publish_compute_batch(operation, batch, frozenset())
        child.refresh_from_db()
        assert child.last_stopped_at is None
        assert operation.placement['root_epoch'] == expected
        assert operation.pending_batch is batch
        assert operation.cursor == 0
    finally:
        extraction.close_compute_application(operation)


@pytest.mark.usefixtures('writer_state')
def test_stopped_agents_release_writer_between_slices():
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import (
        run_compute_application_sync, start_compute_application_executor, stop_compute_application_executor,
    )

    root = make_session()
    children = [make_session(id=f'child-{i}', type='subagent', parent_session=root) for i in range(65)]
    now = timezone.now()
    entries = [stops(child, now)[0] for child in children]

    async def scenario():
        operation = await run_compute_application_sync(stopped_operation, root, entries)
        try:
            first = await run_compute_application_sync(extraction.prepare_compute_batch, operation)
            assert len(first.rows) == 64

            async def publish(batch):
                return await run_compute_application_sync(extraction.publish_compute_batch, operation, batch, frozenset())

            assert await db_writer._background_turn(lambda: publish(first))
            assert operation.phase == 'stopped_agents'
            assert not db_writer._db_write_lock.locked()
            assert not db_writer._storage_admission_lock.locked()

            def foreground_write():
                assert not connection.in_atomic_block
                Session.objects.filter(pk=children[-1].id).update(title='foreground')

            await db_writer.run_under_db_write_lock(
                lambda: run_compute_application_sync(foreground_write))
            second = await run_compute_application_sync(extraction.prepare_compute_batch, operation)
            assert len(second.rows) == 1
            assert await db_writer._background_turn(lambda: publish(second))
            assert operation.phase == 'reconciliation'
        finally:
            await run_compute_application_sync(extraction.close_compute_application, operation)

    async def run():
        start_compute_application_executor()
        try:
            await scenario()
        finally:
            await stop_compute_application_executor()

    asyncio.run(run())
    assert Session.objects.get(pk=children[-1].id).title == 'foreground'
    root.refresh_from_db()
    assert root.content_state_epoch == 2


def test_restart_after_committed_child_stop_replays_root_only():
    from twicc.core.models import SessionItem
    from tests.content_storage_helpers import read_current

    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root)
    SessionItem.objects.create(session=child, line_num=1, content='child content')
    assert BaseSessionCompute.apply_session_complete(message(child)).outcome == 'applied'
    SessionItem.objects.create(session=root, line_num=1, content='root content')
    entries = stops(child, timezone.now())
    operation = stopped_operation(root, entries)
    try:
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
    finally:
        extraction.close_compute_application(operation)
    child.refresh_from_db()
    root.refresh_from_db()
    assert child.compute_version == 123
    assert root.compute_version is None
    with CaptureQueriesContext(connection) as queries:
        assert BaseSessionCompute.apply_session_complete(message(root, agent_stopped=entries)).outcome == 'applied'
    assert not agent_updates(queries)
    child.refresh_from_db()
    root.refresh_from_db()
    assert child.compute_version == root.compute_version == 123
    assert read_current(SessionItem.objects.get(session=root)) == 'root content'


def test_grouped_slice_rollback_retains_cursor_and_expected_epoch(monkeypatch):
    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root)
    operation = stopped_operation(root, stops(child, timezone.now()))
    try:
        batch = extraction.prepare_compute_batch(operation)
        expected = dict(operation.placement)
        real_settle = extraction._settle_placement_epoch

        def fail_after_mutation(placement):
            real_settle(placement)
            raise RuntimeError('publication rollback')

        monkeypatch.setattr(extraction, '_settle_placement_epoch', fail_after_mutation)
        with pytest.raises(RuntimeError, match='publication rollback'):
            extraction.publish_compute_batch(operation, batch, frozenset())
        child.refresh_from_db()
        assert child.last_stopped_at is None
        assert operation.placement == expected
        assert operation.pending_batch is batch
        assert operation.cursor == 0
    finally:
        extraction.close_compute_application(operation)


def test_deferred_child_inspection_keeps_root_only_certificate(monkeypatch):
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage.codec import encode_cold
    from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION, compression_policy_version
    from twicc.core.content_storage.references import intern_contents
    from twicc.core.models import SessionItem, SessionItemContent
    from twicc.providers.compute_lifecycle import normalize_agent_stops, publish_agent_stops

    root = make_session(content_storage_version=CONTENT_STORAGE_VERSION,
                        content_state_epoch=4, plaintext_verified_epoch=4)
    child = make_session(id='child', type='subagent', parent_session=root,
                         content_storage_version=CONTENT_STORAGE_VERSION)
    dictionary = _public_dictionary('codex')
    texts = ['current child content ' * 100, 'original child content ' * 100]
    with transaction.atomic():
        ids = intern_contents(texts, 'codex')
        for text in texts:
            encoded, _ = encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
            SessionItemContent.objects.filter(pk=ids[text]).update(
                payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id, session_ids=[child.id])
        root_ids = intern_contents(['root current', 'root original'], 'codex')
        SessionItem.objects.create(session=root, line_num=1, content='',
                                   current_content_id=root_ids['root current'],
                                   original_content_id=root_ids['root original'])
        SessionItem.objects.create(session=child, line_num=1, content='',
                                   current_content_id=ids[texts[0]], original_content_id=ids[texts[1]])
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kw: requests.append(target))
    monkeypatch.setattr(lifecycle, 'session_plaintext_metadata', lambda sid: pytest.fail('Unbounded inspection'))
    with transaction.atomic():
        assert publish_agent_stops({}, normalize_agent_stops(stops(child, timezone.now()))) == (child.id,)
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == root.content_state_epoch == 5
    assert requests == [child.id]
    assert list(SessionItemContent.objects.filter(pk__in=ids.values()).values_list('codec', flat=True)) == ['zstd'] * 2


def test_normalization_keeps_first_agent_order_and_latest_timestamp():
    from twicc.providers.compute_lifecycle import normalize_agent_stops

    now = timezone.now()
    entries = [{'agent_session_id': identity, 'stopped_at': value.isoformat()} for identity, value in
               [('b', now), ('a', now), ('b', now + timedelta(seconds=1)), ('a', now - timedelta(seconds=1))]]
    result = normalize_agent_stops(entries)
    assert [row.session_id for row in result] == ['b', 'a']
    assert [row.stopped_at for row in result] == [now + timedelta(seconds=1), now]


def test_stop_preparation_obeys_byte_budget_and_retains_normalization(monkeypatch):
    from twicc.providers import compute_lifecycle

    root = make_session()
    children = [make_session(id=f'child-{i}', type='subagent', parent_session=root) for i in range(3)]
    now = timezone.now()
    entries = [stops(child, now)[0] for child in children]
    operation = stopped_operation(root, entries)
    try:
        assert operation.stopped_agents is None
        monkeypatch.setattr(compute_lifecycle, 'STOP_BYTE_BUDGET', 100)
        batch = extraction.prepare_compute_batch(operation)
        assert 0 < len(batch.rows) < len(entries)
        assert batch.bytes <= 100
        normalized = operation.stopped_agents
        extraction.publish_compute_batch(operation, batch, frozenset())
        extraction.prepare_compute_batch(operation)
        assert operation.stopped_agents is normalized
    finally:
        extraction.close_compute_application(operation)


def test_progressive_final_does_not_republish_stops(monkeypatch):
    from twicc.providers import compute_lifecycle

    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root, compute_version=123)
    operation = stopped_operation(root, stops(child, timezone.now()))
    try:
        _advance_storage_operation(operation, 'final')
        monkeypatch.setattr(compute_lifecycle, 'publish_agent_stops',
                            lambda *args: pytest.fail('Final publication repeats completed stops'))
        final = extraction.final_compute_message(operation, frozenset())
        assert BaseSessionCompute._apply_session_complete_final(final).outcome == 'applied'
    finally:
        extraction.close_compute_application(operation)


def test_grouped_stops_invalidate_shared_native_root_once_at_multiple_depths():
    root = make_session()
    child = make_session(id='child', type='subagent', parent_session=root)
    grandchild = make_session(id='grandchild', type='subagent', parent_session=child)
    sibling = make_session(id='sibling', type='subagent', parent_session=root)
    now = timezone.now()
    entries = [stops(member, now)[0] for member in (child, grandchild, sibling)]
    operation = stopped_operation(root, entries)
    try:
        extraction.publish_compute_batch(operation, extraction.prepare_compute_batch(operation), frozenset())
        root.refresh_from_db()
        assert root.content_state_epoch == 1
        assert operation.placement['root_epoch'] == 1
        assert Session.objects.filter(pk__in=[child.id, grandchild.id, sibling.id], last_stopped_at=now).count() == 3
    finally:
        extraction.close_compute_application(operation)
