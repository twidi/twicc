"""Exact content reuse and transaction-owned ownership of persisted item references.

Callers hold the database writer lease around each main-database mutation.
Each item mutation, accounting delta, and membership mutation shares one atomic
transaction. No helper writes item assignments or guesses ignored insert results.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import islice
from threading import get_ident
from uuid import uuid4

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from twicc.core.content_storage.codec import ContentIntegrityError, decode_content, encode_plain
from twicc.core.content_storage.state import invalidate_roots, resolve_root_decision
from twicc.core.content_storage.types import ContentBinding, EncodedContent
from twicc.core.content_storage.writes import write_content_owners
from twicc.core.models import SessionItem, SessionItemContent

COUNT_SCAN_THRESHOLD = 1_000_000
MEMBERSHIP_BATCH_SIZE = 256
_prepared_guard: ContextVar[Callable[[], bool] | None] = ContextVar('content_prepared_guard', default=None)
_reference_operation: ContextVar['SessionReferenceOperation | None'] = ContextVar('content_reference_operation', default=None)


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content references require a caller-owned transaction')


@contextmanager
def prepared_content_guard(validate: Callable[[], bool]) -> Iterator[None]:
    """Install the caller's guarded owner/root/final-date eligibility validation.

    The callback runs inside intern_contents' transaction before each compressed
    insertion candidate. It must recheck the caller's snapshot/fence and all
    owners' eligibility, including active roots. A true constant is not a proof.
    Callers retain their writer lease while this guard is installed.
    """
    token = _prepared_guard.set(validate)
    try:
        yield
    finally:
        _prepared_guard.reset(token)


def intern_contents(
    texts: Sequence[str], provider: str, prepared: Mapping[bytes, EncodedContent] | None = None,
) -> dict[str, int]:
    """Intern a bounded exact-text batch; preserve the first stored representation."""
    _require_transaction()
    candidates = {}
    for text in texts:
        plain = encode_plain(text)
        previous = candidates.get(plain.sha256)
        if previous is not None and previous[0] != text:
            raise ContentIntegrityError('Content digest collision within input batch')
        encoded = prepared.get(plain.sha256, plain) if prepared is not None else plain
        candidate = SessionItemContent(
            sha256=encoded.sha256, payload=encoded.payload, codec=encoded.codec,
            dictionary_id=encoded.dictionary_id, raw_size=encoded.raw_size, dictionary_provider=provider,
        )
        if encoded.sha256 != plain.sha256 or decode_content(candidate) != text:
            raise ContentIntegrityError('Prepared content does not match exact logical text')
        candidates[plain.sha256] = (text, candidate)
    existing = {bytes(row.sha256): row for row in SessionItemContent.objects.filter(sha256__in=candidates)}
    inserts = []
    for digest, (_, candidate) in candidates.items():
        if digest in existing:
            continue
        if candidate.codec == 'zstd':
            guard = _prepared_guard.get()
            if guard is None or not guard():
                raise ContentIntegrityError('Compressed insertion requires guarded caller eligibility proof')
        inserts.append(candidate)
    SessionItemContent.objects.bulk_create(inserts, ignore_conflicts=True)
    rows = {bytes(row.sha256): row for row in SessionItemContent.objects.filter(sha256__in=candidates)}
    result = {}
    for digest, (text, _) in candidates.items():
        row = rows.get(digest)
        if row is None or decode_content(row) != text:
            raise ContentIntegrityError('Stored content digest collision or missing insert')
        result[text] = row.id
    return result


def bind_persisted_items(bindings: Sequence[ContentBinding]) -> None:
    """Validate persisted assignments before adding unique session ownership."""
    _require_transaction()
    actual = {row[0]: row[1:] for row in SessionItem.objects.filter(pk__in=[b.item_id for b in bindings])
              .values_list('id', 'session_id', 'current_content_id', 'original_content_id')}
    additions = {}
    for binding in bindings:
        if actual.get(binding.item_id) != (binding.session_id, binding.current_id, binding.original_id):
            raise ContentIntegrityError('Content binding differs from persisted item assignment')
        for content_id in (binding.current_id, binding.original_id):
            if content_id is not None:
                additions.setdefault(content_id, set()).add(binding.session_id)
    contents = list(SessionItemContent.objects.filter(pk__in=additions).only('id', 'session_ids'))
    if len(contents) != len(additions):
        raise ContentIntegrityError('Content binding references missing content')
    changed = []
    for row in contents:
        if additions[row.id] <= set(row.session_ids):
            continue
        row.session_ids = sorted(set(row.session_ids) | additions[row.id])
        changed.append(row)
    write_content_owners(changed)
    roots = set()
    for session_id in {binding.session_id for binding in bindings}:
        decision = resolve_root_decision(session_id, timezone.now(), frozenset())
        if decision.resolved:
            roots.add(decision.root_id)
    # Any new persisted binding changes verification's graph, including hot
    # reuse of shared compressed rows. The caller promotes before hot reads.
    invalidate_roots(roots, promotion_targets={binding.session_id for binding in bindings})


def _referenced_ids(session_id: str, content_ids: Sequence[int]) -> set[int]:
    operation = _reference_operation.get()
    if operation is not None and operation.session_id == session_id and operation.uses_counts:
        operation.validate_connection()
        operation.require_initialized()
        with connection.cursor() as cursor:
            placeholders = ', '.join(['%s'] * len(content_ids))
            cursor.execute(f'SELECT content_id FROM {operation.table} WHERE count > 0 '
                           f'AND content_id IN ({placeholders})', list(content_ids))
            return {row[0] for row in cursor.fetchall()}
    retained = set()
    candidates = set(content_ids)
    for current, original in SessionItem.objects.filter(session_id=session_id).filter(
        Q(current_content_id__in=content_ids) | Q(original_content_id__in=content_ids),
    ).values_list('current_content_id', 'original_content_id').iterator(chunk_size=MEMBERSHIP_BATCH_SIZE):
        retained.update(content_id for content_id in (current, original) if content_id in candidates)
    return retained


def reconcile_session_memberships(session_id: str, displaced_ids: Sequence[int]) -> None:
    """Remove displaced membership only when neither persisted FK still owns it."""
    _require_transaction()
    ids = sorted(set(displaced_ids))
    if not ids:
        return
    retained = _referenced_ids(session_id, ids)
    rows = list(SessionItemContent.objects.filter(pk__in=ids).only('id', 'session_ids'))
    changed = []
    for row in rows:
        desired = set(row.session_ids)
        if row.id in retained:
            desired.add(session_id)
        else:
            desired.discard(session_id)
        serialized = sorted(desired)
        if serialized != row.session_ids:
            row.session_ids = serialized
            changed.append(row)
    write_content_owners(changed)


def remove_session_memberships(session_id: str, content_ids: Iterable[int]) -> None:
    """Stream exact removal within the caller's existing whole-operation transaction."""
    _require_transaction()
    source = iter(content_ids)
    while batch := list(islice(source, MEMBERSHIP_BATCH_SIZE)):
        reconcile_session_memberships(session_id, batch)


def remove_history_memberships(session_id: str) -> None:
    """Remove both FK owners before whole-history deletion in the same transaction.

    The caller must delete every item for this session before committing. Distinct
    FK IDs stream from the item table; content payloads and reverse scans are not
    needed. Content and dictionary rows remain retained.
    """
    _require_transaction()
    items = SessionItem.objects.filter(session_id=session_id).order_by()
    current = items.exclude(current_content_id=None).values_list('current_content_id', flat=True)
    original = items.exclude(original_content_id=None).values_list('original_content_id', flat=True)
    source = iter(current.union(original).order_by('current_content_id')
                  .iterator(chunk_size=MEMBERSHIP_BATCH_SIZE))
    while batch := list(islice(source, MEMBERSHIP_BATCH_SIZE)):
        rows = list(SessionItemContent.objects.filter(pk__in=batch).only('id', 'session_ids'))
        changed = []
        for row in rows:
            if session_id in row.session_ids:
                row.session_ids = sorted(set(row.session_ids) - {session_id})
                changed.append(row)
        write_content_owners(changed)


class SessionReferenceOperation:
    """Connection-local accounting across bounded preparation and publication calls.

    Call capture_mutation inside the chunk transaction around every item write.
    Supply existing IDs and insertion line numbers, including ignored conflicts.
    Reconciliation follows capture exit within the same transaction. Retrying an
    operation creates a fresh table from all previously committed item chunks.
    """

    def __init__(self, session_id: str, item_count: int, expected_batches: int):
        if item_count < 0 or expected_batches < 1:
            raise ValueError('Reference operation estimates must be nonnegative with at least one batch')
        self.session_id = session_id
        self.uses_counts = expected_batches >= 2 and item_count * expected_batches >= COUNT_SCAN_THRESHOLD
        self.table = connection.ops.quote_name(f'content_reference_counts_{uuid4().hex}')
        self._connection = None
        self._thread = None
        self._table_created = False
        self.initialized = not self.uses_counts
        self.initialization_cursor = -1
        self._closed = False
        self._invalid = False

    def validate_connection(self) -> None:
        """Reject replacement before accessing any connection-local state."""
        if self._closed or self._invalid:
            raise ContentIntegrityError('Reference operation is closed or invalid')
        if self._connection is None:
            connection.ensure_connection()
            self._connection = connection.connection
            self._thread = get_ident()
        elif self._thread != get_ident() or connection.connection is not self._connection:
            self._invalid = True
            raise ContentIntegrityError('Reference operation physical connection changed')
        if connection.needs_rollback or connection.closed_in_transaction:
            self._invalid = True
            raise ContentIntegrityError('Reference operation connection is unusable')

    def require_initialized(self) -> None:
        if not self.initialized:
            raise ContentIntegrityError('Reference counts are not initialized')

    @contextmanager
    def activate(self) -> Iterator['SessionReferenceOperation']:
        """Scope accounting to one synchronous invocation, then reset context."""
        if _reference_operation.get() is not None:
            raise ValueError('Nested reference operations are not supported')
        self.validate_connection()
        token = _reference_operation.set(self)
        try:
            yield self
        finally:
            _reference_operation.reset(token)

    def initialize_page(self, *, row_budget: int = MEMBERSHIP_BATCH_SIZE, validate: Callable[[], bool]) -> bool:
        """Build one metadata-only TEMP page; return true when complete.

        The guard checks the original observation inside this read snapshot.
        It must not acquire the main writer through a no-op UPDATE. The page
        cursor advances only after atomic exit. Any failure invalidates the
        operation; callers close it and start a fresh operation, never retry
        a page against a refreshed observation. Resumable callers enter without
        an outer transaction or open read cursor. Compatibility initialization
        may keep its caller transaction because its handle closes before exit.
        """
        if row_budget < 1:
            raise ValueError('Reference page budget must be positive')
        self.validate_connection()
        if self.initialized:
            return True
        created = self._table_created
        try:
            with transaction.atomic():
                if not validate():
                    raise ContentIntegrityError('Reference initialization observation changed')
                rows = list(SessionItem.objects.filter(
                    session_id=self.session_id, line_num__gt=self.initialization_cursor,
                ).order_by('line_num').values_list(
                    'line_num', 'current_content_id', 'original_content_id',
                )[:row_budget])
                counts = Counter(content_id for _, current, original in rows
                                 for content_id in (current, original) if content_id is not None)
                self.validate_connection()
                with connection.cursor() as cursor:
                    if not created:
                        cursor.execute(f'CREATE TEMP TABLE {self.table} '
                                       '(content_id INTEGER PRIMARY KEY, count INTEGER NOT NULL)')
                    cursor.executemany(f'INSERT INTO {self.table} (content_id, count) VALUES (%s, %s) '
                                       'ON CONFLICT(content_id) DO UPDATE SET count = count + excluded.count',
                                       list(counts.items()))
        except BaseException:
            self._invalid = True
            raise
        self._table_created = True
        if rows:
            self.initialization_cursor = rows[-1][0]
        self.initialized = len(rows) < row_budget
        return self.initialized

    def initialize(self) -> None:
        """Compatibility initialization under the caller's whole-operation lease."""
        while not self.initialize_page(validate=lambda: True):
            pass

    def close(self) -> None:
        """Drop usable TEMP state once; preserve caller-owned connections.

        A broken enclosing transaction cannot run cleanup SQL. Its rollback
        removes a table created inside it. A table from an earlier commit
        survives until explicit worker terminal connection cleanup.
        """
        if self._closed:
            return
        try:
            if self._table_created and self._thread == get_ident() and connection.connection is self._connection:
                if not connection.needs_rollback and not connection.closed_in_transaction:
                    with connection.cursor() as cursor:
                        cursor.execute(f'DROP TABLE IF EXISTS {self.table}')
        finally:
            self._closed = True

    def _assignments(self, item_ids: Sequence[int], line_nums: Sequence[int]) -> Counter:
        counts = Counter()
        rows = SessionItem.objects.filter(session_id=self.session_id).filter(
            Q(pk__in=item_ids) | Q(line_num__in=line_nums),
        ).values_list('current_content_id', 'original_content_id')
        for current, original in rows:
            counts.update(content_id for content_id in (current, original) if content_id is not None)
        return counts

    @contextmanager
    def capture_mutation(self, item_ids: Sequence[int] = (), *, line_nums: Sequence[int] = ()) -> Iterator[None]:
        _require_transaction()
        if not self.uses_counts:
            yield
            return
        self.validate_connection()
        self.require_initialized()
        before = self._assignments(item_ids, line_nums)
        yield
        self.validate_connection()
        after = self._assignments(item_ids, line_nums)
        changed_ids = []
        with connection.cursor() as cursor:
            for content_id in before.keys() | after.keys():
                delta = after[content_id] - before[content_id]
                if delta:
                    changed_ids.append(content_id)
                    cursor.execute(f'INSERT INTO {self.table} (content_id, count) VALUES (%s, %s) '
                                   'ON CONFLICT(content_id) DO UPDATE SET count = count + excluded.count',
                                   [content_id, delta])
            if changed_ids:
                placeholders = ', '.join(['%s'] * len(changed_ids))
                cursor.execute(f'SELECT 1 FROM {self.table} WHERE count < 0 '
                               f'AND content_id IN ({placeholders}) LIMIT 1', changed_ids)
                if cursor.fetchone() is not None:
                    raise ContentIntegrityError('Reference count delta does not match persisted graph')


@contextmanager
def session_reference_operation(
    session_id: str, *, item_count: int, expected_batches: int,
) -> Iterator[SessionReferenceOperation]:
    """Activate measured accounting; the caller holds its writer lease until exit."""
    operation = SessionReferenceOperation(session_id, item_count, expected_batches)
    try:
        with operation.activate():
            operation.initialize()
            yield operation
    finally:
        operation.close()
