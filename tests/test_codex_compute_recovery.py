import asyncio
from types import SimpleNamespace

from twicc.providers.codex import background_compute
from twicc.providers.codex.background_compute import CodexComputeCoordinator, FailedCandidate
from twicc.providers.codex.migration_gate import gate_for
from twicc.providers.db_writer import ComputeApplied


import os
import queue
import contextlib
from pathlib import Path

import orjson
import pytest
from django.db import connections
from twicc.core.enums import Provider
from twicc.providers import background_compute_task as background, db_writer
from twicc.providers.codex.background_compute import PreparedCandidate, CodexComputeCandidate
from twicc.core.models import Session, SessionType
from tests.test_background_compute_recovery import versions

pytest_plugins = ["tests.test_background_compute_recovery"]


@pytest.fixture
def codex_recovery_runtime(recovery_runtime):
    import sqlite3
    from contextlib import closing
    path, progress = recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET provider='codex'")
        database.commit()
    return path, progress


def coordinator():
    return CodexComputeCoordinator(SimpleNamespace(stop_event=asyncio.Event(), compute_version=2, run_id=2), asyncio.Event())


def test_failed_initial_session_releases_barrier_without_repair_completion(monkeypatch):
    async def run():
        owner = coordinator()
        owner.initial_ids = {'failed'}
        owner._initial_total = 1
        progress = []
        async def broadcast(*args, **kwargs):
            progress.append(kwargs.get('completed', False))
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._record_failure(FailedCandidate('failed', 'initialize', 'disk full'))
        assert owner.initial_done.is_set()
        assert owner.initial_classified == {'failed'}
        assert progress and not any(progress)
        assert owner._retry_at['failed'] > background_compute.time.monotonic()
    asyncio.run(run())


def test_old_apply_cannot_release_new_migration():
    async def run():
        owner = coordinator()
        lease = gate_for('late-apply')
        await lease.acquire()
        owner.migration_leases['late-apply'] = lease
        owner.submitted.add('late-apply')
        try:
            await owner._handle_applied(ComputeApplied('late-apply', 'applied', run_id=1))
            assert lease.locked()
            assert owner.submitted == {'late-apply'}
            assert not owner.stats
        finally:
            owner._release_lease('late-apply', replay=False, outcome='cancelled')
    asyncio.run(run())


def test_old_worker_status_cannot_clear_new_submission():
    async def run():
        owner = coordinator()
        owner.in_flight = 'session'
        owner.submitted.add('session')
        await owner._handle_worker_status({'type': 'computed', 'session_id': 'session', '_run_id': 1})
        assert owner.in_flight == 'session'
        assert owner.submitted == {'session'}
    asyncio.run(run())


def test_retirement_fences_writer_before_releasing_migration(monkeypatch):
    async def run():
        owner = coordinator()
        owner.run_active = True
        lease = gate_for('retired')
        await lease.acquire()
        owner.migration_leases['retired'] = lease
        owner.migrated_history.add('retired')
        owner.submitted.add('retired')
        completion = asyncio.get_running_loop().create_future()
        owner._done_future = completion
        entered, drain = asyncio.Event(), asyncio.Event()
        async def retire(ctx, future):
            assert future is completion
            entered.set()
            await drain.wait()
            future.cancel()
        monkeypatch.setattr(background_compute, 'retire_compute_run', retire)
        task = asyncio.create_task(owner._retire_run())
        await entered.wait()
        assert lease.locked()
        assert not owner.run_active
        drain.set()
        await task
        assert not lease.locked()
        assert owner._forced_rebuild == {'retired'}
        assert not owner.submitted
        assert completion.cancelled()
    asyncio.run(run())


class CodexRecoveryCompute:
    provider = Provider.CODEX

    def __init__(self):
        connections['default'].settings_dict = {
            **connections['default'].settings_dict, 'NAME': os.environ['RECOVERY_TEST_DB'],
        }

    def enable_batch_queue_cache(self):
        pass

    def compute_session_metadata(self, session_id, result_queue, run_id):
        if os.environ.get('RECOVERY_TEST_CRASH') == session_id:
            os._exit(42)
        session = Session.objects.get(pk=session_id)
        result_queue.put(orjson.dumps({
            'type': 'session_complete', 'provider': 'codex', 'run_id': run_id,
            'session_id': session_id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'session_fields': {'compute_version': 123},
        }))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['initialize', 'apply', 'process'])
def test_real_codex_pipeline_recovers_without_restart(codex_recovery_runtime, monkeypatch, failure):
    path, progress = codex_recovery_runtime
    from twicc.core.content_storage import extraction
    from twicc.providers.compute_base import BaseSessionCompute
    failed = False
    original_initialize = extraction.initialize_compute_session
    attempts = []

    def initialize(sid):
        nonlocal failed
        attempts.append((sid, Session.objects.get(pk=sid).compute_version))
        if failure == 'initialize' and sid == 'faulty' and not failed:
            failed = True
            raise RuntimeError('one initialization failure')
        return original_initialize(sid)

    original_apply = BaseSessionCompute._apply_session_complete_final

    def apply(message):
        nonlocal failed
        if failure == 'apply' and message['session_id'] == 'faulty' and not failed:
            failed = True
            raise RuntimeError('one final apply failure')
        return original_apply(message)

    monkeypatch.setattr(extraction, 'initialize_compute_session', initialize)
    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(apply))
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)

    async def broadcast(*args, **kwargs):
        progress.append((args[1:3], kwargs.get('completed', False)))
    monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
    if failure == 'process':
        monkeypatch.setenv('RECOVERY_TEST_CRASH', 'faulty')

    async def run():
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event())
        classification_entered, classification_release = asyncio.Event(), asyncio.Event()
        if failure == 'process':
            classify = owner._classify_initial
            async def controlled_classification(sid, **kwargs):
                if sid == 'healthy':
                    classification_entered.set()
                    await classification_release.wait()
                await classify(sid, **kwargs)
            owner._classify_initial = controlled_classification
        releases = []
        owner.on_migration_released = releases.append
        async def load(_version, *, forced_ids=frozenset()):
            current = versions(path)
            return [CodexComputeCandidate(sid, Path('/test') / sid, SessionType.SESSION, 10)
                    for sid in ['faulty', 'healthy'] if current[sid] != 123 or sid in forced_ids]
        monkeypatch.setattr(background_compute, '_load_stale_candidates', load)
        async def prepare(candidate):
            sid = candidate.session_id
            if sid != 'faulty':
                return PreparedCandidate(sid, None, False)
            lease = gate_for(sid)
            await lease.acquire()
            owner.migration_paths[sid] = candidate.file_path
            owner._forced_rebuild.add(sid)
            return PreparedCandidate(sid, lease, True, kind='replaced')
        owner.prepare_candidate = prepare
        db_writer.start_db_writer()
        task = asyncio.create_task(owner.run())
        try:
            async with asyncio.timeout(15):
                while versions(path)['healthy'] != 123:
                    if task.done():
                        await task
                    await asyncio.sleep(.02)
            if failure == 'process':
                assert versions(path)['faulty'] != 123
                await asyncio.wait_for(classification_entered.wait(), 2)
                # SQLite readiness precedes coordinator classification.
                assert not owner.initial_done.is_set()
                assert not any(done for _, done in progress)
                monkeypatch.delenv('RECOVERY_TEST_CRASH')
                classification_release.set()
                await asyncio.wait_for(owner.initial_done.wait(), 2)
                assert owner.initial_done.is_set()
            async with asyncio.timeout(15):
                while versions(path)['faulty'] != 123 or owner.failures:
                    if task.done():
                        await task
                    await asyncio.sleep(.02)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not task.done() and not ctx.stop_event.is_set()
            assert not owner._forced_rebuild
            assert not gate_for('faulty').locked()
            assert [release.outcome for release in releases if release.session_id == 'faulty'][-1] == 'ready'
            assert not any(sid == 'healthy' and version == 123 for sid, version in attempts)
        finally:
            classification_release.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_cancellation_during_retirement_drains_lease_cleanup(monkeypatch):
    async def run():
        owner = coordinator()
        owner.run_active = True
        lease = gate_for('cancel-retirement')
        await lease.acquire()
        owner.migration_leases['cancel-retirement'] = lease
        entered, drain = asyncio.Event(), asyncio.Event()
        async def retire(ctx, future):
            entered.set()
            try:
                await drain.wait()
            except asyncio.CancelledError:
                await drain.wait()
                raise
        monkeypatch.setattr(background_compute, 'retire_compute_run', retire)
        task = asyncio.create_task(owner._retire_run())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert lease.locked()
        drain.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lease.locked()
        assert not owner.run_active
    asyncio.run(run())


def test_initial_candidate_read_recovers_and_stop_does_not_start_worker(monkeypatch):
    async def run():
        owner = coordinator()
        reads = 0
        async def load(*args, **kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                raise RuntimeError('temporary read failure')
            owner.ctx.stop_event.set()
            return []
        async def broadcast(*args, **kwargs):
            pass
        monkeypatch.setattr(background_compute, '_load_stale_candidates', load)
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        monkeypatch.setattr(background_compute, 'stop_background_task', lambda ctx: asyncio.sleep(0))
        await owner.run()
        assert reads == 2
        assert not owner.run_active
    asyncio.run(run())


def test_failure_cooldown_is_bounded_and_keeps_session_eligible():
    async def run():
        owner = coordinator()
        for _ in range(10):
            before = background_compute.time.monotonic()
            await owner._record_failure(FailedCandidate('bad', 'compute', 'broken'))
            after = background_compute.time.monotonic()
            assert 0 < owner._retry_at['bad'] - before <= 60 + after - before
        assert owner._retry_delays['bad'] == 60
        assert not owner.failures['bad'].unavailable_reason
        assert not owner.migration_leases
    asyncio.run(run())


def test_old_completion_callback_keeps_new_run_state(monkeypatch):
    async def run():
        owner = coordinator()
        owner.ctx.command_queue = queue.Queue()
        owner.ctx.status_queue = queue.Queue()
        first, second = (asyncio.get_running_loop().create_future() for _ in range(2))
        completions = iter([(1, first), (2, second)])
        monkeypatch.setattr(background_compute, 'arm_compute_completion', lambda *a, **kw: next(completions))
        monkeypatch.setattr(background_compute, 'start_compute_process', lambda ctx: None)
        owner._start_run()
        owner._status_pump.cancel()
        await asyncio.gather(owner._status_pump, return_exceptions=True)
        owner._retired_run_ids.add(1)
        owner._start_run()
        owner.submitted.add('new')
        owner.in_flight = 'new'
        first.set_exception(RuntimeError('late old transport failure'))
        await asyncio.sleep(0)
        event_type, payload = await owner.events.get()
        assert event_type == 'worker'
        await owner._handle_worker_status(payload)
        assert owner.in_flight == 'new' and owner.submitted == {'new'}
        assert owner._done_future is second and owner.run_active
        owner._status_pump.cancel()
        await asyncio.gather(owner._status_pump, return_exceptions=True)
        second.cancel()
    asyncio.run(run())


def test_new_rebuild_request_survives_older_migrated_apply():
    async def run():
        owner = coordinator()
        owner.migrated_history.add('rebuild')
        owner._forced_rebuild.add('rebuild')
        owner._rebuild_generation['rebuild'] = 2
        owner._prepared_rebuild_generation['rebuild'] = 1
        await owner._handle_applied(ComputeApplied('rebuild', 'applied', run_id=2))
        assert 'rebuild' in owner._forced_rebuild
    asyncio.run(run())


def test_failed_migration_emits_one_release():
    async def run():
        owner = coordinator()
        releases = []
        owner.on_migration_released = releases.append
        lease = gate_for('one-release')
        await lease.acquire()
        owner.migration_leases['one-release'] = lease
        owner.migration_paths['one-release'] = Path('/test/one-release')
        owner._release_lease('one-release', replay=False, outcome='failed')
        await owner._record_failure(FailedCandidate('one-release', 'initialize', 'disk full'))
        assert len(releases) == 1
        assert not lease.locked()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_other_provider_and_foreground_write_finish_while_codex_cools(recovery_runtime, monkeypatch):
    import sqlite3
    from contextlib import closing
    from twicc.core.content_storage import extraction
    path, _progress = recovery_runtime
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE core_session SET provider='codex' WHERE id='faulty'")
        db.commit()
    original = extraction.initialize_compute_session
    attempts = 0
    def initialize(sid):
        nonlocal attempts
        if sid == 'faulty':
            attempts += 1
            raise RuntimeError('persistent faulty initialization')
        return original(sid)
    monkeypatch.setattr(extraction, 'initialize_compute_session', initialize)
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(background_compute, 'broadcast_startup_progress', lambda *a, **kw: asyncio.sleep(0))
    async def load(*args, **kwargs):
        return [CodexComputeCandidate('faulty', Path('/test/faulty'), SessionType.SESSION, 10)]
    monkeypatch.setattr(background_compute, '_load_stale_candidates', load)
    async def run():
        codex_ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(codex_ctx, asyncio.Event())
        owner.prepare_candidate = lambda candidate: _prepared(candidate.session_id)
        claude_ctx = background.ComputeContext(Provider.CLAUDE_CODE, 123,
            'tests.test_background_compute_recovery:RecoveryCompute')
        db_writer.start_db_writer()
        codex_task = asyncio.create_task(owner.run())
        claude_task = None
        try:
            async with asyncio.timeout(5):
                while 'faulty' not in owner._retry_at:
                    await asyncio.sleep(.01)
            assert not gate_for('faulty').locked()
            claude_task = asyncio.create_task(background.start_background_compute_task(claude_ctx))
            future = asyncio.get_running_loop().create_future()
            job = db_writer._RetireSessionsJob(None, {'healthy': {'title': 'foreground'}}, future)
            assert await asyncio.wait_for(db_writer.submit_async_job(job), 2) == 1
            await asyncio.wait_for(claude_task, 10)
            assert versions(path)['healthy'] == 123
            with closing(sqlite3.connect(path)) as db:
                assert db.execute("SELECT title FROM core_session WHERE id='healthy'").fetchone() == ('foreground',)
            assert not codex_task.done() and owner.initial_done.is_set()
            import importlib
            cli_run = importlib.import_module('twicc.cli.run')
            indexed = asyncio.Event()
            async def kick_off():
                indexed.set()
            async def wait_compute():
                await owner.initial_done.wait()
                await claude_task
            registry = SimpleNamespace(wait_initial_sync_done=lambda: asyncio.sleep(0), wait_compute_done=wait_compute)
            monkeypatch.setattr(cli_run, 'init_search_index', lambda: None)
            monkeypatch.setattr(cli_run, 'remove_obsolete_session_documents', lambda: asyncio.sleep(0, result=0))
            monkeypatch.setattr(cli_run, 'start_pending_session_reindexes', lambda: None)
            monkeypatch.setattr(cli_run, 'kick_off_search_indexing', kick_off)
            ready = asyncio.Event()
            await asyncio.wait_for(cli_run._orchestrate_global_search(
                registry, asyncio.Event(), ready, lambda: None,
            ), 2)
            assert ready.is_set() and indexed.is_set()
            assert not codex_task.done() and versions(path)['faulty'] != 123
            assert 1 <= attempts <= 5
        finally:
            codex_task.cancel()
            if claude_task is not None:
                claude_task.cancel()
            await asyncio.gather(codex_task, *([claude_task] if claude_task else []), return_exceptions=True)
            await background.retire_compute_run(claude_ctx)
            await db_writer.stop_db_writer()
    asyncio.run(run())


async def _prepared(sid):
    return PreparedCandidate(sid, None, False)


def test_failed_anchored_migration_keeps_reconstruction_intent():
    async def run():
        owner = coordinator()
        owner.migrated_history.add('anchored')
        await owner._handle_applied(ComputeApplied('anchored', 'failed', 'apply failed', run_id=2))
        assert 'anchored' in owner._forced_rebuild
    asyncio.run(run())


def test_repeated_cancellation_drains_coordinator_shutdown(monkeypatch):
    async def run():
        owner = coordinator()
        entered, drain, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def stop_runner():
            entered.set()
            await drain.wait()
        owner.runner = SimpleNamespace(stop=stop_runner)
        monkeypatch.setattr(background_compute, '_load_stale_candidates', lambda *a, **kw: _empty_candidates())
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', lambda *a, **kw: asyncio.sleep(0))
        async def stop_context(ctx):
            stopped.set()
        monkeypatch.setattr(background_compute, 'stop_background_task', stop_context)
        task = asyncio.create_task(owner.run())
        await owner.initial_done.wait()
        task.cancel()
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        drain.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert all(pump.done() for pump in owner._pumps)
    asyncio.run(run())


async def _empty_candidates():
    return []


def test_retirement_failure_retries_before_releasing_old_lease(monkeypatch):
    async def run():
        owner = coordinator()
        lease = gate_for('retire-retry')
        await lease.acquire()
        owner.migration_leases['retire-retry'] = lease
        attempts, delays = [], []
        async def retire(ctx, future):
            attempts.append(lease.locked())
            if len(attempts) == 1:
                raise RuntimeError('temporary abandonment failure')
        async def wait(stop, delay):
            delays.append(delay)
            assert lease.locked()
            return True
        monkeypatch.setattr(background_compute, 'retire_compute_run', retire)
        monkeypatch.setattr(background_compute, '_wait_for_retry_delay', wait)
        try:
            await owner._retire_run()
            assert attempts == [True, True]
            assert delays == [1.0]
            assert not lease.locked()
        finally:
            owner._release_lease('retire-retry', replay=False, outcome='cancelled')
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_missing_rollout_restored_without_rebuild_or_restart(codex_recovery_runtime, monkeypatch):
    import sqlite3
    from contextlib import closing
    from twicc.core.content_storage import extraction
    from twicc.providers.codex import rollout_migration
    path, progress = codex_recovery_runtime
    rollout = path.parent / 'restored.jsonl'
    diagnosed_initializations = []
    original = extraction.initialize_compute_session
    def initialize(sid):
        diagnosed_initializations.append(Session.objects.get(pk=sid).unavailable_reason)
        if len(diagnosed_initializations) == 1:
            raise RuntimeError('fail after history preparation')
        return original(sid)
    monkeypatch.setattr(extraction, 'initialize_compute_session', initialize)
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(background_compute.search, 'is_initialized', lambda: False)
    async def broadcast(*args, **kwargs):
        progress.append((args[1:3], kwargs.get('completed', False)))
    monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
    async def load(*args, forced_ids=frozenset(), **kwargs):
        with closing(sqlite3.connect(path)) as db:
            offset = db.execute("SELECT last_offset FROM core_session WHERE id='faulty'").fetchone()[0]
        return [CodexComputeCandidate('faulty', rollout, SessionType.SESSION, offset)] if (
            versions(path)['faulty'] != 123 or 'faulty' in forced_ids
        ) else []
    monkeypatch.setattr(background_compute, '_load_stale_candidates', load)
    def unavailable():
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT unavailable_reason FROM core_session WHERE id='faulty'").fetchone()[0]
    async def run():
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event())
        owner._is_agent_active = lambda sid: False
        def database_mode(sid):
            from twicc.core.models import SessionItem
            from tests.content_storage_helpers import read_current
            first = SessionItem.objects.filter(session_id=sid).order_by('line_num').first()
            if first is None:
                raise rollout_migration.RolloutMigrationError('No first item')
            return rollout_migration.history_mode_from_record(orjson.loads(read_current(first)))
        owner._database_mode = lambda candidate: background.sync_to_async(lambda: database_mode(candidate.session_id))()
        # Initial empty history needs the normal offset-zero classification fallback.
        original_mode = owner._database_mode
        async def mode(candidate):
            try:
                return await original_mode(candidate)
            except rollout_migration.RolloutMigrationError:
                if candidate.last_offset == 0:
                    return rollout_migration.HistoryMode.LEGACY
                raise
        owner._database_mode = mode
        db_writer.start_db_writer()
        task = asyncio.create_task(owner.run())
        try:
            async with asyncio.timeout(5):
                while 'faulty' not in owner.failures:
                    await asyncio.sleep(.01)
            assert unavailable() == 'rollout_missing'
            assert not any(completed for _, completed in progress)
            rollout.write_bytes(orjson.dumps({'type': 'session_meta', 'payload': {
                'id': 'faulty', 'cwd': '/test', 'history_mode': 'paginated',
            }}) + b'\n')
            try:
                async with asyncio.timeout(8):
                    while versions(path)['faulty'] != 123 or owner.failures or not progress[-1][1]:
                        if task.done():
                            await task
                        await asyncio.sleep(.01)
            except TimeoutError:
                pytest.fail(f'Recovery timeout: {owner.failures!r}; initializations={diagnosed_initializations!r}')
            assert diagnosed_initializations == ['rollout_missing', 'rollout_missing', 'rollout_missing']
            assert unavailable() is None
            assert not task.done() and not ctx.stop_event.is_set()
            assert not owner._forced_rebuild and not gate_for('faulty').locked()
            assert progress[-1][1]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_current_session_with_unavailable_diagnosis_remains_retryable(provider_home):
    from twicc.core.models import Project
    session = Session.objects.create(id='diagnosed-current', provider=Provider.CODEX,
        project=Project.objects.create(id='diagnosed-current-project'), file_path='diagnosed.jsonl',
        compute_version=123, unavailable_reason='rollout_missing')
    candidates = asyncio.run(background_compute._load_stale_candidates(123))
    assert [candidate.session_id for candidate in candidates] == [session.id]
    assert candidates[0].unavailable_reason == 'rollout_missing'


def test_diagnosed_superseded_metadata_keeps_bounded_repair_pending(monkeypatch):
    async def run():
        owner = coordinator()
        sid = 'diagnosed-superseded'
        owner.initial_ids = {sid}
        owner._initial_total = 1
        owner._unavailable_diagnoses[sid] = 'rollout_missing'
        owner.failures[sid] = FailedCandidate(sid, 'classify', 'missing', 'rollout_missing')
        owner._prepared[sid] = PreparedCandidate(sid, None, False)
        progress = []
        async def broadcast(*args, **kwargs):
            progress.append(kwargs['completed'])
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._handle_applied(ComputeApplied(sid, 'superseded', run_id=2))
        assert owner.initial_done.is_set()
        assert owner._unavailable_diagnoses == {sid: 'rollout_missing'}
        assert sid in owner.failures
        assert 0 < owner._retry_at[sid] - background_compute.time.monotonic() <= 60
        assert owner._retry_delays[sid] <= 60
        assert sid not in owner._prepared
        assert progress and not any(progress)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('deleted', [False, True])
def test_diagnosed_missing_ack_rechecks_database_before_retaining_retry(monkeypatch, deleted):
    from twicc.core.models import Project
    sid = 'diagnosed-missing'
    session = Session.objects.create(id=sid, provider=Provider.CODEX,
        project=Project.objects.create(id='diagnosed-missing-project'), file_path='missing.jsonl',
        unavailable_reason='rollout_missing')
    if deleted:
        session.delete()
    async def run():
        owner = coordinator()
        owner.initial_ids = {sid}
        owner._initial_total = 1
        owner._unavailable_diagnoses[sid] = 'rollout_missing'
        owner.failures[sid] = FailedCandidate(sid, 'classify', 'missing', 'rollout_missing')
        owner._forced_rebuild.add(sid)
        progress = []
        async def broadcast(*args, **kwargs):
            progress.append(kwargs['completed'])
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._handle_applied(ComputeApplied(sid, 'missing', run_id=2))
        assert owner.initial_done.is_set()
        if deleted:
            assert not owner._unavailable_diagnoses and not owner.failures
            assert not owner._retry_at and not owner._forced_rebuild
            assert progress == [False]
        else:
            assert owner._unavailable_diagnoses == {sid: 'rollout_missing'}
            assert sid in owner.failures
            assert owner._retry_at[sid] > background_compute.time.monotonic()
            assert progress == [False]
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_deleted_cooling_diagnosis_releases_barrier_without_false_ready(monkeypatch):
    async def run():
        owner = coordinator()
        sid = 'deleted-during-cooldown'
        owner.initial_ids = {sid}
        owner._initial_total = 1
        owner._unavailable_diagnoses[sid] = 'rollout_missing'
        owner.failures[sid] = FailedCandidate(sid, 'classify', 'missing', 'rollout_missing')
        owner._retry_at[sid] = background_compute.time.monotonic() + 60
        owner._forced_rebuild.add(sid)
        progress = []
        async def broadcast(*args, **kwargs):
            progress.append(kwargs['completed'])
        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._prune_deleted_recovery(set())
        assert not owner._unavailable_diagnoses and not owner.failures
        assert not owner._retry_at and not owner._forced_rebuild
        assert owner.initial_done.is_set() and progress == [False]
    asyncio.run(run())

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('version, expected_current', [(None, 0), (2, 1)])
def test_superseded_progress_counts_database_ready_sessions(monkeypatch, version, expected_current):
    from twicc.core.models import Project
    Session.objects.create(id='progress-session', provider=Provider.CODEX, compute_version=version,
                           project=Project.objects.create(id='progress-project'))

    async def run():
        owner = coordinator()
        owner.initial_ids = {'progress-session'}
        owner._initial_total = 1
        progress = []

        async def broadcast(phase, current, total, **kwargs):
            progress.append((current, total, kwargs.get('completed', False)))

        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._handle_applied(ComputeApplied('progress-session', 'superseded', run_id=2))
        await owner._classify_initial('progress-session')
        assert owner.initial_done.is_set()
        assert progress == [(expected_current, 1, bool(expected_current))] * 2

    asyncio.run(run())

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('pending', ['failure', 'deferred', 'rebuild', 'diagnosis'])
def test_pending_repair_does_not_advance_ready_progress(monkeypatch, pending):
    from twicc.core.models import Project
    Session.objects.create(id='pending', project=Project.objects.create(id='pending-project'),
                           provider=Provider.CODEX, compute_version=2)

    async def run():
        owner = coordinator()
        owner.initial_ids = {'pending'}
        owner._initial_total = 1
        if pending == 'failure':
            owner.failures['pending'] = FailedCandidate('pending', 'compute', 'failed')
        elif pending == 'deferred':
            owner.deferred.add('pending')
        elif pending == 'rebuild':
            owner._forced_rebuild.add('pending')
        else:
            owner._unavailable_diagnoses['pending'] = 'rollout_missing'
        progress = []

        async def broadcast(phase, current, total, **kwargs):
            progress.append((current, total, kwargs.get('completed', False)))

        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._classify_initial('pending')
        assert progress == [(0, 1, False)]
        assert owner.initial_done.is_set()

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_external_ready_confirmation_excludes_subagents_and_deduplicates(monkeypatch):
    from twicc.core.models import Project
    project = Project.objects.create(id='external-project')
    Session.objects.create(id='root', project=project, provider=Provider.CODEX, compute_version=2)
    Session.objects.create(id='child', project=project, provider=Provider.CODEX, compute_version=2, file_path='child',
                           type=SessionType.SUBAGENT)

    async def run():
        owner = coordinator()
        owner.initial_ids = {'root'}
        owner._initial_total = 1
        progress = []

        async def broadcast(phase, current, total, **kwargs):
            progress.append((current, total))

        monkeypatch.setattr(background_compute, 'broadcast_startup_progress', broadcast)
        await owner._confirm_initial_ready({'root', 'child'})
        await owner._classify_initial('root', confirm_ready=False)
        await owner._classify_initial('child')
        await owner._classify_initial('root')
        assert owner.initial_ready == {'root'}
        assert progress == [(1, 1)] * 3

    asyncio.run(run())
