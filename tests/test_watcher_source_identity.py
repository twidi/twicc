"""Timestamp changes preserve append sources; observed content changes do not."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from watchfiles import Change

from twicc.providers import sessions_watcher as module
from twicc.providers.claude_code.sessions_watcher import ClaudeCodeSessionsWatcher
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.parametrize('admission', ['filesystem-event', 'process-path'])
def test_touch_then_append_commits_through_watcher(provider_home, monkeypatch, provider, admission):
    from twicc.core.models import Project, Session, SessionItem
    from twicc.providers import db_writer
    from twicc.providers.codex import sessions_watcher as codex_module

    sid = '00000000-0000-4000-8000-000000000001'
    if provider == 'claude_code':
        watcher = ClaudeCodeSessionsWatcher()
        relative = f'p/{sid}.jsonl'
        records = [{'type': 'user', 'uuid': 'first', 'message': {'role': 'user', 'content': 'a' * 10000}}]
        appended = {'type': 'user', 'uuid': 'second', 'message': {'role': 'user', 'content': 'next'}}
    else:
        watcher = CodexSessionsWatcher()
        relative = '2026/rollout-touch.jsonl'
        records = [
            {'type': 'session_meta', 'payload': {'id': sid, 'cwd': '/tmp/p', 'history_mode': 'paginated'}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'a' * 10000}},
        ]
        appended = {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'next'}}
    path = watcher.projects_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = b''.join(orjson.dumps(record) + b'\n' for record in records)
    path.write_bytes(original_bytes)
    session = Session.objects.create(
        id=sid, project=Project.objects.create(id='p'), provider=provider, file_path=relative,
        compute_version=watcher.get_compute().compute_version, title='known',
    )
    monkeypatch.setattr(module, 'broadcast_message', AsyncMock())
    rebuilds = []
    monkeypatch.setattr(codex_module, 'request_rebuild', rebuilds.append)

    async def run():
        async def process_change():
            if admission == 'process-path':
                await asyncio.wait_for(watcher.process_path(path), 5)
            else:
                watcher._enqueue(path, Change.modified)
            await watcher._consumer_task

        db_writer.start_db_writer()
        try:
            await asyncio.wait_for(watcher.process_path(path), 5)
            await watcher._consumer_task
            committed = await Session.objects.aget(id=sid)
            assert committed.last_offset == len(original_bytes) > 0
            assert committed.last_line == len(records)
            generation = watcher._queue.source_generation(path)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            assert path.read_bytes() == original_bytes
            await process_change()
            with path.open('ab') as stream:
                stream.write(orjson.dumps(appended) + b'\n')
            await process_change()
            committed = await Session.objects.aget(id=sid)
            assert not rebuilds, 'metadata-only touch requested Codex reconstruction'
            assert committed.last_line == len(records) + 1, 'valid append never reached sync'
            assert committed.last_offset == path.stat().st_size
            assert await SessionItem.objects.filter(session_id=sid, line_num=len(records) + 1).aexists()
            assert watcher._queue.source_generation(path) is generation
            assert path not in watcher._replaced_paths
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()

    asyncio.run(run())
    session.refresh_from_db()
    assert session.compute_version == watcher.get_compute().compute_version


@pytest.mark.parametrize('size', [16384, 16 * 1024 * 1024])
def test_touch_and_growth_observation_reads_bounded_source_evidence(tmp_path, monkeypatch, size):
    from pathlib import Path

    path = tmp_path / 'sparse.jsonl'
    with path.open('wb') as stream:
        stream.seek(size - 1)
        stream.write(b'\n')
    real_open = Path.open
    reads = []

    class ReadProbe:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert 0 <= size <= 65536
            result = self.stream.read(size)
            reads.append(len(result))
            return result

    monkeypatch.setattr(Path, 'open', lambda self, *args, **kwargs: ReadProbe(real_open(self, *args, **kwargs)))

    async def run():
        watcher = module.BaseSessionsWatcher()
        before = await watcher._observe_source(path)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        reads.clear()
        touched = await watcher._observe_source(path)
        assert sum(reads) <= 65536 + 3 * 4096
        assert touched.source_generation is before.source_generation
        with real_open(path, 'ab') as stream:
            stream.write(b'{}\n' * 2000)
        reads.clear()
        appended = await watcher._observe_source(path)
        assert sum(reads) <= 65536 + 3 * 4096
        assert appended.source_generation is before.source_generation
        assert appended.end_offset == size + 6000

    asyncio.run(run())


def test_touch_and_append_preserve_an_admitted_waiter(tmp_path):
    async def run():
        path = tmp_path / 'waiting.jsonl'
        path.write_bytes(b'{}\n' * 4000)
        watcher = module.BaseSessionsWatcher()
        before = await watcher._observe_source(path)
        entered, commit = asyncio.Event(), asyncio.Event()

        async def process(*args):
            entered.set()
            await commit.wait()
            return module.SessionChangeResult('drained', before.source_generation, before.end_offset)

        watcher._process_change = process
        waiter = asyncio.create_task(watcher.process_path(path))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            touched = await watcher._observe_source(path)
            assert touched.source_generation is before.source_generation
            assert not waiter.done(), 'touch prematurely settled the admitted target'
            with path.open('ab') as stream:
                stream.write(b'{}\n' * 2000)
            appended = await watcher._observe_source(path)
            assert appended.source_generation is before.source_generation
            assert not waiter.done()
            commit.set()
            await asyncio.wait_for(waiter, 1)
            # The original finite target can finish before the newer bytes commit.
            assert appended.end_offset > before.end_offset
        finally:
            commit.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
            await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.parametrize('growth', [False, True], ids=['same-size', 'larger'])
def test_in_place_tail_rewrite_precedes_append_slice(tmp_path, monkeypatch, provider, growth):
    from twicc.core.models import SessionType
    from twicc.providers.codex import sessions_watcher as codex_module

    async def run():
        watcher = ClaudeCodeSessionsWatcher() if provider == 'claude_code' else CodexSessionsWatcher()
        path = tmp_path / 'rewrite.jsonl'
        path.write_bytes(b'{}\n' * 4000 + b'{"old":1}\n')
        before = await watcher._observe_source(path)
        stat = path.stat()
        with path.open('r+b') as stream:
            stream.seek(-10, os.SEEK_END)
            stream.write(b'{"new":2}\n')
            if growth:
                stream.write(b'{}\n' * 2000)
        assert path.stat().st_ino == stat.st_ino
        after = await watcher._observe_source(path)
        session = SimpleNamespace(id='rewrite', last_offset=stat.st_size)
        monkeypatch.setattr(module, 'get_session_by_id', AsyncMock(return_value=session))
        monkeypatch.setattr(codex_module, 'get_session_by_id', AsyncMock(return_value=session))
        jobs = []
        async def submit(job):
            jobs.append(job)
        monkeypatch.setattr(codex_module, 'submit_async_job', submit)
        monkeypatch.setattr(codex_module, 'request_rebuild', lambda sid: None)
        monkeypatch.setattr(module, 'run_under_db_write_lock', lambda callback: callback())
        watcher.sync_and_broadcast = AsyncMock(side_effect=AssertionError('rewritten source was sliced'))
        parsed = module.ParsedSessionFile(
            'p', session.id, SessionType.SESSION, path.name, title='known', compute_ready_on_create=False,
        )
        result = await watcher._process_parsed_session_change(path, parsed, Change.modified, None)
        assert result.disposition == ('failed' if provider == 'claude_code' else 'deferred')
        assert len(jobs) == (provider == 'codex')
        assert after.source_generation is not before.source_generation
        watcher.sync_and_broadcast.assert_not_awaited()

    asyncio.run(run())
