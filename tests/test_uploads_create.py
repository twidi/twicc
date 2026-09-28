"""Upload creation and listing endpoints, the view decorator and the
guarded-operation runner (design 2026-09-28-file-upload §5.3 POST / GET,
§5.4 runner + creation lock, §5.5)."""

from __future__ import annotations

import asyncio
import contextvars
import errno
import logging
import os
import shutil
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import orjson
import pytest
from django.http import JsonResponse
from django.test import AsyncClient

from twicc.atomic_json import atomic_write_json
from twicc.core.models import Project, Session, SessionType
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views

pytestmark = pytest.mark.django_db(transaction=True)

STANDALONE_URL = "/api/uploads/"
UPLOAD_HEADER = "X-Twicc-Upload"


def _run(coro):
    return asyncio.run(coro)


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


@pytest.fixture(autouse=True)
def layer(monkeypatch):
    fake = FakeLayer()
    monkeypatch.setattr("twicc.uploads.broadcast.get_channel_layer", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def fresh_upload_locks():
    """Each test starts without upload locks (one loop per ``asyncio.run``)."""
    locks._loop_locks.clear()
    yield
    locks._loop_locks.clear()


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "proj"
    (directory / "sub").mkdir(parents=True)
    return Project.objects.create(id="-tmp-upload-proj", directory=str(directory))


@pytest.fixture
def session(project):
    return Session.objects.create(id="upload-session", project=project, type=SessionType.SESSION)


_counter = iter(range(1_000_000))


def _body(target, **overrides):
    body = {
        "filename": "a.txt",
        "size": 10,
        "target_dir": str(target),
        "origin": {"panel": "files", "key": "project:p"},
        "fingerprint": "fp:10",
        "client_id": f"tab:{next(_counter):016x}",
    }
    body.update(overrides)
    return body


def _post(client, url, body):
    raw = body if isinstance(body, bytes) else orjson.dumps(body)
    return _run(client.post(url, raw, content_type="application/json"))


def _json(response):
    return orjson.loads(response.content)


def _staging_files():
    return sorted(p.name for p in store.get_staging_dir().iterdir())


# ── Creation: success, each prefix ────────────────────────────────────────────


def test_standalone_creation(client, target, layer):
    body = _body(target, filename="  a.txt  ")
    resp = _post(client, STANDALONE_URL, body)

    assert resp.status_code == 201
    record = _json(resp)
    upload_id = record["id"]
    assert store.is_valid_upload_id(upload_id)
    assert resp["Location"] == f"/api/uploads/{upload_id}/"
    assert resp[UPLOAD_HEADER] == "1"
    assert "Tus-Resumable" not in resp
    assert record["state"] == "active"
    assert record["version"] == 1
    assert record["offset"] == 0
    assert record["filename"] == "a.txt"  # stored stripped
    assert record["target_dir"] == str(target)
    assert record["client_id"] == body["client_id"]
    assert record["origin"] == body["origin"]
    assert record["final_path"] is None
    assert "scope" not in record

    assert _staging_files() == [f"{upload_id}.json", f"{upload_id}.part"]
    assert store.part_size(upload_id) == 0
    meta = store.read_metadata(upload_id)
    assert meta["scope"] == {"kind": "standalone", "root": None}

    assert len(layer.sent) == 1
    group, message = layer.sent[0]
    assert group == "updates"
    assert message["data"] == {"type": "upload_state", "upload": record}


def test_target_dir_stored_normalised(client, target):
    resp = _post(client, STANDALONE_URL, _body(f"{target}/./x/../"))
    assert resp.status_code == 201
    assert _json(resp)["target_dir"] == str(target)


def test_standalone_root_is_stored(client, target):
    resp = _post(client, STANDALONE_URL, _body(target, root=str(target.parent)))
    assert resp.status_code == 201
    meta = store.read_metadata(_json(resp)["id"])
    assert meta["scope"] == {"kind": "standalone", "root": str(target.parent)}


def test_project_creation(client, project):
    target_dir = os.path.join(project.directory, "sub")
    resp = _post(client, f"/api/projects/{project.id}/uploads/", _body(target_dir))
    assert resp.status_code == 201
    assert resp[UPLOAD_HEADER] == "1"
    meta = store.read_metadata(_json(resp)["id"])
    assert meta["scope"] == {"kind": "project", "project_id": project.id, "session_id": None}


def test_session_creation(client, project, session):
    url = f"/api/projects/{project.id}/sessions/{session.id}/uploads/"
    resp = _post(client, url, _body(project.directory))
    assert resp.status_code == 201
    meta = store.read_metadata(_json(resp)["id"])
    assert meta["scope"] == {"kind": "project", "project_id": project.id, "session_id": session.id}


def test_root_ignored_under_a_project_prefix(client, project, tmp_path):
    """``root`` restricts only the standalone prefix; under a project prefix
    it is ignored, whatever its value or type."""
    for root in (str(tmp_path / "elsewhere"), 12):
        resp = _post(client, f"/api/projects/{project.id}/uploads/", _body(project.directory, root=root))
        assert resp.status_code == 201, root
        meta = store.read_metadata(_json(resp)["id"])
        assert "root" not in meta["scope"]


def test_origin_key_of_263_characters_accepted(client, target):
    key = "project:" + "p" * 255
    assert len(key) == 263
    resp = _post(client, STANDALONE_URL, _body(target, origin={"panel": "artifacts", "key": key}))
    assert resp.status_code == 201
    assert _json(resp)["origin"] == {"panel": "artifacts", "key": key}


def test_filename_of_240_bytes_accepted(client, target):
    name = "é" * 119 + "ab"  # 238 + 2 = 240 bytes
    assert len(name.encode()) == 240
    resp = _post(client, STANDALONE_URL, _body(target, filename=name))
    assert resp.status_code == 201


# ── Check 1: types and bounds ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"size": True},
        {"size": False},
        {"size": -1},
        {"size": "10"},
        {"size": 1.5},
        {"size": None},
        {"filename": 1},
        {"filename": None},
        {"target_dir": None},
        {"target_dir": ["/tmp"]},
        {"client_id": ""},
        {"client_id": None},
        {"client_id": 5},
        {"client_id": "x" * 65},
        {"fingerprint": ""},
        {"fingerprint": None},
        {"fingerprint": "x" * 65},
        {"origin": "files"},
        {"origin": None},
        {"origin": {"panel": "git", "key": "k"}},
        {"origin": {"key": "k"}},
        {"origin": {"panel": "files", "key": 1}},
        {"origin": {"panel": "files"}},
        {"origin": {"panel": "files", "key": "k" * 1025}},
        {"root": 5},
        {"root": ["/"]},
    ],
)
def test_type_and_bound_checks(client, target, overrides):
    resp = _post(client, STANDALONE_URL, _body(target, **overrides))
    assert resp.status_code == 400, _json(resp)
    assert resp[UPLOAD_HEADER] == "1"
    assert "error" in _json(resp)
    assert _staging_files() == []


def test_length_caps_are_inclusive(client, target):
    resp = _post(client, STANDALONE_URL, _body(target, client_id="c" * 64, fingerprint="f" * 64))
    assert resp.status_code == 201


def test_root_null_accepted(client, target):
    resp = _post(client, STANDALONE_URL, _body(target, root=None))
    assert resp.status_code == 201


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'"text"', b"12", b"null"])
def test_invalid_or_non_object_body(client, raw):
    resp = _post(client, STANDALONE_URL, raw)
    assert resp.status_code == 400
    assert resp[UPLOAD_HEADER] == "1"


def test_body_above_the_memory_limit(client, target, settings):
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 50
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == 400
    assert resp[UPLOAD_HEADER] == "1"


# ── Check 2: file name ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename",
    ["", "   ", "a/b", "a\\b", "a\0b", ".", "..", " .. ", ".twicc-upload-abc.tmp", "x" * 241, "é" * 121],
)
def test_filename_rules(client, target, filename):
    resp = _post(client, STANDALONE_URL, _body(target, filename=filename))
    assert resp.status_code == 400
    assert _staging_files() == []


def test_filename_longer_than_pc_name_max(client, target, monkeypatch):
    real_pathconf = os.pathconf

    def fake_pathconf(path, name):
        if name == "PC_NAME_MAX":
            return 20
        return real_pathconf(path, name)

    monkeypatch.setattr(os, "pathconf", fake_pathconf)
    resp = _post(client, STANDALONE_URL, _body(target, filename="x" * 13))  # 13 + 8 > 20
    assert resp.status_code == 400
    resp = _post(client, STANDALONE_URL, _body(target, filename="x" * 12))  # 12 + 8 == 20
    assert resp.status_code == 201


def test_pc_name_max_unavailable_is_ignored(client, target, monkeypatch):
    def failing_pathconf(path, name):
        raise OSError(errno.EINVAL, "not supported")

    monkeypatch.setattr(os, "pathconf", failing_pathconf)
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == 201


# ── Check 3: absolute target, scope ───────────────────────────────────────────


def test_relative_target_dir_standalone(client, target):
    resp = _post(client, STANDALONE_URL, _body("relative/dir"))
    assert resp.status_code == 400


def test_relative_target_dir_checked_before_the_project_scope(client, project):
    """A relative path would make ``validate_path`` answer ``403``."""
    resp = _post(client, f"/api/projects/{project.id}/uploads/", _body("sub"))
    assert resp.status_code == 400


def test_empty_target_dir(client):
    resp = _post(client, STANDALONE_URL, _body(""))
    assert resp.status_code == 400


def test_standalone_out_of_root(client, target, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    resp = _post(client, STANDALONE_URL, _body(target, root=str(other)))
    assert resp.status_code == 403
    assert _staging_files() == []


def test_standalone_missing_directory(client, tmp_path):
    resp = _post(client, STANDALONE_URL, _body(tmp_path / "missing"))
    assert resp.status_code == 404
    assert _json(resp)["error"]


def test_standalone_target_is_a_file(client, target):
    file = target / "f.txt"
    file.write_text("x")
    resp = _post(client, STANDALONE_URL, _body(file))
    assert resp.status_code == 404


def test_project_out_of_scope(client, project, target):
    resp = _post(client, f"/api/projects/{project.id}/uploads/", _body(target))
    assert resp.status_code == 403
    assert resp[UPLOAD_HEADER] == "1"


def test_project_missing_directory(client, project):
    resp = _post(client, f"/api/projects/{project.id}/uploads/", _body(os.path.join(project.directory, "nope")))
    assert resp.status_code == 404


def test_unknown_project(client, target):
    resp = _post(client, "/api/projects/-no-such-project/uploads/", _body(target))
    assert resp.status_code == 404
    assert resp[UPLOAD_HEADER] == "1"
    assert _json(resp)["error"]


def test_unknown_session(client, project):
    url = f"/api/projects/{project.id}/sessions/no-such-session/uploads/"
    resp = _post(client, url, _body(project.directory))
    assert resp.status_code == 404
    assert _json(resp)["error"]


# ── Check 4: writable, never the staging dir ──────────────────────────────────


# ── Order of the checks ──────────────────────────────────────────────────────


def test_filename_checked_before_the_target(client, tmp_path):
    """Check 2 before check 3: invalid name and missing target → 400."""
    resp = _post(client, STANDALONE_URL, _body(tmp_path / "missing", filename="a/b"))
    assert resp.status_code == 400


def test_scope_checked_before_writable_and_space(client, target, tmp_path, monkeypatch):
    """Check 3 before checks 4-5: out of root, not writable, no space → 403
    from the scope check (the writable check is never reached)."""
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    _fake_disk_usage(monkeypatch, {"*": 0})
    resp = _post(client, STANDALONE_URL, _body(target, root=str(other)))
    assert resp.status_code == 403
    assert "root" in _json(resp)["error"]


def test_missing_target_checked_before_space(client, tmp_path, monkeypatch):
    """Check 3 before check 5: missing target and no space → 404."""
    _fake_disk_usage(monkeypatch, {"*": 0})
    resp = _post(client, STANDALONE_URL, _body(tmp_path / "missing"))
    assert resp.status_code == 404


def test_writable_checked_before_space(client, target, monkeypatch):
    """Check 4 before check 5: not writable and no space → 403."""
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    _fake_disk_usage(monkeypatch, {"*": 0})
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == 403


def test_staging_rule_checked_before_space(client, monkeypatch):
    """Check 4 (staging dir) before check 5 → 403."""
    staging = store.get_staging_dir()
    _fake_disk_usage(monkeypatch, {"*": 0})
    resp = _post(client, STANDALONE_URL, _body(staging))
    assert resp.status_code == 403


def test_pc_name_max_checked_before_writable(client, target, monkeypatch):
    """The ``PC_NAME_MAX`` part of check 2 runs after check 3, before check 4."""
    monkeypatch.setattr(os, "pathconf", lambda path, name: 20)
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    resp = _post(client, STANDALONE_URL, _body(target, filename="x" * 13))
    assert resp.status_code == 400


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses permissions")
def test_target_not_writable(client, target):
    target.chmod(0o500)
    try:
        resp = _post(client, STANDALONE_URL, _body(target))
    finally:
        target.chmod(0o700)
    assert resp.status_code == 403


def test_target_is_the_staging_dir(client):
    staging = store.get_staging_dir()
    resp = _post(client, STANDALONE_URL, _body(staging))
    assert resp.status_code == 403
    assert _staging_files() == []


def test_target_inside_the_staging_dir(client):
    inner = store.get_staging_dir() / "inner"
    inner.mkdir()
    resp = _post(client, STANDALONE_URL, _body(inner))
    assert resp.status_code == 403


def test_target_is_a_symlink_to_the_staging_dir(client, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(store.get_staging_dir())
    resp = _post(client, STANDALONE_URL, _body(link))
    assert resp.status_code == 403
    assert _staging_files() == []


# ── Check 5: free space ───────────────────────────────────────────────────────

Usage = namedtuple("Usage", "total used free")


def _fake_disk_usage(monkeypatch, free_by_path):
    """``shutil.disk_usage`` answering ``free_by_path[str(path)]`` (or a default)."""

    def fake(path):
        return Usage(10**12, 0, free_by_path.get(str(path), free_by_path.get("*", 10**12)))

    monkeypatch.setattr(shutil, "disk_usage", fake)


def test_not_enough_space_on_the_staging_filesystem(client, target, monkeypatch):
    _fake_disk_usage(monkeypatch, {"*": 9})
    resp = _post(client, STANDALONE_URL, _body(target, size=10))
    assert resp.status_code == 507
    assert resp[UPLOAD_HEADER] == "1"
    assert _staging_files() == []


def test_space_counts_the_bytes_expected_by_other_uploads(client, target, monkeypatch):
    first = _post(client, STANDALONE_URL, _body(target, size=100))
    assert first.status_code == 201
    with open(store.part_path(_json(first)["id"]), "ab") as f:
        f.write(b"x" * 10)  # 90 bytes still expected

    _fake_disk_usage(monkeypatch, {"*": 150})
    assert _post(client, STANDALONE_URL, _body(target, size=61)).status_code == 507
    assert _post(client, STANDALONE_URL, _body(target, size=60)).status_code == 201


def test_terminal_uploads_expect_no_bytes(client, target, monkeypatch):
    first = _post(client, STANDALONE_URL, _body(target, size=100))
    store.update_metadata(_json(first)["id"], state="cancelled")
    _fake_disk_usage(monkeypatch, {"*": 100})
    assert _post(client, STANDALONE_URL, _body(target, size=100)).status_code == 201


def test_not_enough_space_on_another_target_filesystem(client, target, monkeypatch):
    staging = store.get_staging_dir()
    monkeypatch.setattr(upload_views, "_st_dev", lambda path: 1 if str(path) == str(staging) else 2)
    _fake_disk_usage(monkeypatch, {str(target): 5})
    assert _post(client, STANDALONE_URL, _body(target, size=10)).status_code == 507
    assert _post(client, STANDALONE_URL, _body(target, size=5)).status_code == 201


def test_target_space_ignored_on_the_same_filesystem(client, target, monkeypatch):
    _fake_disk_usage(monkeypatch, {str(target): 5})
    assert _post(client, STANDALONE_URL, _body(target, size=10)).status_code == 201


# ── Idempotency (check 1b) ────────────────────────────────────────────────────


def test_retry_answers_200_with_the_first_upload(client, target, layer):
    body = _body(target)
    first = _post(client, STANDALONE_URL, body)
    second = _post(client, STANDALONE_URL, body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert _json(second) == _json(first)
    assert second["Location"] == first["Location"]
    assert second[UPLOAD_HEADER] == "1"
    assert len(_staging_files()) == 2
    assert len(layer.sent) == 1


def test_retry_of_a_tombstone(client, target):
    body = _body(target)
    first = _json(_post(client, STANDALONE_URL, body))
    cancelled = store.update_metadata(first["id"], state="cancelled")
    second = _post(client, STANDALONE_URL, body)
    assert second.status_code == 200
    assert _json(second) == store.build_record(cancelled)


def test_retry_skips_the_space_check(client, target, monkeypatch):
    """Check 5 would count the first attempt's own upload."""
    body = _body(target, size=100)
    _fake_disk_usage(monkeypatch, {"*": 150})
    assert _post(client, STANDALONE_URL, body).status_code == 201
    assert _post(client, STANDALONE_URL, body).status_code == 200


def test_retry_after_the_target_was_removed(client, target):
    body = _body(target)
    first = _json(_post(client, STANDALONE_URL, body))
    target.rmdir()
    second = _post(client, STANDALONE_URL, body)
    assert second.status_code == 200
    assert _json(second)["id"] == first["id"]


def test_retry_skips_every_check(client, target):
    body = _body(target)
    first = _json(_post(client, STANDALONE_URL, body))
    retry = dict(body, filename="..", target_dir="relative")
    second = _post(client, STANDALONE_URL, retry)
    assert second.status_code == 200
    assert _json(second)["id"] == first["id"]


def test_lookup_skips_unparsable_metadata(client, target):
    staging = store.get_staging_dir()
    (staging / f"{'a' * 32}.json").write_bytes(b"{broken")
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == 201


def test_concurrent_posts_with_one_client_id_create_one_upload(client, target):
    body = orjson.dumps(_body(target))

    async def scenario():
        return await asyncio.gather(
            *(client.post(STANDALONE_URL, body, content_type="application/json") for _ in range(3))
        )

    responses = _run(scenario())
    assert sorted(r.status_code for r in responses) == [200, 200, 201]
    assert len({_json(r)["id"] for r in responses}) == 1
    assert len(_staging_files()) == 2


# ── File creation ─────────────────────────────────────────────────────────────


def test_part_is_created_before_the_json(client, target, monkeypatch):
    seen = []
    real_create_metadata = store.create_metadata

    def spy(upload_id, **fields):
        seen.append((store.part_path(upload_id).exists(), store.metadata_path(upload_id).exists()))
        return real_create_metadata(upload_id, **fields)

    monkeypatch.setattr(store, "create_metadata", spy)
    assert _post(client, STANDALONE_URL, _body(target)).status_code == 201
    assert seen == [(True, False)]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (OSError(errno.ENOSPC, "No space left on device"), 507),
        (OSError(errno.EDQUOT, "Disk quota exceeded"), 507),
        (OSError(errno.EIO, "I/O error"), 500),
        (RuntimeError("boom"), 500),
    ],
)
def test_creation_failure_leaves_no_files(client, target, monkeypatch, layer, error, status):
    def failing_write(path, data):
        raise error

    monkeypatch.setattr(store, "atomic_write_json", failing_write)
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == status
    assert resp[UPLOAD_HEADER] == "1"
    assert _staging_files() == []
    assert layer.sent == []


def test_creation_failure_on_the_part_file(client, target, monkeypatch):
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".part") and mode == "xb":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    resp = _post(client, STANDALONE_URL, _body(target))
    assert resp.status_code == 507
    assert _staging_files() == []


def test_create_upload_leaves_an_existing_part_untouched():
    upload_id = store.new_upload_id()
    store.part_path(upload_id).write_bytes(b"theirs")
    store.metadata_path(upload_id).write_bytes(b"{}")
    with pytest.raises(FileExistsError):
        store.create_upload(
            upload_id,
            client_id="c",
            size=6,
            filename="a.txt",
            target_dir="/tmp",
            scope={"kind": "standalone", "root": None},
            origin={"panel": "files", "key": "k"},
            fingerprint="fp",
        )
    assert store.part_path(upload_id).read_bytes() == b"theirs"
    assert store.metadata_path(upload_id).read_bytes() == b"{}"


def test_create_upload_leaves_an_existing_json_untouched():
    """``create_metadata`` refuses an existing ``.json``: only this call's
    ``.part`` is removed."""
    upload_id = store.new_upload_id()
    store.metadata_path(upload_id).write_bytes(b"{}")
    with pytest.raises(FileExistsError):
        store.create_upload(
            upload_id,
            client_id="c",
            size=6,
            filename="a.txt",
            target_dir="/tmp",
            scope={"kind": "standalone", "root": None},
            origin={"panel": "files", "key": "k"},
            fingerprint="fp",
        )
    assert store.metadata_path(upload_id).read_bytes() == b"{}"
    assert not store.part_path(upload_id).exists()


def test_retry_after_a_creation_failure_creates_the_upload(client, target, monkeypatch):
    body = _body(target)

    def failing_write(path, data):
        raise OSError(errno.EIO, "I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(store, "atomic_write_json", failing_write)
        assert _post(client, STANDALONE_URL, body).status_code == 500
    assert _post(client, STANDALONE_URL, body).status_code == 201


# ── Zero-byte upload ──────────────────────────────────────────────────────────


def test_zero_byte_upload_is_finalized_in_the_creation(client, target):
    resp = _post(client, STANDALONE_URL, _body(target, filename="empty.txt", size=0))
    assert resp.status_code == 201
    record = _json(resp)
    assert record["state"] == "completed"
    assert record["final_path"] == str(target / "empty.txt")
    assert (target / "empty.txt").read_bytes() == b""


def test_zero_byte_upload_calls_the_finalization_hook_under_the_upload_lock(client, target, monkeypatch):
    calls = []

    async def fake_finalize(upload_id):
        lock = locks.get_upload_lock(upload_id)
        calls.append((upload_id, lock.locked(), locks.get_creation_lock().locked()))
        meta = store.update_metadata(upload_id, state="completed", final_path=str(target / "empty.txt"))
        return upload_views.StepOutcome(meta, 204)

    monkeypatch.setattr(upload_views, "finalize_upload", fake_finalize)
    resp = _post(client, STANDALONE_URL, _body(target, size=0))
    assert resp.status_code == 201
    record = _json(resp)
    assert calls == [(record["id"], True, True)]
    assert record["state"] == "completed"
    assert record["version"] == 2


# ── GET ───────────────────────────────────────────────────────────────────────


def _write_tombstone_age(upload_id, age):
    meta = store.read_metadata(upload_id)
    meta["updated_at"] = (datetime.now(UTC) - age).isoformat()
    atomic_write_json(store.metadata_path(upload_id), meta)


def test_get_lists_uploads_and_recent_tombstones(client, target):
    active = _json(_post(client, STANDALONE_URL, _body(target)))
    recent = _json(_post(client, STANDALONE_URL, _body(target)))
    old = _json(_post(client, STANDALONE_URL, _body(target)))
    store.update_metadata(recent["id"], state="completed", final_path=str(target / "a.txt"))
    store.update_metadata(old["id"], state="failed", error="expired")
    _write_tombstone_age(old["id"], timedelta(hours=25))

    before = datetime.now(UTC)
    resp = _run(client.get(STANDALONE_URL))
    after = datetime.now(UTC)

    assert resp.status_code == 200
    assert resp[UPLOAD_HEADER] == "1"
    data = _json(resp)
    assert before <= datetime.fromisoformat(data["now"]) <= after
    ids = {item["id"] for item in data["uploads"]}
    assert ids == {active["id"], recent["id"]}
    completed = next(item for item in data["uploads"] if item["id"] == recent["id"])
    assert completed["state"] == "completed"
    assert completed["final_path"] == str(target / "a.txt")
    assert "scope" not in completed


def test_get_keeps_an_old_non_terminal_upload(client, target):
    active = _json(_post(client, STANDALONE_URL, _body(target)))
    _write_tombstone_age(active["id"], timedelta(days=3))
    data = _json(_run(client.get(STANDALONE_URL)))
    assert [item["id"] for item in data["uploads"]] == [active["id"]]


def test_get_skips_unparsable_and_foreign_files(client, target):
    staging = store.get_staging_dir()
    (staging / f"{'b' * 32}.json").write_bytes(b"{broken")
    (staging / f"{'c' * 32}.json").write_bytes(b"[]")
    (staging / "notes.json").write_bytes(b"{}")
    (staging / f"{'d' * 32}.part").write_bytes(b"xx")
    data = _json(_run(client.get(STANDALONE_URL)))
    assert data["uploads"] == []


def test_get_empty(client):
    data = _json(_run(client.get(STANDALONE_URL)))
    assert data["uploads"] == []
    assert datetime.fromisoformat(data["now"]).tzinfo is not None


# ── Methods ───────────────────────────────────────────────────────────────────


def test_unsupported_methods(client, project):
    resp = _run(client.put(STANDALONE_URL))
    assert resp.status_code == 405
    assert resp[UPLOAD_HEADER] == "1"
    resp = _run(client.get(f"/api/projects/{project.id}/uploads/"))
    assert resp.status_code == 405
    assert resp[UPLOAD_HEADER] == "1"


# ── The view decorator ────────────────────────────────────────────────────────


class _Request:
    method = "GET"


def test_decorator_turns_an_exception_into_a_logged_json_500(caplog):
    @upload_views.upload_view()
    async def view(request):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        resp = _run(view(_Request()))
    assert resp.status_code == 500
    assert isinstance(resp, JsonResponse)
    assert _json(resp)["error"]
    assert resp[UPLOAD_HEADER] == "1"
    assert "Tus-Resumable" not in resp
    logged = [r for r in caplog.records if r.exc_info and r.exc_info[1] is not None]
    assert len(logged) == 1


def test_decorator_adds_the_tus_header_on_id_routes():
    @upload_views.upload_view(tus=True)
    async def ok(request):
        return JsonResponse({}, status=204)

    @upload_views.upload_view(tus=True)
    async def broken(request):
        raise ValueError("boom")

    for view, status in ((ok, 204), (broken, 500)):
        resp = _run(view(_Request()))
        assert resp.status_code == status
        assert resp[UPLOAD_HEADER] == "1"
        assert resp["Tus-Resumable"] == "1.0.0"


def test_decorator_logs_a_guarded_task_exception_once(caplog):
    async def operation():
        raise RuntimeError("inside the guarded task")

    @upload_views.upload_view()
    async def view(request):
        return await locks.run_guarded(operation(), label="test")

    with caplog.at_level(logging.ERROR):
        resp = _run(view(_Request()))
    assert resp.status_code == 500
    logged = [r for r in caplog.records if r.exc_info and r.exc_info[1] is not None]
    assert len(logged) == 1


def test_decorator_lets_cancelled_error_propagate():
    @upload_views.upload_view()
    async def view(request):
        raise asyncio.CancelledError

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await view(_Request())

    _run(scenario())


def test_decorator_lets_a_disconnect_cancel_the_view():
    async def scenario():
        entered = asyncio.Event()

        @upload_views.upload_view()
        async def view(request):
            entered.set()
            await asyncio.sleep(10)

        task = asyncio.create_task(view(_Request()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario())


# ── The guarded-operation runner ──────────────────────────────────────────────

_request_var = contextvars.ContextVar("request_var", default="fresh")


def test_runner_starts_its_task_in_a_fresh_context():
    async def operation():
        return _request_var.get()

    async def scenario():
        _request_var.set("request")
        assert _request_var.get() == "request"
        return await locks.run_guarded(operation(), label="test")

    assert _run(scenario()) == "fresh"


def test_runner_returns_the_result_and_releases_the_reference():
    async def operation():
        return 42

    async def scenario():
        result = await locks.run_guarded(operation(), label="test")
        await asyncio.sleep(0)
        return result, set(locks._GUARDED_TASKS)

    result, remaining = _run(scenario())
    assert result == 42
    assert remaining == set()


def test_runner_task_survives_the_cancellation_of_its_caller():
    """A client disconnect cancels the awaiting view, never the operation: it
    ends under its lock, then releases it."""

    async def scenario():
        lock = locks.get_creation_lock()
        release = asyncio.Event()
        steps = []

        async def operation():
            async with lock:
                steps.append("started")
                await release.wait()
                steps.append("finished")

        caller = asyncio.create_task(locks.run_guarded(operation(), label="test"))
        while not steps:
            await asyncio.sleep(0)
        assert len(locks._GUARDED_TASKS) == 1
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert lock.locked()  # still held by the running operation
        release.set()
        while locks._GUARDED_TASKS:
            await asyncio.sleep(0)
        return steps, lock.locked()

    steps, locked = _run(scenario())
    assert steps == ["started", "finished"]
    assert locked is False


def test_locks_are_kept_per_event_loop():
    """Within one loop the locks are never replaced; a new loop (one
    ``asyncio.run`` per call in tests) gets its own locks."""

    async def contend():
        lock = locks.get_creation_lock()
        async with lock:
            waiter = asyncio.create_task(lock.acquire())
            await asyncio.sleep(0)
            assert locks.get_creation_lock() is lock
        await waiter
        lock.release()

    _run(contend())
    _run(contend())


def test_upload_lock_does_not_create_the_staging_dir(data_dir):
    async def scenario():
        return locks.get_upload_lock("e" * 32)

    assert _run(scenario()) is None
    assert not (data_dir / "uploads").exists()


def test_upload_lock_rejects_an_invalid_id():
    async def scenario():
        locks.get_upload_lock("../x")

    with pytest.raises(ValueError):
        _run(scenario())


def test_upload_lock_exists_only_with_its_metadata(client, target):
    body = _body(target)
    upload_id = _json(_post(client, STANDALONE_URL, body))["id"]

    async def scenario():
        missing = locks.get_upload_lock("f" * 32)
        first = locks.get_upload_lock(upload_id)
        second = locks.get_upload_lock(upload_id)
        return missing, first, second

    missing, first, second = _run(scenario())
    assert missing is None
    assert first is not None
    assert first is second
