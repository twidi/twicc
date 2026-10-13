"""Stop-aware polling and database-read recovery for the active selector."""

import asyncio

from twicc.providers import background_compute_task


def test_explicit_stop_interrupts_retry_wait():
    async def scenario():
        stop = asyncio.Event()
        waiting = asyncio.create_task(background_compute_task._wait_for_retry_delay(stop, 60))
        await asyncio.sleep(0)
        stop.set()
        assert await asyncio.wait_for(waiting, .2) is False
    asyncio.run(scenario())


def test_candidate_read_failure_recovers_without_writer_admission():
    reads = 0
    delays = []

    async def read(ids):
        nonlocal reads
        reads += 1
        if reads == 1:
            from django.db import OperationalError
            raise OperationalError('temporary database read failure')
        return ids

    async def wait(_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(background_compute_task.read_compute_state(
        read, ['healthy'], asyncio.Event(), wait,
    ))
    assert result == ['healthy']
    assert delays == [1]


def test_repeated_read_failures_back_off_until_explicit_stop():
    delays = []
    async def read(ids):
        raise RuntimeError('persistent read failure')
    async def wait(event, delay):
        delays.append(delay)
        if len(delays) == 9:
            event.set()
            return False
        return True
    result = asyncio.run(background_compute_task.read_compute_state(
        read, ['failed'], asyncio.Event(), wait, stopped=['failed'],
    ))
    assert result == ['failed']
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]
