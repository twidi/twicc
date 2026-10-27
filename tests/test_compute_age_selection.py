"""Source age selection uses current database revisions in real pipelines."""

import asyncio
from twicc.providers.compute_work import ComputeWorkState
from contextlib import closing
import sqlite3
import time
from types import SimpleNamespace

import pytest

from twicc.core.enums import Provider
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.codex import background_compute as codex
from tests.test_background_compute_recovery import versions

pytest_plugins = ["tests.test_background_compute_recovery"]


def update_sources(path, **mtimes):
    with closing(sqlite3.connect(path)) as database:
        database.executemany("UPDATE core_session SET mtime=? WHERE id=?", [(value, key) for key, value in mtimes.items()])
        database.commit()


def test_age_boundary():
    from twicc.providers.compute_selection import compute_age_delay
    assert compute_age_delay(100, 129) == 1
    assert compute_age_delay(100, 130) == 0
    assert compute_age_delay(100, 131) == 0
    assert compute_age_delay(140, 130) == 40


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_recent_only_classifies_waits_rechecks_and_stops(recovery_runtime, monkeypatch, provider):
    from twicc.core.content_storage import extraction
    path, progress = recovery_runtime
    now = time.time()
    update_sources(path, faulty=now + 1000, healthy=now + 2000)
    attempts = []
    initialize = extraction.initialize_compute_session
    def record(sid):
        attempts.append(sid)
        return initialize(sid)
    monkeypatch.setattr(extraction, 'initialize_compute_session', record)
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)
    with closing(sqlite3.connect(path)) as database:
        database.execute('UPDATE core_session SET provider=?', (provider.value,))
        database.commit()

    async def scenario():
        db_writer.start_db_writer()
        barrier = asyncio.Event()
        factory = ('tests.test_codex_compute_recovery:CodexRecoveryCompute' if provider == Provider.CODEX
                   else 'tests.test_background_compute_recovery:RecoveryCompute')
        ctx = background.ComputeContext(provider, 123, factory)
        if provider == Provider.CODEX:
            work_state = ComputeWorkState()
            owner = codex.CodexComputeCoordinator(ctx, barrier, work_state=work_state)
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            work_state = ComputeWorkState()
            task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=work_state))
        try:
            await asyncio.wait_for(barrier.wait(), 2)
            assert attempts == []
            assert not task.done()
            assert not any(done for _, done in progress)
            # Committed source changes wake the generic selector.
            update_sources(path, faulty=now - 31)
            work_state.notify_source_changed(provider, "faulty")
            async with asyncio.timeout(6):
                while versions(path)['faulty'] != 123:
                    await asyncio.sleep(.01)
            assert attempts == ['faulty', 'faulty']
            assert versions(path)['healthy'] is None
            assert all(total == 2 for (_, total), _ in progress)
            ctx.stop_event.set()
            await asyncio.wait_for(task, 2)
            assert ctx.process is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('backoff', [-1.0, .05])
def test_codex_recent_failure_waits_for_source_age_deadline(recovery_runtime, monkeypatch, backoff):
    """Expired or earlier backoff cannot bypass the independent source-age deadline."""
    from django.db import connection
    path, _ = recovery_runtime
    wall = [1000.0]
    update_sources(path, faulty=wall[0] - 29.5)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET provider='codex'")
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.commit()
    monkeypatch.setattr(codex, 'time', SimpleNamespace(time=lambda: wall[0], monotonic=time.monotonic))
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)
    reads = []
    ordinary_reader = background.sync_to_async
    def reader(function):
        def wrapped():
            def record(execute, sql, params, many, context):
                if 'ORDER BY' in sql and 'mtime' in sql and 'file_path' in sql:
                    reads.append(time.monotonic())
                return execute(sql, params, many, context)
            with connection.execute_wrapper(record):
                return function()
        return ordinary_reader(wrapped)
    monkeypatch.setattr(codex, 'sync_to_async', reader)

    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, 'tests.test_codex_compute_recovery:CodexRecoveryCompute')
        owner = codex.CodexComputeCoordinator(ctx, asyncio.Event())
        owner.failures['faulty'] = codex.FailedCandidate('faulty', 'compute', 'previous failure')
        owner._retry_at['faulty'] = time.monotonic() + backoff
        admitted = []
        async def prepare(candidate):
            admitted.append(candidate.session_id)
            return codex.PreparedCandidate(candidate.session_id, None, False)
        owner.prepare_candidate = prepare
        task = asyncio.create_task(owner.run())
        try:
            await asyncio.wait_for(owner.initial_done.wait(), 2)
            reads.clear()
            for _ in range(20):
                await asyncio.sleep(.01)
            assert len(reads) <= 1, reads
            assert admitted == []
            wall[0] = 1031.0
            async with asyncio.timeout(5):
                while versions(path)['faulty'] != 123:
                    await asyncio.sleep(.01)
            assert admitted == ['faulty']
            ctx.stop_event.set()
            await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_claude_indexes_after_recent_startup_sweep(recovery_runtime, monkeypatch):
    """A deferred startup session enters real search without a source write."""
    import orjson
    from django.conf import settings
    from twicc import paths, search
    from twicc.core.enums import ItemKind
    from twicc.core.models import SessionItem
    from twicc.core.content_storage import readers
    from twicc import search_indexing_task as indexing
    path, _ = recovery_runtime
    wall = [1000.0]
    update_sources(path, faulty=970.2, healthy=970.2)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET last_line=1 WHERE id='faulty'")
        database.commit()
    monkeypatch.setattr(background, 'time', SimpleNamespace(time=lambda: wall[0], monotonic=time.monotonic))
    monkeypatch.setattr(settings, 'CLAUDE_CODE_COMPUTE_VERSION', 123)
    monkeypatch.setattr(paths, 'get_search_dir', lambda: path.parent / 'search-index')
    monkeypatch.setattr(indexing, '_pending_reindex_ids', set())
    monkeypatch.setattr(indexing, '_indexing_tasks', [])
    monkeypatch.setattr(indexing, '_session_reindex_task', None)
    monkeypatch.setattr(indexing, '_indexing_run_lock', None)
    monkeypatch.setattr(indexing, '_shutting_down', False)
    monkeypatch.setattr(indexing, '_stop_event', None)
    def reader(function):
        async def call(*args, **kwargs):
            return await background.sync_to_async(lambda: function(*args, **kwargs))()
        return call
    monkeypatch.setattr(indexing, 'sync_to_async', reader)
    monkeypatch.setattr(readers, 'sync_to_async', reader)
    monkeypatch.setattr(indexing, 'broadcast_startup_progress', background.broadcast_startup_progress)
    search.init_search_index()

    async def scenario():
        db_writer.start_db_writer()
        await background.sync_to_async(lambda: SessionItem.objects.create(
            session_id='faulty', line_num=1, kind=ItemKind.USER_MESSAGE,
            content=orjson.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'deferredsearchtoken'}}).decode(),
        ))()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'tests.test_background_compute_recovery:RecoveryCompute')
        barrier = asyncio.Event()
        work_state = ComputeWorkState()
        task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=work_state))
        try:
            await asyncio.wait_for(barrier.wait(), 2)
            await indexing.start_search_index_task()
            assert versions(path)['faulty'] is None
            assert search.search('deferredsearchtoken').total_sessions == 0
            wall[0] = 1031.0
            await asyncio.wait_for(task, 6)
            async with asyncio.timeout(3):
                while search.search('deferredsearchtoken').total_sessions == 0:
                    await asyncio.sleep(.01)
            result = search.search('deferredsearchtoken')
            assert [item.session_id for item in result.results] == ['faulty']
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for index_task in indexing.get_active_indexing_tasks():
                await index_task
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    try:
        asyncio.run(scenario())
    finally:
        search.shutdown_search_index()


def clone_session(path, session_id, mtime, *, session_type='session'):
    with closing(sqlite3.connect(path)) as database:
        database.row_factory = sqlite3.Row
        row = dict(database.execute("SELECT * FROM core_session WHERE id='healthy'").fetchone())
        row.update(id=session_id, file_path='/test/' + session_id, mtime=mtime, type=session_type)
        columns = ','.join(row)
        placeholders = ','.join('?' for _ in row)
        database.execute(f'INSERT INTO core_session ({columns}) VALUES ({placeholders})', tuple(row.values()))
        database.commit()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_bounds_unapplied_results_and_reselects_modified_sources(recovery_runtime, monkeypatch, provider):
    """Computed status cannot release writer admission or freeze a candidate snapshot."""
    import threading
    from twicc.providers.compute_base import BaseSessionCompute
    path, progress = recovery_runtime
    now = time.time()
    clone_session(path, 'third', now - 100)
    clone_session(path, 'child', now - 110, session_type='subagent')
    clone_session(path, 'recent', now + 1000)
    update_sources(path, faulty=now - 40, healthy=now - 50)
    clone_session(path, 'root', now - 35)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET parent_session_id='root', type='subagent' WHERE id IN ('faulty', 'healthy', 'child')")
        database.commit()
    entered, release = threading.Event(), threading.Event()
    dispatched = []
    statuses = []
    streams = []
    original_submit = background.start_compute_process
    if provider == Provider.CODEX:
        with closing(sqlite3.connect(path)) as database:
            database.execute('UPDATE core_session SET provider=?', (provider.value,))
            database.commit()
    healthy_initialized = asyncio.Event()
    original_apply = BaseSessionCompute._apply_session_complete_final
    original_turn = db_writer._background_turn
    original_job = db_writer.submit_async_job

    async def submit(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            dispatched.append(job.session_id)
        result = await original_job(job)
        if isinstance(job, db_writer.InitializeComputeStorageJob) and job.session_id == 'healthy':
            healthy_initialized.set()
            db_writer._writer_signals.notify_dispatch()
        return result

    def apply(message):
        if message['session_id'] == 'faulty':
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release application')
        return original_apply(message)

    async def turn(factory, *, diagnostics=None, phase=None, **kwargs):
        # The DB writer is serial: once ``apply`` holds ``faulty`` under its
        # write grant, no other job runs. A coordinator can submit the
        # ``healthy`` initialization job after the worker reports ``faulty``
        # computed, so a fast worker can bring the final application ahead of
        # that job, which then waits for a release that waits for it. Keep the
        # application out of the grant until that job has run.
        if phase == 'final' and diagnostics.message.get('session_id') == 'faulty' and not healthy_initialized.is_set():
            return False
        return await original_turn(factory, diagnostics=diagnostics, phase=phase, **kwargs)

    def start(ctx):
        original_submit(ctx)
        streams.append(ctx.result_stream)
        original_put = ctx.status_queue.put
        def put(status, *args, **kwargs):
            statuses.append(status)
            return original_put(status, *args, **kwargs)
        ctx.status_queue.put = put

    monkeypatch.setattr(db_writer, 'submit_async_job', submit)
    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(apply))
    monkeypatch.setattr(db_writer, '_background_turn', turn)
    monkeypatch.setattr(background, 'start_compute_process', start)
    monkeypatch.setattr(codex, 'start_compute_process', start)
    monkeypatch.setattr(codex, 'submit_async_job', submit)
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def scenario():
        db_writer.start_db_writer()
        factory = ('tests.test_codex_compute_recovery:CodexRecoveryCompute' if provider == Provider.CODEX
                   else 'tests.test_background_compute_recovery:RecoveryCompute')
        ctx = background.ComputeContext(provider, 123, factory)
        barrier = asyncio.Event()
        if provider == Provider.CODEX:
            work_state = ComputeWorkState()
            owner = codex.CodexComputeCoordinator(ctx, barrier, work_state=work_state)
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            work_state = ComputeWorkState()
            task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=work_state))
        try:
            async with asyncio.timeout(4):
                while not entered.is_set() or len(statuses) < 2:
                    await asyncio.sleep(.01)
            # Both workers are computed, but neither result has completed writer application.
            await asyncio.sleep(.15)
            assert dispatched == ['faulty', 'healthy']
            assert versions(path)['third'] is None
            assert not barrier.is_set()
            update_sources(path, third=now + 2000)
            work_state.notify_source_changed(provider, "third")
            release.set()
            async with asyncio.timeout(4):
                while versions(path)['root'] != 123 or not barrier.is_set():
                    await asyncio.sleep(.01)
            assert dispatched == ['faulty', 'healthy', 'child', 'root']
            assert versions(path)['third'] is None
            assert len(streams) == 1
            # Equal root mtimes do not impose a secondary order.
            update_sources(path, third=now - 31, recent=now - 31)
            work_state.notify_source_changed(provider, "third")
            work_state.notify_source_changed(provider, "recent")
            if provider == Provider.CODEX:
                async with asyncio.timeout(6):
                    while (not all(version == 123 for version in versions(path).values())
                           or len(owner.initial_ready) != 3):
                        await asyncio.sleep(.01)
                ctx.stop_event.set()
            await asyncio.wait_for(task, 6)
            assert dispatched[:4] == ['faulty', 'healthy', 'child', 'root']
            assert set(dispatched[4:]) == {'recent', 'third'}
            assert all(total == 3 for (_, total), _ in progress)
            assert progress[-1] == ((3, 3), True)
            if provider == Provider.CLAUDE_CODE:
                assert len(streams) == 1
            assert all(stream.reader.closed for stream in streams)
            assert ctx.process is None
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', [Provider.CLAUDE_CODE, Provider.CODEX])
def test_superseded_result_returns_to_source_age_selection(recovery_runtime, monkeypatch, provider):
    """A real source revision rejection must not immediately retry its new recent source."""
    import threading
    from twicc.core.content_storage import extraction
    path, progress = recovery_runtime
    now = time.time()
    update_sources(path, faulty=now - 40, healthy=now - 100)
    with closing(sqlite3.connect(path)) as database:
        database.execute('UPDATE core_session SET provider=?', (provider.value,))
        database.commit()
    entered, release = threading.Event(), threading.Event()
    first = True
    dispatched = []
    original_begin = extraction.begin_compute_application
    original_job = db_writer.submit_async_job

    def begin(message, **kwargs):
        nonlocal first
        if message['session_id'] == 'faulty' and first:
            first = False
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release source observation')
        return original_begin(message, **kwargs)

    async def submit(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            dispatched.append(job.session_id)
        return await original_job(job)

    monkeypatch.setattr(extraction, 'begin_compute_application', begin)
    monkeypatch.setattr(db_writer, 'submit_async_job', submit)
    monkeypatch.setattr(codex, 'submit_async_job', submit)
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def scenario():
        db_writer.start_db_writer()
        factory = ('tests.test_codex_compute_recovery:CodexRecoveryCompute' if provider == Provider.CODEX
                   else 'tests.test_background_compute_recovery:RecoveryCompute')
        ctx = background.ComputeContext(provider, 123, factory)
        barrier = asyncio.Event()
        if provider == Provider.CODEX:
            work_state = ComputeWorkState()
            owner = codex.CodexComputeCoordinator(ctx, barrier, work_state=work_state)
            async def prepare(candidate):
                return codex.PreparedCandidate(candidate.session_id, None, False)
            owner.prepare_candidate = prepare
            task = asyncio.create_task(owner.run())
        else:
            work_state = ComputeWorkState()
            task = asyncio.create_task(background.start_background_compute_task(ctx, barrier, work_state=work_state))
        try:
            async with asyncio.timeout(4):
                while not entered.is_set():
                    await asyncio.sleep(.01)
            with closing(sqlite3.connect(path)) as database:
                database.execute("UPDATE core_session SET last_offset=1, mtime=? WHERE id='faulty'", (now + 1000,))
                database.commit()
            release.set()
            async with asyncio.timeout(4):
                while versions(path)['healthy'] != 123 or not barrier.is_set():
                    await asyncio.sleep(.01)
            await asyncio.sleep(.15)
            assert versions(path)['faulty'] is None
            assert dispatched.count('faulty') == 1
            assert not task.done()
            assert not any(done for _, done in progress)
            update_sources(path, faulty=now - 31)
            work_state.notify_source_changed(provider, "faulty")
            async with asyncio.timeout(5):
                while versions(path)['faulty'] != 123:
                    await asyncio.sleep(.01)
            assert dispatched.count('faulty') == 2
            if provider == Provider.CODEX:
                ctx.stop_event.set()
            await asyncio.wait_for(task, 2)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_codex_own_migration_mtime_does_not_defer_its_leased_compute(recovery_runtime, monkeypatch):
    """The coordinator checks age before preparation, not after its own history rewrite."""
    import orjson
    from twicc.providers.codex.migration_gate import gate_for
    from twicc.providers.codex.rollout_migration import CodexMigrationOutcome, HistoryMode
    path, progress = recovery_runtime
    now = time.time()
    rollout = path.parent / 'migration.jsonl'
    def write_rollout(mode):
        rollout.write_bytes(orjson.dumps({'type': 'session_meta', 'payload': {
            'id': 'faulty', 'cwd': '/test', 'history_mode': mode,
        }}) + b'\n')
    write_rollout('legacy')
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET provider='codex'")
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.execute("UPDATE core_session SET file_path=?, mtime=? WHERE id='faulty'", (str(rollout), now - 31))
        database.commit()
    monkeypatch.setattr(codex, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(codex, 'load_project_directories', lambda: None)
    monkeypatch.setattr(codex, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(codex, 'broadcast_startup_progress', background.broadcast_startup_progress)
    monkeypatch.setattr(codex.search, 'is_initialized', lambda: False)
    releases = []

    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, 'tests.test_codex_compute_recovery:CodexRecoveryCompute')
        owner = codex.CodexComputeCoordinator(ctx, asyncio.Event(), on_migration_released=releases.append)
        owner._is_agent_active = lambda sid: False
        async def database_mode(candidate):
            return HistoryMode.LEGACY
        owner._database_mode = database_mode
        async def migrate(sid, file_path):
            assert sid == 'faulty' and file_path == rollout
            assert gate_for(sid).locked()
            write_rollout('paginated')
            update_sources(path, faulty=time.time())
            return CodexMigrationOutcome('migrated', 0, None)
        owner.runner.run = migrate
        task = asyncio.create_task(owner.run())
        try:
            async with asyncio.timeout(5):
                while (versions(path)['faulty'] != 123 or gate_for('faulty').locked()
                           or not progress or progress[-1] != ((1, 1), True)):
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            assert owner.stats['migrated'] == 1
            assert [entry.outcome for entry in releases] == ['ready']
            assert progress[-1] == ((1, 1), True)
            ctx.stop_event.set()
            await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db
def test_root_age_does_not_hide_old_required_descendant():
    """Tree discovery ranks roots; the source-age gate evaluates each required source."""
    from twicc.core.models import Project, Session
    from twicc.providers.compute_selection import compute_age_delay, select_required_source_ids
    from twicc.providers.compute_tree import load_native_tree, select_outdated_roots

    project = Project.objects.create(id='age-tree')
    Session.objects.create(id='recent-root', project=project, provider='claude_code',
                           file_path='/age/root', mtime=1000)
    Session.objects.create(id='old-child', project=project, provider='claude_code',
                           file_path='/age/child', mtime=900, type='subagent', parent_session_id='recent-root')
    assert select_outdated_roots('claude_code', 123) == ('recent-root',)
    assert select_required_source_ids(load_native_tree('recent-root'), 123) == ('old-child', 'recent-root')
    assert compute_age_delay(900, 1000) == 0
    assert compute_age_delay(1000, 1000) == 30
