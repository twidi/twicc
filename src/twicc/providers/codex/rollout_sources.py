"""Read-only access to plain and compressed Codex rollout sources.

Preparation validates the complete compressed stream before exposing decoded
bytes. Metadata and backlog reads are cheap hints, never integrity checks.
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, NamedTuple, Self

import orjson
import zstandard


# The installed 0.25.0 C backend takes this limit in bytes.
_MAX_WINDOW_SIZE = 64 * 1024 * 1024
# Small compressed inputs bound each decompressobj() output allocation.
_COMPRESSED_CHUNK_SIZE = 256


class RolloutSourceError(OSError):
    """A rollout cannot provide a complete, stable decoded source."""


class SourceFingerprint(NamedTuple):
    physical_path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class PreparedRollout:
    """A source and its validated read path, with explicit temporary ownership."""

    def __init__(
        self, physical_path: Path, read_path: Path, logical_size: int,
        mtime: float, fingerprint: SourceFingerprint, *, temporary: bool = False,
    ) -> None:
        self.physical_path = physical_path
        self.read_path = read_path
        self.logical_size = logical_size
        self.mtime = mtime
        self.fingerprint = fingerprint
        self._temporary = temporary

    def close(self) -> None:
        if self._temporary:
            self.read_path.unlink(missing_ok=True)
            self._temporary = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()


def logical_rollout_path(path: Path) -> Path:
    """Remove only the final compression suffix, without resolving symlinks."""
    return path.with_suffix("") if path.suffix == ".zst" else path


def is_rollout_path(path: Path) -> bool:
    logical = logical_rollout_path(path)
    return logical.name.startswith("rollout-") and logical.suffix == ".jsonl"


def resolve_rollout(path: Path) -> Path:
    """Prefer plain JSONL, including a restoration between missing lookups.

    Only absence permits a sibling lookup. Permission and other errors propagate.
    """
    plain = logical_rollout_path(path)
    compressed = plain.with_suffix(plain.suffix + ".zst")
    for candidate in (plain, compressed, plain):
        try:
            candidate.stat()
        except FileNotFoundError:
            continue
        return candidate
    raise FileNotFoundError(2, "No rollout source exists", str(plain))


def _fingerprint(path: Path, source_stat: os.stat_result) -> SourceFingerprint:
    return SourceFingerprint(
        path, source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
        source_stat.st_mtime_ns, source_stat.st_ctime_ns,
    )


def source_is_current(fingerprint: SourceFingerprint) -> bool:
    """Check the selected representation and opened source identity.

    Absence means stale. Other filesystem errors remain errors.
    """
    try:
        if resolve_rollout(fingerprint.physical_path) != fingerprint.physical_path:
            return False
        with fingerprint.physical_path.open("rb") as source:
            return _fingerprint(fingerprint.physical_path, os.fstat(source.fileno())) == fingerprint
    except FileNotFoundError:
        return False


def compressed_source_unchanged(path: Path, mtime: float, compressed_size: int | None) -> bool:
    """Compare an imported compressed source with its stored size and mtime."""
    if path.suffix != ".zst" or compressed_size is None:
        return False
    source_stat = path.stat()
    return source_stat.st_size == compressed_size and source_stat.st_mtime == mtime


def _check_stop(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise InterruptedError("Rollout preparation cancelled")


def _decoded_chunks(source: BinaryIO, stop: threading.Event | None) -> Iterator[bytes]:
    """Decode every frame, with bounded input and strict physical EOF checks."""
    decompressor = zstandard.ZstdDecompressor(max_window_size=_MAX_WINDOW_SIZE)
    decoder = None
    while True:
        _check_stop(stop)
        chunk = source.read(_COMPRESSED_CHUNK_SIZE)
        if not chunk:
            if decoder is None or not decoder.eof:
                raise RolloutSourceError("Incomplete compressed rollout frame")
            return
        while chunk:
            _check_stop(stop)
            if decoder is None or decoder.eof:
                decoder = decompressor.decompressobj()
            output = decoder.decompress(chunk)
            if output:
                yield output
            chunk = decoder.unused_data


def prepare_rollout(path: Path, *, stop: threading.Event | None = None) -> PreparedRollout:
    """Provide a stable plain source or a private, fully validated decoded file."""
    _check_stop(stop)
    physical_path = resolve_rollout(path)
    temporary_path = None
    try:
        with physical_path.open("rb") as source:
            source_stat = os.fstat(source.fileno())
            if not stat.S_ISREG(source_stat.st_mode):
                raise RolloutSourceError("Rollout source is not a regular file")
            fingerprint = _fingerprint(physical_path, source_stat)
            if physical_path.suffix != ".zst":
                return PreparedRollout(
                    physical_path, physical_path, source_stat.st_size,
                    source_stat.st_mtime, fingerprint,
                )
            with tempfile.NamedTemporaryFile(prefix="twicc-rollout-", suffix=".jsonl", delete=False) as target:
                temporary_path = Path(target.name)
                logical_size = 0
                for chunk in _decoded_chunks(source, stop):
                    target.write(chunk)
                    logical_size += len(chunk)
            _check_stop(stop)
            if _fingerprint(physical_path, os.fstat(source.fileno())) != fingerprint or not source_is_current(fingerprint):
                raise RolloutSourceError("Rollout source changed during preparation")
            os.utime(temporary_path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        prepared = PreparedRollout(
            physical_path, temporary_path, logical_size, source_stat.st_mtime,
            fingerprint, temporary=True,
        )
        temporary_path = None
        return prepared
    except zstandard.ZstdError as error:
        raise RolloutSourceError("Invalid compressed rollout") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_rollout_meta(path: Path) -> dict | None:
    """Return the first JSON object without certifying the remaining stream.

    The caller retains identity and classification rules. Missing, unreadable,
    undecodable and non-object first records return None, like existing metadata
    discovery. Compressed reads request one decoded byte at a time so later
    records, checksums and frame endings need not be decoded.
    """
    try:
        physical_path = resolve_rollout(path)
        with physical_path.open("rb") as source:
            if physical_path.suffix == ".zst":
                decompressor = zstandard.ZstdDecompressor(max_window_size=_MAX_WINDOW_SIZE)
                with decompressor.stream_reader(source, read_size=_COMPRESSED_CHUNK_SIZE) as reader:
                    first_line = bytearray()
                    while byte := reader.read(1):
                        first_line.extend(byte)
                        if byte == b"\n":
                            break
            else:
                first_line = source.readline()
        parsed = orjson.loads(first_line)
        return parsed if isinstance(parsed, dict) else None
    except (OSError, zstandard.ZstdError, orjson.JSONDecodeError):
        return None


def compressed_backlog_hint(path: Path, offset: int) -> bool | None:
    """Prove positive backlog from the first frame header, never prove EOF.

    A declared first-frame size above the logical offset returns True. All
    other results return None: more frames can follow, even after a known-size
    frame. Read and header errors also return None. No decoding occurs here.
    """
    try:
        physical_path = resolve_rollout(path)
        if physical_path.suffix != ".zst":
            return None
        with physical_path.open("rb") as source:
            parameters = zstandard.get_frame_parameters(source.read(18))
        if parameters.content_size not in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR):
            if parameters.content_size > offset:
                return True
    except (OSError, zstandard.ZstdError):
        pass
    return None
