"""Instrument real ordinary callbacks and compressed-worker isolation."""

import asyncio
import builtins
import threading
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock

import orjson
import pytest
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.test.utils import CaptureQueriesContext
from watchfiles import Change

from tests.test_codex_compressed_initial_sync import existing, produce, rollout
from tests.test_codex_compressed_lifecycle import records
from tests.test_codex_compressed_watcher import SID, compress, settle, source
from twicc.core.models import Project, Session
from twicc.providers import db_writer, sessions_watcher as base
from twicc.providers.codex import rollout_sources, sessions_watcher as module
from twicc.providers.codex.sessions_watcher import CodexSessionsWatcher


@pytest.mark.django_db
def test_unchanged_plain_inventory_has_no_new_source_work(provider_home, monkeypatch):
    path, raw = rollout(provider_home)
    existing(provider_home, path, raw)
    opened = []
    original_open, original_builtin = Path.open, builtins.open

    def path_open(value, *args, **kwargs):
        if value == path:
            opened.append(value)
        return original_open(value, *args, **kwargs)

    def builtin_open(value, *args, **kwargs):
        if value == str(path) or value == path:
            opened.append(value)
        return original_builtin(value, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', path_open)
    monkeypatch.setattr(builtins, 'open', builtin_open)
    monkeypatch.setattr(rollout_sources, '_decoded_chunks', lambda *_a, **_k: pytest.fail('decode'))
    monkeypatch.setattr(rollout_sources.tempfile, 'NamedTemporaryFile', lambda *_a, **_k: pytest.fail('copy'))
    with CaptureQueriesContext(connection) as queries:
        messages, stats = produce()
    assert not opened
    assert len(queries) == 2
    assert stats['items_added'] == 0
    assert not any(isinstance(message, db_writer.UpdateSessionPayload) for message in messages)


@pytest.mark.django_db(transaction=True)
def test_indexed_history_uses_no_rollout_access(provider_home, monkeypatch):
    from django.test import RequestFactory
    from twicc import views

    path, raw = rollout(provider_home)
    session = existing(provider_home, path, raw)
    compressed = compress(path)
    Session.objects.filter(pk=session.pk).update(file_path=str(compressed.relative_to(provider_home.codex / 'sessions')))
    original_open, original_builtin = Path.open, builtins.open

    def guard(value):
        if str(value) in {str(path), str(compressed)}:
            pytest.fail('indexed history accesses provider rollout')

    def path_open(value, *args, **kwargs):
        guard(value)
        return original_open(value, *args, **kwargs)

    def builtin_open(value, *args, **kwargs):
        guard(value)
        return original_builtin(value, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', path_open)
    monkeypatch.setattr(builtins, 'open', builtin_open)
    monkeypatch.setattr(rollout_sources, '_decoded_chunks', lambda *_a, **_k: pytest.fail('decode'))
    monkeypatch.setattr(rollout_sources.tempfile, 'NamedTemporaryFile', lambda *_a, **_k: pytest.fail('copy'))
    request = RequestFactory().get('/items/', {'range': '1:3'})
    response = asyncio.run(views.session_items(request, session.project_id, session.id))
    assert response.status_code == 200
    values = orjson.loads(response.content)
    assert [value['line_num'] for value in values] == [1, 2, 3]
    assert [value['content'] for value in values] == [line.decode() for line in raw.splitlines()]


@pytest.mark.django_db(transaction=True)
def test_real_plain_callback_adds_no_association_or_worker_cost(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    path = source(watcher, records())
    Session.objects.create(id=SID, project=Project.objects.create(id='p'), provider='codex',
                           file_path=str(path.relative_to(watcher.projects_dir)), title='known',
                           compute_version=watcher.get_compute().compute_version)
    monkeypatch.setattr(base, 'broadcast_message', AsyncMock())
    monkeypatch.setattr(module, 'request_rebuild', lambda *_: pytest.fail('rebuild'))
    original_open, original_builtin = Path.open, builtins.open
    original_stat = Path.stat
    counts = Counter()
    queries = []
    reads = []
    original_execute, original_many = CursorWrapper.execute, CursorWrapper.executemany

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            data = self.stream.read(size)
            reads.append(('read', size, len(data)))
            return data

        def readline(self, size=-1):
            data = self.stream.readline(size)
            reads.append(('readline', size, len(data)))
            return data

    def opened(value, *args, **kwargs):
        stream = original_open(value, *args, **kwargs)
        if value == path and args and args[0] == 'rb':
            counts['opens'] += 1
            return Reader(stream)
        return stream

    def builtin_open(value, *args, **kwargs):
        stream = original_builtin(value, *args, **kwargs)
        if value == path or value == str(path):
            counts['opens'] += 1
            return Reader(stream)
        return stream

    def stat(value, *args, **kwargs):
        if value == path.with_suffix('.jsonl.zst'):
            pytest.fail('ordinary callback stats compressed sibling')
        return original_stat(value, *args, **kwargs)

    def execute(cursor, sql, params=None):
        queries.append(sql)
        return original_execute(cursor, sql, params)

    def executemany(cursor, sql, params):
        queries.append(sql)
        return original_many(cursor, sql, params)

    def forbidden(name):
        def fail(*args, **kwargs):
            counts[name] += 1
            pytest.fail(name)
        return fail

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(path)
            await settle(watcher)
            # Warm parsing and history-mode state. Keep the real callback intact.
            counts.clear()
            reads.clear()
            queries.clear()
            with monkeypatch.context() as patch:
                patch.setattr(Path, 'open', opened)
                patch.setattr(Path, 'stat', stat)
                patch.setattr(builtins, 'open', builtin_open)
                patch.setattr(CursorWrapper, 'execute', execute)
                patch.setattr(CursorWrapper, 'executemany', executemany)
                patch.setattr(module, 'resolve_rollout', forbidden('sibling_resolution'), raising=False)
                patch.setattr(module, 'prepare_rollout', forbidden('prepare'), raising=False)
                patch.setattr(module, 'submit_async_job', forbidden('association_jobs'))
                patch.setattr(watcher, '_start_preparations', forbidden('worker_admission'), raising=False)
                patch.setattr(rollout_sources, '_decoded_chunks', forbidden('decode'))
                patch.setattr(rollout_sources.tempfile, 'NamedTemporaryFile', forbidden('copies'))
                # No-op notifications still exercise SQL inside the Codex callback.
                watcher._enqueue(path, Change.modified)
                await settle(watcher)
                unchanged = (dict(counts), len(queries), list(reads))
                counts.clear()
                reads.clear()
                queries.clear()
                append = records(2)[len(records()):]
                with original_open(path, 'ab') as stream:
                    stream.write(append)
                watcher._enqueue(path, Change.modified)
                await settle(watcher)
                appended = (dict(counts), len(queries), list(reads))
            row = await Session.objects.aget(pk=SID)
            assert row.last_offset == len(records(2)) and row.last_line == 3
            print(f'PLAIN_CALLBACK unchanged={unchanged!r} appended={appended!r}')
            assert unchanged[0] == {'opens': 3}
            assert appended[0] == {'opens': 3}
            # Pre-task-3 watcher measured with this same callback test: 15/59.
            assert unchanged[1] <= 15
            assert appended[1] <= 59
            assert len(unchanged[2]) == 5 and len(appended[2]) == 8
            # Existing snapshot, first-record metadata and incremental live reader.
            assert [entry for entry in unchanged[2] if entry[1] < 0] == [('readline', -1, 122)]
            assert [entry for entry in appended[2] if entry[1] < 0] == [('readline', -1, 122)]
            assert sum(length for _, _, length in appended[2]) <= 5 * len(records(2))
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()

    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_blocked_compressed_decode_does_not_delay_real_plain_append(provider_home, monkeypatch):
    watcher = CodexSessionsWatcher()
    plain = source(watcher, records())
    other_id = '00000000-0000-4000-8000-000000000032'
    other = plain.with_name('rollout-blocked.jsonl')
    backlog = records() + b''.join(orjson.dumps({
        'type': 'event_msg', 'payload': {'type': 'token_count', 'n': number},
    }) + b'\n' for number in range(1099))
    other.write_bytes(backlog.replace(SID.encode(), other_id.encode()))
    compressed = compress(other)
    original_bytes = compressed.read_bytes()
    project = Project.objects.create(id='p')
    for sid, path in [(SID, plain), (other_id, compressed)]:
        Session.objects.create(id=sid, project=project, provider='codex', title='known',
                               file_path=str(path.relative_to(watcher.projects_dir)),
                               compute_version=watcher.get_compute().compute_version)
    monkeypatch.setattr(base, 'broadcast_message', AsyncMock())
    monkeypatch.setattr(module, 'request_rebuild', lambda *_: pytest.fail('rebuild'))
    entered, release = threading.Event(), threading.Event()
    prepared, chunks, copies, input_reads, decodes, slices = [], [], [], [], [], []
    original_prepare, original_chunks = module.prepare_rollout, rollout_sources._decoded_chunks
    original_temp = rollout_sources.tempfile.NamedTemporaryFile

    class CompressedReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            assert 0 <= size <= 256
            data = self.stream.read(size)
            input_reads.append(len(data))
            return data

    def decode(stream, stop):
        decodes.append(stream.name)
        entered.set()
        assert release.wait(5)
        for chunk in original_chunks(CompressedReader(stream), stop):
            chunks.append(len(chunk))
            yield chunk

    def prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        prepared.append(result)
        return result

    original_slice = watcher.get_compute().sync_session_slice

    def slice_session(*args, **kwargs):
        result = original_slice(*args, **kwargs)
        if args[0] == other_id:
            slices.append(result.lines_processed)
        return result

    monkeypatch.setattr(watcher.get_compute(), 'sync_session_slice', slice_session)

    def temporary(*args, **kwargs):
        result = original_temp(*args, **kwargs)
        copies.append(Path(result.name))
        return result

    async def run():
        db_writer.start_db_writer()
        try:
            await watcher.process_path(plain)
            await settle(watcher)
            monkeypatch.setattr(rollout_sources, '_decoded_chunks', decode)
            monkeypatch.setattr(module, 'prepare_rollout', prepare)
            monkeypatch.setattr(rollout_sources.tempfile, 'NamedTemporaryFile', temporary)
            watcher._enqueue(compressed, Change.added)
            assert await asyncio.to_thread(entered.wait, 2)
            with plain.open('ab') as stream:
                stream.write(records(2)[len(records()):])
            await asyncio.wait_for(watcher.process_path(plain), 2)
            row = await Session.objects.aget(pk=SID)
            assert row.last_line == 3 and row.last_offset == len(records(2))
            assert not release.is_set() and not prepared
            release.set()
            await settle(watcher)
            other_row = await Session.objects.aget(pk=other_id)
            assert other_row.last_line == 1101
            assert len(prepared) == 1
            assert copies == [prepared[0].read_path]
            assert len(decodes) == 1
            assert sum(input_reads) == len(original_bytes)
            assert len(input_reads) == (len(original_bytes) + 255) // 256 + 1
            assert len(slices) >= 3 and max(slices) <= 500
            assert chunks and max(chunks) <= 3 * 1024 * 1024
            assert not prepared[0].read_path.exists()
            assert compressed.read_bytes() == original_bytes and not other.exists()
            print(f'COMPRESSED_DRAIN decodes={len(decodes)} copies={len(copies)} '
                  f'input_reads={len(input_reads)} input_bytes={sum(input_reads)} '
                  f'output_bytes={sum(chunks)} max_output={max(chunks)} slices={slices!r}')
        finally:
            release.set()
            watcher.stop_watcher()
            await watcher._drain_changes()
            await db_writer.stop_db_writer()

    asyncio.run(run())
