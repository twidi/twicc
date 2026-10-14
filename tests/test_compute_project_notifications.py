"""Real writer notifications must finish before graceful producer completion."""

import asyncio
import threading
import time
from types import SimpleNamespace

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.codex import background_compute as codex
from tests.test_background_compute_recovery import RecoveryCompute, versions
from tests.test_compute_age_selection import clone_session, update_sources

pytest_plugins = ['tests.test_background_compute_recovery']


class ProjectRecoveryCompute(RecoveryCompute):
    def compute_session_metadata(self, session_id, result_queue, run_id):
        def put(raw):
            message = orjson.loads(raw)
            message.update(provider=self.provider.value, project_id='recovery', project_directory='/isolated-test-directory')
            result_queue.put(orjson.dumps(message))
        super().compute_session_metadata(session_id, SimpleNamespace(put=put), run_id)


class CodexProjectRecoveryCompute(ProjectRecoveryCompute):
    provider = Provider.CODEX


@pytest.fixture
def project_notifications(monkeypatch):
    from twicc import projects
    from twicc.providers import statistics_publication
    events = []
    async def statistics_notification(*args):
        pass
    # This fixture observes source lifecycle notifications only. Unified
    # publication notifications have their own serialized-value integration test.
    monkeypatch.setattr(statistics_publication, 'notify_statistics_published', statistics_notification)
    async def workspace(*args):
        events.append('workspace')
    async def link(*args):
        events.append('link')
    async def color(*args):
        events.append('color')
    async def project(*args):
        events.append('project')
    monkeypatch.setattr(db_writer, 'auto_add_project_to_workspaces', workspace)
    monkeypatch.setattr(projects, 'ensure_worktree_link', link)
    monkeypatch.setattr(projects, 'ensure_project_color', color)
    monkeypatch.setattr(db_writer, '_broadcast_project_updated', project)
    return events


@pytest.mark.django_db(transaction=True)
def test_success_waits_for_last_notification_and_project_finalization(recovery_runtime, project_notifications, monkeypatch):
    path, progress = recovery_runtime
    reached, release = asyncio.Event(), asyncio.Event()
    states = []
    async def session(sid):
        if sid == 'healthy':
            states.append(next(iter(db_writer._compute_states.values())))
            reached.set()
            await release.wait()
        project_notifications.append(sid)
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', session)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:ProjectRecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            await asyncio.wait_for(reached.wait(), 4)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            await asyncio.sleep(.15)
            assert not states[0].abandoned
            assert not task.done()
            assert 'project' not in project_notifications
            release.set()
            await asyncio.wait_for(task, 4)
            assert project_notifications == ['faulty', 'workspace', 'link', 'color', 'healthy', 'project']
            assert progress[-1] == ((2, 2), True)
            assert not states[0].abandoned
            assert ctx.process is None
            assert ctx.run_id not in db_writer._compute_states
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_persistent_run_flushes_projects_without_duplicate_progress(recovery_runtime, project_notifications, monkeypatch, provider):
    path, progress = recovery_runtime
    now = time.time()
    update_sources(path, faulty=now + 1000, healthy=now - 40)
    for number in range(4):
        clone_session(path, f'eligible-{number}', now - 50 - number)
    if provider == Provider.CODEX:
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(path)) as database:
            database.execute("UPDATE core_session SET provider='codex'")
            database.commit()
        monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
        monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
        monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
        monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)
    writer_progress = []
    async def broadcast(*args, **kwargs):
        writer_progress.append(args)
    monkeypatch.setattr(db_writer, 'broadcast_startup_progress', broadcast)
    async def scenario():
        db_writer.start_db_writer()
        factory = 'CodexProjectRecoveryCompute' if provider == Provider.CODEX else 'ProjectRecoveryCompute'
        ctx = background.ComputeContext(provider, 123, f'{__name__}:{factory}')
        if provider == Provider.CODEX:
            owner = codex.CodexComputeCoordinator(ctx, asyncio.Event())
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            async with asyncio.timeout(5):
                while sum(value == 123 for value in versions(path).values()) < 5:
                    await asyncio.sleep(.01)
            await asyncio.sleep(.15)
            assert project_notifications.count('project') == 1
            assert not task.done() and ctx.process.is_alive()
            assert writer_progress == []
            assert all(total == 6 for (_, total), _ in progress)
            assert not any(done for _, done in progress)
            ctx.stop_event.set()
            await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_empty_selection_waits_for_age_poll_while_status_ticks_continue(recovery_runtime, monkeypatch):
    from twicc.core.content_storage import extraction
    path, _ = recovery_runtime
    now = time.time()
    update_sources(path, faulty=now - 40, healthy=now + 1000)
    controlled = [100.0]
    monkeypatch.setattr(background, 'time', SimpleNamespace(time=time.time, monotonic=lambda: controlled[0]))
    monkeypatch.setattr(background, 'COMPUTE_STATUS_POLL_SECONDS', .001)
    reached, release = threading.Event(), threading.Event()
    original_begin = extraction.begin_compute_application
    def begin(message, **kwargs):
        if message['session_id'] == 'faulty':
            reached.set()
            if not release.wait(5):
                raise RuntimeError('test did not release application')
        return original_begin(message, **kwargs)
    monkeypatch.setattr(extraction, 'begin_compute_application', begin)
    dispatched = []
    ordinary_submit = db_writer.submit_async_job
    async def submit(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            dispatched.append(job.session_id)
        return await ordinary_submit(job)
    monkeypatch.setattr(db_writer, 'submit_async_job', submit)
    from twicc.providers import compute_selection
    reads = []
    ordinary_select = compute_selection.select_native_tree_source
    def select(*args, **kwargs):
        reads.append(controlled[0])
        return ordinary_select(*args, **kwargs)
    monkeypatch.setattr(compute_selection, 'select_native_tree_source', select)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            async with asyncio.timeout(4):
                while not reached.is_set():
                    await asyncio.sleep(.01)
            await asyncio.sleep(.05)
            reads.clear()
            for _ in range(10):
                await asyncio.sleep(.01)
            assert len(reads) <= 1
            update_sources(path, healthy=now - 31)
            controlled[0] = 101.0
            async with asyncio.timeout(.5):
                while not reads:
                    await asyncio.sleep(.01)
            assert reads == [101.0]
            # Outstanding publication pins the active native tree. A newly
            # aged root waits for its acknowledgement before admission.
            assert 'healthy' not in dispatched
            release.set()
            controlled[0] = 102.0
            await asyncio.wait_for(task, 4)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_default_writer_publishes_progress_and_batches_projects(recovery_runtime, project_notifications):
    path, progress = recovery_runtime
    now = time.time()
    ids = ['healthy']
    for number in range(4):
        sid = f'default-{number}'
        clone_session(path, sid, now - 50 - number)
        ids.append(sid)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:ProjectRecoveryCompute')
        ctx.run_id, completion = db_writer.arm_compute_completion(Provider.CLAUDE_CODE, set(ids), 5)
        try:
            for sid in ids:
                await db_writer.submit_async_job(db_writer.InitializeComputeStorageJob(
                    Provider.CLAUDE_CODE, sid, asyncio.get_running_loop().create_future(),
                ))
            background.start_compute_process(ctx)
            for sid in ids:
                ctx.command_queue.put_nowait({'session_id': sid})
            ctx.command_queue.put_nowait(None)
            assert await asyncio.wait_for(asyncio.shield(completion), 5) == 0
            assert project_notifications.count('project') == 1
            assert progress == [((number, 5), False) for number in range(1, 6)]
            assert all(versions(path)[sid] == 123 for sid in ids)
            await background.stop_compute_worker(ctx)
        finally:
            await background.retire_compute_run(ctx, completion)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('interruption', ['stop', 'cancel'])
def test_interruption_fences_and_drains_normal_completion(recovery_runtime, project_notifications, monkeypatch, interruption):
    reached, release = asyncio.Event(), asyncio.Event()
    states = []
    async def session(sid):
        if sid == 'healthy':
            states.append(next(iter(db_writer._compute_states.values())))
            reached.set()
            await release.wait()
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', session)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:ProjectRecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            await asyncio.wait_for(reached.wait(), 4)
            await asyncio.sleep(.15)
            assert not states[0].abandoned
            if interruption == 'stop':
                ctx.stop_event.set()
            else:
                task.cancel()
            async with asyncio.timeout(1):
                while not states[0].abandoned:
                    await asyncio.sleep(.01)
            if interruption == 'cancel':
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            release.set()
            if interruption == 'cancel':
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
            else:
                await asyncio.wait_for(task, 3)
            assert ctx.process is None
            assert ctx.run_id not in db_writer._compute_states
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_source_outdated_during_graceful_drain_returns_to_selection(recovery_runtime, project_notifications, monkeypatch):
    from contextlib import closing
    import sqlite3
    path, progress = recovery_runtime
    now = time.time()
    reached, release = asyncio.Event(), asyncio.Event()
    first = True
    async def session(sid):
        nonlocal first
        if sid == 'healthy' and first:
            first = False
            reached.set()
            await release.wait()
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', session)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:ProjectRecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            await asyncio.wait_for(reached.wait(), 4)
            await asyncio.sleep(.15)
            old_run = ctx.run_id
            with closing(sqlite3.connect(path)) as database:
                database.execute("UPDATE core_session SET compute_version=NULL, mtime=? WHERE id='healthy'", (now + 1000,))
                database.commit()
            release.set()
            async with asyncio.timeout(2):
                while old_run in db_writer._compute_states or ctx.process is not None:
                    await asyncio.sleep(.01)
            assert not task.done()
            assert versions(path)['healthy'] is None
            assert not any(done for _, done in progress)
            update_sources(path, healthy=now - 31)
            await asyncio.wait_for(task, 5)
            assert ctx.run_id != old_run
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert all(total == 2 for (_, total), _ in progress)
            assert progress[-1] == ((2, 2), True)
            assert ctx.process is None
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_progress_failure_does_not_mark_a_replacement_run_complete(recovery_runtime, project_notifications, monkeypatch):
    from contextlib import closing
    import sqlite3
    path, _ = recovery_runtime
    reached, release = asyncio.Event(), asyncio.Event()
    notifications = 0
    failed = False
    ordinary_progress = background.broadcast_startup_progress
    async def progress(*args, **kwargs):
        nonlocal failed
        if kwargs.get('completed') and not failed:
            failed = True
            with closing(sqlite3.connect(path)) as database:
                database.execute("UPDATE core_session SET compute_version=NULL WHERE id='healthy'")
                database.commit()
            raise RuntimeError('one final progress publication failure')
        await ordinary_progress(*args, **kwargs)
    async def session(sid):
        nonlocal notifications
        if sid == 'healthy':
            notifications += 1
            if notifications == 2:
                reached.set()
                await release.wait()
    monkeypatch.setattr(background, 'broadcast_startup_progress', progress)
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', session)
    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:ProjectRecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            await asyncio.wait_for(reached.wait(), 7)
            assert failed
            task.cancel()
            await asyncio.sleep(.05)
            assert not task.done()
            assert db_writer._compute_states[ctx.run_id].abandoned
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert ctx.process is None
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
