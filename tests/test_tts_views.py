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
    monkeypatch.setattr(views, "schedule_cost_log", lambda gen_id, key, on_cost=None: recorded["cost"].append((gen_id, key)))
    return recorded


@pytest.fixture
def views_log(caplog, monkeypatch):
    """Route the views logger straight to caplog.

    settings_test's ``disable_existing_loggers: True`` can disable this logger,
    depending on the suite's import order.
    """
    logger = logging.getLogger("twicc.tts.views")
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    return caplog


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


# --- POST /api/tts/speech/ ------------------------------------------------------


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
def test_rejects_invalid_payloads(client, calls, monkeypatch, body, message):
    # An invalid payload is a 400 even without a key: validation runs first.
    monkeypatch.setattr(views, "openrouter_api_key", lambda: None)
    response = _post_json(client, body)
    assert response.status_code == 400
    assert response.json() == {"error": message}


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


def test_cost_log_failure_keeps_the_audio(client, calls, monkeypatch, views_log):
    # The audio is billed already: a failing cost lookup must not turn it into a 500.
    _set_synthesize(monkeypatch, calls, SpeechAudio(b"ID3audio", "audio/mpeg", "gen-1"))

    def _broken_cost_log(generation_id, api_key, on_cost=None):
        raise RuntimeError("no running loop")

    monkeypatch.setattr(views, "schedule_cost_log", _broken_cost_log)
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 200
    assert response.content == b"ID3audio"
    assert response["Content-Type"] == "audio/mpeg"
    errors = [record for record in views_log.records if record.exc_info]
    assert [record.getMessage() for record in errors] == ["TTS cost lookup for gen-1 could not be scheduled"]


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


def test_unexpected_exception_returns_own_json_500(client, calls, monkeypatch, settings, views_log):
    settings.DEBUG = True
    _set_synthesize(monkeypatch, calls, RuntimeError("sk-or-leak"))
    response = _post_json(client, VALID_PAYLOAD)
    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"
    assert response.json() == {"error": "Text-to-speech failed"}
    assert b"sk-or-leak" not in response.content
    assert any(record.exc_info for record in views_log.records)


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


def test_a_settings_test_records_its_run_then_its_cost(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechAudio(b"ID3audio", "audio/mpeg", "gen-1"))
    recorded = []

    async def fake_record(config_id, update):
        recorded.append((config_id, update))

    scheduled = []
    monkeypatch.setattr(views, "_record_last_test", fake_record)
    monkeypatch.setattr(views, "schedule_cost_log", lambda gen_id, key, on_cost=None: scheduled.append(on_cost))
    response = _post_json(client, {**VALID_PAYLOAD, "config_id": "cfg-1"})
    assert response.status_code == 200
    [(config_id, run)] = recorded
    assert config_id == "cfg-1"
    assert run["generation_id"] == "gen-1"
    assert run["chars"] == len(VALID_PAYLOAD["text"])
    assert run["cost"] is None and run["generation_ms"] >= 0 and "at" in run
    # The cost callback updates the same run once the lookup finds it.
    asyncio.run(scheduled[0](0.008))
    assert recorded[-1] == ("cfg-1", {"generation_id": "gen-1", "cost": 0.008})


def test_a_message_read_records_no_run(client, calls, monkeypatch):
    _set_synthesize(monkeypatch, calls, SpeechAudio(b"ID3audio", "audio/mpeg", "gen-1"))
    recorded = []

    async def fake_record(config_id, update):
        recorded.append(config_id)

    monkeypatch.setattr(views, "_record_last_test", fake_record)
    assert _post_json(client, VALID_PAYLOAD).status_code == 200
    assert recorded == []
