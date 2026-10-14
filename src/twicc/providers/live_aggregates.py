"""Bounded source counts and native-root statistics invalidations."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.db import connection

from twicc.core.enums import ItemKind
from twicc.core.models import DailyActivity, Session, WeeklyActivity

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


def changed_roots(before_items, after_items, before_sessions, after_sessions) -> frozenset[str]:
    """Resolve relevant bounded source changes through native parent ownership."""
    from twicc.providers.contribution_revisions import _native_root

    before = {s.session_id: s for s in before_sessions}
    after = {s.session_id: s for s in after_sessions}
    def relevant(items):
        return Counter(i for i in items if i.cost is not None or i.kind == ItemKind.USER_MESSAGE)
    old_items, new_items = relevant(before_items), relevant(after_items)
    changed = {i.session_id for i in (old_items - new_items) | (new_items - old_items)}
    for sid in before.keys() | after.keys():
        old, new = before.get(sid), after.get(sid)
        if old is None or new is None:
            changed.add(sid)
        elif (old._replace(created_at=None) != new._replace(created_at=None)
              or (old.created_at != new.created_at and (old.user_message_count or new.user_message_count))):
            changed.add(sid)
    roots = set()
    for sid in sorted(changed):
        for states in (before, after):
            state = states.get(sid)
            if state is None:
                row = Session.objects.filter(pk=sid).first()
                state = session_contribution(row) if row is not None else None
            if state is not None and (root := _native_root(state, states)) is not None:
                roots.add(root.session_id)
    return frozenset(roots)


def apply_message_count_changes(before_items, after_items) -> None:
    """Retain the bounded source count even while background metadata is partial."""
    if not connection.in_atomic_block:
        raise RuntimeError('Message count changes require a caller-owned transaction')
    counts = defaultdict(int)
    for sign, items in ((-1, before_items), (1, after_items)):
        for item in items:
            counts[item.session_id] += sign * (item.kind == ItemKind.USER_MESSAGE)
    for sid, delta in counts.items():
        if delta:
            current = Session.objects.filter(pk=sid).values_list('user_message_count', flat=True).first()
            if current is not None:
                Session.objects.filter(pk=sid).update(user_message_count=max(0, current + delta))


def apply_contribution_changes(
    before_items, after_items, *, before_sessions, after_sessions, repair=False,
    repaired_activity_buckets=None, extra_activity_keys=(),
) -> None:
    """Commit source counts and one durable invalidation per affected native root.

    Legacy repair arguments remain accepted while their callers migrate. They
    never postpone a partial-tree calculation or publish derived statistics.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Contribution changes require a caller-owned transaction')
    from twicc.providers.statistics_queue import append_roots

    apply_message_count_changes(before_items, after_items)
    roots = changed_roots(before_items, after_items, before_sessions, after_sessions)
    # Deleted roots cannot be calculated. Historical removal is a separate path.
    append_roots(sorted(Session.objects.filter(pk__in=roots).values_list('id', flat=True)))


def enqueue_sessions(session_ids) -> None:
    """Invalidate native roots for attribution-only mutations in this transaction."""
    from twicc.providers.statistics_queue import append_roots

    states = [session_contribution(s) for s in Session.objects.filter(pk__in=set(session_ids))]
    append_roots(sorted(changed_roots((), (), (), states)))
