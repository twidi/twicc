"""Coalesced FIFO admission and finite drain targets, owned by one event loop."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import Literal, NamedTuple

from watchfiles import Change

logger = logging.getLogger(__name__)


class QueuedSessionChange(NamedTuple):
    path: Path
    change: Change
    token: int


class PathDrainTarget(NamedTuple):
    source_generation: object
    end_offset: int


class MigrationRelease(NamedTuple):
    session_id: str
    path: Path | None
    outcome: Literal['ready', 'failed', 'cancelled']
    replay: bool
    error: str | None
    release_token: int


class _DrainWaiter(NamedTuple):
    target: PathDrainTarget
    event_token: int


class _PathState:
    def __init__(self):
        self.token = 0
        self.change = Change.modified
        self.queued = False
        self.in_flight = False
        self.deferred = False
        self.generation: object = object()
        self.offset = 0
        self.release: MigrationRelease | None = None
        self.release_event_token = 0
        self.turn_release_token = -1
        self.terminal: str | None = None
        self.waiters: dict[asyncio.Future, _DrainWaiter] = {}


class SessionChangeQueue:
    """One consumer; events during a turn never get cleared by that turn."""

    def __init__(self):
        self._paths: dict[Path, _PathState] = {}
        self._ready: deque[Path] = deque()
        self._wake = asyncio.Event()
        self._in_flight = False
        self.closed = False

    @property
    def idle(self) -> bool:
        return not self._ready and not self._in_flight

    def _state(self, path: Path) -> _PathState:
        return self._paths.setdefault(path, _PathState())

    def _schedule(self, path: Path, state: _PathState) -> None:
        if not state.queued and not state.in_flight:
            state.queued = True
            self._ready.append(path)
            self._wake.set()

    def enqueue(self, path: Path, change: Change) -> None:
        if self.closed:
            raise RuntimeError('Session change queue is closed')
        state = self._state(path)
        state.token += 1
        state.change = change
        state.terminal = None
        state.deferred = False
        if change == Change.deleted:
            self.observe_source(path, object())
        self._schedule(path, state)

    async def next_change(self) -> QueuedSessionChange:
        while not self._ready:
            if self.closed:
                self._settle_closed()
                raise asyncio.CancelledError
            self._wake.clear()
            await self._wake.wait()
        path = self._ready.popleft()
        state = self._paths[path]
        state.queued = False
        state.in_flight = True
        self._in_flight = True
        state.turn_release_token = state.release.release_token if state.release else -1
        return QueuedSessionChange(path, state.change, state.token)

    def finish(self, turn: QueuedSessionChange, *, has_more: bool,
               deferred: bool = False, failed: bool = False) -> None:
        state = self._paths[turn.path]
        state.in_flight = False
        self._in_flight = False
        newer = state.token > turn.token
        release = state.release
        if release and release.release_token > state.turn_release_token and (
            deferred or release.replay or release.outcome != 'ready'
        ):
            # A release can arrive after the provider's check, before finish.
            state.deferred = True
            self._apply_release(turn.path, state, release, admitted_after_turn=newer)
        elif failed:
            self._settle(state)
            state.terminal = 'failed'
            if newer:
                self._schedule(turn.path, state)
        elif deferred:
            state.deferred = True
            if newer:
                self._schedule(turn.path, state)
        elif (has_more and not self.closed) or newer:
            if not newer:
                state.change = Change.modified
            self._schedule(turn.path, state)
        self._settle_closed()

    def current_change(self, path: Path) -> Change:
        return self._state(path).change

    def event_token(self, path: Path) -> int:
        return self._state(path).token

    def has_pending_change(self, path: Path) -> bool:
        state = self._state(path)
        return state.queued or state.in_flight or state.deferred

    def resume_pending_change(self, path: Path) -> None:
        """Resume identification without admitting an event after its release."""
        if not self.closed and self.has_pending_change(path):
            self._schedule(path, self._state(path))

    def release_token(self, path: Path) -> int:
        release = self._state(path).release
        return release.release_token if release else -1

    def source_generation(self, path: Path) -> object:
        return self._state(path).generation

    def observe_source(self, path: Path, generation: object) -> None:
        state = self._state(path)
        if state.generation is not generation:
            if state.waiters:
                logger.warning('Session source replaced or deleted while draining %s', path)
            self._settle(state)
            state.generation = generation
            state.offset = 0

    def committed(self, path: Path, target: PathDrainTarget) -> None:
        state = self._state(path)
        if state.generation is not target.source_generation:
            return
        release = state.release
        if (state.in_flight and release is not None and release.release_token > state.turn_release_token
                and release.outcome != 'ready'):
            # A pre-lease classification failure can overlap a committed slice.
            # finish must settle the terminal outcome before checkpoint success.
            return
        state.offset = max(state.offset, target.end_offset)
        for future, requested in list(state.waiters.items()):
            if requested.target.end_offset <= state.offset:
                if not future.done():
                    future.set_result(None)
                state.waiters.pop(future)

    async def wait_drained(self, path: Path, *, target: PathDrainTarget) -> None:
        state = self._state(path)
        if state.terminal == 'cancelled' or (self.closed and self.idle):
            raise asyncio.CancelledError
        if (state.generation is not target.source_generation or state.offset >= target.end_offset
                or state.terminal == 'failed'):
            return
        future = asyncio.get_running_loop().create_future()
        state.waiters[future] = _DrainWaiter(target, state.token)
        try:
            await asyncio.shield(future)
        finally:
            state.waiters.pop(future, None)
            if not future.done():
                future.cancel()

    def notify_migration_released(self, release: MigrationRelease, *, event_token: int | None = None) -> bool:
        if release.path is None:
            return False
        state = self._state(release.path)
        if state.release and release.release_token <= state.release.release_token:
            return False
        state.release = release
        state.release_event_token = state.token if event_token is None else event_token
        if state.in_flight:
            # finish consumes this outcome after the callback releases its locks.
            return True
        self._apply_release(release.path, state, release)
        return True

    def _apply_release(
        self, path: Path, state: _PathState, release: MigrationRelease, *, admitted_after_turn: bool = False,
    ) -> None:
        if release.outcome == 'ready':
            if state.deferred or state.queued or release.replay:
                state.deferred = False
                state.terminal = None
                if not self.closed or admitted_after_turn:
                    self._schedule(path, state)
        else:
            if release.outcome == 'failed':
                logger.error('Session migration failed for %s: %s', path, release.error)
            state.deferred = False
            self._settle(state, cancel=release.outcome == 'cancelled', through_token=state.release_event_token)
            if state.token > state.release_event_token:
                # A later explicit admission owns a new attempt. Neither the
                # failed turn's backlog nor an earlier dirty event can retry.
                state.terminal = None
                self._schedule(path, state)
            else:
                state.terminal = release.outcome
                if state.queued:
                    self._ready.remove(path)
                    state.queued = False
        self._settle_closed()

    @staticmethod
    def _settle(state: _PathState, *, cancel: bool = False, through_token: int | None = None) -> None:
        for future, requested in list(state.waiters.items()):
            if through_token is not None and requested.event_token > through_token:
                continue
            if not future.done():
                if cancel:
                    future.cancel()
                else:
                    future.set_result(None)
            state.waiters.pop(future)

    def _settle_closed(self) -> None:
        if self.closed and self.idle:
            for state in self._paths.values():
                self._settle(state, cancel=True)
            self._wake.set()

    def close(self, *, cancel_pending: bool = False) -> None:
        """Drain admitted turns, including pre-close dirty events, then cancel waiters.

        Backlog alone cannot admit another turn after close. This bounds shutdown
        even when the external producer continues appending complete records.
        A watcher stopped before activating its consumer cancels queued work
        instead: it cannot process that work before search readiness.
        """
        if cancel_pending and self._in_flight:
            raise RuntimeError('Cannot cancel pending changes with an active turn')
        self.closed = True
        if cancel_pending:
            while self._ready:
                self._paths[self._ready.popleft()].queued = False
        self._wake.set()
        self._settle_closed()
