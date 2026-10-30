"""Text-to-speech REST endpoints: the speech proxy and the speech model list.

Both views are async: on a client disconnect Django cancels the view task,
and the awaited ``httpx`` call to OpenRouter is cancelled with it (a sync call
would run to the end and may be billed).
"""

import logging
import time
from datetime import UTC, datetime

import orjson
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse

from twicc.paths import get_env_path
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.tts.config import (
    TTS_RESPONSE_FORMATS,
    openrouter_api_key,
    positive_speed,
    record_last_test,
    tts_client_payload,
)
from twicc.tts.openrouter import (
    SpeechError,
    SpeechRequest,
    audio_duration_seconds,
    fetch_speech_models,
    schedule_cost_log,
    synthesize,
)

logger = logging.getLogger(__name__)


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


def parse_speech_request(data: object) -> SpeechRequest:
    """Validate the speech payload into a :class:`SpeechRequest`. Raises ``ValueError``.

    ``text`` is kept as is, but must hold more than whitespace. The other
    strings are stripped.
    """
    if not isinstance(data, dict):
        raise ValueError("The body must be a JSON object")  # noqa: TRY004 - the view maps ValueError to 400
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' must be a non-empty string")
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


async def tts_speech(request):
    """POST /api/tts/speech/ — {text, model, voice, response_format, speed, instructions, config_id?} → audio.

    A settings test sends its `config_id`: the run (characters, generation time, audio length,
    then the cost once known) becomes that config's `last_test` in tts.json.

    Every error is a JSON ``{"error": ...}`` of this view, never Django's error
    page (in DEBUG it shows frame locals, the key among them). An upstream
    error is a 502, never a 401: the frontend reads any 401 as an expired
    TwiCC session.
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
        try:
            speech_request = parse_speech_request(data)
        except ValueError as error:
            return _error(str(error), 400)
        api_key = openrouter_api_key()
        if api_key is None:
            return _error(
                f"TWICC_OPENROUTER_API_KEY is not set. Add it to {get_env_path()} and restart TwiCC.", 503
            )
        started = time.monotonic()
        try:
            audio = await synthesize(speech_request, api_key)
        except SpeechError as error:
            extra = {} if error.upstream_status is None else {"upstream_status": error.upstream_status}
            return _error(error.message, 502, **extra)
        generation_ms = round((time.monotonic() - started) * 1000)
        response = HttpResponse(audio.content, content_type=audio.content_type)
        response["Cache-Control"] = "no-store"
    except Exception:
        # Not BaseException: a CancelledError (client disconnect) must propagate.
        logger.exception("TTS speech request failed")
        return _error("Text-to-speech failed", 500)
    # A settings test names its config: its last test run is kept in tts.json.
    config_id = data.get("config_id") if isinstance(data.get("config_id"), str) else None
    if config_id:
        try:
            await _record_last_test(config_id, {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "generation_id": audio.generation_id or "",
                "chars": len(speech_request.text),
                "generation_ms": generation_ms,
                "audio_seconds": audio_duration_seconds(audio.content, audio.content_type),
                "cost": None,
            })
        except Exception:
            logger.exception("TTS last test of %s could not be recorded", config_id)
    if audio.generation_id:
        # The audio is billed already: a failed cost lookup must not cost the user the response.
        on_cost = None
        if config_id:
            generation_id = audio.generation_id

            async def on_cost(cost):
                await _record_last_test(config_id, {"generation_id": generation_id, "cost": cost})

        try:
            schedule_cost_log(audio.generation_id, api_key, on_cost)
        except Exception:
            logger.exception("TTS cost lookup for %s could not be scheduled", audio.generation_id)
    return response


async def _record_last_test(config_id: str, update: dict) -> None:
    """Write a last-test update to tts.json and send the new configs to every client."""
    config = await sync_to_async(record_last_test)(config_id, update)
    if config is None:
        return
    await get_channel_layer().group_send(
        "updates",
        {"type": "broadcast", "data": {"type": "tts_config_updated", **tts_client_payload(config)}},
    )


async def tts_models(request):
    """GET /api/tts/models/ — the speech models (the settings pickers' choices); an empty list when unavailable."""
    if request.method != "GET":
        return _method_not_allowed("GET")
    return JsonResponse({"models": await fetch_speech_models()})
