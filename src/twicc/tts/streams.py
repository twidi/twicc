"""In-memory audio streams of the text-to-speech speeches.

The open step starts a pump (the upstream call) in a background task. The pump
writes into an :class:`AudioStream`. The read, ending and release steps find the
stream by its random id. Every view runs on the one event loop of the ASGI
server: no lock.

An audio id lives until it is released, with two expiries:

- unclaimed: ``UNCLAIMED_TTL`` seconds after its headers, when no read claimed it;
- safety: ``SAFETY_TTL`` seconds after its last activity (open, read, ending, release).

No reader is not a stop: a media element can close its read and come back
with a range, so nothing here closes the upstream because no reader is connected.

Exactly one log line per speech, at its first terminal event. It never holds
the API key: this module never sees it.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from twicc.tts.openrouter import SpeechError

logger = logging.getLogger(__name__)

UNCLAIMED_TTL = 30.0
SAFETY_TTL = 3 * 3600.0

STOPPED_MESSAGE = "Text-to-speech stopped"
FAILED_MESSAGE = "Text-to-speech failed"

# The pump still runs (or has not started yet).
_LIVE_STATES = ("opening", "running")

_streams: dict[str, "AudioStream"] = {}
# Strong references to the running pump tasks (asyncio keeps only weak ones).
# A released stream leaves the registry while its task can still run ``on_end``.
_tasks: set[asyncio.Task] = set()


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


class AudioStream:
    """One speech: its received bytes, its state and the futures of its readers.

    States: ``opening`` (no headers yet), ``running``, ``complete``, ``failed``,
    ``released``. The pump calls the sink methods; the views call the reader methods.
    """

    def __init__(self, label: dict):
        self.id: str = secrets.token_urlsafe(24)
        self.label = label  # model, voice, format, chars: for the log line
        self.content_type: str | None = None
        self.generation_id: str | None = None
        self.state = "opening"
        self.error: str | None = None
        self.upstream_status: int | None = None
        self.data = bytearray()
        self.first_audio_ms: int | None = None
        self.generation_ms: int | None = None
        # Resolved when the headers arrive; fails when the speech ends before them.
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.claimed = False
        self._started = time.monotonic()
        # Set, then replaced, on each change: every waiting reader wakes up.
        self._more = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._unclaimed_timer: asyncio.TimerHandle | None = None
        self._safety_timer: asyncio.TimerHandle | None = None

    # -- Sink: called by the pump. ------------------------------------------

    def set_headers(self, content_type: str, generation_id: str | None) -> None:
        self._expect("set_headers", "opening")
        self.content_type = content_type
        self.generation_id = generation_id
        self.state = "running"
        self._settle_ready()
        self._arm_unclaimed_timer()

    def append(self, chunk: bytes) -> None:
        self._expect("append", "running")
        if not chunk:
            return
        if self.first_audio_ms is None:
            self.first_audio_ms = _elapsed_ms(self._started)
        self.data += chunk
        self._wake()

    def finish(self) -> None:
        self._expect("finish", "running")
        self.generation_ms = _elapsed_ms(self._started)
        self._end("complete", "ok")

    def fail(self, error: SpeechError) -> None:
        self._expect("fail", *_LIVE_STATES)
        self.error = error.message
        self.upstream_status = error.upstream_status
        self._settle_ready(error)
        self._end("failed", "failed" if error.upstream_status is None else f"upstream_error {error.upstream_status}")

    # -- Readers: called by the views. ---------------------------------------

    async def wait_for_more(self, offset: int) -> None:
        """Return when there are bytes past *offset*, or when the stream no longer grows."""
        while len(self.data) <= offset and self.state in _LIVE_STATES:
            await self._more.wait()

    def touch(self) -> None:
        """Re-arm the safety timer: it counts from the last activity."""
        if _streams.get(self.id) is not self:
            return  # released, or never registered
        if self._safety_timer is not None:
            self._safety_timer.cancel()
        self._safety_timer = asyncio.get_running_loop().call_later(
            SAFETY_TTL, release_stream, self.id, "expired_unreleased"
        )

    # -- Internals. ------------------------------------------------------------

    def _expect(self, method: str, *states: str) -> None:
        if self.state not in states:
            raise RuntimeError(f"AudioStream.{method}() in state {self.state!r}")

    def _settle_ready(self, error: BaseException | None = None) -> None:
        # A cancelled open view cancels the future it awaits: never settle it twice.
        if self.ready.done():
            return
        if error is None:
            self.ready.set_result(None)
            return
        self.ready.set_exception(error)
        # Mark it retrieved: a failed speech nobody waits for must not log
        # "Future exception was never retrieved". A waiter still gets the error.
        self.ready.exception()

    def _arm_unclaimed_timer(self) -> None:
        if _streams.get(self.id) is not self:
            return
        self._unclaimed_timer = asyncio.get_running_loop().call_later(UNCLAIMED_TTL, _expire_unclaimed, self.id)

    def _cancel_timers(self) -> None:
        for timer in (self._unclaimed_timer, self._safety_timer):
            if timer is not None:
                timer.cancel()
        self._unclaimed_timer = self._safety_timer = None

    def _wake(self) -> None:
        self._more.set()
        self._more = asyncio.Event()

    def _end(self, state: str, outcome: str) -> None:
        """The first terminal event: the new state, the readers woken, the log line."""
        self.state = state
        self._wake()
        self._log(outcome)

    def _log(self, outcome: str) -> None:
        logger.info(
            "TTS speech %s bytes=%d first_audio_ms=%s generation_ms=%s generation=%s outcome=%s",
            " ".join(f"{name}={value}" for name, value in self.label.items()),
            len(self.data),
            self.first_audio_ms,
            self.generation_ms,
            self.generation_id,
            outcome,
        )


def _register(stream: AudioStream) -> None:
    _streams[stream.id] = stream
    stream.touch()


def _expire_unclaimed(audio_id: str) -> None:
    stream = _streams.get(audio_id)
    if stream is not None and not stream.claimed:
        release_stream(audio_id, "expired_unread")


async def _run(
    stream: AudioStream,
    pump: Callable[[AudioStream], Awaitable[None]],
    on_end: Callable[[AudioStream], Awaitable[None]] | None,
) -> None:
    try:
        await pump(stream)
        if stream.state in _LIVE_STATES:
            raise RuntimeError("The speech pump returned without finish() or fail()")
    except asyncio.CancelledError:
        # A release set the state already. Anything else (a server shutdown) ends
        # the speech here, so that no reader waits for bytes that never come.
        if stream.state in _LIVE_STATES:
            stream.error = STOPPED_MESSAGE
            stream._end("failed", "failed")
        raise
    except Exception as error:
        logger.exception("TTS speech pump failed")
        stream._settle_ready(error)
        if stream.state in _LIVE_STATES:
            stream.error = FAILED_MESSAGE
            stream._end("failed", "failed")
    finally:
        # Whatever happened, no open view waits forever.
        stream._settle_ready(SpeechError(STOPPED_MESSAGE))
    if on_end is not None and stream.state in ("complete", "failed"):
        try:
            await on_end(stream)
        except Exception:
            logger.exception("TTS speech end step failed")


def start_stream(
    pump: Callable[[AudioStream], Awaitable[None]],
    *,
    label: dict,
    on_end: Callable[[AudioStream], Awaitable[None]] | None = None,
) -> AudioStream:
    """Register a new stream and run ``pump(stream)`` in a background task.

    *on_end* runs in the same task once the pump ended the stream ``complete``
    or ``failed`` (not after a release while the pump ran).
    """
    stream = AudioStream(label)
    _register(stream)
    task = asyncio.get_running_loop().create_task(_run(stream, pump, on_end))
    stream._task = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return stream


def stream_from_bytes(content: bytes, content_type: str, *, label: dict) -> AudioStream:
    """A registered stream, already complete, for a cache hit."""
    stream = AudioStream(label)
    stream.content_type = content_type
    stream.data = bytearray(content)
    stream.state = "complete"
    _register(stream)
    stream._settle_ready()
    stream._arm_unclaimed_timer()
    stream._log("cached")
    return stream


def get_stream(audio_id: str) -> AudioStream | None:
    return _streams.get(audio_id)


def release_stream(audio_id: str, outcome: str = "released") -> None:
    """Drop the stream; while its pump runs, stop it and log *outcome*. Idempotent.

    After ``complete`` / ``failed``, the task is left alone: a running ``on_end``
    still records and caches the audio.
    """
    stream = _streams.pop(audio_id, None)
    if stream is None:
        return
    stream._cancel_timers()
    if stream.state in _LIVE_STATES:
        if stream._task is not None:
            stream._task.cancel()  # its ``async with`` closes the upstream call
        stream._settle_ready(SpeechError(STOPPED_MESSAGE))
        stream._end("released", outcome)
    else:
        stream._wake()


def ending_of(audio_id: str) -> dict:
    """How the speech ended, for the ending step: it never waits."""
    stream = _streams.get(audio_id)
    if stream is None:
        return {"state": "unknown", "error": None}
    state = "running" if stream.state in _LIVE_STATES else stream.state
    return {"state": state, "error": stream.error}


def _reset_for_tests() -> None:
    """Forget every stream (tests only; each test runs its own event loop)."""
    for stream in _streams.values():
        stream._cancel_timers()
    _streams.clear()
    _tasks.clear()
