"""Confined inline reads and counted copies use handles throughout."""

import os
import socket
import stat
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from twicc import paths
from twicc.inline_artifacts.files import (
    MAX_INLINE_EXPORT_BYTES,
    InlineArtifactUnavailable,
    copy_source_artifact,
    open_export_asset,
    open_source_asset,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    folder = root / "session" / "inline-artifacts" / "preferences"
    folder.mkdir(parents=True)
    (folder / "index.html").write_bytes(b"<p>preferences</p>")
    monkeypatch.setattr(paths, "get_artifacts_dir", lambda: root)
    return folder


@pytest.fixture
def publication():
    return {"artifact_id": "preferences", "src": "inline-artifacts/preferences/index.html"}


def test_source_and_export_handles(source, tmp_path, publication):
    (source / "app.js").write_bytes(b"console.log(1)")
    (source / "data").mkdir()
    (source / "data" / "saved.json").write_bytes(b"{}")
    destination = tmp_path / "copy"
    assert copy_source_artifact("session", publication, destination, MAX_INLINE_EXPORT_BYTES) == 34
    with open_source_asset("session", "preferences", "app.js") as file:
        assert file.read() == b"console.log(1)"
    with open_export_asset(destination, "data/saved.json") as file:
        assert file.read() == b"{}"


def test_configured_artifacts_root_symlink(source, tmp_path, monkeypatch):
    linked = tmp_path / "linked"
    linked.symlink_to(source.parents[2], target_is_directory=True)
    monkeypatch.setattr(paths, "get_artifacts_dir", lambda: linked)
    with open_source_asset("session", "preferences", "index.html") as file:
        assert file.read() == b"<p>preferences</p>"


@pytest.mark.parametrize(
    "asset", ["../other/index.html", "../../index.html", "/index.html", "a/../index.html", "a\\b", "a\x00b", "missing"]
)
def test_source_rejects_unsafe_or_missing_assets(source, asset):
    with pytest.raises(InlineArtifactUnavailable, match="unavailable"):
        open_source_asset("session", "preferences", asset)


@pytest.mark.parametrize(
    "session,artifact", [("../other", "preferences"), ("session", "../other"), ("session", "Preferences")]
)
def test_source_rejects_sibling_identity(source, session, artifact):
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset(session, artifact, "index.html")


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "fifo", "socket"])
def test_source_and_copy_reject_unsafe_entries(source, tmp_path, publication, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"OUTSIDE")
    entry = source / "unsafe"
    sock = None
    if kind == "file_link":
        entry.symlink_to(outside / "secret")
    elif kind == "directory_link":
        entry.symlink_to(outside, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(entry)
    else:
        sock = socket.socket(socket.AF_UNIX)
        with tempfile.TemporaryDirectory(prefix="ia-socket-") as temporary:
            short_path = os.path.join(temporary, "socket")
            sock.bind(short_path)
            os.rename(short_path, entry)
    try:
        with pytest.raises(InlineArtifactUnavailable):
            open_source_asset("session", "preferences", "unsafe")
        with pytest.raises(InlineArtifactUnavailable):
            copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)
    finally:
        if sock:
            sock.close()


def test_copy_requires_entry_file(source, tmp_path, publication):
    (source / "index.html").unlink()
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)


def test_copy_counts_actual_bytes_and_shared_budget(source, tmp_path, publication):
    (source / "index.html").write_bytes(b"abcd")
    assert copy_source_artifact("session", publication, tmp_path / "first", 8) == 4
    assert copy_source_artifact("session", publication, tmp_path / "second", 4) == 4
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "third", 3)


def test_exact_aggregate_export_limit(source, tmp_path, publication):
    assert MAX_INLINE_EXPORT_BYTES == 200 * 1024 * 1024
    with (source / "index.html").open("wb") as file:
        file.truncate(MAX_INLINE_EXPORT_BYTES)
    assert (
        copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)
        == MAX_INLINE_EXPORT_BYTES
    )
    (source / "extra").write_bytes(b"x")
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "excess", MAX_INLINE_EXPORT_BYTES)


def test_growth_after_open_still_obeys_copy_budget(source, tmp_path, publication, monkeypatch):
    (source / "index.html").write_bytes(b"abcd")
    original = os.read
    grown = False

    def grow(fd, count):
        nonlocal grown
        if not grown:
            grown = True
            with (source / "index.html").open("ab") as file:
                file.write(b"extra")
        return original(fd, count)

    monkeypatch.setattr(os, "read", grow)
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "copy", 4)
    assert (tmp_path / "copy" / "index.html").stat().st_size <= 4


@pytest.mark.parametrize("operation", ["read", "copy", "export"])
def test_directory_replacement_cannot_read_outside(source, tmp_path, publication, monkeypatch, operation):
    inner = source / "nested"
    inner.mkdir()
    (inner / "app.js").write_bytes(b"INSIDE")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.js").write_bytes(b"OUTSIDE")
    barrier = threading.Barrier(2)
    original = os.open

    paused = False

    def paused_open(path, flags, *args, **kwargs):
        nonlocal paused
        if not paused and path == "app.js" and flags & os.O_ACCMODE == os.O_RDONLY and kwargs.get("dir_fd") is not None:
            paused = True
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", paused_open)

    def run():
        try:
            if operation == "copy":
                copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)
                return (tmp_path / "copy" / "nested" / "app.js").read_bytes()
            opener = (
                (lambda: open_export_asset(source, "nested/app.js"))
                if operation == "export"
                else (lambda: open_source_asset("session", "preferences", "nested/app.js"))
            )
            with opener() as file:
                return file.read()
        except InlineArtifactUnavailable:
            return b"REFUSED"

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run)
        barrier.wait(timeout=5)
        inner.rename(source / "original-nested")
        inner.symlink_to(outside, target_is_directory=True)
        barrier.wait(timeout=5)
        assert future.result(timeout=5) in (b"INSIDE", b"REFUSED")


def test_unsupported_safe_handles_fail_closed(source, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "index.html")


@pytest.mark.parametrize("level", ["session", "inline-artifacts", "artifact"])
def test_descendant_directory_symlinks_are_unavailable(source, tmp_path, level):
    target = source if level == "artifact" else source.parent if level == "inline-artifacts" else source.parents[1]
    moved = tmp_path / "moved"
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=True)
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "index.html")


def test_export_rejects_nested_symlinks(source, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")
    (source / "app.js").symlink_to(outside)
    with pytest.raises(InlineArtifactUnavailable):
        open_export_asset(source, "app.js")


def test_final_component_replacement_is_refused(source, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")
    original = os.open

    def replace(path, flags, *args, **kwargs):
        if path == "index.html" and kwargs.get("dir_fd") is not None:
            (source / "index.html").unlink()
            (source / "index.html").symlink_to(outside)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "index.html")


def test_failed_fstat_closes_new_file_handle(source, monkeypatch):
    original_open = os.open
    original_stat = os.fstat
    opened = []

    def record(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == "index.html":
            opened.append(fd)
        return fd

    def fail(fd):
        if fd in opened:
            raise OSError("failed source metadata")
        return original_stat(fd)

    monkeypatch.setattr(os, "open", record)
    monkeypatch.setattr(os, "fstat", fail)
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "index.html")
    assert len(opened) == 1
    with pytest.raises(OSError):
        original_stat(opened[0])


def test_swapped_fifo_is_refused_without_blocking(source, monkeypatch):
    original = os.open

    def swap(path, flags, *args, **kwargs):
        if path == "index.html":
            assert flags & os.O_NONBLOCK
            (source / "index.html").unlink()
            os.mkfifo(source / "index.html")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "index.html")


def test_device_entry_is_unavailable(source, tmp_path, publication):
    try:
        os.mknod(source / "device", 0o600 | stat.S_IFCHR, os.makedev(1, 3))
    except (PermissionError, NotImplementedError):
        pytest.skip("The test host does not permit creation of device nodes")
    with pytest.raises(InlineArtifactUnavailable):
        open_source_asset("session", "preferences", "device")
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)


def test_copy_refuses_entry_removed_after_validation(source, tmp_path, publication, monkeypatch):
    original = os.listdir
    removed = False

    def remove_entry(fd):
        nonlocal removed
        if not removed:
            removed = True
            (source / "index.html").unlink()
        return original(fd)

    monkeypatch.setattr(os, "listdir", remove_entry)
    with pytest.raises(InlineArtifactUnavailable):
        copy_source_artifact("session", publication, tmp_path / "copy", MAX_INLINE_EXPORT_BYTES)
