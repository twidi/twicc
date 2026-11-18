"""``POST /api/stt/transcribe/`` — transcribe one dictation recording.

Multipart body: ``audio`` (the recording), ``engine`` (``openrouter`` or
``script``; the browser engine never calls the server) and optionally
``language`` (overrides the configured one). The model, the language and the
script command come from the synced settings (``sttModel``, ``sttLanguage``,
``sttScriptCommand``): the browser never sends a command line.

Answers ``{"text", "engine", "seconds"?, "cost"?}``.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

from twicc.core.ui_messages import ui_message_text
from twicc.stt.engines import (
    MAX_AUDIO_BYTES,
    TranscriptionError,
    normalize_format,
    normalize_language,
    transcribe_openrouter,
    transcribe_script,
)

logger = logging.getLogger(__name__)

SERVER_ENGINES = ("openrouter", "script")


def _error(message: str, status: int) -> JsonResponse:
    metadata = getattr(message, "ui_message", None)
    return JsonResponse({"error": message, **({"ui_message": metadata} if metadata else {})}, status=status)


async def transcribe(request):
    if request.method != "POST":
        response = _error(ui_message_text("Method not allowed", "backend.common.methodNotAllowed"), 405)
        response["Allow"] = "POST"
        return response

    upload = request.FILES.get("audio")
    if upload is None:
        return _error(ui_message_text("No audio was received", "backend.stt.noAudio"), 400)
    if upload.size > MAX_AUDIO_BYTES:
        return _error(ui_message_text("The recording is too long", "backend.stt.tooLarge"), 413)
    engine = request.POST.get("engine", "")
    if engine not in SERVER_ENGINES:
        return _error(ui_message_text("Unknown transcription engine", "backend.stt.unknownEngine"), 400)
    audio_format = normalize_format(request.POST.get("format") or upload.content_type or "")
    if audio_format is None:
        return _error(ui_message_text("Unsupported audio format", "backend.stt.unsupportedFormat"), 415)

    from twicc.paths import OPENROUTER_API_KEY_ENV, get_env_secret
    from twicc.synced_settings import read_synced_settings

    settings = read_synced_settings()
    language = normalize_language(request.POST.get("language")) or normalize_language(settings.get("sttLanguage"))
    audio = upload.read()
    try:
        if engine == "openrouter":
            result = await transcribe_openrouter(
                audio,
                audio_format,
                api_key=get_env_secret(OPENROUTER_API_KEY_ENV),
                model=settings.get("sttModel"),
                language=language,
            )
        else:
            result = await transcribe_script(
                audio, audio_format, command=settings.get("sttScriptCommand") or "", language=language
            )
    except TranscriptionError as error:
        return _error(error.message, error.status)
    except Exception:
        logger.exception("Transcription failed")
        return _error(ui_message_text("The transcription failed", "backend.stt.failed"), 500)
    payload = {"text": result.text, "engine": result.engine}
    if result.seconds is not None:
        payload["seconds"] = result.seconds
    if result.cost is not None:
        payload["cost"] = result.cost
    return JsonResponse(payload)
