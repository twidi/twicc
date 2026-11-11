"""Git process delivery and cancellation through Django's real ASGI handler."""

import asyncio
import io
import os
import shlex
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import psutil
import pytest
from django.core.handlers.asgi import ASGIHandler

from tests.test_file_download_streaming import AsgiCall, stop, wait_event
from twicc import git, views
from twicc.core.models import Project
from twicc.http_streams import DownloadCleanupMiddleware

pytestmark = pytest.mark.django_db(transaction=True)


class ProcessReader(io.BufferedReader):
    def __init__(self, raw):
        super().__init__(raw)
        self.requests = []
        self.threads = []
        self.started = threading.Event()
        self.settled = threading.Event()
        self.closed_event = threading.Event()
        self.fail = False

    def read(self, size=-1):
        self.requests.append(size)
        self.threads.append(threading.get_ident())
        self.started.set()
        self.settled.clear()
        try:
            if self.fail:
                raise OSError("Pipe read failed")
            return super().read(size)
        finally:
            self.settled.set()

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
    assert not any(
        message["type"] == "http.response.body" and not message.get("more_body", False)
        for message in call.sent
    )
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
    assert not any(
        message["type"] == "http.response.body" and not message.get("more_body", False)
        for message in call.sent
    )


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
async def test_close_kills_process_which_ignores_terminate_and_is_repeatable(producer, monkeypatch):
    def signal_unowned_group(*args):
        pytest.fail("A generic stream must not signal its shared process group")

    monkeypatch.setattr(os, "killpg", signal_unowned_group)
    stream = producer("import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); os.write(1, b'R'); time.sleep(60)")
    assert await asyncio.to_thread(stream.read, 1) == b"R"
    await asyncio.wait_for(asyncio.to_thread(stream.close), 3)
    await asyncio.to_thread(stream.close)
    assert stream._proc.returncode == -signal.SIGKILL
    assert_reaped(stream)


@pytest.fixture
def external_diff(tmp_path, monkeypatch):
    """Use repository-local configuration and retain emergency helper cleanup."""
    directory = tmp_path / "repo"
    directory.mkdir()

    def run(*args):
        return subprocess.run(
            ["git", "-C", str(directory), *args], check=True, capture_output=True, timeout=3,
        )

    run("init", "-q")
    path = directory / "file.txt"
    path.write_text("before\n")
    run("add", "file.txt")
    run("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial")
    path.write_text("after\n")
    project = Project.objects.create(id="external-diff", directory=str(directory), git_root=str(directory))
    state = tmp_path / "helper"
    helper = tmp_path / "external_diff.py"
    helper.write_text('''import os
import signal
import sys
import time
from pathlib import Path

state = Path(sys.argv[1])
mode = sys.argv[2]

def terminate(*args):
    state.with_suffix(".term").write_text("terminated")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, signal.SIG_IGN if mode == "ignore" else terminate)
state.with_suffix(".tmp").write_text(str(os.getpid()))
state.with_suffix(".tmp").replace(state.with_suffix(".pid"))
if mode == "success":
    os.write(1, b"configured external patch\\n")
else:
    os.write(1, b"ready\\n")
    time.sleep(60)
''')
    streams = []
    spawn = git._spawn_git

    def observe(*args, **kwargs):
        stream = spawn(*args, **kwargs)
        stream.reader = stream._proc.stdout = ProcessReader(stream.reader.detach())
        streams.append(stream)
        return stream

    monkeypatch.setattr(git, "_spawn_git", observe)

    def configure(mode):
        run("config", "diff.external", shlex.join([sys.executable, str(helper), str(state), mode]))
        return f"/api/projects/{project.id}/git-diff-download/?path=file.txt&ref=index", state, streams

    yield configure
    # Kill the helper first: old production code can hold its stdout read lock.
    pid_file = state.with_suffix(".pid")
    if pid_file.exists():
        with suppress(psutil.NoSuchProcess):
            psutil.Process(int(pid_file.read_text())).kill()
    for stream in streams:
        if stream._proc.poll() is None:
            stream._proc.kill()
        stream._proc.wait(timeout=3)
        stream.close()


async def wait_until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def helper_stopped(helper):
    try:
        return not helper.is_running() or helper.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, leader_exited", [("graceful", False), ("ignore", False), ("ignore", True)])
async def test_external_diff_disconnect_stops_stdout_helper_after_git_exit(app, external_diff, mode, leader_exited):
    url, state, streams = external_diff(mode)
    call = AsgiCall(app, url)
    helper = None
    try:
        await wait_until(state.with_suffix(".pid").exists)
        helper = psutil.Process(int(state.with_suffix(".pid").read_text()))
        await wait_until(lambda: streams and streams[0].reader.started.is_set())
        stream = streams[0]
        assert not stream.reader.settled.is_set()
        if leader_exited:
            stream._proc.terminate()
            await asyncio.to_thread(stream._proc.wait, timeout=2)
            assert not helper_stopped(helper)

        started = asyncio.get_running_loop().time()
        call.disconnect()
        await asyncio.wait_for(asyncio.shield(call.task), 3)
        assert asyncio.get_running_loop().time() - started < 3
        await wait_until(lambda: helper_stopped(helper))
        assert stream.reader.settled.is_set()
        assert stream.reader.closed_event.is_set()
        assert_reaped(stream)
        assert not call.body
        if mode == "ignore":
            assert not state.with_suffix(".term").exists()
        else:
            assert state.with_suffix(".term").read_text() == "terminated"
        await asyncio.to_thread(stream.close)
    finally:
        if helper is not None:
            with suppress(psutil.NoSuchProcess):
                helper.kill()
        await stop(call)


@pytest.mark.asyncio
async def test_configured_external_diff_preserves_successful_output(app, external_diff):
    url, _, streams = external_diff("success")
    call = AsgiCall(app, url)
    await asyncio.wait_for(call.task, 3)
    assert call.body == b"configured external patch\n"
    assert any(
        message["type"] == "http.response.body" and not message.get("more_body", False)
        for message in call.sent
    )
    assert_reaped(streams[0])
