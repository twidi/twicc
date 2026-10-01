"""Thread-safe ownership of one-shot evidence across live transaction retries.

Captures arrive on the event loop; compute borrows them on the DB worker.
A claimed capture leaves the current slot but stays reserved for its exact
source record. A newer capture can therefore coexist with a failed attempt.
The lock never spans ORM work. Explicit clears invalidate outstanding handles.
"""

import threading
import time
from typing import NamedTuple

ENTRY_TTL = 300


class BorrowedEnrichment[T](NamedTuple):
    key: tuple[str, str]
    source_line: int
    value: T
    token: object


class _Entry[T](NamedTuple):
    value: T
    timestamp: float
    token: object


class _Reservation[T](NamedTuple):
    borrowed: BorrowedEnrichment[T]
    expires_at: float | None  # None pins the active transaction's evidence.


class EnrichmentCache[T]:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry[T]] = {}
        self._reservations: dict[tuple[str, int, str], _Reservation[T]] = {}

    def put(self, key: tuple[str, str], value: T) -> None:
        with self._lock:
            self._entries[key] = _Entry(value, time.monotonic(), object())

    def borrow(self, key: tuple[str, str], *, source_line: int) -> BorrowedEnrichment[T] | None:
        source = (key[0], source_line, key[1])
        with self._lock:
            now = time.monotonic()
            reservation = self._reservations.get(source)
            if reservation is not None:
                if reservation.expires_at is None or now <= reservation.expires_at:
                    self._reservations[source] = reservation._replace(expires_at=None)
                    return reservation.borrowed
                del self._reservations[source]
            entry = self._entries.pop(key, None)
            if entry is None or now - entry.timestamp > ENTRY_TTL:
                return None
            borrowed = BorrowedEnrichment(key, source_line, entry.value, entry.token)
            self._reservations[source] = _Reservation(borrowed, None)
            return borrowed

    def commit(self, borrowed: BorrowedEnrichment[T]) -> None:
        source = (borrowed.key[0], borrowed.source_line, borrowed.key[1])
        with self._lock:
            reservation = self._reservations.get(source)
            if reservation is not None and reservation.borrowed.token is borrowed.token:
                del self._reservations[source]

    def rollback(self, borrowed: BorrowedEnrichment[T]) -> None:
        source = (borrowed.key[0], borrowed.source_line, borrowed.key[1])
        with self._lock:
            reservation = self._reservations.get(source)
            if reservation is not None and reservation.borrowed.token is borrowed.token:
                self._reservations[source] = reservation._replace(expires_at=time.monotonic() + ENTRY_TTL)

    def pop(self, key: tuple[str, str]) -> T | None:
        """Compatibility for nontransactional callers; never steals a reservation."""
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None or time.monotonic() - entry.timestamp > ENTRY_TTL:
                return None
            return entry.value

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            for mapping in (self._entries, self._reservations):
                for key in [key for key in mapping if key[0] == session_id]:
                    del mapping[key]

    def cleanup_expired(self) -> None:
        with self._lock:
            now = time.monotonic()
            for key in [key for key, entry in self._entries.items() if now - entry.timestamp > ENTRY_TTL]:
                del self._entries[key]
            expired = [key for key, reservation in self._reservations.items()
                       if reservation.expires_at is not None and now > reservation.expires_at]
            for key in expired:
                del self._reservations[key]
