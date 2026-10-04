"""Callback verification uses fake senders and never reaches a network."""

import asyncio
import base64
import gc
import weakref
from datetime import UTC, datetime
import time
import ssl

import orjson
import pytest
from standardwebhooks import Webhook

from twicc.mcp.events import Clock
from twicc.mcp.events.delivery import VerificationError, VerificationService
from twicc.mcp.pinned_https import PinnedResponse

@pytest.fixture
def anyio_backend():
    return "asyncio"


SECRET = base64.b64encode(b"v" * 32).decode()
URL = "https://receiver.example/callback"


class Time:
    value = 0

    @property
    def clock(self):
        return Clock(lambda: datetime.fromtimestamp(self.value, UTC), time.time, lambda: self.value)


async def echo(url, *, headers, body):
    return PinnedResponse(200, body, False)


@pytest.mark.anyio
async def test_signed_shared_challenge_cache_and_expiry():
    time = Time()
    calls = []
    gate = asyncio.Event()

    async def send(url, *, headers, body):
        calls.append((url, headers, body))
        assert Webhook(SECRET).verify(body, headers) == orjson.loads(body)
        assert headers["X-MCP-Subscription-Id"] == "sub_0"
        assert headers["webhook-id"].startswith("msg_verification_")
        await gate.wait()
        return PinnedResponse(201, body, False)

    service = VerificationService(clock=time.clock, send=send)
    tasks = [asyncio.create_task(service.verify("conn", URL, f"sub_{i}", SECRET)) for i in range(10)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    assert len(calls) == 1
    await service.verify("conn", URL, "sub_0", SECRET)
    time.value = 86399
    await service.verify("conn", URL, "sub_0", SECRET)
    assert len(calls) == 1
    time.value = 86400
    await service.verify("conn", URL, "sub_0", SECRET)
    await service.verify("other", URL, "sub_0", SECRET)
    await service.verify("conn", URL + "?exact=1", "sub_0", SECRET)
    assert len(calls) == 4
    assert not service._inflight


@pytest.mark.anyio
@pytest.mark.parametrize("status,body,overflow,reason", [
    (302, b'{}', False, "challenge_failed"),
    (400, b'{}', True, "http_4xx"),
    (503, b'{}', True, "http_5xx"),
    (200, b'{}', True, "challenge_failed"),
    (200, b'bad', False, "challenge_failed"),
    (200, b'[]', False, "challenge_failed"),
    (200, b'{}', False, "challenge_failed"),
    (200, b'{"challenge":1}', False, "challenge_failed"),
    (200, b'{"challenge":"different"}', False, "challenge_failed"),
    (200, b'{"challenge":"\\ud800"}', False, "challenge_failed"),
    (200, '{"challenge":"é"}'.encode(), False, "challenge_failed"),
])
async def test_closed_response_reasons(status, body, overflow, reason):
    async def send(*args, **kwargs):
        return PinnedResponse(status, body, overflow)

    service = VerificationService(send=send)
    with pytest.raises(VerificationError) as caught:
        await service.verify("conn", URL, "sub", SECRET)
    assert caught.value.code == -32015
    assert caught.value.data == {"reason": reason}
    assert not service._inflight
    assert service._slots._value == 8
    assert not service._verified


@pytest.mark.anyio
@pytest.mark.parametrize("error,reason", [
    (OSError("refused"), "connection_refused"),
    (TimeoutError(), "timeout"),
    (ssl.SSLError("certificate"), "tls_error"),
])
async def test_transport_reasons_shared_with_joiners(error, reason):
    calls = 0

    async def send(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise error

    service = VerificationService(send=send)
    results = await asyncio.gather(*[service.verify("conn", URL, "sub", SECRET) for _ in range(10)],
                                   return_exceptions=True)
    assert calls == 1
    assert all(result.code == -32015 and result.data == {"reason": reason} for result in results)
    assert all(result is results[0] for result in results)
    assert not service._inflight


@pytest.mark.anyio
async def test_non_ascii_matching_challenge(monkeypatch):
    monkeypatch.setattr("twicc.mcp.events.delivery.secrets.token_urlsafe", lambda size: "é🙂")
    await VerificationService(send=echo).verify("conn", URL, "sub", SECRET)


@pytest.mark.anyio
async def test_slots_timeout_and_shutdown_cleanup():
    entered = 0
    full = asyncio.Event()

    async def blocked(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 8:
            full.set()
        await asyncio.Event().wait()

    service = VerificationService(send=blocked, slot_timeout=0.01)
    tasks = [asyncio.create_task(service.verify(str(i), URL, "sub", SECRET)) for i in range(8)]
    await full.wait()
    with pytest.raises(VerificationError) as caught:
        await service.verify("ninth", URL, "sub", SECRET)
    assert caught.value.code == -32013
    assert caught.value.data == {"limit": "concurrent_verifications", "max": 8}
    assert entered == 8
    await service.aclose()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert service._slots._value == 8
    assert not service._inflight


@pytest.mark.anyio
async def test_host_rate_boundary_and_other_host():
    time = Time()
    service = VerificationService(clock=time.clock, send=echo)
    for i in range(60):
        await service.verify(str(i), URL, "sub", SECRET)
    with pytest.raises(VerificationError) as caught:
        await service.verify("61", URL, "sub", SECRET)
    assert caught.value.data == {"limit": "verifications_per_minute", "max": 60}
    assert caught.value.code == -32013
    await service.verify("61", "https://other.example/callback", "sub", SECRET)
    time.value = 60
    await service.verify("61", URL, "sub", SECRET)
    assert service._slots._value == 8


@pytest.mark.anyio
async def test_caller_cancellation_preserves_shared_leader():
    entered, gate = asyncio.Event(), asyncio.Event()

    async def send(url, *, headers, body):
        entered.set()
        await gate.wait()
        return PinnedResponse(200, body, False)

    service = VerificationService(send=send)
    first = asyncio.create_task(service.verify("conn", URL, "sub", SECRET))
    await entered.wait()
    second = asyncio.create_task(service.verify("conn", URL, "sub", SECRET))
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    gate.set()
    await second
    assert service._slots._value == 8
    assert not service._inflight


@pytest.mark.anyio
async def test_all_cancelled_callers_leave_no_unhandled_failure():
    entered, gate = asyncio.Event(), asyncio.Event()
    errors = []
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))

    async def send(*args, **kwargs):
        entered.set()
        await gate.wait()
        raise OSError("refused")

    service = VerificationService(send=send)
    try:
        caller = asyncio.create_task(service.verify("conn", URL, "sub", SECRET))
        await entered.wait()
        leader = service._inflight[("conn", URL)]
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        gate.set()
        # A done callback signals completion without retrieving the exception.
        # Awaiting/gathering the leader would mask a missing _finished retrieval.
        completed = asyncio.Event()
        leader.add_done_callback(lambda task: completed.set())
        reference = weakref.ref(leader)
        await completed.wait()
        del leader, caller
        await asyncio.sleep(0)
        gc.collect()
        assert reference() is None
        assert not service._inflight
        assert service._slots._value == 8
        assert not errors
        service.send = echo
        await service.verify("conn", URL, "sub", SECRET)
    finally:
        await service.aclose()
        loop.set_exception_handler(original_handler)
