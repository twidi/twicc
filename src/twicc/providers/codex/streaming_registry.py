"""
In-memory FIFO registry that bridges the Codex agent (SDK event stream)
and the Codex watcher (JSONL → DB).

The agent pushes one ``item_id`` per completed ``agentMessage`` SDK event.
The watcher pops one ``item_id`` per canonical ``AgentMessage`` item line it
inserts into the DB and stores it on ``SessionItem.stream_uuid``. The
frontend then matches its streaming placeholder to the freshly-inserted
SessionItem by that uuid — exactly the way Claude does it, but with an
identifier we synthesize ourselves because Codex JSONL doesn't carry
one.

Why this works (the FIFO invariant): Codex CLI is a single Rust process
that emits SDK notifications and JSONL lines sequentially. Both flows
see the same agent messages in the same order. We don't need a shared
id; relative position is enough.

Lifecycle:

- One queue per ``session_id``, populated lazily on first push.
- ``pop_next`` returns ``None`` on an empty/missing queue (sync paths
  that aren't paired with a live agent — initial sync, recompute,
  session opened in another process — silently skip the enrichment).
- ``clear_session`` is called when an agent dies so we don't leak
  item_ids that no longer have a matching JSONL line coming.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque


class StreamedItemRegistry:
    """Process-wide singleton mapping ``session_id`` → FIFO of agent_message ``item_id``s.

    Thread-safe: pushes happen on the asyncio thread (the agent's stream
    consumer), pops can happen on either the asyncio thread (live
    watcher) or a worker thread (initial sync). A single ``threading.Lock``
    is enough — operations are O(1) so contention is negligible.
    """

    def __init__(self) -> None:
        self._queues: dict[str, deque[str]] = defaultdict(deque)
        self._lock = threading.Lock()

    def push(self, session_id: str, item_id: str) -> None:
        """Record one completed ``agentMessage`` item for a session."""
        with self._lock:
            self._queues[session_id].append(item_id)

    def pop_next(self, session_id: str) -> str | None:
        """Return and remove the next ``item_id`` for the session, or None.

        Returning ``None`` is the expected path for any SessionItem
        insert that doesn't have a paired live agent push (initial
        sync, recompute, agent already dead). The caller stores
        ``None`` on ``stream_uuid`` and the frontend simply doesn't
        try to retire a synthetic placeholder for that item.
        """
        with self._lock:
            queue = self._queues.get(session_id)
            if not queue:
                return None
            return queue.popleft()

    def clear_session(self, session_id: str) -> None:
        """Drop any pending entries for a session whose agent has died.

        Called from the Codex agent's ``interrupt_or_kill`` / error
        unwind so an unsent ``item/completed`` doesn't leak into the
        next agent's queue if the session is reopened.
        """
        with self._lock:
            self._queues.pop(session_id, None)


_registry = StreamedItemRegistry()


def get_streamed_item_registry() -> StreamedItemRegistry:
    """Return the process-wide registry singleton.

    Importing the singleton via a function (rather than the module
    attribute directly) lets callers stay decoupled from import order
    and makes it trivial to monkey-patch in tests if we ever add some.
    """
    return _registry
