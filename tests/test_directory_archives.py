"""ZIP consumers verify content, failures, cancellation, and payload bounds."""

import builtins
import io
import os
import socket
import stat
import struct
import threading
import time
import tracemalloc
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from twicc import directory_archives
from twicc.directory_archives import DirectoryZipReader, directory_archive_name


def collect(reader, size=65536):
    with reader:
        return b"".join(iter(lambda: reader.read(size), b""))


def archive(directory):
    return zipfile.ZipFile(io.BytesIO(collect(DirectoryZipReader(str(directory)))))


def test_complete_tree_and_layout(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    files = {
        "data.bin": bytes(range(256)),
        "résumé/日本語.txt": "héllo".encode(),
        ".env": b"secret=value\n",
        ".git/HEAD": b"ref: refs/heads/main\n",
        "node_modules/ignored.js": b"ignored but included",
        "deep/not/in/lazy/tree/payload": b"deep bytes",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "nested/empty").mkdir(parents=True)
    with archive(root) as result:
        assert result.testzip() is None
        assert "reports/" in result.namelist()
        assert "reports/nested/empty/" in result.namelist()
        assert all(name.startswith("reports/") and not name.startswith("/") for name in result.namelist())
        assert {info.filename for info in result.infolist() if not info.is_dir()} == {
            "reports/" + name for name in files
        }
        for name, data in files.items():
            assert result.read("reports/" + name) == data
            assert result.getinfo("reports/" + name).CRC == zlib.crc32(data)


def test_empty_directory_and_root_name(tmp_path):
    root = tmp_path / "résumé"
    root.mkdir()
    with archive(root) as result:
        assert result.namelist() == ["résumé/"]
        assert result.read("résumé/") == b""
    assert directory_archive_name("/") == "root.zip"
    assert directory_archive_name(str(root)) == "résumé.zip"


def test_metadata(tmp_path):
    root = tmp_path / "reports"
    root.mkdir(mode=0o750)
    for name, timestamp in [("run", 1700000000), ("old", 0), ("future", 5000000000)]:
        path = root / name
        path.write_bytes(b"payload")
        path.chmod(0o751)
        os.utime(path, (timestamp, timestamp))
    with archive(root) as result:
        assert stat.S_ISDIR(result.getinfo("reports/").external_attr >> 16)
        info = result.getinfo("reports/run")
        assert info.create_system == 3
        assert stat.S_ISREG(info.external_attr >> 16)
        assert stat.S_IMODE(info.external_attr >> 16) == 0o751
        assert info.date_time[:5] == time.localtime(1700000000)[:5]
        assert abs(info.date_time[5] - time.localtime(1700000000)[5]) <= 1
        assert result.getinfo("reports/old").date_time == (1980, 1, 1, 0, 0, 0)
        assert result.getinfo("reports/future").date_time == (2107, 12, 31, 23, 59, 58)


def test_symlinks_preserved_without_target_access(tmp_path, monkeypatch):
    root = tmp_path / "reports"
    root.mkdir()
    external = tmp_path / "outside"
    external.mkdir()
    (external / "secret").write_bytes(b"never read")
    targets = {"relative": "missing", "broken": "absent", "directory": "../outside", "cycle": ".", "external": str(external / "secret")}
    for name, target in targets.items():
        (root / name).symlink_to(target)
    original_scan = os.scandir

    def scan(path):
        assert os.fspath(path) == str(root), "Must not scan a symlink target"
        return original_scan(path)

    def no_open(*args, **kwargs):
        pytest.fail("Must not open a symlink target")

    monkeypatch.setattr(directory_archives.os, "scandir", scan)
    monkeypatch.setattr(directory_archives, "open", no_open, raising=False)
    with archive(root) as result:
        assert len(result.infolist()) == len(targets) + 1
        for name, target in targets.items():
            info = result.getinfo("reports/" + name)
            assert stat.S_ISLNK(info.external_attr >> 16)
            assert info.compress_type == zipfile.ZIP_STORED
            assert result.read(info) == os.fsencode(target)


def test_invalid_root(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        DirectoryZipReader(str(tmp_path / "missing"))
    file = tmp_path / "file"
    file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        DirectoryZipReader(str(file))
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(NotADirectoryError):
        DirectoryZipReader(str(link))
    scans = []

    def denied(path):
        scans.append(path)
        raise PermissionError("root access")

    monkeypatch.setattr(directory_archives.os, "scandir", denied)
    with pytest.raises(PermissionError, match="root access"):
        DirectoryZipReader(str(tmp_path))
    assert scans == [str(tmp_path)]


@pytest.mark.parametrize("kind", ["fifo", "socket", "device"])
def test_unsupported_entries_fail_before_open(tmp_path, monkeypatch, kind):
    path = tmp_path / "special"
    connection = None
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(path)
    elif kind == "socket":
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("Unix sockets unavailable")
        connection = socket.socket(socket.AF_UNIX)
        connection.bind(str(path))
    else:
        path.touch()
        original = os.lstat

        def device_metadata(name):
            result = original(name)
            if os.fspath(name) == str(path):
                values = list(result)
                values[0] = stat.S_IFCHR | 0o600
                return os.stat_result(values)
            return result

        monkeypatch.setattr(directory_archives.os, "lstat", device_metadata)

    def no_open(*args, **kwargs):
        pytest.fail("Unsupported entries must never be opened")

    monkeypatch.setattr(directory_archives, "open", no_open, raising=False)
    try:
        with pytest.raises(OSError):
            collect(DirectoryZipReader(str(tmp_path)))
    finally:
        if connection:
            connection.close()


@pytest.mark.parametrize("operation", ["scandir", "lstat", "open", "read", "compression", "finalization", "removed"])
def test_source_failures_propagate(tmp_path, monkeypatch, operation):
    root = tmp_path / "reports"
    root.mkdir()
    path = root / "data"
    path.write_bytes(b"payload")
    reader = DirectoryZipReader(str(root))

    def failure(*args, **kwargs):
        raise OSError("injected " + operation)

    if operation == "scandir":
        monkeypatch.setattr(directory_archives.os, "scandir", failure)
    elif operation in {"lstat", "removed"}:
        original = os.lstat

        def metadata(name):
            if os.fspath(name) == str(path):
                if operation == "removed":
                    path.unlink()
                    return original(name)
                return failure()
            return original(name)

        monkeypatch.setattr(directory_archives.os, "lstat", metadata)
    elif operation == "open":
        monkeypatch.setattr(directory_archives, "open", failure, raising=False)
    elif operation == "read":
        class FailingSource(io.BytesIO):
            def read(self, size=-1):
                return failure()

        source = FailingSource()
        monkeypatch.setattr(directory_archives, "open", lambda *args, **kwargs: source, raising=False)
    elif operation == "compression":
        original_compressor = zipfile._get_compressor

        class FailingCompressor:
            def __init__(self, compressor):
                self.compressor = compressor

            def compress(self, data):
                return failure()

            def flush(self):
                return self.compressor.flush()

        def compressor(*args):
            instance = original_compressor(*args)
            return FailingCompressor(instance) if instance is not None else None

        monkeypatch.setattr(zipfile, "_get_compressor", compressor)
    else:
        monkeypatch.setattr(zipfile.ZipFile, "_write_end_record", failure)
    with pytest.raises(OSError):
        collect(reader)
    if operation == "read":
        assert source.closed


def test_reader_sizes_and_empty_compression_output(tmp_path, monkeypatch):
    path = tmp_path / "zeros"
    path.write_bytes(b"\0" * (4 * 1024 * 1024))
    reader = DirectoryZipReader(str(tmp_path))
    original_scan = os.scandir
    scans = []

    def scan(name):
        scans.append(name)
        return original_scan(name)

    monkeypatch.setattr(directory_archives.os, "scandir", scan)
    assert reader.read(0) == b""
    assert not scans
    with pytest.raises(ValueError):
        reader.read(-1)
    chunks = []
    for size in [1, 7, 1000000]:
        chunk = reader.read(size)
        assert 0 < len(chunk) <= min(size, 65536)
        chunks.append(chunk)
    while chunk := reader.read(1000000):
        assert len(chunk) <= 65536
        chunks.append(chunk)
    reader.close()
    with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as result:
        assert result.read(tmp_path.name + "/zeros") == path.read_bytes()


def test_first_output_precedes_traversal_and_content(tmp_path, monkeypatch):
    (tmp_path / "child").mkdir()
    reader = DirectoryZipReader(str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("The first output must precede descendant operations")

    monkeypatch.setattr(directory_archives.os, "scandir", forbidden)
    monkeypatch.setattr(directory_archives, "open", forbidden, raising=False)
    try:
        assert reader.read(65536).startswith(b"PK\x03\x04")
    finally:
        reader.close()


def test_close_before_iteration_and_repeated_close(tmp_path, monkeypatch):
    reader = DirectoryZipReader(str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("An unused reader must not start compression or traversal")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    monkeypatch.setattr(directory_archives.os, "scandir", forbidden)
    monkeypatch.setattr(directory_archives, "open", forbidden, raising=False)
    reader.close()
    reader.close()
    assert reader.closed
    assert reader.read(65536) == b""


@pytest.mark.parametrize("blocks_before_hold", [0, 4])
def test_close_during_pending_read(tmp_path, monkeypatch, blocks_before_hold):
    (tmp_path / "data").touch()
    started = threading.Event()
    release = threading.Event()
    settled = threading.Event()

    class HeldSource(io.BytesIO):
        requests = 0
        closed_during_read = False

        def read(self, size=-1):
            assert 0 < size <= 65536
            self.requests += 1
            if self.requests > blocks_before_hold:
                started.set()
                assert release.wait(3)
                settled.set()
            return b"\0" * size

        def close(self):
            self.closed_during_read = started.is_set() and not settled.is_set()
            super().close()

    source = HeldSource()
    monkeypatch.setattr(directory_archives, "open", lambda *args, **kwargs: source, raising=False)
    reader = DirectoryZipReader(str(tmp_path))

    def consume():
        while reader.read(65536):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        consuming = pool.submit(consume)
        closing = None
        try:
            assert started.wait(2)
            closing = pool.submit(reader.close)
            # The stop event must become visible while read still holds its lock.
            assert reader._stop.wait(2)
            assert not closing.done()
        finally:
            release.set()
        consuming.result(timeout=3)
        if closing:
            closing.result(timeout=3)
    assert source.closed
    assert not source.closed_during_read
    assert source.requests == blocks_before_hold + 1
    assert reader.read(65536) == b""


def test_stop_during_compressible_input(tmp_path, monkeypatch):
    # The held-read variant above reaches this boundary after empty Deflate drains.
    test_close_during_pending_read(tmp_path, monkeypatch, blocks_before_hold=4)


def test_payload_memory_does_not_scale_with_file_size(tmp_path, monkeypatch):
    import random

    path = tmp_path / "generated"
    original_open = builtins.open
    original_write = directory_archives._ZipSink.write
    original_drain = directory_archives._ZipSink.drain
    active = {}
    maximum = [0]

    def account(extra=0):
        reader = active.get("reader")
        if reader is None:
            return
        frame = reader._iterator.gi_frame if reader._iterator else None
        block = frame.f_locals.get("block", b"") if frame else b""
        sink = frame.f_locals.get("sink") if frame else None
        retained = len(block) + len(reader._pending) + (len(sink._buffer) if sink else 0) + extra
        # Returned output and fixed native compressor buffers can coexist with these buffers.
        maximum[0] = max(maximum[0], retained + 65536 + 400 * 1024)

    def write(sink, data):
        result = original_write(sink, data)
        account(len(data))
        return result

    def drain(sink):
        data = original_drain(sink)
        account(len(data))
        return data

    monkeypatch.setattr(directory_archives._ZipSink, "write", write)
    monkeypatch.setattr(directory_archives._ZipSink, "drain", drain)
    peaks = {}
    for random_content in [False, True]:
        for total in [8 * 1024 * 1024, 64 * 1024 * 1024]:
            with original_open(path, "wb") as fixture:
                fixture.truncate(total)

            class GeneratedSource(io.BufferedIOBase):
                remaining = total
                generator = random.Random(17)

                def read(self, size=-1):
                    assert 0 < size <= 65536
                    count = min(size, self.remaining)
                    self.remaining -= count
                    block = self.generator.randbytes(count) if random_content else b"\0" * count
                    account(len(block))
                    return block

            source = GeneratedSource()
            monkeypatch.setattr(directory_archives, "open", lambda *args, **kwargs: source, raising=False)
            tracemalloc.start()
            reader = DirectoryZipReader(str(tmp_path))
            active["reader"] = reader
            delivered = 0
            with reader:
                while chunk := reader.read(65536):
                    delivered += len(chunk)
                    account(len(chunk))
            _, peaks[random_content, total] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert source.remaining == 0
            assert source.closed
            assert delivered > 0
        assert peaks[random_content, 64 * 1024 * 1024] <= peaks[random_content, 8 * 1024 * 1024] + 512 * 1024
    # Payload only. ZipInfo objects, paths, and the central directory grow with entry count.
    assert maximum[0] < 1024 * 1024


def test_zip64_large_member_and_many_entries(tmp_path, monkeypatch):
    root = tmp_path / "reports"
    root.mkdir()
    path = root / "large"
    path.write_bytes(b"small actual content")
    original_lstat = os.lstat

    def large_metadata(name):
        result = original_lstat(name)
        if os.fspath(name) == str(path):
            values = list(result)
            values[6] = 5 * 1024 * 1024 * 1024
            return os.stat_result(values)
        return result

    monkeypatch.setattr(directory_archives.os, "lstat", large_metadata)
    data = collect(DirectoryZipReader(str(root)))
    local = data.index(b"PK\x03\x04", 4)
    header = struct.unpack_from("<4s5H3I2H", data, local)
    assert header[7:9] == (0xFFFFFFFF, 0xFFFFFFFF)
    extra_start = local + 30 + header[9]
    assert struct.unpack_from("<H", data, extra_start)[0] == 1
    with zipfile.ZipFile(io.BytesIO(data)) as result:
        assert result.read("reports/large") == b"small actual content"
        assert result.testzip() is None

    monkeypatch.setattr(directory_archives.os, "lstat", original_lstat)
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 64)
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 2)
    for index in range(4):
        (root / str(index)).write_bytes(b"a" * 100)
    data = collect(DirectoryZipReader(str(root)))
    assert b"PK\x06\x06" in data
    with zipfile.ZipFile(io.BytesIO(data)) as result:
        assert len(result.infolist()) == 6
        assert result.testzip() is None
        assert result.read("reports/3") == b"a" * 100
