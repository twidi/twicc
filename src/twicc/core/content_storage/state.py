"""Native-root metadata policy and transaction-owned durable plaintext generations."""

from collections import ChainMap
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION, cold_from_fields, scratch_cold_from_fields
from twicc.core.content_storage.types import RootDecision, RootStorageSnapshot, SessionPromotionSnapshot
from twicc.core.content_storage.session_policy import resolve_content_decisions
from twicc.core.models import ContentDictionary, Session, SessionItem, SessionItemContent

TREE_PAGE_SIZE = 128
ITEM_PAGE_SIZE = 256
STORAGE_STATE_FIELDS = (
    'storage_incarnation', 'content_storage_version', 'content_state_epoch', 'plaintext_verified_epoch',
)
NATIVE_METADATA_FIELDS = ('id', 'type', 'parent_session_id', *COLD_FIELDS, *STORAGE_STATE_FIELDS)


def _require_writer_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content state writes require a caller-owned transaction')


def native_metadata_row(session: Session) -> dict:
    """Return the native metadata of a freshly loaded instance, shaped like a loaded row."""
    return {name: getattr(session, name) for name in NATIVE_METADATA_FIELDS}


def _native_metadata_page(session_ids: list[str], names: tuple[str, ...]) -> Iterator[dict]:
    """Read the fixed projection without compiling a new QuerySet per tree page.

    Apply the same backend and field converters as Django's SQL compiler.
    In particular, SQLite dates must become aware and booleans stay booleans.
    No database values are cached: every page reads the caller's transaction.
    """
    fields = [Session._meta.get_field(name) for name in names]
    quote = connection.ops.quote_name
    table = Session._meta.db_table
    columns = ', '.join(quote(field.column) for field in fields)
    converters = []
    for index, field in enumerate(fields):
        expression = field.get_col(table)
        functions = connection.ops.get_db_converters(expression) + expression.get_db_converters(connection)
        if functions:
            converters.append((index, functions, expression))
    with connection.cursor() as cursor:
        placeholders = ', '.join(['%s'] * len(session_ids))
        cursor.execute(
            f'SELECT {columns} FROM {quote(table)} WHERE {quote(Session._meta.pk.column)} IN ({placeholders})',
            session_ids,
        )
        rows = cursor.fetchall()
    for row in map(list, rows):
        for index, functions, expression in converters:
            for function in functions:
                row[index] = function(row[index], expression, connection)
        yield dict(zip(names, row, strict=True))


def load_native_metadata[Row: Mapping](
    session_ids: Iterable[str], *, known: Mapping[str, Row | None] | None = None, ancestors: bool = True,
    extra_fields: tuple[str, ...] = (),
) -> Mapping[str, Row | dict | None]:
    """Load native metadata rows, reusing ``known`` rows and querying only the absent ids.

    Every requested id maps to its row, or to ``None`` for a missing session. With
    ``ancestors``, subagent parents are loaded one level at a time, including the
    parents of ``known`` rows; cycles stop the walk. ``known`` is never mutated:
    the result is a view of the loaded rows over ``known`` (no copy, so a caller
    resolving many roots from one ``known`` mapping stays linear). ``extra_fields``
    extends fresh rows for callers that also validate a history revision.
    """
    loaded = {}
    metadata = ChainMap(loaded, known) if known else loaded
    walked = set()
    frontier = set(session_ids)
    names = tuple(dict.fromkeys((*NATIVE_METADATA_FIELDS, *extra_fields)))
    while frontier:
        # Bound SQL parameters and loaded metadata per query.
        missing = sorted(identity for identity in frontier if identity not in metadata)
        for offset in range(0, len(missing), TREE_PAGE_SIZE):
            page = missing[offset:offset + TREE_PAGE_SIZE]
            loaded.update(dict.fromkeys(page))
            loaded.update({row['id']: row for row in _native_metadata_page(page, names)})
        if not ancestors:
            break
        walked |= frontier
        frontier = {row['parent_session_id'] for row in (metadata[identity] for identity in frontier)
                    if row is not None and row['type'] == 'subagent' and row['parent_session_id'] is not None
                    and row['parent_session_id'] not in walked}
    return metadata


def _native_root(session_id: str, metadata: Mapping[str, Mapping | None]) -> str | None:
    """Follow loaded subagent parents to a native root; missing or cyclic ancestry stays unresolved."""
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


def native_root_ids(
    session_ids: Iterable[str], *, metadata: Mapping[str, Mapping | None] | None = None,
) -> dict[str, str | None]:
    """Resolve each session's native root (``None`` when unresolved), reusing ``metadata`` rows."""
    requested = tuple(dict.fromkeys(session_ids))
    loaded = load_native_metadata(requested, known=metadata)
    return {session_id: _native_root(session_id, loaded) for session_id in requested}


def resolve_root_decisions(
    session_ids: Iterable[str], now: datetime, active_root_ids: frozenset[str], *,
    metadata: Mapping[str, Mapping | None] | None = None,
) -> dict[str, RootDecision]:
    """Resolve content eligibility from bounded native-root metadata batches."""
    return _resolve_root_decisions(session_ids, now, active_root_ids, cold_from_fields, metadata=metadata)


def resolve_scratch_root_decisions(
    session_ids: Iterable[str], now: datetime, active_root_ids: frozenset[str],
) -> dict[str, RootDecision]:
    """Apply the independent scratch policy with the same native active-tree protection."""
    return _resolve_root_decisions(session_ids, now, active_root_ids, scratch_cold_from_fields)


def _resolve_root_decisions(session_ids, now, active_root_ids, predicate, *, metadata=None):
    """Load native ancestry by metadata batches, including active native descendants."""
    requested = tuple(set(session_ids))
    metadata = load_native_metadata(set(requested) | set(active_root_ids), known=metadata)
    active = {_native_root(session_id, metadata) for session_id in active_root_ids}
    decisions = {}
    for session_id in requested:
        root_id = _native_root(session_id, metadata)
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


def ensure_known_storage_incarnation(session_id: str, known: str | None) -> str:
    """Initialize legacy identity once, reusing ``known`` (read in this transaction) instead of a re-read.

    The conditional ``UPDATE`` always runs: it stays the write that reserves the writer lock.
    """
    _require_writer_transaction()
    incarnation = str(uuid4())
    if Session.objects.filter(pk=session_id, storage_incarnation__isnull=True).update(storage_incarnation=incarnation):
        return incarnation
    if known:
        return known
    return Session.objects.values_list('storage_incarnation', flat=True).get(pk=session_id)


def invalidate_roots(
    root_ids: Iterable[str], *, promotion_targets: Iterable[str] = (),
    metadata: Mapping[str, Mapping | None] | None = None,
) -> None:
    """Fence native-root mutations separately from the sessions that require plaintext promotion."""
    _require_writer_transaction()
    roots = tuple(set(root_ids))
    for offset in range(0, len(roots), TREE_PAGE_SIZE):
        Session.objects.filter(pk__in=roots[offset:offset + TREE_PAGE_SIZE]).update(
            content_state_epoch=Coalesce(F('content_state_epoch'), 0) + 1,
        )
    targets = tuple(set(promotion_targets))
    if not targets:
        return
    from twicc.core.content_storage.lifecycle import active_session_ids, request_root_promotion
    from twicc.providers.write_coordinator import write_priority

    priority = write_priority.get()
    decisions = resolve_content_decisions(targets, timezone.now(), active_session_ids.get(), metadata=metadata)
    for session_id, decision in decisions.items():
        if decision.resolved and not decision.is_cold:
            transaction.on_commit(lambda session_id=session_id, priority=priority:
                                  request_root_promotion(session_id, priority=priority))


def capture_plaintext_certificate(
    root_id: str, *, metadata: Mapping[str, Mapping | None] | None = None, storage_fields: Mapping | None = None,
) -> RootStorageSnapshot | None:
    """Capture a current certificate only while persisted policy still requires plaintext.

    Wall-clock aging can permit compression without changing the epoch. Runtime
    activity cannot turn that old certificate into proof of current plaintext.
    ``metadata`` rows feed the root decision. ``storage_fields`` holds the root's
    storage-state columns, read in this transaction with no write to the root row since.
    """
    _require_writer_transaction()
    decision = _resolve_root_decisions(
        [root_id], timezone.now(), frozenset(), cold_from_fields, metadata=metadata,
    )[root_id]
    if not decision.resolved or decision.root_id != root_id or decision.is_cold:
        return None
    fields = storage_fields
    if fields is None:
        fields = Session.objects.filter(pk=root_id).values(*STORAGE_STATE_FIELDS).first()
    if (fields is None or not fields['storage_incarnation']
            or fields['content_storage_version'] != CONTENT_STORAGE_VERSION
            or fields['content_state_epoch'] is None
            or fields['plaintext_verified_epoch'] != fields['content_state_epoch']):
        return None
    return RootStorageSnapshot(root_id, fields['storage_incarnation'], fields['content_state_epoch'])


def carry_plaintext_certificate(
    snapshot: RootStorageSnapshot, *, metadata: Mapping[str, Mapping | None] | None = None,
) -> bool:
    """Carry caller-owned plaintext preservation evidence to this transaction's final epoch.

    The caller proves every changed root reference is plaintext, or that only
    descendants/lifecycle metadata changed. Never use this to finish restoration.
    ``metadata`` rows feed the root decision only: the epoch is always read again.
    """
    _require_writer_transaction()
    decision = _resolve_root_decisions(
        [snapshot.root_id], timezone.now(), frozenset(), cold_from_fields, metadata=metadata,
    )[snapshot.root_id]
    if not decision.resolved or decision.root_id != snapshot.root_id or decision.is_cold:
        return False
    epoch = Session.objects.values_list('content_state_epoch', flat=True).get(pk=snapshot.root_id)
    return bool(Session.objects.filter(
        pk=snapshot.root_id, storage_incarnation=snapshot.incarnation,
        content_storage_version=CONTENT_STORAGE_VERSION, content_state_epoch=epoch,
        plaintext_verified_epoch=snapshot.epoch,
    ).update(plaintext_verified_epoch=epoch))


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


def _native_root_identity(session_id: str) -> str | None:
    """Validate native ancestry without reloading lifecycle policy metadata."""
    visited = set()
    while session_id not in visited:
        visited.add(session_id)
        fields = Session.objects.filter(pk=session_id).values('type', 'parent_session_id').first()
        if fields is None:
            return None
        parent_id = fields['parent_session_id']
        if fields['type'] == 'session' and parent_id is None:
            return session_id
        if fields['type'] != 'subagent' or parent_id is None:
            return None
        session_id = parent_id
    return None


def _snapshot_matches(snapshot: RootStorageSnapshot | SessionPromotionSnapshot) -> bool:
    fields = Session.objects.filter(pk=snapshot.root_id).values('storage_incarnation', 'content_state_epoch').first()
    if (fields is None or fields['storage_incarnation'] != snapshot.incarnation
            or fields['content_state_epoch'] != snapshot.epoch):
        return False
    if isinstance(snapshot, SessionPromotionSnapshot):
        incarnation = Session.objects.filter(pk=snapshot.session_id).values_list('storage_incarnation', flat=True).first()
        return (incarnation == snapshot.session_incarnation
                and _native_root_identity(snapshot.session_id) == snapshot.root_id)
    return _native_root_identity(snapshot.root_id) == snapshot.root_id


def session_plaintext_metadata(session_id: str, *, fields: Mapping | None = None) -> bool:
    """Check complete extraction and both plaintext slots for one session without loading content bytes.

    Callers hold one read snapshot or the writer transaction. SQL probes keep
    Python memory and query count bounded. A native parent's certificate never authorizes a child.
    ``fields`` (``content_storage_version``, ``storage_incarnation``) replaces the session row read.
    """
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Plaintext metadata requires a caller-owned transaction')
    if fields is None:
        fields = Session.objects.filter(pk=session_id).values('content_storage_version', 'storage_incarnation').first()
    if (fields is None or fields['content_storage_version'] != CONTENT_STORAGE_VERSION
            or not fields['storage_incarnation']):
        return False
    return not _session_has_invalid_storage_metadata(session_id)


def _session_has_invalid_storage_metadata(session_id: str, *, requires_plaintext: bool = True) -> bool:
    """Probe invalid rows in the caller's transaction without loading per-item metadata."""
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Storage metadata requires a caller-owned transaction')
    items = connection.ops.quote_name(SessionItem._meta.db_table)
    contents = connection.ops.quote_name(SessionItemContent._meta.db_table)
    # SQL comparisons against NULL are UNKNOWN. Test joined IDs explicitly so
    # dangling references cannot pass, including an optional original reference.
    from twicc.core.content_storage.validation import invalid_storage_predicate

    invalid = invalid_storage_predicate(requires_plaintext=requires_plaintext)
    dictionaries = connection.ops.quote_name(ContentDictionary._meta.db_table)
    dictionary_joins = ('' if requires_plaintext else
                        f'LEFT JOIN {dictionaries} current_dict ON current_dict.id = current.dictionary_id '
                        f'LEFT JOIN {dictionaries} original_dict ON original_dict.id = original.dictionary_id ')
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT 1 FROM {items} item '
            f'LEFT JOIN {contents} current ON current.id = item.current_content_id '
            f'LEFT JOIN {contents} original ON original.id = item.original_content_id '
            f'{dictionary_joins}WHERE item.session_id = %s AND ({invalid}) LIMIT 1',
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


def _finish_plaintext_root_storage(root_id: str, *, active_root_ids: frozenset[str]) -> bool:
    """Validate and certify one final compute root in the caller's guarded writer transaction.

    This path owns its metadata probe and version publication. It accepts no
    prior validation evidence. Public verification always performs its own probe.
    """
    from twicc.core.content_storage.codec import ContentIntegrityError

    _require_writer_transaction()
    if _session_has_invalid_storage_metadata(root_id):
        raise ContentIntegrityError('Hot compute storage requires plaintext through both references')
    Session.objects.filter(pk=root_id).update(content_storage_version=CONTENT_STORAGE_VERSION)
    snapshot = snapshot_root_storage(root_id)
    if not _snapshot_matches(snapshot):
        return False
    decision = resolve_root_decision(root_id, timezone.now(), active_root_ids)
    if not decision.resolved or decision.root_id != root_id or decision.is_cold:
        return False
    fields = Session.objects.filter(pk=root_id).values('content_storage_version', 'storage_incarnation').first()
    if (fields is None or fields['content_storage_version'] != CONTENT_STORAGE_VERSION
            or not fields['storage_incarnation']):
        return False
    return bool(Session.objects.filter(
        pk=root_id, storage_incarnation=snapshot.incarnation, content_state_epoch=snapshot.epoch,
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
    if resolve_content_decisions([session_id], timezone.now(), frozenset())[session_id].is_cold:
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
