"""One maintenance pass's committed, invalidatable own-session lifecycle metadata."""

from collections import Counter
from collections.abc import Iterable
from datetime import datetime

from django.db import connection, transaction

from twicc.core.content_storage import cold_cache
from twicc.core.content_storage.policy import COLD_FIELDS
from twicc.core.content_storage.session_policy import content_decision_from_fields
from twicc.core.content_storage.types import SessionContentDecision
from twicc.core.models import Session


class MaintenanceSessionCache:
    """Retain dates and flags, but evaluate time and runtime activity on every call."""

    def __init__(self):
        self._metadata = {}
        self._generations = {}
        self._inflight = Counter()
        self._previously_loaded = set()
        self._reload_pending = set()
        self._database = None
        self._revision = 0
        self._closed = False
        self.statistics = dict.fromkeys(
            ('owner_ids_requested', 'hits', 'ids_loaded', 'grouped_queries', 'invalidations', 'reloads'), 0,
        )
        with cold_cache._lock:
            cold_cache._maintenance_caches.add(self)

    def close(self) -> None:
        """Release pass state; closed caches cannot authorize further compression."""
        with cold_cache._lock:
            self._closed = True
            self._reset_locked()
            self._previously_loaded.clear()
            self._reload_pending.clear()
            cold_cache._maintenance_caches.discard(self)

    def _reset_locked(self):
        self._reload_pending.update(self._previously_loaded)
        self._metadata.clear()
        self._generations.clear()
        self._database = None
        self._revision += 1

    def _invalidate_locked(self, ids):
        # Count only entries this pass holds or is currently loading.
        affected = set(ids) & (self._metadata.keys() | self._inflight.keys())
        # Completed atomic reads still have a pending on_commit fill. Their
        # generations remain fenced even while no query or cached row exists.
        fenced = set(ids) & self._generations.keys()
        if not affected and not fenced:
            return
        self._reload_pending.update((affected | fenced) & self._previously_loaded)
        self.statistics['invalidations'] += len(affected)
        self._revision += 1
        for identity in affected | fenced:
            self._metadata.pop(identity, None)
            self._generations[identity] = self._generations.get(identity, 0) + 1

    def _read_metadata(self, ids):
        with cold_cache._lock:
            self.statistics['grouped_queries'] += 1
        rows = {row['id']: row for row in Session.objects.filter(pk__in=ids).values('id', *COLD_FIELDS)}
        with cold_cache._lock:
            self.statistics['ids_loaded'] += len(rows)
            self.statistics['reloads'] += len(rows.keys() & self._reload_pending)
            self._reload_pending.difference_update(rows)
            self._previously_loaded.update(rows)
        return rows

    def _load(self, requested):
        pending_ids = cold_cache._pending_lifecycle_invalidations()
        loaded = {}
        while missing := requested - loaded.keys():
            with cold_cache._lock:
                loaded.update({key: self._metadata[key] for key in missing
                               if key in self._metadata and key not in pending_ids})
                missing -= loaded.keys()
                revision = self._revision
                generations = {key: self._generations.setdefault(key, 0) for key in missing}
                self._inflight.update(missing)
            if not missing:
                break
            rows = {}
            keys = sorted(missing)
            try:
                for offset in range(0, len(keys), cold_cache._PAGE_SIZE):
                    rows.update(self._read_metadata(keys[offset:offset + cold_cache._PAGE_SIZE]))
                with cold_cache._lock:
                    for key in missing:
                        if self._closed:
                            raise RuntimeError('Maintenance session cache is closed')
                        if revision != self._revision or generations[key] != self._generations.get(key, 0):
                            if connection.in_atomic_block:
                                loaded[key] = None
                            continue
                        value = rows.get(key)
                        loaded[key] = value
                        if value is not None:
                            if connection.in_atomic_block:
                                # Savepoint rollback drops this fill with its transaction.
                                transaction.on_commit(self._committed_fill(key, value, revision))
                            else:
                                self._metadata[key] = value
            finally:
                with cold_cache._lock:
                    self._inflight.subtract(missing)
                    self._inflight += Counter()
        return loaded

    def _committed_fill(self, key, value, revision):
        def install():
            with cold_cache._lock:
                if not self._closed and revision == self._revision:
                    self._metadata[key] = value
        return install

    def decisions(
        self, session_ids: Iterable[str], now: datetime, active_ids: frozenset[str],
    ) -> dict[str, SessionContentDecision]:
        """Resolve only requested owners; reuse committed values across preparation and publication."""
        requested = frozenset(session_ids)
        identity = cold_cache._database_identity()
        with cold_cache._lock:
            if self._closed:
                raise RuntimeError('Maintenance session cache is closed')
            if self._database != identity:
                self._reset_locked()
                self._database = identity
            pending = cold_cache._pending_lifecycle_invalidations()
            self.statistics['owner_ids_requested'] += len(requested)
            self.statistics['hits'] += len((requested & self._metadata.keys()) - pending)
        while True:
            with cold_cache._lock:
                revision = self._revision
            metadata = self._load(requested)
            with cold_cache._lock:
                if self._closed:
                    raise RuntimeError('Maintenance session cache is closed')
                if revision == self._revision:
                    return {key: content_decision_from_fields(key, metadata[key], now, active_ids)
                            for key in requested}
            if connection.in_atomic_block:
                return {key: content_decision_from_fields(key, None, now, active_ids) for key in requested}
