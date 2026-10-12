"""Priority boundaries must preserve resource dependencies and cancellation safety."""

import asyncio
import importlib

import pytest


def api():
    # A missing implementation is an assertion failure, not a collection error.
    assert importlib.util.find_spec('twicc.providers.write_coordinator') is not None
    return importlib.import_module('twicc.providers.write_coordinator')


async def queued(coordinator, resources, priority):
    attempted = asyncio.Event()

    async def acquire():
        attempted.set()
        return await coordinator.acquire(resources, priority)

    task = asyncio.create_task(acquire())
    await attempted.wait()
    return task


def test_foreground_overtakes_background_and_fifo_resumes():
    # A FIFO-only scheduler grants background before already-queued foreground.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        both = mod.WriteResources.ADMISSION | mod.WriteResources.WRITER
        held = await coordinator.acquire(both, mod.WritePriority.BACKGROUND)
        background = [await queued(coordinator, both, mod.WritePriority.BACKGROUND) for _ in range(2)]
        foreground = [await queued(coordinator, both, mod.WritePriority.FOREGROUND) for _ in range(2)]
        held.release()
        for index, task in enumerate(foreground):
            lease = await asyncio.wait_for(task, 1)
            assert not any(item.done() for item in background)
            assert not any(item.done() for item in foreground[index + 1:])
            lease.release()
        for index, task in enumerate(background):
            lease = await asyncio.wait_for(task, 1)
            assert not any(item.done() for item in background[index + 1:])
            lease.release()
        await coordinator.drain()
    asyncio.run(scenario())


def test_busy_writer_does_not_reserve_background_admission():
    # Separately acquiring admission for background work blocks a start unnecessarily.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        writer = await coordinator.acquire(mod.WriteResources.WRITER, mod.WritePriority.FOREGROUND)
        assert coordinator.try_background_lease() is None
        background = await queued(coordinator, mod.WriteResources.ADMISSION | mod.WriteResources.WRITER,
                                  mod.WritePriority.BACKGROUND)
        admission = await asyncio.wait_for(coordinator.acquire(mod.WriteResources.ADMISSION,
                                                               mod.WritePriority.FOREGROUND), 1)
        writer.release()
        assert not background.done()
        admission.release()
        (await asyncio.wait_for(background, 1)).release()
    asyncio.run(scenario())


def test_admission_waiter_does_not_block_holder_writer_dependency():
    # Treating a blocked foreground head as a global barrier deadlocks its holder.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        admission = await coordinator.acquire(mod.WriteResources.ADMISSION, mod.WritePriority.FOREGROUND)
        waiter = await queued(coordinator, mod.WriteResources.ADMISSION, mod.WritePriority.FOREGROUND)
        writer = await asyncio.wait_for(coordinator.acquire(mod.WriteResources.WRITER,
                                                            mod.WritePriority.FOREGROUND), 1)
        writer.release()
        admission.release()
        (await asyncio.wait_for(waiter, 1)).release()
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['settled', 'failed', 'cancelled'])
def test_demand_blocks_compute_and_maintenance_until_released_once(outcome):
    # Dropping pressure before dispatch or decrementing twice admits background early.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        first = coordinator.foreground_demand()
        second = coordinator.foreground_demand()
        both = mod.WriteResources.ADMISSION | mod.WriteResources.WRITER
        compute = await queued(coordinator, both, mod.WritePriority.BACKGROUND)
        assert coordinator.try_background_lease() is None
        foreground = await coordinator.acquire(mod.WriteResources.WRITER, mod.WritePriority.FOREGROUND)
        foreground.release()
        try:
            if outcome == 'failed':
                raise ValueError('dispatch failed')
            if outcome == 'cancelled':
                raise asyncio.CancelledError()
        except (ValueError, asyncio.CancelledError):
            pass
        finally:
            first.release()
            first.release()
        assert not compute.done()
        assert coordinator.try_background_lease() is None
        second.release()
        (await asyncio.wait_for(compute, 1)).release()
        maintenance = coordinator.try_background_lease()
        assert maintenance is not None
        maintenance.release()
        maintenance.release()
        await coordinator.drain()
    asyncio.run(scenario())


@pytest.mark.parametrize('grant_first', [False, True])
def test_cancelled_request_returns_even_an_already_granted_resource(grant_first):
    # A grant before task resumption must not leak resources when cancellation wins.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        held = await coordinator.acquire(mod.WriteResources.WRITER, mod.WritePriority.FOREGROUND)
        waiter = await queued(coordinator, mod.WriteResources.WRITER, mod.WritePriority.FOREGROUND)
        if grant_first:
            held.release()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        held.release()
        await asyncio.wait_for(coordinator.drain(), 1)
        lease = coordinator.try_background_lease()
        assert lease is not None
        lease.release()
    asyncio.run(scenario())


def test_drain_includes_granted_requests_before_their_coroutine_resumes():
    # A sentinel acquisition can overtake priority classes and clear lifecycle too early.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        held = await coordinator.acquire(mod.WriteResources.WRITER, mod.WritePriority.FOREGROUND)
        waiter = await queued(coordinator, mod.WriteResources.WRITER, mod.WritePriority.BACKGROUND)
        held.release()
        draining = asyncio.create_task(coordinator.drain())
        await asyncio.sleep(0)
        assert not draining.done()
        (await waiter).release()
        await asyncio.wait_for(draining, 1)
    asyncio.run(scenario())


def test_foreground_lock_facade_cannot_bypass_priority():
    # A separate raw lock would let compatibility callers write concurrently.
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        lock = coordinator.foreground_lock(mod.WriteResources.WRITER)
        assert await lock.acquire() is True
        assert lock.locked()
        background = await queued(coordinator, mod.WriteResources.ADMISSION | mod.WriteResources.WRITER,
                                  mod.WritePriority.BACKGROUND)
        attempted = asyncio.Event()
        entered = asyncio.Event()
        release = asyncio.Event()
        async def foreground():
            attempted.set()
            async with lock:
                entered.set()
                await release.wait()
        task = asyncio.create_task(foreground())
        await attempted.wait()
        lock.release()
        await asyncio.wait_for(entered.wait(), 1)
        assert lock.locked()
        assert not background.done()
        release.set()
        await task
        lease = await background
        assert lock.locked()
        lease.release()
        assert not lock.locked()
        with pytest.raises(RuntimeError):
            lock.release()
    asyncio.run(scenario())


def test_invalid_priority_type_rejects_before_registering_resources():
    async def scenario():
        mod = api()
        coordinator = mod.WriterCoordinator()
        with pytest.raises(TypeError, match='Expected a write priority'):
            await coordinator.acquire(mod.WriteResources.WRITER, 'foreground')
        assert not coordinator.requests
        assert not coordinator.holders
        assert not coordinator.demands
        await coordinator.drain()
    asyncio.run(scenario())
