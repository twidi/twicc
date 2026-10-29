"""Async client for the OpenRouter text-to-speech API.

Three calls: synthesize a text (``POST /audio/speech``), list the speech models
(``GET /models?output_modalities=speech``), and log the real cost of a
generation (``GET /generation?id=…``) in the background.

The API key is always a parameter: this module never reads it from the
environment, and never puts it (or an exception text, or a header) in a log
line or an error message.
"""

import asyncio
import io
import logging
import time
import wave
from typing import NamedTuple

import httpx
import orjson

logger = logging.getLogger(__name__)

SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=speech"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"

# 20 000 characters took 19 s: leave room for long texts and slow providers.
SPEECH_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
# Models list and cost lookup: small JSON answers.
LOOKUP_TIMEOUT = httpx.Timeout(15.0)

MODELS_CACHE_TTL = 600.0

GENERIC_PROVIDER_ERROR = "Provider returned 400"
GENERIC_PROVIDER_HINT = ". Check the model, the voice, the format and the text length."

# PCM fallback when the upstream Content-Type has no rate.
DEFAULT_PCM_RATE = 24000
DEFAULT_PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2  # 16-bit little-endian
# Above these, the header is malformed (and ``wave`` cannot write it).
MAX_PCM_RATE = 384000
MAX_PCM_CHANNELS = 8


class SpeechRequest(NamedTuple):
    text: str
    model: str
    voice: str
    response_format: str
    speed: float | None
    instructions: str


class SpeechAudio(NamedTuple):
    content: bytes
    content_type: str
    generation_id: str | None


class SpeechError(Exception):
    """A speech request failed. ``message`` is safe to show to the user."""

    def __init__(self, message: str, upstream_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status


def _make_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """The single client factory (tests replace it with a mock transport)."""
    return httpx.AsyncClient(timeout=timeout)


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def build_payload(request: SpeechRequest) -> dict:
    """The JSON body of a speech request; empty optional fields are not sent."""
    payload = {"model": request.model, "input": request.text, "response_format": request.response_format}
    if request.voice:
        payload["voice"] = request.voice
    if request.speed is not None:
        payload["speed"] = request.speed
    if request.instructions:
        payload["instructions"] = request.instructions
    return payload


def parse_pcm_format(content_type: str) -> tuple[int, int] | None:
    """``(rate, channels)`` from ``audio/pcm;rate=24000;channels=1``.

    None without a rate, or when a value is not a plausible integer.
    """
    params = {}
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        params[name.strip().lower()] = value.strip()
    try:
        rate = int(params["rate"])
        channels = int(params.get("channels") or DEFAULT_PCM_CHANNELS)
    except (KeyError, ValueError):
        return None
    if not (0 < rate <= MAX_PCM_RATE and 0 < channels <= MAX_PCM_CHANNELS):
        return None
    return rate, channels


def wrap_pcm_as_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    """Raw 16-bit PCM samples wrapped in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def upstream_error_message(status: int, body: bytes) -> str:
    """OpenRouter's ``error.message`` from *body*, else a message built from *status*."""
    message = None
    try:
        payload = orjson.loads(body)
    except orjson.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        raw = payload["error"].get("message")
        if isinstance(raw, str) and raw:
            message = raw
    if message is None:
        return f"OpenRouter returned HTTP {status}"
    if message == GENERIC_PROVIDER_ERROR:
        return message + GENERIC_PROVIDER_HINT
    return message


def _audio_from_response(response: httpx.Response) -> SpeechAudio:
    """Decide from the upstream ``Content-Type`` (not the requested format)."""
    status = response.status_code
    if not response.is_success:
        raise SpeechError(upstream_error_message(status, response.content), upstream_status=status)

    content_type = response.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    generation_id = response.headers.get("x-generation-id") or None

    if media_type == "audio/pcm":
        pcm_format = parse_pcm_format(content_type)
        if pcm_format is None:
            logger.warning(
                "TTS PCM response without a usable rate/channels (%r); assuming %s Hz mono",
                content_type,
                DEFAULT_PCM_RATE,
            )
            pcm_format = (DEFAULT_PCM_RATE, DEFAULT_PCM_CHANNELS)
        rate, channels = pcm_format
        return SpeechAudio(wrap_pcm_as_wav(response.content, rate, channels), "audio/wav", generation_id)

    if media_type.startswith("audio/"):
        return SpeechAudio(response.content, content_type, generation_id)

    # A success that is not audio (JSON, HTML, ...): its message, else its status.
    raise SpeechError(upstream_error_message(status, response.content), upstream_status=status)


async def synthesize(request: SpeechRequest, api_key: str) -> SpeechAudio:
    """Turn *request* into audio. Raises :class:`SpeechError` with a user-safe message."""
    started = time.monotonic()
    outcome = "error"
    audio_size = 0
    generation_id = None
    try:
        async with _make_client(SPEECH_TIMEOUT) as client:
            response = await client.post(SPEECH_URL, json=build_payload(request), headers=_auth_headers(api_key))
        generation_id = response.headers.get("x-generation-id") or None
        audio = _audio_from_response(response)
        audio_size = len(audio.content)
        outcome = "ok"
        return audio
    except SpeechError as error:
        outcome = f"upstream_error {error.upstream_status}"
        raise
    except httpx.TimeoutException:
        outcome = "timeout"
        raise SpeechError("OpenRouter did not answer in time") from None
    except httpx.HTTPError:
        outcome = "unreachable"
        raise SpeechError("Could not reach OpenRouter") from None
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        logger.info(
            "TTS speech model=%s voice=%s format=%s chars=%d bytes=%d duration_ms=%d generation=%s outcome=%s",
            request.model,
            request.voice,
            request.response_format,
            len(request.text),
            audio_size,
            round((time.monotonic() - started) * 1000),
            generation_id,
            outcome,
        )


# (monotonic time of the fetch, models) of the last successful fetch.
_models_cache: tuple[float, list[dict]] | None = None


def _clear_models_cache() -> None:
    """Forget the cached models list (tests only)."""
    global _models_cache
    _models_cache = None


def _voices(entry: dict) -> list[str]:
    """The string items of ``supported_voices``; ``[]`` when it is not a list."""
    voices = entry.get("supported_voices")
    if not isinstance(voices, list):
        return []
    return [voice for voice in voices if isinstance(voice, str)]


def _copy_models(models: list[dict]) -> list[dict]:
    """A copy a caller can mutate without reaching the cache."""
    return [{**model, "voices": list(model["voices"])} for model in models]


async def fetch_speech_models() -> list[dict]:
    """The speech models as ``[{"id", "name", "voices"}]``; ``[]`` on any failure.

    A success is cached ``MODELS_CACHE_TTL`` seconds; a failure is not cached.
    """
    global _models_cache
    if _models_cache is not None and time.monotonic() - _models_cache[0] < MODELS_CACHE_TTL:
        return _copy_models(_models_cache[1])
    try:
        async with _make_client(LOOKUP_TIMEOUT) as client:
            response = await client.get(MODELS_URL)
        response.raise_for_status()
        entries = orjson.loads(response.content)["data"]
        models = [
            {"id": entry["id"], "name": entry.get("name") or entry["id"], "voices": _voices(entry)}
            for entry in entries
        ]
    except Exception:  # noqa: BLE001 - suggestions only: the fields stay free text
        logger.debug("TTS models list unavailable")
        return []
    _models_cache = (time.monotonic(), models)
    return _copy_models(models)


# Strong references to the running cost lookups (asyncio keeps only weak ones).
_cost_tasks: set[asyncio.Task] = set()


def schedule_cost_log(generation_id: str, api_key: str) -> None:
    """Log the real cost of *generation_id* in the background (fire-and-forget)."""
    task = asyncio.get_running_loop().create_task(_log_generation_cost(generation_id, api_key))
    _cost_tasks.add(task)
    task.add_done_callback(_cost_tasks.discard)


async def _log_generation_cost(
    generation_id: str,
    api_key: str,
    *,
    first_delay: float = 15.0,
    retry_delay: float = 5.0,
    attempts: int = 4,
) -> None:
    """Fetch and log the cost of a generation. Never raises (it runs fire-and-forget).

    OpenRouter answers 404 for some seconds after the speech response: wait
    *first_delay*, then retry every *retry_delay* on 404, *attempts* times at most.
    """
    try:
        await asyncio.sleep(first_delay)
        async with _make_client(LOOKUP_TIMEOUT) as client:
            for attempt in range(attempts):
                if attempt:
                    await asyncio.sleep(retry_delay)
                response = await client.get(
                    GENERATION_URL, params={"id": generation_id}, headers=_auth_headers(api_key)
                )
                if response.status_code == 404:
                    continue
                if not response.is_success:
                    logger.debug("TTS cost %s: lookup answered HTTP %s", generation_id, response.status_code)
                    return
                data = orjson.loads(response.content)["data"]
                logger.info(
                    "TTS cost %s: $%s (provider %s)",
                    generation_id,
                    data.get("total_cost"),
                    data.get("provider_name"),
                )
                return
        logger.debug("TTS cost %s: still not found after %d attempts", generation_id, attempts)
    except Exception:  # noqa: BLE001 - fire-and-forget: never raise, never log the exception text
        logger.debug("TTS cost %s: lookup failed", generation_id)
