"""Coordinator registration, classification and deadline waits."""

import asyncio
import time

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session
from twicc.providers import background_compute_task as background


@pytest.mark.django_db(transaction=True)
def test_initial_done_waits_for_completed_or_explicitly_blocked_roots(monkeypatch):
    from twicc.providers.compute_work import ComputeWorkState

    project = Project.objects.create(id='classification')
    Session.objects.create(id='eligible', project=project, provider=Provider.CLAUDE_CODE, mtime=1, file_path='/test/eligible')
    Session.objects.create(id='recent', project=project, provider=Provider.CLAUDE_CODE, mtime=time.time(), file_path='/test/recent')

    async def run():
        state = ComputeWorkState()
        barrier = asyncio.Event()
        admitted = asyncio.Event()
        release = asyncio.Event()
        async def initialize(job):
            admitted.set()
            await release.wait()
            raise RuntimeError('explicit blocked outcome')
        monkeypatch.setattr('twicc.providers.db_writer.submit_async_job', initialize)
        monkeypatch.setattr(background, 'start_compute_process', lambda ctx: None)
        from twicc.providers import db_writer
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'unused:factory')
        task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=state))
        try:
            await asyncio.wait_for(admitted.wait(), 3)
            assert state.has_pending()
            assert not barrier.is_set()
            release.set()
            await asyncio.wait_for(barrier.wait(), 3)
            assert state.has_pending()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
        assert not state.has_pending()
        assert not state._source_wakeups
    asyncio.run(run())


pytest_plugins = ['tests.test_background_compute_recovery']


@pytest.mark.django_db(transaction=True)
def test_recent_root_deadline_has_no_inventory_polling(recovery_runtime, monkeypatch):
    from contextlib import closing
    import sqlite3
    from django.db import connection
    from twicc.providers.compute_work import ComputeWorkState
    from twicc.providers import db_writer
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET mtime=? WHERE id='faulty'", (time.time() - 28.5,))
        database.commit()
    reads = []
    ordinary = background.sync_to_async
    state = ComputeWorkState()

    def reader(function):
        def read():
            assert Provider.CLAUDE_CODE in state._source_wakeups
            def record(execute, sql, params, many, context):
                if 'core_session' in sql:
                    reads.append(sql)
                return execute(sql, params, many, context)
            with connection.execute_wrapper(record):
                return function()
        return ordinary(read)
    monkeypatch.setattr(background, 'sync_to_async', reader)

    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx, asyncio.Event(), work_state=state))
        try:
            async with asyncio.timeout(5):
                while versions(path)['healthy'] != 123:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.1)
            before = len(reads)
            await asyncio.sleep(.15)
            assert len(reads) == before
            assert versions(path)['faulty'] is None
            assert state.has_pending()
            await asyncio.wait_for(task, 6)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not state.has_pending()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_source_commit_during_final_idle_transition_restarts_selection(recovery_runtime, monkeypatch):
    from contextlib import closing
    import sqlite3
    from twicc.providers.compute_work import ComputeWorkState
    from twicc.providers import db_writer
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    state = ComputeWorkState()
    broadcast = background.broadcast_startup_progress
    injected = False

    async def progress(*args, **kwargs):
        nonlocal injected
        await broadcast(*args, **kwargs)
        if kwargs.get('completed') and not injected:
            injected = True
            with closing(sqlite3.connect(path)) as database:
                database.execute("UPDATE core_session SET compute_version=NULL WHERE id='healthy'")
                database.commit()
            state.notify_source_changed(Provider.CLAUDE_CODE, 'healthy')
            await asyncio.sleep(0)

    monkeypatch.setattr(background, 'broadcast_startup_progress', progress)
    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        try:
            await asyncio.wait_for(background.start_background_compute_task(ctx, work_state=state), 10)
            assert injected
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not state._source_wakeups
        finally:
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('writer', ['initial_sync', 'migration'])
def test_invalidation_notifies_only_after_commit(monkeypatch, writer):
    from django.db import transaction
    from twicc.providers import compute_work, db_writer
    from twicc.providers.codex.rollout_migration import MarkSessionRebuildJob, _apply_mark_session_rebuild_job

    project = Project.objects.create(id='commit-notification')
    session = Session.objects.create(id='source', project=project, provider=Provider.CODEX,
                                     file_path='/test/source', compute_version=123)
    received = []
    monkeypatch.setattr(compute_work, 'notify_committed_source_changed', lambda *args: received.append(args))

    def apply():
        if writer == 'initial_sync':
            db_writer._apply_update_session_payload(db_writer.UpdateSessionPayload(
                Provider.CODEX, session, [], 0, 0, 100, True, False,
            ))
        else:
            _apply_mark_session_rebuild_job(MarkSessionRebuildJob(Provider.CODEX, session.id, None))

    with transaction.atomic():
        apply()
        assert received == []
        transaction.set_rollback(True)
    assert received == []
    with transaction.atomic():
        apply()
        assert received == []
    assert received == [(Provider.CODEX, 'source')]


@pytest.mark.django_db(transaction=True)
def test_higher_versions_do_not_keep_actual_work_pending(monkeypatch):
    from twicc.providers.compute_work import ComputeWorkState

    project = Project.objects.create(id='future-version')
    Session.objects.create(id='future', project=project, provider=Provider.CLAUDE_CODE,
                           file_path='/test/future', compute_version=124, mtime=1)
    monkeypatch.setattr(background, 'start_compute_process', lambda ctx: pytest.fail('no outdated native root'))
    progress = []
    async def broadcast(*args, **kwargs):
        progress.append((args[1:3], kwargs.get('completed')))
    monkeypatch.setattr(background, 'broadcast_startup_progress', broadcast)

    async def run():
        state = ComputeWorkState()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'unused:factory')
        await asyncio.wait_for(background.start_background_compute_task(ctx, work_state=state), 2)
        assert not state.has_pending()
        assert progress[-1] == ((1, 1), True)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_writer_outcome_does_not_release_slot_before_worker_status(recovery_runtime, monkeypatch):
    from contextlib import closing
    import sqlite3
    from twicc.providers import db_writer
    from twicc.providers.compute_work import ComputeWorkState
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET parent_session_id='healthy', type='subagent' WHERE id='faulty'")
        database.commit()
    withheld = []
    submitted = []
    start = background.start_compute_process
    submit = db_writer.submit_async_job

    def hold_status(ctx):
        start(ctx)
        ordinary = ctx.status_queue.put
        def put(status, *args, **kwargs):
            if status.get('session_id') == 'faulty':
                withheld.append((status, ordinary))
            else:
                ordinary(status, *args, **kwargs)
        ctx.status_queue.put = put

    async def record(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            submitted.append(job.session_id)
        return await submit(job)
    monkeypatch.setattr(background, 'start_compute_process', hold_status)
    monkeypatch.setattr(db_writer, 'submit_async_job', record)

    async def run():
        state = ComputeWorkState()
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=state))
        try:
            async with asyncio.timeout(5):
                while versions(path)['faulty'] != 123 or not withheld:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.1)
            assert submitted == ['faulty']
            assert state.has_pending()
            status, send = withheld.pop()
            send(status)
            await asyncio.wait_for(task, 5)
            assert submitted == ['faulty', 'healthy']
            assert not state.has_pending()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_initial_progress_failure_recovers_without_releasing_actual_work(recovery_runtime, monkeypatch):
    from twicc.providers import db_writer
    from twicc.providers.compute_work import ComputeWorkState
    from tests.test_background_compute_recovery import versions

    path, _ = recovery_runtime
    state = ComputeWorkState()
    broadcast = background.broadcast_startup_progress
    first = True
    recovering, release = asyncio.Event(), asyncio.Event()

    async def progress(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError('one initial progress failure')
        await broadcast(*args, **kwargs)

    async def retry(stop, delay):
        assert state.has_pending()
        recovering.set()
        await release.wait()
        return not stop.is_set()

    monkeypatch.setattr(background, 'broadcast_startup_progress', progress)
    monkeypatch.setattr(background, '_wait_for_retry_delay', retry)

    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx, work_state=state))
        recovery_wait = asyncio.create_task(recovering.wait())
        try:
            await asyncio.wait(
                (task, recovery_wait), timeout=3,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert state.has_pending()
            assert not task.done()
            assert recovering.is_set()
            assert versions(path) == {'faulty': None, 'healthy': None}
            release.set()
            await asyncio.wait_for(task, 10)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not state.has_pending()
        finally:
            release.set()
            task.cancel()
            recovery_wait.cancel()
            await asyncio.gather(task, recovery_wait, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())
