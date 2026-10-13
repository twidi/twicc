"""Plain bounded recovery observations, finite sweeps, and compact failure backoff."""
from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from twicc.providers.compute_repair_journal import RepairObservation, RepairSnapshot

RECOVERY_PAGE_ROWS = 64
RECOVERY_QUEUE_LIMIT = 16
RECOVERY_POLL_SECONDS = 1.0
RECOVERY_RETRY_BASE_SECONDS = 1.0
RECOVERY_RETRY_MAX_SECONDS = 60.0


class RetryRecord(NamedTuple):
    journal_id: int
    generation: int
    failure_count: int
    retry_deadline: float


class RepairRecoveryScheduler:
    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._queued: dict[int, RepairObservation] = {}
        self._retries: dict[int, RetryRecord] = {}
        self._closed = False
        self._upper_id: int | None = None
        self._cursor = 0
        self._read_limit = 0
        self._next_sweep = 0.0

    @property
    def sweep_ready(self) -> bool:
        return not self._closed and self._upper_id is None and self._clock() >= self._next_sweep

    def begin_sweep(self, upper_id: int) -> None:
        if not self.sweep_ready:
            return
        # The captured maximum also proves higher failed IDs disappeared.
        for journal_id in tuple(self._retries):
            if journal_id > upper_id:
                del self._retries[journal_id]
        self._upper_id = upper_id
        self._cursor = 0

    def page_request(self) -> tuple[int, int, int] | None:
        if self._closed or self._upper_id is None or len(self._queued) >= RECOVERY_QUEUE_LIMIT:
            return None
        self._read_limit = min(RECOVERY_PAGE_ROWS, RECOVERY_QUEUE_LIMIT - len(self._queued))
        return self._cursor, self._upper_id, self._read_limit

    def observe_page(self, rows: tuple[RepairSnapshot, ...], *, excluded_session_ids: frozenset[str]) -> None:
        if self._closed or self._upper_id is None:
            return
        previous_cursor = self._cursor
        short = len(rows) < self._read_limit
        self._cursor = rows[-1].observation.journal_id if rows else self._cursor
        present = {row.observation.journal_id for row in rows}
        confirmed_end = self._upper_id if short else self._cursor
        # A completed read proves absence only inside its original ID range.
        for journal_id in tuple(self._retries):
            if previous_cursor < journal_id <= confirmed_end and journal_id not in present:
                del self._retries[journal_id]
        for row in rows:
            observation = row.observation
            retry = self._retries.get(observation.journal_id)
            if retry is not None and retry.generation != observation.generation:
                del self._retries[observation.journal_id]
                retry = None
            if observation.session_id in excluded_session_ids:
                self.defer_unadmitted(observation)
                continue
            if retry is not None and retry.retry_deadline > self._clock():
                continue
            previous = self._queued.get(observation.journal_id)
            if previous is None or previous.generation <= observation.generation:
                self._queued[observation.journal_id] = observation
        if short or self._cursor >= self._upper_id:
            self._upper_id = None
            self._next_sweep = self._clock() + RECOVERY_POLL_SECONDS

    def next_candidate(self, excluded_session_ids: frozenset[str]) -> RepairObservation | None:
        if self._closed:
            return None
        for observation in tuple(self._queued.values()):
            if observation.session_id in excluded_session_ids:
                self.defer_unadmitted(observation)
        return next(iter(self._queued.values()), None)

    def defer_unadmitted(self, observation: RepairObservation) -> None:
        if self._queued.get(observation.journal_id) == observation:
            del self._queued[observation.journal_id]

    def mark_admitted(self, observation: RepairObservation) -> None:
        self.defer_unadmitted(observation)

    def settle_attempt(self, observation: RepairObservation, outcome: str, *, failed: bool = False) -> None:
        if self._closed:
            return
        if not failed:
            self._retries.pop(observation.journal_id, None)
            return
        previous = self._retries.get(observation.journal_id)
        count = (previous.failure_count if previous and previous.generation == observation.generation else 0) + 1
        delay = min(RECOVERY_RETRY_MAX_SECONDS, RECOVERY_RETRY_BASE_SECONDS * 2 ** min(count - 1, 6))
        self._retries[observation.journal_id] = RetryRecord(
            observation.journal_id, observation.generation, count, self._clock() + delay,
        )

    def close(self) -> None:
        self._closed = True
        self._queued.clear()
        self._retries.clear()
        self._upper_id = None
