"""Exact aggregate repair and atomic settlement of durable obligations."""

from collections.abc import Iterable, Sequence

from django.db import transaction
from django.db.models import F

from twicc.core.enums import Provider
from twicc.core.models import ComputeAggregateRepair, Project, Session
from twicc.providers.compute_repair_journal import (
    ActivityKey,
    RepairObservation,
    RepairObservationChanged,
    RepairSnapshot,
    _require_transaction,
    require_compute_repair,
    settle_compute_repair,
)
from twicc.providers.live_aggregates import apply_contribution_changes, needs_repair, session_contribution


def repair_retained_parents(parent_ids: Iterable[str]) -> None:
    """Repair direct-parent raw costs without recounting their messages."""
    _require_transaction()
    for parent in Session.objects.filter(id__in=set(parent_ids)):
        parent.recalculate_costs()
        parent.save(update_fields=["self_cost", "subagents_cost", "total_cost"])


def repair_activity_key(key: ActivityKey) -> None:
    """Repair one exact bucket, removing obsolete missing-project scopes."""
    _require_transaction()
    model, project_id, provider, activity_date = key
    if project_id is not None and not Project.objects.filter(pk=project_id).exists():
        model.objects.filter(project_id=project_id, provider=provider, date=activity_date).delete()
        return
    model.recalculate(project_id, activity_date, Provider(provider))


def repair_compute_observation(observation: RepairObservation) -> str:
    """Repair one current target or orphan in a single writer transaction."""
    with transaction.atomic():
        # Acquire SQLite write ownership before any source or journal read.
        matched = ComputeAggregateRepair.objects.filter(
            pk=observation.journal_id,
            session_id=observation.session_id,
            incarnation=observation.incarnation,
            generation=observation.generation,
        ).update(generation=F("generation"))
        if not matched:
            return "changed"
        try:
            snapshot = require_compute_repair(observation)
        except RepairObservationChanged:
            return "changed"
        target = Session.objects.filter(pk=observation.session_id).first()
        if target is not None and target.storage_incarnation == observation.incarnation:
            if needs_repair(target):
                return "stale_target"
            state = session_contribution(target)
            apply_contribution_changes(
                (), (), before_sessions=[state], after_sessions=[state], repair=True,
                extra_activity_keys=snapshot.activity_keys,
            )
        else:
            for key in snapshot.activity_keys:
                repair_activity_key(key)
        repair_retained_parents(snapshot.parent_ids)
        try:
            settle_compute_repair(observation)
        except RepairObservationChanged:
            transaction.set_rollback(True)
            return "changed"
    return "repaired"


def settle_deleted_repairs(snapshots: Sequence[RepairSnapshot]) -> None:
    """Repair deleted-source obligations under the caller's deletion transaction."""
    _require_transaction()
    # Fence every inventory before repairing any of them. Never trust stale keys.
    current = [require_compute_repair(snapshot.observation) for snapshot in snapshots]
    keys = {key for snapshot in current for key in snapshot.activity_keys}
    parents = {parent_id for snapshot in current for parent_id in snapshot.parent_ids}
    for key in keys:
        repair_activity_key(key)
    repair_retained_parents(parents)
    for snapshot in current:
        settle_compute_repair(snapshot.observation)
