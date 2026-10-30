"""Tests for the text-to-speech REST endpoints (twicc.tts.views).

The upstream calls are always mocked: ``pump_speech``, ``fetch_speech_models``,
``schedule_cost_log``, ``_record_last_test`` and ``openrouter_api_key`` are
monkeypatched on the views module.

Every streaming test runs its whole scenario inside one ``asyncio.run(...)``:
streams, their futures and their timers belong to the loop that created them.
Every await on the views is bounded by ``asyncio.timeout``: a regression fails
the test instead of hanging the suite.
"""

import asyncio
import logging

import orjson
import pytest
from django.test import AsyncClient, AsyncRequestFactory

from twicc.tts import streams, views
from twicc.tts.audio_cache import CachedAudio, speech_cache
from twicc.tts.openrouter import SpeechError, SpeechRequest, audio_duration_seconds
from twicc.tts.streams import get_stream
from twicc.tts.views import parse_range, parse_speech_request

SPEECH_PATH = "/api/tts/speech/"
MODELS_PATH = "/api/tts/models/"
STREAMS_LOGGER_NAME = "twicc.tts.streams"
GONE = {"error": "The audio is no longer available"}
CUT = {"error": "The audio stopped before the end"}

VALID_PAYLOAD = {
    "text": "Hello there",
    "model": "openai/gpt-4o-mini-tts",
    "voice": "alloy",
    "response_format": "mp3",
    "speed": 1.0,
    "instructions": "",
}
CACHED_PAYLOAD = {**VALID_PAYLOAD, "cache": True}
TEST_PAYLOAD = {**VALID_PAYLOAD, "config_id": "cfg-1"}


def _audio_path(audio_id: str) -> str:
    return f"/api/tts/audio/{audio_id}/"


def _ending_path(audio_id: str) -> str:
    return f"/api/tts/audio/{audio_id}/ending/"


class FakeSpeech:
    """The fake ``pump_speech``: it runs the queued items in order.

    An item is a sink call ``(name, *args)`` (it returns after ``finish`` or
    ``fail``), ``("raise", error)`` or ``("wait", event)`` (the next items wait
    for the event). With an empty queue it waits, as an upstream that sends
    nothing yet.
    """

    def __init__(self):
        self.queue = asyncio.Queue()
        self.calls = []
        self.cancelled = False

    def send(self, *item) -> None:
        self.queue.put_nowait(item)

    async def __call__(self, request, api_key, stream) -> None:
        self.calls.append((request, api_key))
        try:
            while True:
                name, *args = await self.queue.get()
                if name == "raise":
                    raise args[0]
                if name == "wait":
                    await args[0].wait()
                    continue
                getattr(stream, name)(*args)
                if name in ("finish", "fail"):
                    return
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Upstream:
    """What the views called: the pump, the last-test records and the cost lookups."""

    def __init__(self):
        self.pump = FakeSpeech()
        self.records: list[tuple[str, dict]] = []
        self.cost: list[tuple[tuple[str, ...], str]] = []
        self.on_costs = []

    async def record_last_test(self, config_id, update):
        self.records.append((config_id, update))

    def schedule_cost_log(self, generation_ids, api_key, on_cost=None):
        self.cost.append((tuple(generation_ids), api_key))
        self.on_costs.append(on_cost)


@pytest.fixture(autouse=True)
def _reset_streams_and_cache():
    streams._reset_for_tests()
    speech_cache.clear()
    yield
    streams._reset_for_tests()
    speech_cache.clear()


@pytest.fixture
def partial_ranges(monkeypatch):
    """Answer a ``Range`` read with a 206: the path kept selectable besides the shipped "full"."""
    monkeypatch.setattr(views, "RANGE_ANSWER", "partial")


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def upstream(monkeypatch):
    """The key is ``"k"``; the pump, the last-test record and the cost lookup are fakes."""
    fake = Upstream()
    monkeypatch.setattr(views, "openrouter_api_key", lambda: "k")
    # Through a lambda: a test can swap ``fake.pump`` for a fresh one.
    monkeypatch.setattr(views, "pump_speech", lambda request, api_key, stream: fake.pump(request, api_key, stream))
    monkeypatch.setattr(views, "_record_last_test", fake.record_last_test)
    monkeypatch.setattr(views, "schedule_cost_log", fake.schedule_cost_log)
    return fake


def _route_to_caplog(caplog, monkeypatch, name: str) -> None:
    """Route the *name* logger straight to caplog.

    settings_test's ``disable_existing_loggers: True`` can disable this logger,
    depending on the suite's import order.
    """
    logger = logging.getLogger(name)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])


@pytest.fixture
def views_log(caplog, monkeypatch):
    _route_to_caplog(caplog, monkeypatch, "twicc.tts.views")
    return caplog


@pytest.fixture
def streams_log(caplog, monkeypatch):
    _route_to_caplog(caplog, monkeypatch, STREAMS_LOGGER_NAME)
    caplog.set_level(logging.INFO, logger=STREAMS_LOGGER_NAME)
    return caplog


def _speech_lines(caplog) -> list[str]:
    """The end-of-speech log lines of the streams module, in order."""
    return [r.getMessage() for r in caplog.records if r.name == STREAMS_LOGGER_NAME and r.levelno == logging.INFO]


async def _within(awaitable, seconds: float = 1.0):
    """Await *awaitable*, failing the test instead of hanging."""
    async with asyncio.timeout(seconds):
        return await awaitable


async def _settle() -> None:
    """Let the ready tasks and callbacks run."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _until(predicate) -> None:
    """Return once *predicate()* holds (bound it with ``_within``)."""
    while not predicate():
        await asyncio.sleep(0)


async def _open(client, body=VALID_PAYLOAD):
    return await _within(client.post(SPEECH_PATH, data=orjson.dumps(body), content_type="application/json"))


async def _open_id(client, body=VALID_PAYLOAD) -> str:
    response = await _open(client, body)
    assert response.status_code == 200, response.content
    return response.json()["audio_id"]


async def _read(client, audio_id: str, range_header: str | None = None):
    headers = {} if range_header is None else {"Range": range_header}
    return await _within(client.get(_audio_path(audio_id), headers=headers))


async def _release(client, audio_id: str):
    return await _within(client.delete(_audio_path(audio_id)))


async def _ending(client, audio_id: str) -> dict:
    response = await _within(client.get(_ending_path(audio_id)))
    assert response.status_code == 200
    return response.json()


async def _chunks(response) -> list[bytes]:
    async def collect():
        return [chunk async for chunk in response.streaming_content]

    return await _within(collect())


async def _body(response) -> bytes:
    return b"".join(await _chunks(response))


async def _next(iterator) -> bytes:
    return await _within(anext(iterator))


def _post_json(client, body):
    return asyncio.run(_open(client, body))


# --- parse_speech_request -------------------------------------------------------


def test_parse_speech_request_builds_the_request():
    request = parse_speech_request({
        "text": "  Hi  ",
        "model": "  openai/gpt-4o-mini-tts ",
        "voice": " alloy ",
        "response_format": "pcm",
        "speed": 2,
        "instructions": " calm ",
    })
    assert request == SpeechRequest(
        text="  Hi  ",
        model="openai/gpt-4o-mini-tts",
        voice="alloy",
        response_format="pcm",
        speed=2.0,
        instructions="calm",
    )


def test_parse_speech_request_defaults():
    request = parse_speech_request({"text": "Hi", "model": "m", "response_format": "mp3"})
    assert request == SpeechRequest(
        text="Hi", model="m", voice="", response_format="mp3", speed=None, instructions=""
    )


TEXT_ERROR = "'text' must be a non-empty string"
MODEL_ERROR = "'model' must be a non-empty string"
FORMAT_ERROR = "'response_format' must be one of: mp3, pcm"
SPEED_ERROR = "'speed' must be a finite positive number"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "The body must be a JSON object"),
        ("text", "The body must be a JSON object"),
        (None, "The body must be a JSON object"),
        ({**VALID_PAYLOAD, "text": ""}, TEXT_ERROR),
        ({**VALID_PAYLOAD, "text": "   \n"}, TEXT_ERROR),
        ({**VALID_PAYLOAD, "text": 12}, TEXT_ERROR),
        ({key: value for key, value in VALID_PAYLOAD.items() if key != "model"}, MODEL_ERROR),
        ({**VALID_PAYLOAD, "model": "  "}, MODEL_ERROR),
        ({**VALID_PAYLOAD, "model": 3}, MODEL_ERROR),
        ({**VALID_PAYLOAD, "voice": 3}, "'voice' must be a string"),
        ({**VALID_PAYLOAD, "instructions": ["x"]}, "'instructions' must be a string"),
        ({**VALID_PAYLOAD, "response_format": "ogg"}, FORMAT_ERROR),
        ({key: value for key, value in VALID_PAYLOAD.items() if key != "response_format"}, FORMAT_ERROR),
        ({**VALID_PAYLOAD, "speed": 0}, SPEED_ERROR),
        ({**VALID_PAYLOAD, "speed": -1}, SPEED_ERROR),
        ({**VALID_PAYLOAD, "speed": True}, SPEED_ERROR),
        ({**VALID_PAYLOAD, "speed": "1.0"}, SPEED_ERROR),
        ({**VALID_PAYLOAD, "speed": float("inf")}, SPEED_ERROR),
        ({**VALID_PAYLOAD, "speed": float("nan")}, SPEED_ERROR),
        # orjson refuses such a number in a body; only a direct Python caller can pass it.
        pytest.param({**VALID_PAYLOAD, "speed": 10**400}, SPEED_ERROR, id="huge-int-direct-caller"),
    ],
)
def test_parse_speech_request_rejects(data, message):
    with pytest.raises(ValueError) as excinfo:
        parse_speech_request(data)
    assert str(excinfo.value) == message


# --- parse_range ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-", (0, None)),
        ("bytes=5-9", (5, 9)),
        ("bytes=5-5", (5, 5)),
        ("bytes=-5", None),
        ("items=0-", None),
        ("bytes=0-1,5-9", None),
        ("bytes=9-5", None),
        ("bytes=x-", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_range(header, expected):
    assert parse_range(header) == expected


# --- POST /api/tts/speech/: validation and errors -------------------------------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({key: value for key, value in VALID_PAYLOAD.items() if key != "text"}, TEXT_ERROR),
        ({**VALID_PAYLOAD, "model": "   "}, MODEL_ERROR),
        ({**VALID_PAYLOAD, "response_format": "ogg"}, FORMAT_ERROR),
        ({**VALID_PAYLOAD, "speed": 0}, SPEED_ERROR),
        ([VALID_PAYLOAD], "The body must be a JSON object"),
    ],
)
def test_rejects_invalid_payloads(client, upstream, monkeypatch, body, message):
    # An invalid payload is a 400 even without a key: validation runs first.
    monkeypatch.setattr(views, "openrouter_api_key", lambda: None)
    response = _post_json(client, body)
    assert response.status_code == 400
    assert response.json() == {"error": message}
    assert upstream.pump.calls == []


def test_rejects_invalid_json(client, upstream):
    response = asyncio.run(
        _within(client.post(SPEECH_PATH, data=b"{not json", content_type="application/json"))
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid JSON"}


def test_speech_rejects_other_methods(client, upstream):
    response = asyncio.run(_within(client.get(SPEECH_PATH)))
    assert response.status_code == 405
    assert response["Allow"] == "POST"
    assert isinstance(response.json()["error"], str)


def test_audio_routes_reject_other_methods(client, upstream):
    async def scenario():
        audio = await _within(client.post(_audio_path("some-id")))
        ending = await _within(client.delete(_ending_path("some-id")))
        return audio, ending

    audio, ending = asyncio.run(scenario())
    assert audio.status_code == 405
    assert audio["Allow"] == "GET, DELETE"
    assert isinstance(audio.json()["error"], str)
    assert ending.status_code == 405
    assert ending["Allow"] == "GET"
    assert isinstance(ending.json()["error"], str)


def test_no_key_returns_503_without_upstream_call(client, upstream, monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(views, "openrouter_api_key", lambda: None)
    monkeypatch.setattr(views, "get_env_path", lambda: env_path)
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 503
    assert response.json() == {
        "error": f"TWICC_OPENROUTER_API_KEY is not set. Add it to {env_path} and restart TwiCC."
    }
    assert upstream.pump.calls == []
    assert upstream.cost == []


def test_oversized_body_returns_413_json(client, upstream, settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 413
    assert response.json() == {"error": "The text is too long to send"}


def test_upstream_401_is_not_relayed_as_401(client, upstream):
    upstream.pump.send("fail", SpeechError("No auth credentials found", upstream_status=401))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 502
    assert response.json() == {"error": "No auth credentials found", "upstream_status": 401}
    assert upstream.cost == []
    assert streams._streams == {}


def test_upstream_error_without_status_has_no_upstream_status(client, upstream):
    upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 502
    assert response.json() == {"error": "Could not reach OpenRouter"}
    assert streams._streams == {}


def test_unexpected_exception_returns_own_json_500(client, upstream, settings, views_log, streams_log):
    settings.DEBUG = True
    # The pump raises before its headers.
    upstream.pump.send("raise", RuntimeError("sk-or-leak"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": "Text-to-speech failed"}
    assert b"sk-or-leak" not in response.content
    # One traceback: the pump's task logs it, the open view does not log it again.
    tracebacks = [record for record in views_log.records if record.exc_info]
    assert [record.name for record in tracebacks] == [STREAMS_LOGGER_NAME]
    assert tracebacks[0].exc_info[1].args == ("sk-or-leak",)
    assert streams._streams == {}


def test_cancellation_propagates(upstream, streams_log):
    # A client disconnect cancels the view task while it awaits the headers:
    # the catch-all must not swallow it, and the pump must stop.
    request = AsyncRequestFactory().post(
        SPEECH_PATH, data=orjson.dumps(VALID_PAYLOAD), content_type="application/json"
    )

    async def scenario():
        task = asyncio.create_task(views.tts_speech(request))
        await _settle()
        assert upstream.pump.calls  # the view awaits ``ready`` now
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _within(task)
        await _settle()

    asyncio.run(scenario())
    assert upstream.pump.cancelled
    assert streams._streams == {}
    [line] = _speech_lines(streams_log)
    assert line.endswith("outcome=cancelled")


# --- Open, read ----------------------------------------------------------------


def test_open_returns_the_audio_id_and_type(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        response = await _open(client)
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {"audio_id", "content_type"}
        assert isinstance(data["audio_id"], str) and data["audio_id"]
        assert data["content_type"] == "audio/mpeg"
        assert get_stream(data["audio_id"]) is not None
        assert upstream.pump.calls == [(parse_speech_request(VALID_PAYLOAD), "k")]
        upstream.pump.send("finish")
        await _settle()

    asyncio.run(scenario())


def test_open_waits_for_headers_only(client, upstream):
    async def scenario():
        gate = asyncio.Event()
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("wait", gate)
        # A chunk already queued: the pump holds it back until the open answered.
        upstream.pump.send("append", b"abc")
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        # The open answered while the upstream has sent no audio yet.
        stream = get_stream(audio_id)
        assert stream.state == "running"
        assert stream.data == b""
        gate.set()
        await _settle()
        assert stream.state == "complete"
        assert stream.data == b"abc"

    asyncio.run(scenario())


def test_read_streams_chunks_as_they_arrive(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"first")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        assert response.status_code == 200
        assert response["Content-Type"] == "audio/mpeg"
        assert response["Cache-Control"] == "no-store"
        assert not response.has_header("Accept-Ranges")
        assert not response.has_header("Content-Length")
        assert get_stream(audio_id).claimed
        chunks = aiter(response.streaming_content)
        assert await _next(chunks) == b"first"
        # The second chunk arrives after the first one has been yielded.
        upstream.pump.send("append", b"second")
        assert await _next(chunks) == b"second"
        upstream.pump.send("finish")
        with pytest.raises(StopAsyncIteration):
            await _next(chunks)

    asyncio.run(scenario())


def test_read_after_complete_has_a_content_length(client, upstream):
    audio = bytes(range(256)) * 600  # 153 600 bytes: three slices

    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", audio)
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        await _settle()
        response = await _read(client, audio_id)
        assert response.status_code == 200
        assert response["Content-Length"] == str(len(audio))
        chunks = await _chunks(response)
        assert [len(chunk) for chunk in chunks] == [65536, 65536, len(audio) - 2 * 65536]
        assert b"".join(chunks) == audio

    asyncio.run(scenario())


def test_range_read_serves_received_bytes_from_the_offset(client, upstream, partial_ranges):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"0123456789")
        audio_id = await _open_id(client)

        response = await _read(client, audio_id, "bytes=5-")
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 5-9/*"
        assert response["Accept-Ranges"] == "bytes"
        assert response["Cache-Control"] == "no-store"
        assert response["Content-Length"] == "5"
        # The body ends at the last received byte: the stream still runs.
        assert await _body(response) == b"56789"

        response = await _read(client, audio_id, "bytes=2-4")
        assert response["Content-Range"] == "bytes 2-4/*"
        assert response["Content-Length"] == "3"
        assert await _body(response) == b"234"

        # From the end of the received bytes: the read waits for more.
        waiting = asyncio.create_task(_read(client, audio_id, "bytes=10-"))
        await _settle()
        assert not waiting.done()
        upstream.pump.send("append", b"abc")
        response = await _within(waiting)
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 10-12/*"
        assert response["Content-Length"] == "3"
        assert await _body(response) == b"abc"

        upstream.pump.send("finish")
        await _settle()
        response = await _read(client, audio_id, "bytes=5-")
        assert response["Content-Range"] == "bytes 5-12/13"
        assert response["Content-Length"] == "8"
        assert await _body(response) == b"56789abc"

    asyncio.run(scenario())


def test_range_beyond_a_complete_audio_is_416(client, upstream, partial_ranges):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"0123456789")
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        await _settle()
        response = await _read(client, audio_id, "bytes=10-")
        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */10"

    asyncio.run(scenario())


def test_range_answer_is_full():
    # The spike's decision (plan, Task 1): the tests below run with this real value.
    assert views.RANGE_ANSWER == "full"


def test_range_read_is_a_plain_read(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"0123456789")
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        await _settle()
        response = await _read(client, audio_id, "bytes=5-")
        assert response.status_code == 200
        assert not response.has_header("Accept-Ranges")
        assert not response.has_header("Content-Range")
        assert response["Content-Length"] == "10"
        assert await _body(response) == b"0123456789"

    asyncio.run(scenario())


def test_range_read_of_a_running_stream_streams_from_the_start(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"0123456789")
        audio_id = await _open_id(client)
        # The first read of Chrome on Android: a plain streamed 200, never a short 206.
        response = await _read(client, audio_id, "bytes=0-")
        assert response.status_code == 200
        assert not response.has_header("Accept-Ranges")
        assert not response.has_header("Content-Range")
        assert not response.has_header("Content-Length")
        chunks = aiter(response.streaming_content)
        assert await _next(chunks) == b"0123456789"
        upstream.pump.send("append", b"abc")
        assert await _next(chunks) == b"abc"
        upstream.pump.send("finish")
        with pytest.raises(StopAsyncIteration):
            await _next(chunks)
        # A resumed read from an offset: the whole audio again, from its start.
        response = await _read(client, audio_id, "bytes=5-")
        assert response.status_code == 200
        assert response["Content-Length"] == "13"
        assert await _body(response) == b"0123456789abc"

    asyncio.run(scenario())


def test_no_reader_does_not_stop_the_upstream(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        assert await _next(chunks) == b"abc"
        await _within(chunks.aclose())  # the reader goes away early
        upstream.pump.send("append", b"def")
        upstream.pump.send("finish")
        await _settle()
        assert await _ending(client, audio_id) == {"state": "complete", "error": None}
        assert get_stream(audio_id).data == b"abcdef"

    asyncio.run(scenario())
    assert not upstream.pump.cancelled


def test_upstream_failure_cuts_the_read(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        assert await _next(chunks) == b"abc"
        upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
        with pytest.raises(ConnectionAbortedError):
            await _next(chunks)
        assert await _ending(client, audio_id) == {"state": "failed", "error": "Could not reach OpenRouter"}

    asyncio.run(scenario())


def test_read_of_a_failed_stream_is_410_json(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
        audio_id = await _open_id(client)
        await _settle()
        # A retried read after the cut: an error, never the same bytes then the same cut.
        for range_header in (None, "bytes=0-", "bytes=1-"):
            response = await _read(client, audio_id, range_header)
            assert response.status_code == 410
            assert response["Content-Type"] == "application/json"
            assert response.json() == CUT
        assert await _ending(client, audio_id) == {"state": "failed", "error": "Could not reach OpenRouter"}

    asyncio.run(scenario())


def test_range_read_of_a_failed_stream_is_410_json(client, upstream, partial_ranges):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client)
        stream = get_stream(audio_id)
        # A range read that waits past the received bytes when the failure happens.
        waiting = asyncio.create_task(_read(client, audio_id, "bytes=3-"))
        # The view claims the stream, then waits on it in the same step.
        await _within(_until(lambda: stream.claimed))
        await _settle()
        assert not waiting.done()
        # A range answer given before the failure: its body holds received bytes only, never cut.
        answered = await _read(client, audio_id, "bytes=1-")
        assert answered.status_code == 206
        upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
        response = await _within(waiting)
        assert response.status_code == 410
        assert response.json() == CUT
        assert await _body(answered) == b"bc"
        # Range reads after the failure, within and past the received bytes.
        for range_header in ("bytes=1-", "bytes=3-"):
            response = await _read(client, audio_id, range_header)
            assert response.status_code == 410
            assert response["Content-Type"] == "application/json"
            assert response.json() == CUT

    asyncio.run(scenario())


# --- Release, ending, unknown ids -----------------------------------------------


def test_release_before_any_read_cancels_the_upstream(client, upstream, streams_log):
    async def scenario():
        for body in (CACHED_PAYLOAD, TEST_PAYLOAD):
            upstream.pump = FakeSpeech()
            upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
            upstream.pump.send("append", b"abc")
            audio_id = await _open_id(client, body)
            response = await _release(client, audio_id)
            assert response.status_code == 204
            await _settle()
            assert upstream.pump.cancelled
            assert get_stream(audio_id) is None

    asyncio.run(scenario())
    assert speech_cache.get(parse_speech_request(VALID_PAYLOAD)) is None
    assert upstream.records == []
    assert upstream.cost == []
    lines = _speech_lines(streams_log)
    assert len(lines) == 2
    assert all(line.endswith("outcome=released") for line in lines)


def test_release_ends_a_connected_read(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        assert await _next(chunks) == b"abc"
        waiting = asyncio.create_task(_next(chunks))
        await _settle()
        assert not waiting.done()
        assert (await _release(client, audio_id)).status_code == 204
        with pytest.raises(StopAsyncIteration):
            await _within(waiting)

    asyncio.run(scenario())
    assert upstream.pump.cancelled


def test_release_after_complete_still_records_and_caches(client, upstream, monkeypatch):
    gate = None

    async def slow_record(config_id, update):
        await gate.wait()
        upstream.records.append((config_id, update))

    async def scenario():
        nonlocal gate
        gate = asyncio.Event()
        # A cached read: released at once after its completion.
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"cached")
        audio_id = await _open_id(client, CACHED_PAYLOAD)
        upstream.pump.send("finish")
        await _within(_until(lambda: get_stream(audio_id).state == "complete"))
        assert (await _release(client, audio_id)).status_code == 204
        await _settle()
        assert speech_cache.get(parse_speech_request(VALID_PAYLOAD)) == CachedAudio(b"cached", "audio/mpeg")

        # A settings test: released while its end steps still run.
        upstream.pump = FakeSpeech()
        monkeypatch.setattr(views, "_record_last_test", slow_record)
        upstream.pump.send("set_headers", "audio/mpeg", "gen-2")
        upstream.pump.send("append", b"tested")
        audio_id = await _open_id(client, TEST_PAYLOAD)
        upstream.pump.send("finish")
        await _settle()
        assert upstream.records == []  # the record step waits on the gate
        assert (await _release(client, audio_id)).status_code == 204
        gate.set()
        await _settle()

    asyncio.run(scenario())
    [(config_id, run)] = upstream.records
    assert config_id == "cfg-1"
    assert run["generation_id"] == "gen-2"
    assert upstream.cost == [(("gen-1",), "k"), (("gen-2",), "k")]


def test_read_ending_and_release_rearm_the_safety_timer(client, upstream, monkeypatch):
    touched = []
    touch = streams.AudioStream.touch

    def spy(stream):
        touched.append(stream.id)
        touch(stream)

    monkeypatch.setattr(streams.AudioStream, "touch", spy)

    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        await _settle()
        stream = get_stream(audio_id)
        timers = [stream._safety_timer]

        async def range_read():
            # The range branch runs only with "partial": with the shipped "full", it is a plain read.
            with monkeypatch.context() as patch:
                patch.setattr(views, "RANGE_ANSWER", "partial")
                response = await _read(client, audio_id, "bytes=1-")
            assert response.status_code == 206
            return response

        for step in (
            lambda: _read(client, audio_id),
            range_read,
            lambda: _ending(client, audio_id),
        ):
            touched.clear()
            await step()
            assert touched == [audio_id]
            # A new timer replaced the previous one.
            assert stream._safety_timer is not timers[-1]
            assert timers[-1].cancelled()
            timers.append(stream._safety_timer)
        touched.clear()
        await _release(client, audio_id)
        assert touched == [audio_id]

    asyncio.run(scenario())


def test_release_of_an_unknown_id_is_204(client, upstream):
    response = asyncio.run(_release(client, "unknown-id"))
    assert response.status_code == 204
    assert response.content == b""


def test_read_of_an_unknown_id_is_404_json(client, upstream):
    async def scenario():
        plain = await _read(client, "unknown-id")
        ranged = await _read(client, "unknown-id", "bytes=0-")
        return plain, ranged

    for response in asyncio.run(scenario()):
        assert response.status_code == 404
        assert response["Content-Type"] == "application/json"
        assert response.json() == GONE


def test_range_read_released_while_it_waits_is_404(client, upstream, partial_ranges):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client)
        stream = get_stream(audio_id)
        waiting = asyncio.create_task(_read(client, audio_id, "bytes=3-"))
        # The view claims the stream, then waits on it in the same step.
        await _within(_until(lambda: stream.claimed))
        await _settle()
        assert not waiting.done()
        await _release(client, audio_id)
        response = await _within(waiting)
        assert response.status_code == 404
        assert response.json() == GONE
        # A plain read after the release: the id is gone.
        assert (await _read(client, audio_id)).status_code == 404

    asyncio.run(scenario())


def test_ending_of_an_unknown_id_is_unknown(client, upstream):
    assert asyncio.run(_ending(client, "unknown-id")) == {"state": "unknown", "error": None}


def test_ending_answers_running_while_the_speech_runs(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        audio_id = await _open_id(client)
        assert await _ending(client, audio_id) == {"state": "running", "error": None}
        upstream.pump.send("finish")
        await _settle()
        assert await _ending(client, audio_id) == {"state": "complete", "error": None}

    asyncio.run(scenario())


# --- Cache ---------------------------------------------------------------------


def test_cache_hit_serves_without_upstream(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("finish")
        first = await _open_id(client, CACHED_PAYLOAD)
        await _settle()
        assert len(upstream.pump.calls) == 1

        response = await _open(client, CACHED_PAYLOAD)
        assert response.status_code == 200
        assert response.json()["content_type"] == "audio/mpeg"
        second = response.json()["audio_id"]
        assert second != first
        assert len(upstream.pump.calls) == 1  # no upstream call
        read = await _read(client, second)
        assert read["Content-Length"] == "3"
        assert await _body(read) == b"abc"

        # Without ``cache``, the cache is never read.
        upstream.pump.send("set_headers", "audio/mpeg", "gen-3")
        upstream.pump.send("finish")
        await _open_id(client)
        await _settle()
        assert len(upstream.pump.calls) == 2

    asyncio.run(scenario())
    assert upstream.cost == [(("gen-1",), "k"), (("gen-3",), "k")]  # a cache hit costs nothing


def test_a_cache_hit_survives_eviction(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("finish")
        await _open_id(client, CACHED_PAYLOAD)
        await _settle()
        audio_id = await _open_id(client, CACHED_PAYLOAD)
        speech_cache.clear()
        assert await _body(await _read(client, audio_id)) == b"abc"

    asyncio.run(scenario())


def test_a_settings_test_never_uses_the_cache(client, upstream):
    request = parse_speech_request(VALID_PAYLOAD)
    body = {**TEST_PAYLOAD, "cache": True}

    async def scenario():
        # No fill.
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"fresh")
        upstream.pump.send("finish")
        await _open_id(client, body)
        await _settle()
        assert speech_cache.get(request) is None

        # No read.
        speech_cache.set(request, CachedAudio(b"cached", "audio/mpeg"))
        upstream.pump.send("set_headers", "audio/mpeg", "gen-2")
        upstream.pump.send("append", b"fresh")
        upstream.pump.send("finish")
        audio_id = await _open_id(client, body)
        await _settle()
        assert len(upstream.pump.calls) == 2
        assert await _body(await _read(client, audio_id)) == b"fresh"
        assert speech_cache.get(request) == CachedAudio(b"cached", "audio/mpeg")

    asyncio.run(scenario())


def test_a_failed_or_released_audio_is_not_cached(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
        await _open_id(client, CACHED_PAYLOAD)
        await _settle()

        upstream.pump.send("set_headers", "audio/mpeg", "gen-2")
        upstream.pump.send("append", b"abc")
        audio_id = await _open_id(client, CACHED_PAYLOAD)
        await _release(client, audio_id)
        await _settle()

        upstream.pump = FakeSpeech()
        upstream.pump.send("set_headers", "audio/mpeg", "gen-3")
        upstream.pump.send("finish")
        await _open_id(client, CACHED_PAYLOAD)
        await _settle()
        assert len(upstream.pump.calls) == 1  # no cache hit

    asyncio.run(scenario())
    assert upstream.cost == [(("gen-3",), "k")]  # a failed or released audio has no cost lookup


# --- End of speech: last test run, cost lookup ----------------------------------


def test_an_empty_complete_audio_is_not_cached(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("finish")
        await _open_id(client, CACHED_PAYLOAD)
        await _settle()
        assert speech_cache.get(parse_speech_request(VALID_PAYLOAD)) is None

        upstream.pump.send("set_headers", "audio/mpeg", "gen-2")
        upstream.pump.send("append", b"abc")
        upstream.pump.send("finish")
        await _open_id(client, CACHED_PAYLOAD)
        await _settle()
        assert len(upstream.pump.calls) == 2  # no cache hit

    asyncio.run(scenario())


def test_a_complete_audio_schedules_the_cost_lookup(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"ID3audio")
        await _open_id(client)
        await _settle()
        assert upstream.cost == []  # not at the open: at the complete audio
        upstream.pump.send("finish")
        await _settle()

    asyncio.run(scenario())
    assert upstream.cost == [(("gen-1",), "k")]
    assert upstream.on_costs == [None]  # a message read records no cost


def test_success_without_generation_id_schedules_no_cost(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", None)
        upstream.pump.send("append", b"ID3audio")
        upstream.pump.send("finish")
        await _open_id(client)
        await _settle()

    asyncio.run(scenario())
    assert upstream.cost == []


def test_a_settings_test_records_its_run_then_its_cost(client, upstream, monkeypatch):
    content_type = "audio/pcm;rate=24000;channels=1"
    audio = b"\x00\x00" * 24000  # one second
    measured = []

    def measure(content, content_type):
        measured.append(content)
        return audio_duration_seconds(content, content_type)

    monkeypatch.setattr(views, "audio_duration_seconds", measure)

    async def scenario():
        upstream.pump.send("set_headers", content_type, "gen-1")
        upstream.pump.send("append", audio)
        audio_id = await _open_id(client, TEST_PAYLOAD)
        await _settle()
        assert upstream.records == []  # not at the open: at the complete audio
        upstream.pump.send("finish")
        await _settle()
        stream = get_stream(audio_id)
        [(config_id, run)] = upstream.records
        assert config_id == "cfg-1"
        assert set(run) == {
            "at", "generation_id", "chars", "generation_ms", "first_audio_ms", "audio_seconds", "cost"
        }
        assert run["generation_id"] == "gen-1"
        assert run["chars"] == len(VALID_PAYLOAD["text"])
        assert run["generation_ms"] == stream.generation_ms
        assert run["first_audio_ms"] == stream.first_audio_ms
        assert run["first_audio_ms"] is not None
        assert run["audio_seconds"] == audio_duration_seconds(audio, content_type) == 1.0
        assert measured == [stream.data]
        assert measured[0] is stream.data  # no copy of the audio
        assert run["cost"] is None
        # The cost callback updates the same run once the lookup finds it.
        assert upstream.cost == [(("gen-1",), "k")]
        await _within(upstream.on_costs[0](0.008))
        assert upstream.records[-1] == ("cfg-1", {"generation_id": "gen-1", "cost": 0.008})

    asyncio.run(scenario())


def test_a_message_read_records_no_run(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"ID3audio")
        upstream.pump.send("finish")
        await _open_id(client)
        await _settle()

    asyncio.run(scenario())
    assert upstream.records == []


def test_on_end_steps_fail_independently(client, upstream, monkeypatch, views_log):
    # The audio is billed already: a failing end step must not skip the others.
    class BrokenCache:
        def get(self, key):
            return None

        def set(self, key, audio):
            raise RuntimeError("cache full")

    async def broken_record(config_id, update):
        raise RuntimeError("disk full")

    monkeypatch.setattr(views, "speech_cache", BrokenCache())
    monkeypatch.setattr(views, "_record_last_test", broken_record)

    async def scenario():
        audio_ids = []
        # A failing cache step (a cached message read), then a failing record step (a settings test).
        for body, generation_id in ((CACHED_PAYLOAD, "gen-1"), (TEST_PAYLOAD, "gen-2")):
            upstream.pump.send("set_headers", "audio/mpeg", generation_id)
            upstream.pump.send("append", b"ID3audio")
            upstream.pump.send("finish")
            audio_ids.append(await _open_id(client, body))
            await _settle()
        assert upstream.cost == [(("gen-1",), "k"), (("gen-2",), "k")]

        # A failing cost step: the audio stays readable.
        def broken_cost_log(generation_ids, api_key, on_cost=None):
            raise RuntimeError("no running loop")

        monkeypatch.setattr(views, "schedule_cost_log", broken_cost_log)
        upstream.pump.send("set_headers", "audio/mpeg", "gen-3")
        upstream.pump.send("append", b"ID3audio")
        upstream.pump.send("finish")
        audio_ids.append(await _open_id(client))
        await _settle()
        for audio_id in audio_ids:
            assert await _body(await _read(client, audio_id)) == b"ID3audio"

    asyncio.run(scenario())
    errors = [r.getMessage() for r in views_log.records if r.name == "twicc.tts.views" and r.exc_info]
    assert errors == [
        "TTS audio could not be cached",
        "TTS last test of cfg-1 could not be recorded",
        "TTS cost lookup for gen-3 could not be scheduled",
    ]


# --- GET /api/tts/models/ -------------------------------------------------------


def test_models_endpoint(client, monkeypatch):
    models = [{"id": "openai/gpt-4o-mini-tts", "name": "GPT-4o mini TTS", "voices": ["alloy"]}]

    async def _fetch():
        return models

    monkeypatch.setattr(views, "fetch_speech_models", _fetch)
    response = asyncio.run(_within(client.get(MODELS_PATH)))
    assert response.status_code == 200
    assert response.json() == {"models": models}


def test_models_rejects_other_methods(client):
    response = asyncio.run(_within(client.post(MODELS_PATH)))
    assert response.status_code == 405
    assert response["Allow"] == "GET"
