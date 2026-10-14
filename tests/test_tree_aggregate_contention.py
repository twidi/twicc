"""Contention and proof reuse at real SQL and committed contribution boundaries."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import threading

import pytest
from django.db import connections, transaction

from tests.test_compute_continuation import wal_runtime  # noqa: F401
from tests.test_tree_aggregate_runtime import node, snapshots, coordinator  # noqa: F401
from twicc.core.models import ComputeAggregateRepair, Session, SessionItem
from twicc.providers import tree_aggregate_runtime as runtime
from twicc.providers.tree_aggregate_state import observe_tree_work

pytestmark = pytest.mark.django_db(transaction=True)


def test_completed_shared_tree_does_not_repeat_historical_date_reads():
    """An unchanged shared proof must avoid another timestamp history scan."""
    root = node('reuse-root')
    child = node('reuse-child', root)
    SessionItem.objects.update(timestamp=datetime(2026, 10, 1, tzinfo=UTC))
    rows = snapshots()
    work = observe_tree_work(root.id, frozenset(), candidate_repairs=rows)
    scans = []
    def capture(execute, sql, params, many, context):
        if 'core_sessionitem' in sql.lower():
            scans.append(sql)
        return execute(sql, params, many, context)
    with connections['default'].execute_wrapper(capture):
        required, pending = runtime._observe(next(row for row in rows if row.observation.session_id == child.id),
            {work.observation.root: work}, frozenset(), rows)
    assert required == {work.observation.root}
    assert pending == []
    assert scans == []


def test_tree_guard_reads_journals_in_batches():
    """A shared tree must not issue one journal SELECT per native member."""
    from twicc.providers.tree_aggregate_state import tree_work_matches
    root = node('batched-guard-root')
    for index in range(8):
        node(f'batched-guard-child-{index}', root)
    work = observe_tree_work(root.id, frozenset(), candidate_repairs=snapshots())
    queries = []
    def capture(execute, sql, params, many, context):
        if 'core_computeaggregaterepair' in sql.lower():
            queries.append(sql)
        return execute(sql, params, many, context)
    with connections['default'].execute_wrapper(capture):
        assert tree_work_matches(work, frozenset())
    assert len(queries) <= 2


@pytest.mark.parametrize('mutation', ['source', 'late_scope', 'pending'])
def test_completed_proof_cannot_hide_changed_or_pending_work(mutation):
    from twicc.providers.compute_repair_journal import merge_compute_repair
    from twicc.providers.contribution_revisions import record_contribution_commit, SourceKey
    root = node('fenced-root')
    rows = snapshots()
    work = observe_tree_work(root.id, frozenset(), candidate_repairs=rows)
    pending = frozenset()
    if mutation == 'source':
        with transaction.atomic():
            SessionItem.objects.create(session=root, line_num=2, cost=Decimal(3))
            record_contribution_commit(sources=[SourceKey(root.id, root.storage_incarnation)])
    elif mutation == 'late_scope':
        with transaction.atomic():
            merge_compute_repair('deleted', 'old', parent_ids=[root.id], project_ids=['old-project'], source_changed=True)
        rows = snapshots()
    else:
        pending = frozenset([root.id])
    required, jobs = runtime._observe(rows[0], {work.observation.root: work}, pending, rows)
    if mutation == 'pending':
        assert required == {('deferred', root.id)}
        assert jobs == []
    else:
        assert len(jobs) == 1
        if mutation == 'late_scope':
            assert 'old-project' in jobs[0][1].project_ids


def test_blocked_sql_snapshot_allows_foreground_append_and_rejects_stale_sum(wal_runtime, monkeypatch):  # noqa: F811
    """A real WAL read stays open while a foreground contribution commits."""
    from twicc.providers import compute_executor, db_writer
    from twicc.providers.compute_repair_journal import merge_compute_repair
    from twicc.providers.helpers import get_provider_helpers
    from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution
    from twicc.providers.tree_aggregate_prepare import AggregatePreparationExecutor, ChangedPreparation, prepare_session_values
    session, path = wal_runtime
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(compute_executor, 'run_compute_sync', db_writer.run_compute_sync)
    def configure():
        db = connections['default']
        db.settings_dict = {**db.settings_dict, 'NAME': str(path)}
        return db
    def initialize():
        Session.objects.filter(pk=session.id).update(compute_version=get_provider_helpers('codex').current_compute_version)
        SessionItem.objects.filter(session_id=session.id, line_num=1).update(cost=Decimal(2))
        with transaction.atomic():
            merge_compute_repair(session.id, session.storage_incarnation, source_changed=True)
        return observe_tree_work(session.id, frozenset())
    def foreground():
        row = Session.objects.get(pk=session.id)
        before = session_contribution(row)
        with transaction.atomic():
            item = SessionItem.objects.create(session=row, line_num=601, cost=Decimal(3))
            apply_contribution_changes([], item_contributions(SessionItem.objects.filter(pk=item.pk)),
                before_sessions=[before], after_sessions=[before], repair=False)
    def blocked(work):
        db = configure()
        def barrier(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if 'SUM(' in sql.upper() and 'core_sessionitem' in sql.lower():
                started.set()
                assert release.wait(10)
            return result
        with db.execute_wrapper(barrier):
            return prepare_session_values(work, session.id)
    async def run():
        executor = AggregatePreparationExecutor()
        try:
            work = await db_writer.run_compute_sync(initialize)
            future = executor.submit(blocked, work)
            assert await asyncio.to_thread(started.wait, 5)
            await asyncio.wait_for(db_writer.run_compute_sync(foreground), 5)
            assert not release.is_set()
            release.set()
            with pytest.raises(ChangedPreparation):
                await asyncio.wrap_future(future)
            scheduler = runtime.TreeAggregateScheduler(inventory_ready=lambda: True)
            submit = scheduler._executor.submit
            def configured(function, /, *args, **kwargs):
                def call():
                    configure()
                    return function(*args, **kwargs)
                return submit(call)
            monkeypatch.setattr(scheduler._executor, 'submit', configured)
            scheduler.discover(await db_writer.run_compute_sync(snapshots))
            try:
                async with asyncio.timeout(10):
                    while scheduler._snapshots:
                        await scheduler.advance(lambda: frozenset())
                        await asyncio.sleep(.001)
            finally:
                await scheduler.close()
            def verify():
                row = Session.objects.get(pk=session.id)
                assert row.total_cost == Decimal(5)
                assert SessionItem.objects.filter(session_id=session.id, line_num=601).count() == 1
                assert not ComputeAggregateRepair.objects.exists()
            await db_writer.run_compute_sync(verify)
        finally:
            release.set()
            await executor.aclose()
    asyncio.run(run())
