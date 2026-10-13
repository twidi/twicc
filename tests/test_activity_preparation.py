"""Tree activity prepares outside writer ownership and survives interrupted jobs."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_tree_aggregate_runtime import coordinator, node, snapshots, turns  # noqa: F401 -- imports the shared pytest fixture
from tests.tree_aggregate_helpers import drain_aggregates
from twicc.core.models import ComputeAggregateRepair, DailyActivity, Session, SessionItem, WeeklyActivity
from twicc.providers import tree_aggregate_runtime as runtime, tree_aggregate_prepare as preparation
from twicc.providers.compute_executor import run_compute_sync
from twicc.providers.compute_repair_journal import merge_compute_repair

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 10, 13, tzinfo=UTC)


def dated_root():
    root = node('root')
    SessionItem.objects.filter(session=root).update(timestamp=STAMP)
    return root


def test_one_grouped_source_sum_per_valid_tree(monkeypatch):
    root = node('root')
    node('child', root)
    node('grandchild', Session.objects.get(pk='child'))
    original = preparation._own_values
    sums = []
    def capture(work):
        with CaptureQueriesContext(connection) as queries:
            result = original(work)
        sums.extend(query['sql'] for query in queries if 'SUM(' in query['sql'].upper())
        return result
    monkeypatch.setattr(preparation, '_own_values', capture)
    drain_aggregates()
    root.refresh_from_db()
    assert root.total_cost == Decimal(6)
    assert len(sums) == 1
    assert not ComputeAggregateRepair.objects.exists()


def test_partial_days_then_restart_finishes_weeks():
    dated_root()
    rows = snapshots()
    async def run():
        scheduler = runtime.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            for _ in range(300):
                await scheduler.advance(lambda: frozenset())
                if await run_compute_sync(lambda: DailyActivity.objects.count() == 2):
                    break
                await asyncio.sleep(.002)
            assert await run_compute_sync(lambda: WeeklyActivity.objects.count()) == 0
        finally:
            await scheduler.close()
    asyncio.run(run())
    assert ComputeAggregateRepair.objects.exists()
    drain_aggregates()
    assert WeeklyActivity.objects.get(project_id='p').cost == Decimal(2)
    assert not ComputeAggregateRepair.objects.exists()


def test_source_mutation_after_preparation_keeps_work_durable(monkeypatch):
    root = dated_root()
    rows = snapshots()
    original = runtime.publish_aggregate
    changed = False
    def publish(work, prepared, pending):
        nonlocal changed
        if not changed:
            changed = True
            merge_compute_repair(root.id, root.storage_incarnation, source_changed=True)
        return original(work, prepared, pending)
    monkeypatch.setattr(runtime, 'publish_aggregate', publish)
    async def run():
        scheduler = runtime.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    assert changed
    assert ComputeAggregateRepair.objects.exists()
    monkeypatch.setattr(runtime, 'publish_aggregate', original)
    drain_aggregates()
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)
    assert not ComputeAggregateRepair.objects.exists()


@pytest.mark.parametrize('failure_phase', ['day', 'settlement'])
def test_failed_publication_retains_source_and_restart_retries(monkeypatch, failure_phase):
    root = dated_root()
    original = runtime.publish_aggregate
    settle = runtime.settle_compute_repair
    def publish(work, prepared, pending):
        if failure_phase == prepared.kind:
            raise RuntimeError('injected aggregate failure')
        return original(work, prepared, pending)
    def settlement(observation):
        if failure_phase == 'settlement':
            raise RuntimeError('injected settlement failure')
        return settle(observation)
    monkeypatch.setattr(runtime, 'publish_aggregate', publish)
    monkeypatch.setattr(runtime, 'settle_compute_repair', settlement)
    drain_aggregates()
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)
    assert root.items.get().cost == Decimal(2)
    assert ComputeAggregateRepair.objects.exists()
    monkeypatch.setattr(runtime, 'publish_aggregate', original)
    monkeypatch.setattr(runtime, 'settle_compute_repair', settle)
    drain_aggregates()
    assert WeeklyActivity.objects.get(project_id='p').cost == Decimal(2)
    assert not ComputeAggregateRepair.objects.exists()


def test_units_commit_in_dependency_order_without_historical_writer_reads(monkeypatch):
    root = dated_root()
    child = node('child', root)
    from twicc.providers import compute_executor
    publish = runtime.publish_aggregate
    settle = runtime.settle_compute_repair
    weekly = WeeklyActivity.recalculate_from_daily
    runner = compute_executor.run_compute_sync
    order, sql = [], []
    def capture_publish(work, prepared, pending):
        order.append((prepared.kind, getattr(prepared.key, 'session_id', prepared.key)))
        return publish(work, prepared, pending)
    def capture_week(*args):
        order.append(('week', args))
        return weekly(*args)
    def capture_settle(*args):
        order.append(('settle', args))
        return settle(*args)
    async def capture_writer(function, *args, **kwargs):
        def capture():
            with CaptureQueriesContext(connection) as queries:
                result = function(*args, **kwargs)
            sql.extend(row['sql'] for row in queries)
            return result
        return await runner(capture)
    monkeypatch.setattr(runtime, 'publish_aggregate', capture_publish)
    monkeypatch.setattr(runtime, 'settle_compute_repair', capture_settle)
    monkeypatch.setattr(WeeklyActivity, 'recalculate_from_daily', capture_week)
    monkeypatch.setattr(compute_executor, 'run_compute_sync', capture_writer)
    drain_aggregates()
    assert order[:2] == [('session', child.id), ('session', root.id)]
    ranks = {'session': 0, 'day': 1, 'week': 2, 'project': 3, 'settle': 4}
    assert [ranks[kind] for kind, _ in order] == sorted(ranks[kind] for kind, _ in order)
    assert not any('CORE_SESSIONITEM' in query.upper() for query in sql)
    assert not any('SUM(' in query.upper() and 'CORE_SESSION' in query.upper() for query in sql)
    assert not ComputeAggregateRepair.objects.exists()


@pytest.mark.parametrize('boundary', ['preparation', 'publication'])
def test_late_child_prevents_stale_tree_publication(monkeypatch, boundary):
    from twicc.providers.helpers import get_provider_helpers
    root = dated_root()
    original_prepare = runtime.prepare_session_values
    original_publish = runtime.publish_aggregate
    changed = False
    def add_child():
        nonlocal changed
        changed = True
        Session.objects.create(id='late', project_id=root.project_id, provider='codex', type='subagent',
            parent_session_id=root.id, compute_version=0, storage_incarnation='late')
    def prepare(work, sid):
        result = original_prepare(work, sid)
        if boundary == 'preparation' and not changed:
            add_child()
        return result
    def publish(work, prepared, pending):
        if boundary == 'publication' and not changed:
            add_child()
        return original_publish(work, prepared, pending)
    monkeypatch.setattr(runtime, 'prepare_session_values', prepare)
    monkeypatch.setattr(runtime, 'publish_aggregate', publish)
    drain_aggregates()
    root.refresh_from_db()
    assert changed and root.total_cost is None
    assert ComputeAggregateRepair.objects.filter(session_id=root.id).exists()
    Session.objects.filter(pk='late').update(compute_version=get_provider_helpers('codex').current_compute_version)
    drain_aggregates()
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)
    assert not ComputeAggregateRepair.objects.exists()
