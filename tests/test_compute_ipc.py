"""Killed compute producers must never block the application's event loop."""

import asyncio
import multiprocessing
import os
import queue
import struct
import time

import orjson
import pytest

from twicc.core.enums import Provider
from twicc.providers import db_writer


def _partial_result(sender, ready):
    # Publish a complete frame header and only part of its promised body.
    os.write((sender.connection if hasattr(sender, 'connection') else sender._writer).fileno(), struct.pack('!i', 8 * 1024 * 1024) + b'{')
    ready.send_bytes(b'ready')
    time.sleep(60)


def _healthy_result(sender, run_id):
    sender.put(orjson.dumps({'type': 'done', 'provider': 'claude_code', 'run_id': run_id}))
    sender.close()


async def _partial_frame_scenario():
    db_writer.start_db_writer()
    spawn = multiprocessing.get_context('spawn')
    run_id, failed = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
    healthy_id, completed = db_writer.arm_compute_completion(Provider.CLAUDE_CODE, set(), 0)
    create_stream = getattr(db_writer, 'create_compute_result_stream', None)
    if create_stream is None:
        class LegacyStream:
            sender = db_writer.get_subprocess_queue()
            def start(self):
                pass
            async def close(self):
                pass
        stream = healthy = LegacyStream()
    else:
        stream = create_stream(Provider.CODEX, run_id)
        healthy = create_stream(Provider.CLAUDE_CODE, healthy_id)
    ready_reader, ready_writer = spawn.Pipe(duplex=False)
    process = spawn.Process(target=_partial_result, args=(stream.sender, ready_writer))
    peer = spawn.Process(target=_healthy_result, args=(healthy.sender, healthy_id))
    beats = []
    async def heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(.01)
    pulse = asyncio.create_task(heartbeat())
    try:
        process.start()
        stream.start()
        ready_writer.close()
        assert await asyncio.to_thread(ready_reader.poll, 5)
        ready_reader.recv_bytes()
        before = len(beats)
        await asyncio.sleep(.1)
        assert len(beats) >= before + 3
        peer.start()
        healthy.start()
        assert await asyncio.wait_for(asyncio.shield(completed), 5) == 0
        process.kill()
        await asyncio.to_thread(process.join, 2)
        with pytest.raises(RuntimeError, match='compute.*transport'):
            await asyncio.wait_for(asyncio.shield(failed), 5)
    finally:
        for worker in (process, peer):
            if worker.pid is not None:
                if worker.is_alive():
                    worker.kill()
                await asyncio.to_thread(worker.join, 2)
        await stream.close()
        await healthy.close()
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        ready_reader.close()
        await asyncio.wait_for(db_writer.stop_db_writer(), 5)
    # A new writer lifetime cannot inherit a dead stream or partial frame.
    db_writer.start_db_writer()
    await asyncio.wait_for(db_writer.stop_db_writer(), 5)


def _guarded_partial_frame_scenario(report, database_path):
    # spawn avoids inheriting executor locks from earlier tests. The copied
    # pytest database is the only database this harness or its threads use.
    os.setsid()
    try:
        import django
        from django.conf import settings
        from django.db import connections

        os.environ['DJANGO_SETTINGS_MODULE'] = 'twicc.settings_test'
        django.setup()
        connections.close_all()
        settings.DATABASES['default']['NAME'] = database_path
        connections.databases['default']['NAME'] = database_path
        connections['default'].settings_dict['NAME'] = database_path
        asyncio.run(_partial_frame_scenario())
        report.send(('ok', None))
    except BaseException as error:
        report.send(('error', repr(error)))
    finally:
        report.close()


@pytest.mark.django_db(transaction=True)
def test_partial_frame_kill_preserves_heartbeat_and_other_provider(tmp_path):
    import sqlite3
    from contextlib import closing
    from django.conf import settings
    from django.db import connection

    assert settings.SETTINGS_MODULE == 'twicc.settings_test'
    database_path = tmp_path / 'compute-ipc.sqlite3'
    connection.ensure_connection()
    with closing(sqlite3.connect(database_path)) as destination:
        connection.connection.backup(destination)
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    harness = context.Process(target=_guarded_partial_frame_scenario, args=(writer, str(database_path)))
    harness.start()
    writer.close()
    try:
        assert reader.poll(15), 'compute IPC blocks the event loop or fails to settle the interrupted run'
        status, error = reader.recv()
        assert status == 'ok', error
    finally:
        if harness.is_alive():
            import signal
            try:
                os.killpg(harness.pid, signal.SIGKILL)
            except ProcessLookupError:
                # Startup can fail before setsid creates the isolated group.
                harness.kill()
        harness.join(3)
        harness.close()
        reader.close()


@pytest.mark.django_db(transaction=True)
def test_spawn_failure_closes_transport_and_settles_completion(monkeypatch):
    from twicc.providers import background_compute_task

    class FailedProcess:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise OSError('spawn unavailable')

    monkeypatch.setattr(background_compute_task._mp_ctx, 'Process', FailedProcess)

    async def scenario():
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        ctx = background_compute_task.ComputeContext(Provider.CODEX, 1, 'unused:factory', run_id=run_id)
        try:
            with pytest.raises(OSError, match='spawn unavailable'):
                background_compute_task.start_compute_process(ctx)
            assert ctx.result_stream.reader.closed
            assert ctx.result_stream.sender.connection.closed
            with pytest.raises(RuntimeError, match='compute.*transport'):
                await asyncio.wait_for(asyncio.shield(completion), .5)
        finally:
            await background_compute_task.stop_compute_worker(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def _flood_results(sender, ready, status):
    from twicc.providers.compute_ipc import ComputeStatusSender
    endpoint = ComputeStatusSender(sender) if status else sender
    for index in range(10):
        ready.send_bytes(str(index).encode())
        message = {'type': 'computed' if status else 'error', 'provider': 'codex',
                   'run_id': 7, 'session_id': str(index), 'payload': 'x' * (8 * 1024 * 1024)}
        endpoint.put(message if status else orjson.dumps(message))
    sender.close()


@pytest.mark.parametrize('status', [False, True])
def test_saturated_ingress_stops_receiver_and_backpressures_producer(status):
    from twicc.providers.compute_ipc import ComputeResultStream

    async def scenario():
        spawn = multiprocessing.get_context('spawn')
        ingress = queue.Queue(maxsize=1)
        statuses = queue.Queue(maxsize=1)
        failures = []
        stream = ComputeResultStream(ingress, statuses, 'codex', 7, failures.append)
        reader, writer = spawn.Pipe(duplex=False)
        process = spawn.Process(target=_flood_results, args=(stream.sender, writer, status))
        try:
            process.start()
            stream.start()
            writer.close()
            for expected in (b'0', b'1', b'2'):
                assert await asyncio.to_thread(reader.poll, 5)
                assert reader.recv_bytes() == expected
            # The third large send cannot finish while receiver admission is full.
            assert not await asyncio.to_thread(reader.poll, .2)
            assert process.is_alive()
            target = statuses if status else ingress
            assert target.get_nowait()['session_id'] == '0'
            # Consumer progress admits the second message and unblocks send 3.
            assert await asyncio.to_thread(reader.poll, 5)
            assert reader.recv_bytes() == b'3'
            stream.stop_admission()
            process.kill()
            await asyncio.to_thread(process.join, 2)
            await asyncio.wait_for(stream.close(), 2)
            assert not stream.thread.is_alive()
            assert failures == []
        finally:
            if process.is_alive():
                process.kill()
            await asyncio.to_thread(process.join, 2)
            await stream.close()
            reader.close()
    asyncio.run(scenario())


class _NoopCompute:
    provider = Provider.CODEX
    def enable_batch_queue_cache(self):
        pass
    def compute_session_metadata(self, session_id, result_queue, run_id):
        pass


@pytest.mark.django_db(transaction=True)
def test_real_workers_deliver_status_and_restart_without_receiver_leaks():
    import threading
    from twicc.providers.background_compute_task import ComputeContext, start_compute_process, stop_compute_worker

    async def scenario():
        db_writer.start_db_writer()
        ctx = ComputeContext(Provider.CODEX, 1, f'{_NoopCompute.__module__}:_NoopCompute', status_queue=queue.Queue())
        try:
            for _ in range(3):
                run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
                ctx.run_id = run_id
                start_compute_process(ctx)
                ctx.command_queue.put({'session_id': 'healthy'})
                ctx.command_queue.put(None)
                assert await asyncio.wait_for(asyncio.shield(completion), 8) == 0
                assert ctx.status_queue.get_nowait() == {'type': 'computed', 'session_id': 'healthy', '_run_id': run_id}
                await stop_compute_worker(ctx)
                assert not any(thread.name == f'compute-receiver-{run_id}' for thread in threading.enumerate())
                assert run_id not in db_writer._compute_streams
        finally:
            await stop_compute_worker(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_writer_shutdown_cleans_executors_when_a_live_receiver_cannot_join():
    from twicc.providers import compute_executor

    async def scenario():
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        stream = db_writer.create_compute_result_stream(Provider.CODEX, run_id)
        spawn = multiprocessing.get_context('spawn')
        reader, writer = spawn.Pipe(duplex=False)
        process = spawn.Process(target=_partial_result, args=(stream.sender, writer))
        try:
            process.start()
            stream.start()
            writer.close()
            assert await asyncio.to_thread(reader.poll, 5)
            reader.recv_bytes()
            await asyncio.sleep(.1)
            with pytest.raises(ExceptionGroup, match='compute transport shutdown'):
                await asyncio.wait_for(db_writer.stop_db_writer(), 3)
            assert db_writer._db_writer_task is None
            assert compute_executor._executor is None
            assert compute_executor._application_executor is None
            assert run_id in db_writer._compute_streams
        finally:
            if process.is_alive():
                process.kill()
            await asyncio.to_thread(process.join, 2)
            await db_writer.close_compute_result_stream(run_id)
            completion.cancel()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
            await db_writer.stop_db_writer()
            reader.close()
    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_receiver_start_failure_preserves_worker_for_shutdown(monkeypatch):
    from twicc.providers.background_compute_task import ComputeContext, start_compute_process, stop_compute_worker
    from twicc.providers.compute_ipc import ComputeResultStream

    def fail_start(_stream):
        raise RuntimeError('receiver start failed')
    monkeypatch.setattr(ComputeResultStream, 'start', fail_start)

    async def scenario():
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        ctx = ComputeContext(Provider.CODEX, 1, f'{_NoopCompute.__module__}:_NoopCompute', run_id=run_id)
        process = None
        try:
            with pytest.raises(RuntimeError, match='receiver start failed'):
                start_compute_process(ctx)
            process = ctx.process
            assert process is not None, 'successful spawn loses its process before shutdown'
            assert process.pid is not None
            await asyncio.wait_for(stop_compute_worker(ctx), 5)
            assert process._closed
            with pytest.raises(RuntimeError, match='compute.*transport'):
                await asyncio.wait_for(asyncio.shield(completion), 1)
        finally:
            if process is not None and not process._closed and process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
            await stop_compute_worker(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


class _SlowCompute(_NoopCompute):
    def compute_session_metadata(self, session_id, result_queue, run_id):
        result_queue.put(orjson.dumps({'type': 'error', 'provider': 'codex', 'run_id': run_id,
                                      'session_id': session_id, 'error': 'worker entered compute'}))
        time.sleep(60)


@pytest.mark.django_db(transaction=True)
def test_killed_worker_releases_parent_command_feeder_with_large_backlog():
    from twicc.providers.background_compute_task import ComputeContext, start_compute_process, stop_compute_worker

    async def scenario():
        db_writer.start_db_writer()
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        ctx = ComputeContext(Provider.CODEX, 1, f'{_SlowCompute.__module__}:_SlowCompute', run_id=run_id)
        try:
            start_compute_process(ctx)
            ctx.command_queue.put({'session_id': 'first'})
            async with asyncio.timeout(5):
                while db_writer._compute_states[run_id].failed_count == 0:
                    await asyncio.sleep(.01)
            payload = 'x' * (1024 * 1024)
            for _ in range(20):
                ctx.command_queue.put({'session_id': payload})
            feeder = ctx.command_queue._thread
            ctx.process.kill()
            await asyncio.to_thread(ctx.process.join, 2)
            with pytest.raises(RuntimeError, match='compute.*transport'):
                await asyncio.wait_for(asyncio.shield(completion), 3)
            await asyncio.wait_for(stop_compute_worker(ctx), 5)
            await asyncio.to_thread(feeder.join, 2)
            assert not feeder.is_alive()
        finally:
            await stop_compute_worker(ctx)
            await db_writer.stop_db_writer()
    asyncio.run(scenario())


def test_receiver_thread_enqueue_wakes_writer_after_visible_accounting():
    from twicc.providers.compute_ipc import ComputeResultStream
    from twicc.providers.writer_signals import WriterSignals

    async def scenario():
        signals = WriterSignals(asyncio.get_running_loop())
        ingress = db_writer.ComputeIngressQueue(2, on_put=signals.notify_sources)
        failures = []
        stream = ComputeResultStream(ingress, None, 'codex', 17, failures.append)
        try:
            # A small complete frame fits the pipe before its reader starts.
            stream.sender.put(orjson.dumps({'type': 'session_complete', 'provider': 'codex',
                                            'run_id': 17, 'session_id': 'received'}))
            stream.sender.put(orjson.dumps({'type': 'done', 'provider': 'codex', 'run_id': 17}))
            stream.start()
            await asyncio.wait_for(signals.sources.wait(), 1)
            assert signals.dispatch.is_set()
            assert ingress.get_nowait()['session_id'] == 'received'
            with ingress.mutex:
                assert ingress.pending_sources == {(17, 'received'): 1}
            assert not failures
        finally:
            await stream.close()
            signals.close()
    asyncio.run(scenario())
