"""Real directory ZIP streams through Django ASGI, including interrupted ownership."""

import asyncio
import builtins
import hashlib
import io
import os
import threading
from urllib.parse import urlencode
import zipfile

import pytest
from django.core.handlers.asgi import ASGIHandler

from tests.test_file_download_streaming import AsgiCall, stop, wait_event
from twicc import directory_archives, views
from twicc.http_streams import DownloadCleanupMiddleware, prepare_download_response

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def app(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return DownloadCleanupMiddleware(ASGIHandler())


class SourceProbe:
    """Instrument narrow real source operations without replacing ZIP generation."""

    def __init__(self, root, monkeypatch):
        self.root = root
        self.file = root / "payload.bin"
        self.file.write_bytes(b"source payload" * 20000)
        self.readers = []
        self.responses = []
        self.sources = []
        self.writers = []
        self.scans = []
        self.threads = {name: [] for name in ["scan", "metadata", "read", "compress", "finalize", "close"]}
        self.bytes_read = 0
        self.traversed = 0
        self.hold = None
        self.failure = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.close_started = threading.Event()
        self.closed = threading.Event()
        self.overlap = False
        self.active_reads = 0
        self.max_payload = 0
        owner = self
        original_scan = os.scandir
        original_lstat = os.lstat
        original_write = zipfile._ZipWriteFile.write
        original_zip_close = zipfile.ZipFile.close
        original_zip_init = zipfile.ZipFile.__init__
        original_end_record = zipfile.ZipFile._write_end_record
        original_response = views.AsyncFileResponse

        class TrackedScan:
            def __init__(self, path):
                self.scan = original_scan(path)
                self.closed = False
                owner.scans.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def __iter__(self):
                return self

            def __next__(self):
                owner.operation("traversal")
                entry = next(self.scan)
                owner.traversed += 1
                return entry

            def close(self):
                owner.threads["close"].append(threading.get_ident())
                self.scan.close()
                self.closed = True

        def scan(path):
            if str(path).startswith(str(root)):
                owner.threads["scan"].append(threading.get_ident())
                owner.operation("acquire")
                return TrackedScan(path)
            return original_scan(path)

        def lstat(path, *args, **kwargs):
            if str(path).startswith(str(root)):
                owner.threads["metadata"].append(threading.get_ident())
                if str(path) != str(root):
                    owner.operation("metadata")
            return original_lstat(path, *args, **kwargs)

        class Source(io.BufferedReader):
            def __init__(self, path):
                super().__init__(builtins.open(path, "rb", buffering=0))
                owner.sources.append(self)

            def read(self, size=-1):
                owner.threads["read"].append(threading.get_ident())
                owner.active_reads += 1
                try:
                    owner.operation("read")
                    data = super().read(size)
                    owner.bytes_read += len(data)
                    return data
                finally:
                    owner.active_reads -= 1

            def close(self):
                owner.threads["close"].append(threading.get_ident())
                owner.overlap |= bool(owner.active_reads)
                super().close()

        def source_open(path, mode):
            assert mode == "rb"
            owner.operation("open")
            return Source(path)

        class Reader(directory_archives.DirectoryZipReader):
            def __init__(self, directory):
                super().__init__(directory)
                owner.readers.append(self)

            def close(self):
                owner.close_started.set()
                super().close()
                owner.closed.set()

        def write(member, data):
            if data:
                owner.threads["compress"].append(threading.get_ident())
                owner.operation("compression")
            return original_write(member, data)

        def zip_init(writer, *args, **kwargs):
            original_zip_init(writer, *args, **kwargs)
            if writer.mode == "w":
                owner.writers.append(writer)

        def zip_close(writer):
            if writer.mode == "w" and writer.fp is not None:
                owner.threads["finalize"].append(threading.get_ident())
            return original_zip_close(writer)

        def response(*args, **kwargs):
            prepared = original_response(*args, **kwargs)
            owner.responses.append(prepared)
            return prepared

        def end_record(writer):
            owner.operation("finalization")
            return original_end_record(writer)

        monkeypatch.setattr(os, "scandir", scan)
        monkeypatch.setattr(os, "lstat", lstat)
        monkeypatch.setattr(directory_archives, "open", source_open, raising=False)
        monkeypatch.setattr(views, "DirectoryZipReader", Reader)
        monkeypatch.setattr(views, "AsyncFileResponse", response)
        monkeypatch.setattr(zipfile._ZipWriteFile, "write", write)
        monkeypatch.setattr(zipfile.ZipFile, "__init__", zip_init)
        monkeypatch.setattr(zipfile.ZipFile, "close", zip_close)
        monkeypatch.setattr(zipfile.ZipFile, "_write_end_record", end_record)

    @property
    def url(self):
        return "/api/directory-download/?" + urlencode({"path": self.root, "root": self.root})

    def operation(self, phase):
        if self.hold == phase:
            self.started.set()
            assert self.release.wait(5), f"Unreleased {phase}"
        if self.failure == phase:
            raise OSError(f"Injected {phase} failure")

    def payload_bytes(self, body):
        """Count distinct retained payloads, including the currently sent chunk.

        The source's active block, yielded ZIP output, pending reader bytes,
        sink buffer, and ASGI body cover the adapter's Python payload owners.
        zlib state and buffered file I/O add fixed working memory.
        """
        buffers = {id(body): body}
        for response in self.responses:
            # Django keeps the yielded adapter chunk while sending its own
            # bytes copy. Count both distinct objects, not just send's body.
            iterator = response._iterator
            if iterator.ag_frame is not None:
                for value in iterator.ag_frame.f_locals.values():
                    if isinstance(value, (bytes, bytearray)):
                        buffers[id(value)] = value
            pending = response._pending
            if pending is not None and pending.done() and not pending.cancelled():
                value = pending.result()
                if isinstance(value, bytes):
                    buffers[id(value)] = value
        for reader in self.readers:
            buffers[id(reader._pending)] = reader._pending
            iterator = reader._iterator
            if iterator is not None and iterator.gi_frame is not None:
                local = iterator.gi_frame.f_locals
                for value in local.values():
                    if isinstance(value, (bytes, bytearray)):
                        buffers[id(value)] = value
                sink = local.get("sink")
                if sink is not None:
                    buffers[id(sink._buffer)] = sink._buffer
        total = sum(len(value) for value in buffers.values())
        self.max_payload = max(total, self.max_payload)
        return total

    def assert_closed(self):
        assert self.readers and all(reader.closed for reader in self.readers)
        assert all(source.closed for source in self.sources)
        assert all(writer.fp is None for writer in self.writers)
        assert all(scan.closed for scan in self.scans)
        assert not self.overlap


@pytest.fixture
def probe(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    probe = SourceProbe(root, monkeypatch)
    yield probe
    probe.release.set()


def completed(call):
    return any(message["type"] == "http.response.body" and not message.get("more_body", False)
               for message in call.sent)


async def barrier():
    """Give both the executor and loop an explicit synchronization boundary."""
    await asyncio.to_thread(lambda: None)
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


@pytest.mark.asyncio
async def test_asgi_progressive_zip(app, probe):
    async def inspect(call, message):
        if message.get("body") and not probe.traversed:
            assert probe.bytes_read == 0
            assert call.body.startswith(b"PK\x03\x04")
            await call.send_release.wait()
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            with zipfile.ZipFile(io.BytesIO(call.body)) as archive:
                assert archive.testzip() is None
                assert archive.read("archive/payload.bin") == b"source payload" * 20000

    call = AsgiCall(app, probe.url, send_hook=inspect)
    try:
        await asyncio.wait_for(call.first_body.wait(), 2)
        assert probe.traversed == probe.bytes_read == 0
        call.send_release.set()
        await call.task
        assert completed(call)
        probe.assert_closed()
    finally:
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("size_mib", [8, 64])
@pytest.mark.parametrize("compressible", [True, False])
async def test_asgi_payload_bounds_and_paused_send(app, probe, size_mib, compressible):
    # One file keeps ZIP metadata constant while source size changes eightfold.
    with builtins.open(probe.file, "wb") as source:
        for _ in range(size_mib * 16):
            source.write(b"x" * 65536 if compressible else os.urandom(65536))
    digest = hashlib.sha256()
    sent_bytes = 0
    paused = asyncio.Event()

    async def consume(call, message):
        nonlocal sent_bytes
        body = message.get("body", b"")
        digest.update(body)
        sent_bytes += len(body)
        assert probe.payload_bytes(body) <= 1024 * 1024
        # The shared harness records this dict before calling the hook.
        # Remove content so harness memory cannot grow with the response.
        message["body"] = b""
        if body and not paused.is_set():
            paused.set()
            await call.send_release.wait()

    call = AsgiCall(app, probe.url, send_hook=consume)
    try:
        await asyncio.wait_for(paused.wait(), 2)
        before = (probe.traversed, probe.bytes_read, len(probe.sources))
        await barrier()
        assert (probe.traversed, probe.bytes_read, len(probe.sources)) == before == (0, 0, 0)
        assert all(not message.get("body") for message in call.sent)
        call.send_release.set()
        await call.task
        assert completed(call)
        assert probe.bytes_read == size_mib * 1024 * 1024
        assert 0 < probe.max_payload <= 1024 * 1024
        assert sent_bytes > 0 and digest.digest() != hashlib.sha256().digest()
        assert all(not message.get("body") for message in call.sent)
        probe.assert_closed()
    finally:
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["read", "compression"])
async def test_asgi_loop_progress_during_source_work(app, probe, phase):
    probe.hold = phase
    call = AsgiCall(app, probe.url)
    try:
        await wait_event(probe.started)
        await barrier()
        assert not probe.release.is_set()
        probe.release.set()
        await call.task
        loop_thread = threading.get_ident()
        for operation, threads in probe.threads.items():
            assert threads, operation
            assert loop_thread not in threads, operation
        probe.assert_closed()
    finally:
        probe.release.set()
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["traversal", "metadata", "open", "read", "compression", "finalization", "unsupported"])
async def test_asgi_late_source_failures_abort(app, probe, phase):
    if phase == "unsupported":
        os.mkfifo(probe.root / "pipe")
    else:
        probe.failure = phase
    call = AsgiCall(app, probe.url)
    try:
        with pytest.raises(OSError):
            await call.task
        assert call.sent[0]["status"] == 200
        assert call.body.startswith(b"PK\x03\x04")
        assert not completed(call)
        assert b"Injected" not in call.body
        assert b'"error"' not in call.body
        probe.assert_closed()
    finally:
        probe.failure = None
        await stop(call)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["normal", "before_iteration", "pending_read", "paused_send", "headers_fail", "body_fail"])
async def test_asgi_cleanup_matrix(app, probe, scenario):
    if scenario == "before_iteration":
        response = await prepare_download_response(lambda: views._directory_download_response(str(probe.root), head=False))
        response.close()
        response.close()
        await response._release()
        await response._release()
        assert not probe.sources and not probe.writers
        probe.assert_closed()
        return
    sending = asyncio.Event()
    if scenario == "pending_read":
        probe.hold = "read"

    async def interrupt(call, message):
        target = (scenario == "headers_fail" and message["type"] == "http.response.start") or (
            scenario in ("body_fail", "paused_send") and bool(message.get("body")))
        if target:
            sending.set()
            if scenario.endswith("fail"):
                raise OSError("Send failed")
            await call.send_release.wait()

    call = AsgiCall(app, probe.url, send_hook=interrupt)
    try:
        if scenario == "pending_read":
            await wait_event(probe.started)
            call.disconnect()
            await wait_event(probe.close_started)
            assert not probe.closed.is_set()
            probe.release.set()
        elif scenario == "paused_send":
            await asyncio.wait_for(sending.wait(), 2)
            call.disconnect()
        if scenario.endswith("fail"):
            with pytest.raises(OSError, match="Send failed"):
                await call.task
        else:
            await call.task
        await wait_event(probe.closed)
        probe.assert_closed()
        if scenario != "normal":
            assert not completed(call)
    finally:
        probe.release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_asgi_late_acquisition_and_repeat_cleanup(app, probe, monkeypatch):
    cancelled = asyncio.Event()

    async def observe_preparation(factory):
        try:
            return await prepare_download_response(factory)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(views, "prepare_download_response", observe_preparation)
    probe.hold = "acquire"
    call = AsgiCall(app, probe.url)
    try:
        await wait_event(probe.started)
        call.disconnect()
        await asyncio.wait_for(cancelled.wait(), 2)
        assert not call.sent
        assert not probe.closed.is_set()
        probe.release.set()
        await call.task
        await wait_event(probe.closed)
        assert not probe.sources and not probe.writers
        for response in probe.responses:
            response.close()
            response.close()
            await response._release()
            await response._release()
        probe.assert_closed()
    finally:
        probe.release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_asgi_finalization_cancel(app, probe):
    probe.hold = "finalization"
    call = AsgiCall(app, probe.url)
    try:
        await wait_event(probe.started)
        assert call.body.startswith(b"PK\x03\x04")
        call.disconnect()
        await wait_event(probe.close_started)
        probe.release.set()
        await call.task
        await wait_event(probe.closed)
        assert not completed(call)
        assert b"PK\x05\x06" not in call.body
        probe.assert_closed()
    finally:
        probe.release.set()
        await stop(call)


@pytest.mark.asyncio
async def test_asgi_many_entry_metadata_remains_progressive(app, probe):
    # This entry-count fixture is separate from the constant-metadata size test.
    probe.file.unlink()
    for index in range(300):
        (probe.root / f"entry-{index:03}.txt").touch()

    async def pause(call, message):
        if message.get("body") and not probe.traversed:
            await call.send_release.wait()

    call = AsgiCall(app, probe.url, send_hook=pause)
    try:
        await asyncio.wait_for(call.first_body.wait(), 2)
        assert not probe.traversed
        call.send_release.set()
        await call.task
        with zipfile.ZipFile(io.BytesIO(call.body)) as archive:
            assert len(archive.infolist()) == 301
            assert archive.testzip() is None
        assert probe.traversed == 300
        probe.assert_closed()
    finally:
        await stop(call)


@pytest.mark.asyncio
async def test_asgi_response_construction_failure_closes_reader(app, probe, monkeypatch):
    def fail_response(*args, **kwargs):
        raise OSError("Response construction failed")

    monkeypatch.setattr(views, "AsyncFileResponse", fail_response)
    call = AsgiCall(app, probe.url)
    await call.task
    assert call.sent[0]["status"] == 500
    probe.assert_closed()
    assert not probe.sources and not probe.writers


@pytest.mark.asyncio
async def test_asgi_preparation_oserror_remains_server_error(app, probe):
    probe.failure = "acquire"
    call = AsgiCall(app, probe.url)
    await call.task
    assert call.sent[0]["status"] == 500
    assert not call.body.startswith(b"PK")
    assert not probe.readers


@pytest.mark.asyncio
async def test_asgi_directory_head_has_no_content_work(app, probe):
    call = AsgiCall(app, probe.url, method="HEAD")
    await call.task
    assert call.headers[b"content-type"] == b"application/zip"
    assert call.headers[b"content-disposition"] == b'attachment; filename="archive.zip"'
    assert b"content-length" not in call.headers
    assert not call.body
    assert probe.traversed == probe.bytes_read == 0
    assert not probe.sources and not probe.writers
    probe.assert_closed()


@pytest.mark.asyncio
async def test_asgi_standalone_confinement_runs_off_loop(app, probe, monkeypatch):
    original = os.path.realpath
    threads = []

    def realpath(path, *args, **kwargs):
        if str(path) == str(probe.root):
            threads.append(threading.get_ident())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", realpath)
    call = AsgiCall(app, probe.url)
    await call.task
    assert threads and threading.get_ident() not in threads
    probe.assert_closed()
