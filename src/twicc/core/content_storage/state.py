"""Native-root metadata policy and transaction-owned durable plaintext generations."""

from collections.abc import Iterable, Iterator
from datetime import datetime
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION, cold_from_fields, scratch_cold_from_fields
from twicc.core.content_storage.types import RootDecision, RootStorageSnapshot, SessionPromotionSnapshot
from twicc.core.models import Session, SessionItem, SessionItemContent

TREE_PAGE_SIZE = 128
ITEM_PAGE_SIZE = 256


def _require_writer_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content state writes require a caller-owned transaction')


def resolve_root_decisions(
    session_ids: Iterable[str], now: datetime, active_root_ids: frozenset[str],
) -> dict[str, RootDecision]:
    """Resolve content eligibility from bounded native-root metadata batches."""
    return _resolve_root_decisions(session_ids, now, active_root_ids, cold_from_fields)


def resolve_scratch_root_decisions(
    session_ids: Iterable[str], now: datetime, active_root_ids: frozenset[str],
) -> dict[str, RootDecision]:
    """Apply the independent scratch policy with the same native active-tree protection."""
    return _resolve_root_decisions(session_ids, now, active_root_ids, scratch_cold_from_fields)


def _resolve_root_decisions(session_ids, now, active_root_ids, predicate):
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
                root_id, not is_active and predicate(metadata[root_id], now), True, is_active,
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


def invalidate_roots(root_ids: Iterable[str], *, promotion_targets: Iterable[str] | None = None) -> None:
    """Fence native-root mutations separately from the sessions that require plaintext promotion."""
    _require_writer_transaction()
    roots = tuple(set(root_ids))
    for offset in range(0, len(roots), TREE_PAGE_SIZE):
        Session.objects.filter(pk__in=roots[offset:offset + TREE_PAGE_SIZE]).update(
            content_state_epoch=Coalesce(F('content_state_epoch'), 0) + 1,
        )
    from twicc.core.content_storage.lifecycle import active_session_ids, request_root_promotion

    from twicc.providers.write_coordinator import write_priority

    priority = write_priority.get()
    targets = roots if promotion_targets is None else tuple(set(promotion_targets))
    decisions = resolve_root_decisions(targets, timezone.now(), active_session_ids.get())
    for session_id, decision in decisions.items():
        if decision.resolved and not decision.is_cold:
            transaction.on_commit(lambda session_id=session_id, priority=priority:
                                  request_root_promotion(session_id, priority=priority))


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


def snapshot_session_promotion(session_id: str) -> RootStorageSnapshot | SessionPromotionSnapshot:
    """Capture one target identity and its native-root mutation fence under writer ownership."""
    _require_writer_transaction()
    decision = resolve_root_decision(session_id, timezone.now(), frozenset())
    if not decision.resolved:
        raise ValueError('Storage promotion requires resolved native ancestry')
    root = snapshot_root_storage(decision.root_id)
    if session_id == root.root_id:
        return root
    incarnation = ensure_storage_incarnation(session_id)
    return SessionPromotionSnapshot(root.root_id, root.incarnation, root.epoch, session_id, incarnation)


def _snapshot_matches(snapshot: RootStorageSnapshot | SessionPromotionSnapshot) -> bool:
    fields = Session.objects.filter(pk=snapshot.root_id).values('storage_incarnation', 'content_state_epoch').first()
    if (fields is None or fields['storage_incarnation'] != snapshot.incarnation
            or fields['content_state_epoch'] != snapshot.epoch):
        return False
    if isinstance(snapshot, SessionPromotionSnapshot):
        incarnation = Session.objects.filter(pk=snapshot.session_id).values_list('storage_incarnation', flat=True).first()
        decision = resolve_root_decision(snapshot.session_id, timezone.now(), frozenset())
        return (incarnation == snapshot.session_incarnation and decision.resolved
                and decision.root_id == snapshot.root_id)
    decision = resolve_root_decision(snapshot.root_id, timezone.now(), frozenset())
    return decision.resolved and decision.root_id == snapshot.root_id


def session_plaintext_metadata(session_id: str) -> bool:
    """Check complete extraction and both plaintext slots for one session without loading content bytes.

    Callers hold one read snapshot or the writer transaction. SQL probes keep
    Python memory and query count bounded. A native parent's certificate never authorizes a child.
    """
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Plaintext metadata requires a caller-owned transaction')
    fields = Session.objects.filter(pk=session_id).values('content_storage_version', 'storage_incarnation').first()
    if (fields is None or fields['content_storage_version'] != CONTENT_STORAGE_VERSION
            or not fields['storage_incarnation']):
        return False
    return not _session_has_invalid_storage_metadata(session_id)


def _session_has_invalid_storage_metadata(session_id: str) -> bool:
    """Probe invalid rows in the caller's transaction without loading per-item metadata."""
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Storage metadata requires a caller-owned transaction')
    items = connection.ops.quote_name(SessionItem._meta.db_table)
    contents = connection.ops.quote_name(SessionItemContent._meta.db_table)
    # SQL comparisons against NULL are UNKNOWN. Test joined IDs explicitly so
    # dangling references cannot pass, including an optional original reference.
    invalid = (
        'item.content IS NULL OR LENGTH(item.content) <> 0 OR current.id IS NULL '
        "OR current.codec IS NULL OR current.codec <> 'plain' OR current.dictionary_id IS NOT NULL "
        "OR (item.original_content_id IS NOT NULL AND (original.id IS NULL OR original.codec IS NULL "
        "OR original.codec <> 'plain' OR original.dictionary_id IS NOT NULL))"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT 1 FROM {items} item '
            f'LEFT JOIN {contents} current ON current.id = item.current_content_id '
            f'LEFT JOIN {contents} original ON original.id = item.original_content_id '
            f'WHERE item.session_id = %s AND ({invalid}) LIMIT 1',
            [session_id],
        )
        return cursor.fetchone() is not None


def verify_plaintext_root(
    snapshot: RootStorageSnapshot, *, now: datetime | None = None, active_root_ids: frozenset[str] = frozenset(),
) -> bool:
    """Certify only this root session's extracted plaintext for its current native-root generation."""
    _require_writer_transaction()
    if not _snapshot_matches(snapshot):
        return False
    now = now or timezone.now()
    decision = resolve_root_decision(snapshot.root_id, now, active_root_ids)
    if not decision.resolved or decision.root_id != snapshot.root_id or decision.is_cold:
        return False
    if not session_plaintext_metadata(snapshot.root_id):
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
    overrides it, including in a standalone CLI process. Root certificates cover
    only the root's own references. Children and uncertified roots require scoped
    metadata validation in this same read snapshot. It never publishes a certificate.
    A hot root can still have compressed children.
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
    certified_root = (
        session_id == decision.root_id and bool(fields['storage_incarnation'])
        and fields['content_storage_version'] == CONTENT_STORAGE_VERSION
        and epoch is not None and fields['plaintext_verified_epoch'] == epoch
    )
    # A child-only mutation can stale the root certificate without changing root
    # bytes. Read-only scoped validation preserves availability for that root.
    if not certified_root and not session_plaintext_metadata(session_id):
        raise PlaintextSearchUnavailable('warming')
    return RootStorageSnapshot(decision.root_id, fields['storage_incarnation'], epoch)
