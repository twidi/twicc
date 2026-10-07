"""Inbound instance-to-instance endpoints (``/peer/…``).

Exempt from the human auth gates (``auth/middleware.PUBLIC_PATHS``) — each view
carries its own Bearer auth. With ``peerBaseUrl`` empty the feature is disabled:
every view answers a uniform 404 BEFORE auth (design §4.3).

Rate limiting keys on ``REMOTE_ADDR`` only — deliberately NOT
``auth.views._get_client_ip``: that helper trusts the leftmost client-controlled
proxy header, and these endpoints are public/pre-auth, so a spoofed header must
not bypass their only guard. Deployment caveat: behind a reverse proxy,
``REMOTE_ADDR`` is the proxy for every caller, so these caps become global for
inbound handshake traffic. Acceptable for v1; revisit if a trusted-proxy
configuration is ever added.
"""

from __future__ import annotations

import asyncio
import hmac
import time

import orjson
from asgiref.sync import sync_to_async
from django.http import HttpResponseNotAllowed, JsonResponse

from twicc.core.services import peer_mutation
from twicc.core.services.attachments.inline import INLINE_MAX_REQUEST_BYTES
from twicc.core.services.peer_tokens import aresolve_peer, peer_base_url, peer_credentials_are_active

# Up to 50 MB of attachments, base64-encoded, plus the JSON: the inline request cap (D12).
PEER_MESSAGE_MAX_REQUEST_BYTES = INLINE_MAX_REQUEST_BYTES
# Every other /peer/ view carries tiny JSON bodies.
SMALL_BODY_MAX_BYTES = 64 * 1024

# Unauthenticated handshake endpoint: 5 requests / 300 s window / 60 s lockout
# (the login-limiter numbers). The pending-rows cap (peer_mutation) backs it up.
_handshake_attempts: dict[str, list[float]] = {}
_HANDSHAKE_MAX_ATTEMPTS = 5
_HANDSHAKE_WINDOW_SECONDS = 300
_HANDSHAKE_LOCKOUT_SECONDS = 60

# Verify endpoint: the 6-digit code is the SOLE barrier against an
# attacker-requester (design §4.4) — ~10 calls/min per source, with lockout.
_verify_attempts: dict[str, list[float]] = {}
_VERIFY_MAX_ATTEMPTS = 10
_VERIFY_WINDOW_SECONDS = 60
_VERIFY_LOCKOUT_SECONDS = 60


def _check_rate_limit(
    attempts: dict[str, list[float]], ip: str, *, max_attempts: int, window: float, lockout: float,
) -> int | None:
    """Return None if allowed, else the seconds to wait. Prunes expired-window
    entries for ALL keys so spoofed source addresses can't grow the dict
    unboundedly."""
    now = time.monotonic()
    for key in [k for k, stamps in attempts.items() if not stamps or now - stamps[-1] >= window]:
        del attempts[key]
    timestamps = [t for t in attempts.get(ip, []) if now - t < window]
    if timestamps:
        attempts[ip] = timestamps
    else:
        attempts.pop(ip, None)
    if len(timestamps) >= max_attempts:
        wait = int(lockout - (now - timestamps[-1]))
        if wait > 0:
            return wait
        attempts.pop(ip, None)
    return None


def _record_attempt(attempts: dict[str, list[float]], ip: str) -> None:
    attempts.setdefault(ip, []).append(time.monotonic())


def _feature_disabled() -> bool:
    return not peer_base_url()


def _not_found() -> JsonResponse:
    return JsonResponse({"error": "not_found"}, status=404)


def _bearer(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return None


async def _authenticated_peer(request):
    token = _bearer(request)
    if not token:
        return None
    return await aresolve_peer(token)


def _small_body(request) -> bytes | None:
    """Body for the small views, or ``None`` when Content-Length exceeds the
    cap (Django's global DATA_UPLOAD_MAX_MEMORY_SIZE additionally protects
    the ``request.body`` read)."""
    try:
        length = int(request.headers.get("Content-Length") or 0)
    except ValueError:
        return None
    if length > SMALL_BODY_MAX_BYTES:
        return None
    return request.body


def _rate_limited(wait: int) -> JsonResponse:
    response = JsonResponse({"error": "rate_limited"}, status=429)
    response["Retry-After"] = str(wait)
    return response


def _parse_small_json(request) -> tuple[dict | None, JsonResponse | None]:
    """Size-capped body → parsed JSON OBJECT, or the error response to return.
    A valid-JSON non-object body (``[]``, ``"x"``, ``5``) is invalid_payload,
    not a crash — some of these endpoints are pre-auth."""
    body = _small_body(request)
    if body is None:
        return None, JsonResponse({"error": "too_large"}, status=413)
    try:
        data = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    return data, None


async def handshake_request(request):
    """POST /peer/handshake/request/ — unauthenticated (it IS the friend request)."""
    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ip = request.META.get("REMOTE_ADDR", "")
    wait = _check_rate_limit(
        _handshake_attempts, ip,
        max_attempts=_HANDSHAKE_MAX_ATTEMPTS,
        window=_HANDSHAKE_WINDOW_SECONDS,
        lockout=_HANDSHAKE_LOCKOUT_SECONDS,
    )
    if wait is not None:
        return _rate_limited(wait)
    _record_attempt(_handshake_attempts, ip)
    data, error_response = _parse_small_json(request)
    if error_response is not None:
        return error_response
    display_name = data.get("display_name")
    base_url = data.get("base_url")
    token = data.get("token")
    if not all(isinstance(v, str) and v.strip() for v in (display_name, base_url, token)):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    base_url = peer_mutation.normalize_base_url(base_url)
    if not peer_mutation.valid_base_url(base_url) or len(base_url) > 500 or len(token) > 64:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    status, resp = await peer_mutation.register_incoming_request(
        display_name=display_name.strip()[:255], base_url=base_url, token=token,
    )
    return JsonResponse(resp, status=status)


async def handshake_cancel(request):
    """POST /peer/handshake/cancel/ — requester withdraws one reconnect."""
    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    bearer = _bearer(request)
    if not bearer:
        return JsonResponse({"error": "unknown_request"}, status=404)
    status, resp = await peer_mutation.cancel_incoming_reconnect(bearer)
    return JsonResponse(resp, status=status)


async def handshake_verify(request):
    """POST /peer/handshake/verify/ — the REQUESTER echoes the out-of-band code,
    presenting the token it minted (stored here as ``token_theirs``)."""
    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    ip = request.META.get("REMOTE_ADDR", "")
    wait = _check_rate_limit(
        _verify_attempts, ip,
        max_attempts=_VERIFY_MAX_ATTEMPTS,
        window=_VERIFY_WINDOW_SECONDS,
        lockout=_VERIFY_LOCKOUT_SECONDS,
    )
    if wait is not None:
        return _rate_limited(wait)
    _record_attempt(_verify_attempts, ip)
    data, error_response = _parse_small_json(request)
    if error_response is not None:
        return error_response
    code = data.get("code")
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return JsonResponse({"error": "invalid_payload"}, status=400)
    bearer = _bearer(request)
    if not bearer:
        return JsonResponse({"error": "unknown_token"}, status=403)

    def _lookup():
        # ``active`` included on purpose: verify must be idempotent across the
        # accept transition (held-accept recovery, design §4.2).
        from twicc.core.models import Peer, PeerReconnectDirection, PeerState

        peer = Peer.objects.filter(token_theirs=bearer).first()
        reconnect_received = peer is not None and (
            peer.state in (PeerState.BROKEN, PeerState.REVOKED)
            and peer.reconnect_direction == PeerReconnectDirection.RECEIVED
        )
        if (
            peer is None
            or peer.state not in (PeerState.PENDING_RECEIVED, PeerState.ACTIVE) and not reconnect_received
            or not hmac.compare_digest(peer.token_theirs, bearer)
            or peer.state == PeerState.ACTIVE and not peer_credentials_are_active(peer)
        ):
            return None
        return peer

    peer = await sync_to_async(_lookup)()
    if peer is None:
        return JsonResponse({"error": "unknown_token"}, status=403)
    status, resp = await peer_mutation.record_verification_attempt(peer.id, code)
    return JsonResponse(resp, status=status)


async def handshake_accept(request):
    """POST /peer/handshake/accept/ — acceptor → requester callback."""
    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _authenticated_peer(request)
    if peer is None:
        return JsonResponse({"error": "unknown_token"}, status=403)
    data, error_response = _parse_small_json(request)
    if error_response is not None:
        return error_response
    token = data.get("token")
    display_name = data.get("display_name")
    if (
        not isinstance(token, str) or not token.strip() or len(token) > 64
        or not isinstance(display_name, str) or not display_name.strip()
    ):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    status, resp = await peer_mutation.apply_handshake_accept(
        peer.id, token=token, display_name=display_name.strip()[:255],
    )
    return JsonResponse(resp, status=status)


def _read_message_body(request) -> tuple[dict | None, JsonResponse | None]:
    """Read and parse the message body; blocking (up to 72 MB): run in a worker thread.

    ``request.read()``, NOT ``request.body``: Django's DATA_UPLOAD_MAX_MEMORY_SIZE check fires
    only in the ``body`` property; the per-view cap is enforced here instead.
    """
    body = request.read(PEER_MESSAGE_MAX_REQUEST_BYTES + 1)
    if len(body) > PEER_MESSAGE_MAX_REQUEST_BYTES:
        return None, JsonResponse({"error": "too_large"}, status=413)
    try:
        data = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    return data, None


async def message_receive(request):
    """POST /peer/messages/ — the message itself. 202 = stored pending."""
    from twicc.core.services import peer_messages

    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _authenticated_peer(request)
    if peer is None:
        return JsonResponse({"error": "unknown_token"}, status=403)
    try:
        length = int(request.headers.get("Content-Length") or "")
    except ValueError:
        return JsonResponse({"error": "too_large"}, status=413)
    if length > PEER_MESSAGE_MAX_REQUEST_BYTES:
        return JsonResponse({"error": "too_large"}, status=413)
    data, error_response = await asyncio.to_thread(_read_message_body, request)
    if error_response is not None:
        return error_response
    status, resp = await peer_messages.receive_peer_message(peer, data)
    return JsonResponse(resp, status=status)


async def message_status(request, message_id):
    """POST /peer/messages/<message_id>/status/ — resolution callback."""
    from twicc.core.services import peer_messages

    if _feature_disabled():
        return _not_found()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    peer = await _authenticated_peer(request)
    if peer is None:
        return JsonResponse({"error": "unknown_token"}, status=403)
    data, error_response = _parse_small_json(request)
    if error_response is not None:
        return error_response
    status, resp = await peer_messages.apply_status_callback(peer, message_id, data.get("status"))
    return JsonResponse(resp, status=status)
