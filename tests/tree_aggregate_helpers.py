"""Explicit continuation helpers for standalone private writer tests."""
import asyncio

from twicc.providers import db_writer
from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor
from twicc.providers.compute_repair_journal import compute_repair_upper_id, read_compute_repair_page
from twicc.providers.tree_aggregate_runtime import TreeAggregateScheduler
from twicc.providers.write_coordinator import WriterCoordinator


def prepared_history_job(job):
    from twicc.providers.aggregate_removal import prepare_removal
    return job._replace(aggregate_evidence=prepare_removal([job.session_id]))


def begin_history_replacement(job):
    from twicc.providers.codex.rollout_migration import _begin_replace_codex_history
    return _begin_replace_codex_history(prepared_history_job(job))


def drain_aggregates():
    """Drain ready durable obligations; reject uncommitted caller transactions."""
    from django.db import connection
    from twicc.providers import compute_executor
    assert not connection.in_atomic_block
    rows = read_compute_repair_page(0, compute_repair_upper_id(), limit=10000)
    coordinator = db_writer._write_coordinator
    own_executor = compute_executor._executor is None
    if own_executor:
        start_compute_executor()
    db_writer._write_coordinator = coordinator or WriterCoordinator()
    async def run():
        scheduler = TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            for _ in range(2000):
                progressed = await scheduler.advance(lambda: frozenset())
                if not scheduler._snapshots:
                    return
                if not progressed and scheduler._future is None and scheduler._active is None:
                    return
                await asyncio.sleep(.001)
            raise AssertionError('Aggregate continuation does not become idle')
        finally:
            await scheduler.close()
            if own_executor:
                await stop_compute_executor()
    try:
        asyncio.run(run())
    finally:
        db_writer._write_coordinator = coordinator


def publish_session_values(session):
    from django.db import transaction
    from twicc.providers.tree_aggregate_prepare import AggregatePreparationExecutor, prepare_session_values, publish_aggregate
    from twicc.providers.tree_aggregate_state import observe_tree_work
    work = observe_tree_work(session.id, frozenset())
    assert work is not None
    with AggregatePreparationExecutor() as executor:
        prepared = executor.submit(prepare_session_values, work, session.id).result()
    with transaction.atomic():
        assert publish_aggregate(work, prepared, frozenset()) == 'published'


def publish_project_values(project_id):
    from django.db import transaction
    from twicc.providers.tree_aggregate_prepare import AggregatePreparationExecutor, prepare_project_values, publish_aggregate
    from twicc.providers.tree_aggregate_state import observe_orphan_work
    from twicc.providers.compute_repair_journal import load_compute_repair
    snapshot = load_compute_repair(f'project:{project_id}', 'project')
    assert snapshot is not None
    work = observe_orphan_work(snapshot)
    with AggregatePreparationExecutor() as executor:
        prepared = executor.submit(prepare_project_values, work, project_id).result()
    with transaction.atomic():
        assert publish_aggregate(work, prepared, frozenset()) == 'published'
