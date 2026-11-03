"""Immutable controller facts; these values never contain transcript text."""

from typing import NamedTuple


class NativeMessageRef(NamedTuple):
    kind: str  # claude_uuid, claude_message, codex_item, unresolved
    native_id: str
    turn_id: str | None = None


class CompletionCandidate(NamedTuple):
    turn_id: str
    revision: int
    deadline: float


class CompletionSnapshot(NamedTuple):
    turn_id: str
    run_id: str
    ordinal: int
    revision: int
    first_ref: NativeMessageRef | None
    final_refs: tuple[NativeMessageRef, ...]
    outcome: str | None


class CompletionInput(NamedTuple):
    """Immutable admission facts; a receipt exposes only committed acceptance."""

    turn_id: str | None
    admission_cursor: int
    status: str
