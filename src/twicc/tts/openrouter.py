"""Async client for the OpenRouter text-to-speech API.

Three calls: stream the speech of a text, in one request or part by part, into
an audio stream (``POST /audio/speech``, :func:`pump_speech`), list the speech
models (``GET /models?output_modalities=speech``), and log the real cost of the
generations of a speech (``GET /generation?id=…``) in the background.

The API key is always a parameter: this module never reads it from the
environment, and never puts it (or an exception text, or a header) in a log
line or an error message.
"""

import asyncio
import logging
import math
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

import httpx
import orjson

from twicc.tts.model_catalog import speech_model_facts
from twicc.tts.part_audio import MP3_HEADER_SIZE, Mp3HeadFilter, PcmFrameTrimmer, id3_tag_size, mp3_frame
from twicc.tts.part_source import PartSource, StaticParts

if TYPE_CHECKING:
    # streams.py imports SpeechError from here: the type only, at check time.
    from twicc.tts.streams import AudioStream

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
# Above these, the PCM type is malformed.
MAX_PCM_RATE = 384000
MAX_PCM_CHANNELS = 8
# The MP3 byte rate (for pacing and lookahead) is read in the first bytes of the audio only.
RATE_SCAN_BYTES = 64 * 1024


class SpeechRequest(NamedTuple):
    text: str
    model: str
    voice: str
    response_format: str
    speed: float | None
    instructions: str


class SpeechError(Exception):
    """A speech request failed. ``message`` is safe to show to the user.

    ``log_message`` (default: ``message``) is what the TTS log line writes: a script failure
    shows its stderr line to the user, but logs its exit status only.
    """

    def __init__(self, message: str, upstream_status: int | None = None, *, log_message: str | None = None):
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status
        self.log_message = message if log_message is None else log_message


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


def _pcm_format_or_default(content_type: str) -> tuple[int, int]:
    """``(rate, channels)`` of a PCM ``Content-Type``; 24 kHz mono, with a warning, when unusable."""
    pcm_format = parse_pcm_format(content_type)
    if pcm_format is None:
        logger.warning(
            "TTS PCM response without a usable rate/channels (%r); assuming %s Hz mono",
            content_type,
            DEFAULT_PCM_RATE,
        )
        pcm_format = (DEFAULT_PCM_RATE, DEFAULT_PCM_CHANNELS)
    return pcm_format


def pcm_content_type(content_type: str) -> str:
    """The upstream PCM ``Content-Type`` with an explicit rate and channel count."""
    rate, channels = _pcm_format_or_default(content_type)
    return f"audio/pcm;rate={rate};channels={channels}"


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


def part_source(text: str, parts: Sequence[str] | PartSource | None) -> PartSource:
    """The source a pump pulls its parts from: *parts* itself, or a static source of the parts
    (None: *text* alone, in one part)."""
    if parts is None:
        return StaticParts([text])
    if isinstance(parts, PartSource):
        return parts
    return StaticParts(parts)


async def timed_next_part(source: PartSource, stream: "AudioStream") -> str | None:
    """The next part of *source*; the wait for it counts as generation time of *stream* (the
    test times then include the adaptation of the text). May raise ``SpeechError``."""
    started = time.monotonic()
    text = await source.next_part()
    stream.add_generation_seconds(time.monotonic() - started)
    return text


async def pump_speech(
    request: SpeechRequest,
    api_key: str,
    stream: "AudioStream",
    parts: Sequence[str] | PartSource | None = None,
    *,
    paced: bool = False,
) -> None:
    """Stream the speech of *request* into *stream*, chunk by chunk, as it arrives.

    *parts* are the texts to speak, one request each, in order, into the same
    stream (None: the request's text, in one request): a sequence, or a source
    the pump pulls them from (:mod:`twicc.tts.part_source`), always closed at
    the end. A part is requested once the previous part's audio has fully
    arrived and the stream's lookahead allows it. The source tells, with its
    first part, whether the reading is split; with *paced*, a split reading's
    stream delivers its audio at the playback pace.

    The upstream ``Content-Type`` decides, not the requested format. An upstream
    error, or a success that is not audio, fails the stream (before its headers
    on the first part) and sends no next part. A later part of another type
    fails it too, and so does an error of the source. A timeout or a network
    error fails it with a user-safe message, before or after its headers. A
    cancellation (a release) propagates: the ``async with`` closes the upstream
    call.
    """
    source = part_source(request.text, parts)
    try:
        async with _make_client(SPEECH_TIMEOUT) as client:
            index = 0
            split = False
            while (text := await timed_next_part(source, stream)) is not None:
                if index:
                    # Pulled first: the last part ends the speech without a lookahead wait.
                    await stream.wait_for_lookahead()
                else:
                    split = bool(source.split)  # known once the first part was returned
                part_request = request._replace(text=text)
                if not await _pump_part(
                    client, part_request, api_key, stream, index, split=split, paced=paced and split
                ):
                    return
                index += 1
    except SpeechError as error:
        # The source's error: the audio received still plays, then the reading stops.
        _fail_if_live(stream, error)
        return
    except httpx.TimeoutException:
        _fail_if_live(stream, SpeechError("OpenRouter did not answer in time"))
        return
    except httpx.HTTPError:
        _fail_if_live(stream, SpeechError("Could not reach OpenRouter"))
        return
    finally:
        await source.aclose()
    stream.finish()


async def _pump_part(
    client: httpx.AsyncClient,
    request: SpeechRequest,
    api_key: str,
    stream: "AudioStream",
    index: int,
    *,
    split: bool,
    paced: bool,
) -> bool:
    """Stream the speech of one part into *stream*. False when the part failed the stream.

    The audio of a split text is filtered part by part: an MP3 part loses its
    info frame (and its ID3 tag after the first part), a PCM part its partial
    last frame. The audio of a single part is never filtered.
    """
    stream.begin_part()
    async with client.stream(
        "POST", SPEECH_URL, json=build_payload(request), headers=_auth_headers(api_key)
    ) as response:
        status = response.status_code
        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if not response.is_success or not media_type.startswith("audio/"):
            # An error, or a success that is not audio (JSON, HTML, ...): its message, else its status.
            body = await response.aread()
            stream.fail(SpeechError(upstream_error_message(status, body), upstream_status=status))
            return False
        if media_type == "audio/pcm":
            content_type = pcm_content_type(content_type)
        generation_id = response.headers.get("x-generation-id") or None
        if index == 0:
            stream.set_headers(content_type, generation_id)
            if paced:
                stream.pace()
        elif not stream.add_part_headers(content_type, generation_id):
            return False
        head_filter = trimmer = None
        if split and media_type == "audio/mpeg":
            head_filter = Mp3HeadFilter(drop_id3=index > 0)
        elif split and media_type == "audio/pcm":
            _rate, channels = parse_pcm_format(content_type)
            trimmer = PcmFrameTrimmer(channels * PCM_SAMPLE_WIDTH)
        # aiter_bytes, not aiter_raw: httpx asks for gzip, and only aiter_bytes
        # decodes it (without re-chunking).
        async for chunk in response.aiter_bytes():
            if head_filter is not None:
                chunk = head_filter.feed(chunk)
            elif trimmer is not None:
                chunk = trimmer.feed(chunk)
            stream.append(chunk)
        if head_filter is not None:
            stream.append(head_filter.flush())
        elif trimmer is not None and (dropped := trimmer.end()):
            logger.debug("TTS part %d: %d bytes of a partial PCM frame dropped", index + 1, dropped)
        stream.end_part()
    return True


def _fail_if_live(stream: "AudioStream", error: SpeechError) -> None:
    # An error while closing the upstream call can follow an upstream error that
    # failed the stream already: the first failure stands.
    if stream.state in ("opening", "running"):
        stream.fail(error)


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


def _price(value: object) -> float | None:
    """A listed unit price (OpenRouter sends decimal strings); ``None`` when unusable."""
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price >= 0 else None


def _pricing(entry: dict) -> dict:
    """The listed ``prompt`` / ``completion`` unit prices, as floats or ``None``."""
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    return {"prompt": _price(pricing.get("prompt")), "completion": _price(pricing.get("completion"))}


def _copy_models(models: list[dict]) -> list[dict]:
    """A copy a caller can mutate without reaching the cache, with TwiCC's known facts of each
    model (``formats``, ``streams``, ``max_chars``, ``whole_formats``: model_catalog.py)."""
    return [
        {
            **model,
            "voices": list(model["voices"]),
            "pricing": dict(model["pricing"]),
            **speech_model_facts(model["id"]),
        }
        for model in models
    ]


async def fetch_speech_models() -> list[dict]:
    """The speech models as ``[{"id", "name", "voices", "pricing", "formats", "streams",
    "max_chars", "whole_formats"}]``; ``[]`` on any failure.

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
            {
                "id": entry["id"],
                "name": entry.get("name") or entry["id"],
                "voices": _voices(entry),
                "pricing": _pricing(entry),
            }
            for entry in entries
        ]
    except Exception:  # noqa: BLE001 - an empty list: the settings show the list did not load
        logger.debug("TTS models list unavailable")
        return []
    _models_cache = (time.monotonic(), models)
    return _copy_models(models)


# Strong references to the running cost lookups (asyncio keeps only weak ones).
_cost_tasks: set[asyncio.Task] = set()


def schedule_cost_log(generation_ids: list[str], api_key: str, on_cost=None) -> None:
    """Log the real cost of every part of one speech, in the background.

    One lookup per id of *generation_ids*, in parallel, one line each (fire-and-forget).
    *on_cost*, an async callable, then receives the total cost, once, when every
    lookup found a cost.
    """
    task = asyncio.get_running_loop().create_task(_costs_then(list(generation_ids), api_key, on_cost))
    _cost_tasks.add(task)
    task.add_done_callback(_cost_tasks.discard)


async def _costs_then(generation_ids: list[str], api_key: str, on_cost) -> None:
    costs = await asyncio.gather(*(_log_generation_cost(generation_id, api_key) for generation_id in generation_ids))
    if not costs or None in costs or on_cost is None:
        return
    try:
        await on_cost(sum(costs))
    except Exception:  # noqa: BLE001 - fire-and-forget: never raise
        logger.exception("TTS cost %s: could not be recorded", ",".join(generation_ids))


async def _log_generation_cost(
    generation_id: str,
    api_key: str,
    *,
    first_delay: float = 15.0,
    retry_delay: float = 5.0,
    attempts: int = 4,
) -> float | None:
    """Fetch and log the cost of a generation, and return it (None when unknown).

    Never raises (it runs fire-and-forget).

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
                return _price(data.get("total_cost"))
        logger.debug("TTS cost %s: still not found after %d attempts", generation_id, attempts)
    except Exception:  # noqa: BLE001 - fire-and-forget: never raise, never log the exception text
        logger.debug("TTS cost %s: lookup failed", generation_id)
    return None


def _mp3_bitrate(content: bytes | bytearray, scan_bytes: int | None = None) -> int | None:
    """The bitrate (bit/s) of the first MPEG Layer III frame, past an ID3v2 tag, whose header
    lies in the first ``scan_bytes`` bytes of ``content`` (all of it when None)."""
    size = len(content) if scan_bytes is None else min(len(content), scan_bytes)
    last_header = size - MP3_HEADER_SIZE
    position = id3_tag_size(content) or 0
    while position <= last_header and (position := content.find(b"\xff", position, last_header + 1)) != -1:
        if frame := mp3_frame(content, position):
            return frame.bitrate
        position += 1
    return None


def mp3_frame_bitrates(head: bytes | bytearray) -> list[int]:
    """The bitrates (bit/s) of the consecutive MPEG Layer III frames from the first one, past an
    ID3v2 tag, within the first ``RATE_SCAN_BYTES`` bytes of ``head``. The walk stops at the end
    of those bytes, at a truncated frame, or at bytes that are not a frame header.

    An info frame (Xing/Info/VBRI) is a frame too and is counted: call it on filtered audio (the
    stream holds the parts without their info frames)."""
    size = min(len(head), RATE_SCAN_BYTES)
    position = id3_tag_size(head) or 0
    bitrates = []
    while position + MP3_HEADER_SIZE <= size:
        frame = mp3_frame(head, position)
        if frame is None or position + frame.length > size:
            break
        bitrates.append(frame.bitrate)
        position += frame.length
    return bitrates


def _byte_rate(content_type: str, content: bytes | bytearray, scan_bytes: int | None) -> float | None:
    """The audio bytes per second of a body of ``content_type``, its MP3 bitrate read in the
    first ``scan_bytes`` bytes of ``content`` (all of it when None). None for another type, an
    unusable PCM type, or no readable MP3 frame."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "audio/pcm":
        pcm_format = parse_pcm_format(content_type)
        if pcm_format is None:
            return None
        rate, channels = pcm_format
        return float(rate * channels * PCM_SAMPLE_WIDTH)
    if media_type == "audio/mpeg":
        bitrate = _mp3_bitrate(content, scan_bytes)
        return bitrate / 8 if bitrate else None
    return None


def bytes_per_second(content_type: str, head: bytes | bytearray) -> float | None:
    """The audio bytes per second of a stream of ``content_type``: from the rate and channels of
    a PCM type, from the bitrate of the first frame in the first ``RATE_SCAN_BYTES`` bytes of
    ``head`` for MP3 (constant bitrate assumed). None for another type, an unusable PCM type, or
    no readable MP3 frame in the scanned bytes."""
    return _byte_rate(content_type, head, RATE_SCAN_BYTES)


def audio_duration_seconds(content: bytes | bytearray, content_type: str) -> float | None:
    """The length of an audio body: exact for PCM (from the rate and channels of its type),
    from the bitrate for MP3 (constant bitrate, as OpenRouter sends it). The whole body is
    read for the bitrate, so a large ID3 tag still gives a duration. None for another type or
    an unreadable body."""
    rate = _byte_rate(content_type, content, None)
    return len(content) / rate if rate else None
