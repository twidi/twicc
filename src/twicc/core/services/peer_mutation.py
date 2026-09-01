"""Peer relationship mutations (create / verify / accept / refuse / rename / revoke).

Single source of truth for the two surfaces that mutate ``Peer``:
- the owner REST endpoints (``/api/peers/…``, human-only — no CLI/MCP surface
  for relationship management, by design), and
- the inbound instance-to-instance endpoints (``/peer/handshake/…``).

Mirrors ``share_mutation.py``: results are NamedTuples (never raise for
business-rule errors), writes run under ``run_under_db_write_lock``, broadcasts
go out AFTER the lock is released.

Activation race note (design §4.2): on requester-side rows,
``active ⇔ code_confirmed_at AND remote_accepted_at`` is split across two
writers (the inbound accept callback and the local code submission). EVERY
mutation therefore re-fetches the Peer INSIDE the write lock and evaluates
activation from the fresh fields — whichever write lands second flips the row
to ``active``. Never decide from an instance fetched before the lock.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, UTC
from typing import NamedTuple
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.services.public_origin import normalize_public_origin
from twicc.core.services.peer_tokens import mint_token, mint_verification_code, peer_base_url
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

# Failed code echoes before the code is regenerated; regenerations before the
# pending request is dropped entirely (hard guess ceiling: ≤ 15 total guesses).
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_MAX_REGENS = 3

# Junk unauthenticated requests can crowd out legitimate ones at this cap —
# acceptable: the manager UI lets the user refuse/clear pending rows at any time.
MAX_PENDING_RECEIVED = 20


class PeerError(NamedTuple):
    field: str
    code: str
    message: str


class PeerMutationResult(NamedTuple):
    success: bool
    peer_id: str | None
    errors: list[PeerError] | None


def _now() -> datetime:
    return datetime.now(tz=UTC)


_HANDSHAKE_UPDATE_FIELDS = [
    "token_ours",
    "token_theirs",
    "verification_code",
    "verification_attempts",
    "verification_regens",
    "verified_at",
    "code_confirmed_at",
    "remote_accepted_at",
    "reconnect_direction",
]


def _clear_handshake(peer) -> None:
    peer.token_ours = None
    peer.token_theirs = None
    peer.verification_code = ""
    peer.verification_attempts = 0
    peer.verification_regens = 0
    peer.verified_at = None
    peer.code_confirmed_at = None
    peer.remote_accepted_at = None
    peer.reconnect_direction = ""


def normalize_base_url(url: str) -> str:
    raw = (url or "").strip()
    try:
        if urlparse(raw).scheme.lower() not in ("http", "https"):
            return ""
    except ValueError:
        return ""
    return normalize_public_origin(raw).value or ""


def valid_base_url(url: str) -> bool:
    return bool(url) and normalize_public_origin(url).value == url


def _peers_matching_origin(peer_model, base_url: str) -> list:
    """Return all rows whose stored origin canonicalizes to ``base_url``."""
    return [
        peer
        for peer in peer_model.objects.all().order_by("created_at")
        if normalize_base_url(peer.base_url) == base_url
    ]


def own_display_name() -> str:
    """The name this instance advertises in handshakes: the ``peerDisplayName``
    synced setting, falling back to the hostname of our own ``peerBaseUrl``."""
    from twicc.synced_settings import read_synced_settings

    name = (read_synced_settings().get("peerDisplayName") or "").strip()
    if name:
        return name
    from twicc.core.services.public_origin import normalize_public_origin

    hostname = normalize_public_origin(peer_base_url()).hostname
    if not hostname:
        return "twicc"
    # ``hostname`` is the bare canonical host, so an IPv6 literal arrives
    # unbracketed. Peers display this name as an address, so bracket it.
    return f"[{hostname}]" if ":" in hostname else hostname


# ── Broadcasts ──────────────────────────────────────────────────────────────
# serialize_peer is pure attribute access (no queries), safe to call directly
# from async. ShareConsumer's broadcast whitelist silently drops every peer_*
# type — load-bearing: verification codes must never reach share viewers.

async def _broadcast(data: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {"type": "broadcast", "data": data})


async def broadcast_peer_updated(peer) -> None:
    from twicc.core.serializers import serialize_peer

    await _broadcast({"type": "peer_updated", "peer": serialize_peer(peer)})


async def broadcast_peer_removed(peer_id: str) -> None:
    await _broadcast({"type": "peer_removed", "peer_id": peer_id})


async def broadcast_peer_request_received(peer) -> None:
    from twicc.core.serializers import serialize_peer
    from twicc.external_notifications import notify_peer_request

    await _broadcast({"type": "peer_request_received", "peer": serialize_peer(peer)})
    # Fire-and-forget, after the broadcast — same event as an incoming message
    # (see `notify_peer_request`), and never allowed to affect the handshake.
    notify_peer_request(peer)


async def broadcast_peer_accepted(peer) -> None:
    from twicc.core.serializers import serialize_peer

    await _broadcast({"type": "peer_accepted", "peer": serialize_peer(peer)})


async def _emit(action: str | None, obj) -> None:
    """Dispatch one deferred broadcast decided inside a locked write."""
    if action is None:
        return
    if action == "peer_removed":
        await broadcast_peer_removed(obj)
    elif action == "peer_updated":
        await broadcast_peer_updated(obj)
    elif action == "peer_request_received":
        await broadcast_peer_request_received(obj)
    elif action == "peer_accepted":
        await broadcast_peer_accepted(obj)


# ── Owner-side mutations (REST) ─────────────────────────────────────────────

async def create_peer_and_request(*, name: str, base_url: str) -> PeerMutationResult:
    """Create a ``pending_sent`` row and POST the handshake request. On any
    outbound failure the row is deleted — the user retries the whole add."""
    from twicc.core.models import Peer, PeerState
    from twicc.peer import outbound

    own_url = peer_base_url()
    if not own_url:
        return PeerMutationResult(False, None, [PeerError(
            "base_url", "peer_host_unset", "Configure your peer address in Settings → Peers first.",
        )])
    base_url = normalize_base_url(base_url)
    if not valid_base_url(base_url):
        return PeerMutationResult(False, None, [PeerError(
            "base_url", "invalid_url", "The peer address must be an absolute http(s) URL.",
        )])
    token = mint_token()
    peer = Peer(
        name=(name or "").strip(),
        base_url=base_url,
        state=PeerState.PENDING_SENT,
        token_ours=token,
    )

    def _insert():
        matches = _peers_matching_origin(Peer, base_url)
        if len(matches) > 1:
            return None, PeerError(
                "base_url", "ambiguous_peer", "More than one peer has this address.",
            )
        if matches:
            existing = matches[0]
            if existing.state in (PeerState.BROKEN, PeerState.REVOKED):
                return None, PeerError(
                    "base_url", "reconnect_required", "Reconnect the existing peer for this address.",
                )
            return None, PeerError(
                "base_url", "duplicate", "A peer with this address already exists.",
            )
        peer.save(force_insert=True)
        return peer, None

    peer, insert_error = await run_under_db_write_lock(lambda: sync_to_async(_insert)())
    if insert_error is not None:
        return PeerMutationResult(False, None, [insert_error])

    body = {}
    detail = ""
    try:
        status, body = await outbound.post_handshake_request(
            base_url, display_name=own_display_name(), own_base_url=own_url, token=token,
        )
    except outbound.PeerOutboundError as exc:
        status, detail = None, str(exc)

    # The peer's own CROSSED request can land during the outbound window and
    # flip this row to pending_received (register_incoming_request). Every
    # decision below therefore re-fetches — acting on the pre-call instance
    # would broadcast stale state or delete the peer's just-registered request.
    if status is None or status >= 400:
        error_message = detail or outbound.response_error_message(
            body, "The remote instance rejected the Peer request.",
        )

        def _delete_if_untouched():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return False
            if fresh.state == PeerState.PENDING_SENT and fresh.token_theirs is None:
                fresh.delete()
                return False
            return True  # crossed flip landed — keep the row (their request is real)

        kept = await run_under_db_write_lock(lambda: sync_to_async(_delete_if_untouched)())
        if not kept:
            return PeerMutationResult(False, None, [PeerError(
                "base_url", "unreachable", error_message,
            )])
        # Our request failed but theirs arrived: surface the failure, the
        # incoming request lives its own life (already broadcast).
        return PeerMutationResult(False, peer.id, [PeerError(
            "base_url", "unreachable", error_message,
        )])

    fresh = await sync_to_async(lambda: Peer.objects.filter(pk=peer.pk).first())()
    if fresh is None:
        return PeerMutationResult(False, None, [PeerError("peer", "not_found", "Peer no longer exists.")])
    await broadcast_peer_updated(fresh)
    logger.info("[peer_create] id=%s base_url=%s", fresh.id, base_url)
    return PeerMutationResult(True, fresh.id, None)


async def accept_peer(peer, *, name: str) -> PeerMutationResult:
    """Accept an incoming request: mint (or, after a crossed handshake, reuse)
    ``token_ours``, call back the requester, then activate locally."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState
    from twicc.peer import outbound

    if peer.state == PeerState.ACTIVE:
        # Idempotent no-op — happens after a crossed handshake resolved from the other side.
        return PeerMutationResult(True, peer.id, None)
    reconnect_received = (
        peer.state in (PeerState.BROKEN, PeerState.REVOKED)
        and peer.reconnect_direction == PeerReconnectDirection.RECEIVED
    )
    if peer.state != PeerState.PENDING_RECEIVED and not reconnect_received:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer is not pending acceptance.")])
    if peer.verified_at is None:
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "not_verified", "The requester has not confirmed the verification code yet.",
        )])

    # Reuse an existing token — after a crossed handshake the other side already
    # knows the one we minted for our own outbound request; never re-mint. When
    # minting fresh, PERSIST it before the outbound call: if the requester
    # processes the accept but our 200 is lost, the retry must present the SAME
    # token (their now-active row compares it) — a fresh mint per attempt would
    # wedge the handshake in a permanent 409/pending_received dead end.
    own_url = peer_base_url()
    if not own_url:
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "peer_host_unset", "Configure your peer address before accepting.",
        )])

    token = peer.token_ours
    if token is None:
        minted = mint_token()

        def _persist_token():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return None
            valid = fresh.state == PeerState.PENDING_RECEIVED or (
                fresh.state in (PeerState.BROKEN, PeerState.REVOKED)
                and fresh.reconnect_direction == PeerReconnectDirection.RECEIVED
            )
            if not valid or fresh.token_theirs != peer.token_theirs:
                return None
            if fresh.token_ours:
                return fresh.token_ours
            fresh.token_ours = minted
            fresh.save(update_fields=["token_ours"])
            return minted

        token = await run_under_db_write_lock(lambda: sync_to_async(_persist_token)())
        if token is None:
            return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])

    body = {}
    detail = ""
    try:
        status, body = await outbound.post_handshake_accept(
            peer.base_url, bearer=peer.token_theirs, token=token, display_name=own_display_name(),
        )
    except outbound.PeerOutboundError as exc:
        status, detail = None, str(exc)
    if status is None or status >= 400:
        # Row stays pending_received (token kept for the retry), the user retries.
        return PeerMutationResult(False, peer.id, [PeerError(
            "base_url", "unreachable", detail or outbound.response_error_message(
                body, "The remote instance rejected the acceptance.",
            ),
        )])

    clean_name = (name or "").strip()
    now = _now()

    def _apply():
        fresh = Peer.objects.filter(pk=peer.pk).first()
        if fresh is None:
            return None
        valid = fresh.state == PeerState.PENDING_RECEIVED or (
            fresh.state in (PeerState.BROKEN, PeerState.REVOKED)
            and fresh.reconnect_direction == PeerReconnectDirection.RECEIVED
        )
        if not valid or fresh.token_theirs != peer.token_theirs or fresh.token_ours != token:
            return None
        if clean_name:
            fresh.name = clean_name
        fresh.token_ours = token
        fresh.state = PeerState.ACTIVE
        if fresh.accepted_at is None:
            fresh.accepted_at = now
        fresh.last_contact_at = now
        fresh.broken_reason = ""
        fresh.reconnect_direction = ""
        fresh.paired_local_base_url = own_url
        # Deliberately NOT clearing verification_code: handshake_verify stays
        # idempotent on active rows (held-accept recovery on the requester side).
        fresh.save(update_fields=[
            "name", "token_ours", "state", "accepted_at", "last_contact_at",
            "broken_reason", "reconnect_direction", "paired_local_base_url",
        ])
        return fresh

    fresh = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    if fresh is None:
        return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])
    await broadcast_peer_updated(fresh)
    logger.info("[peer_accept] id=%s", fresh.id)
    return PeerMutationResult(True, fresh.id, None)


async def submit_verification_code(peer, code: str) -> PeerMutationResult:
    """Requester-side: echo the out-of-band code to the peer. On success the row
    records ``code_confirmed_at`` and activates if the accept was already held."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState
    from twicc.peer import outbound

    code = (code or "").strip()
    # pending_received with a non-null token_ours = crossed row acting as
    # requester for its outbound leg.
    allowed = peer.state == PeerState.PENDING_SENT or (
        peer.state == PeerState.PENDING_RECEIVED and peer.token_ours
    ) or (
        peer.state in (PeerState.BROKEN, PeerState.REVOKED)
        and peer.reconnect_direction == PeerReconnectDirection.SENT
        and peer.token_ours
    )
    if not allowed:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer has no outbound request to verify.")])

    try:
        status, body = await outbound.post_handshake_verify(peer.base_url, bearer=peer.token_ours, code=code)
    except outbound.PeerOutboundError as exc:
        return PeerMutationResult(False, peer.id, [PeerError("code", "unreachable", str(exc))])

    if status == 200:
        now = _now()

        def _apply():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None:
                return None, False
            sent = fresh.state == PeerState.PENDING_SENT or (
                fresh.state == PeerState.PENDING_RECEIVED and fresh.token_ours
            ) or (
                fresh.state in (PeerState.BROKEN, PeerState.REVOKED)
                and fresh.reconnect_direction == PeerReconnectDirection.SENT
            )
            if not sent or fresh.token_ours != peer.token_ours:
                return None, False
            if fresh.code_confirmed_at is None:
                fresh.code_confirmed_at = now
            activated = False
            # Activation race note (module docstring): decide from the FRESH row.
            if fresh.remote_accepted_at is not None:
                fresh.state = PeerState.ACTIVE
                if fresh.accepted_at is None:
                    fresh.accepted_at = now
                fresh.last_contact_at = now
                fresh.broken_reason = ""
                fresh.reconnect_direction = ""
                fresh.paired_local_base_url = peer_base_url()
                activated = True
            fresh.save(update_fields=[
                "code_confirmed_at", "state", "accepted_at", "last_contact_at",
                "broken_reason", "reconnect_direction", "paired_local_base_url",
            ])
            return fresh, activated

        fresh, activated = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
        if fresh is None:
            return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])
        await broadcast_peer_updated(fresh)
        if activated:
            await broadcast_peer_accepted(fresh)
        return PeerMutationResult(True, fresh.id, None)

    remote_error = (body or {}).get("error") if status == 403 else None
    if remote_error == "bad_code":
        return PeerMutationResult(False, peer.id, [PeerError("code", "bad_code", "Wrong code — check with your peer.")])
    if remote_error == "too_many_attempts":
        return PeerMutationResult(False, peer.id, [PeerError(
            "code", "code_regenerated", "Too many attempts — the peer's code was regenerated, ask them for the new one.",
        )])
    if remote_error == "unknown_token":
        return PeerMutationResult(False, peer.id, [PeerError(
            "code", "relationship_gone",
            "The peer no longer has this pending request — ask them to check their side, or remove and re-add the peer.",
        )])
    return PeerMutationResult(False, peer.id, [PeerError(
        "code", "verify_failed", "Verification could not be completed — check the relationship with your peer.",
    )])


async def refuse_peer(peer) -> PeerMutationResult:
    """Refuse an initial request or clear one received reconnect attempt."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState

    reconnect_received = (
        peer.state in (PeerState.BROKEN, PeerState.REVOKED)
        and peer.reconnect_direction == PeerReconnectDirection.RECEIVED
    )
    if peer.state != PeerState.PENDING_RECEIVED and not reconnect_received:
        return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "This peer is not pending acceptance.")])
    if reconnect_received:
        def _clear():
            fresh = Peer.objects.filter(pk=peer.pk).first()
            if fresh is None or fresh.reconnect_direction != PeerReconnectDirection.RECEIVED:
                return None
            _clear_handshake(fresh)
            fresh.save(update_fields=_HANDSHAKE_UPDATE_FIELDS)
            return fresh

        fresh = await run_under_db_write_lock(lambda: sync_to_async(_clear)())
        if fresh is None:
            return PeerMutationResult(False, peer.id, [PeerError("state", "bad_state", "Reconnect changed.")])
        await broadcast_peer_updated(fresh)
        return PeerMutationResult(True, fresh.id, None)
    peer_id = peer.id
    await run_under_db_write_lock(lambda: peer.adelete())
    await broadcast_peer_removed(peer_id)
    return PeerMutationResult(True, peer_id, None)


async def rename_peer(peer, name: str) -> PeerMutationResult:
    peer.name = (name or "").strip()
    await run_under_db_write_lock(lambda: peer.asave(update_fields=["name"]))
    await broadcast_peer_updated(peer)
    return PeerMutationResult(True, peer.id, None)


async def delete_peer(peer) -> PeerMutationResult:
    """Delete an initial pending row. Established rows use ``revoke_peer``."""
    from twicc.core.models import PeerState

    if peer.state not in (PeerState.PENDING_SENT, PeerState.PENDING_RECEIVED):
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "bad_state", "Only an initial pending request can be removed.",
        )])
    peer_id = peer.id
    await run_under_db_write_lock(lambda: peer.adelete())
    await broadcast_peer_removed(peer_id)
    return PeerMutationResult(True, peer_id, None)


async def revoke_peer(peer) -> PeerMutationResult:
    """Silently revoke an established Peer while preserving its history."""
    from twicc.core.models import Peer, PeerState

    def _apply():
        fresh = Peer.objects.filter(pk=peer.pk).first()
        if fresh is None:
            return None, "not_found"
        if fresh.state not in (PeerState.ACTIVE, PeerState.BROKEN, PeerState.REVOKED):
            return fresh, "bad_state"
        fresh.state = PeerState.REVOKED
        fresh.broken_reason = ""
        _clear_handshake(fresh)
        fresh.save(update_fields=[
            "state", "broken_reason", *_HANDSHAKE_UPDATE_FIELDS,
        ])
        return fresh, None

    fresh, error = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    if error == "not_found":
        return PeerMutationResult(False, peer.id, [PeerError("peer", "not_found", "Peer no longer exists.")])
    if error == "bad_state":
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "bad_state", "Only an established peer can be revoked.",
        )])
    await broadcast_peer_updated(fresh)
    logger.info("[peer_revoke] id=%s", fresh.id)
    return PeerMutationResult(True, fresh.id, None)


async def reconnect_peer(peer) -> PeerMutationResult:
    """Start or manually retry one reconnect attempt with the same token."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState
    from twicc.peer import outbound

    own_url = peer_base_url()
    if not own_url:
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", "peer_host_unset", "Configure your peer address before reconnecting.",
        )])

    minted = mint_token()

    def _prepare():
        fresh = Peer.objects.filter(pk=peer.pk).first()
        if fresh is None:
            return None, "not_found"
        if fresh.state not in (PeerState.BROKEN, PeerState.REVOKED):
            return fresh, "bad_state"
        if fresh.reconnect_direction == PeerReconnectDirection.RECEIVED:
            return fresh, "attempt_received"
        if fresh.reconnect_direction == PeerReconnectDirection.SENT:
            if not fresh.token_ours:
                return fresh, "invalid_attempt"
            return fresh, None
        _clear_handshake(fresh)
        fresh.token_ours = minted
        fresh.reconnect_direction = PeerReconnectDirection.SENT
        fresh.save(update_fields=_HANDSHAKE_UPDATE_FIELDS)
        return fresh, None

    fresh, error = await run_under_db_write_lock(lambda: sync_to_async(_prepare)())
    if error:
        messages = {
            "not_found": "Peer no longer exists.",
            "bad_state": "Only a broken or revoked peer can reconnect.",
            "attempt_received": "This peer has an incoming reconnect request.",
            "invalid_attempt": "Cancel this reconnect attempt before trying again.",
        }
        return PeerMutationResult(False, peer.id, [PeerError("state", error, messages[error])])

    await broadcast_peer_updated(fresh)
    body = {}
    detail = ""
    try:
        status, body = await outbound.post_handshake_request(
            fresh.base_url,
            display_name=own_display_name(),
            own_base_url=own_url,
            token=fresh.token_ours,
        )
    except outbound.PeerOutboundError as exc:
        status, detail = None, str(exc)
    if status is None or status >= 400:
        return PeerMutationResult(False, fresh.id, [PeerError(
            "base_url", "unreachable", detail or outbound.response_error_message(
                body, "The remote instance rejected the reconnect request.",
            ),
        )])
    return PeerMutationResult(True, fresh.id, None)


async def cancel_reconnect(peer) -> PeerMutationResult:
    """Withdraw one sent reconnect attempt from both instances."""
    from twicc.core.models import Peer, PeerReconnectDirection
    from twicc.peer import outbound

    def _snapshot():
        fresh = Peer.objects.filter(pk=peer.pk).first()
        if fresh is None:
            return None, "not_found"
        if fresh.reconnect_direction != PeerReconnectDirection.SENT or not fresh.token_ours:
            return fresh, "bad_state"
        return fresh, None

    fresh, error = await sync_to_async(_snapshot)()
    if error:
        return PeerMutationResult(False, peer.id, [PeerError(
            "state", error, "This peer has no sent reconnect attempt.",
        )])

    token = fresh.token_ours
    body = {}
    try:
        status, body = await outbound.post_handshake_cancel(fresh.base_url, bearer=token)
    except outbound.PeerOutboundError:
        return PeerMutationResult(False, fresh.id, [PeerError(
            "base_url",
            "unreachable",
            "The remote instance could not be reached. The reconnect request was not cancelled. Try again.",
        )])
    remote_request_absent = status == 404 and body.get("error") == "unknown_request"
    remote_cancelled = 200 <= status < 300
    if not remote_cancelled and not remote_request_absent:
        return PeerMutationResult(False, fresh.id, [PeerError(
            "base_url",
            "cancel_failed",
            "The remote instance did not cancel the reconnect request. Try again.",
        )])

    def _clear_if_same():
        current = Peer.objects.filter(pk=peer.pk).first()
        if current is None:
            return None, False
        same_attempt = (
            current.reconnect_direction == PeerReconnectDirection.SENT
            and hmac.compare_digest(current.token_ours or "", token)
        )
        if not same_attempt:
            return current, False
        _clear_handshake(current)
        current.save(update_fields=_HANDSHAKE_UPDATE_FIELDS)
        return current, True

    fresh, cleared = await run_under_db_write_lock(lambda: sync_to_async(_clear_if_same)())
    if fresh is None:
        return PeerMutationResult(False, peer.id, [PeerError(
            "peer", "not_found", "Peer no longer exists.",
        )])
    if cleared:
        await broadcast_peer_updated(fresh)
    return PeerMutationResult(True, fresh.id, None)


async def invalidate_peers_for_local_origin(
    previous_base_url: str,
    current_base_url: str,
    *,
    broadcast_changes: bool = True,
) -> None:
    """Invalidate local Peer credentials after a supported address change."""
    from django.db import transaction

    from twicc.core.models import Peer, PeerBrokenReason, PeerState

    def _apply():
        with transaction.atomic():
            active = Peer.objects.filter(state=PeerState.ACTIVE)
            changed = bool(previous_base_url) and previous_base_url != current_base_url
            interrupted = previous_base_url == current_base_url and active.exclude(
                paired_local_base_url=current_base_url,
            ).exists()
            if not changed and not interrupted:
                return [], []

            removed_ids = list(Peer.objects.filter(
                state__in=[PeerState.PENDING_SENT, PeerState.PENDING_RECEIVED],
            ).values_list("id", flat=True))
            if removed_ids:
                Peer.objects.filter(id__in=removed_ids).delete()

            updated = []
            for fresh in Peer.objects.filter(
                state__in=[PeerState.ACTIVE, PeerState.BROKEN, PeerState.REVOKED],
            ):
                fields = []
                if fresh.state == PeerState.ACTIVE:
                    fresh.state = PeerState.BROKEN
                    fresh.broken_reason = (
                        PeerBrokenReason.LOCAL_ADDRESS_DISABLED
                        if not current_base_url
                        else PeerBrokenReason.LOCAL_ADDRESS_CHANGED
                    )
                    _clear_handshake(fresh)
                    fields.extend(["state", "broken_reason", *_HANDSHAKE_UPDATE_FIELDS])
                elif fresh.reconnect_direction:
                    _clear_handshake(fresh)
                    fields.extend(_HANDSHAKE_UPDATE_FIELDS)
                if fields:
                    fresh.save(update_fields=fields)
                    updated.append(fresh)
            return updated, removed_ids

    updated, removed_ids = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    if not broadcast_changes:
        return
    for peer_id in removed_ids:
        await broadcast_peer_removed(peer_id)
    for fresh in updated:
        await broadcast_peer_updated(fresh)


def mark_peer_broken(peer) -> None:
    """Sync body — async callers MUST wrap it in ``sync_to_async`` under the
    write lock like every other mutation. Caller broadcasts."""
    from twicc.core.models import PeerBrokenReason, PeerState

    peer.state = PeerState.BROKEN
    peer.broken_reason = PeerBrokenReason.REMOTE_CREDENTIAL_REJECTED
    peer.save(update_fields=["state", "broken_reason"])


# ── Inbound-side writes (instance-to-instance endpoints) ────────────────────
# Each returns ``(http_status, response_body)`` after re-fetching inside the
# lock and emitting the decided broadcast outside it — views stay thin.

async def register_incoming_request(*, display_name: str, base_url: str, token: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/request/`` (unauthenticated)."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState

    base_url = normalize_base_url(base_url)

    def _apply():
        matches = _peers_matching_origin(Peer, base_url)
        if len(matches) > 1:
            return 409, {"error": "ambiguous_peer"}, None, None
        row = matches[0] if matches else None
        if row is not None:
            if row.state in (PeerState.BROKEN, PeerState.REVOKED):
                if row.reconnect_direction:
                    same_request = (
                        row.reconnect_direction == PeerReconnectDirection.RECEIVED
                        and hmac.compare_digest(row.token_theirs or "", token)
                    )
                    if same_request:
                        return 200, {}, None, None
                    return 409, {"error": "reconnect_in_progress"}, None, None
                _clear_handshake(row)
                row.remote_display_name = display_name
                row.token_theirs = token
                row.reconnect_direction = PeerReconnectDirection.RECEIVED
                row.verification_code = mint_verification_code()
                row.save(update_fields=["remote_display_name", *_HANDSHAKE_UPDATE_FIELDS])
                return 200, {}, "peer_request_received", row
            if row.state == PeerState.PENDING_RECEIVED:
                if row.verified_at is not None:
                    # This endpoint is unauthenticated and dedups by base_url
                    # alone — a forged re-request must not strip a completed
                    # verification or swap the bound token. No mutation.
                    return 200, {}, None, None
                row.remote_display_name = display_name
                row.token_theirs = token
                row.verification_code = mint_verification_code()
                row.verification_attempts = 0
                row.verification_regens = 0
                row.save(update_fields=[
                    "remote_display_name", "token_theirs", "verification_code",
                    "verification_attempts", "verification_regens",
                ])
                return 200, {}, "peer_updated", row
            if row.state == PeerState.PENDING_SENT:
                # Crossed handshake (both users added each other): merge into
                # this row. Keep our minted token_ours — accept_peer reuses it.
                # No exemption from the code: the user verifies + accepts
                # exactly like any incoming request (design §4.2).
                row.remote_display_name = display_name
                row.token_theirs = token
                row.state = PeerState.PENDING_RECEIVED
                row.verification_code = mint_verification_code()
                row.verification_attempts = 0
                row.verification_regens = 0
                row.save(update_fields=[
                    "remote_display_name", "token_theirs", "state", "verification_code",
                    "verification_attempts", "verification_regens",
                ])
                return 200, {}, "peer_request_received", row
            # active
            return 409, {"error": "already_related"}, None, None
        if Peer.objects.filter(state=PeerState.PENDING_RECEIVED).count() >= MAX_PENDING_RECEIVED:
            return 429, {"error": "too_many_pending"}, None, None
        peer = Peer(
            state=PeerState.PENDING_RECEIVED,
            name="",
            remote_display_name=display_name,
            base_url=base_url,
            token_theirs=token,
            token_ours=None,
            verification_code=mint_verification_code(),
        )
        peer.save(force_insert=True)
        return 201, {}, "peer_request_received", peer

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body


async def cancel_incoming_reconnect(token: str) -> tuple[int, dict]:
    """Clear the received reconnect attempt identified by ``token``."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState

    def _apply():
        peer = Peer.objects.filter(
            token_theirs=token,
            state__in=(PeerState.BROKEN, PeerState.REVOKED),
            reconnect_direction=PeerReconnectDirection.RECEIVED,
        ).first()
        if peer is None:
            return None
        _clear_handshake(peer)
        peer.save(update_fields=_HANDSHAKE_UPDATE_FIELDS)
        return peer

    peer = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    if peer is None:
        return 404, {"error": "unknown_request"}
    await broadcast_peer_updated(peer)
    return 200, {}


async def record_verification_attempt(peer_id: str, code: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/verify/`` (the requester echoes the
    out-of-band code). Constant-time compare; hard guess ceiling."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState

    def _apply():
        peer = Peer.objects.filter(pk=peer_id).first()
        reconnect_received = peer is not None and (
            peer.state in (PeerState.BROKEN, PeerState.REVOKED)
            and peer.reconnect_direction == PeerReconnectDirection.RECEIVED
        )
        if peer is None or peer.state not in (PeerState.PENDING_RECEIVED, PeerState.ACTIVE) and not reconnect_received:
            return 403, {"error": "unknown_token"}, None, None
        if peer.state == PeerState.ACTIVE:
            from twicc.core.services.peer_tokens import peer_credentials_are_active

            if not peer_credentials_are_active(peer):
                return 403, {"error": "unknown_token"}, None, None
        if hmac.compare_digest(peer.verification_code or "", code):
            if peer.state == PeerState.ACTIVE:
                # Pure no-op: idempotent across the accept transition, so a
                # requester whose earlier verify 200 was lost can recover the
                # held accept after the acceptor already went active.
                return 200, {}, None, None
            if peer.verified_at is None:
                peer.verified_at = _now()
            peer.verification_attempts = 0
            peer.save(update_fields=["verified_at", "verification_attempts"])
            return 200, {}, "peer_updated", peer
        if peer.state == PeerState.ACTIVE:
            # Never let mismatches damage an established relationship.
            return 403, {"error": "bad_code"}, None, None
        peer.verification_attempts += 1
        if peer.verification_attempts >= VERIFICATION_MAX_ATTEMPTS:
            peer.verification_regens += 1
            if peer.verification_regens >= VERIFICATION_MAX_REGENS:
                if reconnect_received:
                    _clear_handshake(peer)
                    peer.save(update_fields=_HANDSHAKE_UPDATE_FIELDS)
                    return 403, {"error": "too_many_attempts"}, "peer_updated", peer
                # "5-then-regenerate" alone is an unbounded guessing loop —
                # drop the pending request entirely (silent-refusal semantics).
                dropped_id = peer.id
                peer.delete()
                return 403, {"error": "too_many_attempts"}, "peer_removed", dropped_id
            peer.verification_code = mint_verification_code()
            peer.verification_attempts = 0
            peer.save(update_fields=["verification_code", "verification_attempts", "verification_regens"])
            # The owner sees the new code live.
            return 403, {"error": "too_many_attempts"}, "peer_updated", peer
        peer.save(update_fields=["verification_attempts"])
        return 403, {"error": "bad_code"}, None, None

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body


async def apply_handshake_accept(peer_id: str, *, token: str, display_name: str) -> tuple[int, dict]:
    """Write path of ``POST /peer/handshake/accept/`` (requester side)."""
    from twicc.core.models import Peer, PeerReconnectDirection, PeerState

    def _apply():
        peer = Peer.objects.filter(pk=peer_id).first()
        if peer is None:
            return 403, {"error": "unknown_token"}, None, None
        now = _now()
        reconnect_sent = (
            peer.state in (PeerState.BROKEN, PeerState.REVOKED)
            and peer.reconnect_direction == PeerReconnectDirection.SENT
        )
        if peer.state == PeerState.PENDING_SENT or reconnect_sent:
            peer.token_theirs = token
            peer.remote_display_name = display_name
            if peer.code_confirmed_at is not None:
                # Honest flow: the acceptor cannot accept before our code
                # submission succeeded.
                peer.state = PeerState.ACTIVE
                if peer.accepted_at is None:
                    peer.accepted_at = now
                peer.last_contact_at = now
                peer.broken_reason = ""
                peer.reconnect_direction = ""
                peer.paired_local_base_url = peer_base_url()
                peer.save(update_fields=[
                    "token_theirs", "remote_display_name", "state", "accepted_at", "last_contact_at",
                    "broken_reason", "reconnect_direction", "paired_local_base_url",
                ])
                return 200, {}, "peer_accepted", peer
            # Held accept: an accept from a stale/hijacked URL must not
            # silently open the channel (design §4.2 step 4). Activation
            # happens later inside submit_verification_code.
            peer.remote_accepted_at = now
            peer.save(update_fields=["token_theirs", "remote_display_name", "remote_accepted_at"])
            return 200, {}, "peer_updated", peer
        if peer.state == PeerState.PENDING_RECEIVED and peer.token_ours:
            # Crossed row: the data is already present; activation only ever
            # comes from the LOCAL verify + accept path on this side.
            return 200, {}, None, None
        if peer.state == PeerState.ACTIVE:
            from twicc.core.services.peer_tokens import peer_credentials_are_active

            if not peer_credentials_are_active(peer):
                return 403, {"error": "unknown_token"}, None, None
            if hmac.compare_digest(peer.token_theirs or "", token):
                return 200, {}, None, None
        return 409, {"error": "bad_state"}, None, None

    status, body, action, obj = await run_under_db_write_lock(lambda: sync_to_async(_apply)())
    await _emit(action, obj)
    return status, body
