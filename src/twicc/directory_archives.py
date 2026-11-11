"""Incremental directory ZIPs with bounded payload buffers and owned cleanup.

ZipInfo records and the final central directory grow with the entry count.
Payload buffers and zlib's working memory do not grow with source file sizes.
"""

import errno
import io
import os
import stat
import threading
import time
import zipfile
from contextlib import suppress


_BLOCK_SIZE = 64 * 1024


def directory_archive_name(directory: str) -> str:
    """Name an attachment after its normalized source directory."""
    return f"{os.path.basename(directory) or 'root'}.zip"


class _ZipSink(io.RawIOBase):
    """Non-seekable ZIP output; drain each bounded producer operation."""

    def __init__(self):
        super().__init__()
        self._buffer = bytearray()
        self._position = 0

    def writable(self):
        return True

    def write(self, data):
        self._buffer.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self):
        return self._position

    def drain(self):
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    def close(self):
        self._buffer.clear()
        super().close()


def _entry_info(name, metadata):
    """Preserve Unix types, permissions, and representable local modification time."""
    modified = time.localtime(metadata.st_mtime)[:6]
    modified = max((1980, 1, 1, 0, 0, 0), min(modified, (2107, 12, 31, 23, 59, 58)))
    info = zipfile.ZipInfo(name, modified)
    info.create_system = 3
    info.external_attr = metadata.st_mode << 16
    if stat.S_ISDIR(metadata.st_mode):
        info.external_attr |= 0x10
    elif stat.S_ISREG(metadata.st_mode):
        info.compress_type = zipfile.ZIP_DEFLATED
        info.compress_level = 6
        # ZIP64 local headers must be selected before the first member byte.
        info.file_size = metadata.st_size
    return info


def _archive_chunks(directory, root_metadata, stop):
    """Own the writer, traversal iterators, and the one active regular source."""
    sink = _ZipSink()
    archive = None
    scans = []
    root_name = os.path.basename(directory) or "root"
    try:
        if stop.is_set():
            return
        archive = zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        archive.writestr(_entry_info(root_name + "/", root_metadata), b"")
        yield sink.drain()
        if stop.is_set():
            return
        scans.append(os.scandir(directory))
        while scans:
            if stop.is_set():
                return
            try:
                entry = next(scans[-1])
            except StopIteration:
                scans.pop().close()
                continue
            if stop.is_set():
                return
            metadata = os.lstat(entry.path)
            if stop.is_set():
                return
            mode = metadata.st_mode
            name = root_name + "/" + os.path.relpath(entry.path, directory).replace(os.sep, "/")
            if stat.S_ISDIR(mode):
                archive.writestr(_entry_info(name + "/", metadata), b"")
                yield sink.drain()
                if stop.is_set():
                    return
                scans.append(os.scandir(entry.path))
            elif stat.S_ISLNK(mode):
                target = os.fsencode(os.readlink(entry.path))
                if stop.is_set():
                    return
                archive.writestr(_entry_info(name, metadata), target)
                yield sink.drain()
            elif stat.S_ISREG(mode):
                with open(entry.path, "rb") as source:
                    if stop.is_set():
                        return
                    with archive.open(_entry_info(name, metadata), "w") as member:
                        yield sink.drain()
                        while not stop.is_set():
                            block = source.read(_BLOCK_SIZE)
                            if stop.is_set():
                                return
                            if not block:
                                break
                            member.write(block)
                            yield sink.drain()
                if stop.is_set():
                    return
                yield sink.drain()
            else:
                raise OSError(errno.ENOTSUP, "Unsupported directory archive entry", entry.path)
        if stop.is_set():
            return
        archive.close()
        archive = None
        yield sink.drain()
    finally:
        # Cleanup can write a trailer, but cancellation/failure never delivers it.
        if archive is not None:
            with suppress(Exception):
                archive.close()
        for scan in reversed(scans):
            with suppress(Exception):
                scan.close()
        sink.close()


class DirectoryZipReader(io.BufferedIOBase):
    """A bounded binary reader accepted by aiofiles' BufferedIOBase adapter.

    Close sets the stop event before it waits for an active read. The read lock
    serializes generator advancement and closure, including blocked source reads.
    """

    def __init__(self, directory: str):
        super().__init__()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._iterator = None
        self._pending = b""
        self._failure = None
        metadata = os.lstat(directory)
        if not stat.S_ISDIR(metadata.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "Not a real directory", directory)
        # Validate root access without traversing or retaining a scandir resource.
        with os.scandir(directory):
            pass
        self._iterator = _archive_chunks(directory, metadata, self._stop)

    def readable(self):
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("Directory ZIP reads require a nonnegative size")
        if size == 0:
            return b""
        with self._lock:
            if self.closed or self._stop.is_set():
                return b""
            if self._failure is not None:
                raise self._failure
            try:
                while not self._pending and self._iterator is not None:
                    try:
                        self._pending = next(self._iterator)
                    except StopIteration:
                        self._iterator = None
                    if self._stop.is_set():
                        self._pending = b""
                        return b""
                limit = min(size, _BLOCK_SIZE)
                chunk = self._pending[:limit]
                self._pending = self._pending[limit:]
                return chunk
            except BaseException as error:
                self._pending = b""
                self._failure = error
                self._iterator = None
                raise

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self.closed:
                return
            try:
                if self._iterator is not None:
                    self._iterator.close()
            finally:
                self._iterator = None
                self._pending = b""
                self._failure = None
                super().close()
