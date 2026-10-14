"""Native tree barriers and retained repair scopes use real database evidence."""
from datetime import UTC, date, datetime
from importlib import import_module, util

import pytest
from django.db import transaction

from twicc.core.models import ComputeAggregateRepair, DailyActivity, Project, Session, SessionItem, WeeklyActivity
from twicc.providers.compute_repair_journal import merge_compute_repair
from twicc.providers.contribution_revisions import DailyKey, SourceKey, record_contribution_commit
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)
DAY = date(2026, 10, 13)


def api():
    assert util.find_spec('twicc.providers.tree_aggregate_state') is not None, 'tree observation API is missing'
    return import_module('twicc.providers.tree_aggregate_state')


def node(sid, parent=None, *, ready=True, native=True, project='project'):
    p, _ = Project.objects.get_or_create(id=project)
    return Session.objects.create(id=sid, file_path=f'/test/{sid}.jsonl', project=p, provider='codex', storage_incarnation=f'inc-{sid}',
        parent_session=parent, type='subagent' if parent and native else 'session',
        compute_version=get_provider_helpers('codex').current_compute_version if ready else None)


def repair(sid, inc=None, **kwargs):
    with transaction.atomic():
        return merge_compute_repair(sid, inc or f'inc-{sid}', source_changed=True, **kwargs)


def test_applied_root_does_not_make_failed_descendant_ready():
    tree = api()
    root = node('root')
    child = node('child', root, ready=False)
    assert tree.observe_tree_work(root.id, frozenset()) is None
    Session.objects.filter(pk=child.id).update(compute_version=get_provider_helpers('codex').current_compute_version)
    assert tree.observe_tree_work(root.id, frozenset({'child'})) is None
    assert tree.observe_tree_work(root.id, frozenset()) is not None


def test_flat_native_tree_and_unflattened_chain_exclude_spawned_session():
    tree = api()
    root = node('root')
    launcher = node('launcher', root)
    node('flat', root)
    node('deep', launcher)
    node('independent', root, ready=False, native=False)
    work = tree.observe_tree_work('deep', frozenset())
    assert work.observation.root == SourceKey('root', 'inc-root')
    assert {member.source.session_id for member in work.observation.members} == {'root', 'launcher', 'flat', 'deep'}


def test_missing_parent_and_cycle_remain_unresolved():
    tree = api()
    root = node('root')
    child = node('child', root)
    Session.objects.filter(pk=root.id).update(type='subagent', parent_session_id=child.id)
    assert tree.observe_tree_work('child', frozenset()) is None
    Session.objects.filter(pk=root.id).update(parent_session_id=None)
    assert tree.observe_tree_work('child', frozenset()) is None


def test_late_child_invalidates_observed_tree():
    tree = api()
    root = node('root')
    work = tree.observe_tree_work(root.id, frozenset())
    node('late', root)
    assert tree.tree_work_matches(work, frozenset()) is False


def test_source_revision_and_incarnation_invalidate_but_derived_costs_do_not():
    tree = api()
    root = node('root')
    work = tree.observe_tree_work(root.id, frozenset())
    Session.objects.filter(pk=root.id).update(total_cost='12', user_message_count=3)
    assert tree.tree_work_matches(work, frozenset())
    with transaction.atomic():
        record_contribution_commit(sources=[SourceKey('root', 'inc-root')])
    assert not tree.tree_work_matches(work, frozenset())
    work = tree.observe_tree_work(root.id, frozenset())
    Session.objects.filter(pk=root.id).update(storage_incarnation='replacement')
    assert not tree.tree_work_matches(work, frozenset())


def test_reparent_preserves_former_tree_dates():
    tree = api()
    old = node('old')
    new = node('new')
    child = node('child', new)
    snapshot = repair(child.id, parent_ids=[old.id], activity_keys=[(DailyActivity, 'old-project', 'codex', DAY)])
    work = tree.observe_tree_work(new.id, frozenset())
    assert DailyKey('old-project', 'codex', DAY) in work.days
    assert work.former_parents == frozenset({'old'})
    assert work.project_ids == frozenset({'project', 'old-project'})
    assert tree.former_tree_dependency_ids(work.observation.repairs) == frozenset({'old'})
    former = tree.observe_tree_work('old', frozenset(), retained_repairs=(snapshot,))
    assert snapshot in former.observation.repairs


def test_ready_new_tree_does_not_settle_incomplete_former_tree():
    tree = api()
    old = node('old')
    node('failed', old, ready=False)
    new = node('new')
    child = node('child', new)
    shared = repair(child.id, parent_ids=[old.id])
    new_tree_work = tree.observe_tree_work(new.id, frozenset())
    former_tree_work = tree.observe_tree_work(old.id, frozenset(), retained_repairs=(shared,))
    assert new_tree_work is not None
    assert tree.tree_work_matches(new_tree_work, frozenset())
    assert former_tree_work is None
    assert ComputeAggregateRepair.objects.filter(pk=shared.observation.journal_id).exists()


def test_orphan_does_not_settle_incomplete_former_tree():
    tree = api()
    old = node('old', ready=False)
    snapshot = repair('deleted', parent_ids=[old.id, 'missing'], project_ids=['historical'])
    work = tree.observe_orphan_work(snapshot)
    assert work.observation.root is None
    assert work.project_ids == frozenset({'historical', 'project'})
    assert tree.tree_work_matches(work, frozenset())
    assert tree.former_tree_dependency_ids((snapshot,)) == frozenset({'old'})
    assert tree.observe_tree_work('old', frozenset(), retained_repairs=(snapshot,)) is None
    assert ComputeAggregateRepair.objects.filter(pk=snapshot.observation.journal_id).exists()


def test_old_incarnation_obligation_survives_source_replacement():
    tree = api()
    root = node('root')
    snapshot = repair(root.id, 'old-inc', project_ids=['historical'])
    work = tree.observe_tree_work(root.id, frozenset())
    assert snapshot in work.observation.repairs
    assert work.project_ids == frozenset({'historical', 'project'})
    assert tree.tree_work_matches(work, frozenset())
    repair(root.id, 'old-inc', project_ids=['another'])
    assert not tree.tree_work_matches(work, frozenset())


def test_legacy_week_expands_seven_days_and_current_item_dates():
    tree = api()
    root = node('root')
    SessionItem.objects.create(session=root, line_num=1, content='{}', timestamp=datetime(2026,10,20,tzinfo=UTC))
    repair(root.id, activity_keys=[(WeeklyActivity, 'legacy', 'codex', date(2026,10,12))])
    work = tree.observe_tree_work(root.id, frozenset())
    assert {key.day for key in work.days if key.project_id == 'legacy'} == {
        date(2026,10,12), date(2026,10,13), date(2026,10,14), date(2026,10,15),
        date(2026,10,16), date(2026,10,17), date(2026,10,18)}
    assert DailyKey('project', 'codex', date(2026,10,20)) in work.days
    assert DailyKey(None, 'codex', date(2026,10,20)) in work.days
    assert DailyKey('legacy', 'codex', date(2026,10,12)) in work.weeks


def test_orphan_retains_surviving_former_project_without_dates():
    tree = api()
    former = node('former', project='former-project')
    snapshot = repair('deleted', parent_ids=[former.id])
    work = tree.observe_orphan_work(snapshot)
    assert work.days == frozenset()
    assert work.project_ids == frozenset({'former-project'})


def test_orphan_source_revision_invalidates_observation():
    tree = api()
    snapshot = repair('deleted')
    work = tree.observe_orphan_work(snapshot)
    with transaction.atomic():
        record_contribution_commit(sources=[SourceKey('deleted', 'inc-deleted')])
    assert not tree.tree_work_matches(work, frozenset())


def test_surviving_unresolved_former_parent_remains_dependency():
    tree = api()
    former = node('former')
    Session.objects.filter(pk=former.id).update(type='subagent')
    snapshot = repair('deleted', parent_ids=[former.id, 'missing'])
    assert tree.former_tree_dependency_ids((snapshot,)) == frozenset({'former'})
    assert tree.observe_tree_work('former', frozenset(), retained_repairs=(snapshot,)) is None


def test_flat_414_node_tree_uses_indexed_parent_lookup(django_assert_num_queries):
    tree = api()
    root = node('root')
    version = get_provider_helpers('codex').current_compute_version
    Session.objects.bulk_create([Session(id=f'child-{i}', file_path=f'/test/child-{i}.jsonl', project=root.project,
        provider='codex', type='subagent', parent_session=root, storage_incarnation=f'inc-{i}', compute_version=version)
        for i in range(413)])
    # Root, two indexed breadth layers, journal, timestamps. No per-child reads.
    with django_assert_num_queries(5):
        work = tree.observe_tree_work('root', frozenset())
    assert len(work.observation.members) == 414
    plan = Session.objects.filter(parent_session_id__in=['root']).explain()
    assert 'SEARCH core_session USING INDEX' in plan
    assert 'parent_session_id' in plan


def test_orphan_matching_rejects_surviving_same_incarnation():
    tree = api()
    snapshot = repair('source')
    work = tree.observe_orphan_work(snapshot)
    node('source', ready=False)
    assert not tree.tree_work_matches(work, frozenset())


def test_tree_matching_does_not_read_item_history(django_assert_num_queries):
    tree = api()
    node('root')
    work = tree.observe_tree_work('root', frozenset())
    # Root, empty child layer, journal discovery. No item-history query.
    with django_assert_num_queries(3):
        assert tree.tree_work_matches(work, frozenset())


@pytest.mark.parametrize('mutation', ['missing', 'recreated', 'generation', 'source', 'pending'])
def test_partial_settlement_evidence_preserves_all_other_fences(mutation):
    from twicc.providers.compute_repair_journal import settle_compute_repair

    tree = api()
    root = node('settlement-root')
    child = node('settlement-child', root)
    first = repair(root.id)
    remaining = repair(child.id)
    work = tree.observe_tree_work(root.id, frozenset())
    with transaction.atomic():
        settle_compute_repair(first.observation)
    evidence = frozenset((first,))
    assert not tree.tree_work_matches(work, frozenset())
    assert tree.tree_work_matches(work, frozenset(), settled_repairs=evidence)
    pending = frozenset()
    if mutation == 'missing':
        with transaction.atomic():
            settle_compute_repair(remaining.observation)
    elif mutation == 'recreated':
        recreated = repair(root.id)
        assert recreated.observation != first.observation
    elif mutation == 'generation':
        changed = repair(child.id)
        assert changed.observation.generation != remaining.observation.generation
    elif mutation == 'source':
        with transaction.atomic():
            record_contribution_commit(sources=[SourceKey(root.id, root.storage_incarnation)])
    else:
        pending = frozenset((child.id,))
    assert not tree.tree_work_matches(work, pending, settled_repairs=evidence)
