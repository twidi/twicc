"""Process-local committed lifecycle metadata, independent of content generations."""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from threading import RLock

from django.db import connection, transaction

from twicc.core.content_storage.policy import COLD_FIELDS, cold_from_fields
from twicc.core.content_storage.types import RootDecision
from twicc.core.models import Session

_lock = RLock()
_metadata = {}
_generations = {}
_database = None
_reset_generation = 0
_revision = 0
_PAGE_SIZE = 128


def _database_identity():
    name = str(connection.settings_dict['NAME'])
    try:
        stat = Path(name).stat()
        return name, stat.st_dev, stat.st_ino
    except OSError:
        return name, id(connection.connection)


def reset_cold_cache() -> None:
    """Fence all pending fills and discard metadata after database replacement."""
    global _reset_generation, _database, _revision
    with _lock:
        _metadata.clear()
        _generations.clear()
        _database = None
        _reset_generation += 1
        _revision += 1


def invalidate_cold_metadata(session_ids: Iterable[str], *, mappings: bool = False) -> None:
    """Discard committed lifecycle changes; mapping changes can affect every descendant."""
    global _reset_generation, _revision
    with _lock:
        _revision += 1
        if mappings:
            _metadata.clear()
            _generations.clear()
            _reset_generation += 1
        else:
            for identity in set(session_ids):
                _metadata.pop(identity, None)
                # Keep tombstones: a pending read can still hold the previous generation.
                _generations[identity] = _generations.get(identity, 0) + 1


def schedule_cold_invalidation(session_ids: Iterable[str], *, mappings: bool = False) -> None:
    """Use Django's savepoint-aware commit queue as the transaction-local overlay."""
    ids = frozenset(session_ids)

    def committed():
        invalidate_cold_metadata(ids, mappings=mappings)

    committed.cold_metadata_invalidation = (ids, mappings)
    transaction.on_commit(committed)


def _pending_invalidations():
    ids = set()
    mappings = False
    # Django removes these callbacks on savepoint rollback and connection rollback.
    # Inspect only callbacks belonging to this thread's database connection.
    for _, callback, _ in connection.run_on_commit:
        pending = getattr(callback, 'cold_metadata_invalidation', None)
        if pending is not None:
            ids.update(pending[0])
            mappings |= pending[1]
    return ids, mappings


def _read_metadata(ids):
    return {row['id']: row for row in Session.objects.filter(pk__in=ids).values(
        'id', 'type', 'parent_session_id', *COLD_FIELDS,
    )}


def _load_metadata(ids):
    global _database, _reset_generation, _revision
    identity = _database_identity()
    with _lock:
        if _database != identity:
            _metadata.clear()
            _generations.clear()
            _database = identity
            _reset_generation += 1
            _revision += 1
    loaded = {}
    pending_ids, pending_mappings = _pending_invalidations()
    while missing := set(ids) - loaded.keys():
        with _lock:
            loaded.update({key: _metadata[key] for key in missing if key in _metadata
                           and not pending_mappings and key not in pending_ids})
            missing -= loaded.keys()
            reset_generation = _reset_generation
            generations = {key: _generations.get(key, 0) for key in missing}
        if not missing:
            break
        keys = sorted(missing)
        rows = {}
        for start in range(0, len(keys), _PAGE_SIZE):
            rows.update(_read_metadata(keys[start:start + _PAGE_SIZE]))
        with _lock:
            for key in missing:
                if (reset_generation != _reset_generation
                        or generations[key] != _generations.get(key, 0)):
                    if connection.in_atomic_block:
                        # Retry outside writer ownership. This publication remains conservative.
                        loaded[key] = None
                    continue
                value = rows.get(key)
                loaded[key] = value
                # Transaction-local reads must never become committed cache entries.
                # Missing ancestors are not cached, so late parents resolve on the next read.
                if not connection.in_atomic_block and value is not None:
                    _metadata[key] = value
    return loaded


def cached_lifecycle_metadata(session_ids: Iterable[str]) -> dict[str, dict | None]:
    """Copy shared committed metadata for operation-local publication proofs."""
    return {identity: None if fields is None else fields.copy()
            for identity, fields in _load_metadata(session_ids).items()}


def cached_root_decisions(
    session_ids: Iterable[str], now: datetime, active_ids: frozenset[str],
) -> dict[str, RootDecision]:
    """Resolve cached ancestry and evaluate wall time and active descendants afresh."""
    requested = frozenset(session_ids)
    _load_metadata(())  # Establish database identity before capturing the batch fence.
    while True:
        with _lock:
            revision = _revision
        decisions = _cached_root_decisions(requested, now, active_ids)
        with _lock:
            if revision == _revision:
                return decisions
        if connection.in_atomic_block:
            return {identity: RootDecision(None, False, False, False) for identity in requested}


def _cached_root_decisions(session_ids, now, active_ids):
    requested = set(session_ids)
    metadata = {}
    frontier = requested | set(active_ids)
    while frontier:
        metadata.update(_load_metadata(frontier))
        frontier = {row['parent_session_id'] for row in metadata.values() if row is not None
                    and row['type'] == 'subagent' and row['parent_session_id'] is not None
                    and row['parent_session_id'] not in metadata}

    def root(identity):
        visited = set()
        while identity not in visited:
            visited.add(identity)
            fields = metadata.get(identity)
            if fields is None:
                return None
            parent = fields['parent_session_id']
            if fields['type'] == 'session' and parent is None:
                return identity
            if fields['type'] != 'subagent' or parent is None:
                return None
            identity = parent
        return None

    active_roots = {root(identity) for identity in active_ids}
    decisions = {}
    for identity in requested:
        root_id = root(identity)
        active = root_id is not None and root_id in active_roots
        decisions[identity] = RootDecision(
            root_id, root_id is not None and not active and cold_from_fields(metadata[root_id], now),
            root_id is not None, active,
        )
    return decisions
