"""Durable obligation unions, caller transactions, and settlement identity fences."""

import io
from datetime import date, datetime, timedelta
from importlib import import_module, util

import pytest
from django.core.management import call_command
from django.db import transaction
from django.db.transaction import TransactionManagementError

from twicc.core.models import DailyActivity, Project, Session, WeeklyActivity
from twicc.providers.compute_repair_journal import merge_compute_repair, load_compute_repair

pytestmark = pytest.mark.django_db(transaction=True)
DAY = date(2026, 10, 13)
WEEK = date(2026, 10, 12)
KEY = (DailyActivity, None, "codex", DAY)


def api():
    assert util.find_spec("twicc.providers.compute_repair_journal") is not None, "journal API is missing"
    return import_module("twicc.providers.compute_repair_journal")


def merge(journal, **kwargs):
    with transaction.atomic():
        return journal.merge_compute_repair("source", "incarnation", **kwargs)


def test_same_day_source_change_retains_union_and_advances_generation():
    journal = api()
    first = merge(journal, activity_keys=[KEY], parent_ids=["old"], source_changed=True)
    second = merge(journal, activity_keys=[KEY, KEY], parent_ids=["new"], source_changed=True)
    assert first.observation.generation == 1
    assert second.activity_keys == frozenset([KEY])
    assert second.parent_ids == frozenset(["old", "new"])
    assert second.observation.generation == 2
    third = merge(journal, source_changed=True)
    assert third.observation.generation == 3
    assert third.activity_keys == second.activity_keys


def test_final_noop_retains_observation_but_new_obligation_advances_it():
    journal = api()
    first = merge(journal, activity_keys=[KEY], source_changed=True)
    same = merge(journal, activity_keys=[KEY, KEY], source_changed=False)
    assert same == first
    changed = merge(journal, parent_ids=["parent"], source_changed=False)
    assert changed.observation.generation == 2


def test_empty_inventory_still_retains_source_evidence():
    journal = api()
    empty = merge(journal, source_changed=True)
    assert empty.observation.generation == 1
    assert empty.activity_keys == frozenset()
    final = merge(journal, source_changed=False)
    assert final == empty


def test_create_false_does_not_create_but_updates_existing_journal():
    journal = api()
    assert merge(journal, source_changed=True, create=False) is None
    first = merge(journal, source_changed=False)
    assert first.observation.generation == 0
    second = merge(journal, activity_keys=[KEY], source_changed=False, create=False)
    assert second.observation.generation == 1


def test_stale_generation_cannot_require_or_settle_enlarged_obligations():
    journal = api()
    first = merge(journal, activity_keys=[KEY], source_changed=True)
    newer = merge(journal, source_changed=True)
    with transaction.atomic():
        with pytest.raises(journal.RepairObservationChanged):
            journal.require_compute_repair(first.observation)
        with pytest.raises(journal.RepairObservationChanged):
            journal.settle_compute_repair(first.observation)
        assert journal.require_compute_repair(newer.observation) == newer
    assert journal.load_compute_repair("source", "incarnation") == newer


def test_recreated_row_and_other_incarnation_survive_old_settlement():
    journal = api()
    first = merge(journal, source_changed=True)
    with transaction.atomic():
        journal.settle_compute_repair(first.observation)
    assert journal.load_compute_repair("source", "incarnation") is None
    recreated = merge(journal, source_changed=True)
    with transaction.atomic():
        replacement = journal.merge_compute_repair("source", "replacement", source_changed=True)
        with pytest.raises(journal.RepairObservationChanged):
            journal.settle_compute_repair(first.observation)
    assert recreated.observation.journal_id != first.observation.journal_id
    assert journal.load_session_repairs(["source", "source"]) == (recreated, replacement)
    with transaction.atomic():
        forged = recreated.observation._replace(incarnation="replacement")
        with pytest.raises(journal.RepairObservationChanged):
            journal.require_compute_repair(forged)


def test_source_rollback_restores_source_and_journal():
    journal = api()
    project = Project.objects.create(id="project")
    session = Session.objects.create(id="source", project=project, provider="codex")
    first = merge(journal, source_changed=True)
    with pytest.raises(ValueError, match="rollback"), transaction.atomic():
        Session.objects.filter(pk=session.pk).update(hidden=True)
        journal.merge_compute_repair("source", "incarnation", activity_keys=[KEY], source_changed=True)
        raise ValueError("rollback")
    session.refresh_from_db()
    assert session.hidden is False
    assert journal.load_compute_repair("source", "incarnation") == first
    with pytest.raises(ValueError, match="rollback"), transaction.atomic():
        journal.merge_compute_repair("new", "incarnation", source_changed=True)
        raise ValueError("rollback")
    assert journal.load_compute_repair("new", "incarnation") is None


def test_orphan_inventory_exceeds_preparation_capacity_without_foreign_keys():
    journal = api()
    keys = [(DailyActivity, None, "codex", DAY + timedelta(days=i)) for i in range(4097)]
    snapshot = merge(journal, activity_keys=keys, parent_ids=["missing-parent"], source_changed=True)
    assert len(snapshot.activity_keys) == 4097
    assert journal.load_compute_repair("source", "incarnation") == snapshot
    model = import_module("twicc.core.models").ComputeAggregateRepair
    assert not any(field.is_relation for field in model._meta.fields)


def test_serialized_union_round_trips_with_deterministic_order():
    journal = api()
    keys = [KEY, (WeeklyActivity, "project", "claude_code", WEEK),
            (DailyActivity, "project", "claude_code", DAY), KEY]
    snapshot = merge(journal, activity_keys=reversed(keys), parent_ids=["z", "a", "z"], source_changed=True)
    model = import_module("twicc.core.models").ComputeAggregateRepair
    row = model.objects.get(pk=snapshot.observation.journal_id)
    assert row.activity_keys == [["day", None, "codex", "2026-10-13"],
                                 ["day", "project", "claude_code", "2026-10-13"],
                                 ["week", "project", "claude_code", "2026-10-12"]]
    assert row.parent_ids == ["a", "z"]
    assert journal.load_compute_repair("source", "incarnation").activity_keys == frozenset(keys)


@pytest.mark.parametrize("bad_key", [
    (Session, None, "codex", DAY),
    (DailyActivity, None, "codex", "2026-10-13"),
    (DailyActivity, None, "codex", datetime(2026, 10, 13)),
    (WeeklyActivity, None, "codex", DAY),
    (DailyActivity, None, "codex"),
])
def test_malformed_runtime_key_rejected_before_writing(bad_key):
    journal = api()
    with transaction.atomic(), pytest.raises(ValueError):
        journal.merge_compute_repair("source", "incarnation", activity_keys=[KEY, bad_key], source_changed=True)
    assert journal.load_compute_repair("source", "incarnation") is None


@pytest.mark.parametrize("bad_key", [
    ["month", None, "codex", "2026-10-13"],
    ["day", None, "codex", "20261013"],
    ["day", None, "codex", "2026-02-30"],
    ["week", None, "codex", "2026-10-13"],
    ["day", None, "codex"],
])
def test_malformed_stored_key_fails_closed(bad_key):
    journal = api()
    snapshot = merge(journal, source_changed=True)
    model = import_module("twicc.core.models").ComputeAggregateRepair
    model.objects.filter(pk=snapshot.observation.journal_id).update(activity_keys=[bad_key])
    with pytest.raises(ValueError):
        journal.load_compute_repair("source", "incarnation")


def test_mutating_and_fenced_helpers_require_caller_transaction():
    journal = api()
    snapshot = merge(journal, source_changed=True)
    with pytest.raises(TransactionManagementError):
        journal.merge_compute_repair("source", "incarnation", source_changed=True)
    with pytest.raises(TransactionManagementError):
        journal.require_compute_repair(snapshot.observation)
    with pytest.raises(TransactionManagementError):
        journal.settle_compute_repair(snapshot.observation)
    assert journal.load_compute_repair("source", "incarnation") == snapshot


def test_discovery_pages_are_bounded_materialized_and_exclude_later_rows():
    journal = api()
    assert journal.compute_repair_upper_id() == 0
    snapshots = []
    for number in range(4):
        with transaction.atomic():
            snapshots.append(journal.merge_compute_repair(str(number), "old", source_changed=True))
    upper = journal.compute_repair_upper_id()
    with transaction.atomic():
        journal.merge_compute_repair("later", "new", source_changed=True)
    first = journal.read_compute_repair_page(0, upper, limit=2)
    second = journal.read_compute_repair_page(first[-1].observation.journal_id, upper, limit=2)
    assert isinstance(first, tuple)
    assert first + second == tuple(snapshots)
    assert journal.read_compute_repair_page(upper, upper) == ()
    assert journal.load_session_repairs([]) == ()
    with transaction.atomic():
        journal.settle_compute_repair(first[0].observation)
    assert first[0] == snapshots[0]  # Already materialized, independent of later deletion.


def test_migration_creates_only_small_journal_table():
    assert util.find_spec("twicc.core.migrations.0157_compute_aggregate_repair") is not None
    sql = call_command("sqlmigrate", "core", "0157_compute_aggregate_repair", stdout=io.StringIO())
    assert sql.upper().count("CREATE TABLE") == 1
    assert 'CREATE TABLE "core_computeaggregaterepair"' in sql
    assert "compute_repair_source_identity" in sql
    assert "core_sessionitem" not in sql.lower()
    assert "SELECT " not in sql.upper()


def test_project_tags_round_trip_union_and_generation_without_activity_keys():
    journal = api()
    first = merge(journal, activity_keys=[KEY], project_ids=['old', 'old'], source_changed=False)
    assert first.project_ids == frozenset({'old'})
    same = merge(journal, project_ids=['old'], source_changed=False)
    assert same == first
    changed = merge(journal, project_ids=['new'], source_changed=False)
    assert changed.project_ids == frozenset({'old', 'new'})
    assert changed.observation.generation == first.observation.generation + 1
    assert changed.activity_keys == frozenset({KEY})
    model = import_module('twicc.core.models').ComputeAggregateRepair
    assert model.objects.get(pk=changed.observation.journal_id).activity_keys == [
        ['day', None, 'codex', '2026-10-13'], ['project', 'new'], ['project', 'old']]
    assert journal.load_compute_repair('source', 'incarnation') == changed


def test_legacy_row_has_no_project_obligations():
    journal = api()
    snapshot = merge(journal, activity_keys=[KEY], source_changed=True)
    assert journal.load_compute_repair('source', 'incarnation').project_ids == frozenset()


@pytest.mark.parametrize('tag', [['project', ''], ['project', None], ['project', 3], ['project'], ['other', 'id']])
def test_malformed_project_tag_fails_closed(tag):
    journal = api()
    snapshot = merge(journal, source_changed=True)
    model = import_module('twicc.core.models').ComputeAggregateRepair
    model.objects.filter(pk=snapshot.observation.journal_id).update(activity_keys=[tag])
    with pytest.raises(ValueError):
        journal.load_compute_repair('source', 'incarnation')


@pytest.mark.parametrize('project_id', ['', None, 4, ['nested']])
def test_invalid_project_id_is_rejected_before_writing(project_id):
    journal = api()
    with transaction.atomic(), pytest.raises(ValueError):
        journal.merge_compute_repair('source', 'incarnation', project_ids=[project_id], source_changed=True)
    assert journal.load_compute_repair('source', 'incarnation') is None


def test_mixed_scope_project_activity_and_baseline_tags_roundtrip():
    from datetime import date
    from twicc.providers.compute_repair_journal import own_baseline_pending, clear_own_baseline_pending
    day = (DailyActivity, 'old', 'codex', date(2026, 10, 13))
    with transaction.atomic():
        first = merge_compute_repair('mixed-scope', 'inc', activity_keys=[day], project_ids=['old'],
            activity_scopes=[('old', 'codex'), (None, 'codex')], source_changed=True, baseline_pending=True)
        second = merge_compute_repair('mixed-scope', 'inc', project_ids=['new'],
            activity_scopes=[('new', 'claude_code'), (None, 'claude_code')], source_changed=False)
    assert second.activity_keys == frozenset({day})
    assert second.project_ids == frozenset({'old', 'new'})
    assert second.activity_scopes == frozenset({('old', 'codex'), (None, 'codex'),
                                               ('new', 'claude_code'), (None, 'claude_code')})
    assert second.observation.generation == first.observation.generation + 1
    assert load_compute_repair('mixed-scope', 'inc') == second
    assert own_baseline_pending('mixed-scope', 'inc')
    with transaction.atomic():
        clear_own_baseline_pending('mixed-scope', 'inc')
    assert load_compute_repair('mixed-scope', 'inc') == second
    assert not own_baseline_pending('mixed-scope', 'inc')


@pytest.mark.parametrize('scope', [('p', ''), ('', 'codex'), (3, 'codex'), ('p', None), ('p',), 'p'])
def test_invalid_activity_scope_rolls_back_before_writes(scope):
    with pytest.raises(ValueError), transaction.atomic():
        merge_compute_repair('invalid-scope', 'inc', activity_scopes=[scope], source_changed=True)
    assert load_compute_repair('invalid-scope', 'inc') is None
