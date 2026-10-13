"""Exact session, direct-parent, and activity contributions in writer transactions."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.db import connection

from twicc.core.enums import ItemKind
from twicc.core.models import DailyActivity, Session, SessionType, WeeklyActivity

ZERO = Decimal(0)
COST_QUANTUM = Decimal("0.000001")


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
    storage_incarnation: str = ""


def persisted_cost(value: Decimal | str | float | None) -> Decimal | None:
    """SQLite does not enforce DecimalField scale at write time."""
    return None if value is None else Decimal(str(value)).quantize(COST_QUANTUM)


def session_contribution(session: Session) -> SessionContribution:
    return SessionContribution(session.id, session.project_id, session.provider, session.parent_session_id,
                               session.type, session.hidden, session.created_at, session.user_message_count,
                               session.storage_incarnation)


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


def contribution_activity_keys(items=(), sessions=()) -> set[tuple]:
    """Enumerate exact UTC project/global day/week obligations without deltas."""
    return {key for row in (*items, *sessions)
            for key in _activity_keys(row.project_id, row.provider,
                                      row.timestamp if isinstance(row, ItemContribution) else row.created_at)}


def _save_session_aggregates(session: Session) -> None:
    total = (session.self_cost or ZERO) + (session.subagents_cost or ZERO)
    session.total_cost = total if total > 0 else None
    save_derived_costs(session, update_fields=['self_cost', 'subagents_cost', 'total_cost', 'user_message_count'])


def save_derived_costs(session: Session, *, update_fields) -> None:
    from twicc.providers.contribution_revisions import record_cost_publication

    previous = Session.objects.filter(pk=session.pk).values_list('self_cost', 'subagents_cost', 'total_cost').first()
    session.save(update_fields=update_fields)
    if previous != (session.self_cost, session.subagents_cost, session.total_cost):
        record_cost_publication(session)


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


def apply_contribution_changes(
    before_items: Sequence[ItemContribution], after_items: Sequence[ItemContribution], *,
    before_sessions: Sequence[SessionContribution], after_sessions: Sequence[SessionContribution], repair: bool,
    repaired_activity_buckets: set[tuple] | None = None,
    extra_activity_keys: Iterable[tuple] = (),
) -> None:
    """Retain source obligations and update established live baselines by delta.

    Invalid baselines and scope changes await guarded tree continuation. The
    compatibility cache argument has no effect; no historical repair runs here.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Contribution changes require a caller-owned transaction')
    from twicc.providers.contribution_revisions import record_source_changes

    extra_activity_keys = tuple(extra_activity_keys)
    record_source_changes(before_items, after_items, before_sessions=before_sessions,
                          after_sessions=after_sessions, extra_activity_keys=extra_activity_keys)
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
    from twicc.providers.compute_repair_journal import merge_compute_repair, own_baseline_pending

    keys = contribution_activity_keys((*before_items, *after_items), (*before_sessions, *after_sessions))
    keys.update(extra_activity_keys)
    invalid = False
    for sid in session_ids:
        states = [state for state in (*before_sessions, *after_sessions) if state.session_id == sid]
        if not states:
            continue
        incarnation = states[-1].storage_incarnation
        if not incarnation:
            continue
        baseline_pending = sid in sessions and (needs_repair(sessions[sid])
            or (repair and tuple(before_items) != tuple(after_items)))
        invalid |= baseline_pending or own_baseline_pending(sid, incarnation)
        merge_compute_repair(sid, incarnation, activity_keys=keys,
            parent_ids=[state.parent_session_id for state in states if state.parent_session_id],
            project_ids=[state.project_id for state in states], source_changed=True,
            activity_scopes=((scope, state.provider) for state in states for scope in (state.project_id, None)),
            baseline_pending=baseline_pending)
    if repair or metadata_changed or invalid:
        return
    cost_delta = defaultdict(lambda: ZERO)
    count_delta = defaultdict(int)
    for sign, items in ((-1, before_items), (1, after_items)):
        for item in items:
            cost_delta[item.session_id] += sign * (item.cost or ZERO)
            count_delta[item.session_id] += sign * (item.kind == ItemKind.USER_MESSAGE)
    old_self = {sid: s.self_cost for sid, s in sessions.items()}
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
        if (parent := sessions.get(pid)) and not needs_repair(parent) and not own_baseline_pending(
                parent.id, parent.storage_incarnation):
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
    for key in extra_activity_keys:
        buckets[key]
    for key, delta in buckets.items():
        _apply_activity_delta(key, delta)
