"""Publication admission must precede writer ownership and survive cancellation."""

import asyncio
import queue
import threading

import orjson
import pytest

from twicc.providers import db_writer


@pytest.fixture
def writer_state(monkeypatch):
    """Use the existing isolated writer-lock fixture pattern, without a polling task."""
    monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr(db_writer, "_storage_admission_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(db_writer, "_subprocess_queue", queue.Queue())
    monkeypatch.setattr(db_writer, "_thread_queue", queue.Queue())
    monkeypatch.setattr(db_writer, "_async_queue", asyncio.Queue())


@pytest.mark.parametrize("publication_path", ["compute", "maintenance"])
def test_pending_start_and_compression_never_invert_locks(writer_state, monkeypatch, publication_path):
    # Removing the fence lets stale publication compress before registration persists.
    async def scenario():
        registration_paused = asyncio.Event()
        registration_release = asyncio.Event()
        publication_attempted = asyncio.Event()
        manager_lock = asyncio.Lock()
        active_roots = set()
        admission_trace = []
        publication_results = []

        async def persist_start():
            admission_trace.append("writer_acquired")

        async def start():
            async with manager_lock, db_writer.storage_admission():
                admission_trace.append("fence_acquired")
                registration_paused.set()
                await registration_release.wait()
                active_roots.add("root")
                await db_writer.run_under_db_write_lock(persist_start)

        async def publish(_message=None):
            # Immutable protection travels to the synchronous publication in later tasks.
            protection = frozenset(active_roots)
            publication_results.append("root" not in protection)

        async def publication():
            publication_attempted.set()
            if publication_path == "compute":
                db_writer._subprocess_queue.put(orjson.dumps({"type": "session_complete", "provider": "codex"}))
                await db_writer._drain_one()
            else:
                async with db_writer.storage_admission():
                    await db_writer.run_under_db_write_lock(publish)

        monkeypatch.setattr(db_writer, "_process_compute_message", publish)
        start_task = asyncio.create_task(start())
        await registration_paused.wait()
        publication_task = asyncio.create_task(publication())
        try:
            await publication_attempted.wait()
            # Hand control to the already-created writer drive, without a timing sleep.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            registration_release.set()
        await asyncio.wait_for(asyncio.gather(start_task, publication_task), timeout=1)
        assert admission_trace.index("fence_acquired") < admission_trace.index("writer_acquired")
        assert publication_results == [False]

    asyncio.run(scenario())


def test_cancelled_publication_keeps_ownership_until_worker_finishes(writer_state, compute_executor_started):
    # Releasing admission on cancellation lets a second publication enter before worker completion.
    worker_started = threading.Event()
    worker_release = threading.Event()
    worker_finished = threading.Event()

    def worker():
        worker_started.set()
        assert worker_release.wait(5), "worker release missing"
        worker_finished.set()

    async def scenario():
        ownership_released = asyncio.Event()
        second_admitted = asyncio.Event()
        second_writer_entered = asyncio.Event()
        second_attempted = asyncio.Event()

        async def first():
            try:
                async with db_writer.storage_admission():
                    await db_writer.run_under_db_write_lock(lambda: db_writer.run_compute_sync(worker))
            finally:
                ownership_released.set()

        async def second_apply():
            second_writer_entered.set()

        async def second():
            second_attempted.set()
            async with db_writer.storage_admission():
                second_admitted.set()
                await db_writer.run_under_db_write_lock(second_apply)

        first_task = asyncio.create_task(first())
        assert await asyncio.to_thread(worker_started.wait, 2)
        first_task.cancel()
        second_task = asyncio.create_task(second())
        try:
            await second_attempted.wait()
            await asyncio.sleep(0)
            assert not second_writer_entered.is_set()
            assert not ownership_released.is_set()
            assert not second_admitted.is_set()
        finally:
            worker_release.set()
            with pytest.raises(asyncio.CancelledError):
                await first_task
            await asyncio.wait_for(second_task, 1)
        assert worker_finished.is_set()
        assert ownership_released.is_set()
        assert second_writer_entered.is_set()

    asyncio.run(scenario())


def test_writer_held_apply_rejects_storage_admission(writer_state):
    async def forbidden():
        with pytest.raises(RuntimeError, match="before writer"):
            async with db_writer.storage_admission():
                pass

    asyncio.run(db_writer.run_under_db_write_lock(forbidden))


def test_storage_admission_requires_writer_lifetime():
    async def scenario():
        with pytest.raises(RuntimeError, match="not started"):
            async with db_writer.storage_admission():
                pass

    asyncio.run(scenario())


def test_shutdown_drains_admission_before_clearing_locks():
    async def scenario():
        db_writer.start_db_writer()
        admitted = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with db_writer.storage_admission():
                admitted.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await admitted.wait()
        shutdown = asyncio.create_task(db_writer.stop_db_writer())
        try:
            # Join the writer loop only; shutdown must still wait for admission.
            await asyncio.shield(db_writer._db_writer_task)
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert db_writer._db_write_lock is not None
        finally:
            release.set()
            await task
            await shutdown
        with pytest.raises(RuntimeError, match="not started"):
            async with db_writer.storage_admission():
                pass

    asyncio.run(scenario())


def test_unrelated_writer_skips_active_registry_snapshot(writer_state, monkeypatch):
    def forbidden():
        raise AssertionError('Unrelated writer work must not read active agents')

    monkeypatch.setattr('twicc.agent.registry.get_agent_manager_registry', forbidden)

    async def job():
        return 'settings'

    assert asyncio.run(db_writer.run_under_db_write_lock(job)) == 'settings'


def test_content_writer_captures_actual_active_snapshot(writer_state, monkeypatch):
    from types import SimpleNamespace
    from twicc.core.content_storage.lifecycle import active_session_ids

    calls = []

    def snapshot():
        calls.append('snapshot')
        return [SimpleNamespace(session_id='native-leaf')]

    monkeypatch.setattr('twicc.agent.registry.get_agent_manager_registry', lambda: SimpleNamespace(
        get_active_agents=snapshot,
    ))

    async def job():
        assert db_writer._lease_admits_current()
        return active_session_ids.get()

    assert asyncio.run(db_writer.run_under_db_write_lock(job, content_activity=True)) == frozenset({'native-leaf'})
    assert calls == ['snapshot']
    assert active_session_ids.get() == frozenset()
