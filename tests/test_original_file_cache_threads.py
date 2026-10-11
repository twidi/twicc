"""Live compute and agent hooks access the original-file caches on different threads."""

from __future__ import annotations

import threading
import time

import pytest

from twicc.providers.enrichment_cache import EnrichmentCache
from twicc.providers.claude_code.agent import original_file_cache
from twicc.providers.codex.agent import original_files_cache


class _PausedCache(dict):
    def __init__(self, data, paused: threading.Event, resume: threading.Event):
        super().__init__(data)
        self.paused = paused
        self.resume = resume

    def _pause(self, iterator):
        yield next(iterator)
        self.paused.set()
        assert self.resume.wait(3)
        yield from iterator

    def items(self):
        return self._pause(iter(super().items()))

    def __iter__(self):
        return self._pause(super().__iter__())


class _ObservedLock:
    """A plain lock that reports when a thread has to wait for it."""

    def __init__(self, contended: threading.Event):
        self._lock = threading.Lock()
        self.contended = contended

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(self, *exc_info):
        self._lock.release()


@pytest.mark.parametrize("module,operation", [
    (original_file_cache, "cleanup"),
    (original_files_cache, "cleanup"),
    (original_files_cache, "clear_session"),
])
def test_cache_compound_operation_is_safe_during_concurrent_capture(monkeypatch, module, operation):
    paused = threading.Event()
    resume = threading.Event()
    inserted = threading.Event()
    shared = EnrichmentCache()
    value = "old content" if module is original_file_cache else {"/old": "content"}
    shared.put(("old", "a"), value)
    shared.put(("old", "b"), value)
    cache = _PausedCache(shared._entries, paused, resume)
    monkeypatch.setattr(shared, "_entries", cache)
    contended = threading.Event()
    monkeypatch.setattr(shared, "_lock", _ObservedLock(contended))
    monkeypatch.setattr(module, "_cache", shared)
    errors = []

    def run_compound():
        try:
            if operation == "cleanup":
                module.cleanup_expired()
            else:
                module.clear_session("old")
        except Exception as exc:
            errors.append(exc)

    def capture():
        if module is original_file_cache:
            module.cache_original_file("new", "c", "new content")
        else:
            module.cache_original_files("new", "c", {"/new": "content"})
        inserted.set()

    cleaner = threading.Thread(target=run_compound)
    writer = threading.Thread(target=capture)
    cleaner.start()
    assert paused.wait(2)
    writer.start()
    # Resume once the writer either waits on the lock (the cleaner holds it)
    # or already inserted mid-iteration (an unlocked side: the resumed
    # iteration then raises). Either is final, so no fixed wait is needed.
    deadline = time.monotonic() + 2
    while not (contended.is_set() or inserted.is_set()) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert contended.is_set() or inserted.is_set()
    resume.set()
    cleaner.join(3)
    writer.join(3)

    assert not cleaner.is_alive() and not writer.is_alive()
    assert errors == []
    assert ("new", "c") in cache
