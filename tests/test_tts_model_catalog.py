"""Tests for the known speech model facts (twicc.tts.model_catalog)."""

import asyncio

import httpx
import orjson

from twicc.tts import model_catalog, openrouter
from twicc.tts.model_catalog import (
    DEFAULT_MAX_CHARS,
    KNOWN_SPEECH_MODELS,
    WHOLE_MP3_MODELS,
    max_chars_of,
    speech_model_facts,
)


def test_every_known_model_is_consistent():
    for model_id, facts in KNOWN_SPEECH_MODELS.items():
        assert facts.formats, model_id
        assert set(facts.formats) <= {"mp3", "pcm"}, model_id
        # A format can only stream if the model accepts it.
        assert set(facts.streams) <= set(facts.formats), model_id
        assert facts.max_chars > 0, model_id
    # A model whose MP3 is sent whole must be a known model that accepts MP3.
    for model_id in WHOLE_MP3_MODELS:
        assert model_id in KNOWN_SPEECH_MODELS, model_id
        assert "mp3" in KNOWN_SPEECH_MODELS[model_id].formats, model_id


def test_known_model_facts_for_the_browser(monkeypatch):
    monkeypatch.setattr(model_catalog, "WHOLE_MP3_MODELS", frozenset())
    assert speech_model_facts("heygen/heygen-voice-1-turbo:free") == {
        "formats": ["pcm"], "streams": ["pcm"], "max_chars": 5000, "whole_formats": [],
    }
    assert speech_model_facts("minimax/speech-2.8-hd") == {
        "formats": ["mp3"], "streams": ["mp3"], "max_chars": 9999, "whole_formats": [],
    }
    assert speech_model_facts("sesame/csm-1b") == {
        "formats": ["mp3", "pcm"], "streams": [], "max_chars": 200, "whole_formats": [],
    }


def test_whole_mp3_models_reach_the_browser(monkeypatch):
    monkeypatch.setattr(model_catalog, "WHOLE_MP3_MODELS", frozenset({"elevenlabs/eleven-v3"}))
    assert speech_model_facts("elevenlabs/eleven-v3")["whole_formats"] == ["mp3"]


def test_an_unknown_model_claims_nothing_but_the_default_length():
    assert speech_model_facts("someone/new-tts") == {
        "formats": None, "streams": None, "max_chars": DEFAULT_MAX_CHARS, "whole_formats": [],
    }
    assert DEFAULT_MAX_CHARS == 2000
    assert max_chars_of("someone/new-tts") == 2000
    assert max_chars_of("sesame/csm-1b") == 200


def test_the_model_list_carries_the_facts(monkeypatch):
    payload = {"data": [{"id": "google/gemini-3.8-flash-tts", "name": "Gemini"}, {"id": "x/unknown", "name": "X"}]}

    def handler(request):
        return httpx.Response(200, content=orjson.dumps(payload), headers={"content-type": "application/json"})

    monkeypatch.setattr(openrouter, "_make_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    openrouter._clear_models_cache()
    try:
        models = asyncio.run(openrouter.fetch_speech_models())
    finally:
        openrouter._clear_models_cache()
    gemini, unknown = models
    assert (gemini["formats"], gemini["streams"], gemini["max_chars"]) == (["pcm"], [], 500)
    assert (unknown["formats"], unknown["streams"], unknown["max_chars"]) == (None, None, 2000)
    assert gemini["whole_formats"] == unknown["whole_formats"] == []
    # A caller mutating its copy never reaches the table.
    gemini["formats"].append("mp3")
    assert KNOWN_SPEECH_MODELS["google/gemini-3.8-flash-tts"].formats == ("pcm",)
