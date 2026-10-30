"""Tests for the in-memory audio streams (twicc.tts.streams).

Every test runs its whole scenario inside one ``asyncio.run(...)``: streams,
futures and ``call_later`` timers belong to the loop that created them.

A fake pump drives the stream's sink from an ``asyncio.Queue``.
"""

import asyncio
import logging
import re

import pytest

from twicc.tts import streams
from twicc.tts.audio_cache import speech_cache
from twicc.tts.openrouter import SpeechError
from twicc.tts.streams import ending_of, get_stream, release_stream, start_stream, stream_from_bytes

LOGGER_NAME = "twicc.tts.streams"
LABEL = {"model": "openai/gpt-4o-mini-tts", "voice": "Kore", "format": "mp3", "chars": 11}
HINTED_400 = "Provider returned 400. Check the model, the voice, the format and the text length."


@pytest.fixture(autouse=True)
def _reset_streams_and_cache():
    streams._reset_for_tests()
    speech_cache.clear()
    yield
    streams._reset_for_tests()
    speech_cache.clear()


@pytest.fixture
def streams_log(caplog, monkeypatch):
    """Route the streams logger straight to caplog, at INFO.

    settings_test's ``disable_existing_loggers: True`` can disable this logger,
    depending on the suite's import order.
    """
    logger = logging.getLogger(LOGGER_NAME)
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return caplog


def _speech_lines(caplog) -> list[str]:
    """The end-of-speech log lines (INFO), in order."""
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.INFO]


def _errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.ERROR]


class FakePump:
    """A pump that runs the queued items in order.

    An item is a sink call ``(name, *args)`` (it returns after ``finish`` or
    ``fail``), ``("sleep", seconds)``, ``("raise", error)`` or ``("return",)``.
    """

    def __init__(self):
        self.queue = asyncio.Queue()
        self.cancelled = False

    def send(self, *item) -> None:
        self.queue.put_nowait(item)

    async def __call__(self, stream) -> None:
        try:
            while True:
                name, *args = await self.queue.get()
                if name == "sleep":
                    await asyncio.sleep(args[0])
                elif name == "raise":
                    raise args[0]
                elif name == "return":
                    return
                else:
                    getattr(stream, name)(*args)
                    if name in ("finish", "fail"):
                        return
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def _settle() -> None:
    """Let the ready tasks and callbacks run."""
    for _ in range(10):
        await asyncio.sleep(0)


async def _within(awaitable, seconds: float = 1.0):
    """Await *awaitable*, failing the test instead of hanging."""
    async with asyncio.timeout(seconds):
        return await awaitable


async def _await(future):
    """A coroutine that awaits *future*, for a waiter task (as the open view is)."""
    return await future


def _on_end_recorder():
    calls = []

    async def on_end(stream):
        calls.append((stream.id, stream.state))

    return calls, on_end


def test_ready_resolves_on_headers_and_fails_before_them():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        assert stream.state == "opening"
        assert stream.label == LABEL
        assert get_stream(stream.id) is stream
        await _settle()
        assert not stream.ready.done()

        pump.send("set_headers", "audio/mpeg", "gen-1")
        assert await _within(stream.ready) is None
        assert (stream.state, stream.content_type, stream.generation_id) == ("running", "audio/mpeg", "gen-1")
        pump.send("finish")

        failing = FakePump()
        failed = start_stream(failing, label=LABEL)
        failing.send("fail", SpeechError(HINTED_400, upstream_status=400))
        with pytest.raises(SpeechError) as exc_info:
            await _within(failed.ready)
        assert exc_info.value.message == HINTED_400
        assert (failed.state, failed.error, failed.upstream_status) == ("failed", HINTED_400, 400)
        assert failed.content_type is None
        await _settle()

    asyncio.run(scenario())


def test_append_wakes_waiting_readers():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)

        readers = [asyncio.create_task(stream.wait_for_more(0)) for _ in range(2)]
        await _settle()
        assert not any(reader.done() for reader in readers)

        pump.send("append", b"")  # an empty chunk is no new data
        await _settle()
        assert not any(reader.done() for reader in readers)

        pump.send("append", b"abc")
        await _within(asyncio.gather(*readers))
        assert bytes(stream.data) == b"abc"

        # Bytes past the offset are there already: no wait.
        await _within(stream.wait_for_more(2))
        later = asyncio.create_task(stream.wait_for_more(3))
        await _settle()
        assert not later.done()
        pump.send("append", b"d")
        await _within(later)
        assert bytes(stream.data) == b"abcd"

        # The end wakes a reader with nothing more to read.
        last = asyncio.create_task(stream.wait_for_more(4))
        await _settle()
        assert not last.done()
        pump.send("finish")
        await _within(last)
        assert stream.state == "complete"

    asyncio.run(scenario())


def test_first_audio_and_generation_times_are_measured():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        pump.send("sleep", 0.05)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"")
        await _within(stream.ready)
        await _settle()
        assert stream.first_audio_ms is None  # an empty chunk is not audio

        pump.send("append", b"a")
        await _within(stream.wait_for_more(0))
        first_audio_ms = stream.first_audio_ms
        assert first_audio_ms >= 50
        assert stream.generation_ms is None

        pump.send("sleep", 0.05)
        pump.send("append", b"b")
        pump.send("finish")
        await _within(stream.wait_for_more(2))
        assert stream.state == "complete"
        assert stream.first_audio_ms == first_audio_ms  # set on the first audio only
        assert stream.generation_ms >= first_audio_ms + 50

    asyncio.run(scenario())


def test_a_finished_stream_lives_until_released(monkeypatch):
    # A playback lasts far longer than its download: the id must outlive the pump.
    monkeypatch.setattr(streams, "UNCLAIMED_TTL", 0.05)

    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        stream.claimed = True
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"audio")
        pump.send("finish")
        await _within(stream.ready)
        await _settle()
        assert stream.state == "complete"

        await asyncio.sleep(0.2)
        assert get_stream(stream.id) is stream
        assert bytes(stream.data) == b"audio"
        assert ending_of(stream.id) == {"state": "complete", "error": None}

        release_stream(stream.id)
        assert get_stream(stream.id) is None
        assert ending_of(stream.id) == {"state": "unknown", "error": None}

    asyncio.run(scenario())


def test_release_cancels_a_running_pump():
    async def scenario():
        calls, on_end = _on_end_recorder()

        # Before the headers: the open view's pending ready fails.
        pump = FakePump()
        stream = start_stream(pump, label=LABEL, on_end=on_end)
        waiter = asyncio.create_task(_await(stream.ready))
        await _settle()
        release_stream(stream.id)
        assert stream.state == "released"
        with pytest.raises(SpeechError) as exc_info:
            await _within(waiter)
        assert exc_info.value.message == "Text-to-speech stopped"
        await _settle()
        assert pump.cancelled
        assert stream._task.cancelled()
        assert ending_of(stream.id) == {"state": "unknown", "error": None}

        # While the audio streams.
        pump = FakePump()
        running = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"abc")
        await _within(running.wait_for_more(0))
        release_stream(running.id)
        await _settle()
        assert pump.cancelled
        assert running.state == "released"
        assert get_stream(running.id) is None

        assert calls == []

    asyncio.run(scenario())


def test_release_wakes_a_waiting_reader():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)

        reader = asyncio.create_task(stream.wait_for_more(0))
        await _settle()
        assert not reader.done()
        release_stream(stream.id)
        await _within(reader)
        assert stream.state == "released"

        # A reader that comes after the release does not wait either.
        await _within(stream.wait_for_more(0))

    asyncio.run(scenario())


def test_release_after_complete_lets_on_end_finish():
    async def scenario():
        ended = []

        async def slow_on_end(stream):
            await asyncio.sleep(0.05)
            ended.append(stream.state)

        pump = FakePump()
        stream = start_stream(pump, label=LABEL, on_end=slow_on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"audio")
        pump.send("finish")
        await _within(stream.ready)
        await _settle()  # the pump returned, on_end sleeps
        assert stream.state == "complete"

        release_stream(stream.id)
        assert get_stream(stream.id) is None
        assert stream.state == "complete"  # the end state is kept for on_end
        await _within(stream._task)
        assert not stream._task.cancelled()
        assert not pump.cancelled
        assert ended == ["complete"]

        release_stream(stream.id)  # idempotent

    asyncio.run(scenario())


def test_unclaimed_timer_starts_when_ready_resolves(monkeypatch):
    # The wait for the headers can last up to the upstream timeout: it never counts.
    monkeypatch.setattr(streams, "UNCLAIMED_TTL", 0.05)

    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        pump.send("sleep", 0.2)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)
        assert get_stream(stream.id) is stream
        assert stream.state == "running"

        await asyncio.sleep(0.2)
        assert get_stream(stream.id) is None  # it expires from the headers on

    asyncio.run(scenario())


def test_unclaimed_stream_expires_and_cancels_its_pump(monkeypatch):
    monkeypatch.setattr(streams, "UNCLAIMED_TTL", 0.05)

    async def scenario():
        calls, on_end = _on_end_recorder()
        pump = FakePump()
        stream = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)

        claimed_pump = FakePump()
        claimed = start_stream(claimed_pump, label=LABEL)
        claimed_pump.send("set_headers", "audio/mpeg", None)
        await _within(claimed.ready)
        claimed.claimed = True

        cached = stream_from_bytes(b"audio", "audio/mpeg", label=LABEL)

        await asyncio.sleep(0.2)
        assert pump.cancelled
        assert stream.state == "released"
        assert get_stream(stream.id) is None
        assert get_stream(cached.id) is None  # a cache hit has the same timer
        assert calls == []

        assert get_stream(claimed.id) is claimed
        assert not claimed_pump.cancelled
        claimed_pump.send("finish")
        await _settle()

    asyncio.run(scenario())


def test_safety_expiry_counts_from_the_last_touch(monkeypatch):
    # Wide margins: the touches must land well before each deadline, even on a busy machine.
    monkeypatch.setattr(streams, "SAFETY_TTL", 0.2)

    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        stream.claimed = True
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)

        for _ in range(4):  # 0.4 s in all: twice the TTL
            await asyncio.sleep(0.1)
            assert get_stream(stream.id) is stream
            stream.touch()

        await asyncio.sleep(0.35)
        assert get_stream(stream.id) is None
        assert stream.state == "released"
        assert pump.cancelled

        cached = stream_from_bytes(b"audio", "audio/mpeg", label=LABEL)
        cached.claimed = True
        await asyncio.sleep(0.35)
        assert get_stream(cached.id) is None  # a cache hit has the same timer

    asyncio.run(scenario())


def test_unexpected_pump_error_fails_ready_and_logs(streams_log):
    async def scenario():
        calls, on_end = _on_end_recorder()

        # An exception before the headers reaches the pending ready.
        boom = RuntimeError("boom")
        pump = FakePump()
        raised = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("raise", boom)
        with pytest.raises(RuntimeError) as exc_info:
            await _within(raised.ready)
        assert exc_info.value is boom
        await _within(raised._task)
        assert (raised.state, raised.error) == ("failed", "Text-to-speech failed")

        # A pump that returns without finish() or fail(), after its headers.
        pump = FakePump()
        returned = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"abc")
        pump.send("return")
        await _within(returned.ready)
        await _within(returned._task)
        assert (returned.state, returned.error) == ("failed", "Text-to-speech failed")

        # The same before its headers: the pending ready gets the exception.
        pump = FakePump()
        early = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("return")
        with pytest.raises(RuntimeError):
            await _within(early.ready)
        await _within(early._task)
        assert (early.state, early.error) == ("failed", "Text-to-speech failed")

        # A failed stream still runs on_end, like any failed stream.
        assert calls == [(raised.id, "failed"), (returned.id, "failed"), (early.id, "failed")]

    asyncio.run(scenario())

    errors = _errors(streams_log)
    assert len(errors) == 3
    assert all(record.exc_info for record in errors)
    assert errors[0].exc_info[1].args == ("boom",)
    lines = _speech_lines(streams_log)
    assert len(lines) == 3
    assert all(line.endswith("outcome=failed") for line in lines)
    assert "bytes=3" in lines[1]


def test_a_cancelled_pump_task_settles_ready():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        waiter = asyncio.create_task(_await(stream.ready))
        reader = asyncio.create_task(stream.wait_for_more(0))
        await _settle()

        stream._task.cancel()  # not through release_stream (a server shutdown, say)
        with pytest.raises(SpeechError) as exc_info:
            await _within(waiter)
        assert exc_info.value.message == "Text-to-speech stopped"
        await _within(reader)
        assert pump.cancelled
        assert stream._task.cancelled()
        assert ending_of(stream.id) == {"state": "failed", "error": "Text-to-speech stopped"}

    asyncio.run(scenario())


def test_a_task_cancelled_before_its_first_step_ends_the_stream(streams_log):
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        # Cancelled before the task ran at all: ``_run`` never starts.
        stream._task.cancel()
        reader = asyncio.create_task(stream.wait_for_more(0))
        with pytest.raises(SpeechError) as exc_info:
            await _within(stream.ready)
        assert exc_info.value.message == "Text-to-speech stopped"
        await _within(reader)
        assert stream._task.cancelled()
        assert not pump.cancelled  # the pump never started
        assert ending_of(stream.id) == {"state": "failed", "error": "Text-to-speech stopped"}

    asyncio.run(scenario())
    [line] = _speech_lines(streams_log)
    assert line.endswith("error='Text-to-speech stopped' outcome=failed")


def test_a_settle_after_a_cancelled_waiter_does_not_raise():
    # A cancelled open view cancels the ready future it awaits.
    async def cancel_a_waiter(stream):
        waiter = asyncio.create_task(_await(stream.ready))
        await _settle()
        waiter.cancel()
        await _settle()
        assert stream.ready.cancelled()

    async def scenario():
        pump = FakePump()
        headers = start_stream(pump, label=LABEL)
        await cancel_a_waiter(headers)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("finish")
        await _within(headers._task)
        assert headers.state == "complete"
        assert headers._task.exception() is None

        pump = FakePump()
        failed = start_stream(pump, label=LABEL)
        await cancel_a_waiter(failed)
        pump.send("fail", SpeechError("Could not reach OpenRouter"))
        await _within(failed._task)
        assert failed.state == "failed"
        assert failed._task.exception() is None

        pump = FakePump()
        released = start_stream(pump, label=LABEL)
        await cancel_a_waiter(released)
        release_stream(released.id, outcome="cancelled")
        await _settle()
        assert released.state == "released"
        assert pump.cancelled

    asyncio.run(scenario())


def test_on_end_runs_for_complete_and_failed_only():
    async def scenario():
        calls, on_end = _on_end_recorder()

        pump = FakePump()
        complete = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("append", b"audio")
        pump.send("finish")
        await _within(complete._task)

        pump = FakePump()
        failed = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("fail", SpeechError("OpenRouter did not answer in time"))
        await _within(failed._task)

        pump = FakePump()
        released = start_stream(pump, label=LABEL, on_end=on_end)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(released.ready)
        release_stream(released.id)
        await _settle()

        pump = FakePump()
        cancelled = start_stream(pump, label=LABEL, on_end=on_end)
        await _settle()
        cancelled._task.cancel()
        await _settle()

        assert calls == [(complete.id, "complete"), (failed.id, "failed")]

        # Without on_end, a stream ends the same.
        pump = FakePump()
        plain = start_stream(pump, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("finish")
        await _within(plain._task)
        assert plain.state == "complete"

    asyncio.run(scenario())


def test_on_end_error_is_logged_not_raised(streams_log):
    async def scenario():
        async def broken_on_end(stream):
            raise RuntimeError("on_end broke")

        pump = FakePump()
        stream = start_stream(pump, label=LABEL, on_end=broken_on_end)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("finish")
        await _within(stream._task)
        assert stream._task.exception() is None
        assert stream.state == "complete"

    asyncio.run(scenario())

    errors = _errors(streams_log)
    assert len(errors) == 1
    assert errors[0].exc_info[1].args == ("on_end broke",)


def test_ending_of_maps_states():
    async def scenario():
        pump = FakePump()
        stream = start_stream(pump, label=LABEL)
        assert ending_of(stream.id) == {"state": "running", "error": None}  # opening
        pump.send("set_headers", "audio/mpeg", None)
        await _within(stream.ready)
        assert ending_of(stream.id) == {"state": "running", "error": None}
        pump.send("finish")
        await _within(stream._task)
        assert ending_of(stream.id) == {"state": "complete", "error": None}

        pump = FakePump()
        failed = start_stream(pump, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("fail", SpeechError("Could not reach OpenRouter"))
        await _within(failed._task)
        assert ending_of(failed.id) == {"state": "failed", "error": "Could not reach OpenRouter"}

        cached = stream_from_bytes(b"audio", "audio/mpeg", label=LABEL)
        assert ending_of(cached.id) == {"state": "complete", "error": None}

        assert ending_of("no-such-id") == {"state": "unknown", "error": None}

    asyncio.run(scenario())


def test_stream_from_bytes_is_complete_and_ready():
    async def scenario():
        stream = stream_from_bytes(b"ID3audio", "audio/mpeg", label=LABEL)
        assert get_stream(stream.id) is stream
        assert await _within(stream.ready) is None
        assert (stream.state, stream.content_type, bytes(stream.data)) == ("complete", "audio/mpeg", b"ID3audio")
        assert stream.generation_id is None
        await _within(stream.wait_for_more(len(stream.data)))  # nothing more will come
        other = stream_from_bytes(b"ID3audio", "audio/mpeg", label=LABEL)
        assert other.id != stream.id
        assert len(stream.id) >= 32  # token_urlsafe(24): unguessable

    asyncio.run(scenario())


def test_one_log_line_per_speech_without_the_key(monkeypatch, streams_log):
    monkeypatch.setattr(streams, "UNCLAIMED_TTL", 0.05)
    monkeypatch.setattr(streams, "SAFETY_TTL", 0.3)
    key = "sk-or-XYZ"

    async def scenario():
        def keyed_pump():
            pump = FakePump()

            async def run(stream):  # the closure holds the key, as the real pump does
                assert key
                await pump(stream)

            return pump, run

        # ok, then a release: one line.
        pump, run = keyed_pump()
        ok = start_stream(run, label=LABEL)
        ok.claimed = True
        pump.send("set_headers", "audio/mpeg", "gen-1")
        pump.send("append", b"12345")
        pump.send("finish")
        await _within(ok._task)
        release_stream(ok.id)
        release_stream(ok.id)

        # A release while the upstream runs, and the outcome the open view passes.
        for outcome in ("released", "cancelled"):
            pump, run = keyed_pump()
            stream = start_stream(run, label=LABEL)
            pump.send("set_headers", "audio/mpeg", "gen-2")
            await _within(stream.ready)
            release_stream(stream.id, *([] if outcome == "released" else [outcome]))
            await _settle()

        # An upstream error, then the open view's release: one line.
        pump, run = keyed_pump()
        error = start_stream(run, label=LABEL)
        pump.send("fail", SpeechError(HINTED_400, upstream_status=400))
        with pytest.raises(SpeechError):
            await _within(error.ready)
        release_stream(error.id)

        # A failure during the stream.
        pump, run = keyed_pump()
        failed = start_stream(run, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        pump.send("fail", SpeechError("Could not reach OpenRouter"))
        await _within(failed._task)

        # The two expiries.
        pump, run = keyed_pump()
        unread = start_stream(run, label=LABEL)
        pump.send("set_headers", "audio/mpeg", None)
        await _within(unread.ready)
        pump, run = keyed_pump()
        unreleased = start_stream(run, label=LABEL)
        unreleased.claimed = True
        pump.send("set_headers", "audio/mpeg", None)
        await _within(unreleased.ready)
        await asyncio.sleep(0.5)
        assert get_stream(unread.id) is None
        assert get_stream(unreleased.id) is None

        # A cache hit, then its release: one line.
        cached = stream_from_bytes(b"ID3audio", "audio/mpeg", label=LABEL)
        release_stream(cached.id)

        # The failed stream's release adds no line.
        release_stream(failed.id)

    asyncio.run(scenario())

    lines = _speech_lines(streams_log)
    outcomes = [line.rsplit("outcome=", 1)[1] for line in lines]
    assert outcomes == [
        "ok",
        "released",
        "cancelled",
        "upstream_error 400",
        "failed",
        "expired_unread",
        "expired_unreleased",
        "cached",
    ]
    for line in lines:
        assert line.startswith("TTS speech model=openai/gpt-4o-mini-tts voice=Kore format=mp3 chars=11 ")
    assert "bytes=5 first_audio_ms=" in lines[0]
    assert "generation=gen-1" in lines[0]
    assert "generation_ms=None" not in lines[0]
    assert "generation=gen-2" in lines[1]
    assert re.search(r"bytes=8 first_audio_ms=None generation_ms=None duration_ms=\d+ generation=None ", lines[7])
    # Every line has its duration, and the user-safe error of a failed speech.
    assert all(re.search(r" duration_ms=\d+ ", line) for line in lines)
    errors = [line.split(" error=", 1)[1].rsplit(" outcome=", 1)[0] for line in lines]
    assert errors == [
        "None",
        "None",
        "None",
        repr(HINTED_400),
        "'Could not reach OpenRouter'",
        "None",
        "None",
        "None",
    ]
    assert key not in streams_log.text
