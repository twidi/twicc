"""Captured semantic candidates, independent of inference and database access."""

from datetime import datetime
from typing import NamedTuple

from twicc.agent.completion_types import CompletionCandidate, CompletionSnapshot, NativeMessageRef


def diagnostic_ref(ref: dict | str | None) -> dict | str | None:
    """Bound captured identifiers and omit all evidence fields from diagnostics."""
    if isinstance(ref, str):
        return ref[:500]
    if isinstance(ref, dict):
        return {key: diagnostic_ref(ref.get(key)) if isinstance(ref.get(key), str) else None
                for key in ("kind", "native_id", "turn_id")}
    return None


class SourceScope(NamedTuple):
    provider: str
    storage_incarnation: str | None
    history_epoch: int | None


class ShellExitEvidence(NamedTuple):
    """An indexed process exit identity, without command or result content."""

    process_id: str
    ref: NativeMessageRef
    scope: SourceScope


class DecisionRequest(NamedTuple):
    id: str
    kind: str
    session_id: str
    scope: SourceScope
    payload: dict
    expires_at: datetime
    requested_route: str


class Classification(NamedTuple):
    verdict: str
    reason: str


class ToolObservation(NamedTuple):
    """One completed root tool in the captured root source ordering."""

    tool_id: str | None
    line: int
    command: str
    result: str


class EndingContext(NamedTuple):
    id: str
    candidate: CompletionCandidate
    snapshot: CompletionSnapshot
    occurred_at: datetime
    scope: SourceScope
    payload: dict


class EndingResolution(NamedTuple):
    """Local race winner retained until its admitted effects commit."""

    status: str
    reason: str
    classification: Classification | None = None
    effective_route: str | None = None
    resolved_at: datetime | None = None
