"""In-memory cache of generated speech audio, capped by total size and by age.

The key is the :class:`~twicc.tts.openrouter.SpeechRequest` itself: the text and
the speech fields. One process-wide instance: :data:`speech_cache`.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import NamedTuple

from twicc.tts.openrouter import SpeechRequest


class CachedAudio(NamedTuple):
    content: bytes
    content_type: str


class _Entry(NamedTuple):
    audio: CachedAudio
    stored_at: float


class AudioCache:
    """Least-recently-used cache of audio bodies.

    The ``OrderedDict`` keeps the recency order: the first entry is the least
    recently used. An entry expires *ttl* seconds after it was stored; reading it
    refreshes its recency, not its lifetime.
    """

    def __init__(
        self,
        max_bytes: int = 50 * 1024 * 1024,
        ttl: float = 600.0,
        now: Callable[[], float] = time.monotonic,
    ):
        self._max_bytes = max_bytes
        self._ttl = ttl
        self._now = now
        self._entries: OrderedDict[SpeechRequest, _Entry] = OrderedDict()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def get(self, key: SpeechRequest) -> CachedAudio | None:
        """The cached audio, or None when absent or expired."""
        self._drop_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.audio

    def set(self, key: SpeechRequest, audio: CachedAudio) -> bool:
        """Store *audio*. Returns False, storing nothing, when it alone exceeds the cap."""
        size = len(audio.content)
        if size > self._max_bytes:
            return False
        self._remove(key)
        self._drop_expired()
        while self._total_bytes + size > self._max_bytes:
            self._remove(next(iter(self._entries)))
        self._entries[key] = _Entry(audio, self._now())
        self._total_bytes += size
        return True

    def clear(self) -> None:
        self._entries.clear()
        self._total_bytes = 0

    def _remove(self, key: SpeechRequest) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= len(entry.audio.content)

    def _drop_expired(self) -> None:
        now = self._now()
        for expired in [key for key, entry in self._entries.items() if now - entry.stored_at >= self._ttl]:
            self._remove(expired)


speech_cache = AudioCache()
