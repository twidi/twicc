"""Restart recovery uses only retained journals and committed source rows."""
from decimal import Decimal

import pytest
from django.db import transaction

from tests.test_tree_aggregate_runtime import node
from tests.tree_aggregate_helpers import drain_aggregates
from twicc.core.models import ComputeAggregateRepair, Project
from twicc.providers.compute_repair_journal import load_compute_repair, merge_compute_repair
from twicc.providers.contribution_revisions import reset_contribution_revisions
from twicc.providers.live_aggregates import apply_contribution_changes, session_contribution

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('mutation', ['move', 'delete'])
def test_null_timestamp_projects_survive_restart(mutation):
    root = node('root')
    Project.objects.create(id='q')
    drain_aggregates()
    assert Project.objects.get(pk='p').total_cost == Decimal(2)
    if mutation == 'delete':
        from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
        _apply_delete_sessions_payload(DeleteSessionsPayload('codex', [root.id]))
    else:
        before = session_contribution(root)
        with transaction.atomic():
            root.project_id = 'q'
            root.save(update_fields=['project'])
            apply_contribution_changes([], [], before_sessions=[before],
                after_sessions=[session_contribution(root)], repair=True)
        snapshot = load_compute_repair(root.id, root.storage_incarnation)
        assert snapshot.project_ids == frozenset({'p', 'q'})
    reset_contribution_revisions()
    drain_aggregates()
    assert Project.objects.get(pk='p').total_cost is None
    assert Project.objects.get(pk='q').total_cost == (Decimal(2) if mutation == 'move' else None)
    assert not ComputeAggregateRepair.objects.exists()


def test_former_tree_dependency_survives_scheduler_restart():
    from twicc.providers.helpers import get_provider_helpers
    former = node('former', ready=False)
    current = node('current')
    with transaction.atomic():
        merge_compute_repair(current.id, current.storage_incarnation, parent_ids=[former.id], source_changed=True)
    drain_aggregates()
    current.refresh_from_db()
    assert current.total_cost == Decimal(2)
    assert load_compute_repair(current.id, current.storage_incarnation) is not None
    former.compute_version = get_provider_helpers('codex').current_compute_version
    former.save(update_fields=['compute_version'])
    reset_contribution_revisions()
    drain_aggregates()
    former.refresh_from_db()
    assert former.total_cost == Decimal(2)
    assert not ComputeAggregateRepair.objects.exists()


def test_legacy_deletion_retains_removed_dates_before_identity_disappears():
    from datetime import UTC, datetime
    from twicc.core.models import DailyActivity, Project, Session, SessionItem, ComputeAggregateRepair
    from twicc.core.enums import Provider
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload
    from tests.tree_aggregate_helpers import drain_aggregates
    project = Project.objects.create(id='legacy-delete')
    session = Session.objects.create(id='legacy-delete', project=project, provider=Provider.CODEX)
    stamp = datetime(2026, 10, 13, tzinfo=UTC)
    SessionItem.objects.create(session=session, line_num=1, timestamp=stamp, cost=2)
    DailyActivity.objects.create(project=project, provider=Provider.CODEX, date=stamp.date(), cost=2)
    _apply_delete_sessions_payload(DeleteSessionsPayload(Provider.CODEX, [session.id]))
    assert ComputeAggregateRepair.objects.filter(session_id=session.id).exists()
    drain_aggregates()
    assert not DailyActivity.objects.filter(project=project).exists()


def test_provider_move_without_item_evidence_repairs_old_scope_after_restart():
    from datetime import UTC, datetime
    from twicc.core.models import DailyActivity
    from twicc.providers.helpers import get_provider_helpers
    root = node('provider-move')
    root.items.update(timestamp=datetime(2026, 10, 13, tzinfo=UTC))
    drain_aggregates()
    assert DailyActivity.objects.filter(provider='codex').count() == 2
    before = session_contribution(root)
    with transaction.atomic():
        root.provider = 'claude_code'
        root.compute_version = get_provider_helpers(root.provider).current_compute_version
        root.save(update_fields=['provider', 'compute_version'])
        apply_contribution_changes([], [], before_sessions=[before], after_sessions=[session_contribution(root)],
            repair=True)
    reset_contribution_revisions()
    drain_aggregates()
    assert not DailyActivity.objects.filter(provider='codex').exists()
    assert DailyActivity.objects.filter(provider='claude_code').count() == 2
    assert not ComputeAggregateRepair.objects.exists()


def test_existing_project_registration_retains_old_and_new_worktree_scopes():
    from twicc.projects import register_project_db_only
    root = node('registered')
    Project.objects.create(id='old-main')
    Project.objects.create(id='new-main')
    Project.objects.filter(pk=root.project_id).update(worktree_of_id='old-main')
    register_project_db_only(root.project_id, worktree_of_id='new-main')
    snapshot = load_compute_repair('project:p', 'project')
    assert snapshot is not None
    assert snapshot.project_ids >= {'p', 'old-main', 'new-main'}
