"""Explicit transaction-owned lifecycle writes and post-commit promotion wakeups."""

import logging
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.utils import timezone

from twicc.core.content_storage.policy import COLD_FIELDS
from twicc.core.content_storage.session_policy import resolve_content_decisions
from twicc.core.content_storage.state import (
    capture_plaintext_certificate, carry_plaintext_certificate, invalidate_roots, load_native_metadata,
    native_root_ids, session_plaintext_metadata,
)
from twicc.core.models import Session
from twicc.providers.write_coordinator import WritePriority, write_priority

logger = logging.getLogger(__name__)
LIFECYCLE_FIELDS = (*COLD_FIELDS, 'type', 'parent_session_id')
active_session_ids: ContextVar[frozenset[str]] = ContextVar('content_active_session_ids', default=frozenset())
_promotion_wakeup: Callable[..., None] | None = None


def set_root_promotion_wakeup(callback: Callable[..., None] | None) -> None:
    """Install Task 12's synchronous scheduler callback; pending generations remain durable."""
    global _promotion_wakeup
    _promotion_wakeup = callback


def request_root_promotion(session_id: str, *, priority: WritePriority | None = None) -> None:
    """Wake one target session after commit; the historical name also accepts native children."""
    if _promotion_wakeup is not None:
        try:
            _promotion_wakeup(session_id, priority=write_priority.get() if priority is None else priority)
        except Exception:
            logger.exception('Content promotion wakeup failed for session %s', session_id)


@contextmanager
def lifecycle_change(session_ids: Iterable[str], *, defer_plaintext_inspection: bool = False):
    """Invalidate changed lifecycle metadata and both native roots in the actual write transaction."""
    with transaction.atomic():
        ids = tuple(set(session_ids))
        before_rows = load_native_metadata(ids)
        before = {sid: before_rows[sid] for sid in ids if before_rows[sid] is not None}
        # Unresolved identities cannot publish readiness, including a former root cycle.
        old_roots = {sid: root or sid for sid, root in native_root_ids(ids, metadata=before_rows).items()}
        # Compare persisted policy. Runtime activity may precede the timestamp write.
        old_decisions = resolve_content_decisions(ids, timezone.now(), frozenset(), metadata=before_rows)
        # ``before_rows`` are still current: nothing is written between their read and these captures.
        certificates = {root: capture_plaintext_certificate(root, metadata=before_rows,
                                                            storage_fields=before_rows.get(root))
                        for root in set(old_roots.values())}
        yield
        after_rows = load_native_metadata(ids, ancestors=False)
        after = {sid: after_rows[sid] for sid in ids if after_rows[sid] is not None}
        changed = {sid for sid in ids if sid not in before or sid not in after
                   or any(before[sid][field] != after[sid][field] for field in LIFECYCLE_FIELDS)}
        if not changed:
            return
        # The new state is read after the write, with the fresh ancestors of the changed rows.
        after_rows = load_native_metadata(changed, known=after_rows)
        roots = {root or sid for sid, root in native_root_ids(changed, metadata=after_rows).items()}
        for session_id in changed:
            roots.add(old_roots[session_id])
        from twicc.core.content_storage.cold_cache import schedule_cold_invalidation

        mappings = any(before.get(identity) is None or after.get(identity) is None
                       or any(before[identity][field] != after[identity][field]
                              for field in ('type', 'parent_session_id')) for identity in changed)
        schedule_cold_invalidation(changed | roots, mappings=mappings, lifecycle_session_ids=changed)
        decisions = resolve_content_decisions(changed, timezone.now(), active_session_ids.get(), metadata=after_rows)
        from twicc.core.content_storage.ingestion import ingestion_preserves_plaintext

        # Deferred inspection retains a targeted obligation; a root proof covers only its own references.
        # ``after_rows`` are still current here: nothing has been written since their read.
        targets = {sid for sid, decision in decisions.items()
                   if decision.resolved and not decision.is_cold
                   and (old_decisions[sid].is_cold
                        or (certificates.get(sid) is None and not ingestion_preserves_plaintext(sid)
                            and (defer_plaintext_inspection
                                 or not session_plaintext_metadata(sid, fields=after_rows[sid]))))}
        invalidate_roots(roots, promotion_targets=targets, metadata=after_rows)
        # The epoch UPDATE of ``invalidate_roots`` and earlier carries write neither the ancestry nor the
        # cold-policy columns of ``after_rows``; each carry still reads the epoch it compares.
        for root in roots:
            certificate = certificates.get(root)
            if certificate is not None:
                carry_plaintext_certificate(certificate, metadata=after_rows)


def update_lifecycle(queryset, *, defer_plaintext_inspection: bool = False, **updates) -> int:
    """Apply a scoped QuerySet update, preserving its predicates and transaction boundary."""
    with transaction.atomic():
        ids = tuple(queryset.values_list('id', flat=True))
        with lifecycle_change(ids, defer_plaintext_inspection=defer_plaintext_inspection):
            return queryset.update(**updates)


def save_lifecycle(session, *, update_fields=None) -> None:
    """Save a model lifecycle mutation without changing unrelated save behavior."""
    with lifecycle_change([session.id]):
        session.save(update_fields=update_fields)


def apply_compute_lifecycle(msg: dict, mutation: Callable):
    """Account only for this guarded compute result's own lifecycle generation changes."""
    from twicc.core.content_storage.extraction import SupersededStorageResult

    pending = msg.get('_storage_final_root')
    if pending and pending['root_id'] is None:
        pending = None
    with transaction.atomic():
        if pending:
            identity = Session.objects.filter(pk=pending['root_id']).values(
                'storage_incarnation', 'content_state_epoch',
            ).first()
            if identity is None or (identity['storage_incarnation'] != pending['root_incarnation']
                                    or identity['content_state_epoch'] != pending['root_epoch']):
                raise SupersededStorageResult('superseded')
        result = mutation()
        if pending:
            pending['root_epoch'] = Session.objects.values_list('content_state_epoch', flat=True).get(
                pk=pending['root_id'],
            )
        return result
