"""Pinned address policy, transport profiles, and real TLS exception chains."""

import asyncio
import logging
import socket
import ssl
import subprocess

import httpx
import pytest

from twicc.mcp import pinned_https


def dns(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


@pytest.mark.parametrize("addresses", [(), ("127.0.0.1",), ("::1",), ("10.0.0.1",),
                                       ("8.8.8.8", "192.168.0.1")])
def test_address_refusal_precedes_connection(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns(*addresses))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: pytest.fail("Address refusal must precede HTTP"))
    with pytest.raises(pinned_https.NonGlobalAddressError):
        asyncio.run(pinned_https.post_webhook("https://callback.example", headers={}, body=b"{}"))


@pytest.mark.parametrize("address", ["8.8.8.8", "2001:4860:4860::8888"])
def test_pin_host_sni_port_path_query_headers_body(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns(address))
    original = httpx.AsyncClient

    def respond(request):
        assert request.method == "POST"
        assert request.url.host == address
        assert request.url.port == 8443
        assert request.url.raw_path == b"/callback%2Fpath?key=value"
        assert request.headers["host"] == "callback.example:8443"
        assert request.extensions["sni_hostname"] == "callback.example"
        assert request.headers["authorization"] == "Bearer example"
        assert request.content == b"payload"
        return httpx.Response(302, content=b"redirect", headers={"Location": "https://elsewhere.example"})

    def client(**kwargs):
        assert kwargs == {"timeout": 10, "follow_redirects": False, "trust_env": False}
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    response = asyncio.run(pinned_https.post_webhook(
        "https://callback.example:8443/callback%2Fpath?key=value",
        headers={"Host": "wrong.example", "Authorization": "Bearer example"}, body=b"payload",
    ))
    assert response == (302, b"redirect", False)


@pytest.mark.parametrize("length,overflow", [(4096, False), (4097, True)])
def test_response_cap_keeps_status_and_bounded_body(monkeypatch, length, overflow):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns("8.8.8.8"))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, content=b"x" * length)), **kw,
    ))
    response = asyncio.run(pinned_https.post_webhook("https://example.com", headers={}, body=b""))
    assert response == (503, b"x" * min(length, 4096), overflow)


def test_classifier_walks_both_links_and_cycles_timeout_first():
    error = httpx.ConnectError("outer")
    error.__cause__ = ssl.SSLError("TLS")
    error.__context__ = TimeoutError("deadline")
    error.__cause__.__context__ = error
    assert pinned_https.classify_send_error(error) == "timeout"


@pytest.mark.parametrize("kind", [ssl.SSLWantReadError, ssl.SSLWantWriteError, ssl.SSLEOFError,
                                  ssl.SSLZeroReturnError, ssl.SSLSyscallError])
def test_ssl_io_conditions_are_connection_failures(kind):
    error = httpx.ConnectError("outer")
    error.__cause__ = kind("I/O")
    assert pinned_https.classify_send_error(error) == "connection_refused"


def test_dns_and_non_global_are_expected_failures(caplog):
    with caplog.at_level(logging.ERROR):
        for error in (socket.gaierror("DNS"), pinned_https.NonGlobalAddressError("private")):
            assert pinned_https.classify_send_error(error) == "connection_refused"
    assert not caplog.records


def test_unexpected_failure_logs_traceback_every_time(caplog, monkeypatch):
    monkeypatch.setattr(pinned_https.logger, "disabled", False)
    monkeypatch.setattr(pinned_https.logger, "propagate", True)
    try:
        raise httpx.InvalidURL("bug")
    except httpx.InvalidURL as error:
        with caplog.at_level(logging.ERROR):
            assert pinned_https.classify_send_error(error) == "connection_refused"
            assert pinned_https.classify_send_error(error) == "connection_refused"
    assert len(caplog.records) == 2
    assert all("Traceback (most recent call last):" in record.message for record in caplog.records)
    assert all("test_unexpected_failure_logs_traceback_every_time" in record.message for record in caplog.records)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.fixture(scope="module")
def tls_context(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pinned-tls")
    certificate, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=callback.example", "-keyout", str(key), "-out", str(certificate),
    ], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    return context


@pytest.mark.parametrize("case,expected", [
    ("stalled_handshake", "timeout"), ("stalled_read", "timeout"),
    ("outer_timeout", "timeout"), ("plain_http", "tls_error"),
    ("clienthello_close", "connection_refused"), ("certificate", "tls_error"),
])
def test_real_socket_exception_chains(monkeypatch, tls_context, case, expected):
    async def run():
        writers = set()
        release = asyncio.Event()

        async def serve(reader, writer):
            writers.add(writer)
            try:
                if case in ("plain_http", "clienthello_close"):
                    await reader.read(4096)
                    if case == "plain_http":
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                else:
                    await release.wait()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ssl.SSLError):
                    pass
                writers.discard(writer)

        context = tls_context if case in ("stalled_read", "certificate") else None
        server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=context)
        port = server.sockets[0].getsockname()[1]

        # Test-only seam: production DNS validation continues to reject loopback.
        async def local_address(hostname, requested_port):
            assert hostname == "callback.example" and requested_port == port
            return "127.0.0.1"

        monkeypatch.setattr(pinned_https, "_resolve_address", local_address)
        original = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
            **kw, verify=case == "certificate",
        ))
        try:
            with pytest.raises(Exception) as caught:
                await asyncio.wait_for(pinned_https.request(
                    f"https://callback.example:{port}", method="POST", headers={}, body=b"{}",
                    timeout=1 if case == "outer_timeout" else 0.15, response_cap=4096,
                ), timeout=0.05 if case == "outer_timeout" else 2)
            assert pinned_https.classify_send_error(caught.value) == expected
            if case in ("stalled_handshake", "stalled_read", "outer_timeout"):
                assert any(isinstance(error, ssl.SSLWantReadError)
                           for error in pinned_https._exception_chain(caught.value))
            if case == "certificate":
                assert any(isinstance(error, ssl.SSLCertVerificationError)
                           for error in pinned_https._exception_chain(caught.value))
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            for writer in tuple(writers):
                writer.close()
            await asyncio.sleep(0)

    asyncio.run(run())


def test_webhook_outer_deadline_includes_dns(monkeypatch):
    async def stall(*args, **kwargs):
        await asyncio.Future()

    original = asyncio.wait_for

    async def fast_deadline(awaitable, timeout):
        assert timeout == 10
        return await original(awaitable, timeout=0.01)

    monkeypatch.setattr(pinned_https, "_resolve_address", stall)
    monkeypatch.setattr(asyncio, "wait_for", fast_deadline)
    with pytest.raises(TimeoutError):
        asyncio.run(pinned_https.post_webhook("https://example.com", headers={}, body=b""))


@pytest.mark.parametrize("length,valid", [(65536, True), (65537, False)])
def test_cimd_keeps_get_timeout_and_64k_cap(monkeypatch, length, valid):
    import orjson
    from twicc.mcp.oauth.provider import fetch_metadata

    document = "https://client.example/client.json"
    metadata = orjson.dumps({"client_id": document, "redirect_uris": ["https://client.example/callback"]})
    data = metadata + b" " * (length - len(metadata))
    original = httpx.AsyncClient
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns("8.8.8.8"))

    def respond(request):
        assert request.method == "GET"
        return httpx.Response(200, content=data)

    def client(**kwargs):
        assert kwargs == {"timeout": 5, "follow_redirects": False, "trust_env": False}
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    result = asyncio.run(fetch_metadata(document))
    assert (result is not None) == valid


@pytest.mark.parametrize("status", [201, 302, 400, 500])
def test_cimd_rejects_status_without_reading_body(monkeypatch, status):
    from twicc.mcp.oauth.provider import fetch_metadata

    class Unreadable(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("CIMD must not read a rejected response")
            yield b""

    original = httpx.AsyncClient
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: dns("8.8.8.8"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, stream=Unreadable())), **kw,
    ))
    assert asyncio.run(fetch_metadata("https://client.example/client.json")) is None


def test_cimd_outer_deadline_includes_slot_wait(monkeypatch):
    from twicc.mcp.oauth import provider

    original = asyncio.wait_for

    async def fast_deadline(awaitable, timeout):
        assert timeout == 10
        return await original(awaitable, timeout=0.01)

    monkeypatch.setattr(asyncio, "wait_for", fast_deadline)
    monkeypatch.setattr(provider, "_metadata_slots", asyncio.Semaphore(0))
    assert asyncio.run(provider.fetch_metadata("https://client.example/client.json")) is None


@pytest.mark.parametrize("error", [socket.gaierror("DNS"), httpx.ConnectError("connect"), ValueError("invalid")])
def test_cimd_failure_stays_none_without_classifier(monkeypatch, error):
    from twicc.mcp.oauth import provider

    async def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider, "request", failed)
    monkeypatch.setattr(pinned_https, "classify_send_error", lambda error: pytest.fail("CIMD has no classifier"))
    assert asyncio.run(provider.fetch_metadata("https://client.example/client.json")) is None


@pytest.mark.parametrize("lengths,overflow", [([2048, 2048], False), ([2048, 2048, 1], True), ([3, 5000], True)])
def test_streamed_response_stops_at_first_overflow_and_closes(monkeypatch, lengths, overflow):
    consumed, closed = [], []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for length in lengths:
                consumed.append(length)
                yield b"x" * length
            if overflow:
                pytest.fail("The transport reads after detecting overflow")

        async def aclose(self):
            closed.append(True)

    original = httpx.AsyncClient
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: dns("8.8.8.8"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, stream=Chunks())), **kwargs,
    ))
    response = asyncio.run(pinned_https.post_webhook("https://receiver.example/", headers={}, body=b"{}"))
    assert response == (503, b"x" * 4096, overflow)
    assert consumed == lengths
    assert closed == [True]
