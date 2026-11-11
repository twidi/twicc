"""Admitted decision mutations; callers own the existing writer admission.

Atomic blocks keep decision writes consistent with the caller's publication
transaction. They do not acquire writer admission. Accepted rows with no
``applied_at`` retain their effects as durable retry obligations. The runtime
registers one wakeup callback to consume those obligations after commit.

Private payload metadata uses ``run_id``, ``turn_id``, ``candidate`` and
``snapshot`` dictionaries, plus captured ``message_refs`` and ``shell_refs``.
Snapshots use CompletionSnapshot field names and native-reference dictionaries.
These metadata fields are not classifier wire fields.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import orjson
from django.db import transaction
from django.utils import timezone

from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
from twicc.agent.semantic_types import Classification, DecisionRequest, SourceScope
from twicc.core.models import LogicalTurn, Session, TimelineDecision
from twicc.core.services.logical_turns import store_turn_snapshot

logger = logging.getLogger(__name__)

_wakeup: Callable[[], None] | None = None
_UNRESOLVED = ("pending", "running")
_ROUTES = ("provider", "haiku", "luna", "openrouter")


def register_decision_wakeup(callback: Callable[[], None] | None) -> None:
    """Install the runtime's thread-safe scheduler handoff; None clears it."""
    global _wakeup
    _wakeup = callback


def _wake_after_commit() -> None:
    # Capture the registered runtime, rather than a later replacement at commit.
    if _wakeup is not None:
        transaction.on_commit(_wakeup, robust=True)


def _reason(reason: str) -> str:
    return " ".join(reason.split())[:500]


def _native_ref(ref: dict | None) -> dict | None:
    if not isinstance(ref, dict):
        return None
    return {key: ref.get(key) for key in ("kind", "native_id", "turn_id")}


def _record_after_commit(row: TimelineDecision, *, event: str, verdict: str | None = None) -> None:
    """Capture safe references now; log no database reads or private evidence."""
    shells = []
    for ref in row.payload.get("shell_refs", []):
        shells.append({
            **{key: ref[key] for key in ("key", "started_at", "generation") if key in ref},
            "owner_ref": _native_ref(ref.get("owner_ref")),
        })
    record = {
        "event": event, "session_id": row.session_id, "decision_id": str(row.pk), "kind": row.kind,
        "source_scope": SourceScope(row.provider, row.storage_incarnation, row.history_epoch)._asdict(),
        "message_refs": [_native_ref(ref) for ref in row.payload.get("message_refs", [])],
        "shell_refs": shells, "requested_route": row.requested_route, "effective_route": row.effective_route,
        "model": None, "verdict": verdict, "outcome": row.status, "reason": _reason(row.reason),
    }
    # Serialize before registering: later caller mutations cannot change the log.
    message = orjson.dumps(record).decode()
    transaction.on_commit(lambda: logger.debug("semantic_decision %s", message), robust=True)


def _snapshot(payload: dict) -> CompletionSnapshot:
    captured = payload["snapshot"]
    first_ref = captured["first_ref"]
    return CompletionSnapshot(
        captured["turn_id"], captured["run_id"], captured["ordinal"], captured["revision"],
        NativeMessageRef(**first_ref) if first_ref is not None else None,
        tuple(NativeMessageRef(**ref) for ref in captured["final_refs"]), captured["outcome"],
    )


def _persist_open_ending(row: TimelineDecision) -> bool:
    """Keep the exact captured open revision, never a newer journal snapshot."""
    snapshot = _snapshot(row.payload)
    candidate = row.payload["candidate"]
    if (snapshot.outcome is not None or snapshot.revision != candidate["revision"]
            or snapshot.turn_id != candidate["turn_id"]
            or UUID(snapshot.turn_id) != row.turn_id or UUID(snapshot.run_id) != row.run_id):
        raise ValueError("Ending metadata must describe one captured open revision")
    turn = LogicalTurn.objects.filter(
        pk=row.turn_id, run_id=row.run_id, session_id=row.session_id, provider=row.provider,
        ordinal=snapshot.ordinal, outcome__isnull=True, closed_at__isnull=True,
    ).first()
    if (turn is None or turn.revision > snapshot.revision
            or turn.storage_incarnation is not None and turn.storage_incarnation != row.storage_incarnation
            or turn.history_epoch is not None and turn.history_epoch != row.history_epoch):
        return False
    if turn.revision < snapshot.revision:
        return store_turn_snapshot(snapshot, closed_at=None)
    # A retry can observe a revision already persisted by another admitted path.
    return (turn.first_ref == (snapshot.first_ref._asdict() if snapshot.first_ref else None)
            and turn.final_refs == [ref._asdict() for ref in dict.fromkeys(snapshot.final_refs)])


def admit_decision(
    request: DecisionRequest, *, identity_key: str, occurred_at: datetime, not_before: datetime,
) -> str:
    """Idempotently persist a frozen candidate, under caller-owned admission."""
    if request.kind not in ("message", "ending") or request.requested_route not in _ROUTES:
        raise ValueError("Invalid semantic decision kind or route")
    if not identity_key or request.scope.storage_incarnation == "":
        raise ValueError("Decision identity and source incarnation must not be empty")
    # A JSON copy freezes caller-owned dictionaries, including nested references.
    payload = orjson.loads(orjson.dumps(request.payload))
    identity = dict(
        session_id=request.session_id, provider=request.scope.provider,
        storage_incarnation=request.scope.storage_incarnation, history_epoch=request.scope.history_epoch,
        kind=request.kind, identity_key=identity_key,
    )
    with transaction.atomic():
        row = TimelineDecision.objects.filter(**identity).first()
        if row is not None:
            return str(row.pk)
        row = TimelineDecision.objects.create(
            id=request.id, **identity, occurred_at=occurred_at, not_before=not_before,
            expires_at=request.expires_at, payload=payload, requested_route=request.requested_route,
            run_id=UUID(payload["run_id"]) if payload.get("run_id") else None,
            turn_id=UUID(payload["turn_id"]) if payload.get("turn_id") else None,
        )
        if request.kind == "ending" and not _persist_open_ending(row):
            row.status, row.reason, row.resolved_at = "canceled", "stale_snapshot", timezone.now()
            row.save(update_fields=["status", "reason", "resolved_at"])
            _record_after_commit(row, event="canceled")
        _wake_after_commit()
        return str(row.pk)


def _resolve(
    row: TimelineDecision, *, status: str, reason: str, now: datetime,
    effective_route: str | None, event: str, verdict: str | None = None,
) -> bool:
    values = dict(
        status=status, reason=_reason(reason), resolved_at=now, effective_route=effective_route,
        force_pending=row.kind == "ending" and status == "accepted",
    )
    changed = TimelineDecision.objects.filter(pk=row.pk, status__in=_UNRESOLVED, forced=False).update(**values)
    if not changed:
        return False
    for key, value in values.items():
        setattr(row, key, value)
    _record_after_commit(row, event=event, verdict=verdict)
    _wake_after_commit()
    return True


def resolve_decision(
    *, decision_id: str, classification: Classification, effective_route: str | None, now: datetime,
) -> bool:
    """Apply one verdict only while unresolved; deadlines win inside admission."""
    if classification.verdict not in ("YES", "NO") or effective_route not in (*_ROUTES, None):
        raise ValueError("Invalid semantic classification or effective route")
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, status__in=_UNRESOLVED, forced=False).first()
        if row is None:
            return False
        if now >= row.expires_at:
            return _resolve(row, status="accepted", reason="timeout", now=now, effective_route=None, event="fallback")
        return _resolve(
            row, status="accepted" if classification.verdict == "YES" else "rejected",
            reason=classification.reason, now=now, effective_route=effective_route,
            event="resolved", verdict=classification.verdict,
        )


def accept_fallback(*, decision_id: str, reason: str, now: datetime) -> bool:
    """Accept failure behavior without overriding rejection or cancellation."""
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, status__in=_UNRESOLVED, forced=False).first()
        return row is not None and _resolve(
            row, status="accepted", reason=reason, now=now, effective_route=None, event="fallback",
        )


def cancel_decision(*, decision_id: str, reason: str) -> bool:
    """Cancel an unresolved candidate before root work or lifecycle changes."""
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, status__in=_UNRESOLVED, forced=False).first()
        return row is not None and _resolve(
            row, status="canceled", reason=reason, now=timezone.now(), effective_route=None, event="canceled",
        )


def force_message(*, decision_id: str, reason: str) -> None:
    """Force an eligible message; canceled sources and endings never qualify."""
    with transaction.atomic():
        rows = TimelineDecision.objects.filter(
            pk=decision_id, kind="message", status__in=(*_UNRESOLVED, "accepted", "rejected"), forced=False,
        )
        row = rows.first()
        if row is None:
            return
        values = dict(status="accepted", forced=True, reason=_reason(reason), resolved_at=row.resolved_at or timezone.now())
        if not rows.update(**values):
            return
        for key, value in values.items():
            setattr(row, key, value)
        _record_after_commit(row, event="forced")
        _wake_after_commit()


def bind_decision_source(*, decision_id: str, scope: SourceScope, observed_ref: NativeMessageRef) -> bool:
    """Bind missing scope after the caller verifies this exact native source.

    Like bind_turn_source, captured membership alone is not source proof. The
    caller must observe the reference in this session's current native source.
    Select an existing proven canonical candidate before binding an unknown
    row. A collision raises ValueError without merging publication obligations.
    """
    if observed_ref.kind == "unresolved" or not observed_ref.native_id or not scope.storage_incarnation:
        return False
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, provider=scope.provider).first()
        if row is None or scope.history_epoch is None or not Session.objects.filter(
            pk=row.session_id, provider=scope.provider, storage_incarnation=scope.storage_incarnation,
            history_epoch=scope.history_epoch,
        ).exists():
            return False
        if (observed_ref._asdict() not in row.payload.get("message_refs", [])
                or row.storage_incarnation is not None and row.storage_incarnation != scope.storage_incarnation
                or row.history_epoch is not None and row.history_epoch != scope.history_epoch):
            return False
        if row.storage_incarnation is None or row.history_epoch is None:
            if TimelineDecision.objects.filter(
                session_id=row.session_id, provider=scope.provider, kind=row.kind,
                identity_key=row.identity_key, storage_incarnation=scope.storage_incarnation,
                history_epoch=scope.history_epoch,
            ).exclude(pk=row.pk).exists():
                raise ValueError("Decision source scope binding collision")
            TimelineDecision.objects.filter(pk=row.pk).update(
                storage_incarnation=scope.storage_incarnation, history_epoch=scope.history_epoch,
            )
            _wake_after_commit()
        return True


def source_is_current(*, session_id: str, scope: SourceScope) -> bool:
    """Compare current scope exactly; callers also prove captured native source."""
    return (Session.objects.filter(pk=session_id, provider=scope.provider,
                storage_incarnation=scope.storage_incarnation, history_epoch=scope.history_epoch).exists())


def publish_message_if_ready(*, decision_id: str) -> int | None:
    """Publish accepted Claude text atomically; failed inserts remain retryable."""
    from twicc.core.services.timeline_messages import _message_application_candidate, _publish_decided_message
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, kind='message').first()
        if row is None or row.status != 'accepted' and not row.payload.get('canonical_decision_id'):
            return None
        row = _message_application_candidate(row)
        if row is None or row.status != 'accepted':
            return None
        return _publish_decided_message(row)


def fulfill_final_message_obligation(*, ending_id: str) -> bool:
    """Resolve one frozen ending's exact last eligible native assistant group."""
    from twicc.core.services.timeline_messages import _ensure_decision_source, _fulfill_final_message
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=ending_id, kind='ending', status='accepted').first()
        if row is None:
            return False
        if not row.force_pending:
            return True
        if not _ensure_decision_source(row):
            return False
        if not _fulfill_final_message(row):
            return False
        TimelineDecision.objects.filter(pk=row.pk, force_pending=True).update(force_pending=False)
        _wake_after_commit()
        return True


@transaction.atomic
def _redirect_forced_message(*, duplicate: TimelineDecision, canonical: TimelineDecision) -> None:
    """Persist canonical precedence before retiring an exactly proven duplicate."""
    if duplicate.status == 'invalidated' and duplicate.payload.get('canonical_decision_id') == str(canonical.pk):
        return
    force_message(decision_id=str(canonical.pk), reason='final_message')
    payload = {**duplicate.payload, 'canonical_decision_id': str(canonical.pk)}
    TimelineDecision.objects.filter(pk=duplicate.pk).update(
        status='invalidated', reason='duplicate_canonical_message', payload=payload,
        resolved_at=timezone.now(),
    )
    duplicate.status, duplicate.reason, duplicate.payload = 'invalidated', 'duplicate_canonical_message', payload
    _record_after_commit(duplicate, event='invalidated')
    _wake_after_commit()
