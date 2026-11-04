"""The parts of a speech, as the speech pumps pull them.

A part source gives the parts one at a time, so that its parts can come over time (the adaptation
of the text, ``twicc.tts.adapt``). It tells whether the reading is split once it gave its first
part. This module imports nothing from ``twicc.tts``: the pumps' modules import it.
"""

import time
from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from twicc.tts.streams import AudioStream


@runtime_checkable
class PartSource(Protocol):
    """Where a pump takes the parts of a speech from."""

    # Whether the reading is split (more than one part): known once the first part was returned.
    split: bool | None

    async def next_part(self) -> str | None:
        """The next part, or ``None`` when there is no more. May raise ``SpeechError``."""
        ...

    async def aclose(self) -> None:
        """Release what the source holds (the pump always calls it)."""
        ...


class StaticParts:
    """The parts of a text split before the speech: all known at the start."""

    def __init__(self, parts: Sequence[str]) -> None:
        self._parts = deque(parts)
        self.split = len(parts) > 1

    async def next_part(self) -> str | None:
        return self._parts.popleft() if self._parts else None

    async def aclose(self) -> None:
        self._parts.clear()


class PrefixedParts:
    """Plain speech parts before a body source, with the body's lifetime."""

    def __init__(self, prefix: Sequence[str], body: PartSource) -> None:
        self._prefix = deque(prefix)
        self._body = body
        self._has_prefix = bool(self._prefix)

    @property
    def split(self) -> bool | None:
        # A spoken prefix and the message require separate audio parts.
        return True if self._has_prefix else self._body.split

    async def next_part(self) -> str | None:
        if self._prefix:
            return self._prefix.popleft()
        return await self._body.next_part()

    async def aclose(self) -> None:
        self._prefix.clear()
        await self._body.aclose()


def as_part_source(text: str, parts: Sequence[str] | PartSource | None) -> PartSource:
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
