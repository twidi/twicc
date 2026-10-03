"""The spawned-session ancestors of a session, for the live process payloads.

A session spawned by another one records its direct parent in
``Session.spawned_by``. The frontend needs the whole chain to tell whether a
live process belongs to a given session's sub-hierarchy (the Orchestration
tab's activity indicator), and it cannot rebuild it from the session rows it
holds: rows of unloaded projects, of later pages and of hidden sessions are
missing, so the chain breaks at the first gap.

``spawned_by`` is immutable once the row exists, so a chain read from rows is
cached for the life of the process. A session whose row does not exist yet
(the watcher creates it on the first JSONL line) reads its parent from the
pending session attributes, and that chain is not cached.
"""

from __future__ import annotations

# session_id -> (parent, grandparent, ..., root), only for chains read from rows.
_cache: dict[str, tuple[str, ...]] = {}


def _parent_of(session_id: str) -> tuple[str | None, bool]:
    """``(spawned_by id or None, read from a row)``."""
    from twicc.core.models import Session
    from twicc.pending_session_attributes import get_pending_session_attributes

    row = Session.objects.filter(pk=session_id).values_list("spawned_by_id").first()
    if row is not None:
        return row[0], True
    pending = get_pending_session_attributes(session_id)
    return (pending.spawned_by_id if pending else None), False


def get_spawn_ancestors(session_id: str) -> tuple[str, ...]:
    """The ids from the session's parent up to its spawn root (sync).

    Empty for a session nobody spawned. A cycle in the links (never expected)
    ends the chain instead of looping.
    """
    cached = _cache.get(session_id)
    if cached is not None:
        return cached

    chain: list[str] = []
    seen = {session_id}
    parent, from_rows = _parent_of(session_id)
    while parent is not None and parent not in seen:
        chain.append(parent)
        seen.add(parent)
        known = _cache.get(parent)
        if known is not None:
            chain.extend(ancestor for ancestor in known if ancestor not in seen)
            break
        parent, from_row = _parent_of(parent)
        from_rows = from_rows and from_row

    result = tuple(chain)
    if from_rows:
        _cache[session_id] = result
    return result


def clear_spawn_ancestors_cache() -> None:
    """Forget every cached chain (tests)."""
    _cache.clear()
