"""Captured semantic candidates, independent of inference and database access."""

from datetime import datetime
from typing import NamedTuple

from twicc.agent.completion_types import CompletionCandidate, CompletionSnapshot


class SourceScope(NamedTuple):
    provider: str
    storage_incarnation: str | None
    history_epoch: int | None


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


class EndingContext(NamedTuple):
    id: str
    candidate: CompletionCandidate
    snapshot: CompletionSnapshot
    occurred_at: datetime
    scope: SourceScope
    payload: dict
