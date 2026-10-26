"""Exact content reuse and transaction-owned ownership of persisted item references.

Callers hold the database writer lease around each main-database mutation.
Each item mutation, accounting delta, and membership mutation shares one atomic
transaction. No helper writes item assignments or guesses ignored insert results.
"""

import logging
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

from twicc.core.content_storage.codec import ContentIntegrityError, encode_plain, encode_cold
from twicc.core.content_storage.state import (
    capture_plaintext_certificate, carry_plaintext_certificate, invalidate_roots, native_root_ids,
    resolve_root_decision,
)
from twicc.core.content_storage.types import ContentBinding, PreparedInternContent
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
    insertion candidate. Guarded assignment paths may seed known initial owners
    after their persisted source checks, including plain hot placement. Binding
    still validates the final assignments. The callback must recheck the snapshot/fence and all
    owners' eligibility, including active roots. A true constant is not a proof.
    Callers retain their writer lease while this guard is installed.
    """
    token = _prepared_guard.set(validate)
    try:
        yield
    finally:
        _prepared_guard.reset(token)


def prepare_intern_contents(
    texts: Sequence[str], provider: str, *, cold: bool,
) -> dict[bytes, PreparedInternContent]:
    """Retain each exact identity before optional representation preparation.

    Digest reuse trusts SHA-256 identity. Payload integrity remains enforced by
    actual reads. Known conflicting strings inside this batch fail immediately.
    """
    from twicc.core.content_storage.dictionaries import get_provider_dictionary

    identities = {}
    for text in dict.fromkeys(texts):
        plain = encode_plain(text)
        if plain.sha256 in identities and identities[plain.sha256][0] != text:
            raise ContentIntegrityError('Content digest collision within input batch')
        identities[plain.sha256] = (text, plain)
    existing = dict(SessionItemContent.objects.filter(sha256__in=identities).values_list('sha256', 'id'))
    existing = {bytes(digest): row_id for digest, row_id in existing.items()}
    dictionary = get_provider_dictionary(provider) if cold and identities.keys() - existing.keys() else None
    result = {}
    for digest, (text, plain) in identities.items():
        row_id = existing.get(digest)
        encoded, policy, no_gain = (None if row_id is not None else plain), None, False
        if row_id is None and dictionary is not None:
            try:
                encoded, no_gain = encode_cold(text, dictionary.row.id, dictionary.policy_version, plain=plain)
                policy = dictionary.policy_version
            except Exception:
                logging.getLogger(__name__).warning('Optional initial content compression failed', exc_info=True)
        result[digest] = PreparedInternContent(text, digest, plain.raw_size, row_id, encoded, policy, no_gain)
    return result


def intern_contents(
    texts: Sequence[str], provider: str, prepared: Mapping[bytes, PreparedInternContent] | None = None,
    *, initial_owners: Sequence[str] = (),
) -> dict[str, int]:
    """Publish retained exact identities; preserve the first stored representation."""
    from twicc.core.content_storage.dictionaries import get_dictionary, get_provider_dictionary

    _require_transaction()
    if prepared is None:
        prepared = prepare_intern_contents(texts, provider, cold=False)
    records = {}
    by_text = {}
    for digest, record in prepared.items():
        if not isinstance(record, PreparedInternContent):
            raise ContentIntegrityError('Invalid prepared content identity')
        if (digest != record.sha256 or len(digest) != 32 or type(record.raw_size) is not int
                or record.raw_size != len(record.text.encode('utf-8'))):
            raise ContentIntegrityError('Prepared content metadata mismatch')
        if record.text in by_text and by_text[record.text] != digest:
            raise ContentIntegrityError('Conflicting prepared content identities')
        by_text[record.text] = digest
        records[digest] = record
    if set(texts) != set(by_text):
        raise ContentIntegrityError('Prepared content differs from exact input texts')
    existing = {bytes(digest): (row_id, size) for digest, row_id, size in
                SessionItemContent.objects.filter(sha256__in=records).values_list('sha256', 'id', 'raw_size')}
    inserts = []
    for digest, record in records.items():
        if record.encoded is not None and (record.encoded.sha256 != digest
                or record.encoded.raw_size != record.raw_size):
            raise ContentIntegrityError('Prepared representation metadata mismatch')
        if digest in existing:
            if (existing[digest][1] != record.raw_size
                    or record.existing_id not in (None, existing[digest][0])):
                raise ContentIntegrityError('Stored content identity size mismatch')
            continue
        encoded = record.encoded
        if encoded is None or encoded.sha256 != digest or encoded.raw_size != record.raw_size:
            raise ContentIntegrityError('Prepared representation metadata mismatch')
        if encoded.codec == 'plain':
            if encoded.dictionary_id is not None or encoded.payload != record.text.encode('utf-8'):
                raise ContentIntegrityError('Prepared plain content mismatch')
        elif encoded.codec == 'zstd':
            if encoded.dictionary_id is None or not encoded.payload or len(encoded.payload) >= record.raw_size:
                raise ContentIntegrityError('Invalid prepared compressed content')
        else:
            raise ContentIntegrityError('Invalid prepared content codec')
        if record.policy_version is not None:
            dictionary = (get_dictionary(encoded.dictionary_id) if encoded.dictionary_id is not None else
                          get_provider_dictionary(provider))
            if (dictionary is None or dictionary.row.provider != provider
                    or dictionary.policy_version != record.policy_version
                    or record.no_gain != (encoded.codec == 'plain')):
                raise ContentIntegrityError('Prepared dictionary or policy mismatch')
        elif encoded.codec != 'plain' or record.no_gain:
            raise ContentIntegrityError('Prepared compression requires policy metadata')
        if initial_owners and _prepared_guard.get() is None:
            raise ContentIntegrityError('Initial owners require guarded assignment publication')
        if encoded.codec == 'zstd' or record.policy_version is not None:
            guard = _prepared_guard.get()
            if guard is None or not guard():
                raise ContentIntegrityError('Compressed insertion requires guarded caller eligibility proof')
        inserts.append(SessionItemContent(
            sha256=digest, payload=encoded.payload, codec=encoded.codec,
            dictionary_id=encoded.dictionary_id, raw_size=record.raw_size, dictionary_provider=provider,
            compression_attempt_version=record.policy_version, compression_no_gain=record.no_gain,
            session_ids=sorted(set(initial_owners)),
        ))
    SessionItemContent.objects.bulk_create(inserts, ignore_conflicts=True)
    rows = {bytes(digest): (row_id, size) for digest, row_id, size in
            SessionItemContent.objects.filter(sha256__in=records).values_list('sha256', 'id', 'raw_size')}
    result = {}
    for digest, record in records.items():
        row = rows.get(digest)
        if row is None or row[1] != record.raw_size:
            raise ContentIntegrityError('Stored content identity mismatch or missing insert')
        result[record.text] = row[0]
    return result


def bind_persisted_items(
    bindings: Sequence[ContentBinding], *, metadata: Mapping | None = None,
) -> None:
    """Validate persisted assignments before adding unique session ownership.

    ``metadata`` holds native rows whose ancestry and cold-policy columns are still
    current; they feed the root resolution, the captures and the carries. Their
    storage-state values are never used: every capture and carry reads them fresh.
    """
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
    owners = {binding.session_id for binding in bindings}
    if metadata is None:
        roots = set()
        for session_id in owners:
            decision = resolve_root_decision(session_id, timezone.now(), frozenset())
            if decision.resolved:
                roots.add(decision.root_id)
    else:
        # The caller's rows were read before any lifecycle write of this transaction, the only writer
        # of their ancestry and cold-policy columns. Each batch's invalidation and carry change the
        # storage state, so the captures get no storage fields and the carries re-read the epoch.
        roots = {root for root in native_root_ids(owners, metadata=metadata).values() if root is not None}
    # Ingestion owns exact-reference promotion. A binding alone only fences the graph.
    certificates = [certificate for root in roots - owners
                    if (certificate := capture_plaintext_certificate(root, metadata=metadata)) is not None]
    invalidate_roots(roots)
    for certificate in certificates:
        carry_plaintext_certificate(certificate, metadata=metadata)
    from twicc.core.content_storage.ingestion import record_ingestion_bindings

    record_ingestion_bindings(bindings)


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
