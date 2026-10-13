"""Runtime tests exercise committed rows and the real preparation worker."""
import asyncio
from decimal import Decimal
from importlib import import_module, util

import pytest
from django.db import transaction

from twicc.core.models import ComputeAggregateRepair, Project, Session, SessionItem
from twicc.providers.compute_repair_journal import merge_compute_repair, read_compute_repair_page
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def coordinator(monkeypatch):
    from twicc.providers import db_writer
    from twicc.providers.write_coordinator import WriterCoordinator
    monkeypatch.setattr(db_writer, '_write_coordinator', WriterCoordinator())
    from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor
    start_compute_executor()
    yield
    asyncio.run(stop_compute_executor())


def api():
    assert util.find_spec('twicc.providers.tree_aggregate_runtime'), 'tree runtime is missing'
    return import_module('twicc.providers.tree_aggregate_runtime')


def node(sid, parent=None, ready=True):
    project, _ = Project.objects.get_or_create(id='p')
    row = Session.objects.create(id=sid, project=project, file_path=f'/{sid}', provider='codex',
        storage_incarnation=sid, parent_session=parent, type='subagent' if parent else 'session',
        compute_version=get_provider_helpers('codex').current_compute_version if ready else 0)
    SessionItem.objects.create(session=row, line_num=1, cost=Decimal(2))
    with transaction.atomic():
        merge_compute_repair(row.id, row.storage_incarnation, project_ids=[project.id], source_changed=True)
    return row


def snapshots():
    return read_compute_repair_page(0, 1000000, limit=64)


async def turns(scheduler, pending=lambda: frozenset(), count=150):
    for _ in range(count):
        await scheduler.advance(pending)
        await asyncio.sleep(.002)


def test_incomplete_child_blocks_both_normal_and_recovery_activity():
    module = api()
    root = node('root')
    node('child', root, ready=False)
    rows = snapshots()
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.note_source_applied(root.id)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.count() == 2


def test_failed_inventory_prevents_publication():
    module = api()
    root = node('root')
    rows = snapshots()
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: False)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.exists()


def test_complete_tree_publishes_and_settles():
    module = api()
    root = node('root')
    node('child', root)
    rows = snapshots()
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost == Decimal(4)
    assert Project.objects.get(pk='p').total_cost == Decimal(4)
    assert not ComputeAggregateRepair.objects.exists()


def test_source_ack_does_not_wait_for_tree():
    from twicc.providers.compute_base import BaseSessionCompute
    root = node('root', ready=False)
    node('child', root, ready=False)
    result = BaseSessionCompute.apply_session_complete({'session_id': root.id, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers('codex').current_compute_version}})
    assert result.outcome == 'applied'
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.filter(session_id=root.id).exists()


def test_pending_application_appearing_during_admission_defers_publication(monkeypatch):
    module = api()
    from twicc.providers import db_writer
    root = node('root')
    rows = snapshots()
    pending = set()
    original = db_writer._background_turn
    async def admit(factory, **kwargs):
        pending.add(root.id)
        return await original(factory, **kwargs)
    monkeypatch.setattr(db_writer, '_background_turn', admit)
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            await turns(scheduler, lambda: frozenset(pending))
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.exists()


def test_other_root_continues_when_first_tree_has_incomplete_child():
    module = api()
    blocked = node('blocked')
    node('child', blocked, ready=False)
    ready = node('ready')
    rows = snapshots()
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    blocked.refresh_from_db()
    ready.refresh_from_db()
    assert blocked.total_cost is None
    assert ready.total_cost == Decimal(2)
    assert ComputeAggregateRepair.objects.filter(session_id=blocked.id).exists()
    assert not ComputeAggregateRepair.objects.filter(session_id=ready.id).exists()


def test_current_tree_publishes_while_former_tree_waits():
    module = api()
    former = node('former', ready=False)
    root = node('root')
    with transaction.atomic():
        merge_compute_repair(root.id, root.storage_incarnation, parent_ids=[former.id], source_changed=True)
    rows = snapshots()
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost == Decimal(2)
    assert ComputeAggregateRepair.objects.filter(session_id=root.id).exists()


def test_source_finalization_retains_null_timestamp_project_obligation():
    from twicc.providers.compute_base import BaseSessionCompute
    from twicc.providers.compute_repair_journal import load_compute_repair
    root = node('root', ready=False)
    ComputeAggregateRepair.objects.all().delete()
    BaseSessionCompute.apply_session_complete({'session_id': root.id, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers('codex').current_compute_version}})
    root.refresh_from_db()
    snapshot = load_compute_repair(root.id, root.storage_incarnation)
    assert snapshot.project_ids == frozenset({'p'})


def test_claude_inventory_has_explicit_unsuccessful_initial_state():
    from twicc.providers.claude_code.orchestrator import ClaudeCodeOrchestrator
    orchestrator = ClaudeCodeOrchestrator()
    assert getattr(orchestrator, 'initial_sync_succeeded', None) is False


def test_source_finalization_marks_own_baseline_pending():
    from twicc.providers.compute_base import BaseSessionCompute
    root = node('root', ready=False)
    BaseSessionCompute.apply_session_complete({'session_id': root.id, 'history_facts': [],
        'session_fields': {'compute_version': get_provider_helpers('codex').current_compute_version}})
    journal = ComputeAggregateRepair.objects.get(session_id=root.id)
    assert ['baseline_pending'] in journal.activity_keys


def test_queued_source_is_pending_before_application(monkeypatch):
    from twicc.providers import db_writer
    assert hasattr(db_writer, 'ComputeIngressQueue'), 'accepted source registry is missing'
    ingress = db_writer.ComputeIngressQueue(maxsize=2)
    monkeypatch.setattr(db_writer, '_subprocess_queue', ingress)
    ingress.put_nowait({'type': 'session_complete', 'session_id': 'queued', 'run_id': 12})
    message = ingress.get_nowait()
    assert 'queued' in db_writer._pending_repair_exclusions()
    db_writer._release_source_application(message)
    assert 'queued' not in db_writer._pending_repair_exclusions()


def test_incomplete_live_source_records_work_without_historical_sum():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.providers.live_aggregates import apply_contribution_changes, session_contribution
    root = node('root', ready=False)
    state = session_contribution(root)
    with transaction.atomic(), CaptureQueriesContext(connection) as queries:
        apply_contribution_changes([], [], before_sessions=[state], after_sessions=[state], repair=True)
    assert not any('SUM(' in row['sql'].upper() for row in queries)
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.filter(session_id=root.id).exists()


@pytest.mark.parametrize('invalid', [False, True])
def test_live_baseline_marker_distinguishes_immediate_delta(invalid):
    from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution
    from twicc.providers.compute_repair_journal import own_baseline_pending
    from tests.tree_aggregate_helpers import drain_aggregates
    root = node('root')
    root.self_cost = root.total_cost = Decimal(99) if invalid else Decimal(2)
    root.save(update_fields=['self_cost', 'total_cost'])
    if invalid:
        with transaction.atomic():
            merge_compute_repair(root.id, root.storage_incarnation, source_changed=True, baseline_pending=True)
    before = session_contribution(root)
    with transaction.atomic():
        row = SessionItem.objects.create(session=root, line_num=2, cost=Decimal(3))
        apply_contribution_changes([], item_contributions(SessionItem.objects.filter(pk=row.pk)),
            before_sessions=[before], after_sessions=[before], repair=False)
    root.refresh_from_db()
    assert root.total_cost == (Decimal(99) if invalid else Decimal(5))
    assert own_baseline_pending(root.id, root.storage_incarnation) == invalid
    drain_aggregates()
    root.refresh_from_db()
    assert root.total_cost == Decimal(5)
    assert not own_baseline_pending(root.id, root.storage_incarnation)


def test_later_external_journal_requires_its_new_scope(monkeypatch):
    from datetime import UTC, datetime
    from twicc.core.models import DailyActivity
    from twicc.providers.live_aggregates import _activity_keys
    module = api()
    former = node('former')
    old_project = Project.objects.create(id='old-scope', total_cost=99)
    stamp = datetime(2026, 10, 1, tzinfo=UTC)
    DailyActivity.objects.create(project=old_project, provider='codex', date=stamp.date(), cost=99)
    with transaction.atomic():
        late = merge_compute_repair('deleted', 'old', parent_ids=[former.id], project_ids=[old_project.id],
            activity_keys=set(_activity_keys(old_project.id, 'codex', stamp)), source_changed=True)
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.providers import tree_aggregate_prepare
    own_values = tree_aggregate_prepare._own_values
    sums = []
    def capture_own(work):
        with CaptureQueriesContext(connection) as queries:
            result = own_values(work)
        sums.extend(row['sql'] for row in queries if 'SUM(' in row['sql'].upper())
        return result
    monkeypatch.setattr(tree_aggregate_prepare, '_own_values', capture_own)
    publications = []
    publish = module.publish_aggregate
    def capture(work, prepared, pending):
        publications.append((work, prepared))
        return publish(work, prepared, pending)
    monkeypatch.setattr(module, 'publish_aggregate', capture)
    rows = tuple(row for row in snapshots() if row.observation.session_id != 'deleted')
    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        try:
            # Keep the first tree completed but stop before its settlement.
            for _ in range(300):
                await scheduler.advance(lambda: frozenset())
                if scheduler._completed:
                    break
                await asyncio.sleep(.002)
            assert scheduler._completed
            scheduler.discover((late,))
            await turns(scheduler, count=300)
        finally:
            await scheduler.close()
    asyncio.run(run())
    assert not DailyActivity.objects.filter(project=old_project).exists()
    old_project.refresh_from_db()
    assert old_project.total_cost is None
    assert not ComputeAggregateRepair.objects.exists()
    assert any(work.observation.root and work.observation.root.session_id == former.id
        and late in work.observation.repairs and prepared.kind == 'project'
        and prepared.key == old_project.id for work, prepared in publications)
    assert len(sums) == 1


def test_many_shared_journals_settle_one_per_admission_without_republication(monkeypatch):
    """Foreground demand queued during settlement runs before the next journal."""
    from threading import Event
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from twicc.providers import db_writer, tree_aggregate_prepare
    from twicc.providers.write_coordinator import WriteResources

    module = api()
    root = node('many-root')
    for index in range(19):
        node(f'many-child-{index}', root)
    rows = snapshots()
    started, release = Event(), Event()
    settled, admissions, sums, publications = [], [], [], []
    original_settle = module.settle_compute_repair
    original_turn = db_writer._background_turn
    original_own = tree_aggregate_prepare._own_values
    original_publish = module.publish_aggregate

    def settle(observation):
        original_settle(observation)
        settled.append(observation.journal_id)
        if len(settled) == 1:
            started.set()
            assert release.wait(5)

    async def turn(callback, **kwargs):
        before = len(settled)
        result = await original_turn(callback, **kwargs)
        admissions.append(len(settled) - before)
        return result

    def own(work):
        with CaptureQueriesContext(connection) as queries:
            result = original_own(work)
        sums.extend(row['sql'] for row in queries if 'SUM(' in row['sql'].upper())
        return result

    def publish(work, prepared, pending):
        publications.append((prepared.kind, prepared.key))
        return original_publish(work, prepared, pending)

    monkeypatch.setattr(module, 'settle_compute_repair', settle)
    monkeypatch.setattr(db_writer, '_background_turn', turn)
    monkeypatch.setattr(tree_aggregate_prepare, '_own_values', own)
    monkeypatch.setattr(module, 'publish_aggregate', publish)

    async def run():
        scheduler = module.TreeAggregateScheduler(inventory_ready=lambda: True)
        scheduler.discover(rows)
        foreground_counts = []

        async def foreground():
            for _ in range(3000):
                if started.is_set():
                    break
                await asyncio.sleep(.001)
            assert started.is_set()
            coordinator = db_writer._write_coordinator
            demand = coordinator.foreground_demand()
            release.set()
            try:
                async with coordinator.foreground_lock(WriteResources.WRITER):
                    foreground_counts.append(len(settled))
            finally:
                demand.release()

        task = asyncio.create_task(foreground())
        try:
            for _ in range(3000):
                await scheduler.advance(lambda: frozenset())
                if not scheduler._snapshots:
                    break
                await asyncio.sleep(.001)
            await task
            assert not scheduler._snapshots
        finally:
            release.set()
            await scheduler.close()
            await task
        assert foreground_counts == [1]

    asyncio.run(run())
    assert max(admissions) == 1
    assert len(settled) == len(rows) == 20
    assert len(sums) == 1
    assert sum(kind == 'session' for kind, _ in publications) == 20
    assert not ComputeAggregateRepair.objects.exists()
    root.refresh_from_db()
    assert root.total_cost == Decimal(40)
