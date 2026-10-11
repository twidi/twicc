"""Native-root metadata policy and transaction-owned durable plaintext generations."""

from collections.abc import Iterable, Iterator
from datetime import datetime
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import F
from django.db.models.functions import Coalesce, Length
from django.utils import timezone

from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION, cold_from_fields
from twicc.core.content_storage.types import RootDecision, RootStorageSnapshot
from twicc.core.models import Session, SessionItem

TREE_PAGE_SIZE = 128
ITEM_PAGE_SIZE = 256


def _require_writer_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content state writes require a caller-owned transaction')


def resolve_root_decisions(
    session_ids: Iterable[str], now: datetime, active_root_ids: frozenset[str],
) -> dict[str, RootDecision]:
    """Load native ancestry by metadata batches, including active native descendants."""
    requested = tuple(set(session_ids))
    metadata = {}
    frontier = set(requested) | set(active_root_ids)
    while frontier:
        found = {}
        # Bound SQL parameters and loaded metadata per query.
        ids = sorted(frontier)
        for offset in range(0, len(ids), TREE_PAGE_SIZE):
            found.update({row['id']: row for row in Session.objects.filter(
                pk__in=ids[offset:offset + TREE_PAGE_SIZE],
            ).values('id', 'type', 'parent_session_id', *COLD_FIELDS)})
        metadata.update(dict.fromkeys(frontier))
        metadata.update(found)
        frontier = {row['parent_session_id'] for row in found.values()
                    if row['type'] == 'subagent' and row['parent_session_id'] is not None
                    and row['parent_session_id'] not in metadata}

    def native_root(session_id):
        visited = set()
        while session_id not in visited:
            visited.add(session_id)
            fields = metadata.get(session_id)
            if fields is None:
                break
            parent_id = fields['parent_session_id']
            if fields['type'] == 'session' and parent_id is None:
                return session_id
            if fields['type'] != 'subagent' or parent_id is None:
                break
            session_id = parent_id
        return None

    active = {native_root(session_id) for session_id in active_root_ids}
    decisions = {}
    for session_id in requested:
        root_id = native_root(session_id)
        if root_id is None:
            decisions[session_id] = RootDecision(None, False, False, False)
        else:
            is_active = root_id in active
            decisions[session_id] = RootDecision(
                root_id, not is_active and cold_from_fields(metadata[root_id], now), True, is_active,
            )
    return decisions


def resolve_root_decision(session_id: str, now: datetime, active_root_ids: frozenset[str]) -> RootDecision:
    """Resolve native ancestry; unresolved trees stay conservative."""
    return resolve_root_decisions([session_id], now, active_root_ids)[session_id]


def ensure_storage_incarnation(session_id: str) -> str:
    """Initialize legacy identity once, under the caller's writer transaction."""
    _require_writer_transaction()
    Session.objects.filter(pk=session_id, storage_incarnation__isnull=True).update(storage_incarnation=str(uuid4()))
    return Session.objects.values_list('storage_incarnation', flat=True).get(pk=session_id)


def invalidate_roots(root_ids: Iterable[str]) -> None:
    """Advance each supplied root once for this mutation group; callers supply old and new roots."""
    _require_writer_transaction()
    roots = tuple(set(root_ids))
    for offset in range(0, len(roots), TREE_PAGE_SIZE):
        Session.objects.filter(pk__in=roots[offset:offset + TREE_PAGE_SIZE]).update(
            content_state_epoch=Coalesce(F('content_state_epoch'), 0) + 1,
        )
    from twicc.core.content_storage.lifecycle import active_session_ids, request_root_promotion

    decisions = resolve_root_decisions(roots, timezone.now(), active_session_ids.get())
    for root_id, decision in decisions.items():
        if decision.resolved and decision.root_id == root_id and not decision.is_cold:
            transaction.on_commit(lambda root_id=root_id: request_root_promotion(root_id))


def iter_native_tree_session_pages(
    root_id: str, after_id: str | None, page_size: int,
) -> Iterator[list[str]]:
    """Page native descendants in ID order; recursive SQL bounds Python memory at every depth."""
    if page_size <= 0:
        raise ValueError('Native tree page size must be positive')
    decision = resolve_root_decision(root_id, timezone.now(), frozenset())
    if not decision.resolved or decision.root_id != root_id:
        return
    table = connection.ops.quote_name(Session._meta.db_table)
    while True:
        with connection.cursor() as cursor:
            cursor.execute(
                f'WITH RECURSIVE native_tree(id) AS ('
                f'SELECT id FROM {table} WHERE id = %s '
                f'UNION SELECT child.id FROM {table} child JOIN native_tree parent '
                f"ON child.parent_session_id = parent.id WHERE child.type = 'subagent') "
                'SELECT id FROM native_tree WHERE (%s IS NULL OR id > %s) ORDER BY id LIMIT %s',
                [root_id, after_id, after_id, page_size],
            )
            page = [row[0] for row in cursor.fetchall()]
        if not page:
            return
        yield page
        after_id = page[-1]


def snapshot_root_storage(root_id: str) -> RootStorageSnapshot:
    """Prepare a durable root identity and generation under caller writer ownership."""
    _require_writer_transaction()
    decision = resolve_root_decision(root_id, timezone.now(), frozenset())
    if not decision.resolved or decision.root_id != root_id:
        raise ValueError('Storage snapshot requires a resolved native root')
    incarnation = ensure_storage_incarnation(root_id)
    Session.objects.filter(pk=root_id, content_state_epoch__isnull=True).update(content_state_epoch=0)
    epoch = Session.objects.values_list('content_state_epoch', flat=True).get(pk=root_id)
    return RootStorageSnapshot(root_id, incarnation, epoch)


def _snapshot_matches(snapshot: RootStorageSnapshot) -> bool:
    fields = Session.objects.filter(pk=snapshot.root_id).values('storage_incarnation', 'content_state_epoch').first()
    return (fields is not None and fields['storage_incarnation'] == snapshot.incarnation
            and fields['content_state_epoch'] == snapshot.epoch)


def verify_plaintext_root(
    snapshot: RootStorageSnapshot, *, now: datetime | None = None, active_root_ids: frozenset[str] = frozenset(),
) -> bool:
    """Publish only complete extracted plaintext for the same root incarnation and generation.

    The caller owns the writer transaction and invalidates every ownership/tree mutation.
    This checks payload metadata only. It never loads payload or legacy text bytes.
    The caller supplies its active-root admission snapshot. Cold inactive roots cannot publish readiness.
    """
    _require_writer_transaction()
    if not _snapshot_matches(snapshot):
        return False
    now = now or timezone.now()
    decision = resolve_root_decision(snapshot.root_id, now, active_root_ids)
    if not decision.resolved or decision.root_id != snapshot.root_id or decision.is_cold:
        return False
    found_root = False
    for page in iter_native_tree_session_pages(snapshot.root_id, None, TREE_PAGE_SIZE):
        found_root |= snapshot.root_id in page
        metadata = Session.objects.filter(pk__in=page).values('content_storage_version', 'storage_incarnation')
        if any(row['content_storage_version'] != CONTENT_STORAGE_VERSION or not row['storage_incarnation']
               for row in metadata):
            return False
        if Session.objects.filter(parent_session_id__in=page).exclude(type='subagent').exists():
            return False
        for session_id in page:
            after_line = -1
            while True:
                rows = list(SessionItem.objects.filter(session_id=session_id, line_num__gt=after_line)
                            .annotate(legacy_length=Length('content'))
                            .order_by('line_num').values(
                                'line_num', 'legacy_length', 'current_content_id', 'original_content_id',
                                'current_content__codec', 'current_content__dictionary_id',
                                'original_content__codec', 'original_content__dictionary_id',
                            )[:ITEM_PAGE_SIZE])
                if not rows:
                    break
                for row in rows:
                    if row['legacy_length'] != 0 or row['current_content_id'] is None:
                        return False
                    for slot in ('current_content', 'original_content'):
                        if row[f'{slot}_id'] is not None and (
                            row[f'{slot}__codec'] != 'plain' or row[f'{slot}__dictionary_id'] is not None
                        ):
                            return False
                after_line = rows[-1]['line_num']
    decision = resolve_root_decision(snapshot.root_id, now, active_root_ids)
    if (not found_root or not _snapshot_matches(snapshot) or not decision.resolved
            or decision.root_id != snapshot.root_id or decision.is_cold):
        return False
    return bool(Session.objects.filter(
        pk=snapshot.root_id, storage_incarnation=snapshot.incarnation, content_state_epoch=snapshot.epoch,
    ).update(plaintext_verified_epoch=snapshot.epoch))


def extract_storage_slice(session_id: str, observed_incarnation: str, observed_epoch: int,
                          upper_line: int, cursor: int, row_budget: int, byte_budget: int):
    """Expose the shared bounded extraction interface without an import cycle."""
    from twicc.core.content_storage.extraction import extract_storage_slice

    return extract_storage_slice(session_id, observed_incarnation, observed_epoch,
                                 upper_line, cursor, row_budget, byte_budget)


class PlaintextSearchUnavailable(ValueError):
    """Public substring search cannot use this root's current durable state."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f'Substring search unavailable: {reason}. '
                         'Use global full-text search or read specific lines without --contains.')


def require_plaintext_search(session_id: str) -> RootStorageSnapshot:
    """Validate current DB ancestry and readiness inside the caller's read snapshot.

    Persisted cold policy governs this public operation. Runtime activity never
    overrides it, including in a standalone CLI process. The verified generation
    certifies complete extraction and both plaintext FK slots across the native
    tree; ownership and tree writers invalidate it in their mutation transaction.
    """
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Substring search requires a caller-owned read transaction')
    decision = resolve_root_decision(session_id, timezone.now(), frozenset())
    if not decision.resolved:
        raise PlaintextSearchUnavailable('unresolved_ancestry')
    if decision.is_cold:
        raise PlaintextSearchUnavailable('cold')
    fields = Session.objects.filter(pk=decision.root_id).values(
        'storage_incarnation', 'content_storage_version', 'content_state_epoch', 'plaintext_verified_epoch',
    ).get()
    epoch = fields['content_state_epoch']
    if (not fields['storage_incarnation'] or fields['content_storage_version'] != CONTENT_STORAGE_VERSION
            or epoch is None or fields['plaintext_verified_epoch'] != epoch):
        raise PlaintextSearchUnavailable('warming')
    return RootStorageSnapshot(decision.root_id, fields['storage_incarnation'], epoch)
