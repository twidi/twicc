"""Materialized native-tree readiness and durable aggregate obligations.

Callers must establish authoritative provider inventory completion and success
before observation and again after writer admission. A compute classification
notification does not establish that boundary. This module neither publishes
nor settles journals. Shared journals settle only after every surviving former
and current native-tree dependency completes with fresh observations.
"""
from collections.abc import Iterable
from datetime import UTC, timedelta
from typing import NamedTuple

from twicc.core.models import DailyActivity, Session, SessionItem, SessionType
from twicc.providers.compute_repair_journal import RepairSnapshot, load_session_repairs
from twicc.providers.contribution_revisions import (
    DailyKey, RevisionObservation, SourceKey, observe_revisions, revisions_match,
)
from twicc.providers.helpers import get_provider_helpers


class TreeMember(NamedTuple):
    source: SourceKey
    compute_version: int
    # These fields describe source and membership, never derived counters.
    baseline: tuple


class TreeObservation(NamedTuple):
    root: SourceKey | None
    members: tuple[TreeMember, ...]
    revisions: RevisionObservation
    repairs: tuple[RepairSnapshot, ...]


class TreeAggregateWork(NamedTuple):
    observation: TreeObservation
    days: frozenset[DailyKey]
    weeks: frozenset[DailyKey]
    former_parents: frozenset[str]
    project_ids: frozenset[str]


_FIELDS = ('id', 'storage_incarnation', 'compute_version', 'provider', 'project_id',
           'parent_session_id', 'type', 'hidden', 'created_at', 'last_offset', 'last_line')


def _sessions():
    return Session.objects.only(*_FIELDS)


def _root(session_id: str) -> Session | None:
    row = _sessions().filter(pk=session_id).first()
    visited = set()
    while row is not None and row.id not in visited:
        visited.add(row.id)
        if row.type != SessionType.SUBAGENT:
            return row
        # A native subagent with an absent parent cannot prove tree membership.
        if not row.parent_session_id:
            return None
        row = _sessions().filter(pk=row.parent_session_id).first()
    return None


def _members(root: Session) -> tuple[Session, ...]:
    rows = {root.id: root}
    frontier = {root.id}
    while frontier:
        # Filtering type in Python prevents SQLite choosing its low-selectivity
        # type index instead of the indexed parent lookup.
        children = tuple(row for row in _sessions().filter(parent_session_id__in=frontier)
                         if row.type == SessionType.SUBAGENT)
        frontier = {row.id for row in children} - rows.keys()
        rows.update((row.id, row) for row in children)
    return tuple(rows[sid] for sid in sorted(rows))


def _identity(row: Session) -> TreeMember:
    return TreeMember(SourceKey(row.id, row.storage_incarnation), row.compute_version,
                      tuple(getattr(row, field) for field in _FIELDS[3:]))


def _ready(rows: Iterable[Session], pending_source_ids: frozenset[str]) -> bool:
    return all(row.id not in pending_source_ids and row.storage_incarnation
               and row.compute_version == get_provider_helpers(row.provider).current_compute_version for row in rows)


def _repairs(rows, retained):
    snapshots = {snapshot.observation.journal_id: snapshot for snapshot in load_session_repairs(row.id for row in rows)}
    for snapshot in retained:
        snapshots.setdefault(snapshot.observation.journal_id, snapshot)
    return tuple(snapshots[key] for key in sorted(snapshots))


def _project_ids(rows, repairs):
    projects = {row.project_id for row in rows}
    parents = set()
    for snapshot in repairs:
        projects.update(snapshot.project_ids)
        projects.update(project for project, _ in snapshot.activity_scopes if project is not None)
        projects.update(project for _, project, _, _ in snapshot.activity_keys if project is not None)
        parents.update(snapshot.parent_ids)
    projects.update(Session.objects.filter(pk__in=parents).values_list('project_id', flat=True))
    return projects


def _work(root, rows, repairs):
    days = set()
    projects = _project_ids(rows, repairs)
    parents = set()
    retained_scopes = {scope for snapshot in repairs for scope in snapshot.activity_scopes}
    retained_projects = {scope for snapshot in repairs for scope in snapshot.project_ids}
    for snapshot in repairs:
        parents.update(snapshot.parent_ids)
        for model, project, provider, day in snapshot.activity_keys:
            offsets = range(1) if model is DailyActivity else range(7)
            days.update(DailyKey(project, provider, day + timedelta(days=offset)) for offset in offsets)
    for row in rows:
        if row.created_at:
            day = row.created_at.astimezone(UTC).date()
            days.update(DailyKey(project, row.provider, day) for project in (row.project_id, None))
            days.update(DailyKey(project, provider, day) for project, provider in retained_scopes)
    scopes = {row.id: (row.project_id, row.provider) for row in rows}
    # Indexed session filtering materializes timestamps without loading content.
    for sid, timestamp in SessionItem.objects.filter(session_id__in=scopes, timestamp__isnull=False).order_by().values_list(
            'session_id', 'timestamp').distinct():
        project, provider = scopes[sid]
        day = timestamp.astimezone(UTC).date()
        days.update(DailyKey(scope, provider, day) for scope in (project, *retained_projects, None))
        days.update(DailyKey(scope, old_provider, day) for scope, old_provider in retained_scopes)
    members = tuple(_identity(row) for row in rows)
    root_key = SourceKey(root.id, root.storage_incarnation) if root else None
    observation = TreeObservation(root_key, members,
        observe_revisions(sources=(*[member.source for member in members],
            *[SourceKey(snapshot.observation.session_id, snapshot.observation.incarnation) for snapshot in repairs]),
            roots=(root_key,) if root_key else ()), repairs)
    return TreeAggregateWork(observation, frozenset(days),
        frozenset(DailyKey(key.project_id, key.provider, key.day - timedelta(days=key.day.weekday())) for key in days),
        frozenset(parents), frozenset(projects))


def observe_tree_work(session_id: str, pending_source_ids: frozenset[str], *,
                      retained_repairs: tuple[RepairSnapshot, ...] = (),
                      candidate_repairs: tuple[RepairSnapshot, ...] = ()) -> TreeAggregateWork | None:
    """Observe one eligible tree independently of its shared former-tree work."""
    root = _root(session_id)
    if root is None:
        return None
    rows = _members(root)
    if not _ready(rows, pending_source_ids):
        return None
    member_ids = {row.id for row in rows}
    retained_repairs += tuple(snapshot for snapshot in candidate_repairs
        if snapshot.observation.session_id in member_ids or snapshot.parent_ids & member_ids)
    return _work(root, rows, _repairs(rows, retained_repairs))


def former_tree_dependency_ids(snapshots: Iterable[RepairSnapshot]) -> frozenset[str]:
    """Resolve surviving former parents; missing parents leave retained scopes.

    An unresolved surviving ancestry remains an explicit dependency ID, whose
    observation returns None. It must never disappear as if already complete.
    """
    dependencies = set()
    for parent in {parent for snapshot in snapshots for parent in snapshot.parent_ids}:
        root = _root(parent)
        if root is not None:
            dependencies.add(root.id)
        elif Session.objects.filter(pk=parent).exists():
            dependencies.add(parent)
    return frozenset(dependencies)


def observe_orphan_work(snapshot: RepairSnapshot) -> TreeAggregateWork:
    """Retain absent-source scopes without requiring absent-source compute."""
    return _work(None, (), (snapshot,))


def tree_work_matches(work: TreeAggregateWork, pending_source_ids: frozenset[str], *,
                      settled_repairs: frozenset[RepairSnapshot] = frozenset()) -> bool:
    """Validate source fences and exact journals, including known committed removals.

    Only completed publication proofs receive settled_repairs. A missing journal
    remains invalid unless this scheduler successfully settled that exact snapshot.
    A recreated journal never matches the prior settlement evidence.
    """
    observation = work.observation
    if not revisions_match(observation.revisions):
        return False
    current_repairs = {
        (snapshot.observation.session_id, snapshot.observation.incarnation): snapshot
        for snapshot in load_session_repairs(snapshot.observation.session_id for snapshot in observation.repairs)
    }
    for snapshot in observation.repairs:
        key = snapshot.observation
        current = current_repairs.get((key.session_id, key.incarnation))
        if current != snapshot and not (current is None and snapshot in settled_repairs):
            return False
    if observation.root is None:
        if any(Session.objects.filter(pk=snapshot.observation.session_id,
                storage_incarnation=snapshot.observation.incarnation).exists() for snapshot in observation.repairs):
            return False
        # A replaced source is a separate current-tree dependency. An old
        # incarnation journal remains valid and independently repairable.
        return (all(snapshot.observation.session_id not in pending_source_ids for snapshot in observation.repairs)
                and _work(None, (), observation.repairs) == work)
    root = _root(observation.root.session_id)
    if root is None or SourceKey(root.id, root.storage_incarnation) != observation.root:
        return False
    rows = _members(root)
    return (_ready(rows, pending_source_ids)
            and tuple(_identity(row) for row in rows) == observation.members
            and _repairs(rows, observation.repairs) == observation.repairs
            and frozenset(_project_ids(rows, observation.repairs)) == work.project_ids)
