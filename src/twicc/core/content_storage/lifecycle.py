"""Explicit transaction-owned lifecycle writes and post-commit promotion wakeups."""

import logging
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.utils import timezone

from twicc.core.content_storage.policy import COLD_FIELDS
from twicc.core.content_storage.state import (
    TREE_PAGE_SIZE, capture_plaintext_certificate, carry_plaintext_certificate,
    invalidate_roots, resolve_root_decisions, session_plaintext_metadata,
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


def _metadata(ids):
    metadata = {}
    for offset in range(0, len(ids), TREE_PAGE_SIZE):
        metadata.update({row['id']: row for row in Session.objects.filter(
            pk__in=ids[offset:offset + TREE_PAGE_SIZE],
        ).values('id', *LIFECYCLE_FIELDS)})
    return metadata


def _root_map(ids):
    decisions = resolve_root_decisions(ids, timezone.now(), active_session_ids.get())
    # Unresolved identities cannot publish readiness, including a former root cycle.
    return {session_id: decision.root_id if decision.resolved else session_id
            for session_id, decision in decisions.items()}


@contextmanager
def lifecycle_change(session_ids: Iterable[str], *, defer_plaintext_inspection: bool = False):
    """Invalidate changed lifecycle metadata and both native roots in the actual write transaction."""
    with transaction.atomic():
        ids = tuple(set(session_ids))
        before = _metadata(ids)
        old_roots = _root_map(ids)
        # Compare persisted policy. Runtime activity may precede the timestamp write.
        old_decisions = resolve_root_decisions(ids, timezone.now(), frozenset())
        certificates = {root: capture_plaintext_certificate(root) for root in set(old_roots.values())}
        yield
        after = _metadata(ids)
        changed = {sid for sid in ids if sid not in before or sid not in after
                   or any(before[sid][field] != after[sid][field] for field in LIFECYCLE_FIELDS)}
        if not changed:
            return
        roots = set(_root_map(changed).values())
        for session_id in changed:
            roots.add(old_roots[session_id])
        from twicc.core.content_storage.cold_cache import schedule_cold_invalidation

        mappings = any(before.get(identity) is None or after.get(identity) is None
                       or any(before[identity][field] != after[identity][field]
                              for field in ('type', 'parent_session_id')) for identity in changed)
        schedule_cold_invalidation(changed | roots, mappings=mappings, lifecycle_session_ids=changed)
        decisions = resolve_root_decisions(changed, timezone.now(), active_session_ids.get())
        from twicc.core.content_storage.ingestion import ingestion_preserves_plaintext

        # Deferred inspection retains a targeted obligation; a root proof covers only its own references.
        targets = {sid for sid, decision in decisions.items()
                   if decision.resolved and not decision.is_cold
                   and (old_decisions[sid].is_cold
                        or (certificates.get(sid) is None and not ingestion_preserves_plaintext(sid)
                            and (defer_plaintext_inspection or not session_plaintext_metadata(sid))))}
        invalidate_roots(roots, promotion_targets=targets)
        for root in roots:
            certificate = certificates.get(root)
            if certificate is not None:
                carry_plaintext_certificate(certificate)


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
