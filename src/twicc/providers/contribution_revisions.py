"""Process-local contribution fences, advanced only after successful commits.

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
    sources: tuple[tuple[SourceKey, int], ...]
    roots: tuple[tuple[SourceKey, int], ...]
    days: tuple[tuple[DailyKey, int], ...]
    costs: tuple[tuple[SourceKey, int], ...]
    projects: tuple[tuple[str, int], ...]
    identity: tuple
    generation: int
    day_scopes: tuple = ()


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


def observe_revisions(*, sources: Iterable[SourceKey] = (), roots: Iterable[SourceKey] = (),
                      days: Iterable[DailyKey] = (), costs: Iterable[SourceKey] = (),
                      projects: Iterable[str] = ()) -> RevisionObservation:
    identity = _identity()
    domains = tuple(tuple(dict.fromkeys(keys)) for keys in (sources, roots, days, costs, projects))
    with _lock:
        observations = tuple(tuple((key, _revisions.get((identity, domain, key), 0)) for key in keys)
                             for domain, keys in enumerate(domains))
        scopes = {(scope, key.provider) for key in domains[2] for scope in (key.project_id, None)}
        scope_revisions = tuple((scope, _revisions.get((identity, 5, scope), 0)) for scope in scopes)
        return RevisionObservation(*observations, identity, _generation, scope_revisions)


def revisions_match(observation: RevisionObservation) -> bool:
    identity = _identity()
    with _lock:
        return (observation.identity == identity and observation.generation == _generation
                and all(_revisions.get((identity, domain, key), 0) == revision
                        for domain, pairs in enumerate(observation[:5]) for key, revision in pairs)
                and all(_revisions.get((identity, 5, key), 0) == revision
                        for key, revision in observation.day_scopes))


def record_contribution_commit(*, sources: Iterable[SourceKey] = (), roots: Iterable[SourceKey] = (),
                               days: Iterable[DailyKey] = (), costs: Iterable[SourceKey] = (),
                               projects: Iterable[str] = (), day_scopes: Iterable[tuple] = ()) -> None:
    identity = _identity()
    domains = tuple(frozenset(keys) for keys in (sources, roots, days, costs, projects, day_scopes))

    def committed():
        with _lock:
            for domain, keys in enumerate(domains):
                for key in keys:
                    scoped = identity, domain, key
                    _revisions[scoped] = _revisions.get(scoped, 0) + 1

    transaction.on_commit(committed)


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


def record_source_changes(before_items=(), after_items=(), *, before_sessions=(), after_sessions=(),
                          extra_activity_keys=(), force_sources=()):
    """Use retained mutation evidence; never enumerate source history here."""
    from twicc.core.models import DailyActivity, SessionType
    from twicc.providers.live_aggregates import contribution_activity_keys

    before = {state.session_id: state for state in before_sessions}
    after = {state.session_id: state for state in after_sessions}
    changed_states = {sid for sid in before.keys() | after.keys() if before.get(sid) != after.get(sid)}
    changed = set(changed_states)
    if tuple(before_items) != tuple(after_items):
        changed.update(item.session_id for item in (*before_items, *after_items))
    sources = set(force_sources)
    sources.update(SourceKey(state.session_id, state.storage_incarnation)
                   for state in (*before_sessions, *after_sessions) if state.session_id in changed)
    if not sources:
        return
    states = tuple(state for state in (*before_sessions, *after_sessions) if state.session_id in changed_states)
    keys = contribution_activity_keys((*before_items, *after_items), states) | set(extra_activity_keys)
    roots = set()
    for sid in before.keys() | after.keys():
        old, new = before.get(sid), after.get(sid)
        if old is None or new is None or (old.parent_session_id, old.type, old.storage_incarnation) != (
                new.parent_session_id, new.type, new.storage_incarnation):
            for state, mapping in ((old, before), (new, after)):
                if state is not None and (root := _native_root(state, mapping)) is not None:
                    roots.add(root)
    day_scopes = set()
    for sid in before.keys() & after.keys():
        old, new = before[sid], after[sid]
        if (old.project_id, old.provider, old.type, old.hidden) != (new.project_id, new.provider, new.type, new.hidden):
            day_scopes.update((scope, state.provider) for state in (old, new) for scope in (state.project_id, None))
    record_contribution_commit(
        sources=sources, roots=roots, day_scopes=day_scopes,
        days=(DailyKey(project, provider, day) for model, project, provider, day in keys if model is DailyActivity),
        projects=(state.project_id for state in (*before_sessions, *after_sessions)
                  if state.type == SessionType.SESSION),
    )


def record_cost_publication(session):
    """Publishing node outputs invalidates project inputs, never source inputs."""
    from twicc.core.models import SessionType

    record_contribution_commit(
        costs=[SourceKey(session.id, session.storage_incarnation)],
        projects=[session.project_id] if session.type == SessionType.SESSION else (),
    )
