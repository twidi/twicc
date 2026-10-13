"""Transaction-owned aggregate obligations and fully materialized discovery reads.

The caller owns the serialized writer transaction. These helpers never open or
commit a transaction. Journal generations fence obligations, not source revisions.
"""

from collections.abc import Iterable
from datetime import date
from typing import NamedTuple

from django.db import connection
from django.db.models import Max
from django.db.transaction import TransactionManagementError

from twicc.core.models import ComputeAggregateRepair, DailyActivity, WeeklyActivity, Project

ActivityKey = tuple[type[DailyActivity] | type[WeeklyActivity], str | None, str, date]
# JSON shapes: ["day" | "week", nullable project ID, provider, ISO date]
# plus ["project", ID], ["scope", nullable project ID, provider], and
# ["baseline_pending"]. Scope/project markers stay outside ActivityKey.
SerializedActivityKey = list[str | None]


class RepairObservation(NamedTuple):
    journal_id: int
    session_id: str
    incarnation: str
    generation: int


class RepairSnapshot(NamedTuple):
    observation: RepairObservation
    activity_keys: frozenset[ActivityKey]
    parent_ids: frozenset[str]
    project_ids: frozenset[str] = frozenset()
    activity_scopes: frozenset[tuple[str | None, str]] = frozenset()


class RepairObservationChanged(RuntimeError):
    """The observed row was removed, recreated, or changed."""


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise TransactionManagementError("Compute repair requires a caller-owned transaction")


def _serialize_key(key: ActivityKey) -> SerializedActivityKey:
    if not isinstance(key, (tuple, list)) or len(key) != 4:
        raise ValueError("Activity key must contain kind, project, provider, and date")
    model, project_id, provider, activity_date = key
    if model is not DailyActivity and model is not WeeklyActivity:
        raise ValueError("Activity kind must be daily or weekly")
    if project_id is not None and not isinstance(project_id, str):
        raise ValueError("Activity project must be a string or null")
    if not isinstance(provider, str) or not provider:
        raise ValueError("Activity provider must be a nonempty string")
    if type(activity_date) is not date:
        raise ValueError("Activity date must be a date, without a time")
    if model is WeeklyActivity and activity_date.weekday() != 0:
        raise ValueError("Weekly activity date must be Monday")
    return ["day" if model is DailyActivity else "week", project_id, provider, activity_date.isoformat()]


def _deserialize_key(key: SerializedActivityKey) -> ActivityKey:
    if not isinstance(key, list) or len(key) != 4:
        raise ValueError("Serialized activity key must contain four values")
    kind, project_id, provider, date_string = key
    if kind not in ("day", "week") or not isinstance(date_string, str):
        raise ValueError("Invalid serialized activity kind or date")
    activity_date = date.fromisoformat(date_string)
    if activity_date.isoformat() != date_string:
        raise ValueError("Activity date must use ISO YYYY-MM-DD format")
    result = (DailyActivity if kind == "day" else WeeklyActivity, project_id, provider, activity_date)
    _serialize_key(result)
    return result


def _parents(parent_ids: Iterable[str]) -> frozenset[str]:
    result = tuple(parent_ids)
    if any(not isinstance(parent_id, str) or not parent_id for parent_id in result):
        raise ValueError("Parent and project IDs must be nonempty strings")
    return frozenset(result)


def _activity_scope(scope):
    if not isinstance(scope, (tuple, list)) or len(scope) != 2:
        raise ValueError('Activity scope must contain project and provider')
    project, provider = scope
    if project is not None and (not isinstance(project, str) or not project):
        raise ValueError('Activity scope project must be a nonempty string or null')
    if not isinstance(provider, str) or not provider:
        raise ValueError('Activity scope provider must be a nonempty string')
    return project, provider


def _ordered_scopes(scopes):
    return [['scope', project, provider] for project, provider in sorted(scopes,
        key=lambda scope: (scope[0] is not None, scope[0] or '', scope[1]))]


def _snapshot(row: ComputeAggregateRepair) -> RepairSnapshot:
    keys = []
    projects = set()
    scopes = set()
    for tag in row.activity_keys:
        if tag == ["baseline_pending"]:
            continue
        if isinstance(tag, list) and len(tag) == 3 and tag[0] == "scope":
            scopes.add(_activity_scope(tag[1:]))
        elif isinstance(tag, list) and len(tag) == 2 and tag[0] == "project":
            projects.update(_parents([tag[1]]))
        else:
            keys.append(_deserialize_key(tag))
    return RepairSnapshot(
        RepairObservation(row.pk, row.session_id, row.incarnation, row.generation),
        frozenset(keys),
        _parents(row.parent_ids),
        frozenset(projects),
        frozenset(scopes),
    )


def _ordered_keys(keys: Iterable[ActivityKey]) -> list[SerializedActivityKey]:
    return sorted(
        (_serialize_key(key) for key in keys),
        key=lambda key: (key[0], key[1] is not None, key[1] or "", key[2], key[3]),
    )


def load_compute_repair(session_id: str, incarnation: str) -> RepairSnapshot | None:
    row = ComputeAggregateRepair.objects.filter(session_id=session_id, incarnation=incarnation).first()
    return _snapshot(row) if row is not None else None


def merge_compute_repair(
    session_id: str,
    incarnation: str,
    *,
    activity_keys: Iterable[ActivityKey] = (),
    parent_ids: Iterable[str] = (),
    project_ids: Iterable[str] = (),
    activity_scopes: Iterable[tuple[str | None, str]] = (),
    source_changed: bool,
    create: bool = True,
    baseline_pending: bool = False,
) -> RepairSnapshot | None:
    """Retain old obligations and advance generation for every source mutation."""
    _require_transaction()
    # Validate the complete input before the first write, including generator inputs.
    keys = frozenset(_deserialize_key(_serialize_key(key)) for key in activity_keys)
    scopes = frozenset(_activity_scope(scope) for scope in activity_scopes)
    parents = _parents(parent_ids)
    projects = _parents(project_ids)
    projects = projects | frozenset(Project.objects.filter(pk__in=projects, worktree_of__isnull=False).values_list(
        'worktree_of_id', flat=True))
    row = ComputeAggregateRepair.objects.filter(session_id=session_id, incarnation=incarnation).first()
    if row is None:
        if not create:
            return None
        row = ComputeAggregateRepair.objects.create(
            session_id=session_id,
            incarnation=incarnation,
            generation=int(source_changed or bool(keys) or bool(parents) or bool(projects) or bool(scopes)),
            activity_keys=_ordered_keys(keys) + [["project", project] for project in sorted(projects)]
                + _ordered_scopes(scopes) + ([["baseline_pending"]] if baseline_pending else []),
            parent_ids=sorted(parents),
        )
        return _snapshot(row)

    previous = _snapshot(row)
    merged_keys = previous.activity_keys | keys
    merged_parents = previous.parent_ids | parents
    merged_projects = previous.project_ids | projects
    merged_scopes = previous.activity_scopes | scopes
    if (source_changed or merged_keys != previous.activity_keys or merged_parents != previous.parent_ids
            or merged_projects != previous.project_ids or merged_scopes != previous.activity_scopes
            or (baseline_pending and ["baseline_pending"] not in row.activity_keys)):
        row.generation += 1
        pending = baseline_pending or ["baseline_pending"] in row.activity_keys
        row.activity_keys = (_ordered_keys(merged_keys) + [["project", project] for project in sorted(merged_projects)]
                             + _ordered_scopes(merged_scopes) + ([["baseline_pending"]] if pending else []))
        row.parent_ids = sorted(merged_parents)
        row.save(update_fields=["generation", "activity_keys", "parent_ids"])
        return _snapshot(row)
    return previous


def _observed_rows(observation: RepairObservation):
    return ComputeAggregateRepair.objects.filter(
        pk=observation.journal_id,
        session_id=observation.session_id,
        incarnation=observation.incarnation,
        generation=observation.generation,
    )


def require_compute_repair(observation: RepairObservation) -> RepairSnapshot:
    _require_transaction()
    row = _observed_rows(observation).first()
    if row is None:
        raise RepairObservationChanged("Compute repair observation changed")
    return _snapshot(row)


def settle_compute_repair(observation: RepairObservation) -> None:
    _require_transaction()
    deleted, _ = _observed_rows(observation).delete()
    if not deleted:
        raise RepairObservationChanged("Compute repair observation changed")


def load_session_repairs(session_ids: Iterable[str]) -> tuple[RepairSnapshot, ...]:
    rows = ComputeAggregateRepair.objects.filter(session_id__in=set(session_ids)).order_by("id")
    return tuple(_snapshot(row) for row in rows)


def read_compute_repair_page(after_id: int, upper_id: int, *, limit: int = 64) -> tuple[RepairSnapshot, ...]:
    if limit <= 0:
        raise ValueError("Compute repair page limit must be positive")
    rows = ComputeAggregateRepair.objects.filter(id__gt=after_id, id__lte=upper_id).order_by("id")[:limit]
    return tuple(_snapshot(row) for row in rows)


def compute_repair_upper_id() -> int:
    return ComputeAggregateRepair.objects.aggregate(upper_id=Max("id"))["upper_id"] or 0


def own_baseline_pending(session_id: str, incarnation: str) -> bool:
    keys = ComputeAggregateRepair.objects.filter(session_id=session_id, incarnation=incarnation).values_list(
        'activity_keys', flat=True).first()
    return keys is not None and ['baseline_pending'] in keys


def clear_own_baseline_pending(session_id: str, incarnation: str) -> None:
    """Discharge derived work without changing the source generation fence."""
    _require_transaction()
    row = ComputeAggregateRepair.objects.filter(session_id=session_id, incarnation=incarnation).first()
    if row is not None and ['baseline_pending'] in row.activity_keys:
        row.activity_keys = [tag for tag in row.activity_keys if tag != ['baseline_pending']]
        row.save(update_fields=['activity_keys'])
