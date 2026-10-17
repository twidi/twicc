import asyncio
from twicc.providers.compute_work import ComputeWorkState
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


@pytest.mark.parametrize('retired_run', [1, 2])
def test_old_apply_cannot_release_new_migration(retired_run):
    async def run():
        owner = coordinator()
        owner._retired_run_ids.add(retired_run)
        lease = gate_for('late-apply')
        await lease.acquire()
        owner.migration_leases['late-apply'] = lease
        owner.submitted.add('late-apply')
        try:
            await owner._handle_applied(ComputeApplied('late-apply', 'applied', run_id=retired_run))
            assert lease.locked()
            assert owner.submitted == {'late-apply'}
            assert not owner.stats
        finally:
            owner._release_lease('late-apply', replay=False, outcome='cancelled')
    asyncio.run(run())


@pytest.mark.parametrize('retired_run', [1, 2])
def test_old_worker_status_cannot_clear_new_submission(retired_run):
    async def run():
        owner = coordinator()
        owner._retired_run_ids.add(retired_run)
        owner.in_flight = 'session'
        owner.submitted.add('session')
        await owner._handle_worker_status({'type': 'computed', 'session_id': 'session', '_run_id': retired_run})
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


from twicc.providers.compute_base import BaseSessionCompute


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
            'tree_completion': getattr(self, '_tree_completion', None),
            'session_id': session_id, 'observed_last_offset': session.last_offset,
            'observed_incarnation': session.storage_incarnation, 'observed_epoch': session.history_epoch,
            'observed_upper_line': session.last_line, 'history_facts': [],
            'force_cost_session_ids': BaseSessionCompute._completed_cost_session_ids(self, session),
            'session_fields': {'compute_version': 123},
        }))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['initialize', 'apply', 'process'])
def test_real_codex_pipeline_recovers_without_restart(codex_recovery_runtime, monkeypatch, failure):
    path, progress = codex_recovery_runtime
    from twicc.core.content_storage import extraction
    from twicc.providers.compute_base import BaseSessionCompute
    failed = False
    state = ComputeWorkState()
    original_initialize = extraction.initialize_compute_session
    attempts = []

    def initialize(sid):
        assert state.has_pending()
        nonlocal failed
        attempts.append((sid, Session.objects.get(pk=sid).compute_version))
        if failure == 'initialize' and sid == 'faulty' and not failed:
            failed = True
            raise RuntimeError('one initialization failure')
        return original_initialize(sid)

    original_apply = BaseSessionCompute._apply_session_complete_final

    def apply(message):
        assert state.has_pending()
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
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
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
        async def load(_version, *, forced_ids=frozenset(), source_ids=None):
            current = versions(path)
            return [CodexComputeCandidate(sid, Path('/test') / sid, SessionType.SESSION, 10)
                    for sid in ['faulty', 'healthy'] if current[sid] != 123 or sid in forced_ids]
        monkeypatch.setattr(background_compute, '_load_stale_candidates', load)
        async def prepare(candidate):
            sid = candidate.session_id
            if sid != 'faulty':
                return PreparedCandidate(sid, None, False)
            assert state.has_pending()
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
                assert state.has_pending()
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
            work_state = ComputeWorkState()
            claude_task = asyncio.create_task(background.start_background_compute_task(claude_ctx, work_state=work_state))
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
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.commit()
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
            # Restored history is stable before its first replacement attempt.
            os.utime(rollout, (1, 1))
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


@pytest.mark.parametrize('outcome', ['applied', 'failed'])
def test_early_apply_retains_submitted_slot_until_worker_status(outcome):
    async def run():
        owner = coordinator()
        owner.submitted.add('early')
        owner.in_flight = 'early'
        owner.computed_not_applied.add('other')
        await owner._handle_applied(ComputeApplied('early', outcome, run_id=2))
        assert owner.submitted == {'early'}
        assert owner.computed_not_applied == {'other'}
        assert owner.applied_before_computed == {'early'}
        assert owner.in_flight == 'early'
        await owner._handle_worker_status({'type': 'computed', 'session_id': 'early', '_run_id': 2})
        assert not owner.submitted
        assert owner.computed_not_applied == {'other'}
        assert not owner.applied_before_computed
        assert owner.in_flight is None
    asyncio.run(run())


class CodexAdmissionFailureCompute(CodexRecoveryCompute):
    """Fail the second worker result once while the first result awaits application."""

    def compute_session_metadata(self, session_id, result_queue, run_id):
        marker = Path(os.environ['RECOVERY_TEST_FAIL_ONCE'])
        if session_id == 'healthy' and not marker.exists():
            marker.touch()
            raise RuntimeError('one pending compute failure')
        super().compute_session_metadata(session_id, result_queue, run_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('stop_at_capacity', [False, True])
def test_codex_failed_result_keeps_admission_bounded(codex_recovery_runtime, monkeypatch, stop_at_capacity):
    import threading
    from tests.test_compute_age_selection import clone_session
    from twicc.providers.compute_base import BaseSessionCompute

    path, _ = codex_recovery_runtime
    clone_session(path, 'third', 1)
    # Bounded overlap is within one native tree, at a shared launcher depth.
    clone_session(path, 'root', 0)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET parent_session_id='root', type='subagent' WHERE id != 'root'")
        database.commit()
    monkeypatch.setenv('RECOVERY_TEST_FAIL_ONCE', str(path.parent / 'failed-once'))
    entered, release = threading.Event(), threading.Event()
    initialized, prepared = [], []
    original_apply = BaseSessionCompute._apply_session_complete_final
    original_submit = background_compute.submit_async_job

    def apply(message):
        if message['session_id'] == 'faulty':
            entered.set()
            if not release.wait(8):
                raise RuntimeError('test did not release application')
        return original_apply(message)

    async def submit(job):
        if isinstance(job, db_writer.InitializeComputeStorageJob):
            initialized.append(job.session_id)
        return await original_submit(job)

    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(apply))
    monkeypatch.setattr(background_compute, 'submit_async_job', submit)
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(background_compute, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexAdmissionFailureCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event())
        async def prepare(candidate):
            prepared.append(candidate.session_id)
            return PreparedCandidate(candidate.session_id, None, False)
        owner.prepare_candidate = prepare
        task = asyncio.create_task(owner.run())
        try:
            async with asyncio.timeout(5):
                while not entered.is_set() or 'healthy' not in owner.worker_errors:
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            await asyncio.sleep(.15)
            assert initialized == ['faulty', 'healthy']
            assert prepared == ['faulty', 'healthy']
            assert owner.submitted | owner.computed_not_applied == {'faulty', 'healthy'}
            assert versions(path)['third'] is None
            if stop_at_capacity:
                ctx.stop_event.set()
                await asyncio.sleep(.05)
                assert not task.done()
                assert initialized == ['faulty', 'healthy']
                release.set()
                await asyncio.wait_for(task, 3)
                assert not owner.submitted and not owner.computed_not_applied
                assert ctx.process is None
            else:
                release.set()
                async with asyncio.timeout(8):
                    while not all(value == 123 for value in versions(path).values()) or owner.failures:
                        if task.done():
                            await task
                        await asyncio.sleep(.01)
                assert initialized.count('healthy') == 2
                assert initialized.count('third') == 1
                ctx.stop_event.set()
                await asyncio.wait_for(task, 3)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_real_early_apply_keeps_slot_while_worker_status_waits(codex_recovery_runtime, monkeypatch):
    path, _ = codex_recovery_runtime
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(background_compute, 'broadcast_startup_progress', background.broadcast_startup_progress)

    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event())
        release_status, applied = asyncio.Event(), asyncio.Event()
        pump = owner._status_pump_loop
        handle_applied = owner._handle_applied
        async def paused_pump(status_queue, run_id):
            await release_status.wait()
            await pump(status_queue, run_id)
        async def record_applied(signal):
            await handle_applied(signal)
            if signal.session_id == 'faulty':
                applied.set()
        owner._status_pump_loop = paused_pump
        owner._handle_applied = record_applied
        owner.prepare_candidate = lambda candidate: _prepared(candidate.session_id)
        task = asyncio.create_task(owner.run())
        try:
            await asyncio.wait_for(applied.wait(), 5)
            assert versions(path) == {'faulty': 123, 'healthy': None}
            assert owner.submitted == {'faulty'}
            assert owner.applied_before_computed == {'faulty'}
            assert owner.in_flight == 'faulty'
            release_status.set()
            await asyncio.wait_for(owner.initial_done.wait(), 5)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            ctx.stop_event.set()
            await asyncio.wait_for(task, 3)
            assert ctx.process is None
            assert not owner.submitted and not owner.computed_not_applied
        finally:
            release_status.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_codex_selection_drains_once_then_wakes_without_inventory_polling(codex_recovery_runtime, monkeypatch):
    import sqlite3
    from contextlib import closing
    path, _ = codex_recovery_runtime
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    state = ComputeWorkState()
    loads = []
    refreshes = []
    load = background_compute._load_stale_candidates
    async def startup_load(*args, **kwargs):
        if kwargs.get("source_ids") is None:
            loads.append(kwargs)
        else:
            refreshes.append(set(kwargs["source_ids"]))
            assert len(refreshes[-1]) == 1
        return await load(*args, **kwargs)
    monkeypatch.setattr(background_compute, '_load_stale_candidates', startup_load)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)

    async def run():
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event())
        owner.work_state = state
        owner.prepare_candidate = lambda candidate: _prepared(candidate.session_id)
        task = asyncio.create_task(owner.run())
        try:
            async with asyncio.timeout(8):
                while versions(path) != {'faulty': 123, 'healthy': 123} or state.has_pending():
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            assert len(loads) == 1
            assert {next(iter(ids)) for ids in refreshes} == {"faulty", "healthy"}
            assert not task.done()
            assert Provider.CODEX in state._source_wakeups
            await asyncio.sleep(.15)
            assert len(loads) == 1
            with closing(sqlite3.connect(path)) as database:
                database.execute("UPDATE core_session SET compute_version=NULL WHERE id='healthy'")
                database.commit()
            state.notify_source_changed(Provider.CODEX, 'healthy')
            async with asyncio.timeout(8):
                while versions(path)['healthy'] != 123 or state.has_pending():
                    await asyncio.sleep(.01)
            assert len(loads) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
        assert not state.has_pending()
        assert not state._source_wakeups
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('recovery', ['rebuild', 'unavailable'])
def test_current_root_recovery_uses_explicit_selection_without_downgrade(
    codex_recovery_runtime, monkeypatch, recovery,
):
    import sqlite3
    from contextlib import closing
    path, _ = codex_recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute('UPDATE core_session SET compute_version=123')
        if recovery == 'unavailable':
            database.execute("UPDATE core_session SET unavailable_reason='rollout_missing' WHERE id='faulty'")
        database.commit()
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    async def run():
        owner = CodexComputeCoordinator(
            background.ComputeContext(Provider.CODEX, 123, 'unused:factory'), asyncio.Event(),
        )
        if recovery == 'rebuild':
            owner._forced_rebuild.add('faulty')
        candidate, forced, pending, deadline = await owner._select_candidate()
        assert candidate.session_id == 'faulty'
        assert forced == {'faulty'} and pending and deadline is None
        assert versions(path) == {'faulty': 123, 'healthy': 123}
        assert not owner.run_active
    asyncio.run(run())


@pytest.mark.parametrize('failed_drain', [False, True])
@pytest.mark.parametrize('explicit_stop', [False, True])
def test_exit_releases_work_gate_only_after_explicit_stop_and_shutdown_drain(
    monkeypatch, failed_drain, explicit_stop,
):
    async def run():
        owner = coordinator()
        owner.work_state = ComputeWorkState()
        entered, release = asyncio.Event(), asyncio.Event()
        async def publish():
            if explicit_stop:
                owner.ctx.stop_event.set()
            raise RuntimeError('progress publication failed')
        async def shutdown():
            entered.set()
            await release.wait()
            if failed_drain:
                raise RuntimeError('writer retirement failed')
            owner._shutdown_drained = True
        monkeypatch.setattr(owner, '_run', publish)
        monkeypatch.setattr(owner, '_shutdown', shutdown)
        task = asyncio.create_task(owner.run())
        await entered.wait()
        assert owner.work_state.has_pending()
        assert Provider.CODEX in owner.work_state._source_wakeups
        release.set()
        with pytest.raises(RuntimeError):
            await task
        assert owner.work_state.has_pending() == (not explicit_stop or failed_drain)
        assert not owner.work_state._source_wakeups
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_unavailable_recovery_reads_one_page_and_advances_past_blocked_roots(
    codex_recovery_runtime, monkeypatch,
):
    import sqlite3
    import time
    from contextlib import closing
    from tests.test_compute_age_selection import clone_session
    path, _ = codex_recovery_runtime
    clone_session(path, 'later-page', 1)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET compute_version=123, unavailable_reason='rollout_missing'")
        database.execute("UPDATE core_session SET mtime=? WHERE id != 'later-page'", (time.time() + 1000,))
        database.commit()
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, '_UNAVAILABLE_RECOVERY_PAGE_SIZE', 2)
    async def run():
        owner = CodexComputeCoordinator(
            background.ComputeContext(Provider.CODEX, 123, 'unused:factory'), asyncio.Event(),
        )
        candidate, _, pending, deadline = await owner._select_candidate()
        assert candidate is None and pending and deadline is not None
        assert set(owner._blocked_roots) == {'faulty', 'healthy'}
        candidate, _, pending, _ = await owner._select_candidate()
        assert candidate.session_id == 'later-page' and pending
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_codex_source_age_deadline_wakes_without_notification(codex_recovery_runtime, monkeypatch):
    import sqlite3
    import time
    from contextlib import closing
    path, _ = codex_recovery_runtime
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET mtime=? WHERE id='faulty'", (time.time() - 29,))
        database.execute("UPDATE core_session SET compute_version=123 WHERE id='healthy'")
        database.commit()
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    async def run():
        state = ComputeWorkState()
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
        owner.prepare_candidate = lambda candidate: _prepared(candidate.session_id)
        task = asyncio.create_task(owner.run())
        try:
            await asyncio.wait_for(owner.initial_done.wait(), 2)
            assert versions(path)['faulty'] is None
            assert state.has_pending()
            async with asyncio.timeout(6):
                while versions(path)['faulty'] != 123 or state.has_pending():
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_admitted_tree_keeps_current_diagnosed_member_required_until_application(
    codex_recovery_runtime, monkeypatch,
):
    import sqlite3
    from contextlib import closing
    from tests.test_compute_age_selection import clone_session
    path, _ = codex_recovery_runtime
    clone_session(path, 'first-child', 30)
    clone_session(path, 'unrelated-recent', background_compute.time.time() + 1000)
    with closing(sqlite3.connect(path)) as database:
        database.execute("UPDATE core_session SET type='subagent', parent_session_id='healthy' WHERE id IN ('faulty', 'first-child')")
        database.execute("UPDATE core_session SET compute_version=123, unavailable_reason='rollout_missing' WHERE id='faulty'")
        database.execute("UPDATE core_session SET compute_version=123, unavailable_reason='rollout_missing' WHERE id='unrelated-recent'")
        database.commit()
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, '_UNAVAILABLE_RECOVERY_PAGE_SIZE', 1)
    async def run():
        owner = CodexComputeCoordinator(
            background.ComputeContext(Provider.CODEX, 123, 'unused:factory'), asyncio.Event(),
        )
        candidate, _, _, _ = await owner._select_candidate()
        assert candidate.session_id == 'first-child'
        with closing(sqlite3.connect(path)) as database:
            database.execute("UPDATE core_session SET compute_version=123 WHERE id='first-child'")
            database.commit()
        candidate, _, _, _ = await owner._select_candidate()
        assert candidate.session_id == 'faulty'
        assert versions(path)['healthy'] is None
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_actual_work_pending_during_migration_lock_and_application_drain(codex_recovery_runtime, monkeypatch):
    import threading
    from twicc.providers.compute_base import BaseSessionCompute
    path, _ = codex_recovery_runtime
    entered, release = threading.Event(), threading.Event()
    apply = BaseSessionCompute._apply_session_complete_final
    def paused_apply(message):
        if message['session_id'] == 'faulty':
            entered.set()
            assert release.wait(6)
        return apply(message)
    monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', staticmethod(paused_apply))
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    async def run():
        state = ComputeWorkState()
        db_writer.start_db_writer()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
        lease = gate_for('faulty')
        await lease.acquire()
        waiting = asyncio.Event()
        async def prepare(candidate):
            if candidate.session_id != 'faulty':
                return PreparedCandidate(candidate.session_id, None, False)
            waiting.set()
            await lease.acquire()
            owner._forced_rebuild.add('faulty')
            owner.migration_paths['faulty'] = candidate.file_path
            return PreparedCandidate('faulty', lease, True, kind='replaced')
        owner.prepare_candidate = prepare
        task = asyncio.create_task(owner.run())
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            assert state.has_pending() and lease.locked()
            assert versions(path)['faulty'] is None
            lease.release()
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(.01)
            assert state.has_pending() and lease.locked()
            assert owner.submitted or owner.computed_not_applied
            release.set()
            async with asyncio.timeout(7):
                while state.has_pending():
                    await asyncio.sleep(.01)
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not lease.locked()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if lease.locked():
                lease.release()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['progress', 'project_cache'])
def test_startup_failure_recovers_outdated_roots_before_periodic_admission(
    codex_recovery_runtime, monkeypatch, settings, failure,
):
    from twicc import content_maintenance_task as maintenance

    path, _ = codex_recovery_runtime
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(background_compute, 'sync_to_async', background.sync_to_async)
    monkeypatch.setattr(background_compute, 'load_project_directories', lambda: None)
    monkeypatch.setattr(background_compute, 'load_project_git_roots', lambda: None)
    monkeypatch.setattr(maintenance, 'pending_root_page', lambda *args: [])
    clock, preparations = [0], []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])

    def compression(*args, **kwargs):
        preparations.append(versions(path))
        return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)

    async def run():
        state = ComputeWorkState()
        retrying, retry_release = asyncio.Event(), asyncio.Event()
        overdue, admitted = asyncio.Event(), asyncio.Event()
        maintenance_stop = asyncio.Event()
        ctx = background.ComputeContext(Provider.CODEX, 123, f'{__name__}:CodexRecoveryCompute')
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
        owner.prepare_candidate = lambda candidate: _prepared(candidate.session_id)
        failed = False
        initialize = owner._initialize_progress

        async def progress(candidates):
            nonlocal failed
            await initialize(candidates)
            if failure == 'progress' and not failed:
                failed = True
                raise RuntimeError('one startup progress failure')

        def project_cache():
            nonlocal failed
            if failure == 'project_cache' and not failed:
                failed = True
                raise RuntimeError('one startup project cache failure')

        async def retry(stop, delay):
            assert 0 < delay <= 60
            retrying.set()
            await retry_release.wait()
            return not stop.is_set()

        wait = maintenance._wait

        async def maintenance_wait(stop, wake, timeout):
            if clock[0] == 0:
                clock[0] = 1800
                return
            if preparations:
                admitted.set()
                stop.set()
                return
            assert timeout is None
            overdue.set()
            await wait(stop, wake, timeout)

        monkeypatch.setattr(owner, '_initialize_progress', progress)
        monkeypatch.setattr(background_compute, 'load_project_directories', project_cache)
        monkeypatch.setattr(background_compute, '_wait_for_retry_delay', retry)
        monkeypatch.setattr(maintenance, '_wait', maintenance_wait)
        db_writer.start_db_writer()
        task = asyncio.create_task(owner.run())
        periodic = None
        try:
            async with asyncio.timeout(3):
                while not retrying.is_set():
                    if task.done():
                        await task
                    await asyncio.sleep(.01)
            periodic = asyncio.create_task(maintenance.start_content_maintenance_task(
                maintenance_stop, compute_work=state,
            ))
            await asyncio.wait_for(overdue.wait(), 3)
            assert failed and state.has_pending()
            assert versions(path) == {'faulty': None, 'healthy': None}
            assert preparations == []
            retry_release.set()
            await asyncio.wait_for(admitted.wait(), 8)
            await asyncio.wait_for(periodic, 3)
            assert preparations == [{'faulty': 123, 'healthy': 123}]
            assert versions(path) == {'faulty': 123, 'healthy': 123}
            assert not state.has_pending()
            assert not task.done()
        finally:
            retry_release.set()
            maintenance_stop.set()
            if periodic is not None:
                await asyncio.gather(periodic, return_exceptions=True)
            ctx.stop_event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.parametrize('explicit_stop', [False, True])
def test_real_shutdown_preserves_unexpected_exit_pending_state(monkeypatch, explicit_stop):
    async def run():
        state = ComputeWorkState()
        ctx = background.ComputeContext(Provider.CODEX, 123, 'unused:factory')
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)

        async def fail():
            if explicit_stop:
                ctx.stop_event.set()
            raise RuntimeError('unexpected coordinator exit')

        monkeypatch.setattr(owner, '_run', fail)
        with pytest.raises(RuntimeError, match='unexpected coordinator exit'):
            await owner.run()
        # Real cleanup sets stop_event even when no external stop occurs.
        assert ctx.stop_event.is_set()
        assert owner._shutdown_drained
        assert state.has_pending() == (not explicit_stop)
        assert not state._source_wakeups

    asyncio.run(run())


def test_real_shutdown_keeps_pending_after_cancellation_without_explicit_stop(monkeypatch):
    async def run():
        state = ComputeWorkState()
        ctx = background.ComputeContext(Provider.CODEX, 123, 'unused:factory')
        owner = CodexComputeCoordinator(ctx, asyncio.Event(), work_state=state)
        entered = asyncio.Event()

        async def wait():
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(owner, '_run', wait)
        task = asyncio.create_task(owner.run())
        await entered.wait()
        assert state.has_pending()
        assert not ctx.stop_event.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ctx.stop_event.is_set()
        assert owner._shutdown_drained
        assert state.has_pending()
        assert not state._source_wakeups

    asyncio.run(run())
