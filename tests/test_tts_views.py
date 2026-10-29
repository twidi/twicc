"""Tests for the text-to-speech REST endpoints (twicc.tts.views).

The upstream calls are always mocked: ``synthesize``, ``fetch_speech_models``,
``schedule_cost_log`` and ``openrouter_api_key`` are monkeypatched on the views module.
"""

import asyncio
import logging

import orjson
import pytest
from django.test import AsyncClient, AsyncRequestFactory

from twicc.tts import views
from twicc.tts.openrouter import SpeechAudio, SpeechError, SpeechRequest
from twicc.tts.views import parse_speech_request

SPEECH_PATH = "/api/tts/speech/"
MODELS_PATH = "/api/tts/models/"

VALID_PAYLOAD = {
    "text": "Hello there",
    "model": "openai/gpt-4o-mini-tts",
    "voice": "alloy",
    "response_format": "mp3",
    "speed": 1.0,
    "instructions": "",
}


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def calls(monkeypatch):
    """Record the upstream calls; the key is ``"k"`` and synthesize must be set per test."""
    recorded = {"synthesize": [], "cost": []}

    async def _no_synthesize(request, api_key):
        raise AssertionError("synthesize must not be called")

    monkeypatch.setattr(views, "openrouter_api_key", lambda: "k")
    monkeypatch.setattr(views, "synthesize", _no_synthesize)
    monkeypatch.setattr(views, "schedule_cost_log", lambda gen_id, key: recorded["cost"].append((gen_id, key)))
    return recorded


def _run(coro):
    return asyncio.run(coro)


def _post_json(client, body):
    return _run(client.post(SPEECH_PATH, data=orjson.dumps(body), content_type="application/json"))


def _set_synthesize(monkeypatch, calls, outcome):
    """Make ``synthesize`` record its call, then return *outcome* or raise it."""

    async def _synthesize(request, api_key):
        calls["synthesize"].append((request, api_key))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(views, "synthesize", _synthesize)


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


@pytest.mark.parametrize(
    "data",
    [
        [],
        "text",
        None,
        {**VALID_PAYLOAD, "text": ""},
        {**VALID_PAYLOAD, "text": "   \n"},
        {**VALID_PAYLOAD, "text": 12},
        {key: value for key, value in VALID_PAYLOAD.items() if key != "model"},
        {**VALID_PAYLOAD, "model": "  "},
        {**VALID_PAYLOAD, "model": 3},
        {**VALID_PAYLOAD, "voice": 3},
        {**VALID_PAYLOAD, "instructions": ["x"]},
        {**VALID_PAYLOAD, "response_format": "ogg"},
        {key: value for key, value in VALID_PAYLOAD.items() if key != "response_format"},
        {**VALID_PAYLOAD, "speed": 0},
        {**VALID_PAYLOAD, "speed": -1},
        {**VALID_PAYLOAD, "speed": True},
        {**VALID_PAYLOAD, "speed": "1.0"},
        {**VALID_PAYLOAD, "speed": float("inf")},
        {**VALID_PAYLOAD, "speed": float("nan")},
        {**VALID_PAYLOAD, "speed": 10**400},
    ],
)
def test_parse_speech_request_rejects(data):
    with pytest.raises(ValueError):
        parse_speech_request(data)


# --- POST /api/tts/speech/ ------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in VALID_PAYLOAD.items() if key != "text"},
        {**VALID_PAYLOAD, "model": "   "},
        {**VALID_PAYLOAD, "response_format": "ogg"},
        {**VALID_PAYLOAD, "speed": 0},
    ],
)
def test_rejects_invalid_payloads(client, calls, monkeypatch, body):
    # An invalid payload is a 400 even without a key: validation runs first.
    monkeypatch.setattr(views, "openrouter_api_key", lambda: None)
    response = _post_json(client, body)
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)


def test_rejects_invalid_json(client, calls):
    response = _run(client.post(SPEECH_PATH, data=b"{not json", content_type="application/json"))
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid JSON"}


def test_speech_rejects_other_methods(client, calls):
    response = _run(client.get(SPEECH_PATH))
    assert response.status_code == 405
    assert response["Allow"] == "POST"
    assert isinstance(response.json()["error"], str)


def test_no_key_returns_503_without_upstream_call(client, calls, monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(views, "openrouter_api_key", lambda: None)
    monkeypatch.setattr(views, "get_env_path", lambda: env_path)
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 503
    assert response.json() == {
        "error": f"TWICC_OPENROUTER_API_KEY is not set. Add it to {env_path} and restart TwiCC."
    }
    assert calls["synthesize"] == []
    assert calls["cost"] == []


def test_success_returns_audio_and_schedules_cost(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechAudio(b"ID3audio", "audio/mpeg", "gen-1"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 200
    assert response.content == b"ID3audio"
    assert response["Content-Type"] == "audio/mpeg"
    assert response["Cache-Control"] == "no-store"
    assert calls["synthesize"] == [(parse_speech_request(VALID_PAYLOAD), "k")]
    assert calls["cost"] == [("gen-1", "k")]


def test_success_without_generation_id_schedules_no_cost(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechAudio(b"RIFFwav", "audio/wav", None))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 200
    assert response["Content-Type"] == "audio/wav"
    assert calls["cost"] == []


def test_upstream_401_is_not_relayed_as_401(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechError("No auth credentials found", upstream_status=401))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 502
    assert response.json() == {"error": "No auth credentials found", "upstream_status": 401}
    assert calls["cost"] == []


def test_upstream_error_without_status_has_no_upstream_status(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechError("Could not reach OpenRouter"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 502
    assert response.json() == {"error": "Could not reach OpenRouter"}


def test_unexpected_exception_returns_own_json_500(client, calls, monkeypatch, settings, caplog):
    settings.DEBUG = True
    # settings_test's ``disable_existing_loggers: True`` can disable this logger,
    # depending on the suite's import order: route it straight to caplog.
    logger = logging.getLogger("twicc.tts.views")
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    _set_synthesize(monkeypatch, calls, RuntimeError("sk-or-leak"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": "Text-to-speech failed"}
    assert b"sk-or-leak" not in response.content
    assert any(record.exc_info for record in caplog.records)


def test_cancellation_propagates(calls, monkeypatch):
    # A client disconnect cancels the view task: the catch-all must not swallow it.
    _set_synthesize(monkeypatch, calls, asyncio.CancelledError())
    request = AsyncRequestFactory().post(
        SPEECH_PATH, data=orjson.dumps(VALID_PAYLOAD), content_type="application/json"
    )
    with pytest.raises(asyncio.CancelledError):
        _run(views.tts_speech(request))


def test_oversized_body_returns_413_json(client, calls, settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 413
    assert response.json() == {"error": "The text is too long to send"}


# --- GET /api/tts/models/ -------------------------------------------------------


def test_models_endpoint(client, monkeypatch):
    models = [{"id": "openai/gpt-4o-mini-tts", "name": "GPT-4o mini TTS", "voices": ["alloy"]}]

    async def _fetch():
        return models

    monkeypatch.setattr(views, "fetch_speech_models", _fetch)
    response = _run(client.get(MODELS_PATH))
    assert response.status_code == 200
    assert response.json() == {"models": models}


def test_models_rejects_other_methods(client):
    response = _run(client.post(MODELS_PATH))
    assert response.status_code == 405
    assert response["Allow"] == "GET"
