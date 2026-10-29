"""Tests for the OpenRouter speech client (twicc.tts.openrouter).

The network is always mocked: every client comes from ``_make_client``,
monkeypatched to an ``httpx.MockTransport``.
"""

import asyncio
import io
import logging
import wave

import httpx
import orjson
import pytest

from twicc.tts import openrouter
from twicc.tts.openrouter import (
    GENERATION_URL,
    MODELS_URL,
    SPEECH_URL,
    SpeechAudio,
    SpeechError,
    SpeechRequest,
    _log_generation_cost,
    build_payload,
    fetch_speech_models,
    parse_pcm_format,
    synthesize,
    upstream_error_message,
    wrap_pcm_as_wav,
)

LOGGER_NAME = "twicc.tts.openrouter"
PCM_TYPE = "audio/pcm;rate=24000;channels=1"


@pytest.fixture(autouse=True)
def _clean_models_cache():
    openrouter._clear_models_cache()
    yield
    openrouter._clear_models_cache()


@pytest.fixture(autouse=True)
def _capture_module_logger(caplog, monkeypatch):
    # settings_test's ``disable_existing_loggers: True`` can disable this logger,
    # depending on the suite's import order (see test_compute_shutdown.py).
    # Re-enable it and route it straight to caplog, whatever its ancestors do.
    logger = logging.getLogger(LOGGER_NAME)
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


@pytest.mark.parametrize(
    "content_type",
    ["audio/pcm;rate=99999999999;channels=1", "audio/pcm;rate=24000;channels=40000"],
)
def test_malformed_pcm_header_falls_back_to_24k_mono(monkeypatch, caplog, content_type):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request):
        return httpx.Response(200, content=b"\x00\x00" * 10, headers={"content-type": content_type})

    _mock_transport(monkeypatch, handler)
    audio = asyncio.run(synthesize(_request("pcm"), "k"))

    with wave.open(io.BytesIO(audio.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 10)
    assert any(r.levelno == logging.WARNING for r in caplog.records if r.name == LOGGER_NAME)


def test_wrap_pcm_as_wav_header():
    wav = wrap_pcm_as_wav(b"\x00\x01" * 24000, 24000, 1)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 24000)


def test_synthesize_sends_bearer_and_wraps_pcm(monkeypatch):
    pcm = b"\x00\x01" * 2400

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == SPEECH_URL
        assert request.headers["authorization"] == "Bearer k"
        assert orjson.loads(request.content) == {
            "model": "openai/gpt-4o-mini-tts",
            "input": "Hello there",
            "response_format": "pcm",
            "voice": "Kore",
        }
        return httpx.Response(200, content=pcm, headers={"content-type": PCM_TYPE, "x-generation-id": "gen-1"})

    _mock_transport(monkeypatch, handler)
    audio = asyncio.run(synthesize(_request("pcm"), "k"))

    assert isinstance(audio, SpeechAudio)
    assert audio.content_type == "audio/wav"
    assert audio.generation_id == "gen-1"
    with wave.open(io.BytesIO(audio.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert w.readframes(w.getnframes()) == pcm


def test_synthesize_passes_mp3_through(monkeypatch):
    mp3 = b"ID3\x04fake-mp3-bytes"

    def handler(request):
        return httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg", "x-generation-id": "gen-2"})

    _mock_transport(monkeypatch, handler)
    audio = asyncio.run(synthesize(_request("mp3"), "k"))

    assert audio == SpeechAudio(content=mp3, content_type="audio/mpeg", generation_id="gen-2")


def test_synthesize_without_generation_id(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg"})

    _mock_transport(monkeypatch, handler)
    assert asyncio.run(synthesize(_request(), "k")).generation_id is None


def test_synthesize_decides_from_upstream_type_not_requested_format(monkeypatch):
    mp3 = b"ID3\x04fake-mp3-bytes"

    def handler(request):
        return httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg"})

    _mock_transport(monkeypatch, handler)
    audio = asyncio.run(synthesize(_request("pcm"), "k"))

    assert audio.content == mp3
    assert audio.content_type == "audio/mpeg"


def test_pcm_without_rate_falls_back_to_24k_mono(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request):
        return httpx.Response(200, content=b"\x00\x00" * 100, headers={"content-type": "audio/pcm"})

    _mock_transport(monkeypatch, handler)
    audio = asyncio.run(synthesize(_request("pcm"), "k"))

    assert audio.content_type == "audio/wav"
    with wave.open(io.BytesIO(audio.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 100)
    warnings = [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "audio/pcm" in warnings[0].getMessage()


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


def test_upstream_error_status_is_relayed(monkeypatch):
    body = orjson.dumps({"error": {"message": "User not found.", "code": 401}})

    def handler(request):
        return httpx.Response(401, content=body, headers={"content-type": "application/json"})

    _mock_transport(monkeypatch, handler)
    with pytest.raises(SpeechError) as exc_info:
        asyncio.run(synthesize(_request(), "k"))

    assert exc_info.value.message == "User not found."
    assert exc_info.value.upstream_status == 401


def test_non_audio_success_is_an_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b'{"ok": true}', headers={"content-type": "application/json"})

    _mock_transport(monkeypatch, handler)
    with pytest.raises(SpeechError) as exc_info:
        asyncio.run(synthesize(_request(), "k"))

    assert exc_info.value.message == "OpenRouter returned HTTP 200"
    assert exc_info.value.upstream_status == 200


def test_non_audio_success_keeps_the_upstream_message(monkeypatch):
    body = orjson.dumps({"error": {"message": "Model is overloaded"}})

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    _mock_transport(monkeypatch, handler)
    with pytest.raises(SpeechError) as exc_info:
        asyncio.run(synthesize(_request(), "k"))

    assert exc_info.value.message == "Model is overloaded"


def test_network_error_message_hides_details(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("secret-ish detail")

    _mock_transport(monkeypatch, handler)
    with pytest.raises(SpeechError) as exc_info:
        asyncio.run(synthesize(_request(), "k"))

    assert exc_info.value.message == "Could not reach OpenRouter"
    assert exc_info.value.upstream_status is None
    assert "secret-ish" not in str(exc_info.value)


def test_timeout_message(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow detail")

    _mock_transport(monkeypatch, handler)
    with pytest.raises(SpeechError) as exc_info:
        asyncio.run(synthesize(_request(), "k"))

    assert exc_info.value.message == "OpenRouter did not answer in time"
    assert exc_info.value.upstream_status is None


def test_synthesize_uses_the_speech_timeout(monkeypatch):
    seen = []

    def factory(timeout):
        seen.append(timeout)
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x", headers={"content-type": "audio/mpeg"})),
            timeout=timeout,
        )

    monkeypatch.setattr(openrouter, "_make_client", factory)
    asyncio.run(synthesize(_request(), "k"))

    assert seen == [openrouter.SPEECH_TIMEOUT]
    assert openrouter.SPEECH_TIMEOUT.read == 180.0
    assert openrouter.SPEECH_TIMEOUT.connect == 15.0


def test_cancelled_request_is_logged_and_reraised(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, content=b"x", headers={"content-type": "audio/mpeg"})

    _mock_transport(monkeypatch, handler)

    async def main():
        task = asyncio.create_task(synthesize(_request(), "k"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.INFO]
    assert len(lines) == 1
    assert "cancelled" in lines[0]


def _capture_http_stack_logs(caplog, monkeypatch):
    """Capture httpx and httpcore at DEBUG too, so a key leak there is caught."""
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
        caplog.set_level(logging.DEBUG, logger=name)


def test_logs_never_contain_the_key(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    _capture_http_stack_logs(caplog, monkeypatch)
    key = "sk-or-XYZ"
    responses = iter(
        [
            httpx.Response(200, content=b"mp3", headers={"content-type": "audio/mpeg", "x-generation-id": "gen-1"}),
            httpx.Response(400, content=orjson.dumps({"error": {"message": "Provider returned 400", "code": 400}})),
        ]
    )

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {key}"
        return next(responses)

    _mock_transport(monkeypatch, handler)
    asyncio.run(synthesize(_request(), key))
    with pytest.raises(SpeechError):
        asyncio.run(synthesize(_request(), key))

    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.INFO]
    assert len(lines) == 2
    assert "gen-1" in lines[0] and "ok" in lines[0]
    assert "openai/gpt-4o-mini-tts" in lines[0] and "Kore" in lines[0] and "mp3" in lines[0]
    assert "upstream_error 400" in lines[1]
    # The HTTP stack logs reached caplog too (httpx logs each request).
    assert any(r.name == "httpx" for r in caplog.records)
    assert key not in caplog.text


def test_unexpected_error_is_logged_as_error(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request):
        raise RuntimeError("boom")

    _mock_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        asyncio.run(synthesize(_request(), "k"))

    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.INFO]
    assert len(lines) == 1
    assert lines[0].endswith("outcome=error")


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
    expected = [
        {"id": "openai/gpt-4o-mini-tts", "name": "OpenAI: GPT-4o mini TTS", "voices": ["alloy", "nova"]},
        {"id": "google/gemini-tts", "name": "google/gemini-tts", "voices": []},
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

    assert asyncio.run(fetch_speech_models()) == [{"id": "m", "name": "M", "voices": ["alloy"]}]


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
