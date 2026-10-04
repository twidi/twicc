"""Daily retention of the composer staging store and of the artifacts pre-copies.

Design: docs/plans/2026-10-03-composer-attachments-any-file-design.md §6.1.4 "Server-side retention".
Every age is measured against an injected clock: no test sleeps.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest

from twicc import composer_attachments_cleanup_task as cleanup
from twicc.core.services.attachments import lifecycle
from twicc.core.services.attachments.types import AttachmentRef
from twicc.paths import get_artifacts_dir, get_composer_attachments_dir
from twicc.uploads import locks, store

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


class FakeLayer:
    async def group_send(self, group, message):
        pass


@pytest.fixture(autouse=True)
def layer(monkeypatch):
    monkeypatch.setattr("twicc.uploads.broadcast.get_channel_layer", lambda: FakeLayer())


@pytest.fixture(autouse=True)
def fresh_upload_locks():
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()
    yield
    locks._loop_locks.clear()
    locks._FINALIZING_NOW.clear()


@pytest.fixture(autouse=True)
def sweep_enabled(settings):
    settings.SESSION_DIRS_CLEANUP_ENABLED = True
    return settings


def new_ref(bucket="draft-session"):
    return AttachmentRef(bucket, str(uuid.uuid4()))


def root():
    return Path(os.path.realpath(get_composer_attachments_dir()))


def entry_path(ref):
    return root() / ref.bucket / ref.id


def set_mtime(path, when: datetime):
    ts = when.timestamp()
    os.utime(path, (ts, ts), follow_symlinks=False)


def make_entry(ref, *, age: timedelta, committed=False):
    entry = entry_path(ref)
    (entry / "file").mkdir(parents=True, exist_ok=True)
    (entry / "file" / "a.bin").write_bytes(b"abc")
    (entry / "ready.json").write_bytes(orjson.dumps({"filename": "a.bin", "size": 3}))
    if committed:
        (entry / "committed.json").write_bytes(b'{"at": "x"}')
    set_mtime(entry, NOW - age)
    return entry


def make_tombstone(ref, *, age: timedelta):
    path = root() / ref.bucket / ".released" / ref.id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    set_mtime(path, NOW - age)
    return path


def make_live_upload(ref, state=store.STATE_ACTIVE):
    upload_id = store.new_upload_id()
    store.create_upload(
        upload_id,
        client_id=f"c-{upload_id[:8]}",
        size=10,
        filename="a.bin",
        target_dir=str(entry_path(ref) / "file"),
        scope={"kind": "composer"},
        origin={"panel": "composer", "key": f"{ref.bucket}/{ref.id}"},
        fingerprint="test:0",
    )
    if state != store.STATE_ACTIVE:
        store.update_metadata(upload_id, state=state)
    return upload_id


def run_pass(now=NOW):
    return asyncio.run(cleanup.run_cleanup_pass(now=lambda: now))


# ── Entry expiry ──────────────────────────────────────────────────────────────


def test_committed_entries_expire_at_exactly_7_days():
    expired, kept = new_ref(), new_ref()
    make_entry(expired, age=timedelta(days=7), committed=True)
    make_entry(kept, age=timedelta(days=7) - SECOND, committed=True)
    stats = run_pass()
    assert not entry_path(expired).exists()
    assert entry_path(kept).exists()
    assert stats.entries == 1


def test_draft_entries_expire_at_exactly_30_days():
    expired, kept, week_old = new_ref(), new_ref(), new_ref()
    make_entry(expired, age=timedelta(days=30))
    make_entry(kept, age=timedelta(days=30) - SECOND)
    make_entry(week_old, age=timedelta(days=8))
    run_pass()
    assert not entry_path(expired).exists()
    assert entry_path(kept).exists()
    assert entry_path(week_old).exists()


def test_a_recent_touch_keeps_an_entry():
    ref = new_ref()
    make_entry(ref, age=timedelta(days=40), committed=True)
    lifecycle.touch_refs((ref,), holder="snapshot")
    run_pass(now=datetime.now(UTC))
    assert entry_path(ref).exists()
    assert (entry_path(ref) / "committed.json").exists()


def test_a_draft_touch_turns_a_committed_entry_into_a_draft_one():
    ref = new_ref()
    entry = make_entry(ref, age=timedelta(days=8), committed=True)
    lifecycle.touch_refs((ref,), holder="draft")
    assert not (entry / "committed.json").exists()
    set_mtime(entry, NOW - timedelta(days=8))
    run_pass()
    assert entry.exists()


@pytest.mark.parametrize("state", [store.STATE_ACTIVE, store.STATE_FINALIZING])
def test_live_uploads_keep_their_entry_and_are_never_cancelled(state):
    ref = new_ref()
    make_entry(ref, age=timedelta(days=60))
    upload_id = make_live_upload(ref, state)
    set_mtime(entry_path(ref), NOW - timedelta(days=60))
    run_pass()
    assert entry_path(ref).exists()
    assert store.read_metadata(upload_id)["state"] == state


@pytest.mark.parametrize("state", [store.STATE_CANCELLED, store.STATE_FAILED, store.STATE_COMPLETED])
def test_terminal_uploads_do_not_keep_an_entry(state):
    ref = new_ref()
    make_entry(ref, age=timedelta(days=60))
    upload_id = make_live_upload(ref)
    store.update_metadata(upload_id, state=state)
    set_mtime(entry_path(ref), NOW - timedelta(days=60))
    run_pass()
    assert not entry_path(ref).exists()
    assert store.read_metadata(upload_id)["state"] == state


def test_candidates_are_reread_under_the_creation_lock():
    """An upload created while the reaper waits for the creation lock keeps its entry."""
    ref = new_ref()
    make_entry(ref, age=timedelta(days=60))

    async def scenario():
        lock = locks.get_creation_lock()
        await lock.acquire()
        sweep = asyncio.create_task(cleanup.run_cleanup_pass(now=lambda: NOW))
        await asyncio.sleep(0.05)
        assert not sweep.done()
        await asyncio.to_thread(make_live_upload, ref)
        lock.release()
        return await sweep

    asyncio.run(scenario())
    assert entry_path(ref).exists()


def test_the_age_is_reread_under_the_lock():
    ref = new_ref()
    make_entry(ref, age=timedelta(days=60))

    async def scenario():
        lock = locks.get_creation_lock()
        await lock.acquire()
        sweep = asyncio.create_task(cleanup.run_cleanup_pass(now=lambda: NOW))
        await asyncio.sleep(0.05)
        set_mtime(entry_path(ref), NOW)  # a touch arrives meanwhile
        lock.release()
        return await sweep

    asyncio.run(scenario())
    assert entry_path(ref).exists()


def test_unknown_names_are_never_touched():
    bucket = root() / "draft-session"
    bucket.mkdir(parents=True)
    odd_dir = bucket / "not-a-uuid"
    odd_dir.mkdir()
    odd_file = bucket / str(uuid.uuid4())
    odd_file.write_bytes(b"x")
    top_file = root() / "stray.txt"
    top_file.write_bytes(b"x")
    for path in (odd_dir, odd_file, top_file):
        set_mtime(path, NOW - timedelta(days=90))
    run_pass()
    assert odd_dir.exists()
    assert odd_file.exists()
    assert top_file.exists()


def test_symlinked_entries_and_buckets_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    target_id = str(uuid.uuid4())
    (outside / target_id / "file").mkdir(parents=True)
    set_mtime(outside / target_id, NOW - timedelta(days=90))
    root().mkdir(parents=True)
    (root() / "linked-bucket").symlink_to(outside)
    real_bucket = root() / "real-bucket"
    real_bucket.mkdir()
    linked_entry = real_bucket / str(uuid.uuid4())
    linked_entry.symlink_to(outside / target_id)
    set_mtime(linked_entry, NOW - timedelta(days=90))
    run_pass()
    assert (outside / target_id / "file").is_dir()
    assert os.path.islink(root() / "linked-bucket")


# ── Tombstones and empty directories ─────────────────────────────────────────


def test_release_tombstones_expire_after_24_hours_then_empty_dirs_go():
    old, recent = new_ref("bucket-a"), new_ref("bucket-b")
    old_tombstone = make_tombstone(old, age=timedelta(hours=24))
    recent_tombstone = make_tombstone(recent, age=timedelta(hours=24) - SECOND)
    stats = run_pass()
    assert not old_tombstone.exists()
    assert not (root() / "bucket-a" / ".released").exists()
    assert not (root() / "bucket-a").exists()
    assert recent_tombstone.exists()
    assert stats.tombstones == 1


def test_a_bucket_with_a_live_entry_is_kept():
    tombstoned, live = new_ref("shared"), new_ref("shared")
    make_tombstone(tombstoned, age=timedelta(days=2))
    make_entry(live, age=timedelta(days=1))
    run_pass()
    assert not (root() / "shared" / ".released").exists()
    assert entry_path(live).exists()


def test_an_expired_entry_leaves_an_empty_bucket_removed():
    ref = new_ref("lonely")
    make_entry(ref, age=timedelta(days=31))
    run_pass()
    assert not (root() / "lonely").exists()
    assert root().exists()


def test_no_staging_dir_is_not_an_error():
    stats = run_pass()
    assert stats == cleanup.CleanupStats(0, 0, 0, 0)
    assert not root().exists()


# ── Artifacts pre-copies ──────────────────────────────────────────────────────


def _artifact(path: Path, *, age: timedelta, directory=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"x")
    set_mtime(path, NOW - age)
    return path


def test_only_old_top_level_precopies_are_removed(tmp_path):
    artifacts = get_artifacts_dir()
    old = _artifact(artifacts / ".twicc-upload-old.tmp", age=timedelta(hours=24))
    recent = _artifact(artifacts / ".twicc-upload-new.tmp", age=timedelta(hours=24) - SECOND)
    nested = _artifact(artifacts / "sess" / ".twicc-upload-nested.tmp", age=timedelta(days=3))
    other = _artifact(artifacts / "report.txt", age=timedelta(days=3))
    other_tmp = _artifact(artifacts / "x.tmp", age=timedelta(days=3))
    a_dir = _artifact(artifacts / ".twicc-upload-dir.tmp", age=timedelta(days=3), directory=True)
    target = tmp_path / "target.bin"
    target.write_bytes(b"x")
    link = artifacts / ".twicc-upload-link.tmp"
    link.symlink_to(target)
    set_mtime(link, NOW - timedelta(days=3))
    stats = run_pass()
    assert not old.exists()
    for path in (recent, nested, other, other_tmp, a_dir, target):
        assert path.exists()
    assert os.path.islink(link)
    assert stats.precopies == 1


def test_the_artifacts_sweep_is_gated_for_shared_worktree_symlinks(tmp_path, sweep_enabled):
    sweep_enabled.SESSION_DIRS_CLEANUP_ENABLED = False
    shared = tmp_path / "main-artifacts"
    shared.mkdir()
    get_artifacts_dir().symlink_to(shared)
    precopy = _artifact(shared / ".twicc-upload-main.tmp", age=timedelta(days=3))
    ref = new_ref()
    make_entry(ref, age=timedelta(days=31))
    stats = run_pass()
    assert precopy.exists()
    assert stats.precopies == 0
    # The staging store, owned by this instance, is still swept.
    assert not entry_path(ref).exists()


# ── Loop ──────────────────────────────────────────────────────────────────────


def test_the_task_stops_on_the_shutdown_event():
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(cleanup.start_composer_attachments_cleanup_task(stop))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_the_task_runs_a_pass_then_waits_a_day(monkeypatch):
    calls = []

    async def fake_pass(**kwargs):
        calls.append(kwargs)
        stop.set()

    monkeypatch.setattr(cleanup, "COMPOSER_CLEANUP_FIRST_DELAY", 0)
    monkeypatch.setattr(cleanup, "run_cleanup_pass", fake_pass)
    stop = None

    async def scenario():
        nonlocal stop
        stop = asyncio.Event()
        await asyncio.wait_for(cleanup.start_composer_attachments_cleanup_task(stop), 2)

    asyncio.run(scenario())
    assert len(calls) == 1
    assert cleanup.COMPOSER_CLEANUP_INTERVAL == 24 * 60 * 60
