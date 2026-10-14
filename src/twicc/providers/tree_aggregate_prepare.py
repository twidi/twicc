"""Exact aggregate reads on a scheduler-owned worker, never the writer queue.

The scheduler establishes authoritative inventory admission before preparing and
again under writer ownership. It owns one executor for its lifetime, polls the
returned future, and retries ChangedPreparation outside writer ownership.
Commit child publications before preparing ancestors, and session publications
before preparing days or projects. Retain at most one valid tree source cache;
release abandoned work and close the cache with the scheduler.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC
from decimal import Decimal
from threading import Lock, local
from typing import Literal, NamedTuple

from django.db import connection, connections, transaction
from django.db.models import Count, Sum

from twicc.core.enums import ItemKind
from twicc.core.models import DailyActivity, Project, Session, SessionItem, SessionType
from twicc.providers.contribution_revisions import (
    DailyKey, RevisionObservation, SourceKey, observe_revisions,
    record_contribution_commit, record_cost_publication, revisions_match,
)
from twicc.providers.tree_aggregate_state import TreeAggregateWork, tree_work_matches


class AggregateValues(NamedTuple):
    cost: Decimal | None = None
    message_count: int | None = None
    session_count: int | None = None
    self_cost: Decimal | None = None
    subagents_cost: Decimal | None = None


class PreparedAggregate(NamedTuple):
    kind: Literal['session', 'day', 'project']
    key: SourceKey | DailyKey | str
    values: AggregateValues
    observation: RevisionObservation


class ChangedPreparation(Exception):
    """The scheduler must refresh work and prepare again outside the writer."""


_worker = local()


class AggregatePreparationExecutor:
    """One scheduler-owned preparation slot with nonblocking submit/poll.

    Cancellation of the consumer must not release this slot. Future.cancel()
    cannot cancel running reads; submit rejects until that read actually ends.
    close() drains admitted work and closes connections on their owning thread.
    Async schedulers use aclose(), which also drains through cancellation.
    """

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='twicc-aggregate-prepare')
        self._lock = Lock()
        self._future = None
        self._closed = False

    def submit(self, function, /, *args, **kwargs):
        with self._lock:
            if self._closed:
                raise RuntimeError('Aggregate preparation executor is closed')
            if self._future is not None and not self._future.done():
                raise RuntimeError('Aggregate preparation is already outstanding')
            def run():
                _worker.enabled = True
                return function(*args, **kwargs)
            self._future = self._executor.submit(run)
            return self._future

    def release_work(self, work: TreeAggregateWork):
        """Release a completed or abandoned job's cached own values on the worker."""
        def release():
            cached = getattr(_worker, 'cache', None)
            if cached is not None and cached[0] == work.observation:
                _worker.cache = None
        return self.submit(release)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            def cleanup():
                _worker.cache = None
                connections.close_all()
            cleanup_future = self._executor.submit(cleanup)
        try:
            cleanup_future.result()
        finally:
            self._executor.shutdown(wait=True)

    async def aclose(self):
        task = asyncio.create_task(asyncio.to_thread(self.close))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def _snapshot(work):
    if not getattr(_worker, 'enabled', False):
        raise RuntimeError('Preparation requires its dedicated executor')
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError('Preparation cannot inherit a transaction')
    if not revisions_match(work.observation.revisions):
        raise ChangedPreparation()
    # Unit observations are captured by the caller BEFORE the first snapshot
    # read. A commit between observation and read invalidates the post-check.
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA query_only = ON')
    try:
        with transaction.atomic():
            if not tree_work_matches(work, frozenset()):
                raise ChangedPreparation()
            yield
        if not revisions_match(work.observation.revisions):
            raise ChangedPreparation()
    finally:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA query_only = OFF')


def _checked(prepared):
    if not revisions_match(prepared.observation):
        raise ChangedPreparation()
    return prepared


def _own_source_signature(work):
    observation = work.observation
    keys = {member.source for member in observation.members}
    revisions = observation.revisions
    return (observation.root, observation.members,
            tuple(pair for pair in revisions.sources if pair[0] in keys),
            revisions.roots, revisions.identity, revisions.generation)


def _own_values(work):
    cached = getattr(_worker, 'cache', None)
    if cached is not None and cached[2] == _own_source_signature(work):
        _worker.cache = work.observation, cached[1], cached[2]
        return cached[1]
    ids = [member.source.session_id for member in work.observation.members]
    costs = dict(SessionItem.objects.filter(session_id__in=ids, cost__isnull=False).order_by()
                 .values('session_id').annotate(value=Sum('cost')).values_list('session_id', 'value'))
    counts = dict(SessionItem.objects.filter(session_id__in=ids, kind=ItemKind.USER_MESSAGE).order_by()
                  .values('session_id').annotate(value=Count('pk')).values_list('session_id', 'value'))
    values = {sid: (costs.get(sid), counts.get(sid, 0)) for sid in ids}
    _worker.cache = work.observation, values, _own_source_signature(work)
    return values


def prepare_session_values(work: TreeAggregateWork, session_id: str) -> PreparedAggregate:
    members = {member.source.session_id: member for member in work.observation.members}
    member = members[session_id]
    # Descendant own costs are disjoint, including valid unflattened ancestry.
    descendants = set()
    frontier = {session_id}
    while frontier:
        frontier = {sid for sid, row in members.items() if row.baseline[2] in frontier} - descendants
        descendants.update(frontier)
    observation = observe_revisions(sources=[member.source, *[members[sid].source for sid in descendants]],
                                    costs=[members[sid].source for sid in descendants],
                                    roots=[key for key, _ in work.observation.revisions.roots])
    with _snapshot(work):
        own = _own_values(work)
        self_cost, count = own[session_id]
        child_costs = [own[sid][0] for sid in descendants if own[sid][0] is not None]
        child_cost = sum(child_costs, Decimal(0)) if child_costs else None
        total = (self_cost or Decimal(0)) + (child_cost or Decimal(0))
        values = AggregateValues(total if total > 0 else None, count, None, self_cost, child_cost)
    return _checked(PreparedAggregate('session', member.source, values, observation))


def prepare_daily_values(work: TreeAggregateWork, key: DailyKey) -> PreparedAggregate:
    if key not in work.days:
        raise ValueError('Day is outside tree obligations')
    observation = observe_revisions(days=[key])
    with _snapshot(work):
        start, end = DailyActivity.date_range(key.day)
        items = SessionItem.objects.filter(session__provider=key.provider, timestamp__gte=start, timestamp__lt=end)
        sessions = Session.objects.filter(provider=key.provider, created_at__gte=start, created_at__lt=end,
                                          type=SessionType.SESSION, hidden=False, user_message_count__gt=0)
        if key.project_id is not None:
            items = items.filter(session__project_id=key.project_id)
            sessions = sessions.filter(project_id=key.project_id)
        count = items.filter(kind=ItemKind.USER_MESSAGE, session__type=SessionType.SESSION,
                             session__hidden=False).count()
        cost = items.filter(cost__isnull=False).aggregate(value=Sum('cost'))['value'] or Decimal(0)
        values = AggregateValues(cost, count, sessions.count())
    return _checked(PreparedAggregate('day', key, values, observation))


def prepare_project_values(work: TreeAggregateWork, project_id: str) -> PreparedAggregate:
    if project_id not in work.project_ids:
        raise ValueError('Project is outside tree obligations')
    # Capture the project input fence BEFORE enumerating eligible persisted roots.
    observation = observe_revisions(projects=[project_id])
    with _snapshot(work):
        roots = Session.objects.filter(project_id=project_id, type=SessionType.SESSION)
        keys = tuple(SourceKey(*row) for row in roots.order_by().values_list('id', 'storage_incarnation'))
        costs = observe_revisions(costs=keys)
        total = roots.aggregate(value=Sum('total_cost'))['value'] or Decimal(0)
        visible_count = roots.filter(hidden=False, created_at__isnull=False, user_message_count__gt=0).count()
        values = AggregateValues(total if total > 0 else None, session_count=visible_count)
    _checked(PreparedAggregate('project', project_id, values, observation))
    return _checked(PreparedAggregate('project', project_id, values, observation._replace(costs=costs.costs)))


def publish_aggregate(work: TreeAggregateWork, prepared: PreparedAggregate,
                      pending_source_ids: frozenset[str]) -> str:
    """Caller holds writer ownership through transaction commit and callbacks."""
    if not connection.in_atomic_block:
        raise RuntimeError('Publication requires the caller writer transaction')
    if any(member.source.session_id in pending_source_ids for member in work.observation.members):
        return 'deferred'
    if not tree_work_matches(work, pending_source_ids) or not revisions_match(prepared.observation):
        return 'changed'
    values = prepared.values
    if prepared.kind == 'session':
        key = prepared.key
        session = Session.objects.only(
            'id', 'storage_incarnation', 'type', 'project_id', 'provider',
            'hidden', 'created_at', 'user_message_count',
        ).get(pk=key.session_id, storage_incarnation=key.incarnation)
        Session.objects.filter(pk=session.id).update(self_cost=values.self_cost, subagents_cost=values.subagents_cost,
                                                    total_cost=values.cost, user_message_count=values.message_count)
        from twicc.providers.compute_repair_journal import clear_own_baseline_pending
        clear_own_baseline_pending(session.id, session.storage_incarnation)
        record_cost_publication(session)
        # Creation-day counts depend on derived own-message eligibility. This
        # output change does not invalidate the tree's source/membership proof.
        if (session.type == SessionType.SESSION and not session.hidden and session.created_at is not None
                and bool(session.user_message_count) != bool(values.message_count)):
            day = session.created_at.astimezone(UTC).date()
            record_contribution_commit(days=[DailyKey(scope, session.provider, day)
                                             for scope in (session.project_id, None)])
    elif prepared.kind == 'day':
        key = prepared.key
        rows = DailyActivity.objects.filter(project_id=key.project_id, provider=key.provider, date=key.day)
        if not (values.cost or values.message_count or values.session_count):
            rows.delete()
        else:
            DailyActivity.objects.update_or_create(project_id=key.project_id, provider=key.provider, date=key.day,
                defaults={'cost': values.cost, 'user_message_count': values.message_count,
                          'session_count': values.session_count})
    elif prepared.kind == 'project':
        Project.objects.filter(pk=prepared.key).update(total_cost=values.cost, sessions_count=values.session_count)
    else:
        raise ValueError('Unknown aggregate kind')
    return 'published'
