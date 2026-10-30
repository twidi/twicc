"""Tests for the OpenRouter speech client (twicc.tts.openrouter).

The network is always mocked: every client comes from ``_make_client``,
monkeypatched to an ``httpx.MockTransport``.

Every streaming test runs its whole scenario inside one ``asyncio.run(...)``:
streams and their futures belong to the loop that created them.
"""

import asyncio
import gzip
import logging

import httpx
import orjson
import pytest

from twicc.tts import openrouter, streams
from twicc.tts.openrouter import (
    GENERATION_URL,
    MODELS_URL,
    SPEECH_URL,
    SpeechError,
    SpeechRequest,
    _log_generation_cost,
    build_payload,
    fetch_speech_models,
    parse_pcm_format,
    pcm_content_type,
    pump_speech,
    upstream_error_message,
)
from twicc.tts.streams import AudioStream, start_stream

LOGGER_NAME = "twicc.tts.openrouter"
STREAMS_LOGGER_NAME = "twicc.tts.streams"
LABEL = {"model": "openai/gpt-4o-mini-tts", "voice": "Kore", "format": "mp3", "chars": 11}
HINTED_400 = "Provider returned 400. Check the model, the voice, the format and the text length."


@pytest.fixture(autouse=True)
def _clean_models_cache():
    openrouter._clear_models_cache()
    yield
    openrouter._clear_models_cache()


@pytest.fixture(autouse=True)
def _reset_streams():
    streams._reset_for_tests()
    yield
    streams._reset_for_tests()


@pytest.fixture(autouse=True)
def _capture_module_logger(caplog, monkeypatch):
    # settings_test's ``disable_existing_loggers: True`` can disable these loggers,
    # depending on the suite's import order (see test_compute_shutdown.py).
    # Re-enable them and route them straight to caplog, whatever their ancestors do.
    for name in (LOGGER_NAME, STREAMS_LOGGER_NAME):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "disabled", False)
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "handlers", [caplog.handler])


def _mock_transport(monkeypatch, handler):
    monkeypatch.setattr(
        openrouter,
        "_make_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout),
    )


def _request(response_format="mp3", voice="Kore"):
    return SpeechRequest("Hello there", "openai/gpt-4o-mini-tts", voice, response_format, None, "")


def test_build_payload_omits_empty_optional_fields():
    r = SpeechRequest("Hi", "m", "", "mp3", None, "")
    assert build_payload(r) == {"model": "m", "input": "Hi", "response_format": "mp3"}
    r = SpeechRequest("Hi", "m", "Kore", "pcm", 1.2, "calm")
    assert build_payload(r) == {
        "model": "m",
        "input": "Hi",
        "response_format": "pcm",
        "voice": "Kore",
        "speed": 1.2,
        "instructions": "calm",
    }


def test_parse_pcm_format():
    assert parse_pcm_format("audio/pcm;rate=24000;channels=1") == (24000, 1)
    assert parse_pcm_format("audio/pcm; rate=16000") == (16000, 1)
    assert parse_pcm_format("audio/pcm;rate=48000;channels=2") == (48000, 2)
    assert parse_pcm_format("audio/pcm") is None
    assert parse_pcm_format("audio/pcm;rate=abc") is None
    assert parse_pcm_format("audio/pcm;rate=384000;channels=8") == (384000, 8)
    assert parse_pcm_format("audio/pcm;rate=384001") is None
    assert parse_pcm_format("audio/pcm;rate=99999999999") is None
    assert parse_pcm_format("audio/pcm;rate=24000;channels=9") is None
    assert parse_pcm_format("audio/pcm;rate=24000;channels=40000") is None
    assert parse_pcm_format("audio/pcm;rate=0") is None
    assert parse_pcm_format("audio/pcm;rate=24000;channels=0") is None


def test_pcm_content_type_is_explicit():
    assert pcm_content_type("audio/pcm;rate=44100;channels=1") == "audio/pcm;rate=44100;channels=1"
    assert pcm_content_type("audio/pcm;rate=44100") == "audio/pcm;rate=44100;channels=1"


@pytest.mark.parametrize("content_type", ["audio/pcm", "audio/pcm;rate=abc", "audio/pcm;rate=999999"])
def test_pcm_content_type_falls_back_to_24k_mono(caplog, content_type):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    assert pcm_content_type(content_type) == "audio/pcm;rate=24000;channels=1"
    warnings = [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert content_type in warnings[0].getMessage()


def test_upstream_error_message_and_hint():
    body = orjson.dumps({"error": {"message": "Provider returned 400", "code": 400}})
    assert (
        upstream_error_message(400, body)
        == "Provider returned 400. Check the model, the voice, the format and the text length."
    )
    other = orjson.dumps({"error": {"message": "No auth credentials found", "code": 401}})
    assert upstream_error_message(401, other) == "No auth credentials found"
    assert upstream_error_message(500, b"<html>") == "OpenRouter returned HTTP 500"
    assert upstream_error_message(502, orjson.dumps({"error": "flat"})) == "OpenRouter returned HTTP 502"
    assert upstream_error_message(503, orjson.dumps({"error": {"message": ""}})) == "OpenRouter returned HTTP 503"
    assert upstream_error_message(504, orjson.dumps([1, 2])) == "OpenRouter returned HTTP 504"


class _QueueBody(httpx.AsyncByteStream):
    """A response body that yields the chunks of a queue as they come.

    An exception item is raised, ``None`` ends the body.
    """

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    async def __aiter__(self):
        while (item := await self._queue.get()) is not None:
            if isinstance(item, BaseException):
                raise item
            yield item


async def _within(awaitable, seconds: float = 1.0):
    """Await *awaitable*, failing the test instead of hanging."""
    async with asyncio.timeout(seconds):
        return await awaitable


def _capture_http_stack_logs(caplog, monkeypatch):
    """Capture httpx and httpcore at DEBUG too, so a key leak there is caught."""
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
        caplog.set_level(logging.DEBUG, logger=name)


def test_pump_streams_chunks_as_they_arrive(monkeypatch):
    async def scenario():
        body = asyncio.Queue()
        _mock_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, headers={"content-type": "audio/mpeg", "x-generation-id": "gen-2"}, stream=_QueueBody(body)
            ),
        )
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_speech(_request(), "k", stream))

        await _within(stream.ready)
        assert (stream.state, stream.content_type, stream.generation_id) == ("running", "audio/mpeg", "gen-2")
        assert bytes(stream.data) == b""

        body.put_nowait(b"ID3first")
        await _within(stream.wait_for_more(0))
        assert bytes(stream.data) == b"ID3first"
        assert stream.state == "running"

        body.put_nowait(b"-second")
        await _within(stream.wait_for_more(8))
        assert bytes(stream.data) == b"ID3first-second"
        assert stream.state == "running"

        body.put_nowait(None)
        await _within(pump)
        assert stream.state == "complete"
        assert bytes(stream.data) == b"ID3first-second"

    asyncio.run(scenario())


def test_pump_decodes_a_gzip_body(monkeypatch):
    audio = b"ID3" + bytes(range(256)) * 64

    def handler(request):
        assert "gzip" in request.headers["accept-encoding"]
        return httpx.Response(
            200,
            content=gzip.compress(audio),
            headers={"content-type": "audio/mpeg", "content-encoding": "gzip"},
        )

    async def scenario():
        _mock_transport(monkeypatch, handler)
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream))
        assert stream.state == "complete"
        assert bytes(stream.data) == audio

    asyncio.run(scenario())


def test_pump_sends_the_bearer_key_and_payload(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    caplog.set_level(logging.DEBUG, logger=STREAMS_LOGGER_NAME)
    _capture_http_stack_logs(caplog, monkeypatch)
    key = "sk-or-XYZ"
    timeouts = []
    responses = iter(
        [
            httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg", "x-generation-id": "gen-1"}),
            httpx.Response(400, content=orjson.dumps({"error": {"message": "Provider returned 400", "code": 400}})),
        ]
    )

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == SPEECH_URL
        assert request.headers["authorization"] == f"Bearer {key}"
        assert orjson.loads(request.content) == {
            "model": "openai/gpt-4o-mini-tts",
            "input": "Hello there",
            "response_format": "pcm",
            "voice": "Kore",
        }
        return next(responses)

    def factory(timeout):
        timeouts.append(timeout)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(openrouter, "_make_client", factory)

    async def scenario():
        ok = start_stream(lambda s: pump_speech(_request("pcm"), key, s), label=LABEL)
        await _within(ok._task)
        assert (ok.state, bytes(ok.data), ok.generation_id) == ("complete", b"ID3mp3", "gen-1")

        failed = start_stream(lambda s: pump_speech(_request("pcm"), key, s), label=LABEL)
        with pytest.raises(SpeechError):
            await _within(failed.ready)
        await _within(failed._task)

    asyncio.run(scenario())

    assert timeouts == [openrouter.SPEECH_TIMEOUT, openrouter.SPEECH_TIMEOUT]
    assert openrouter.SPEECH_TIMEOUT.read == 180.0
    assert openrouter.SPEECH_TIMEOUT.connect == 15.0
    lines = [r.getMessage() for r in caplog.records if r.name == STREAMS_LOGGER_NAME and r.levelno == logging.INFO]
    assert len(lines) == 2
    assert "generation=gen-1" in lines[0] and lines[0].endswith("outcome=ok")
    assert lines[1].endswith("outcome=upstream_error 400")
    # The HTTP stack logs reached caplog too (httpx logs each request).
    assert any(r.name == "httpx" for r in caplog.records)
    assert key not in caplog.text


@pytest.mark.parametrize(
    ("requested", "upstream_type", "expected_type"),
    [
        ("pcm", "audio/pcm;rate=44100", "audio/pcm;rate=44100;channels=1"),
        ("pcm", "audio/pcm; rate=16000; channels=2", "audio/pcm;rate=16000;channels=2"),
        ("pcm", "audio/pcm", "audio/pcm;rate=24000;channels=1"),
        # The upstream type decides, not the requested format.
        ("mp3", "audio/pcm;rate=24000;channels=1", "audio/pcm;rate=24000;channels=1"),
        ("pcm", "audio/mpeg", "audio/mpeg"),
        ("wav", "audio/wav", "audio/wav"),
    ],
)
def test_pump_makes_pcm_types_explicit(monkeypatch, requested, upstream_type, expected_type):
    pcm = b"\x00\x01" * 2400

    async def scenario():
        _mock_transport(monkeypatch, lambda request: httpx.Response(200, content=pcm, headers={"content-type": upstream_type}))
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(requested), "k", stream))
        assert stream.state == "complete"
        assert stream.content_type == expected_type
        assert stream.generation_id is None
        assert bytes(stream.data) == pcm  # never re-wrapped: the bytes stream as they come

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (400, {"error": {"message": "Provider returned 400", "code": 400}}, HINTED_400),
        (401, {"error": {"message": "User not found.", "code": 401}}, "User not found."),
        (500, None, "OpenRouter returned HTTP 500"),
    ],
)
def test_pump_upstream_error_fails_before_ready(monkeypatch, status, body, message):
    content = b"<html>" if body is None else orjson.dumps(body)

    async def scenario():
        _mock_transport(
            monkeypatch,
            lambda request: httpx.Response(status, content=content, headers={"content-type": "application/json"}),
        )
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream))
        with pytest.raises(SpeechError) as exc_info:
            await stream.ready
        assert (exc_info.value.message, exc_info.value.upstream_status) == (message, status)
        assert (stream.state, stream.error, stream.upstream_status) == ("failed", message, status)
        assert stream.content_type is None
        assert bytes(stream.data) == b""

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({"content-type": "application/json"}, b'{"ok": true}', "OpenRouter returned HTTP 200"),
        ({"content-type": "application/json"}, orjson.dumps({"error": {"message": "Model is overloaded"}}), "Model is overloaded"),
        ({"content-type": "text/html"}, b"<html>", "OpenRouter returned HTTP 200"),
        ({}, b"ID3", "OpenRouter returned HTTP 200"),
    ],
)
def test_pump_non_audio_success_is_an_error(monkeypatch, headers, body, message):
    async def scenario():
        _mock_transport(monkeypatch, lambda request: httpx.Response(200, content=body, headers=headers))
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream))
        with pytest.raises(SpeechError) as exc_info:
            await stream.ready
        assert (exc_info.value.message, exc_info.value.upstream_status) == (message, 200)
        assert (stream.state, stream.error) == ("failed", message)
        assert bytes(stream.data) == b""

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ConnectTimeout("secret-ish detail"), "OpenRouter did not answer in time"),
        (httpx.ReadTimeout("secret-ish detail"), "OpenRouter did not answer in time"),
        (httpx.ConnectError("secret-ish detail"), "Could not reach OpenRouter"),
        (httpx.ReadError("secret-ish detail"), "Could not reach OpenRouter"),
        (httpx.RemoteProtocolError("secret-ish detail"), "Could not reach OpenRouter"),
    ],
)
@pytest.mark.parametrize("when", ["before_headers", "after_headers"])
def test_pump_timeout_and_network_errors(monkeypatch, error, message, when):
    async def scenario():
        body = asyncio.Queue()

        def handler(request):
            if when == "before_headers":
                raise error
            return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=_QueueBody(body))

        _mock_transport(monkeypatch, handler)
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_speech(_request(), "k", stream))
        if when == "after_headers":
            await _within(stream.ready)
            body.put_nowait(b"ID3a")
            body.put_nowait(error)
        await _within(pump)

        assert (stream.state, stream.error, stream.upstream_status) == ("failed", message, None)
        assert "secret-ish" not in stream.error
        if when == "before_headers":
            with pytest.raises(SpeechError) as exc_info:
                await stream.ready
            assert exc_info.value.message == message
            assert exc_info.value.upstream_status is None
            assert "secret-ish" not in str(exc_info.value)
        else:
            assert stream.ready.result() is None
            assert bytes(stream.data) == b"ID3a"

    asyncio.run(scenario())


def test_pump_keeps_the_first_failure_when_the_close_fails(monkeypatch):
    # An upstream error fails the stream; then closing the upstream call raises a
    # network error. The first failure stands, and the pump does not raise.
    class FailingCloseClient(httpx.AsyncClient):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            raise httpx.ReadError("close failed")

    monkeypatch.setattr(
        openrouter,
        "_make_client",
        lambda timeout: FailingCloseClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, content=b"<html>")), timeout=timeout
        ),
    )

    async def scenario():
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream))
        assert (stream.state, stream.error, stream.upstream_status) == ("failed", "OpenRouter returned HTTP 500", 500)
        with pytest.raises(SpeechError):
            await stream.ready

    asyncio.run(scenario())


def test_pump_lets_an_unexpected_error_through(monkeypatch):
    # Not an upstream failure: the stream task logs it with its traceback.
    def handler(request):
        raise RuntimeError("boom")

    async def scenario():
        _mock_transport(monkeypatch, handler)
        stream = AudioStream(LABEL)
        with pytest.raises(RuntimeError):
            await _within(pump_speech(_request(), "k", stream))
        assert stream.state == "opening"

    asyncio.run(scenario())


@pytest.mark.parametrize("when", ["before_headers", "after_headers"])
def test_pump_cancellation_propagates(monkeypatch, when):
    async def scenario():
        body = asyncio.Queue()

        async def handler(request):
            if when == "before_headers":
                await asyncio.sleep(10)
            return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=_QueueBody(body))

        _mock_transport(monkeypatch, handler)
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_speech(_request(), "k", stream))
        if when == "after_headers":
            await _within(stream.ready)
            body.put_nowait(b"ID3a")
            await _within(stream.wait_for_more(0))
        else:
            await asyncio.sleep(0.01)

        pump.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pump
        # The pump neither failed nor finished the stream: its owner decides.
        assert stream.state == ("opening" if when == "before_headers" else "running")
        assert stream.error is None

    asyncio.run(scenario())


def test_models_are_shaped_cached_and_failures_not_cached(monkeypatch):
    hits = []
    payload = {
        "data": [
            {
                "id": "openai/gpt-4o-mini-tts",
                "name": "OpenAI: GPT-4o mini TTS",
                "supported_voices": ["alloy", "nova"],
                "pricing": {"prompt": "0.0000006"},
            },
            {"id": "google/gemini-tts", "name": "", "supported_voices": None},
        ]
    }
    unknown = {"formats": None, "streams": None, "max_chars": 2000, "whole_formats": []}
    expected = [
        {
            "id": "openai/gpt-4o-mini-tts",
            "name": "OpenAI: GPT-4o mini TTS",
            "voices": ["alloy", "nova"],
            "pricing": {"prompt": 0.0000006, "completion": None},
            **unknown,
        },
        {
            "id": "google/gemini-tts",
            "name": "google/gemini-tts",
            "voices": [],
            "pricing": {"prompt": None, "completion": None},
            **unknown,
        },
    ]

    def ok_handler(request):
        hits.append(str(request.url))
        assert "authorization" not in request.headers
        return httpx.Response(200, content=orjson.dumps(payload), headers={"content-type": "application/json"})

    _mock_transport(monkeypatch, ok_handler)
    assert asyncio.run(fetch_speech_models()) == expected
    assert hits == [MODELS_URL]
    assert asyncio.run(fetch_speech_models()) == expected
    assert len(hits) == 1

    openrouter._clear_models_cache()

    def failing_handler(request):
        hits.append(str(request.url))
        raise httpx.ConnectError("down")

    _mock_transport(monkeypatch, failing_handler)
    assert asyncio.run(fetch_speech_models()) == []
    assert len(hits) == 2

    _mock_transport(monkeypatch, ok_handler)
    assert asyncio.run(fetch_speech_models()) == expected
    assert len(hits) == 3


def test_models_callers_cannot_corrupt_the_cache(monkeypatch):
    payload = {"data": [{"id": "m", "name": "M", "supported_voices": ["alloy"]}]}

    def handler(request):
        return httpx.Response(200, content=orjson.dumps(payload))

    _mock_transport(monkeypatch, handler)
    first = asyncio.run(fetch_speech_models())
    first[0]["voices"].append("intruder")
    first[0]["name"] = "changed"
    first.append({"id": "extra"})
    second = asyncio.run(fetch_speech_models())
    second[0]["voices"].clear()
    second[0]["pricing"]["prompt"] = 1.0

    assert asyncio.run(fetch_speech_models()) == [
        {
            "id": "m", "name": "M", "voices": ["alloy"], "pricing": {"prompt": None, "completion": None},
            "formats": None, "streams": None, "max_chars": 2000, "whole_formats": [],
        },
    ]


def test_models_pricing_keeps_only_usable_prices(monkeypatch):
    payload = {
        "data": [
            {"id": "a", "pricing": {"prompt": "0.00000062", "completion": "0"}},
            {"id": "b", "pricing": {"prompt": 0.0025, "completion": "-1"}},
            {"id": "c", "pricing": {"prompt": "nan", "completion": True}},
            {"id": "d", "pricing": {"prompt": "free", "completion": None}},
            {"id": "e", "pricing": "0.1"},
            {"id": "f"},
        ]
    }

    def handler(request):
        return httpx.Response(200, content=orjson.dumps(payload))

    _mock_transport(monkeypatch, handler)
    assert [m["pricing"] for m in asyncio.run(fetch_speech_models())] == [
        {"prompt": 0.00000062, "completion": 0.0},
        {"prompt": 0.0025, "completion": None},
        {"prompt": None, "completion": None},
        {"prompt": None, "completion": None},
        {"prompt": None, "completion": None},
        {"prompt": None, "completion": None},
    ]


def test_models_voices_keep_only_a_list_of_strings(monkeypatch):
    payload = {
        "data": [
            {"id": "a", "supported_voices": ["alloy", 3, None, {"x": 1}, "nova"]},
            {"id": "b", "supported_voices": "alloy"},
            {"id": "c", "supported_voices": {"alloy": True}},
            {"id": "d"},
        ]
    }

    def handler(request):
        return httpx.Response(200, content=orjson.dumps(payload))

    _mock_transport(monkeypatch, handler)
    assert [m["voices"] for m in asyncio.run(fetch_speech_models())] == [["alloy", "nova"], [], [], []]


def test_models_bad_payloads_return_empty(monkeypatch):
    bodies = iter([(500, b"oops"), (200, b"not json"), (200, orjson.dumps({"nope": []})), (200, orjson.dumps({"data": [{"name": "no id"}]}))])

    def handler(request):
        status, body = next(bodies)
        return httpx.Response(status, content=body)

    _mock_transport(monkeypatch, handler)
    for _ in range(4):
        assert asyncio.run(fetch_speech_models()) == []


def test_models_cache_expires(monkeypatch):
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(200, content=orjson.dumps({"data": []}))

    _mock_transport(monkeypatch, handler)
    now = [1000.0]
    monkeypatch.setattr(openrouter.time, "monotonic", lambda: now[0])
    asyncio.run(fetch_speech_models())
    now[0] += openrouter.MODELS_CACHE_TTL - 1
    asyncio.run(fetch_speech_models())
    assert len(hits) == 1
    now[0] += 2
    asyncio.run(fetch_speech_models())
    assert len(hits) == 2


def test_cost_lookup_retries_on_404(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    _capture_http_stack_logs(caplog, monkeypatch)
    calls = []
    responses = iter(
        [
            httpx.Response(404, content=orjson.dumps({"error": {"message": "Generation gen-1 not found"}})),
            httpx.Response(404, content=orjson.dumps({"error": {"message": "Generation gen-1 not found"}})),
            httpx.Response(200, content=orjson.dumps({"data": {"total_cost": 0.008, "provider_name": "Together"}})),
        ]
    )

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.copy_with(query=None) == httpx.URL(GENERATION_URL)
        assert request.url.params["id"] == "gen-1"
        assert request.headers["authorization"] == "Bearer sk-or-XYZ"
        return next(responses)

    _mock_transport(monkeypatch, handler)
    asyncio.run(_log_generation_cost("gen-1", "sk-or-XYZ", first_delay=0, retry_delay=0))

    assert len(calls) == 3
    assert "0.008" in caplog.text
    assert "Together" in caplog.text
    assert "sk-or-XYZ" not in caplog.text


def test_cost_lookup_gives_up_after_the_attempts(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    _mock_transport(monkeypatch, handler)
    asyncio.run(_log_generation_cost("gen-1", "k", first_delay=0, retry_delay=0, attempts=3))

    assert len(calls) == 3
    assert not [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno >= logging.INFO]


def test_cost_lookup_never_raises(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)

    def raising(request):
        raise httpx.ConnectError("secret-ish detail")

    handlers = [
        raising,
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(200, content=orjson.dumps({"nope": 1})),
        lambda request: httpx.Response(200, content=b"not json"),
    ]
    for handler in handlers:
        _mock_transport(monkeypatch, handler)
        assert asyncio.run(_log_generation_cost("gen-1", "k", first_delay=0, retry_delay=0)) is None

    assert "secret-ish" not in caplog.text


def test_cost_lookup_logs_missing_values_as_none(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request):
        return httpx.Response(200, content=orjson.dumps({"data": {"total_cost": 0.01}}))

    _mock_transport(monkeypatch, handler)
    asyncio.run(_log_generation_cost("gen-9", "k", first_delay=0, retry_delay=0))

    assert "TTS cost gen-9: $0.01 (provider None)" in caplog.text


def test_schedule_cost_log_runs_in_the_background(monkeypatch):
    seen = []

    async def fake_log(generation_id, api_key):
        seen.append((generation_id, api_key))

    monkeypatch.setattr(openrouter, "_log_generation_cost", fake_log)

    async def main():
        openrouter.schedule_cost_log("gen-1", "k")
        assert len(openrouter._cost_tasks) == 1
        await asyncio.gather(*openrouter._cost_tasks)
        await asyncio.sleep(0)

    asyncio.run(main())
    assert seen == [("gen-1", "k")]
    assert not openrouter._cost_tasks


def test_audio_duration_of_mp3():
    from twicc.tts.openrouter import audio_duration_seconds
    # MPEG-2 Layer III, 128 kbit/s (FF F3, bitrate index 12 = 128): 32 000 bytes = 2 s.
    mp3 = (b"\xff\xf3\xc4\x00" + b"\x00" * 412) * 77
    mp3 = mp3[:32000]
    assert audio_duration_seconds(mp3, "audio/mpeg") == 2.0
    # An ID3v2 tag before the first frame is skipped.
    tagged = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + mp3
    assert audio_duration_seconds(tagged, "audio/mpeg") == len(tagged) * 8 / 128000
    assert audio_duration_seconds(b"junk", "audio/mpeg") is None
    assert audio_duration_seconds(b"x", "audio/ogg") is None


def test_audio_duration_of_pcm():
    from twicc.tts.openrouter import audio_duration_seconds
    # 16-bit samples: 88 200 bytes at 44 100 Hz mono = 1 s.
    assert audio_duration_seconds(b"\x00" * 88200, "audio/pcm;rate=44100;channels=1") == 1.0
    assert audio_duration_seconds(b"\x00" * 88200, "audio/pcm;rate=22050;channels=2") == 1.0
    assert audio_duration_seconds(b"\x00" * 88200, "audio/pcm") is None


def test_audio_duration_reads_a_bytearray():
    # The views pass the stream's own bytearray: no copy of the audio.
    from twicc.tts.openrouter import audio_duration_seconds
    assert audio_duration_seconds(bytearray(88200), "audio/pcm;rate=44100;channels=1") == 1.0
    mp3 = ((b"\xff\xf3\xc4\x00" + b"\x00" * 412) * 77)[:32000]
    tagged = bytearray(b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + mp3)
    assert audio_duration_seconds(bytearray(mp3), "audio/mpeg") == 2.0
    assert audio_duration_seconds(tagged, "audio/mpeg") == len(tagged) * 8 / 128000


def test_cost_lookup_returns_the_cost(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=orjson.dumps({"data": {"total_cost": 0.0008, "provider_name": "P"}}))

    _mock_transport(monkeypatch, handler)
    cost = asyncio.run(openrouter._log_generation_cost("gen-1", "k", first_delay=0, retry_delay=0))
    assert cost == 0.0008
