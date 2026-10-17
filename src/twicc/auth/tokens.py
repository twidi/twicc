"""API token store for the RPC API.

Tokens authenticate ``/rpc/`` calls (HTTP Bearer). A token is a high-entropy
random string; only its SHA-256 digest is stored. A fast digest is the right
choice here (not PBKDF2): a 256-bit random token is not brute-forceable, so
the slow KDF that protects low-entropy passwords buys nothing and would cost
N×600k iterations per request. Lookup is O(1) with a constant-time compare.

The store is a JSON file in the data dir, ``chmod 600``. The request-time
accessors are mtime-cached so a token created by the CLI (a separate process)
takes effect in the running backend without a restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, UTC
from typing import NamedTuple

import orjson

from twicc.atomic_json import locked_json_file
from twicc.paths import get_api_tokens_path

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "twicc_pat_"

# Empty on-disk store shape. ``locked_json_file`` deep-copies the default when
# the file is absent, so this module-level literal is never mutated.
_EMPTY_STORE = {"version": 1, "tokens": []}

# Coalesced last_used telemetry. A burst of authenticated /rpc/ requests would
# otherwise rewrite api-tokens.json on every call; instead the middleware only
# records the touch in memory (``note_used``) and ``start_last_used_flush_task``
# persists everything pending at most once every LAST_USED_FLUSH_INTERVAL
# seconds. ``{token_id: latest ISO-8601}``. Backend-process-local (touching is
# backend-only); touches still pending at shutdown are dropped, which is fine
# for an approximate ``last_used_at`` (at most one interval of staleness).
LAST_USED_FLUSH_INTERVAL = 10  # seconds
_pending_last_used: dict[str, str] = {}


class TokenRecord(NamedTuple):
    id: str
    name: str
    digest: str
    created_at: str
    last_used_at: str | None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _read_raw() -> dict:
    path = get_api_tokens_path()
    try:
        return orjson.loads(path.read_bytes())
    except (FileNotFoundError, ValueError, OSError):
        return {"version": 1, "tokens": []}


def load_tokens() -> list[TokenRecord]:
    """Read the store fresh from disk (used by the CLI for read-modify-write)."""
    out: list[TokenRecord] = []
    for t in _read_raw().get("tokens", []):
        try:
            out.append(
                TokenRecord(
                    id=t["id"],
                    name=t.get("name", ""),
                    digest=t["digest"],
                    created_at=t.get("created_at", ""),
                    last_used_at=t.get("last_used_at"),
                )
            )
        except (KeyError, TypeError):
            continue
    return out


# --- mtime cache for the request hot path -------------------------------

_cache: dict = {"key": object(), "records": []}


def load_tokens_cached() -> list[TokenRecord]:
    """Read the store with a (path, mtime) cache (used per request by the middleware).

    The path is part of the key: two absent stores share the ``None`` mtime, so
    a data dir change would otherwise keep serving the previous store's records.
    """
    path = get_api_tokens_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    key = (path, mtime)
    if key != _cache["key"]:
        _cache["records"] = load_tokens()
        _cache["key"] = key
    return _cache["records"]


# --- public API ----------------------------------------------------------

def create_token(name: str) -> tuple[str, TokenRecord]:
    """Mint a token; return ``(plaintext, record)``. Only the digest is stored."""
    token = generate_token()
    rec = TokenRecord(
        id="tok_" + secrets.token_hex(4),
        name=name,
        digest=digest_token(token),
        created_at=_now_iso(),
        last_used_at=None,
    )
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        txn.data.setdefault("version", 1)
        txn.data.setdefault("tokens", []).append(rec._asdict())
        txn.write()
    return token, rec


def revoke_token(token_id: str) -> bool:
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        tokens = txn.data.get("tokens", [])
        kept = [t for t in tokens if not (isinstance(t, dict) and t.get("id") == token_id)]
        if len(kept) == len(tokens):
            return False
        txn.data["tokens"] = kept
        txn.write()
        return True


def has_tokens() -> bool:
    return bool(load_tokens_cached())


def verify_token(token: str) -> TokenRecord | None:
    """Return the matching record (constant-time compare) or ``None``."""
    if not token:
        return None
    candidate = digest_token(token)
    match: TokenRecord | None = None
    for rec in load_tokens_cached():
        if hmac.compare_digest(candidate, rec.digest):
            match = rec
    return match


def note_used(token_id: str) -> None:
    """Record that *token_id* was just used — in memory only, no I/O.

    Called from the auth middleware on the event loop, so it never blocks the
    request on a file write. The actual api-tokens.json write is coalesced by
    :func:`start_last_used_flush_task`.
    """
    _pending_last_used[token_id] = _now_iso()


def _drain_pending_last_used() -> dict[str, str]:
    """Atomically take and clear the pending touches.

    Event-loop only: ``note_used`` and this run on the same (event-loop) thread,
    so the copy-then-clear never races a concurrent mutation.
    """
    snapshot = dict(_pending_last_used)
    _pending_last_used.clear()
    return snapshot


def _write_last_used(snapshot: dict[str, str]) -> int:
    """Apply *snapshot* (``token_id -> ISO``) to api-tokens.json in one locked
    write. Returns the number of records updated; a token absent from the file
    (e.g. revoked since the touch) is skipped. Blocking — run via
    :func:`asyncio.to_thread`.
    """
    with locked_json_file(get_api_tokens_path(), default=_EMPTY_STORE) as txn:
        by_id = {t["id"]: t for t in txn.data.get("tokens", [])
                 if isinstance(t, dict) and "id" in t}
        updated = 0
        for token_id, iso in snapshot.items():
            rec = by_id.get(token_id)
            if rec is not None:
                rec["last_used_at"] = iso
                updated += 1
        if updated:
            txn.write()
        return updated


async def start_last_used_flush_task(stop_event: asyncio.Event) -> None:
    """Flush pending token last_used touches every LAST_USED_FLUSH_INTERVAL s.

    Coalesces a burst of authenticated /rpc/ requests into at most one
    api-tokens.json write per interval (so at most ~one interval of staleness).
    Exits when *stop_event* is set; touches still pending at that point are
    dropped (acceptable for an approximate timestamp).
    """
    logger.info("Token last-used flush task started (every %ss)", LAST_USED_FLUSH_INTERVAL)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LAST_USED_FLUSH_INTERVAL)
        except TimeoutError:
            pass
        else:
            break  # stop_event fired — exit without another flush

        snapshot = _drain_pending_last_used()
        if not snapshot:
            continue
        try:
            updated = await asyncio.to_thread(_write_last_used, snapshot)
            if updated:
                logger.debug("Flushed last_used for %d token(s)", updated)
        except Exception:  # noqa: BLE001 — keep the loop alive across transient errors
            # Re-queue without clobbering any newer touch that arrived meanwhile.
            for token_id, iso in snapshot.items():
                _pending_last_used.setdefault(token_id, iso)
            logger.warning("Token last_used flush failed (re-queued)", exc_info=True)
