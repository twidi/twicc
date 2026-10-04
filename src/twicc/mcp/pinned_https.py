"""Public-address HTTPS transport for CIMD and event webhooks."""

import asyncio
import ipaddress
import logging
import socket
import ssl
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class NonGlobalAddressError(OSError):
    """DNS has no addresses, or contains a non-global address."""


class PinnedResponse(NamedTuple):
    status_code: int
    body: bytes
    overflow: bool


async def _resolve_address(hostname, port):
    """Validate every DNS answer before selecting the first address."""
    addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise NonGlobalAddressError("HTTPS requires global addresses")
    return addresses[0][4][0]


async def request(url, *, method, headers=None, body=None, timeout, response_cap, required_status=None):
    """Send to one pinned address without redirects or environment proxies.

    Callers own the overall deadline. A required status avoids reading rejected
    CIMD responses. Overflow remains separate from the response status.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("An HTTPS URL without credentials or fragment is required")
    port = parsed.port or 443
    address = await _resolve_address(parsed.hostname, port)
    host = f"[{address}]" if ":" in address else address
    target = f"https://{host}:{port}{parsed.path or '/'}"
    if parsed.query:
        target += "?" + parsed.query
    outgoing = httpx.Headers(headers)
    outgoing["Host"] = parsed.netloc
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client,
        client.stream(method, target, headers=outgoing, content=body,
                      extensions={"sni_hostname": parsed.hostname}) as response,
    ):
        if required_status is not None and response.status_code != required_status:
            return PinnedResponse(response.status_code, b"", False)
        data = bytearray()
        async for part in response.aiter_bytes():
            remaining = response_cap - len(data)
            data.extend(part[:remaining])
            if len(part) > remaining:
                return PinnedResponse(response.status_code, bytes(data), True)
        return PinnedResponse(response.status_code, bytes(data), False)


async def post_webhook(url, *, headers, body):
    """Apply the webhook's overall deadline and response cap."""
    return await asyncio.wait_for(
        request(url, method="POST", headers=headers, body=body, timeout=10, response_cap=4096),
        timeout=10,
    )


def _exception_chain(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)


def classify_send_error(error):
    """Map send exceptions, checking timeout evidence before TLS evidence."""
    chain = tuple(_exception_chain(error))
    if any(isinstance(item, (httpx.TimeoutException, TimeoutError)) for item in chain):
        return "timeout"
    excluded = (ssl.SSLWantReadError, ssl.SSLWantWriteError, ssl.SSLEOFError,
                ssl.SSLZeroReturnError, ssl.SSLSyscallError)
    if any(isinstance(item, ssl.SSLError) and not isinstance(item, excluded) for item in chain):
        return "tls_error"
    if not isinstance(error, (httpx.TransportError, OSError)):
        logger.error("Unexpected webhook send exception", exc_info=(type(error), error, error.__traceback__))
    return "connection_refused"
