"""Speech to text: format/language normalization, the two server engines, the view."""

import asyncio

import httpx
import orjson
import pytest
from django.test import AsyncClient

from twicc.stt import engines
from twicc.stt.engines import TranscriptionError, normalize_format, normalize_language


@pytest.mark.parametrize("value,expected", [
    ("audio/webm;codecs=opus", "webm"),
    ("audio/mp4", "m4a"),
    ("audio/ogg; codecs=opus", "ogg"),
    ("audio/x-wav", "wav"),
    ("webm", "webm"),
    ("audio/x-matroska", None),
    ("", None),
])
def test_normalize_format(value, expected):
    assert normalize_format(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("pt-BR", "pt"), ("en", "en"), ("EN_us", "en"), ("", None), ("auto", None), (None, None), ("12", None),
])
def test_normalize_language(value, expected):
    assert normalize_language(value) == expected


def _mock_openrouter(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(engines.httpx, "AsyncClient", factory)


def test_openrouter_sends_model_language_and_file(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": "  olá   mundo ", "usage": {"seconds": 2.5, "cost": 0.0001}})

    _mock_openrouter(monkeypatch, handler)
    result = asyncio.run(engines.transcribe_openrouter(
        b"AUDIO", "webm", api_key="sk-test", model="", language="pt"))
    assert result.text == "olá mundo"
    assert result.seconds == 2.5 and result.cost == 0.0001
    assert seen["auth"] == "Bearer sk-test"
    assert b'name="model"' in seen["body"] and engines.DEFAULT_MODEL.encode() in seen["body"]
    assert b'filename="dictation.webm"' in seen["body"]
    assert b'name="language"' in seen["body"]


def test_openrouter_without_key_is_refused():
    with pytest.raises(TranscriptionError) as exc:
        asyncio.run(engines.transcribe_openrouter(b"A", "webm", api_key=None, model=None, language=None))
    assert exc.value.status == 409


def test_openrouter_error_never_leaks_the_body(monkeypatch):
    _mock_openrouter(monkeypatch, lambda request: httpx.Response(401, json={"error": {"message": "bad key sk-x"}}))
    with pytest.raises(TranscriptionError) as exc:
        asyncio.run(engines.transcribe_openrouter(b"A", "webm", api_key="sk-x", model=None, language=None))
    assert "sk-x" not in exc.value.message


def test_script_reads_the_recording_and_prints_the_text():
    command = 'printf "%s|%s|" "$TWICC_STT_FORMAT" "$TWICC_STT_LANGUAGE"; cat "$TWICC_STT_FILE"'
    result = asyncio.run(engines.transcribe_script(b"hello world", "m4a", command=command, language="en"))
    assert result.text == "m4a|en|hello world"


def test_script_failure_and_missing_command():
    with pytest.raises(TranscriptionError) as exc:
        asyncio.run(engines.transcribe_script(b"x", "webm", command="exit 3", language=None))
    assert exc.value.status == 502
    with pytest.raises(TranscriptionError) as exc:
        asyncio.run(engines.transcribe_script(b"x", "webm", command="  ", language=None))
    assert exc.value.status == 409


def test_script_timeout_stops_the_process(monkeypatch):
    monkeypatch.setattr(engines, "SCRIPT_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(TranscriptionError) as exc:
        asyncio.run(engines.transcribe_script(b"x", "webm", command="sleep 30", language=None))
    assert exc.value.status == 504


def test_view_uses_the_synced_script_command(monkeypatch, settings):
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {"sttScriptCommand": 'cat "$TWICC_STT_FILE"', "sttLanguage": ""},
    )
    client = AsyncClient()
    from django.core.files.uploadedfile import SimpleUploadedFile

    audio = SimpleUploadedFile("rec.webm", b"dictated text", content_type="audio/webm")
    response = asyncio.run(client.post("/api/stt/transcribe/", {"audio": audio, "engine": "script"}))
    assert response.status_code == 200, response.content
    assert orjson.loads(response.content) == {"text": "dictated text", "engine": "script"}


def test_view_rejects_unknown_engine_and_format(settings):
    settings.TWICC_PASSWORD_HASH = ""
    client = AsyncClient()
    from django.core.files.uploadedfile import SimpleUploadedFile

    audio = SimpleUploadedFile("rec.bin", b"x", content_type="audio/webm")
    assert asyncio.run(client.post("/api/stt/transcribe/", {"audio": audio, "engine": "browser"})).status_code == 400
    audio = SimpleUploadedFile("rec.bin", b"x", content_type="video/x-matroska")
    response = asyncio.run(client.post("/api/stt/transcribe/", {"audio": audio, "engine": "script"}))
    assert response.status_code == 415
