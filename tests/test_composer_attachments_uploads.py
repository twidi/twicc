"""The ``composer`` tus upload origin and the settle rule of composer staging entries.

Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.1.
Each async scenario runs in one ``asyncio.run``: the upload locks are per event loop.
"""

from __future__ import annotations

import asyncio
import errno
import os
import uuid
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient

from twicc.core.models import Project
from twicc.core.services.attachments import lifecycle, staging
from twicc.core.services.attachments.types import AttachmentRef
from twicc.paths import get_composer_attachments_dir
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views

pytestmark = pytest.mark.django_db(transaction=True)

STANDALONE_URL = "/api/uploads/"
TUS_HEADERS = {"Tus-Resumable": "1.0.0"}
OCTET = "application/offset+octet-stream"


# ── Local fixtures (same behaviour as tests/test_uploads_create.py) ──────────


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """An isolated data dir: uploads staging and composer staging live under it."""
    path = tmp_path / "data"
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


class FakeLayer:
    """Records every ``group_send``."""

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
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "proj"
    directory.mkdir()
    return Project.objects.create(id="-tmp-composer-proj", directory=str(directory))


@pytest.fixture
def project_upload_url(project):
    return f"/api/projects/{project.id}/uploads/"


@pytest.fixture
def ref():
    return AttachmentRef("draft-session", str(uuid.uuid4()))


@pytest.fixture
def entry(data_dir, ref):
    return Path(os.path.realpath(get_composer_attachments_dir())) / ref.bucket / ref.id


@pytest.fixture
def release_tombstone(data_dir, ref):
    return get_composer_attachments_dir() / ref.bucket / ".released" / ref.id


# ── Helpers ───────────────────────────────────────────────────────────────────


async def post_composer(client, ref, *, filename="x.bin", size=0, client_id="attempt", url=STANDALONE_URL):
    body = {
        "filename": filename,
        "size": size,
        "client_id": client_id,
        "fingerprint": "test:0",
        "origin": {"panel": "composer", "key": f"{ref.bucket}/{ref.id}"},
    }
    return await client.post(url, data=orjson.dumps(body), content_type="application/json")


async def patch_bytes(client, upload_id, offset, data):
    return await client.patch(
        f"/api/uploads/{upload_id}/",
        data=data,
        content_type=OCTET,
        headers={**TUS_HEADERS, "Upload-Offset": str(offset)},
    )


def _meta(upload_id):
    return store.read_metadata(upload_id)


def _seed_entry(entry, *, content=b"old", markers=("ready.json", "committed.json", "promoted.json")):
    """Put a file and markers in an entry, as a previous attempt or a commit left them."""
    (entry / "file").mkdir(parents=True, exist_ok=True)
    (entry / "file" / "old.bin").write_bytes(content)
    for name in markers:
        (entry / name).write_bytes(b"{}")


def _entry_snapshot(entry):
    return sorted(str(p.relative_to(entry)) for p in entry.rglob("*"))


def _ready(entry):
    return orjson.loads((entry / "ready.json").read_bytes())


async def _locked(upload_id, coro_factory):
    async with locks.get_upload_lock(upload_id):
        return await coro_factory()


async def _complete_part(upload_id, data):
    store.part_path(upload_id).write_bytes(data)


# ── Creation contract ────────────────────────────────────────────────────────


async def assert_creation_contract(client, ref, project_upload_url, release_tombstone):
    first = await post_composer(client, ref, client_id="same-attempt")
    assert first.status_code == 201
    repeat = await post_composer(client, ref, client_id="same-attempt")
    assert repeat.status_code == 200
    assert repeat.json() == first.json()
    scoped = await post_composer(client, ref, url=project_upload_url)
    assert scoped.status_code == 400
    release_tombstone.parent.mkdir(parents=True, exist_ok=True)
    release_tombstone.touch()
    refused = await post_composer(client, ref, client_id="different-attempt")
    assert refused.status_code == 410


def test_composer_creation_contract(client, ref, project_upload_url, release_tombstone):
    asyncio.run(assert_creation_contract(client, ref, project_upload_url, release_tombstone))


def test_session_scoped_route_refuses_composer(client, ref, project):
    url = f"/api/projects/{project.id}/sessions/some-session/uploads/"
    response = asyncio.run(post_composer(client, ref, url=url))
    assert response.status_code == 400
    assert not store.list_metadata()


def test_creation_without_target_dir_or_root(client, ref, entry):
    async def scenario():
        return await post_composer(client, ref, filename="  a/b\nc.txt ", size=4)

    response = asyncio.run(scenario())
    assert response.status_code == 201
    record = response.json()
    meta = _meta(record["id"])
    assert meta["scope"] == {"kind": "composer"}
    assert meta["target_dir"] == str(entry / "file")
    assert meta["filename"] == "a_b_c.txt"
    assert meta["origin"] == {"panel": "composer", "key": f"{ref.bucket}/{ref.id}"}
    assert (entry / "file").is_dir()
    assert list((entry / "file").iterdir()) == []


@pytest.mark.parametrize("extra", [{"target_dir": "/tmp"}, {"root": "/tmp"}])
def test_composer_refuses_a_client_target(client, ref, extra):
    body = {
        "filename": "x.bin",
        "size": 1,
        "client_id": "c1",
        "fingerprint": "test:0",
        "origin": {"panel": "composer", "key": f"{ref.bucket}/{ref.id}"},
        **extra,
    }
    response = asyncio.run(client.post(STANDALONE_URL, data=orjson.dumps(body), content_type="application/json"))
    assert response.status_code == 400
    assert not store.list_metadata()


@pytest.mark.parametrize("key", ["bucket/not-a-uuid", "no-slash", "../x/" + str(uuid.uuid4()), "/" + str(uuid.uuid4())])
def test_composer_refuses_an_invalid_key(client, key):
    body = {
        "filename": "x.bin",
        "size": 1,
        "client_id": "c1",
        "fingerprint": "test:0",
        "origin": {"panel": "composer", "key": key},
    }
    response = asyncio.run(client.post(STANDALONE_URL, data=orjson.dumps(body), content_type="application/json"))
    assert response.status_code == 400
    assert not get_composer_attachments_dir().exists()


@pytest.mark.parametrize("panel", ["files", "artifacts"])
@pytest.mark.parametrize("filename", ["a/b", "a\nb", ".twicc-upload-x.tmp", ".."])
def test_files_and_artifacts_filename_refusal_unchanged(client, tmp_path, panel, filename):
    target = tmp_path / "target"
    target.mkdir()
    body = {
        "filename": filename,
        "size": 1,
        "target_dir": str(target),
        "client_id": "c1",
        "fingerprint": "test:0",
        "origin": {"panel": panel, "key": "k"},
    }
    response = asyncio.run(client.post(STANDALONE_URL, data=orjson.dumps(body), content_type="application/json"))
    if filename == "a\nb":
        # The Files/Artifacts origins never normalize: a control character is kept as is.
        assert response.status_code == 201
        assert _meta(response.json()["id"])["filename"] == "a\nb"
    else:
        assert response.status_code == 400


def test_idempotence_lookup_runs_before_the_release_tombstone_check(client, ref, release_tombstone):
    async def scenario():
        first = await post_composer(client, ref, client_id="same-attempt", size=3)
        release_tombstone.parent.mkdir(parents=True, exist_ok=True)
        release_tombstone.touch()
        repeat = await post_composer(client, ref, client_id="same-attempt", size=3)
        return first, repeat

    first, repeat = asyncio.run(scenario())
    assert first.status_code == 201
    assert repeat.status_code == 200
    assert repeat.json() == first.json()


def test_release_tombstone_creates_nothing(client, ref, entry, release_tombstone):
    release_tombstone.parent.mkdir(parents=True, exist_ok=True)
    release_tombstone.touch()
    response = asyncio.run(post_composer(client, ref, size=3))
    assert response.status_code == 410
    assert not entry.exists()
    assert not store.list_metadata()


def test_composer_creation_order(client, ref, monkeypatch):
    calls = []

    def recorder(name, real):
        def sync(*args, **kwargs):
            calls.append(name)
            return real(*args, **kwargs)

        async def coro(*args, **kwargs):
            calls.append(name)
            return await real(*args, **kwargs)

        return coro if asyncio.iscoroutinefunction(real) else sync

    monkeypatch.setattr(upload_views, "_find_by_client_id", recorder("idempotence", upload_views._find_by_client_id))
    monkeypatch.setattr(lifecycle, "is_released", recorder("tombstone", lifecycle.is_released))
    monkeypatch.setattr(staging, "normalize_filename", recorder("normalize", staging.normalize_filename))
    monkeypatch.setattr(lifecycle, "settle_entry_uploads", recorder("settle", lifecycle.settle_entry_uploads))
    monkeypatch.setattr(lifecycle, "reset_entry", recorder("reset", lifecycle.reset_entry))
    monkeypatch.setattr(upload_views, "_check_target_sync", recorder("target_checks", upload_views._check_target_sync))
    monkeypatch.setattr(store, "create_upload", recorder("create_upload", store.create_upload))

    response = asyncio.run(post_composer(client, ref, size=3))
    assert response.status_code == 201
    assert calls == ["idempotence", "tombstone", "normalize", "settle", "reset", "target_checks", "create_upload"]


def test_failed_target_check_leaves_the_entry_reset_without_upload(client, ref, entry, monkeypatch):
    _seed_entry(entry)
    monkeypatch.setattr(
        upload_views,
        "_check_target_sync",
        lambda *args: upload_views._error("The target directory is not writable", 403),
    )
    response = asyncio.run(post_composer(client, ref, size=3))
    assert response.status_code == 403
    assert _entry_snapshot(entry) == ["file"]
    assert not store.list_metadata()


# ── Settle rule ──────────────────────────────────────────────────────────────


def test_new_attempt_cancels_the_previous_one_and_resets_the_entry(client, ref, entry, layer):
    async def scenario():
        first = await post_composer(client, ref, client_id="attempt-1", size=5)
        _seed_entry(entry)
        second = await post_composer(client, ref, client_id="attempt-2", size=5)
        return first.json(), second

    first, second = asyncio.run(scenario())
    assert second.status_code == 201
    assert _meta(first["id"])["state"] == store.STATE_CANCELLED
    assert store.part_size(first["id"]) is None
    assert _meta(second.json()["id"])["state"] == store.STATE_ACTIVE
    assert _entry_snapshot(entry) == ["file"]
    assert any(r["id"] == first["id"] and r["state"] == store.STATE_CANCELLED for r in layer.records())


def test_settle_waits_for_a_running_finalization_that_completes(client, ref, entry):
    async def scenario():
        first = (await post_composer(client, ref, client_id="attempt-1", size=5)).json()
        lock = locks.get_upload_lock(first["id"])
        await lock.acquire()
        try:
            creation = asyncio.create_task(post_composer(client, ref, client_id="attempt-2", size=5))
            await asyncio.sleep(0.05)
            assert not creation.done()
            await _complete_part(first["id"], b"12345")
            outcome = await upload_views.finalize_upload(first["id"])
            assert outcome.code == 204
            assert _ready(entry) == {"filename": "x.bin", "size": 5}
        finally:
            lock.release()
        return first, await creation

    first, second = asyncio.run(scenario())
    assert second.status_code == 201
    # Completed before the settle: not cancelled; the entry is still reset for the new attempt.
    assert _meta(first["id"])["state"] == store.STATE_COMPLETED
    assert _entry_snapshot(entry) == ["file"]


def test_settle_cancels_a_waited_for_finalization_that_ended_active(client, ref, entry):
    async def scenario():
        first = (await post_composer(client, ref, client_id="attempt-1", size=5)).json()
        lock = locks.get_upload_lock(first["id"])
        await lock.acquire()
        try:
            creation = asyncio.create_task(post_composer(client, ref, client_id="attempt-2", size=5))
            await asyncio.sleep(0.05)
            assert not creation.done()
            # The finalization failed with an unexpected error: back to ``active`` with ``error``.
            await asyncio.to_thread(
                store.update_metadata,
                first["id"],
                state=store.STATE_ACTIVE,
                error="Unexpected error",
                finalize_error_code=500,
                finalize_failed_at=store.now_iso(),
            )
        finally:
            lock.release()
        return first, await creation

    first, second = asyncio.run(scenario())
    assert second.status_code == 201
    assert _meta(first["id"])["state"] == store.STATE_CANCELLED


@pytest.mark.parametrize(
    ("recovered_state", "expect_cancel"),
    [(store.STATE_COMPLETED, False), (store.STATE_ACTIVE, True), (store.STATE_FINALIZING, True)],
)
def test_crashed_finalizing_is_recovered_before_the_cancel(
    client, ref, entry, monkeypatch, recovered_state, expect_cancel
):
    recovered = []
    cancelled = []
    real_cancel = store.cancel_upload

    async def fake_recover(upload_id):
        recovered.append((upload_id, _meta(upload_id)["state"]))
        meta = await asyncio.to_thread(store.update_metadata, upload_id, state=recovered_state)
        return upload_views.StepOutcome(meta, 200)

    def recording_cancel(upload_id):
        cancelled.append((upload_id, _meta(upload_id)["state"]))
        return real_cancel(upload_id)

    monkeypatch.setattr(upload_views, "recover_upload", fake_recover)
    monkeypatch.setattr(store, "cancel_upload", recording_cancel)

    async def scenario():
        first = (await post_composer(client, ref, client_id="attempt-1", size=5)).json()
        # A crash left the upload ``finalizing`` (not "finalizing now").
        await asyncio.to_thread(store.update_metadata, first["id"], state=store.STATE_FINALIZING)
        second = await post_composer(client, ref, client_id="attempt-2", size=5)
        return first, second

    first, second = asyncio.run(scenario())
    assert second.status_code == 201
    assert recovered == [(first["id"], store.STATE_FINALIZING)]
    if expect_cancel:
        assert cancelled == [(first["id"], recovered_state)]
        assert _meta(first["id"])["state"] == store.STATE_CANCELLED
    else:
        assert cancelled == []
        assert _meta(first["id"])["state"] == store.STATE_COMPLETED


def test_cancel_write_failure_keeps_the_entry_and_answers_500(client, ref, entry, monkeypatch):
    async def scenario():
        first = (await post_composer(client, ref, client_id="attempt-1", size=5)).json()
        _seed_entry(entry)
        before = _entry_snapshot(entry)
        monkeypatch.setattr(store, "cancel_upload", lambda upload_id: store.WriteOutcome(None, 507))
        second = await post_composer(client, ref, client_id="attempt-2", size=5)
        return first, before, second

    first, before, second = asyncio.run(scenario())
    assert second.status_code == 500
    assert _entry_snapshot(entry) == before
    assert (entry / "file" / "old.bin").read_bytes() == b"old"
    assert _meta(first["id"])["state"] == store.STATE_ACTIVE
    assert [m["client_id"] for m in store.list_metadata()] == ["attempt-1"]


def test_settle_entry_uploads_ignores_other_entries(client, ref):
    other = AttachmentRef(ref.bucket, str(uuid.uuid4()))

    async def scenario():
        mine = (await post_composer(client, ref, client_id="mine", size=5)).json()
        theirs = (await post_composer(client, other, client_id="theirs", size=5)).json()
        async with lifecycle.composer_creation_guard():
            await lifecycle.settle_entry_uploads(ref)
        return mine, theirs

    mine, theirs = asyncio.run(scenario())
    assert _meta(mine["id"])["state"] == store.STATE_CANCELLED
    assert _meta(theirs["id"])["state"] == store.STATE_ACTIVE


def test_settle_failure_raises(client, ref, monkeypatch):
    async def scenario():
        await post_composer(client, ref, client_id="mine", size=5)
        monkeypatch.setattr(store, "cancel_upload", lambda upload_id: store.WriteOutcome(None, 500))
        async with lifecycle.composer_creation_guard():
            await lifecycle.settle_entry_uploads(ref)

    with pytest.raises(lifecycle.SettleError):
        asyncio.run(scenario())


def test_creation_guard_is_the_upload_creation_lock():
    async def scenario():
        async with lifecycle.composer_creation_guard():
            assert locks.get_creation_lock().locked()
        assert not locks.get_creation_lock().locked()

    asyncio.run(scenario())


# ── Disk full, zero-byte ────────────────────────────────────────────────────


def test_disk_full_creation_answers_507_without_live_upload(client, ref, monkeypatch):
    def full(upload_id, **fields):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(store, "create_upload", full)
    response = asyncio.run(post_composer(client, ref, size=3))
    assert response.status_code == 507
    assert [m for m in store.list_metadata() if not store.is_terminal(m["state"])] == []


def test_disk_space_check_answers_507(client, ref, monkeypatch):
    usage = upload_views.shutil.disk_usage

    monkeypatch.setattr(upload_views.shutil, "disk_usage", lambda path: usage(path)._replace(free=0))
    response = asyncio.run(post_composer(client, ref, size=3))
    assert response.status_code == 507
    assert not store.list_metadata()


def test_zero_byte_composer_upload_completes_ready(client, ref, entry):
    response = asyncio.run(post_composer(client, ref, filename="empty.txt", size=0))
    assert response.status_code == 201
    record = response.json()
    assert record["state"] == store.STATE_COMPLETED
    assert record["final_path"] == str(entry / "file" / "empty.txt")
    assert _ready(entry) == {"filename": "empty.txt", "size": 0}


# ── Completion hook ordering ─────────────────────────────────────────────────


@pytest.fixture
def hook_calls(monkeypatch, layer):
    """Record, at each hook call, the state on disk and whether ``completed`` was broadcast."""
    calls = []
    real = staging.on_upload_completed

    def recording(meta, final_path):
        calls.append(
            {
                "state": _meta(meta["id"])["state"],
                "completed_broadcast": any(r["state"] == store.STATE_COMPLETED for r in layer.records()),
                "final_path": str(final_path),
            }
        )
        return real(meta, final_path)

    monkeypatch.setattr(staging, "on_upload_completed", recording)
    return calls


def _assert_hook_before_completed(hook_calls):
    assert hook_calls
    for call in hook_calls:
        assert call["state"] != store.STATE_COMPLETED
        assert not call["completed_broadcast"]


def test_finalization_writes_ready_before_completed(client, ref, entry, hook_calls):
    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        response = await patch_bytes(client, record["id"], 0, b"12345")
        return record, response

    record, response = asyncio.run(scenario())
    assert response.status_code == 204
    assert _meta(record["id"])["state"] == store.STATE_COMPLETED
    assert _ready(entry) == {"filename": "x.bin", "size": 5}
    _assert_hook_before_completed(hook_calls)


def test_zero_byte_creation_writes_ready_before_completed(client, ref, entry, hook_calls):
    response = asyncio.run(post_composer(client, ref, size=0))
    assert response.json()["state"] == store.STATE_COMPLETED
    _assert_hook_before_completed(hook_calls)


def _committed_finalizing(client, ref, entry, *, renamed=False):
    """A composer upload left ``finalizing`` after its commit write (link placed, ``final_path`` set)."""

    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        name = "x (1).bin" if renamed else "x.bin"
        final = entry / "file" / name
        os.link(store.part_path(upload_id), final)
        await asyncio.to_thread(
            store.update_metadata,
            upload_id,
            state=store.STATE_FINALIZING,
            final_path=str(final),
            final_method=store.FINAL_METHOD_LINK,
        )
        return upload_id, final

    return asyncio.run(scenario())


def test_committed_recovery_writes_ready_before_completed(client, ref, entry, hook_calls):
    upload_id, final = _committed_finalizing(client, ref, entry)
    outcome = asyncio.run(_locked(upload_id, lambda: upload_views.recover_upload(upload_id)))
    assert outcome.meta["state"] == store.STATE_COMPLETED
    assert _ready(entry) == {"filename": "x.bin", "size": 5}
    _assert_hook_before_completed(hook_calls)


def test_committed_replace_recovery_writes_ready_before_completed(client, ref, entry, hook_calls):
    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        final = entry / "file" / "x.bin"
        final.touch()  # the empty reservation
        await asyncio.to_thread(
            store.update_metadata,
            upload_id,
            state=store.STATE_FINALIZING,
            final_path=str(final),
            final_method=store.FINAL_METHOD_REPLACE,
            final_source=store.FINAL_SOURCE_PART,
        )
        return await _locked(upload_id, lambda: upload_views.recover_upload(upload_id))

    outcome = asyncio.run(scenario())
    assert outcome.meta["state"] == store.STATE_COMPLETED
    assert _ready(entry) == {"filename": "x.bin", "size": 5}
    _assert_hook_before_completed(hook_calls)


def _uncommitted_finalizing(client, ref, entry, name="x (1).bin"):
    """A composer upload left ``finalizing`` before its commit write: the link exists, ``final_path`` is unset."""

    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        os.link(store.part_path(upload_id), entry / "file" / name)
        await asyncio.to_thread(store.update_metadata, upload_id, state=store.STATE_FINALIZING)
        return upload_id

    return asyncio.run(scenario())


def test_uncommitted_recovery_writes_ready_before_completed(client, ref, entry, hook_calls):
    upload_id = _uncommitted_finalizing(client, ref, entry)
    outcome = asyncio.run(_locked(upload_id, lambda: upload_views.recover_upload(upload_id)))
    assert outcome.meta["state"] == store.STATE_COMPLETED
    assert outcome.meta["final_path"] == str(entry / "file" / "x (1).bin")
    assert _ready(entry) == {"filename": "x (1).bin", "size": 5}
    _assert_hook_before_completed(hook_calls)
    assert hook_calls[0]["final_path"] == str(entry / "file" / "x (1).bin")


# ── Completion hook failure ─────────────────────────────────────────────────


@pytest.fixture
def failing_hook_once(monkeypatch):
    real = staging.on_upload_completed
    failures = []

    def hook(meta, final_path):
        if not failures:
            failures.append(str(final_path))
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return real(meta, final_path)

    monkeypatch.setattr(staging, "on_upload_completed", hook)
    return failures


def test_hook_failure_in_finalization_keeps_finalizing_then_recovers(client, ref, entry, failing_hook_once, layer):
    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        response = await patch_bytes(client, upload_id, 0, b"12345")
        assert response.status_code == 500
        meta = _meta(upload_id)
        assert meta["state"] == store.STATE_FINALIZING
        assert meta["final_path"] == str(entry / "file" / "x.bin")
        assert not (entry / "ready.json").exists()
        assert not any(r["state"] == store.STATE_COMPLETED for r in layer.records())
        return upload_id, await _locked(upload_id, lambda: upload_views.recover_upload(upload_id))

    upload_id, outcome = asyncio.run(scenario())
    assert outcome.meta["state"] == store.STATE_COMPLETED
    assert _ready(entry) == {"filename": "x.bin", "size": 5}


def test_hook_failure_in_uncommitted_recovery_keeps_finalizing_then_recovers(client, ref, entry, failing_hook_once):
    upload_id = _uncommitted_finalizing(client, ref, entry)
    found = str(entry / "file" / "x (1).bin")

    first = asyncio.run(_locked(upload_id, lambda: upload_views.recover_upload(upload_id)))
    assert first.code == 500
    assert failing_hook_once == [found]
    meta = _meta(upload_id)
    assert meta["state"] == store.STATE_FINALIZING
    assert meta["final_path"] is None
    assert not (entry / "ready.json").exists()

    second = asyncio.run(_locked(upload_id, lambda: upload_views.recover_upload(upload_id)))
    assert second.meta["state"] == store.STATE_COMPLETED
    assert second.meta["final_path"] == found
    assert _ready(entry) == {"filename": "x (1).bin", "size": 5}


def test_permanent_hook_conditions_do_not_block_completion(entry, ref):
    """A non-dict origin or an invalid key is not a retryable error: the hook returns without raising."""
    staging.on_upload_completed({"origin": "composer", "size": 1}, "/nowhere")
    staging.on_upload_completed({"origin": {"panel": "composer", "key": "bad"}, "size": 1}, "/nowhere")
    staging.on_upload_completed({"origin": {"panel": "composer", "key": 5}, "size": 1}, "/nowhere")


# ── Re-validation of the composer target ─────────────────────────────────────


def test_finalization_of_a_removed_entry_fails(client, ref, entry):
    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        for path in sorted(entry.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        entry.rmdir()
        return await _locked(upload_id, lambda: upload_views.finalize_upload(upload_id))

    outcome = asyncio.run(scenario())
    assert outcome.code == 422
    assert outcome.meta["state"] == store.STATE_FAILED
    assert not entry.exists()


def test_finalization_refuses_a_foreign_target(client, ref, entry, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()

    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        meta = _meta(upload_id)
        meta["target_dir"] = str(other)
        store.atomic_write_json(store.metadata_path(upload_id), meta)
        return await _locked(upload_id, lambda: upload_views.finalize_upload(upload_id))

    outcome = asyncio.run(scenario())
    assert outcome.code == 422
    assert list(other.iterdir()) == []


def test_finalization_refuses_a_released_entry(client, ref, entry, release_tombstone):
    async def scenario():
        record = (await post_composer(client, ref, size=5)).json()
        upload_id = record["id"]
        await _complete_part(upload_id, b"12345")
        release_tombstone.parent.mkdir(parents=True, exist_ok=True)
        release_tombstone.touch()
        return await _locked(upload_id, lambda: upload_views.finalize_upload(upload_id))

    outcome = asyncio.run(scenario())
    assert outcome.code == 422
    assert not (entry / "ready.json").exists()


# ── Entry mtime refresh ──────────────────────────────────────────────────────


OLD = 1_000_000_000


def test_creation_and_progress_refresh_the_entry_mtime(client, ref, entry):
    async def scenario():
        record = (await post_composer(client, ref, size=10)).json()
        upload_id = record["id"]
        assert entry.stat().st_mtime > OLD + 1

        (entry / "committed.json").write_bytes(b"{}")
        os.utime(entry, (OLD, OLD))
        response = await patch_bytes(client, upload_id, 0, b"12345")
        assert response.status_code == 204
        assert entry.stat().st_mtime > OLD + 1
        # Touching never changes the committed ownership.
        assert (entry / "committed.json").exists()

        # An entry removed meanwhile is never re-created by progress.
        for path in sorted(entry.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        entry.rmdir()
        response = await patch_bytes(client, upload_id, 5, b"678")
        assert response.status_code == 204
        assert not entry.exists()

    asyncio.run(scenario())


def test_progress_of_another_origin_touches_nothing(client, tmp_path, ref, entry):
    target = tmp_path / "target"
    target.mkdir()
    entry.mkdir(parents=True)
    os.utime(entry, (OLD, OLD))

    async def scenario():
        body = {
            "filename": "a.txt",
            "size": 10,
            "target_dir": str(target),
            "client_id": "files-1",
            "fingerprint": "test:0",
            "origin": {"panel": "files", "key": f"{ref.bucket}/{ref.id}"},
        }
        record = (await client.post(STANDALONE_URL, data=orjson.dumps(body), content_type="application/json")).json()
        response = await patch_bytes(client, record["id"], 0, b"12345")
        assert response.status_code == 204

    asyncio.run(scenario())
    assert entry.stat().st_mtime == OLD


def test_long_names_are_truncated_to_fit_pc_name_max(client, ref, monkeypatch):
    real_pathconf = os.pathconf
    monkeypatch.setattr(os, "pathconf", lambda path, name: 20 if name == "PC_NAME_MAX" else real_pathconf(path, name))
    response = asyncio.run(post_composer(client, ref, filename="a" * 30 + ".txt", size=1))
    assert response.status_code == 201
    assert _meta(response.json()["id"])["filename"] == "a" * 8 + ".txt"  # 12 bytes + 8 of suffix room
