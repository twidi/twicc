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
    session_ids: Iterable[str], now: datetime, active_ids: frozenset[str], *,
    metadata: Mapping[str, Mapping | None] | None = None,
) -> dict[str, SessionContentDecision]:
    """Load only requested sessions in bounded metadata queries.

    Ids present as keys in ``metadata`` use that row (``None`` = missing session)
    and are not read again.
    """
    requested = tuple(dict.fromkeys(session_ids))
    known = metadata or {}
    missing = tuple(identity for identity in requested if identity not in known)
    loaded = {}
    for offset in range(0, len(missing), TREE_PAGE_SIZE):
        ids = missing[offset:offset + TREE_PAGE_SIZE]
        loaded.update({row['id']: row for row in Session.objects.filter(pk__in=ids).values('id', *COLD_FIELDS)})
    return {identity: content_decision_from_fields(
        identity, known[identity] if identity in known else loaded.get(identity), now, active_ids,
    ) for identity in requested}
