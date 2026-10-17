"""Bounded operation metrics use existing page records, without database reads."""

import asyncio
import logging

import pytest

from tests import test_content_storage_admission
from twicc.providers.compute_work import ComputeWorkState

from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage import maintenance_metrics as metrics


def test_counts_progress_and_timing(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(metrics, 'monotonic', lambda: clock[0])
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    operation = metrics.MaintenanceMetrics('operation', 'periodic_compression')
    operation.emit('start')
    page = maintenance.CompressionPage(100, (), False, maintenance.CompressionPageCounts(scanned=100, prepared=3))
    operation.record_page(page)
    operation.emit('progress')
    clock[0] = 10
    operation.emit('progress')
    operation.record_publication(maintenance.PublicationResult(changed=2, stale=1),
                                 wait_ms=500, transaction_ms=5, hold_ms=10)
    operation.emit('complete')
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    assert [event['event'] for event in events] == ['start', 'progress', 'complete']
    summary = events[-1]
    assert summary['scanned'] == 100 and summary['prepared'] == 3
    assert summary['changed'] == 2 and summary['stale'] == 1
    assert summary['resource_wait_ms'] == 500 and summary['writer_hold_max_ms'] == 10
    assert summary['transaction_ms'] == 5
    assert summary['cursor'] == 100 and 'percentage' not in summary
    assert summary['writer_hold_p95_upper_ms'] >= 10


def test_read_only_progress_outcomes_and_bounded_histogram(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(metrics, 'monotonic', lambda: clock[0])
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    operation = metrics.MaintenanceMetrics('read', 'periodic_compression')
    operation.emit('start')
    clock[0] = 11
    operation.record_page(maintenance.CompressionPage(4, (), True, maintenance.CompressionPageCounts(scanned=4)))
    operation.emit('progress')
    for _ in range(2000):
        operation.record_publication(maintenance.PublicationResult(), wait_ms=0, transaction_ms=1, hold_ms=250)
    operation.emit('cancelled')
    summary = caplog.records[-1].maintenance
    assert summary['writer_hold_ge_250_ms'] == 2000
    assert summary['scanned'] == 4 and summary['prepared'] == 0
    assert len(operation.hold_bins) < 20
    operation.emit('failure')
    assert caplog.records[-1].maintenance['event'] == 'failure'


writer_state = test_content_storage_admission.writer_state


@pytest.fixture(autouse=True)
def enabled_logger(monkeypatch):
    monkeypatch.setattr(metrics.logger, 'disabled', False)
    monkeypatch.setattr(metrics.logger, 'propagate', True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('outcome', ['complete', 'failure', 'cancelled'])
def test_periodic_outcome_cache_lifetime_and_deadline(
    writer_state, compute_executor_started, monkeypatch, settings, caplog, outcome,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    clock, passes, caches, recovered = [0], [], [], []
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: clock[0])
    original_recovery = maintenance.recovery_promotion_targets
    stop = asyncio.Event()

    def discovery(after):
        return ['missing-owner'] if after is None else []

    def recovery(ids, cache):
        recovered.append(cache)
        return original_recovery(ids, cache)

    def compression(cursor, *args, session_cache):
        passes.append(clock[0])
        caches.append(session_cache)
        clock[0] += 20
        if outcome == 'failure':
            raise ValueError('injected pass failure')
        if outcome == 'cancelled':
            stop.set()
        return maintenance.CompressionPage(100, (), True, maintenance.CompressionPageCounts(scanned=100))

    async def wait(stop, wake, timeout):
        if not passes:
            clock[0] += timeout
        else:
            assert timeout == 86400
            stop.set()

    monkeypatch.setattr(maintenance, 'pending_session_page', discovery)
    monkeypatch.setattr(maintenance, 'recovery_promotion_targets', recovery)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(stop, compute_work=ComputeWorkState()))
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    assert [event['event'] for event in events] == ['start', outcome]
    assert passes == [1800]
    assert caches[0] is recovered[-1]
    from twicc.providers.db_writer import get_write_coordinator
    assert not get_write_coordinator()._foreground_wait_observers


def test_publication_measures_actual_lease_hold_separately(writer_state, monkeypatch):
    from twicc.providers import compute_executor, db_writer, write_coordinator

    clock = [3.0]
    monkeypatch.setattr(maintenance, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(write_coordinator, 'perf_counter', lambda: clock[0])
    operation = metrics.MaintenanceMetrics('timed', 'periodic_compression')

    async def run(function, *args):
        return function(*args)

    def publish(active):
        clock[0] += .005
        return maintenance.PublicationResult(changed=2)

    monkeypatch.setattr(compute_executor, 'run_compute_sync', run)

    async def scenario():
        lease = db_writer.get_write_coordinator().try_background_lease()
        clock[0] += .100
        await maintenance._apply(publish, lease=lease, metrics=operation)
        assert lease.released

    asyncio.run(scenario())
    assert operation.counts['writer_hold_ms'] == pytest.approx(105)
    assert operation.counts['transaction_ms'] == pytest.approx(5)
    assert operation.counts['resource_wait_ms'] == 0
    assert operation.counts['changed'] == 2


def test_nested_publication_observes_only_outer_foreground_grant(writer_state):
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WritePriority

    async def scenario():
        samples = []
        unsubscribe = db_writer.get_write_coordinator().observe_foreground_wait(samples.append)
        async def nested():
            assert len(samples) == 1
        async def outer():
            await db_writer.run_under_storage_write_lock(nested, priority=WritePriority.FOREGROUND)
            await db_writer.run_under_db_write_lock(nested)
        try:
            await db_writer.run_under_storage_write_lock(outer, priority=WritePriority.FOREGROUND)
            assert len(samples) == 1
        finally:
            unsubscribe()
    asyncio.run(scenario())
