"""Tests for the OpenRouter text call (twicc.openrouter_text).

The network is always mocked: every client comes from ``_make_client``, monkeypatched to an
``httpx.MockTransport``; the key comes from a monkeypatched ``get_env_secret``.
Every scenario runs inside one ``asyncio.run(...)``.
"""

import asyncio
import logging

import httpx
import orjson
import pytest

from twicc import openrouter_text
from twicc.openrouter_text import (
    CHAT_URL,
    QWEN_MODEL,
    OpenRouterTextError,
    complete_openrouter_text,
    openrouter_key_set,
    stream_openrouter_text,
)

LOGGER_NAME = "twicc.openrouter_text"
MODES = ("stream", "complete")


@pytest.fixture
def mock_openrouter(monkeypatch):
    """Install *handler* as the OpenRouter server, and the test key."""
    monkeypatch.setattr(openrouter_text, "get_env_secret", lambda name: "sk-test")

    def install(handler):
        monkeypatch.setattr(
            openrouter_text,
            "_make_client",
            lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
        )

    return install


@pytest.fixture
def records(caplog, monkeypatch):
    """The records of the module logger, routed straight to caplog.

    settings_test's ``disable_existing_loggers: True`` can disable this logger, depending on the
    suite's import order (as in tests/test_tts_adapt.py).
    """
    logger = logging.getLogger(LOGGER_NAME)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    return lambda: [record for record in caplog.records if record.name == LOGGER_NAME]


def _sse(*chunks) -> bytes:
    """An SSE body: a processing comment, one ``data:`` line per chunk, then ``[DONE]``.

    A chunk is a dict (sent as JSON) or a str (sent as is).
    """
    lines = [b": OPENROUTER PROCESSING\n\n"]
    for chunk in chunks:
        payload = chunk.encode() if isinstance(chunk, str) else orjson.dumps(chunk)
        lines.append(b"data: " + payload + b"\n\n")
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _sse_response(*chunks) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(*chunks))


def _answer(mode, *, provider="Cerebras", content="x", finish_reason="stop", refusal=None, cost=None):
    """A 200 answer in *mode*: one content chunk then a finish chunk (stream), or one JSON body."""
    if mode == "stream":
        delta = {"content": content}
        if refusal is not None:
            delta["refusal"] = refusal
        finish = {"provider": provider, "choices": [{"delta": {}, "finish_reason": finish_reason}]}
        if cost is not None:
            finish["usage"] = {"cost": cost}
        return _sse_response({"provider": provider, "choices": [{"delta": delta, "finish_reason": None}]}, finish)
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    body = {"provider": provider, "choices": [{"message": message, "finish_reason": finish_reason}]}
    if cost is not None:
        body["usage"] = {"cost": cost}
    return httpx.Response(200, json=body)


async def _call(mode, prompt="hi", **kwargs):
    """Run the call in *mode*: the list of deltas (stream) or the whole answer (complete)."""
    if mode == "stream":
        return [delta async for delta in stream_openrouter_text(QWEN_MODEL, prompt, **kwargs)]
    return await complete_openrouter_text(QWEN_MODEL, prompt, timeout_seconds=5, **kwargs)


def test_the_qwen_model():
    assert QWEN_MODEL.model == "qwen/qwen3.8-27b"
    assert QWEN_MODEL.provider == "Cerebras"
    assert QWEN_MODEL.options == {"reasoning": {"effort": "none"}, "max_tokens": 8000}


def test_openrouter_key_set(monkeypatch):
    seen = []

    def secret(name):
        seen.append(name)
        return "sk-test"

    monkeypatch.setattr(openrouter_text, "get_env_secret", secret)
    assert openrouter_key_set() is True
    assert seen == ["TWICC_OPENROUTER_API_KEY"]
    monkeypatch.setattr(openrouter_text, "get_env_secret", lambda name: None)
    assert openrouter_key_set() is False


@pytest.mark.parametrize("mode", MODES)
def test_the_request_forces_the_provider_and_sends_the_options(mock_openrouter, mode):
    requests = []

    def handler(request):
        requests.append(request)
        return _answer(mode)

    mock_openrouter(handler)
    asyncio.run(_call(mode))
    asyncio.run(_call(mode, system_prompt="sys"))

    request = requests[0]
    assert str(request.url) == CHAT_URL == "https://openrouter.ai/api/v1/chat/completions"
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = orjson.loads(request.content)
    assert body["model"] == "qwen/qwen3.8-27b"
    assert body["provider"] == {"order": ["Cerebras"], "allow_fallbacks": False}
    assert body["usage"] == {"include": True}
    assert body["reasoning"] == {"effort": "none"}
    assert body["max_tokens"] == 8000
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["stream"] is (mode == "stream")

    with_system = orjson.loads(requests[1].content)
    assert with_system["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def test_stream_yields_the_content_deltas(mock_openrouter, records):
    mock_openrouter(
        lambda request: _sse_response(
            {"provider": "Cerebras", "choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
            {"provider": "Cerebras", "choices": [{"delta": {"content": "lo"}}]},
            {"provider": "Cerebras", "choices": [{"delta": {"content": ""}}]},
            {"provider": "Cerebras", "choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"provider": "Cerebras", "choices": [], "usage": {"cost": 0.0012}},
            {"provider": "Cerebras"},
        )
    )
    assert asyncio.run(_call("stream")) == ["Hel", "lo"]
    [record] = records()
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert "outcome=ok" in message
    assert "cost=0.0012" in message


def test_complete_returns_the_whole_answer(mock_openrouter):
    mock_openrouter(
        lambda request: httpx.Response(
            200,
            json={
                "provider": "Cerebras",
                "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
                "usage": {"cost": 0.001},
            },
        )
    )
    assert asyncio.run(_call("complete")) == "Hello"


def _raise_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


FAILURES = [
    # (id, modes, handler(mode, request), reason, status)
    ("http", MODES, lambda mode, request: httpx.Response(401, json={"error": {"message": "No auth"}}), "http", 401),
    ("network", MODES, lambda mode, request: _raise_connect_error(request), "network", None),
    ("wrong-provider", MODES, lambda mode, request: _answer(mode, provider="Groq"), "wrong_provider", None),
    ("cut", MODES, lambda mode, request: _answer(mode, finish_reason="length"), "cut", None),
    ("content-filter", MODES, lambda mode, request: _answer(mode, finish_reason="content_filter"), "declined", None),
    ("finish-error", MODES, lambda mode, request: _answer(mode, finish_reason="error"), "declined", None),
    ("refusal", MODES, lambda mode, request: _answer(mode, content="", refusal="I can't."), "declined", None),
    (
        "stream-error",
        ("stream",),
        lambda mode, request: _sse_response({"error": {"message": "x"}}),
        "stream_error",
        None,
    ),
    ("bad-sse-json", ("stream",), lambda mode, request: _sse_response("not json"), "bad_answer", None),
    (
        "bad-body-json",
        ("complete",),
        lambda mode, request: httpx.Response(200, content=b"not json"),
        "bad_answer",
        None,
    ),
    (
        "no-choices",
        ("complete",),
        lambda mode, request: httpx.Response(200, json={"provider": "Cerebras", "usage": {"cost": 0.1}}),
        "bad_answer",
        None,
    ),
    (
        "empty-choices",
        ("complete",),
        lambda mode, request: httpx.Response(200, json={"provider": "Cerebras", "choices": []}),
        "bad_answer",
        None,
    ),
]


@pytest.mark.parametrize(
    ("mode", "make_response", "reason", "status"),
    [
        pytest.param(mode, make_response, reason, status, id=f"{case_id}-{mode}")
        for case_id, modes, make_response, reason, status in FAILURES
        for mode in modes
    ],
)
def test_failures(mock_openrouter, mode, make_response, reason, status):
    mock_openrouter(lambda request: make_response(mode, request))
    with pytest.raises(OpenRouterTextError) as raised:
        asyncio.run(_call(mode))
    assert raised.value.reason == reason
    assert raised.value.status == status


@pytest.mark.parametrize("mode", MODES)
def test_no_key_fails_without_a_request(mock_openrouter, monkeypatch, mode):
    calls = []

    def handler(request):
        calls.append(request)
        return _answer(mode)

    mock_openrouter(handler)
    monkeypatch.setattr(openrouter_text, "get_env_secret", lambda name: None)
    with pytest.raises(OpenRouterTextError) as raised:
        asyncio.run(_call(mode))
    assert raised.value.reason == "no_key"
    assert raised.value.status is None
    assert calls == []


def test_complete_times_out(mock_openrouter, records):
    async def handler(request):
        await asyncio.sleep(1)
        return _answer("complete")

    mock_openrouter(handler)
    with pytest.raises(OpenRouterTextError) as raised:
        asyncio.run(complete_openrouter_text(QWEN_MODEL, "hi", timeout_seconds=0.05))
    assert raised.value.reason == "timeout"
    [record] = records()
    assert "outcome=timeout" in record.getMessage()
    assert "cancelled" not in record.getMessage()


class _HangingStream(httpx.AsyncByteStream):
    """One SSE chunk, then waits forever; records its ``aclose()``."""

    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield _sse({"provider": "Cerebras", "choices": [{"delta": {"content": "first"}}]}).removesuffix(
            b"data: [DONE]\n\n"
        )
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def test_closing_the_stream_ends_the_request(mock_openrouter, records):
    body = _HangingStream()
    mock_openrouter(lambda request: httpx.Response(200, stream=body))

    async def scenario():
        async with asyncio.timeout(5):
            stream = stream_openrouter_text(QWEN_MODEL, "hi")
            first = await anext(stream)
            await stream.aclose()
            return first

    assert asyncio.run(scenario()) == "first"
    assert body.closed
    [record] = records()
    assert "outcome=cancelled" in record.getMessage()


@pytest.mark.parametrize("mode", MODES)
def test_one_log_line_without_the_text(mock_openrouter, records, mode):
    answer = "the secret answer"

    mock_openrouter(lambda request: _answer(mode, content=answer, cost=0.0042))
    result = asyncio.run(_call(mode, prompt="secret prompt", system_prompt="secret system"))
    assert answer in ("".join(result) if mode == "stream" else result)
    [success] = records()
    assert success.levelno == logging.INFO
    message = success.getMessage()
    assert "model=qwen/qwen3.8-27b" in message
    assert "provider=Cerebras" in message
    assert f"mode={mode}" in message
    assert "outcome=ok" in message
    assert "cost=0.0042" in message

    mock_openrouter(lambda request: _answer(mode, content=answer, provider="Groq"))
    with pytest.raises(OpenRouterTextError):
        asyncio.run(_call(mode, prompt="secret prompt", system_prompt="secret system"))
    assert len(records()) == 2
    failure = records()[1]
    assert failure.levelno == logging.WARNING
    message = failure.getMessage()
    assert "model=qwen/qwen3.8-27b" in message
    assert "provider=Cerebras" in message
    assert "outcome=wrong_provider" in message

    for record in records():
        text = record.getMessage() + repr(record.args) + str(record.exc_info)
        for secret in ("secret prompt", "secret system", answer, "sk-test"):
            assert secret not in text
