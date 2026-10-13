"""Exact durable repair matches persisted source and isolates source incarnations."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db import connection, transaction

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.providers import compute_repair_journal as journal
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db
STAMP = datetime(2026, 10, 13, tzinfo=UTC)
DAY = date(2026, 10, 13)
MONDAY = date(2026, 10, 12)


def repair_module():
    from twicc.providers import compute_repair
    return compute_repair


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
    parent = make_session("parent", user_message_count=19, compute_version=1)
    target = make_session("target", parent_session=parent, self_cost=Decimal(99))
    child = make_session("child", parent_session=target, type="subagent", hidden=True, self_cost=Decimal(99))
    sibling = make_session("sibling", parent_session=parent, type="subagent", self_cost=Decimal(88))
    item(target, 0)
    item(child, 0, ItemKind.ASSISTANT_MESSAGE)
    item(sibling, Decimal("0.7"))
    # Bypass DecimalField scale to retain a legacy SQLite fractional cost.
    with connection.cursor() as cursor:
        cursor.execute("UPDATE core_sessionitem SET cost = 0.123456789 WHERE session_id = %s", [target.id])
        cursor.execute("UPDATE core_sessionitem SET cost = 0.234567891 WHERE session_id = %s", [child.id])
    snapshot = record(target.id, target.storage_incarnation, parents=[parent.id, "missing-parent"])
    assert repair_module().repair_compute_observation(snapshot.observation) == "repaired"
    actual = raw_state()
    assert actual == reference(snapshot.activity_keys, [target, child, parent])
    target.refresh_from_db()
    parent.refresh_from_db()
    sibling.refresh_from_db()
    assert target.user_message_count == 1
    assert parent.user_message_count == 19
    assert sibling.self_cost == Decimal(88)
    assert DailyActivity.objects.get(project_id=None).user_message_count == 1
    assert DailyActivity.objects.get(project_id=None).session_count == 2
    assert journal.load_compute_repair(target.id, target.storage_incarnation) is None
    with connection.cursor() as cursor:
        cursor.execute("SELECT SUM(total_cost) FROM core_session WHERE id = %s", [target.id])
        assert cursor.fetchone()[0] == pytest.approx(0.35802468)


def test_old_incarnation_recovery_never_changes_replacement_caches():
    target = make_session("reused", storage_incarnation="new", self_cost=Decimal(99), user_message_count=9)
    item(target, Decimal(2))
    old = record(target.id, "old")
    new = record(target.id, "new")
    before = raw_state()[0]
    assert repair_module().repair_compute_observation(old.observation) == "repaired"
    assert raw_state()[0] == before
    assert journal.load_compute_repair(target.id, "new") == new
    assert journal.load_compute_repair(target.id, "old") is None


def test_orphan_repairs_global_and_retained_parent_without_project_fk():
    parent = make_session("parent", subagents_cost=Decimal(99))
    child = make_session("child", parent_session=parent, type="subagent", hidden=True)
    item(child, Decimal("0.3"))
    obsolete = Project.objects.create(id="obsolete")
    DailyActivity.objects.create(project=obsolete, date=DAY, provider=Provider.CLAUDE_CODE, cost=9)
    obsolete.delete()
    snapshot = record("gone", "old", parents=[parent.id, "missing"], projects=("obsolete", None))
    assert repair_module().repair_compute_observation(snapshot.observation) == "repaired"
    parent.refresh_from_db()
    assert parent.subagents_cost == Decimal("0.3")
    assert not DailyActivity.objects.filter(project_id="obsolete").exists()
    assert DailyActivity.objects.get(project_id=None).cost == Decimal("0.3")
    assert DailyActivity.objects.get(project_id=None).user_message_count == 0
    assert raw_state() == reference([key for key in snapshot.activity_keys if key[1] is None], [parent])


def test_stale_target_waits_for_metadata_compute():
    target = make_session("stale", compute_version=1, self_cost=Decimal(99))
    item(target, Decimal(2))
    snapshot = record(target.id, target.storage_incarnation)
    before = raw_state()
    assert repair_module().repair_compute_observation(snapshot.observation) == "stale_target"
    assert raw_state() == before
    assert journal.load_compute_repair(target.id, target.storage_incarnation) == snapshot


def test_changed_observation_does_not_repair_or_settle():
    first = record("gone", "old")
    second = record("gone", "old")
    assert repair_module().repair_compute_observation(first.observation) == "changed"
    assert journal.load_compute_repair("gone", "old") == second


@pytest.mark.parametrize("failure", ["bucket", "settlement"])
def test_failure_rolls_back_caches_buckets_and_journal(monkeypatch, failure):
    target = make_session("target", self_cost=Decimal(99))
    item(target, Decimal(2))
    snapshot = record(target.id, target.storage_incarnation)
    module = repair_module()
    before = raw_state()

    def fail(*args):
        raise journal.RepairObservationChanged("injected failure")

    monkeypatch.setattr(module, "repair_activity_key" if failure == "bucket" else "settle_compute_repair", fail)
    if failure == "bucket":
        with pytest.raises(journal.RepairObservationChanged):
            module.repair_compute_observation(snapshot.observation)
    else:
        assert module.repair_compute_observation(snapshot.observation) == "changed"
    assert raw_state() == before
    assert journal.load_compute_repair(target.id, target.storage_incarnation) == snapshot


def test_recovery_acquires_writer_before_reading_snapshot():
    snapshot = record("gone", "old")
    statements = []

    def capture(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        assert repair_module().repair_compute_observation(snapshot.observation) == "repaired"
    sql = next(sql for sql in statements if not sql.startswith(("SAVEPOINT", "BEGIN")))
    assert sql.startswith('UPDATE "core_computeaggregaterepair"')


def test_deleted_repairs_settle_complete_inventory_atomically():
    parent = make_session("parent", total_cost=Decimal(99), user_message_count=8)
    snapshot = record("gone", "old", parents=[parent.id])
    DailyActivity.objects.create(project_id="p", date=DAY, provider=Provider.CLAUDE_CODE, cost=99)
    with transaction.atomic():
        repair_module().settle_deleted_repairs([snapshot])
    parent.refresh_from_db()
    assert parent.total_cost is None
    assert parent.user_message_count == 8
    assert DailyActivity.objects.get(project_id="p").cost == 0
    assert DailyActivity.objects.get(project_id="p").session_count == 1
    assert journal.load_compute_repair("gone", "old") is None


@pytest.mark.parametrize("raw,expected_self,expected_total", [(None, None, None), (0, Decimal(0), None)])
def test_recovery_preserves_nullable_and_zero_costs(raw, expected_self, expected_total):
    target = make_session("target", self_cost=Decimal(99), total_cost=Decimal(99))
    item(target, raw)
    snapshot = record(target.id, target.storage_incarnation)
    assert repair_module().repair_compute_observation(snapshot.observation) == "repaired"
    target.refresh_from_db()
    assert target.self_cost == expected_self
    assert target.total_cost == expected_total
    assert target.user_message_count == 1


def test_deleted_inventory_change_rolls_back_whole_deletion_transaction():
    parent = make_session("parent", total_cost=Decimal(99))
    first = record("gone", "old", parents=[parent.id])
    changed = record("gone", "old", parents=[parent.id])
    with pytest.raises(journal.RepairObservationChanged), transaction.atomic():
        parent.delete()
        repair_module().settle_deleted_repairs([first])
    assert Session.objects.get(pk="parent").total_cost == Decimal(99)
    assert journal.load_compute_repair("gone", "old") == changed


@pytest.mark.django_db(transaction=True)
def test_caller_owned_helpers_reject_calls_without_transaction():
    from django.db.transaction import TransactionManagementError
    module = repair_module()
    for operation in (lambda: module.repair_retained_parents([]),
                      lambda: module.repair_activity_key((DailyActivity, None, "claude_code", DAY)),
                      lambda: module.settle_deleted_repairs([])):
        with pytest.raises(TransactionManagementError):
            operation()
