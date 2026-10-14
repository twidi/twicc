"""Narrow own-cost invalidations, advanced only after background commits.

Writer ownership must enclose transaction exit so commit callbacks complete
before a prepared operation can validate its observations under that ownership.
"""
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from threading import RLock
from typing import NamedTuple

from django.db import connection, transaction


class DailyKey(NamedTuple):
    project_id: str | None
    provider: str
    day: date


class SourceKey(NamedTuple):
    session_id: str
    incarnation: str


class RevisionObservation(NamedTuple):
    identity: tuple
    generation: int
    own_costs: tuple[tuple[SourceKey, int], ...] = ()


_lock = RLock()
_revisions = {}
_generation = 0


def _identity():
    name = str(connection.settings_dict['NAME'])
    try:
        stat = Path(name).stat()
        return connection.alias, name, stat.st_dev, stat.st_ino
    except OSError:
        # Shared-cache SQLite test databases use a URI across worker connections.
        return connection.alias, name


def revisions_match(observation: RevisionObservation) -> bool:
    return observations_match((observation,))


def observations_match(observations: Iterable[RevisionObservation]) -> bool:
    """Validate a publication slice with one database identity lookup."""
    identity = _identity()
    with _lock:
        return all(
            observation.identity == identity and observation.generation == _generation
            and all(_revisions.get((identity, key), 0) == revision
                    for key, revision in observation.own_costs)
            for observation in observations
        )


def reset_contribution_revisions() -> None:
    global _generation
    with _lock:
        _revisions.clear()
        _generation += 1


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


def observe_own_cost_invalidations() -> RevisionObservation:
    """Capture background-only counters before opening a statistics snapshot."""
    identity = _identity()
    with _lock:
        costs = tuple((key, revision) for (scope, key), revision in _revisions.items()
                      if scope == identity)
        return RevisionObservation(identity, _generation, own_costs=costs)


def restrict_own_cost_observation(observation: RevisionObservation, source: SourceKey, lookup: dict | None = None) -> RevisionObservation:
    """Keep one source counter, including an implicit zero captured before reads."""
    return observation._replace(own_costs=((source, (dict(observation.own_costs) if lookup is None else lookup).get(source, 0)),))


def record_own_cost_invalidation(session_id: str, storage_incarnation: str) -> None:
    """Advance only after background item-cost or final metadata publication commits.

    Call inside the transaction which resets the own-cost marker. Writer admission
    must enclose commit, including this callback, and publication validation.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Own-cost invalidation requires the source transaction')
    identity = _identity()
    source = SourceKey(session_id, storage_incarnation)

    def committed():
        with _lock:
            key = identity, source
            _revisions[key] = _revisions.get(key, 0) + 1

    transaction.on_commit(committed)
