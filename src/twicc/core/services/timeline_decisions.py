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
from django.db import models, transaction
from django.utils import timezone

from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
from twicc.agent.semantic_types import Classification, DecisionRequest, SourceScope, diagnostic_ref
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


def _record_after_commit(row: TimelineDecision, *, event: str, verdict: str | None = None) -> None:
    """Capture safe references now; log no database reads or private evidence."""
    shells = []
    for ref in row.payload.get("shell_refs", []):
        shells.append({
            **{key: ref[key] for key in ("key", "started_at", "generation") if key in ref},
            "owner_ref": diagnostic_ref(ref.get("owner_ref")),
        })
    record = {
        "event": event, "session_id": row.session_id, "decision_id": str(row.pk), "kind": row.kind,
        "source_scope": SourceScope(row.provider, row.storage_incarnation, row.history_epoch)._asdict(),
        "message_refs": [diagnostic_ref(ref) for ref in row.payload.get("message_refs", [])],
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


def _persist_open_ending(row: TimelineDecision, *, allow_missing_turn: bool = False) -> bool:
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
    if turn is None:
        return allow_missing_turn and not LogicalTurn.objects.filter(pk=row.turn_id).exists()
    if (turn.revision > snapshot.revision
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
    allow_missing_turn: bool = False,
) -> str:
    """Idempotently persist a frozen candidate, under caller-owned admission.

    ``allow_missing_turn`` requires the same live controller's proof that its
    previously persisted journal row expires. It bypasses only row absence.
    Recovery callers must keep the default and require an open journal row.
    """
    if request.kind not in ("message", "ending") or request.requested_route not in _ROUTES:
        raise ValueError("Invalid semantic decision kind or route")
    if not identity_key or request.scope.storage_incarnation == "":
        raise ValueError("Decision identity and source incarnation must not be empty")
    # A JSON copy freezes caller-owned dictionaries, including nested references.
    payload = orjson.loads(orjson.dumps(request.payload))
    identity = {
        "session_id": request.session_id, "provider": request.scope.provider,
        "storage_incarnation": request.scope.storage_incarnation, "history_epoch": request.scope.history_epoch,
        "kind": request.kind, "identity_key": identity_key,
    }
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
        if request.kind == "ending" and not _persist_open_ending(row, allow_missing_turn=allow_missing_turn):
            row.status, row.reason, row.resolved_at = "canceled", "stale_snapshot", timezone.now()
            row.save(update_fields=["status", "reason", "resolved_at"])
            _record_after_commit(row, event="canceled")
        _wake_after_commit()
        return str(row.pk)


def _resolve(
    row: TimelineDecision, *, status: str, reason: str, now: datetime,
    effective_route: str | None, event: str, verdict: str | None = None,
) -> bool:
    values = {
        "status": status, "reason": _reason(reason), "resolved_at": now, "effective_route": effective_route,
        "force_pending": row.kind == "ending" and status == "accepted",
    }
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
        values = {"status": "accepted", "forced": True, "reason": _reason(reason), "resolved_at": row.resolved_at or timezone.now()}
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
    from twicc.core.models import TimelineEvent, TimelineKind
    from twicc.core.services.session_timeline import record_event_sync
    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=ending_id, kind='ending', status='accepted').first()
        if row is None:
            return False
        if not row.force_pending:
            return True
        if not _ensure_decision_source(row):
            return False
        result = _fulfill_final_message(row)
        if not result.resolved:
            return False
        event = TimelineEvent.objects.filter(session_id=row.session_id,
            dedupe_key=f'turn:{row.turn_id}:{TimelineKind.TURN_ENDED}').first()
        # Legacy or intentionally absent records retain their old publication behavior.
        if event is not None and event.data.get('final_message') is not None:
            resolution = {'state': 'ready' if result.event_id is not None else 'none',
                'storage_incarnation': row.storage_incarnation, 'history_epoch': row.history_epoch}
            if result.event_id is not None:
                resolution['event_id'] = result.event_id
            if record_event_sync(TimelineKind.TURN_ENDED, session_id=row.session_id,
                    project_id=row.session.project_id, provider=row.provider, actor='agent',
                    occurred_at=row.occurred_at, dedupe_key=event.dedupe_key,
                    final_message_resolution=resolution) is None:
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


@transaction.atomic
def invalidate_semantic_decisions(*, session_id: str, deleted: bool = False) -> int:
    """Retire incompatible captured history, preserving published timeline events."""
    session = Session.objects.filter(pk=session_id).first()
    changed = 0
    for row in TimelineDecision.objects.filter(session_id=session_id).filter(
            models.Q(status__in=_UNRESOLVED) | models.Q(status='accepted', applied_at__isnull=True)
            | models.Q(force_pending=True) | models.Q(status='rejected')):
        incompatible = (deleted or session is None or session.stale or row.provider != session.provider
            or row.storage_incarnation is not None and row.storage_incarnation != session.storage_incarnation
            or row.history_epoch is not None and row.history_epoch != session.history_epoch)
        if not incompatible:
            continue
        TimelineDecision.objects.filter(pk=row.pk).update(status='invalidated', reason='source_changed',
            force_pending=False, resolved_at=timezone.now())
        row.status, row.reason = 'invalidated', 'source_changed'
        _record_after_commit(row, event='invalidated')
        changed += 1
    if changed:
        _wake_after_commit()
    return changed


def _matching_open_turn(row: TimelineDecision) -> LogicalTurn | None:
    """Recovery never uses the live controller's missing-journal exception."""
    snapshot = _snapshot(row.payload)
    return LogicalTurn.objects.filter(pk=row.turn_id, session_id=row.session_id, provider=row.provider,
        run_id=row.run_id, ordinal=snapshot.ordinal, revision=snapshot.revision,
        storage_incarnation=row.storage_incarnation, history_epoch=row.history_epoch,
        **({"first_ref": snapshot.first_ref._asdict()} if snapshot.first_ref else {"first_ref__isnull": True}),
        final_refs=[ref._asdict() for ref in dict.fromkeys(snapshot.final_refs)],
        outcome__isnull=True, closed_at__isnull=True).first()


def pending_normal_ending_turns() -> set:
    """Keep exact accepted closures out of exceptional boot settlement."""
    protected = set()
    rows = TimelineDecision.objects.filter(kind='ending', status='accepted', applied_at__isnull=True,
        turn_id__isnull=False, run_id__isnull=False).select_related('session')
    for row in rows.iterator(chunk_size=100):
        session = row.session
        if (session.stale or row.provider != session.provider
                or row.storage_incarnation is not None and row.storage_incarnation != session.storage_incarnation
                or row.history_epoch is not None and row.history_epoch != session.history_epoch):
            continue
        turn = _matching_open_turn(row)
        if turn is not None:
            protected.add(turn.pk)
    return protected


def _try_recovered_effect(function, **kwargs):
    """A failed source/publication attempt retains the committed retry obligation."""
    try:
        with transaction.atomic():
            return function(**kwargs)
    except Exception:
        logger.exception('Semantic decision recovery effect remains pending')
        return None


def apply_accepted_ending(*, decision_id: str, now: datetime) -> bool:
    """Retry historical closure effects without reviving a process or notifying."""
    from twicc.core.models import TimelineActor, TimelineEvent, TimelineKind
    from twicc.core.services.session_timeline import record_event_sync

    with transaction.atomic():
        row = TimelineDecision.objects.filter(pk=decision_id, kind='ending', status='accepted').first()
        if row is None:
            return False
        if row.applied_at is None:
            snapshot = _snapshot(row.payload)
            turn = _matching_open_turn(row)
            if turn is not None:
                if not store_turn_snapshot(snapshot._replace(revision=snapshot.revision + 1, outcome='normal'),
                                           closed_at=row.occurred_at):
                    return False
            elif not LogicalTurn.objects.filter(pk=row.turn_id, run_id=row.run_id, session_id=row.session_id,
                    revision=snapshot.revision + 1, outcome='normal', closed_at=row.occurred_at,
                    final_refs=[ref._asdict() for ref in dict.fromkeys(snapshot.final_refs)]).exists():
                return False
            key = f'turn:{row.turn_id}:{TimelineKind.TURN_ENDED}'
            event = record_event_sync(TimelineKind.TURN_ENDED, session_id=row.session_id,
                project_id=row.session.project_id, provider=row.provider, actor=TimelineActor.AGENT,
                occurred_at=row.occurred_at, dedupe_key=key,
                data={'message_refs': row.payload.get('boundary_refs', row.payload.get('message_refs', [])),
                      'input_origins': None, 'background_work': row.payload.get('background_work'),
                      'final_message': {'state': 'pending', 'storage_incarnation': row.storage_incarnation,
                                        'history_epoch': row.history_epoch}})
            if event is None and not TimelineEvent.objects.filter(session_id=row.session_id, dedupe_key=key,
                    kind=TimelineKind.TURN_ENDED, occurred_at=row.occurred_at).exists():
                raise RuntimeError('Recovered ending event was not persisted')
            TimelineDecision.objects.filter(pk=row.pk).update(applied_at=now)
        _try_recovered_effect(fulfill_final_message_obligation, ending_id=decision_id)
        return True


def recover_semantic_decisions(*, now: datetime) -> int:
    """Fail open old unresolved work before lost-process reconciliation, without inference."""
    count = 0
    cursor = None
    while True:
        query = TimelineDecision.objects.filter(status__in=_UNRESOLVED).order_by('pk')
        if cursor is not None:
            query = query.filter(pk__gt=cursor)
        rows = list(query[:100])
        if not rows:
            break
        for row in rows:
            with transaction.atomic():
                if invalidate_semantic_decisions(session_id=row.session_id):
                    row.refresh_from_db()
                    if row.status == 'invalidated':
                        count += 1
                        continue
                if row.kind == 'ending' and (row.storage_incarnation is None or row.history_epoch is None):
                    from twicc.core.services.timeline_messages import _ensure_decision_source

                    # Native indexing can bind the open journal after admission.
                    # Bind only proven captured refs before the exact boot match.
                    _ensure_decision_source(row)
                if row.kind == 'ending' and _matching_open_turn(row) is None:
                    count += cancel_decision(decision_id=str(row.pk), reason='stale_snapshot')
                    continue
                count += accept_fallback(decision_id=str(row.pk), reason='boot_recovery', now=now)
                if row.kind == 'ending':
                    _try_recovered_effect(apply_accepted_ending, decision_id=str(row.pk), now=now)
                else:
                    _try_recovered_effect(publish_message_if_ready, decision_id=str(row.pk))
        cursor = rows[-1].pk
    # Accepted commits can survive a crash before application/acknowledgment.
    cursor = None
    while True:
        query = TimelineDecision.objects.filter(status='accepted').filter(
            models.Q(applied_at__isnull=True) | models.Q(force_pending=True)).order_by('pk')
        if cursor is not None:
            query = query.filter(pk__gt=cursor)
        rows = list(query[:100])
        if not rows:
            break
        for row in rows:
            with transaction.atomic():
                invalidate_semantic_decisions(session_id=row.session_id)
                row.refresh_from_db()
                if row.status != 'accepted':
                    continue
                if row.kind == 'ending':
                    _try_recovered_effect(apply_accepted_ending, decision_id=str(row.pk), now=now)
                else:
                    _try_recovered_effect(publish_message_if_ready, decision_id=str(row.pk))
        cursor = rows[-1].pk
    return count


def _message_turn(row: TimelineDecision) -> LogicalTurn | None:
    """Associate only exact native references in the existing scoped journal."""
    turns = LogicalTurn.objects.filter(session_id=row.session_id, provider=row.provider)
    if row.turn_id is not None:
        return turns.filter(pk=row.turn_id, run_id=row.run_id).first()
    refs = row.payload.get('message_refs', [])
    if not refs:
        return None
    # The short-lived journal contains only first/final identities. It is not
    # a transcript scan. Dictionary equality ignores JSON key/spacing order.
    matches = [turn for turn in turns if (any(ref == turn.first_ref or ref in turn.final_refs for ref in refs)
        and (row.storage_incarnation is None or turn.storage_incarnation is None
            or row.storage_incarnation == turn.storage_incarnation)
        and (row.history_epoch is None or turn.history_epoch is None or row.history_epoch == turn.history_epoch))]
    if len(matches) != 1:
        return None
    turn = matches[0]
    TimelineDecision.objects.filter(pk=row.pk, turn_id__isnull=True).update(turn_id=turn.pk, run_id=turn.run_id)
    row.turn_id, row.run_id = turn.pk, turn.run_id
    return turn


def protected_semantic_turns() -> set:
    """Protect unresolved effects and every journal needed to retain a rejection."""
    protected = set()
    rows = TimelineDecision.objects.filter(models.Q(status__in=_UNRESOLVED)
        | models.Q(status='accepted', applied_at__isnull=True) | models.Q(force_pending=True)
        | models.Q(kind='message', status='rejected'))
    for row in rows.iterator(chunk_size=100):
        if row.turn_id is not None:
            protected.add(row.turn_id)
        elif row.kind == 'message':
            turn = _message_turn(row)
            if turn is not None:
                protected.add(turn.pk)
            else:
                # Uncertain association cannot authorize removal of its proof.
                candidates = LogicalTurn.objects.filter(session_id=row.session_id, provider=row.provider)
                if row.storage_incarnation is not None:
                    candidates = candidates.filter(models.Q(storage_incarnation=row.storage_incarnation)
                        | models.Q(storage_incarnation__isnull=True))
                if row.history_epoch is not None:
                    candidates = candidates.filter(models.Q(history_epoch=row.history_epoch) | models.Q(history_epoch__isnull=True))
                protected.update(candidates.values_list('pk', flat=True))
    return protected


@transaction.atomic
def purge_semantic_decisions(*, now: datetime, limit: int = 500) -> int:
    """Purge settled work after 48 hours; rejected turn age starts at closure."""
    from datetime import timedelta
    from twicc.core.models import ProcessRun
    from twicc.agent.states import AgentState

    if not 1 <= limit <= 500:
        raise ValueError('Cleanup batch size must be between 1 and 500')
    cutoff = now - timedelta(hours=48)
    ids = []
    query = TimelineDecision.objects.exclude(status__in=_UNRESOLVED).filter(force_pending=False).exclude(
        status='accepted', applied_at__isnull=True).filter(resolved_at__lte=cutoff).order_by('resolved_at', 'pk')
    for row in query.iterator(chunk_size=100):
        if row.kind == 'message' and row.status == 'rejected':
            turn = _message_turn(row)
            if turn is not None:
                if turn.closed_at is None or turn.closed_at > cutoff:
                    continue
            elif (row.turn_id is not None or LogicalTurn.objects.filter(session_id=row.session_id).exists()
                    or ProcessRun.objects.filter(session_id=row.session_id).exclude(state=AgentState.DEAD.value).exists()):
                continue
            # Unmanaged watcher messages have neither a journal nor a live
            # process. Their settled resolution retains the normal 48h window.
        ids.append(row.pk)
        if len(ids) == limit:
            break
    TimelineDecision.objects.filter(pk__in=ids).delete()
    return len(ids)
