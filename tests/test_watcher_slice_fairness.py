"""The watcher commits one slice per turn and drains finite source targets."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from watchfiles import Change

from twicc.providers import sessions_watcher as module
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher



@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('subagent', [False, True])
def test_real_slices_release_locks_and_small_file_commits_first(provider_home, monkeypatch, subagent):
    from twicc.core.models import Project, Session
    project = Project.objects.create(id='p')
    watcher = ClaudeCodeSessionsWatcher()
    paths = []
    sessions = []
    parent = Session.objects.create(id='00000000-0000-4000-8000-000000000003', project=project,
                                    provider='claude_code', file_path='p/parent.jsonl') if subagent else None
    for suffix, lines in [('1', 1001), ('2', 1)]:
        sid = f'00000000-0000-4000-8000-{suffix:0>12}'
        file_path = f'p/{sid}.jsonl'
        if subagent and suffix == '1':
            sid = 'abcdef123'
            file_path = f'p/{parent.id}/subagents/agent-{sid}.jsonl'
        session = Session.objects.create(id=sid, project=project, provider='claude_code',
            file_path=file_path, compute_version=watcher.get_compute().compute_version,
            type='subagent' if subagent and suffix == '1' else 'session',
            parent_session=parent if suffix == '1' else None)
        sessions.append(session)
        path = provider_home.claude / 'projects' / session.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{}\n' * lines)
        paths.append(path)
    monkeypatch.setattr(module, 'broadcast_message', AsyncMock())
    turns = []
    original = watcher._process_change
    async def record(*args):
        result = await original(*args)
        turns.append((args[1], result.disposition))
        return result
    watcher._process_change = record
    finish = watcher._queue.finish
    def finish_unlocked(*args, **kwargs):
        from twicc.providers import db_writer
        assert not watcher._change_lock.locked()
        assert not db_writer._db_write_lock.locked()
        finish(*args, **kwargs)
    watcher._queue.finish = finish_unlocked
    async def run():
        from twicc.providers import db_writer
        db_writer.start_db_writer()
        first = asyncio.create_task(watcher.process_path(paths[0]))
        second = asyncio.create_task(watcher.process_path(paths[1]))
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        watcher.stop_watcher()
        await watcher._consumer_task
        assert not watcher._change_lock.locked()
        await db_writer.stop_db_writer()
    asyncio.run(run())
    assert [path for path, _ in turns[:2]] == [str(paths[0]), str(paths[1])]
    assert turns[0][1] == 'ready'
    assert Session.objects.get(id=sessions[0].id).last_line == 1001


def test_process_path_finite_target_and_incomplete_tail(tmp_path):
    async def run():
        from twicc.providers.sessions_watcher import SessionChangeResult
        path = tmp_path / 'session.jsonl'
        path.write_bytes(b'{}\npartial')
        first_done = asyncio.Event()
        continue_backlog = asyncio.Event()
        watcher = module.BaseSessionsWatcher()
        count = 0
        async def process(change, filename, layer):
            nonlocal count
            count += 1
            generation = watcher._queue.source_generation(path)
            if count == 1:
                path.write_bytes(b'{}\npartial\n{}\n')
                first_done.set()
                return SessionChangeResult('ready', generation, 3)
            await continue_backlog.wait()
            return SessionChangeResult('drained', generation, path.stat().st_size)
        watcher._process_change = process
        waiter = asyncio.create_task(watcher.process_path(path))
        await asyncio.wait_for(first_done.wait(), 1)
        await asyncio.wait_for(waiter, 1)
        assert count <= 2
        watcher.stop_watcher()
        continue_backlog.set()
        await watcher._consumer_task
    asyncio.run(run())


def test_process_path_file_error_returns_without_retry(tmp_path):
    async def run():
        watcher = module.BaseSessionsWatcher()
        path = tmp_path / 'missing.jsonl'
        await asyncio.wait_for(watcher.process_path(path), 1)
        watcher.stop_watcher()
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['ready', 'failed', 'cancelled'])
def test_coordinator_releases_notify_even_without_replay(tmp_path, outcome):
    from twicc.providers.codex.background_compute import CodexComputeCoordinator
    from twicc.providers.codex.migration_gate import gate_for, mark_migrating, is_migrating
    async def run():
        seen = []
        def notify(release):
            assert not is_migrating('release')
            seen.append(release)
        coordinator = CodexComputeCoordinator(SimpleNamespace(), asyncio.Event(), on_migration_released=notify)
        gate = gate_for('release')
        await gate.acquire()
        mark_migrating('release')
        coordinator.migration_leases['release'] = gate
        path = tmp_path / 'release.jsonl'
        coordinator.migration_paths['release'] = path
        coordinator._release_lease('release', replay=False, outcome=outcome, error='failure')
        assert len(seen) == 1
        assert seen[0].path == path and seen[0].outcome == outcome and not seen[0].replay
        assert not gate.locked()
    asyncio.run(run())


def test_codex_gate_excludes_migration_through_callback_awaits(monkeypatch, tmp_path):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex.migration_gate import gate_for
    from twicc.providers.sessions_watcher import ParsedSessionFile, SessionChangeResult
    from twicc.core.models import SessionType
    async def run():
        watcher = CodexSessionsWatcher()
        parsed = ParsedSessionFile('p', 'gate-race', SessionType.SESSION, 's.jsonl')
        entered, finish, migrated = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def callback(*_args):
            entered.set()
            await finish.wait()
            assert not migrated.is_set()
            return SessionChangeResult('ready')
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', callback)
        monkeypatch.setattr(watcher, '_rewrite_detected', AsyncMock(return_value=False))
        task = asyncio.create_task(watcher._process_parsed_session_change(tmp_path / 's', parsed, Change.modified, None))
        await entered.wait()
        async def migration():
            async with gate_for(parsed.session_id):
                migrated.set()
        migration_task = asyncio.create_task(migration())
        await asyncio.sleep(0)
        assert not migrated.is_set()
        finish.set()
        assert (await task).disposition == 'ready'
        await migration_task
        assert migrated.is_set()
    asyncio.run(run())


def test_generation_from_before_slice_is_not_relabelled_after_delete(tmp_path, monkeypatch):
    from twicc.providers.sessions_watcher import ParsedSessionFile, SessionChangeResult
    from twicc.core.models import SessionType
    async def run():
        watcher = module.BaseSessionsWatcher()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{}\n')
        before = await watcher._observe_source(path)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        watcher.parse_session_file = AsyncMock(return_value=ParsedSessionFile('p', 's', SessionType.SESSION, 's'))
        async def callback(*args):
            watcher._queue.enqueue(path, Change.deleted)
            return SessionChangeResult('drained', watcher._queue.source_generation(path), 3)
        watcher._process_parsed_session_change = callback
        result = await watcher._process_change(Change.modified, str(path), None)
        assert result.source_generation is before.source_generation
        assert result.source_generation is not watcher._queue.source_generation(path)
    asyncio.run(run())


@pytest.mark.parametrize('first_observation', [False, True])
def test_replacement_runs_codex_rebuild_before_append_slice(tmp_path, monkeypatch, first_observation):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex import sessions_watcher as codex_module
    from twicc.providers.sessions_watcher import ParsedSessionFile
    from twicc.core.models import SessionType
    async def run():
        watcher = CodexSessionsWatcher()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{"old": 1}\n')
        if not first_observation:
            await watcher._observe_source(path)
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'{"new": 2}\n')
        replacement.replace(path)
        await watcher._observe_source(path)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change',
                            AsyncMock(side_effect=AssertionError('append ran before replacement handling')))
        session = SimpleNamespace(id='replaced', last_offset=300 if first_observation else path.stat().st_size)
        monkeypatch.setattr(codex_module, 'get_session_by_id', AsyncMock(return_value=session))
        jobs = []
        async def submit(job):
            jobs.append(job)
        monkeypatch.setattr(codex_module, 'submit_async_job', submit)
        monkeypatch.setattr(codex_module, 'request_rebuild', lambda sid: None)
        parsed = ParsedSessionFile('p', session.id, SessionType.SESSION, 's', compute_ready_on_create=False)
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, None)
        assert result.disposition == 'deferred'
        assert len(jobs) == 1
    asyncio.run(run())


def test_duplicate_migration_notification_keeps_replay_generation(tmp_path):
    from twicc.providers.session_change_queue import MigrationRelease
    async def run():
        watcher = module.BaseSessionsWatcher()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{}\n')
        # Keep admission pending to inspect the notification without starting ORM work.
        watcher._ensure_consumer = lambda: None
        release = MigrationRelease('s', path, 'ready', True, None, 987)
        watcher.notify_migration_released(release)
        target = await watcher._observe_source(path)
        watcher.notify_migration_released(release)
        assert watcher._queue.source_generation(path) is target.source_generation
        watcher.stop_watcher()
    asyncio.run(run())


def test_migration_notification_before_watcher_start_does_not_start_consumer(tmp_path):
    from twicc.providers.session_change_queue import MigrationRelease
    async def run():
        watcher = module.BaseSessionsWatcher()
        watcher.notify_migration_released(MigrationRelease('s', tmp_path / 's.jsonl', 'ready', True, None, 123))
        assert watcher._consumer_task is None
        assert not watcher._queue.idle
        watcher.stop_watcher()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures('compute_executor_started')
@pytest.mark.parametrize('current, elapsed, expected', [(True, 101, 250), (True, 20, 500), (False, 101, 500)])
def test_tuning_applies_only_to_future_current_version_slices(provider_home, monkeypatch, current, elapsed, expected):
    from twicc.core.models import Project, Session, SessionType
    watcher = ClaudeCodeSessionsWatcher()
    session = Session.objects.create(id='00000000-0000-4000-8000-000000000999',
        project=Project.objects.create(id='p'), provider='claude_code', file_path='p/tune.jsonl',
        compute_version=watcher.get_compute().compute_version if current else None)
    path = provider_home.claude / 'projects' / session.file_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{}\n' * 1200)
    parsed = module.ParsedSessionFile('p', session.id, SessionType.SESSION, session.file_path)
    limits = []
    original = module._sync_live_session_items
    def measured(compute, sid, path, limit):
        limits.append(limit.max_lines)
        return original(compute, sid, path, limit)._replace(elapsed_ms=elapsed)
    monkeypatch.setattr(module, '_sync_live_session_items', measured)
    monkeypatch.setattr(module, 'broadcast_message', AsyncMock())
    async def run():
        first = await watcher.sync_and_broadcast(path, parsed, Change.modified, None)
        assert first.disposition == 'ready' and first.end_offset == 1500
        second = await watcher.sync_and_broadcast(path, parsed, Change.modified, None)
        assert second.disposition == 'ready'
        while (await watcher.sync_and_broadcast(path, parsed, Change.modified, None)).disposition == 'ready':
            pass
        assert path not in watcher._line_limits
    asyncio.run(run())
    assert limits[:2] == [500, expected]


def test_claude_replacement_with_committed_offset_fails_before_slice(tmp_path, monkeypatch):
    from twicc.core.models import SessionType
    async def run():
        watcher = ClaudeCodeSessionsWatcher()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{}\n' * 5)
        await watcher._observe_source(path)
        path.write_bytes(b'{}\n')
        await watcher._observe_source(path)
        parsed = module.ParsedSessionFile('p', 's', SessionType.SESSION, 's', title='known')
        monkeypatch.setattr(module, 'get_session_by_id', AsyncMock(return_value=SimpleNamespace(last_offset=15)))
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        watcher.sync_and_broadcast = AsyncMock(side_effect=AssertionError('replacement was sliced'))
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, None)
        assert result.disposition == 'failed'
    asyncio.run(run())


def test_queued_release_activates_at_start_without_filesystem_event(tmp_path, monkeypatch):
    from twicc.providers.session_change_queue import MigrationRelease
    from twicc.core.enums import Provider
    async def run():
        watcher = module.BaseSessionsWatcher()
        watcher.projects_dir = tmp_path
        watcher.get_compute = lambda: SimpleNamespace(provider=Provider.CODEX)
        seen = asyncio.Event()
        async def process(*args):
            seen.set()
            return module.SessionChangeResult('drained')
        watcher._process_change = process
        monkeypatch.setattr(module, 'load_project_directories', lambda: None)
        monkeypatch.setattr(module, 'load_project_git_roots', lambda: None)
        async def watch(*args, stop_event):
            await stop_event.wait()
            if False:
                yield set()
        monkeypatch.setattr(module, 'awatch', watch)
        watcher.notify_migration_released(MigrationRelease('s', tmp_path / 's.jsonl', 'ready', True, None, 1001))
        assert watcher._consumer_task is None
        task = asyncio.create_task(watcher.start_watcher())
        await asyncio.wait_for(seen.wait(), 1)
        watcher.stop_watcher()
        await asyncio.wait_for(task, 1)
        assert watcher._consumer_task.done()
    asyncio.run(run())


def test_producer_repeated_cancellation_drains_callback_and_waiters(tmp_path, monkeypatch):
    from twicc.core.enums import Provider
    async def run():
        watcher = module.BaseSessionsWatcher()
        watcher.projects_dir = tmp_path
        watcher.get_compute = lambda: SimpleNamespace(provider=Provider.CODEX)
        entered, finish, watching = asyncio.Event(), asyncio.Event(), asyncio.Event()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{}\n')
        async def process(*args):
            entered.set()
            await finish.wait()
            return module.SessionChangeResult('drained', watcher._queue.source_generation(path), 3)
        watcher._process_change = process
        monkeypatch.setattr(module, 'load_project_directories', lambda: None)
        monkeypatch.setattr(module, 'load_project_git_roots', lambda: None)
        async def watch(*args, stop_event):
            yield {(Change.modified, str(path))}
            watching.set()
            await stop_event.wait()
        monkeypatch.setattr(module, 'awatch', watch)
        admitted = asyncio.Event()
        wait_drained = watcher._queue.wait_drained
        async def wait_target(*args, **kwargs):
            admitted.set()
            await wait_drained(*args, **kwargs)
        watcher._queue.wait_drained = wait_target
        producer = asyncio.create_task(watcher.start_watcher())
        waiter = asyncio.create_task(watcher.process_path(path))
        await entered.wait()
        await watching.wait()
        await admitted.wait()
        producer.cancel()
        await asyncio.sleep(0)
        producer.cancel()
        await asyncio.sleep(0)
        producer.cancel()
        await asyncio.sleep(0)
        assert not watcher._consumer_task.done()
        assert not producer.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await producer
        await asyncio.wait_for(waiter, 1)
        assert watcher._consumer_task.done()
        assert watcher._queue.idle
    asyncio.run(run())


def test_busy_agent_gate_and_waiting_migration_do_not_block_small_session(tmp_path, monkeypatch):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex.migration_gate import gate_for, mark_migrating, unmark_migrating
    from twicc.providers.session_change_queue import MigrationRelease
    from twicc.core.models import SessionType
    async def run():
        watcher = CodexSessionsWatcher()
        big, small = tmp_path / 'big.jsonl', tmp_path / 'small.jsonl'
        for path in (big, small):
            path.write_bytes(b'{}\n')
        watcher.parse_session_file = AsyncMock(side_effect=lambda path: module.ParsedSessionFile(
            'p', path.stem, SessionType.SESSION, path.name, title='known'))
        watcher._rewrite_detected = AsyncMock(return_value=False)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        small_done = asyncio.Event()
        async def callback(self, path, *_args):
            if path == small:
                small_done.set()
            return module.SessionChangeResult('drained', watcher._queue.source_generation(path), 3)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', callback)
        gate = gate_for('big')
        await gate.acquire()
        migration_entered, migration_finish = asyncio.Event(), asyncio.Event()
        async def migrate():
            async with gate:
                mark_migrating('big')
                migration_entered.set()
                await migration_finish.wait()
                unmark_migrating('big')
            watcher.notify_migration_released(MigrationRelease('big', big, 'failed', False, 'failed', 42))
        migration_task = asyncio.create_task(migrate())
        await asyncio.sleep(0)
        big_waiter = asyncio.create_task(watcher.process_path(big))
        small_waiter = asyncio.create_task(watcher.process_path(small))
        await asyncio.wait_for(small_done.wait(), 1)
        gate.release()
        await migration_entered.wait()
        assert not big_waiter.done()
        await small_waiter
        migration_finish.set()
        await migration_task
        await big_waiter
        await asyncio.sleep(0)
        watcher.stop_watcher()
        await watcher._drain_changes()
        assert not watcher._wake_tasks
        assert watcher._queue.idle
    asyncio.run(run())


def test_stop_cancels_gate_wake_and_drain_waiter(tmp_path, monkeypatch):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex.migration_gate import gate_for
    from twicc.core.models import SessionType
    async def run():
        watcher = CodexSessionsWatcher()
        path = tmp_path / 'busy.jsonl'
        path.write_bytes(b'{}\n')
        parsed = module.ParsedSessionFile('p', 'stop-gate', SessionType.SESSION, path.name)
        watcher.parse_session_file = AsyncMock(return_value=parsed)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        gate = gate_for(parsed.session_id)
        await gate.acquire()
        waiter = asyncio.create_task(watcher.process_path(path))
        async def pending():
            while not watcher._wake_tasks:
                await asyncio.sleep(0)
        await asyncio.wait_for(pending(), 1)
        watcher.stop_watcher()
        await watcher._drain_changes()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not watcher._wake_tasks and not watcher._gate_wakes and not watcher._gate_wake_targets
        assert gate.locked()
        gate.release()
    asyncio.run(run())


def test_compute_only_release_does_not_release_an_agent_gate():
    from twicc.providers.codex.background_compute import CodexComputeCoordinator
    from twicc.providers.codex.migration_gate import gate_for
    async def run():
        coordinator = CodexComputeCoordinator(SimpleNamespace(), asyncio.Event())
        gate = gate_for('agent-owned')
        await gate.acquire()
        coordinator._release_lease('agent-owned')
        assert gate.locked()
        gate.release()
    asyncio.run(run())


def test_cancel_during_unavailable_mark_releases_migration(tmp_path):
    from twicc.providers.codex.background_compute import CodexComputeCoordinator
    from twicc.providers.codex.migration_gate import gate_for, mark_migrating, is_migrating
    async def run():
        releases = []
        coordinator = CodexComputeCoordinator(SimpleNamespace(), asyncio.Event(), on_migration_released=releases.append)
        gate = gate_for('condemn-cancel')
        await gate.acquire()
        mark_migrating('condemn-cancel')
        coordinator.migration_paths['condemn-cancel'] = tmp_path / 's'
        coordinator._submit_job = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._condemn('condemn-cancel', 'read', 'missing', gate, reason='rollout_missing')
        assert not gate.locked() and not is_migrating('condemn-cancel')
        assert len(releases) == 1 and releases[0].outcome == 'cancelled'
    asyncio.run(run())


@pytest.mark.parametrize('error', [RuntimeError('classification failed'), asyncio.CancelledError()])
def test_classification_failure_before_lease_notifies_candidate_path(tmp_path, error):
    from twicc.providers.codex.background_compute import CodexComputeCandidate, CodexComputeCoordinator
    from twicc.providers.codex.migration_gate import gate_for
    async def run():
        releases = []
        coordinator = CodexComputeCoordinator(SimpleNamespace(), asyncio.Event(), on_migration_released=releases.append)
        candidate = CodexComputeCandidate('early', tmp_path / 'early.jsonl', 'session')
        coordinator._source_mode = AsyncMock(side_effect=error)
        coordinator.runner.stop = AsyncMock()
        gate = gate_for('early')
        await gate.acquire()
        try:
            if isinstance(error, asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await coordinator.prepare_candidate(candidate)
            else:
                await coordinator.prepare_candidate(candidate)
            assert len(releases) == 1 and releases[0].path == candidate.file_path
            assert releases[0].outcome == ('cancelled' if isinstance(error, asyncio.CancelledError) else 'failed')
            assert gate.locked()
        finally:
            gate.release()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_real_incomplete_tail_parks_and_append_during_turn_is_not_lost(provider_home, monkeypatch):
    from twicc.core.models import Project, Session
    from twicc.providers import db_writer
    watcher = ClaudeCodeSessionsWatcher()
    session = Session.objects.create(id='00000000-0000-4000-8000-000000000888',
        project=Project.objects.create(id='p'), provider='claude_code',
        file_path='p/00000000-0000-4000-8000-000000000888.jsonl',
        compute_version=watcher.get_compute().compute_version)
    path = provider_home.claude / 'projects' / session.file_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{}\npartial')
    monkeypatch.setattr(module, 'broadcast_message', AsyncMock())
    turns = []
    original = watcher._process_change
    async def process(*args):
        result = await original(*args)
        turns.append(result)
        if len(turns) == 1:
            # The first callback has read EOF. An append arrives before finish.
            with path.open('ab') as file:
                file.write(b'\n{}\nunfinished')
            watcher._enqueue(path, Change.modified)
        return result
    watcher._process_change = process
    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await watcher._consumer_task
            assert watcher._queue.idle
            assert len(turns) == 2
            row = await Session.objects.aget(id=session.id)
            assert row.last_offset == len(b'{}\npartial\n{}\n')
            with path.open('ab') as file:
                file.write(b'\n')
            await watcher.process_path(path)
            await watcher._consumer_task
            row = await Session.objects.aget(id=session.id)
            assert row.last_offset == path.stat().st_size
            assert len(turns) == 3
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_deletion_recreation_reparses_before_migration_exclusion(tmp_path, monkeypatch):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex.migration_gate import mark_migrating, unmark_migrating
    from twicc.providers.session_change_queue import MigrationRelease
    import orjson
    async def run():
        watcher = CodexSessionsWatcher()
        watcher.projects_dir = tmp_path
        path = tmp_path / '2026' / 'rollout-source.jsonl'
        path.parent.mkdir()
        def write(sid):
            path.write_bytes(orjson.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': '/tmp/p'}}) + b'\n')
        write('first')
        watcher._rewrite_detected = AsyncMock(return_value=False)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        seen = []
        async def process(self, path, parsed, *_args):
            seen.append(parsed.session_id)
            return module.SessionChangeResult('drained', watcher._queue.source_generation(path), path.stat().st_size)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', process)
        await watcher.process_path(path)
        await watcher._consumer_task
        assert seen == ['first']
        path.unlink()
        watcher._enqueue(path, Change.deleted)
        write('second')
        mark_migrating('second')
        watcher._enqueue(path, Change.added)
        await watcher._consumer_task
        assert seen == ['first']
        assert watcher._parsed_paths[path].session_id == 'second'
        unmark_migrating('second')
        watcher.notify_migration_released(MigrationRelease('second', path, 'ready', False, None, 3001))
        await watcher._consumer_task
        assert seen == ['first', 'second']
        watcher.stop_watcher()
        await watcher._drain_changes()
    asyncio.run(run())


@pytest.mark.parametrize('replacement', ['delete_add', 'atomic_replace'])
def test_recreated_source_does_not_defer_on_old_migrating_identity(tmp_path, monkeypatch, replacement):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.codex.migration_gate import mark_migrating, unmark_migrating
    from twicc.providers.session_change_queue import MigrationRelease
    import orjson
    async def run():
        watcher = CodexSessionsWatcher()
        watcher.projects_dir = tmp_path
        path = tmp_path / '2026' / 'rollout-source.jsonl'
        path.parent.mkdir()
        def content(sid):
            return orjson.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': '/tmp/p'}}) + b'\n'
        path.write_bytes(content('old'))
        watcher._rewrite_detected = AsyncMock(return_value=False)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        seen = []
        async def process(self, source, parsed, *_args):
            seen.append(parsed.session_id)
            if parsed.session_id == 'new':
                # An outcome for the deleted source must not cancel the new target.
                watcher.notify_migration_released(MigrationRelease('old', source, 'cancelled', False, None, 4001))
            return module.SessionChangeResult('drained', watcher._queue.source_generation(source), source.stat().st_size)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', process)
        await watcher.process_path(path)
        await watcher._consumer_task
        mark_migrating('old')
        try:
            if replacement == 'delete_add':
                path.unlink()
                watcher._enqueue(path, Change.deleted)
                path.write_bytes(content('new'))
                watcher._enqueue(path, Change.added)
            else:
                other = tmp_path / 'replacement'
                other.write_bytes(content('new'))
                other.replace(path)
                watcher._enqueue(path, Change.modified)
            await asyncio.wait_for(watcher.process_path(path), 1)
            await watcher._consumer_task
            assert seen[0] == 'old' and seen[1:] and set(seen[1:]) == {'new'}
            assert watcher._parsed_paths[path].session_id == 'new'
        finally:
            unmark_migrating('old')
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('replacement_text', ['new', 'new and larger'])
def test_paginated_replacement_reaches_real_gated_reconstruction_and_release(tmp_path, monkeypatch, replacement_text):
    import orjson
    from twicc.core.models import Project, Session, SessionItem
    from twicc.providers import db_writer
    from twicc.providers.codex import migration_gate
    from twicc.providers.codex.background_compute import CodexComputeCandidate, CodexComputeCoordinator, FailedCandidate
    from twicc.providers.codex.rollout_migration import ReplaceCodexHistoryJob
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

    monkeypatch.setattr(migration_gate, '_rebuild_requests', set())
    watcher = CodexSessionsWatcher()
    watcher.projects_dir = tmp_path
    path = tmp_path / '2026' / 'rollout-replaced.jsonl'
    path.parent.mkdir()
    meta = {'type': 'session_meta', 'payload': {'id': 'replaced', 'cwd': '/tmp/p', 'history_mode': 'paginated'}}
    old_message = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'old'}}
    new_message = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': replacement_text}}
    old_records = [orjson.dumps(meta), orjson.dumps(old_message)]
    new_records = [orjson.dumps(meta), orjson.dumps(new_message)]
    path.write_bytes(b'\n'.join(old_records) + b'\n')
    old_offset = path.stat().st_size
    session = Session.objects.create(id='replaced', project=Project.objects.create(id='p'), provider='codex',
        file_path='2026/rollout-replaced.jsonl', last_offset=old_offset, last_line=2,
        compute_version=watcher.get_compute().compute_version)
    for line, record in enumerate(old_records, 1):
        SessionItem.objects.create(session=session, line_num=line, content=record.decode())
    monkeypatch.setattr(module, 'broadcast_message', AsyncMock())

    async def run():
        releases = []
        def notify(release):
            releases.append(release)
            watcher.notify_migration_released(release)
        coordinator = CodexComputeCoordinator(SimpleNamespace(), asyncio.Event(), on_migration_released=notify)
        # The external agent manager is irrelevant; all database jobs and source reads stay real.
        coordinator._is_agent_active = lambda sid: False
        db_writer.start_db_writer()
        try:
            await watcher._observe_source(path)
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(b'\n'.join(new_records) + b'\n')
            replacement.replace(path)
            watcher._enqueue(path, Change.modified)
            await watcher._consumer_task
            # This is the real MarkSessionRebuildJob result, not a mocked submission.
            marked = await Session.objects.aget(id=session.id)
            assert marked.compute_version is None and marked.last_offset == old_offset
            candidate = CodexComputeCandidate(session.id, path, 'session', marked.last_offset)
            coordinator._absorb_rebuild_requests()

            submit = coordinator._submit_job
            fail_once = True
            async def fail_replacement_once(job_type, *args):
                nonlocal fail_once
                if job_type is ReplaceCodexHistoryJob and fail_once:
                    fail_once = False
                    raise RuntimeError('injected replacement failure')
                return await submit(job_type, *args)
            coordinator._submit_job = fail_replacement_once
            failed = await coordinator.prepare_candidate(candidate)
            assert isinstance(failed, FailedCandidate)
            assert session.id in coordinator._forced_rebuild
            assert path in watcher._replaced_paths
            assert releases[-1].outcome == 'failed' and releases[-1].path == path
            assert await SessionItem.objects.filter(session_id=session.id, content=old_records[1].decode()).aexists()

            # Retry the retained intent without a new request. The gated recheck
            # sees paginated/paginated again and must still reconstruct history.
            prepared = await coordinator.prepare_candidate(candidate)
            assert prepared.migrated_history and prepared.kind == 'replaced'
            assert prepared.migration_lease.locked()
            assert session.id not in coordinator._forced_rebuild
            assert path in watcher._replaced_paths
            rebuilt = await Session.objects.aget(id=session.id)
            assert rebuilt.last_offset == path.stat().st_size
            actual = [content async for content in SessionItem.objects.filter(session_id=session.id)
                      .order_by('line_num').values_list('content', flat=True)]
            assert actual == [record.decode() for record in new_records]
            coordinator.migration_leases[session.id] = prepared.migration_lease
            coordinator._release_lease(session.id)
            await watcher._consumer_task
            assert releases[-1].outcome == 'ready' and releases[-1].path == path and releases[-1].replay
            assert path not in watcher._replaced_paths
            assert watcher._queue.idle and not prepared.migration_lease.locked()
        finally:
            if session.id in coordinator.migration_paths:
                coordinator._release_lease(session.id, replay=False, outcome='cancelled')
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['failed', 'cancelled'])
@pytest.mark.parametrize('same_session', [False, True], ids=['new-identity', 'same-identity'])
@pytest.mark.parametrize('lifecycle', ['coalesced-delete', 'completed-delete', 'startup'])
def test_release_before_replacement_parse_waits_for_source_identity(
    tmp_path, monkeypatch, outcome, same_session, lifecycle,
):
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.session_change_queue import MigrationRelease
    import orjson
    async def run():
        watcher = CodexSessionsWatcher()
        watcher.projects_dir = tmp_path
        path = tmp_path / '2026' / 'rollout-source.jsonl'
        path.parent.mkdir()
        def content(sid, version):
            return orjson.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': '/tmp/p', 'version': version}}) + b'\n'
        path.write_bytes(content('old', 1))
        watcher._rewrite_detected = AsyncMock(return_value=False)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        seen = []
        async def process(self, source, parsed, change, *_args):
            seen.append(parsed.session_id)
            end = 0 if change == Change.deleted else source.stat().st_size
            return module.SessionChangeResult('drained', watcher._queue.source_generation(source), end)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', process)
        if lifecycle != 'startup':
            await watcher.process_path(path)
            await watcher._consumer_task
        if lifecycle == 'completed-delete':
            path.unlink()
            watcher._enqueue(path, Change.deleted)
            await watcher._consumer_task
            assert path not in watcher._deleted_parsed_paths
        ensure_consumer = watcher._ensure_consumer
        watcher._ensure_consumer = lambda: None
        if lifecycle == 'coalesced-delete' and same_session:
            other = tmp_path / 'replacement'
            other.write_bytes(content('old', 2))
            other.replace(path)
        else:
            if lifecycle == 'coalesced-delete':
                path.unlink()
                watcher._enqueue(path, Change.deleted)
            path.write_bytes(content('old' if same_session else 'new', 2))
        target = await watcher._observe_source(path)
        watcher._enqueue(path, Change.added)
        waiter = asyncio.create_task(watcher._queue.wait_drained(path, target=target))
        await asyncio.sleep(0)
        watcher.notify_migration_released(MigrationRelease('old', path, outcome, False, 'migration failed', 4002))
        assert not watcher._queue.idle, 'terminal release dropped the only reparse event'
        watcher._ensure_consumer = ensure_consumer
        watcher._ensure_consumer()
        await watcher._consumer_task
        if same_session and outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            await asyncio.wait_for(waiter, 1)
        previous = {'coalesced-delete': ['old'], 'completed-delete': ['old', 'old'], 'startup': []}[lifecycle]
        assert seen == previous + ([] if same_session else ['new'])
        watcher.stop_watcher()
        await watcher._drain_changes()
        assert not watcher._pending_source_releases
    asyncio.run(run())


def test_old_duplicate_release_does_not_defer_replacement_identity_again(tmp_path):
    from twicc.providers.session_change_queue import MigrationRelease
    from twicc.core.models import SessionType
    async def run():
        watcher = module.BaseSessionsWatcher()
        watcher._ensure_consumer = lambda: None
        path = tmp_path / 's.jsonl'
        release = MigrationRelease('s', path, 'failed', False, 'old failure', 5001)
        watcher.notify_migration_released(release)
        watcher._deleted_parsed_paths[path] = module.ParsedSessionFile('p', 's', SessionType.SESSION, path.name)
        watcher._enqueue(path, Change.added)
        watcher.notify_migration_released(release)
        assert not watcher._pending_source_releases
        watcher.stop_watcher()
        await watcher._drain_changes()
    asyncio.run(run())


def test_stop_drains_identification_before_clearing_pending_release(tmp_path, monkeypatch):
    from twicc.core.models import SessionType
    from twicc.providers.session_change_queue import MigrationRelease
    async def run():
        watcher = module.BaseSessionsWatcher()
        path = tmp_path / 's.jsonl'
        path.write_bytes(b'{}\n')
        old = module.ParsedSessionFile('p', 'old', SessionType.SESSION, path.name)
        new = module.ParsedSessionFile('p', 'new', SessionType.SESSION, path.name)
        await watcher._observe_source(path)
        watcher._parsed_paths[path] = old
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'{"new":true}\n')
        replacement.replace(path)
        entered, identified = asyncio.Event(), asyncio.Event()
        async def parse(source):
            entered.set()
            await identified.wait()
            return new
        watcher.parse_session_file = parse
        async def process(source, *_args):
            return module.SessionChangeResult('drained', watcher._queue.source_generation(source), source.stat().st_size)
        watcher._process_parsed_session_change = process
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        waiter = asyncio.create_task(watcher.process_path(path))
        await entered.wait()
        watcher.notify_migration_released(MigrationRelease('old', path, 'cancelled', False, None, 6001))
        assert watcher._pending_source_releases
        watcher.stop_watcher()
        drain = asyncio.create_task(watcher._drain_changes())
        await asyncio.sleep(0)
        assert not drain.done()
        identified.set()
        await asyncio.wait_for(drain, 1)
        await waiter
        assert not watcher._pending_source_releases
        assert watcher._consumer_task.done() and watcher._queue.idle
    asyncio.run(run())


@pytest.mark.parametrize('outcome, release_during_gate_wait', [
    (None, False), ('failed', False), ('cancelled', False), ('failed', True), ('cancelled', True),
])
def test_gate_wake_tracks_same_session_replacement(tmp_path, monkeypatch, outcome, release_during_gate_wait):
    from twicc.core.models import SessionType
    from twicc.providers.codex.migration_gate import gate_for
    from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher
    from twicc.providers.session_change_queue import MigrationRelease

    async def run():
        watcher = CodexSessionsWatcher()
        path = tmp_path / 'busy-replacement.jsonl'
        path.write_bytes(b'{}\n')
        parsed = module.ParsedSessionFile('p', 'same-session-gate', SessionType.SESSION, path.name)
        watcher.parse_session_file = AsyncMock(return_value=parsed)
        watcher._rewrite_detected = AsyncMock(return_value=False)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        sliced = []
        async def process(self, source, *_args):
            sliced.append(source)
            return module.SessionChangeResult('drained', watcher._queue.source_generation(source), source.stat().st_size)
        monkeypatch.setattr(module.BaseSessionsWatcher, '_process_parsed_session_change', process)
        gate = gate_for(parsed.session_id)
        await gate.acquire()
        try:
            original = await watcher._observe_source(path)
            watcher._enqueue(path, Change.modified)
            await watcher._consumer_task
            assert watcher._gate_wakes and not sliced
            old_wake = watcher._gate_wakes[parsed.session_id]
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(b'{"new":true}\n')
            replacement.replace(path)
            target = await watcher._observe_source(path)
            assert target.source_generation is not original.source_generation
            waiter = asyncio.create_task(watcher._queue.wait_drained(path, target=target))
            await asyncio.sleep(0)
            release = MigrationRelease(parsed.session_id, path, outcome, False, None, 7001)
            if release_during_gate_wait:
                wake_after_gate = watcher._wake_after_gate
                def release_before_wake(*args):
                    watcher.notify_migration_released(release)
                    wake_after_gate(*args)
                monkeypatch.setattr(watcher, '_wake_after_gate', release_before_wake)
            watcher._enqueue(path, Change.modified)
            await watcher._consumer_task
            assert watcher._gate_wakes[parsed.session_id] is old_wake
            if outcome is not None and not release_during_gate_wait:
                watcher.notify_migration_released(release)
            gate.release()
            await old_wake
            if watcher._consumer_task is not None:
                await watcher._consumer_task
            assert waiter.done(), 'replacement lost its gate wake'
            if outcome == 'cancelled':
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            else:
                await waiter
            assert sliced == ([path] if outcome is None else [])
        finally:
            if gate.locked():
                gate.release()
            watcher.stop_watcher()
            await watcher._drain_changes()
        assert not watcher._gate_wakes and not watcher._wake_tasks and not watcher._gate_wake_targets
    asyncio.run(run())


def test_first_observed_claude_truncation_fails_before_slice(tmp_path, monkeypatch, caplog):
    from twicc.core.models import SessionType
    async def run():
        watcher = ClaudeCodeSessionsWatcher()
        path = tmp_path / 'first-truncated.jsonl'
        path.write_bytes(b'{}\n')
        target = await watcher._observe_source(path)
        assert path not in watcher._replaced_paths
        parsed = module.ParsedSessionFile('p', 'first-truncated', SessionType.SESSION, path.name, title='known')
        monkeypatch.setattr(module, 'get_session_by_id', AsyncMock(return_value=SimpleNamespace(last_offset=300)))
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        watcher.sync_and_broadcast = AsyncMock(return_value=module.SessionChangeResult('drained', target.source_generation, 300))
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, None)
        assert result.disposition == 'failed'
        watcher.sync_and_broadcast.assert_not_awaited()
        assert path in watcher._replaced_paths
        assert watcher._queue.source_generation(path) is not target.source_generation
        assert 'without a provider rebuild handler' in caplog.text
        # Later growth cannot make the obsolete checkpoint valid again.
        path.write_bytes(b'{}\n' * 101)
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, None)
        assert result.disposition == 'failed'
        watcher.sync_and_broadcast.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['failed', 'cancelled'])
@pytest.mark.parametrize('later_event', [False, True], ids=['other-identity-release', 'later-explicit-event'])
def test_unidentified_source_preserves_release_identity_and_original_event_order(
    tmp_path, monkeypatch, outcome, later_event,
):
    from twicc.core.models import SessionType
    from twicc.providers.session_change_queue import MigrationRelease
    async def run():
        watcher = module.BaseSessionsWatcher()
        path = tmp_path / 'unknown.jsonl'
        path.write_bytes(b'{}\n')
        parsed = module.ParsedSessionFile('p', 'new', SessionType.SESSION, path.name)
        watcher.parse_session_file = AsyncMock(return_value=parsed)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda fn: fn())
        sliced = []
        async def process(source, *_args):
            sliced.append(source)
            return module.SessionChangeResult('drained', watcher._queue.source_generation(source), 3)
        watcher._process_parsed_session_change = process
        ensure_consumer = watcher._ensure_consumer
        watcher._ensure_consumer = lambda: None
        target = await watcher._observe_source(path)
        watcher._enqueue(path, Change.added)
        before = asyncio.create_task(watcher._queue.wait_drained(path, target=target))
        await asyncio.sleep(0)
        watcher.notify_migration_released(MigrationRelease('new', path, outcome, False, 'failed', 8001))
        if later_event:
            watcher._enqueue(path, Change.modified)
            after = asyncio.create_task(watcher._queue.wait_drained(path, target=target))
            await asyncio.sleep(0)
        else:
            # A later outcome for a different identity cannot hide the first.
            watcher.notify_migration_released(MigrationRelease('old', path, 'ready', True, None, 8002))
        assert watcher._consumer_task is None
        watcher._ensure_consumer = ensure_consumer
        watcher._ensure_consumer()
        await watcher._consumer_task
        if outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await before
        else:
            await before
        if later_event:
            await after
        assert sliced == ([path] if later_event else [])
        watcher.stop_watcher()
        await watcher._drain_changes()
        assert not watcher._pending_source_releases
    asyncio.run(run())
