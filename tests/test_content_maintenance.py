from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from tests import content_storage_helpers, test_content_storage_admission
from tests.content_storage_helpers import _public_dictionary
from tests.test_content_root_state import session
from twicc.core.content_storage.codec import decode_content
from twicc.core.content_storage.policy import compression_policy_version
from twicc.content_maintenance_task import prepare_representation, publish_representation

content_row_factory = content_storage_helpers.content_row_factory
writer_state = test_content_storage_admission.writer_state

pytestmark = pytest.mark.django_db


def cold(name):
    from twicc.providers.helpers import get_provider_helpers

    _public_dictionary('codex')
    return session(name, archived=True, created_at=timezone.now() - timedelta(days=31),
                   content_storage_version=1, storage_incarnation=name, content_state_epoch=0,
                   compute_version=get_provider_helpers('codex').current_compute_version)


def policy():
    return compression_policy_version('codex', _public_dictionary('codex').id)


def test_shared_hot_original_owner_prevents_compression(content_row_factory):
    owner = cold('cold')
    hot = session('hot', content_storage_version=1)
    row = content_row_factory('large content ' * 100, False, session_ids=[owner.id, hot.id])
    candidate = prepare_representation(row.id, policy())
    with transaction.atomic():
        assert not publish_representation(candidate, frozenset())
    row.refresh_from_db()
    assert row.codec == 'plain'


def test_active_native_child_prevents_publication(content_row_factory):
    owner = cold('cold')
    child = session('child', type='subagent', parent_session=owner)
    row = content_row_factory('large content ' * 100, False, session_ids=[owner.id])
    candidate = prepare_representation(row.id, policy())
    with transaction.atomic():
        assert not publish_representation(candidate, frozenset({child.id}))


def test_empty_ownership_is_not_cold(content_row_factory):
    row = content_row_factory('large content ' * 100, False)
    candidate = prepare_representation(row.id, policy())
    with transaction.atomic():
        assert not publish_representation(candidate, frozenset())


def test_successful_plain_promotion_can_recompress_same_policy(content_row_factory):
    owner = cold('cold')
    row = content_row_factory('large content ' * 100, False, session_ids=[owner.id])
    row.compression_attempt_version = policy()
    row.compression_no_gain = False
    row.save()
    candidate = prepare_representation(row.id, policy())
    with transaction.atomic():
        assert publish_representation(candidate, frozenset())
    row.refresh_from_db()
    assert row.codec == 'zstd'
    assert decode_content(row) == 'large content ' * 100


def test_representation_change_rejects_old_candidate(content_row_factory):
    owner = cold('cold')
    row = content_row_factory('large content ' * 100, False, session_ids=[owner.id])
    candidate = prepare_representation(row.id, policy())
    row.codec = 'zstd'
    row.save(update_fields=['codec'])
    with transaction.atomic():
        assert not publish_representation(candidate, frozenset())


def test_same_no_gain_skips_and_changed_policy_retries(content_row_factory, monkeypatch):
    from twicc import content_maintenance_task as maintenance

    owner = cold('cold')
    row = content_row_factory('x', False, session_ids=[owner.id])
    row.compression_attempt_version = policy()
    row.compression_no_gain = True
    row.save()
    attempts = []
    original = maintenance.prepare_representation
    def prepare(*args):
        attempts.append(args)
        return original(*args)
    monkeypatch.setattr(maintenance, 'prepare_representation', prepare)
    maintenance.prepare_compression_batch(0)
    assert attempts == []
    row.compression_attempt_version = 'old-policy'
    row.save(update_fields=['compression_attempt_version'])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    assert len(attempts) == len(entries) == 1
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 1
    row.refresh_from_db()
    assert row.compression_no_gain and row.codec == 'plain'


def test_optional_encoder_failure_does_not_record_no_gain(content_row_factory, monkeypatch):
    from twicc import content_maintenance_task as maintenance

    owner = cold('cold')
    row = content_row_factory('content' * 100, False, session_ids=[owner.id])
    def failure(*args, **kwargs):
        raise RuntimeError('optional encoder unavailable')
    monkeypatch.setattr(maintenance, 'transcode_content', failure)
    _, entries, _ = maintenance.prepare_compression_batch(0)
    row.refresh_from_db()
    assert entries == ()
    assert row.compression_attempt_version is None and not row.compression_no_gain


def test_stale_root_metadata_retains_plain_then_periodic_recovers(content_row_factory):
    from twicc import content_maintenance_task as maintenance

    owner = cold('cold')
    current_version = owner.compute_version
    owner.compute_version = 0
    owner.save(update_fields=['compute_version'])
    row = content_row_factory('content' * 100, False, session_ids=[owner.id])
    assert maintenance.prepare_compression_batch(0)[1] == ()
    owner.compute_version = current_version
    owner.save(update_fields=['compute_version'])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    with transaction.atomic():
        assert maintenance.publish_prepared_batch(entries, frozenset()) == 1
    row.refresh_from_db()
    assert row.codec == 'zstd'


def test_stored_byte_budget_allows_one_oversized_row(content_row_factory):
    from twicc import content_maintenance_task as maintenance

    owner = cold('cold')
    first = content_row_factory('first' * 100, False, session_ids=[owner.id])
    second = content_row_factory('second' * 100, False, session_ids=[owner.id])
    cursor, entries, done = maintenance.prepare_compression_batch(0, 500, 1)
    assert cursor == first.id and not done and len(entries) == 1
    cursor, entries, done = maintenance.prepare_compression_batch(cursor, 500, 1)
    assert cursor == second.id and done and len(entries) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('change', ['epoch', 'membership', 'incarnation'])
def test_actual_publication_path_rejects_change_back(content_row_factory, writer_state,
                                                     compute_executor_started, monkeypatch, change):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.core.content_storage import state
    from twicc.agent.registry import get_agent_manager_registry

    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    owner = cold('cold')
    other = cold('other')
    row = content_row_factory('content' * 100, False, session_ids=[owner.id])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    with transaction.atomic():
        if change == 'membership':
            row.session_ids = [owner.id, other.id]
            row.save(update_fields=['session_ids'])
            state.invalidate_roots([owner.id, other.id])
            row.session_ids = [owner.id]
            row.save(update_fields=['session_ids'])
            state.invalidate_roots([owner.id, other.id])
        elif change == 'epoch':
            from twicc.core.content_storage.lifecycle import update_lifecycle
            update_lifecycle(type(owner).objects.filter(pk=owner.id), archived=False)
            update_lifecycle(type(owner).objects.filter(pk=owner.id), archived=True)
        else:
            type(owner).objects.filter(pk=owner.id).update(storage_incarnation='replacement')
    assert asyncio.run(maintenance._apply(maintenance._publish, entries)) == 0
    row.refresh_from_db()
    assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_actual_publication_captures_active_native_child(content_row_factory, writer_state,
                                                         compute_executor_started, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry

    owner = cold('cold')
    child = session('child', type='subagent', parent_session=owner)
    row = content_row_factory('content' * 100, False, session_ids=[owner.id])
    _, entries, _ = maintenance.prepare_compression_batch(0)
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents',
                        lambda: [SimpleNamespace(session_id=child.id)])
    assert asyncio.run(maintenance._apply(maintenance._publish, entries)) == 0
    row.refresh_from_db()
    assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_combined_timer_uses_controlled_clock_and_filesystem_flag(writer_state, compute_executor_started,
                                                                 monkeypatch, settings):
    import asyncio
    from twicc import content_maintenance_task as maintenance, session_dirs_cleanup_task

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock = [0.0]
    waits = []
    passes = []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original = maintenance.prepare_compression_batch
    def compression(*args):
        passes.append(clock[0])
        return original(*args)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    async def no_filesystem():
        pytest.fail('Filesystem disable must retain content maintenance only')
    monkeypatch.setattr(session_dirs_cleanup_task, '_run_cleanup_pass', no_filesystem)
    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 3:
            stop.set()
        else:
            clock[0] += timeout
        return False
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    assert waits == [1800, 86400, 86400]
    assert passes == [1800, 88200]


@pytest.mark.django_db(transaction=True)
def test_startup_recovers_metadata_and_publishes_bounded_readiness(content_row_factory, writer_state,
                                                                 compute_executor_started, monkeypatch, settings):
    import asyncio
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    root = session('root', content_storage_version=1, storage_incarnation='r', content_state_epoch=4)
    for line in range(1, 4):
        row = content_row_factory(str(line) * 1000, True, session_ids=[root.id])
        SessionItem.objects.create(session=root, line_num=line, current_content=row, content='')
    final_queries = []
    original = maintenance.apply_promotion_batch
    def apply(batch, active, **kwargs):
        with CaptureQueriesContext(connection) as queries:
            result = original(batch, active, **kwargs)
        if batch.cursor.done:
            final_queries.extend(query['sql'] for query in queries)
        return result
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    async def wait(stop, wake, timeout):
        stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == 4
    assert final_queries
    assert not any('core_sessionitem"' in query for query in final_queries)
    assert len(final_queries) < 20


@pytest.mark.django_db(transaction=True)
def test_interactive_write_runs_between_maintenance_slices(content_row_factory, writer_state,
                                                           compute_executor_started, monkeypatch, settings):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.providers import db_writer
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    root = session('root', content_storage_version=1, storage_incarnation='r', content_state_epoch=4)
    for line in range(1, 4):
        row = content_row_factory(str(line) * 1000, True, session_ids=[root.id])
        SessionItem.objects.create(session=root, line_num=line, current_content=row, content='')
    second_preparation = threading.Event()
    release = threading.Event()
    trace = []
    original_prepare = maintenance.prepare_promotion_batch
    def prepare(snapshot, cursor, *args):
        if cursor.session_id is not None and not second_preparation.is_set():
            second_preparation.set()
            assert release.wait(5)
        return original_prepare(snapshot, cursor, *args)
    original_apply = maintenance.apply_promotion_batch
    def apply(batch, active, **kwargs):
        result = original_apply(batch, active, **kwargs)
        trace.append('final' if batch.cursor.done else 'slice')
        return result
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    async def wait(stop, wake, timeout):
        stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    def interactive():
        with transaction.atomic():
            type(root).objects.filter(pk=root.id).update(title='interactive')
        trace.append('interactive')
    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(second_preparation.wait, 5)
            assert not db_writer._db_write_lock.locked()
            assert not db_writer._storage_admission_lock.locked()
            await db_writer.run_under_db_write_lock(lambda: run_compute_sync(interactive))
        finally:
            release.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())
    assert trace.index('interactive') < trace.index('final')
    root.refresh_from_db()
    assert root.title == 'interactive' and root.plaintext_verified_epoch == 4


@pytest.mark.django_db(transaction=True)
def test_hot_wakeup_promotes_without_timer_delay(content_row_factory, writer_state, compute_executor_started,
                                                monkeypatch, settings):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.content_storage import state
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='r',
                   content_state_epoch=4, plaintext_verified_epoch=4)
    row = content_row_factory('hot data' * 100, True, session_ids=[root.id])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')
    waits = []
    def mutation(active):
        with transaction.atomic():
            state.invalidate_roots([root.id])
    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            await maintenance._apply(mutation)
            await asyncio.wait_for(wake.wait(), 1)
        else:
            stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.codec == 'plain' and root.plaintext_verified_epoch == 5
    assert all(1700 < delay <= 1800 for delay in waits)


@pytest.mark.django_db(transaction=True)
def test_cancelled_coordinator_drains_admitted_publication(content_row_factory, writer_state,
                                                          compute_executor_started, monkeypatch, settings):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.providers import db_writer

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'INITIAL_DELAY', 0)
    owner = cold('cold')
    row = content_row_factory('content' * 100, False, session_ids=[owner.id])
    entered = threading.Event()
    release = threading.Event()
    original = maintenance._publish
    def publish(entries, active):
        entered.set()
        assert release.wait(5)
        return original(entries, active)
    monkeypatch.setattr(maintenance, '_publish', publish)
    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert db_writer._storage_admission_lock.locked()
            assert db_writer._db_write_lock.locked()
            assert not task.done()
        finally:
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not db_writer._storage_admission_lock.locked()
        assert not db_writer._db_write_lock.locked()
    asyncio.run(scenario())
    row.refresh_from_db()
    assert row.codec == 'zstd' and decode_content(row) == 'content' * 100
    assert maintenance._wakeup is None


@pytest.mark.django_db(transaction=True)
def test_cancelled_promotion_restarts_proof_from_zero(content_row_factory, writer_state,
                                                     compute_executor_started, monkeypatch, settings):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    root = session('root', content_storage_version=1, storage_incarnation='r', content_state_epoch=4)
    rows = []
    for line in range(1, 4):
        row = content_row_factory(str(line) * 1000, True, session_ids=[root.id])
        SessionItem.objects.create(session=root, line_num=line, current_content=row, content='')
        rows.append(row)
    entered = threading.Event()
    release = threading.Event()
    original = maintenance.prepare_promotion_batch
    cursors = []
    def prepare(snapshot, cursor, *args):
        cursors.append(cursor)
        if cursor.session_id is not None and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(snapshot, cursor, *args)
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    async def cancel():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(cancel())
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None
    for row in rows:
        row.refresh_from_db()
        assert decode_content(row) == str(rows.index(row) + 1) * 1000
    assert rows[0].codec == 'plain' and rows[-1].codec == 'zstd'
    cursors.clear()
    async def wait(stop, wake, timeout):
        stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    assert cursors[0] == maintenance.START_CURSOR
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == 4


@pytest.mark.django_db(transaction=True)
def test_promotion_failure_does_not_disable_periodic_recovery(content_row_factory, writer_state,
                                                            compute_executor_started, monkeypatch, settings):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='r', content_state_epoch=4)
    row = content_row_factory('hot data' * 100, True, session_ids=[root.id])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')
    attempts = []
    original = maintenance.prepare_promotion_batch
    def prepare(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('temporary worker failure')
        return original(*args)
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    clock = [0]
    waits = []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            clock[0] += timeout
        else:
            stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.codec == 'plain' and root.plaintext_verified_epoch == 4
    assert len(attempts) == 2 and waits == [1800, 86400]


@pytest.mark.django_db(transaction=True)
def test_hot_promotion_precedes_prepared_compression_publication(content_row_factory, writer_state,
                                                                compute_executor_started, monkeypatch, settings):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.content_storage import state
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'INITIAL_DELAY', 0)
    cold_owner = cold('cold')
    cold_row = content_row_factory('cold data' * 100, False, session_ids=[cold_owner.id])
    hot_owner = session('hot', content_storage_version=1, storage_incarnation='h',
                        content_state_epoch=4, plaintext_verified_epoch=4)
    hot_row = content_row_factory('hot data' * 100, True, session_ids=[hot_owner.id])
    SessionItem.objects.create(session=hot_owner, line_num=1, current_content=hot_row, content='')
    prepared = threading.Event()
    release = threading.Event()
    trace = []
    original_prepare = maintenance.prepare_compression_batch
    def prepare(*args):
        result = original_prepare(*args)
        prepared.set()
        assert release.wait(5)
        return result
    original_publish = maintenance._publish
    def publish(entries, active):
        trace.append('compression')
        return original_publish(entries, active)
    original_promote = maintenance.apply_promotion_batch
    def promote(*args, **kwargs):
        trace.append('promotion')
        return original_promote(*args, **kwargs)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', prepare)
    monkeypatch.setattr(maintenance, '_publish', publish)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', promote)
    async def wait(stop, wake, timeout):
        stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    def mutation(active):
        with transaction.atomic():
            state.invalidate_roots([hot_owner.id])
    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(prepared.wait, 5)
            await maintenance._apply(mutation)
        finally:
            release.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())
    assert trace == ['promotion', 'compression']
    cold_row.refresh_from_db()
    hot_row.refresh_from_db()
    assert cold_row.codec == 'zstd' and hot_row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_root_readiness_does_not_wait_for_unextracted_empty_descendant(content_row_factory, writer_state,
                                                                 compute_executor_started, monkeypatch, settings):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    root = session('a-root', content_storage_version=1, storage_incarnation='r', content_state_epoch=4)
    session('b-child', type='subagent', parent_session=root, storage_incarnation='c')
    row = content_row_factory('hot data' * 100, True, session_ids=[root.id])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')
    async def wait(stop, wake, timeout):
        stop.set()
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    root.refresh_from_db()
    row.refresh_from_db()
    assert root.plaintext_verified_epoch == root.content_state_epoch and row.codec == 'plain'


def test_compression_never_queries_reverse_item_ownership(content_row_factory):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc import content_maintenance_task as maintenance

    owner = cold('cold')
    content_row_factory('content' * 100, False, session_ids=[owner.id])
    with CaptureQueriesContext(connection) as queries:
        _, entries, _ = maintenance.prepare_compression_batch(0)
        with transaction.atomic():
            assert maintenance.publish_prepared_batch(entries, frozenset()) == 1
    assert not any('core_sessionitem"' in query['sql'] for query in queries)


def test_recompression_budget_includes_decoded_input_for_large_compressed_rows(content_row_factory):
    import hashlib
    from twicc import content_maintenance_task as maintenance
    from twicc.core.models import ContentDictionary

    owner = cold('cold')
    rows = [content_row_factory(str(n) * (5 * 1024 * 1024), True, session_ids=[owner.id]) for n in range(3)]
    payload = b'new public synthetic dictionary'
    ContentDictionary.objects.create(provider='codex', version=2, format='raw', payload=payload,
                                     sha256=hashlib.sha256(payload).digest())
    cursor, entries, done = maintenance.prepare_compression_batch(0, 500, 8 * 1024 * 1024)
    assert len(entries) == 1
    assert cursor == rows[0].id and not done


def test_compression_budget_charges_owner_list_bytes_even_for_ineligible_rows(content_row_factory, monkeypatch):
    from twicc import content_maintenance_task as maintenance

    owners = [f'missing-owner-{n}' for n in range(1000)]
    first = content_row_factory('first', False, session_ids=owners)
    content_row_factory('second', False, session_ids=owners)
    from twicc.core.models import SessionItemContent

    field = SessionItemContent._meta.get_field('session_ids')
    decode = field.from_db_value
    decoded_owner_lists = []

    def record_decode(value, expression, connection):
        decoded_owner_lists.append(len(value.encode()))
        return decode(value, expression, connection)

    monkeypatch.setattr(field, 'from_db_value', record_decode)
    cursor, entries, done = maintenance.prepare_compression_batch(0, 500, 1024)
    assert cursor == first.id and entries == () and not done
    assert len(decoded_owner_lists) == 1


def test_compression_prepares_the_observed_content_once(content_row_factory):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc import content_maintenance_task as maintenance

    owner = cold('one-observed-row')
    row = content_row_factory('exact observed bytes ' * 100, False, session_ids=[owner.id])
    with CaptureQueriesContext(connection) as queries:
        _, entries, _ = maintenance.prepare_compression_batch(0)
    payload_reads = [query for query in queries if 'SELECT' in query['sql']
                     and '"core_sessionitemcontent"."payload"' in query['sql'] and 'LENGTH(' not in query['sql']]
    assert len(payload_reads) == 1
    assert len(entries) == 1
    assert entries[0].proof.payload == bytes(row.payload)
    assert decode_content(type(row)(**entries[0].candidate.encoded._asdict())) == 'exact observed bytes ' * 100


@pytest.mark.django_db(transaction=True)
def test_foreground_targets_interrupt_recovery_at_next_grant(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    # Finishing A's local loop or using LIFO violates foreground B/C ordering.
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    for name, count in [('a', 3), ('b', 1), ('c', 1)]:
        owner = session(name, content_storage_version=1, storage_incarnation=name, content_state_epoch=4,
                        plaintext_verified_epoch=None if name == 'a' else 4)
        for line in range(1, count + 1):
            row = content_row_factory((name + str(line)) * 1000, True, session_ids=[name])
            SessionItem.objects.create(session=owner, line_num=line, current_content=row, content='')
    entered, release = threading.Event(), threading.Event()
    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch
    trace = []

    def prepare(snapshot, cursor, *args):
        result = original_prepare(snapshot, cursor, *args)
        if snapshot.root_id == 'a' and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return result

    def apply(batch, active, **kwargs):
        result = original_apply(batch, active, **kwargs)
        trace.append(batch.snapshot.root_id)
        return result

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            maintenance.request_root_promotion('b')
            maintenance.request_root_promotion('c')
            maintenance.request_root_promotion('b')
            await asyncio.sleep(0)
        finally:
            release.set()
        await asyncio.wait_for(task, 5)
        assert not db_writer.get_write_coordinator().demands

    asyncio.run(scenario())
    assert trace[:2] == ['b', 'c']
    assert trace.count('b') == trace.count('c') == 1
    assert trace[2:] == ['a', 'a', 'a']


@pytest.mark.django_db(transaction=True)
def test_foreground_demand_interrupts_background_wait_without_self_blocking(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WritePriority, WriteResources

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    for name in ('a', 'b'):
        owner = session(name, content_storage_version=1, storage_incarnation=name, content_state_epoch=4,
                        plaintext_verified_epoch=None if name == 'a' else 4)
        row = content_row_factory(name * 1000, True, session_ids=[name])
        SessionItem.objects.create(session=owner, line_num=1, current_content=row, content='')
    trace = []
    original = maintenance.apply_promotion_batch
    waiting = asyncio.Event()
    original_wait = maintenance._wait

    async def wait(stop, wake, timeout):
        if trace == ['b', 'a']:
            stop.set()
        else:
            waiting.set()
            return await original_wait(stop, wake, timeout)

    def apply(batch, active, **kwargs):
        trace.append(batch.snapshot.root_id)
        return original(batch, active, **kwargs)

    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        coordinator = db_writer.get_write_coordinator()
        holder = await coordinator.acquire(WriteResources.WRITER, WritePriority.FOREGROUND)
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            maintenance.request_root_promotion('b')
            await asyncio.sleep(0)
            assert len(coordinator.demands) == 1
            holder.release()
            await asyncio.wait_for(task, 5)
            assert not coordinator.demands and not coordinator.holders
        finally:
            holder.release()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert trace == ['b', 'a']


@pytest.mark.django_db(transaction=True)
def test_commit_callback_captures_priority_before_origin_resets(monkeypatch):
    from twicc.core.content_storage import lifecycle, state
    from twicc.providers.write_coordinator import WritePriority, write_priority

    root = session('origin', storage_incarnation='origin', content_state_epoch=4)
    calls = []
    monkeypatch.setattr(lifecycle, '_promotion_wakeup', lambda target, *, priority: calls.append((target, priority)))
    with transaction.atomic():
        token = write_priority.set(WritePriority.BACKGROUND)
        try:
            state.invalidate_roots([root.id])
        finally:
            write_priority.reset(token)
        assert calls == []
    assert calls == [('origin', WritePriority.BACKGROUND)]
    assert write_priority.get() is WritePriority.FOREGROUND


@pytest.mark.django_db(transaction=True)
def test_recovery_upgrade_preserves_prepared_work_and_foreground_fifo(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WritePriority, write_priority

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    for name in ('a', 'b'):
        owner = session(name, content_storage_version=1, storage_incarnation=name, content_state_epoch=4,
                        plaintext_verified_epoch=None if name == 'a' else 4)
        row = content_row_factory(name * 1000, True, session_ids=[name])
        SessionItem.objects.create(session=owner, line_num=1, current_content=row, content='')
    entered, release = threading.Event(), threading.Event()
    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch
    preparations, publications = [], []

    def prepare(snapshot, *args):
        preparations.append(snapshot.root_id)
        result = original_prepare(snapshot, *args)
        if snapshot.root_id == 'a':
            entered.set()
            assert release.wait(5)
        return result

    def apply(batch, active, **kwargs):
        publications.append((batch.snapshot.root_id, getattr(batch.snapshot, 'recovery', False), write_priority.get()))
        return original_apply(batch, active, **kwargs)

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            maintenance.request_root_promotion('b')
            maintenance.request_root_promotion('a')
            maintenance.request_root_promotion('a', priority=WritePriority.BACKGROUND)
            await asyncio.sleep(0)
            assert len(db_writer.get_write_coordinator().demands) == 2
        finally:
            release.set()
        await asyncio.wait_for(task, 5)
        assert not db_writer.get_write_coordinator().demands

    asyncio.run(scenario())
    assert preparations == ['a', 'b']
    assert publications == [('b', False, WritePriority.FOREGROUND), ('a', False, WritePriority.FOREGROUND)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('phase', ['prepare', 'publish'])
@pytest.mark.parametrize('outcome', ['cancel', 'stop', 'failure'])
def test_foreground_pressure_settles_after_worker_drain(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings, phase, outcome,
):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    owner = session('a', content_storage_version=1, storage_incarnation='a', content_state_epoch=4)
    row = content_row_factory('a' * 1000, True, session_ids=['a'])
    SessionItem.objects.create(session=owner, line_num=1, current_content=row, content='')
    prepared, allow_prepare = threading.Event(), threading.Event()
    publishing, allow_publish = threading.Event(), threading.Event()
    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch

    def prepare(*args):
        result = original_prepare(*args)
        prepared.set()
        assert allow_prepare.wait(5)
        if phase == 'prepare' and outcome == 'failure':
            raise RuntimeError('injected preparation failure')
        return result

    def apply(*args, **kwargs):
        publishing.set()
        assert allow_publish.wait(5)
        if phase == 'publish' and outcome == 'failure':
            raise RuntimeError('injected publication failure')
        return original_apply(*args, **kwargs)

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(maintenance.start_content_maintenance_task(stop))
        coordinator = db_writer.get_write_coordinator()
        try:
            assert await asyncio.to_thread(prepared.wait, 5)
            maintenance.request_root_promotion('a')
            await asyncio.sleep(0)
            assert len(coordinator.demands) == 1
            if phase == 'publish':
                allow_prepare.set()
                assert await asyncio.to_thread(publishing.wait, 5)
            if outcome == 'cancel':
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
            elif outcome == 'stop':
                stop.set()
                await asyncio.sleep(0)
            assert not task.done()
            assert len(coordinator.demands) == 1
            assert bool(coordinator.holders) == (phase == 'publish')
        finally:
            allow_prepare.set()
            allow_publish.set()
        if outcome == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        else:
            await asyncio.wait_for(task, 5)
        assert not coordinator.demands and not coordinator.holders and not coordinator.requests
        assert maintenance._wakeup is None

    asyncio.run(scenario())
    row.refresh_from_db()
    assert row.codec == ('plain' if phase == 'publish' and outcome != 'failure' else 'zstd')


@pytest.mark.django_db(transaction=True)
def test_background_deferral_coalesces_changed_epochs_and_preserves_exact_targets(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.content_storage import state
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WritePriority, write_priority

    # Missing deferral repeatedly scans the root after each binding generation change.
    assert hasattr(maintenance, 'defer_background_promotions'), 'Operation-scoped promotion deferral is required'
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='root-inc', content_state_epoch=4)
    child = session('child', type='subagent', parent_session=root, content_storage_version=1,
                    storage_incarnation='child-inc')
    sibling = session('sibling', type='subagent', parent_session=root, content_storage_version=1,
                      storage_incarnation='sibling-inc')
    rows = []
    for owner in (root, child, sibling):
        row = content_row_factory(owner.id * 1000, True, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=1, current_content=row, original_content=row, content='')
        rows.append(row)
    preparations, publications = [], []
    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch

    def prepare(snapshot, *args):
        preparations.append(getattr(snapshot, 'session_id', snapshot.root_id))
        return original_prepare(snapshot, *args)

    def apply(batch, active, **kwargs):
        publications.append((getattr(batch.snapshot, 'session_id', batch.snapshot.root_id),
                             getattr(batch.snapshot, 'recovery', False), write_priority.get()))
        return original_apply(batch, active, **kwargs)

    def mutation(active):
        with transaction.atomic():
            state.invalidate_roots(['root'], promotion_targets=['child'])
        assert state.Session.objects.get(pk='root').plaintext_verified_epoch is None
        return [decode_content(row) for row in rows]

    waits = []
    token = None

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) <= 3:
            assert preparations == []
            result = await maintenance._apply(mutation, priority=WritePriority.BACKGROUND)
            assert result == ['root' * 1000, 'child' * 1000, 'sibling' * 1000]
            await asyncio.sleep(0)
            assert not db_writer.get_write_coordinator().demands
        elif len(waits) == 4:
            assert preparations == []
            token.release()
            await asyncio.wait_for(wake.wait(), 1)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        nonlocal token
        token = maintenance.defer_background_promotions('root', 'root-inc')
        try:
            await asyncio.wait_for(maintenance.start_content_maintenance_task(asyncio.Event()), 5)
        finally:
            token.release()
        assert not db_writer.get_write_coordinator().demands

    asyncio.run(scenario())
    assert preparations == ['root', 'child']
    assert publications == [('root', True, WritePriority.BACKGROUND), ('child', False, WritePriority.BACKGROUND)]
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['plain', 'plain', 'zstd']
    root.refresh_from_db()
    assert root.content_state_epoch == root.plaintext_verified_epoch == 7


@pytest.mark.django_db(transaction=True)
def test_deferral_pauses_existing_recovery_and_foreground_bypasses_it(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    import threading
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WritePriority, write_priority

    assert hasattr(maintenance, 'defer_background_promotions'), 'Operation-scoped promotion deferral is required'
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='root-inc', content_state_epoch=4)
    row = content_row_factory('root' * 1000, True, session_ids=['root'])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')
    prepared, release = threading.Event(), threading.Event()
    original_prepare, original_apply = maintenance.prepare_promotion_batch, maintenance.apply_promotion_batch
    publications = []
    token = None

    def prepare(*args):
        batch = original_prepare(*args)
        prepared.set()
        assert release.wait(5)
        return batch

    def apply(batch, active, **kwargs):
        publications.append((getattr(batch.snapshot, 'recovery', False), write_priority.get()))
        return original_apply(batch, active, **kwargs)

    async def wait(stop, wake, timeout):
        if not publications:
            assert token is not None
            maintenance.request_root_promotion('root')
            await asyncio.wait_for(wake.wait(), 1)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        nonlocal token
        task = asyncio.create_task(maintenance.start_content_maintenance_task(asyncio.Event()))
        try:
            assert await asyncio.to_thread(prepared.wait, 5)
            token = maintenance.defer_background_promotions('root', 'root-inc')
            release.set()
            await asyncio.wait_for(task, 5)
            assert publications == [(False, WritePriority.FOREGROUND)]
            assert not db_writer.get_write_coordinator().demands
        finally:
            release.set()
            if token is not None:
                # Maintenance already stopped. Releasing still works and remains idempotent.
                token.release()
                token.release()

    asyncio.run(scenario())
    row.refresh_from_db()
    assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_deferral_successor_survives_old_release_and_late_callback_across_restart(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers.write_coordinator import WritePriority

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='root-inc', content_state_epoch=4)
    row = content_row_factory('root' * 1000, True, session_ids=['root'])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')
    original = maintenance.prepare_promotion_batch
    preparations = []
    phase, waits = 0, 0
    successor = None

    def prepare(*args):
        preparations.append(args[0].root_id)
        return original(*args)

    async def wait(stop, wake, timeout):
        nonlocal waits
        waits += 1
        if phase == 0:
            assert preparations == []
            stop.set()
        elif waits == 1:
            assert preparations == []
            # A thread-safe callback after the predecessor released uses the current token.
            await asyncio.to_thread(maintenance.request_root_promotion, 'root', priority=WritePriority.BACKGROUND)
            await asyncio.sleep(0)
        elif waits == 2:
            assert preparations == []
            successor.release()
            await asyncio.wait_for(wake.wait(), 1)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        nonlocal successor, phase, waits
        old = maintenance.defer_background_promotions('root', 'root-inc')
        try:
            await asyncio.wait_for(maintenance.start_content_maintenance_task(asyncio.Event()), 5)
            old.release()
            successor = maintenance.defer_background_promotions('root', 'root-inc')
            old.release()  # Must not remove the successor's root scope.
            phase, waits = 1, 0
            await asyncio.wait_for(maintenance.start_content_maintenance_task(asyncio.Event()), 5)
        finally:
            old.release()
            if successor is not None:
                successor.release()

    asyncio.run(scenario())
    assert preparations == ['root']
    row.refresh_from_db()
    assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_old_incarnation_deferral_cannot_pause_recreated_root(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    root = session('root', content_storage_version=1, storage_incarnation='new-inc', content_state_epoch=4)
    row = content_row_factory('root' * 1000, True, session_ids=['root'])
    SessionItem.objects.create(session=root, line_num=1, current_content=row, content='')

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        token = maintenance.defer_background_promotions('root', 'old-inc')
        try:
            await asyncio.wait_for(maintenance.start_content_maintenance_task(asyncio.Event()), 5)
        finally:
            token.release()

    asyncio.run(scenario())
    row.refresh_from_db()
    assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('phase', ['snapshot', 'later_preparation'])
@pytest.mark.parametrize('outcome', ['complete', 'stop', 'cancel'])
def test_explicit_upgrade_retries_recovery_eligibility_rejected_during_await(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings, phase, outcome,
):
    import asyncio
    import threading
    from types import SimpleNamespace
    from twicc import content_maintenance_task as maintenance
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.models import SessionItem
    from twicc.providers import db_writer

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents',
                        lambda: [SimpleNamespace(session_id='root')])
    now = [timezone.now()]
    root = session('root', archived=True, created_at=now[0] - timedelta(days=14),
                   content_storage_version=1, storage_incarnation='root-inc', content_state_epoch=4)
    rows = []
    for line in (1, 2):
        row = content_row_factory(str(line) * 1000, True, session_ids=['root'])
        SessionItem.objects.create(session=root, line_num=line, current_content=row, content='')
        rows.append(row)
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now[0])
    rejected, release = threading.Event(), threading.Event()
    original_snapshot, original_prepare = maintenance._snapshot, maintenance.prepare_promotion_batch

    def snapshot(*args, **kwargs):
        if phase == 'snapshot' and not rejected.is_set():
            now[0] += timedelta(days=2)
            result = original_snapshot(*args, **kwargs)
            assert result is None  # Recovery has no active override.
            rejected.set()
            assert release.wait(5)
            return result
        return original_snapshot(*args, **kwargs)

    def prepare(snapshot, cursor, *args):
        if phase == 'later_preparation' and cursor != maintenance.START_CURSOR and not rejected.is_set():
            now[0] += timedelta(days=2)
            result = original_prepare(snapshot, cursor, *args)
            assert result.cursor == cursor and not result.verified
            rejected.set()
            assert release.wait(5)
            return result
        return original_prepare(snapshot, cursor, *args)

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, '_snapshot', snapshot)
    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, '_wait', wait)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(maintenance.start_content_maintenance_task(stop))
        coordinator = db_writer.get_write_coordinator()
        try:
            assert await asyncio.to_thread(rejected.wait, 5)
            maintenance.request_root_promotion('root')
            await asyncio.sleep(0)
            assert len(coordinator.demands) == 1
            if outcome == 'cancel':
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            elif outcome == 'stop':
                stop.set()
        finally:
            release.set()
        if outcome == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        else:
            await asyncio.wait_for(task, 5)
        assert not coordinator.demands and not coordinator.holders and not coordinator.requests

    asyncio.run(scenario())
    root.refresh_from_db()
    for row in rows:
        row.refresh_from_db()
    if outcome == 'complete':
        assert [row.codec for row in rows] == ['plain', 'plain']
        assert root.plaintext_verified_epoch == 4
    else:
        assert rows[-1].codec == 'zstd'
        assert root.plaintext_verified_epoch is None
