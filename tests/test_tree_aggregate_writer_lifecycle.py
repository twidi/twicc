"""The real writer continues sources and foreground writes during tree preparation."""
import asyncio
import sqlite3
import threading

import pytest
from django.db import connections

from tests.test_compute_continuation import wal_runtime, result_message, wait_until  # noqa: F401 -- imports the shared pytest fixture
from twicc.core.enums import Provider
from twicc.providers import db_writer, compute_executor, tree_aggregate_runtime as runtime
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)


def test_other_source_and_foreground_continue_and_shutdown_drains_worker(wal_runtime, monkeypatch):  # noqa: F811
    session, path = wal_runtime
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = runtime.prepare_session_values
    def prepare(*args):
        started.set()
        try:
            assert release.wait(5)
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(runtime, 'prepare_session_values', prepare)
    monkeypatch.setattr(compute_executor, 'run_compute_sync', db_writer.run_compute_sync)
    submit = runtime.AggregatePreparationExecutor.submit
    def in_database(executor, function, /, *args, **kwargs):
        def configure():
            db = connections['default']
            db.settings_dict = {**db.settings_dict, 'NAME': str(path)}
            return function(*args, **kwargs)
        return submit(executor, configure)
    monkeypatch.setattr(runtime.AggregatePreparationExecutor, 'submit', in_database)
    async def run():
        db_writer.start_db_writer()
        applied = asyncio.Queue()
        run_id, done = db_writer.arm_compute_completion(Provider.CODEX, set(), 0, applied_queue=applied)
        message = result_message(session, run_id)
        message['session_fields']['compute_version'] = get_provider_helpers('codex').current_compute_version
        try:
            db_writer.get_subprocess_queue().put(message)
            assert (await asyncio.wait_for(applied.get(), 5)).outcome == 'applied'
            db_writer._tree_aggregates._inventory_ready = lambda: True
            assert await asyncio.to_thread(started.wait, 3)
            future = asyncio.get_running_loop().create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None,
                {session.id: {'title': 'foreground during aggregate'}}, future))
            assert await asyncio.wait_for(future, 3) == 1
            db_writer.get_subprocess_queue().put(message)
            assert session.id in db_writer._pending_repair_exclusions()
            assert (await asyncio.wait_for(applied.get(), 5)).outcome == 'applied'
            await wait_until(lambda: session.id not in db_writer._pending_repair_exclusions())
            assert not finished.is_set()
            stop = asyncio.create_task(db_writer.stop_db_writer())
            await asyncio.sleep(.02)
            stop.cancel()
            await asyncio.sleep(.02)
            stop.cancel()
            assert not stop.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(stop, 5)
            assert finished.is_set()
        finally:
            release.set()
            await db_writer.stop_db_writer()
            db_writer._compute_states.pop(run_id, None)
            db_writer._compute_done_events.pop(run_id, None)
    asyncio.run(run())
    with sqlite3.connect(path) as database:
        assert database.execute('SELECT title FROM core_session').fetchone() == ('foreground during aggregate',)
        assert database.execute('SELECT COUNT(*) FROM core_computeaggregaterepair').fetchone()[0] > 0


@pytest.mark.parametrize('cancel', [False, True])
def test_removal_preparation_leaves_foreground_worker_available(wal_runtime, monkeypatch, cancel):  # noqa: F811
    from twicc.providers import aggregate_removal
    session, path = wal_runtime
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    prepare = aggregate_removal.prepare_removal
    def blocked(*args):
        db = connections['default']
        db.settings_dict = {**db.settings_dict, 'NAME': str(path)}
        started.set()
        try:
            assert release.wait(5)
            return prepare(*args)
        finally:
            finished.set()
    monkeypatch.setattr(aggregate_removal, 'prepare_removal', blocked)
    async def run():
        db_writer.start_db_writer()
        stop = None
        try:
            db_writer.get_thread_queue().put(db_writer.DeleteSessionsPayload(Provider.CODEX, [session.id]))
            assert await asyncio.to_thread(started.wait, 3)
            future = asyncio.get_running_loop().create_future()
            db_writer.enqueue_async_job(db_writer._RetireSessionsJob(None,
                {session.id: {'title': 'foreground during removal'}}, future))
            assert await asyncio.wait_for(asyncio.shield(future), .5) == 1
            stop = asyncio.create_task(db_writer.stop_db_writer())
            await asyncio.sleep(.02)
            if cancel:
                stop.cancel()
                await asyncio.sleep(.02)
                stop.cancel()
            assert not stop.done() and not finished.is_set()
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(stop, 5)
            else:
                await asyncio.wait_for(stop, 5)
            assert finished.is_set()
        finally:
            release.set()
            if stop is not None and not stop.done():
                await asyncio.gather(stop, return_exceptions=True)
            await db_writer.stop_db_writer()
    asyncio.run(run())
    with sqlite3.connect(path) as database:
        assert database.execute('SELECT COUNT(*) FROM core_session').fetchone() == (0,)
        assert database.execute('SELECT COUNT(*) FROM core_computeaggregaterepair').fetchone()[0] > 0
