"""Text-to-speech REST endpoints: the speech audio by id, and the speech model list.

A speech goes through four steps (design: ``docs/plans/2026-10-30-tts-streaming-design.md``):

- open (``POST /api/tts/speech/``): start the upstream call (OpenRouter, or the stored
  command of a script config) in the background, and answer an audio id once its headers
  arrive; a long text is sent in parts (:mod:`twicc.tts.parts`); a small model can adapt the
  text for speech first (:mod:`twicc.tts.adapt`);
- read (``GET /api/tts/audio/<id>/``): stream the audio bytes as they arrive, at the
  playback pace for a paced stream (a 410 once the speech failed);
- ending (``GET /api/tts/audio/<id>/ending/``): how the speech ended;
- release (``DELETE /api/tts/audio/<id>/``): drop the audio id; a running upstream call stops.

The streams live in :mod:`twicc.tts.streams`. Every view is async and runs on the
one event loop of the ASGI server.

Every error before a body byte is a JSON ``{"error": ...}`` of these views, never
Django's error page (in DEBUG it shows frame locals, the key among them).
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from twicc.paths import get_env_path
from twicc.tts.adapt import TEST_TEXT_KEEP_CHARS, AdaptedParts, resolve_engine
from twicc.tts.audio_cache import CachedAudio, speech_cache
from twicc.tts.config import (
    TTS_RESPONSE_FORMATS,
    openrouter_api_key,
    positive_speed,
    read_tts_config,
    record_last_test,
    script_config_validated,
    script_fields,
    stored_script_config,
    tts_client_payload,
)
from twicc.tts.openrouter import (
    SpeechError,
    SpeechRequest,
    audio_duration_seconds,
    fetch_speech_models,
    pump_speech,
    schedule_cost_log,
)
from twicc.tts.part_source import PartSource
from twicc.tts.parts import adapted_limits as speech_adapted_limits
from twicc.tts.parts import has_speech, speech_parts
from twicc.tts.script import ScriptSpeechRequest, pump_script_speech, script_parts
from twicc.tts.script import adapted_limits as script_adapted_limits
from twicc.tts.streams import (
    AudioStream,
    ending_of,
    get_stream,
    release_stream,
    start_stream,
    stream_from_bytes,
)

logger = logging.getLogger(__name__)

# How a read with a ``Range`` header is answered (the decision of the plan's spike, Task 1):
# - "partial": a 206 with the received bytes from the offset, and ``Accept-Ranges: bytes``;
# - "full": the ``Range`` header is ignored (a plain read), and no ``Accept-Ranges`` is sent.
# "full" is the decision: Chrome on Android takes the end of a short ``206 bytes 0-L/*``
# answer to its first ``Range: bytes=0-`` as the end of the media.
RANGE_ANSWER = "full"

# The largest slice of audio that a read yields at once.
READ_SLICE = 64 * 1024

SPEECH_FAILED = "Text-to-speech failed"
AUDIO_GONE = "The audio is no longer available"
AUDIO_CUT = "The audio stopped before the end"
NOTHING_TO_SAY = "There is nothing to read aloud in this text"

# The status of a speech that failed upstream (OpenRouter) or in its script: "Failed
# Dependency". Not 502: Cloudflare (a tunnel in front of TwiCC) replaces an origin's 502
# and 504 bodies with its own page, and the browser would lose the error message.
SPEECH_ERROR_STATUS = 424
NOT_SAVED = "This config is not saved yet: try again"
NOT_VALIDATED = "This script config is not validated: test it again in the settings"
NO_SUCH_SCRIPT = "This script config does not exist"
NO_PCM_RATE = "This script config needs a sample rate for PCM"
NO_COMMAND = "This script config has no command"

_RANGE_PATTERN = re.compile(r"bytes=(\d+)-(\d*)")


def _error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


def _method_not_allowed(allowed: str) -> JsonResponse:
    response = _error("Method not allowed", 405)
    response["Allow"] = allowed
    return response


def _optional_string(data: dict, field: str) -> str:
    value = data.get(field, "")
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string")  # noqa: TRY004 - the view maps ValueError to 400
    return value.strip()


def _speed(value: object) -> float | None:
    """None, or *value* as a finite positive float. Raises ``ValueError`` otherwise."""
    if value is None:
        return None
    speed = positive_speed(value)
    if speed is None:
        raise ValueError("'speed' must be a finite positive number")
    return speed


def _speech_text(data: dict) -> str:
    """The ``text`` of *data*, kept as is. Raises ``ValueError`` without something to read aloud."""
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' must be a non-empty string")
    if not has_speech(text):
        raise ValueError(NOTHING_TO_SAY)
    return text


def parse_speech_request(data: object) -> SpeechRequest:
    """Validate the speech payload into a :class:`SpeechRequest`. Raises ``ValueError``.

    ``text`` is kept as is, but must hold more than whitespace, and something to
    read aloud (a letter or a digit). The other strings are stripped.
    """
    if not isinstance(data, dict):
        raise ValueError("The body must be a JSON object")  # noqa: TRY004 - the view maps ValueError to 400
    text = _speech_text(data)
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("'model' must be a non-empty string")
    response_format = data.get("response_format")
    if response_format not in TTS_RESPONSE_FORMATS:
        raise ValueError(f"'response_format' must be one of: {', '.join(TTS_RESPONSE_FORMATS)}")
    return SpeechRequest(
        text=text,
        model=model.strip(),
        voice=_optional_string(data, "voice"),
        response_format=response_format,
        speed=_speed(data.get("speed")),
        instructions=_optional_string(data, "instructions"),
    )


def parse_range(header: str | None) -> tuple[int, int | None] | None:
    """``(N, M)`` from ``bytes=N-M``, ``(N, None)`` from ``bytes=N-``.

    None for anything else (another unit, a suffix range, several ranges, ``M < N``):
    the read is then a plain read.
    """
    if not header:
        return None
    match = _RANGE_PATTERN.fullmatch(header.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    if end is not None and end < start:
        return None
    return start, end


def _opened(stream: AudioStream) -> JsonResponse:
    return JsonResponse({"audio_id": stream.id, "content_type": stream.content_type})


async def tts_speech(request):
    """POST /api/tts/speech/ — open a speech.

    Body: ``{text, model, voice, response_format, speed, instructions, config_id?, cache?, buffered?}``,
    or for a script config ``{kind: "script", text, script_id, <its six fields>, config_id?, cache?, buffered?}``
    (:func:`_open_script_speech`).
    Answer: ``{"audio_id", "content_type"}``, once the upstream headers arrived.

    A text without speech is a 400, without upstream call. A text longer than its
    first part is sent in parts, into one audio; its reads are paced when its byte
    rate is known, unless ``buffered: true`` (the browser buffers the whole audio
    before it plays). The stored ``adapt`` choice of tts.json decides whether a small
    model adapts the text for speech first (:func:`_open_speech`).

    A settings test sends its ``config_id``: once the audio is complete, the run
    (characters of the original text, times, audio length, parts, the adaptation, then
    the cost once known) becomes that config's ``last_test`` in tts.json. ``cache: true``
    reads and fills the audio cache, except for a settings test.

    An upstream or script error is a ``SPEECH_ERROR_STATUS`` (424), never a 401 (the
    frontend reads any 401 as an expired TwiCC session) nor a 502 or 504 (a proxy such
    as Cloudflare replaces those bodies with its own error page: the message is lost).
    """
    if request.method != "POST":
        return _method_not_allowed("POST")
    try:
        try:
            body = request.body
        except RequestDataTooBig:
            return _error("The text is too long to send", 413)
        try:
            data = orjson.loads(body)
        except orjson.JSONDecodeError:
            return _error("Invalid JSON", 400)
        if isinstance(data, dict) and data.get("kind") == "script":
            return await _open_script_speech(data)
        try:
            speech_request = parse_speech_request(data)
        except ValueError as error:
            return _error(str(error), 400)
        api_key = openrouter_api_key()
        if api_key is None:
            return _error(
                f"TWICC_OPENROUTER_API_KEY is not set. Add it to {get_env_path()} and restart TwiCC.", 503
            )
        config_id = data.get("config_id")
        if not isinstance(config_id, str) or not config_id:
            config_id = None
        label = {
            "model": speech_request.model,
            "voice": speech_request.voice,
            "format": speech_request.response_format,
            "chars": len(speech_request.text),
        }
        return await _open_speech(
            speech_request,
            data,
            config_id,
            label,
            speech_parts,
            speech_adapted_limits,
            lambda sink, parts, paced: pump_speech(speech_request, api_key, sink, parts, paced=paced),
            api_key,
        )
    except Exception:
        # Not BaseException: a CancelledError (client disconnect) must propagate.
        logger.exception("TTS speech request failed")
        return _error(SPEECH_FAILED, 500)


async def _open_script_speech(data: dict) -> JsonResponse:
    """The open step of a script config: it runs the command stored in tts.json for ``script_id``.

    The six script fields of the body serve only a settings test (``config_id`` equal to
    ``script_id``): they must equal the stored ones (else a 409, an edit not saved yet). A message
    read runs only a validated config (else a 409). Nothing runs in either case. No OpenRouter key
    is needed.
    """
    try:
        text = _speech_text(data)
    except ValueError as error:
        return _error(str(error), 400)
    script_id = data.get("script_id")
    if not isinstance(script_id, str) or not script_id:
        return _error("'script_id' must be a non-empty string", 400)
    config_id = data.get("config_id")
    if config_id is not None and config_id != script_id:
        return _error("'config_id' must equal 'script_id'", 400)
    entry = await sync_to_async(stored_script_config)(script_id)
    if entry is None:
        return _error(NO_SUCH_SCRIPT, 404)
    fields = script_fields(entry)
    if config_id is not None:
        if script_fields(data) != fields:
            return _error(NOT_SAVED, 409)
    elif not script_config_validated(entry):
        return _error(NOT_VALIDATED, 409)
    # The settings never test nor validate such a config: only a stale tab or a direct request sends one.
    if not fields["command"].strip():
        return _error(NO_COMMAND, 400)
    if fields["audio_format"] == "pcm" and fields["pcm_rate"] is None:
        return _error(NO_PCM_RATE, 400)
    speech_request = ScriptSpeechRequest(text, **fields)
    # Never the command: it can hold a key.
    label = {
        "kind": "script",
        "output": speech_request.output,
        "format": speech_request.audio_format,
        "chars": len(text),
    }
    return await _open_speech(
        speech_request,
        data,
        config_id,
        label,
        script_parts,
        script_adapted_limits,
        # The stored option, never one from the body; not in the request: a toggle keeps the cache.
        lambda sink, parts, paced: pump_script_speech(
            speech_request, sink, parts, paced=paced, delete_file=entry["delete_file"]
        ),
        None,
    )


async def _open_speech(
    speech_request: SpeechRequest | ScriptSpeechRequest,
    data: dict,
    config_id: str | None,
    label: dict,
    split: Callable[..., list[str]],
    limits: Callable[..., tuple[int | None, int | None]],
    pump: Callable[[AudioStream, list[str] | PartSource, bool], Awaitable[None]],
    api_key: str | None,
) -> JsonResponse:
    """The open step of a valid speech request: the cache, else the pump (*pump(sink, parts, paced)*;
    the pump paces a split reading only), awaited until its headers arrive.

    The stored ``adapt`` choice resolves to an engine, or to why the text is read as written. With
    an engine, the parts are an :class:`AdaptedParts` source, cut to *limits*, that falls back to
    the parts of *split*; without one, the parts of *split*. The label tells which (``adapted``,
    ``adapt_reason``), never the text. The cache key is ``(request, engine name or None)``.
    """
    # A settings test never reads nor fills the cache.
    use_cache = data.get("cache") is True and config_id is None
    engine, adapt_reason = resolve_engine(await sync_to_async(_stored_adapt_choice)(), speech_request.text)
    if engine is None:
        label["adapted"] = "no"
        label["adapt_reason"] = adapt_reason
    else:
        label["adapted"] = engine.name
    if use_cache:
        cached = speech_cache.get((speech_request, None if engine is None else engine.name))
        if cached is not None:
            return _opened(stream_from_bytes(cached.content, cached.content_type, label=label))
    buffered = data.get("buffered") is True
    parts = split(speech_request)
    source = None
    if engine is not None:
        source = AdaptedParts(speech_request.text, engine, *limits(speech_request), fallback=parts, label=label)
    paced = not buffered
    stream = start_stream(
        lambda sink: pump(sink, parts if source is None else source, paced),
        label=label,
        on_end=_end_steps(speech_request, api_key, config_id, use_cache, source, adapt_reason),
    )
    try:
        await stream.ready
    except SpeechError as error:
        # The stream is failed, and logged, already: only drop its id.
        release_stream(stream.id)
        extra = {} if error.upstream_status is None else {"upstream_status": error.upstream_status}
        return _error(error.message, SPEECH_ERROR_STATUS, **extra)
    except asyncio.CancelledError:
        # A client disconnect: stop the upstream call.
        release_stream(stream.id, outcome="cancelled")
        raise
    except Exception:
        # The pump's own error: its task logged it with its traceback already.
        release_stream(stream.id)
        return _error(SPEECH_FAILED, 500)
    return _opened(stream)


def _stored_adapt_choice() -> str | None:
    """The stored text adaptation choice (None: on, with the first running provider)."""
    return read_tts_config()["adapt"]


def _adaptation_fields(source: AdaptedParts | None, adapt_reason: str | None) -> dict:
    """The adaptation fields of a last test: how *source* adapted the text, or, without a source,
    why the text was read as written. The adapted text is cut to ``TEST_TEXT_KEEP_CHARS``."""
    if source is None:
        return {"adapted_by": None, "adapt_reason": adapt_reason, "adapt_first_ms": None, "adapted_text": None}
    adapted_text = source.adapted_text
    return {
        "adapted_by": source.adapted_by,
        "adapt_reason": source.adapt_reason,
        "adapt_first_ms": source.adapt_first_ms,
        "adapted_text": None if adapted_text is None else adapted_text[:TEST_TEXT_KEEP_CHARS],
    }


def _end_steps(
    speech_request: SpeechRequest | ScriptSpeechRequest,
    api_key: str | None,
    config_id: str | None,
    use_cache: bool,
    source: AdaptedParts | None,
    adapt_reason: str | None,
):
    """The ``on_end`` of a speech: once its audio is complete, cache it, record the test run, look up the cost.

    Each step has its own guard: one failure never skips the others. A failed speech does nothing.
    The audio is cached under the adaptation that really ran: a reading that fell back to the
    original text is cached as unadapted (*source*'s ``adapted_by`` is then None). The cost of a
    split speech is the sum of its parts' costs: it is recorded only when every part has a
    generation id. A script speech (no *api_key*) has no cost lookup.
    """

    async def on_end(stream: AudioStream) -> None:
        if stream.state != "complete":
            return
        # A pump that marks no part is one part (the rule of ``AudioStream.finish``).
        parts = max(stream.parts_sent, 1)
        # An empty audio is not worth a cache entry: the next read asks again.
        if use_cache and stream.data:
            try:
                adapted_by = None if source is None else source.adapted_by
                speech_cache.set((speech_request, adapted_by), CachedAudio(bytes(stream.data), stream.content_type))
            except Exception:
                logger.exception("TTS audio could not be cached")
        if config_id:
            try:
                await _record_last_test(config_id, {
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "generation_id": stream.generation_id or "",
                    "chars": len(speech_request.text),
                    "generation_ms": stream.generation_ms,
                    "first_audio_ms": stream.first_audio_ms,
                    "audio_seconds": audio_duration_seconds(stream.data, stream.content_type),
                    "cost": None,
                    "parts": parts,
                    **_adaptation_fields(source, adapt_reason),
                })
            except Exception:
                logger.exception("TTS last test of %s could not be recorded", config_id)
        if stream.generation_ids and api_key is not None:
            # The audio is billed already: a failed cost lookup must not cost the user anything else.
            generation_ids = list(stream.generation_ids)
            on_cost = None
            if config_id and len(generation_ids) == parts:
                # The recorded run carries the first part's id: the total updates it.
                generation_id = stream.generation_id

                async def on_cost(cost):
                    await _record_last_test(config_id, {"generation_id": generation_id, "cost": cost})

            try:
                schedule_cost_log(generation_ids, api_key, on_cost)
            except Exception:
                logger.exception("TTS cost lookup for %s could not be scheduled", ",".join(generation_ids))

    return on_end


async def _record_last_test(config_id: str, update: dict) -> None:
    """Write a last-test update to tts.json and send the new configs to every client."""
    config = await sync_to_async(record_last_test)(config_id, update)
    if config is None:
        return
    await get_channel_layer().group_send(
        "updates",
        {"type": "broadcast", "data": {"type": "tts_config_updated", **tts_client_payload(config)}},
    )


def _gone() -> JsonResponse:
    return _error(AUDIO_GONE, 404)


def _cut() -> JsonResponse:
    return _error(AUDIO_CUT, 410)


def _audio_headers(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    if RANGE_ANSWER == "partial":
        response["Accept-Ranges"] = "bytes"
    return response


async def _audio_body(stream: AudioStream, start: int, last: int | None):
    """The audio bytes of *stream* from *start*, as they become deliverable, in slices of ``READ_SLICE`` at most.

    A paced stream delivers its bytes at the playback pace (``AudioStream.deliverable``),
    also once it is complete or failed.

    *last* set: a range body, which ends after the byte *last*, already deliverable.
    *last* None: the body ends with the stream.
    """
    offset = start
    while True:
        limit = stream.deliverable() if last is None else last + 1
        if offset < limit:
            end = min(limit, offset + READ_SLICE)
            yield bytes(stream.data[offset:end])
            offset = end
            continue
        # A released stream ends the body too: its reader is gone, or does not matter any more.
        if last is not None or stream.state == "released":
            return
        if stream.state == "complete" and offset >= len(stream.data):
            return
        if stream.state == "failed" and offset >= len(stream.data):
            # The abnormal end: the ASGI server closes the connection without the final
            # chunk, so the browser sees a cut audio, not a complete one. Uvicorn then logs
            # "Exception in ASGI application" with a traceback, once per failed stream that
            # still has a reader. This is expected, and rare.
            raise ConnectionAbortedError("The speech failed")
        await stream.wait_for_more(offset)


async def tts_audio(request, audio_id: str):
    """GET /api/tts/audio/<id>/ — read the audio. DELETE — release it.

    A plain read streams the audio from its start, as it arrives (at the playback
    pace for a paced stream). A ``Range`` read (with ``RANGE_ANSWER == "partial"``)
    answers the deliverable bytes from its offset, after waiting for the first one.

    A read of a failed speech is a 410: Chrome retries a cut read, and given the
    same bytes then the same cut, it hangs without an ``error`` event. A read
    already streaming when the speech fails ends abnormally instead.
    """
    if request.method == "DELETE":
        return _release(audio_id)
    if request.method != "GET":
        return _method_not_allowed("GET, DELETE")
    try:
        stream = get_stream(audio_id)
        if stream is None:
            return _gone()
        stream.claimed = True
        stream.touch()
        if stream.state == "failed":
            return _cut()
        requested = parse_range(request.headers.get("Range")) if RANGE_ANSWER == "partial" else None
        if requested is None:
            response = StreamingHttpResponse(_audio_body(stream, 0, None), content_type=stream.content_type)
            if stream.state == "complete":
                response["Content-Length"] = str(len(stream.data))
            return _audio_headers(response)
        start, end = requested
        await stream.wait_for_more(start)
        if stream.state == "released":
            return _gone()
        # The received bytes a read may send now: all of them, unless the stream is paced.
        received = stream.deliverable()
        if received <= start and stream.state == "failed":
            return _cut()
        if received > start:
            last = received - 1 if end is None else min(received - 1, end)
            total = len(stream.data) if stream.state == "complete" else "*"
            response = StreamingHttpResponse(
                _audio_body(stream, start, last), status=206, content_type=stream.content_type
            )
            response["Content-Range"] = f"bytes {start}-{last}/{total}"
            response["Content-Length"] = str(last - start + 1)
            return _audio_headers(response)
        # No byte from the offset: the speech completed before it.
        response = HttpResponse(status=416, content_type=stream.content_type)
        response["Content-Range"] = f"bytes */{len(stream.data)}"
        return _audio_headers(response)
    except Exception:
        logger.exception("TTS audio read failed")
        return _error(SPEECH_FAILED, 500)


def _release(audio_id: str) -> HttpResponse:
    """Drop the audio id; a running upstream call stops. A 204, also for an unknown id."""
    try:
        stream = get_stream(audio_id)
        if stream is not None:
            stream.touch()
        release_stream(audio_id)
        return HttpResponse(status=204)
    except Exception:
        logger.exception("TTS audio release failed")
        return _error(SPEECH_FAILED, 500)


async def tts_audio_ending(request, audio_id: str):
    """GET /api/tts/audio/<id>/ending/ — ``{"state", "error"}``: how the speech ended, without waiting."""
    if request.method != "GET":
        return _method_not_allowed("GET")
    try:
        stream = get_stream(audio_id)
        if stream is not None:
            stream.touch()
        return JsonResponse(ending_of(audio_id))
    except Exception:
        logger.exception("TTS audio ending failed")
        return _error(SPEECH_FAILED, 500)


async def tts_models(request):
    """GET /api/tts/models/ — the speech models (the settings pickers' choices); an empty list when unavailable."""
    if request.method != "GET":
        return _method_not_allowed("GET")
    return JsonResponse({"models": await fetch_speech_models()})
