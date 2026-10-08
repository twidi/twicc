"""Open and copy inline files beneath pinned, no-follow directory handles.

Only the configured artifacts root can be a symlink. Every component below
that trusted root uses descriptor-relative operations. Platforms without those
primitives fail closed instead of falling back to pathname checks.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from twicc import paths

MAX_INLINE_EXPORT_BYTES = 200 * 1024 * 1024
_COPY_CHUNK_BYTES = 64 * 1024
_OPEN_DIR_FD_SUPPORTED = os.open in os.supports_dir_fd
_ARTIFACT_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class InlineArtifactUnavailable(Exception):
    """Stable failure that never discloses an absolute source path."""

    def __init__(self):
        super().__init__("Inline artifact unavailable")


def _require_safe_handles():
    if not _OPEN_DIR_FD_SUPPORTED or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise InlineArtifactUnavailable()


def _parts(asset_path: str, *, directory: bool = False) -> list[str]:
    if not isinstance(asset_path, str) or any(char in asset_path for char in "\\\x00"):
        raise InlineArtifactUnavailable()
    if directory:
        asset_path = asset_path.removesuffix("/")
    parts = asset_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise InlineArtifactUnavailable()
    return parts


def _source_parts(session_id: str, artifact_id: str) -> list[str]:
    if len(_parts(session_id)) != 1 or not _ARTIFACT_ID.fullmatch(artifact_id):
        raise InlineArtifactUnavailable()
    return [session_id, "inline-artifacts", artifact_id]


def _open_directory(name, *, parent: int | None = None) -> int:
    _require_safe_handles()
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


@contextmanager
def _directory_at(root_fd: int, parts: list[str], *, create: bool = False) -> Iterator[int]:
    current = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=current)
                except FileExistsError:
                    pass
            following = _open_directory(part, parent=current)
            os.close(current)
            current = following
        yield current
    finally:
        os.close(current)


@contextmanager
def source_artifact_directory(session_id: str, artifact_id: str) -> Iterator[int]:
    """Yield a pinned artifact directory. Never use its original path again."""
    components = _source_parts(session_id, artifact_id)
    _require_safe_handles()
    try:
        # The root is configured by the owner; its symlink is supported.
        resolved_root = paths.get_artifacts_dir().resolve(strict=True)
        root_fd = _open_directory(resolved_root)
        try:
            with _directory_at(root_fd, components) as artifact_fd:
                yield artifact_fd
        finally:
            os.close(root_fd)
    except (OSError, ValueError, RuntimeError):
        raise InlineArtifactUnavailable() from None


def _open_regular(parent_fd: int, name: str) -> int:
    # Precheck avoids opening existing special files. fstat handles replacement
    # between this precheck and open. NONBLOCK prevents a swapped FIFO blocking.
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise InlineArtifactUnavailable()
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise InlineArtifactUnavailable()
        return file_fd
    except BaseException:
        os.close(file_fd)
        raise


def _open_asset(root_fd: int, asset_path: str) -> BinaryIO:
    parts = _parts(asset_path)
    with _directory_at(root_fd, parts[:-1]) as parent:
        file_fd = _open_regular(parent, parts[-1])
    try:
        return os.fdopen(file_fd, "rb")
    except BaseException:
        os.close(file_fd)
        raise


def open_source_asset(session_id: str, artifact_id: str, asset_path: str) -> BinaryIO:
    """Return a confined regular-file handle, owned by the caller."""
    try:
        with source_artifact_directory(session_id, artifact_id) as root_fd:
            return _open_asset(root_fd, asset_path)
    except (OSError, ValueError):
        raise InlineArtifactUnavailable() from None


@contextmanager
def export_artifact_directory(export_root: Path) -> Iterator[int]:
    """Pin a leased immutable copy directory without following its symlink."""
    try:
        root_fd = _open_directory(export_root)
        try:
            yield root_fd
        finally:
            os.close(root_fd)
    except (OSError, ValueError):
        raise InlineArtifactUnavailable() from None


def open_export_asset(export_root: Path, asset_path: str) -> BinaryIO:
    """Return a confined export handle without following nested symlinks."""
    _require_safe_handles()
    try:
        root_fd = _open_directory(export_root)
        try:
            return _open_asset(root_fd, asset_path)
        finally:
            os.close(root_fd)
    except (OSError, ValueError):
        raise InlineArtifactUnavailable() from None


def _copy_directory(source_fd: int, destination_fd: int, remaining_bytes: int, *, exclude=()) -> int:
    copied = 0
    for name in sorted(os.listdir(source_fd)):
        if name in exclude:
            continue
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            with _directory_at(source_fd, [name]) as child_source:
                os.mkdir(name, dir_fd=destination_fd)
                with _directory_at(destination_fd, [name]) as child_destination:
                    copied += _copy_directory(child_source, child_destination, remaining_bytes - copied)
        elif stat.S_ISREG(metadata.st_mode):
            source_file = _open_regular(source_fd, name)
            try:
                destination_file = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination_fd
                )
                try:
                    while True:
                        chunk = os.read(source_file, min(_COPY_CHUNK_BYTES, remaining_bytes - copied + 1))
                        if not chunk:
                            break
                        if len(chunk) > remaining_bytes - copied:
                            raise InlineArtifactUnavailable()
                        view = memoryview(chunk)
                        while view:
                            written = os.write(destination_file, view)
                            if not written:
                                raise InlineArtifactUnavailable()
                            view = view[written:]
                        copied += len(chunk)
                finally:
                    os.close(destination_file)
            finally:
                os.close(source_file)
        else:
            raise InlineArtifactUnavailable()
    return copied


def copy_source_artifact(session_id: str, publication: dict, destination: Path, remaining_bytes: int) -> int:
    """Copy one folder and count bytes read, including optional saved data.

    The caller provides a new staging destination and removes it on failure.
    Call from asyncio.to_thread, outside database locks. Success never exceeds
    the supplied remaining aggregate budget or the 200 MiB share limit.
    """
    _require_safe_handles()
    try:
        artifact_id = publication["artifact_id"]
        source = _parts(publication["src"])
        if (
            len(source) != 3
            or source[:2] != ["inline-artifacts", artifact_id]
            or not source[-1].endswith((".html", ".htm"))
            or not isinstance(remaining_bytes, int)
            or remaining_bytes < 0
        ):
            raise InlineArtifactUnavailable()
        budget = min(remaining_bytes, MAX_INLINE_EXPORT_BYTES)
        with source_artifact_directory(session_id, artifact_id) as source_fd:
            entry_fd = _open_regular(source_fd, source[-1])
            os.close(entry_fd)
            destination.mkdir()
            destination_fd = _open_directory(destination)
            try:
                copied = _copy_directory(source_fd, destination_fd, budget)
                # Source edits can remove the entry after its initial check.
                # Never return a successful staged copy without its document.
                copied_entry = _open_regular(destination_fd, source[-1])
                os.close(copied_entry)
                return copied
            finally:
                os.close(destination_fd)
    except (OSError, ValueError, KeyError, TypeError):
        raise InlineArtifactUnavailable() from None


def copy_export_with_source_data(
    export_root: Path, session_id: str, artifact_id: str, destination: Path, remaining_bytes: int,
) -> int:
    """Keep published code/assets and replace only optional source data.

    Both trees use pinned no-follow handles. The caller owns staging cleanup
    and holds a lease on the published copy until this worker finishes.
    """
    try:
        if remaining_bytes < 0:
            raise InlineArtifactUnavailable()
        published_fd = _open_directory(export_root)
        try:
            destination.mkdir()
            destination_fd = _open_directory(destination)
            try:
                copied = _copy_directory(published_fd, destination_fd, remaining_bytes, exclude=('data',))
                with source_artifact_directory(session_id, artifact_id) as source_fd:
                    try:
                        metadata = os.stat('data', dir_fd=source_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        return copied
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise InlineArtifactUnavailable()
                    with _directory_at(source_fd, ['data']) as data_fd:
                        os.mkdir('data', dir_fd=destination_fd)
                        with _directory_at(destination_fd, ['data']) as target_fd:
                            copied += _copy_directory(data_fd, target_fd, remaining_bytes - copied)
                return copied
            finally:
                os.close(destination_fd)
        finally:
            os.close(published_fd)
    except (OSError, ValueError):
        raise InlineArtifactUnavailable() from None
