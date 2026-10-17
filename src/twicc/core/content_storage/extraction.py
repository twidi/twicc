"""Bounded storage preparation and independently guarded publication.

Preparation runs outside atomic blocks. Each assignment rechecks the observed
session, native root and final lifecycle decision inside its own transaction.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from time import perf_counter
from datetime import datetime

import orjson

from django.db import connection, transaction
from django.db.models import BinaryField, F, Max, Q
from django.db.models.functions import Cast, Length
from django.utils import timezone

from twicc.core.content_storage.dictionaries import get_provider_dictionary
from twicc.core.content_storage.codec import ContentIntegrityError, encode_plain, transcode_content
from twicc.core.content_storage.policy import (
    COLD_FIELDS, compression_policy_version,
)
from twicc.core.content_storage.references import (
    bind_persisted_items, intern_contents, prepare_intern_contents, prepared_content_guard, reconcile_session_memberships,
)
from twicc.core.content_storage.state import resolve_root_decision
from twicc.core.content_storage.session_policy import content_decision_from_fields, resolve_content_decisions
from twicc.core.content_storage.types import (
    ContentBinding, StorageCursor, ItemStorageObservation,
    PreparedComputeBatch, ComputeStorageProgress, ComputeStorageObservation,
    ContentMetadataObservation, ReconciliationAction,
)
from twicc.core.content_storage.writes import write_item_content_assignments
from twicc.core.models import Session, SessionItem, SessionItemContent
from twicc.sync_diagnostics import timed_sync_phase

logger = logging.getLogger(__name__)

ROW_BUDGET = 256
BYTE_BUDGET = 8 * 1024 * 1024
_publication_cold: ContextVar[dict | None] = ContextVar('publication_cold_decisions', default=None)
_placement: ContextVar[dict | None] = ContextVar('compute_content_placement', default=None)


class SupersededStorageResult(ValueError):
    """A committed compute observation no longer identifies the current history."""


def guard_storage(session_id, incarnation, epoch, upper_line, offset=None):
    """Acquire the write lock before validating history and session boundaries."""
    from twicc.providers.compute_base import BaseSessionCompute

    outcome = BaseSessionCompute.guard_compute_revision(session_id, offset, incarnation, epoch, upper_line)
    if outcome != 'ok':
        raise SupersededStorageResult(outcome)
    placement = _placement.get()
    if placement is not None and placement.get('operation') is not None:
        validate_compute_application(placement['operation'])
    if placement is not None:
        root_id, actual = pending_root_fields(session_id, {}, placement['active_session_ids'])
        if root_id != placement['root_id'] or actual and (
            actual['storage_incarnation'] != placement['root_incarnation']
            or actual['content_state_epoch'] != placement['root_epoch']
            or any(actual[name] != value for name, value in placement['initial_fields'].items())
        ):
            raise SupersededStorageResult('superseded')


def pending_root_fields(session_id, pending, _active_session_ids):
    decision = resolve_root_decision(session_id, timezone.now(), frozenset())
    if not decision.resolved:
        return None, None
    fields = Session.objects.filter(pk=decision.root_id).values(
        'storage_incarnation', 'content_state_epoch', *COLD_FIELDS,
    ).get()
    effective = dict(fields)
    if session_id == decision.root_id:
        for name in COLD_FIELDS:
            if name in pending:
                value = pending[name]
                effective[name] = datetime.fromisoformat(value) if isinstance(value, str) else value
    return decision.root_id, effective


@contextmanager
def _publication_decisions():
    """Reuse eligibility only during one guarded publication with its writer lease."""
    token = _publication_cold.set({})
    try:
        yield
    finally:
        _publication_cold.reset(token)


def cold_placement(session_id):
    cache = _publication_cold.get()
    if cache is None:
        return _cold_placement(session_id)
    if session_id not in cache:
        cache[session_id] = _cold_placement(session_id)
    return cache[session_id]


def _cold_placement(session_id):
    """Evaluate each owner's lifecycle, using this result's final target fields."""
    placement = _placement.get()
    if placement is None:
        return False
    if session_id == placement['pending_session_id']:
        if placement.get('operation') is not None and placement['operation'].plaintext_required:
            return False
        fields = placement['session_final_fields']
        return content_decision_from_fields(session_id, fields, placement['now'], placement['active_session_ids']).is_cold
    return resolve_content_decisions([session_id], placement['now'], placement['active_session_ids'])[session_id].is_cold


def prepare_texts(texts, provider, session_id):
    return prepare_intern_contents(texts, provider, cold=cold_placement(session_id))


def _read_page(session_id, upper_line, cursor, row_budget, byte_budget):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Storage budgets must be positive')
    rows = []
    size = 0
    queryset = SessionItem.objects.filter(session_id=session_id, line_num__gt=cursor, line_num__lte=upper_line)
    for item in queryset.order_by('line_num').only(
        'id', 'session_id', 'line_num', 'content', 'current_content_id', 'original_content_id',
    ).iterator(chunk_size=1):
        item_size = len(item.content.encode('utf-8'))
        if rows and (len(rows) >= row_budget or size + item_size > byte_budget):
            break
        rows.append(item)
        size += item_size
        if len(rows) >= row_budget or size >= byte_budget:
            break
    return rows, size


def extract_storage_slice(session_id: str, observed_incarnation: str, observed_epoch: int,
                          upper_line: int, cursor: int, row_budget: int, byte_budget: int) -> StorageCursor:
    """Move one exact legacy slice; preserve NULL provenance and current authority."""
    batch = _prepare_extraction(session_id, upper_line, cursor, row_budget, byte_budget)
    return _publish_extraction(session_id, observed_incarnation, observed_epoch, upper_line, batch)


def _prepare_extraction(session_id, upper_line, cursor, row_budget, byte_budget):
    rows, size = _read_page(session_id, upper_line, cursor, row_budget, byte_budget)
    rows = tuple(ItemStorageObservation(*(getattr(item, name) for name in ItemStorageObservation._fields))
                 for item in rows)
    provider = Session.objects.values_list('provider', flat=True).get(pk=session_id)
    texts = [item.content for item in rows if item.current_content_id is None]
    prepared = prepare_texts(texts, provider, session_id)
    last_line = rows[-1].line_num if rows else cursor
    done = not SessionItem.objects.filter(session_id=session_id, line_num__gt=last_line,
                                          line_num__lte=upper_line).exists()
    return PreparedComputeBatch('extraction', last_line, rows, tuple(prepared.items()), provider, size, done)


@_publication_decisions()
def _publish_extraction(session_id, observed_incarnation, observed_epoch, upper_line, batch):
    rows, size, provider = batch.rows, batch.bytes, batch.provider
    texts = [item.content for item in rows if item.current_content_id is None]
    prepared = dict(batch.prepared)
    with transaction.atomic():
        placement = _placement.get()
        guard_storage(session_id, observed_incarnation, observed_epoch, upper_line,
                      placement['observed_offset'] if placement else None)
        actual = list(SessionItem.objects.filter(pk__in=[i.id for i in rows], session_id=session_id).only(
            'id', 'session_id', 'line_num', 'content', 'current_content_id', 'original_content_id',
        ))
        expected = {item.id: (item.content, item.current_content_id, item.original_content_id) for item in rows}
        if len(actual) != len(rows) or any(
            expected[item.id] != (item.content, item.current_content_id, item.original_content_id) for item in actual
        ):
            raise SupersededStorageResult('superseded')
        if any(record.policy_version is not None for record in prepared.values()) and not cold_placement(session_id):
            raise SupersededStorageResult('superseded')
        with prepared_content_guard(lambda: cold_placement(session_id)):
            ids = intern_contents(texts, provider, prepared, initial_owners=[session_id])
        bindings = []
        changed = [item for item in actual if item.current_content_id is None or item.content != '']
        for item in changed:
            if item.current_content_id is None:
                item.current_content_id = ids[item.content]
                bindings.append(ContentBinding(item.id, session_id, item.current_content_id, item.original_content_id))
            item.content = ''
        from twicc.core.content_storage.references import _reference_operation
        operation = _reference_operation.get()
        if operation is None:
            write_item_content_assignments(changed)
        else:
            with operation.capture_mutation([item.id for item in changed]):
                write_item_content_assignments(changed)
        bind_persisted_items(bindings)
        _settle_placement_epoch(placement)
    return StorageCursor(batch.cursor, len(rows), size, batch.done)


def apply_override_slice(msg, overrides, operation):
    batch = _prepare_overrides(msg, overrides)
    return _publish_overrides(msg, batch, operation)


def _prepare_overrides(msg, overrides):
    provider = Session.objects.values_list('provider', flat=True).get(pk=msg['session_id'])
    texts = [override['content'] for override in overrides]
    prepared = prepare_texts(texts, provider, msg['session_id'])
    return PreparedComputeBatch('overrides', None, tuple(MappingProxyType(dict(row)) for row in overrides),
                                tuple(prepared.items()), provider,
                                sum(len(text.encode('utf-8')) for text in texts), False)


@_publication_decisions()
def _publish_overrides(msg, batch, operation):
    overrides, provider = batch.rows, batch.provider
    texts = [override['content'] for override in overrides]
    prepared = dict(batch.prepared)
    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
        items = {item.id: item for item in SessionItem.objects.filter(
            pk__in=[override['id'] for override in overrides], session_id=msg['session_id'],
        )}
        assignments = {}
        for override in overrides:
            if override['id'] in assignments and assignments[override['id']] != override['content']:
                raise ContentIntegrityError('Conflicting content assignments for the same item')
            assignments[override['id']] = override['content']
        displaced = []
        for override in overrides:
            item = items.get(override['id'])
            if item is None:
                raise SupersededStorageResult('superseded')
            if 'observed_current_id' in override:
                matches = item.current_content_id == override['observed_current_id']
                if item.current_content_id is None:
                    matches &= encode_plain(item.content).sha256.hex() == override.get('observed_legacy_digest')
                if not matches:
                    # A retry can encounter a previously committed identical result.
                    if item.current_content_id is None or bytes(item.current_content.sha256) != encode_plain(
                        override['content'],
                    ).sha256:
                        raise SupersededStorageResult('superseded')
            if item.current_content_id is not None:
                displaced.append(item.current_content_id)
        if any(record.policy_version is not None for record in prepared.values()) and not cold_placement(msg['session_id']):
            raise SupersededStorageResult('superseded')
        with prepared_content_guard(lambda: cold_placement(msg['session_id'])):
            ids = intern_contents(texts, provider, prepared, initial_owners=[msg['session_id']])
        changed = []
        for item_id, text in assignments.items():
            item = items[item_id]
            if item.current_content_id != ids[text] or item.content != '':
                item.current_content_id = ids[text]
                item.content = ''
                changed.append(item)
        with operation.capture_mutation([item.id for item in changed]):
            write_item_content_assignments(changed)
        bind_persisted_items([ContentBinding(item.id, item.session_id, item.current_content_id,
                                            item.original_content_id) for item in items.values()])
        placement = _placement.get()
        _settle_placement_epoch(placement)
        reconcile_session_memberships(msg['session_id'], displaced)


def _owners_cold(row, cache=None):
    # Ownership metadata is maintained from both actual FK assignments by the
    # shared reference service. Unknown/deleted owners cannot authorize cold.
    placement = _placement.get()
    if placement is None or not row.session_ids:
        return False
    # The reference service validates actual persisted bindings at assignment
    # and maintains this complete owner list through both FK slots. Repeating
    # reverse item scans here is quadratic without child FK indexes.
    cache = {} if cache is None else cache
    for start in range(0, len(row.session_ids), 128):
        owner_ids = [owner for owner in row.session_ids[start:start + 128] if owner not in cache]
        if not owner_ids:
            continue
        others = [owner for owner in owner_ids if owner != placement['pending_session_id']]
        decisions = resolve_content_decisions(others, placement['now'], placement['active_session_ids'])
        cache.update({owner: decision.is_cold for owner, decision in decisions.items()})
        if placement['pending_session_id'] in owner_ids:
            cache[placement['pending_session_id']] = cold_placement(placement['pending_session_id'])
    return all(cache.get(owner, False) for owner in row.session_ids)


def reconcile_storage_slice(msg, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    """Bound payload retention and resume between an item's two content references."""
    batch = _prepare_reconciliation(msg, cursor, row_budget, byte_budget)
    result = _publish_reconciliation(msg, batch)
    msg['_storage_reconcile_position'] = batch.cursor
    if batch.done:
        msg.pop('_storage_reconciled_contents', None)
    return result


def _prepare_reconciliation(msg, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Storage budgets must be positive')
    position = msg.get('_storage_reconcile_position', (cursor, 1))
    line, slot = position
    query = SessionItem.objects.filter(session_id=msg['session_id'], line_num__lte=msg['observed_upper_line'])
    query = query.filter(line_num__gte=line) if slot == 0 else query.filter(line_num__gt=line)
    rows = list(query.order_by('line_num').values_list('line_num', 'current_content_id', 'original_content_id')[:row_budget])
    ids = {pk for _, current, original in rows for pk in (current, original) if pk is not None}
    metadata = {row['id']: row for row in SessionItemContent.objects.filter(pk__in=ids).annotate(
        stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
    ).values('id', 'codec', 'raw_size', 'stored_size', 'owner_size')}
    prepared = []
    observations = {}
    eligibility = {}
    representations = {}
    visited = []
    admitted = set()
    size = 0
    last_line = cursor
    owner_cache = {}
    dictionaries = {}
    dictionary_cache = {}
    placement = _placement.get()
    operation = placement.get('operation') if placement else None
    seen = operation.reconciled_contents if operation else msg.setdefault('_storage_reconciled_contents', {})
    incoming_cold = cold_placement(msg['session_id'])
    stopped = False
    for item_line, current, original in rows:
        for item_slot, pk in enumerate((current, original)):
            if item_line == line and item_slot <= slot:
                continue
            meta = metadata.get(pk)
            if pk is not None and meta is None:
                raise ContentIntegrityError('Missing referenced content')
            skip = meta is None or pk in admitted or not incoming_cold and meta['codec'] == 'plain'
            cost = 0 if skip else meta['raw_size'] + meta['stored_size'] + meta['owner_size']
            if cost and admitted and size + cost > byte_budget:
                stopped = True
                break
            if not skip:
                admitted.add(pk)
                size += cost
                row = SessionItemContent.objects.defer('payload').get(pk=pk)
                observed = _metadata_observation(row)
                observations[pk] = observed
                owners_cold = _owners_cold(row, owner_cache)
                eligibility[pk] = owners_cold
                if owners_cold or row.codec != 'plain':
                    if owners_cold:
                        if row.dictionary_provider not in dictionaries:
                            entry = get_provider_dictionary(row.dictionary_provider)
                            dictionaries[row.dictionary_provider] = entry.row if entry else None
                        dictionary = dictionaries[row.dictionary_provider]
                        if dictionary is not None:
                            policy = compression_policy_version(
                                row.dictionary_provider, dictionary.id, dictionary=dictionary,
                            )
                            if seen.get(pk) == (observed, owners_cold, policy):
                                pass
                            elif row.codec == 'zstd' and row.dictionary_id == dictionary.id:
                                if row.compression_attempt_version != policy or row.compression_no_gain:
                                    prepared.append((row, None, policy, False))
                            elif not (row.compression_attempt_version == policy and row.compression_no_gain):
                                try:
                                    encoded, no_gain = transcode_content(
                                        row, dictionary.id, dictionary_cache=dictionary_cache,
                                    )
                                except ContentIntegrityError:
                                    raise
                                except Exception:
                                    logger.warning('Optional initial content compression failed', exc_info=True)
                                else:
                                    representation = None if no_gain and row.codec == 'plain' else encoded
                                    prepared.append((row, representation, policy, no_gain))
                    else:
                        encoded, _ = transcode_content(row, dictionary_cache=dictionary_cache)
                        prepared.append((row, encoded, row.compression_attempt_version, row.compression_no_gain))
            visited.append((item_line, item_slot, pk))
            position = (item_line, item_slot)
            if item_slot == 1:
                last_line = item_line
        if stopped:
            break
    done = position[1] == 1 and not SessionItem.objects.filter(
        session_id=msg['session_id'], line_num__gt=last_line, line_num__lte=msg['observed_upper_line'],
    ).exists()
    actions = []
    for row, encoded, policy, no_gain in prepared:
        actions.append(ReconciliationAction(row.id, observations[row.id], encoded, policy, no_gain))
        if 'payload' in row.__dict__:
            representations[row.id] = bytes(row.payload)
    return PreparedComputeBatch('reconciliation', position, tuple(visited), tuple(actions), None, size, done,
                                last_line, tuple(observations.values()), tuple(representations.items()),
                                tuple(eligibility.items()))


def _metadata_observation(row):
    return ContentMetadataObservation(*(bytes(row.sha256) if name == 'sha256' else
                                       tuple(row.session_ids) if name == 'session_ids' else getattr(row, name)
                                       for name in ContentMetadataObservation._fields))


@_publication_decisions()
def _publish_reconciliation(msg, batch):
    visited, prepared = batch.rows, batch.prepared
    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
        actual_items = {values[0]: values[1:] for values in SessionItem.objects.filter(
            session_id=msg['session_id'], line_num__in={entry[0] for entry in visited},
        ).values_list('line_num', 'current_content_id', 'original_content_id')}
        if any(item_line not in actual_items or actual_items[item_line][item_slot] != pk
               for item_line, item_slot, pk in visited):
            raise SupersededStorageResult('superseded')
        publication_cache = {}
        actual_rows = {row.id: row for row in SessionItemContent.objects.defer('payload').filter(
            pk__in={observed.id for observed in batch.observations},
        )}
        representations = dict(batch.representations)
        eligibility = dict(batch.eligibility)
        for observed in batch.observations:
            actual = actual_rows.get(observed.id)
            if actual is None or _metadata_observation(actual) != observed:
                raise SupersededStorageResult('superseded')
            if _owners_cold(actual, publication_cache) != eligibility[observed.id]:
                raise SupersededStorageResult('superseded')
        for content_id, observed, encoded, policy, no_gain in prepared:
            actual = actual_rows[content_id]
            if content_id in representations and bytes(actual.payload) != representations[content_id]:
                raise SupersededStorageResult('superseded')
            if (encoded.codec == 'zstd' if encoded is not None else actual.codec == 'zstd') and not _owners_cold(
                actual, publication_cache,
            ):
                raise SupersededStorageResult('superseded')
            updates = {'compression_attempt_version': policy, 'compression_no_gain': no_gain}
            if encoded is not None:
                updates.update(payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id)
            SessionItemContent.objects.filter(pk=actual.id).update(**updates)
            for name, value in updates.items():
                setattr(actual, name, value)
        placement = _placement.get()
        operation = placement.get('operation') if placement else None
        seen = operation.reconciled_contents if operation else msg.setdefault('_storage_reconciled_contents', {})
        settled = {}
        for pk, actual in actual_rows.items():
            policy = None
            if eligibility[pk]:
                dictionary = get_provider_dictionary(actual.dictionary_provider)
                policy = dictionary.policy_version if dictionary else None
            settled[pk] = (_metadata_observation(actual), eligibility[pk], policy)
        transaction.on_commit(lambda: seen.update(settled))
    return StorageCursor(batch.line_num, len({entry[0] for entry in visited}), batch.bytes, batch.done)


def initialize_compute_session(session_id):
    """Bounded writer initialization before read-only worker dispatch."""
    from twicc.core.content_storage.ingestion import initialize_ingestion_session
    from twicc.core.content_storage.state import snapshot_root_storage

    with transaction.atomic():
        # Reserve SQLite's writer before reading. Django session saves can
        # commit outside our coordinator and invalidate a deferred read snapshot.
        Session.objects.filter(pk=session_id).update(storage_incarnation=F('storage_incarnation'))
        session = Session.objects.filter(pk=session_id).first()
        if session is None:
            return
        initialize_ingestion_session(session)
        root = resolve_root_decision(session_id, timezone.now(), frozenset())
        if root.resolved:
            snapshot_root_storage(root.root_id)


_TARGET_FIELDS = ('storage_incarnation', 'history_epoch', 'last_offset', 'last_line', 'type', 'parent_session_id',
                  *COLD_FIELDS)


def _native_ancestry(session_id):
    ancestry = []
    seen = set()
    while session_id is not None and session_id not in seen:
        seen.add(session_id)
        row = Session.objects.filter(pk=session_id).values_list(
            'id', 'storage_incarnation', 'type', 'parent_session_id',
        ).first()
        ancestry.append(row or (session_id, None, None, None))
        if row is None or row[2] != 'subagent':
            break
        session_id = row[3]
    return tuple(ancestry)


def _settle_placement_epoch(placement):
    if placement is not None and placement['root_id'] is not None:
        placement['root_epoch'] = Session.objects.values_list('content_state_epoch', flat=True).get(
            pk=placement['root_id'])


class ComputeStorageOperation:
    """Worker-owned mutable state; only successful publications settle cursors."""

    def __init__(self, msg, observation, placement, references, *, progressive_facts=False):
        self.message = msg
        self.observation = observation
        self.placement = placement
        self.references = references
        self.phase = 'counts'
        self.cursor = 0
        self.closed = False
        self.item_row_budget = 2000
        self.pending_batch = None
        self.reconciled_contents = {}
        self.history_facts_stage = None
        self.progressive_facts = progressive_facts
        self.history_facts_published = False
        self.validation = None
        self.plaintext_required = False
        self.reconciled_requires_plaintext = False
        self.return_to_validation = False
        self.final_message = None
        self.final_transaction = None
        self.stopped_agents = None
        self.stopped_agents_complete = False
        self.preparation_ms = 0.0
        self.max_publication_ms = 0.0

    @property
    def connection_identity(self):
        return self.references._connection


@contextmanager
def _compute_scope(operation, active_ids):
    operation.references.validate_connection()
    placement = dict(operation.placement)
    _refresh_active_placement(placement, active_ids)
    placement['operation'] = operation
    token = _placement.set(placement)
    try:
        with operation.references.activate():
            yield placement
    finally:
        _placement.reset(token)


def _refresh_active_placement(placement, active_ids):
    placement.update(active_session_ids=frozenset(active_ids), now=timezone.now())


def validate_compute_application(operation):
    """Read-only immutable fence, usable inside TEMP preparation snapshots."""
    if operation.closed:
        raise SupersededStorageResult('superseded')
    operation.references.validate_connection()
    if operation.progressive_facts:
        from twicc.providers.history_facts import history_facts_are_current

        session = Session.objects.only('provider', 'compute_version').filter(
            pk=operation.observation.session_id).first()
        if session is None or history_facts_are_current(session):
            raise SupersededStorageResult('superseded')
    return _validate_observation(operation.observation, operation.placement['root_epoch'])


def _validate_observation(observation, expected_epoch):
    target = Session.objects.filter(pk=observation.session_id).values_list(*_TARGET_FIELDS).first()
    if target != observation.target or _native_ancestry(observation.session_id) != observation.ancestry:
        raise SupersededStorageResult('superseded')
    decision = resolve_root_decision(observation.session_id, timezone.now(), frozenset())
    if decision.root_id != observation.root_id:
        raise SupersededStorageResult('superseded')
    if observation.root_id is not None:
        root = Session.objects.filter(pk=observation.root_id).values(
            'storage_incarnation', 'content_state_epoch', *COLD_FIELDS,
        ).first()
        if root is None or root['storage_incarnation'] != observation.root_incarnation or (
            root['content_state_epoch'] != expected_epoch
            or tuple(root[name] for name in COLD_FIELDS) != observation.initial_fields
        ):
            raise SupersededStorageResult('superseded')
    return True


def begin_compute_application(msg, *, progressive_facts=False):
    """Capture exact target/root observations under a short write-first guard."""
    from twicc.core.content_storage.references import SessionReferenceOperation
    from twicc.providers.compute_base import BaseSessionCompute

    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
        if 'history_facts' not in msg:
            raise ValueError('session_complete is missing history_facts')
        BaseSessionCompute.validate_item_fields(msg.get('item_fields', ()))
        initialize_compute_session(msg['session_id'])
        session = Session.objects.get(pk=msg['session_id'])
        upper_line = msg.get('observed_upper_line')
        if upper_line is None:
            upper_line = max(session.last_line,
                             SessionItem.objects.filter(session_id=session.id).aggregate(last=Max('line_num'))['last'] or 0)
        msg = {**msg, 'observed_incarnation': msg.get('observed_incarnation') or session.storage_incarnation,
               'observed_epoch': msg.get('observed_epoch', session.history_epoch), 'observed_upper_line': upper_line}
        root_id, final = pending_root_fields(session.id, msg.get('session_fields', {}), frozenset())
        final = final or {}
        initial = Session.objects.filter(pk=root_id).values(*COLD_FIELDS).first()
        session_final = {name: getattr(session, name) for name in COLD_FIELDS}
        for name in COLD_FIELDS:
            if name in msg.get('session_fields', {}):
                value = msg['session_fields'][name]
                session_final[name] = datetime.fromisoformat(value) if isinstance(value, str) else value
        placement = {'session_final_fields': session_final, 'root_id': root_id,
                     'root_incarnation': final.get('storage_incarnation'),
                     'root_epoch': final.get('content_state_epoch'), 'initial_fields': initial,
                     'final_fields': {name: final[name] for name in COLD_FIELDS if name in final},
                     'active_session_ids': frozenset(), 'now': timezone.now(),
                     'pending_session_id': session.id, 'observed_offset': msg.get('observed_last_offset')}
        observation = ComputeStorageObservation(session.id, tuple(getattr(session, name) for name in _TARGET_FIELDS),
                                               _native_ancestry(session.id), root_id,
                                               placement['root_incarnation'],
                                               tuple(initial[name] for name in COLD_FIELDS) if initial else ())
    estimate = max(1, (len(msg.get('content_overrides') or ()) + ROW_BUDGET - 1) // ROW_BUDGET
                   + (session.last_line + ROW_BUDGET - 1) // ROW_BUDGET)
    references = SessionReferenceOperation(session.id, session.last_line, estimate)
    if root_id is None:
        references.uses_counts = False
        references.initialized = True
    references.validate_connection()
    operation = ComputeStorageOperation(msg, observation, placement, references, progressive_facts=progressive_facts)
    return operation


def prepare_compute_batch(operation):
    """Prepare one bounded batch outside admission and main writer ownership."""
    if operation.pending_batch is not None:
        raise ValueError('A prepared compute batch already exists')
    started = perf_counter()
    msg = operation.message
    try:
        with _compute_scope(operation, msg.get('_active_session_ids', frozenset())):
            if operation.phase == 'counts':
                def validate_page():
                    if not validate_compute_application(operation):
                        raise SupersededStorageResult('superseded')
                    return True
                operation.references.initialize_page(row_budget=ROW_BUDGET,
                                                      validate=validate_page)
                batch = PreparedComputeBatch('counts', operation.references.initialization_cursor, (), (), None, 0,
                                             operation.references.initialized)
            elif operation.phase == 'metadata':
                updates = msg.get('item_updates') or ()
                end = min(len(updates), operation.cursor + operation.item_row_budget)
                batch = PreparedComputeBatch('metadata', end,
                                             tuple(MappingProxyType(dict(row)) for row in updates[operation.cursor:end]),
                                             (), None, 0, end == len(updates))
            elif operation.phase == 'overrides':
                overrides = msg.get('content_overrides') or ()
                chunk, size = [], 0
                for row in overrides[operation.cursor:operation.cursor + ROW_BUDGET]:
                    length = len(row['content'].encode('utf-8'))
                    if chunk and size + length > BYTE_BUDGET:
                        break
                    chunk.append(row)
                    size += length
                batch = _prepare_overrides(msg, chunk)._replace(cursor=operation.cursor + len(chunk),
                                                                done=operation.cursor + len(chunk) == len(overrides))
            elif operation.phase == 'extraction':
                batch = _prepare_extraction(msg['session_id'], msg['observed_upper_line'], operation.cursor,
                                            ROW_BUDGET, BYTE_BUDGET)
            elif operation.phase == 'stopped_agents':
                from twicc.providers.compute_lifecycle import (
                    STOP_AGENT_BUDGET, STOP_BYTE_BUDGET, normalize_agent_stops,
                )
                if operation.stopped_agents is None:
                    operation.stopped_agents = normalize_agent_stops(msg.get('agent_stopped') or ())
                chunk, size = [], 0
                for update in operation.stopped_agents[operation.cursor:operation.cursor + STOP_AGENT_BUDGET]:
                    length = len(orjson.dumps((update.session_id, update.stopped_at)))
                    if chunk and size + length > STOP_BYTE_BUDGET:
                        break
                    chunk.append(update)
                    size += length
                end = operation.cursor + len(chunk)
                batch = PreparedComputeBatch('stopped_agents', end, tuple(chunk), (), None, size,
                                             end == len(operation.stopped_agents))
            elif operation.phase == 'reconciliation':
                local = {**msg, '_storage_reconcile_position': operation.cursor}
                batch = _prepare_reconciliation(local, operation.cursor[0], ROW_BUDGET, BYTE_BUDGET)
            elif operation.phase == 'validation':
                from twicc.core.content_storage.validation import prepare_validation_page

                batch = prepare_validation_page(operation)
            elif operation.phase == 'history_stage':
                prepare_compute_history_stage(operation)
                batch = PreparedComputeBatch('history_stage', 0, (), (), None, 0, True)
            elif operation.phase in {'history_delete', 'history_insert'}:
                from twicc.providers.history_facts import prepare_fact_delete_page, prepare_fact_insert_page

                prepare = prepare_fact_delete_page if operation.phase == 'history_delete' else prepare_fact_insert_page
                page = prepare(operation.history_facts_stage, operation.cursor)
                batch = PreparedComputeBatch(operation.phase, page.cursor, page.positions, (), None, page.bytes, page.done)
            else:
                raise ValueError('Compute storage preparation is complete')
    finally:
        operation.preparation_ms += (perf_counter() - started) * 1000
    operation.pending_batch = batch
    logger.debug('Compute storage preparation: session=%s phase=%s rows=%d bytes=%d elapsed_ms=%.1f cursor=%s',
                 msg['session_id'], batch.phase, len(batch.rows), batch.bytes,
                 (perf_counter() - started) * 1000, batch.cursor)
    return batch


def publish_compute_batch(operation, batch, active_ids):
    """Publish one guarded batch; settle Python state only after atomic exit."""
    from twicc.providers.compute_base import BaseSessionCompute

    if batch is not operation.pending_batch or batch.phase != operation.phase:
        raise ValueError('Prepared compute batch does not match operation')
    started = perf_counter()
    msg = operation.message
    with _compute_scope(operation, active_ids) as placement, transaction.atomic():
        guard_storage(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                      msg['observed_upper_line'], msg.get('observed_last_offset'))
        from twicc.core.content_storage.validation import restart_stronger_validation

        if restart_stronger_validation(operation, placement):
            return ComputeStorageProgress('reconciliation', (-1, 1), placement['root_epoch'], 0, 0, False)
        validation = None
        if batch.phase == 'validation':
            from twicc.core.content_storage.validation import publish_validation_page

            validation = publish_validation_page(operation, batch)
        elif batch.phase == 'metadata' and batch.rows:
            outcome = BaseSessionCompute.apply_session_items_chunk(
                msg['session_id'], msg.get('observed_last_offset'), msg.get('item_fields', []),
                list(batch.rows), [],
                msg['observed_incarnation'], msg['observed_epoch'], msg['observed_upper_line'])
            if outcome != 'ok':
                raise SupersededStorageResult(outcome)
        elif batch.phase == 'overrides':
            _publish_overrides(msg, batch, operation.references)
        elif batch.phase == 'extraction':
            _publish_extraction(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                                msg['observed_upper_line'], batch)
        elif batch.phase == 'stopped_agents':
            from twicc.providers.compute_lifecycle import publish_agent_stops

            publish_agent_stops({**msg, '_storage_final_root': placement}, batch.rows)
        elif batch.phase == 'reconciliation':
            _publish_reconciliation(msg, batch)
        elif batch.phase in {'history_delete', 'history_insert'}:
            from twicc.providers.history_facts import FactPage, publish_fact_delete_page, publish_fact_insert_page

            publish = publish_fact_delete_page if batch.phase == 'history_delete' else publish_fact_insert_page
            publish(operation.history_facts_stage, FactPage(batch.cursor, batch.rows, batch.bytes, batch.done))
        from twicc.core.content_storage.validation import requires_plaintext

        reconciliation_requires_plaintext = requires_plaintext(operation, placement)
        _settle_placement_epoch(placement)
        epoch = placement['root_epoch']
    operation.max_publication_ms = max(operation.max_publication_ms, (perf_counter() - started) * 1000)

    def adopt():
        operation.placement['root_epoch'] = epoch
        if batch.phase == 'reconciliation' and batch.done:
            operation.reconciled_requires_plaintext = reconciliation_requires_plaintext
        if validation is not None:
            operation.validation = validation
        operation.pending_batch = None
        operation.cursor = batch.cursor
        if batch.done:
            if batch.phase == 'stopped_agents':
                operation.stopped_agents_complete = True
            if batch.phase == 'history_insert':
                operation.history_facts_published = True
            next_phase = {'counts': 'metadata', 'metadata': 'overrides', 'overrides': 'extraction',
                          'extraction': 'stopped_agents', 'stopped_agents': 'reconciliation',
                          'reconciliation': ('validation' if operation.return_to_validation else
                                             'history_stage' if operation.progressive_facts else 'final'),
                          'history_stage': 'history_delete', 'history_delete': 'history_insert',
                          'history_insert': 'validation', 'validation': 'final'}[batch.phase]
            if batch.phase == 'reconciliation':
                operation.return_to_validation = False
            operation.phase = next_phase
            operation.cursor = (-1 if next_phase in {'extraction', 'history_insert'}
                                else (-1, 1) if next_phase == 'reconciliation' else 0)

    if operation.progressive_facts:
        transaction.on_commit(adopt)
    else:
        # Detached compatibility runs inside its caller's transaction and closes
        # this private handle before returning. Its final checks remain exhaustive.
        adopt()
    logger.debug('Compute storage publication: session=%s phase=%s rows=%d bytes=%d elapsed_ms=%.1f cursor=%s',
                 msg['session_id'], batch.phase, len(batch.rows), batch.bytes,
                 (perf_counter() - started) * 1000, batch.cursor)
    return ComputeStorageProgress(operation.phase, operation.cursor, epoch, len(batch.rows), batch.bytes,
                                  operation.phase == 'final')


def final_compute_message(operation, active_ids) -> dict | None:
    # None retains the application and requests bounded repair before final apply.
    if operation.phase != 'final' or operation.pending_batch is not None:
        raise ValueError('Compute storage publication is incomplete')
    with _compute_scope(operation, active_ids) as placement, transaction.atomic():
        msg = operation.message
        guard_storage(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                      msg['observed_upper_line'], msg.get('observed_last_offset'))
        if operation.progressive_facts:
            from twicc.core.content_storage.validation import restart_stronger_validation

            if restart_stronger_validation(operation, placement):
                return None
        final = {**operation.message, 'item_updates': [], 'content_overrides': [], '_storage_prepared': True,
                 '_storage_operation': operation, '_storage_final_root': dict(placement),
                 '_active_session_ids': active_ids}
    operation.final_message = final
    return final


def prepare_compute_history_stage(operation):
    """Build private facts on the retained worker before activity version sampling."""
    from twicc.providers.history_facts import HistoryFact, prepare_compute_history_facts

    if operation.phase not in {'history_stage', 'final'} or operation.pending_batch is not None:
        raise ValueError('Compute storage publication is incomplete')
    validate_compute_application(operation)
    if operation.history_facts_stage is None:
        started = perf_counter()
        try:
            with timed_sync_phase('compute_history_facts_preparation'):
                operation.history_facts_stage = prepare_compute_history_facts(
                    operation.message['session_id'],
                    (HistoryFact(**fact) for fact in operation.message['history_facts']),
                )
            validate_compute_application(operation)
        finally:
            if operation.phase == 'final':
                operation.preparation_ms += (perf_counter() - started) * 1000


def close_compute_application(operation):
    if not operation.closed:
        try:
            if operation.history_facts_stage is not None:
                operation.history_facts_stage.close()

            operation.references.close()
        finally:
            operation.reconciled_contents.clear()
            operation.closed = True
            operation.pending_batch = None
            operation.validation = None
            operation.final_message = None
            operation.final_transaction = None


def prepare_compute_result(msg, item_row_budget=2000):
    """Synchronous compatibility wrapper sharing the resumable phase primitives."""
    if item_row_budget < 1:
        raise ValueError('Metadata row budget must be positive')
    if msg.get('_storage_prepared'):
        return msg
    operation = begin_compute_application(msg)
    operation.item_row_budget = item_row_budget
    try:
        while operation.phase != 'final':
            batch = prepare_compute_batch(operation)
            publish_compute_batch(operation, batch, msg.get('_active_session_ids', frozenset()))
        final = final_compute_message(operation, msg.get('_active_session_ids', frozenset()))
        if final is None:
            raise ValueError('Detached compatibility completion cannot restart progressive validation')
        # Compatibility callers publish after this handle's TEMP scope ends.
        final.pop('_storage_operation')
        final['agent_stopped'] = []
        final['_storage_observation'] = operation.observation
        final['_storage_final_root'].pop('operation')
        return final
    finally:
        close_compute_application(operation)


def validate_final_compute_operation(msg):
    """Called only after the final helper acquires its write-first guard."""
    operation = msg.get('_storage_operation')
    if operation is not None:
        validate_compute_application(operation)
    elif msg.get('_storage_observation') is not None:
        _validate_observation(msg['_storage_observation'], msg['_storage_final_root']['root_epoch'])
    pending = msg.get('_storage_final_root')
    if pending is not None:
        _refresh_active_placement(pending, msg.get('_active_session_ids', ()))
        plaintext_needed = not content_decision_from_fields(
            msg['session_id'], pending['session_final_fields'], timezone.now(), pending['active_session_ids'],
        ).is_cold
        if operation is not None and operation.progressive_facts:
            from twicc.core.content_storage.validation import requires_plaintext

            if (msg is not operation.final_message or operation.phase != 'final'
                    or operation.pending_batch is not None or operation.validation is None
                    or not operation.validation.complete
                    or requires_plaintext(operation, pending)
                    and not operation.validation.requires_plaintext):
                raise SupersededStorageResult('superseded')
            operation.final_transaction = connection.atomic_blocks[-1]
            return
        if plaintext_needed and SessionItem.objects.filter(session_id=msg['session_id']).filter(
            Q(current_content__codec='zstd') | Q(original_content__codec='zstd'),
        ).exists():
            raise SupersededStorageResult('superseded')


def finish_storage_result(msg):
    """Verify this computed session and optionally certify the root's own plaintext."""
    from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION
    from twicc.core.content_storage.state import (
        _finish_plaintext_root_storage, _session_has_invalid_storage_metadata, ensure_storage_incarnation,
    )

    session_id = msg['session_id']
    pending = msg.get('_storage_final_root')
    if pending:
        root_id, actual = pending_root_fields(session_id, {}, pending['active_session_ids'])
        if root_id != pending['root_id'] or (actual and (
            actual['storage_incarnation'] != pending['root_incarnation']
            or actual['content_state_epoch'] != pending['root_epoch']
            or any(actual[name] != value for name, value in pending['final_fields'].items())
        )):
            raise SupersededStorageResult('superseded')
    decision = resolve_root_decision(session_id, timezone.now(), frozenset())
    content = resolve_content_decisions(
        [session_id], timezone.now(), pending['active_session_ids'] if pending else frozenset(),
    )[session_id]
    if decision.resolved and decision.root_id == session_id and not content.is_cold:
        _finish_plaintext_root_storage(session_id, active_root_ids=pending['active_session_ids'] if pending else frozenset())
    else:
        plaintext_needed = not content.is_cold
        if _session_has_invalid_storage_metadata(session_id, requires_plaintext=plaintext_needed):
            if plaintext_needed:
                raise ContentIntegrityError('Hot compute storage requires plaintext through both references')
            raise ContentIntegrityError('Compute storage placement is incomplete')
        ensure_storage_incarnation(session_id)
        Session.objects.filter(pk=session_id).update(content_storage_version=CONTENT_STORAGE_VERSION)
    operation = msg.get('_storage_operation')
    if operation is not None:
        epoch = (Session.objects.values_list('content_state_epoch', flat=True).get(pk=decision.root_id)
                 if decision.resolved else None)
        transaction.on_commit(lambda: operation.placement.update(root_epoch=epoch))
