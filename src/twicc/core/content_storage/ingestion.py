"""Bounded initial/live inserts under the existing writer transaction."""

from collections.abc import Sequence
from itertools import islice

from django.db import connection, transaction
from django.utils import timezone

from twicc.core.content_storage.lifecycle import active_session_ids
from twicc.core.content_storage.readers import effective_current, set_current_override
from twicc.core.content_storage.references import bind_persisted_items, intern_contents
from twicc.core.content_storage.state import ensure_storage_incarnation, invalidate_roots, resolve_root_decision
from twicc.core.content_storage.types import ContentBinding
from twicc.core.models import Session, SessionItem, SessionItemContent

INSERT_BATCH_SIZE = 50


def _require_transaction() -> None:
    if not connection.in_atomic_block:
        raise transaction.TransactionManagementError('Content ingestion requires a caller-owned transaction')


def initialize_ingestion_session(session: Session, *, invalidate: bool = False) -> None:
    """Prepare touched session/root identities without claiming legacy extraction."""
    session.storage_incarnation = ensure_storage_incarnation(session.id)
    decision = resolve_root_decision(session.id, timezone.now(), active_session_ids.get())
    if decision.resolved:
        ensure_storage_incarnation(decision.root_id)
        if invalidate:
            invalidate_roots([decision.root_id], promotion_targets=[session.id])


def insert_ingested_items(
    session: Session, items: Sequence[tuple[SessionItem, str]], *, promote: bool = True,
) -> set[int]:
    """Persist source/current strings, bind actual inserts, and promote reused hot rows.

    Source strings are captured before any transform. Current overrides remain
    available for the caller's unsaved-item second pass. Ignored lines retain
    their existing references and do not authorize unused-content promotion.
    """
    _require_transaction()
    source = iter(items)
    inserted = set()
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
        ids = intern_contents(texts, session.provider)
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
        bind_persisted_items(bindings)
        inserted.update(i.line_num for i in actual)
    return inserted


def promote_ingested_items(session: Session, items: Sequence[tuple[SessionItem, str]]) -> None:
    """Place only this batch's persisted references in plaintext under final policy."""
    _require_transaction()
    decision = resolve_root_decision(session.id, timezone.now(), active_session_ids.get())
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
            # intern_contents already validates exact text against stored digest.
            row.payload = text_by_id[row.id].encode('utf-8')
            row.codec = 'plain'
            row.dictionary_id = None
        SessionItemContent.objects.bulk_update(rows, ['payload', 'codec', 'dictionary_id'])
