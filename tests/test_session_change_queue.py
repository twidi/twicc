"""Event-loop queue contracts, independent of ORM and provider state."""
import asyncio
from pathlib import Path

import pytest
from watchfiles import Change


def queue_type():
    from twicc.providers import session_change_queue
    return session_change_queue


def test_fifo_coalesces_and_preserves_event_during_turn():
    async def run():
        q = queue_type().SessionChangeQueue()
        big, small = Path('big'), Path('small')
        q.enqueue(big, Change.modified)
        q.enqueue(big, Change.modified)
        turn = await q.next_change()
        q.enqueue(small, Change.added)
        q.enqueue(big, Change.modified)
        q.finish(turn, has_more=True)
        other = await q.next_change()
        assert other.path == small
        q.finish(other, has_more=False)
        again = await q.next_change()
        assert again.path == big and again.token > turn.token
        q.finish(again, has_more=False)
        assert q.idle
        q.close()
    asyncio.run(run())


@pytest.mark.parametrize('in_flight', [False, True])
@pytest.mark.parametrize('outcome', ['ready', 'failed', 'cancelled'])
def test_migration_release_is_total_and_survives_deferral_race(in_flight, outcome):
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('session')
        generation = object()
        q.observe_source(path, generation)
        q.enqueue(path, Change.modified)
        waiter = asyncio.create_task(q.wait_drained(path, target=module.PathDrainTarget(generation, 10)))
        await asyncio.sleep(0)
        turn = await q.next_change()
        release = module.MigrationRelease('id', path, outcome, False, 'test error', 1)
        if not in_flight:
            q.finish(turn, has_more=False, deferred=True)
        q.notify_migration_released(release)
        q.notify_migration_released(release)
        if in_flight:
            q.finish(turn, has_more=False, deferred=True)
        if outcome == 'ready':
            resumed = await asyncio.wait_for(q.next_change(), 1)
            q.committed(path, module.PathDrainTarget(generation, 10))
            q.finish(resumed, has_more=False)
        if outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            await asyncio.wait_for(waiter, 1)
        assert q.idle
        q.close()
    asyncio.run(run())


def test_finite_target_cancellation_replacement_and_close():
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('session')
        generation = object()
        q.observe_source(path, generation)
        target = module.PathDrainTarget(generation, 10)
        a = asyncio.create_task(q.wait_drained(path, target=target))
        b = asyncio.create_task(q.wait_drained(path, target=target))
        await asyncio.sleep(0)
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.committed(path, target)
        q.finish(turn, has_more=True)
        await asyncio.wait_for(b, 1)
        old = asyncio.create_task(q.wait_drained(path, target=module.PathDrainTarget(generation, 100)))
        await asyncio.sleep(0)
        q.observe_source(path, object())
        await asyncio.wait_for(old, 1)
        pending = asyncio.create_task(q.wait_drained(path, target=module.PathDrainTarget(q.source_generation(path), 100)))
        await asyncio.sleep(0)
        q.close()
        turn = await q.next_change()
        q.finish(turn, has_more=False)
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(asyncio.CancelledError):
            await q.next_change()
        with pytest.raises(RuntimeError):
            q.enqueue(path, Change.modified)
    asyncio.run(run())


def test_failed_turn_parks_until_new_event():
    async def run():
        q = queue_type().SessionChangeQueue()
        path = Path('bad')
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.finish(turn, has_more=True, failed=True)
        assert q.idle
        q.enqueue(path, Change.modified)
        retry = await q.next_change()
        assert retry.token > turn.token
        q.finish(retry, has_more=False)
        q.close()
    asyncio.run(run())


def test_close_does_not_admit_unbounded_new_slices():
    async def run():
        q = queue_type().SessionChangeQueue()
        path = Path('continuous')
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.close()
        q.finish(turn, has_more=True)
        assert q.idle
        with pytest.raises(asyncio.CancelledError):
            await q.next_change()
    asyncio.run(run())


def test_replay_release_during_non_deferred_callback_is_not_lost():
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('replay')
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.notify_migration_released(module.MigrationRelease('s', path, 'ready', True, None, 1))
        q.finish(turn, has_more=False)
        assert not q.idle
        replay = await q.next_change()
        q.finish(replay, has_more=False)
        assert q.idle
    asyncio.run(run())


def test_close_drains_dirty_event_admitted_before_close():
    async def run():
        q = queue_type().SessionChangeQueue()
        path = Path('continuous')
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.enqueue(path, Change.modified)
        q.close()
        q.finish(turn, has_more=True)
        assert not q.idle
        last = await q.next_change()
        assert last.token > turn.token
        q.finish(last, has_more=True)
        assert q.idle
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['failed', 'cancelled'])
@pytest.mark.parametrize('has_more', [False, True], ids=['drained', 'ready'])
def test_terminal_release_wins_over_non_deferred_commit(outcome, has_more):
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('terminal-in-flight')
        generation = object()
        q.observe_source(path, generation)
        target = module.PathDrainTarget(generation, 100)
        waiter = asyncio.create_task(q.wait_drained(path, target=target))
        await asyncio.sleep(0)
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.notify_migration_released(module.MigrationRelease('s', path, outcome, False, 'classification failed', 1))
        # The real consumer reports a committed checkpoint before finish.
        q.committed(path, target)
        q.finish(turn, has_more=has_more)
        assert q.idle
        if outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            await asyncio.wait_for(waiter, 1)
        assert q._paths[path].terminal == outcome
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['failed', 'cancelled'])
@pytest.mark.parametrize('event_after_release', [False, True], ids=['event-before', 'event-after'])
@pytest.mark.parametrize('disposition', ['ready', 'drained', 'deferred'])
@pytest.mark.parametrize('close_before_finish', [False, True], ids=['open', 'closing'])
def test_terminal_release_preserves_only_later_explicit_admissions(
    outcome, event_after_release, disposition, close_before_finish,
):
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('ordered-admissions')
        generation = object()
        q.observe_source(path, generation)
        q.enqueue(path, Change.modified)
        old_target = module.PathDrainTarget(generation, 100)
        new_target = module.PathDrainTarget(generation, 200)
        old_waiter = asyncio.create_task(q.wait_drained(path, target=old_target))
        await asyncio.sleep(0)
        turn = await q.next_change()
        release = module.MigrationRelease('s', path, outcome, False, 'failed', 9)
        if event_after_release:
            q.notify_migration_released(release)
        q.enqueue(path, Change.modified)
        new_waiter = asyncio.create_task(q.wait_drained(path, target=new_target))
        await asyncio.sleep(0)
        if not event_after_release:
            q.notify_migration_released(release)
        if close_before_finish:
            q.close()
        q.committed(path, old_target)
        q.finish(turn, has_more=disposition == 'ready', deferred=disposition == 'deferred')
        if outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await old_waiter
        else:
            await old_waiter
        if event_after_release:
            assert not q.idle, 'release discarded a later explicit event'
            assert not new_waiter.done(), 'release settled a later admission'
            next_turn = await q.next_change()
            q.committed(path, new_target)
            q.finish(next_turn, has_more=False)
            await new_waiter
        elif outcome == 'cancelled':
            with pytest.raises(asyncio.CancelledError):
                await new_waiter
        else:
            await new_waiter
        assert q.idle
    asyncio.run(run())


@pytest.mark.parametrize('replay', [False, True])
def test_ready_release_during_close_preserves_preclose_dirty_turn(replay):
    async def run():
        module = queue_type()
        q = module.SessionChangeQueue()
        path = Path('closing-release')
        generation = object()
        q.observe_source(path, generation)
        q.enqueue(path, Change.modified)
        turn = await q.next_change()
        q.enqueue(path, Change.modified)
        target = module.PathDrainTarget(generation, 3)
        waiter = asyncio.create_task(q.wait_drained(path, target=target))
        await asyncio.sleep(0)
        q.notify_migration_released(module.MigrationRelease('s', path, 'ready', replay, None, 10))
        q.close()
        q.finish(turn, has_more=True, deferred=True)
        assert not q.idle, 'ready release discarded the admitted dirty turn at close'
        next_turn = await q.next_change()
        q.committed(path, target)
        q.finish(next_turn, has_more=True)
        await waiter
        assert q.idle
    asyncio.run(run())
