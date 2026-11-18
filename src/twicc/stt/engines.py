"""The server-side transcription engines: OpenRouter and a user script.

Both take the recorded audio (bytes + format) and return the text. The
OpenRouter key and the script command line never reach a log line or an error
message (either can hold a secret); a failure logs a status, or TwiCC's own
message.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import NamedTuple

import httpx

from twicc.core.ui_messages import ui_message_text

logger = logging.getLogger(__name__)

TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODEL = "openai/whisper-large-v3"
# OpenRouter's multipart upload cap; recordings are capped client-side well below it.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# Upstream providers stop at 60 s of processing; leave room for the upload.
OPENROUTER_TIMEOUT = httpx.Timeout(90.0, connect=15.0)
SCRIPT_TIMEOUT_SECONDS = 180.0
SCRIPT_MAX_OUTPUT_BYTES = 1024 * 1024
STOP_GRACE_SECONDS = 2.0

# Container formats the browsers record, mapped to the extension OpenRouter
# (and most local tools) read the format from.
AUDIO_FORMATS = {
    "webm": "webm",  # Chrome, Firefox, Edge, Android (Opus)
    "ogg": "ogg",  # Firefox (Opus)
    "mp4": "m4a",  # Safari macOS/iOS (AAC in MP4)
    "m4a": "m4a",
    "aac": "aac",
    "wav": "wav",
    "mpeg": "mp3",
    "mp3": "mp3",
    "flac": "flac",
}

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")

NO_KEY = ui_message_text("No OpenRouter API key is configured", "backend.stt.noKey")
NO_COMMAND = ui_message_text("No transcription script is configured", "backend.stt.noCommand")
UNREACHABLE = ui_message_text("Could not reach OpenRouter", "backend.stt.unreachable")
REFUSED = ui_message_text("OpenRouter refused the transcription", "backend.stt.refused")
SCRIPT_START = ui_message_text("The transcription script could not start", "backend.stt.scriptStart")
SCRIPT_FAILED = ui_message_text("The transcription script failed", "backend.stt.scriptFailed")
SCRIPT_TIMEOUT = ui_message_text("The transcription script took too long", "backend.stt.scriptTimeout")
SCRIPT_TOO_LARGE = ui_message_text("The transcription script output is too large", "backend.stt.scriptTooLarge")


class TranscriptionError(Exception):
    """A refused transcription: ``message`` is safe to show, ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


class Transcription(NamedTuple):
    text: str
    engine: str
    seconds: float | None = None
    cost: float | None = None


def normalize_format(value: str | None) -> str | None:
    """The file extension for a recorded format/MIME subtype, or None when unsupported."""
    if not value:
        return None
    subtype = value.lower().split(";", 1)[0].strip()
    subtype = subtype.rsplit("/", 1)[-1]
    subtype = subtype.removeprefix("x-")
    return AUDIO_FORMATS.get(subtype)


def normalize_language(value: str | None) -> str | None:
    """An ISO-639-1/2 code (``pt-BR`` → ``pt``), or None for auto-detection."""
    if not value or not isinstance(value, str):
        return None
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    return code if _LANGUAGE_RE.match(code) else None


def clean_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").strip())


# --- OpenRouter --------------------------------------------------------------


async def transcribe_openrouter(
    audio: bytes, audio_format: str, *, api_key: str | None, model: str | None, language: str | None
) -> Transcription:
    if not api_key:
        raise TranscriptionError(NO_KEY, 409)
    data = {"model": (model or "").strip() or DEFAULT_MODEL}
    if language:
        data["language"] = language
    files = {"file": (f"dictation.{audio_format}", audio, "application/octet-stream")}
    try:
        async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT) as client:
            response = await client.post(
                TRANSCRIPTION_URL, data=data, files=files, headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx.HTTPError as exc:
        logger.warning("OpenRouter transcription unreachable (%s)", type(exc).__name__)
        raise TranscriptionError(UNREACHABLE, 502) from exc
    if response.status_code != 200:
        logger.warning("OpenRouter transcription answered %s", response.status_code)
        status = 402 if response.status_code == 402 else 502
        raise TranscriptionError(REFUSED, status)
    try:
        payload = response.json()
    except ValueError as exc:
        raise TranscriptionError(REFUSED, 502) from exc
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return Transcription(
        text=clean_text(str(payload.get("text") or "")),
        engine="openrouter",
        seconds=usage.get("seconds") if isinstance(usage.get("seconds"), int | float) else None,
        cost=usage.get("cost") if isinstance(usage.get("cost"), int | float) else None,
    )


# --- user script -------------------------------------------------------------


async def transcribe_script(audio: bytes, audio_format: str, *, command: str, language: str | None) -> Transcription:
    """Run the user's command on the recording; its standard output is the text.

    The command runs like a text-to-speech script (the user's login shell, in
    the home directory, with the terminals' environment, in its own process
    group) and receives:

    - ``TWICC_STT_FILE`` — absolute path of the recording (deleted afterwards)
    - ``TWICC_STT_FORMAT`` — its extension (``webm``, ``m4a``, ``ogg``…)
    - ``TWICC_STT_LANGUAGE`` — ISO code, or empty for auto-detection
    """
    from twicc.terminal import terminal_environment
    from twicc.tts.script import shell_argv

    command = (command or "").strip()
    if not command:
        raise TranscriptionError(NO_COMMAND, 409)

    with tempfile.TemporaryDirectory(prefix="twicc-stt-") as tmp:
        path = Path(tmp) / f"dictation.{audio_format}"
        path.write_bytes(audio)
        env = terminal_environment(os.environ)
        env.update({
            "TWICC_STT_FILE": str(path),
            "TWICC_STT_FORMAT": audio_format,
            "TWICC_STT_LANGUAGE": language or "",
        })
        try:
            process = await asyncio.create_subprocess_exec(
                *shell_argv(command),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=Path.home(),
                env=env,
                start_new_session=True,  # its own process group: a stop reaches every process
            )
        except OSError as exc:
            logger.warning("Transcription script could not start (%s)", type(exc).__name__)
            raise TranscriptionError(SCRIPT_START, 500) from exc
        try:
            output = await asyncio.wait_for(_read_capped(process), SCRIPT_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            await _stop(process)
            raise TranscriptionError(SCRIPT_TIMEOUT, 504) from exc
        except _TooLarge as exc:
            await _stop(process)
            raise TranscriptionError(SCRIPT_TOO_LARGE, 502) from exc
        except asyncio.CancelledError:
            await _stop(process)
            raise
        status = await process.wait()
        if status != 0:
            logger.warning("Transcription script failed (exit status %s)", status)
            raise TranscriptionError(SCRIPT_FAILED, 502)
    return Transcription(text=clean_text(output.decode("utf-8", errors="replace")), engine="script")


class _TooLarge(Exception):
    pass


async def _read_capped(process) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await process.stdout.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > SCRIPT_MAX_OUTPUT_BYTES:
            raise _TooLarge()
        chunks.append(chunk)


async def _stop(process) -> None:
    """SIGTERM the whole process group, then SIGKILL after a grace delay."""
    import signal

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(process.wait(), STOP_GRACE_SECONDS)
            return
        except TimeoutError:
            continue
