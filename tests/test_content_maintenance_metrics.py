"""Bounded operation metrics use existing page records, without database reads."""

import asyncio
import logging

import pytest

from tests import test_content_storage_admission
from tests.test_content_maintenance_retry import retry_scenario
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
    operation.record_discovery('session-cursor', 2, 1, 25)
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
    assert summary['discovery_cursor'] == 'session-cursor'
    assert summary['discovery_pages'] == 1 and summary['recovery_reference_probes'] == 1
    assert summary['preparation_ms'] == summary['discovery_preparation_ms'] == 25
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
    monkeypatch.setattr(maintenance, 'periodic_hot_root_page', lambda after, now: discovery(after))
    monkeypatch.setattr(maintenance, 'recovery_promotion_targets', recovery)
    monkeypatch.setattr(maintenance, 'prepare_compression_batch', compression)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(stop, compute_work=ComputeWorkState()))
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    assert [event['event'] for event in events if event['operation_kind'] == 'periodic_compression'] == ['start', 'stage_transition', outcome]
    periodic_events = [event for event in events if event['operation_kind'] == 'periodic_compression']
    assert periodic_events[-1]['stage'] == 'plaintext_compression'
    assert periodic_events[-1]['root_selected'] == periodic_events[-1]['root_inspected'] == 1
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('cancel_during', [None, 'startup', 'periodic'])
def test_conformant_hot_recovery_reports_discovery_progress(
    writer_state, compute_executor_started, content_row_factory, monkeypatch, settings, caplog, cancel_during,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from tests.test_content_root_state import session
    from tests.test_content_maintenance_reads import payload_selects
    from twicc.core.models import SessionItem
    from twicc.providers.db_writer import get_write_coordinator

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    owners = [session(f'hot-{index}', content_storage_version=1, storage_incarnation=f'inc-{index}')
              for index in range(3)]
    for owner in owners:
        row = content_row_factory(owner.id, False, session_ids=[owner.id])
        SessionItem.objects.create(session=owner, line_num=1, content='', current_content=row)
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    clock, scheduler_clock, scans, payload_reads, caches = [0.0], [0.0], [], [], []
    stop = asyncio.Event()
    monkeypatch.setattr(metrics, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(maintenance, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(maintenance, '_maintenance_time', lambda: scheduler_clock[0])
    monkeypatch.setattr(maintenance, 'TREE_PAGE_SIZE', 1)
    original = maintenance.recovery_promotion_targets

    def recovery(ids, cache):
        with CaptureQueriesContext(connection) as queries:
            result = original(ids, cache)
        assert not result
        payload_reads.extend(payload_selects(queries))
        caches.append(cache)
        clock[0] += 11
        phase = 'startup' if scheduler_clock[0] == 0 else 'periodic'
        scans.append(phase)
        if phase == cancel_during and scans.count(phase) == 2:
            stop.set()
        return result

    def forbidden_grant():
        pytest.fail('Conformant discovery must not request a writer grant')

    async def wait(stop, wake, timeout):
        if scheduler_clock[0] == 0:
            assert timeout == 1800
            scheduler_clock[0] = 1800
        else:
            assert timeout == 86400
            stop.set()

    monkeypatch.setattr(maintenance, 'recovery_promotion_targets', recovery)
    monkeypatch.setattr(get_write_coordinator(), 'try_background_lease', forbidden_grant)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(stop, compute_work=ComputeWorkState()))
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    startup = [event for event in events if event['operation_kind'] == 'plaintext_recovery_discovery']
    assert startup[0]['event'] == 'start'
    assert startup[-1]['event'] == ('cancelled' if cancel_during == 'startup' else 'complete')
    kinds = ['plaintext_recovery_discovery'] + ([] if cancel_during == 'startup' else ['periodic_compression'])
    for kind in kinds:
        progress = [event for event in events if event['operation_kind'] == kind and event['event'] == 'progress']
        assert len(progress) >= 2
        assert [event['discovery_cursor'] for event in progress[:2]] == ['hot-0', 'hot-1']
        assert progress[1]['discovery_pages'] == progress[1]['recovery_reference_probes'] == 2
        assert progress[1]['discovery_preparation_ms'] == 22000
        if kind == 'periodic_compression':
            assert progress[1]['root_selection_ms'] == 0
            assert progress[1]['root_inspection_ms'] == 22000
        assert progress[1]['cursor'] is None and progress[1]['scanned'] == 0
        assert progress[1]['payload_bytes_loaded'] == progress[1]['publication_batches'] == 0
    periodic_events = [event for event in events if event['operation_kind'] == 'periodic_compression']
    if cancel_during == 'periodic':
        assert periodic_events[-1]['stage'] == 'root_restoration'
        assert periodic_events[-1]['root_selected'] == periodic_events[-1]['root_inspected'] == 2
    assert not payload_reads
    assert all(cache._closed and not cache._metadata for cache in caches)
    assert not get_write_coordinator()._foreground_wait_observers


def test_periodic_stages_have_distinct_populations(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(metrics, 'monotonic', lambda: clock[0])
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    operation = metrics.MaintenanceMetrics('staged', 'periodic_compression')
    operation.emit('start')
    clock[0] = 11
    operation.record_root_selection(selected=3, inspected=2, duration_ms=25)
    operation.emit('progress')
    clock[0] = 12
    operation.set_stage('plaintext_compression')
    operation.record_page(maintenance.CompressionPage(7, (), True, maintenance.CompressionPageCounts(scanned=4)))
    clock[0] = 22
    operation.emit('progress')
    clock[0] = 25
    operation.emit('complete')
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    assert [event['event'] for event in events] == ['start', 'progress', 'stage_transition', 'progress', 'complete']
    assert [event['stage'] for event in events] == ['root_restoration', 'root_restoration',
                                                 'plaintext_compression', 'plaintext_compression',
                                                 'plaintext_compression']
    assert events[0]['selection'] == 'hot_roots'
    assert events[1]['scanned'] == 0
    summary = events[-1]
    assert summary['root_selected'] == 3 and summary['root_inspected'] == 2
    assert summary['root_selection_ms'] == 25 and summary['scanned'] == 4
    assert summary['stage_elapsed_ms'] == {'root_restoration': 12000, 'plaintext_compression': 13000}
    assert summary['selection'] == 'plaintext'


@pytest.mark.django_db
def test_stage_metrics_do_not_count_compressed_rows_with_sql(caplog):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    caplog.set_level(logging.INFO, logger=metrics.__name__)
    with CaptureQueriesContext(connection) as queries:
        operation = metrics.MaintenanceMetrics('query-free', 'periodic_compression')
        operation.emit('start')
        operation.record_root_selection(2, 2, 1)
        operation.set_stage('plaintext_compression')
        operation.emit('complete')
    assert len(queries) == 0
    assert caplog.records[-1].maintenance['scanned'] == 0


@pytest.mark.django_db(transaction=True)
def test_retry_diagnostics_survive_pass_completion(retry_scenario, caplog):
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    row, attempts, _, _, caches = retry_scenario(origin='periodic')
    assert row.codec == 'plain' and attempts == [1900, 1901]
    assert caches[0] is not caches[1]
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    periodic = next(event for event in events
                    if event['operation_kind'] == 'periodic_compression' and event['event'] == 'complete')
    restoration = next(event for event in events
                       if event['operation_kind'] == 'plaintext_restoration' and event['event'] == 'complete')
    assert restoration['parent_operation_id'] == periodic['operation_id']
    assert events.index(restoration) > events.index(periodic)
    assert periodic['changed'] == periodic['publication_batches'] == 0
    assert restoration['changed'] == 1


@pytest.mark.django_db(transaction=True)
def test_root_inspection_failure_reports_selection_and_closes_pass_cache(
    writer_state, compute_executor_started, monkeypatch, settings, caplog,
):
    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    caplog.set_level(logging.INFO, logger=metrics.__name__)
    caches = []
    monkeypatch.setattr(maintenance, 'INITIAL_DELAY', 0)
    monkeypatch.setattr(maintenance, 'pending_session_page', lambda after: [])
    monkeypatch.setattr(maintenance, 'periodic_hot_root_page', lambda after, now: ['root'])

    def fail(ids, cache):
        caches.append(cache)
        raise ValueError('injected root inspection failure')

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, 'recovery_promotion_targets', fail)
    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event(), compute_work=ComputeWorkState()))
    events = [record.maintenance for record in caplog.records if hasattr(record, 'maintenance')]
    failure = next(event for event in events if event['event'] == 'failure')
    assert failure['stage'] == 'root_restoration'
    assert failure['root_selected'] == 1 and failure['root_inspected'] == 0
    assert failure['scanned'] == failure['payload_bytes_loaded'] == failure['publication_batches'] == 0
    assert caches[0]._closed
