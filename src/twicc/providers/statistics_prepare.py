"""Prepare selective own costs and launcher totals on a reader thread."""

from datetime import datetime
from decimal import Decimal
from threading import current_thread, main_thread
from typing import NamedTuple

from django.db import connection, transaction
from django.db.models import Sum

from twicc.core.models import AgentLink, Session, SessionItem, Workflow
from twicc.providers.compute_tree import load_native_tree
from twicc.providers.contribution_revisions import (
    RevisionObservation, SourceKey, observe_own_cost_invalidations, restrict_own_cost_observation,
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
                observation, SourceKey(sid, rows[sid]['storage_incarnation']),
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
                                          persisted_cost(total) if total > 0 else None))
                if sid != tree.root_id:
                    # Unlinked components attach once to the native root. Known
                    # launcher descendants accumulate into their direct parent.
                    parent = parents.get(sid, tree.root_id)
                    if own[sid] is not None or subagents is not None:
                        descendants[parent] = (descendants[parent] or Decimal(0)) + total
        projects = tuple(sorted({row['project_id'] for row in rows.values()}))
        workflows = tuple(Workflow.objects.filter(session_id__in=members).order_by('pk').values_list('pk', flat=True))
    return PreparedTreeCosts(updates, tuple(costs), projects, workflows)
