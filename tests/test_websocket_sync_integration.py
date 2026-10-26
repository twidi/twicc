"""Combined transport, writer, history lookup, and fair live replay contracts."""

import asyncio
import threading
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import orjson
import pytest
from watchfiles import Change
from django.contrib.sessions.backends.db import SessionStore
from asgiref.sync import sync_to_async
from django.db import connection, connections

from tests.test_history_fact_extraction import call, output
from tests.test_websocket_transport import DISCONNECT, PING, PONG, TrackingLayer, Wire
from twicc.auth.session_auth import bind_session
from twicc.core.models import Project, Session
from twicc.providers import db_writer
from twicc.providers.compute_executor import run_compute_sync
from twicc.providers.live_sync import LiveSyncLimits
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile


@pytest.mark.django_db(transaction=True)
def test_authenticated_initialization_and_pong_survive_heavy_writer(monkeypatch, settings):
    """Routing heavy work back to the shared executor deadlocks initialization."""
    from twicc.asgi import WSConsumer

    settings.TWICC_PASSWORD_HASH = 'disposable-integration-hash'
    auth = SessionStore()
    bind_session(auth, settings.TWICC_PASSWORD_HASH)
    auth.save()
    session = Session.objects.create(id='viewed', provider='codex', project=Project.objects.create(id='ws-integration'))
    monkeypatch.setattr('twicc.asgi.is_provider_enabled', lambda provider: False)

    async def run():
        layer, wire = TrackingLayer(), Wire()
        monkeypatch.setattr('channels.consumer.get_channel_layer', lambda alias: layer)
        entered, command = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def heavy():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10), 'test did not release heavy work'

        consumer = WSConsumer()
        viewed = consumer._handle_session_viewed

        async def record_viewed(content):
            command.set()
            await viewed(content)

        consumer._handle_session_viewed = record_viewed
        db_writer.start_db_writer()
        job = asyncio.create_task(db_writer.run_under_db_write_lock(lambda: run_compute_sync(heavy)))
        connection = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            scope = {'type': 'websocket', 'path': '/ws/', 'headers': [],
                     'query_string': b'subscribe=hidden_sessions', 'client': ('127.0.0.1', 1234),
                     'session': SessionStore(session_key=auth.session_key)}
            connection = asyncio.create_task(consumer(scope, wire.receive, wire.send))
            wire.put({'type': 'websocket.connect'})
            assert (await wire.output())['type'] == 'websocket.accept'
            assert orjson.loads((await wire.output())['text']) == {'type': 'hidden_sessions', 'session_ids': []}
            wire.put({'type': 'websocket.receive', 'text': orjson.dumps({
                'type': 'session_viewed', 'session_id': session.id}).decode()})
            await asyncio.wait_for(command.wait(), 2)
            wire.put(PING)
            assert await wire.output() == PONG
            assert not job.done()
            assert await Session.objects.filter(id=session.id, last_viewed_at=None).aexists()
            release.set()
            await job
            wire.put(DISCONNECT)
            await asyncio.wait_for(connection, 2)
            assert await Session.objects.filter(id=session.id, last_viewed_at__isnull=False).aexists()
        finally:
            release.set()
            try:
                await job
                if connection is not None:
                    connection.cancel()
                    with suppress(asyncio.CancelledError):
                        await connection
            finally:
                await db_writer.stop_db_writer()
        assert not layer.groups.get('updates') and not layer.receivers and wire.receiving == 0

    asyncio.run(run())


# ``records_for`` dates its lines 2026-09-29: tests replaying them freeze the clock one day later so the
# content storage sees hot sessions.
FROZEN_AFTER_RECORDS = datetime(2026, 9, 30, 10, tzinfo=UTC)


def records_for(provider):
    if provider == 'codex':
        return [call('reuse'), output('reuse', 'Process running with session ID 42'),
                *[{} for _ in range(499)], call('reuse'),
                output('reuse', 'Process running with session ID 42'),
                call('poll', 'write_stdin', arguments='{"session_id":42}'), output('poll', 'done')]
    def assistant(id, blocks):
        return {'type': 'assistant', 'timestamp': '2026-09-29T10:00:00Z', 'message': {
            'id': id, 'role': 'assistant', 'model': 'claude-sonnet-4-20250514',
            'usage': {'input_tokens': 100, 'output_tokens': 10}, 'content': blocks}}
    tool = {'type': 'tool_use', 'id': 'reuse', 'name': 'Bash', 'input': {'command': 'true'}}
    result = {'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'reuse', 'content': 'done'}]}}
    # The filler only pushes the reuse past the 500-line slice. A SYSTEM line
    # (DEBUG_ONLY) keeps that: a bare ``{}`` is COLLAPSIBLE and joins the open
    # tool group, so every line rewrites the whole group (quadratic, ~2 s),
    # and group fields are not part of the compared snapshot.
    filler = {'type': 'queue-operation'}
    return [assistant('message-1', [tool]), result, *[filler for _ in range(499)],
            assistant('message-2', [tool]), result,
            assistant('message-2', [{'type': 'text', 'text': 'stream duplicate'}])]


@pytest.fixture
def replay_wal_database(tmp_path, monkeypatch):
    """Historical readers and foreground writes require the production WAL topology."""
    connection.ensure_connection()
    original = connection.connection
    path = tmp_path / 'replay.sqlite3'
    database = sqlite3.connect(path)
    try:
        original.backup(database)
        assert database.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    finally:
        database.close()
    # Worker wrappers inherit this settings dictionary. Keep pytest's original
    # in-memory connection alive so its teardown still owns the original database.
    monkeypatch.setitem(connection.settings_dict, 'NAME', str(path))
    monkeypatch.setitem(connections.databases['default'], 'NAME', str(path))

    def reset_reader():
        db = connections['default']
        db.close()
        db.settings_dict = connections.databases['default']

    asyncio.run(sync_to_async(reset_reader)())
    connection.connection = None
    try:
        connection.ensure_connection()
        yield path
    finally:
        asyncio.run(sync_to_async(reset_reader)())
        connection.close()
        connection.connection = original


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.parametrize('current', [False, True])
@pytest.mark.usefixtures("frozen_now")
@pytest.mark.frozen_now_at(FROZEN_AFTER_RECORDS)
def test_fair_queues_preserve_history_links_costs_and_checkpoints(tmp_path, monkeypatch, provider, current, replay_wal_database):
    """Removing requeue, prior-line resolution, or bounded dedup breaks parity."""
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.codex.compute import CodexSessionCompute
    from twicc.providers import sessions_watcher

    compute = ClaudeCodeSessionCompute() if provider == 'claude_code' else CodexSessionCompute()
    project = Project.objects.create(id='integration', directory=str(tmp_path))
    paths = {}
    for name, records in [('large', records_for(provider)), ('small', [{}]), ('reference', records_for(provider))]:
        paths[name] = tmp_path / f'{name}.jsonl'
        paths[name].write_bytes(b''.join(orjson.dumps(record) + b'\n' for record in records))
        Session.objects.create(id=name, project=project, provider=provider, file_path=name,
                               compute_version=compute.compute_version if current else 0)
    compute.sync_session_slice('reference', paths['reference'], limits=LiveSyncLimits(1000))
    watcher = BaseSessionsWatcher()
    watcher.get_compute = lambda: compute
    watcher.projects_dir = tmp_path

    async def parse(path):
        return ParsedSessionFile(project.id, path.stem, 'session', path.name, title='fixture')

    watcher.parse_session_file = parse
    monkeypatch.setattr(sessions_watcher, 'broadcast_message', AsyncMock())
    turns = []
    original = watcher._process_change

    async def record(*args):
        result = await original(*args)
        turns.append((args[1], result.disposition))
        return result

    watcher._process_change = record

    async def run():
        db_writer.start_db_writer()
        jobs = []
        try:
            # Capture targets sequentially. Filesystem thread completion order
            # must not decide which source enters the ready queue first.
            targets = {name: await watcher._observe_source(paths[name]) for name in ('large', 'small')}
            for name in ('large', 'small'):
                watcher._enqueue(paths[name], Change.modified)
            jobs = [asyncio.create_task(watcher._queue.wait_drained(paths[name], target=targets[name]))
                    for name in ('large', 'small')]
            await asyncio.wait_for(asyncio.gather(*jobs), 10)
        finally:
            watcher.stop_watcher()
            await watcher._drain_changes()
            await asyncio.gather(*jobs, return_exceptions=True)
            await db_writer.stop_db_writer()

    asyncio.run(run())
    assert all(disposition != 'failed' for _, disposition in turns), turns
    assert turns[0] == (str(paths['large']), 'ready')
    assert turns[1] == (str(paths['small']), 'drained')

    def snapshot(name):
        session = Session.objects.get(id=name)
        return (session.last_line, session.last_offset, session.self_cost, session.total_cost,
                list(session.items.order_by('line_num').values_list('line_num', 'message_id', 'cost', 'kind')),
                list(session.tool_result_links.order_by('tool_result_line_num').values_list(
                    'tool_use_line_num', 'tool_result_line_num', 'tool_use_id', 'tool_name')))

    assert snapshot('large') == snapshot('reference')
    assert snapshot('large')[1] == paths['large'].stat().st_size
    assert Session.objects.get(id='small').last_line == 1
    assert list(Session.objects.get(id='large').tool_result_links.order_by('tool_result_line_num')
                .values_list('tool_use_line_num', flat=True))[:2] == [1, 502]
    if not current:
        from tests.tree_aggregate_helpers import drain_aggregates
        from twicc.providers.compute_base import BaseSessionCompute
        assert snapshot('large')[2] is None
        for name in ('large', 'small', 'reference'):
            session = Session.objects.get(pk=name)
            result = BaseSessionCompute.apply_session_complete({
                'session_id': name, 'observed_last_offset': session.last_offset,
                'observed_incarnation': session.storage_incarnation,
                'observed_epoch': session.history_epoch, 'observed_upper_line': session.last_line,
                'history_facts': list(session.history_facts.values('line_num', 'kind', 'key', 'data')),
                'session_fields': {'compute_version': compute.compute_version},
            })
            assert result.outcome == 'applied'
        drain_aggregates()
        assert snapshot('large') == snapshot('reference')
    # Source counts and checkpoints are immediate; costs converge through the queue.
    from tests.statistics_helpers import drain_statistics
    drain_statistics()
    assert snapshot('large') == snapshot('reference')
    if provider == 'claude_code':
        assert snapshot('large')[2] > 0
    if provider == 'codex':
        assert Session.objects.get(id='large').history_facts.filter(
            kind='process_start', line_num=503).get().data['call_line'] == 502
        assert compute._lookup_exec_command_call_id('large', 506, 42, 'missing') == 'reuse'
        assert compute._lookup_tool_call('large', 502, 'reuse')[1] == 1
        assert compute._lookup_tool_call('large', 506, 'absent') is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
@pytest.mark.usefixtures("frozen_now")
@pytest.mark.frozen_now_at(FROZEN_AFTER_RECORDS)
def test_new_watcher_session_has_immediate_live_baseline(tmp_path, provider):
    """A newly watched session must not depend on a completed startup compute job."""
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.codex.compute import CodexSessionCompute
    compute = ClaudeCodeSessionCompute() if provider == 'claude_code' else CodexSessionCompute()
    project = Project.objects.create(id='new-live', directory=str(tmp_path))
    watcher = BaseSessionsWatcher()
    watcher.get_compute = lambda: compute
    session = watcher.create_session_sync(ParsedSessionFile(project.id, 'new-live', 'session', 'new-live.jsonl'), project)
    assert session.compute_version == compute.compute_version
    path = tmp_path / 'new-live.jsonl'
    user = ({'type': 'user', 'message': {'role': 'user', 'content': 'hello'}} if provider == 'claude_code' else
            {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
             'type': 'UserMessage', 'id': 'first', 'content': [{'type': 'text', 'text': 'hello'}]}}})
    records = [user, *records_for(provider)]
    path.write_bytes(b''.join(orjson.dumps(row) + b'\n' for row in records))
    compute.sync_session_slice(session.id, path, limits=LiveSyncLimits(1000))
    session.refresh_from_db()
    assert session.user_message_count == 1
    from tests.statistics_helpers import drain_statistics
    drain_statistics()
    session.refresh_from_db()
    if provider == 'claude_code':
        assert session.self_cost > 0
