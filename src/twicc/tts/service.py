"""Shared speech preparation, generation, cache, and completion telemetry."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.models import MessageSummary
from twicc.paths import get_env_path
from twicc.summaries import service as summaries
from twicc.summaries.speech import SummaryParts
from twicc.tts.adapt import (
    OPENROUTER_ENGINE,
    TEST_TEXT_KEEP_CHARS,
    AdaptedParts,
    fallback_engine,
    resolve_engine,
)
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
    SpeechError as UpstreamSpeechError,
    SpeechRequest,
    audio_duration_seconds,
    pump_speech,
    schedule_cost_log,
)
from twicc.tts.part_source import PartSource, PrefixedParts, as_part_source
from twicc.tts.parts import adapted_limits as speech_adapted_limits
from twicc.tts.parts import has_speech, speech_parts
from twicc.tts.script import ScriptSpeechRequest, pump_script_speech, script_parts
from twicc.tts.script import adapted_limits as script_adapted_limits
from twicc.tts.simple_text import ORIGINS, direct_text
from twicc.tts.streams import (
    AudioStream,
    release_stream,
    start_stream,
    stream_from_bytes,
)

logger = logging.getLogger(__name__)

SPEECH_FAILED = "Text-to-speech failed"
# The cache name of a directly read text (no engine ran): never an engine name.
DIRECT_ENGINE = "direct"
SUMMARY_ENGINE = "summary"
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


class OpenedSpeech(NamedTuple):
    """One registered audio stream, shared by all playback clients."""

    audio_id: str
    content_type: str


class SpeechError(Exception):
    """A safe preparation failure, with the existing HTTP status and fields."""

    def __init__(self, message: str, status: int, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


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


def _origin(data: dict) -> str | None:
    """The ``origin`` of *data*: None (absent or null: a message), or one of ``ORIGINS``. Raises ``ValueError``."""
    origin = data.get("origin")
    if origin is None:
        return None
    if not isinstance(origin, str) or origin not in ORIGINS:
        raise ValueError(f"'origin' must be one of: {', '.join(ORIGINS)}")
    return origin


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


def _opened(stream: AudioStream) -> OpenedSpeech:
    return OpenedSpeech(stream.id, stream.content_type)


def _summary_reference(data: object) -> dict | None:
    """Validate only public handles; client metadata never authorizes a bypass."""
    if not isinstance(data, dict):
        return None
    live, saved = 'summary_id' in data, 'saved_summary_id' in data
    if not live and not saved:
        if 'summary_result_hash' in data:
            raise ValueError("'summary_result_hash' requires 'saved_summary_id'")
        return None
    if (live and saved) or 'text' in data or 'config_id' in data:
        raise ValueError('A summary needs exactly one reference, without text or a configuration test')
    if live:
        handle = data['summary_id']
        if not isinstance(handle, str) or not handle.strip() or 'summary_result_hash' in data:
            raise ValueError("'summary_id' must be a non-empty handle")
        return {'id': handle}
    saved_id, result_hash = data['saved_summary_id'], data.get('summary_result_hash')
    if type(saved_id) is not int or saved_id < 1:
        raise ValueError("'saved_summary_id' must be a positive integer")
    if not isinstance(result_hash, str) or re.fullmatch('[0-9a-f]{64}', result_hash) is None:
        raise ValueError("'summary_result_hash' must be the saved result fingerprint")
    return {'saved_id': saved_id, 'result_hash': result_hash}


async def open_speech(data: dict, *, _policy: dict | None = None) -> OpenedSpeech:
    """Prepare existing speech payloads without an HTTP request or response.

    Cancellation releases preparation resources. Success transfers the registered
    stream to the caller, which releases it through ``release_stream``.
    """
    try:
        try:
            summary = _summary_reference(data)
        except ValueError as error:
            raise SpeechError(str(error), 400)
        if summary is not None:
            if 'saved_id' in summary:
                try:
                    # One row read captures the exact body and its fingerprint atomically.
                    saved = await sync_to_async(summaries.load_saved_summary)(summary['saved_id'])
                except MessageSummary.DoesNotExist:
                    raise SpeechError('The saved summary is no longer available', 404)
                if saved['result_hash'] != summary['result_hash']:
                    raise SpeechError('The saved summary changed. Refresh it before reading.', 409,
                                      summary_changed=True, saved_id=summary['saved_id'])
                summary = saved
            data = {**data, 'text': summary.get('text', 'Summary')}
        if isinstance(data, dict) and data.get("kind") == "script":
            return await _open_script_speech(data, summary=summary, _policy=_policy)
        try:
            speech_request = parse_speech_request(data)
            origin = _origin(data)
        except ValueError as error:
            raise SpeechError(str(error), 400)
        api_key = openrouter_api_key()
        if api_key is None:
            raise SpeechError(
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
            origin=origin,
            summary=summary,
            _policy=_policy,
        )
    except SpeechError:
        raise
    except Exception:
        # Not BaseException: a CancelledError (client disconnect) must propagate.
        logger.exception("TTS speech request failed")
        raise SpeechError(SPEECH_FAILED, 500)


async def _open_script_speech(
    data: dict, *, summary: dict | None = None, _policy: dict | None = None,
) -> OpenedSpeech:
    """The open step of a script config: it runs the command stored in tts.json for ``script_id``.

    The six script fields of the body serve only a settings test (``config_id`` equal to
    ``script_id``): they must equal the stored ones (else a 409, an edit not saved yet). A message
    read runs only a validated config (else a 409). Nothing runs in either case. No OpenRouter key
    is needed.
    """
    try:
        text = _speech_text(data)
        origin = _origin(data)
    except ValueError as error:
        raise SpeechError(str(error), 400)
    script_id = data.get("script_id")
    if not isinstance(script_id, str) or not script_id:
        raise SpeechError("'script_id' must be a non-empty string", 400)
    config_id = data.get("config_id")
    if config_id is not None and config_id != script_id:
        raise SpeechError("'config_id' must equal 'script_id'", 400)
    # Only an internal native selection supplies this server-read snapshot.
    # HTTP JSON never supplies keyword arguments to open_speech.
    entry = _policy['entry'] if _policy is not None else await sync_to_async(stored_script_config)(script_id)
    if entry is None:
        raise SpeechError(NO_SUCH_SCRIPT, 404)
    fields = script_fields(entry)
    if config_id is not None:
        if script_fields(data) != fields:
            raise SpeechError(NOT_SAVED, 409)
    elif not script_config_validated(entry):
        raise SpeechError(NOT_VALIDATED, 409)
    # The settings never test nor validate such a config: only a stale tab or a direct request sends one.
    if not fields["command"].strip():
        raise SpeechError(NO_COMMAND, 400)
    if fields["audio_format"] == "pcm" and fields["pcm_rate"] is None:
        raise SpeechError(NO_PCM_RATE, 400)
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
        origin=origin,
        summary=summary,
        _policy=_policy,
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
    *,
    origin: str | None = None,
    summary: dict | None = None,
    _policy: dict | None = None,
) -> OpenedSpeech:
    """The open step of a valid speech request: the cache, else the pump (*pump(sink, parts, paced)*;
    the pump paces a split reading only), awaited until its headers arrive.

    First the decision: unless the stored ``adapt`` choice is ``"off"`` or the request is a settings
    test (*config_id*), a text that :func:`~twicc.tts.simple_text.direct_text` finds simple (judged
    by *origin*) is read **directly**: its cleaned text is split into the parts, with no model call,
    whatever its size and the engines available. A cleaned text with nothing to read aloud is a 400.
    The label then says ``adapted="direct"`` and the cache key is ``(request, "direct")``: a direct
    and an as-written reading never share an entry. Otherwise the stored ``adapt`` choice resolves
    to an engine, or to why the text is read as written. With
    an engine, the parts are an :class:`AdaptedParts` source, cut to *limits*, that falls back to
    the parts of *split*; without one, the parts of *split*. With OpenRouter, the source hands the
    text to the first running agent (``then``) when OpenRouter fails before its first part. The
    label tells which (``adapted``, ``adapt_reason``), never the text. The cache key is
    ``(request, engine name or None)``; with OpenRouter, a cached reading of ``then`` plays when no
    OpenRouter one is cached, so that a re-read stays instant while OpenRouter keeps failing.
    An optional plain announcement is split separately and precedes the message source. It stays
    outside text cleaning and adaptation. Its normalized text extends the cache key when non-empty.
    """
    try:
        announcement = _optional_string(data, "announcement")
    except ValueError as error:
        raise SpeechError(str(error), 400)
    # A settings test never reads nor fills the cache.
    use_cache = data.get("cache") is True and config_id is None
    summary_source = None
    if summary is not None:
        label['adapted'] = SUMMARY_ENGINE
        if 'text' in summary:
            parts = split(speech_request)
        else:
            try:
                reader = await summaries.acquire_summary_reader(summary['id'])
            except summaries.SummaryGone:
                raise SpeechError('The summary is no longer available', 410)
            summary_source = SummaryParts(reader, *limits(speech_request))
            parts = summary_source
        source, adapt_reason, direct = None, None, False
        engine, then = None, None
    else:
        choice = _policy['adapt'] if _policy is not None else await sync_to_async(_stored_adapt_choice)()
        cleaned = None
        if choice != "off" and config_id is None:
            cleaned = await sync_to_async(direct_text)(speech_request.text, origin)
            if cleaned is not None and not has_speech(cleaned):
                raise SpeechError(NOTHING_TO_SAY, 400)
        direct = cleaned is not None
        if direct:
            engine, adapt_reason = None, None
            label["adapted"] = "direct"
        else:
            engine, adapt_reason = resolve_engine(choice, speech_request.text)
        then = fallback_engine(engine) if engine == OPENROUTER_ENGINE else None
        if engine is None and not direct:
            label["adapted"] = "no"
            label["adapt_reason"] = adapt_reason
        parts = split(speech_request._replace(text=cleaned) if direct else speech_request)
        source = None
        if engine is not None:
            source = AdaptedParts(
                speech_request.text, engine, *limits(speech_request), fallback=parts, label=label, then=then
            )
    if use_cache:
        if summary is not None:
            names = [SUMMARY_ENGINE]
        else:
            names = [DIRECT_ENGINE] if direct else [None] if engine is None else [engine.name]
        if then is not None:
            names.append(then.name)
        cache_request = _summary_cache_request(speech_request, summary_source) if summary_source else speech_request
        for name in names:
            key = (cache_request, name, announcement) if announcement else (cache_request, name)
            cached = speech_cache.get(key) if cache_request is not None else None
            if cached is not None:
                if summary_source is not None:
                    await summary_source.aclose()
                if name is not None:
                    label["adapted"] = name
                return _opened(stream_from_bytes(cached.content, cached.content_type, label=label))
    buffered = data.get("buffered") is True
    pump_parts = parts if source is None else source
    if announcement and has_speech(announcement):
        prefix = split(speech_request._replace(text=announcement))
        if prefix:
            pump_parts = PrefixedParts(prefix, as_part_source(speech_request.text, pump_parts))
    paced = not buffered
    stream = start_stream(
        lambda sink: pump(sink, pump_parts, paced),
        label=label,
        on_end=_end_steps(
            speech_request, api_key, config_id, use_cache, source, adapt_reason,
            cache_name=SUMMARY_ENGINE if summary is not None else DIRECT_ENGINE if direct else None,
            announcement=announcement, summary_source=summary_source,
        ),
    )
    try:
        await stream.ready
    except UpstreamSpeechError as error:
        # The stream is failed, and logged, already: only drop its id.
        release_stream(stream.id)
        if summary_source is not None:
            await summary_source.reader.aclose()
        extra = {} if error.upstream_status is None else {"upstream_status": error.upstream_status}
        raise SpeechError(error.message, SPEECH_ERROR_STATUS, **extra)
    except asyncio.CancelledError:
        # A client disconnect: stop the upstream call.
        release_stream(stream.id, outcome="cancelled")
        if summary_source is not None:
            await summary_source.reader.aclose()
        raise
    except Exception:
        # The pump's own error: its task logged it with its traceback already.
        release_stream(stream.id)
        if summary_source is not None:
            await summary_source.reader.aclose()
        raise SpeechError(SPEECH_FAILED, 500)
    return _opened(stream)


def _summary_cache_request(speech_request, source):
    """Only an exact, complete durable summary can share saved-summary audio."""
    snapshot = source.reader.snapshot
    if snapshot['state'] != 'complete' or snapshot['saved_id'] is None:
        return None
    return speech_request._replace(text=snapshot['text'])


def _stored_adapt_choice() -> str | None:
    """The stored text adaptation choice (None: on, with the first running provider)."""
    return read_tts_config()["adapt"]


def _adaptation_fields(source: AdaptedParts | None, adapt_reason: str | None) -> dict:
    """The adaptation fields of a last test: how *source* adapted the text, the engine of its last
    attempt (and why OpenRouter failed before an agent took over), or, without a source, why the
    text was read as written. The adapted text is cut to ``TEST_TEXT_KEEP_CHARS``."""
    if source is None:
        return {
            "adapt_engine": None,
            "adapted_by": None,
            "adapt_reason": adapt_reason,
            "adapt_first_ms": None,
            "adapted_text": None,
            "fallback_reason": None,
        }
    adapted_text = source.adapted_text
    return {
        "adapt_engine": source.adapt_engine,
        "adapted_by": source.adapted_by,
        "adapt_reason": source.adapt_reason,
        "adapt_first_ms": source.adapt_first_ms,
        "adapted_text": None if adapted_text is None else adapted_text[:TEST_TEXT_KEEP_CHARS],
        "fallback_reason": source.fallback_reason,
    }


def _end_steps(
    speech_request: SpeechRequest | ScriptSpeechRequest,
    api_key: str | None,
    config_id: str | None,
    use_cache: bool,
    source: AdaptedParts | None,
    adapt_reason: str | None,
    cache_name: str | None = None,
    announcement: str = "",
    summary_source: SummaryParts | None = None,
):
    """The ``on_end`` of a speech: once its audio is complete, cache it, record the test run, look up the cost.

    Each step has its own guard: one failure never skips the others. A failed speech does nothing.
    The audio is cached under the adaptation that really ran: a reading that fell back to the
    original text is cached as unadapted (*source*'s ``adapted_by`` is then None). The cost of a
    split speech is the sum of its parts' costs: it is recorded only when every part has a
    generation id. A script speech (no *api_key*) has no cost lookup. A direct reading passes its
    *cache_name* (``"direct"``), which replaces the engine name.
    """

    async def on_end(stream: AudioStream) -> None:
        if stream.state != "complete":
            return
        # A pump that marks no part is one part (the rule of ``AudioStream.finish``).
        parts = max(stream.parts_sent, 1)
        # An empty audio is not worth a cache entry: the next read asks again.
        if use_cache and stream.data:
            try:
                adapted_by = cache_name or (None if source is None else source.adapted_by)
                cache_request = _summary_cache_request(speech_request, summary_source) if summary_source else speech_request
                if cache_request is not None:
                    key = (cache_request, adapted_by, announcement) if announcement else (cache_request, adapted_by)
                    speech_cache.set(key, CachedAudio(bytes(stream.data), stream.content_type))
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
