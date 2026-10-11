"""Bounded progressive storage writes within one compute writer lease.

Preparation runs outside atomic blocks. Each assignment rechecks the observed
session, native root and final lifecycle decision inside its own transaction.
"""

import logging
from contextvars import ContextVar
from datetime import datetime

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from twicc.core.content_storage.codec import ContentIntegrityError, decode_content, encode_cold, encode_plain
from twicc.core.content_storage.policy import COLD_FIELDS, cold_from_fields, compression_policy_version
from twicc.core.content_storage.references import (
    bind_persisted_items, intern_contents, prepared_content_guard, reconcile_session_memberships,
)
from twicc.core.content_storage.state import resolve_root_decision
from twicc.core.content_storage.types import ContentBinding, StorageCursor
from twicc.core.models import ContentDictionary, Session, SessionItem, SessionItemContent

logger = logging.getLogger(__name__)

ROW_BUDGET = 256
BYTE_BUDGET = 8 * 1024 * 1024
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
    if placement is not None:
        root_id, actual = pending_root_fields(session_id, {}, placement['active_roots'])
        if root_id != placement['root_id'] or actual and (
            actual['storage_incarnation'] != placement['root_incarnation']
            or actual['content_state_epoch'] != placement['root_epoch']
            or any(actual[name] != value for name, value in placement['initial_fields'].items())
        ):
            raise SupersededStorageResult('superseded')


def pending_root_fields(session_id, pending, active_roots):
    decision = resolve_root_decision(session_id, timezone.now(), active_roots)
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


def cold_placement(session_id):
    """Recheck real native ancestry and guarded final lifecycle values."""
    placement = _placement.get()
    if placement is None or placement['unresolved_active']:
        return False
    root_id, effective = pending_root_fields(session_id, {}, placement['active_roots'])
    if (root_id is None or root_id in placement['active_roots']
            or session_id == placement['pending_session_id'] and root_id != placement['root_id']):
        return False
    if root_id != placement['pending_session_id']:
        from twicc.providers.helpers import get_provider_helpers

        root = Session.objects.only('provider', 'compute_version').get(pk=root_id)
        if root.compute_version != get_provider_helpers(root.provider).current_compute_version:
            return False
    if root_id == placement['root_id']:
        # Representation updates invalidate epochs; incarnation and lifecycle
        # fields remain stable under the held writer/admission lease.
        if (effective['storage_incarnation'] != placement['root_incarnation']
                or effective['content_state_epoch'] != placement['root_epoch']
                or any(effective[name] != value for name, value in placement['initial_fields'].items())):
            return False
        effective.update(placement['final_fields'])
    return cold_from_fields(effective, placement['now'])


def prepare_texts(texts, provider, session_id):
    if not cold_placement(session_id):
        return {}
    dictionary = ContentDictionary.objects.filter(provider=provider).order_by('-version').first()
    if dictionary is None:
        return {}
    policy = compression_policy_version(provider, dictionary.id)
    prepared = {}
    for text in texts:
        try:
            encoded, _ = encode_cold(text, dictionary.id, policy)
        except Exception:
            logger.warning('Optional initial content compression failed', exc_info=True)
            continue
        prepared[encoded.sha256] = encoded
    return prepared


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
    rows, size = _read_page(session_id, upper_line, cursor, row_budget, byte_budget)
    provider = Session.objects.values_list('provider', flat=True).get(pk=session_id)
    texts = [item.content for item in rows if item.current_content_id is None]
    prepared = prepare_texts(texts, provider, session_id)
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
        with prepared_content_guard(lambda: cold_placement(session_id)):
            ids = intern_contents(texts, provider, prepared)
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
            SessionItem.objects.bulk_update(changed, ['current_content', 'content'])
        else:
            with operation.capture_mutation([item.id for item in changed]):
                SessionItem.objects.bulk_update(changed, ['current_content', 'content'])
        bind_persisted_items(bindings)
        if bindings and placement is not None and placement['root_id'] is not None:
            placement['root_epoch'] += 1
    last_line = rows[-1].line_num if rows else cursor
    done = not SessionItem.objects.filter(session_id=session_id, line_num__gt=last_line,
                                          line_num__lte=upper_line).exists()
    return StorageCursor(last_line, len(rows), size, done)


def apply_override_slice(msg, overrides, operation):
    texts = [override['content'] for override in overrides]
    provider = Session.objects.values_list('provider', flat=True).get(pk=msg['session_id'])
    prepared = prepare_texts(texts, provider, msg['session_id'])
    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
        items = {item.id: item for item in SessionItem.objects.filter(
            pk__in=[override['id'] for override in overrides], session_id=msg['session_id'],
        )}
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
        with prepared_content_guard(lambda: cold_placement(msg['session_id'])):
            ids = intern_contents(texts, provider, prepared)
        with operation.capture_mutation(list(items)):
            for override in overrides:
                item = items[override['id']]
                item.current_content_id = ids[override['content']]
                item.content = ''
            SessionItem.objects.bulk_update(list(items.values()), ['current_content', 'content'])
        bind_persisted_items([ContentBinding(item.id, item.session_id, item.current_content_id,
                                            item.original_content_id) for item in items.values()])
        placement = _placement.get()
        if items and placement is not None and placement['root_id'] is not None:
            placement['root_epoch'] += 1
        reconcile_session_memberships(msg['session_id'], displaced)


def _owners_cold(row):
    # Ownership metadata is maintained from both actual FK assignments by the
    # shared reference service. Unknown/deleted owners cannot authorize cold.
    placement = _placement.get()
    if placement is None or not row.session_ids:
        return False
    # The reference service validates actual persisted bindings at assignment
    # and maintains this complete owner list through both FK slots. Repeating
    # reverse item scans here is quadratic without child FK indexes.
    for start in range(0, len(row.session_ids), 128):
        owner_ids = row.session_ids[start:start + 128]
        owners = list(Session.objects.filter(pk__in=owner_ids).values('id', 'content_storage_version'))
        if len(owners) != len(owner_ids) or any(
            owner['id'] != placement['pending_session_id'] and owner['content_storage_version'] != 1 for owner in owners
        ):
            return False
        if not all(cold_placement(owner) for owner in owner_ids):
            return False
    return True


def reconcile_storage_slice(msg, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    """Prepare requested references outside atomic; revalidate every published representation."""
    rows, _ = _read_page(msg['session_id'], msg['observed_upper_line'], cursor, row_budget, byte_budget)
    prepared = []
    size = 0
    last_line = cursor
    for item in rows:
        # Stop before fetching another oversized pair, but always advance one item.
        if last_line != cursor and size >= byte_budget:
            break
        for row in SessionItemContent.objects.filter(pk__in={item.current_content_id, item.original_content_id} - {None}):
            if any(entry[0].id == row.id for entry in prepared):
                continue
            owners_cold = _owners_cold(row)
            if not owners_cold and row.codec == 'plain':
                continue
            text = decode_content(row)
            if owners_cold:
                dictionary = ContentDictionary.objects.filter(provider=row.dictionary_provider).order_by('-version').first()
                if dictionary is None:
                    continue
                policy = compression_policy_version(row.dictionary_provider, dictionary.id)
                if row.codec == 'zstd' and row.dictionary_id == dictionary.id:
                    # Retain exact existing compressed bytes; only establish attempt metadata.
                    from twicc.core.content_storage.types import EncodedContent

                    encoded = EncodedContent(bytes(row.payload), row.codec, row.dictionary_id, row.raw_size,
                                             bytes(row.sha256))
                    no_gain = False
                elif row.compression_attempt_version == policy and row.compression_no_gain:
                    continue
                else:
                    try:
                        encoded, no_gain = encode_cold(text, dictionary.id, policy)
                    except Exception:
                        logger.warning('Optional initial content compression failed', exc_info=True)
                        continue
            else:
                policy, no_gain = row.compression_attempt_version, row.compression_no_gain
                encoded = encode_plain(text)
            prepared.append((row, encoded, policy, no_gain))
            size += row.raw_size
        last_line = item.line_num
    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
        actual_ids = set()
        for current, original in SessionItem.objects.filter(session_id=msg['session_id'],
                                                            line_num__gt=cursor, line_num__lte=last_line).values_list(
                                                                'current_content_id', 'original_content_id'):
            actual_ids.update(pk for pk in (current, original) if pk is not None)
        for observed, encoded, policy, no_gain in prepared:
            actual = SessionItemContent.objects.get(pk=observed.id)
            if (actual.id not in actual_ids or actual.codec != observed.codec
                    or actual.dictionary_id != observed.dictionary_id or actual.session_ids != observed.session_ids):
                raise SupersededStorageResult('superseded')
            if encoded.codec == 'zstd' and not _owners_cold(actual):
                continue
            SessionItemContent.objects.filter(pk=actual.id).update(
                payload=encoded.payload, codec=encoded.codec, dictionary_id=encoded.dictionary_id,
                compression_attempt_version=policy, compression_no_gain=no_gain,
            )
    return StorageCursor(last_line, len(rows), size, not SessionItem.objects.filter(
        session_id=msg['session_id'], line_num__gt=last_line, line_num__lte=msg['observed_upper_line'],
    ).exists())


def initialize_compute_session(session_id):
    """Bounded writer initialization before read-only worker dispatch."""
    from twicc.core.content_storage.ingestion import initialize_ingestion_session
    from twicc.core.content_storage.state import snapshot_root_storage

    with transaction.atomic():
        session = Session.objects.filter(pk=session_id).first()
        if session is None:
            return
        initialize_ingestion_session(session)
        root = resolve_root_decision(session_id, timezone.now(), frozenset())
        if root.resolved:
            snapshot_root_storage(root.root_id)


def prepare_compute_result(msg, item_row_budget=2000):
    """Keep reference accounting on one connection across separately committed slices."""
    from twicc.core.content_storage.references import session_reference_operation
    from twicc.providers.compute_base import BaseSessionCompute

    if msg.get('_storage_prepared'):
        return msg
    with transaction.atomic():
        guard_storage(msg['session_id'], msg.get('observed_incarnation'), msg.get('observed_epoch'),
                      msg.get('observed_upper_line'), msg.get('observed_last_offset'))
    if 'history_facts' not in msg:
        raise ValueError('session_complete is missing history_facts')
    initialize_compute_session(msg['session_id'])
    session = Session.objects.get(pk=msg['session_id'])
    upper_line = msg.get('observed_upper_line')
    if upper_line is None:
        upper_line = max(session.last_line,
                         SessionItem.objects.filter(session_id=session.id).aggregate(last=Max('line_num'))['last'] or 0)
    msg = {**msg, 'observed_incarnation': msg.get('observed_incarnation') or session.storage_incarnation,
           'observed_epoch': msg.get('observed_epoch', session.history_epoch),
           'observed_upper_line': upper_line}
    now = timezone.now()
    active_roots = set()
    unresolved_active = False
    for active_id in msg.get('_active_session_ids', ()):
        decision = resolve_root_decision(active_id, now, frozenset())
        if decision.resolved:
            active_roots.add(decision.root_id)
        else:
            unresolved_active = True
    root_id, final = pending_root_fields(msg['session_id'], msg.get('session_fields', {}), frozenset(active_roots))
    final = final or {}
    initial_root = Session.objects.filter(pk=root_id).values(*COLD_FIELDS).first()
    placement = {'root_id': root_id, 'root_incarnation': final.get('storage_incarnation'),
                 'root_epoch': final.get('content_state_epoch'),
                 'final_fields': {name: final[name] for name in COLD_FIELDS if name in final},
                 'initial_fields': initial_root, 'active_roots': frozenset(active_roots), 'now': now,
                 'unresolved_active': unresolved_active, 'pending_session_id': msg['session_id'],
                 'observed_offset': msg.get('observed_last_offset')}
    msg['_storage_final_root'] = placement
    token = _placement.set(placement)
    updates = msg.get('item_updates') or []
    overrides = msg.get('content_overrides') or []
    estimate = max(1, (len(overrides) + ROW_BUDGET - 1) // ROW_BUDGET
                   + (session.last_line + ROW_BUDGET - 1) // ROW_BUDGET)
    try:
        with session_reference_operation(msg['session_id'], item_count=session.last_line,
                                         expected_batches=estimate) as operation:
            for start in range(0, len(updates), item_row_budget):
                outcome = BaseSessionCompute.apply_session_items_chunk(
                    msg['session_id'], msg.get('observed_last_offset'), msg.get('item_fields', []),
                    updates[start:start + item_row_budget], [], msg.get('_repaired_activity_buckets'),
                    msg['observed_incarnation'], msg['observed_epoch'], msg['observed_upper_line'],
                )
                if outcome != 'ok':
                    raise SupersededStorageResult(outcome)
            offset = 0
            while offset < len(overrides):
                chunk = []
                size = 0
                while offset < len(overrides) and len(chunk) < ROW_BUDGET:
                    override = overrides[offset]
                    length = len(override['content'].encode('utf-8'))
                    if chunk and size + length > BYTE_BUDGET:
                        break
                    chunk.append(override)
                    size += length
                    offset += 1
                apply_override_slice(msg, chunk, operation)
            cursor = StorageCursor(-1, 0, 0, False)
            while not cursor.done:
                cursor = extract_storage_slice(msg['session_id'], msg['observed_incarnation'], msg['observed_epoch'],
                                               msg['observed_upper_line'], cursor.line_num, ROW_BUDGET, BYTE_BUDGET)
            cursor = StorageCursor(-1, 0, 0, False)
            while not cursor.done:
                cursor = reconcile_storage_slice(msg, cursor.line_num)
    finally:
        _placement.reset(token)
    return {**msg, 'item_updates': [], 'content_overrides': [], '_storage_prepared': True}


def finish_storage_result(msg):
    """Verify final lifecycle, placement and optional complete native-tree plaintext."""
    from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION
    from twicc.core.content_storage.state import snapshot_root_storage, verify_plaintext_root

    session_id = msg['session_id']
    pending = msg.get('_storage_final_root')
    if pending:
        root_id, actual = pending_root_fields(session_id, {}, pending['active_roots'])
        if root_id != pending['root_id'] or (actual and (
            actual['storage_incarnation'] != pending['root_incarnation']
            or actual['content_state_epoch'] != pending['root_epoch']
            or any(actual[name] != value for name, value in pending['final_fields'].items())
        )):
            raise SupersededStorageResult('superseded')
    if SessionItem.objects.filter(session_id=session_id).filter(
        Q(current_content_id__isnull=True) | ~Q(content=''),
    ).exists():
        raise ContentIntegrityError('Compute storage placement is incomplete')
    decision = resolve_root_decision(session_id, timezone.now(), pending['active_roots'] if pending else frozenset())
    if not decision.is_cold and SessionItem.objects.filter(session_id=session_id).filter(
        ~Q(current_content__codec='plain')
        | Q(original_content_id__isnull=False) & ~Q(original_content__codec='plain'),
    ).exists():
        raise ContentIntegrityError('Hot compute storage requires plaintext through both references')
    Session.objects.filter(pk=session_id).update(content_storage_version=CONTENT_STORAGE_VERSION)
    if decision.resolved and not decision.is_cold:
        verify_plaintext_root(snapshot_root_storage(decision.root_id),
                              active_root_ids=pending['active_roots'] if pending else frozenset())
