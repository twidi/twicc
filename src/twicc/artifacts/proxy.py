"""Server-side guard for the artifact network broker (phase 1).

See ``docs/archives/plans/2026-06-18-artifact-network-broker-design.md`` §6.2. The
boundary is **not** a block of internal ranges: only the cloud metadata address
is ever hard-blocked. Every other target is classified (loopback / lan / public)
so the host can show the user the true resolved destination before they consent.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
from typing import NamedTuple
from collections.abc import Awaitable, Callable

import httpx
import orjson
from django.http import HttpResponseNotAllowed, JsonResponse

# Cloud instance metadata endpoints (IMDS) — the sole unconditional hard-block:
# reachable server-side, no legitimate widget use, and a GET returns the cloud
# account's IAM credentials. AWS/GCP/Azure share the IPv4 link-local address;
# AWS also exposes it over IPv6.
_METADATA_IPS = frozenset(
    ipaddress.ip_address(ip) for ip in ("169.254.169.254", "fd00:ec2::254")
)

# Effective default port per scheme — used to normalize the allowlist key and to
# derive the connect port. The scheme set also gates which schemes are allowed.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def classify_ip(ip: str) -> str:
    """Classify a resolved IP into one of four kinds.

    ``"metadata"`` is the sole hard-block; ``"loopback"`` / ``"lan"`` /
    ``"public"`` are all reachable with the user's consent and differ only in
    what the prompt shows. ``"lan"`` is the catch-all for any non-loopback,
    non-global address (RFC1918, ULA, link-local, CGNAT, reserved, …) — i.e.
    "internal, but not loopback".
    """
    addr = ipaddress.ip_address(ip)
    if addr in _METADATA_IPS:
        return "metadata"
    if addr.is_loopback:
        return "loopback"
    if not addr.is_global:
        return "lan"
    return "public"


class ResolvedTarget(NamedTuple):
    """The server-side resolution of a requested host:port.

    ``ip`` is the address the proxy will **pin** the outbound connection to (no
    re-resolution at connect time → no rebinding within a request). ``kind`` is
    ``classify_ip(ip)`` — what the prompt shows the user, and what is stored on
    the allowlist for the rebinding re-prompt (§6.2).
    """

    ip: str
    kind: str


class ResolutionError(Exception):
    """The hostname did not resolve to any address (NXDOMAIN / empty)."""


# A resolver maps (host, port) → an ordered list of IP strings. Injectable so
# the rebinding / metadata cases are unit-testable without real DNS.
Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for *_unused, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in ips:
            ips.append(ip)
    return ips


async def resolve_target(host: str, port: int, *, resolver: Resolver = _default_resolver) -> ResolvedTarget:
    """Resolve ``host:port`` server-side and classify it for the prompt + pin.

    Validates **every** returned address: if any is the metadata endpoint, the
    whole target is blocked (``kind == "metadata"``), regardless of ordering.
    Otherwise the first resolved address is the one we pin to, and its kind is
    reported — the proxy only ever connects to the exact address it classified.
    """
    ips = await resolver(host, port)
    if not ips:
        raise ResolutionError(f"{host!r} did not resolve to any address")
    classified = [(ip, classify_ip(ip)) for ip in ips]
    for ip, kind in classified:
        if kind == "metadata":
            return ResolvedTarget(ip=ip, kind="metadata")
    ip, kind = classified[0]
    return ResolvedTarget(ip=ip, kind=kind)


def normalize_host_key(url: str) -> str:
    """The canonical allowlist key for a URL: ``scheme://host:port`` with the
    scheme/host lower-cased and the **effective** port made explicit (the scheme
    default when implicit). IPv6 hosts keep their brackets. Raises ``ValueError``
    for a non-http/https scheme or a missing host.

    This is the exact key stored on ``ArtifactBookmark.allowed_hosts`` and the
    one the proxy looks up for membership — port-by-port (§6.4).
    """
    parsed = httpx.URL(url)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"unsupported scheme: {scheme!r}")
    host = parsed.host.lower()
    if not host:
        raise ValueError("missing host")
    if ":" in host:  # IPv6 literal → bracket it in the key
        host = f"[{host}]"
    port = parsed.port or _DEFAULT_PORTS[scheme]
    return f"{scheme}://{host}:{port}"


# Header policy is **pass-through** (design §6.5): an artifact may legitimately
# send any header (an `Authorization` token, a custom `X-…`, …), and it is not
# the broker's place to decide what it may use — the user consents per host. We
# only drop what is *mechanically* wrong to forward, never on policy grounds.
#
# The TwiCC session cookie is a non-issue here: a browser `fetch` does not expose
# it in `Request.headers` and JS cannot set a `Cookie` header, so it never
# reaches the proxy. (Authenticated calls to TwiCC's *own* origin go host-direct
# in the browser — never through this proxy — see §6.6.)

# Hop-by-hop headers (RFC 7230 §6.1) — connection-scoped, never forwarded end to
# end on either side.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Outbound request: drop hop-by-hop plus the two we control mechanically —
# `host` (set from the pinned target's real vhost) and `content-length`
# (recomputed by httpx from the body we hand it).
_REQUEST_DROP = _HOP_BY_HOP | {"host", "content-length"}

# Inbound response: drop hop-by-hop plus the two that would mismatch the
# re-serialized body — httpx already content-decoded the stream, so a stale
# `content-encoding` would make the browser try to decode plain bytes, and a
# stale `content-length` would mismatch.
_RESPONSE_DROP = _HOP_BY_HOP | {"content-length", "content-encoding"}


def filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    """Forward the artifact's request headers verbatim, minus mechanical ones."""
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() not in _REQUEST_DROP
    }


def filter_response_headers(headers: dict[str, str]) -> dict[str, str]:
    """Hand back the upstream response headers, minus mechanical ones."""
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() not in _RESPONSE_DROP
    }


# Outbound caps. Timeout kept well under the Cloudflare-tunnel ~100s 524 ceiling
# (§13); size cap bounds memory since the body is buffered to cross postMessage.
OUTBOUND_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class ProxyResult(NamedTuple):
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes


class ResponseTooLarge(Exception):
    """The upstream response exceeded the size cap."""


def _host_header(url: httpx.URL) -> str:
    """The `Host` value the upstream server should see: the original host, with
    the port only when explicit/non-default."""
    if url.port is None:
        return url.host
    return f"{url.host}:{url.port}"


async def proxy_fetch(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | str | None,
    pinned_ip: str,
    client: httpx.AsyncClient,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> ProxyResult:
    """Perform the outbound request, **pinned** to ``pinned_ip``.

    The TCP connection targets ``pinned_ip`` (no re-resolution → no rebinding
    within the request) while the original host drives the ``Host`` header and
    the TLS SNI / certificate check. Redirects are never followed; the response
    body is streamed under ``max_bytes``. The caller must have already
    classified ``pinned_ip`` as non-metadata.
    """
    original = httpx.URL(url)
    pinned_url = original.copy_with(host=pinned_ip)
    out_headers = filter_request_headers(headers)

    request = client.build_request(method, pinned_url, headers=out_headers, content=body)
    # Force the real vhost (build_request derives Host from the pinned IP URL).
    request.headers["host"] = _host_header(original)
    # TLS: SNI + certificate verification against the real hostname, not the IP.
    request.extensions["sni_hostname"] = original.host

    response = await client.send(request, follow_redirects=False, stream=True)
    stream = response.aiter_bytes()
    try:
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream:
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLarge(f"upstream response exceeded {max_bytes} bytes")
            chunks.append(chunk)
    finally:
        await stream.aclose()
        await response.aclose()

    return ProxyResult(
        status=response.status_code,
        reason=response.reason_phrase,
        headers=filter_response_headers(dict(response.headers)),
        body=b"".join(chunks),
    )


# --- the Django async view: POST /api/artifact-proxy/ -------------------------
#
# Two modes in one endpoint (§6.1). All proxy-level outcomes (resolved target,
# refusal, fetched response, runtime errors) return HTTP 200 with a structured
# JSON body the host inspects; only malformed input (400) and wrong method (405)
# use HTTP status. Phase 1 enforces the IP guard (metadata block, pinning,
# caps, header hygiene); the per-artifact allowlist + consent are phases 2/4.

def _decode_request_body(req: dict) -> bytes | str | None:
    # The client (shim.js / host.js) serializes a non-empty request body as
    # ``body_base64`` holding the base64 STRING itself — there is no separate
    # ``body`` field and no boolean flag. Decode it back to raw bytes; fall back
    # to a plain ``body`` string only for hypothetical non-shim callers.
    b64 = req.get("body_base64")
    if b64:
        return base64.b64decode(b64)
    return req.get("body")


async def artifact_proxy(request, *, enforced_allowlist: set[str] | None = None, on_not_allowed=None):
    # ``enforced_allowlist`` is set ONLY by the share proxy (design §9.3/D6): the
    # normalized scheme://host:port must be in it or the fetch is refused 403.
    # The owner path passes None and deliberately does NOT re-check (broker §6.4).
    # ``on_not_allowed`` is called with ``(host_key, kind)`` when a share fetch is
    # refused as not_allowed; used for denial recording — design 2026-07-10 §4.1.
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        payload = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "bad_request", "reason": "invalid_json"}, status=400)

    req = payload.get("request") or {}
    url = req.get("url")
    if not isinstance(url, str) or not url:
        return JsonResponse({"error": "bad_request", "reason": "missing_url"}, status=400)
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return JsonResponse({"error": "bad_request", "reason": "invalid_url"}, status=400)
    if parsed.scheme not in _DEFAULT_PORTS:
        return JsonResponse({"error": "bad_request", "reason": "unsupported_scheme"}, status=400)
    host = parsed.host
    if not host:
        return JsonResponse({"error": "bad_request", "reason": "missing_host"}, status=400)
    port = parsed.port or _DEFAULT_PORTS[parsed.scheme]

    mode = payload.get("mode")

    if mode == "preflight":
        if enforced_allowlist is not None:
            # The share host fetches directly (no preflight) — a preflight request
            # here means a misconfigured client; refuse it.
            return JsonResponse({"error": "bad_request", "reason": "preflight_disabled"}, status=400)
        try:
            target = await resolve_target(host, port)
        except ResolutionError:
            return JsonResponse({"error": "unresolved"})
        if target.kind == "metadata":
            return JsonResponse({"error": "blocked", "reason": "metadata"})
        return JsonResponse({"target": {"ip": target.ip, "kind": target.kind}})

    if mode == "fetch":
        # Pin to the IP the host already approved (fresh once-grant); otherwise
        # resolve now (pre-approved host) and report the kind so the host can
        # detect a rebind. The metadata block applies on either path.
        #
        # SECURITY: a client-supplied ``pinned_ip`` is honoured ONLY on the owner
        # path (``enforced_allowlist is None``), where it comes from the owner's own
        # trusted preflight. On the SHARE path the allowlist is checked against the
        # URL host, but a viewer-supplied ``pinned_ip`` would decouple the connected
        # IP from that host — an anonymous SSRF pivot into loopback/LAN. The share
        # path has no preflight, so a legitimate share client never sends one; always
        # resolve server-side there and pin to the allowlisted host's real IP.
        pinned_ip = payload.get("pinned_ip")
        if enforced_allowlist is None and isinstance(pinned_ip, str) and pinned_ip:
            ip, kind = pinned_ip, classify_ip(pinned_ip)
        else:
            try:
                target = await resolve_target(host, port)
            except ResolutionError:
                return JsonResponse({"error": "unresolved"})
            ip, kind = target.ip, target.kind
        if kind == "metadata":
            return JsonResponse({"error": "blocked", "reason": "metadata"})
        if enforced_allowlist is not None:
            key = normalize_host_key(url)
            if key not in enforced_allowlist:
                if on_not_allowed is not None:
                    on_not_allowed(key, kind)
                return JsonResponse({"error": "blocked", "reason": "not_allowed"})

        try:
            async with httpx.AsyncClient(timeout=OUTBOUND_TIMEOUT_SECONDS) as client:
                result = await proxy_fetch(
                    method=(req.get("method") or "GET").upper(),
                    url=url,
                    headers=req.get("headers") or {},
                    body=_decode_request_body(req),
                    pinned_ip=ip,
                    client=client,
                )
        except ResponseTooLarge:
            return JsonResponse({"error": "too_large"})
        except httpx.HTTPError as exc:
            return JsonResponse({"error": "upstream", "reason": type(exc).__name__})

        return JsonResponse(
            {
                "status": result.status,
                "reason": result.reason,
                "kind": kind,
                "headers": result.headers,
                "body_base64": base64.b64encode(result.body).decode("ascii"),
            }
        )

    return JsonResponse({"error": "bad_request", "reason": "invalid_mode"}, status=400)
