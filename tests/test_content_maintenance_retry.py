"""Failed restoration remains pending without retaining foreground resources."""

import asyncio

import pytest

from tests.content_storage_helpers import content_row_factory
from tests.test_content_root_state import session
from tests.test_content_storage_admission import writer_state
from twicc.core.models import SessionItem
from twicc import content_maintenance_task as maintenance
from twicc.providers import db_writer
from twicc.providers.compute_work import ComputeWorkState
from twicc.providers.write_coordinator import WritePriority


def test_retry_backoff_is_capped():
    target = maintenance._PromotionTarget(recovery=False, priority=WritePriority.BACKGROUND)
    try:
        delays = []
        for _ in range(8):
            target.schedule_retry(100.0)
            delays.append(target.retry_at - 100.0)
            target.reset()
        assert delays == [1, 2, 4, 8, 16, 32, 60, 60]
        target.clear_retry()
        assert target.retry_at is None and target.retry_attempts == 0
    finally:
        target.finish('cancelled')


def test_retry_retains_target_and_releases_foreground_demand(writer_state, monkeypatch, settings):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    targets, clock, waits, observations = [], [100.0], [], []
    original = maintenance._PromotionTarget

    def target(**kwargs):
        result = original(**kwargs)
        targets.append(result)
        return result

    async def apply(*args, **kwargs):
        raise RuntimeError('injected snapshot failure')

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            maintenance.request_root_promotion('root')
            await wake.wait()
        else:
            observations.append((getattr(targets[0], 'retry_at', None),
                                 targets[0].session_cache._closed, targets[0].priority,
                                 bool(db_writer.get_write_coordinator().demands), timeout))
            stop.set()

    monkeypatch.setattr(maintenance, '_PromotionTarget', target)
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    monkeypatch.setattr(maintenance, 'pending_session_page', lambda *args: [])
    monkeypatch.setattr(maintenance, '_apply', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    assert observations == [(101, False, WritePriority.BACKGROUND, False, 1)]
    assert len(waits) == 2
    assert targets[0].session_cache._closed


@pytest.fixture
def retry_scenario(content_row_factory, writer_state, compute_executor_started, monkeypatch, settings):
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)

    def run(*, due=False, origin='startup', phase='prepare', failures=1, upgrade=False, block=None,
            cancel=False, child=False, stop_after_compression=False):
        if due:
            monkeypatch.setattr(maintenance, 'INITIAL_DELAY', 0)
        root = session('root', content_storage_version=1, storage_incarnation='root-inc', content_state_epoch=3)
        owner = session('child', type='subagent', parent_session=root, content_storage_version=1,
                        storage_incarnation='child-inc', content_state_epoch=2) if child else root
        row = content_row_factory('restore bytes ' * 1000, True, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
        clock, waits, attempts, compressed, targets, caches = [100.0], [], [], [], [], []
        work = ComputeWorkState()
        original_target = maintenance._PromotionTarget
        original_page = maintenance.pending_session_page
        original_periodic_page = maintenance.periodic_hot_root_page
        original_prepare = maintenance.prepare_promotion_batch
        original_publish = maintenance.apply_promotion_batch
        tokens = []
        observations = []

        def target(**kwargs):
            result = original_target(**kwargs)
            targets.append(result)
            return result

        def page(*args):
            if origin == 'explicit' or origin == 'periodic' and clock[0] == 100:
                return []
            return original_page(*args)

        def periodic_page(after, now):
            return [] if origin == 'explicit' else original_periodic_page(after, now)

        def fail_or_run(function, *args, **kwargs):
            attempts.append(clock[0])
            caches.append(kwargs['session_cache'])
            if len(attempts) <= failures:
                raise RuntimeError('controlled restoration failure')
            return function(*args, **kwargs)

        def prepare(*args, **kwargs):
            return fail_or_run(original_prepare, *args, **kwargs) if phase == 'prepare' else original_prepare(*args, **kwargs)

        def publish(*args, **kwargs):
            return fail_or_run(original_publish, *args, **kwargs) if phase == 'publish' else original_publish(*args, **kwargs)

        def compression(*args, **kwargs):
            compressed.append(clock[0])
            return maintenance.CompressionPage(0, (), True, maintenance.CompressionPageCounts())

        async def wait(stop, wake, timeout):
            waits.append(timeout)
            if not attempts:
                if origin == 'explicit':
                    maintenance.request_root_promotion(owner.id)
                    await wake.wait()
                else:
                    clock[0] = 1900
                return
            coordinator = db_writer.get_write_coordinator()
            observations.append((bool(coordinator.demands), bool(coordinator.holders)))
            # Backoff must leave real coordinated foreground work runnable.
            async def foreground_write():
                await asyncio.to_thread(lambda: SessionItem.objects.filter(session=owner).update(content=''))
            await db_writer.run_under_db_write_lock(foreground_write)
            if stop_after_compression and compressed:
                observations.append((targets[0].session_cache._closed, False))
                stop.set()
                return
            if cancel:
                raise asyncio.CancelledError
            if block == 'compute' and len(attempts) == 1 and not tokens:
                work.set_pending('codex', True)
                tokens.append(True)
                clock[0] += timeout
                return
            if block == 'compute' and work.has_pending():
                work.set_pending('codex', False)
                return
            if block == 'deferral' and len(attempts) == 1 and not tokens:
                tokens.append(maintenance.defer_background_promotions(root.id, root.storage_incarnation))
                clock[0] += timeout
                return
            if block == 'deferral' and not tokens[0].released:
                tokens[0].release()
                return
            if upgrade and len(attempts) == 1:
                maintenance.request_root_promotion(owner.id)
                await wake.wait()
                return
            if len(attempts) <= failures:
                clock[0] += timeout
            else:
                stop.set()

        monkeypatch.setattr(maintenance, '_PromotionTarget', target)
        monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
        monkeypatch.setattr(maintenance, 'pending_session_page', page)
        monkeypatch.setattr(maintenance, 'periodic_hot_root_page', periodic_page)
        monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
        monkeypatch.setattr(maintenance, 'apply_promotion_batch', publish)
        monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
        monkeypatch.setattr(maintenance, '_wait', wait)
        async def scenario():
            task = maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=work)
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            else:
                await asyncio.wait_for(task, 5)
        try:
            asyncio.run(scenario())
        finally:
            if block == 'deferral':
                for token in tokens:
                    token.release()
        row.refresh_from_db()
        assert all(not demand and not held for demand, held in observations)
        assert all(target.session_cache._closed for target in targets)
        assert len(targets) == 1
        return row, attempts, waits, compressed, caches
    return run


@pytest.mark.django_db(transaction=True)
def test_subagent_retry_succeeds_without_new_event(retry_scenario):
    row, attempts, waits, _, _ = retry_scenario(child=True)
    assert row.codec == 'plain'
    assert attempts == [100, 101]
    assert waits == [1, 1799]


@pytest.mark.django_db(transaction=True)
def test_new_foreground_request_upgrades_retry(retry_scenario):
    row, attempts, _, _, _ = retry_scenario(origin='explicit', upgrade=True)
    assert row.codec == 'plain'
    assert attempts == [100, 100]


@pytest.mark.django_db(transaction=True)
def test_publication_failures_keep_increasing_backoff(retry_scenario):
    row, attempts, waits, _, _ = retry_scenario(phase='publish', failures=3)
    assert row.codec == 'plain'
    assert attempts == [100, 101, 103, 107]
    assert waits == [1, 2, 4, 1793]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('origin', ['startup', 'periodic'])
def test_failed_periodic_target_does_not_block_compression(retry_scenario, origin):
    row, attempts, _, compressed, _ = retry_scenario(
        origin=origin, failures=10**9, due=origin == 'startup', stop_after_compression=True,
    )
    assert row.codec == 'zstd'
    assert compressed == attempts
    assert len(attempts) == 1


@pytest.mark.django_db(transaction=True)
def test_retry_survives_periodic_cache_close(retry_scenario):
    row, attempts, _, compressed, caches = retry_scenario(origin='periodic')
    assert row.codec == 'plain'
    assert attempts == [1900, 1901] and compressed == [1900]
    assert caches[0] is not caches[1]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('block', ['compute', 'deferral'])
def test_retry_compute_gate_and_deferral_do_not_spin(retry_scenario, block):
    row, attempts, waits, _, _ = retry_scenario(origin='periodic', block=block)
    assert row.codec == 'plain'
    assert attempts == [1900, 1901]
    assert 0 not in waits
    if block == 'compute':
        assert waits == [1800, 1, 86399, 86399]


@pytest.mark.django_db(transaction=True)
def test_retry_cancel_drains_admitted_work_and_closes_cache(retry_scenario):
    row, attempts, _, _, caches = retry_scenario(cancel=True)
    assert row.codec == 'zstd' and attempts == [100]
    assert caches[0]._closed


@pytest.mark.django_db(transaction=True)
def test_retry_wait_releases_writer_and_foreground_demand(retry_scenario):
    row, attempts, _, _, _ = retry_scenario(origin='explicit')
    assert row.codec == 'plain' and attempts == [100, 101]


def test_due_retry_peers_preserve_resource_contention_wait(writer_state, monkeypatch, settings):
    """Due peers must not reduce the background resource wait to zero."""
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    clock, waits, failed = [100.0], [], []
    coordinator = db_writer.get_write_coordinator()
    pressure = []

    async def apply(function, session_id, **kwargs):
        failed.append(session_id)
        raise RuntimeError('injected snapshot failure')

    async def wait(stop, wake, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            maintenance.request_root_promotion('first')
            maintenance.request_root_promotion('second')
            await wake.wait()
        elif len(waits) == 2:
            clock[0] = 101
            # Real foreground pressure denies background leases to both due retries.
            pressure.append(coordinator.foreground_demand())
        elif len(waits) == 4:
            stop.set()

    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    monkeypatch.setattr(maintenance, 'pending_session_page', lambda *args: [])
    monkeypatch.setattr(maintenance, '_apply', apply)
    monkeypatch.setattr(maintenance, '_wait', wait)
    try:
        asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    finally:
        for demand in pressure:
            demand.release()
    assert failed == ['first', 'second']
    assert waits == [1800, 1, 0.05, 0.05]
    assert not coordinator.holders and not coordinator.demands
