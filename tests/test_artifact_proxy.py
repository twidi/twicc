"""Tests for the artifact network broker's server-side guard (phase 1).

The security core of the broker (see
``docs/archives/plans/2026-06-18-artifact-network-broker-design.md`` §6.2): a pure
``classify_ip`` plus the async resolve-and-pin guard. The boundary is **not** a
block of internal ranges — only the cloud metadata address is ever hard-blocked;
loopback / LAN / public are classified so the prompt can show the true target.
"""

import asyncio
import base64

import httpx
import orjson
import pytest
from django.test import AsyncClient

from twicc.artifacts import proxy as proxy_module

from twicc.artifacts.proxy import (
    ProxyResult,
    ResolutionError,
    ResolvedTarget,
    ResponseTooLarge,
    _decode_request_body,
    classify_ip,
    filter_request_headers,
    filter_response_headers,
    normalize_host_key,
    proxy_fetch,
    resolve_target,
)


def _run(coro):
    return asyncio.run(coro)


def _fake_resolver(*ips):
    """Build an injectable resolver that returns a fixed list of IPs, ignoring
    the host/port — lets the rebinding/metadata cases be tested without DNS."""

    async def resolver(host, port):
        return list(ips)

    return resolver


@pytest.mark.parametrize(
    "ip, expected",
    [
        # The one unconditional hard-block: cloud instance metadata (IMDS).
        ("169.254.169.254", "metadata"),     # AWS / GCP / Azure IMDS (IPv4)
        ("fd00:ec2::254", "metadata"),       # AWS IMDS over IPv6
        # Loopback.
        ("127.0.0.1", "loopback"),
        ("127.0.0.5", "loopback"),
        ("::1", "loopback"),
        # Private / internal ("lan"): RFC1918, ULA, link-local (non-metadata).
        ("10.0.0.5", "lan"),
        ("172.16.0.1", "lan"),
        ("192.168.1.10", "lan"),
        ("169.254.0.1", "lan"),              # link-local, but NOT the metadata address
        ("fc00::1", "lan"),                  # IPv6 ULA (not the metadata address)
        ("fe80::1", "lan"),                  # IPv6 link-local
        # Public (globally routable).
        ("8.8.8.8", "public"),
        ("1.1.1.1", "public"),
        ("2606:4700:4700::1111", "public"),
    ],
)
def test_classify_ip(ip, expected):
    assert classify_ip(ip) == expected


# --- resolve_target: server-side resolution, pinning, metadata block ----------


def test_resolve_target_public_pins_the_resolved_ip():
    # 8.8.8.8 is genuinely globally routable (unlike RFC 5737 doc ranges such as
    # 203.0.113.0/24, which is_global correctly reports as non-global → "lan").
    target = _run(resolve_target("api.example.com", 443, resolver=_fake_resolver("8.8.8.8")))
    assert target == ResolvedTarget(ip="8.8.8.8", kind="public")


def test_resolve_target_reports_loopback_when_a_name_rebinds_inward():
    # A name that resolves to 127.0.0.1 must surface as loopback (the honest
    # target the prompt shows), never silently as the requested host.
    target = _run(resolve_target("innocent.example.com", 5432, resolver=_fake_resolver("127.0.0.1")))
    assert target == ResolvedTarget(ip="127.0.0.1", kind="loopback")


def test_resolve_target_reports_lan_for_private_ip():
    target = _run(resolve_target("nas.local", 80, resolver=_fake_resolver("192.168.1.10")))
    assert target == ResolvedTarget(ip="192.168.1.10", kind="lan")


def test_resolve_target_blocks_metadata():
    target = _run(resolve_target("metadata.evil", 80, resolver=_fake_resolver("169.254.169.254")))
    assert target.kind == "metadata"


def test_resolve_target_blocks_metadata_hidden_among_public_addresses():
    # Multi-answer trick: a public IP first, the metadata IP second. The block
    # must win regardless of ordering.
    resolver = _fake_resolver("8.8.8.8", "169.254.169.254")
    target = _run(resolve_target("sneaky.evil", 80, resolver=resolver))
    assert target.kind == "metadata"


def test_resolve_target_raises_when_name_does_not_resolve():
    with pytest.raises(ResolutionError):
        _run(resolve_target("nxdomain.invalid", 80, resolver=_fake_resolver()))


# --- header hygiene -----------------------------------------------------------


def test_filter_request_headers_forwards_artifact_headers():
    # Pass-through policy: the artifact's own headers — including Authorization
    # and arbitrary custom ones — are forwarded verbatim. It is not the broker's
    # place to decide what an artifact may send (the user consents per host).
    # Only mechanical headers are dropped: `host` (we set the real vhost),
    # `content-length` (httpx recomputes), and hop-by-hop headers.
    out = filter_request_headers(
        {
            "Accept": "application/json",
            "Authorization": "Bearer token-123",
            "X-Custom": "keep-me",
            "Host": "twicc.local",
            "Content-Length": "42",
            "Connection": "keep-alive",
        }
    )
    assert out == {
        "accept": "application/json",
        "authorization": "Bearer token-123",
        "x-custom": "keep-me",
    }


def test_filter_response_headers_forwards_but_drops_mechanical():
    # Response headers are handed back as-is except the ones that would mismatch
    # the re-serialized body: httpx already content-decoded the stream, so a
    # stale `content-encoding`/`content-length` would corrupt it; hop-by-hop is
    # connection-scoped. Nothing is dropped on policy grounds — `set-cookie` is
    # inert once reconstructed into a Response object, so it is forwarded too.
    out = filter_response_headers(
        {
            "Content-Type": "application/json",
            "X-Custom": "keep-me",
            "Set-Cookie": "foo=1",
            "Content-Encoding": "gzip",
            "Content-Length": "99",
            "Transfer-Encoding": "chunked",
        }
    )
    assert out == {
        "content-type": "application/json",
        "x-custom": "keep-me",
        "set-cookie": "foo=1",
    }


# --- proxy_fetch: pinned outbound request, no redirect, size cap --------------


def _mock_client(handler, captured=None):
    def _wrapped(request):
        if captured is not None:
            captured["request"] = request
        return handler(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_wrapped))


def test_proxy_fetch_pins_ip_preserves_host_and_forwards_headers():
    captured = {}
    client = _mock_client(
        lambda r: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok":true}',
        ),
        captured,
    )
    result = _run(
        proxy_fetch(
            method="GET",
            url="https://api.example.com:8443/data",
            headers={"Authorization": "Bearer t", "Accept": "application/json"},
            body=None,
            pinned_ip="8.8.8.8",
            client=client,
        )
    )
    req = captured["request"]
    assert req.url.host == "8.8.8.8"                       # connection pinned to the IP
    assert req.headers["host"] == "api.example.com:8443"  # original vhost preserved
    assert req.headers["authorization"] == "Bearer t"     # artifact header forwarded
    assert req.extensions.get("sni_hostname") == "api.example.com"  # TLS SNI / cert host
    assert isinstance(result, ProxyResult)
    assert result.status == 200
    assert result.body == b'{"ok":true}'
    _run(client.aclose())


def test_proxy_fetch_does_not_follow_redirects():
    # An allowed public host could 302 to an internal one (or to metadata). The
    # broker must surface the redirect, never follow it.
    client = _mock_client(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    result = _run(
        proxy_fetch(method="GET", url="https://api.example.com/", headers={}, body=None, pinned_ip="8.8.8.8", client=client)
    )
    assert result.status == 302
    _run(client.aclose())


def test_proxy_fetch_caps_response_size():
    client = _mock_client(lambda r: httpx.Response(200, content=b"x" * 1000))
    with pytest.raises(ResponseTooLarge):
        _run(
            proxy_fetch(
                method="GET",
                url="https://api.example.com/",
                headers={},
                body=None,
                pinned_ip="8.8.8.8",
                client=client,
                max_bytes=100,
            )
        )
    _run(client.aclose())


# --- the Django view: POST /api/artifact-proxy/ -------------------------------


def _post(body):
    return _run(
        AsyncClient().post("/api/artifact-proxy/", data=orjson.dumps(body), content_type="application/json")
    )


def test_proxy_view_preflight_returns_resolved_target(monkeypatch):
    async def fake_resolve(host, port, **kw):
        assert (host, port) == ("api.example.com", 443)
        return ResolvedTarget(ip="8.8.8.8", kind="public")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    res = _post({"mode": "preflight", "request": {"url": "https://api.example.com/data"}})
    assert res.status_code == 200
    assert orjson.loads(res.content) == {"target": {"ip": "8.8.8.8", "kind": "public"}}


def test_proxy_view_preflight_blocks_metadata(monkeypatch):
    async def fake_resolve(host, port, **kw):
        return ResolvedTarget(ip="169.254.169.254", kind="metadata")

    monkeypatch.setattr(proxy_module, "resolve_target", fake_resolve)
    res = _post({"mode": "preflight", "request": {"url": "http://metadata.evil/"}})
    assert res.status_code == 200
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "metadata"}


def test_proxy_view_rejects_non_http_scheme():
    res = _post({"mode": "preflight", "request": {"url": "file:///etc/passwd"}})
    assert res.status_code == 400


def test_proxy_view_fetch_pins_approved_ip_and_returns_body(monkeypatch):
    seen = {}

    async def fake_fetch(*, method, url, headers, body, pinned_ip, client, **kw):
        seen.update(method=method, url=url, pinned_ip=pinned_ip)
        return ProxyResult(status=201, reason="Created", headers={"content-type": "application/json"}, body=b'{"id":7}')

    monkeypatch.setattr(proxy_module, "proxy_fetch", fake_fetch)
    res = _post(
        {
            "mode": "fetch",
            "pinned_ip": "8.8.8.8",
            "request": {"url": "https://api.example.com/items", "method": "POST"},
        }
    )
    assert res.status_code == 200
    assert seen == {"method": "POST", "url": "https://api.example.com/items", "pinned_ip": "8.8.8.8"}
    payload = orjson.loads(res.content)
    assert payload["status"] == 201
    assert payload["headers"] == {"content-type": "application/json"}
    assert base64.b64decode(payload["body_base64"]) == b'{"id":7}'


def test_decode_request_body_contract():
    # The shim/host serialize a non-empty body as ``body_base64`` holding the
    # base64 STRING itself — there is no ``body`` field and no boolean flag.
    payload = b'{"sql":"SELECT 1","params":[]}'
    b64 = base64.b64encode(payload).decode("ascii")
    assert _decode_request_body({"body_base64": b64}) == payload  # decoded to raw bytes
    assert _decode_request_body({}) is None                       # no body → None
    assert _decode_request_body({"body": "raw"}) == "raw"         # plain-string fallback


def test_proxy_view_fetch_decodes_request_body(monkeypatch):
    # Regression: a POST body arrives as ``body_base64`` (base64 string). The view
    # must decode it back to raw bytes and hand it to proxy_fetch — otherwise the
    # request reaches upstream with an empty body, and a JSON API rejects it
    # (Cloudflare D1: "Invalid non-JSON request", code 7400). The earlier fetch
    # test above mocked proxy_fetch but never asserted on ``body``, so this path
    # went uncovered.
    seen = {}
    payload_bytes = b'{"sql":"SELECT COUNT(*) FROM payloads","params":[]}'

    async def fake_fetch(*, method, url, headers, body, pinned_ip, client, **kw):
        seen["body"] = body
        return ProxyResult(status=200, reason="OK", headers={}, body=b"{}")

    monkeypatch.setattr(proxy_module, "proxy_fetch", fake_fetch)
    res = _post(
        {
            "mode": "fetch",
            "pinned_ip": "8.8.8.8",
            "request": {
                "url": "https://api.example.com/query",
                "method": "POST",
                "body_base64": base64.b64encode(payload_bytes).decode("ascii"),
            },
        }
    )
    assert res.status_code == 200
    assert seen["body"] == payload_bytes


def test_proxy_view_fetch_refuses_metadata_pinned_ip(monkeypatch):
    called = False

    async def fake_fetch(**kw):
        nonlocal called
        called = True

    monkeypatch.setattr(proxy_module, "proxy_fetch", fake_fetch)
    res = _post(
        {"mode": "fetch", "pinned_ip": "169.254.169.254", "request": {"url": "https://x/", "method": "GET"}}
    )
    assert orjson.loads(res.content) == {"error": "blocked", "reason": "metadata"}
    assert called is False  # never even attempt the fetch


def test_proxy_view_rejects_get():
    res = _run(AsyncClient().get("/api/artifact-proxy/"))
    assert res.status_code == 405


# --- normalize_host_key: the allowlist key (scheme + host + effective port) ----


@pytest.mark.parametrize(
    "url, key",
    [
        # Default ports are made explicit.
        ("https://api.example.com/data?x=1", "https://api.example.com:443"),
        ("http://api.example.com/", "http://api.example.com:80"),
        # Explicit ports are preserved (port-by-port: §6.4).
        ("https://api.example.com:8443/p", "https://api.example.com:8443"),
        ("http://localhost:9000", "http://localhost:9000"),
        ("http://127.0.0.1:3502/x", "http://127.0.0.1:3502"),
        # Scheme + host are lower-cased (DNS is case-insensitive).
        ("HTTPS://API.Example.COM/", "https://api.example.com:443"),
        # IPv6 literals keep their brackets.
        ("http://[::1]:8080/x", "http://[::1]:8080"),
    ],
)
def test_normalize_host_key(url, key):
    assert normalize_host_key(url) == key


def test_normalize_host_key_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        normalize_host_key("ftp://example.com/x")
