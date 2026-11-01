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
    RATE_SCAN_BYTES,
    SPEECH_URL,
    SpeechError,
    SpeechRequest,
    _log_generation_cost,
    build_payload,
    bytes_per_second,
    fetch_speech_models,
    mp3_frame_bitrates,
    parse_pcm_format,
    pcm_content_type,
    pump_speech,
    upstream_error_message,
)
from twicc.tts.part_source import StaticParts
from twicc.tts.streams import AudioStream, start_stream

from tests.test_tts_part_audio import _frame, _id3

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


PARTS = ["Part one.", "Part two.", "Part three."]


def _parts_transport(monkeypatch, answers) -> list[str]:
    """Answer the n-th speech request with ``answers[n]`` (a response, or an exception to raise).

    Returns the list that receives the ``input`` of each request, as it arrives.
    """
    inputs = []

    def handler(request):
        inputs.append(orjson.loads(request.content)["input"])
        answer = answers[len(inputs) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    _mock_transport(monkeypatch, handler)
    return inputs


def _audio(content: bytes, content_type: str = "audio/mpeg", generation_id: str | None = None) -> httpx.Response:
    headers = {"content-type": content_type}
    if generation_id:
        headers["x-generation-id"] = generation_id
    return httpx.Response(200, content=content, headers=headers)


async def _until(predicate, seconds: float = 1.0) -> None:
    """Wait until ``predicate()`` holds, failing the test instead of hanging."""
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.005)


def test_pump_sends_the_parts_one_after_the_other(monkeypatch):
    async def scenario():
        bodies = [asyncio.Queue() for _ in PARTS]
        inputs = _parts_transport(
            monkeypatch,
            [
                httpx.Response(
                    200, headers={"content-type": "audio/mpeg", "x-generation-id": f"gen-{n}"}, stream=_QueueBody(body)
                )
                for n, body in enumerate(bodies, 1)
            ],
        )
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_speech(_request(), "k", stream, PARTS))

        await _within(stream.ready)
        bodies[0].put_nowait(b"audio-one")
        await _within(stream.wait_for_more(0))
        await asyncio.sleep(0.05)
        assert inputs == ["Part one."]  # the next part waits for this body to close

        bodies[0].put_nowait(None)
        await _until(lambda: len(inputs) == 2)
        assert inputs == ["Part one.", "Part two."]
        bodies[1].put_nowait(b"audio-two")
        bodies[1].put_nowait(None)
        await _until(lambda: len(inputs) == 3)
        bodies[2].put_nowait(b"audio-three")
        bodies[2].put_nowait(None)
        await _within(pump)

        assert inputs == PARTS
        assert bytes(stream.data) == b"audio-oneaudio-twoaudio-three"
        assert stream.parts_sent == 3
        assert stream.generation_ids == ["gen-1", "gen-2", "gen-3"]
        assert stream.generation_id == "gen-1"
        assert stream.state == "complete"

    asyncio.run(scenario())


@pytest.mark.parametrize("paced", [True, False])
def test_pump_paces_only_when_asked(monkeypatch, paced):
    async def scenario():
        _parts_transport(monkeypatch, [_audio(b"one"), _audio(b"two")])
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS[:2], paced=paced))
        assert stream.state == "complete"
        assert stream.paced is paced

    asyncio.run(scenario())


class FakeSource:
    """A part source over *parts*: an item is a text, a ``SpeechError`` to raise, or a float, the
    seconds to wait before the next item (``None``: wait forever). Records its ``aclose()``."""

    def __init__(self, items, *, split: bool):
        self._items = list(items)
        self._split = split
        self.split: bool | None = None
        self.closed = 0

    async def next_part(self) -> str | None:
        while self._items:
            item = self._items.pop(0)
            if isinstance(item, SpeechError):
                raise item
            if isinstance(item, float):
                await asyncio.sleep(item)
            elif item is None:
                await asyncio.Event().wait()  # never set
            else:
                self.split = self._split
                return item
        return None

    async def aclose(self) -> None:
        self.closed += 1


def test_pacing_follows_the_source_split(monkeypatch):
    part = _id3(30) + _frame(b"Info") + _frame()

    async def scenario():
        _parts_transport(monkeypatch, [_audio(part)])
        single = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", single, StaticParts(PARTS[:1]), paced=True))
        assert (single.state, single.paced) == ("complete", False)
        assert bytes(single.data) == part  # a one-part reading is never filtered

        _parts_transport(monkeypatch, [_audio(part), _audio(part)])
        split = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", split, StaticParts(PARTS[:2]), paced=True))
        assert (split.state, split.paced) == ("complete", True)
        assert bytes(split.data) == _id3(30) + _frame() + _frame()

    asyncio.run(scenario())


def test_the_split_comes_from_the_source_not_from_the_part_count(monkeypatch):
    # A source cut its first part before its text ended: split, though it gives one part.
    part = _id3(30) + _frame(b"Info") + _frame()

    async def scenario():
        _parts_transport(monkeypatch, [_audio(part)])
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, FakeSource(["one"], split=True), paced=True))
        assert (stream.state, stream.paced) == ("complete", True)
        assert bytes(stream.data) == _id3(30) + _frame()

    asyncio.run(scenario())


def test_a_source_error_on_a_later_part_fails_the_stream_after_its_audio(monkeypatch):
    async def scenario():
        inputs = _parts_transport(monkeypatch, [_audio(b"one"), _audio(b"two")])
        source = FakeSource(["Part one.", SpeechError("The text adaptation stopped before the end")], split=True)
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, source, paced=True))
        assert (stream.state, stream.error) == ("failed", "The text adaptation stopped before the end")
        assert stream.ready.result() is None  # the first part's headers had arrived
        assert bytes(stream.data) == b"one"
        assert inputs == ["Part one."]
        assert source.closed == 1

    asyncio.run(scenario())


def test_a_source_error_on_the_first_part_fails_the_stream_before_its_headers(monkeypatch):
    async def scenario():
        inputs = _parts_transport(monkeypatch, [_audio(b"one")])
        source = FakeSource([SpeechError("no text")], split=False)
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, source))
        assert (stream.state, stream.error) == ("failed", "no text")
        with pytest.raises(SpeechError):
            stream.ready.result()
        assert inputs == []
        assert source.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["complete", "failed", "cancelled"])
def test_the_pump_always_closes_its_source(monkeypatch, ending):
    error = httpx.Response(429, content=b"{}", headers={"content-type": "application/json"})
    _parts_transport(monkeypatch, [_audio(b"one"), error if ending == "failed" else _audio(b"two")])
    # Cancelled: the second part never comes.
    source = FakeSource(["Part one.", None if ending == "cancelled" else "Part two."], split=True)

    async def scenario():
        stream = AudioStream(LABEL)
        pump = asyncio.create_task(pump_speech(_request(), "k", stream, source))
        if ending == "cancelled":
            await _until(lambda: stream.parts_sent == 1 and stream._part_started is None)
            await asyncio.sleep(0.01)
            assert source.closed == 0
            pump.cancel()
            with pytest.raises(asyncio.CancelledError):
                await _within(pump)
        else:
            await _within(pump)
        # Cancelled: the pump neither failed nor finished the stream (its owner decides).
        assert stream.state == ("running" if ending == "cancelled" else ending)

    asyncio.run(scenario())
    assert source.closed == 1


def test_the_wait_for_a_part_counts_as_generation_time(monkeypatch):
    async def scenario():
        _parts_transport(monkeypatch, [_audio(b"one"), _audio(b"two")])
        stream = AudioStream(LABEL)
        source = FakeSource(["Part one.", 0.2, "Part two."], split=True)
        await _within(pump_speech(_request(), "k", stream, source))
        assert stream.state == "complete"
        assert 200 <= stream.generation_ms < 400

    asyncio.run(scenario())


def test_pump_waits_for_the_lookahead_before_each_next_part(monkeypatch):
    async def scenario():
        inputs = _parts_transport(monkeypatch, [_audio(b"one"), _audio(b"two"), _audio(b"three")])
        stream = AudioStream(LABEL)
        waits = []

        async def wait_for_lookahead():
            waits.append(len(inputs))  # the requests sent when the wait starts

        stream.wait_for_lookahead = wait_for_lookahead
        await _within(pump_speech(_request(), "k", stream, PARTS))
        assert waits == [1, 2]
        assert stream.state == "complete"

    asyncio.run(scenario())


def test_a_later_part_error_fails_the_running_stream(monkeypatch):
    async def scenario():
        inputs = _parts_transport(
            monkeypatch,
            [
                _audio(b"one", generation_id="gen-1"),
                httpx.Response(
                    429,
                    content=orjson.dumps({"error": {"message": "Rate limited"}}),
                    headers={"content-type": "application/json"},
                ),
                _audio(b"three"),
            ],
        )
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS))
        assert (stream.state, stream.error, stream.upstream_status) == ("failed", "Rate limited", 429)
        assert stream.ready.result() is None  # the first part's headers had arrived
        assert bytes(stream.data) == b"one"
        assert inputs == PARTS[:2]

    asyncio.run(scenario())


def test_a_later_part_that_is_not_audio_fails_the_running_stream(monkeypatch):
    async def scenario():
        inputs = _parts_transport(
            monkeypatch,
            [_audio(b"one"), httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}), _audio(b"three")],
        )
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS))
        assert (stream.state, stream.error, stream.upstream_status) == ("failed", "OpenRouter returned HTTP 200", 200)
        assert bytes(stream.data) == b"one"
        assert inputs == PARTS[:2]

    asyncio.run(scenario())


def test_a_later_part_network_error_fails_with_its_message(monkeypatch):
    async def scenario():
        inputs = _parts_transport(
            monkeypatch, [_audio(b"one"), httpx.ConnectError("secret-ish detail"), _audio(b"three")]
        )
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS))
        assert (stream.state, stream.error, stream.upstream_status) == ("failed", "Could not reach OpenRouter", None)
        assert bytes(stream.data) == b"one"
        assert inputs == PARTS[:2]

    asyncio.run(scenario())


def test_a_later_part_with_another_type_fails(monkeypatch):
    async def scenario():
        inputs = _parts_transport(
            monkeypatch,
            [
                _audio(b"\x00\x01" * 4, "audio/pcm;rate=44100;channels=1"),
                _audio(b"\x00\x01" * 4, "audio/pcm;rate=24000"),
                _audio(b"\x00\x01" * 4, "audio/pcm;rate=44100;channels=1"),
            ],
        )
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request("pcm"), "k", stream, PARTS))
        assert (stream.state, stream.error) == ("failed", streams.PART_TYPE_CHANGED)
        assert bytes(stream.data) == b"\x00\x01" * 4
        assert inputs == PARTS[:2]

    asyncio.run(scenario())


def test_pump_strips_mp3_heads_of_split_texts_only(monkeypatch):
    part = _id3(30) + _frame(b"Info") + _frame()

    async def scenario():
        _parts_transport(monkeypatch, [_audio(part), _audio(part)])
        split = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", split, PARTS[:2]))
        assert split.state == "complete"
        assert bytes(split.data) == _id3(30) + _frame() + _frame()

        _parts_transport(monkeypatch, [_audio(part)])
        single = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", single))
        assert bytes(single.data) == part

    asyncio.run(scenario())


def test_a_xing_tagged_constant_bitrate_first_part_keeps_pacing(monkeypatch):
    # MiniMax writes an empty Xing placeholder on constant-bitrate audio: the tag alone does not
    # make the rate variable. The info frame is still dropped from every part.
    first = _id3(30) + _frame(b"Xing") + _frame() + _frame()
    later = _id3(30) + _frame(b"Xing") + _frame()

    async def scenario():
        _parts_transport(monkeypatch, [_audio(first), _audio(later)])
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS[:2], paced=True))
        assert stream.state == "complete"
        assert bytes(stream.data) == _id3(30) + _frame() * 3
        assert stream.byte_rate() == 16000.0

    asyncio.run(scenario())


def test_a_variable_bitrate_first_part_disables_pacing(monkeypatch):
    # Frames with different bitrates: the rate is unknown, the speech is neither paced nor held.
    # No margin: a paced stream then delivers less than it received (the constant-rate control).
    monkeypatch.setattr(streams, "PACE_MARGIN", 0.0)
    first = _frame() + _frame(bitrate_index=5) + _frame()

    async def scenario():
        _parts_transport(monkeypatch, [_audio(_frame() * 3), _audio(_frame())])
        constant = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", constant, PARTS[:2], paced=True))
        assert constant.state == "complete"
        assert constant.byte_rate() == 16000.0
        assert constant.deliverable() < len(constant.data)

        _parts_transport(monkeypatch, [_audio(first), _audio(_frame())])
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS[:2], paced=True))
        assert stream.state == "complete"
        assert stream.byte_rate() is None
        assert stream.deliverable() == len(stream.data)

    asyncio.run(scenario())


def test_pump_trims_split_pcm_parts_to_whole_frames(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    pcm_type = "audio/pcm;rate=24000;channels=2"  # 4-byte frames

    def chunked(*chunks: bytes) -> httpx.Response:
        body = asyncio.Queue()
        for chunk in (*chunks, None):
            body.put_nowait(chunk)
        return httpx.Response(200, headers={"content-type": pcm_type}, stream=_QueueBody(body))

    async def scenario():
        _parts_transport(monkeypatch, [chunked(b"abc", b"defg"), chunked(b"12345678")])
        split = AudioStream(LABEL)
        await _within(pump_speech(_request("pcm"), "k", split, PARTS[:2]))
        assert split.state == "complete"
        assert bytes(split.data) == b"abcd" + b"12345678"
        dropped = [r.getMessage() for r in caplog.records if "partial PCM frame" in r.getMessage()]
        assert dropped == ["TTS part 1: 3 bytes of a partial PCM frame dropped"]  # none for part 2

        _parts_transport(monkeypatch, [chunked(b"abc", b"defg")])
        single = AudioStream(LABEL)
        await _within(pump_speech(_request("pcm"), "k", single))
        assert bytes(single.data) == b"abcdefg"

    asyncio.run(scenario())


def test_release_during_the_lookahead_wait_sends_no_next_part(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=STREAMS_LOGGER_NAME)
    inputs = _parts_transport(monkeypatch, [_audio(b"one"), _audio(b"two"), _audio(b"three")])
    entered = []
    cancelled = []

    async def wait_for_lookahead(self):
        entered.append(len(inputs))
        try:
            await asyncio.Event().wait()  # never set
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(AudioStream, "wait_for_lookahead", wait_for_lookahead)

    async def scenario():
        stream = start_stream(lambda s: pump_speech(_request(), "k", s, PARTS), label=LABEL)
        await _until(lambda: entered)
        streams.release_stream(stream.id)
        with pytest.raises(asyncio.CancelledError):
            await _within(stream._task)
        assert stream.state == "released"

    asyncio.run(scenario())

    assert cancelled == [True]
    assert inputs == PARTS[:1]
    lines = [r.getMessage() for r in caplog.records if r.name == STREAMS_LOGGER_NAME and r.levelno == logging.INFO]
    assert len(lines) == 1
    assert " parts=1 " in lines[0]
    assert lines[0].endswith("outcome=released")


def test_an_empty_later_part_does_not_end_the_speech(monkeypatch):
    async def scenario():
        inputs = _parts_transport(monkeypatch, [_audio(_frame()), _audio(b""), _audio(_frame())])
        stream = AudioStream(LABEL)
        await _within(pump_speech(_request(), "k", stream, PARTS))
        assert inputs == PARTS
        assert bytes(stream.data) == _frame() * 2
        assert stream.state == "complete"

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
        openrouter.schedule_cost_log(["gen-1"], "k")
        assert len(openrouter._cost_tasks) == 1
        await asyncio.gather(*openrouter._cost_tasks)
        await asyncio.sleep(0)

    asyncio.run(main())
    assert seen == [("gen-1", "k")]
    assert not openrouter._cost_tasks


def _run_cost_log(monkeypatch, costs: dict) -> tuple[list[str], list[float]]:
    """``schedule_cost_log`` over the ids of *costs*, each lookup returning its cost.

    Returns the looked-up ids and the totals given to ``on_cost``.
    """
    looked_up = []
    totals = []

    async def fake_log(generation_id, api_key):
        looked_up.append(generation_id)
        return costs[generation_id]

    async def on_cost(total):
        totals.append(total)

    monkeypatch.setattr(openrouter, "_log_generation_cost", fake_log)

    async def main():
        openrouter.schedule_cost_log(list(costs), "k", on_cost)
        await asyncio.gather(*openrouter._cost_tasks)

    asyncio.run(main())
    return looked_up, totals


def test_schedule_cost_log_sums_the_parts(monkeypatch):
    looked_up, totals = _run_cost_log(monkeypatch, {"gen-1": 0.001, "gen-2": 0.002, "gen-3": 0.003})
    assert sorted(looked_up) == ["gen-1", "gen-2", "gen-3"]
    assert totals == [pytest.approx(0.006)]


def test_schedule_cost_log_gives_no_total_when_a_cost_is_missing(monkeypatch):
    looked_up, totals = _run_cost_log(monkeypatch, {"gen-1": 0.001, "gen-2": None, "gen-3": 0.003})
    assert sorted(looked_up) == ["gen-1", "gen-2", "gen-3"]
    assert totals == []


def test_schedule_cost_log_looks_the_parts_up_in_parallel(monkeypatch):
    running = []
    peak = []

    async def fake_log(generation_id, api_key):
        running.append(generation_id)
        await asyncio.sleep(0.01)
        peak.append(len(running))
        running.remove(generation_id)
        return 0.001

    monkeypatch.setattr(openrouter, "_log_generation_cost", fake_log)

    async def main():
        openrouter.schedule_cost_log(["gen-1", "gen-2", "gen-3"], "k")
        await asyncio.gather(*openrouter._cost_tasks)

    asyncio.run(main())
    assert max(peak) == 3


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


def test_bytes_per_second():
    assert bytes_per_second("audio/pcm;rate=44100;channels=1", b"") == 88200.0
    assert bytes_per_second("audio/pcm;rate=24000;channels=2", b"") == 96000.0
    assert bytes_per_second("audio/mpeg", _frame()) == 16000.0
    assert bytes_per_second("audio/mpeg", _id3(30) + _frame()) == 16000.0
    assert bytes_per_second("audio/mpeg", bytearray(_frame())) == 16000.0
    assert bytes_per_second("audio/mpeg", b"xxxx") is None
    # Only the first RATE_SCAN_BYTES bytes are read.
    assert bytes_per_second("audio/mpeg", b"x" * RATE_SCAN_BYTES + _frame()) is None
    assert bytes_per_second("audio/pcm", b"") is None
    assert bytes_per_second("audio/ogg", _frame()) is None


def test_mp3_frame_bitrates():
    frames = _id3(30) + _frame() + _frame(bitrate_index=5) + _frame()
    assert mp3_frame_bitrates(frames) == [128000, 64000, 128000]
    assert mp3_frame_bitrates(bytearray(frames)) == [128000, 64000, 128000]
    # A truncated last frame is not counted.
    assert mp3_frame_bitrates(frames[:-1]) == [128000, 64000]
    # The walk stops at bytes that are not a frame header.
    assert mp3_frame_bitrates(_frame() + b"xxxx" + _frame()) == [128000]
    assert mp3_frame_bitrates(b"xxxx") == []
    assert mp3_frame_bitrates(b"") == []
    # Only the first RATE_SCAN_BYTES bytes are read.
    many = _frame() * (RATE_SCAN_BYTES // len(_frame()) + 5)
    assert len(mp3_frame_bitrates(many)) == RATE_SCAN_BYTES // len(_frame())


def test_audio_duration_reads_past_the_rate_scan():
    # The duration reads the whole body: a large ID3 tag keeps the MP3 measurable.
    from twicc.tts.openrouter import audio_duration_seconds
    tagged = _id3(RATE_SCAN_BYTES) + _frame() * 10
    assert audio_duration_seconds(tagged, "audio/mpeg") == len(tagged) / 16000.0
    assert bytes_per_second("audio/mpeg", tagged) is None


def test_cost_lookup_returns_the_cost(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=orjson.dumps({"data": {"total_cost": 0.0008, "provider_name": "P"}}))

    _mock_transport(monkeypatch, handler)
    cost = asyncio.run(openrouter._log_generation_cost("gen-1", "k", first_delay=0, retry_delay=0))
    assert cost == 0.0008
