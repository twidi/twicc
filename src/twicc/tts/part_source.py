"""The parts of a speech, as the speech pumps pull them.

A part source gives the parts one at a time, so that its parts can come over time (the adaptation
of the text, ``twicc.tts.adapt``). It tells whether the reading is split once it gave its first
part. This module imports nothing from ``twicc.tts``: the pumps' modules import it.
"""

from collections import deque
from collections.abc import Sequence
from typing import Protocol, runtime_checkable


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
