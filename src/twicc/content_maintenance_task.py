"""Bounded content promotion and the combined daily data-maintenance timer."""

import asyncio
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import islice
from typing import NamedTuple

import orjson

from channels.db import database_sync_to_async
from django.db import connections, transaction
from django.db.models import BinaryField, F, Q
from django.db.models.functions import Cast, Length
from django.utils import timezone

from twicc.core.content_storage.codec import ContentIntegrityError, decode_content, encode_cold, encode_plain
from twicc.core.content_storage.lifecycle import active_session_ids, set_root_promotion_wakeup
from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION, compression_policy_version
from twicc.core.content_storage.state import (
    TREE_PAGE_SIZE, _require_writer_transaction, _snapshot_matches, iter_native_tree_session_pages,
    resolve_root_decision, resolve_root_decisions, snapshot_root_storage,
)
from twicc.core.content_storage.types import PreparedRepresentation, RootPromotionCursor, RootStorageSnapshot
from twicc.core.models import ContentDictionary, Session, SessionItem, SessionItemContent

logger = logging.getLogger(__name__)
INITIAL_DELAY = 1800
INTERVAL = 86400
ROW_BUDGET = 100
BYTE_BUDGET = 8 * 1024 * 1024
START_CURSOR = RootPromotionCursor(None, -1, 1, 0, 0, False)
_wakeup = None


class RepresentationProof(NamedTuple):
    """Operation-local observations; never persisted or cached across batches."""
    owners: tuple[str, ...]
    metadata_digest: bytes
    payload: bytes
    sha256: bytes
    raw_size: int


class PreparedEntry(NamedTuple):
    candidate: PreparedRepresentation
    proof: RepresentationProof


class PromotionEntry(NamedTuple):
    candidate: PreparedRepresentation
    observed_payload: bytes


class PromotionBatch(NamedTuple):
    snapshot: RootStorageSnapshot
    cursor: RootPromotionCursor
    entries: tuple[PromotionEntry, ...]
    verified: bool


def request_root_promotion(root_id: str) -> None:
    """Marshal DB-thread wakeups to the installed event-loop coordinator."""
    if _wakeup is not None:
        _wakeup(root_id)


def _metadata_proof(owners):
    """Hash deterministic owner/root pages; retain no whole shared-owner metadata map."""
    digest = hashlib.sha256()
    owner_fields = ('id', 'type', 'parent_session_id', 'storage_incarnation', 'content_storage_version')
    root_fields = ('id', 'storage_incarnation', 'content_state_epoch', 'provider', 'compute_version', *COLD_FIELDS)
    now = timezone.now()
    for offset in range(0, len(owners), TREE_PAGE_SIZE):
        page = owners[offset:offset + TREE_PAGE_SIZE]
        metadata = {row[0]: row for row in Session.objects.filter(pk__in=page).values_list(*owner_fields)}
        decisions = resolve_root_decisions(page, now, frozenset())
        root_ids = sorted({decision.root_id for decision in decisions.values() if decision.resolved})
        roots = {row[0]: row for row in Session.objects.filter(pk__in=root_ids).values_list(*root_fields)}
        # Include missing/cyclic ancestry explicitly. Current active eligibility
        # is independently captured and checked under admission at publication.
        for owner_id in page:
            decision = decisions[owner_id]
            record = (owner_id, metadata.get(owner_id), decision.root_id, decision.resolved,
                      roots.get(decision.root_id))
            digest.update(orjson.dumps(record))
            digest.update(b'\n')
    return digest.digest()


def _proof(row):
    return RepresentationProof(tuple(row.session_ids), _metadata_proof(row.session_ids), bytes(row.payload),
                               bytes(row.sha256), row.raw_size)


def _proof_matches(entry, row):
    proof = entry.proof
    # Compare incarnation-bearing metadata before accepting any old generation.
    return (tuple(row.session_ids) == proof.owners and _metadata_proof(row.session_ids) == proof.metadata_digest
            and bytes(row.payload) == proof.payload and bytes(row.sha256) == proof.sha256
            and row.raw_size == proof.raw_size)


def _owners_cold(row, active):
    if not row.session_ids:
        return False
    from twicc.providers.helpers import get_provider_helpers

    now = timezone.now()
    # Unresolved active ancestry cannot prove that an owner tree is inactive.
    for offset in range(0, len(active), TREE_PAGE_SIZE):
        if any(not decision.resolved for decision in resolve_root_decisions(
            tuple(active)[offset:offset + TREE_PAGE_SIZE], now, active,
        ).values()):
            return False
    for offset in range(0, len(row.session_ids), TREE_PAGE_SIZE):
        ids = row.session_ids[offset:offset + TREE_PAGE_SIZE]
        owners = list(Session.objects.filter(pk__in=ids).values('id', 'content_storage_version'))
        if len(owners) != len(ids) or any(owner['content_storage_version'] != CONTENT_STORAGE_VERSION
                                         for owner in owners):
            return False
        decisions = resolve_root_decisions(ids, now, active)
        if any(not decision.resolved or not decision.is_cold for decision in decisions.values()):
            return False
        roots = {decision.root_id for decision in decisions.values()}
        metadata = list(Session.objects.filter(pk__in=roots).values('provider', 'compute_version',
                                                                   'storage_incarnation', 'content_state_epoch'))
        if any(not root['storage_incarnation'] or root['content_state_epoch'] is None
               or root['compute_version'] != get_provider_helpers(root['provider']).current_compute_version
               for root in metadata):
            return False
    return True


def _dictionary(row):
    return ContentDictionary.objects.filter(provider=row.dictionary_provider).order_by('-version').first()


def prepare_representation(content_id: int, policy_version: str) -> PreparedRepresentation:
    """Prepare exact bytes on the read/codec worker without a writer lease."""
    row = SessionItemContent.objects.get(pk=content_id)
    text = decode_content(row)
    if policy_version == 'plain':
        encoded, no_gain = encode_plain(text), False
    else:
        dictionary = _dictionary(row)
        if dictionary is None or compression_policy_version(row.dictionary_provider, dictionary.id) != policy_version:
            raise ValueError('Compression policy no longer selects the provider dictionary')
        encoded, no_gain = encode_cold(text, dictionary.id, policy_version)
    return PreparedRepresentation(row.id, row.codec, row.dictionary_id, encoded, policy_version, no_gain)


def publish_representation(candidate: PreparedRepresentation, active_root_ids: frozenset[str]) -> bool:
    """Publish under admission and writer ownership after the caller validates its companion proof."""
    _require_writer_transaction()
    row = SessionItemContent.objects.filter(pk=candidate.content_id).first()
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
    elif not row.session_ids or not any(
        decision.resolved and not decision.is_cold for decision in resolve_root_decisions(
            row.session_ids, timezone.now(), active_root_ids,
        ).values()
    ):
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


def publish_prepared_batch(entries: tuple[PreparedEntry, ...], active: frozenset[str]) -> int:
    """Recheck every preparation observation in the publication transaction.

    Callers must use entries from the same operation-local preparation batch.
    A public candidate alone cannot establish preparation-time ownership or
    root generations. No proof survives cancellation, restart, or tree changes.
    """
    _require_writer_transaction()
    published = 0
    for entry in entries:
        row = SessionItemContent.objects.filter(pk=entry.candidate.content_id).first()
        if row is not None and _proof_matches(entry, row):
            published += publish_representation(entry.candidate, active)
    return published


def _promotion_keys(snapshot, cursor):
    if cursor.session_id is not None:
        sessions = [[cursor.session_id]]
        yield from _session_keys(sessions, cursor)
    pages = iter_native_tree_session_pages(snapshot.root_id, cursor.session_id, TREE_PAGE_SIZE)
    yield from _session_keys(pages, cursor)


def _session_keys(pages, cursor):
    for page in pages:
        invalid_parents = set(Session.objects.filter(parent_session_id__in=page).exclude(type='subagent')
                              .values_list('parent_session_id', flat=True))
        sessions = {row['id']: row for row in Session.objects.filter(pk__in=page).values(
            'id', 'storage_incarnation', 'content_storage_version',
        )}
        for session_id in page:
            session = sessions.get(session_id)
            valid = bool(session_id not in invalid_parents and session and session['storage_incarnation']
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


@transaction.atomic
def prepare_promotion_batch(snapshot, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    if not _snapshot_matches(snapshot):
        return PromotionBatch(snapshot, cursor, (), False)
    decision = resolve_root_decision(snapshot.root_id, timezone.now(), frozenset())
    if not decision.resolved:
        return PromotionBatch(snapshot, cursor, (), False)
    # Fetch only bounded FK metadata before loading any payload bytes.
    keys = list(islice(_promotion_keys(snapshot, cursor), row_budget + 1))
    ids = {key[3] for key in keys if key[3] is not None}
    metadata = {row['id']: row for row in SessionItemContent.objects.filter(pk__in=ids).annotate(
        stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
    ).values('id', 'raw_size', 'stored_size', 'owner_size')}
    selected = []
    size = 0
    for key in keys:
        content = metadata.get(key[3])
        length = content['raw_size'] + content['stored_size'] + content['owner_size'] if content else 0
        if selected and (len(selected) >= row_budget or size + length > byte_budget):
            break
        selected.append(key)
        size += length
    rows = SessionItemContent.objects.in_bulk({key[3] for key in selected if key[3] is not None})
    entries = []
    prepared_ids = set()
    last = cursor
    verified = True
    for session_id, line, slot, content_id, item_valid in selected:
        row = rows.get(content_id)
        verified &= item_valid and (content_id is None or row is not None)
        if row is not None and row.codec == 'plain' and row.dictionary_id is not None:
            verified = False
        if row is not None and row.codec != 'plain' and row.id not in prepared_ids:
            prepared_ids.add(row.id)
            encoded = encode_plain(decode_content(row))
            candidate = PreparedRepresentation(row.id, row.codec, row.dictionary_id, encoded, 'plain', False)
            entries.append(PromotionEntry(candidate, bytes(row.payload)))
        last = RootPromotionCursor(session_id, line, slot, len(selected), size, False)
    return PromotionBatch(snapshot, last._replace(rows=len(selected), bytes=size, done=len(selected) == len(keys)),
                          tuple(entries), verified)


def apply_promotion_batch(batch, active, *, pass_verified=False):
    with transaction.atomic():
        if not _snapshot_matches(batch.snapshot):
            return START_CURSOR
        decision = resolve_root_decision(batch.snapshot.root_id, timezone.now(), active)
        if not decision.resolved or decision.is_cold:
            return START_CURSOR
        _require_writer_transaction()
        rows = SessionItemContent.objects.in_bulk(entry.candidate.content_id for entry in batch.entries)
        changed = []
        for entry in batch.entries:
            candidate = entry.candidate
            row = rows.get(candidate.content_id)
            encoded = candidate.encoded
            # Decompression needs representation CAS, not compression eligibility.
            # Owner changes remain intact; tree/reference changes invalidate the root epoch.
            if (row is None or row.codec != candidate.observed_codec
                    or row.dictionary_id != candidate.observed_dictionary_id
                    or bytes(row.payload) != entry.observed_payload
                    or bytes(row.sha256) != encoded.sha256 or row.raw_size != encoded.raw_size):
                return START_CURSOR
            row.payload = encoded.payload
            row.codec = 'plain'
            row.dictionary_id = None
            changed.append(row)
        if changed:
            SessionItemContent.objects.bulk_update(changed, ['payload', 'codec', 'dictionary'], batch_size=ROW_BUDGET)
        if batch.cursor.done and pass_verified and batch.verified:
            # The operation checks every session/item slot across admitted slices.
            # The unchanged root generation fences every tree/reference mutation.
            Session.objects.filter(pk=batch.snapshot.root_id, storage_incarnation=batch.snapshot.incarnation,
                                   content_state_epoch=batch.snapshot.epoch).update(
                                       plaintext_verified_epoch=batch.snapshot.epoch,
                                   )
        return batch.cursor


def promote_root_slice(snapshot: RootStorageSnapshot, cursor: RootPromotionCursor,
                       row_budget: int, byte_budget: int) -> RootPromotionCursor:
    """Promote one bounded slice; async production prepares before acquiring admission.

    An externally resumed cursor carries no prior readiness proof. Only a full
    pass from START_CURSOR can certify readiness. The coordinator accumulates
    each batch's explicit metadata proof and restarts from zero after changes.
    """
    batch = prepare_promotion_batch(snapshot, cursor, row_budget, byte_budget)
    return apply_promotion_batch(batch, active_session_ids.get(), pass_verified=cursor == START_CURSOR)


def pending_root_page(after_id=None):
    """Recover durable pending roots from bounded metadata, without reading content."""
    query = Session.objects.filter(type='session', parent_session__isnull=True,
                                   storage_incarnation__isnull=False, content_state_epoch__isnull=False)
    query = query.filter(Q(plaintext_verified_epoch__isnull=True) | ~Q(plaintext_verified_epoch=F('content_state_epoch')))
    if after_id is not None:
        query = query.filter(pk__gt=after_id)
    return list(query.order_by('id').values_list('id', flat=True)[:TREE_PAGE_SIZE])


def prepare_compression_batch(after_id, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    entries = []
    size = 0
    last = after_id
    query = SessionItemContent.objects.filter(pk__gt=after_id).order_by('id')
    rows = list(query.annotate(
        stored_size=Length('payload'), owner_size=Length(Cast('session_ids', BinaryField())),
    ).values('id', 'raw_size', 'stored_size', 'owner_size')[:row_budget])
    for metadata in rows:
        cost = metadata['raw_size'] + metadata['stored_size'] + metadata['owner_size']
        if last != after_id and size + cost > byte_budget:
            break
        last = metadata['id']
        size += cost
        row = SessionItemContent.objects.get(pk=last)
        if not _owners_cold(row, frozenset()):
            continue
        dictionary = _dictionary(row)
        if dictionary is None:
            continue
        policy = compression_policy_version(row.dictionary_provider, dictionary.id)
        if (row.compression_attempt_version == policy and row.compression_no_gain
                or row.codec == 'zstd' and row.dictionary_id == dictionary.id):
            continue
        proof = _proof(row)
        try:
            candidate = prepare_representation(row.id, policy)
        except ContentIntegrityError:
            raise
        except Exception:
            logger.warning('Optional content compression failed for %s', row.id, exc_info=True)
            continue
        entries.append(PreparedEntry(candidate, proof))
    return last, tuple(entries), not SessionItemContent.objects.filter(pk__gt=last).exists()


async def _apply(function, *args):
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.db_writer import run_under_db_write_lock, storage_admission

    async def apply():
        return await run_compute_sync(function, *args, active_session_ids.get())

    async with storage_admission():
        return await run_under_db_write_lock(apply, content_activity=True)


def _snapshot(root_id, active):
    with transaction.atomic():
        decision = resolve_root_decision(root_id, timezone.now(), active)
        if not decision.resolved or decision.root_id != root_id or decision.is_cold:
            return None
        return snapshot_root_storage(root_id)


def _publish(entries, active):
    with transaction.atomic():
        return publish_prepared_batch(entries, active)


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


async def start_content_maintenance_task(stop_event: asyncio.Event) -> None:
    """Run one bounded preparation at a time, releasing admission between each slice."""
    global _wakeup
    from django.conf import settings

    from twicc.session_dirs_cleanup_task import _run_cleanup_pass

    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    roots = set()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='twicc-content-codec')

    def enqueue(root_id):
        roots.add(root_id)
        wake.set()

    def wakeup(root_id):
        loop.call_soon_threadsafe(enqueue, root_id)

    _wakeup = wakeup
    set_root_promotion_wakeup(request_root_promotion)

    async def prepare(function, *args):
        return await database_sync_to_async(partial(function, *args), thread_sensitive=False, executor=executor)()

    async def recover():
        after = None
        while not stop_event.is_set():
            page = await prepare(pending_root_page, after)
            if not page:
                break
            roots.update(page)
            await promote()
            after = page[-1]

    async def promote():
        while roots and not stop_event.is_set():
            root_id = roots.pop()
            try:
                snapshot = await _apply(_snapshot, root_id)
                if snapshot is None:
                    continue
                cursor = START_CURSOR
                pass_verified = True
                while not cursor.done and not stop_event.is_set():
                    batch = await prepare(prepare_promotion_batch, snapshot, cursor, ROW_BUDGET, BYTE_BUDGET)
                    pass_verified &= batch.verified
                    cursor = await _apply(partial(apply_promotion_batch, pass_verified=pass_verified), batch)
                    if cursor == START_CURSOR:
                        roots.add(root_id)
                        break
                    await asyncio.sleep(0)
                # Incomplete extraction does not spin. Its writer invalidation wakes us again.
            except Exception:
                # The pending generation survives failures and restarts.
                logger.exception('Content promotion failed for root %s', root_id)

    deadline = _maintenance_time() + INITIAL_DELAY
    try:
        try:
            await recover()
        except Exception:
            logger.exception('Content promotion startup recovery failed')
        while not stop_event.is_set():
            wake.clear()
            await promote()
            if stop_event.is_set():
                break
            if _maintenance_time() >= deadline:
                try:
                    await recover()
                    await promote()
                    cursor, done = 0, False
                    while not done and not stop_event.is_set():
                        await promote()
                        cursor, entries, done = await prepare(prepare_compression_batch, cursor, ROW_BUDGET, BYTE_BUDGET)
                        if roots:
                            await promote()
                        if entries:
                            await _apply(_publish, entries)
                        await asyncio.sleep(0)
                except Exception:
                    logger.exception('Periodic content maintenance failed')
                if settings.SESSION_DIRS_CLEANUP_ENABLED and not stop_event.is_set():
                    try:
                        await _run_cleanup_pass()
                    except Exception:
                        logger.exception('Session directory maintenance failed')
                deadline = _maintenance_time() + INTERVAL
            await _wait(stop_event, wake, max(0, deadline - _maintenance_time()))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Content maintenance task failed')
    finally:
        set_root_promotion_wakeup(None)
        _wakeup = None
        # Submitted read/CPU work can outlive cancellation. Drain it before teardown.
        async def close():
            await loop.run_in_executor(executor, connections.close_all)
            await asyncio.to_thread(executor.shutdown, wait=True)
        closing = asyncio.create_task(close())
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                continue
        closing.result()
