"""Bounded content promotion and the combined daily data-maintenance timer."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import islice
from typing import NamedTuple
from time import perf_counter
from uuid import uuid4


from channels.db import database_sync_to_async
from django.db import connections, transaction
from django.db.models import BinaryField
from django.db.models.functions import Cast, Length
from django.utils import timezone

from twicc.core.content_storage.codec import ContentIntegrityError, transcode_content
from twicc.core.content_storage.lifecycle import active_session_ids, set_root_promotion_wakeup
from twicc.core.content_storage.maintenance_cache import MaintenanceSessionCache
from twicc.core.content_storage.maintenance_metrics import MaintenanceMetrics
from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION, compression_policy_version
from twicc.core.content_storage.session_policy import resolve_content_decisions
from twicc.core.content_storage.state import (
    TREE_PAGE_SIZE, _require_writer_transaction, _snapshot_matches,
    session_plaintext_metadata, snapshot_session_promotion,
)
from twicc.core.content_storage.transactions import immediate_content_transaction
from twicc.core.content_storage.types import PreparedRepresentation, RootPromotionCursor, RootStorageSnapshot, SessionPromotionSnapshot
from twicc.core.models import Session, SessionItem, SessionItemContent
from twicc.core.content_storage.dictionaries import get_provider_dictionary
from twicc.providers.compute_work import ComputeWorkState
from twicc.providers.write_coordinator import WritePriority, WriteResources, write_priority

logger = logging.getLogger(__name__)
INITIAL_DELAY = 1800
INTERVAL = 86400
ROW_BUDGET = 100
BYTE_BUDGET = 8 * 1024 * 1024
START_CURSOR = RootPromotionCursor(None, -1, 1, 0, 0, False)
_wakeup = None
_deferral_wakeup = None
_promotion_deferrals = set()


class PromotionDeferral:
    """An operation-owned scheduling pause, independent of maintenance startup/shutdown."""

    def __init__(self, root_id: str, root_incarnation: str):
        self.root_id = root_id
        self.root_incarnation = root_incarnation
        self.released = False
        _promotion_deferrals.add(self)

    def release(self) -> None:
        """Release on the owning event loop, even after maintenance has stopped."""
        if self.released:
            return
        self.released = True
        _promotion_deferrals.remove(self)
        if _deferral_wakeup is not None:
            _deferral_wakeup()


def defer_background_promotions(root_id: str, root_incarnation: str) -> PromotionDeferral:
    """Defer one guarded root scope; callers own release on every terminal path.

    Register on the writer event loop before releasing guarded initialization.
    This token owns no resource, demand, readiness certificate, or inferred target.
    """
    if not root_id or not root_incarnation:
        raise ValueError('Promotion deferral requires a resolved root and incarnation')
    return PromotionDeferral(root_id, root_incarnation)


class CompressionObservation(NamedTuple):
    id: int
    sha256: bytes
    raw_size: int
    codec: str
    dictionary_id: int | None
    dictionary_provider: str
    session_ids: tuple[str, ...]
    compression_attempt_version: str | None
    compression_no_gain: bool
    stored_size: int


class PreparedEntry(NamedTuple):
    candidate: PreparedRepresentation
    observation: CompressionObservation


class CompressionPageCounts(NamedTuple):
    scanned: int = 0
    already_compressed: int = 0
    no_gain_skips: int = 0
    ineligible_owners: int = 0
    prepared: int = 0
    payload_bytes_loaded: int = 0
    candidate_raw_bytes: int = 0
    candidate_stored_bytes: int = 0


class CompressionPage(NamedTuple):
    cursor: int
    entries: tuple[PreparedEntry, ...]
    done: bool
    counts: CompressionPageCounts


class PublicationResult(NamedTuple):
    changed: int = 0
    no_gain_recorded: int = 0
    already_conformant: int = 0
    stale: int = 0
    bytes_saved: int = 0


class PromotionEntry(NamedTuple):
    candidate: PreparedRepresentation
    observation: CompressionObservation


class PromotionBatch(NamedTuple):
    snapshot: RootStorageSnapshot | SessionPromotionSnapshot
    cursor: RootPromotionCursor
    entries: tuple[PromotionEntry, ...]
    verified: bool


def request_root_promotion(session_id: str, *, priority: WritePriority | None = None) -> None:
    """Marshal one actual target; the historical name also accepts native-child IDs."""
    if _wakeup is not None:
        _wakeup(session_id, write_priority.get() if priority is None else priority)


def _owners_cold(row, active):
    """Authorize legacy direct publication with the same own-session policy as batch publication."""
    cache = MaintenanceSessionCache()
    try:
        decisions = cache.decisions(row.session_ids, timezone.now(), active)
        return _candidate_owners_cold(row.session_ids, decisions)
    finally:
        cache.close()


def _dictionary(row):
    entry = get_provider_dictionary(row.dictionary_provider)
    return None if entry is None else entry.row


def prepare_representation(content_id: int, policy_version: str, observed=None, dictionary=None,
                           dictionary_cache=None) -> PreparedRepresentation:
    """Prepare exact bytes on the read/codec worker without a writer lease."""
    row = SessionItemContent.objects.get(pk=content_id) if observed is None else observed
    if row.id != content_id:
        raise ValueError('Observed content does not match requested identity')
    if policy_version == 'plain':
        encoded, no_gain = transcode_content(row, dictionary_cache=dictionary_cache)
    else:
        dictionary = _dictionary(row) if dictionary is None else dictionary
        if dictionary is None or compression_policy_version(
            row.dictionary_provider, dictionary.id, dictionary=dictionary,
        ) != policy_version:
            raise ValueError('Compression policy no longer selects the provider dictionary')
        if dictionary_cache is None:
            dictionary_cache = {}
        if dictionary.id not in dictionary_cache and len(dictionary_cache) >= 32:
            dictionary_cache.pop(next(iter(dictionary_cache)))
        from twicc.core.content_storage.dictionaries import get_dictionary

        dictionary_cache[dictionary.id] = get_dictionary(dictionary.id)
        encoded, no_gain = transcode_content(row, dictionary.id, dictionary_cache=dictionary_cache)
    return PreparedRepresentation(row.id, row.codec, row.dictionary_id, encoded, policy_version, no_gain)


def publish_representation(candidate: PreparedRepresentation, active_root_ids: frozenset[str]) -> bool:
    """Publish under admission and writer ownership after the caller validates its preparation."""
    _require_writer_transaction()
    row = SessionItemContent.objects.filter(pk=candidate.content_id).only(*COMPRESSION_METADATA_FIELDS).first()
    if (row is None or row.codec != candidate.observed_codec
            or row.dictionary_id != candidate.observed_dictionary_id):
        return False
    encoded = candidate.encoded
    if bytes(row.sha256) != encoded.sha256 or row.raw_size != encoded.raw_size:
        return False
    if candidate.policy_version != 'plain':
        dictionary = _dictionary(row)
        if (dictionary is None or compression_policy_version(row.dictionary_provider, dictionary.id)
                != candidate.policy_version or not _owners_cold(row, active_root_ids)):
            return False
        if encoded.codec == 'zstd' and encoded.dictionary_id != dictionary.id:
            return False
    else:
        hot_owner = any(decision.resolved and not decision.is_cold for decision in resolve_content_decisions(
            row.session_ids, timezone.now(), active_root_ids,
        ).values())
        if not row.session_ids or not hot_owner:
            return False
    # CPU preparation already verifies the exact logical bytes. Publication
    # compares their identity with the persisted observation, without decoding.
    if encoded.codec == 'plain':
        if encoded.dictionary_id is not None or len(encoded.payload) != encoded.raw_size:
            return False
    elif encoded.codec != 'zstd' or encoded.dictionary_id is None or candidate.no_gain:
        return False
    updates = {'payload': encoded.payload, 'codec': encoded.codec, 'dictionary_id': encoded.dictionary_id}
    if candidate.policy_version != 'plain':
        updates.update(compression_attempt_version=candidate.policy_version, compression_no_gain=candidate.no_gain)
    SessionItemContent.objects.filter(pk=row.id).update(**updates)
    return True


COMPRESSION_METADATA_FIELDS = (
    'id', 'sha256', 'raw_size', 'codec', 'dictionary_id', 'dictionary_provider',
    'session_ids', 'compression_attempt_version', 'compression_no_gain',
)


def _candidate_owners_cold(owner_ids, decisions):
    return bool(owner_ids and len(owner_ids) == len(set(owner_ids))
                and all(decisions[identity].resolved and decisions[identity].is_cold for identity in owner_ids))


def publish_prepared_batch(entries: tuple[PreparedEntry, ...], active: frozenset[str], *,
                           session_cache: MaintenanceSessionCache) -> PublicationResult:
    """Validate candidate metadata under write ownership without loading persisted payloads."""
    _require_writer_transaction()
    if not entries:
        return PublicationResult()
    rows = {row['id']: row for row in SessionItemContent.objects.filter(
        pk__in=[entry.candidate.content_id for entry in entries],
    ).values(*COMPRESSION_METADATA_FIELDS)}
    decisions = session_cache.decisions(
        {identity for row in rows.values() for identity in row['session_ids']}, timezone.now(), active,
    )
    changed = no_gain_recorded = already_conformant = stale = bytes_saved = 0
    for entry in entries:
        candidate, observed = entry
        row = rows.get(candidate.content_id)
        encoded = candidate.encoded
        if (row is None or row['id'] != observed.id or bytes(row['sha256']) != observed.sha256
                or row['raw_size'] != observed.raw_size
                or row['dictionary_provider'] != observed.dictionary_provider
                or encoded.sha256 != observed.sha256 or encoded.raw_size != observed.raw_size):
            stale += 1
            continue
        dictionary_entry = get_provider_dictionary(row['dictionary_provider'])
        dictionary = None if dictionary_entry is None else dictionary_entry.row
        if (dictionary is None or candidate.policy_version != compression_policy_version(
                row['dictionary_provider'], dictionary.id, dictionary=dictionary)
                or not _candidate_owners_cold(row['session_ids'], decisions)):
            stale += 1
            continue
        if (row['codec'] == 'zstd' and row['dictionary_id'] == dictionary.id
                or row['compression_attempt_version'] == candidate.policy_version and row['compression_no_gain']):
            already_conformant += 1
            continue
        if (row['codec'] != observed.codec or row['dictionary_id'] != observed.dictionary_id
                or row['compression_attempt_version'] != observed.compression_attempt_version
                or row['compression_no_gain'] != observed.compression_no_gain
                or candidate.observed_codec != observed.codec
                or candidate.observed_dictionary_id != observed.dictionary_id):
            stale += 1
            continue
        if candidate.no_gain:
            if encoded.codec != 'plain' or encoded.dictionary_id is not None or len(encoded.payload) != encoded.raw_size:
                stale += 1
                continue
            updates = {'compression_attempt_version': candidate.policy_version, 'compression_no_gain': True}
            no_gain_recorded += 1
        elif encoded.codec == 'zstd' and encoded.dictionary_id == dictionary.id:
            updates = {'payload': encoded.payload, 'codec': encoded.codec, 'dictionary_id': encoded.dictionary_id,
                       'compression_attempt_version': candidate.policy_version, 'compression_no_gain': False}
            changed += 1
            bytes_saved += observed.stored_size - len(encoded.payload)
        else:
            stale += 1
            continue
        updates = {field: value for field, value in updates.items()
                   if field == 'payload' or row[field] != value}
        # Ownership is independently mutable. Never publish the preparation's owner list.
        SessionItemContent.objects.filter(pk=row['id']).update(**updates)
    return PublicationResult(changed, no_gain_recorded, already_conformant, stale, bytes_saved)


def _promotion_keys(snapshot, cursor):
    target_id = getattr(snapshot, 'session_id', snapshot.root_id)
    yield from _session_keys([[target_id]], cursor)


def _session_keys(pages, cursor):
    for page in pages:
        sessions = {row['id']: row for row in Session.objects.filter(pk__in=page).values(
            'id', 'storage_incarnation', 'content_storage_version',
        )}
        for session_id in page:
            session = sessions.get(session_id)
            valid = bool(session and session['storage_incarnation']
                         and session['content_storage_version'] == CONTENT_STORAGE_VERSION)
            query = SessionItem.objects.filter(session_id=session_id)
            if session_id == cursor.session_id:
                query = query.filter(line_num__gte=cursor.line_num)
            found = False
            for row in query.annotate(legacy_length=Length('content')).order_by('line_num').values(
                'line_num', 'current_content_id', 'original_content_id', 'legacy_length',
            ).iterator(chunk_size=ROW_BUDGET):
                line = row['line_num']
                item_valid = valid and row['legacy_length'] == 0 and row['current_content_id'] is not None
                slots = [(slot, pk) for slot, pk in enumerate((row['current_content_id'], row['original_content_id']))
                         if pk is not None] or [(1, None)]
                for slot, content_id in slots:
                    if session_id == cursor.session_id and (line, slot) <= (cursor.line_num, cursor.fk_slot):
                        continue
                    found = True
                    yield session_id, line, slot, content_id, item_valid
            if not found and session_id != cursor.session_id:
                yield session_id, -1, 1, None, valid


def _target_decision(session_id, active, session_cache=None):
    owned = session_cache is None
    session_cache = MaintenanceSessionCache() if owned else session_cache
    try:
        return session_cache.decisions([session_id], timezone.now(), active)[session_id]
    finally:
        if owned:
            session_cache.close()


@transaction.atomic
def prepare_promotion_batch(snapshot, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET, *, session_cache=None):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    if not _snapshot_matches(snapshot):
        return PromotionBatch(snapshot, cursor, (), False)
    target_id = getattr(snapshot, 'session_id', snapshot.root_id)
    eligibility_active = active_session_ids.get()
    decision = _target_decision(target_id, eligibility_active, session_cache)
    if not decision.resolved or (getattr(snapshot, 'recovery', False) and decision.is_cold):
        return PromotionBatch(snapshot, cursor, (), False)
    # Fetch only bounded FK metadata before loading any payload bytes.
    keys = list(islice(_promotion_keys(snapshot, cursor), row_budget + 1))
    ids = {key[3] for key in keys if key[3] is not None}
    metadata = {row['id']: row for row in SessionItemContent.objects.filter(pk__in=ids).annotate(
        stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
    ).values('id', 'raw_size', 'stored_size', 'owner_size', 'codec', 'dictionary_id')}
    selected = []
    size = 0
    for key in keys:
        content = metadata.get(key[3])
        length = content['raw_size'] + content['stored_size'] + content['owner_size'] if content else 0
        if selected and (len(selected) >= row_budget or size + length > byte_budget):
            break
        selected.append(key)
        size += length
    payload_ids = {key[3] for key in selected if key[3] in metadata and metadata[key[3]]['codec'] != 'plain'}
    rows = SessionItemContent.objects.in_bulk(payload_ids)
    entries = []
    prepared_ids = set()
    dictionary_cache = {}
    last = cursor
    verified = True
    for session_id, line, slot, content_id, item_valid in selected:
        row = rows.get(content_id)
        content = metadata.get(content_id)
        verified &= item_valid and (content_id is None or content is not None)
        if content is not None and content['codec'] == 'plain' and content['dictionary_id'] is not None:
            verified = False
        if row is not None and row.codec != 'plain' and row.id not in prepared_ids:
            prepared_ids.add(row.id)
            encoded, _ = transcode_content(row, dictionary_cache=dictionary_cache)
            candidate = PreparedRepresentation(row.id, row.codec, row.dictionary_id, encoded, 'plain', False)
            entries.append(PromotionEntry(candidate, CompressionObservation(
                row.id, bytes(row.sha256), row.raw_size, row.codec, row.dictionary_id,
                row.dictionary_provider, tuple(row.session_ids), row.compression_attempt_version,
                row.compression_no_gain, len(row.payload),
            )))
        last = RootPromotionCursor(session_id, line, slot, len(selected), size, False)
    return PromotionBatch(snapshot, last._replace(rows=len(selected), bytes=size, done=len(selected) == len(keys)),
                          tuple(entries), verified)


def apply_promotion_batch(batch, active, *, pass_verified=False, session_cache=None):
    with immediate_content_transaction():
        if not _snapshot_matches(batch.snapshot):
            return START_CURSOR
        target_id = getattr(batch.snapshot, 'session_id', batch.snapshot.root_id)
        decision = _target_decision(target_id, active, session_cache)
        if not decision.resolved or decision.is_cold:
            return START_CURSOR
        _require_writer_transaction()
        rows = {row['id']: row for row in SessionItemContent.objects.filter(
            pk__in=[entry.candidate.content_id for entry in batch.entries],
        ).values(*COMPRESSION_METADATA_FIELDS)}
        changed = []
        for entry in batch.entries:
            candidate = entry.candidate
            row = rows.get(candidate.content_id)
            encoded = candidate.encoded
            observed = entry.observation
            # Immutable logical identity and representation metadata fence prepared bytes.
            # Owner changes remain intact; reference changes invalidate the root epoch.
            if (row is None or row['codec'] != observed.codec
                    or row['dictionary_id'] != observed.dictionary_id
                    or bytes(row['sha256']) != observed.sha256 or row['raw_size'] != observed.raw_size
                    or row['dictionary_provider'] != observed.dictionary_provider
                    or encoded.sha256 != observed.sha256 or encoded.raw_size != observed.raw_size
                    or encoded.codec != 'plain' or encoded.dictionary_id is not None
                    or len(encoded.payload) != encoded.raw_size):
                return START_CURSOR
            changed.append((row['id'], encoded.payload))
        for content_id, payload in changed:
            SessionItemContent.objects.filter(pk=content_id).update(payload=payload, codec='plain', dictionary_id=None)
        if (batch.cursor.done and pass_verified and batch.verified
                and getattr(batch.snapshot, 'session_id', batch.snapshot.root_id) == batch.snapshot.root_id):
            # Only the root's own slots receive a durable readiness certificate.
            # The root generation still fences every native-tree/reference mutation.
            Session.objects.filter(pk=batch.snapshot.root_id, storage_incarnation=batch.snapshot.incarnation,
                                   content_state_epoch=batch.snapshot.epoch).update(
                                       plaintext_verified_epoch=batch.snapshot.epoch,
                                   )
        return batch.cursor


def promote_root_slice(snapshot: RootStorageSnapshot, cursor: RootPromotionCursor,
                       row_budget: int, byte_budget: int) -> RootPromotionCursor:
    """Promote one bounded slice; async production prepares before acquiring admission.

    An externally resumed cursor carries no prior readiness proof. Only a full
    pass from START_CURSOR can certify root readiness. Native-child passes carry
    no durable certificate. Each pass visits only its target session's FK slots.
    """
    batch = prepare_promotion_batch(snapshot, cursor, row_budget, byte_budget)
    return apply_promotion_batch(batch, active_session_ids.get(), pass_verified=cursor == START_CURSOR)


def pending_session_page(after_id: str | None = None) -> list[str]:
    """Page durable extraction identities at every native depth, independent of certificates."""
    query = Session.objects.filter(storage_incarnation__isnull=False,
                                   content_storage_version=CONTENT_STORAGE_VERSION)
    if after_id is not None:
        query = query.filter(pk__gt=after_id)
    return list(query.order_by('id').values_list('id', flat=True)[:TREE_PAGE_SIZE])


def recovery_promotion_targets(session_ids, session_cache=None):
    """Probe only hot sessions' reference metadata outside writer ownership."""
    owned = session_cache is None
    session_cache = MaintenanceSessionCache() if owned else session_cache
    try:
        # Fill committed cache metadata before entering the short reference snapshot.
        decisions = session_cache.decisions(session_ids, timezone.now(), active_session_ids.get())
        hot_ids = [session_id for session_id, decision in decisions.items()
                   if decision.resolved and not decision.is_cold]
        session_cache.statistics['recovery_reference_probes'] = (
            session_cache.statistics.get('recovery_reference_probes', 0) + len(hot_ids)
        )
        with transaction.atomic():
            return {session_id for session_id in hot_ids if not session_plaintext_metadata(session_id)}
    finally:
        if owned:
            session_cache.close()


def prepare_compression_batch(after_id, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET, *,
                              session_cache: MaintenanceSessionCache) -> CompressionPage:
    """Filter bounded metadata before resolving owners or loading candidate payloads."""
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    entries = []
    size = 0
    last = after_id
    dictionaries = {}
    dictionary_cache = {}
    counts = CompressionPageCounts()._asdict()
    query = SessionItemContent.objects.filter(pk__gt=after_id).order_by('id')
    rows = list(query.annotate(
        stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
    ).values(*(field for field in COMPRESSION_METADATA_FIELDS if field != 'session_ids'),
             'stored_size', 'owner_size')[:row_budget])
    selected = []
    for metadata in rows:
        cost = metadata['raw_size'] + metadata['stored_size'] + metadata['owner_size']
        if last != after_id and size + cost > byte_budget:
            break
        last = metadata['id']
        size += cost
        counts['scanned'] += 1
        provider = metadata['dictionary_provider']
        if provider not in dictionaries:
            entry = get_provider_dictionary(provider)
            dictionary = None if entry is None else entry.row
            policy = None if dictionary is None else compression_policy_version(
                provider, dictionary.id, dictionary=dictionary,
            )
            dictionaries[provider] = dictionary, policy
        dictionary, policy = dictionaries[provider]
        if dictionary is None:
            continue
        if metadata['codec'] == 'zstd' and metadata['dictionary_id'] == dictionary.id:
            counts['already_compressed'] += 1
            continue
        if metadata['compression_attempt_version'] == policy and metadata['compression_no_gain']:
            counts['no_gain_skips'] += 1
            continue
        selected.append((metadata, dictionary, policy))
    owners = dict(SessionItemContent.objects.filter(
        pk__in=[metadata['id'] for metadata, _, _ in selected],
    ).values_list('id', 'session_ids'))
    selected = [(dict(metadata, session_ids=owners[metadata['id']]), dictionary, policy)
                for metadata, dictionary, policy in selected if metadata['id'] in owners]
    active = active_session_ids.get()
    decisions = session_cache.decisions(
        {identity for metadata, _, _ in selected for identity in metadata['session_ids']}, timezone.now(), active,
    )
    pending = []
    for metadata, dictionary, policy in selected:
        if not _candidate_owners_cold(metadata['session_ids'], decisions):
            counts['ineligible_owners'] += 1
            continue
        pending.append((metadata, dictionary, policy))
    payloads = {}
    payload_ids = [metadata['id'] for metadata, _, _ in pending]
    for offset in range(0, len(payload_ids), TREE_PAGE_SIZE):
        payloads.update(SessionItemContent.objects.filter(pk__in=payload_ids[offset:offset + TREE_PAGE_SIZE])
                        .values_list('id', 'payload'))
    counts['payload_bytes_loaded'] = sum(len(payload) for payload in payloads.values())
    for metadata, dictionary, policy in pending:
        if metadata['id'] not in payloads:
            continue
        row = SessionItemContent(**{field: metadata[field] for field in COMPRESSION_METADATA_FIELDS},
                                 payload=payloads[metadata['id']])
        observation = CompressionObservation(
            row.id, bytes(row.sha256), row.raw_size, row.codec, row.dictionary_id, row.dictionary_provider,
            tuple(row.session_ids), row.compression_attempt_version, row.compression_no_gain, metadata['stored_size'],
        )
        try:
            candidate = prepare_representation(row.id, policy, row, dictionary, dictionary_cache)
        except ContentIntegrityError:
            raise
        except Exception:
            logger.warning('Optional content compression failed for %s', row.id, exc_info=True)
            continue
        entries.append(PreparedEntry(candidate, observation))
        counts['prepared'] += 1
        counts['candidate_raw_bytes'] += candidate.encoded.raw_size
        counts['candidate_stored_bytes'] += len(candidate.encoded.payload)
    return CompressionPage(last, tuple(entries), not SessionItemContent.objects.filter(pk__gt=last).exists(),
                           CompressionPageCounts(**counts))


async def _apply(function, *args, priority=WritePriority.FOREGROUND, lease=None, metrics=None):
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.db_writer import (
        _drive_publication, _run_with_content_activity, get_write_coordinator, run_under_storage_write_lock,
    )

    requested = perf_counter()
    ownership = None
    owned_lease = lease
    transaction_ms = 0.0
    result = None

    def measured(*values):
        nonlocal transaction_ms
        started = perf_counter()
        try:
            return function(*values)
        finally:
            transaction_ms += (perf_counter() - started) * 1000

    async def apply():
        nonlocal ownership, owned_lease, result
        if owned_lease is None:
            owned_lease = next((holder for holder in get_write_coordinator().holders
                                if holder.resources & WriteResources.WRITER), None)
        ownership = owned_lease.granted_at if owned_lease is not None else perf_counter()
        result = await run_compute_sync(measured, *args, active_session_ids.get())
        return result

    try:
        if lease is None:
            return await run_under_storage_write_lock(apply, priority=priority, content_activity=True)
        return await _drive_publication(
            lambda: _run_with_content_activity(apply), lease.priority, lease.resources,
        )
    finally:
        released = perf_counter()
        if lease is not None:
            lease.release()
        if metrics is not None and ownership is not None:
            publication = result if isinstance(result, PublicationResult) else None
            if args and isinstance(args[0], PromotionBatch):
                batch = args[0]
                publication = PublicationResult(
                    changed=len(batch.entries) if result == batch.cursor else 0,
                    stale=len(batch.entries) if result == START_CURSOR else 0,
                )
            metrics.record_publication(publication, wait_ms=max(0, (ownership - requested) * 1000),
                                       transaction_ms=transaction_ms,
                                       hold_ms=((owned_lease.released_at or released) - ownership) * 1000)


def _snapshot(session_id, active, *, recovery=False, session_cache=None):
    with transaction.atomic():
        decision = _target_decision(session_id, active, session_cache)
        if not decision.resolved or decision.is_cold:
            return None
        snapshot = snapshot_session_promotion(session_id)
        if recovery:
            return SessionPromotionSnapshot(snapshot.root_id, snapshot.incarnation, snapshot.epoch,
                                            session_id, getattr(snapshot, 'session_incarnation', snapshot.incarnation),
                                            recovery=True)
        return snapshot


def _publish(entries, active, *, session_cache):
    if not entries:
        return PublicationResult()
    with immediate_content_transaction():
        return publish_prepared_batch(entries, active, session_cache=session_cache)


def _maintenance_time():
    return asyncio.get_running_loop().time()


async def _wait(stop_event, wake, timeout):
    tasks = [asyncio.create_task(stop_event.wait()), asyncio.create_task(wake.wait())]
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        return bool(done)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class _PromotionTarget:
    """One resumable target; scheduling priority never determines recovery eligibility."""

    def __init__(self, *, recovery, priority, periodic=False, session_cache=None, coordinator=None):
        self.periodic = periodic
        self.recovery = recovery
        self.priority = priority
        self.demand = None
        self.owns_cache = session_cache is None
        self.session_cache = MaintenanceSessionCache() if self.owns_cache else session_cache
        self.metrics = MaintenanceMetrics(uuid4().hex, 'plaintext_restoration')
        self.metrics.cache_statistics = self.session_cache.statistics
        self.unsubscribe = (coordinator.observe_foreground_wait(self.metrics.record_foreground_wait)
                            if coordinator is not None else lambda: None)
        self.metrics.emit('start')
        self.reset()

    def reset(self):
        self.snapshot = None
        self.cursor = START_CURSOR
        self.verified = True
        self.batch = None
        self.deferred = False

    def finish(self, event):
        try:
            self.metrics.cache_statistics = self.session_cache.statistics
            self.metrics.emit(event)
        finally:
            self.unsubscribe()
            if self.owns_cache:
                self.session_cache.close()

    def explicit(self):
        if self.periodic and not self.owns_cache:
            self.session_cache = MaintenanceSessionCache()
            self.owns_cache = True
        self.periodic = False
        self.recovery = False
        if self.snapshot is not None and getattr(self.snapshot, 'recovery', False):
            self.snapshot = self.snapshot._replace(recovery=False)
            if self.batch is not None:
                self.batch = self.batch._replace(snapshot=self.snapshot)


async def start_content_maintenance_task(stop_event: asyncio.Event, *, compute_work: ComputeWorkState) -> None:
    """Dispatch bounded target steps; foreground demand wins each new resource grant."""
    global _wakeup, _deferral_wakeup
    from django.conf import settings

    from twicc.providers.db_writer import (
        get_write_coordinator, has_pending_source_applications, source_application_changes,
    )
    from twicc.session_dirs_cleanup_task import _run_cleanup_pass

    loop = asyncio.get_running_loop()
    coordinator = get_write_coordinator()
    sources_changed = source_application_changes()

    def compute_pending():
        return compute_work.has_pending() or has_pending_source_applications()

    wake = asyncio.Event()
    targets = {}
    closing = False
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='twicc-content-codec')

    def enqueue(session_id, priority):
        if closing or stop_event.is_set():
            return
        target = targets.get(session_id)
        if target is None:
            target = targets[session_id] = _PromotionTarget(recovery=False, priority=priority, coordinator=coordinator)
        target.explicit()
        if priority is WritePriority.FOREGROUND:
            if target.priority is WritePriority.BACKGROUND:
                targets[session_id] = targets.pop(session_id)
            target.priority = priority
            if target.demand is None:
                target.demand = coordinator.foreground_demand()
        wake.set()

    def wakeup(session_id, priority):
        loop.call_soon_threadsafe(enqueue, session_id, priority)

    _wakeup = wakeup
    _deferral_wakeup = wake.set
    set_root_promotion_wakeup(request_root_promotion)

    async def prepare(function, *args):
        from twicc.agent.registry import get_agent_manager_registry

        token = active_session_ids.set(frozenset(
            agent.session_id for agent in get_agent_manager_registry().get_active_agents()
        ))
        try:
            return await database_sync_to_async(partial(function, *args), thread_sensitive=False, executor=executor)()
        finally:
            active_session_ids.reset(token)

    async def wait(timeout):
        # Callers clear events before inspecting authoritative state. A change
        # between that inspection and task creation remains visible here.
        tasks = [asyncio.create_task(_wait(stop_event, wake, timeout)),
                 asyncio.create_task(compute_work.changed.wait()),
                 asyncio.create_task(sources_changed.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task.done():
                    task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def periodic_ready():
        while not stop_event.is_set():
            wake.clear()
            compute_work.changed.clear()
            sources_changed.clear()
            await promote()
            if not compute_pending():
                return not stop_event.is_set()
            # An overdue deadline must not become a zero-timeout polling loop.
            await wait(None)
        return False

    async def recover(*, periodic=False, session_cache=None):
        owned = session_cache is None
        cache = MaintenanceSessionCache() if owned else session_cache
        try:
            await recover_pages(cache, periodic=periodic)
        finally:
            if owned:
                cache.close()

    async def recover_pages(cache, *, periodic=False):
        after = None
        while not stop_event.is_set():
            if periodic and not await periodic_ready():
                return
            page = await prepare(pending_session_page, after)
            if not page:
                break
            if periodic and not await periodic_ready():
                return
            eligible = await prepare(recovery_promotion_targets, page, cache)
            for session_id in page:
                if session_id in eligible and session_id not in targets:
                    targets[session_id] = _PromotionTarget(
                        recovery=True, priority=WritePriority.BACKGROUND, periodic=periodic,
                        session_cache=cache if periodic else None, coordinator=coordinator,
                    )
            await promote()
            after = page[-1]

    def settle(session_id, target, event='complete'):
        targets.pop(session_id)
        target.finish(event)
        if target.demand is not None:
            target.demand.release()

    def eligible(target):
        if target.periodic and compute_pending():
            return False
        snapshot = target.snapshot
        if (target.priority is WritePriority.BACKGROUND and snapshot is not None and any(
            token.root_id == snapshot.root_id and token.root_incarnation == snapshot.incarnation
            for token in _promotion_deferrals
        )):
            target.deferred = True
            return False
        if target.deferred:
            # Compute may change generations while paused. Never adopt those epochs.
            target.reset()
        return True

    async def promote():
        while targets and not stop_event.is_set():
            wake.clear()
            compute_work.changed.clear()
            sources_changed.clear()
            ready = [key for key, target in targets.items() if eligible(target)]
            if not ready:
                return
            session_id = next((key for key in ready if targets[key].priority is WritePriority.FOREGROUND), ready[0])
            target = targets[session_id]
            try:
                if target.snapshot is not None and target.batch is None:
                    recovery = target.recovery
                    preparation_started = perf_counter()
                    batch = await prepare(partial(prepare_promotion_batch, session_cache=target.session_cache),
                                          target.snapshot, target.cursor,
                                          ROW_BUDGET, BYTE_BUDGET)
                    target.metrics.record_preparation((perf_counter() - preparation_started) * 1000)
                    target.metrics.record_page(CompressionPage(
                        batch.cursor, (), batch.cursor.done, CompressionPageCounts(
                            scanned=batch.cursor.rows, prepared=len(batch.entries),
                            payload_bytes_loaded=sum(entry.observation.stored_size for entry in batch.entries),
                            candidate_raw_bytes=sum(entry.candidate.encoded.raw_size for entry in batch.entries),
                            candidate_stored_bytes=sum(len(entry.candidate.encoded.payload) for entry in batch.entries),
                        ),
                    ))
                    target.metrics.cache_statistics = target.session_cache.statistics
                    target.metrics.emit('progress')
                    # An upgrade must retry a rejection under the old recovery eligibility.
                    if recovery and not target.recovery and batch.cursor == target.cursor and not batch.verified:
                        continue
                    # An explicit enqueue can upgrade recovery while its codec thread runs.
                    target.batch = batch._replace(snapshot=target.snapshot)
                    continue
                lease = None
                if target.priority is WritePriority.BACKGROUND:
                    lease = coordinator.try_background_lease()
                    if lease is None:
                        # Never await a background grant in the foreground dispatcher.
                        waiting = perf_counter()
                        await wait(0.05)
                        target.metrics.counts['resource_wait_ms'] += (perf_counter() - waiting) * 1000
                        continue
                if target.periodic and compute_pending():
                    if lease is not None:
                        lease.release()
                    continue
                if target.snapshot is None:
                    recovery = target.recovery
                    target.snapshot = await _apply(partial(_snapshot, recovery=recovery, session_cache=target.session_cache), session_id,
                                                   priority=target.priority, lease=lease, metrics=target.metrics)
                    if target.snapshot is None:
                        if recovery and not target.recovery:
                            continue
                        settle(session_id, target)
                    elif not target.recovery:
                        target.explicit()
                    continue
                verified = target.verified and target.batch.verified
                cursor = await _apply(partial(apply_promotion_batch, pass_verified=verified,
                                              session_cache=target.session_cache), target.batch,
                                      priority=target.priority, lease=lease, metrics=target.metrics)
                if cursor == START_CURSOR:
                    target.reset()
                elif cursor.done:
                    # Incomplete extraction sleeps until a new writer invalidation.
                    settle(session_id, target)
                else:
                    target.cursor, target.verified, target.batch = cursor, verified, None
                    # Eligible peers retain FIFO at each bounded publication boundary.
                    targets[session_id] = targets.pop(session_id)
                await asyncio.sleep(0)
            except Exception:
                settle(session_id, target, 'failure')
                logger.exception('Content promotion failed for session %s', session_id)

    deadline = _maintenance_time() + INITIAL_DELAY
    try:
        try:
            await recover()
        except Exception:
            logger.exception('Content promotion startup recovery failed')
        while not stop_event.is_set():
            wake.clear()
            compute_work.changed.clear()
            sources_changed.clear()
            await promote()
            if stop_event.is_set():
                break
            if _maintenance_time() >= deadline and not compute_pending():
                session_cache = MaintenanceSessionCache()
                metrics = MaintenanceMetrics(uuid4().hex, 'periodic_compression')
                metrics.cache_statistics = session_cache.statistics
                unsubscribe = coordinator.observe_foreground_wait(metrics.record_foreground_wait)
                outcome = 'cancelled'
                failure_deadline = None
                metrics.emit('start')
                try:
                    await recover(periodic=True, session_cache=session_cache)
                    while any(target.periodic for target in targets.values()) and not stop_event.is_set():
                        if not await periodic_ready():
                            break
                        if any(target.periodic for target in targets.values()):
                            await wait(None)
                    await promote()
                    cursor, done = 0, False
                    while not done and not stop_event.is_set():
                        if not await periodic_ready():
                            break
                        preparation_started = perf_counter()
                        page = await prepare(partial(prepare_compression_batch, session_cache=session_cache),
                                             cursor, ROW_BUDGET, BYTE_BUDGET)
                        metrics.record_preparation((perf_counter() - preparation_started) * 1000)
                        metrics.record_page(page)
                        metrics.emit('progress')
                        cursor, entries, done = page.cursor, page.entries, page.done
                        while entries and not stop_event.is_set():
                            if not await periodic_ready():
                                break
                            if stop_event.is_set():
                                break
                            lease = coordinator.try_background_lease()
                            if lease is None:
                                waiting = perf_counter()
                                await wait(0.05)
                                metrics.counts['resource_wait_ms'] += (perf_counter() - waiting) * 1000
                                continue
                            if compute_pending():
                                lease.release()
                                continue
                            await _apply(partial(_publish, session_cache=session_cache), entries, lease=lease,
                                         metrics=metrics)
                            break
                        await asyncio.sleep(0)
                    if done and not stop_event.is_set():
                        outcome = 'complete'
                except Exception:
                    outcome = 'failure'
                    failure_deadline = _maintenance_time() + INTERVAL
                    logger.exception('Periodic content maintenance failed')
                finally:
                    try:
                        for session_id, target in tuple(targets.items()):
                            if target.session_cache is session_cache:
                                settle(session_id, target, outcome)
                        metrics.emit(outcome)
                    finally:
                        unsubscribe()
                        session_cache.close()
                if settings.SESSION_DIRS_CLEANUP_ENABLED and not stop_event.is_set():
                    try:
                        while await periodic_ready():
                            # Cleanup rechecks after its awaited manager locks.
                            if await _run_cleanup_pass(compute_work=compute_work) is not False:
                                break
                    except Exception:
                        logger.exception('Session directory maintenance failed')
                deadline = failure_deadline if failure_deadline is not None else _maintenance_time() + INTERVAL
            remaining = max(0, deadline - _maintenance_time())
            await wait(None if remaining == 0 and compute_pending() else remaining)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Content maintenance task failed')
    finally:
        closing = True
        set_root_promotion_wakeup(None)
        _wakeup = None
        _deferral_wakeup = None
        # Submitted read/CPU work can outlive cancellation. Drain it before teardown.
        async def close():
            await loop.run_in_executor(executor, connections.close_all)
            await asyncio.to_thread(executor.shutdown, wait=True)
        close_task = asyncio.create_task(close())
        try:
            while not close_task.done():
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError:
                    continue
            close_task.result()
        finally:
            for target in targets.values():
                target.finish('cancelled')
                if target.demand is not None:
                    target.demand.release()
