"""Bounded initial/live inserts under the existing writer transaction."""

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import islice

from django.db import connection, transaction
from django.utils import timezone

from twicc.core.content_storage.lifecycle import active_session_ids
from twicc.core.content_storage.policy import COLD_FIELDS, CONTENT_STORAGE_VERSION
from twicc.core.content_storage.readers import effective_current, set_current_override
from twicc.core.content_storage.references import (
    bind_persisted_items, intern_contents, prepare_intern_contents, prepared_content_guard,
)
from twicc.core.content_storage.session_policy import content_decision_from_fields, resolve_content_decisions
from twicc.core.content_storage.state import (
    capture_plaintext_certificate, carry_plaintext_certificate, ensure_known_storage_incarnation,
    ensure_storage_incarnation, invalidate_roots, native_root_ids, resolve_root_decision,
)
from twicc.core.content_storage.types import ContentBinding, RootStorageSnapshot
from twicc.core.models import Session, SessionItem, SessionItemContent

INSERT_BATCH_SIZE = 50
_ingestion_proof: ContextVar[tuple[str, set[int | None], RootStorageSnapshot | None] | None] = ContextVar(
    'content_ingestion_proof', default=None,
)


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content ingestion requires a caller-owned transaction')


@contextmanager
def ingestion_plaintext_change(session: Session, *, metadata: dict | None = None):
    """Carry prior root proof only after the whole ingestion transaction places changed references.

    Watcher inserts bind before its lifecycle and final promotion steps. Nesting
    shares the outer proof, so no intermediate epoch becomes certified.
    ``metadata`` holds native rows read in this transaction before its first write;
    they feed the certificate capture only, never the final carry.
    """
    _require_transaction()
    proof = _ingestion_proof.get()
    if proof is not None and proof[0] == session.id:
        yield
        return
    if metadata is None:
        certificate = capture_plaintext_certificate(session.id)
    else:
        certificate = capture_plaintext_certificate(
            session.id, metadata=metadata, storage_fields=metadata.get(session.id),
        )
    required_ids = set()
    token = _ingestion_proof.set((session.id, required_ids, certificate))
    try:
        yield
        if certificate is not None:
            # Only exact newly bound references are examined; existing root rows
            # remain covered by the certificate captured before the first write.
            ids = iter(required_ids)
            complete = True
            while batch := list(islice(ids, INSERT_BATCH_SIZE * 2)):
                rows = SessionItemContent.objects.filter(pk__in=batch)
                if rows.count() != len(batch) or rows.exclude(codec='plain', dictionary_id=None).exists():
                    complete = False
                    break
            if complete:
                carry_plaintext_certificate(certificate)
            else:
                # An incomplete exact-reference promotion remains genuine demand.
                from twicc.core.content_storage.lifecycle import request_root_promotion
                from twicc.providers.write_coordinator import write_priority

                priority = write_priority.get()
                transaction.on_commit(lambda: request_root_promotion(session.id, priority=priority))
    finally:
        _ingestion_proof.reset(token)


def ingestion_preserves_plaintext(session_id: str) -> bool:
    """Identify hot lifecycle writes whose exact references the outer ingestion still owns."""
    proof = _ingestion_proof.get()
    return proof is not None and proof[0] == session_id and proof[2] is not None


def record_ingestion_bindings(bindings: Sequence[ContentBinding]) -> None:
    """Retain the exact changed root references within the active ingestion transaction."""
    proof = _ingestion_proof.get()
    if proof is not None:
        session_id, required_ids, _ = proof
        for binding in bindings:
            if binding.session_id == session_id:
                # A missing required current slot must fail final proof. The original slot is optional.
                required_ids.add(binding.current_id)
                if binding.original_id is not None:
                    required_ids.add(binding.original_id)


def initialize_ingestion_session(
    session: Session, *, invalidate: bool = False, invalidate_mappings: bool = False, metadata: dict | None = None,
) -> None:
    """Prepare touched session/root identities without claiming legacy extraction.

    ``metadata`` holds native rows read in this transaction with no write to their
    storage state since. An incarnation set once never changes, so a row holding
    one needs no write; the values written here are written through into the rows.
    """
    if invalidate_mappings:
        from twicc.core.content_storage.cold_cache import schedule_cold_invalidation

        schedule_cold_invalidation([session.id], mappings=True)
    if metadata is None:
        session.storage_incarnation = ensure_storage_incarnation(session.id)
        decision = resolve_root_decision(session.id, timezone.now(), active_session_ids.get())
        resolved, root_id = decision.resolved, decision.root_id
        if resolved and root_id != session.id:
            ensure_storage_incarnation(root_id)
    else:
        # The conditional UPDATE always runs: it stays the slice's first write (writer-lock reservation).
        row = metadata[session.id]
        session.storage_incarnation = row['storage_incarnation'] = ensure_known_storage_incarnation(
            session.id, row['storage_incarnation'],
        )
        root_id = native_root_ids([session.id], metadata=metadata)[session.id]
        resolved = root_id is not None
        root_row = metadata.get(root_id) if resolved else None
        if resolved and root_id != session.id and (root_row is None or not root_row['storage_incarnation']):
            incarnation = ensure_known_storage_incarnation(root_id, None)
            if root_row is not None:
                root_row['storage_incarnation'] = incarnation
    if resolved and invalidate:
        certificate = capture_plaintext_certificate(root_id) if root_id != session.id else None
        # New extracted sessions require no restoration. Legacy initialization
        # retains an explicit wakeup, with the epoch also supporting recovery.
        targets = [session.id] if session.content_storage_version != CONTENT_STORAGE_VERSION else ()
        invalidate_roots([root_id], promotion_targets=targets)
        if certificate is not None:
            carry_plaintext_certificate(certificate)


def prepare_ingestion_contents(session: Session, texts: Sequence[str]):
    """Prepare initial cold representations before the source writer transaction."""
    fields = Session.objects.filter(pk=session.id).values(*COLD_FIELDS).first()
    decision = content_decision_from_fields(
        session.id, fields if fields is not None else session.__dict__, timezone.now(), active_session_ids.get(),
    )
    return prepare_intern_contents(texts, session.provider, cold=True) if texts and decision.is_cold else None


def insert_ingested_items(
    session: Session, items: Sequence[tuple[SessionItem, str]], *, promote: bool = True, prepared=None,
    metadata: Mapping | None = None,
) -> set[int]:
    """Persist source/current strings, bind actual inserts, and promote reused hot rows.

    Source strings are captured before any transform. Current overrides remain
    available for the caller's unsaved-item second pass. Ignored lines retain
    their existing references and do not authorize unused-content promotion.
    ``metadata`` holds native rows whose ancestry and cold-policy columns are still
    current; they feed each batch's cold decision and bindings, never storage state.
    """
    _require_transaction()
    with ingestion_plaintext_change(session):
        source = iter(items)
        inserted = set()
        prepared_by_text = ({record.text: (digest, record) for digest, record in prepared.items()}
                            if prepared is not None else {})
        while batch := list(islice(source, INSERT_BATCH_SIZE)):
            existing = set(SessionItem.objects.filter(session_id=session.id, line_num__in=[i.line_num for i, _ in batch])
                           .values_list('line_num', flat=True))
            candidates = []
            texts = []
            seen = set(existing)
            for item, original in batch:
                if item.line_num in seen:
                    continue
                seen.add(item.line_num)
                current = effective_current(item)
                candidates.append((item, original, current))
                texts.extend((original, current))
            if not candidates:
                continue
            batch_prepared = None
            cold = resolve_content_decisions(
                [session.id], timezone.now(), active_session_ids.get(), metadata=metadata,
            )[session.id].is_cold
            if prepared is not None and cold:
                wanted = set(texts)
                batch_prepared = dict(prepared_by_text[text] for text in wanted if text in prepared_by_text)
                if {record.text for record in batch_prepared.values()} != wanted:
                    batch_prepared = None
            with prepared_content_guard(lambda cold=cold: cold):
                ids = intern_contents(texts, session.provider, batch_prepared)
            for item, original, current in candidates:
                item.original_content_id = ids[original]
                item.current_content_id = ids[current]
                item.content = ''
                set_current_override(item, current)
            SessionItem.objects.bulk_create([item for item, _, _ in candidates], ignore_conflicts=True,
                                            batch_size=INSERT_BATCH_SIZE)
            actual = list(SessionItem.objects.filter(session_id=session.id,
                                                    line_num__in=[i.line_num for i, _, _ in candidates])
                          .only('id', 'session_id', 'line_num', 'current_content_id', 'original_content_id'))
            bindings = [ContentBinding(i.id, i.session_id, i.current_content_id, i.original_content_id) for i in actual]
            if promote:
                promote_ingested_items(session, [(item, original) for item, original, _ in candidates])
            bind_persisted_items(bindings, metadata=metadata)
            inserted.update(i.line_num for i in actual)
        return inserted


def promote_ingested_items(session: Session, items: Sequence[tuple[SessionItem, str]]) -> None:
    """Place only this batch's persisted references in plaintext under final policy."""
    _require_transaction()
    decision = resolve_content_decisions([session.id], timezone.now(), active_session_ids.get())[session.id]
    if decision.is_cold:
        return
    source = iter(items)
    while batch := list(islice(source, INSERT_BATCH_SIZE)):
        text_by_id = {}
        expected = {}
        for item, original in batch:
            text_by_id[item.original_content_id] = original
            text_by_id[item.current_content_id] = effective_current(item)
            expected[item.line_num] = (item.current_content_id, item.original_content_id)
        actual_ids = set()
        for line, current, original in SessionItem.objects.filter(
            session_id=session.id, line_num__in=expected,
        ).values_list('line_num', 'current_content_id', 'original_content_id'):
            if (current, original) != expected[line]:
                # An ignored insertion cannot authorize representation changes.
                continue
            actual_ids.update(pk for pk in (current, original) if pk is not None)
        rows = list(SessionItemContent.objects.filter(pk__in=actual_ids).exclude(codec='plain')
                    .only('id', 'payload', 'codec', 'dictionary_id'))
        for row in rows:
            # intern_contents trusts digest identity; actual reads validate payload integrity.
            row.payload = text_by_id[row.id].encode('utf-8')
            row.codec = 'plain'
            row.dictionary_id = None
        SessionItemContent.objects.bulk_update(rows, ['payload', 'codec', 'dictionary_id'])
