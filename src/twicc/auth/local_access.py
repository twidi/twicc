"""Local-only access gating for an *unprotected* TwiCC instance.

When no password is configured, TwiCC has no way to authenticate anyone, so it
must not be silently reachable over the network. These helpers decide whether
an incoming request / WebSocket connection is a *direct local* one (loopback
peer, no proxy in front) and — combined with the password / opt-out settings —
whether a non-local request to an unprotected instance must be refused.

The IP classification reuses ``twicc.artifacts.proxy.classify_ip`` (the same
loopback / lan / public logic the artifact network broker uses for host
validation).

Trust model — deliberately the **opposite** of ``views._get_client_ip``: that
helper *trusts* forwarding headers to recover the real client IP for
rate-limiting; here we must never let a header *grant* access. A connection
counts as local only when the raw TCP peer is loopback **and** no forwarding
header is present, so a forged ``X-Forwarded-For`` can only ever push the
decision toward refusal (fail-safe), and a reverse proxy / tunnel in front
(which adds such a header) is correctly treated as remote.
"""

from __future__ import annotations

from django.conf import settings

from twicc.artifacts.proxy import classify_ip

# Presence of any of these means the request transited a proxy / tunnel, so a
# loopback TCP peer no longer implies a local client. Mirrors the set consulted
# by ``views._get_client_ip`` plus the standard ``Forwarded`` header.
_FORWARDING_META_KEYS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_REAL_IP",
    "HTTP_CF_CONNECTING_IP",
    "HTTP_FLY_CLIENT_IP",
    "HTTP_FORWARDED",
)

# Same set as ASGI header names (lowercased bytes, per the ASGI spec).
_FORWARDING_HEADER_NAMES = frozenset(
    name.encode()
    for name in (
        "x-forwarded-for",
        "x-real-ip",
        "cf-connecting-ip",
        "fly-client-ip",
        "forwarded",
    )
)


def _ip_is_loopback(ip: str) -> bool:
    """True only for a parseable loopback address; anything unparseable
    (empty REMOTE_ADDR, ``"unknown"``, …) fails toward *not* local."""
    try:
        return classify_ip(ip) == "loopback"
    except ValueError:
        return False


def request_is_local(request) -> bool:
    """Whether an HTTP request is a direct loopback connection (no proxy)."""
    for key in _FORWARDING_META_KEYS:
        if request.META.get(key):
            return False
    return _ip_is_loopback(request.META.get("REMOTE_ADDR", ""))


def scope_is_local(scope) -> bool:
    """Whether an ASGI (WebSocket) connection is a direct loopback one."""
    for name, _value in scope.get("headers") or ():
        if name in _FORWARDING_HEADER_NAMES:
            return False
    client = scope.get("client")
    if not client:
        return False
    return _ip_is_loopback(client[0])


def _unprotected_and_not_opted_out() -> bool:
    """The gate only applies to an instance with no password where the operator
    has not explicitly allowed insecure remote access."""
    from twicc.accounts.principal import accounts_enabled_cached

    # Accounts make the instance authenticated even without an instance password.
    if accounts_enabled_cached():
        return False
    return not settings.TWICC_PASSWORD_HASH and not settings.TWICC_ALLOW_INSECURE_REMOTE


def remote_access_blocked(request) -> bool:
    """True when an unprotected TwiCC must refuse this non-local HTTP request."""
    if not _unprotected_and_not_opted_out():
        return False
    return not request_is_local(request)


def scope_remote_access_blocked(scope) -> bool:
    """True when an unprotected TwiCC must refuse this non-local WS connection."""
    if not _unprotected_and_not_opted_out():
        return False
    return not scope_is_local(scope)
