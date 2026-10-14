"""Prepare selective own costs and launcher totals on a reader thread."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import current_thread, main_thread
from typing import NamedTuple

from django.db import connection, transaction
from django.db.models import Sum, Q
from django.db.models.functions import TruncDate

from twicc.core.models import AgentLink, DailyActivity, Session, SessionItem, SessionType, Workflow
from twicc.providers.compute_tree import load_native_tree
from twicc.providers.contribution_revisions import (
    DailyKey, RevisionObservation, SourceKey, observe_own_cost_invalidations, restrict_own_cost_observation,
)
from twicc.providers.live_aggregates import persisted_cost


class PreparedOwnCost(NamedTuple):
    session_id: str
    observed_at: datetime | None
    self_cost: Decimal | None
    invalidation_observation: RevisionObservation


class SessionCosts(NamedTuple):
    session_id: str
    self_cost: Decimal | None
    subagents_cost: Decimal | None
    total_cost: Decimal | None
    storage_incarnation: str | None = None


class PreparedTreeCosts(NamedTuple):
    own_updates: tuple[PreparedOwnCost, ...]
    session_costs: tuple[SessionCosts, ...]
    project_ids: tuple[str, ...]
    workflow_ids: tuple[int, ...]


def prepare_tree_costs(root_ids: tuple[str, ...]) -> PreparedTreeCosts:
    """Use one thread-owned read snapshot, without writer admission.

    The statistics executor owns this thread and its Django connection. Capture
    invalidation counters before the first snapshot read. Publication must check
    each own update's observation under writer admission before writing markers.
    Equal or regressing source timestamps intentionally retain cached own costs.
    """
    if current_thread() is main_thread():
        raise RuntimeError('Statistics preparation requires a dedicated reader thread')
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError('Statistics preparation requires its own read transaction')
    observation = observe_own_cost_invalidations()
    own_revision_lookup = dict(observation.own_costs)
    with transaction.atomic():
        trees = tuple(load_native_tree(root) for root in dict.fromkeys(root_ids))
        members = tuple(dict.fromkeys(sid for tree in trees for sid in tree.member_ids))
        rows = {row['id']: row for row in Session.objects.filter(pk__in=members).values(
            'id', 'project_id', 'storage_incarnation', 'last_updated_at',
            'items_cost_computed_through_at', 'self_cost',
        )}
        changed = tuple(sid for sid in members if (
            rows[sid]['items_cost_computed_through_at'] is None
            or rows[sid]['last_updated_at'] is None
            or rows[sid]['last_updated_at'] > rows[sid]['items_cost_computed_through_at']
        ))
        sums = dict(SessionItem.objects.filter(session_id__in=changed).order_by().values('session_id').annotate(
            own_cost=Sum('cost'),
        ).values_list('session_id', 'own_cost')) if changed else {}
        changed_set = set(changed)
        own = {sid: persisted_cost(sums.get(sid) if sid in changed_set else rows[sid]['self_cost'])
               for sid in members}
        updates = tuple(PreparedOwnCost(
            sid, rows[sid]['last_updated_at'], own[sid], restrict_own_cost_observation(
                observation, SourceKey(sid, rows[sid]['storage_incarnation']), own_revision_lookup,
            ),
        ) for sid in changed)
        parents = dict(AgentLink.objects.filter(agent_id__in=members).values_list('agent_id', 'session_id'))
        costs = []
        for tree in trees:
            descendants = dict.fromkeys(tree.member_ids)
            for sid in tree.ordered_source_ids:
                subagents = descendants[sid]
                total = (own[sid] or Decimal(0)) + (subagents or Decimal(0))
                costs.append(SessionCosts(sid, own[sid], persisted_cost(subagents),
                                          persisted_cost(total) if total > 0 else None, rows[sid]['storage_incarnation']))
                if sid != tree.root_id:
                    # Unlinked components attach once to the native root. Known
                    # launcher descendants accumulate into their direct parent.
                    parent = parents.get(sid, tree.root_id)
                    if own[sid] is not None or subagents is not None:
                        descendants[parent] = (descendants[parent] or Decimal(0)) + total
        projects = tuple(sorted({row['project_id'] for row in rows.values()}))
        workflows = tuple(Workflow.objects.filter(session_id__in=members).order_by('pk').values_list('pk', flat=True))
    return PreparedTreeCosts(updates, tuple(costs), projects, workflows)


class ActivityValues(NamedTuple):
    user_message_count: int
    session_count: int
    cost: Decimal


def _reader_snapshot(function):
    """Give each executor operation an independent, thread-owned snapshot."""
    from functools import wraps

    @wraps(function)
    def wrapped(*args, **kwargs):
        if current_thread() is main_thread():
            raise RuntimeError('Statistics preparation requires a dedicated reader thread')
        if connection.in_atomic_block or not connection.get_autocommit():
            raise RuntimeError('Statistics preparation requires its own read transaction')
        with transaction.atomic():
            return function(*args, **kwargs)
    return wrapped


@_reader_snapshot
def prepare_affected_days(root_ids: tuple[str, ...]) -> tuple[DailyKey, ...]:
    members = {sid for root in dict.fromkeys(root_ids) for sid in load_native_tree(root).member_ids}
    keys = set()
    rows = SessionItem.objects.filter(session_id__in=members, timestamp__isnull=False).filter(
        Q(cost__isnull=False) | Q(kind='user_message', session__type=SessionType.SESSION, session__hidden=False),
    ).order_by().annotate(day=TruncDate('timestamp', tzinfo=UTC)).values_list(
        'session__project_id', 'session__provider', 'day',
    ).distinct().iterator()
    for project, provider, day in rows:
        keys.update((DailyKey(project, provider, day), DailyKey(None, provider, day)))
    for project, provider, created in Session.objects.filter(
        pk__in=members, type=SessionType.SESSION, hidden=False, user_message_count__gt=0, created_at__isnull=False,
    ).values_list('project_id', 'provider', 'created_at'):
        day = created.astimezone(UTC).date()
        keys.update((DailyKey(project, provider, day), DailyKey(None, provider, day)))
    return tuple(sorted(keys, key=lambda key: (key.provider, key.day, key.project_id or '')))


@_reader_snapshot
def prepare_daily(key: DailyKey) -> ActivityValues:
    start, end = DailyActivity.date_range(key.day)
    items = SessionItem.objects.filter(session__provider=key.provider, timestamp__gte=start, timestamp__lt=end)
    sessions = Session.objects.filter(provider=key.provider, created_at__gte=start, created_at__lt=end,
                                      type=SessionType.SESSION, hidden=False, user_message_count__gt=0)
    if key.project_id is not None:
        items = items.filter(session__project_id=key.project_id)
        sessions = sessions.filter(project_id=key.project_id)
    messages = items.filter(kind='user_message', session__type=SessionType.SESSION, session__hidden=False).count()
    return ActivityValues(messages, sessions.count(), items.aggregate(cost=Sum('cost'))['cost'] or Decimal(0))


@_reader_snapshot
def prepare_weekly(key: DailyKey) -> ActivityValues:
    if key.day.weekday() != 0:
        raise ValueError('Weekly activity date must be Monday')
    values = DailyActivity.objects.filter(project_id=key.project_id, provider=key.provider,
        date__gte=key.day, date__lt=key.day + timedelta(days=7)).aggregate(
            messages=Sum('user_message_count'), sessions=Sum('session_count'), cost=Sum('cost'))
    return ActivityValues(values['messages'] or 0, values['sessions'] or 0, values['cost'] or Decimal(0))


@_reader_snapshot
def prepare_project_cost(project_id: str) -> Decimal | None:
    value = Session.objects.filter(project_id=project_id, type=SessionType.SESSION).aggregate(
        cost=Sum('total_cost'))['cost']
    return persisted_cost(value) if value and value > 0 else None


@_reader_snapshot
def prepare_workflow_cost(workflow_id: int) -> tuple[Decimal, dict[str, float]]:
    workflow = Workflow.objects.only('run_id', 'raw_json').get(pk=workflow_id)
    import orjson

    envelope = orjson.loads(workflow.raw_json) if workflow.raw_json else {}
    return Workflow.compute_costs(workflow.run_id, envelope)
