"""Logical Codex drains follow plain/compressed source transitions."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import orjson
import pytest
import zstandard
from watchfiles import Change

from twicc.providers import sessions_watcher as base
from twicc.providers.codex import sessions_watcher as module
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher

SID = '00000000-0000-4000-8000-000000000031'


def records(count=1):
    return b''.join(orjson.dumps(value) + b'\n' for value in [
        {'type': 'session_meta', 'payload': {'id': SID, 'cwd': '/tmp/p', 'history_mode': 'paginated'}},
        *[{'type': 'event_msg', 'payload': {'type': 'user_message', 'message': f'message {n}'}} for n in range(count)],
    ])


def source(watcher, data):
    path = watcher.projects_dir / '2026/rollout-transition.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def compress(path):
    compressed = Path(str(path) + '.zst')
    compressed.write_bytes(zstandard.ZstdCompressor().compress(path.read_bytes()))
    path.unlink()
    return compressed


async def settle(watcher):
    for _ in range(1000):
        task = watcher._consumer_task
        if task:
            await asyncio.wait_for(asyncio.shield(task), 5)
        if not getattr(watcher, '_preparations', {}) and watcher._queue.idle:
            return
        await asyncio.sleep(.001)
    raise AssertionError('watcher did not settle')


@pytest.fixture
def stored(provider_home, monkeypatch):
    from twicc.core.models import Project, Session
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    session = Session.objects.create(
        id=SID, project=Project.objects.create(id='p'), provider='codex',
        file_path=str(path.relative_to(watcher.projects_dir)), title='known',
        compute_version=watcher.get_compute().compute_version,
    )
    monkeypatch.setattr(base, 'broadcast_message', AsyncMock())
    monkeypatch.setattr(module, 'request_rebuild', lambda sid: pytest.fail('unexpected rebuild'))
    return watcher, path, session


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('reverse', [False, True])
def test_deleted_plain_with_compressed_sibling_never_broadcasts_stale(stored, reverse):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    watcher, path, _ = stored

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            generation = watcher._queue.source_generation(path)
            compressed = compress(path)
            events = [(path, Change.deleted), (compressed, Change.added)]
            for event in reversed(events) if reverse else events:
                watcher._enqueue(*event)
            watcher._enqueue(path, Change.deleted)
            await settle(watcher)
            row = await Session.objects.aget(id=SID)
            assert row.file_path.endswith('.jsonl.zst')
            assert not row.stale
            assert watcher._queue.source_generation(path) is generation
            assert all(not call.args[1].get('session', {}).get('stale')
                       for call in base.broadcast_message.await_args_list)
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_restoration_and_immediate_append_keep_history_and_generation(stored):
    from twicc.core.models import Session, SessionItem
    from twicc.providers import db_writer
    watcher, path, _ = stored

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            ids = [row.id async for row in SessionItem.objects.filter(session_id=SID).order_by('line_num')]
            generation = watcher._queue.source_generation(path)
            compressed = compress(path)
            await asyncio.wait_for(watcher.process_path(compressed), 5)
            await settle(watcher)
            path.write_bytes(records(2))
            watcher._enqueue(path, Change.added)
            compressed.unlink()
            watcher._enqueue(compressed, Change.deleted)
            await settle(watcher)
            row = await Session.objects.aget(id=SID)
            assert row.file_path == str(path.relative_to(watcher.projects_dir))
            assert row.last_line == 3
            assert row.last_offset == len(records(2))
            assert [item.id async for item in SessionItem.objects.filter(session_id=SID).order_by('line_num')][:2] == ids
            assert watcher._queue.source_generation(path) is generation
            assert compressed not in watcher._queue._paths
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('compressed_source', [False, True])
def test_unrelated_logical_source_keeps_generic_drain(stored, monkeypatch, compressed_source):
    from twicc.providers import db_writer
    from twicc.providers.codex.source_binding import ReconcileCodexSourceJob
    watcher, path, _ = stored
    jobs = []
    submit = module.submit_async_job

    async def track(job):
        jobs.append(job)
        return await submit(job)

    monkeypatch.setattr(module, 'submit_async_job', track)
    monkeypatch.setattr(module, '_watcher', watcher)

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            other = path.with_name('rollout-unrelated.jsonl')
            other.write_bytes(records())
            selected = compress(other) if compressed_source else other
            await asyncio.wait_for(watcher.process_path(selected), 2)
            await settle(watcher)
            assert not any(isinstance(job, ReconcileCodexSourceJob) for job in jobs)
            assert watcher._queue.event_token(other) == 1
            state = watcher._queue._paths[other]
            assert watcher._queue.idle and not state.queued and not state.in_flight
            assert not state.waiters
            assert not watcher._target_waiters
            assert not watcher._prepared and not watcher._preparations
            assert not watcher._preparation_waiting
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('compressed_source', [False, True])
def test_writer_logical_conflict_settles_waiter_without_retry(stored, monkeypatch, compressed_source):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    from twicc.providers.codex.source_binding import ReconcileCodexSourceJob
    watcher, path, _ = stored
    conflict = path.with_name('rollout-conflict.jsonl')
    conflict.write_bytes(records())
    jobs = []
    temporary_paths = []
    submit = module.submit_async_job
    prepare = module.prepare_rollout

    def track_preparation(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        if prepared.physical_path.suffix == '.zst':
            temporary_paths.append(prepared.read_path)
        return prepared

    async def reject(job):
        if isinstance(job, ReconcileCodexSourceJob):
            jobs.append(job)
            # The producer already validated an exact sibling. A different
            # association wins before the real writer publishes its envelope.
            await Session.objects.filter(pk=SID).aupdate(
                file_path=str(conflict.relative_to(watcher.projects_dir)),
            )
        return await submit(job)

    monkeypatch.setattr(module, '_watcher', watcher)

    async def run():
        db_writer.start_db_writer()
        try:
            selected = path if compressed_source else compress(path)
            await watcher.process_path(selected)
            await settle(watcher)
            if compressed_source:
                selected = compress(path)
            else:
                path.write_bytes(records())
                selected.unlink()
                selected = path
            monkeypatch.setattr(module, 'submit_async_job', reject)
            monkeypatch.setattr(module, 'prepare_rollout', track_preparation)
            token = watcher._queue.event_token(path)
            await asyncio.wait_for(watcher.process_path(selected), 2)
            await settle(watcher)
            assert len(jobs) == 1
            assert watcher._queue.event_token(path) == token + 1
            assert watcher._queue._paths[path].terminal == 'failed'
            state = watcher._queue._paths[path]
            assert watcher._queue.idle and not state.queued and not state.in_flight
            assert not state.waiters
            assert not watcher._target_waiters
            assert not watcher._prepared and not watcher._preparations
            assert not watcher._preparation_waiting
            assert all(not temporary.exists() for temporary in temporary_paths)
            row = await Session.objects.aget(pk=SID)
            assert row.file_path == str(conflict.relative_to(watcher.projects_dir))
            assert (row.last_offset, row.last_line) == (len(records()), 2)
            assert not row.stale and row.unavailable_reason is None
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_compressed_drain_decodes_once_across_slices(stored, monkeypatch):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    from twicc.providers.codex.rollout_sources import prepare_rollout
    watcher, path, _ = stored
    path.write_bytes(records(1100))
    compressed = compress(path)
    preparations = []

    def prepare(*args, **kwargs):
        result = prepare_rollout(*args, **kwargs)
        preparations.append(result)
        return result
    monkeypatch.setattr(module, 'prepare_rollout', prepare, raising=False)
    slices = []
    compute = watcher.get_compute()
    original = compute.sync_session_slice

    def sliced(*args, **kwargs):
        result = original(*args, **kwargs)
        slices.append((kwargs['limits'], result.lines_processed))
        return result
    monkeypatch.setattr(compute, 'sync_session_slice', sliced)

    async def run():
        db_writer.start_db_writer()
        try:
            await asyncio.wait_for(watcher.process_path(compressed), 5)
            await settle(watcher)
            assert (await Session.objects.aget(id=SID)).last_line == 1101
            assert len(preparations) == 1
            assert len(slices) >= 3
            assert all(count <= 500 for _, count in slices)
            assert not preparations[0].read_path.exists()
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_distinct_temp_files_for_unchanged_zst_preserve_generation(stored, monkeypatch):
    from twicc.providers import db_writer
    watcher, path, _ = stored
    original = module.prepare_rollout
    read_paths = []

    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        read_paths.append(result.read_path)
        return result
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        db_writer.start_db_writer()
        try:
            compressed = compress(path)
            await asyncio.wait_for(watcher.process_path(compressed), 5)
            await settle(watcher)
            generation = watcher._queue.source_generation(path)
            await asyncio.wait_for(watcher.process_path(compressed), 5)
            await settle(watcher)
            assert watcher._queue.source_generation(path) is generation
            assert path not in watcher._replaced_paths
            assert len(read_paths) == 2 and read_paths[0] != read_paths[1]
            assert all(not read_path.exists() for read_path in read_paths)
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_late_root_enumerates_both_extensions_once(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    plain = source(watcher, records())
    compressed = Path(str(plain) + '.zst')
    compressed.write_bytes(b'ignored sibling')
    other = plain.with_name('rollout-other.jsonl.zst')
    other.write_bytes(b'compressed')
    seen = []
    monkeypatch.setattr(watcher, '_enqueue', lambda path, change: seen.append(path))
    asyncio.run(watcher._catch_up_existing_files(None))
    assert seen == [other, plain]


def lightweight(watcher, monkeypatch):
    async def call(callback, **kwargs):
        return await callback()
    monkeypatch.setattr(base, 'run_under_db_write_lock', call)
    processed = []

    async def process(path, parsed, change, channel, **kwargs):
        prepared = watcher._prepared.get(path)
        data = (prepared.read_path if prepared else path).read_bytes()
        processed.append((path, parsed.file_path, data))
        return base.SessionChangeResult('drained', watcher._queue.source_generation(path), len(data))
    monkeypatch.setattr(watcher, '_process_parsed_session_change', process)
    return processed


def test_preparation_completion_restarts_idle_consumer(provider_home, monkeypatch):
    import threading
    from twicc.providers.codex.rollout_sources import prepare_rollout
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    entered, release = threading.Event(), threading.Event()
    processed = lightweight(watcher, monkeypatch)

    def prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return prepare_rollout(*args, **kwargs)
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        watcher._enqueue(compressed, Change.added)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await watcher._consumer_task
            assert watcher._queue.idle and not processed
            first = watcher._consumer_task
            token = watcher._queue.event_token(path)
            release.set()
            await settle(watcher)
            assert watcher._consumer_task is not first
            assert watcher._queue.event_token(path) == token
            assert processed == [(path, str(compressed.relative_to(watcher.projects_dir)), records())]
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_preparation_failure_settles_admitted_waiter(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    compressed.write_bytes(compressed.read_bytes()[:-1])
    processed = lightweight(watcher, monkeypatch)

    async def run():
        try:
            await asyncio.wait_for(watcher.process_path(compressed), 3)
            await settle(watcher)
            assert not processed
            assert watcher._queue._paths[path].terminal == 'failed'
            assert not watcher._target_waiters
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


@pytest.mark.parametrize('supersede', ['event', 'compressed-event', 'failed-release', 'shutdown'])
def test_stale_preparation_never_publishes(provider_home, monkeypatch, supersede):
    import threading
    from twicc.providers.codex.rollout_sources import prepare_rollout
    from twicc.providers.session_change_queue import MigrationRelease
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    entered, release = threading.Event(), threading.Event()
    prepared_files = []
    processed = lightweight(watcher, monkeypatch)

    def prepare(*args, **kwargs):
        # Finish a real decode even when shutdown races with completion.
        result = prepare_rollout(*args)
        if result.physical_path.suffix == '.zst':
            prepared_files.append(result.read_path)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        watcher._enqueue(compressed, Change.added)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await watcher._consumer_task
            if supersede == 'event':
                path.write_bytes(records(2))
                watcher._enqueue(path, Change.added)
            elif supersede == 'compressed-event':
                compressed.write_bytes(zstandard.ZstdCompressor().compress(records(2)))
                watcher._enqueue(compressed, Change.modified)
                await watcher._consumer_task
            elif supersede == 'failed-release':
                # Attribute the migration outcome without any DB lookup.
                watcher._parsed_paths[path] = base.ParsedSessionFile('p', SID, 'session', str(compressed))
                watcher.notify_migration_released(MigrationRelease(SID, path, 'failed', False, 'failure', 1))
            else:
                watcher.stop_watcher()
            release.set()
            if supersede == 'shutdown':
                await watcher._drain_changes()
                watcher.prepare_start()
                assert watcher._queue.idle
            else:
                await settle(watcher)
            if supersede in ('event', 'compressed-event'):
                assert len(processed) == 1
                assert processed[0][2] == records(2)
            else:
                assert not processed
            assert all(not file.exists() for file in prepared_files)
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_plain_append_bypasses_compressed_preparation_and_sibling_stats(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    processed = lightweight(watcher, monkeypatch)
    monkeypatch.setattr(module, 'prepare_rollout', lambda *a, **k: pytest.fail('plain preparation'))
    monkeypatch.setattr(module, 'resolve_rollout', lambda *a: pytest.fail('plain sibling resolution'))
    monkeypatch.setattr(module, 'submit_async_job', AsyncMock(side_effect=AssertionError('plain association query')))
    # Saturated compressed admission cannot delay the plain callback.
    watcher._preparation_waiting[path.with_name('rollout-waiting.jsonl')] = None

    async def run():
        try:
            await asyncio.wait_for(watcher.process_path(path), 2)
            await settle(watcher)
            path.write_bytes(records(2))
            await asyncio.wait_for(watcher.process_path(path), 2)
            await settle(watcher)
            assert len(processed) == 2
            assert not watcher._preparations
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_same_representation_replacement_still_requests_rebuild(provider_home, monkeypatch):
    from types import SimpleNamespace
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    old = path.with_suffix('.old')
    session = SimpleNamespace(id=SID, last_offset=len(records()))
    monkeypatch.setattr(module, 'get_session_by_id', AsyncMock(return_value=session))
    jobs = []
    async def submit(job):
        jobs.append(job)
    monkeypatch.setattr(module, 'submit_async_job', submit)
    rebuilds = []
    monkeypatch.setattr(module, 'request_rebuild', rebuilds.append)

    async def run():
        before = await watcher._observe_source(path)
        path.rename(old)
        path.write_bytes(records())
        after = await watcher._observe_source(path)
        parsed = base.ParsedSessionFile('p', SID, 'session', path.name)
        assert await watcher._rewrite_detected(parsed, path)
        assert after.source_generation is not before.source_generation
        assert len(jobs) == 1 and rebuilds == [SID]
    asyncio.run(run())


def test_compressed_replacement_reaches_existing_rebuild_handler(provider_home, monkeypatch):
    from types import SimpleNamespace
    from twicc.providers.codex.rollout_sources import prepare_rollout
    watcher = CodexSessionsWatcher()
    path = source(watcher, records(20))
    compressed = compress(path)
    monkeypatch.setattr(module, 'get_session_by_id', AsyncMock(return_value=SimpleNamespace(id=SID, last_offset=1, stale=False, unavailable_reason=None)))
    jobs = []
    async def submit(job):
        jobs.append(job)
    monkeypatch.setattr(module, 'submit_async_job', submit)
    monkeypatch.setattr(module, 'request_rebuild', lambda sid: None)

    async def run():
        first = prepare_rollout(compressed)
        watcher._prepared[path] = first
        before = await watcher._observe_source(path)
        first.close()
        old = compressed.with_suffix('.old')
        compressed.rename(old)
        compressed.write_bytes(old.read_bytes())
        second = prepare_rollout(compressed)
        watcher._prepared[path] = second
        try:
            after = await watcher._observe_source(path)
            assert after.source_generation is not before.source_generation
            parsed = base.ParsedSessionFile('p', SID, 'session', str(compressed))
            assert await watcher._rewrite_detected(parsed, path, read_path=second.read_path)
            assert len(jobs) == 1
            assert path in watcher._replaced_paths
            assert second.read_path not in watcher._replaced_paths
        finally:
            second.close()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_genuine_deletion_marks_stale(stored):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    watcher, path, _ = stored

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            path.unlink()
            watcher._enqueue(path, Change.deleted)
            await settle(watcher)
            assert (await Session.objects.aget(id=SID)).stale
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_two_prepared_drains_bound_admission_without_blocking_plain(provider_home, monkeypatch):
    import threading
    from twicc.providers.codex.rollout_sources import prepare_rollout
    watcher = CodexSessionsWatcher()
    first = source(watcher, records())
    paths = [first.with_name(f'rollout-{n}.jsonl') for n in range(3)]
    for path in paths:
        path.write_bytes(records())
    compressed = [compress(path) for path in paths]
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    started = []
    prepared_files = []
    processed = lightweight(watcher, monkeypatch)

    def prepare(path, **kwargs):
        started.append(path)
        number = len(started) - 1
        if number < 2:
            entered[number].set()
            assert release.wait(5)
        result = prepare_rollout(path, **kwargs)
        prepared_files.append(result.read_path)
        return result
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        try:
            for path in compressed:
                watcher._enqueue(path, Change.added)
            assert all(await asyncio.gather(*(asyncio.to_thread(event.wait, 2) for event in entered)))
            await watcher._consumer_task
            assert len(started) == 2
            assert len(watcher._preparation_waiting) == 1
            assert not prepared_files
            await asyncio.wait_for(watcher.process_path(first), 2)
            assert len(processed) == 1 and processed[0][0] == first
            release.set()
            await settle(watcher)
            assert len(started) == 3
            assert len(processed) == 4
            assert all(not file.exists() for file in prepared_files)
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_compressed_finite_target_survives_restoration_with_later_append(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    lightweight(watcher, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pinned = []

    async def process(logical, parsed, change, channel, **kwargs):
        prepared = watcher._prepared[logical]
        pinned.append(prepared.read_path)
        entered.set()
        await release.wait()
        assert prepared.read_path.exists()
        return base.SessionChangeResult('drained', watcher._queue.source_generation(logical), len(records()))
    monkeypatch.setattr(watcher, '_process_parsed_session_change', process)

    async def run():
        waiter = asyncio.create_task(watcher.process_path(compressed))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            path.write_bytes(records(2))
            # The later append is outside the admitted snapshot's finite target.
            assert not waiter.done()
            release.set()
            await asyncio.wait_for(waiter, 2)
            await settle(watcher)
            assert watcher._sources[path].end_offset == len(records())
            assert all(not file.exists() for file in pinned)
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
            await asyncio.gather(waiter, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['invalid-zst', 'missing-read'])
def test_failed_compressed_read_keeps_history_and_existing_source_available(stored, monkeypatch, failure):
    from twicc.core.models import Session, SessionItem
    from twicc.providers import db_writer
    watcher, path, _ = stored

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            before = await Session.objects.aget(id=SID)
            ids = [item.id async for item in SessionItem.objects.filter(session_id=SID).order_by('line_num')]
            compressed = compress(path)
            if failure == 'invalid-zst':
                compressed.write_bytes(compressed.read_bytes()[:-1])
            else:
                def missing(*args, **kwargs):
                    raise FileNotFoundError('read race, selected source still exists')
                monkeypatch.setattr(module, 'prepare_rollout', missing)
            await asyncio.wait_for(watcher.process_path(compressed), 3)
            await settle(watcher)
            after = await Session.objects.aget(id=SID)
            assert not after.stale
            assert after.last_offset == before.last_offset
            assert after.last_line == before.last_line
            assert after.compute_version == before.compute_version
            assert ids == [item.id async for item in SessionItem.objects.filter(session_id=SID).order_by('line_num')]
            assert compressed.exists()
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_startup_logical_catch_up_resolves_zst_after_search_activation(stored):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    watcher, path, _ = stored
    compressed = compress(path)

    async def run():
        db_writer.start_db_writer()
        try:
            # Startup rejection queues directly before search activation.
            watcher._queue.enqueue(path, Change.modified)
            assert watcher._consumer_task is None
            watcher._channel_layer = object()
            watcher._ensure_consumer()
            await settle(watcher)
            row = await Session.objects.aget(id=SID)
            assert row.last_offset == len(records())
            assert row.file_path == str(compressed.relative_to(watcher.projects_dir))
            assert not row.stale
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            watcher._channel_layer = None
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_plain_open_race_rechecks_compressed_sibling_before_stale(stored, monkeypatch):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    watcher, path, _ = stored
    original_prepare = module._prepare_plain_rollout
    raced = []

    def prepare(selected, **kwargs):
        if selected.suffix != '.zst' and not raced:
            raced.append(compress(selected))
            raise FileNotFoundError('plain switched after selection')
        return original_prepare(selected, **kwargs)

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            compressed = compress(path)
            await watcher.process_path(compressed)
            await settle(watcher)
            path.write_bytes(records(2))
            # Keep the compressed inode: the injected race rewrites this
            # sibling in place before the watcher observes the plain source.
            monkeypatch.setattr(module, '_prepare_plain_rollout', prepare)
            watcher._enqueue(path, Change.added)
            await settle(watcher)
            assert raced
            row = await Session.objects.aget(id=SID)
            assert not row.stale
            assert row.last_line == 3
            assert row.file_path.endswith('.jsonl.zst')
            assert all(not call.args[1].get('session', {}).get('stale')
                       for call in base.broadcast_message.await_args_list)
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_direct_plain_restoration_drain_retains_generation(stored):
    from twicc.core.models import Session
    from twicc.providers import db_writer
    watcher, path, _ = stored

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            compressed = compress(path)
            await watcher.process_path(compressed)
            await settle(watcher)
            generation = watcher._queue.source_generation(path)
            path.write_bytes(records(2))
            await asyncio.wait_for(watcher.process_path(path), 3)
            await settle(watcher)
            assert watcher._queue.source_generation(path) is generation
            row = await Session.objects.aget(id=SID)
            assert row.last_line == 3
            assert row.file_path.endswith('.jsonl')
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_migration_release_uses_logical_path(provider_home):
    from twicc.providers.session_change_queue import MigrationRelease
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    watcher.notify_migration_released(MigrationRelease(SID, compressed, 'failed', False, 'failure', 123))
    assert watcher._queue.release_token(path) == 123
    assert compressed not in watcher._queue._paths


def test_new_event_does_not_inherit_completed_preparation_error(provider_home, monkeypatch):
    import threading
    from twicc.providers.codex.rollout_sources import prepare_rollout
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    processed = lightweight(watcher, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def prepare(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            entered.set()
            assert release.wait(5)
            raise OSError('old attempt failed')
        return prepare_rollout(*args, **kwargs)
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        ensure = watcher._ensure_consumer
        watcher._enqueue(compressed, Change.added)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await watcher._consumer_task
            watcher._ensure_consumer = lambda: None
            preparation = watcher._preparations[path]
            release.set()
            await preparation
            assert path in watcher._preparation_errors
            compressed.write_bytes(zstandard.ZstdCompressor().compress(records(2)))
            watcher._ensure_consumer = ensure
            watcher._enqueue(compressed, Change.modified)
            await settle(watcher)
            assert len(processed) == 1 and processed[0][2] == records(2)
        finally:
            release.set()
            watcher._ensure_consumer = ensure
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_both_siblings_select_plain_without_decoding(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = Path(str(path) + '.zst')
    compressed.write_bytes(b'broken obsolete sibling')
    processed = lightweight(watcher, monkeypatch)
    monkeypatch.setattr(module, 'prepare_rollout', lambda *a, **k: pytest.fail('plain sibling decoded'))

    async def run():
        try:
            watcher._enqueue(compressed, Change.added)
            await settle(watcher)
            assert processed == [(path, str(path.relative_to(watcher.projects_dir)), records())]
            assert not watcher._preparations
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


@pytest.mark.parametrize('active', [False, True], ids=['deferred', 'active-reader'])
def test_terminal_migration_releases_prepared_file_after_reader_finishes(provider_home, monkeypatch, active):
    from twicc.providers.session_change_queue import MigrationRelease
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    lightweight(watcher, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pinned = []

    async def process(logical, *args, **kwargs):
        read_path = watcher._prepared[logical].read_path
        pinned.append(read_path)
        entered.set()
        if active:
            await release.wait()
            assert read_path.exists(), 'migration release deleted an active read path'
        return base.SessionChangeResult('deferred')
    monkeypatch.setattr(watcher, '_process_parsed_session_change', process)

    async def run():
        try:
            watcher._enqueue(compressed, Change.added)
            await asyncio.wait_for(entered.wait(), 2)
            if not active:
                await watcher._consumer_task
            watcher.notify_migration_released(MigrationRelease(SID, compressed, 'failed', False, 'failure', 901))
            if active:
                assert pinned[0].exists()
                release.set()
                await watcher._consumer_task
            assert not pinned[0].exists(), 'terminal deferred source kept its preparation slot'
            assert path not in watcher._prepared
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_ready_migration_replay_replaces_obsolete_preparation(provider_home, monkeypatch):
    import threading
    from twicc.providers.codex.rollout_sources import prepare_rollout
    from twicc.providers.session_change_queue import MigrationRelease
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    processed = lightweight(watcher, monkeypatch)
    watcher._parsed_paths[path] = base.ParsedSessionFile('p', SID, 'session', str(compressed))
    entered, release = threading.Event(), threading.Event()
    preparations = []

    def prepare(*args, **kwargs):
        result = prepare_rollout(*args)
        preparations.append(result.read_path)
        if len(preparations) == 1:
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(module, 'prepare_rollout', prepare)

    async def run():
        try:
            watcher._enqueue(compressed, Change.modified)
            assert await asyncio.to_thread(entered.wait, 2)
            await watcher._consumer_task
            watcher.notify_migration_released(MigrationRelease(SID, compressed, 'ready', True, None, 902))
            await watcher._consumer_task
            release.set()
            await settle(watcher)
            assert len(processed) == 1
            assert len(preparations) == 2
            assert all(not file.exists() for file in preparations)
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_decode_error_from_restored_plain_retries_without_new_event(provider_home, monkeypatch):
    from twicc.providers.codex import rollout_sources
    watcher = CodexSessionsWatcher()
    data = records()
    path = source(watcher, data)
    compressed = compress(path)
    processed = lightweight(watcher, monkeypatch)
    decoded_chunks = rollout_sources._decoded_chunks
    restorations = []

    def restore_after_output(stream, stop):
        for chunk in decoded_chunks(stream, stop):
            yield chunk
            if not restorations:
                # The real decoder rejects its old source at final validation.
                path.write_bytes(data)
                compressed.unlink()
                restorations.append(path)
    monkeypatch.setattr(rollout_sources, '_decoded_chunks', restore_after_output)

    async def run():
        try:
            await asyncio.wait_for(watcher.process_path(compressed), 3)
            await settle(watcher)
            assert restorations == [path]
            assert processed == [(path, str(path.relative_to(watcher.projects_dir)), data)]
            assert watcher._queue.event_token(path) == 1
            assert watcher._queue._paths[path].terminal is None
            assert not watcher._preparation_errors
            assert not watcher._prepared
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


@pytest.mark.parametrize('replacement', ['plain', 'compressed'])
def test_fingerprint_open_after_plain_restoration_retries_without_new_event(provider_home, monkeypatch, replacement):
    watcher = CodexSessionsWatcher()
    data = records()
    path = source(watcher, data)
    compressed = compress(path)
    compressed_data = compressed.read_bytes()
    selected_after = path if replacement == 'plain' else compressed
    processed = lightweight(watcher, monkeypatch)
    capture = module._prepare_plain_rollout
    restorations = []

    def restore_before_capture(selected):
        if selected == compressed and not restorations:
            compressed.unlink()
            if replacement == 'plain':
                path.write_bytes(data)
                restorations.append(path)
            else:
                try:
                    return capture(selected)
                finally:
                    # The real missing open precedes a same-name replacement.
                    compressed.write_bytes(compressed_data)
                    restorations.append(compressed)
        # Exercise the real FileNotFoundError from opening the old selection.
        return capture(selected)
    monkeypatch.setattr(module, '_prepare_plain_rollout', restore_before_capture)

    async def run():
        try:
            await asyncio.wait_for(watcher.process_path(compressed), 3)
            await settle(watcher)
            assert restorations == [selected_after]
            assert processed == [(path, str(selected_after.relative_to(watcher.projects_dir)), data)]
            assert watcher._queue.event_token(path) == 1
            assert watcher._queue._paths[path].terminal is None
            assert not watcher._preparation_errors
            assert not watcher._prepared
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())


def test_fingerprint_permission_failure_remains_terminal(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    compressed = compress(path)
    processed = lightweight(watcher, monkeypatch)
    captures = []

    def denied(selected):
        captures.append(selected)
        raise PermissionError(13, 'Permission denied', str(selected))
    monkeypatch.setattr(module, '_prepare_plain_rollout', denied)

    async def run():
        try:
            await asyncio.wait_for(watcher.process_path(compressed), 3)
            await settle(watcher)
            assert captures == [compressed]
            assert not processed
            assert watcher._queue._paths[path].terminal == 'failed'
            assert watcher._queue.event_token(path) == 1
            assert compressed.exists()
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
    asyncio.run(run())
