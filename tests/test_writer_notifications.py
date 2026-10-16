"""Writer wakeups cover producer APIs, idle races, tasks and lifetime boundaries."""

import asyncio
import importlib
import queue
import threading
from types import SimpleNamespace

import pytest

from twicc.core.enums import Provider
from twicc.providers import db_writer


def signals_api():
    assert importlib.util.find_spec('twicc.providers.writer_signals') is not None
    return importlib.import_module('twicc.providers.writer_signals')


@pytest.mark.parametrize('method', ['put', 'put_nowait'])
def test_thread_queue_notifies_once_after_visibility_and_mutex_release(method):
    mod = signals_api()
    observations = []
    def inserted():
        acquired = ingress.mutex.acquire(blocking=False)
        assert acquired
        ingress.mutex.release()
        observations.append(ingress.get_nowait())
    ingress = mod.NotifyingQueue(1, on_put=inserted)
    getattr(ingress, method)('accepted')
    assert observations == ['accepted']
    ingress.task_done()
    ingress.join()



def test_full_thread_queue_preserves_backpressure_without_false_notification():
    mod = signals_api()
    calls = []
    ingress = mod.NotifyingQueue(1, on_put=lambda: calls.append('accepted'))
    ingress.put_nowait('first')
    with pytest.raises(queue.Full):
        ingress.put_nowait('rejected')
    with pytest.raises(queue.Full):
        ingress.put('rejected', timeout=0)
    assert calls == ['accepted']
    assert ingress.get_nowait() == 'first'


def test_async_queue_put_notifies_once():
    async def run():
        mod = signals_api()
        calls = []
        ingress = mod.NotifyingAsyncQueue(on_put=lambda: calls.append(ingress.qsize()))
        await ingress.put('first')
        ingress.put_nowait('second')
        assert calls == [1, 2]
    asyncio.run(run())


def test_old_lifetime_callback_cannot_wake_restarted_writer():
    async def run():
        mod = signals_api()
        old = mod.WriterSignals(asyncio.get_running_loop())
        new = mod.WriterSignals(asyncio.get_running_loop())
        thread = threading.Thread(target=old.notify_sources)
        thread.start()
        thread.join()
        old.close()
        await asyncio.sleep(0)
        old.notify_dispatch()
        await asyncio.sleep(0)
        assert not old.dispatch.is_set() and not old.sources.is_set()
        assert not new.dispatch.is_set() and not new.sources.is_set()
        await asyncio.to_thread(new.notify_sources)
        await asyncio.wait_for(new.dispatch.wait(), 1)
        assert new.sources.is_set()
        new.close()
    asyncio.run(run())


async def start_dispatch(monkeypatch):
    # Keep real queues, coordinator and drain. Omit database preload/statistics.
    monkeypatch.setattr(db_writer, '_db_writer_loop', db_writer._db_writer_loop_body)
    db_writer.start_db_writer()
    return db_writer._writer_signals


@pytest.mark.parametrize('kind', ['thread', 'compute', 'foreground', 'initialization'])
@pytest.mark.parametrize('method', ['put', 'put_nowait'])
def test_enqueue_during_idle_check_is_not_lost(monkeypatch, kind, method):
    async def run():
        idle, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = db_writer._drain_one
        first = True
        async def drain():
            nonlocal first
            result = await original()
            if first:
                first = False
                idle.set()
                await release.wait()
            return result
        monkeypatch.setattr(db_writer, '_drain_one', drain)
        async def applied(*args):
            completed.set()
        monkeypatch.setattr(db_writer, '_process_thread_message', applied)
        monkeypatch.setattr(db_writer, '_process_compute_message', applied)
        monkeypatch.setattr(db_writer, '_dispatch_queued_job', applied)
        signals = await start_dispatch(monkeypatch)
        try:
            assert signals is not None
            await asyncio.wait_for(idle.wait(), 1)
            if kind in ('thread', 'compute'):
                ingress = db_writer.get_thread_queue() if kind == 'thread' else db_writer.get_subprocess_queue()
                item = SimpleNamespace(provider=Provider.CODEX) if kind == 'thread' else {'type': 'done', 'provider': 'codex'}
                await asyncio.to_thread(getattr(ingress, method), item)
            else:
                item = object() if kind == 'foreground' else db_writer.InitializeComputeStorageJob(Provider.CODEX, 'id', None)
                producer = getattr(db_writer._async_queue, method)
                if method == 'put':
                    await producer(item)
                else:
                    producer(item)
            release.set()
            await asyncio.wait_for(completed.wait(), 1)
        finally:
            release.set()
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_stop_with_blocked_worker_waits_without_spinning(monkeypatch):
    async def run():
        signals = await start_dispatch(monkeypatch)
        admission = await db_writer.get_write_coordinator().acquire(
            db_writer.WriteResources.ADMISSION, db_writer.WritePriority.FOREGROUND,
        )
        drained = asyncio.Event()
        drain_waiting = asyncio.Event()
        class DrainEvent(asyncio.Event):
            async def wait(self):
                if db_writer._db_writer_stop_event.is_set():
                    drain_waiting.set()
                return await super().wait()
        signals.dispatch = DrainEvent()
        calls = 0
        original = db_writer._drain_one
        async def drain():
            nonlocal calls
            calls += 1
            result = await original()
            drained.set()
            return result
        monkeypatch.setattr(db_writer, '_drain_one', drain)
        async def applied(*args):
            pass
        monkeypatch.setattr(db_writer, '_process_thread_message', applied)
        db_writer.get_thread_queue().put_nowait(SimpleNamespace(provider=Provider.CODEX))
        stopping = asyncio.create_task(db_writer.stop_db_writer())
        try:
            await asyncio.wait_for(drained.wait(), 1)
            await asyncio.wait_for(drain_waiting.wait(), 1)
            for _ in range(30):
                await asyncio.sleep(0)
            before = calls
            for _ in range(30):
                await asyncio.sleep(0)
            assert calls == before
            assert not stopping.done()
            assert signals is not None
            admission.release()
            await asyncio.wait_for(stopping, 1)
        finally:
            admission.release()
            await stopping
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['metadata', 'final'])
@pytest.mark.parametrize('failure', [None, 'preparation', 'notification'])
@pytest.mark.parametrize('stop_during_preparation', [False, True])
def test_preparation_and_terminal_completion_wake_dispatch(monkeypatch, phase, failure, stop_during_preparation):
    from twicc.core.content_storage import extraction
    from twicc.providers.compute_base import BaseSessionCompute, ComputeApplyResult

    async def run():
        prepared, release = threading.Event(), threading.Event()
        notification, finish_notification = asyncio.Event(), asyncio.Event()
        foreground = asyncio.Event()
        operation = SimpleNamespace(phase=phase, history_facts_stage=None if phase == 'final' else object(),
                                    observation=SimpleNamespace(root_id=None, root_incarnation=None), preparation_ms=0)
        monkeypatch.setattr(extraction, 'begin_compute_application', lambda message: operation)
        def prepare(op):
            prepared.set()
            assert release.wait(2)
            if failure == 'preparation':
                raise ValueError('preparation failed')
            if phase == 'final':
                op.history_facts_stage = object()
                return None
            return SimpleNamespace(phase='metadata')
        monkeypatch.setattr(extraction, 'prepare_compute_batch', prepare)
        monkeypatch.setattr(extraction, 'prepare_compute_history_stage', prepare)
        monkeypatch.setattr(extraction, 'publish_compute_batch', lambda op, batch, active: setattr(op, 'phase', 'final'))
        monkeypatch.setattr(extraction, 'final_compute_message', lambda op, active: {})
        monkeypatch.setattr(extraction, 'close_compute_application', lambda op: None)
        monkeypatch.setattr(BaseSessionCompute, '_apply_session_complete_final', lambda msg: ComputeApplyResult('applied'))
        monkeypatch.setattr(db_writer, '_active_compute_ids', lambda: frozenset())
        async def notify(*args):
            notification.set()
            await finish_notification.wait()
            if failure == 'notification':
                raise ValueError('notification failed')
        monkeypatch.setattr(db_writer, '_complete_compute_result', notify)
        async def dispatched(job):
            foreground.set()
        monkeypatch.setattr(db_writer, '_dispatch_queued_job', dispatched)
        signals = await start_dispatch(monkeypatch)
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        stopping = None
        ingress = db_writer.get_subprocess_queue()
        try:
            ingress.put_nowait({'type': 'session_complete', 'provider': 'codex',
                                                        'run_id': run_id, 'session_id': 'prepared'})
            assert await asyncio.to_thread(prepared.wait, 1)
            db_writer._async_queue.put_nowait(object())
            await asyncio.wait_for(foreground.wait(), 1)
            assert db_writer._pending_compute.preparation is not None
            assert db_writer.has_pending_source_applications()
            if stop_during_preparation:
                stopping = asyncio.create_task(db_writer.stop_db_writer())
                for _ in range(30):
                    await asyncio.sleep(0)
                assert not stopping.done()
            signals.sources.clear()
            release.set()
            async with asyncio.timeout(2):
                while db_writer._pending_compute is not None and not db_writer._pending_compute.terminal:
                    await asyncio.sleep(0)
            assert signals.sources.is_set()
            assert not ingress.pending_sources
            assert not db_writer.has_pending_source_applications()
            assert db_writer.source_application_changes() is signals.sources
            if failure == 'preparation':
                async with asyncio.timeout(1):
                    while db_writer._pending_compute is not None:
                        await asyncio.sleep(0)
                assert db_writer._compute_states[run_id].failed_count == 1
            else:
                await asyncio.wait_for(notification.wait(), 1)
                assert db_writer._pending_compute is not None
                if stopping is not None:
                    assert not stopping.done()
                finish_notification.set()
                async with asyncio.timeout(1):
                    while db_writer._pending_compute is not None:
                        await asyncio.sleep(0)
        finally:
            release.set()
            finish_notification.set()
            if stopping is not None:
                await stopping
            else:
                await db_writer.stop_db_writer()
            completion.cancel()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(run())


def test_idle_dispatch_waits_for_notification_without_poll_timeout(monkeypatch):
    async def run():
        signals = await start_dispatch(monkeypatch)
        waiting = asyncio.Event()
        class ObservedEvent(asyncio.Event):
            async def wait(self):
                waiting.set()
                return await super().wait()
        signals.dispatch = ObservedEvent()
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            assert not db_writer._db_writer_task.done()
            waiting.clear()
            for _ in range(30):
                await asyncio.sleep(0)
            assert not waiting.is_set()
            signals.notify_dispatch()
            await asyncio.wait_for(waiting.wait(), 1)
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(run())


def test_old_stream_failure_cannot_enqueue_into_restarted_writer(monkeypatch):
    async def run():
        await start_dispatch(monkeypatch)
        run_id, completion = db_writer.arm_compute_completion(Provider.CODEX, set(), 0)
        stream = db_writer.create_compute_result_stream(Provider.CODEX, run_id)
        await db_writer.stop_db_writer()
        await start_dispatch(monkeypatch)
        try:
            stream.failed('late transport failure')
            for _ in range(10):
                await asyncio.sleep(0)
            assert not completion.done()
            assert run_id in db_writer._compute_states
            assert not db_writer._compute_transport_failures
        finally:
            await db_writer.stop_db_writer()
            completion.cancel()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(run())


def test_foreground_arrival_wakes_dispatch_while_background_admission_is_refused(monkeypatch):
    async def run():
        signals = await start_dispatch(monkeypatch)
        foreground, background, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class ObservedEvent(asyncio.Event):
            async def wait(self):
                waiting.set()
                return await super().wait()
        signals.dispatch = ObservedEvent()
        async def dispatch(job):
            foreground.set()
        async def ingest(payload):
            background.set()
        monkeypatch.setattr(db_writer, '_dispatch_queued_job', dispatch)
        monkeypatch.setattr(db_writer, '_process_thread_message', ingest)
        admission = await db_writer.get_write_coordinator().acquire(
            db_writer.WriteResources.ADMISSION, db_writer.WritePriority.FOREGROUND,
        )
        try:
            db_writer.get_thread_queue().put_nowait(SimpleNamespace(provider=Provider.CODEX))
            await asyncio.wait_for(waiting.wait(), 1)
            assert not background.is_set()
            db_writer._async_queue.put_nowait(object())
            await asyncio.wait_for(foreground.wait(), 1)
            assert not background.is_set()
            admission.release()
            await asyncio.wait_for(background.wait(), 1)
        finally:
            admission.release()
            await db_writer.stop_db_writer()
    asyncio.run(run())


@pytest.mark.django_db(transaction=True)
def test_cancelled_cleanup_retains_source_gate_until_worker_and_temp_cleanup_finish(
    monkeypatch, compute_executor_started,
):
    from twicc.core.content_storage import extraction
    from twicc.providers.compute_base import ComputeApplyResult
    from twicc.providers.compute_executor import (
        start_compute_application_executor, stop_compute_application_executor,
    )

    async def run():
        start_compute_application_executor()
        signals = signals_api().WriterSignals(asyncio.get_running_loop())
        monkeypatch.setattr(db_writer, '_writer_signals', signals)
        ingress = db_writer.ComputeIngressQueue(on_put=signals.notify_sources)
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
        message = {'type': 'session_complete', 'run_id': 50, 'session_id': 'cancelled'}
        ingress.put_nowait(message)
        ingress.get_nowait()
        worker_started, worker_release = threading.Event(), threading.Event()
        cleanup_started, cleanup_release = threading.Event(), threading.Event()

        def prepare():
            worker_started.set()
            assert worker_release.wait(2)

        def close(operation):
            cleanup_started.set()
            assert cleanup_release.wait(2)

        monkeypatch.setattr(extraction, 'close_compute_application', close)
        pending = db_writer.PendingComputeApplication(message, 50,
            operation=SimpleNamespace(preparation_ms=0), result=ComputeApplyResult('failed'))
        monkeypatch.setattr(db_writer, '_pending_compute', pending)
        pending.preparation = asyncio.create_task(db_writer.run_compute_application_sync(prepare))
        finishing = None
        try:
            assert await asyncio.to_thread(worker_started.wait, 1)
            finishing = asyncio.create_task(db_writer._finish_pending_compute(pending, notify=False))
            await asyncio.sleep(0)
            finishing.cancel()
            await asyncio.sleep(0)
            signals.sources.clear()
            assert not finishing.done() and db_writer.has_pending_source_applications()
            assert not pending.terminal and not signals.sources.is_set()
            worker_release.set()
            assert await asyncio.to_thread(cleanup_started.wait, 1)
            assert db_writer.has_pending_source_applications() and not pending.terminal
            assert not signals.sources.is_set()
            cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await finishing
            assert pending.terminal and not db_writer.has_pending_source_applications()
            assert not ingress.pending_sources and signals.sources.is_set()
            # A second terminal call must not release accounting or notify twice.
            signals.sources.clear()
            await db_writer._finish_pending_compute(pending, notify=False)
            assert not signals.sources.is_set()
            await pending.notifications
            signals.dispatch.clear()
            assert db_writer._collect_pending_notifications(pending)
            assert db_writer._pending_compute is None and signals.dispatch.is_set()
        finally:
            worker_release.set()
            cleanup_release.set()
            if finishing is not None:
                await asyncio.gather(finishing, return_exceptions=True)
            await pending.preparation
            if pending.notifications is not None:
                await pending.notifications
            signals.close()
            await stop_compute_application_executor()

    asyncio.run(run())


def test_accepted_sources_count_duplicate_keys_until_each_release(monkeypatch):
    async def run():
        signals = signals_api().WriterSignals(asyncio.get_running_loop())
        monkeypatch.setattr(db_writer, '_writer_signals', signals)
        monkeypatch.setattr(db_writer, '_pending_compute', None)
        ingress = db_writer.ComputeIngressQueue(on_put=signals.notify_sources)
        monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
        message = {'type': 'session_complete', 'run_id': 51, 'session_id': 'same-source'}
        ingress.put_nowait(message)
        ingress.put_nowait(message)
        ingress.get_nowait()
        ingress.get_nowait()
        signals.sources.clear()
        db_writer._release_source_application(message)
        assert db_writer.has_pending_source_applications() and signals.sources.is_set()
        signals.sources.clear()
        db_writer._release_source_application(message)
        assert not db_writer.has_pending_source_applications() and signals.sources.is_set()
        signals.sources.clear()
        db_writer._release_source_application(message)
        assert not db_writer.has_pending_source_applications() and not signals.sources.is_set()
        signals.close()
    asyncio.run(run())
