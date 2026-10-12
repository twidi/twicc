"""Heavy database work must leave Channels' shared executor available."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from channels.db import database_sync_to_async
from django.db import connections
from django.db.models import Sum

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem
from twicc.providers import db_writer
from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult


def _blocked_work(started: threading.Event, release: threading.Event):
    started.set()
    assert release.wait(5), "test did not release the compute worker"


async def _shared_cleanup_completes_while_blocked(started):
    assert await asyncio.to_thread(started.wait, 2), "compute work did not start"
    return await asyncio.wait_for(database_sync_to_async(lambda: "channels-ready")(), 0.5)


@pytest.mark.django_db(transaction=True)
def test_heavy_work_does_not_block_channels_cleanup(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def apply(_message):
        _blocked_work(started, release)
        return ComputeApplyResult("superseded")

    monkeypatch.setattr(BaseSessionCompute, "apply_session_complete", staticmethod(apply))

    async def scenario():
        db_writer.start_db_writer()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0)
        task = asyncio.create_task(db_writer._process_compute_message({
            "type": "session_complete", "provider": Provider.CODEX.value,
            "run_id": run_id, "session_id": "heavy-session",
        }))
        try:
            assert await _shared_cleanup_completes_while_blocked(started) == "channels-ready"
        finally:
            release.set()
            await task
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_db_writer()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["completed", "abandoned"])
def test_final_apply_aggregate_does_not_block_channels_or_repeat_at_finalization(monkeypatch, path):
    started = threading.Event()
    release = threading.Event()
    project = Project.objects.create(id='aggregate-project')
    session = Session.objects.create(id='aggregate-session', project=project, provider=Provider.CODEX,
                                     created_at=datetime(2026, 9, 29, tzinfo=UTC))
    calls = []
    original = DailyActivity.recalculate
    def recalculate(project_id, day, provider):
        calls.append((project_id, day, provider))
        _blocked_work(started, release)
        original(project_id, day, provider)
    monkeypatch.setattr(DailyActivity, 'recalculate', staticmethod(recalculate))

    async def no_broadcast(_session_id):
        pass
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', no_broadcast)

    async def scenario():
        db_writer.start_db_writer()
        run_id, future = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0)
        task = asyncio.create_task(db_writer._process_compute_message({
            'type': 'session_complete', 'provider': Provider.CODEX.value,
            'run_id': run_id, 'session_id': session.id, 'history_facts': [],
            'observed_last_offset': 0,
        }))
        try:
            assert await _shared_cleanup_completes_while_blocked(started) == 'channels-ready'
        finally:
            release.set()
            await task
            count = len(calls)
            assert count == 2  # Project and global buckets, maintained in final apply.
            assert db_writer._compute_states[run_id].failed_count == 0
            if path == 'completed':
                await db_writer._finalize_compute_run(run_id, Provider.CODEX)
                assert future.done() and future.result() == 0
            else:
                await db_writer._finalize_abandoned_run(run_id, Provider.CODEX)
            assert len(calls) == count
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_compute_run_without_private_proof_repairs_activity_for_each_result(monkeypatch):
    project = Project.objects.create(id='shared-activity-project')
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    sessions = [Session.objects.create(id=f'activity-{index}', project=project, provider=Provider.CODEX,
                                       file_path=f'activity-{index}.jsonl', created_at=stamp, user_message_count=1)
                for index in (1, 2)]
    for session in sessions:
        SessionItem.objects.create(session=session, line_num=1, content='{}',
                                   timestamp=stamp, kind=ItemKind.USER_MESSAGE)
    with connections['default'].cursor() as cursor:
        cursor.execute('UPDATE core_sessionitem SET cost=0.0000006 WHERE session_id IN (%s, %s)',
                       [session.id for session in sessions])
    calls = []
    original = DailyActivity.recalculate

    def counted(project_id, day, provider):
        calls.append((project_id, day, provider))
        return original(project_id, day, provider)

    monkeypatch.setattr(DailyActivity, 'recalculate', staticmethod(counted))

    async def no_broadcast(_session_id):
        pass

    monkeypatch.setattr(db_writer, 'broadcast_session_updated', no_broadcast)

    async def scenario():
        db_writer.start_compute_executor()
        run_id, _ = db_writer.arm_compute_completion(Provider.CODEX, display_session_ids=set(), total_display=0)
        try:
            for session in sessions:
                await db_writer._process_compute_message({
                    'type': 'session_complete', 'provider': Provider.CODEX.value,
                    'run_id': run_id, 'session_id': session.id, 'history_facts': [],
                    'observed_last_offset': 0,
                })
            assert db_writer._compute_states[run_id].failed_count == 0
        finally:
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_compute_executor()

    asyncio.run(scenario())
    assert len(calls) == 4  # Compatibility results have no private preparation proof.
    assert {row.session_count for row in DailyActivity.objects.all()} == {2}
    for scope in (project.id, None):
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT SUM(cost) FROM core_dailyactivity WHERE project_id IS %s', [scope])
            assert cursor.fetchone()[0] == pytest.approx(.0000012, abs=1e-15)
        assert DailyActivity.objects.filter(project_id=scope).aggregate(cost=Sum('cost'))['cost'] == Decimal('.0000012')


@pytest.mark.django_db(transaction=True)
def test_stop_drains_closes_worker_connection_and_allows_restart():
    started = threading.Event()
    release = threading.Event()
    close_threads = []

    def query_connection():
        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone() == (1,)
        assert connection in connections.all(initialized_only=True)
        assert not connection.in_atomic_block
        original_close = connection.close

        def record_close():
            close_threads.append(threading.current_thread())
            original_close()

        connection.close = record_close
        _blocked_work(started, release)
        return threading.current_thread(), connection

    async def scenario():
        db_writer.start_db_writer()
        try:
            job = asyncio.create_task(db_writer.run_compute_sync(query_connection))
            assert await asyncio.to_thread(started.wait, 2)
            stop = asyncio.create_task(db_writer.stop_db_writer())
            await asyncio.sleep(0)
            assert not stop.done()
            release.set()
            first_thread, first_connection = await job
            await stop
        finally:
            release.set()
            await db_writer.stop_db_writer()
        assert not first_thread.is_alive()
        # Django intentionally keeps an in-memory SQLite connection open when
        # close() is called. The close callback and worker join are observable.
        assert len(close_threads) >= 2
        assert all(thread is first_thread for thread in close_threads)
        if not first_connection.is_in_memory_db():
            assert first_connection.connection is None

        db_writer.start_db_writer()
        try:
            second_thread, second_connection = await db_writer.run_compute_sync(
                lambda: (threading.current_thread(), connections["default"])
            )
            assert second_thread is not first_thread
            assert second_connection is not first_connection
        finally:
            await db_writer.stop_db_writer()

    asyncio.run(scenario())


def test_cancelled_writer_stop_finishes_worker_shutdown_before_raising():
    started = threading.Event()
    release = threading.Event()

    async def scenario():
        db_writer.start_db_writer()
        job = asyncio.create_task(db_writer.run_under_db_write_lock(
            lambda: db_writer.run_compute_sync(_blocked_work, started, release)
        ))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            stop = asyncio.create_task(db_writer.stop_db_writer())
            await asyncio.sleep(0)
            stop.cancel()
            await asyncio.sleep(0)
            assert not stop.done()
        finally:
            release.set()
        await job
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert db_writer._db_writer_task is None
        assert db_writer._db_write_lock is None
        db_writer.start_db_writer()
        await db_writer.stop_db_writer()

    asyncio.run(scenario())


def test_compute_executor_cannot_restart_or_finish_second_stop_during_shutdown():
    started = threading.Event()
    release = threading.Event()

    async def scenario():
        db_writer.start_compute_executor()
        job = asyncio.create_task(db_writer.run_compute_sync(_blocked_work, started, release))
        assert await asyncio.to_thread(started.wait, 2)
        first_stop = asyncio.create_task(db_writer.stop_compute_executor())
        await asyncio.sleep(0)
        second_stop = None
        try:
            with pytest.raises(RuntimeError, match="already started"):
                db_writer.start_compute_executor()
            second_stop = asyncio.create_task(db_writer.stop_compute_executor())
            await asyncio.sleep(0)
            assert not second_stop.done()
        finally:
            release.set()
            await job
            await first_stop
            if second_stop is not None:
                await second_stop
            await db_writer.stop_compute_executor()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_application_worker_pins_temp_connection_and_copies_context(tmp_path):
    from contextvars import ContextVar
    from twicc.providers import compute_executor as executor
    marker = ContextVar('application_test_marker', default='empty')

    def create():
        connection = connections['default']
        assert connection.settings_dict['CONN_MAX_AGE'] == 0
        # A file-backed worker connection makes Django close() effective.
        # In-memory test connections deliberately ignore close().
        connection.settings_dict = {**connection.settings_dict, 'NAME': str(tmp_path / 'pinned.sqlite3')}
        with connection.cursor() as cursor:
            cursor.execute('CREATE TEMP TABLE pinned_test (value INTEGER)')
            cursor.execute('INSERT INTO pinned_test VALUES (7)')
        marker.set('worker-change')
        return connection.connection

    def read():
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT value FROM pinned_test')
            assert cursor.fetchone() == (7,)
        return connections['default'].connection, marker.get()

    async def scenario():
        executor.start_compute_application_executor()
        try:
            marker.set('caller')
            physical = await executor.run_compute_application_sync(create)
            assert marker.get() == 'caller'
            assert await executor.run_compute_application_sync(read) == (physical, 'caller')
        finally:
            await executor.stop_compute_application_executor()
    asyncio.run(scenario())


def test_application_preparation_does_not_block_writer_and_shutdown_drains():
    from twicc.providers import compute_executor as executor
    started, release = threading.Event(), threading.Event()

    async def scenario():
        executor.start_compute_executor()
        executor.start_compute_application_executor()
        job = asyncio.create_task(executor.run_compute_application_sync(_blocked_work, started, release))
        stop = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert await asyncio.wait_for(executor.run_compute_sync(lambda: 'writer-ready'), .5) == 'writer-ready'
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            stop = asyncio.create_task(executor.stop_compute_application_executor())
            await asyncio.sleep(0)
            stop.cancel()
            await asyncio.sleep(0)
            assert not stop.done()
            with pytest.raises(RuntimeError, match='already started'):
                executor.start_compute_application_executor()
        finally:
            release.set()
            if stop is not None:
                with pytest.raises(asyncio.CancelledError):
                    await stop
            await executor.stop_compute_application_executor()
            await executor.stop_compute_executor()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_application_terminal_cleanup_runs_on_worker_after_broken_transaction():
    from django.db import IntegrityError, transaction
    from twicc.core.content_storage.references import SessionReferenceOperation
    from twicc.providers import compute_executor as executor
    close_threads = []

    def broken():
        project = Project.objects.create(id='broken-worker')
        session = Session.objects.create(id='broken-worker', project=project)
        operation = SessionReferenceOperation(session.id, 500000, 2)
        operation.initialize()
        SessionItem.objects.create(session=session, line_num=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            try:
                SessionItem.objects.create(session=session, line_num=1)
            finally:
                assert connections['default'].needs_rollback
                operation.close()  # Skip SQL inside the broken transaction.
        connection = connections['default']
        original_close = connection.close
        def close():
            close_threads.append(threading.get_ident())
            original_close()
        connection.close = close
        executor.close_compute_application_connection()
        # Django intentionally keeps shared in-memory test connections open.
        if connection.is_in_memory_db():
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS {operation.table}')
        return threading.get_ident()

    async def scenario():
        executor.start_compute_application_executor()
        try:
            worker = await executor.run_compute_application_sync(broken)
            assert close_threads == [worker]
            with pytest.raises(RuntimeError, match='requires its worker'):
                executor.close_compute_application_connection()
        finally:
            await executor.stop_compute_application_executor()
    asyncio.run(scenario())
