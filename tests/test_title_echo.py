"""Automatic title echoes expire and are consumed once."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event, Lock, current_thread
from types import SimpleNamespace

import pytest


@pytest.fixture
def echoes(monkeypatch):
    module = import_module("twicc.title_echo")
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(module, "_automatic_title_echoes", {})
    return module, clock


def test_no_record_does_not_skip_provider_title(echoes):
    module, _ = echoes
    assert not module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")


def test_unmatched_title_keeps_record_until_matching_echo_is_consumed(echoes):
    module, _ = echoes
    module.record_automatic_title_push("s", "A")
    assert not module.should_skip_automatic_title_echo("s", "U", title="U", title_origin="user")
    assert module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")
    assert not module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")


@pytest.mark.parametrize("title, origin", [("A", "auto"), ("Other", "auto"), ("A", "user"), ("Other", "")])
def test_matching_echo_is_consumed_even_when_it_is_applied(echoes, title, origin):
    module, _ = echoes
    module.record_automatic_title_push("s", "A")
    assert not module.should_skip_automatic_title_echo("s", "A", title=title, title_origin=origin)
    assert not module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")


def test_matching_echo_does_not_overwrite_null_user_title(echoes):
    module, _ = echoes
    module.record_automatic_title_push("s", "A")
    assert module.should_skip_automatic_title_echo("s", "A", title=None, title_origin="user")


def test_latest_push_replaces_record_and_restarts_expiry(echoes):
    module, clock = echoes
    module.record_automatic_title_push("s", "A")
    clock.now = 1599.0
    module.record_automatic_title_push("s", "B")
    clock.now = 1600.0
    assert not module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")
    assert module.should_skip_automatic_title_echo("s", "B", title="U", title_origin="user")


@pytest.mark.parametrize("elapsed, skip", [(599.999, True), (600.0, False), (601.0, False)])
def test_echo_expires_at_ten_minutes(echoes, elapsed, skip):
    module, clock = echoes
    module.record_automatic_title_push("s", "A")
    clock.now += elapsed
    assert module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user") is skip


def test_batched_unmatched_title_allows_later_legitimate_rename_after_expiry(echoes):
    module, clock = echoes
    module.record_automatic_title_push("s", "A")
    # A batch [A, U] exposes only its final title to the apply hook.
    assert not module.should_skip_automatic_title_echo("s", "U", title="U", title_origin="user")
    clock.now += 600.0
    assert not module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")


@pytest.mark.parametrize("operation", ["record", "consult"])
def test_any_api_call_prunes_expired_inactive_sessions(echoes, operation):
    module, clock = echoes
    module.record_automatic_title_push("inactive", "A")
    clock.now += 300.0
    module.record_automatic_title_push("active", "B")
    clock.now += 300.0
    if operation == "record":
        module.record_automatic_title_push("new", "C")
    else:
        assert not module.should_skip_automatic_title_echo("missing", "C", title="U", title_origin="user")
    # Memory cleanup must remove other sessions, not only the consulted session.
    assert "inactive" not in module._automatic_title_echoes
    assert module.should_skip_automatic_title_echo("active", "B", title="U", title_origin="user")


def test_pending_title_write_and_flush_leave_automatic_echo_record(echoes, monkeypatch):
    from twicc import pending_titles

    module, _ = echoes
    monkeypatch.setattr(pending_titles, "_pending", {})
    module.record_automatic_title_push("s", "A")
    pending_titles.set_pending_title("s", "U")
    assert pending_titles.pop_pending_title("s") == "U"
    assert module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")


def test_matching_consumer_cannot_remove_concurrent_newer_record(echoes, monkeypatch):
    module, _ = echoes
    consumer_read = Event()
    replacement_attempted = Event()
    writer_thread = []
    consumer_thread = []
    lock = Lock()

    class CoordinatedLock:
        def __enter__(self):
            if writer_thread and current_thread() is writer_thread[0]:
                replacement_attempted.set()
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    class CoordinatedRecords(dict):
        def get(self, key, default=None):
            record = super().get(key, default)
            if consumer_thread and current_thread() is consumer_thread[0]:
                consumer_read.set()
                assert replacement_attempted.wait(timeout=5)
            return record

        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            # Without locking, replacement completes while the old lookup pauses.
            if writer_thread and current_thread() is writer_thread[0]:
                replacement_attempted.set()

    monkeypatch.setattr(module, "_automatic_title_echoes", CoordinatedRecords())
    monkeypatch.setattr(module, "_echo_lock", CoordinatedLock(), raising=False)
    module.record_automatic_title_push("s", "A")

    def consume_old():
        consumer_thread.append(current_thread())
        return module.should_skip_automatic_title_echo("s", "A", title="U", title_origin="user")

    def replace():
        writer_thread.append(current_thread())
        module.record_automatic_title_push("s", "B")

    with ThreadPoolExecutor(max_workers=2) as executor:
        consume = executor.submit(consume_old)
        assert consumer_read.wait(timeout=5)
        replacement = executor.submit(replace)
        assert consume.result(timeout=5)
        replacement.result(timeout=5)
    assert module.should_skip_automatic_title_echo("s", "B", title="U", title_origin="user")
