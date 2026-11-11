"""Git process delivery and cancellation through Django's real ASGI handler."""

import asyncio
import io
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.handlers.asgi import ASGIHandler

from tests.test_file_download_streaming import AsgiCall, stop, wait_event
from twicc import git, views
from twicc.http_streams import DownloadCleanupMiddleware

pytestmark = pytest.mark.django_db(transaction=True)


class ProcessReader(io.BufferedReader):
    def __init__(self, raw):
        super().__init__(raw)
        self.requests = []
        self.threads = []
        self.started = threading.Event()
        self.closed_event = threading.Event()
        self.fail = False

    def read(self, size=-1):
        self.requests.append(size)
        self.threads.append(threading.get_ident())
        self.started.set()
        if self.fail:
            raise OSError("Pipe read failed")
        return super().read(size)

    def close(self):
        super().close()
        self.closed_event.set()


@pytest.fixture
def app(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return DownloadCleanupMiddleware(ASGIHandler())


@pytest.fixture
def producer(monkeypatch):
    processes = []

    def spawn(code):
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, bufsize=0)
        proc.stdout = ProcessReader(proc.stdout)
        stream = git.GitProcessStream(proc)
        processes.append((proc, stream))
        return stream

    async def resolve(*args, **kwargs):
        return "/repo", "file.txt", "abcd", None

    monkeypatch.setattr(views, "_resolve_git_download", resolve)
    monkeypatch.setattr(git, "blob_exists_at_ref", lambda *args: True)
    yield spawn
    for proc, _ in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)
        proc.stdout.close()


def install(monkeypatch, stream, endpoint):
    helper = "stream_blob_at_ref" if endpoint == "git-file-download" else "stream_commit_patch"
    monkeypatch.setattr(git, helper, lambda *args: stream)
    return f"/api/projects/project/{endpoint}/?path=file.txt&ref=abcd"


def assert_reaped(stream):
    assert stream._proc.returncode is not None
    assert stream._proc.stdout.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(stream._proc.pid, os.WNOHANG)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["git-file-download", "git-diff-download"])
async def test_large_output_streams_before_process_exit_and_respects_backpressure(app, producer, monkeypatch, endpoint):
    stream = producer("import os; os.write(1, b'x' * 1048576)")
    url = install(monkeypatch, stream, endpoint)

    async def pause(call, message):
        if message.get("body"):
            await call.send_release.wait()

    call = AsgiCall(app, url, send_hook=pause)
    try:
        await asyncio.wait_for(call.first_body.wait(), 2)
        assert call.body == b"x" * 65536
        assert stream._proc.stdout.requests == [65536]
        assert stream._proc.poll() is None
        assert b"content-length" not in call.headers
        assert b"content-encoding" not in call.headers
        call.send_release.set()
        await asyncio.wait_for(call.task, 3)
        assert call.body == b"x" * 1048576
        assert threading.get_ident() not in stream._proc.stdout.threads
        assert_reaped(stream)
    finally:
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [b"", b"partial"])
async def test_nonzero_exit_aborts_before_successful_body_termination(app, producer, monkeypatch, output):
    stream = producer(f"import os; os.write(1, {output!r}); raise SystemExit(7)")
    url = install(monkeypatch, stream, "git-diff-download")
    call = AsgiCall(app, url)
    with pytest.raises(git.GitError):
        await asyncio.wait_for(call.task, 3)
    assert call.body == output
    assert not any(message.get("more_body") is False for message in call.sent)
    assert_reaped(stream)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["git-file-download", "git-diff-download"])
async def test_head_closes_process_without_reading_output(app, producer, monkeypatch, endpoint):
    stream = producer("import time; time.sleep(60)")
    call = AsgiCall(app, install(monkeypatch, stream, endpoint), method="HEAD")
    await asyncio.wait_for(call.task, 3)
    assert call.body == b""
    assert not stream._proc.stdout.requests
    assert b"content-length" not in call.headers
    assert_reaped(stream)


@pytest.mark.asyncio
async def test_disconnect_unblocks_pending_stdout_read(app, producer, monkeypatch):
    stream = producer("import time; time.sleep(60)")
    call = AsgiCall(app, install(monkeypatch, stream, "git-file-download"))
    try:
        await wait_event(stream._proc.stdout.started)
        progressed = asyncio.Event()
        asyncio.get_running_loop().call_soon(progressed.set)
        await asyncio.wait_for(progressed.wait(), 1)
        call.disconnect()
        await asyncio.wait_for(call.task, 3)
        await wait_event(stream._proc.stdout.closed_event)
        assert not call.body
        assert_reaped(stream)
    finally:
        if stream._proc.poll() is None:
            stream._proc.kill()
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("failure", ["disconnect", "send_error"])
async def test_interrupted_send_reaps_process(app, producer, monkeypatch, phase, failure):
    stream = producer("import os; os.write(1, b'x' * 1048576)")
    sending = asyncio.Event()

    async def interrupt(call, message):
        target = message["type"] == "http.response.start" if phase == "headers" else bool(message.get("body"))
        if target:
            sending.set()
            if failure == "send_error":
                raise OSError("Send failed")
            await asyncio.Future()

    call = AsgiCall(app, install(monkeypatch, stream, "git-diff-download"), send_hook=interrupt)
    try:
        await asyncio.wait_for(sending.wait(), 2)
        if failure == "disconnect":
            call.disconnect()
            await asyncio.wait_for(call.task, 3)
        else:
            with pytest.raises(OSError, match="Send failed"):
                await asyncio.wait_for(call.task, 3)
        await wait_event(stream._proc.stdout.closed_event)
        assert stream._proc.stdout.requests == ([] if phase == "headers" else [65536])
        assert_reaped(stream)
    finally:
        await stop(call)


@pytest.mark.asyncio
async def test_read_error_stops_process_and_aborts_transfer(app, producer, monkeypatch):
    stream = producer("import time; time.sleep(60)")
    stream._proc.stdout.fail = True
    call = AsgiCall(app, install(monkeypatch, stream, "git-file-download"))
    with pytest.raises(OSError, match="Pipe read failed"):
        await asyncio.wait_for(call.task, 3)
    assert_reaped(stream)
    assert not any(message.get("more_body") is False for message in call.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["git-file-download", "git-diff-download"])
async def test_cancelled_acquisition_releases_late_process(app, producer, monkeypatch, endpoint):
    stream = producer("import time; time.sleep(60)")
    url = install(monkeypatch, stream, endpoint)
    started, release = threading.Event(), threading.Event()

    def acquire(*args):
        started.set()
        assert release.wait(3)
        return stream

    helper = "stream_blob_at_ref" if endpoint == "git-file-download" else "stream_commit_patch"
    monkeypatch.setattr(git, helper, acquire)
    call = AsgiCall(app, url)
    try:
        await wait_event(started)
        call.disconnect()
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(call.task, 3)
        await wait_event(stream._proc.stdout.closed_event)
        assert not call.sent
        assert_reaped(stream)
    finally:
        release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_response_construction_failure_releases_process(app, producer, monkeypatch):
    stream = producer("import time; time.sleep(60)")
    url = install(monkeypatch, stream, "git-file-download")

    def fail(*args, **kwargs):
        raise RuntimeError("Response construction failed")

    monkeypatch.setattr(views, "AsyncFileResponse", fail)
    call = AsgiCall(app, url)
    await asyncio.wait_for(call.task, 3)
    assert call.sent[0]["status"] == 500
    assert_reaped(stream)


@pytest.mark.asyncio
async def test_cancelled_completion_validation_terminates_without_wait_lock_deadlock(app, producer, monkeypatch):
    stream = producer("import os, time; os.close(1); time.sleep(60)")
    url = install(monkeypatch, stream, "git-diff-download")
    started = threading.Event()
    original = getattr(stream, "finish", lambda: stream._proc.wait())

    def finish():
        started.set()
        original()

    stream.finish = finish
    call = AsgiCall(app, url)
    try:
        await wait_event(started)
        call.disconnect()
        await asyncio.wait_for(call.task, 3)
        await wait_event(stream._proc.stdout.closed_event)
        assert_reaped(stream)
    finally:
        if stream._proc.poll() is None:
            stream._proc.kill()
        await stop(call)


@pytest.mark.asyncio
async def test_cleanup_does_not_wait_for_saturated_default_executor(app, producer, monkeypatch):
    stream = producer("import time; time.sleep(60)")
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    call = AsgiCall(app, install(monkeypatch, stream, "git-file-download"))
    try:
        # Poll from the event loop: the only worker is blocked on stdout.
        for _ in range(400):
            if stream._proc.stdout.started.is_set():
                break
            await asyncio.sleep(0.005)
        assert stream._proc.stdout.started.is_set()
        call.disconnect()
        await asyncio.wait_for(call.task, 3)
        assert_reaped(stream)
    finally:
        if stream._proc.poll() is None:
            stream._proc.kill()
        await stop(call)
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_close_kills_process_which_ignores_terminate_and_is_repeatable(producer):
    stream = producer("import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); os.write(1, b'R'); time.sleep(60)")
    assert await asyncio.to_thread(stream.read, 1) == b"R"
    await asyncio.wait_for(asyncio.to_thread(stream.close), 3)
    await asyncio.to_thread(stream.close)
    assert stream._proc.returncode == -signal.SIGKILL
    assert_reaped(stream)
