"""Fresh admission and transaction fences replace final-source repair fallbacks."""
import asyncio
from datetime import UTC, datetime

import pytest
from django.db import transaction

from tests.test_tree_aggregate_runtime import coordinator, node, snapshots, turns  # noqa: F401 -- imports the shared pytest fixture
from twicc.core.models import ComputeAggregateRepair, SessionItem
from twicc.core.services.session_visibility import _apply_visibility_flag
from twicc.providers import db_writer
from twicc.providers.contribution_revisions import DailyKey
from twicc.providers.tree_aggregate_prepare import AggregatePreparationExecutor, prepare_daily_values, publish_aggregate
from twicc.providers.tree_aggregate_runtime import TreeAggregateScheduler
from twicc.providers.tree_aggregate_state import observe_tree_work

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 10, 13, tzinfo=UTC)


def test_failed_inventory_after_admission_prevents_publication(monkeypatch):
    root = node('root')
    rows = snapshots()
    ready = [True]
    original = db_writer._background_turn
    async def grant(factory, **kwargs):
        ready[0] = False
        return await original(factory, **kwargs)
    monkeypatch.setattr(db_writer, '_background_turn', grant)
    async def run():
        scheduler = TreeAggregateScheduler(inventory_ready=lambda: ready[0])
        scheduler.discover(rows)
        try:
            await turns(scheduler)
        finally:
            await scheduler.close()
    asyncio.run(run())
    root.refresh_from_db()
    assert root.total_cost is None
    assert ComputeAggregateRepair.objects.exists()


@pytest.mark.parametrize('rollback', [False, True])
def test_cross_tree_visibility_change_fences_prepared_day(rollback):
    root = node('prepared')
    other = node('changed')
    SessionItem.objects.filter(session__in=[root, other]).update(timestamp=STAMP, kind='user_message')
    work = observe_tree_work(root.id, frozenset())
    key = DailyKey(None, 'codex', STAMP.date())
    with AggregatePreparationExecutor() as executor:
        prepared = executor.submit(prepare_daily_values, work, key).result()
    with transaction.atomic():
        _apply_visibility_flag(other.id, True)
        if rollback:
            transaction.set_rollback(True)
    with transaction.atomic():
        assert publish_aggregate(work, prepared, frozenset()) == ('published' if rollback else 'changed')


def test_deletion_rejects_source_change_after_date_inventory():
    from twicc.providers.db_writer import _prepare_delete_payload, DeleteSessionsPayload, _apply_delete_sessions_payload
    from twicc.providers.live_aggregates import apply_contribution_changes, item_contributions, session_contribution
    root = node('root')
    payload = _prepare_delete_payload(DeleteSessionsPayload('codex', [root.id]))
    before = session_contribution(root)
    with transaction.atomic():
        item = SessionItem.objects.create(session=root, line_num=2, timestamp=STAMP, cost=1)
        apply_contribution_changes([], item_contributions(SessionItem.objects.filter(pk=item.pk)),
            before_sessions=[before], after_sessions=[before], repair=False)
    with pytest.raises(RuntimeError, match='Source changed'):
        _apply_delete_sessions_payload(payload)
    assert root.items.count() == 2
    assert ComputeAggregateRepair.objects.filter(session_id=root.id).exists()


def test_deletion_rejects_late_child_membership():
    from twicc.providers.db_writer import _prepare_delete_payload, DeleteSessionsPayload, _apply_delete_sessions_payload
    root = node('root')
    payload = _prepare_delete_payload(DeleteSessionsPayload('codex', [root.id]))
    child = node('child', root)
    with pytest.raises(RuntimeError, match='membership changed'):
        _apply_delete_sessions_payload(payload)
    child.refresh_from_db()
    assert child.parent_session_id == root.id
