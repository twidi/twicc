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

A long text is sent in parts, one request each, into the same stream. A paced
stream (a split speech) delivers its audio at most ``PACE_MARGIN`` seconds ahead
of its playback position, and the pump waits before a next part while more than
``LOOKAHEAD_SECONDS`` of audio is ahead: a stop then spares the parts not sent.
Both need the byte rate of the audio; when it is unknown, the speech is neither
paced nor held.

Exactly one log line per speech, at its first terminal event. It never holds
the API key: this module never sees it.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from twicc.tts.openrouter import RATE_SCAN_BYTES, SpeechError, bytes_per_second, mp3_frame_bitrates

logger = logging.getLogger(__name__)

UNCLAIMED_TTL = 30.0
SAFETY_TTL = 3 * 3600.0

STOPPED_MESSAGE = "Text-to-speech stopped"
FAILED_MESSAGE = "Text-to-speech failed"
PART_TYPE_CHANGED = "The audio format changed between two parts of the text"

# Audio ahead of the playback position past which the pump waits before a next part.
LOOKAHEAD_SECONDS = 60.0
# How far ahead of the playback position a paced read delivers (never above LOOKAHEAD_SECONDS).
PACE_MARGIN = 10.0
# The shortest wait of a paced read for its next byte.
PACE_TICK = 1.0

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

    The playback position of a paced stream is estimated from the time of its
    first audio byte and its byte rate.
    """

    def __init__(self, label: dict):
        self.id: str = secrets.token_urlsafe(24)
        self.label = label  # model, voice, format, chars: for the log line
        self.content_type: str | None = None
        self.generation_id: str | None = None  # the first part's
        self.generation_ids: list[str] = []  # the non-empty ids of every part, in order
        self.parts_sent = 0
        self.paced = False
        self.state = "opening"
        self.error: str | None = None
        self.log_message: str | None = None  # what the log line writes instead of ``error``
        self.upstream_status: int | None = None
        self.data = bytearray()
        self.first_audio_ms: int | None = None
        self.generation_ms: int | None = None
        # Resolved when the headers arrive; fails when the speech ends before them.
        self.ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.claimed = False
        self._started = time.monotonic()
        self._first_audio_at: float | None = None
        self._part_started: float | None = None
        self._parts_seconds = 0.0
        # The byte rate, once decided (``None`` then means unknown for good).
        self._rate: float | None = None
        self._rate_decided = False
        # An MP3 part ended after some audio: its rate can be decided below RATE_SCAN_BYTES.
        self._rate_scan_due = False
        # Set, then replaced, on each change: every waiting reader wakes up.
        self._more = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._unclaimed_timer: asyncio.TimerHandle | None = None
        self._safety_timer: asyncio.TimerHandle | None = None

    # -- Sink: called by the pump. ------------------------------------------

    def begin_part(self) -> None:
        """A part request starts (the first one comes before any headers)."""
        self._expect("begin_part", *_LIVE_STATES)
        self.parts_sent += 1
        self._part_started = time.monotonic()

    def end_part(self) -> None:
        """The part's last byte arrived: its request-to-last-byte time counts as generation time."""
        self._expect("end_part", "running")
        if self._part_started is None:
            raise RuntimeError("AudioStream.end_part() without a started part")
        self._parts_seconds += time.monotonic() - self._part_started
        self._part_started = None
        if self._first_audio_at is not None:
            self._rate_scan_due = True
            self.byte_rate()  # decide it on this part's bytes, before the next part grows them

    def add_generation_seconds(self, seconds: float) -> None:
        """Time the pump waited for a part's text (its adaptation): it counts as generation time."""
        self._expect("add_generation_seconds", *_LIVE_STATES)
        self._parts_seconds += seconds

    def set_headers(self, content_type: str, generation_id: str | None) -> None:
        self._expect("set_headers", "opening")
        self.content_type = content_type
        self.generation_id = generation_id
        if generation_id:
            self.generation_ids.append(generation_id)
        self.state = "running"
        self._settle_ready()
        self._arm_unclaimed_timer()

    def add_part_headers(self, content_type: str, generation_id: str | None) -> bool:
        """A later part's headers. Another type than the first part's fails the stream (False)."""
        self._expect("add_part_headers", "running")
        if content_type != self.content_type:
            self.fail(SpeechError(PART_TYPE_CHANGED))
            return False
        if generation_id:
            self.generation_ids.append(generation_id)
        return True

    def append(self, chunk: bytes) -> None:
        self._expect("append", "running")
        if not chunk:
            return
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            self.first_audio_ms = _elapsed_ms(self._started)
        self.data += chunk
        self._wake()

    def finish(self) -> None:
        self._expect("finish", "running")
        if self._part_started is not None:  # a part still open counts up to now
            self._parts_seconds += time.monotonic() - self._part_started
            self._part_started = None
        # A pump that marks no part is one part, from the stream creation on.
        if self.parts_sent:
            self.generation_ms = round(self._parts_seconds * 1000)
        else:
            self.generation_ms = _elapsed_ms(self._started)
        self._end("complete", "ok")

    def pace(self) -> None:
        """Deliver the audio at the playback pace, and hold the next parts by the lookahead."""
        self.paced = True

    def fail(self, error: SpeechError) -> None:
        self._expect("fail", *_LIVE_STATES)
        self.error = error.message
        self.log_message = error.log_message
        self.upstream_status = error.upstream_status
        self._settle_ready(error)
        self._end("failed", "failed" if error.upstream_status is None else f"upstream_error {error.upstream_status}")

    def fail_if_live(self, error: SpeechError) -> None:
        """Fail the stream unless it ended already: the first failure stands (an error while the
        pump closes the upstream call can follow an upstream error that failed it)."""
        if self.state in _LIVE_STATES:
            self.fail(error)

    # -- Readers: called by the views. ---------------------------------------

    def byte_rate(self) -> float | None:
        """The audio bytes per second, or None while unknown.

        PCM: from its type. MP3: decided once, on the received bytes, when they reach
        ``RATE_SCAN_BYTES`` or when a part ends after some audio; before that it is None,
        undecided. The scanned frames must share one bitrate, else it is None for good.
        """
        if self._rate_decided:
            return self._rate
        if self.content_type is None:
            return None
        if self.content_type.split(";", 1)[0].strip().lower() == "audio/mpeg":
            if len(self.data) < RATE_SCAN_BYTES and not self._rate_scan_due:
                return None
            bitrates = mp3_frame_bitrates(self.data)
            rate = bytes_per_second(self.content_type, self.data) if len(set(bitrates)) == 1 else None
        else:
            rate = bytes_per_second(self.content_type, self.data)
        self._rate, self._rate_decided = rate, True
        return rate

    def _playback_seconds(self) -> float | None:
        """Seconds since the first audio byte; None when not paced or without byte rate or audio."""
        if not self.paced or self._first_audio_at is None or self.byte_rate() is None:
            return None
        return time.monotonic() - self._first_audio_at

    def deliverable(self) -> int:
        """The bytes any read may send now: all received, or up to PACE_MARGIN past the position."""
        elapsed = self._playback_seconds()
        if elapsed is None:
            return len(self.data)
        return min(len(self.data), int((elapsed + PACE_MARGIN) * self._rate))

    async def wait_for_more(self, offset: int) -> None:
        """Return when bytes past *offset* are deliverable, or when no more will come."""
        while True:
            if self.state == "released" or self.deliverable() > offset:
                return
            if self.state not in _LIVE_STATES and offset >= len(self.data):
                return
            more = self._more
            elapsed = self._playback_seconds()
            if elapsed is None or len(self.data) <= offset:
                await more.wait()
                continue
            # Paced: the byte at *offset* is received; wait until the position reaches it.
            needed = (offset + 1) / self._rate - PACE_MARGIN - elapsed
            try:
                async with asyncio.timeout(max(PACE_TICK, needed)):
                    await more.wait()
            except TimeoutError:
                pass

    async def wait_for_lookahead(self) -> None:
        """Before a next part: wait while more than LOOKAHEAD_SECONDS of audio is ahead."""
        elapsed = self._playback_seconds()
        if elapsed is None:
            return
        ahead = len(self.data) / self._rate - elapsed
        if ahead > LOOKAHEAD_SECONDS:
            await asyncio.sleep(ahead - LOOKAHEAD_SECONDS)

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
        # ``error`` is the user-safe message of the speech (never the key), or None. A failure's
        # log message replaces it (a script's exit status, never its stderr line).
        logger.info(
            "TTS speech %s parts=%d bytes=%d first_audio_ms=%s generation_ms=%s duration_ms=%d generation=%s "
            "error=%r outcome=%s",
            " ".join(f"{name}={value}" for name, value in self.label.items()),
            self.parts_sent,
            len(self.data),
            self.first_audio_ms,
            self.generation_ms,
            _elapsed_ms(self._started),
            ",".join(self.generation_ids) or None,
            self.error if self.log_message is None else self.log_message,
            outcome,
        )


def _register(stream: AudioStream) -> None:
    _streams[stream.id] = stream
    stream.touch()


def retain_stream(audio_id: str) -> None:
    """Transfer the unread expiry lease to a native session.

    The native owner releases on finish, cancellation, or disconnect expiry.
    Keep the existing safety timer and actual HTTP claim unchanged.
    """
    stream = get_stream(audio_id)
    if stream is not None and stream._unclaimed_timer is not None:
        stream._unclaimed_timer.cancel()
        stream._unclaimed_timer = None


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
    task.add_done_callback(lambda _task: _end_unstarted(stream))
    return stream


def _end_unstarted(stream: AudioStream) -> None:
    """The task ended: settle what ``_run`` did not.

    A task cancelled before its first step never enters ``_run``: its ``finally``
    never settles ``ready``, and its stream would stay live, readers waiting.
    """
    stream._settle_ready(SpeechError(STOPPED_MESSAGE))
    if stream.state in _LIVE_STATES:
        stream.error = STOPPED_MESSAGE
        stream._end("failed", "failed")


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
