"""Real writer lifetimes retain partial publication and rebuild outdated sources."""

import asyncio
from collections import Counter
from contextlib import closing
from datetime import timedelta
import sqlite3
import threading
from uuid import uuid4

import orjson
import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from tests import content_storage_helpers, test_compute_continuation
from tests.content_storage_helpers import _public_dictionary
from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage import extraction
from twicc.core.models import HistoryFactKind, Session, SessionItem
from twicc.providers import compute_executor, db_writer, history_facts
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.compute_work import ComputeWorkState
from twicc.providers.helpers import get_provider_helpers

wal_runtime = test_compute_continuation.wal_runtime
content_row_factory = content_storage_helpers.content_row_factory

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def no_scheduler_errors(caplog, monkeypatch):
    close_connection = db_writer.close_compute_application_connection

    def close_clean_connection():
        with connections['default'].cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_temp_master WHERE type='table'")
            assert cursor.fetchall() == [], 'Application cleanup must remove retained TEMP tables'
        close_connection()

    monkeypatch.setattr(db_writer, 'close_compute_application_connection', close_clean_connection)
    yield
    assert not [record for record in caplog.get_records('call') if record.levelname in {'ERROR', 'CRITICAL'}]


@pytest.fixture
def publication_tree(wal_runtime, content_row_factory, request):
    root, path = wal_runtime
    provider = request.param
    target = get_provider_helpers(provider).current_compute_version
    now = timezone.now()
    Session.objects.filter(pk=root.pk).update(provider=provider, items_cost_computed_through_at=now)
    root.refresh_from_db()
    children = []
    # Cross the real lifecycle budget and include a second ancestry depth.
    for index in range(65):
        children.append(Session.objects.create(
            id=f'child-{index:02}', project=root.project, provider=provider, type='subagent',
            parent_session=root if index < 64 else children[0], file_path=str(uuid4()),
            storage_incarnation=str(uuid4()), compute_version=target, content_storage_version=1,
            items_cost_computed_through_at=now,
        ))
    owner = Session.objects.create(
        id='other-root', project=root.project, provider=provider, file_path=str(uuid4()),
        archived=True, created_at=now - timedelta(days=60), storage_incarnation=str(uuid4()),
        content_storage_version=1, content_state_epoch=0, compute_version=target,
    )
    _public_dictionary(provider)
    shared = content_row_factory('shared history ' * 1000, False, provider, session_ids=[root.id, owner.id])
    SessionItem.objects.filter(session=root, line_num=1).update(content='', current_content=shared)
    candidate = content_row_factory('periodic candidate ' * 1000, False, provider, session_ids=[owner.id])
    SessionItem.objects.create(session=owner, line_num=1, content='', current_content=candidate)
    history_facts.append_history_facts(root.id, [
        history_facts.HistoryFact(line, HistoryFactKind.TOOL_CALL, 'old', {'old': line})
        for line in range(1, 258)
    ])
    facts = [history_facts.HistoryFact(line, HistoryFactKind.TOOL_CALL, 'new', {'value': line})._asdict()
             for line in range(1, 258)]
    msg = {
        'type': 'session_complete', 'provider': provider, 'session_id': root.id,
        'observed_last_offset': 0, 'observed_incarnation': root.storage_incarnation,
        'observed_epoch': 0, 'observed_upper_line': 600, 'history_facts': facts,
        'session_fields': {'compute_version': target},
        'force_cost_session_ids': sorted([root.id, *[child.id for child in children]]),
        'agent_stopped': [{'agent_session_id': child.id, 'stopped_at': now.isoformat()} for child in children],
    }
    with closing(sqlite3.connect(path)) as database:
        connection.connection.backup(database)
    return root, children, owner, shared, candidate, path, msg


def stored(path, sql, params=()):
    with closing(sqlite3.connect(path)) as database:
        return database.execute(sql, params).fetchall()


def enqueue(msg):
    run_id, done = db_writer.arm_compute_completion(msg['provider'], display_session_ids=set(), total_display=0,
                                                   applied_queue=asyncio.Queue())
    applied = db_writer._compute_states[run_id].applied_queue
    db_writer.get_subprocess_queue().put({**msg, 'run_id': run_id})
    db_writer.get_subprocess_queue().put({'type': 'done', 'provider': msg['provider'], 'run_id': run_id})
    return run_id, done, applied


def assert_complete(tree):
    root, children, owner, shared, candidate, path, msg = tree
    rows = stored(path, 'SELECT line_num,kind,key,data FROM core_sessionhistoryfact WHERE session_id=? ORDER BY line_num',
                  [root.id])
    assert [(line, kind, key, orjson.loads(data)) for line, kind, key, data in rows] == [
        (line, 'tool_call', 'new', {'value': line}) for line in range(1, 258)]
    assert stored(path, 'SELECT compute_version,content_storage_version,plaintext_verified_epoch=content_state_epoch '
                  'FROM core_session WHERE id=?', [root.id]) == [(msg['session_fields']['compute_version'], 1, 1)]
    assert stored(path, 'SELECT DISTINCT compute_version,content_storage_version,plaintext_verified_epoch '
                  'FROM core_session WHERE type=\'subagent\'') == [(msg['session_fields']['compute_version'], 1, None)]
    assert stored(path, 'SELECT count(*) FROM core_session WHERE type=\'subagent\' AND last_stopped_at IS NOT NULL') == [(65,)]
    owners = stored(path, 'SELECT session_ids,codec FROM core_sessionitemcontent WHERE id=?', [shared.id])[0]
    assert orjson.loads(owners[0]) == sorted([root.id, owner.id])
    assert owners[1] == 'plain'
    assert stored(path, 'SELECT items_cost_computed_through_at FROM core_session WHERE id=?', [root.id]) == [(None,)]
    queue = stored(path, 'SELECT root_session_id,force_cost_session_ids FROM core_statisticsqueueentry')
    assert len(queue) == 1 and queue[0][0] == root.id
    assert orjson.loads(queue[0][1]) == msg['force_cost_session_ids']


@pytest.mark.parametrize('publication_tree', ['codex', 'claude_code'], indirect=True)
@pytest.mark.parametrize('outcome', ['applied', 'superseded', 'cancelled'])
def test_periodic_candidate_waits_for_real_compute_cleanup(publication_tree, monkeypatch, settings, outcome):
    tree = publication_tree
    root, _, _, _, candidate, path, msg = tree
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    prepared, release_prepare, cleanup, release_cleanup = [threading.Event() for _ in range(4)]
    phases, published, operations, atomic_completions = Counter(), [], [], []
    from twicc.providers import statistics_queue
    append_roots = statistics_queue.append_roots

    def append_in_final(*args, **kwargs):
        append_roots(*args, **kwargs)
        assert connections['default'].in_atomic_block
        assert Session.objects.get(pk=root.id).compute_version == msg['session_fields']['compute_version']
        assert stored(path, 'SELECT compute_version FROM core_session WHERE id=?', [root.id]) == [(None,)]
        assert not stored(path, 'SELECT root_session_id FROM core_statisticsqueueentry')
        transaction.on_commit(lambda: atomic_completions.append(root.id))

    monkeypatch.setattr(statistics_queue, 'append_roots', append_in_final)
    clock = [0]
    original_prepare = extraction.prepare_compute_batch
    original_publish = extraction.publish_compute_batch
    original_close = extraction.close_compute_application
    original_compress = maintenance.prepare_compression_batch
    original_maintenance_publish = maintenance._publish
    loop = None
    maintenance_stop = None
    compute_queued = asyncio.Event()
    runs = []

    def prepare(operation):
        if operation.phase == 'history_insert' and operation.cursor >= 0 and not prepared.is_set():
            operations.append(operation)
            prepared.set()
            assert release_prepare.wait(8)
        return original_prepare(operation)

    def publish(operation, batch, active):
        result = original_publish(operation, batch, active)
        phases[batch.phase] += 1
        source = Session.objects.get(pk=root.id)
        assert not history_facts.history_facts_are_current(source)
        # Even partially replaced durable facts never become resolver history.
        assert next(history_facts.iter_resolver_items(root.id, HistoryFactKind.TOOL_CALL)).line_num == 600
        return result

    def close(operation):
        cleanup.set()
        assert release_cleanup.wait(8)
        return original_close(operation)

    def configure(function, *args, **kwargs):
        connections['default'].settings_dict = {**connections['default'].settings_dict, 'NAME': str(path)}
        return function(*args, **kwargs)

    def compression(*args, **kwargs):
        result = configure(original_compress, *args, **kwargs)
        assert any(entry.candidate.content_id == candidate.id for entry in result[1])
        # Arrival follows actual codec preparation, before maintenance publication.
        loop.call_soon_threadsafe(queue_compute)
        assert prepared.wait(8)
        return result

    def queue_compute():
        runs.append(enqueue(msg))
        compute_queued.set()

    def maintenance_publish(entries, active, **kwargs):
        assert operations[0].closed
        assert not db_writer.has_pending_source_applications()
        published.extend(entries)
        result = original_maintenance_publish(entries, active, **kwargs)
        loop.call_soon_threadsafe(maintenance_stop.set)
        return result

    async def wait(stop, wake, timeout):
        if clock[0] == 0:
            clock[0] = maintenance.INITIAL_DELAY
            return False
        return await original_wait(stop, wake, timeout)

    original_wait = maintenance._wait
    monkeypatch.setattr(extraction, 'prepare_compute_batch', prepare)
    monkeypatch.setattr(extraction, 'publish_compute_batch', publish)
    monkeypatch.setattr(extraction, 'close_compute_application', close)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_publish', maintenance_publish)
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    monkeypatch.setattr(maintenance, '_wait', wait)
    # The codec worker and publication worker use the same isolated WAL database.
    monkeypatch.setattr(maintenance, 'pending_root_page', lambda *args: [])
    monkeypatch.setattr(compute_executor, 'run_compute_sync', db_writer.run_compute_sync)

    async def scenario():
        nonlocal loop, maintenance_stop
        loop = asyncio.get_running_loop()
        maintenance_stop = asyncio.Event()
        db_writer.start_db_writer()
        task = asyncio.create_task(maintenance.start_content_maintenance_task(maintenance_stop, compute_work=ComputeWorkState()))
        try:
            await asyncio.wait_for(compute_queued.wait(), 5)
            assert await asyncio.to_thread(prepared.wait, 3)
            heartbeat = asyncio.Event()
            loop.call_soon(heartbeat.set)
            await asyncio.wait_for(heartbeat.wait(), .5)
            assert not published
            assert stored(path, 'SELECT count(*) FROM core_sessionhistoryfact WHERE session_id=?', [root.id]) == [(128,)]
            committed_phases = phases.copy()
            future = loop.create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {root.id: {'title': 'foreground'}}, future))
            assert await asyncio.wait_for(future, 1) == 1
            assert phases == committed_phases, 'Foreground commits before the next compute publication'
            if outcome == 'superseded':
                with closing(sqlite3.connect(path)) as database, database:
                    database.execute('UPDATE core_session SET last_offset=1 WHERE id=?', [root.id])
            if outcome == 'cancelled':
                db_writer._db_writer_task.cancel()
                await asyncio.sleep(0)
                assert not published and db_writer.has_pending_source_applications()
                assert not operations[0].closed
            release_prepare.set()
            assert await asyncio.to_thread(cleanup.wait, 5)
            assert not published and db_writer.has_pending_source_applications()
            assert not operations[0].closed
            release_cleanup.set()
            await asyncio.wait_for(task, 6)
        finally:
            release_prepare.set()
            release_cleanup.set()
            maintenance_stop.set()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
        assert operations[0].closed and not db_writer.has_pending_source_applications()
        assert published
        assert stored(path, 'SELECT codec FROM core_sessionitemcontent WHERE id=?', [candidate.id]) == [('zstd',)]
        _, done, applied = runs[0]
        signal = applied.get_nowait()
        if outcome == 'cancelled':
            assert signal.outcome == 'failed' and signal.error == 'Compute application cancelled'
        else:
            assert done.done() and done.result() == 0
            assert signal.outcome == outcome
        assert applied.empty()
        if outcome == 'applied':
            assert phases['stopped_agents'] >= 2 and phases['history_insert'] >= 3 and phases['validation'] >= 3
            assert atomic_completions == [root.id]
        else:
            assert stored(path, 'SELECT compute_version FROM core_session WHERE id=?', [root.id]) == [(None,)]
            assert not stored(path, 'SELECT root_session_id FROM core_statisticsqueueentry')
            assert atomic_completions == []

    asyncio.run(scenario())
    if outcome == 'applied':
        assert_complete(tree)


@pytest.mark.parametrize('publication_tree', ['codex', 'claude_code'], indirect=True)
@pytest.mark.parametrize('interrupt', ['stopped_agents', 'history_delete', 'history_insert', 'validation'])
def test_new_writer_lifetime_rebuilds_committed_partial_source(publication_tree, monkeypatch, interrupt):
    tree = publication_tree
    root, children, _, _, _, path, msg = tree
    committed, release = threading.Event(), threading.Event()
    publish = extraction.publish_compute_batch
    captured = []

    def publish_then_pause(operation, batch, active):
        result = publish(operation, batch, active)
        if batch.phase == interrupt and batch.rows and not committed.is_set():
            captured.append(operation)
            committed.set()
            assert release.wait(8)
        return result

    monkeypatch.setattr(extraction, 'publish_compute_batch', publish_then_pause)

    async def scenario():
        db_writer.start_db_writer()
        run_id, _, applied = enqueue(msg)
        abandoned = None
        try:
            assert await asyncio.to_thread(committed.wait, 5)
            assert stored(path, 'SELECT compute_version FROM core_session WHERE id=?', [root.id]) == [(None,)]
            assert not stored(path, 'SELECT root_session_id FROM core_statisticsqueueentry')
            abandoned = asyncio.create_task(db_writer.abandon_compute_run(run_id, msg['provider']))
            await asyncio.sleep(0)
            assert not captured[0].closed
            assert db_writer.has_pending_source_applications()
            release.set()
            await asyncio.wait_for(abandoned, 5)
        finally:
            release.set()
            if abandoned is not None:
                await abandoned
            await db_writer.stop_db_writer()
        assert captured[0].closed and not db_writer.has_pending_source_applications()
        assert applied.empty()
        # The second lifetime retains committed DB rows and current descendants.
        target = msg['session_fields']['compute_version']
        assert stored(path, 'SELECT DISTINCT compute_version FROM core_session WHERE type=\'subagent\'') == [(target,)]
        before = stored(path, 'SELECT id,items_cost_computed_through_at FROM core_session WHERE type=\'subagent\' ORDER BY id')
        assert len(before) == len(children) and all(value for _, value in before)
        monkeypatch.setattr(extraction, 'publish_compute_batch', publish)
        db_writer.start_db_writer()
        try:
            _, done, applied = enqueue(msg)
            signal = await asyncio.wait_for(applied.get(), 7)
            assert signal.outcome == 'applied' and signal.completed_root_id == root.id
            assert captured[0].history_facts_stage is None or captured[0].history_facts_stage.closed
            assert await asyncio.wait_for(done, 3) == 0
        finally:
            await db_writer.stop_db_writer()
        assert not db_writer.has_pending_source_applications()
        assert stored(path, 'SELECT id,items_cost_computed_through_at FROM core_session WHERE type=\'subagent\' ORDER BY id') == before

    asyncio.run(scenario())
    assert_complete(tree)


@pytest.mark.parametrize('publication_tree', ['codex', 'claude_code'], indirect=True)
def test_final_metadata_failure_leaves_partial_source_outdated(publication_tree, monkeypatch):
    tree = publication_tree
    root, _, _, _, _, path, msg = tree
    final = BaseSessionCompute._apply_session_complete_final
    msg['session_fields']['invalid_field'] = True
    captured = []

    def fail_final(message):
        operation = db_writer._pending_compute.operation
        assert operation.history_facts_published and operation.validation.complete
        captured.append(operation)
        # The invalid field fails inside the real final Atomic transaction.
        return final(message)

    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(fail_final))

    async def scenario():
        db_writer.start_db_writer()
        try:
            _, done, applied = enqueue(msg)
            assert (await asyncio.wait_for(applied.get(), 7)).outcome == 'failed'
            assert await asyncio.wait_for(done, 3) == 1
        finally:
            await db_writer.stop_db_writer()
        assert captured[0].closed and not db_writer.has_pending_source_applications()

    asyncio.run(scenario())
    assert stored(path, 'SELECT compute_version,content_storage_version,plaintext_verified_epoch '
                  'FROM core_session WHERE id=?', [root.id]) == [(None, None, None)]
    assert not stored(path, 'SELECT root_session_id FROM core_statisticsqueueentry')
    assert stored(path, 'SELECT count(*) FROM core_sessionhistoryfact WHERE session_id=?', [root.id]) == [(257,)]
