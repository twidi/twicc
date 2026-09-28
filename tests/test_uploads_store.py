"""Upload staging area and metadata store (design 2026-09-28-file-upload
§5.1, §5.2, §5.8): staging dir, name filter, metadata write rules, record
shape and the ``upload_state`` broadcast."""

from __future__ import annotations

import asyncio
import errno
import os
import stat
from unittest.mock import patch

import orjson
import pytest

from twicc import atomic_json
from twicc.paths import get_uploads_dir
from twicc.uploads import store
from twicc.uploads.broadcast import broadcast_upload_state


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Point the data dir (so the staging dir) at a per-test directory."""
    path = tmp_path / "data"
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


class FakeLayer:
    """Records every ``group_send``."""

    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))


@pytest.fixture
def layer():
    fake = FakeLayer()
    with patch("twicc.uploads.broadcast.get_channel_layer", return_value=fake):
        yield fake


def _new_upload(size=10, **overrides):
    """Create ``<id>.part`` (empty) then ``<id>.json``, as creation does."""
    upload_id = store.new_upload_id()
    store.part_path(upload_id).touch()
    fields = {
        "client_id": "tab:0123456789abcdef",
        "size": size,
        "filename": "a.txt",
        "target_dir": "/tmp/target",
        "scope": {"kind": "standalone", "root": None},
        "origin": {"panel": "files", "key": "project:p"},
        "fingerprint": "fp",
    }
    fields.update(overrides)
    meta = store.create_metadata(upload_id, **fields)
    return upload_id, meta


def _append(upload_id, data):
    with open(store.part_path(upload_id), "ab") as f:
        f.write(data)


def _disk_json(upload_id):
    return orjson.loads(store.metadata_path(upload_id).read_bytes())


def _enospc(*args, **kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


# ── Staging dir ───────────────────────────────────────────────────────────────


def test_uploads_dir_is_under_the_data_dir(data_dir):
    assert get_uploads_dir() == data_dir.resolve() / "uploads"


def test_staging_dir_created_with_mode_0700(data_dir):
    old_umask = os.umask(0o022)
    try:
        path = store.get_staging_dir()
    finally:
        os.umask(old_umask)
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_part_and_metadata_paths_reject_invalid_ids():
    for bad in ("", "../x", "A" * 32, "0" * 31, "g" * 32):
        with pytest.raises(ValueError):
            store.part_path(bad)
        with pytest.raises(ValueError):
            store.metadata_path(bad)


def test_is_valid_upload_id():
    assert store.is_valid_upload_id(store.new_upload_id())
    assert not store.is_valid_upload_id("0" * 31)
    assert not store.is_valid_upload_id("F" * 32)
    assert not store.is_valid_upload_id(None)


def test_is_in_staging_dir_uses_realpath(tmp_path):
    staging = store.get_staging_dir()
    assert store.is_in_staging_dir(str(staging))
    assert store.is_in_staging_dir(str(staging / "sub"))
    link = tmp_path / "link-to-staging"
    link.symlink_to(staging)
    assert store.is_in_staging_dir(str(link))
    assert not store.is_in_staging_dir(str(tmp_path))
    # A sibling whose name starts with the staging dir name is not inside it.
    assert not store.is_in_staging_dir(str(staging) + "-other")


# ── Error numbers (§5.1) ──────────────────────────────────────────────────────


def test_disk_full_and_failure_code():
    assert store.is_disk_full(OSError(errno.ENOSPC, "x"))
    assert store.is_disk_full(OSError(errno.EDQUOT, "x"))
    assert not store.is_disk_full(OSError(errno.EACCES, "x"))
    assert not store.is_disk_full(ValueError())
    assert store.failure_code(OSError(errno.ENOSPC, "x")) == 507
    assert store.failure_code(OSError(errno.EIO, "x")) == 500
    assert store.failure_code(RuntimeError()) == 500


def test_target_refusal(tmp_path):
    existing = str(tmp_path)
    gone = str(tmp_path / "gone")
    for code in (errno.EACCES, errno.EROFS, errno.EINVAL, errno.EILSEQ, errno.ENAMETOOLONG, errno.EFBIG):
        assert store.is_target_refusal(OSError(code, "x"), existing)
    # ENOENT / ENOTDIR: a refusal only when the target dir is gone.
    assert store.is_target_refusal(OSError(errno.ENOENT, "x"), gone)
    assert store.is_target_refusal(OSError(errno.ENOTDIR, "x"), gone)
    assert not store.is_target_refusal(OSError(errno.ENOENT, "x"), existing)
    assert not store.is_target_refusal(OSError(errno.ENOSPC, "x"), existing)
    assert not store.is_target_refusal(OSError(errno.EPERM, "x"), existing)
    assert not store.is_target_refusal(ValueError(), existing)


# ── Name filter and unparsable files ─────────────────────────────────────────


def test_classify_staging_name():
    upload_id = "0123456789abcdef0123456789abcdef"
    assert store.classify_staging_name(f"{upload_id}.json") == ("json", upload_id)
    assert store.classify_staging_name(f"{upload_id}.part") == ("part", upload_id)
    assert store.classify_staging_name(f"{upload_id}.json.abc_12.tmp") == ("json_tmp", upload_id)
    for other in (
        f"{upload_id}.json.tmp",
        f"{upload_id}.txt",
        f"{upload_id.upper()}.json",
        f"{upload_id[:-1]}.json",
        f"x{upload_id}.json",
        f"{upload_id}.part.bak",
        ".DS_Store",
        "notes.json",
    ):
        assert store.classify_staging_name(other) is None, other


def test_read_metadata_missing_returns_none():
    assert store.read_metadata(store.new_upload_id()) is None


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{\"id\": ",
        b"[]",
        b"{}",
        b"{\"id\": \"other\", \"version\": 1, \"state\": \"active\"}",
        b"{\"id\": \"@ID@\", \"version\": true, \"state\": \"active\"}",
        b"{\"id\": \"@ID@\", \"version\": 1, \"state\": \"weird\"}",
    ],
)
def test_read_metadata_unparsable(content):
    upload_id = store.new_upload_id()
    store.metadata_path(upload_id).write_bytes(content.replace(b"@ID@", upload_id.encode()))
    with pytest.raises(store.UnparsableMetadataError):
        store.read_metadata(upload_id)


def test_list_metadata_applies_the_name_filter_and_skips_unparsable():
    good_id, _ = _new_upload()
    staging = store.get_staging_dir()
    bad_id = store.new_upload_id()
    (staging / f"{bad_id}.json").write_bytes(b"{truncated")
    (staging / f"{bad_id}.part").write_bytes(b"xx")
    # Names outside the filter, even with valid JSON, are ignored.
    (staging / "other.json").write_bytes(orjson.dumps({"id": "x", "version": 1, "state": "active"}))
    (staging / f"{good_id}.json.abc.tmp").write_bytes(b"{}")
    (staging / "sub").mkdir()

    records = store.list_metadata()
    assert [meta["id"] for meta in records] == [good_id]


def test_list_metadata_skips_a_file_that_disappears(monkeypatch):
    kept_id, _ = _new_upload()
    gone_id, _ = _new_upload()
    real_read = store.read_metadata

    def racing_read(upload_id):
        if upload_id == gone_id:
            store.metadata_path(gone_id).unlink()
        return real_read(upload_id)

    monkeypatch.setattr(store, "read_metadata", racing_read)
    assert [meta["id"] for meta in store.list_metadata()] == [kept_id]


def test_update_metadata_refuses_missing_and_unparsable():
    with pytest.raises(store.UploadNotFoundError):
        store.update_metadata(store.new_upload_id(), state="cancelled")
    upload_id = store.new_upload_id()
    store.metadata_path(upload_id).write_bytes(b"nope")
    with pytest.raises(store.UnparsableMetadataError):
        store.update_metadata(upload_id, state="cancelled")
    assert store.metadata_path(upload_id).read_bytes() == b"nope"


# ── Creation ─────────────────────────────────────────────────────────────────


def test_create_metadata_initial_fields():
    upload_id, meta = _new_upload(size=42)
    assert meta == _disk_json(upload_id)
    assert meta["state"] == "active"
    assert meta["version"] == 1
    assert meta["offset"] == 0
    assert meta["size"] == 42
    assert meta["created_at"] == meta["updated_at"] == meta["last_transfer_at"]
    for field in ("final_path", "final_method", "final_source", "error", "finalize_error_code", "finalize_failed_at"):
        assert meta[field] is None


def test_create_metadata_requires_the_part_first_and_no_json():
    upload_id = store.new_upload_id()
    kwargs = {
        "client_id": "c",
        "size": 1,
        "filename": "a",
        "target_dir": "/t",
        "scope": {"kind": "standalone", "root": None},
        "origin": {"panel": "files", "key": "k"},
        "fingerprint": "f",
    }
    with pytest.raises(FileNotFoundError):
        store.create_metadata(upload_id, **kwargs)
    assert not store.metadata_path(upload_id).exists()
    store.part_path(upload_id).touch()
    store.create_metadata(upload_id, **kwargs)
    with pytest.raises(FileExistsError):
        store.create_metadata(upload_id, **kwargs)


# ── Metadata write rules (§5.2) ──────────────────────────────────────────────


def test_version_comes_from_disk():
    upload_id, _ = _new_upload()
    first = store.update_metadata(upload_id, state="finalizing")
    assert first["version"] == 2
    # Another writer bumped the version on disk: the next write follows the disk.
    on_disk = _disk_json(upload_id)
    on_disk["version"] = 10
    store.metadata_path(upload_id).write_bytes(orjson.dumps(on_disk))
    assert store.update_metadata(upload_id, state="active")["version"] == 11


def test_failed_write_changes_nothing_and_uses_no_version():
    upload_id, _ = _new_upload()
    store.update_metadata(upload_id, state="finalizing")
    before = store.metadata_path(upload_id).read_bytes()

    with patch.object(atomic_json.os, "replace", _enospc), pytest.raises(OSError) as info:
        store.update_metadata(upload_id, state="completed", final_path="/t/a.txt")
    assert store.is_disk_full(info.value)
    assert store.failure_code(info.value) == 507

    assert store.metadata_path(upload_id).read_bytes() == before
    # No atomic_write_json temp file left behind.
    assert [name for name in os.listdir(store.get_staging_dir()) if name.endswith(".tmp")] == []
    # The failed write used no version number.
    assert store.update_metadata(upload_id, state="completed")["version"] == 3


def test_updated_at_changes_on_every_write():
    upload_id, meta = _new_upload()
    with patch.object(store, "now_iso", return_value="2099-01-01T00:00:00+00:00"):
        after = store.update_metadata(upload_id, state="finalizing")
    assert after["updated_at"] == "2099-01-01T00:00:00+00:00"
    assert after["created_at"] == meta["created_at"]


def test_non_terminal_write_resyncs_offset_from_part():
    upload_id, _ = _new_upload(size=10)
    _append(upload_id, b"abcd")
    # The append sync failed: the metadata offset is stale.
    with patch.object(atomic_json.os, "replace", _enospc), pytest.raises(OSError):
        store.update_metadata(upload_id, last_transfer_at=store.now_iso())
    assert _disk_json(upload_id)["offset"] == 0
    # The next write (whatever it changes) corrects it.
    _append(upload_id, b"ef")
    meta = store.update_metadata(upload_id, state="finalizing")
    assert meta["offset"] == 6


def test_non_terminal_write_without_part_keeps_offset():
    upload_id, _ = _new_upload(size=10)
    _append(upload_id, b"abc")
    store.update_metadata(upload_id, last_transfer_at=store.now_iso())
    store.part_path(upload_id).unlink()
    assert store.update_metadata(upload_id, state="finalizing")["offset"] == 3


def test_terminal_write_keeps_the_last_offset():
    upload_id, _ = _new_upload(size=10)
    _append(upload_id, b"abc")
    store.update_metadata(upload_id, last_transfer_at=store.now_iso())
    _append(upload_id, b"defg")  # bytes after the last sync, then a cancel
    meta = store.update_metadata(upload_id, state="cancelled")
    assert meta["offset"] == 3


def test_state_change_clears_the_finalize_error_fields():
    upload_id, _ = _new_upload()
    store.update_metadata(upload_id, state="finalizing")
    failed_at = store.now_iso()
    meta = store.update_metadata(
        upload_id, state="active", error="disk full", finalize_error_code=507, finalize_failed_at=failed_at
    )
    assert (meta["error"], meta["finalize_error_code"], meta["finalize_failed_at"]) == ("disk full", 507, failed_at)
    # A write that does not change the state keeps them.
    meta = store.update_metadata(upload_id, last_transfer_at=store.now_iso())
    assert meta["finalize_error_code"] == 507
    # The next state change clears all three.
    meta = store.update_metadata(upload_id, state="finalizing")
    assert (meta["error"], meta["finalize_error_code"], meta["finalize_failed_at"]) == (None, None, None)


def test_update_refuses_unknown_fields_and_states():
    upload_id, _ = _new_upload()
    for changes in ({"version": 5}, {"offset": 3}, {"id": "x"}, {"size": 1}, {"state": "paused"}):
        with pytest.raises(ValueError):
            store.update_metadata(upload_id, **changes)
    assert _disk_json(upload_id)["version"] == 1


def test_remove_best_effort(tmp_path, caplog):
    path = tmp_path / "f"
    path.write_bytes(b"x")
    store.remove_best_effort(path)
    assert not path.exists()
    store.remove_best_effort(path)  # ENOENT ignored
    directory = tmp_path / "d"
    directory.mkdir()
    store.remove_best_effort(directory)  # other error: logged, not raised
    assert directory.exists()
    assert "cannot remove" in caplog.text


# ── Record shape (§5.8) ──────────────────────────────────────────────────────

RECORD_KEYS = {
    "id",
    "client_id",
    "state",
    "version",
    "filename",
    "target_dir",
    "size",
    "offset",
    "origin",
    "fingerprint",
    "final_path",
    "error",
    "created_at",
    "updated_at",
}


def test_record_shape():
    upload_id, meta = _new_upload(size=10)
    record = store.build_record(meta)
    assert set(record) == RECORD_KEYS
    assert record["id"] == upload_id
    assert record["origin"] == {"panel": "files", "key": "project:p"}
    assert record["final_path"] is None
    assert record["error"] is None


def test_record_final_path_only_for_completed():
    upload_id, _ = _new_upload(size=0)
    finalizing = store.update_metadata(upload_id, state="finalizing")
    committed = store.update_metadata(upload_id, final_path="/t/a.txt", final_method="replace", final_source="part")
    assert store.build_record(committed)["final_path"] is None
    completed = store.update_metadata(upload_id, state="completed")
    record = store.build_record(completed)
    assert record["final_path"] == "/t/a.txt"
    assert "final_method" not in record and "final_source" not in record and "scope" not in record
    assert finalizing["version"] < committed["version"] < completed["version"]


# ── upload_state broadcast ───────────────────────────────────────────────────


async def _write_and_broadcast(upload_id, **changes):
    """The step pattern of §5.2: the blocking write in a thread, then a
    broadcast of the record it persisted. A failed write raises first."""
    meta = await asyncio.to_thread(store.update_metadata, upload_id, **changes)
    await broadcast_upload_state(meta)
    return meta


def test_broadcast_message_shape(layer):
    upload_id, meta = _new_upload()
    asyncio.run(broadcast_upload_state(meta))
    assert layer.sent == [
        ("updates", {"type": "broadcast", "data": {"type": "upload_state", "upload": store.build_record(meta)}})
    ]


def test_broadcast_without_channel_layer_is_a_noop():
    _, meta = _new_upload()
    with patch("twicc.uploads.broadcast.get_channel_layer", return_value=None):
        asyncio.run(broadcast_upload_state(meta))


def test_one_broadcast_per_persisted_write_with_increasing_version(layer):
    upload_id, meta = _new_upload(size=10)

    async def scenario():
        await broadcast_upload_state(meta)
        _append(upload_id, b"abcd")
        await _write_and_broadcast(upload_id, last_transfer_at=store.now_iso())
        # A failed write: nothing persisted, nothing broadcast.
        with patch.object(atomic_json.os, "replace", _enospc), pytest.raises(OSError):
            await _write_and_broadcast(upload_id, state="cancelled")
        await _write_and_broadcast(upload_id, state="cancelled")

    asyncio.run(scenario())

    uploads = [message["data"]["upload"] for _, message in layer.sent]
    assert [u["version"] for u in uploads] == [1, 2, 3]
    assert [u["state"] for u in uploads] == ["active", "active", "cancelled"]
    assert [u["offset"] for u in uploads] == [0, 4, 4]
    # Every broadcast record is the persisted one.
    assert uploads[-1] == store.build_record(_disk_json(upload_id))


def test_cancel_after_failed_append_sync_outranks_every_broadcast(layer):
    upload_id, meta = _new_upload(size=10)

    async def scenario():
        await broadcast_upload_state(meta)
        _append(upload_id, b"abc")
        await _write_and_broadcast(upload_id, last_transfer_at=store.now_iso())
        _append(upload_id, b"de")
        with patch.object(atomic_json.os, "replace", _enospc), pytest.raises(OSError):
            await _write_and_broadcast(upload_id, last_transfer_at=store.now_iso())
        return await _write_and_broadcast(upload_id, state="cancelled")

    cancelled = asyncio.run(scenario())
    versions = [message["data"]["upload"]["version"] for _, message in layer.sent]
    assert cancelled["version"] > max(versions[:-1])
    assert versions == sorted(set(versions))
