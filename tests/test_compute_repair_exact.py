"""Exact durable repair matches persisted source and isolates source incarnations."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import connection, transaction

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.providers import compute_repair_journal as journal
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 10, 13, tzinfo=UTC)
DAY = date(2026, 10, 13)
MONDAY = date(2026, 10, 12)


def make_session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id="p")
    defaults = {
        "storage_incarnation": name, "created_at": STAMP,
        "compute_version": get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version,
    }
    defaults.update(kwargs)
    return Session.objects.create(id=name, project=project, provider=Provider.CLAUDE_CODE,
                                  file_path=name, **defaults)


def item(session, cost, kind=ItemKind.USER_MESSAGE):
    return SessionItem.objects.create(session=session, line_num=1, content="{}", timestamp=STAMP,
                                      kind=kind, cost=cost)


def record(session_id, incarnation, *, parents=(), projects=("p", None)):
    keys = [(model, project, Provider.CLAUDE_CODE.value, day)
            for model, day in ((DailyActivity, DAY), (WeeklyActivity, MONDAY)) for project in projects]
    with transaction.atomic():
        return journal.merge_compute_repair(session_id, incarnation, activity_keys=keys,
                                             parent_ids=parents, source_changed=True)


def raw_state():
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, self_cost, subagents_cost, total_cost, user_message_count "
                       "FROM core_session ORDER BY id")
        sessions = cursor.fetchall()
        buckets = []
        for model in (DailyActivity, WeeklyActivity):
            cursor.execute(f"SELECT project_id, provider, date, cost, user_message_count, session_count "
                           f"FROM {model._meta.db_table} ORDER BY project_id, provider, date")
            buckets.append(cursor.fetchall())
    return sessions, buckets


def reference(keys, sessions):
    for session in sessions:
        session.refresh_from_db()
        session.recalculate_costs()
        session.save(update_fields=["self_cost", "subagents_cost", "total_cost"])
    for model, project, provider, day in keys:
        model.recalculate(project, day, Provider(provider))
    return raw_state()


def test_current_target_recovery_matches_independent_reference():
    from tests.tree_aggregate_helpers import drain_aggregates
    parent = make_session("parent")
    target = make_session("target", self_cost=Decimal(99))
    child = make_session("child", parent_session=target, type="subagent", hidden=True, self_cost=Decimal(99))
    sibling = make_session("sibling", parent_session=parent, type="subagent", self_cost=Decimal(88))
    item(target, Decimal("0.123456"))
    item(child, Decimal("0.234567"), ItemKind.ASSISTANT_MESSAGE)
    item(sibling, Decimal("0.7"))
    snapshot = record(target.id, target.storage_incarnation, parents=[parent.id, "missing-parent"])
    drain_aggregates()
    actual = raw_state()
    assert actual == reference(snapshot.activity_keys, [child, target, sibling, parent])
    target.refresh_from_db()
    parent.refresh_from_db()
    sibling.refresh_from_db()
    assert target.user_message_count == 1
    assert parent.user_message_count == 0
    assert sibling.self_cost == Decimal("0.7")
    assert DailyActivity.objects.get(project_id=None).user_message_count == 1
    assert journal.load_compute_repair(target.id, target.storage_incarnation) is None


def test_old_incarnation_recovery_never_changes_replacement_caches():
    from tests.tree_aggregate_helpers import drain_aggregates
    target = make_session("reused", storage_incarnation="new", self_cost=Decimal(99), user_message_count=9)
    item(target, Decimal(2))
    record(target.id, "old")
    before = raw_state()[0]
    drain_aggregates()
    assert raw_state()[0] == before
    assert journal.load_compute_repair(target.id, "old") is None


def test_orphan_repairs_global_and_retained_parent_without_project_fk():
    from tests.tree_aggregate_helpers import drain_aggregates
    parent = make_session("parent", subagents_cost=Decimal(99))
    child = make_session("child", parent_session=parent, type="subagent", hidden=True)
    item(child, Decimal("0.3"))
    snapshot = record("gone", "old", parents=[parent.id, "missing"], projects=("obsolete", None))
    drain_aggregates()
    parent.refresh_from_db()
    assert parent.subagents_cost == Decimal("0.3")
    assert not DailyActivity.objects.filter(project_id="obsolete").exists()
    assert DailyActivity.objects.get(project_id=None).cost == Decimal("0.3")
    assert DailyActivity.objects.get(project_id=None).user_message_count == 0
    assert raw_state() == reference([key for key in snapshot.activity_keys if key[1] is None], [child, parent])


def test_stale_target_waits_for_metadata_compute():
    from tests.tree_aggregate_helpers import drain_aggregates
    target = make_session("stale", compute_version=1, self_cost=Decimal(99))
    item(target, Decimal(2))
    snapshot = record(target.id, target.storage_incarnation)
    before = raw_state()
    drain_aggregates()
    assert raw_state() == before
    assert journal.load_compute_repair(target.id, target.storage_incarnation) == snapshot


def test_changed_observation_does_not_settle():
    first = record("gone", "old")
    second = record("gone", "old")
    with pytest.raises(journal.RepairObservationChanged), transaction.atomic():
        journal.settle_compute_repair(first.observation)
    assert journal.load_compute_repair("gone", "old") == second


@pytest.mark.parametrize("raw,expected_self,expected_total", [(None, None, None), (0, Decimal(0), None)])
def test_recovery_preserves_nullable_and_zero_costs(raw, expected_self, expected_total):
    from tests.tree_aggregate_helpers import drain_aggregates
    target = make_session("target", self_cost=Decimal(99), total_cost=Decimal(99))
    item(target, raw)
    record(target.id, target.storage_incarnation)
    drain_aggregates()
    target.refresh_from_db()
    assert target.self_cost == expected_self
    assert target.total_cost == expected_total
    assert target.user_message_count == 1


def test_caller_owned_settlement_rejects_calls_without_transaction():
    from django.db.transaction import TransactionManagementError
    snapshot = record('gone', 'old')
    with pytest.raises(TransactionManagementError):
        journal.settle_compute_repair(snapshot.observation)
