"""Resolve flat tree anchors without imposing a valid-tree depth limit."""

from collections.abc import Mapping

from twicc.core.models import Session, SessionType


def resolve_flat_parent_id(
    parent_session_id: str | None,
    *,
    parent_of: Mapping[str, str | None] | None = None,
) -> str | None:
    """Return a proven root, or None for missing ancestry or a cycle.

    ``parent_of`` contains producer entries already known to be queued,
    including roots mapped to None. These rows need not exist in the DB.
    """
    current = parent_session_id
    seen = set()
    while current is not None and current not in seen:
        seen.add(current)
        if parent_of is not None and current in parent_of:
            parent = parent_of[current]
            if parent is None:
                return current
        else:
            row = Session.objects.filter(id=current).values("parent_session_id", "type").first()
            if row is None:
                return None
            parent = row["parent_session_id"]
            if parent is None:
                return current if row["type"] == SessionType.SESSION else None
        current = parent
    return None
