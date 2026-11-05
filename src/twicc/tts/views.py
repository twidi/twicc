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

import logging
import re

import orjson
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from twicc.tts.openrouter import fetch_speech_models
from twicc.tts.service import SPEECH_FAILED, SpeechError, open_speech
from twicc.tts.streams import AudioStream, ending_of, get_stream, release_stream

logger = logging.getLogger(__name__)

# How a read with a ``Range`` header is answered (the decision of the plan's spike, Task 1):
# - "partial": a 206 with the received bytes from the offset, and ``Accept-Ranges: bytes``;
# - "full": the ``Range`` header is ignored (a plain read), and no ``Accept-Ranges`` is sent.
# "full" is the decision: Chrome on Android takes the end of a short ``206 bytes 0-L/*``
# answer to its first ``Range: bytes=0-`` as the end of the media.
RANGE_ANSWER = "full"

# The largest slice of audio that a read yields at once.
READ_SLICE = 64 * 1024

AUDIO_GONE = "The audio is no longer available"
AUDIO_CUT = "The audio stopped before the end"

_RANGE_PATTERN = re.compile(r"bytes=(\d+)-(\d*)")


def _error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


def _method_not_allowed(allowed: str) -> JsonResponse:
    response = _error("Method not allowed", 405)
    response["Allow"] = allowed
    return response


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


async def tts_speech(request):
    """POST /api/tts/speech/ — open existing text, script, or summary speech."""
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
        opened = await open_speech(data)
        return JsonResponse({"audio_id": opened.audio_id, "content_type": opened.content_type})
    except SpeechError as error:
        return _error(error.message, error.status, **error.extra)
    except Exception:
        logger.exception("TTS speech request failed")
        return _error(SPEECH_FAILED, 500)


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
