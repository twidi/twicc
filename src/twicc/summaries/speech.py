"""Feed an independently owned summary reader to the existing speech pumps."""

from collections import deque
from typing import TYPE_CHECKING

from twicc.tts.openrouter import SpeechError
from twicc.tts.parts import PartSplitter
from twicc.core.ui_messages import ui_message_text

if TYPE_CHECKING:
    from twicc.summaries.service import SummaryReader

SUMMARY_STOPPED = ui_message_text('The summary stopped before the end', 'backend.tts.summaryStopped')


class SummaryParts:
    """Pull speech parts from full revisioned snapshots, without another text call."""

    def __init__(self, reader: "SummaryReader", limit: int | None, first: int | None) -> None:
        self.reader = reader
        self._limit, self._first = limit, first
        self._splitter = PartSplitter(limit, first)
        self._snapshots = reader.snapshots()
        self._revision = None
        self._received = ''
        self._parts: deque[str] = deque()
        self._returned = False
        self._ended = False
        self._failed = False
        self._closed = False
        self.split: bool | None = None

    def _update(self, snapshot: dict) -> None:
        revision, text = snapshot['revision'], snapshot['text']
        if revision != self._revision:
            if self._returned:
                # The service's commitment latch prevents this; never restart a spoken body.
                self._parts.clear()
                self._failed = True
                return
            self._revision = revision
            self._received = ''
            self._parts.clear()
            self._splitter = PartSplitter(self._limit, self._first)
        if self._ended or self._failed:
            return
        self._parts.extend(self._splitter.feed(text[len(self._received):]))
        self._received = text
        state = snapshot['state']
        if state == 'complete':
            self._parts.extend(self._splitter.end())
            self._ended = True
        elif state != 'running':
            self._failed = True
            if not self._returned:
                self._parts.clear()
            else:
                tail = self._splitter.tail()
                if tail:
                    self._parts.append(tail)

    async def next_part(self) -> str | None:
        while not self._closed:
            # Always check the latest revision before releasing queued provisional text.
            self._update(self.reader.snapshot)
            if self._parts:
                if not self._returned:
                    self.split = not self._ended or len(self._parts) > 1
                    # No await separates the latch and handing the body to the pump.
                    self.reader.mark_audio_committed()
                self._returned = True
                return self._parts.popleft()
            if self._failed:
                raise SpeechError(SUMMARY_STOPPED)
            if self._ended:
                return None
            try:
                self._update(await anext(self._snapshots))
            except StopAsyncIteration:
                self._failed = True
        return None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._parts.clear()
        await self._snapshots.aclose()
        await self.reader.aclose()
