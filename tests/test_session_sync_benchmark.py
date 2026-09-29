"""The benchmark cannot reuse instance storage or hide quadratic work."""

import pytest

from scripts.benchmark_session_sync import prepare_directory, scaling_report


def test_benchmark_refuses_normal_inherited_and_nonempty_directories(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    with pytest.raises(ValueError, match='normal'):
        prepare_directory(tmp_path / '.twicc')
    inherited = tmp_path / 'instance'
    monkeypatch.setenv('TWICC_DATA_DIR', str(inherited))
    with pytest.raises(ValueError, match='inherited'):
        prepare_directory(inherited)
    database = tmp_path / 'reused' / 'db' / 'data.sqlite'
    database.parent.mkdir(parents=True)
    database.touch()
    with pytest.raises(ValueError, match='preexisting'):
        prepare_directory(database.parent.parent)
    nonempty = tmp_path / 'nonempty'
    nonempty.mkdir()
    (nonempty / '.env').touch()
    with pytest.raises(ValueError, match='empty'):
        prepare_directory(nonempty)
    empty = tmp_path / 'new'
    assert prepare_directory(empty) == empty
    assert list(empty.iterdir()) == []


def test_vm_step_scaling_rejects_prefix_scans():
    rows = [{'session': 'large', 'phase': 'replay', 'lines': 125, 'vm_steps': 1000 * n}
            for n in range(1, 21)]
    with pytest.raises(AssertionError):
        scaling_report(rows)
    for row in rows:
        row['vm_steps'] = 1000
    assert scaling_report(rows)['125']['ratio'] == 1


@pytest.mark.django_db(transaction=True)
def test_each_cold_lookup_opens_its_own_connection(tmp_path, monkeypatch):
    import sqlite3
    import orjson
    from django.db import connections
    from scripts.benchmark_session_sync import lookup_report, record
    from twicc.core.models import Project, Session, SessionHistoryFact, SessionItem
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    from twicc.providers.history_facts import HistoryFactContext

    session = Session.objects.create(id='large', provider='claude_code',
        project=Project.objects.create(id='benchmark'), compute_version=ClaudeCodeSessionCompute().compute_version)
    SessionItem.objects.create(session=session, line_num=1, content=orjson.dumps(record(0, 'fixture')).decode())
    SessionHistoryFact.objects.create(session=session, line_num=1, kind='tool_call', key='reused-tool',
                                     data={'name': 'Bash'})
    # Closing an in-memory Django test connection is intentionally a no-op.
    # Copy only this test database into a disposable file to test real closes.
    original = connections['default']
    database = tmp_path / 'lookups.sqlite'
    destination = sqlite3.connect(database)
    try:
        original.connection.backup(destination)
    finally:
        destination.close()
    isolated = original.copy(alias='default')
    isolated.settings_dict['NAME'] = str(database)
    connections['default'] = isolated
    observed = []
    lookup = HistoryFactContext.lookup_tool_call

    def observe(self, key, **kwargs):
        before = isolated.connection
        result = lookup(self, key, **kwargs)
        observed.append((key, before, isolated.connection))
        return result

    monkeypatch.setattr(HistoryFactContext, 'lookup_tool_call', observe)
    try:
        report = lookup_report(10)
        assert [row[1] for row in observed[:2]] == [None, None]
        assert observed[0][2] is not observed[1][2]
        assert all(row[1] is observed[1][2] for row in observed[2:8])
        assert observed[8][1] is None and observed[9][1] is None
        assert report['lookups']['current_cold_connection'][1]['owner_line'] is None
    finally:
        isolated.close()
        connections['default'] = original


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure, cancel_stop', [
    ('body', False), ('body', True), ('startup', False), ('cleanup', False),
])
def test_benchmark_exception_always_drains_writer_and_closes_server(
    tmp_path, monkeypatch, settings, failure, cancel_stop,
):
    import asyncio
    import socket
    from scripts import benchmark_session_sync as benchmark
    from twicc.providers import db_writer
    from twicc.providers import compute_executor

    settings.TWICC_PASSWORD_HASH = 'disposable-test-hash'
    sockets, servers, stopped = [], [], []
    stop_entered, stop_release = asyncio.Event(), asyncio.Event()
    real_socket = socket.socket
    real_start = db_writer.start_db_writer
    real_stop = db_writer.stop_db_writer

    class Server:
        def __init__(self, _config):
            self.started = True
            self.entered = asyncio.Event()
            self.exit = asyncio.Event()
            self.task = None
            servers.append(self)

        @property
        def should_exit(self):
            return self.exit.is_set()

        @should_exit.setter
        def should_exit(self, value):
            if value:
                self.exit.set()

        async def serve(self, *, sockets):
            self.task = asyncio.current_task()
            self.entered.set()
            await self.exit.wait()

    class FailingConnection:
        async def __aenter__(self):
            await servers[0].entered.wait()
            raise RuntimeError('injected body failure after writer startup')

        async def __aexit__(self, *args):
            pass

    def open_socket(*args, **kwargs):
        sock = real_socket(*args, **kwargs)
        sockets.append(sock)
        return sock

    def start():
        real_start()
        if failure == 'startup':
            raise RuntimeError('injected failure after writer startup')

    async def stop():
        await real_stop()
        if failure == 'cleanup':
            stop_entered.set()
            await stop_release.wait()
        stopped.append(True)
        if cancel_stop:
            raise asyncio.CancelledError('injected writer shutdown cancellation')

    monkeypatch.setattr('uvicorn.Server', Server)
    monkeypatch.setattr('websockets.asyncio.client.connect', lambda *args, **kwargs: FailingConnection())
    monkeypatch.setattr(db_writer, 'start_db_writer', start)
    monkeypatch.setattr(db_writer, 'stop_db_writer', stop)

    async def run():
        # Patch socket creation only after asyncio creates its own wakeup pair.
        monkeypatch.setattr(benchmark.socket, 'socket', open_socket)
        operation = asyncio.create_task(benchmark.exercise(tmp_path, 10, {}))
        try:
            if failure == 'cleanup':
                await asyncio.wait_for(stop_entered.wait(), 2)
                operation.cancel()
                await asyncio.sleep(0)
                operation.cancel()
                await asyncio.sleep(0)
                assert not operation.done(), 'caller cancellation interrupted cleanup'
                stop_release.set()
            expected = asyncio.CancelledError if cancel_stop or failure == 'cleanup' else RuntimeError
            with pytest.raises(expected):
                await operation
            assert stopped, 'writer cleanup skipped'
            assert servers[0].should_exit, 'server shutdown skipped'
            assert sockets[0].fileno() == -1, 'listening socket leaked'
            assert servers[0].task.done(), 'server task leaked'
            assert db_writer._db_writer_task is None
            assert compute_executor._executor is None
        finally:
            # Keep RED runs contained too. No actual ASGI server is launched.
            stop_release.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await real_stop()
            for server in servers:
                server.should_exit = True
                if server.task is not None:
                    await server.task
            for sock in sockets:
                sock.close()

    asyncio.run(run())
