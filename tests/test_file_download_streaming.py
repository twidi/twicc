"""Disk downloads through Django's real ASGI handler and request cleanup."""

import asyncio
import base64
import builtins
import io
import os
import threading
from contextlib import suppress

import pytest
from django.core.handlers.asgi import ASGIHandler

from twicc import views
from twicc.http_streams import DownloadCleanupMiddleware

pytestmark = pytest.mark.django_db(transaction=True)


class DiskReader(io.BufferedReader):
    """Instrument real disk reads while preserving aiofiles' file adapter."""

    def __init__(self, path):
        super().__init__(builtins.open(path, "rb", buffering=0))
        self.requests = []
        self.read_threads = []
        self.close_threads = []
        self.read_started = threading.Event()
        self.read_release = threading.Event()
        self.read_settled = threading.Event()
        self.closed_event = threading.Event()
        self.hold_read = False
        self.fail_read = False
        self.closed_during_read = False

    def read(self, size=-1):
        self.requests.append(size)
        self.read_threads.append(threading.get_ident())
        self.read_started.set()
        self.read_settled.clear()
        try:
            if self.hold_read:
                assert self.read_release.wait(3), "Read was not released"
            if self.fail_read:
                raise OSError("Disk read failed")
            return super().read(size)
        finally:
            self.read_settled.set()

    def close(self):
        self.closed_during_read = self.read_started.is_set() and not self.read_settled.is_set()
        self.close_threads.append(threading.get_ident())
        super().close()
        self.closed_event.set()


class AsgiCall:
    def __init__(self, app, path, *, method="GET", send_hook=None):
        self.inbound = asyncio.Queue()
        self.inbound.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        self.sent = []
        self.first_body = asyncio.Event()
        self.headers_sent = asyncio.Event()
        self.send_release = asyncio.Event()
        self.send_hook = send_hook
        self.path, _, query = path.partition("?")
        self.scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": self.path,
            "raw_path": self.path.encode(), "query_string": query.encode(),
            "root_path": "", "headers": [], "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 3500),
        }
        self.task = asyncio.create_task(app(self.scope, self.inbound.get, self.send))

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "http.response.start":
            self.headers_sent.set()
        elif message.get("body"):
            self.first_body.set()
        if self.send_hook is not None:
            await self.send_hook(self, message)

    def disconnect(self):
        self.inbound.put_nowait({"type": "http.disconnect"})

    @property
    def body(self):
        return b"".join(message.get("body", b"") for message in self.sent)

    @property
    def headers(self):
        start = next(message for message in self.sent if message["type"] == "http.response.start")
        assert start["status"] == 200
        return {name.lower(): value for name, value in start["headers"]}


@pytest.fixture
def app(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return DownloadCleanupMiddleware(ASGIHandler())


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    path.write_bytes(b"x" * (65536 * 3 + 7))
    reader = DiskReader(path)

    def open_source(name, mode):
        assert str(name) == str(path)
        assert mode == "rb"
        return reader

    monkeypatch.setattr(views, "open", open_source, raising=False)
    root = base64.urlsafe_b64encode(str(tmp_path).encode()).decode().rstrip("=")
    yield path, reader, f"/api/file-raw/{root}{path}?download=1"
    reader.read_release.set()
    if not reader.closed:
        reader.close()


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 2), "Resource did not progress"


async def stop(call):
    call.send_release.set()
    call.disconnect()
    with suppress(asyncio.CancelledError, OSError):
        await asyncio.wait_for(call.task, 3)


@pytest.mark.asyncio
async def test_first_body_and_slow_client_do_not_read_ahead(app, source):
    _, reader, url = source

    async def pause(call, message):
        if message.get("body"):
            await call.send_release.wait()

    call = AsgiCall(app, url, send_hook=pause)
    try:
        await asyncio.wait_for(call.first_body.wait(), 2)
        assert call.body == b"x" * 65536
        assert reader.requests == [65536]
        for _ in range(10):
            await asyncio.sleep(0)
        assert reader.requests == [65536]
        call.send_release.set()
        await call.task
        assert call.body == b"x" * (65536 * 3 + 7)
        assert reader.requests == [65536, 65536, 65536, 7]
        assert reader.closed
    finally:
        await stop(call)


@pytest.mark.asyncio
async def test_held_disk_read_allows_unrelated_asyncio_progress(app, source):
    _, reader, url = source
    reader.hold_read = True
    call = AsgiCall(app, url)
    try:
        await wait_event(reader.read_started)
        progressed = asyncio.Event()
        asyncio.get_running_loop().call_soon(progressed.set)
        await asyncio.wait_for(progressed.wait(), 1)
        assert not reader.read_settled.is_set()
        assert threading.get_ident() not in reader.read_threads
        reader.read_release.set()
        await call.task
        assert reader.closed
        assert threading.get_ident() not in reader.close_threads
    finally:
        reader.read_release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_standalone_realpath_check_runs_off_loop_before_open(app, source, monkeypatch):
    path, reader, url = source
    loop_thread = threading.get_ident()
    check_threads = []
    check_started = threading.Event()
    check_release = threading.Event()
    original = os.path.realpath

    def realpath(name, *args, **kwargs):
        if str(name) == str(path):
            check_threads.append(threading.get_ident())
            check_started.set()
            assert check_release.wait(2), "Metadata check blocked the event loop"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", realpath)
    # The timer also releases the old synchronous branch, so RED cannot hang.
    timer = threading.Timer(1, check_release.set)
    timer.start()
    call = AsgiCall(app, url)
    try:
        await wait_event(check_started)
        await asyncio.sleep(0)
        assert not check_release.is_set()
        assert loop_thread not in check_threads
        assert not reader.requests
        check_release.set()
        await call.task
        assert reader.closed
    finally:
        check_release.set()
        timer.cancel()
        await stop(call)


@pytest.mark.asyncio
async def test_head_closes_the_open_file_without_reading(app, source):
    _, reader, url = source
    call = AsgiCall(app, url, method="HEAD")
    await call.task
    assert call.headers[b"content-length"] == b"196615"
    assert call.headers[b"content-disposition"] == b'attachment; filename="file.bin"'
    assert call.body == b""
    assert reader.requests == []
    assert reader.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["grow", "replace", "shrink"])
async def test_open_source_stays_fixed_and_length_limits_output(app, source, mutation):
    path, reader, url = source

    def change_file():
        if mutation == "grow":
            with builtins.open(path, "ab") as file:
                file.write(b"extra")
        elif mutation == "replace":
            replacement = path.with_suffix(".new")
            replacement.write_bytes(b"replacement")
            replacement.replace(path)
        else:
            path.write_bytes(b"")

    async def mutate(call, message):
        if not message.get("body") or call.send_release.is_set():
            return
        await asyncio.to_thread(change_file)
        call.send_release.set()

    call = AsgiCall(app, url, send_hook=mutate)
    if mutation == "shrink":
        with pytest.raises(OSError, match="announced length"):
            await call.task
        assert call.body == b"x" * 65536
        assert not any(message.get("more_body") is False for message in call.sent)
    else:
        await call.task
        assert call.body == b"x" * (65536 * 3 + 7)
    assert call.headers[b"content-length"] == b"196615"
    assert reader.closed


@pytest.mark.asyncio
async def test_length_and_file_type_come_from_open_descriptor(app, source, monkeypatch):
    path, reader, url = source
    # Change the path before the factory opens it. The descriptor remains original.
    replacement = path.with_suffix(".new")
    replacement.write_bytes(b"new")
    replacement.replace(path)
    call = AsgiCall(app, url)
    await call.task
    assert call.headers[b"content-length"] == b"196615"
    assert call.body == b"x" * (65536 * 3 + 7)
    assert reader.closed


@pytest.mark.asyncio
async def test_nonregular_open_descriptor_is_refused(app, source, monkeypatch):
    _, reader, url = source
    original = os.fstat

    def fstat(fd):
        info = original(fd)
        if fd == reader.fileno():
            return os.stat_result((0o040755, *info[1:]))
        return info

    monkeypatch.setattr(os, "fstat", fstat)
    call = AsgiCall(app, url)
    await call.task
    assert call.sent[0]["status"] == 404
    assert not reader.requests
    assert reader.closed


@pytest.mark.asyncio
async def test_open_failure_remains_a_404(app, source, monkeypatch):
    _, reader, url = source

    def fail(*args, **kwargs):
        raise PermissionError("No access")

    monkeypatch.setattr(views, "open", fail)
    call = AsgiCall(app, url)
    await call.task
    assert call.sent[0]["status"] == 404
    assert not reader.requests


@pytest.mark.asyncio
async def test_response_factory_failure_closes_acquired_file(app, source, monkeypatch):
    _, reader, url = source

    def fail(*args, **kwargs):
        raise OSError("Cannot determine media type")

    monkeypatch.setattr(views, "_guess_raw_content_type", fail)
    call = AsgiCall(app, url)
    await call.task
    assert call.sent[0]["status"] == 500
    assert reader.closed
    assert not reader.requests


@pytest.mark.asyncio
async def test_disconnect_during_open_releases_late_acquisition(app, source, monkeypatch):
    path, reader, url = source
    started = threading.Event()
    release = threading.Event()

    def delayed_open(name, mode):
        assert str(name) == str(path)
        started.set()
        assert release.wait(3)
        return reader

    monkeypatch.setattr(views, "open", delayed_open)
    call = AsgiCall(app, url)
    try:
        await wait_event(started)
        call.disconnect()
        # Let Django observe the disconnect before acquisition returns.
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        await call.task
        await wait_event(reader.closed_event)
        assert not call.sent
        assert not reader.requests
    finally:
        release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_disconnect_during_read_waits_for_read_before_close(app, source):
    _, reader, url = source
    reader.hold_read = True
    call = AsgiCall(app, url)
    try:
        await wait_event(reader.read_started)
        call.disconnect()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not reader.closed
        reader.read_release.set()
        await call.task
        await wait_event(reader.closed_event)
        assert not call.body
        assert not reader.closed_during_read
        assert len(reader.close_threads) == 1
    finally:
        reader.read_release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_disk_read_failure_aborts_transfer_and_closes_file(app, source):
    _, reader, url = source
    reader.fail_read = True
    call = AsgiCall(app, url)
    with pytest.raises(OSError, match="Disk read failed"):
        await call.task
    assert reader.closed
    assert not call.body
    assert not any(message.get("more_body") is False for message in call.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("failure", ["disconnect", "send_error"])
async def test_interrupted_send_closes_source_even_before_iteration(app, source, phase, failure):
    _, reader, url = source
    sending = asyncio.Event()

    async def interrupt(call, message):
        target = message["type"] == "http.response.start" if phase == "headers" else bool(message.get("body"))
        if target:
            sending.set()
            if failure == "send_error":
                raise OSError("Send failed")
            await asyncio.Future()

    call = AsgiCall(app, url, send_hook=interrupt)
    try:
        await asyncio.wait_for(sending.wait(), 2)
        if failure == "disconnect":
            call.disconnect()
            await call.task
        else:
            with pytest.raises(OSError, match="Send failed"):
                await call.task
        await wait_event(reader.closed_event)
        assert reader.requests == ([] if phase == "headers" else [65536])
        assert len(reader.close_threads) == 1
    finally:
        await stop(call)


def test_production_django_entrypoint_installs_download_cleanup():
    from twicc.asgi import django_asgi_app

    assert isinstance(django_asgi_app, DownloadCleanupMiddleware)
    assert isinstance(django_asgi_app.application, ASGIHandler)
