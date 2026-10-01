"""Exact session, direct-parent, and activity contributions in writer transactions."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.db import connection, transaction

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import DailyActivity, Session, SessionItem, SessionType, WeeklyActivity

ZERO = Decimal(0)
COST_QUANTUM = Decimal("0.000001")
MAX_REPAIRED_ACTIVITY_BUCKETS = 4096


class ItemContribution(NamedTuple):
    session_id: str
    project_id: str
    provider: str
    timestamp: datetime | None
    kind: str | None
    cost: Decimal | None


class SessionContribution(NamedTuple):
    session_id: str
    project_id: str
    provider: str
    parent_session_id: str | None
    type: str
    hidden: bool
    created_at: datetime | None
    user_message_count: int


def persisted_cost(value: Decimal | str | float | None) -> Decimal | None:
    """SQLite does not enforce DecimalField scale at write time."""
    return None if value is None else Decimal(str(value)).quantize(COST_QUANTUM)


def session_contribution(session: Session) -> SessionContribution:
    return SessionContribution(session.id, session.project_id, session.provider, session.parent_session_id,
                               session.type, session.hidden, session.created_at, session.user_message_count)


def item_contributions(queryset) -> list[ItemContribution]:
    """Read persisted Decimal values, never unrounded provider calculations."""
    return [ItemContribution(*row) for row in queryset.order_by().values_list(
        'session_id', 'session__project_id', 'session__provider', 'timestamp', 'kind', 'cost',
    )]


def needs_repair(session: Session) -> bool:
    from twicc.providers.helpers import get_provider_helpers
    return session.compute_version != get_provider_helpers(session.provider).current_compute_version


def _eligible(session: SessionContribution) -> bool:
    return session.type == SessionType.SESSION and not session.hidden


def _activity_keys(project_id, provider, timestamp):
    if timestamp is not None:
        day = timestamp.astimezone(UTC).date()
        for scope in (project_id, None):
            yield DailyActivity, scope, provider, day
            yield WeeklyActivity, scope, provider, day - timedelta(days=day.weekday())


def _save_session_aggregates(session: Session) -> None:
    total = (session.self_cost or ZERO) + (session.subagents_cost or ZERO)
    session.total_cost = total if total > 0 else None
    session.save(update_fields=['self_cost', 'subagents_cost', 'total_cost', 'user_message_count'])


def _apply_activity_delta(key: tuple, delta: list) -> None:
    model, project_id, provider, day = key
    messages, count, cost = delta
    if messages == count == cost == 0:
        return
    row, _ = model.objects.get_or_create(project_id=project_id, provider=provider, date=day)
    row.user_message_count += messages
    row.session_count += count
    row.cost += cost
    if row.user_message_count == row.session_count == row.cost == 0:
        row.delete()
    else:
        row.save(update_fields=['user_message_count', 'session_count', 'cost'])


def _remember_repaired_bucket(repaired: set[tuple], key: tuple) -> None:
    if len(repaired) >= MAX_REPAIRED_ACTIVITY_BUCKETS:
        repaired.clear()
    repaired.add(key)


def apply_contribution_changes(
    before_items: Sequence[ItemContribution], after_items: Sequence[ItemContribution], *,
    before_sessions: Sequence[SessionContribution], after_sessions: Sequence[SessionContribution], repair: bool,
    repaired_activity_buckets: set[tuple] | None = None,
) -> None:
    """Maintain aggregates after source writes, under the caller's transaction.

    Full repair deliberately reads stored history. Current-version appends
    use only changed rows and indexed existence probes for nullable costs.
    A compute run may reuse an exact, committed activity bucket and apply
    later persisted deltas. Other callers continue to repair each bucket.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Contribution changes require a caller-owned transaction')
    before = {s.session_id: s for s in before_sessions}
    after = {s.session_id: s for s in after_sessions}
    session_ids = set(before) | set(after) | {i.session_id for i in (*before_items, *after_items)}
    parents = {s.parent_session_id for s in (*before_sessions, *after_sessions) if s.parent_session_id}
    sessions = {s.id: s for s in Session.objects.filter(id__in=session_ids | parents)}
    # Changes to eligibility/project/provider move the existing history too.
    metadata_changed = any(
        (b.project_id, b.provider, b.type, b.hidden) != (after[sid].project_id, after[sid].provider,
                                                        after[sid].type, after[sid].hidden)
        for sid, b in before.items() if sid in after
    )
    repair = repair or metadata_changed or any(needs_repair(sessions[pid]) for pid in parents if pid in sessions)
    cost_delta = defaultdict(lambda: ZERO)
    count_delta = defaultdict(int)
    for sign, items in ((-1, before_items), (1, after_items)):
        for item in items:
            cost_delta[item.session_id] += sign * (item.cost or ZERO)
            count_delta[item.session_id] += sign * (item.kind == ItemKind.USER_MESSAGE)
    old_self = {sid: s.self_cost for sid, s in sessions.items()}
    if repair:
        # An explicit parent repair also repairs its children's cached costs.
        # Repairing one child only needs that child and the parent's raw sum;
        # it must not recalculate every sibling for each child.
        repair_ids = session_ids | parents
        children = Session.objects.filter(parent_session_id__in=session_ids)
        for child in children:
            child.recalculate_costs()
            child.save(update_fields=['self_cost', 'subagents_cost', 'total_cost'])
        for sid in repair_ids:
            if session := sessions.get(sid):
                session.recalculate_costs()
                if sid in session_ids:
                    session.user_message_count = session.items.filter(kind=ItemKind.USER_MESSAGE).count()
                _save_session_aggregates(session)
    else:
        for sid in session_ids:
            if session := sessions.get(sid):
                session.self_cost = ((session.self_cost or ZERO) + cost_delta[sid]
                                     if session.items.filter(cost__isnull=False).exists() else None)
                session.user_message_count += count_delta[sid]
                _save_session_aggregates(session)
        parent_delta = defaultdict(lambda: ZERO)
        for sid in session_ids:
            b, a = before.get(sid), after.get(sid)
            if b and b.parent_session_id:
                # Deleted sessions do not have a row in ``sessions``.
                previous = old_self.get(sid, sum((i.cost or ZERO for i in before_items if i.session_id == sid), ZERO))
                parent_delta[b.parent_session_id] -= previous or ZERO
            if a and a.parent_session_id and sid in sessions:
                parent_delta[a.parent_session_id] += sessions[sid].self_cost or ZERO
        for pid in parents:
            if parent := sessions.get(pid):
                parent.subagents_cost = ((parent.subagents_cost or ZERO) + parent_delta[pid]
                    if Session.objects.filter(parent_session_id=pid, self_cost__isnull=False).exists() else None)
                _save_session_aggregates(parent)
    # Session counts use the cached count, exactly as PeriodicActivity does.
    after = {sid: session_contribution(sessions[sid]) for sid in after if sid in sessions}
    buckets = defaultdict(lambda: [0, 0, ZERO])
    for sign, items, states in ((-1, before_items, before), (1, after_items, after)):
        for item in items:
            state = states.get(item.session_id)
            for key in _activity_keys(item.project_id, item.provider, item.timestamp):
                buckets[key][0] += sign * int(bool(state and _eligible(state) and item.kind == ItemKind.USER_MESSAGE))
                buckets[key][2] += sign * (item.cost or ZERO)
    for sign, states in ((-1, before), (1, after)):
        for state in states.values():
            for key in _activity_keys(state.project_id, state.provider, state.created_at):
                buckets[key][1] += sign * int(_eligible(state) and state.user_message_count > 0)
    if repair:
        # Cover unchanged historical buckets as well as removed/moved rows.
        for item in item_contributions(SessionItem.objects.filter(session_id__in=session_ids)):
            for key in _activity_keys(item.project_id, item.provider, item.timestamp):
                buckets[key]
            old = before.get(item.session_id)
            if old:
                for key in _activity_keys(old.project_id, old.provider, item.timestamp):
                    buckets[key]
        for key, delta in buckets.items():
            model, project_id, provider, day = key
            # Project, provider, visibility, and session type change the
            # contribution of unchanged historical items. Their delta is not
            # represented by before_items/after_items, so recalculate them.
            if repaired_activity_buckets is not None and key in repaired_activity_buckets and not metadata_changed:
                _apply_activity_delta(key, delta)
                continue
            model.recalculate(project_id, day, Provider(provider))
            if repaired_activity_buckets is not None:
                transaction.on_commit(
                    lambda key=key: _remember_repaired_bucket(repaired_activity_buckets, key),
                )
        return
    for key, delta in buckets.items():
        _apply_activity_delta(key, delta)
