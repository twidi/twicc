"""Who made a request and through which path (design 2026-10-14-timeline-actor-origin).

The origin of a request is held in a ``ContextVar`` set by every entry point (WebSocket frame,
REST middleware, RPC view, MCP call, drop-file watcher) and read by the services that write
timeline events. Pure module: no Django import, so every layer can use it.

Three kinds of value live in the variable:

* ``None``: unset. A request-caused write falls back to :data:`DEFAULT_ORIGIN`.
* a :class:`RequestOrigin` of a caller (``user``, ``agent``, ``external``).
* :data:`SYSTEM_ORIGIN`, the explicit system marker: an automatic step. It is *not* "unset".
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextvars import ContextVar
from typing import NamedTuple

logger = logging.getLogger(__name__)

ACTOR_USER = "user"
ACTOR_AGENT = "agent"
ACTOR_SYSTEM = "system"
ACTOR_EXTERNAL = "external"

VIA_UI = "ui"
VIA_CLI = "cli"
VIA_RPC = "rpc"
VIA_MCP_INTERNAL = "mcp_internal"
VIA_MCP_EXTERNAL = "mcp_external"
VIA_VALUES = frozenset({VIA_UI, VIA_CLI, VIA_RPC, VIA_MCP_INTERNAL, VIA_MCP_EXTERNAL})

#: Column widths of ``TimelineEvent.via`` / ``actor_ref`` / ``actor_name`` (a test pins the model to them).
VIA_MAX_LENGTH = 20
ACTOR_REF_MAX_LENGTH = 255
ACTOR_NAME_MAX_LENGTH = 80

#: Reserved top-level key of a drop-file payload carrying the CLI-side origin (decision D2).
ORIGIN_PAYLOAD_KEY = "_origin"

# Actors a drop file may claim. ``system`` is reserved for code paths: a forged one is ignored.
_CALLER_ACTORS = frozenset({ACTOR_USER, ACTOR_AGENT, ACTOR_EXTERNAL})


class RequestOrigin(NamedTuple):
    """``(actor, via, actor_ref, actor_name)`` of the request that caused an event."""

    actor: str
    via: str | None = None
    actor_ref: str | None = None
    actor_name: str | None = None


UI_ORIGIN = RequestOrigin(ACTOR_USER, VIA_UI)
#: Applied to a request-caused write that reaches the writer with no origin at all.
DEFAULT_ORIGIN = UI_ORIGIN
SYSTEM_ORIGIN = RequestOrigin(ACTOR_SYSTEM)

request_origin: ContextVar[RequestOrigin | None] = ContextVar("twicc_request_origin", default=None)


def current_origin() -> RequestOrigin | None:
    """The origin held by the carrier, ``None`` when unset."""
    return request_origin.get()


def capture_origin() -> RequestOrigin:
    """The origin to store for a request-caused fact: the carrier, else the default. Never ``None``."""
    return request_origin.get() or DEFAULT_ORIGIN


@contextlib.contextmanager
def origin_scope(origin: RequestOrigin | None) -> Iterator[None]:
    """Hold ``origin`` for the enclosed block, then restore the previous value.

    ``None`` is an explicit unset (the default then applies). Use it from a single task or thread:
    the token is reset in the context that set it.
    """
    token = request_origin.set(origin)
    try:
        yield
    finally:
        request_origin.reset(token)


def system_marker() -> contextlib.AbstractContextManager[None]:
    """Mark the enclosed block as an automatic step (design section 2, *System marker*)."""
    return origin_scope(SYSTEM_ORIGIN)


# --- Builders: one per row of the mapping table (design section 4) ---------------------------------------


def human_cli_origin() -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_CLI)


def agent_cli_origin(session_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_AGENT, VIA_CLI, session_id)


def rpc_origin(token_id: str | None) -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_RPC, token_id)


def mcp_session_origin(session_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_AGENT, VIA_MCP_INTERNAL, session_id)


def mcp_token_origin(token_id: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_USER, VIA_MCP_INTERNAL, token_id)


def mcp_external_origin(connection_id: str, name: str) -> RequestOrigin:
    return RequestOrigin(ACTOR_EXTERNAL, VIA_MCP_EXTERNAL, connection_id, name)


# --- Drop-file codec (design section 6, requirement 2) ----------------------------------------------------


def origin_to_payload(origin: RequestOrigin) -> dict:
    """The JSON-able form written under :data:`ORIGIN_PAYLOAD_KEY` of a drop payload."""
    return origin._asdict()


def origin_from_payload(raw: object) -> RequestOrigin | None:
    """Parse a drop-file origin; ``None`` (= unset, the default applies) when absent or not credible.

    The file is forgeable by any local process (design section 11): this is an audit trail, not a
    security control. Still, ``system`` is never accepted from a file and every field must be a string.
    """
    if not isinstance(raw, dict):
        return None
    actor = raw.get("actor")
    if not isinstance(actor, str) or actor not in _CALLER_ACTORS:
        return None
    fields = [raw.get(key) for key in ("via", "actor_ref", "actor_name")]
    if any(field is not None and not isinstance(field, str) for field in fields):
        return None
    return RequestOrigin(actor, *fields)


# --- Resolution used by the recording service -------------------------------------------------------------


def resolve_event_origin(actor: str | None, origin: RequestOrigin | None) -> RequestOrigin:
    """The origin an event is written with (design section 5, *Precedence*).

    1. A captured full ``origin`` wins; all four fields are kept as captured.
    2. An explicit ``actor`` (system and non-request writers) gives NULL origin fields.
    3. Neither: a request-caused write. The carrier, else the default ``user`` / ``ui``; a carrier
       holding the system marker gives ``system`` with NULL fields.

    Never raises: reading the carrier failing falls back to the default (request-caused writes only).
    """
    if origin is not None:
        return origin
    if actor is not None:
        return RequestOrigin(actor)
    try:
        return capture_origin()
    except Exception:
        logger.exception("Could not read the request origin; using the default")
        return DEFAULT_ORIGIN
