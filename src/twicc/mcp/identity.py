"""Caller identity for the MCP endpoint.

A session token is self-describing and deterministic:
``twicc_mcp_<session_id>.<sig>``, the signature being a salted HMAC of the
session id under the per-install ``SECRET_KEY`` (random, persisted in the
data dir — see :mod:`twicc.secret_key`; the salt domain-separates it from
every other SECRET_KEY use). Tokens therefore survive backend restarts (a
hybrid tmux agent outlives the backend and must keep calling ``/mcp`` with
the token baked into its launch config) and need no storage or revocation:
they only grant "act as this session on this machine", the same authority
the PID-ancestry CLI grants any local process today.

Brand-new Codex sessions are the one wrinkle: the token is minted against the
frontend draft id (the canonical id only exists once ``thread_start``
returns), so the Codex manager registers a draft→canonical alias right after
the thread starts. The alias map is process-local by design — after a backend
restart the resume path re-wires the session with a token minted against the
canonical id, and the alias is no longer needed.
"""

from __future__ import annotations

import hmac
import logging
from contextvars import ContextVar
from typing import NamedTuple

from django.utils.crypto import salted_hmac

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "twicc_mcp_"
_KEY_SALT = "twicc.mcp.identity"
_SIG_LEN = 32  # hex chars = 128 bits, ample for a local HMAC capability

_draft_aliases: dict[str, str] = {}


def _reset_for_tests() -> None:
    _draft_aliases.clear()


def _sign(session_id: str) -> str:
    mac = salted_hmac(_KEY_SALT, f"mcp:{session_id}", algorithm="sha256")
    return mac.hexdigest()[:_SIG_LEN]


def mint_session_token(session_id: str) -> str:
    return f"{TOKEN_PREFIX}{session_id}.{_sign(session_id)}"


def resolve_session_token(token: str) -> str | None:
    """Return the (alias-resolved) session id, or None if invalid."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    session_id, sep, sig = token.removeprefix(TOKEN_PREFIX).rpartition(".")
    if not sep or not session_id:
        return None
    if not hmac.compare_digest(sig, _sign(session_id)):
        return None
    return _draft_aliases.get(session_id, session_id)


def register_draft_alias(draft_id: str, canonical_id: str) -> None:
    """Map a Codex draft session id to the canonical id minted by thread_start."""
    if draft_id != canonical_id:
        _draft_aliases[draft_id] = canonical_id
        logger.info("MCP identity: draft %s aliased to %s", draft_id, canonical_id)


class ExternalCaller(NamedTuple):
    connection_id: str
    name: str


external_caller: ContextVar[ExternalCaller | None] = ContextVar("mcp_external_caller", default=None)
