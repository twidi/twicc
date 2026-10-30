"""What TwiCC knows in advance about the OpenRouter speech models.

The OpenRouter models API says nothing about formats, streaming or input limits, so these facts
were measured through OpenRouter (and read in the providers' docs) on ``CATALOG_CHECKED``. They
cover only the models listed then: a model missing here gets no format or streaming claim and
``DEFAULT_MAX_CHARS``. Update the table when OpenRouter's list changes.
"""

from typing import NamedTuple

CATALOG_CHECKED = "2026-10-30"

# The input length assumed for a model missing from the table. Every model measured on
# CATALOG_CHECKED accepted 2,000 characters, except the ones the table limits further.
DEFAULT_MAX_CHARS = 2000


class SpeechModelFacts(NamedTuple):
    formats: tuple[str, ...]  # the response formats the model accepts
    streams: tuple[str, ...]  # the formats whose audio arrives while it is generated
    max_chars: int  # the longest input known to give a complete audio in one request


_BOTH = ("mp3", "pcm")
_NONE = ()

# max_chars: the provider's documented limit, else the longest length measured complete. 500 for
# the generative models (Gemini, Deepgram Flux, Seed Audio): they accept more, but above ~500
# characters their audio drifts (repeats, or stops early).
KNOWN_SPEECH_MODELS: dict[str, SpeechModelFacts] = {
    "assemblyai/assemblyai-tts-1": SpeechModelFacts(_BOTH, _BOTH, 5000),
    "bytedance-seed/seed-audio-1-0": SpeechModelFacts(_BOTH, _NONE, 500),
    "canopylabs/orpheus-3b-0.1-ft": SpeechModelFacts(_BOTH, _NONE, 2000),
    "deepgram/aura-2": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "deepgram/flux-tts": SpeechModelFacts(_BOTH, _BOTH, 500),
    "elevenlabs/eleven-flash-v2": SpeechModelFacts(_BOTH, _NONE, 30000),
    "elevenlabs/eleven-flash-v2.5": SpeechModelFacts(_BOTH, _NONE, 40000),
    "elevenlabs/eleven-multilingual-v2": SpeechModelFacts(_BOTH, _NONE, 10000),
    "elevenlabs/eleven-turbo-v2": SpeechModelFacts(_BOTH, _NONE, 30000),
    "elevenlabs/eleven-turbo-v2.5": SpeechModelFacts(_BOTH, _NONE, 40000),
    "elevenlabs/eleven-v3": SpeechModelFacts(_BOTH, _NONE, 5000),
    "elevenlabs/eleven-v3-conversational": SpeechModelFacts(_BOTH, _NONE, 5000),
    "elevenlabs/eleven-v4": SpeechModelFacts(_BOTH, _NONE, 10000),
    "elevenlabs/eleven-v4-turbo": SpeechModelFacts(_BOTH, _NONE, 10000),
    "fish-audio/drama-3": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "fish-audio/s1": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "fish-audio/s2-pro": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "fish-audio/s2.1-pro": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "fish-audio/s2.1-pro-free:free": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "google/gemini-3.1-flash-tts-preview": SpeechModelFacts(("pcm",), _NONE, 500),
    "google/gemini-3.8-flash-lite-tts": SpeechModelFacts(("pcm",), _NONE, 500),
    "google/gemini-3.8-flash-tts": SpeechModelFacts(("pcm",), _NONE, 500),
    "hexgrad/kokoro-82m": SpeechModelFacts(_BOTH, _NONE, 20000),
    "heygen/heygen-voice-1": SpeechModelFacts(("pcm",), ("pcm",), 5000),
    "heygen/heygen-voice-1-turbo": SpeechModelFacts(("pcm",), ("pcm",), 5000),
    "heygen/heygen-voice-1-turbo:free": SpeechModelFacts(("pcm",), ("pcm",), 5000),
    "microsoft/mai-voice-2": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "microsoft/mai-voice-2-flash": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "microsoft/mai-voice-2.1": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "microsoft/mai-voice-2.1-flash": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "minimax/speech-2.8-hd": SpeechModelFacts(("mp3",), ("mp3",), 9999),
    "minimax/speech-2.8-turbo": SpeechModelFacts(("mp3",), ("mp3",), 9999),
    "mistralai/voxtral-mini-tts-2603": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "mistralai/voxtral-mini-tts-3-0": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "qwen/qwen-audio-3.0-tts-flash": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "qwen/qwen-audio-3.0-tts-plus": SpeechModelFacts(_BOTH, _BOTH, 2000),
    "sesame/csm-1b": SpeechModelFacts(_BOTH, _NONE, 200),
    "x-ai/grok-voice-tts-1.0": SpeechModelFacts(_BOTH, _BOTH, 15000),
}

# The known models whose MP3 parts do not join into one playable file: their MP3 is sent whole,
# in one request (their PCM is still split). Every model checked on 2026-10-30 joined cleanly.
WHOLE_MP3_MODELS: frozenset[str] = frozenset()


def speech_model_facts(model_id: str) -> dict:
    """The known facts of a model, for the browser: ``formats`` and ``streams`` are lists, or
    None when the model is not in the table (nothing is known); ``max_chars`` is always set;
    ``whole_formats`` lists the formats sent whole, never split (``[]`` for an unknown model)."""
    whole_formats = ["mp3"] if model_id in WHOLE_MP3_MODELS else []
    facts = KNOWN_SPEECH_MODELS.get(model_id)
    if facts is None:
        return {"formats": None, "streams": None, "max_chars": DEFAULT_MAX_CHARS, "whole_formats": whole_formats}
    return {
        "formats": list(facts.formats),
        "streams": list(facts.streams),
        "max_chars": facts.max_chars,
        "whole_formats": whole_formats,
    }


def max_chars_of(model_id: str) -> int:
    """The longest input to send in one request to this model."""
    facts = KNOWN_SPEECH_MODELS.get(model_id)
    return facts.max_chars if facts else DEFAULT_MAX_CHARS
