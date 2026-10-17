"""Content eligibility uses each session's own lifecycle and activity."""

from collections.abc import Iterable, Mapping
from datetime import datetime

from twicc.core.content_storage.policy import COLD_FIELDS, cold_from_fields
from twicc.core.content_storage.types import SessionContentDecision
from twicc.core.models import Session

TREE_PAGE_SIZE = 128


def content_decision_from_fields(
    session_id: str, fields: Mapping | None, now: datetime, active_ids: frozenset[str],
) -> SessionContentDecision:
    """Evaluate loaded session metadata without ancestry or database access."""
    active = session_id in active_ids
    resolved = fields is not None
    return SessionContentDecision(
        session_id, resolved and not active and cold_from_fields(fields, now), resolved, active,
    )


def resolve_content_decisions(
    session_ids: Iterable[str], now: datetime, active_ids: frozenset[str],
) -> dict[str, SessionContentDecision]:
    """Load only requested sessions in bounded metadata queries."""
    requested = tuple(dict.fromkeys(session_ids))
    decisions = {}
    for offset in range(0, len(requested), TREE_PAGE_SIZE):
        ids = requested[offset:offset + TREE_PAGE_SIZE]
        metadata = {row['id']: row for row in Session.objects.filter(pk__in=ids).values('id', *COLD_FIELDS)}
        decisions.update({identity: content_decision_from_fields(identity, metadata.get(identity), now, active_ids)
                          for identity in ids})
    return decisions
