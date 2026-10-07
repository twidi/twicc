"""Per-session send lanes: one ordered queue per session id, shared process-wide.

Every ``send_message`` for a session runs inside ``send_lane(session_id)``, so
two sends to one session (from any connection) never overlap and keep their
arrival order. Session-scoped soft controls (interrupt, soft stop) first await
``wait_for_send_barrier`` so they run after the sends queued before them,
without holding the lane while they execute.

A lane entry is refcounted: each send or barrier takes a reference *before*
it waits for the lock and drops it when it leaves, and the entry disappears
when the count reaches zero. ``bind_send_lane`` makes a canonical id (Codex
mints its own) an alias of the draft id's live entry, so sends and barriers
addressed to either id join the same queue until the shared count reaches
zero — not merely until the draft's own send ends.

All functions run on the backend event loop; the registry mutations are
synchronous, so no extra lock guards them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class _Lane:
    """One shared lock, its reference count, and every id that maps to it."""

    __slots__ = ("keys", "lock", "references")

    def __init__(self, session_id: str) -> None:
        self.lock = asyncio.Lock()
        self.references = 0
        self.keys: set[str] = {session_id}


_lanes: dict[str, _Lane] = {}


def _acquire_reference(session_id: str) -> _Lane:
    lane = _lanes.get(session_id)
    if lane is None:
        lane = _lanes[session_id] = _Lane(session_id)
    lane.references += 1
    return lane


def _release_reference(lane: _Lane) -> None:
    lane.references -= 1
    if lane.references > 0:
        return
    for key in lane.keys:
        if _lanes.get(key) is lane:
            del _lanes[key]


@asynccontextmanager
async def send_lane(session_id: str) -> AsyncIterator[None]:
    """Hold the session's lane for the whole body, in arrival order.

    The reference is taken synchronously, before the first await, so a lane
    never disappears under a waiter. It is dropped on success, failure and
    cancellation alike (including a cancellation while still waiting).
    """
    lane = _acquire_reference(session_id)
    try:
        async with lane.lock:
            yield
    finally:
        _release_reference(lane)


async def wait_for_send_barrier(session_id: str) -> None:
    """Wait for every send queued on the session's lane, then return at once.

    The caller does not hold the lane afterwards: a control action that
    follows (a soft stop's grace window, an interrupt) never blocks later sends.
    """
    async with send_lane(session_id):
        pass


def bind_send_lane(draft_id: str, canonical_id: str) -> None:
    """Alias ``canonical_id`` to the live lane of ``draft_id``.

    Called while the draft's creation send holds its lane, before the
    canonical binding is exposed to later sends. A no-op when the ids are
    equal (Claude Code) or when the draft has no live lane (a creation
    outside any WS send). An existing distinct lane for the canonical id is
    kept: two locks cannot be merged without breaking their holders.
    """
    if draft_id == canonical_id:
        return
    lane = _lanes.get(draft_id)
    if lane is None:
        return
    existing = _lanes.get(canonical_id)
    if existing is lane:
        return
    if existing is not None:
        logger.warning(
            "send lane: %s already has its own lane; not aliasing it to draft %s",
            canonical_id, draft_id,
        )
        return
    _lanes[canonical_id] = lane
    lane.keys.add(canonical_id)


def _live_keys() -> set[str]:
    """Ids with a live lane entry (tests only)."""
    return set(_lanes)


def _reference_count(session_id: str) -> int:
    """References held on the session's lane entry (tests only)."""
    lane = _lanes.get(session_id)
    return 0 if lane is None else lane.references


def _reset_for_tests() -> None:
    _lanes.clear()
