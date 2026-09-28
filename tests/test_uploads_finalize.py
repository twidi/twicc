"""Finalization and recovery of the uploads, the ``HEAD`` gates, and the
``ArtifactsWatcher`` temp-file filter (design 2026-09-28-file-upload §5.3
``HEAD`` / ``PATCH`` answers, §5.6, §5.7).

Crashes are simulated by raising at the right step, then running recovery
(through ``HEAD``). Another filesystem is simulated by faking ``st_dev``.
"""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.core.handlers.asgi import ASGIHandler
from django.db import OperationalError

from tests.test_uploads_transfer import (
    AsgiCall,
    FakeLayer,
    _new_upload,
    _patch,
    _patch_headers,
    _request,
    _run,
    _wait_guarded_tasks,
)
from twicc import artifacts_watcher
from twicc.core.models import Project
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views

pytestmark = pytest.mark.django_db(transaction=True)

DATA = b"hello world"


# ── Fixtures (same as tests/test_uploads_transfer.py) ────────────────────────


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def layer(monkeypatch):
    fake = FakeLayer()
    monkeypatch.setattr("twicc.uploads.broadcast.get_channel_layer", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def fresh_upload_locks():
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()
    yield
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()


@pytest.fixture
def app(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return ASGIHandler()


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


# ── Helpers ───────────────────────────────────────────────────────────────────


def _finalize(upload_id):
    """Run the finalization step under the upload's lock (as its callers do)."""

    async def go():
        async with locks.get_upload_lock(upload_id):
            return await upload_views.finalize_upload(upload_id)

    return _run(go())


def _meta(upload_id):
    return store.read_metadata(upload_id)


def _complete_upload(target, data=DATA, filename="a.txt"):
    upload_id = _new_upload(target, size=len(data), content=data)
    if filename != "a.txt":
        meta = store.read_metadata(upload_id)
        meta["filename"] = filename
        store.atomic_write_json(store.metadata_path(upload_id), meta)
    return upload_id


def _temp(upload_id, target):
    return target / f".twicc-upload-{upload_id}.tmp"


def _other_filesystem(monkeypatch, target):
    """Make ``target`` look like another filesystem than the staging dir."""
    real = store._st_dev

    def fake(path):
        dev = real(path)
        return dev + 1 if str(path).startswith(str(target)) else dev

    monkeypatch.setattr(store, "_st_dev", fake)


def _link_raising(monkeypatch, error_number, *, times=None, only_part=False, before=None):
    """``os.link`` raising *error_number* (the first *times* calls, or always)."""
    real = os.link
    calls = []

    def fake(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        if before is not None:
            before()
        failing = times is None or len(calls) <= times
        if failing and (not only_part or str(src).endswith(".part")):
            raise OSError(error_number, os.strerror(error_number))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", fake)
    return calls


def _update_raising(monkeypatch, error_number, predicate, *, times=None):
    """``store.update_metadata`` raising when *predicate(changes)* holds."""
    real = store.update_metadata
    hits = []

    def fake(upload_id, **changes):
        if predicate(changes) and (times is None or len(hits) < times):
            hits.append(changes)
            raise OSError(error_number, os.strerror(error_number))
        return real(upload_id, **changes)

    monkeypatch.setattr(store, "update_metadata", fake)
    return hits


def _is_commit(changes):
    return "final_path" in changes and changes.get("final_path") is not None and "state" not in changes


def _head(app, upload_id):
    return _request(app, "HEAD", upload_id)


def _assert_completed_head(resp, size):
    assert resp.status == 200
    assert resp.headers["upload-offset"] == resp.headers["upload-length"] == str(size)


def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


# ── Free names ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.txt", ["a.txt", "a (1).txt", "a (2).txt"]),
        ("archive.tar.gz", ["archive.tar.gz", "archive.tar (1).gz", "archive.tar (2).gz"]),
        (".env", [".env", ".env (1)", ".env (2)"]),
        ("README", ["README", "README (1)", "README (2)"]),
        (".env.local", [".env.local", ".env (1).local", ".env (2).local"]),
    ],
)
def test_candidate_names(filename, expected):
    names = list(store.candidate_names(filename))
    assert names[:3] == expected
    assert len(names) == 1000
    assert names[-1] == expected[1].replace("(1)", "(999)")


def test_finalize_takes_the_first_free_name(app, target):
    (target / "a.txt").write_bytes(b"old")
    (target / "a (1).txt").write_bytes(b"older")
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    assert (target / "a.txt").read_bytes() == b"old"
    assert (target / "a (1).txt").read_bytes() == b"older"
    assert (target / "a (2).txt").read_bytes() == DATA
    meta = _meta(upload_id)
    assert meta["state"] == "completed"
    assert meta["final_path"] == str(target / "a (2).txt")


def test_no_free_name_is_422_and_failed(app, target):
    for name in store.candidate_names("a.txt"):
        (target / name).write_bytes(b"x")
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 422
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"]
    assert not store.part_path(upload_id).exists()


# ── Same filesystem: link ─────────────────────────────────────────────────────


def test_same_filesystem_links_and_broadcasts(app, target, layer):
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    assert resp.headers["upload-offset"] == str(len(DATA))
    meta = _meta(upload_id)
    assert meta["state"] == "completed"
    assert meta["final_method"] == "link"
    assert meta["offset"] == len(DATA)
    assert (target / "a.txt").read_bytes() == DATA
    assert os.stat(target / "a.txt").st_nlink == 1  # the .part was removed
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()
    records = layer.records()
    assert [r["state"] for r in records] == ["active", "finalizing", "completed"]
    assert records[-1]["final_path"] == str(target / "a.txt")
    versions = [r["version"] for r in records]
    assert versions == sorted(set(versions))


def test_final_file_keeps_the_part_mode(app, target):
    upload_id = _new_upload(target, size=len(DATA))
    part_mode = stat.S_IMODE(os.stat(store.part_path(upload_id)).st_mode)
    assert _patch(app, upload_id, 0, DATA).status == 204
    assert stat.S_IMODE(os.stat(target / "a.txt").st_mode) == part_mode


# ── Other filesystem: copy ────────────────────────────────────────────────────


def test_cross_filesystem_copies_through_a_temp_file(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    opened = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *args, **kwargs):
        opened.append((str(path), flags, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    assert (target / "a.txt").read_bytes() == DATA
    assert not _temp(upload_id, target).exists()
    assert not store.part_path(upload_id).exists()
    meta = _meta(upload_id)
    assert meta["final_method"] == "link"
    tmp_opens = [entry for entry in opened if entry[0] == str(_temp(upload_id, target))]
    assert tmp_opens == [(str(_temp(upload_id, target)), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)]
    assert stat.S_IMODE(os.stat(target / "a.txt").st_mode) == 0o666 & ~_umask()


def test_cross_filesystem_leftover_temp_is_removed_never_written_through(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    upload_id = _new_upload(target, size=len(DATA))
    keep = target / "keep.txt"
    keep.write_bytes(b"previous upload")
    os.link(keep, _temp(upload_id, target))  # a leftover linked to a final name
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    assert keep.read_bytes() == b"previous upload"
    assert (target / "a.txt").read_bytes() == DATA


def test_exdev_on_the_part_goes_to_the_copy_path(app, target, monkeypatch):
    calls = _link_raising(monkeypatch, errno.EXDEV, only_part=True)
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    assert (target / "a.txt").read_bytes() == DATA
    assert calls[0][0] == str(store.part_path(upload_id))
    assert calls[-1][0] == str(_temp(upload_id, target))
    assert not _temp(upload_id, target).exists()
    assert _meta(upload_id)["final_method"] == "link"


def test_cross_filesystem_disk_full_is_507_then_head_finalizes(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    real_free = store._free_bytes
    space = {"target": 0}

    def fake_free(path):
        return space["target"] if str(path).startswith(str(target)) else real_free(path)

    monkeypatch.setattr(store, "_free_bytes", fake_free)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 507
    meta = _meta(upload_id)
    assert meta["state"] == "active"
    assert meta["finalize_error_code"] == 507
    assert meta["error"] and meta["finalize_failed_at"]
    assert store.part_path(upload_id).read_bytes() == DATA
    assert not _temp(upload_id, target).exists()

    # Space still missing: 507 without a copy.
    copies = []
    real_copy = store._copy_to_temp
    monkeypatch.setattr(store, "_copy_to_temp", lambda *a: (copies.append(a), real_copy(*a))[1])
    assert _head(app, upload_id).status == 507
    assert copies == []
    assert _meta(upload_id)["version"] == meta["version"]

    # Space freed: the finalization runs at once.
    space["target"] = 10**12
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert len(copies) == 1
    assert (target / "a.txt").read_bytes() == DATA
    assert _meta(upload_id)["state"] == "completed"


# ── No hard links: reserve + replace ──────────────────────────────────────────


@pytest.mark.parametrize("error_number", [errno.EPERM, errno.ENOTSUP, errno.ENOSYS])
def test_no_hard_link_support_reserves_then_replaces(app, target, monkeypatch, error_number):
    _link_raising(monkeypatch, error_number)
    opened = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *args, **kwargs):
        opened.append((str(path), flags, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)
    (target / "a.txt").write_bytes(b"taken")
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 204
    final = target / "a (1).txt"
    assert final.read_bytes() == DATA
    assert (target / "a.txt").read_bytes() == b"taken"
    meta = _meta(upload_id)
    assert (meta["final_method"], meta["final_source"]) == ("replace", "part")
    assert not store.part_path(upload_id).exists()
    reserve = [entry for entry in opened if entry[0] == str(final)]
    assert reserve == [(str(final), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)]


def test_reserve_eperm_is_a_target_refusal(app, target, monkeypatch):
    _link_raising(monkeypatch, errno.ENOSYS)
    real_open = os.open

    def fake_open(path, flags, *args, **kwargs):
        if str(path).startswith(str(target)):
            raise OSError(errno.EPERM, "no")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fake_open)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 422
    assert _meta(upload_id)["state"] == "failed"


def test_cross_filesystem_reserve_and_replace_from_the_temp_file(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    _link_raising(monkeypatch, errno.EOPNOTSUPP)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 204
    meta = _meta(upload_id)
    assert (meta["final_method"], meta["final_source"]) == ("replace", "tmp")
    assert (target / "a.txt").read_bytes() == DATA
    assert not _temp(upload_id, target).exists()


# ── Error mapping ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error_number",
    [errno.EACCES, errno.EROFS, errno.EINVAL, errno.EILSEQ, errno.ENAMETOOLONG, errno.EFBIG],
)
def test_target_refusal_is_422_and_failed(app, target, monkeypatch, layer, error_number):
    _link_raising(monkeypatch, error_number)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 422
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"]
    assert meta["finalize_error_code"] is None
    assert not store.part_path(upload_id).exists()
    assert layer.records()[-1]["state"] == "failed"


def test_enoent_with_the_target_gone_is_422(app, target, monkeypatch):
    def remove_target():
        for entry in target.iterdir():
            entry.unlink()
        target.rmdir()

    _link_raising(monkeypatch, errno.ENOENT, before=remove_target)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 422
    assert _meta(upload_id)["state"] == "failed"
    assert not store.part_path(upload_id).exists()


def test_enoent_with_the_target_present_is_unexpected(app, target, monkeypatch, layer):
    _link_raising(monkeypatch, errno.ENOENT)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 500
    meta = _meta(upload_id)
    assert meta["state"] == "active"
    assert meta["finalize_error_code"] == 500
    assert meta["error"] and meta["finalize_failed_at"]
    assert store.part_path(upload_id).read_bytes() == DATA
    assert layer.records()[-1]["state"] == "active"
    assert layer.records()[-1]["error"] == meta["error"]


def test_disk_full_on_link_is_507_and_keeps_the_part(app, target, monkeypatch):
    _link_raising(monkeypatch, errno.ENOSPC)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 507
    meta = _meta(upload_id)
    assert (meta["state"], meta["finalize_error_code"]) == ("active", 507)
    assert store.part_path(upload_id).read_bytes() == DATA


def test_target_dir_deleted_is_422_failed_and_staging_removed(app, target):
    upload_id = _new_upload(target, size=len(DATA))
    target.rmdir()
    assert _patch(app, upload_id, 0, DATA).status == 422
    assert _meta(upload_id)["state"] == "failed"
    assert not store.part_path(upload_id).exists()


def test_revalidation_refusal_for_a_non_writable_target(app, target, monkeypatch):
    upload_id = _new_upload(target, size=len(DATA))
    monkeypatch.setattr(upload_views.os, "access", lambda path, mode: False)
    assert _patch(app, upload_id, 0, DATA).status == 422
    assert _meta(upload_id)["state"] == "failed"


def test_revalidation_exception_keeps_the_part_and_answers_500(app, target, monkeypatch):
    project = Project.objects.create(id="-tmp-finalize-proj", directory=str(target))
    upload_id = _new_upload(target, size=len(DATA), content=DATA)
    meta = store.read_metadata(upload_id)
    meta["scope"] = {"kind": "project", "project_id": project.id, "session_id": None}
    store.atomic_write_json(store.metadata_path(upload_id), meta)

    def broken(*args, **kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(upload_views, "validate_path", broken)
    outcome = _finalize(upload_id)
    assert outcome.code == 500
    meta = _meta(upload_id)
    assert (meta["state"], meta["finalize_error_code"]) == ("active", 500)
    assert store.part_path(upload_id).read_bytes() == DATA


def test_project_scope_revalidation_passes(app, target):
    project = Project.objects.create(id="-tmp-finalize-proj2", directory=str(target))
    upload_id = _new_upload(target, size=len(DATA), content=DATA)
    meta = store.read_metadata(upload_id)
    meta["scope"] = {"kind": "project", "project_id": project.id, "session_id": None}
    store.atomic_write_json(store.metadata_path(upload_id), meta)
    assert _finalize(upload_id).code == 204
    assert (target / "a.txt").read_bytes() == DATA


def test_standalone_root_revalidation_refuses_out_of_root(app, target, tmp_path):
    upload_id = _new_upload(target, size=len(DATA), content=DATA)
    meta = store.read_metadata(upload_id)
    meta["scope"] = {"kind": "standalone", "root": str(tmp_path / "elsewhere")}
    store.atomic_write_json(store.metadata_path(upload_id), meta)
    assert _finalize(upload_id).code == 422
    assert _meta(upload_id)["state"] == "failed"


# ── Metadata-write failures ───────────────────────────────────────────────────


def test_finalizing_write_disk_full_is_507_and_changes_nothing(app, target, monkeypatch, layer):
    upload_id = _complete_upload(target)
    before = _meta(upload_id)
    _update_raising(monkeypatch, errno.ENOSPC, lambda c: c.get("state") == "finalizing")
    outcome = _finalize(upload_id)
    assert outcome.code == 507
    after = _meta(upload_id)
    assert after == before
    assert layer.sent == []
    assert not locks.is_finalizing_now(upload_id)


def test_failure_write_disk_full_answers_507_and_stays_finalizing(app, target, monkeypatch, layer):
    _link_raising(monkeypatch, errno.EIO)
    _update_raising(monkeypatch, errno.ENOSPC, lambda c: c.get("state") == "active")
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 507
    assert _meta(upload_id)["state"] == "finalizing"
    assert layer.records()[-1]["state"] == "finalizing"
    assert store.part_path(upload_id).read_bytes() == DATA


@pytest.mark.parametrize(("error_number", "status"), [(errno.EIO, 500), (errno.ENOSPC, 507)])
def test_commit_write_failure_after_a_link_then_head_recovers(app, target, monkeypatch, error_number, status):
    upload_id = _new_upload(target, size=len(DATA))
    with monkeypatch.context() as patch:
        _update_raising(patch, error_number, _is_commit)
        assert _patch(app, upload_id, 0, DATA).status == status
    meta = _meta(upload_id)
    assert meta["state"] == "finalizing"
    assert meta["final_path"] is None
    assert (target / "a.txt").read_bytes() == DATA  # the link is kept
    assert not locks.is_finalizing_now(upload_id)

    links = _link_raising(monkeypatch, errno.EIO)  # no second link may be made
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert links == []
    meta = _meta(upload_id)
    assert (meta["state"], meta["final_path"], meta["final_method"]) == ("completed", str(target / "a.txt"), "link")
    assert not store.part_path(upload_id).exists()
    assert not (target / "a (1).txt").exists()


@pytest.mark.parametrize(("error_number", "status"), [(errno.EIO, 500), (errno.ENOSPC, 507)])
def test_commit_write_failure_after_a_reservation(app, target, monkeypatch, error_number, status):
    _link_raising(monkeypatch, errno.ENOSYS)
    _update_raising(monkeypatch, error_number, _is_commit)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == status
    assert not (target / "a.txt").exists()  # the reservation was removed
    meta = _meta(upload_id)
    assert (meta["state"], meta["finalize_error_code"]) == ("active", status)
    assert store.part_path(upload_id).read_bytes() == DATA


@pytest.mark.parametrize(("error_number", "status"), [(errno.EIO, 500), (errno.ENOSPC, 507)])
def test_completed_write_failure_then_head_recovers(app, target, monkeypatch, error_number, status):
    upload_id = _new_upload(target, size=len(DATA))
    with monkeypatch.context() as patch:
        _update_raising(patch, error_number, lambda c: c.get("state") == "completed")
        assert _patch(app, upload_id, 0, DATA).status == status
    meta = _meta(upload_id)
    assert (meta["state"], meta["final_method"]) == ("finalizing", "link")
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert _meta(upload_id)["state"] == "completed"
    assert not store.part_path(upload_id).exists()


def test_finalizing_now_set_emptied_after_an_exception(app, target, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(store, "finalize_files", boom)
    upload_id = _new_upload(target, size=len(DATA))
    resp = _patch(app, upload_id, 0, DATA)
    assert resp.status == 500
    assert not locks.is_finalizing_now(upload_id)
    assert locks._FINALIZING_NOW == set()


def test_failed_removal_after_the_terminal_write_does_not_fail_the_step(app, target, monkeypatch):
    real_unlink = os.unlink
    upload_id = _new_upload(target, size=len(DATA))
    part = str(store.part_path(upload_id))

    def fake_unlink(path, *args, **kwargs):
        if str(path) == part:
            raise PermissionError(errno.EACCES, "no")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fake_unlink)
    assert _patch(app, upload_id, 0, DATA).status == 204
    assert _meta(upload_id)["state"] == "completed"
    assert os.path.exists(part)  # left for the janitor


def test_zero_byte_upload_through_finalize(app, target):
    upload_id = _new_upload(target, size=0)
    outcome = _finalize(upload_id)
    assert outcome.code == 204
    assert (target / "a.txt").read_bytes() == b""
    assert outcome.meta["state"] == "completed"


# ── HEAD gates ────────────────────────────────────────────────────────────────


def test_head_throttles_a_recent_500_then_retries_after_60s(app, target, monkeypatch):
    links = _link_raising(monkeypatch, errno.EIO, times=1)
    upload_id = _new_upload(target, size=len(DATA))
    assert _patch(app, upload_id, 0, DATA).status == 500
    assert len(links) == 1

    assert _head(app, upload_id).status == 500  # within 60 s: no new attempt
    assert len(links) == 1

    meta = _meta(upload_id)
    meta["finalize_failed_at"] = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    store.atomic_write_json(store.metadata_path(upload_id), meta)
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert len(links) == 2
    assert (target / "a.txt").read_bytes() == DATA


@pytest.mark.parametrize(("error_number", "status"), [(errno.ENOSPC, 507), (errno.EIO, 500)])
def test_head_recovery_whose_finalization_fails(app, target, monkeypatch, error_number, status):
    upload_id = _complete_upload(target)  # a crash after the last append
    _link_raising(monkeypatch, error_number)
    resp = _head(app, upload_id)
    assert resp.status == status
    assert "upload-offset" not in resp.headers  # never offset = length
    assert _meta(upload_id)["finalize_error_code"] == status


def test_head_507_gate_staging_full_even_when_the_target_has_space(app, target, monkeypatch):
    upload_id = _complete_upload(target)
    store.update_metadata(
        upload_id, error="full", finalize_error_code=507, finalize_failed_at=datetime.now(UTC).isoformat()
    )
    staging = str(store.get_staging_dir())
    real_free = store._free_bytes
    monkeypatch.setattr(store, "_free_bytes", lambda p: 0 if str(p) == staging else real_free(p))
    links = _link_raising(monkeypatch, errno.EIO)
    assert _head(app, upload_id).status == 507
    assert links == []
    assert _meta(upload_id)["state"] == "active"


def test_head_507_gate_same_filesystem_needs_one_mib(app, target, monkeypatch):
    upload_id = _complete_upload(target)
    store.update_metadata(
        upload_id, error="full", finalize_error_code=507, finalize_failed_at=datetime.now(UTC).isoformat()
    )
    staging = str(store.get_staging_dir())
    real_free = store._free_bytes
    free = {"target": store.FINALIZE_SPACE_MARGIN - 1}

    def fake_free(path):
        return real_free(path) if str(path) == staging else free["target"]

    monkeypatch.setattr(store, "_free_bytes", fake_free)
    assert _head(app, upload_id).status == 507
    free["target"] = store.FINALIZE_SPACE_MARGIN
    _assert_completed_head(_head(app, upload_id), len(DATA))


def test_head_507_gate_target_gone_runs_recovery_to_410(app, target):
    upload_id = _complete_upload(target)
    store.update_metadata(
        upload_id, error="full", finalize_error_code=507, finalize_failed_at=datetime.now(UTC).isoformat()
    )
    target.rmdir()
    assert _head(app, upload_id).status == 410
    assert _meta(upload_id)["state"] == "failed"


def test_head_waiting_behind_a_finalization_that_fails_answers_500_without_copying(app, target, monkeypatch):
    links = _link_raising(monkeypatch, errno.EIO)
    real_update = store.update_metadata
    gate = threading.Event()
    blocked = threading.Event()

    def slow_sync(upload_id, **changes):
        if "last_transfer_at" in changes and not blocked.is_set():
            blocked.set()
            gate.wait(5)
        return real_update(upload_id, **changes)

    monkeypatch.setattr(store, "update_metadata", slow_sync)
    upload_id = _new_upload(target, size=len(DATA))

    async def scenario():
        patch = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0), body=DATA)
        await asyncio.to_thread(blocked.wait, 5)
        # The .part is complete, the state active, nobody finalizes yet: HEAD
        # chooses recovery and waits for the lock.
        head = AsgiCall(app, "HEAD", upload_id)
        await asyncio.sleep(0.05)
        gate.set()
        return await patch.response(), await head.response()

    patch_resp, head_resp = _run(scenario())
    assert patch_resp.status == 500
    assert head_resp.status == 500
    assert len(links) == 1  # the HEAD did not finalize again


# ── Recovery crash windows ────────────────────────────────────────────────────


def test_recovery_cross_filesystem_link_made_commit_write_lost(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    copies = []
    real_copy = store._copy_to_temp
    monkeypatch.setattr(store, "_copy_to_temp", lambda *a: (copies.append(a), real_copy(*a))[1])
    upload_id = _new_upload(target, size=len(DATA))
    with monkeypatch.context() as patch:
        _update_raising(patch, errno.EIO, _is_commit)
        assert _patch(app, upload_id, 0, DATA).status == 500
    assert _temp(upload_id, target).exists()
    assert os.stat(_temp(upload_id, target)).st_nlink == 2

    # The scan meets the temp file (same inode) first: it must skip it.
    real_scandir = os.scandir

    class TempFirst:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __enter__(self):
            entries = list(self._it)
            return iter(sorted(entries, key=lambda e: not e.name.startswith(".twicc-upload-")))

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(os, "scandir", lambda path: TempFirst(path) if str(path) == str(target) else real_scandir(path))
    _assert_completed_head(_head(app, upload_id), len(DATA))
    meta = _meta(upload_id)
    assert meta["final_path"] == str(target / "a.txt")  # never the temp file
    assert len(copies) == 1  # no second copy
    assert not _temp(upload_id, target).exists()
    assert sorted(p.name for p in target.iterdir()) == ["a.txt"]


def test_recovery_after_the_commit_write_with_link(app, target):
    upload_id = _complete_upload(target)
    os.link(store.part_path(upload_id), target / "a.txt")
    store.update_metadata(upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="link")
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert _meta(upload_id)["state"] == "completed"
    assert not store.part_path(upload_id).exists()


@pytest.mark.parametrize("cross_filesystem", [False, True])
def test_recovery_replace_before_os_replace(app, target, monkeypatch, cross_filesystem):
    if cross_filesystem:
        _other_filesystem(monkeypatch, target)
    _link_raising(monkeypatch, errno.ENOSYS)
    upload_id = _new_upload(target, size=len(DATA))
    real_replace = os.replace
    with monkeypatch.context() as patch:

        def crash(src, dst, *args, **kwargs):
            if str(dst).startswith(str(target)):
                raise OSError(errno.EIO, "crash")
            return real_replace(src, dst, *args, **kwargs)

        patch.setattr(os, "replace", crash)
        assert _patch(app, upload_id, 0, DATA).status == 500
    meta = _meta(upload_id)
    assert (meta["state"], meta["final_method"]) == ("finalizing", "replace")
    assert meta["final_source"] == ("tmp" if cross_filesystem else "part")
    assert (target / "a.txt").read_bytes() == b""  # the reservation

    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert (target / "a.txt").read_bytes() == DATA
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()


@pytest.mark.parametrize("cross_filesystem", [False, True])
def test_recovery_replace_after_os_replace(app, target, monkeypatch, cross_filesystem):
    if cross_filesystem:
        _other_filesystem(monkeypatch, target)
    _link_raising(monkeypatch, errno.ENOSYS)
    upload_id = _new_upload(target, size=len(DATA))
    with monkeypatch.context() as patch:
        _update_raising(patch, errno.EIO, lambda c: c.get("state") == "completed")
        assert _patch(app, upload_id, 0, DATA).status == 500
    assert _meta(upload_id)["state"] == "finalizing"
    assert (target / "a.txt").read_bytes() == DATA

    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert (target / "a.txt").read_bytes() == DATA
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()


def test_recovery_replace_with_the_source_lost(app, target, layer):
    upload_id = _complete_upload(target)
    (target / "a.txt").write_bytes(b"")  # our reservation
    store.update_metadata(
        upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="replace", final_source="part"
    )
    store.part_path(upload_id).unlink()
    assert _head(app, upload_id).status == 410
    meta = _meta(upload_id)
    assert (meta["state"], meta["error"]) == ("failed", "staging file lost")
    assert not (target / "a.txt").exists()
    assert layer.records()[-1]["state"] == "failed"


def test_recovery_replace_with_the_target_dir_gone(app, target):
    upload_id = _complete_upload(target)
    store.update_metadata(
        upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="replace", final_source="part"
    )
    target.rmdir()
    assert _head(app, upload_id).status == 410
    assert _meta(upload_id)["state"] == "failed"
    assert not store.part_path(upload_id).exists()


def test_recovery_replace_raising_disk_full_is_507(app, target, monkeypatch):
    upload_id = _complete_upload(target)
    (target / "a.txt").write_bytes(b"")
    store.update_metadata(
        upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="replace", final_source="part"
    )

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(os, "replace", full)
    assert _head(app, upload_id).status == 507
    assert _meta(upload_id)["state"] == "finalizing"
    assert store.part_path(upload_id).read_bytes() == DATA


def test_recovery_replace_whose_final_path_holds_other_content(app, target):
    upload_id = _complete_upload(target)
    (target / "a.txt").write_bytes(b"someone else's file")
    store.update_metadata(
        upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="replace", final_source="part"
    )
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert (target / "a.txt").read_bytes() == b"someone else's file"
    assert (target / "a (1).txt").read_bytes() == DATA
    meta = _meta(upload_id)
    assert meta["final_path"] == str(target / "a (1).txt")
    assert meta["final_method"] == "link"


def test_recovery_cross_filesystem_temp_file_not_linked(app, target, monkeypatch):
    _other_filesystem(monkeypatch, target)
    upload_id = _complete_upload(target)
    store.update_metadata(upload_id, state="finalizing")
    _temp(upload_id, target).write_bytes(b"hel")  # an incomplete copy
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert (target / "a.txt").read_bytes() == DATA
    assert not _temp(upload_id, target).exists()


def test_recovery_of_an_active_upload_with_a_complete_part(app, target, layer):
    upload_id = _complete_upload(target)
    _assert_completed_head(_head(app, upload_id), len(DATA))
    assert (target / "a.txt").read_bytes() == DATA
    assert [r["state"] for r in layer.records()] == ["finalizing", "completed"]


def test_recovery_finalizing_with_a_short_part_goes_back_to_active(app, target, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")
    store.update_metadata(upload_id, state="finalizing")
    resp = _head(app, upload_id)
    assert resp.status == 200
    assert resp.headers["upload-offset"] == "3"
    assert _meta(upload_id)["state"] == "active"
    assert layer.records()[-1]["state"] == "active"


def test_recovery_write_disk_full_answers_507(app, target, monkeypatch):
    upload_id = _complete_upload(target)
    os.link(store.part_path(upload_id), target / "a.txt")
    store.update_metadata(upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="link")
    _update_raising(monkeypatch, errno.ENOSPC, lambda c: True)
    assert _head(app, upload_id).status == 507
    assert _meta(upload_id)["state"] == "finalizing"


def test_inode_scan_with_the_target_gone_fails_the_upload(app, target, tmp_path):
    upload_id = _complete_upload(target)
    store.update_metadata(upload_id, state="finalizing")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.link(store.part_path(upload_id), elsewhere / "copy")  # st_nlink > 1
    target.rmdir()
    assert _head(app, upload_id).status == 410
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert not store.part_path(upload_id).exists()


def test_recovery_on_a_terminal_state_removes_leftovers(app, target):
    upload_id = _complete_upload(target)
    store.update_metadata(upload_id, state="cancelled")
    _temp(upload_id, target).write_bytes(b"x")
    outcome = _run(_recover(upload_id))
    assert outcome.meta["state"] == "cancelled"
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()


async def _recover(upload_id):
    async with locks.get_upload_lock(upload_id):
        return await upload_views.recover_upload(upload_id)


# ── Cancellation of the awaiting coroutine ────────────────────────────────────


def test_disconnect_during_finalization_runs_no_cleanup_in_parallel(app, target, monkeypatch):
    real_link = os.link
    in_link = threading.Event()
    release = threading.Event()

    def slow_link(src, dst, *args, **kwargs):
        in_link.set()
        release.wait(5)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", slow_link)
    upload_id = _new_upload(target, size=len(DATA))

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        call = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0), body=DATA)
        await asyncio.to_thread(in_link.wait, 5)
        call.disconnect()
        response = await call.response()  # the view was cancelled
        during = (lock.locked(), locks.is_finalizing_now(upload_id), _meta(upload_id)["state"])
        release.set()
        await _wait_guarded_tasks()
        return response, during, lock.locked()

    response, during, held_after = _run(scenario())
    assert response is None
    assert during == (True, True, "finalizing")
    assert held_after is False
    assert not locks.is_finalizing_now(upload_id)
    meta = _meta(upload_id)
    assert meta["state"] == "completed"
    assert (target / "a.txt").read_bytes() == DATA


# ── ArtifactsWatcher ──────────────────────────────────────────────────────────


class _WatchLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append(message["data"])


def _run_watcher(monkeypatch, root, batches, known=()):
    watch_layer = _WatchLayer()
    monkeypatch.setattr(artifacts_watcher, "get_channel_layer", lambda: watch_layer)
    monkeypatch.setattr(artifacts_watcher, "get_artifacts_dir", lambda: root)

    async def fake_awatch(directory, stop_event=None):
        for batch in batches:
            yield batch

    monkeypatch.setattr(artifacts_watcher, "awatch", fake_awatch)

    async def go():
        watcher = artifacts_watcher.ArtifactsWatcher()
        watcher._sessions.update(known)
        await watcher._watch_loop()
        return watcher

    return _run(go()), watch_layer.sent


def test_artifacts_watcher_ignores_an_upload_temp_file(monkeypatch, tmp_path):
    root = tmp_path / "artifacts"
    session_dir = root / "sess1"
    session_dir.mkdir(parents=True)
    tmp = session_dir / f".twicc-upload-{'a' * 32}.tmp"
    tmp.write_bytes(b"partial")
    watcher, sent = _run_watcher(monkeypatch, root, [{(1, str(tmp))}, {(2, str(tmp))}])
    assert not watcher.has("sess1")  # the temp file alone never marks the session
    assert sent == []


def test_artifacts_watcher_relays_the_final_name_only(monkeypatch, tmp_path):
    root = tmp_path / "artifacts"
    session_dir = root / "sess1" / "sub"
    session_dir.mkdir(parents=True)
    tmp = session_dir / f".twicc-upload-{'b' * 32}.tmp"
    final = session_dir / "video.mp4"
    final.write_bytes(b"data")
    watcher, sent = _run_watcher(
        monkeypatch,
        root,
        [{(1, str(tmp))}, {(2, str(tmp))}, {(1, str(final)), (3, str(tmp))}],
        known={"sess1"},
    )
    assert [(m["type"], m["paths"]) for m in sent] == [("artifact_files_changed", ["sub/video.mp4"])]
