"""Automatic recovery uses real worker pipes, writer jobs, and isolated SQLite."""

import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import threading

from asgiref.sync import sync_to_async
from django.db import connection, connections
import orjson
import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session
from twicc.providers import background_compute_task as background
from twicc.providers import compute_executor, db_writer


class RecoveryCompute:
    provider = Provider.CLAUDE_CODE

    def __init__(self):
        connections['default'].settings_dict = {
            **connections['default'].settings_dict, 'NAME': os.environ['RECOVERY_TEST_DB'],
        }

    def enable_batch_queue_cache(self):
        pass

    def compute_session_metadata(self, session_id, result_queue, run_id):
        if os.environ.get('RECOVERY_TEST_CRASH') == session_id:
            os._exit(42)
        crash_once = os.environ.get('RECOVERY_TEST_CRASH_ONCE')
        if session_id == 'faulty' and crash_once and not Path(crash_once).exists():
            Path(crash_once).touch()
            os._exit(42)
        fail_once = os.environ.get('RECOVERY_TEST_FAIL_ONCE')
        if session_id == 'faulty' and fail_once and not Path(fail_once).exists():
            Path(fail_once).touch()
            raise RuntimeError('one compute failure')
        session = Session.objects.get(pk=session_id)
        result_queue.put(orjson.dumps({
            'type': 'session_complete', 'provider': 'claude_code', 'run_id': run_id,
            'session_id': session_id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'session_fields': {'compute_version': 123},
        }))


@pytest.fixture
def recovery_runtime(tmp_path, monkeypatch):
    project = Project.objects.create(id='recovery')
    Session.objects.create(id='faulty', project=project, provider=Provider.CLAUDE_CODE, mtime=20, file_path='/test/faulty')
    Session.objects.create(id='healthy', project=project, provider=Provider.CLAUDE_CODE, mtime=10, file_path='/test/healthy')
    path = tmp_path / 'recovery.sqlite3'
    with closing(sqlite3.connect(path)) as destination:
        connection.connection.backup(destination)
        destination.execute('PRAGMA journal_mode=WAL')
    monkeypatch.setenv('RECOVERY_TEST_DB', str(path))

    def configured(function, args, kwargs):
        connections['default'].settings_dict = {**connections['default'].settings_dict, 'NAME': str(path)}
        return function(*args, **kwargs)

    # Historical preparation owns a separate worker and must read this WAL copy.
    from twicc.providers.statistics_runtime import StatisticsConsumer
    original_read = StatisticsConsumer._read

    async def preparation(consumer, function, /, *args):
        return await original_read(consumer, configured, function, args, {})

    monkeypatch.setattr(StatisticsConsumer, '_read', preparation)
    ordinary = db_writer.run_compute_sync
    application = compute_executor.run_compute_application_sync

    async def writer(function, /, *args, **kwargs):
        return await ordinary(configured, function, args, kwargs)

    async def pinned(function, /, *args, **kwargs):
        return await application(configured, function, args, kwargs)

    def reader(function):
        return sync_to_async(lambda: configured(function, (), {}), thread_sensitive=False)

    monkeypatch.setattr(background, 'sync_to_async', reader)
    monkeypatch.setattr(db_writer, 'run_compute_sync', writer)
    monkeypatch.setattr(compute_executor, 'run_compute_application_sync', pinned)
    monkeypatch.setattr(db_writer, 'run_compute_application_sync', pinned)
    progress = []

    async def broadcast(*args, **kwargs):
        if args and args[0] == 'background_compute':
            progress.append((args[1:3], kwargs.get('completed', False)))

    monkeypatch.setattr(background, 'broadcast_startup_progress', broadcast)
    monkeypatch.setattr(db_writer, 'broadcast_startup_progress', broadcast)
    monkeypatch.setattr(db_writer, 'broadcast_session_updated', broadcast)
    return path, progress


def versions(path):
    with closing(sqlite3.connect(path)) as database:
        return dict(database.execute('SELECT id, compute_version FROM core_session'))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['initialization', 'apply', 'compute', 'process', 'retirement'])
def test_real_pipeline_recovers_and_preserves_healthy_progress(recovery_runtime, monkeypatch, failure):
    path, progress = recovery_runtime
    from twicc.core.content_storage import extraction
    from twicc.providers.compute_base import BaseSessionCompute

    failed = False
    initialized = []
    initialize = extraction.initialize_compute_session

    def initialize_with_failure(session_id):
        nonlocal failed
        initialized.append((session_id, Session.objects.get(pk=session_id).compute_version))
        if failure == 'initialization' and session_id == 'faulty' and not failed:
            failed = True
            raise RuntimeError('one initialization failure')
        return initialize(session_id)

    monkeypatch.setattr(extraction, 'initialize_compute_session', initialize_with_failure)
    apply = BaseSessionCompute._apply_session_complete_final

    def apply_with_failure(message):
        nonlocal failed
        if failure == 'apply' and message['session_id'] == 'faulty' and not failed:
            failed = True
            raise RuntimeError('one apply failure')
        return apply(message)

    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(apply_with_failure))
    if failure == 'compute':
        monkeypatch.setenv('RECOVERY_TEST_FAIL_ONCE', str(path.parent / 'compute-failed'))
    if failure == 'process':
        monkeypatch.setenv('RECOVERY_TEST_CRASH', 'faulty')

    if failure == 'retirement':
        monkeypatch.setenv('RECOVERY_TEST_CRASH_ONCE', str(path.parent / 'crashed-once'))
        retire = background.retire_compute_run
        retirement_failed = False
        async def temporary_retirement_failure(ctx, completion=None):
            nonlocal retirement_failed
            if ctx.process is not None and not retirement_failed:
                retirement_failed = True
                raise RuntimeError('one retirement failure')
            await retire(ctx, completion)
        monkeypatch.setattr(background, 'retire_compute_run', temporary_retirement_failure)

    streams = []
    create_stream = db_writer.create_compute_result_stream

    def track_stream(*args, **kwargs):
        stream = create_stream(*args, **kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(db_writer, 'create_compute_result_stream', track_stream)

    async def scenario():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx))
        try:
            if failure == 'process':
                async with asyncio.timeout(15):
                    while versions(path)['healthy'] != 123:
                        await asyncio.sleep(.02)
                assert versions(path)['faulty'] != 123
                assert not ctx.stop_event.is_set()
                assert not any(done for _, done in progress)
                monkeypatch.delenv('RECOVERY_TEST_CRASH')
            await asyncio.wait_for(task, 20)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not ctx.stop_event.is_set()
            assert not any(sid == 'healthy' and version == 123 for sid, version in initialized)
            assert progress[-1] == ((2, 2), True)
            assert all(total == 2 for (_, total), _ in progress)
            assert [current for (current, _), _ in progress] == sorted(
                current for (current, _), _ in progress
            )
            assert ((1, 2), False) in progress
            assert ctx.process is None
            assert len(streams) >= (2 if failure in {'process', 'retirement'} else 1)
            assert len({stream.run_id for stream in streams}) == len(streams)
            assert len({id(stream.reader) for stream in streams}) == len(streams)
            assert all(stream.reader.closed for stream in streams)
            assert not any(t.name.startswith('compute-receiver-') for t in threading.enumerate())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())

@pytest.mark.django_db(transaction=True)
def test_retirement_drains_writer_abandonment_through_repeated_cancellation(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    abandon = db_writer._finalize_abandoned_run

    async def delayed_abandon(run_id, provider):
        entered.set()
        await release.wait()
        await abandon(run_id, provider)

    monkeypatch.setattr(db_writer, '_finalize_abandoned_run', delayed_abandon)

    async def scenario():
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(Provider.CLAUDE_CODE, set(), 0)
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, 'unused:factory', run_id=run_id)
        cleanup = asyncio.create_task(background.retire_compute_run(ctx, completion))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            cleanup.cancel()
            await asyncio.sleep(0)
            cleanup.cancel()
            await asyncio.sleep(0)
            assert not cleanup.done()
            assert db_writer._compute_states[run_id].abandoned
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(cleanup, 2)
            assert completion.cancelled()
            assert run_id not in db_writer._compute_states
            assert not ctx.stop_event.is_set()
            new_id, fresh = db_writer.arm_compute_completion(Provider.CLAUDE_CODE, set(), 0)
            # A late old done marker cannot settle or remove the new run.
            await db_writer._process_compute_message({
                'type': 'done', 'provider': 'claude_code', 'run_id': run_id,
            })
            assert not fresh.done()
            assert new_id in db_writer._compute_states
            ctx.run_id = new_id
            await background.retire_compute_run(ctx, fresh)
        finally:
            release.set()
            await asyncio.gather(cleanup, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_claude_startup_barrier_allows_indexing_during_permanent_recovery(recovery_runtime, monkeypatch):
    from types import SimpleNamespace
    import importlib
    cli_run = importlib.import_module('twicc.cli.run')
    from twicc.core.content_storage import extraction
    path, progress = recovery_runtime
    original = extraction.initialize_compute_session
    def initialize(sid):
        if sid == 'faulty':
            raise RuntimeError('permanent initialization failure')
        return original(sid)
    monkeypatch.setattr(extraction, 'initialize_compute_session', initialize)
    indexed = asyncio.Event()
    async def kickoff():
        indexed.set()
    monkeypatch.setattr(cli_run, 'init_search_index', lambda: None)
    monkeypatch.setattr(cli_run, 'remove_obsolete_session_documents', lambda: asyncio.sleep(0, result=0))
    monkeypatch.setattr(cli_run, 'start_pending_session_reindexes', lambda: None)
    monkeypatch.setattr(cli_run, 'kick_off_search_indexing', kickoff)
    async def run():
        db_writer.start_db_writer()
        barrier = asyncio.Event()
        ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123, f'{__name__}:RecoveryCompute')
        task = asyncio.create_task(background.start_background_compute_task(ctx, initial_done=barrier))
        registry = SimpleNamespace(wait_initial_sync_done=lambda: asyncio.sleep(0), wait_compute_done=barrier.wait)
        search = asyncio.create_task(cli_run._orchestrate_global_search(
            registry, asyncio.Event(), asyncio.Event(), lambda: None,
        ))
        try:
            await asyncio.wait_for(search, 25)
            assert indexed.is_set() and barrier.is_set()
            assert not task.done() and not ctx.stop_event.is_set()
            assert versions(path) == {'faulty': None, 'healthy': 123}
            assert not any(completed for _, completed in progress)
        finally:
            task.cancel()
            search.cancel()
            await asyncio.gather(task, search, return_exceptions=True)
            await background.retire_compute_run(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(run())
