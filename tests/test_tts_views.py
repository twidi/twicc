"""Tests for the text-to-speech REST endpoints (twicc.tts.views).

The upstream calls are always mocked. Speech preparation dependencies are patched
on the speech service. ``fetch_speech_models`` is patched on the views module.

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

from twicc.core.enums import Provider
from twicc.openrouter_text import OpenRouterTextError
from twicc.paths import get_tts_config_path
from twicc.tts import adapt, model_catalog, service as speech_service, streams, views
from twicc.tts.audio_cache import CachedAudio, speech_cache
from twicc.tts.config import read_tts_config, write_tts_config
from twicc.tts.openrouter import SpeechError, SpeechRequest, audio_duration_seconds
from twicc.tts.part_source import as_part_source
from twicc.tts.parts import has_speech
from twicc.tts.script import ScriptSpeechRequest, script_parts
from twicc.tts.simple_text import direct_text
from twicc.tts.streams import get_stream
from twicc.tts.views import parse_range
from twicc.tts.service import (
    NO_COMMAND,
    NO_PCM_RATE,
    NO_SUCH_SCRIPT,
    NOT_SAVED,
    NOT_VALIDATED,
    NOTHING_TO_SAY,
    parse_speech_request,
)

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
# 1,000 samples of 2 bytes a second: 2,000 bytes are one second of audio.
PACED_TYPE = "audio/pcm;rate=1000;channels=1"
PACED_AUDIO = bytes(range(250)) * 8  # 2,000 bytes


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
    """What the views called: the pump (and its parts and pacing), the last-test records and the cost lookups."""

    def __init__(self):
        self.pump = FakeSpeech()
        self.pump_args: list[tuple[list[str] | None, bool]] = []
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


@pytest.fixture(autouse=True)
def _no_adaptation_model(monkeypatch):
    """No provider runs, and no view test reaches a real model: the adaptation call fails the test.

    A test that adapts a text swaps both (the ``adaptation`` fixture). The call runs in a
    background task, which would swallow a raise: the teardown checks the calls instead.
    """
    calls = []

    async def open_stream(engine, prompt):
        calls.append(engine.name)
        raise AssertionError("A view test reached the real adaptation call")
        yield  # an async generator, like the real one

    monkeypatch.setattr(adapt, "_open_stream", open_stream)
    monkeypatch.setattr(adapt, "is_provider_running", lambda provider: False)
    # No text is simple for the existing tests: they keep the engine, the reason and the cache key they
    # check. The direct reading has its own tests (the ``direct_reading`` fixture undoes this patch).
    monkeypatch.setattr(speech_service, "direct_text", lambda text, origin=None: None)
    yield
    assert calls == [], "A view test reached the real adaptation call"


@pytest.fixture
def fast_pacing(monkeypatch):
    """A paced read delivers 0.1 s ahead of the playback position, and wakes up every 0.05 s at least."""
    monkeypatch.setattr(streams, "PACE_MARGIN", 0.1)
    monkeypatch.setattr(streams, "PACE_TICK", 0.05)


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
    monkeypatch.setattr(speech_service, "openrouter_api_key", lambda: "k")

    # Through a function: a test can swap ``fake.pump`` for a fresh one.
    def pump_speech(request, api_key, stream, parts=None, *, paced=False):
        fake.pump_args.append((parts, paced))
        return fake.pump(request, api_key, stream)

    monkeypatch.setattr(speech_service, "pump_speech", pump_speech)
    monkeypatch.setattr(speech_service, "_record_last_test", fake.record_last_test)
    monkeypatch.setattr(speech_service, "schedule_cost_log", fake.schedule_cost_log)
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
    _route_to_caplog(caplog, monkeypatch, "twicc.tts.service")
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


async def _collect(chunks) -> bytes:
    """The rest of an already started body (bound it with ``_within``)."""
    return b"".join([chunk async for chunk in chunks])


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
        ({**VALID_PAYLOAD, "text": "👍"}, NOTHING_TO_SAY),
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
    monkeypatch.setattr(speech_service, "openrouter_api_key", lambda: None)
    response = _post_json(client, body)
    assert response.status_code == 400
    assert response.json() == {"error": message}
    assert upstream.pump.calls == []


def test_a_text_with_nothing_to_say_is_400_without_upstream(client, upstream):
    response = _post_json(client, {**VALID_PAYLOAD, "text": "…"})
    assert response.status_code == 400
    assert response.json() == {"error": "There is nothing to read aloud in this text"}
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
    monkeypatch.setattr(speech_service, "openrouter_api_key", lambda: None)
    monkeypatch.setattr(speech_service, "get_env_path", lambda: env_path)
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
    assert response.status_code == 424
    assert response.json() == {"error": "No auth credentials found", "upstream_status": 401}
    assert upstream.cost == []
    assert streams._streams == {}


def test_upstream_error_without_status_has_no_upstream_status(client, upstream):
    upstream.pump.send("fail", SpeechError("Could not reach OpenRouter"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 424
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
        # The open step reads the stored adaptation choice in a thread first.
        await _within(_until(lambda: upstream.pump.calls))  # the view awaits ``ready`` now
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


def test_open_passes_the_parts_and_pacing_to_the_pump(client, upstream, monkeypatch):
    async def run(body):
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("finish")
        await _open_id(client, body)
        await _settle()

    async def scenario():
        with monkeypatch.context() as patch:
            patch.setattr(speech_service, "speech_parts", lambda request: ["a", "b"])
            await run(VALID_PAYLOAD)
            await run({**VALID_PAYLOAD, "buffered": True})
        await run(VALID_PAYLOAD)

    asyncio.run(scenario())
    # The pump paces a split reading only: a one-part reading is asked to pace too.
    assert upstream.pump_args == [
        (["a", "b"], True),
        (["a", "b"], False),
        ([VALID_PAYLOAD["text"]], True),
    ]


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


def test_range_read_of_a_paced_stream_serves_the_deliverable_bytes(client, upstream, partial_ranges, fast_pacing):
    async def scenario():
        upstream.pump.send("set_headers", PACED_TYPE, "gen-1")
        upstream.pump.send("pace")
        upstream.pump.send("append", PACED_AUDIO)
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id, "bytes=0-")
        assert response.status_code == 206
        last = int(response["Content-Length"]) - 1
        # The received bytes of a paced stream are its deliverable ones; the total stays the whole audio.
        assert 200 <= last + 1 <= 600
        assert response["Content-Range"] == f"bytes 0-{last}/{len(PACED_AUDIO)}"
        assert await _body(response) == PACED_AUDIO[: last + 1]

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


def test_a_paced_read_delivers_the_audio_as_the_playback_advances(client, upstream, fast_pacing):
    async def scenario():
        upstream.pump.send("set_headers", PACED_TYPE, "gen-1")
        upstream.pump.send("pace")
        upstream.pump.send("append", PACED_AUDIO)
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        first = await _next(chunks)
        # About PACE_MARGIN (0.1 s, 200 bytes) ahead of the playback position, not the whole audio.
        assert 200 <= len(first) <= 600
        rest = await _within(_collect(chunks), 2.0)
        return first + rest

    assert asyncio.run(scenario()) == PACED_AUDIO


def test_a_second_read_of_a_paced_stream_gets_the_deliverable_bytes_at_once(client, upstream, fast_pacing):
    async def scenario():
        upstream.pump.send("set_headers", PACED_TYPE, "gen-1")
        upstream.pump.send("pace")
        upstream.pump.send("append", PACED_AUDIO)
        upstream.pump.send("finish")
        audio_id = await _open_id(client)
        stream = get_stream(audio_id)
        first_read = asyncio.create_task(_within(_body(await _read(client, audio_id)), 2.0))
        await asyncio.sleep(0.5)
        # A media element that reads the stream again from its start (Review Focus 3).
        deliverable = stream.deliverable()
        assert 0 < deliverable < len(PACED_AUDIO)
        second = await _read(client, audio_id)
        chunks = aiter(second.streaming_content)
        received = b""
        async with asyncio.timeout(0.1):
            while len(received) < deliverable:
                received += await anext(chunks)
        assert received == PACED_AUDIO[: len(received)]
        rest = await _within(_collect(chunks), 2.0)
        return await first_read, received + rest

    first, second = asyncio.run(scenario())
    assert first == second == PACED_AUDIO


def test_a_later_part_failure_delivers_the_received_audio_then_cuts(client, upstream, fast_pacing):
    async def scenario():
        upstream.pump.send("begin_part")
        upstream.pump.send("set_headers", PACED_TYPE, "g1")
        upstream.pump.send("pace")
        upstream.pump.send("append", PACED_AUDIO)
        upstream.pump.send("end_part")
        upstream.pump.send("begin_part")
        audio_id = await _open_id(client)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        received = await _next(chunks)
        # The second part fails while the read still delivers the first part's audio.
        upstream.pump.send("fail", SpeechError("Rate limited", upstream_status=429))
        await _settle()
        assert await _ending(client, audio_id) == {"state": "failed", "error": "Rate limited"}
        retried = await _read(client, audio_id)
        assert retried.status_code == 410
        assert retried.json() == CUT
        with pytest.raises(ConnectionAbortedError):
            async with asyncio.timeout(2.0):
                async for chunk in chunks:
                    received += chunk
        return received

    assert asyncio.run(scenario()) == PACED_AUDIO


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
    assert speech_cache.get((parse_speech_request(VALID_PAYLOAD), None)) is None
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
        assert speech_cache.get((parse_speech_request(VALID_PAYLOAD), None)) == CachedAudio(b"cached", "audio/mpeg")

        # A settings test: released while its end steps still run.
        upstream.pump = FakeSpeech()
        monkeypatch.setattr(speech_service, "_record_last_test", slow_record)
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
    key = (parse_speech_request(VALID_PAYLOAD), None)
    body = {**TEST_PAYLOAD, "cache": True}

    async def scenario():
        # No fill.
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"fresh")
        upstream.pump.send("finish")
        await _open_id(client, body)
        await _settle()
        assert speech_cache.get(key) is None

        # No read.
        speech_cache.set(key, CachedAudio(b"cached", "audio/mpeg"))
        upstream.pump.send("set_headers", "audio/mpeg", "gen-2")
        upstream.pump.send("append", b"fresh")
        upstream.pump.send("finish")
        audio_id = await _open_id(client, body)
        await _settle()
        assert len(upstream.pump.calls) == 2
        assert await _body(await _read(client, audio_id)) == b"fresh"
        assert speech_cache.get(key) == CachedAudio(b"cached", "audio/mpeg")

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
        assert speech_cache.get((parse_speech_request(VALID_PAYLOAD), None)) is None

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

    monkeypatch.setattr(speech_service, "audio_duration_seconds", measure)

    async def scenario():
        upstream.pump.send("begin_part")
        upstream.pump.send("set_headers", content_type, "gen-1")
        upstream.pump.send("append", audio)
        audio_id = await _open_id(client, TEST_PAYLOAD)
        await _settle()
        assert upstream.records == []  # not at the open: at the complete audio
        upstream.pump.send("end_part")
        upstream.pump.send("finish")
        await _settle()
        stream = get_stream(audio_id)
        [(config_id, run)] = upstream.records
        assert config_id == "cfg-1"
        assert set(run) == {
            "at", "generation_id", "chars", "generation_ms", "first_audio_ms", "audio_seconds", "cost", "parts",
            "adapt_engine", "adapted_by", "adapt_reason", "adapt_first_ms", "adapted_text", "fallback_reason",
        }
        # No provider runs in these tests: the text is read as written.
        assert (run["adapt_engine"], run["adapted_by"], run["adapt_reason"], run["adapt_first_ms"], run["adapted_text"]) == (
            None, None, "no_provider", None, None
        )
        assert run["generation_id"] == "gen-1"
        assert run["parts"] == 1
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


def test_a_pump_without_part_marks_counts_as_one_part(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("append", b"ID3audio")
        upstream.pump.send("finish")
        await _open_id(client, TEST_PAYLOAD)
        await _settle()

    asyncio.run(scenario())
    [(_config_id, run)] = upstream.records
    assert run["parts"] == 1
    assert upstream.cost == [(("gen-1",), "k")]
    assert upstream.on_costs[0] is not None


def _send_parts(pump, generation_ids) -> None:
    """Queue a speech of one part per id (None: a part without id), as the real pump sends it."""
    for index, generation_id in enumerate(generation_ids):
        pump.send("begin_part")
        if index:
            pump.send("add_part_headers", "audio/mpeg", generation_id)
        else:
            pump.send("set_headers", "audio/mpeg", generation_id)
        pump.send("append", b"part%d" % index)
        pump.send("end_part")
    pump.send("finish")


def test_a_split_run_records_its_parts_and_the_summed_cost(client, upstream):
    async def scenario():
        _send_parts(upstream.pump, ["g1", "g2", "g3"])
        await _open_id(client, TEST_PAYLOAD)
        await _settle()
        [(config_id, run)] = upstream.records
        assert config_id == "cfg-1"
        assert run["parts"] == 3
        assert run["generation_id"] == "g1"
        assert upstream.cost == [(("g1", "g2", "g3"), "k")]
        # The lookup gives the total of the three parts: it updates the run of the first id.
        await _within(upstream.on_costs[0](0.006))
        assert upstream.records[-1] == ("cfg-1", {"generation_id": "g1", "cost": 0.006})

    asyncio.run(scenario())


def test_a_part_without_generation_id_records_no_cost(client, upstream):
    async def scenario():
        _send_parts(upstream.pump, ["g1", None, "g3"])
        await _open_id(client, TEST_PAYLOAD)
        await _settle()

    asyncio.run(scenario())
    [(_config_id, run)] = upstream.records
    assert run["parts"] == 3
    # The two known costs are still logged, but their sum is not the run's cost.
    assert upstream.cost == [(("g1", "g3"), "k")]
    assert upstream.on_costs == [None]


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

    monkeypatch.setattr(speech_service, "speech_cache", BrokenCache())
    monkeypatch.setattr(speech_service, "_record_last_test", broken_record)

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

        monkeypatch.setattr(speech_service, "schedule_cost_log", broken_cost_log)
        upstream.pump.send("set_headers", "audio/mpeg", "gen-3")
        upstream.pump.send("append", b"ID3audio")
        upstream.pump.send("finish")
        audio_ids.append(await _open_id(client))
        await _settle()
        for audio_id in audio_ids:
            assert await _body(await _read(client, audio_id)) == b"ID3audio"

    asyncio.run(scenario())
    errors = [r.getMessage() for r in views_log.records if r.name == "twicc.tts.service" and r.exc_info]
    assert errors == [
        "TTS audio could not be cached",
        "TTS last test of cfg-1 could not be recorded",
        "TTS cost lookup for gen-3 could not be scheduled",
    ]


# --- Script configs: the open step ----------------------------------------------

SCRIPT_ID = "scr-1"
SCRIPT_FIELDS = {
    "command": "say",
    "output": "stream",
    "audio_format": "mp3",
    "pcm_rate": None,
    "pcm_channels": 1,
    "max_chars": None,
}
SCRIPT_READ = {"kind": "script", "text": "Hello there", "script_id": SCRIPT_ID, **SCRIPT_FIELDS}
SCRIPT_CACHED_READ = {**SCRIPT_READ, "cache": True}
SCRIPT_TEST = {**SCRIPT_READ, "config_id": SCRIPT_ID}
CONFIG_ID_ERROR = "'config_id' must equal 'script_id'"
SCRIPT_ID_ERROR = "'script_id' must be a non-empty string"


@pytest.fixture
def tts_json():
    """The isolated test data dir, without a ``tts.json`` before and after the test."""
    path = get_tts_config_path()
    path.unlink(missing_ok=True)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def script_upstream(monkeypatch, tts_json):
    """The script pump, the last-test record and the cost lookup are fakes. The pump runs
    ``fake.pump`` (with no key), records ``(request, parts, paced)`` in ``fake.script_runs`` and
    the ``delete_file`` keyword (required: the view must pass it) in ``fake.delete_files``."""
    fake = Upstream()
    fake.script_runs = []
    fake.delete_files = []

    def pump_script_speech(request, stream, parts=None, *, paced=False, delete_file):
        fake.script_runs.append((request, parts, paced))
        fake.delete_files.append(delete_file)
        return fake.pump(request, None, stream)

    monkeypatch.setattr(speech_service, "pump_script_speech", pump_script_speech)
    monkeypatch.setattr(speech_service, "_record_last_test", fake.record_last_test)
    monkeypatch.setattr(speech_service, "schedule_cost_log", fake.schedule_cost_log)
    return fake


def _store_script(config_id: str = SCRIPT_ID, validated: object = True, delete_file: object = None, **overrides) -> dict:
    """Write one script config to tts.json; *validated* True: its snapshot is its fields.
    *delete_file* None: the entry has none (the default applies)."""
    fields = {**SCRIPT_FIELDS, **overrides}
    snapshot = fields if validated is True else validated
    extra = {} if delete_file is None else {"delete_file": delete_file}
    write_tts_config({
        "configs": [{"id": config_id, "name": "Script", "kind": "script", **fields, **extra, "validated": snapshot}]
    })
    return fields


def _send_audio(pump, content: bytes = b"ID3audio") -> None:
    pump.send("set_headers", "audio/mpeg", None)
    pump.send("append", content)
    pump.send("finish")


def test_a_script_speech_runs_the_stored_command(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump)
        audio_id = await _open_id(client, {**SCRIPT_READ, "command": "evil"})
        assert await _body(await _read(client, audio_id)) == b"ID3audio"

    asyncio.run(scenario())
    [(request, _parts, _paced)] = script_upstream.script_runs
    assert request == ScriptSpeechRequest(text="Hello there", **SCRIPT_FIELDS)
    assert request.command == "say"


def test_a_script_speech_needs_no_key(client, script_upstream, monkeypatch):
    monkeypatch.setattr(speech_service, "openrouter_api_key", lambda: None)
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump)
        response = await _open(client, SCRIPT_READ)
        assert response.status_code == 200, response.content
        assert response.json()["content_type"] == "audio/mpeg"
        await _settle()

    asyncio.run(scenario())


def test_an_unknown_script_is_404(client, script_upstream):
    # No such config, then an OpenRouter config of that id: neither is a script config.
    response = _post_json(client, SCRIPT_READ)
    assert response.status_code == 404
    assert response.json() == {"error": NO_SUCH_SCRIPT}
    write_tts_config({"configs": [{"id": SCRIPT_ID, "kind": "openrouter", "model": "m"}]})
    response = _post_json(client, SCRIPT_TEST)
    assert response.status_code == 404
    assert response.json() == {"error": NO_SUCH_SCRIPT}
    assert script_upstream.script_runs == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({key: value for key, value in SCRIPT_READ.items() if key != "text"}, TEXT_ERROR),
        ({**SCRIPT_READ, "text": "…"}, NOTHING_TO_SAY),
        ({key: value for key, value in SCRIPT_READ.items() if key != "script_id"}, SCRIPT_ID_ERROR),
        ({**SCRIPT_READ, "script_id": ""}, SCRIPT_ID_ERROR),
        ({**SCRIPT_READ, "script_id": 3}, SCRIPT_ID_ERROR),
    ],
)
def test_rejects_invalid_script_payloads(client, script_upstream, body, message):
    _store_script()
    response = _post_json(client, body)
    assert response.status_code == 400
    assert response.json() == {"error": message}
    assert script_upstream.script_runs == []


def test_a_test_with_unsaved_fields_is_409_and_runs_nothing(client, script_upstream):
    _store_script()
    response = _post_json(client, {**SCRIPT_TEST, "command": "say --fast"})
    assert response.status_code == 409
    assert response.json() == {"error": NOT_SAVED}
    assert script_upstream.script_runs == []
    assert script_upstream.records == []


def test_a_test_of_unvalidated_saved_fields_runs(client, script_upstream):
    _store_script(validated=None)

    async def scenario():
        _send_audio(script_upstream.pump)
        await _open_id(client, SCRIPT_TEST)
        await _settle()

    asyncio.run(scenario())
    assert len(script_upstream.script_runs) == 1


@pytest.mark.parametrize("snapshot", [None, {**SCRIPT_FIELDS, "command": "say --old"}])
def test_a_message_read_of_an_unvalidated_script_is_409(client, script_upstream, snapshot):
    # Never validated, or edited after its validation.
    _store_script(validated=snapshot)
    response = _post_json(client, SCRIPT_READ)
    assert response.status_code == 409
    assert response.json() == {"error": NOT_VALIDATED}
    assert script_upstream.script_runs == []


@pytest.mark.parametrize("config_id", ["other", "", 3])
def test_a_config_id_other_than_the_script_id_is_400(client, script_upstream, config_id):
    _store_script()
    response = _post_json(client, {**SCRIPT_READ, "config_id": config_id})
    assert response.status_code == 400
    assert response.json() == {"error": CONFIG_ID_ERROR}
    assert script_upstream.script_runs == []


def test_a_script_test_records_its_run_without_cost(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump)
        await _open_id(client, SCRIPT_TEST)
        await _settle()

    asyncio.run(scenario())
    [(config_id, run)] = script_upstream.records
    assert config_id == SCRIPT_ID
    assert run["generation_id"] == ""
    assert run["cost"] is None
    assert run["parts"] == 1
    assert run["chars"] == len(SCRIPT_READ["text"])
    assert script_upstream.cost == []  # no cost lookup


def test_a_script_message_read_records_no_run(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump)
        await _open_id(client, SCRIPT_READ)
        await _settle()

    asyncio.run(scenario())
    assert script_upstream.records == []
    assert script_upstream.cost == []


def test_a_script_message_read_uses_the_cache(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump, b"abc")
        await _open_id(client, SCRIPT_CACHED_READ)
        await _settle()
        audio_id = await _open_id(client, SCRIPT_CACHED_READ)
        assert await _body(await _read(client, audio_id)) == b"abc"

    asyncio.run(scenario())
    assert len(script_upstream.script_runs) == 1


def test_a_script_test_never_reads_the_cache(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump, b"cached")
        await _open_id(client, SCRIPT_CACHED_READ)
        await _settle()
        _send_audio(script_upstream.pump, b"fresh")
        audio_id = await _open_id(client, {**SCRIPT_TEST, "cache": True})
        assert await _body(await _read(client, audio_id)) == b"fresh"

    asyncio.run(scenario())
    assert len(script_upstream.script_runs) == 2


def test_a_changed_command_misses_the_cache(client, script_upstream):
    _store_script()

    async def scenario():
        _send_audio(script_upstream.pump, b"first")
        await _open_id(client, SCRIPT_CACHED_READ)
        await _settle()
        _store_script(command="say --voice=other")
        _send_audio(script_upstream.pump, b"second")
        audio_id = await _open_id(client, SCRIPT_CACHED_READ)
        assert await _body(await _read(client, audio_id)) == b"second"

    asyncio.run(scenario())
    assert [request.command for request, _parts, _paced in script_upstream.script_runs] == [
        "say", "say --voice=other"
    ]


@pytest.mark.parametrize("body", [SCRIPT_READ, SCRIPT_TEST])
def test_a_pcm_script_without_a_rate_is_400(client, script_upstream, body):
    fields = _store_script(audio_format="pcm")
    response = _post_json(client, {**body, **fields})
    assert response.status_code == 400
    assert response.json() == {"error": NO_PCM_RATE}
    assert script_upstream.script_runs == []


@pytest.mark.parametrize("body", [SCRIPT_READ, SCRIPT_TEST])
@pytest.mark.parametrize("command", ["", "   "])
def test_a_script_without_a_command_is_400(client, script_upstream, body, command):
    fields = _store_script(command=command)
    response = _post_json(client, {**body, **fields})
    assert response.status_code == 400
    assert response.json() == {"error": NO_COMMAND}
    assert script_upstream.script_runs == []


def test_script_parts_and_pacing_reach_the_pump(client, script_upstream):
    text = "word " * 180  # 900 characters
    body = {**SCRIPT_READ, "text": text}

    async def run(request_body):
        _send_audio(script_upstream.pump)
        await _open_id(client, request_body)
        await _settle()

    async def scenario():
        await run(body)
        _store_script(max_chars=300)
        await run(body)
        await run({**body, "buffered": True})

    _store_script()
    asyncio.run(scenario())
    parts = script_parts(ScriptSpeechRequest(text=text, **{**SCRIPT_FIELDS, "max_chars": 300}))
    assert len(parts) > 1
    assert [(run_parts, paced) for _request, run_parts, paced in script_upstream.script_runs] == [
        ([text], True),
        (parts, True),
        (parts, False),
    ]


def test_a_script_failure_before_audio_is_a_424(client, script_upstream):
    _store_script()
    failure = SpeechError("sh: say: not found", log_message="The script failed (exit status 127)")
    script_upstream.pump.send("fail", failure)
    response = _post_json(client, SCRIPT_READ)
    assert response.status_code == 424
    assert response.json() == {"error": "sh: say: not found"}
    assert streams._streams == {}


def test_the_log_label_has_no_command(client, script_upstream, streams_log):
    _store_script(command="say --token=secret-value")

    async def scenario():
        _send_audio(script_upstream.pump)
        await _open_id(client, {**SCRIPT_READ, "command": "say --token=secret-value"})
        await _settle()

    asyncio.run(scenario())
    [line] = _speech_lines(streams_log)
    assert line.startswith(f"TTS speech kind=script output=stream format=mp3 chars={len(SCRIPT_READ['text'])} ")
    assert "secret-value" not in line
    assert "say" not in line


def test_an_openrouter_body_with_kind_openrouter_is_unchanged(client, upstream):
    async def scenario():
        upstream.pump.send("set_headers", "audio/mpeg", "gen-1")
        upstream.pump.send("finish")
        await _open_id(client, {**VALID_PAYLOAD, "kind": "openrouter"})
        await _settle()

    asyncio.run(scenario())
    assert upstream.pump.calls == [(parse_speech_request(VALID_PAYLOAD), "k")]
    assert upstream.cost == [(("gen-1",), "k")]


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


@pytest.mark.parametrize("delete_file, expected", [(None, True), (True, True), (False, False)])
def test_the_stored_delete_file_reaches_the_pump(client, script_upstream, delete_file, expected):
    _store_script(delete_file=delete_file)

    async def scenario():
        _send_audio(script_upstream.pump)
        await _open_id(client, SCRIPT_READ)
        await _settle()

    asyncio.run(scenario())
    assert script_upstream.delete_files == [expected]
    [(request, _parts, _paced)] = script_upstream.script_runs
    assert request == ScriptSpeechRequest(text="Hello there", **SCRIPT_FIELDS)  # not in the request


def test_a_toggled_delete_file_does_not_miss_the_cache(client, script_upstream):
    _store_script(delete_file=True)

    async def scenario():
        _send_audio(script_upstream.pump, b"abc")
        await _open_id(client, SCRIPT_CACHED_READ)
        await _settle()
        _store_script(delete_file=False)
        audio_id = await _open_id(client, SCRIPT_CACHED_READ)
        assert await _body(await _read(client, audio_id)) == b"abc"

    asyncio.run(scenario())
    assert len(script_upstream.script_runs) == 1


def test_a_test_that_differs_only_in_delete_file_runs(client, script_upstream):
    _store_script(delete_file=True)

    async def scenario():
        _send_audio(script_upstream.pump)
        response = await _open(client, {**SCRIPT_TEST, "delete_file": False})
        assert response.status_code == 200, response.content
        await _settle()

    asyncio.run(scenario())
    assert script_upstream.delete_files == [True]  # the stored value, never the body's


# --- The text adaptation --------------------------------------------------------

ADAPTED = "Hello to you."


class Adaptation:
    """The fakes of an adapted reading: the running providers, the streamed call and a pump that
    pulls every part of its source (an adaptation runs then), records them and sends one audio."""

    def __init__(self, upstream: Upstream):
        self.upstream = upstream
        self.running = {Provider.CLAUDE_CODE, Provider.CODEX}
        # Each call takes the next answer: a list of text deltas, or an exception to raise.
        self.answers: list = []
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.pulled: list[list[str]] = []
        self.sources: list = []
        self.paced: list[bool] = []

    async def open_stream(self, engine, prompt):
        self.calls.append(engine.name)
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        for delta in answer:
            yield delta

    async def pump(self, request, stream, parts, paced) -> None:
        self.sources.append(parts)
        self.paced.append(paced)
        source = as_part_source(request.text, parts)
        texts = []
        try:
            while (text := await source.next_part()) is not None:
                texts.append(text)
        finally:
            await source.aclose()
        self.pulled.append(texts)
        stream.set_headers("audio/mpeg", None)
        stream.append(b"ID3" + str(len(self.pulled)).encode())
        stream.finish()


@pytest.fixture
def adaptation(monkeypatch, upstream, tts_json):
    """Both providers run; the adaptation call and both pumps are fakes (:class:`Adaptation`)."""
    fake = Adaptation(upstream)
    monkeypatch.setattr(adapt, "is_provider_running", lambda provider: provider in fake.running)
    monkeypatch.setattr(adapt, "_open_stream", fake.open_stream)

    def pump_speech(request, api_key, stream, parts=None, *, paced=False):
        return fake.pump(request, stream, parts, paced)

    def pump_script_speech(request, stream, parts=None, *, paced=False, delete_file):
        return fake.pump(request, stream, parts, paced)

    monkeypatch.setattr(speech_service, "pump_speech", pump_speech)
    monkeypatch.setattr(speech_service, "pump_script_speech", pump_script_speech)
    return fake


class RecordedAdaptedParts(adapt.AdaptedParts):
    """The real part source, with the arguments the view gave it."""

    made: list[dict] = []

    def __init__(self, text, engine, limit, first, *, fallback, label, then=None):
        RecordedAdaptedParts.made.append({
            "text": text,
            "engine": engine.name,
            "limits": (limit, first),
            "fallback": list(fallback),
            "label": label,
            "then": None if then is None else then.name,
        })
        super().__init__(text, engine, limit, first, fallback=fallback, label=label, then=then)


@pytest.fixture
def recorded_sources(monkeypatch):
    RecordedAdaptedParts.made = []
    monkeypatch.setattr(speech_service, "AdaptedParts", RecordedAdaptedParts)
    return RecordedAdaptedParts.made


def _store_adapt(choice) -> None:
    write_tts_config({**read_tts_config(), "adapt": choice})


async def _read_through(client, body) -> str:
    """Open a speech and wait for its end steps (cache, last test): its id."""
    audio_id = await _open_id(client, body)
    task = get_stream(audio_id)._task
    if task is not None:  # None: a cache hit
        await _within(asyncio.wait([task]))
    return audio_id


@pytest.mark.parametrize(
    "model, response_format, whole_mp3, limits",
    [
        ("openai/gpt-4o-mini-tts", "mp3", False, (2000, 250)),  # unknown: the default limit
        ("sesame/csm-1b", "mp3", False, (200, 200)),
        ("deepgram/aura-2", "mp3", False, (2000, 250)),  # a streaming format starts small too
        ("elevenlabs/eleven-v3", "pcm", False, (2000, 250)),  # at most MAX_PART_CHARS
        ("openai/gpt-4o-mini-tts", "mp3", True, (None, None)),  # sent whole: one part
        ("openai/gpt-4o-mini-tts", "pcm", True, (2000, 250)),  # only its MP3 is sent whole
    ],
)
def test_an_adapted_openrouter_reading_gets_an_adapted_source(
    client, adaptation, recorded_sources, monkeypatch, model, response_format, whole_mp3, limits
):
    if whole_mp3:
        monkeypatch.setattr(model_catalog, "WHOLE_MP3_MODELS", frozenset({model}))
    body = {**VALID_PAYLOAD, "model": model, "response_format": response_format}
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, body))
    [made] = recorded_sources
    request = parse_speech_request(body)
    assert made["text"] == VALID_PAYLOAD["text"]
    assert made["engine"] == "haiku"
    assert made["limits"] == limits
    assert made["fallback"] == speech_service.speech_parts(request)
    [source] = adaptation.sources
    assert isinstance(source, adapt.AdaptedParts)
    assert adaptation.paced == [True]
    assert adaptation.pulled == [[ADAPTED]]
    assert made["label"]["adapted"] == "haiku"


@pytest.mark.parametrize(
    "fields, limits",
    [
        ({}, (None, None)),  # no max characters per run: the whole adaptation in one run
        ({"max_chars": 300}, (300, 250)),
        ({"max_chars": 100}, (100, 100)),
        ({"max_chars": 300, "output": "whole"}, (300, 250)),
    ],
)
def test_an_adapted_script_reading_gets_an_adapted_source(client, adaptation, recorded_sources, fields, limits):
    stored = _store_script(**fields)
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, {**SCRIPT_READ, **stored, "buffered": True}))
    [made] = recorded_sources
    assert made["limits"] == limits
    assert made["fallback"] == script_parts(ScriptSpeechRequest(SCRIPT_READ["text"], **stored))
    assert adaptation.paced == [False]
    assert adaptation.pulled == [[ADAPTED]]


@pytest.mark.parametrize("kind", ["openrouter", "script"])
@pytest.mark.parametrize("case", ["off", "no_provider"])
def test_a_reading_not_adapted_gets_todays_parts_and_says_why(
    client, adaptation, recorded_sources, streams_log, kind, case
):
    text = VALID_PAYLOAD["text"]
    if kind == "script":
        _store_script(max_chars=300)
        body = {**SCRIPT_READ, "max_chars": 300, "text": text}
        expected = script_parts(ScriptSpeechRequest(text, **{**SCRIPT_FIELDS, "max_chars": 300}))
    else:
        body = {**VALID_PAYLOAD, "text": text}
        expected = speech_service.speech_parts(parse_speech_request(body))
    if case == "off":
        _store_adapt("off")
    if case == "no_provider":
        adaptation.running = set()
    asyncio.run(_read_through(client, body))
    assert recorded_sources == []
    assert adaptation.calls == []
    assert adaptation.sources == [expected]
    [line] = _speech_lines(streams_log)
    assert f" adapted=no adapt_reason={case} " in line
    assert "adapt_first_ms" not in line


def test_the_engine_follows_the_choice_and_the_running_providers(client, adaptation, recorded_sources):
    async def scenario():
        for choice, running in [
            (None, {Provider.CLAUDE_CODE, Provider.CODEX}),
            ("luna", {Provider.CLAUDE_CODE, Provider.CODEX}),
            ("haiku", {Provider.CODEX}),
        ]:
            _store_adapt(choice)
            adaptation.running = running
            adaptation.answers.append([ADAPTED])
            await _read_through(client, VALID_PAYLOAD)

    asyncio.run(scenario())
    assert adaptation.calls == ["haiku", "luna", "luna"]


def test_the_cache_key_carries_the_adaptation(client, adaptation, streams_log):
    request = parse_speech_request(VALID_PAYLOAD)

    async def scenario():
        adaptation.answers.append([ADAPTED])
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, "haiku")) == CachedAudio(b"ID31", "audio/mpeg")
        assert speech_cache.get((request, None)) is None

        # The same choice: the cache answers, no model runs.
        audio_id = await _read_through(client, CACHED_PAYLOAD)
        assert await _body(await _read(client, audio_id)) == b"ID31"
        assert adaptation.calls == ["haiku"]

        # Another engine misses the cache.
        adaptation.running = {Provider.CODEX}
        adaptation.answers.append([ADAPTED])
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, "luna")) == CachedAudio(b"ID32", "audio/mpeg")

        # Off: an unadapted reading, then its cache hit.
        _store_adapt("off")
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, None)) == CachedAudio(b"ID33", "audio/mpeg")
        await _read_through(client, CACHED_PAYLOAD)

    asyncio.run(scenario())
    assert adaptation.calls == ["haiku", "luna"]
    assert adaptation.pulled == [[ADAPTED], [ADAPTED], [VALID_PAYLOAD["text"]]]
    lines = _speech_lines(streams_log)
    assert len(lines) == 5
    assert " adapted=haiku adapt_first_ms=" in lines[0]
    # A cache hit tells the adaptation of its key, without a time: no model ran.
    assert lines[1].endswith("outcome=cached")
    assert " adapted=haiku " in lines[1]
    assert "adapt_first_ms" not in lines[1]
    assert " adapted=luna adapt_first_ms=" in lines[2]
    assert " adapted=no adapt_reason=off " in lines[3]
    assert lines[4].endswith("outcome=cached")
    assert " adapted=no adapt_reason=off " in lines[4]
    for line in lines:
        assert VALID_PAYLOAD["text"] not in line
        assert ADAPTED not in line


@pytest.mark.parametrize("text_came", [False, True])
def test_a_released_reading_tells_whether_adapted_text_came(
    client, adaptation, recorded_sources, streams_log, monkeypatch, text_came
):
    opened = []

    async def open_stream(engine, prompt):
        opened.append(engine.name)
        if text_came:
            yield "Hello to you"  # no sentence end: no part yet
        await asyncio.Event().wait()

    async def pump_speech(request, api_key, stream, parts=None, *, paced=False):
        # Headers first, so that the reading can be released while the adaptation runs.
        stream.set_headers("audio/mpeg", None)
        try:
            await parts.next_part()
        finally:
            await parts.aclose()

    monkeypatch.setattr(adapt, "_open_stream", open_stream)
    monkeypatch.setattr(speech_service, "pump_speech", pump_speech)

    async def scenario():
        audio_id = await _open_id(client, VALID_PAYLOAD)
        [made] = recorded_sources
        await _within(_until(lambda: opened))
        if text_came:
            await _within(_until(lambda: made["label"]["adapted"] == "haiku"))
        else:
            await _settle()
        response = await _release(client, audio_id)
        assert response.status_code == 204
        await _settle()

    asyncio.run(scenario())
    [line] = _speech_lines(streams_log)
    assert line.endswith("outcome=released")
    if text_came:
        assert " adapted=haiku adapt_first_ms=" in line
    else:
        assert " adapted=pending:haiku " in line
        assert "adapt_first_ms" not in line
    assert "Hello to you" not in line


def test_a_script_cache_key_carries_the_adaptation(client, adaptation):
    stored = _store_script()
    request = ScriptSpeechRequest(SCRIPT_READ["text"], **stored)

    async def scenario():
        adaptation.answers.append([ADAPTED])
        await _read_through(client, SCRIPT_CACHED_READ)
        await _read_through(client, SCRIPT_CACHED_READ)

    asyncio.run(scenario())
    assert speech_cache.get((request, "haiku")) == CachedAudio(b"ID31", "audio/mpeg")
    assert speech_cache.get((request, None)) is None
    assert adaptation.calls == ["haiku"]


def test_a_fallen_back_reading_is_cached_unadapted(client, adaptation, streams_log):
    request = parse_speech_request(VALID_PAYLOAD)

    async def scenario():
        adaptation.answers.append(RuntimeError("the model is down"))
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, None)) == CachedAudio(b"ID31", "audio/mpeg")
        assert speech_cache.get((request, "haiku")) is None

        # The next reading tries the adaptation again.
        adaptation.answers.append([ADAPTED])
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, "haiku")) == CachedAudio(b"ID32", "audio/mpeg")

    asyncio.run(scenario())
    assert adaptation.calls == ["haiku", "haiku"]
    assert adaptation.pulled == [[VALID_PAYLOAD["text"]], [ADAPTED]]
    first, second = _speech_lines(streams_log)
    assert " adapted=no adapt_reason=failed " in first
    assert " adapted=haiku adapt_first_ms=" in second


def test_a_settings_test_records_the_adaptation(client, adaptation):
    long_answer = [f"Sentence number {index:04d} of the adapted text. " for index in range(200)]  # ~9,600 chars

    async def scenario():
        adaptation.answers.append(["Hello ", "to you."])
        await _read_through(client, TEST_PAYLOAD)
        adaptation.answers.append(long_answer)
        await _read_through(client, TEST_PAYLOAD)
        adaptation.answers.append(RuntimeError("the model is down"))
        await _read_through(client, TEST_PAYLOAD)
        adaptation.running = set()
        await _read_through(client, TEST_PAYLOAD)

    asyncio.run(scenario())
    adapted, long_run, fallen_back, not_adapted = (run for _config_id, run in adaptation.upstream.records)
    assert adapted["adapted_by"] == "haiku"
    assert adapted["adapt_engine"] == "haiku"
    assert adapted["adapt_reason"] is None
    assert isinstance(adapted["adapt_first_ms"], int)
    assert adapted["adapted_text"] == ADAPTED
    # The characters are those of the original text.
    assert adapted["chars"] == len(VALID_PAYLOAD["text"])
    assert long_run["adapted_text"] == "".join(long_answer)[: adapt.TEST_TEXT_KEEP_CHARS]
    assert len(long_run["adapted_text"]) == adapt.TEST_TEXT_KEEP_CHARS
    assert long_run["chars"] == len(VALID_PAYLOAD["text"])
    fields = ("adapt_engine", "adapted_by", "adapt_reason", "adapt_first_ms", "adapted_text")
    assert {key: fallen_back[key] for key in fields} == {
        "adapt_engine": "haiku", "adapted_by": None, "adapt_reason": "failed", "adapt_first_ms": None,
        "adapted_text": None,
    }
    # No engine ran.
    assert {key: not_adapted[key] for key in fields} == {
        "adapt_engine": None, "adapted_by": None, "adapt_reason": "no_provider", "adapt_first_ms": None,
        "adapted_text": None,
    }


def test_a_script_settings_test_records_the_adaptation(client, adaptation):
    _store_script()
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, SCRIPT_TEST))
    [(config_id, run)] = adaptation.upstream.records
    assert config_id == SCRIPT_ID
    assert (run["adapted_by"], run["adapt_reason"], run["adapted_text"]) == ("haiku", None, ADAPTED)
    assert run["chars"] == len(SCRIPT_READ["text"])


@pytest.mark.parametrize("body", [CACHED_PAYLOAD, TEST_PAYLOAD])
def test_an_adaptation_failure_after_the_first_part_cuts_the_reading(client, adaptation, monkeypatch, body):
    request = parse_speech_request(VALID_PAYLOAD)
    sentences = [f"Sentence number {index:02d} of the adapted text. " for index in range(12)]  # ~480 chars
    gate = None

    async def open_stream(engine, prompt):
        for delta in sentences:
            yield delta
        await gate.wait()
        raise RuntimeError("the model is down")

    async def pump_speech(request, api_key, stream, parts=None, *, paced=False):
        # As the real pump: one audio per part, and an error of the source fails the stream.
        source = as_part_source(request.text, parts)
        try:
            index = 0
            while True:
                try:
                    text = await source.next_part()
                except SpeechError as error:
                    stream.fail_if_live(error)
                    return
                if text is None:
                    break
                if not index:
                    stream.set_headers("audio/mpeg", None)
                stream.append(f"part{index};".encode())
                index += 1
        finally:
            await source.aclose()
        stream.finish()

    monkeypatch.setattr(adapt, "_open_stream", open_stream)
    monkeypatch.setattr(speech_service, "pump_speech", pump_speech)

    async def scenario():
        nonlocal gate
        gate = asyncio.Event()
        audio_id = await _open_id(client, body)
        response = await _read(client, audio_id)
        chunks = aiter(response.streaming_content)
        received = await _next(chunks)
        gate.set()
        with pytest.raises(ConnectionAbortedError):
            async with asyncio.timeout(2.0):
                async for chunk in chunks:
                    received += chunk
        await _within(asyncio.wait([get_stream(audio_id)._task]))
        assert await _ending(client, audio_id) == {"state": "failed", "error": adapt.ADAPT_STOPPED}
        return received

    received = asyncio.run(scenario())
    # The first part, then the tail of the received text (up to its last sentence end).
    assert received == b"part0;part1;"
    assert speech_cache.get((request, "haiku")) is None
    assert speech_cache.get((request, None)) is None
    assert adaptation.upstream.records == []


# --- OpenRouter, then the agent ---------------------------------------------------


@pytest.fixture
def openrouter_key(monkeypatch):
    """The OpenRouter key is set (its value never matters: the call is faked)."""
    monkeypatch.setattr(adapt, "openrouter_key_set", lambda: True)


def test_openrouter_choice_passes_the_agent_as_then(client, adaptation, recorded_sources, openrouter_key):
    _store_adapt("openrouter")

    async def scenario():
        adaptation.running = {Provider.CLAUDE_CODE}
        adaptation.answers.append([ADAPTED])
        await _read_through(client, VALID_PAYLOAD)
        # No provider runs: OpenRouter still adapts, with no agent after it.
        adaptation.running = set()
        adaptation.answers.append([ADAPTED])
        await _read_through(client, VALID_PAYLOAD)

    asyncio.run(scenario())
    assert [(made["engine"], made["then"]) for made in recorded_sources] == [("openrouter", "haiku"), ("openrouter", None)]
    assert adaptation.calls == ["openrouter", "openrouter"]
    assert adaptation.pulled == [[ADAPTED], [ADAPTED]]
    assert [made["label"]["adapted"] for made in recorded_sources] == ["openrouter", "openrouter"]


def test_openrouter_cache_lookup_tries_openrouter_then_the_agent(client, adaptation, streams_log, openrouter_key):
    _store_adapt("openrouter")
    request = parse_speech_request(VALID_PAYLOAD)

    async def scenario():
        speech_cache.set((request, "haiku"), CachedAudio(b"HAIKU", "audio/mpeg"))
        audio_id = await _read_through(client, CACHED_PAYLOAD)
        assert await _body(await _read(client, audio_id)) == b"HAIKU"
        # An OpenRouter reading wins over the agent's.
        speech_cache.set((request, "openrouter"), CachedAudio(b"OPENROUTER", "audio/mpeg"))
        audio_id = await _read_through(client, CACHED_PAYLOAD)
        assert await _body(await _read(client, audio_id)) == b"OPENROUTER"

    asyncio.run(scenario())
    assert adaptation.calls == []
    first, second = _speech_lines(streams_log)
    assert first.endswith("outcome=cached") and " adapted=haiku " in first
    assert second.endswith("outcome=cached") and " adapted=openrouter " in second


def test_openrouter_without_the_key_never_calls_openrouter(client, adaptation, recorded_sources, monkeypatch):
    monkeypatch.setattr(adapt, "openrouter_key_set", lambda: False)
    _store_adapt("openrouter")

    async def scenario():
        adaptation.answers.append([ADAPTED])
        await _read_through(client, VALID_PAYLOAD)
        adaptation.running = {Provider.CODEX}
        adaptation.answers.append([ADAPTED])
        await _read_through(client, VALID_PAYLOAD)

    asyncio.run(scenario())
    assert adaptation.calls == ["haiku", "luna"]
    assert [(made["engine"], made["then"]) for made in recorded_sources] == [("haiku", None), ("luna", None)]
    # The stored choice is never rewritten.
    assert read_tts_config()["adapt"] == "openrouter"


def test_a_fallback_reading_is_cached_under_the_agent(client, adaptation, streams_log, openrouter_key):
    _store_adapt("openrouter")
    request = parse_speech_request(VALID_PAYLOAD)

    async def scenario():
        adaptation.answers.extend([OpenRouterTextError("http", 500), [ADAPTED]])
        await _read_through(client, CACHED_PAYLOAD)
        assert speech_cache.get((request, "haiku")) == CachedAudio(b"ID31", "audio/mpeg")
        assert speech_cache.get((request, "openrouter")) is None
        assert speech_cache.get((request, None)) is None
        # A re-read plays the agent's reading: no new call while OpenRouter keeps failing.
        audio_id = await _read_through(client, CACHED_PAYLOAD)
        assert await _body(await _read(client, audio_id)) == b"ID31"

    asyncio.run(scenario())
    assert adaptation.calls == ["openrouter", "haiku"]
    assert adaptation.pulled == [[ADAPTED]]
    first, second = _speech_lines(streams_log)
    assert " adapted=haiku " in first
    assert " fallback_from=openrouter fallback_reason=failed" in first
    assert second.endswith("outcome=cached") and " adapted=haiku " in second
    for line in (first, second):
        assert VALID_PAYLOAD["text"] not in line
        assert ADAPTED not in line


def test_last_test_records_the_fallback_reason(client, adaptation, openrouter_key):
    _store_adapt("openrouter")

    async def scenario():
        adaptation.answers.extend([OpenRouterTextError("http", 500), [ADAPTED]])
        await _read_through(client, TEST_PAYLOAD)
        adaptation.answers.append([ADAPTED])
        await _read_through(client, TEST_PAYLOAD)

    asyncio.run(scenario())
    fallen_back, openrouter = (run for _config_id, run in adaptation.upstream.records)
    assert fallen_back["adapted_by"] == "haiku"
    assert fallen_back["fallback_reason"] == "failed"
    assert fallen_back["adapt_reason"] is None
    assert isinstance(fallen_back["adapt_first_ms"], int)
    assert fallen_back["adapted_text"] == ADAPTED
    assert (openrouter["adapted_by"], openrouter["fallback_reason"], openrouter["adapted_text"]) == ("openrouter", None, ADAPTED)
    assert (fallen_back["adapt_engine"], openrouter["adapt_engine"]) == ("haiku", "openrouter")


def test_last_test_records_the_engine_of_the_last_failed_attempt(client, adaptation, openrouter_key):
    _store_adapt("openrouter")

    async def scenario():
        # OpenRouter fails, then the agent fails too.
        adaptation.answers.extend([OpenRouterTextError("http", 401), RuntimeError("the model is down")])
        await _read_through(client, TEST_PAYLOAD)
        # OpenRouter fails and no agent runs.
        adaptation.running = set()
        adaptation.answers.append(OpenRouterTextError("http", 500))
        await _read_through(client, TEST_PAYLOAD)

    asyncio.run(scenario())
    both_failed, openrouter_only = (run for _config_id, run in adaptation.upstream.records)
    fields = ("adapt_engine", "adapted_by", "adapt_reason", "fallback_reason")
    assert {key: both_failed[key] for key in fields} == {
        "adapt_engine": "haiku", "adapted_by": None, "adapt_reason": "failed", "fallback_reason": "refused",
    }
    assert {key: openrouter_only[key] for key in fields} == {
        "adapt_engine": "openrouter", "adapted_by": None, "adapt_reason": "failed", "fallback_reason": None,
    }


# --- The direct reading of a simple text ----------------------------------------

SIMPLE = "Hello **world**."
CLEANED = "Hello world."
TABLE = "| a | b |\n|---|---|\n| 1 | 2 |"


@pytest.fixture
def direct_reading(monkeypatch):
    """Undo the autouse patch of ``speech_service.direct_text``: the real decision runs."""
    monkeypatch.setattr(speech_service, "direct_text", direct_text)


def _speech_key(body: dict, name: str | None):
    return (parse_speech_request(body), name)


@pytest.mark.parametrize("choice", [None, "haiku"])
def test_a_simple_text_is_read_directly(client, adaptation, direct_reading, streams_log, choice):
    if choice is not None:
        _store_adapt(choice)
    body = {**CACHED_PAYLOAD, "text": SIMPLE}
    asyncio.run(_read_through(client, body))
    assert adaptation.calls == []
    assert adaptation.pulled == [[CLEANED]]
    [line] = _speech_lines(streams_log)
    assert " adapted=direct " in line
    assert "adapt_reason" not in line
    assert speech_cache.get(_speech_key(body, "direct")) is not None
    assert speech_cache.get(_speech_key(body, None)) is None


def test_a_complex_text_still_goes_through_the_model(client, adaptation, direct_reading):
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, {**VALID_PAYLOAD, "text": TABLE}))
    assert adaptation.calls == ["haiku"]
    assert adaptation.pulled == [[ADAPTED]]


def test_off_reads_as_written_without_the_decision(client, adaptation, direct_reading, streams_log):
    _store_adapt("off")
    asyncio.run(_read_through(client, {**VALID_PAYLOAD, "text": SIMPLE}))
    assert adaptation.calls == []
    assert adaptation.pulled == [[SIMPLE]]
    [line] = _speech_lines(streams_log)
    assert " adapted=no adapt_reason=off " in line


@pytest.mark.parametrize("kind", ["openrouter", "script"])
def test_direct_comes_before_the_engine(client, adaptation, direct_reading, streams_log, kind):
    text = SIMPLE.strip()
    adaptation.running = set()
    if kind == "script":
        _store_script()
        body = {**SCRIPT_READ, "text": text}
    else:
        body = {**VALID_PAYLOAD, "text": text}
    asyncio.run(_read_through(client, body))
    assert adaptation.calls == []
    [line] = _speech_lines(streams_log)
    assert " adapted=direct " in line
    assert "no_provider" not in line
    # The parts cut the text where whitespace falls: compare the words.
    assert " ".join(adaptation.pulled[0]).split() == CLEANED.split()


def test_a_settings_test_always_adapts(client, adaptation, direct_reading):
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, {**TEST_PAYLOAD, "text": SIMPLE}))
    assert adaptation.calls == ["haiku"]
    [(config_id, run)] = adaptation.upstream.records
    assert config_id == "cfg-1"
    assert (run["adapted_by"], run["adapt_reason"], run["adapted_text"]) == ("haiku", None, ADAPTED)


def test_direct_and_as_written_never_share_the_cache(client, adaptation, direct_reading):
    body = {**CACHED_PAYLOAD, "text": SIMPLE}

    async def scenario():
        # Direct first: the as-written reading is not served from the direct entry.
        await _read_through(client, body)
        assert adaptation.pulled == [[CLEANED]]
        _store_adapt("off")
        await _read_through(client, body)
        assert adaptation.pulled == [[CLEANED], [SIMPLE]]
        assert speech_cache.get(_speech_key(body, "direct")) is not None
        assert speech_cache.get(_speech_key(body, None)) is not None
        # The reverse order, from an empty cache: as written first, then direct.
        speech_cache.clear()
        await _read_through(client, body)
        assert adaptation.pulled == [[CLEANED], [SIMPLE], [SIMPLE]]
        _store_adapt(None)
        await _read_through(client, body)
        assert adaptation.pulled == [[CLEANED], [SIMPLE], [SIMPLE], [CLEANED]]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin, text, model",
    [
        ("file", SIMPLE, True),
        ("rendered", "a\nb", True),
        ("rendered", "one line", False),
        (None, SIMPLE, False),
        (None, "a\n\nb", False),
    ],
)
def test_origin_is_validated_and_applied(client, adaptation, direct_reading, origin, text, model):
    body = {**VALID_PAYLOAD, "text": text}
    if origin is not None:
        body["origin"] = origin
    adaptation.answers.append([ADAPTED])
    asyncio.run(_read_through(client, body))
    assert adaptation.calls == (["haiku"] if model else [])


@pytest.mark.parametrize("origin", ["nope", 3, True, ["file"]])
def test_a_bad_origin_is_a_400(client, upstream, direct_reading, origin):
    for body in ({**VALID_PAYLOAD, "origin": origin}, {**SCRIPT_READ, "origin": origin}):
        response = asyncio.run(_open(client, body))
        assert response.status_code == 400
        assert response.json() == {"error": "'origin' must be one of: rendered, file"}
    assert upstream.pump.calls == []


def test_a_null_origin_counts_as_absent(client, adaptation, direct_reading):
    asyncio.run(_read_through(client, {**VALID_PAYLOAD, "text": SIMPLE, "origin": None}))
    assert adaptation.calls == []
    assert adaptation.pulled == [[CLEANED]]


def test_cleaned_to_nothing_is_refused(client, upstream, direct_reading):
    text = "[](x)"
    assert has_speech(text)
    cleaned = direct_text(text)
    assert cleaned is not None
    assert not has_speech(cleaned)
    response = asyncio.run(_open(client, {**VALID_PAYLOAD, "text": text}))
    assert response.status_code == 400
    assert response.json() == {"error": NOTHING_TO_SAY}
    assert upstream.pump.calls == []
    assert streams._streams == {}


def test_a_direct_reading_works_for_a_script_config(client, adaptation, direct_reading, streams_log):
    _store_script()
    asyncio.run(_read_through(client, {**SCRIPT_READ, "text": SIMPLE}))
    assert adaptation.calls == []
    assert adaptation.pulled == [[CLEANED]]
    [line] = _speech_lines(streams_log)
    assert " adapted=direct " in line


def test_the_open_step_logs_no_text(client, adaptation, direct_reading, views_log, streams_log):
    views_log.set_level(logging.DEBUG)
    asyncio.run(_read_through(client, {**VALID_PAYLOAD, "text": SIMPLE}))
    asyncio.run(_open(client, {**VALID_PAYLOAD, "text": "[](x)"}))
    messages = [record.getMessage() for record in views_log.records]
    assert any(record.name == STREAMS_LOGGER_NAME for record in views_log.records)  # the stream's line
    for message in messages:
        for text in ("Hello", "world", "[](x)"):
            assert text not in message


# --- Context spoken before a message --------------------------------------------


@pytest.mark.parametrize("kind", ["openrouter", "script"])
@pytest.mark.parametrize("path", ["direct", "off", "adapted", "fallback"])
def test_announcement_precedes_message(client, adaptation, direct_reading, kind, path):
    if kind == "script":
        _store_script()
    text = SIMPLE if path == "direct" else TABLE
    body = {**(SCRIPT_READ if kind == "script" else VALID_PAYLOAD), "text": text}
    expected = CLEANED if path == "direct" else text
    if path == "off":
        _store_adapt("off")
    elif path == "adapted":
        adaptation.answers.append(["Adapted message."])
        expected = "Adapted message."
    elif path == "fallback":
        adaptation.answers.append(RuntimeError("Adaptation unavailable"))
    asyncio.run(_read_through(client, {**body, "announcement": "  Project. Session.  "}))
    assert adaptation.pulled == [["Project. Session.", expected]]
    assert adaptation.calls == ([] if path in ("direct", "off") else ["haiku"])
    assert adaptation.sources[0].split is True


@pytest.mark.parametrize("kind", ["openrouter", "script"])
def test_announcement_outside_adaptation(client, adaptation, kind):
    if kind == "script":
        _store_script()
    body = SCRIPT_READ if kind == "script" else VALID_PAYLOAD
    adaptation.answers.append(["Adapted message."])
    asyncio.run(_read_through(client, {**body, "announcement": "Project. Session."}))
    assert adaptation.prompts == [adapt.ADAPT_PROMPT.format(text=body["text"])]
    assert "Project. Session." not in adaptation.prompts[0]
    assert adaptation.pulled == [["Project. Session.", "Adapted message."]]


@pytest.mark.parametrize("kind", ["openrouter", "script"])
@pytest.mark.parametrize("path", ["direct", "off", "adapted", "fallback"])
def test_announcement_cache_isolation(client, adaptation, direct_reading, openrouter_key, kind, path):
    fields = _store_script() if kind == "script" else None
    body = {**(SCRIPT_CACHED_READ if kind == "script" else CACHED_PAYLOAD),
            "text": SIMPLE if path == "direct" else TABLE}
    request = ScriptSpeechRequest(body["text"], **fields) if fields else parse_speech_request(body)
    engine = {"direct": "direct", "off": None, "adapted": "haiku", "fallback": "haiku"}[path]
    if path == "off":
        _store_adapt("off")
    elif path == "fallback":
        _store_adapt("openrouter")

    def queue_answer():
        if path == "fallback":
            adaptation.answers.append(OpenRouterTextError("http", 500))
        if path in ("adapted", "fallback"):
            adaptation.answers.append(["Adapted message."])

    async def scenario():
        queue_answer()
        first_id = await _read_through(client, {**body, "announcement": "  Project. Session.  "})
        first_audio = await _body(await _read(client, first_id))
        repeated_id = await _read_through(client, {**body, "announcement": "Project. Session."})
        repeated_announcement_uses_cache = (
            len(adaptation.pulled) == 1 and await _body(await _read(client, repeated_id)) == first_audio
        )
        assert repeated_announcement_uses_cache is True
        assert speech_cache.get((request, engine, "Project. Session.")) is not None
        if path == "fallback":
            fallback_cache_uses_actual_engine_and_announcement = (
                speech_cache.get((request, "openrouter", "Project. Session.")) is None
                and speech_cache.get((request, "haiku")) is None
            )
            assert fallback_cache_uses_actual_engine_and_announcement is True

        queue_answer()
        await _read_through(client, {**body, "announcement": "Different session."})
        changed_announcement_calls_pump = len(adaptation.pulled) == 2
        assert changed_announcement_calls_pump is True
        assert adaptation.pulled[-1][0] == "Different session."

        queue_answer()
        await _read_through(client, body)
        omitted_announcement_does_not_play_previous_prefix = (
            len(adaptation.pulled) == 3 and "Project. Session." not in adaptation.pulled[-1]
            and "Different session." not in adaptation.pulled[-1]
        )
        assert omitted_announcement_does_not_play_previous_prefix is True
        assert speech_cache.get((request, engine)) is not None
        # An empty or whitespace announcement keeps the existing two-field key.
        for announcement in ("", " \n "):
            await _read_through(client, {**body, "announcement": announcement})
        assert len(adaptation.pulled) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["openrouter", "script"])
def test_announcement_fallback_to_original_cache(client, adaptation, kind):
    fields = _store_script() if kind == "script" else None
    body = SCRIPT_CACHED_READ if kind == "script" else CACHED_PAYLOAD
    request = ScriptSpeechRequest(body["text"], **fields) if fields else parse_speech_request(body)
    adaptation.answers.append(RuntimeError("Adaptation unavailable"))

    async def scenario():
        await _read_through(client, {**body, "announcement": "Project. Session."})
        assert speech_cache.get((request, None, "Project. Session.")) is not None
        assert speech_cache.get((request, "haiku", "Project. Session.")) is None
        assert speech_cache.get((request, None)) is None
        _store_adapt("off")
        await _read_through(client, {**body, "announcement": "Project. Session."})
        assert adaptation.pulled == [["Project. Session.", body["text"]]]

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["direct", "off", "adapted", "fallback"])
def test_long_announcement_respects_script_limits(client, adaptation, direct_reading, path):
    _store_script(max_chars=30)
    announcement = "Long project title. " * 20 + "Long session title. " * 20
    if path == "off":
        _store_adapt("off")
    elif path == "adapted":
        adaptation.answers.append(["Adapted message."])
    elif path == "fallback":
        adaptation.answers.append(RuntimeError("Adaptation unavailable"))
    text = SIMPLE if path == "direct" else TABLE
    asyncio.run(_read_through(client, {**SCRIPT_READ, "text": text, "announcement": announcement}))
    parts = adaptation.pulled[0]
    assert len(parts) > 2
    assert all(0 < len(part) <= 30 for part in parts)
    assert " ".join(parts[:-1]).split() == announcement.split()
    assert parts[-1] == (CLEANED if path == "direct" else "Adapted message." if path == "adapted" else text)


@pytest.mark.parametrize("announcement", [None, 3, True, ["Project"], {"project": "Project"}])
@pytest.mark.parametrize("kind", ["openrouter", "script"])
def test_non_string_announcement_is_400_without_pump(client, adaptation, kind, announcement):
    if kind == "script":
        _store_script()
    body = SCRIPT_READ if kind == "script" else VALID_PAYLOAD
    response = asyncio.run(_open(client, {**body, "announcement": announcement}))
    assert response.status_code == 400
    assert response.json() == {"error": "'announcement' must be a string"}
    assert adaptation.sources == []
    assert adaptation.calls == []


def test_saved_summary_bypasses_adaptation_and_checks_exact_body_before_audio(client, upstream, monkeypatch):
    import hashlib
    from twicc.summaries import service
    text = 'Exact previous-policy summary.'
    saved = {'id': 8, 'text': text, 'result_hash': hashlib.sha256(text.encode()).hexdigest()}
    monkeypatch.setattr(service, 'load_saved_summary', lambda saved_id: saved.copy())
    monkeypatch.setattr(speech_service, '_stored_adapt_choice', lambda: 'openrouter')
    monkeypatch.setattr(speech_service, 'resolve_engine', lambda *_: pytest.fail('Summary invokes adaptation'))
    async def scenario():
        payload = {k: v for k, v in CACHED_PAYLOAD.items() if k != 'text'}
        payload.update(saved_summary_id=8, summary_result_hash=saved['result_hash'])
        upstream.pump.send('set_headers', 'audio/mpeg', None)
        upstream.pump.send('append', b'audio')
        upstream.pump.send('finish')
        response = await client.post(SPEECH_PATH, payload, content_type='application/json')
        assert response.status_code == 200
        assert upstream.pump.calls[0][0].text == text
        assert upstream.pump_args[0][0] == [text]
        saved.update(text='Replacement B.', result_hash=hashlib.sha256(b'Replacement B.').hexdigest())
        mismatch = await client.post(SPEECH_PATH, payload, content_type='application/json')
        assert mismatch.status_code == 409
        assert mismatch.json()['summary_changed'] is True
        assert len(upstream.pump.calls) == 1
        payload['summary_result_hash'] = saved['result_hash']
        upstream.pump.send('set_headers', 'audio/mpeg', None)
        upstream.pump.send('append', b'new audio')
        upstream.pump.send('finish')
        refreshed = await client.post(SPEECH_PATH, payload, content_type='application/json')
        assert refreshed.status_code == 200
        assert upstream.pump.calls[-1][0].text == 'Replacement B.'
        assert len(upstream.pump.calls) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('patch', [
    {'summary_id': 'missing', 'saved_summary_id': 1},
    {'saved_summary_id': 1},
    {'summary_id': ''},
    {'summary_id': 'x', 'config_id': 'test'},
    {'summary_id': 'x', 'text': 'Do not read original'},
])
def test_summary_request_rejects_ambiguous_or_test_sources(client, upstream, patch):
    async def scenario():
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != 'text'}
        async with asyncio.timeout(1):
            response = await client.post(SPEECH_PATH, {**payload, **patch}, content_type='application/json')
        assert response.status_code == 400
        assert not upstream.pump.calls
    asyncio.run(scenario())
