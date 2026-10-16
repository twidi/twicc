"""Publication admission must precede writer ownership and survive cancellation."""

import asyncio
import queue
import threading
from types import SimpleNamespace

import pytest

from twicc.providers import db_writer


@pytest.fixture
def writer_state(monkeypatch):
    """Use the existing isolated writer-lock fixture pattern, without a polling task."""
    from twicc.providers.write_coordinator import WriterCoordinator, WriteResources
    coordinator = WriterCoordinator()
    monkeypatch.setattr(db_writer, "_writer_signals", SimpleNamespace(sources=asyncio.Event()))
    monkeypatch.setattr(db_writer, "_pending_compute", None)
    monkeypatch.setattr(db_writer, "_write_coordinator", coordinator)
    monkeypatch.setattr(db_writer, "_db_write_lock", coordinator.foreground_lock(WriteResources.WRITER))
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr(db_writer, "_storage_admission_lock", coordinator.foreground_lock(WriteResources.ADMISSION))
    monkeypatch.setattr(db_writer, "_subprocess_queue", queue.Queue())
    monkeypatch.setattr(db_writer, "_thread_queue", queue.Queue())
    monkeypatch.setattr(db_writer, "_async_queue", asyncio.Queue())
    monkeypatch.setattr(db_writer, "_background_async_queue", asyncio.Queue())


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
                # Combined background acquisition returns immediately while
                # registration owns admission. The permanent tick stays free.
                assert not await db_writer._background_turn(publish)
                assert publication_results == []
                await start_task
                assert await db_writer._background_turn(publish)
            else:
                async with db_writer.storage_admission():
                    await db_writer.run_under_db_write_lock(publish)

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


def test_shutdown_drains_admission_before_clearing_locks(transactional_db):
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


@pytest.fixture
def coordinated_writer(monkeypatch):
    import importlib
    assert importlib.util.find_spec('twicc.providers.write_coordinator') is not None
    from twicc.providers.write_coordinator import WriterCoordinator, WriteResources
    coordinator = WriterCoordinator()
    monkeypatch.setattr(db_writer, '_write_coordinator', coordinator, raising=False)
    monkeypatch.setattr(db_writer, '_db_write_lock', coordinator.foreground_lock(WriteResources.WRITER))
    monkeypatch.setattr(db_writer, '_storage_admission_lock', coordinator.foreground_lock(WriteResources.ADMISSION))
    monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
    return coordinator


@pytest.mark.parametrize('eager', [False, True])
def test_combined_publication_preserves_origin_and_nested_ownership(coordinated_writer, eager):
    # Resetting priority in a nested writer turns background promotion into foreground demand.
    from twicc.providers.write_coordinator import WritePriority, write_priority

    async def scenario():
        loop = asyncio.get_running_loop()
        if eager:
            loop.set_task_factory(asyncio.eager_task_factory)
        async def inspect():
            assert write_priority.get() is WritePriority.BACKGROUND
            assert db_writer._db_write_lock.locked()
            assert db_writer._storage_admission_lock.locked()
            return 42
        async def nested():
            assert await db_writer.run_under_db_write_lock(inspect) == 42
            return await db_writer.run_under_storage_write_lock(inspect, priority=WritePriority.FOREGROUND)
        try:
            result = await db_writer.run_under_storage_write_lock(nested, priority=WritePriority.BACKGROUND)
            assert result == 42
            assert write_priority.get() is WritePriority.FOREGROUND
        finally:
            loop.set_task_factory(None)
    asyncio.run(scenario())


def test_combined_call_under_admission_keeps_foreground_dependency(coordinated_writer):
    from twicc.providers.write_coordinator import WritePriority, WriteResources, write_priority

    async def scenario():
        async with db_writer.storage_admission():
            attempted = asyncio.Event()
            async def waiter():
                attempted.set()
                return await coordinated_writer.acquire(WriteResources.ADMISSION, WritePriority.FOREGROUND)
            waiting = asyncio.create_task(waiter())
            await attempted.wait()
            async def inspect():
                assert write_priority.get() is WritePriority.FOREGROUND
            async with asyncio.timeout(1):
                await db_writer.run_under_storage_write_lock(inspect, priority=WritePriority.BACKGROUND)
            assert not waiting.done()
        (await asyncio.wait_for(waiting, 1)).release()
    asyncio.run(scenario())


def test_combined_call_rejects_writer_before_admission(coordinated_writer):
    from twicc.providers.write_coordinator import WritePriority

    async def inner():
        with pytest.raises(RuntimeError, match='before writer'):
            await db_writer.run_under_storage_write_lock(lambda: asyncio.sleep(0), priority=WritePriority.FOREGROUND)
    asyncio.run(db_writer.run_under_db_write_lock(inner))


def test_combined_child_must_wait_for_its_own_resources(coordinated_writer):
    from twicc.providers.write_coordinator import WritePriority, write_priority

    async def scenario():
        attempted = asyncio.Event()
        entered = asyncio.Event()
        async def child_factory():
            entered.set()
            assert write_priority.get() is WritePriority.FOREGROUND
        async def child():
            attempted.set()
            await db_writer.run_under_storage_write_lock(child_factory, priority=WritePriority.FOREGROUND)
        async def parent():
            task = asyncio.create_task(child())
            await attempted.wait()
            assert not entered.is_set()
            return task
        task = await db_writer.run_under_storage_write_lock(parent, priority=WritePriority.BACKGROUND)
        await asyncio.wait_for(task, 1)
        assert entered.is_set()
    asyncio.run(scenario())


def test_repeated_cancellation_drains_combined_worker(coordinated_writer, compute_executor_started):
    from twicc.providers.write_coordinator import WritePriority
    started = threading.Event()
    release = threading.Event()
    def worker():
        started.set()
        assert release.wait(5)
    async def scenario():
        first = asyncio.create_task(db_writer.run_under_storage_write_lock(
            lambda: db_writer.run_compute_sync(worker), priority=WritePriority.BACKGROUND,
        ))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            for _ in range(3):
                first.cancel()
                await asyncio.sleep(0)
                assert db_writer._db_write_lock.locked()
                assert db_writer._storage_admission_lock.locked()
            assert coordinated_writer.try_background_lease() is None
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(coordinated_writer.drain(), 1)
        assert not db_writer._db_write_lock.locked()
        assert not db_writer._storage_admission_lock.locked()
    asyncio.run(scenario())


def test_combined_stop_guards_and_internal_drain(coordinated_writer):
    from twicc.providers.write_coordinator import WritePriority, WriteResources
    async def scenario():
        held = await coordinated_writer.acquire(WriteResources.WRITER, WritePriority.FOREGROUND)
        called = []
        async def factory():
            called.append(True)
        pending = asyncio.create_task(db_writer.run_under_storage_write_lock(factory, priority=WritePriority.BACKGROUND))
        await asyncio.sleep(0)
        db_writer._db_writer_stop_event.set()
        held.release()
        with pytest.raises(RuntimeError, match='stopping'):
            await pending
        with pytest.raises(RuntimeError, match='stopping'):
            await db_writer.run_under_storage_write_lock(factory, priority=WritePriority.FOREGROUND)
        assert not called
        await db_writer.run_under_storage_write_lock(factory, priority=WritePriority.BACKGROUND, internal=True)
        assert called == [True]
    asyncio.run(scenario())


def test_child_cannot_reuse_inherited_admission_owner(coordinated_writer):
    # A ContextVar-only check would let a child publish before its parent's start completes.
    from twicc.providers.write_coordinator import WritePriority
    async def scenario():
        attempted = asyncio.Event()
        entered = asyncio.Event()
        async def publication():
            entered.set()
        async def child():
            attempted.set()
            await db_writer.run_under_storage_write_lock(publication, priority=WritePriority.FOREGROUND)
        async with db_writer.storage_admission():
            task = asyncio.create_task(child())
            await attempted.wait()
            assert not entered.is_set()
        await asyncio.wait_for(task, 1)
        assert entered.is_set()
    asyncio.run(scenario())


def test_combined_content_snapshot_and_origin_restore_after_failure(coordinated_writer, monkeypatch):
    from types import SimpleNamespace
    from twicc.core.content_storage.lifecycle import active_session_ids
    from twicc.providers.write_coordinator import WritePriority, write_priority
    def snapshot():
        assert db_writer._storage_admission_lock.locked()
        assert db_writer._db_write_lock.locked()
        return [SimpleNamespace(session_id='live-leaf')]
    monkeypatch.setattr('twicc.agent.registry.get_agent_manager_registry', lambda: SimpleNamespace(
        get_active_agents=snapshot,
    ))
    async def scenario():
        async def factory():
            assert active_session_ids.get() == frozenset({'live-leaf'})
            assert write_priority.get() is WritePriority.BACKGROUND
            raise ValueError('publication failed')
        with pytest.raises(ValueError, match='publication failed'):
            await db_writer.run_under_storage_write_lock(
                factory, priority=WritePriority.BACKGROUND, content_activity=True,
            )
        assert write_priority.get() is WritePriority.FOREGROUND
        assert active_session_ids.get() == frozenset()
        assert not db_writer._db_write_lock.locked()
        assert not db_writer._storage_admission_lock.locked()
    asyncio.run(scenario())


def test_shutdown_waits_for_background_grant_across_repeated_cancellation(transactional_db):
    # A foreground drain sentinel can overtake a queued background resource request.
    from twicc.providers.write_coordinator import WritePriority, WriteResources
    async def scenario():
        db_writer.start_db_writer()
        coordinator = db_writer.get_write_coordinator()
        foreground = await coordinator.acquire(WriteResources.WRITER, WritePriority.FOREGROUND)
        attempted = asyncio.Event()
        async def background():
            attempted.set()
            return await coordinator.acquire(
                WriteResources.ADMISSION | WriteResources.WRITER, WritePriority.BACKGROUND,
            )
        request = asyncio.create_task(background())
        await attempted.wait()
        stop = asyncio.create_task(db_writer.stop_db_writer())
        try:
            await asyncio.shield(db_writer._db_writer_task)
            for _ in range(2):
                stop.cancel()
                await asyncio.sleep(0)
                assert db_writer.get_write_coordinator() is coordinator
                assert not stop.done()
            foreground.release()
            lease = await asyncio.wait_for(request, 1)
            assert not stop.done()
            assert db_writer.get_write_coordinator() is coordinator
            lease.release()
            with pytest.raises(asyncio.CancelledError):
                await stop
            with pytest.raises(RuntimeError, match='not started'):
                db_writer.get_write_coordinator()
        finally:
            foreground.release()
            if request.done() and not request.cancelled():
                request.result().release()
            await db_writer.stop_db_writer()
    asyncio.run(scenario())
