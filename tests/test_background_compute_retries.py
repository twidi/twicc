import asyncio

from twicc.providers import background_compute_task

run_compute_with_stale_retries = background_compute_task.run_compute_with_stale_retries


def test_stale_compute_retries_only_a_session_that_stayed_quiet():
    pass_calls = []
    remaining_results = [["active"], []]
    offset_results = [
        {"active": 12},
        {"active": 12},
    ]
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return remaining_results.pop(0)

    async def load_offsets(_session_ids):
        return offset_results.pop(0)

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ["ready", "active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
    ))

    assert pass_calls == [["ready", "active"], ["active"]]
    assert delays == [1.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == []


def test_stale_compute_stops_after_three_changing_windows():
    pass_calls = []
    offsets = iter((10, 11, 12, 13, 14, 15))
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return ["active"]

    async def load_offsets(_session_ids):
        return {"active": next(offsets)}

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ["active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
    ))

    assert pass_calls == [["active"]]
    assert delays == [1.0, 2.0, 4.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == ["active"]


def test_failed_pass_retries_only_outdated_sessions():
    current = set()
    attempts = []

    async def run_pass(session_ids):
        attempts.append(list(session_ids))
        current.add("healthy")
        if len(attempts) == 1:
            return 1
        current.add("failed")
        return 0

    async def load_remaining(session_ids):
        return [sid for sid in session_ids if sid not in current]

    async def load_offsets(session_ids):
        return dict.fromkeys(session_ids, 10)

    async def wait_for_delay(_stop_event, _delay):
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ["failed", "healthy"], run_pass=run_pass,
        load_remaining=load_remaining, load_offsets=load_offsets,
        stop_event=asyncio.Event(), wait_for_delay=wait_for_delay,
    ))
    assert current == {"healthy", "failed"}
    assert attempts == [["failed", "healthy"], ["failed"]]
    assert result == (0, [])


def test_stale_compute_recovery_keeps_monitoring_until_the_session_is_quiet():
    pass_calls = []
    offsets = iter((10, 11, 12, 13, 14, 15, 16, 16))
    delays = []

    async def run_pass(session_ids):
        pass_calls.append(list(session_ids))
        return 0

    async def load_remaining(_session_ids):
        return [] if pass_calls else ["active"]

    async def load_offsets(_session_ids):
        return {"active": next(offsets)}

    async def wait_for_delay(_stop_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(background_compute_task.recover_stale_compute_sessions(
        ["active"],
        run_pass=run_pass,
        load_remaining=load_remaining,
        load_offsets=load_offsets,
        stop_event=asyncio.Event(),
        wait_for_delay=wait_for_delay,
        recovery_delays=(15.0, 30.0),
        recovery_interval=60.0,
    ))

    assert pass_calls == [["active"]]
    assert delays == [15.0, 30.0, 60.0, 60.0]
    assert result.failed_count == 0
    assert result.remaining_session_ids == []

def test_dead_run_rotates_faulty_newest_session_before_retry():
    current = set()
    attempts = []

    async def run_pass(session_ids):
        attempts.append(list(session_ids))
        for sid in session_ids:
            if sid == 'faulty':
                raise RuntimeError('worker died')
            current.add(sid)
        return 0

    async def load_remaining(session_ids):
        return [sid for sid in session_ids if sid not in current]

    async def load_offsets(session_ids):
        return dict.fromkeys(session_ids, 10)

    async def wait(_stop_event, _delay):
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ['faulty', 'older'], run_pass=run_pass, load_remaining=load_remaining,
        load_offsets=load_offsets, stop_event=asyncio.Event(), wait_for_delay=wait,
    ))
    assert current == {'older'}
    assert result.remaining_session_ids == ['faulty']
    assert attempts[1] == ['older']

def test_repeated_failures_back_off_until_explicit_stop():
    stop = asyncio.Event()
    delays = []
    attempts = []

    async def run_pass(ids):
        attempts.append(list(ids))
        return 1

    async def remaining(ids):
        return ids

    async def offsets(ids):
        return dict.fromkeys(ids, 10)

    async def wait(event, delay):
        delays.append(delay)
        if len(delays) == 6:
            event.set()
            return False
        return True

    result = asyncio.run(background_compute_task.recover_stale_compute_sessions(
        ['failed'], run_pass=run_pass, load_remaining=remaining,
        load_offsets=offsets, stop_event=stop, wait_for_delay=wait,
    ))
    assert delays == [15, 30, 60, 60, 60, 60]
    assert len(attempts) == 5
    assert result == (1, ['failed'])


def test_explicit_stop_interrupts_retry_wait():
    async def scenario():
        stop = asyncio.Event()
        waiting = asyncio.create_task(background_compute_task._wait_for_retry_delay(stop, 60))
        await asyncio.sleep(0)
        stop.set()
        assert await asyncio.wait_for(waiting, .2) is False
    asyncio.run(scenario())

def test_candidate_read_failure_recovers_after_completed_pass():
    reads = 0
    current = set()
    delays = []

    async def run_pass(ids):
        current.update(ids)
        return 0

    async def remaining(ids):
        nonlocal reads
        reads += 1
        if reads == 1:
            from django.db import OperationalError
            raise OperationalError('temporary database read failure')
        return [sid for sid in ids if sid not in current]

    async def offsets(ids):
        return dict.fromkeys(ids, 10)

    async def wait(_event, delay):
        delays.append(delay)
        return True

    result = asyncio.run(run_compute_with_stale_retries(
        ['healthy'], run_pass=run_pass, load_remaining=remaining,
        load_offsets=offsets, stop_event=asyncio.Event(), wait_for_delay=wait,
    ))
    assert result == (0, [])
    assert delays == [1]
