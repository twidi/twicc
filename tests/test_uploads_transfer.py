"""tus transfer endpoints of the uploads: HEAD, PATCH, DELETE and locking
(design 2026-09-28-file-upload §5.3 ``<id>`` routes, §5.4, §5.5).

Every request goes through Django's real ``ASGIHandler`` (middleware, body
spooling, disconnect handling), driven with raw ASGI messages.
"""

from __future__ import annotations

import asyncio
import errno
import threading
from typing import NamedTuple

import orjson
import pytest
from django.core.handlers.asgi import ASGIHandler

from twicc.atomic_json import atomic_write_json
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views

pytestmark = pytest.mark.django_db(transaction=True)

UPLOAD_HEADER = "x-twicc-upload"
TUS = {"Tus-Resumable": "1.0.0"}
OCTET = "application/offset+octet-stream"
TASK_4 = "finalization and recovery are task 4 of docs/plans/2026-09-28-file-upload-implementation-plan.md"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


class FakeLayer:
    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))

    def records(self):
        return [message["data"]["upload"] for _group, message in self.sent]


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


def _new_upload(target, size=10, content=b""):
    upload_id = store.new_upload_id()
    store.create_upload(
        upload_id,
        client_id=f"tab:{upload_id[:16]}",
        size=size,
        filename="a.txt",
        target_dir=str(target),
        scope={"kind": "standalone", "root": None},
        origin={"panel": "files", "key": "project:p"},
        fingerprint="fp",
    )
    if content:
        store.part_path(upload_id).write_bytes(content)
    return upload_id


def _set_state(upload_id, **changes):
    return store.update_metadata(upload_id, **changes)


# ── ASGI driver ───────────────────────────────────────────────────────────────


class Response(NamedTuple):
    status: int
    headers: dict
    body: bytes


class AsgiCall:
    """One HTTP request driven through the real ``ASGIHandler``.

    The body messages are queued at once; after them, ``receive()`` blocks
    until :meth:`disconnect` queues an ``http.disconnect``.
    """

    def __init__(self, app, method, upload_id, *, headers=None, body=b"", content_length=True, chunks=None):
        self.inbound = asyncio.Queue()
        self.sent = []
        hdrs = dict(headers if headers is not None else TUS)
        if content_length and body:
            hdrs.setdefault("Content-Length", str(len(body)))
        path = f"/api/uploads/{upload_id}/"
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 3500),
        }
        parts = chunks if chunks is not None else [body]
        for index, part in enumerate(parts):
            self.inbound.put_nowait({"type": "http.request", "body": part, "more_body": index < len(parts) - 1})
        self.task = asyncio.create_task(app(self.scope, self.receive, self.send))

    async def receive(self):
        return await self.inbound.get()

    async def send(self, message):
        self.sent.append(message)

    def disconnect(self):
        self.inbound.put_nowait({"type": "http.disconnect"})

    async def response(self) -> Response | None:
        await self.task
        start = next((m for m in self.sent if m["type"] == "http.response.start"), None)
        if start is None:
            return None
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        body = b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")
        return Response(start["status"], headers, body)


async def _call(app, method, upload_id, **kwargs) -> Response:
    return await AsgiCall(app, method, upload_id, **kwargs).response()


def _run(coro):
    return asyncio.run(coro)


def _request(app, method, upload_id, **kwargs) -> Response:
    return _run(_call(app, method, upload_id, **kwargs))


def _patch_headers(offset, **extra):
    return {**TUS, "Content-Type": OCTET, "Upload-Offset": str(offset), **extra}


def _patch(app, upload_id, offset, body, **kwargs) -> Response:
    headers = kwargs.pop("headers", None) or _patch_headers(offset)
    return _request(app, "PATCH", upload_id, headers=headers, body=body, **kwargs)


async def _wait_guarded_tasks():
    while locks._GUARDED_TASKS:
        await asyncio.sleep(0.01)


async def _until(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def _assert_tus_headers(resp):
    assert resp.headers[UPLOAD_HEADER] == "1"
    assert resp.headers["tus-resumable"] == "1.0.0"


# ── General rules of the <id> routes ─────────────────────────────────────────


@pytest.mark.parametrize("method", ["HEAD", "PATCH", "DELETE"])
@pytest.mark.parametrize("upload_id", ["not-an-id", "A" * 32, "a" * 31, "g" * 32])
def test_invalid_id_format_is_404(app, method, upload_id, data_dir):
    resp = _request(app, method, upload_id, headers=_patch_headers(0) if method == "PATCH" else None)
    assert resp.status == 404
    _assert_tus_headers(resp)
    assert not (data_dir / "uploads").exists()  # no path work


@pytest.mark.parametrize("method", ["HEAD", "PATCH", "DELETE"])
def test_unknown_id_is_404_without_a_lock(app, method, data_dir):
    resp = _request(app, method, "a" * 32, headers=_patch_headers(0) if method == "PATCH" else None, body=b"x")
    assert resp.status == 404
    _assert_tus_headers(resp)
    assert not (data_dir / "uploads").exists()
    assert all(not state.uploads for state in locks._loop_locks.values())


@pytest.mark.parametrize("method", ["HEAD", "PATCH", "DELETE"])
@pytest.mark.parametrize("content", [b"{broken", b"[]", b'{"id": "x"}'])
def test_unparsable_json_is_404(app, method, content):
    upload_id = "b" * 32
    staging = store.get_staging_dir()
    (staging / f"{upload_id}.json").write_bytes(content)
    (staging / f"{upload_id}.part").write_bytes(b"12")

    async def scenario():
        resp = await _call(app, method, upload_id, headers=_patch_headers(2) if method == "PATCH" else None, body=b"x")
        return resp, [state.uploads for state in locks._loop_locks.values()]

    resp, lock_maps = _run(scenario())
    assert resp.status == 404
    assert all(not uploads for uploads in lock_maps)  # before any lock
    assert (staging / f"{upload_id}.part").read_bytes() == b"12"


@pytest.mark.parametrize("method", ["HEAD", "PATCH", "DELETE"])
@pytest.mark.parametrize("headers", [{}, {"Tus-Resumable": "0.2.2"}])
def test_missing_tus_resumable_is_412(app, target, method, headers):
    upload_id = _new_upload(target)
    if method == "PATCH":
        headers = {**headers, "Content-Type": OCTET, "Upload-Offset": "0"}
    resp = _request(app, method, upload_id, headers=headers, body=b"abc" if method == "PATCH" else b"")
    assert resp.status == 412
    assert resp.headers["tus-version"] == "1.0.0"
    _assert_tus_headers(resp)
    assert store.part_size(upload_id) == 0
    assert store.read_metadata(upload_id)["state"] == "active"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "OPTIONS"])
def test_other_methods_are_405(app, target, method):
    upload_id = _new_upload(target)
    resp = _request(app, method, upload_id)
    assert resp.status == 405
    assert resp.headers["allow"] == "HEAD, PATCH, DELETE"
    _assert_tus_headers(resp)


def test_metadata_removed_while_waiting_for_the_lock_is_404(app, target):
    upload_id = _new_upload(target)

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        await lock.acquire()
        call = AsgiCall(app, "DELETE", upload_id)
        await asyncio.sleep(0.05)
        store.metadata_path(upload_id).unlink()
        lock.release()
        return await call.response()

    assert _run(scenario()).status == 404


# ── tus flow ──────────────────────────────────────────────────────────────────


def test_tus_flow_up_to_a_complete_part(app, target, layer, monkeypatch):
    data = bytes(range(256)) * 40  # 10240 bytes
    upload_id = _new_upload(target, size=len(data))
    calls = []

    async def fake_finalize(uid):
        calls.append((uid, locks.get_upload_lock(uid).locked(), store.part_path(uid).read_bytes()))
        meta = store.update_metadata(uid, state="completed", final_path=str(target / "a.txt"))
        return upload_views.StepOutcome(meta, 204)

    monkeypatch.setattr(upload_views, "finalize_upload", fake_finalize)

    async def scenario():
        answers = []
        head = await _call(app, "HEAD", upload_id)
        answers.append(head)
        offset = int(head.headers["upload-offset"])
        for start in range(0, len(data), 4096):
            chunk = data[start : start + 4096]
            resp = await _call(app, "PATCH", upload_id, headers=_patch_headers(offset), body=chunk)
            answers.append(resp)
            if resp.status == 204:
                offset = int(resp.headers["upload-offset"])
        answers.append(await _call(app, "HEAD", upload_id))
        return answers

    answers = _run(scenario())
    head, *patches, final_head = answers
    assert head.status == 200
    assert head.headers["upload-offset"] == "0"
    assert head.headers["upload-length"] == str(len(data))
    assert head.headers["cache-control"] == "no-store"
    assert [r.status for r in patches] == [204, 204, 204]
    assert [r.headers["upload-offset"] for r in patches] == ["4096", "8192", str(len(data))]
    for resp in patches:
        _assert_tus_headers(resp)
    assert calls == [(upload_id, True, data)]
    assert final_head.headers["upload-offset"] == final_head.headers["upload-length"] == str(len(data))

    # One broadcast per chunk (offset synced, version increasing), then the finalization's.
    records = layer.records()
    assert [r["offset"] for r in records[:3]] == [4096, 8192, len(data)]
    versions = [r["version"] for r in records]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)


def test_patch_updates_last_transfer_at_and_offset(app, target):
    upload_id = _new_upload(target, size=10)
    before = store.read_metadata(upload_id)
    resp = _patch(app, upload_id, 0, b"abc")
    assert resp.status == 204
    meta = store.read_metadata(upload_id)
    assert meta["offset"] == 3
    assert meta["version"] == before["version"] + 1
    assert meta["last_transfer_at"] > before["last_transfer_at"]
    assert store.part_path(upload_id).read_bytes() == b"abc"


def test_patch_accepts_a_body_split_in_several_asgi_messages(app, target):
    upload_id = _new_upload(target, size=9)
    resp = _patch(app, upload_id, 0, b"abcdef", chunks=[b"ab", b"cd", b"ef"])
    assert resp.status == 204
    assert resp.headers["upload-offset"] == "6"


def test_patch_reads_the_body_in_blocks(app, target, monkeypatch):
    monkeypatch.setattr(store, "APPEND_BLOCK_SIZE", 3)
    upload_id = _new_upload(target, size=20)
    resp = _patch(app, upload_id, 0, b"0123456789")
    assert resp.status == 204
    assert store.part_path(upload_id).read_bytes() == b"0123456789"


@pytest.mark.xfail(strict=True, reason=TASK_4)
def test_tus_flow_completes_into_the_target(app, target):
    data = b"hello world"
    upload_id = _new_upload(target, size=len(data))
    resp = _patch(app, upload_id, 0, data)
    assert resp.status == 204
    assert resp.headers["upload-offset"] == str(len(data))
    assert (target / "a.txt").read_bytes() == data
    assert store.read_metadata(upload_id)["state"] == "completed"


def test_final_patch_answers_500_until_finalization_exists(app, target):
    upload_id = _new_upload(target, size=3)
    resp = _patch(app, upload_id, 0, b"abc")
    assert resp.status == 500
    _assert_tus_headers(resp)
    # The bytes and the offset sync happened before the finalization hook.
    assert store.part_size(upload_id) == 3
    assert store.read_metadata(upload_id)["offset"] == 3


@pytest.mark.parametrize(
    ("final", "code", "status"),
    [
        ({"state": "completed", "final_path": "/x/a.txt"}, 204, 204),
        ({"state": "failed", "error": "target gone"}, 422, 422),
        ({"state": "active", "error": "disk full", "finalize_error_code": 507, "finalize_failed_at": "t"}, 507, 507),
        ({"state": "active", "error": "boom", "finalize_error_code": 500, "finalize_failed_at": "t"}, 500, 500),
        ({"state": "finalizing"}, 507, 507),
        ({"state": "finalizing"}, 500, 500),
    ],
)
def test_final_patch_answer_follows_the_finalization(app, target, monkeypatch, final, code, status):
    upload_id = _new_upload(target, size=3)

    async def fake_finalize(uid):
        return upload_views.StepOutcome(store.update_metadata(uid, **final), code)

    monkeypatch.setattr(upload_views, "finalize_upload", fake_finalize)
    resp = _patch(app, upload_id, 0, b"abc")
    assert resp.status == status
    if status == 204:
        assert resp.headers["upload-offset"] == "3"
    else:
        assert "upload-offset" not in resp.headers


def test_last_bytes_with_a_failed_offset_sync_still_finalize(app, target, monkeypatch, layer):
    upload_id = _new_upload(target, size=3)
    calls = []

    async def fake_finalize(uid):
        calls.append((uid, store.part_size(uid), store.read_metadata(uid)["offset"]))
        return upload_views.StepOutcome(dict(store.read_metadata(uid), state="completed", final_path="/x"), 204)

    def failing_write(*args, **kwargs):
        raise OSError(errno.EIO, "boom")

    monkeypatch.setattr(upload_views, "finalize_upload", fake_finalize)
    monkeypatch.setattr(store, "atomic_write_json", failing_write)
    resp = _patch(app, upload_id, 0, b"abc")
    assert calls == [(upload_id, 3, 0)]  # the sync failed, finalization still ran
    assert resp.status == 204
    assert layer.sent == []  # no broadcast for the failed sync


# ── PATCH errors ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("content_type", [None, "application/octet-stream", "text/plain"])
def test_patch_wrong_content_type_is_415(app, target, content_type):
    upload_id = _new_upload(target)
    headers = {**TUS, "Upload-Offset": "0"}
    if content_type:
        headers["Content-Type"] = content_type
    resp = _request(app, "PATCH", upload_id, headers=headers, body=b"abc")
    assert resp.status == 415
    assert store.part_size(upload_id) == 0


@pytest.mark.parametrize("offset", [None, "", "abc", "-1", "1.5", " 1"])
def test_patch_missing_or_invalid_upload_offset_is_400(app, target, offset):
    upload_id = _new_upload(target)
    headers = {**TUS, "Content-Type": OCTET}
    if offset is not None:
        headers["Upload-Offset"] = offset
    resp = _request(app, "PATCH", upload_id, headers=headers, body=b"abc")
    assert resp.status == 400
    assert store.part_size(upload_id) == 0


def test_patch_offset_mismatch_is_409(app, target, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")
    for offset in (0, 2, 4):
        resp = _patch(app, upload_id, offset, b"def")
        assert resp.status == 409
    assert store.part_path(upload_id).read_bytes() == b"abc"
    assert layer.sent == []


def test_patch_on_finalizing_is_409(app, target):
    upload_id = _new_upload(target, size=10, content=b"abc")
    _set_state(upload_id, state="finalizing")
    assert _patch(app, upload_id, 3, b"def").status == 409
    assert store.part_size(upload_id) == 3


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_patch_on_a_terminal_upload_is_410(app, target, state):
    upload_id = _new_upload(target, size=10, content=b"abc")
    _set_state(upload_id, state=state, **({"error": "x"} if state == "failed" else {}))
    assert _patch(app, upload_id, 3, b"def").status == 410
    assert store.part_path(upload_id).read_bytes() == b"abc"


def test_patch_with_a_complete_part_is_409(app, target, monkeypatch):
    upload_id = _new_upload(target, size=3, content=b"abc")

    async def no_finalize(uid):  # pragma: no cover - must not run
        raise AssertionError("PATCH must not finalize")

    monkeypatch.setattr(upload_views, "finalize_upload", no_finalize)
    assert _patch(app, upload_id, 3, b"d").status == 409
    assert _patch(app, upload_id, 0, b"d").status == 409
    assert store.read_metadata(upload_id)["state"] == "active"


def test_patch_with_a_lost_part_fails_the_upload(app, target, layer):
    upload_id = _new_upload(target, size=10)
    store.part_path(upload_id).unlink()
    resp = _patch(app, upload_id, 0, b"abc")
    assert resp.status == 410
    meta = store.read_metadata(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"] == "staging file lost"
    assert [r["state"] for r in layer.records()] == ["failed"]
    assert not store.part_path(upload_id).exists()


def test_patch_with_a_lost_part_and_a_disk_full_write(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10)
    store.part_path(upload_id).unlink()

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(store, "atomic_write_json", full)
    assert _patch(app, upload_id, 0, b"abc").status == 507
    assert store.read_metadata(upload_id)["state"] == "active"
    assert layer.sent == []


@pytest.mark.parametrize("with_length", [True, False])
def test_patch_excess_bytes_are_400_and_truncated_back(app, target, layer, with_length):
    upload_id = _new_upload(target, size=10, content=b"abcd")
    before = store.read_metadata(upload_id)
    resp = _patch(app, upload_id, 4, b"0123456789", content_length=with_length)
    assert resp.status == 400
    assert orjson.loads(resp.body)["error"]
    assert store.part_path(upload_id).read_bytes() == b"abcd"
    after = store.read_metadata(upload_id)
    assert after["state"] == "active"
    assert after["offset"] == 4
    assert after["last_transfer_at"] == before["last_transfer_at"]


def test_patch_excess_by_one_byte_without_content_length(app, target):
    upload_id = _new_upload(target, size=5)
    resp = _patch(app, upload_id, 0, b"abcdef", content_length=False, chunks=[b"abcde", b"f"])
    assert resp.status == 400
    assert store.part_size(upload_id) == 0


def test_patch_exact_body_without_content_length(app, target):
    upload_id = _new_upload(target, size=10)
    resp = _patch(app, upload_id, 0, b"abcde", content_length=False)
    assert resp.status == 204
    assert resp.headers["upload-offset"] == "5"


def test_patch_short_body_keeps_the_bytes_and_answers_500(app, target):
    upload_id = _new_upload(target, size=10)
    headers = _patch_headers(0, **{"Content-Length": "8"})
    resp = _patch(app, upload_id, 0, b"abcde", headers=headers, content_length=False)
    assert resp.status == 500
    assert store.part_path(upload_id).read_bytes() == b"abcde"
    meta = store.read_metadata(upload_id)
    assert meta["state"] == "active"
    assert meta["offset"] == 5


def _failing_block_writer(error_number, *, after=0):
    original = store._write_block
    state = {"written": 0}

    def writer(file, data):
        if state["written"] >= after:
            raise OSError(error_number, "write failed")
        original(file, data)
        state["written"] += 1

    return writer


def test_patch_disk_full_is_507_and_stays_active(app, target, layer, monkeypatch):
    monkeypatch.setattr(store, "APPEND_BLOCK_SIZE", 2)
    monkeypatch.setattr(store, "_write_block", _failing_block_writer(errno.ENOSPC, after=1))
    upload_id = _new_upload(target, size=10)
    resp = _patch(app, upload_id, 0, b"abcdef")
    assert resp.status == 507
    meta = store.read_metadata(upload_id)
    assert meta["state"] == "active"
    assert meta["offset"] == 2 == store.part_size(upload_id)
    assert [r["offset"] for r in layer.records()] == [2]


def test_patch_disk_full_answers_507_even_when_the_offset_sync_fails(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10)

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "full")

    with monkeypatch.context() as patch:
        patch.setattr(store, "APPEND_BLOCK_SIZE", 2)
        patch.setattr(store, "_write_block", _failing_block_writer(errno.EDQUOT, after=1))
        patch.setattr(store, "atomic_write_json", full)
        resp = _patch(app, upload_id, 0, b"abcdef")
    assert resp.status == 507
    meta = store.read_metadata(upload_id)
    assert meta["state"] == "active"
    assert meta["offset"] == 0  # the sync failed; the .part size stays the truth
    assert store.part_size(upload_id) == 2
    assert layer.sent == []

    # HEAD reads the .part size; the next write re-syncs the offset.
    head = _request(app, "HEAD", upload_id)
    assert head.headers["upload-offset"] == "2"
    assert _patch(app, upload_id, 2, b"cd").status == 204
    assert store.read_metadata(upload_id)["offset"] == 4


def test_patch_other_write_error_is_500_and_stays_active(app, target, monkeypatch):
    monkeypatch.setattr(store, "_write_block", _failing_block_writer(errno.EIO))
    upload_id = _new_upload(target, size=10)
    resp = _patch(app, upload_id, 0, b"abc")
    assert resp.status == 500
    assert store.read_metadata(upload_id)["state"] == "active"


def test_patch_resyncs_a_stale_metadata_offset_without_new_bytes(app, target, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")  # metadata offset still 0
    resp = _patch(app, upload_id, 3, b"0123456789")  # excess: nothing added
    assert resp.status == 400
    assert store.read_metadata(upload_id)["offset"] == 3
    assert [r["offset"] for r in layer.records()] == [3]


# ── HEAD ──────────────────────────────────────────────────────────────────────


def test_head_on_a_partial_active_upload(app, target):
    upload_id = _new_upload(target, size=10, content=b"abcd")
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == 200
    assert resp.headers["upload-offset"] == "4"
    assert resp.headers["upload-length"] == "10"
    assert resp.headers["cache-control"] == "no-store"
    _assert_tus_headers(resp)


def test_head_on_a_completed_tombstone(app, target):
    upload_id = _new_upload(target, size=10, content=b"abcd")
    _set_state(upload_id, state="completed", final_path=str(target / "a.txt"))
    store.part_path(upload_id).unlink()
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == 200
    assert resp.headers["upload-offset"] == resp.headers["upload-length"] == "10"


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_head_on_failed_or_cancelled_is_410(app, target, state):
    upload_id = _new_upload(target)
    _set_state(upload_id, state=state)
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == 410
    assert resp.headers["cache-control"] == "no-store"


def test_head_answers_at_once_while_finalizing_now(app, target):
    """The "finalizing now" set is read without the lock: HEAD does not wait
    for the finalization that holds it."""
    upload_id = _new_upload(target, size=3, content=b"abc")
    _set_state(upload_id, state="finalizing")

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        async with lock:
            with locks.finalizing_now(upload_id):
                return await asyncio.wait_for(_call(app, "HEAD", upload_id), 2)

    resp = _run(scenario())
    assert resp.status == 200
    assert resp.headers["upload-offset"] == resp.headers["upload-length"] == "3"


def test_head_lock_free_rows_do_not_wait_for_the_lock(app, target):
    partial = _new_upload(target, size=10, content=b"ab")
    completed = _new_upload(target, size=10)
    _set_state(completed, state="completed", final_path="/x")
    cancelled = _new_upload(target)
    _set_state(cancelled, state="cancelled")

    async def scenario():
        held = [locks.get_upload_lock(uid) for uid in (partial, completed, cancelled)]
        for lock in held:
            await lock.acquire()
        return [await asyncio.wait_for(_call(app, "HEAD", uid), 2) for uid in (partial, completed, cancelled)]

    assert [r.status for r in _run(scenario())] == [200, 200, 410]


@pytest.mark.parametrize(
    "situation",
    ["active_complete", "active_missing_part", "finalizing_not_in_set", "active_complete_with_error"],
)
def test_head_runs_recovery_under_the_lock(app, target, monkeypatch, situation):
    upload_id = _new_upload(target, size=3, content=b"abc")
    if situation == "active_missing_part":
        store.part_path(upload_id).unlink()
    elif situation == "finalizing_not_in_set":
        _set_state(upload_id, state="finalizing")
    elif situation == "active_complete_with_error":
        _set_state(
            upload_id, error="disk full", finalize_error_code=500, finalize_failed_at="2000-01-01T00:00:00+00:00"
        )
    calls = []

    async def fake_recover(uid):
        calls.append((uid, locks.get_upload_lock(uid).locked()))
        meta = store.update_metadata(uid, state="completed", final_path=str(target / "a.txt"))
        return upload_views.StepOutcome(meta, 200)

    monkeypatch.setattr(upload_views, "recover_upload", fake_recover)
    resp = _request(app, "HEAD", upload_id)
    assert calls == [(upload_id, True)]
    assert resp.status == 200
    assert resp.headers["upload-offset"] == resp.headers["upload-length"] == "3"


@pytest.mark.parametrize(
    ("result", "code", "status", "offset"),
    [
        ({"state": "failed", "error": "target gone"}, 422, 410, None),
        ({"state": "active"}, 200, 200, "1"),  # recovery moved a short .part back to active
        ({"state": "active", "error": "full", "finalize_error_code": 507, "finalize_failed_at": "t"}, 507, 507, None),
        ({"state": "active", "error": "boom", "finalize_error_code": 500, "finalize_failed_at": "t"}, 500, 500, None),
        ({"state": "finalizing"}, 507, 507, None),
        ({"state": "finalizing"}, 500, 500, None),
        ({"state": "finalizing"}, 200, 500, None),  # not settled: never offset = length
    ],
)
def test_head_answer_after_recovery(app, target, monkeypatch, result, code, status, offset):
    upload_id = _new_upload(target, size=3, content=b"abc")

    async def fake_recover(uid):
        if offset is not None:
            store.part_path(uid).write_bytes(b"a")
        return upload_views.StepOutcome(store.update_metadata(uid, **result), code)

    monkeypatch.setattr(upload_views, "recover_upload", fake_recover)
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == status
    if offset is None:
        assert "upload-offset" not in resp.headers
    else:
        assert resp.headers["upload-offset"] == offset
        assert resp.headers["upload-length"] == "3"


def test_head_answers_500_until_recovery_exists(app, target):
    upload_id = _new_upload(target, size=3, content=b"abc")
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == 500
    _assert_tus_headers(resp)
    assert resp.headers["cache-control"] == "no-store"


def test_head_waiting_behind_a_cancel_never_overwrites_the_terminal_state(app, target, monkeypatch):
    """The lock-free read chose recovery; under the lock the state is
    terminal: the operation does nothing and answers from that state."""
    upload_id = _new_upload(target, size=10)
    store.part_path(upload_id).unlink()  # active without .part → recovery path

    async def no_recover(uid):  # pragma: no cover - must not run
        raise AssertionError("recovery must not run on a terminal state")

    monkeypatch.setattr(upload_views, "recover_upload", no_recover)

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        await lock.acquire()
        call = AsgiCall(app, "HEAD", upload_id)
        await asyncio.sleep(0.05)
        store.update_metadata(upload_id, state="cancelled")
        lock.release()
        return await call.response()

    assert _run(scenario()).status == 410
    assert store.read_metadata(upload_id)["state"] == "cancelled"


def test_head_waiting_behind_the_final_patch_answers_completed(app, target, monkeypatch):
    upload_id = _new_upload(target, size=3, content=b"ab")
    events = {}

    async def slow_finalize(uid):
        await events["release"].wait()
        return upload_views.StepOutcome(store.update_metadata(uid, state="completed", final_path="/x"), 204)

    async def no_recover(uid):  # pragma: no cover - must not run
        raise AssertionError("recovery must not run on a terminal state")

    monkeypatch.setattr(upload_views, "finalize_upload", slow_finalize)
    monkeypatch.setattr(upload_views, "recover_upload", no_recover)

    async def scenario():
        events["release"] = asyncio.Event()
        patch = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(2), body=b"c")
        await _until(lambda: store.part_size(upload_id) == 3)
        head = AsgiCall(app, "HEAD", upload_id)  # complete .part, active → recovery path
        await asyncio.sleep(0.05)
        events["release"].set()
        return await patch.response(), await head.response()

    patch_resp, head_resp = _run(scenario())
    assert patch_resp.status == 204
    assert head_resp.status == 200
    assert head_resp.headers["upload-offset"] == "3"
    assert store.read_metadata(upload_id)["state"] == "completed"


@pytest.mark.xfail(strict=True, reason=TASK_4)
def test_head_on_active_without_part_fails_it_under_the_lock(app, target):
    upload_id = _new_upload(target, size=10)
    store.part_path(upload_id).unlink()
    assert _request(app, "HEAD", upload_id).status == 410
    assert store.read_metadata(upload_id)["state"] == "failed"


@pytest.mark.xfail(strict=True, reason=TASK_4)
def test_head_on_finalizing_not_in_the_set_runs_recovery(app, target):
    upload_id = _new_upload(target, size=3, content=b"abc")
    _set_state(upload_id, state="finalizing")
    resp = _request(app, "HEAD", upload_id)
    assert resp.status == 200
    assert (target / "a.txt").read_bytes() == b"abc"
    assert store.read_metadata(upload_id)["state"] == "completed"


# ── DELETE ────────────────────────────────────────────────────────────────────


def test_delete_writes_the_metadata_before_removing_the_part(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10, content=b"abc")
    order = []
    original_update = store.update_metadata
    original_remove = store.remove_best_effort

    def spy_update(uid, **changes):
        order.append(("write", changes.get("state"), store.part_path(uid).exists()))
        return original_update(uid, **changes)

    def spy_remove(path):
        order.append(("remove", str(path)))
        original_remove(path)

    monkeypatch.setattr(store, "update_metadata", spy_update)
    monkeypatch.setattr(store, "remove_best_effort", spy_remove)
    resp = _request(app, "DELETE", upload_id)
    assert resp.status == 204
    _assert_tus_headers(resp)
    assert order == [("write", "cancelled", True), ("remove", str(store.part_path(upload_id)))]
    meta = store.read_metadata(upload_id)
    assert meta["state"] == "cancelled"
    assert meta["offset"] == 0  # a terminal write keeps the last offset
    assert not store.part_path(upload_id).exists()
    assert [r["state"] for r in layer.records()] == ["cancelled"]


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_delete_is_idempotent_on_a_terminal_upload(app, target, layer, state):
    upload_id = _new_upload(target)
    meta = _set_state(upload_id, state=state)
    assert _request(app, "DELETE", upload_id).status == 204
    assert _request(app, "DELETE", upload_id).status == 204
    assert store.read_metadata(upload_id) == meta
    assert layer.sent == []


def test_delete_while_finalizing_now_is_409_at_once(app, target):
    upload_id = _new_upload(target, size=3, content=b"abc")
    _set_state(upload_id, state="finalizing")

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        async with lock:
            with locks.finalizing_now(upload_id):
                return await asyncio.wait_for(_call(app, "DELETE", upload_id), 2)

    assert _run(scenario()).status == 409
    assert store.read_metadata(upload_id)["state"] == "finalizing"


def test_delete_on_finalizing_under_the_lock_is_409(app, target):
    upload_id = _new_upload(target, size=3, content=b"abc")
    _set_state(upload_id, state="finalizing")
    assert _request(app, "DELETE", upload_id).status == 409
    assert store.part_size(upload_id) == 3


def test_delete_on_disk_full_removes_the_part_first(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10, content=b"abc")
    original = store.atomic_write_json
    attempts = []

    def full_once(path, data):
        attempts.append((data["state"], store.part_path(upload_id).exists()))
        if len(attempts) == 1:
            raise OSError(errno.ENOSPC, "full")
        return original(path, data)

    monkeypatch.setattr(store, "atomic_write_json", full_once)
    resp = _request(app, "DELETE", upload_id)
    assert resp.status == 204
    assert attempts == [("cancelled", True), ("cancelled", False)]
    assert store.read_metadata(upload_id)["state"] == "cancelled"
    assert [r["state"] for r in layer.records()] == ["cancelled"]


def test_delete_when_the_second_write_fails_too_is_507(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10, content=b"abc")

    def full(path, data):
        raise OSError(errno.EDQUOT, "quota")

    monkeypatch.setattr(store, "atomic_write_json", full)
    resp = _request(app, "DELETE", upload_id)
    assert resp.status == 507
    assert store.read_metadata(upload_id)["state"] == "active"
    assert not store.part_path(upload_id).exists()
    assert layer.sent == []


def test_delete_other_write_error_is_500_and_keeps_the_part(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10, content=b"abc")

    def broken(path, data):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(store, "atomic_write_json", broken)
    assert _request(app, "DELETE", upload_id).status == 500
    assert store.part_path(upload_id).read_bytes() == b"abc"
    assert layer.sent == []


def test_cancel_after_a_failed_append_sync_has_a_higher_version(app, target, layer, monkeypatch):
    upload_id = _new_upload(target, size=10)
    assert _patch(app, upload_id, 0, b"ab").status == 204  # version 2, broadcast
    with monkeypatch.context() as patch:
        patch.setattr(store, "atomic_write_json", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "x")))
        assert _patch(app, upload_id, 2, b"cd").status == 204  # sync failed: no broadcast
    assert _request(app, "DELETE", upload_id).status == 204
    versions = [r["version"] for r in layer.records()]
    assert versions == [2, 3]
    assert store.read_metadata(upload_id)["version"] == 3


# ── Disconnects through the real ASGIHandler (§5.4, §7) ──────────────────────


def test_disconnect_while_waiting_for_the_lock_appends_nothing(app, target):
    upload_id = _new_upload(target, size=10, content=b"ab")

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        await lock.acquire()
        call = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(2), body=b"cdef")
        await _until(lambda: len(locks._GUARDED_TASKS) == 1)
        call.disconnect()
        response = await call.response()  # the view was cancelled, the body file closed
        still_waiting = len(locks._GUARDED_TASKS)
        lock.release()
        await _wait_guarded_tasks()
        follow = await _call(app, "PATCH", upload_id, headers=_patch_headers(2), body=b"cdef")
        return response, still_waiting, follow

    response, still_waiting, follow = _run(scenario())
    assert response is None  # nothing was sent to the disconnected client
    assert still_waiting == 1
    # The follow-up PATCH continues at the right offset: nothing was appended.
    assert follow.status == 204
    assert store.part_path(upload_id).read_bytes() == b"abcdef"
    assert store.read_metadata(upload_id)["offset"] == 6


def test_disconnect_while_appending_keeps_the_lock_until_the_end(app, target, monkeypatch):
    monkeypatch.setattr(store, "APPEND_BLOCK_SIZE", 4)
    original = store._write_block
    first_block_written = threading.Event()
    resume = threading.Event()

    def slow_writer(file, data):
        original(file, data)
        if not first_block_written.is_set():
            first_block_written.set()
            resume.wait(5)

    monkeypatch.setattr(store, "_write_block", slow_writer)
    upload_id = _new_upload(target, size=100)
    body = b"0123456789abcdef"

    async def scenario():
        lock = locks.get_upload_lock(upload_id)
        call = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0), body=body)
        await asyncio.to_thread(first_block_written.wait, 5)
        call.disconnect()
        response = await call.response()  # Django cancelled the view and closed the body
        held = lock.locked()
        resume.set()
        await _wait_guarded_tasks()
        return response, held, lock.locked()

    response, held_during, held_after = _run(scenario())
    assert response is None
    assert held_during is True  # the operation still runs under its lock
    assert held_after is False
    size = store.part_size(upload_id)
    assert size == 4  # the next read failed on the closed body file
    assert store.part_path(upload_id).read_bytes() == body[:4]
    assert store.read_metadata(upload_id)["offset"] == size

    resume.set()
    assert _patch(app, upload_id, 0, b"x").status == 409
    resp = _patch(app, upload_id, size, body[size:])
    assert resp.status == 204
    assert store.part_path(upload_id).read_bytes() == body


def test_disconnect_during_the_body_appends_nothing(app, target):
    upload_id = _new_upload(target, size=10)

    async def scenario():
        call = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0, **{"Content-Length": "8"}), chunks=[])
        call.inbound.put_nowait({"type": "http.request", "body": b"abcd", "more_body": True})
        call.disconnect()
        return await call.response()

    assert _run(scenario()) is None
    assert store.part_size(upload_id) == 0
    meta = store.read_metadata(upload_id)
    assert meta["version"] == 1
    assert locks._GUARDED_TASKS == set()


def test_two_patches_on_one_upload_do_not_interleave(app, target):
    upload_id = _new_upload(target, size=10)

    async def scenario():
        first = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0), body=b"abc")
        second = AsgiCall(app, "PATCH", upload_id, headers=_patch_headers(0), body=b"xyz")
        return sorted([(await first.response()).status, (await second.response()).status])

    assert _run(scenario()) == [204, 409]
    assert store.part_size(upload_id) == 3


# ── Store helpers ─────────────────────────────────────────────────────────────


def test_peek_metadata(target, data_dir):
    assert store.peek_metadata("x") is None
    assert store.peek_metadata("c" * 32) is None
    assert not (data_dir / "uploads").exists()
    upload_id = _new_upload(target)
    assert store.peek_metadata(upload_id)["id"] == upload_id
    atomic_write_json(store.metadata_path(upload_id), {"broken": True})
    assert store.peek_metadata(upload_id) is None


def test_finalizing_now_set_is_emptied_after_an_exception():
    with pytest.raises(RuntimeError), locks.finalizing_now("d" * 32):
        assert locks.is_finalizing_now("d" * 32)
        raise RuntimeError("boom")
    assert not locks.is_finalizing_now("d" * 32)
