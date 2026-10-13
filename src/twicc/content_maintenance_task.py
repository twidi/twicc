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

from twicc.core.content_storage.codec import ContentIntegrityError, transcode_content
from twicc.core.content_storage.lifecycle import active_session_ids, set_root_promotion_wakeup
from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION, compression_policy_version
from twicc.core.content_storage.state import (
    TREE_PAGE_SIZE, _require_writer_transaction, _snapshot_matches,
    resolve_root_decision, resolve_root_decisions, snapshot_session_promotion,
)
from twicc.core.content_storage.types import PreparedRepresentation, RootPromotionCursor, RootStorageSnapshot, SessionPromotionSnapshot
from twicc.core.models import Session, SessionItem, SessionItemContent
from twicc.core.content_storage.dictionaries import get_provider_dictionary
from twicc.providers.write_coordinator import WritePriority, write_priority

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
    snapshot: RootStorageSnapshot | SessionPromotionSnapshot
    cursor: RootPromotionCursor
    entries: tuple[PromotionEntry, ...]
    verified: bool


def request_root_promotion(session_id: str, *, priority: WritePriority | None = None) -> None:
    """Marshal one actual target; the historical name also accepts native-child IDs."""
    if _wakeup is not None:
        _wakeup(session_id, write_priority.get() if priority is None else priority)


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


@transaction.atomic
def prepare_promotion_batch(snapshot, cursor, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    if not _snapshot_matches(snapshot):
        return PromotionBatch(snapshot, cursor, (), False)
    decision = resolve_root_decision(snapshot.root_id, timezone.now(), frozenset())
    if not decision.resolved or (getattr(snapshot, 'recovery', False) and decision.is_cold):
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
    dictionary_cache = {}
    last = cursor
    verified = True
    for session_id, line, slot, content_id, item_valid in selected:
        row = rows.get(content_id)
        verified &= item_valid and (content_id is None or row is not None)
        if row is not None and row.codec == 'plain' and row.dictionary_id is not None:
            verified = False
        if row is not None and row.codec != 'plain' and row.id not in prepared_ids:
            prepared_ids.add(row.id)
            encoded, _ = transcode_content(row, dictionary_cache=dictionary_cache)
            candidate = PreparedRepresentation(row.id, row.codec, row.dictionary_id, encoded, 'plain', False)
            entries.append(PromotionEntry(candidate, bytes(row.payload)))
        last = RootPromotionCursor(session_id, line, slot, len(selected), size, False)
    return PromotionBatch(snapshot, last._replace(rows=len(selected), bytes=size, done=len(selected) == len(keys)),
                          tuple(entries), verified)


def apply_promotion_batch(batch, active, *, pass_verified=False):
    with transaction.atomic():
        if not _snapshot_matches(batch.snapshot):
            return START_CURSOR
        eligibility_active = frozenset() if getattr(batch.snapshot, 'recovery', False) else active
        decision = resolve_root_decision(batch.snapshot.root_id, timezone.now(), eligibility_active)
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


def pending_root_page(after_id=None):
    """Recover root-only demand from metadata; never infer demand for inherited-hot children."""
    query = Session.objects.filter(type='session', parent_session__isnull=True,
                                   storage_incarnation__isnull=False, content_state_epoch__isnull=False)
    query = query.filter(Q(plaintext_verified_epoch__isnull=True) | ~Q(plaintext_verified_epoch=F('content_state_epoch')))
    if after_id is not None:
        query = query.filter(pk__gt=after_id)
    return list(query.order_by('id').values_list('id', flat=True)[:TREE_PAGE_SIZE])


def recovery_promotion_targets(root_ids):
    """Recover persisted-hot roots only; an active child does not create demand for its cold root."""
    decisions = resolve_root_decisions(root_ids, timezone.now(), frozenset())
    return {root_id for root_id, decision in decisions.items()
            if decision.resolved and decision.root_id == root_id and not decision.is_cold}


def prepare_compression_batch(after_id, row_budget=ROW_BUDGET, byte_budget=BYTE_BUDGET):
    if row_budget <= 0 or byte_budget <= 0:
        raise ValueError('Maintenance budgets must be positive')
    entries = []
    size = 0
    last = after_id
    dictionaries = {}
    dictionary_cache = {}
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
        if row.dictionary_provider not in dictionaries:
            dictionary = _dictionary(row)
            policy = None if dictionary is None else compression_policy_version(
                row.dictionary_provider, dictionary.id, dictionary=dictionary,
            )
            dictionaries[row.dictionary_provider] = dictionary, policy
        dictionary, policy = dictionaries[row.dictionary_provider]
        if dictionary is None:
            continue
        if (row.compression_attempt_version == policy and row.compression_no_gain
                or row.codec == 'zstd' and row.dictionary_id == dictionary.id):
            continue
        proof = _proof(row)
        try:
            candidate = prepare_representation(row.id, policy, row, dictionary, dictionary_cache)
        except ContentIntegrityError:
            raise
        except Exception:
            logger.warning('Optional content compression failed for %s', row.id, exc_info=True)
            continue
        entries.append(PreparedEntry(candidate, proof))
    return last, tuple(entries), not SessionItemContent.objects.filter(pk__gt=last).exists()


async def _apply(function, *args, priority=WritePriority.FOREGROUND, lease=None):
    from twicc.providers.compute_executor import run_compute_sync
    from twicc.providers.db_writer import (
        _drive_publication, _run_with_content_activity, run_under_storage_write_lock,
    )

    async def apply():
        return await run_compute_sync(function, *args, active_session_ids.get())

    if lease is None:
        return await run_under_storage_write_lock(apply, priority=priority, content_activity=True)
    try:
        return await _drive_publication(
            lambda: _run_with_content_activity(apply), lease.priority, lease.resources,
        )
    finally:
        lease.release()


def _snapshot(session_id, active, *, recovery=False):
    with transaction.atomic():
        eligibility_active = frozenset() if recovery else active
        decision = resolve_root_decision(session_id, timezone.now(), eligibility_active)
        if (not decision.resolved or decision.is_cold
                or recovery and decision.root_id != session_id):
            return None
        snapshot = snapshot_session_promotion(session_id)
        if recovery:
            return SessionPromotionSnapshot(snapshot.root_id, snapshot.incarnation, snapshot.epoch,
                                            session_id, snapshot.incarnation, recovery=True)
        return snapshot


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


class _PromotionTarget:
    """One resumable target; scheduling priority never determines recovery eligibility."""

    def __init__(self, *, recovery, priority):
        self.recovery = recovery
        self.priority = priority
        self.demand = None
        self.reset()

    def reset(self):
        self.snapshot = None
        self.cursor = START_CURSOR
        self.verified = True
        self.batch = None
        self.deferred = False

    def explicit(self):
        self.recovery = False
        if self.snapshot is not None and getattr(self.snapshot, 'recovery', False):
            self.snapshot = self.snapshot._replace(recovery=False)
            if self.batch is not None:
                self.batch = self.batch._replace(snapshot=self.snapshot)


async def start_content_maintenance_task(stop_event: asyncio.Event) -> None:
    """Dispatch bounded target steps; foreground demand wins each new resource grant."""
    global _wakeup, _deferral_wakeup
    from django.conf import settings

    from twicc.providers.db_writer import get_write_coordinator
    from twicc.session_dirs_cleanup_task import _run_cleanup_pass

    loop = asyncio.get_running_loop()
    coordinator = get_write_coordinator()
    wake = asyncio.Event()
    targets = {}
    closing = False
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='twicc-content-codec')

    def enqueue(session_id, priority):
        if closing or stop_event.is_set():
            return
        target = targets.setdefault(session_id, _PromotionTarget(recovery=False, priority=priority))
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
        return await database_sync_to_async(partial(function, *args), thread_sensitive=False, executor=executor)()

    async def recover():
        after = None
        while not stop_event.is_set():
            page = await prepare(pending_root_page, after)
            if not page:
                break
            eligible = await prepare(recovery_promotion_targets, page)
            for session_id in page:
                if session_id in eligible:
                    targets.setdefault(session_id, _PromotionTarget(recovery=True, priority=WritePriority.BACKGROUND))
            await promote()
            after = page[-1]

    def settle(session_id, target):
        targets.pop(session_id)
        if target.demand is not None:
            target.demand.release()

    def eligible(target):
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
            ready = [key for key, target in targets.items() if eligible(target)]
            if not ready:
                return
            session_id = next((key for key in ready if targets[key].priority is WritePriority.FOREGROUND), ready[0])
            target = targets[session_id]
            try:
                if target.snapshot is not None and target.batch is None:
                    recovery = target.recovery
                    batch = await prepare(prepare_promotion_batch, target.snapshot, target.cursor,
                                          ROW_BUDGET, BYTE_BUDGET)
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
                        await _wait(stop_event, wake, 0.05)
                        continue
                if target.snapshot is None:
                    recovery = target.recovery
                    target.snapshot = await _apply(partial(_snapshot, recovery=recovery), session_id,
                                                   priority=target.priority, lease=lease)
                    if target.snapshot is None:
                        if recovery and not target.recovery:
                            continue
                        settle(session_id, target)
                    elif not target.recovery:
                        target.explicit()
                    continue
                verified = target.verified and target.batch.verified
                cursor = await _apply(partial(apply_promotion_batch, pass_verified=verified), target.batch,
                                      priority=target.priority, lease=lease)
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
                settle(session_id, target)
                logger.exception('Content promotion failed for session %s', session_id)

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
                        while entries and not stop_event.is_set():
                            wake.clear()
                            await promote()
                            if stop_event.is_set():
                                break
                            lease = coordinator.try_background_lease()
                            if lease is None:
                                await _wait(stop_event, wake, 0.05)
                                continue
                            await _apply(_publish, entries, lease=lease)
                            break
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
                if target.demand is not None:
                    target.demand.release()
