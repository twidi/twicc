"""
Pending per-session title buffer (provider-agnostic).

When the front sends a custom title together with the very first
message of a new session (a draft becoming real), the corresponding
provider session file does not exist yet — it will be created when
the provider's process starts. This module bridges the gap with a
simple in-memory keyed store:

- :func:`set_pending_title` is called by the WS handler when a new
  session is sent with a title;
- :func:`get_pending_title` is consumed by the session serializer so
  the front sees the chosen title immediately, without waiting for
  the provider to absorb it;
- :func:`pop_pending_title` is consumed by the provider's agent
  manager once the session file is ready, just before persisting
  the title to the provider's backing store.

A pending title carries the origin of the request that set it: the deferred
flush runs long after that request and records the title with it.
"""

import logging

from twicc.request_origin import RequestOrigin

logger = logging.getLogger(__name__)

# session_id -> title
_pending: dict[str, str] = {}
# session_id -> origin of the request that stored the title (design 2026-10-14, section 6.5). The flush
# runs long after that request, so it cannot read the carrier: it uses this captured origin.
_origins: dict[str, RequestOrigin] = {}


def set_pending_title(session_id: str, title: str, origin: RequestOrigin | None = None) -> None:
    """Store a pending title for a draft session (provider file not yet created).

    ``origin`` is the origin of the request that chose the title. Setting a title again replaces the
    origin too (``None`` forgets an older one).
    """
    _pending[session_id] = title
    if origin is None:
        _origins.pop(session_id, None)
    else:
        _origins[session_id] = origin
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Return the pending title for ``session_id``, or ``None`` if none pending. Does not remove it."""
    return _pending.get(session_id)


def get_pending_title_origin(session_id: str) -> RequestOrigin | None:
    """The origin captured with the pending title of ``session_id``, or ``None``. Does not remove it."""
    return _origins.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove the pending title (and its origin) for ``session_id``."""
    _origins.pop(session_id, None)
    return _pending.pop(session_id, None)


def rekey_pending_title(old_id: str, new_id: str) -> bool:
    """Move the whole pending entry (title and origin) from ``old_id`` to ``new_id``.

    Used by the Codex draft -> canonical id re-key. Returns whether there was an entry to move.
    """
    origin = _origins.pop(old_id, None)
    title = _pending.pop(old_id, None)
    if title is None:
        return False
    set_pending_title(new_id, title, origin)
    return True
