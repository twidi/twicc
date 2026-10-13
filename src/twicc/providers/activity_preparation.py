"""Private, post-source exact activity repairs for one compute operation.

The application worker must exclusively serve this operation until terminal
cleanup. Other workers/processes use other physical connections. No transaction,
iterator, cursor, or context manager survives a step. Final cache admission
occurs only within the guarded final transaction.
"""

import logging
from datetime import datetime
from threading import get_ident
from typing import NamedTuple

from django.db import connection, transaction

from twicc.core.enums import Provider
from twicc.core.models import Session, SessionItem
from twicc.providers.compute_repair_journal import (
    RepairObservation, RepairObservationChanged, RepairSnapshot, load_compute_repair, require_compute_repair,
)
from twicc.providers.live_aggregates import MAX_REPAIRED_ACTIVITY_BUCKETS, _activity_keys

ACTIVITY_PAGE_ROWS = 256
logger = logging.getLogger(__name__)


class ActivityPreparation:
    """Worker-owned bounded evidence, separate from retained repair obligations."""

    def __init__(self, operation):
        self.operation = operation
        self.physical_connection = operation.connection_identity
        self.thread = get_ident()
        self.data_version = None
        self.required_keys = set()
        self.skipped_keys = set()
        self.repaired_keys = set()
        self.ordered_keys = ()
        self.enumeration_cursor = -1
        self.enumeration_complete = False
        self.repair_cursor = 0
        self.fallback_reason = None
        self.scope = None
        self.metadata = None
        self.repair_observation: RepairObservation | None = None

    def disable(self, reason, *, discard=True):
        if self.fallback_reason is None:
            self.fallback_reason = reason
        if discard:
            self.repaired_keys.clear()
        return False


class ActivityKeyPage(NamedTuple):
    rows: int
    cursor: int
    complete: bool
    fallback_reason: str | None


class ActivityPublication(NamedTuple):
    published: bool
    complete: bool
    fallback_reason: str | None


class _ActivityFallback(Exception):
    pass


def _check_state(state, *, fresh=True):
    """Check identity and source completion before any connection use."""
    if state.fallback_reason is not None:
        return False
    operation = state.operation
    if (get_ident() != state.thread or connection.connection is not state.physical_connection
            or operation.connection_identity is not state.physical_connection):
        return state.disable('connection_changed')
    if connection.needs_rollback or connection.closed_in_transaction:
        return state.disable('connection_unusable')
    try:
        transaction_open = (connection.in_atomic_block or not connection.get_autocommit()
                            or state.physical_connection.in_transaction)
    except Exception:
        return state.disable('connection_unusable')
    if fresh and transaction_open:
        return state.disable('transaction_open')
    if operation.closed or operation.phase != 'final' or operation.pending_batch is not None:
        return state.disable('source_incomplete')
    return True


def _data_version():
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA main.data_version')
        return cursor.fetchone()[0]


def _check_version(state):
    if _data_version() != state.data_version:
        return state.disable('foreign_commit')
    return True


def _validate_operation(state):
    from twicc.core.content_storage.extraction import validate_compute_application

    validate_compute_application(state.operation)


def begin_activity_preparation(operation):
    """Start one fixed-version enumeration, or select permanent exact fallback.

    Repeated calls return the same state. No restart adopts a newer version.
    The unbounded correctness inventory stays on operation.activity_repair_keys.
    """
    if operation.activity_preparation is not None:
        return operation.activity_preparation
    state = ActivityPreparation(operation)
    operation.activity_preparation = state
    if not _check_state(state):
        return state
    try:
        state.data_version = _data_version()
        _validate_operation(state)
        snapshot = load_compute_repair(operation.message['session_id'], operation.message['observed_incarnation'])
        operation.repair_snapshot = snapshot
        if snapshot is not None:
            state.repair_observation = snapshot.observation
            # This inventory is correctness evidence, independent of the proof cap.
            operation.activity_repair_keys.update(snapshot.activity_keys)
        row = Session.objects.filter(pk=operation.message['session_id']).values(
            'project_id', 'provider', 'type', 'hidden', 'created_at',
        ).get()
        pending = operation.message.get('session_fields', {})
        fields = ('project_id', 'provider', 'type', 'hidden')
        if any(pending.get(field, row[field]) != row[field] for field in fields):
            state.disable('metadata_changed')
            return state
        state.scope = row['project_id'], row['provider']
        state.metadata = tuple(row[field] for field in fields)
        created = pending.get('created_at', row['created_at'])
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        # Creation counts always need exact final repair. Keep this exclusion
        # fixed even when the final before/effective creation metadata changes.
        state.skipped_keys.update(_activity_keys(*state.scope, row['created_at']))
        state.skipped_keys.update(_activity_keys(pending.get('project_id', row['project_id']),
                                               pending.get('provider', row['provider']), created))
        if len(operation.activity_repair_keys) > MAX_REPAIRED_ACTIVITY_BUCKETS:
            state.disable('capacity')
            return state
        state.required_keys.update(operation.activity_repair_keys)
        _check_version(state)
    except Exception:
        state.disable('initialization_failed')
    except BaseException:
        state.disable('cancelled')
        raise
    return state


def prepare_activity_key_page(state, *, row_budget=ACTIVITY_PAGE_ROWS):
    """Read one bounded timestamp-only page with fixed-version fences."""
    if not 1 <= row_budget <= ACTIVITY_PAGE_ROWS:
        raise ValueError('Activity page budget must be between 1 and 256')
    if not _check_state(state):
        return _page_result(state, 0)
    try:
        if not _check_version(state):
            return _page_result(state, 0)
        _validate_operation(state)
        if state.enumeration_complete:
            return _page_result(state, 0)
        operation = state.operation
        # Materialize only bounded metadata; no QuerySet/iterator crosses calls.
        rows = tuple(SessionItem.objects.filter(
            session_id=operation.message['session_id'], line_num__gt=state.enumeration_cursor,
            line_num__lte=operation.message['observed_upper_line'],
        ).order_by('line_num').values_list('line_num', 'timestamp')[:row_budget])
        keys = {key for _, timestamp in rows for key in _activity_keys(*state.scope, timestamp)}
        if not _check_state(state) or not _check_version(state):
            return _page_result(state, 0)
        if len(state.required_keys | keys) > MAX_REPAIRED_ACTIVITY_BUCKETS:
            state.disable('capacity')
            return _page_result(state, len(rows))
        state.required_keys.update(keys)
        if rows:
            state.enumeration_cursor = rows[-1][0]
        state.enumeration_complete = len(rows) < row_budget
        if state.enumeration_complete:
            state.ordered_keys = tuple(sorted(state.required_keys - state.skipped_keys, key=lambda key: (
                key[0]._meta.label, key[1] or '', key[2], key[3],
            )))
        return _page_result(state, len(rows))
    except Exception:
        state.disable('enumeration_failed')
        return _page_result(state, 0)
    except BaseException:
        state.disable('cancelled')
        raise


def _page_result(state, rows):
    return ActivityKeyPage(rows, state.enumeration_cursor, state.enumeration_complete, state.fallback_reason)


def publish_activity_key(state, key):
    """Repair exactly one inventoried key in a fresh write-first transaction.

    The writer/admission lease belongs to the caller. The version sampled under
    writer ownership remains fixed. Commit callbacks publish only Python evidence;
    they never read a replacement epoch or write through this connection.
    """
    from twicc.core.content_storage.extraction import guard_storage

    if not _check_state(state):
        return _publication_result(state, False)
    if not state.enumeration_complete:
        state.disable('enumeration_incomplete')
        return _publication_result(state, False)
    if (key not in state.required_keys or state.repair_cursor >= len(state.ordered_keys)
            or key != state.ordered_keys[state.repair_cursor]):
        state.disable('missing_key')
        return _publication_result(state, False)
    previous_keys = set(state.repaired_keys)
    previous_cursor = state.repair_cursor
    try:
        with transaction.atomic():
            msg = state.operation.message
            # No main-database read or PRAGMA precedes this target UPDATE.
            guard_storage(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                          msg['observed_upper_line'], msg.get('observed_last_offset'))
            if not _check_state(state, fresh=False):
                raise _ActivityFallback()
            _validate_operation(state)
            if not _check_version(state):
                raise _ActivityFallback()
            if state.repair_observation is not None:
                try:
                    require_compute_repair(state.repair_observation)
                except RepairObservationChanged:
                    state.disable('journal_changed')
                    raise _ActivityFallback()
            model, project_id, provider, day = key
            model.recalculate(project_id, day, Provider(provider))
            sampled_version = _data_version()
            if sampled_version != state.data_version:
                state.disable('foreign_commit')
                raise _ActivityFallback()

            def settle():
                # No SQL after commit can silently refresh the fixed baseline.
                if _check_state(state) and sampled_version == state.data_version:
                    state.repaired_keys.add(key)
                    state.repair_cursor = previous_cursor + 1

            transaction.on_commit(settle)
        return _publication_result(state, key in state.repaired_keys and state.fallback_reason is None)
    except _ActivityFallback:
        return _publication_result(state, False)
    except Exception:
        # A callback can fail after SQL commit and after another callback settles.
        # Restore earlier evidence, but prohibit every shortcut on this result.
        state.repaired_keys = previous_keys
        state.repair_cursor = previous_cursor
        state.disable('publication_failed', discard=False)
        return _publication_result(state, False)
    except BaseException:
        state.repaired_keys = previous_keys
        state.repair_cursor = previous_cursor
        state.disable('cancelled', discard=False)
        raise


def _publication_result(state, published):
    return ActivityPublication(published, state.enumeration_complete
                               and state.repair_cursor == len(state.ordered_keys)
                               and state.fallback_reason is None, state.fallback_reason)


def close_activity_preparation(state, *, reason='closed'):
    """Discard private evidence on cancellation or terminal operation cleanup."""
    state.disable(reason)
    state.required_keys.clear()
    state.skipped_keys.clear()
    state.ordered_keys = ()


def activity_final_policy(state, before_session, effective_fields, *, item_updates=(), content_overrides=(),
                          extra_activity_keys=(), repair_snapshot: RepairSnapshot | None = None):
    """Admit private zero-delta keys under the final transaction's writer lock.

    Initial storage validation occurs before final source/lifecycle writes.
    Do not repeat that immutable observation after this transaction's own writes.
    The fixed physical connection/version and actual before/effective metadata
    are rechecked here, directly before the aggregate helper consumes the cache.
    """
    cache = None
    if state is not None:
        if not connection.in_atomic_block:
            state.disable('final_transaction_missing')
        elif _check_state(state, fresh=False):
            try:
                version_matches = _check_version(state)
                if version_matches and state.repair_observation is not None:
                    if repair_snapshot is None or repair_snapshot.observation != state.repair_observation:
                        version_matches = state.disable('journal_changed')
                    else:
                        # The final no-op merge may retain generation. A changed
                        # union or actual source write cannot reuse this proof.
                        current = require_compute_repair(state.repair_observation)
                        if not current.activity_keys <= state.required_keys:
                            version_matches = state.disable('missing_key')
                elif version_matches and repair_snapshot is not None:
                    version_matches = state.disable('journal_changed')
            except RepairObservationChanged:
                version_matches = state.disable('journal_changed')
            except Exception:
                version_matches = state.disable('final_validation_failed')
            if version_matches:
                fields = ('project_id', 'provider', 'type', 'hidden')
                before = tuple(getattr(before_session, field) for field in fields)
                effective = tuple(effective_fields.get(field, getattr(before_session, field)) for field in fields)
                if item_updates or content_overrides:
                    state.disable('source_incomplete')
                elif before != state.metadata or effective != state.metadata:
                    state.disable('metadata_changed')
                elif (not state.enumeration_complete or state.repair_cursor != len(state.ordered_keys)
                      or set(state.ordered_keys) != state.required_keys - state.skipped_keys
                      or state.repaired_keys != state.required_keys - state.skipped_keys
                      or not state.operation.activity_repair_keys <= state.required_keys
                      or any(key not in state.required_keys for key in extra_activity_keys)):
                    state.disable('missing_key')
                elif len(state.required_keys) > MAX_REPAIRED_ACTIVITY_BUCKETS:
                    state.disable('capacity')
                else:
                    created = effective_fields.get('created_at', before_session.created_at)
                    if isinstance(created, str):
                        created = datetime.fromisoformat(created)
                    excluded = state.skipped_keys | set(_activity_keys(
                        before_session.project_id, before_session.provider, before_session.created_at))
                    excluded.update(_activity_keys(effective[0], effective[1], created))
                    cache = state.repaired_keys - excluded
    required = len(state.required_keys) if state is not None else 0
    hits = len(cache) if cache is not None else 0
    logger.debug('Compute activity final policy: fallback=%s retry_count=0 cache_hits=%d cache_misses=%d',
                 state.fallback_reason if state is not None and state.fallback_reason else
                 ('none' if cache is not None else 'missing_proof'), hits, required - hits)
    return cache
