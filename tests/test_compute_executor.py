"""Heavy database work must leave Channels' shared executor available."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from channels.db import database_sync_to_async
from django.db import connections

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
def test_compute_run_reuses_exact_activity_bucket_after_first_apply(monkeypatch):
    project = Project.objects.create(id='shared-activity-project')
    stamp = datetime(2026, 9, 29, tzinfo=UTC)
    sessions = [Session.objects.create(id=f'activity-{index}', project=project, provider=Provider.CODEX,
                                       file_path=f'activity-{index}.jsonl', created_at=stamp, user_message_count=1)
                for index in (1, 2)]
    for session in sessions:
        SessionItem.objects.create(session=session, line_num=1, content='{}',
                                   timestamp=stamp, kind=ItemKind.USER_MESSAGE)
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
    assert len(calls) == 2  # Project and global are each rebuilt once.
    assert {row.session_count for row in DailyActivity.objects.all()} == {2}


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
