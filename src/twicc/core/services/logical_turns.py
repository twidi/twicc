"""Writer-side logical interval journal.

Call mutations only inside the existing writer admission/locking path. Atomic
blocks protect multi-query operations; they do not replace writer admission.
Ephemeral agents must never call these persistence functions.
"""

from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max

from twicc.agent.completion_types import CompletionSnapshot, NativeMessageRef
from twicc.core.models import LogicalTurn, LogicalTurnInput, Session


def open_logical_turn(
    *, turn_id: str, session_id: str, provider: str, run_id: str,
    process_run_id: int | None, ordinal: int, source_floor: int,
    storage_incarnation: str | None, history_epoch: int | None, opened_at: datetime,
) -> None:
    """Allocate session order under writer admission, before a Session row exists if needed."""
    if ordinal < 1 or source_floor < 0:
        raise ValueError("Invalid logical turn ordinal or source floor")
    values = dict(
        session_id=session_id, provider=provider, run_id=UUID(run_id), process_run_id=process_run_id,
        ordinal=ordinal, source_floor=source_floor, storage_incarnation=storage_incarnation,
        history_epoch=history_epoch, opened_at=opened_at,
    )
    with transaction.atomic():
        existing = LogicalTurn.objects.filter(pk=turn_id).first()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise ValueError("Logical turn identity and source scope are immutable")
            return
        position = (LogicalTurn.objects.filter(session_id=session_id).aggregate(value=Max("position"))["value"] or 0) + 1
        LogicalTurn.objects.create(id=turn_id, position=position, **values)


def store_turn_snapshot(snapshot: CompletionSnapshot, *, closed_at: datetime | None) -> bool:
    """Store a strictly newer snapshot while its interval remains open."""
    if snapshot.outcome not in (None, "normal", "ended", "provider_error"):
        raise ValueError("Invalid logical turn outcome")
    if (snapshot.outcome is None) != (closed_at is None):
        raise ValueError("Terminal outcomes require a closing timestamp")
    stored = bool(LogicalTurn.objects.filter(
        pk=snapshot.turn_id, run_id=snapshot.run_id, ordinal=snapshot.ordinal,
        revision__lt=snapshot.revision, outcome__isnull=True, closed_at__isnull=True,
    ).update(
        revision=snapshot.revision,
        first_ref=snapshot.first_ref._asdict() if snapshot.first_ref is not None else None,
        final_refs=[ref._asdict() for ref in dict.fromkeys(snapshot.final_refs)],
        outcome=snapshot.outcome, closed_at=closed_at,
    ))
    if stored:
        from twicc.core.services.logical_turn_answers import bind_indexed_turn_sources

        session_id = LogicalTurn.objects.values_list('session_id', flat=True).get(pk=snapshot.turn_id)
        bind_indexed_turn_sources(session_id)
    return stored


def bind_turn_input(*, token: str, turn_id: str, admission_cursor: int, status: str) -> None:
    """Bind a token once; later delivery updates preserve its interval and cursor."""
    if status not in ("pending", "accepted", "rejected", "uncertain") or admission_cursor < 0:
        raise ValueError("Invalid input admission")
    with transaction.atomic():
        item, created = LogicalTurnInput.objects.get_or_create(
            id=token, defaults=dict(turn_id=turn_id, admission_cursor=admission_cursor, status=status),
        )
        if created:
            return
        if item.turn_id != UUID(turn_id) or item.admission_cursor != admission_cursor:
            raise ValueError("Input interval and admission cursor are immutable")
        if item.status in ("accepted", "rejected") and item.status != status:
            raise ValueError("Settled input status is immutable")
        if item.status != status:
            LogicalTurnInput.objects.filter(pk=token).update(status=status)


def input_for_token(*, session_id: str, token: str) -> LogicalTurnInput | None:
    """Return the association only in its owning session; malformed tokens never match."""
    try:
        return LogicalTurnInput.objects.filter(pk=token, turn__session_id=session_id).first()
    except (ValidationError, ValueError, TypeError):
        return None


def settle_abandoned_turns(*, at: datetime) -> int:
    """End unconfirmed intervals at boot without reclassifying closed intervals."""
    return LogicalTurn.objects.filter(outcome__isnull=True, closed_at__isnull=True).update(
        outcome="ended", closed_at=at, revision=F("revision") + 1,
    )


def bind_turn_source(
    *, turn_id: str, session_id: str, provider: str, storage_incarnation: str,
    history_epoch: int, observed_ref: NativeMessageRef,
) -> bool:
    """Bind missing scope after the caller verifies a native observation in this session.

    Journal membership alone is not source proof. The caller must read the current
    session's source and verify this exact root native identity before calling.
    This operation never changes terminal outcomes or answer identities.
    """
    if observed_ref.kind == "unresolved" or not observed_ref.native_id or not storage_incarnation:
        return False
    with transaction.atomic():
        if not Session.objects.filter(
            pk=session_id, provider=provider, storage_incarnation=storage_incarnation, history_epoch=history_epoch,
        ).exists():
            return False
        row = LogicalTurn.objects.filter(pk=turn_id, session_id=session_id, provider=provider).first()
        if row is None:
            return False
        reference = observed_ref._asdict()
        if reference != row.first_ref and reference not in row.final_refs:
            return False
        if (row.storage_incarnation is not None and row.storage_incarnation != storage_incarnation
                or row.history_epoch is not None and row.history_epoch != history_epoch):
            return False
        LogicalTurn.objects.filter(pk=turn_id).update(
            storage_incarnation=storage_incarnation, history_epoch=history_epoch,
        )
        return True
