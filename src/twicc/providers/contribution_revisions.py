"""Shared native-root and activity contribution keys."""
from datetime import date
from typing import NamedTuple


class DailyKey(NamedTuple):
    project_id: str | None
    provider: str
    day: date


class SourceKey(NamedTuple):
    session_id: str
    incarnation: str


def _native_root(state, states):
    from twicc.core.models import Session, SessionType
    from twicc.providers.live_aggregates import session_contribution

    visited = set()
    while state is not None and state.session_id not in visited:
        visited.add(state.session_id)
        if state.type != SessionType.SUBAGENT or not state.parent_session_id:
            return SourceKey(state.session_id, state.storage_incarnation)
        parent = states.get(state.parent_session_id)
        if parent is None:
            row = Session.objects.filter(pk=state.parent_session_id).only(
                'id', 'storage_incarnation', 'project_id', 'provider', 'parent_session_id',
                'type', 'hidden', 'created_at', 'user_message_count',
            ).first()
            parent = session_contribution(row) if row is not None else None
        state = parent
    return None
