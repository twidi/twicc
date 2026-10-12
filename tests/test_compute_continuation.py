"""Real WAL compute continuation barriers and terminal ordering."""

import asyncio
import queue
import sqlite3
import threading
from uuid import uuid4

import pytest
from django.db import connection, connections

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.core.content_storage import extraction
from twicc.providers import db_writer, compute_executor

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def wal_runtime(tmp_path, monkeypatch):
    project = Project.objects.create(id='continuation-project')
    session = Session.objects.create(id='continuation', project=project, provider='codex', last_line=600,
                                     storage_incarnation=str(uuid4()))
    SessionItem.objects.bulk_create([SessionItem(session=session, line_num=n, content=f'legacy {n}')
                                    for n in range(1, 601)])
    path = tmp_path / 'continuation.sqlite3'
    destination = sqlite3.connect(path)
    connection.connection.backup(destination)
    destination.execute('PRAGMA journal_mode=WAL')
    destination.close()

    def configure(function, args, kwargs):
        db = connections['default']
        db.settings_dict = {**db.settings_dict, 'NAME': str(path)}
        return function(*args, **kwargs)

    ordinary = db_writer.run_compute_sync
    application = compute_executor.run_compute_application_sync

    async def writer(function, /, *args, **kwargs):
        return await ordinary(configure, function, args, kwargs)

    async def pinned(function, /, *args, **kwargs):
        return await application(configure, function, args, kwargs)

    monkeypatch.setattr(db_writer, 'run_compute_sync', writer)
    monkeypatch.setattr(compute_executor, 'run_compute_application_sync', pinned)
    # Runtime imports the same exported runner in the revised implementation.
    monkeypatch.setattr(db_writer, 'run_compute_application_sync', pinned, raising=False)
    monkeypatch.setattr(db_writer._mp_ctx, 'Queue', lambda **kwargs: queue.Queue())

    async def no_broadcast(*args, **kwargs):
        pass

    monkeypatch.setattr(db_writer, 'broadcast_session_updated', no_broadcast)
    monkeypatch.setattr(db_writer, 'broadcast_startup_progress', no_broadcast)
    return session, path


def result_message(session, run_id):
    return {'type': 'session_complete', 'provider': 'codex', 'run_id': run_id,
            'session_id': session.id, 'observed_last_offset': 0,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': 0,
            'observed_upper_line': 600, 'history_facts': [], 'session_fields': {'compute_version': 123}}


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


def test_queued_foreground_pressure_precedes_background_grant(wal_runtime):
    async def scenario():
        db_writer.start_db_writer()
        try:
            future = asyncio.get_running_loop().create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {'continuation': {'title': 'foreground'}}, future))
            lease = db_writer.get_write_coordinator().try_background_lease()
            if lease is not None:
                lease.release()
            assert lease is None
            assert await future == 1
            await wait_until(lambda: not db_writer.get_write_coordinator().demands)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_blocked_preparation_leaves_foreground_worker_available(wal_runtime, monkeypatch):
    session, path = wal_runtime
    started, release = threading.Event(), threading.Event()
    original = extraction.prepare_compute_batch

    def prepare(operation):
        if not started.is_set():
            started.set()
            assert release.wait(5)
        return original(operation)

    monkeypatch.setattr(extraction, 'prepare_compute_batch', prepare)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            future = asyncio.get_running_loop().create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {session.id: {'title': 'queued'}}, future))
            assert await asyncio.wait_for(asyncio.shield(future), .5) == 1
            assert applied.empty()
            async def watcher():
                return await db_writer.run_compute_sync(lambda: Session.objects.filter(pk=session.id).update(pinned='always'))
            assert await asyncio.wait_for(db_writer.run_under_db_write_lock(watcher), .5) == 1
            assert not done.done()
        finally:
            release.set()
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            await db_writer.stop_db_writer()
        assert applied.get_nowait() == db_writer.ComputeApplied(session.id, 'applied')
        assert applied.empty()
        assert done.result() == 0
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT title, pinned, compute_version FROM core_session WHERE id=?', (session.id,)).fetchone() == ('queued', 'always', 123)
    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['abandoned', 'shutdown', 'cancelled'])
def test_preparation_terminal_drains_before_deferral_release(wal_runtime, monkeypatch, terminal):
    from twicc import content_maintenance_task as maintenance
    session, _ = wal_runtime
    started, release = threading.Event(), threading.Event()
    original = extraction.prepare_compute_batch
    cleanup_calls = []
    close = extraction.close_compute_application

    def prepare(operation):
        started.set()
        assert release.wait(5)
        return original(operation)

    def cleanup(operation):
        assert release.is_set()
        assert any(token.root_id == session.id for token in maintenance._promotion_deferrals)
        cleanup_calls.append(operation)
        return close(operation)

    monkeypatch.setattr(extraction, 'prepare_compute_batch', prepare)
    monkeypatch.setattr(extraction, 'close_compute_application', cleanup)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        action = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert len(maintenance._promotion_deferrals) == 1
            if terminal == 'abandoned':
                action = asyncio.create_task(db_writer.abandon_compute_run(run_id, Provider.CODEX))
            elif terminal == 'shutdown':
                action = asyncio.create_task(db_writer.stop_db_writer())
                await asyncio.sleep(0)
                action.cancel()
            else:
                db_writer._db_writer_task.cancel()
                action = asyncio.create_task(db_writer.stop_db_writer())
            await asyncio.sleep(.02)
            assert len(maintenance._promotion_deferrals) == 1
            assert applied.empty()
            if terminal != 'cancelled':
                assert not action.done()
        finally:
            release.set()
            if action is not None:
                try:
                    await action
                except asyncio.CancelledError:
                    pass
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
        assert len(cleanup_calls) == 1
        assert not maintenance._promotion_deferrals
        if terminal == 'shutdown':
            assert applied.get_nowait().outcome == 'applied'
        elif terminal == 'abandoned':
            assert applied.empty()
        else:
            assert applied.get_nowait() == db_writer.ComputeApplied(session.id, 'failed', 'Compute application cancelled')
        assert applied.empty()
    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['superseded', 'failed'])
def test_partial_storage_survives_terminal_without_final_version(wal_runtime, monkeypatch, terminal):
    from django.db import transaction
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc import content_maintenance_task as maintenance
    session, path = wal_runtime
    first_slice, release = threading.Event(), threading.Event()
    prepare = extraction.prepare_compute_batch
    published = extraction.publish_compute_batch

    def publication(operation, batch, active):
        assert len(maintenance._promotion_deferrals) == 1
        result = published(operation, batch, active)
        if batch.phase == 'extraction' and not batch.done:
            first_slice.set()
        return result

    def preparation(operation):
        if terminal == 'superseded' and first_slice.is_set():
            assert release.wait(5)
        return prepare(operation)

    def failed_final(msg):
        with transaction.atomic():
            Session.objects.filter(pk=session.id).update(compute_version=123)
            raise RuntimeError('final rollback')

    monkeypatch.setattr(extraction, 'publish_compute_batch', publication)
    monkeypatch.setattr(extraction, 'prepare_compute_batch', preparation)
    if terminal == 'failed':
        monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(failed_final))

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        try:
            assert await asyncio.to_thread(first_slice.wait, 2)
            if terminal == 'superseded':
                async def replace():
                    return await db_writer.run_compute_sync(lambda: Session.objects.filter(pk=session.id).update(history_epoch=1))
                await db_writer.run_under_db_write_lock(replace)
            release.set()
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await asyncio.wait_for(done, 3) == (1 if terminal == 'failed' else 0)
        finally:
            release.set()
            await db_writer.stop_db_writer()
        signal = applied.get_nowait()
        assert signal.outcome == terminal
        assert signal.error == ('final rollback' if terminal == 'failed' else None)
        assert applied.empty()
        assert not maintenance._promotion_deferrals
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT compute_version FROM core_session WHERE id=?', (session.id,)).fetchone()[0] != 123
            count = db.execute('SELECT COUNT(*) FROM core_sessionitem WHERE session_id=? AND current_content_id IS NOT NULL', (session.id,)).fetchone()[0]
            assert 0 < count <= 600
            assert db.execute("SELECT COUNT(*) FROM core_sessionitem WHERE current_content_id IS NOT NULL AND content <> ''").fetchone()[0] == 0
    asyncio.run(scenario())


def test_writer_start_failure_closes_owned_executors(monkeypatch):
    def fail():
        raise RuntimeError('application startup failed')
    original = db_writer.start_compute_application_executor
    monkeypatch.setattr(db_writer, 'start_compute_application_executor', fail)

    async def scenario():
        try:
            with pytest.raises(RuntimeError, match='application startup failed'):
                db_writer.start_db_writer()
            await wait_until(lambda: compute_executor._executor is None)
            assert db_writer._db_writer_task is None
            assert compute_executor._application_executor is None
            monkeypatch.setattr(db_writer, 'start_compute_application_executor', original)
            db_writer.start_db_writer()
        finally:
            await db_writer.stop_db_writer()
            await compute_executor.stop_compute_executor()
            await compute_executor.stop_compute_application_executor()
    asyncio.run(scenario())


@pytest.mark.parametrize('commit', [True, False])
def test_repeated_cancel_during_final_publication_drains_actual_outcome(wal_runtime, monkeypatch, commit):
    from twicc import content_maintenance_task as maintenance
    from twicc.providers.compute_base import BaseSessionCompute
    session, path = wal_runtime
    started, release = threading.Event(), threading.Event()
    original = BaseSessionCompute._apply_session_complete_final

    def apply(msg):
        started.set()
        assert release.wait(5)
        if not commit:
            raise RuntimeError('admitted final failed')
        return original(msg)

    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(apply))

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                    applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            writer = db_writer._db_writer_task
            writer.cancel()
            await asyncio.sleep(.01)
            writer.cancel()
            await asyncio.sleep(.01)
            assert not writer.done()
            assert applied.empty()
            assert len(maintenance._promotion_deferrals) == 1
        finally:
            release.set()
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
        ack = applied.get_nowait()
        assert ack.outcome == ('applied' if commit else 'failed')
        assert ack.error == (None if commit else 'Compute application cancelled')
        assert applied.empty()
        assert not maintenance._promotion_deferrals
        with sqlite3.connect(path) as db:
            version = db.execute('SELECT compute_version FROM core_session WHERE id=?', (session.id,)).fetchone()[0]
            assert (version == 123) == commit
    asyncio.run(scenario())


def test_ready_batch_waits_for_foreground_and_tick_does_not_spin(wal_runtime, monkeypatch):
    from twicc.providers.write_coordinator import WritePriority, write_priority
    from twicc import content_maintenance_task as maintenance
    session, _ = wal_runtime
    prepared, release = threading.Event(), threading.Event()
    original = extraction.prepare_compute_batch
    publications = []
    publish = extraction.publish_compute_batch
    register = maintenance.defer_background_promotions

    def prepare(operation):
        assert write_priority.get() is WritePriority.BACKGROUND
        batch = original(operation)
        if batch.phase == 'extraction' and not prepared.is_set():
            prepared.set()
            assert release.wait(5)
        return batch

    def publication(operation, batch, active):
        if batch.phase == 'extraction':
            publications.append(Session.objects.get(pk=session.id).title)
        return publish(operation, batch, active)

    def defer(root_id, incarnation):
        coordinator = db_writer.get_write_coordinator()
        from twicc.providers.write_coordinator import WriteResources
        assert coordinator.locked(WriteResources.ADMISSION | WriteResources.WRITER)
        assert write_priority.get() is WritePriority.BACKGROUND
        return register(root_id, incarnation)

    monkeypatch.setattr(extraction, 'prepare_compute_batch', prepare)
    monkeypatch.setattr(extraction, 'publish_compute_batch', publication)
    monkeypatch.setattr(maintenance, 'defer_background_promotions', defer)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        held = None
        try:
            assert await asyncio.to_thread(prepared.wait, 2)
            held = db_writer.get_db_write_lock()
            await held.acquire()
            # A waiting preparation is idle work, not a busy tick.
            assert not await db_writer._drain_one()
            async def foreground():
                return await db_writer.run_compute_sync(lambda: Session.objects.filter(pk=session.id).update(title='first'))
            waiter = asyncio.create_task(db_writer.run_under_db_write_lock(foreground))
            await asyncio.sleep(0)
            preparation_task = db_writer._pending_compute.preparation
            release.set()
            await wait_until(preparation_task.done)
            assert not await db_writer._drain_one()
            assert publications == []
            held.release()
            held = None
            assert await waiter == 1
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await asyncio.wait_for(done, 3) == 0
        finally:
            release.set()
            if held is not None:
                held.release()
            await db_writer.stop_db_writer()
        assert publications and set(publications) == {'first'}
        assert applied.get_nowait().outcome == 'applied'
    asyncio.run(scenario())


@pytest.mark.parametrize('bypass', [False, True])
def test_child_compute_defers_existing_root_recovery_and_binding_targets(
    wal_runtime, monkeypatch, settings, content_row_factory, bypass,
):
    from twicc import content_maintenance_task as maintenance
    from twicc.providers.write_coordinator import WritePriority, write_priority
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    root, path = wal_runtime
    recovery_prepared, release_recovery = threading.Event(), threading.Event()
    compute_paused, release_compute = threading.Event(), threading.Event()
    scans, promotions = [], []
    original_prepare = maintenance.prepare_promotion_batch
    original_apply = maintenance.apply_promotion_batch
    original_compute_prepare = extraction.prepare_compute_batch
    original_publish = extraction.publish_compute_batch
    slice_count = []
    original_database = maintenance.database_sync_to_async

    def configure(function, args, kwargs):
        db = connections['default']
        db.settings_dict = {**db.settings_dict, 'NAME': str(path)}
        return function(*args, **kwargs)

    def database(function, **options):
        async def call(*args, **kwargs):
            return await original_database(configure, **options)(function, args, kwargs)
        return call

    monkeypatch.setattr(maintenance, 'database_sync_to_async', database)
    monkeypatch.setattr(compute_executor, 'run_compute_sync', db_writer.run_compute_sync)

    def fixture():
        child = Session.objects.create(id='continuation-child', project_id=root.project_id, provider='codex',
                                       type='subagent', parent_session_id=root.id, last_line=600, file_path=str(uuid4()),
                                       storage_incarnation=str(uuid4()))
        SessionItem.objects.filter(session_id=root.id).update(session_id=child.id)
        row = content_row_factory('compressed root ' * 1000, True, session_ids=[root.id])
        SessionItem.objects.create(session_id=root.id, line_num=1, current_content=row, content='')
        Session.objects.filter(pk=root.id).update(last_line=1, content_storage_version=1, content_state_epoch=0)
        return child

    def prepare(snapshot, *args):
        scans.append(getattr(snapshot, 'session_id', snapshot.root_id))
        batch = original_prepare(snapshot, *args)
        if not recovery_prepared.is_set():
            recovery_prepared.set()
            assert release_recovery.wait(5)
        return batch

    def promotion(batch, active, **kwargs):
        promotions.append((getattr(batch.snapshot, 'session_id', batch.snapshot.root_id),
                           getattr(batch.snapshot, 'recovery', False), write_priority.get()))
        return original_apply(batch, active, **kwargs)

    def compute_prepare(operation):
        if slice_count and not compute_paused.is_set():
            compute_paused.set()
            assert release_compute.wait(5)
        return original_compute_prepare(operation)

    def compute_publish(operation, batch, active):
        result = original_publish(operation, batch, active)
        if batch.phase == 'extraction' and not batch.done:
            slice_count.append(batch.cursor)
        return result

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    monkeypatch.setattr(maintenance, 'apply_promotion_batch', promotion)
    monkeypatch.setattr(extraction, 'prepare_compute_batch', compute_prepare)
    monkeypatch.setattr(extraction, 'publish_compute_batch', compute_publish)

    async def scenario():
        db_writer.start_db_writer()
        stop = asyncio.Event()
        maintenance_task = None
        applied = asyncio.Queue()
        try:
            child = await db_writer.run_compute_sync(fixture)
            maintenance_task = asyncio.create_task(maintenance.start_content_maintenance_task(stop))
            assert await asyncio.to_thread(recovery_prepared.wait, 2)
            run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                           applied_queue=applied)
            db_writer.get_subprocess_queue().put(result_message(child, run_id))
            assert await asyncio.to_thread(compute_paused.wait, 2)
            assert len(maintenance._promotion_deferrals) == 1
            release_recovery.set()
            # Let the retained recovery reach its deferral boundary.
            await asyncio.sleep(.03)
            assert scans == [root.id]
            assert promotions == []
            assert not db_writer.get_write_coordinator().demands
            if bypass:
                maintenance.request_root_promotion(root.id, priority=WritePriority.FOREGROUND)
                await wait_until(lambda: bool(promotions))
                assert promotions[0] == (root.id, False, WritePriority.FOREGROUND)
                assert len(maintenance._promotion_deferrals) == 1
            release_compute.set()
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await asyncio.wait_for(done, 3) == 0
            assert applied.get_nowait().outcome == 'applied'
            if not bypass:
                await wait_until(lambda: bool(promotions))
                assert promotions[0] == (root.id, True, WritePriority.BACKGROUND)
                assert len(slice_count) >= 2
            assert not maintenance._promotion_deferrals
        finally:
            release_recovery.set()
            release_compute.set()
            stop.set()
            if maintenance_task is not None:
                await asyncio.wait_for(maintenance_task, 3)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_cleanup_failure_closes_connection_and_releases_before_ack(wal_runtime, monkeypatch):
    from twicc import content_maintenance_task as maintenance
    session, _ = wal_runtime
    actual_close = extraction.close_compute_application
    physical_close = db_writer.close_compute_application_connection
    calls = []

    def fail_close(operation):
        actual_close(operation)
        raise RuntimeError('TEMP cleanup failed')

    def close_connection():
        physical_close()
        assert connections['default'].connection is None
        calls.append('physical close')

    class Acknowledgements(asyncio.Queue):
        def put_nowait(self, ack):
            assert calls == ['physical close']
            assert not maintenance._promotion_deferrals
            return super().put_nowait(ack)

    monkeypatch.setattr(extraction, 'close_compute_application', fail_close)
    monkeypatch.setattr(db_writer, 'close_compute_application_connection', close_connection)

    async def scenario():
        db_writer.start_db_writer()
        applied = Acknowledgements()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
        try:
            assert await asyncio.wait_for(done, 3) == 0
            assert applied.get_nowait().outcome == 'applied'  # Final commit remains authoritative.
            assert applied.empty()
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_foreground_fifo_excludes_initialization_then_background_classes_progress(wal_runtime, monkeypatch):
    session, _ = wal_runtime
    titles = []
    initialize = extraction.initialize_compute_session

    def initialization(identity):
        titles.append(Session.objects.get(pk=identity).title)
        return initialize(identity)

    monkeypatch.setattr(extraction, 'initialize_compute_session', initialization)

    async def scenario():
        db_writer.start_db_writer()
        try:
            loop = asyncio.get_running_loop()
            init = loop.create_future()
            db_writer.enqueue_async_job(db_writer.InitializeComputeStorageJob(Provider.CODEX, session.id, init))
            first, second = loop.create_future(), loop.create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {session.id: {'title': 'first'}}, first))
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {session.id: {'title': 'second'}}, second))
            db_writer.get_thread_queue().put(db_writer.MarkSessionsStalePayload(Provider.CODEX, [session.id]))
            run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0)
            db_writer.get_subprocess_queue().put(result_message(session, run_id))
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await first == 1
            assert not init.done()
            assert await second == 1
            await asyncio.wait_for(init, 2)
            assert await asyncio.wait_for(done, 3) == 0
            assert titles and set(titles) == {'second'}
            assert await db_writer.run_compute_sync(lambda: Session.objects.get(pk=session.id).stale)
            assert not db_writer.get_write_coordinator().demands
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_compute_fifo_waits_for_cleanup_and_terminal_notifications(wal_runtime, monkeypatch):
    session, _ = wal_runtime
    first_notification, release_notification = asyncio.Event(), asyncio.Event()
    begun, closed = [], []
    original_begin, original_close = extraction.begin_compute_application, extraction.close_compute_application

    def begin(msg):
        assert len(begun) == len(closed)
        begun.append(msg['session_fields']['compute_version'])
        return original_begin(msg)

    def close(operation):
        original_close(operation)
        closed.append(operation.message['session_fields']['compute_version'])

    async def notification(_identity):
        if not first_notification.is_set():
            first_notification.set()
            await release_notification.wait()

    monkeypatch.setattr(extraction, 'begin_compute_application', begin)
    monkeypatch.setattr(extraction, 'close_compute_application', close)
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', notification)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        first = result_message(session, run_id)
        second = {**first, 'session_fields': {'compute_version': 124}}
        db_writer.get_subprocess_queue().put(first)
        db_writer.get_subprocess_queue().put(second)
        db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
        try:
            await asyncio.wait_for(first_notification.wait(), 3)
            assert begun == closed == [123]
            assert applied.qsize() == 1
            assert not done.done()
            foreground = asyncio.get_running_loop().create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {session.id: {'title': 'during notification'}}, foreground))
            assert await asyncio.wait_for(foreground, .5) == 1
            release_notification.set()
            assert await asyncio.wait_for(done, 3) == 0
            assert begun == closed == [123, 124]
            assert applied.qsize() == 2
        finally:
            release_notification.set()
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_abandon_after_notification_finishes_does_not_strand_control_job(wal_runtime, monkeypatch):
    session, _ = wal_runtime
    allow_collection = False
    collect = db_writer._collect_pending_notifications
    def gated_collect(pending):
        return collect(pending) if allow_collection else False
    monkeypatch.setattr(db_writer, '_collect_pending_notifications', gated_collect)

    async def scenario():
        nonlocal allow_collection
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                     applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        try:
            # Keep terminal notifications done but uncollected until abandonment
            # is synchronously enqueued. No independent tick runs in this block.
            await wait_until(lambda: db_writer._pending_compute is not None
                             and db_writer._pending_compute.notifications is not None)
            pending = db_writer._pending_compute
            await asyncio.shield(pending.notifications)
            abandonment = asyncio.create_task(db_writer.abandon_compute_run(run_id, Provider.CODEX))
            await asyncio.sleep(0)
            allow_collection = True
            await asyncio.wait_for(abandonment, .5)
            assert run_id not in db_writer._compute_states
        finally:
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(scenario())


def test_hot_compute_reuses_compressed_row_with_original_null(wal_runtime, content_row_factory):
    session, path = wal_runtime

    def fixture():
        owner = Session.objects.create(id='compressed-owner', project_id=session.project_id, provider='codex',
                                       file_path=str(uuid4()), storage_incarnation=str(uuid4()))
        row = content_row_factory('legacy 1', True, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=1, current_content=row, content='')
        return row.id

    async def scenario():
        db_writer.start_db_writer()
        try:
            content_id = await db_writer.run_compute_sync(fixture)
            applied = asyncio.Queue()
            run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                           applied_queue=applied)
            db_writer.get_subprocess_queue().put(result_message(session, run_id))
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await asyncio.wait_for(done, 3) == 0
            assert applied.get_nowait().outcome == 'applied'
            with sqlite3.connect(path) as db:
                assert db.execute('SELECT current_content_id, original_content_id, content FROM core_sessionitem WHERE session_id=? AND line_num=1', (session.id,)).fetchone() == (content_id, None, '')
                row = db.execute('SELECT codec, payload, session_ids FROM core_sessionitemcontent WHERE id=?', (content_id,)).fetchone()
                assert row[:2] == ('plain', b'legacy 1')
                import orjson
                assert set(orjson.loads(row[2])) == {session.id, 'compressed-owner'}
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_each_publication_captures_fresh_actual_active_child_ids(wal_runtime, monkeypatch):
    from types import SimpleNamespace
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.providers.write_coordinator import WriteResources
    session, _ = wal_runtime
    active = []
    captured = []
    original = extraction.publish_compute_batch
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', lambda: list(active))

    def publish(operation, batch, actual_ids):
        captured.append((batch.phase, actual_ids))
        return original(operation, batch, actual_ids)

    def snapshot():
        coordinator = db_writer.get_write_coordinator()
        assert coordinator.locked(WriteResources.ADMISSION | WriteResources.WRITER)
        result = frozenset(info.session_id for info in active)
        if not active:
            active.append(SimpleNamespace(session_id='active-native-child'))
        return result

    monkeypatch.setattr(extraction, 'publish_compute_batch', publish)
    monkeypatch.setattr(db_writer, '_active_compute_ids', snapshot)

    async def scenario():
        db_writer.start_db_writer()
        try:
            await db_writer.run_compute_sync(lambda: Session.objects.create(
                id='active-native-child', provider='codex', project_id=session.project_id,
                type='subagent', parent_session_id=session.id, file_path=str(uuid4())))
            applied = asyncio.Queue()
            run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                           applied_queue=applied)
            db_writer.get_subprocess_queue().put(result_message(session, run_id))
            db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
            assert await asyncio.wait_for(done, 3) == 0
            assert applied.get_nowait().outcome == 'applied'
            assert captured[0][1] == frozenset()
            assert all(ids == frozenset({'active-native-child'}) for _, ids in captured[1:])
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_abandonment_between_grant_and_drive_prevents_final_publication(wal_runtime, monkeypatch):
    session, path = wal_runtime
    original = db_writer._run_with_content_activity

    async def abandon_before_factory(factory):
        pending = db_writer._pending_compute
        if pending is not None and pending.operation is not None and pending.operation.phase == 'final':
            db_writer._compute_states[pending.run_id].abandoned = True
        return await original(factory)

    monkeypatch.setattr(db_writer, '_run_with_content_activity', abandon_before_factory)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
        try:
            assert await asyncio.wait_for(done, 3) == 0
            assert applied.empty()
            with sqlite3.connect(path) as db:
                assert db.execute('SELECT compute_version FROM core_session WHERE id=?', (session.id,)).fetchone()[0] != 123
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_start_without_event_loop_creates_no_executor():
    try:
        with pytest.raises(RuntimeError, match='no running event loop'):
            db_writer.start_db_writer()
        assert compute_executor._executor is None
        assert compute_executor._application_executor is None
    finally:
        asyncio.run(db_writer.stop_db_writer())


@pytest.mark.parametrize('missing', [False, True])
def test_runtime_missing_or_malformed_result_never_calls_whole_storage_wrapper(wal_runtime, monkeypatch, missing):
    from twicc.providers.compute_base import BaseSessionCompute
    session, _ = wal_runtime

    def forbidden(_msg):
        raise AssertionError('whole-session runtime wrapper called')
    monkeypatch.setattr(BaseSessionCompute, 'apply_session_complete', staticmethod(forbidden))

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                       applied_queue=applied)
        msg = result_message(session, run_id)
        msg.pop('history_facts')
        if missing:
            msg['session_id'] = 'missing-session'
        db_writer.get_subprocess_queue().put(msg)
        db_writer.get_subprocess_queue().put({'type': 'done', 'provider': 'codex', 'run_id': run_id})
        try:
            assert await asyncio.wait_for(done, 3) == (0 if missing else 1)
            ack = applied.get_nowait()
            assert ack.outcome == ('missing' if missing else 'failed')
            assert ack.error == (None if missing else 'session_complete is missing history_facts')
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_cancelled_dequeued_foreground_job_settles_future_and_pressure(wal_runtime):
    async def scenario():
        db_writer.start_db_writer()
        lock = db_writer.get_db_write_lock()
        await lock.acquire()
        future = asyncio.get_running_loop().create_future()
        db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None, {'continuation': {'title': 'queued'}}, future))
        try:
            await wait_until(lambda: bool(db_writer.get_write_coordinator().requests))
            db_writer._db_writer_task.cancel()
            await asyncio.sleep(.01)
        finally:
            lock.release()
            await db_writer.stop_db_writer()
        assert future.done()
        assert isinstance(future.exception(), RuntimeError)
        assert not compute_executor._executor
    asyncio.run(scenario())


@pytest.mark.parametrize('forced_cancel', [False, True])
def test_notification_failure_still_settles_parked_abandonment(wal_runtime, monkeypatch, forced_cancel):
    session, _ = wal_runtime

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        async def fail_notification(*args):
            reached.set()
            await release.wait()
            raise RuntimeError('postcommit notification failed')
        monkeypatch.setattr(db_writer, 'auto_add_project_to_workspaces', fail_notification)
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                    applied_queue=applied)
        msg = {**result_message(session, run_id), 'project_id': session.project_id,
               'project_directory': '/isolated-test-directory'}
        db_writer.get_subprocess_queue().put(msg)
        abandonment = None
        try:
            await asyncio.wait_for(reached.wait(), 3)
            abandonment = asyncio.create_task(db_writer.abandon_compute_run(run_id, Provider.CODEX))
            await wait_until(lambda: bool(db_writer._pending_compute.abandon_jobs))
            writer = db_writer._db_writer_task
            if forced_cancel:
                writer.cancel()
                await asyncio.sleep(.01)
                writer.cancel()
                await asyncio.sleep(.01)
                assert not writer.done()
            release.set()
            await asyncio.wait_for(abandonment, .5)
            assert run_id not in db_writer._compute_states
            assert applied.get_nowait().outcome == 'applied'
            assert applied.empty()
            if forced_cancel:
                await wait_until(writer.done)
                assert writer.cancelled()
        finally:
            release.set()
            if abandonment is not None:
                abandonment.cancel()
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(scenario())


def test_late_abandonment_during_cancelled_preparation_drains_without_demand(wal_runtime, monkeypatch):
    from twicc import content_maintenance_task as maintenance
    session, _ = wal_runtime
    started, release = threading.Event(), threading.Event()
    prepare = extraction.prepare_compute_batch
    close_connection = db_writer.close_compute_application_connection
    closed = []

    def preparation(operation):
        started.set()
        assert release.wait(5)
        return prepare(operation)

    def close():
        assert release.is_set()
        close_connection()
        closed.append(connections['default'].connection is None)

    monkeypatch.setattr(extraction, 'prepare_compute_batch', preparation)
    monkeypatch.setattr(db_writer, 'close_compute_application_connection', close)

    async def scenario():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0,
                                                    applied_queue=applied)
        db_writer.get_subprocess_queue().put(result_message(session, run_id))
        abandonment = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            writer = db_writer._db_writer_task
            pending = db_writer._pending_compute
            writer.cancel()
            await wait_until(db_writer._db_writer_stop_event.is_set)
            abandonment = asyncio.create_task(db_writer.abandon_compute_run(run_id, Provider.CODEX))
            await wait_until(lambda: db_writer._compute_states[run_id].abandoned)
            assert not abandonment.done()
            assert not writer.done()
            assert not db_writer.get_write_coordinator().demands
            release.set()
            await asyncio.wait_for(asyncio.shield(abandonment), .5)
            await wait_until(writer.done)
            assert writer.cancelled()
            assert closed == [True]
            assert pending.preparation.done()
            assert pending.operation.closed
            assert db_writer._pending_compute is None
            assert applied.empty()
            assert not maintenance._promotion_deferrals
            assert not db_writer.get_write_coordinator().demands
            assert db_writer._async_queue.empty()
            assert run_id not in db_writer._compute_states
            await asyncio.wait_for(db_writer.stop_db_writer(), 2)
        finally:
            release.set()
            if abandonment is not None:
                abandonment.cancel()
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(scenario())


@pytest.mark.parametrize('boundary', ['workspace', 'project_lease'])
@pytest.mark.parametrize('detach', ['abandon', 'replace'])
def test_postcommit_progress_rechecks_run_after_project_await(wal_runtime, monkeypatch, boundary, detach):
    session, _ = wal_runtime

    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        progress = []
        async def workspace(*args):
            if boundary == 'workspace':
                reached.set()
                await release.wait()
        original_lock = db_writer.run_under_storage_write_lock
        async def lock(factory, **kwargs):
            if boundary == 'project_lease':
                reached.set()
                await release.wait()
            return await original_lock(factory, **kwargs)
        async def broadcast(*args, **kwargs):
            progress.append((args, kwargs))
        monkeypatch.setattr(db_writer, 'auto_add_project_to_workspaces', workspace)
        monkeypatch.setattr(db_writer, 'run_under_storage_write_lock', lock)
        monkeypatch.setattr(db_writer, 'broadcast_startup_progress', broadcast)
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=None, total_display=1,
                                                    applied_queue=applied)
        state = db_writer._compute_states[run_id]
        msg = {**result_message(session, run_id), 'project_id': session.project_id,
               'project_directory': '/isolated-test-directory'}
        db_writer.get_subprocess_queue().put(msg)
        try:
            await asyncio.wait_for(reached.wait(), 3)
            if detach == 'abandon':
                state.abandoned = True
            else:
                db_writer._compute_states[run_id] = db_writer._ComputeProviderState(
                    provider=Provider.CODEX, run_id=run_id, display_session_ids=None, total_display=1)
            release.set()
            await wait_until(lambda: db_writer._pending_compute is None)
            assert applied.get_nowait().outcome == 'applied'
            assert state.completed_count == 0
            assert state.sessions_since_project_broadcast == 0
            assert progress == []
        finally:
            release.set()
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(scenario())
