"""The upload janitor (design 2026-09-28-file-upload §5.9, §5.7 "Recovery
runs", §5.1 unparsable ``.json``).

Ages are controlled with the injectable clock of ``run_cleanup_pass``: a pass
"in 8 days" sees every ``last_transfer_at`` / ``updated_at`` / mtime written
now as 8 days old.
"""

from __future__ import annotations

import asyncio
import errno
import os
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_uploads_transfer import FakeLayer, _new_upload, _run
from twicc import upload_cleanup_task as janitor
from twicc.uploads import locks, store
from twicc.uploads import views as upload_views

pytestmark = pytest.mark.django_db(transaction=True)

DATA = b"hello world"


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
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


@pytest.fixture
def recoveries(monkeypatch):
    """Spy on the recovery step the janitor runs (the real step still runs)."""
    real = upload_views.recover_upload
    calls = []

    async def spy(upload_id):
        calls.append(upload_id)
        return await real(upload_id)

    monkeypatch.setattr(upload_views, "recover_upload", spy)
    return calls


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clock(delta=timedelta(0)):
    return lambda: datetime.now(UTC) + delta


IN_25_HOURS = _clock(timedelta(hours=25))
IN_2_HOURS = _clock(timedelta(hours=2))


def _pass(first_pass=False, now=None):
    return _run(janitor.run_cleanup_pass(first_pass=first_pass, now=now or _clock()))


def _meta(upload_id):
    return store.read_metadata(upload_id)


def _complete(target, data=DATA):
    return _new_upload(target, size=len(data), content=data)


def _temp(upload_id, target):
    return target / f".twicc-upload-{upload_id}.tmp"


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


def _finalize_failing(monkeypatch, code=507):
    """Every finalization fails before the commit point with *code*."""

    def fake(upload_id, current):
        return store.finalize_precommit_failure(upload_id, current, code, "Not enough disk space")

    monkeypatch.setattr(store, "finalize_files", fake)


def _recovery_failing(monkeypatch):
    """Recovery keeps failing: its thread part answers ``500`` and changes nothing."""

    def fake(upload_id):
        return store.RecoveryVerdict(store.RECOVERY_ANSWER, store.peek_metadata(upload_id), 500, False)

    monkeypatch.setattr(store, "recover_files", fake)


def _stale(upload_id, days=8):
    """Move ``last_transfer_at`` *days* back (the expiry reference)."""
    store.update_metadata(upload_id, last_transfer_at=(datetime.now(UTC) - timedelta(days=days)).isoformat())
    return upload_id


def _age_file(path, delta):
    stamp = (datetime.now(UTC) - delta).timestamp()
    os.utime(path, (stamp, stamp))


# ── Pass order and recovery ───────────────────────────────────────────────────


def test_recovery_runs_before_expiry(target, layer):
    """A complete transfer older than 7 days left by a crash is finalized, not expired."""
    upload_id = _stale(_complete(target))
    stats = _pass(first_pass=False)
    assert _meta(upload_id)["state"] == "completed"
    assert (target / "a.txt").read_bytes() == DATA
    assert stats.recovered == 1
    assert stats.expired == 0
    assert [r["state"] for r in layer.records()] == ["finalizing", "completed"]


def test_old_finalizing_upload_is_recovered_not_expired(target):
    upload_id = _complete(target)
    store.update_metadata(upload_id, state="finalizing")
    _stale(upload_id)
    _pass()
    assert _meta(upload_id)["state"] == "completed"


def test_complete_upload_whose_finalization_always_fails_still_expires(target, monkeypatch, layer):
    upload_id = _stale(_complete(target))
    _finalize_failing(monkeypatch)
    stats = _pass()
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"] == "expired"
    assert not store.part_path(upload_id).exists()
    assert stats == janitor.PassStats(1, 1, 0, 0, 0, 0)
    assert [r["state"] for r in layer.records()] == ["finalizing", "active", "failed"]


def test_first_pass_recovers_every_non_terminal_upload(target, recoveries):
    short = _new_upload(target, size=10, content=b"abc")
    finalizing = _new_upload(target, size=10, content=b"abc")
    store.update_metadata(finalizing, state="finalizing")
    missing = _new_upload(target, size=10)
    store.part_path(missing).unlink()
    _pass(first_pass=True)
    assert sorted(recoveries) == sorted([short, finalizing, missing])
    assert _meta(short)["state"] == "active"
    assert _meta(finalizing)["state"] == "active"
    assert _meta(missing)["state"] == "failed"
    assert _meta(missing)["error"] == "staging file lost"


def test_recovery_count_ignores_no_op_recoveries(target, recoveries):
    upload_id = _new_upload(target, size=10, content=b"abc")
    stats = _pass(first_pass=True)
    assert recoveries == [upload_id]  # recovery ran
    assert stats.recovered == 0  # but changed nothing
    assert _meta(upload_id)["version"] == 1


def test_first_pass_skips_an_active_upload_with_error(target, recoveries, monkeypatch):
    upload_id = _complete(target)
    store.update_metadata(
        upload_id, error="Not enough disk space", finalize_error_code=507, finalize_failed_at=store.now_iso()
    )
    _pass(first_pass=True)
    assert recoveries == []
    assert _meta(upload_id)["state"] == "active"
    assert not (target / "a.txt").exists()


def test_later_pass_selection(target, recoveries):
    finalizing = _new_upload(target, size=10, content=b"abc")
    store.update_metadata(finalizing, state="finalizing")
    missing = _new_upload(target, size=10)
    store.part_path(missing).unlink()
    complete = _complete(target)
    short = _new_upload(target, size=10, content=b"abc")
    with_error = _complete(target)
    store.update_metadata(with_error, error="Unexpected", finalize_error_code=500, finalize_failed_at=store.now_iso())

    _pass(first_pass=False)

    assert sorted(recoveries) == sorted([finalizing, missing, complete])
    assert _meta(finalizing)["state"] == "active"
    assert _meta(missing)["state"] == "failed"
    assert _meta(complete)["state"] == "completed"
    assert _meta(short)["state"] == "active"
    assert _meta(short)["version"] == 1
    assert _meta(with_error)["state"] == "active"
    assert _meta(with_error)["error"] == "Unexpected"


def test_later_pass_settles_an_active_upload_with_error_and_no_part(target, recoveries):
    upload_id = _complete(target)
    store.update_metadata(upload_id, error="Unexpected", finalize_error_code=500, finalize_failed_at=store.now_iso())
    store.part_path(upload_id).unlink()
    _pass(first_pass=True)
    assert recoveries == []
    _pass(first_pass=False)
    assert recoveries == [upload_id]
    assert _meta(upload_id)["state"] == "failed"


def test_finalizing_now_is_neither_recovered_nor_expired(target, recoveries):
    upload_id = _complete(target)
    store.update_metadata(upload_id, state="finalizing")
    _stale(upload_id)
    locks._FINALIZING_NOW.add(upload_id)
    stats = _pass(first_pass=True)
    assert recoveries == []
    assert stats.expired == 0
    assert _meta(upload_id)["state"] == "finalizing"


def test_active_upload_in_progress_is_kept(target, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")
    stats = _pass(first_pass=False)
    assert stats == janitor.PassStats(0, 0, 0, 0, 0, 0)
    assert _meta(upload_id)["version"] == 1
    assert store.part_path(upload_id).read_bytes() == b"abc"
    assert layer.records() == []


# ── Re-check under the lock ───────────────────────────────────────────────────


async def _pass_behind_lock(upload_id, change, *, first_pass=False, now=None):
    """Start a pass while the upload's lock is held, apply *change*, release."""
    lock = locks.get_upload_lock(upload_id)
    async with lock:
        task = asyncio.create_task(janitor.run_cleanup_pass(first_pass=first_pass, now=now or _clock()))
        while not lock._waiters:  # the pass waits for the lock
            await asyncio.sleep(0.005)
        await asyncio.to_thread(change)
    return await task


def test_recovery_recheck_under_the_lock_skips_a_just_failed_finalization(target, recoveries):
    upload_id = _complete(target)

    def failed_meanwhile():
        store.update_metadata(
            upload_id, error="Not enough disk space", finalize_error_code=507, finalize_failed_at=store.now_iso()
        )

    stats = _run(_pass_behind_lock(upload_id, failed_meanwhile))
    assert stats.recovered == 0
    assert recoveries == []
    assert _meta(upload_id)["state"] == "active"
    assert not (target / "a.txt").exists()


def test_expiry_recheck_under_the_lock_skips_a_fresh_transfer(target):
    upload_id = _new_upload(target, size=10, content=b"abc")
    later = datetime.now(UTC) + timedelta(days=8)

    def transferred_meanwhile():
        store.update_metadata(upload_id, last_transfer_at=later.isoformat())

    stats = _run(_pass_behind_lock(upload_id, transferred_meanwhile, now=lambda: later))
    assert stats.expired == 0
    assert _meta(upload_id)["state"] == "active"


def test_expiry_recheck_under_the_lock_skips_a_terminal_upload(target):
    upload_id = _new_upload(target, size=10, content=b"abc")

    def cancelled_meanwhile():
        store.cancel_upload(upload_id)

    _stale(upload_id)
    stats = _run(_pass_behind_lock(upload_id, cancelled_meanwhile))
    assert stats.expired == 0
    meta = _meta(upload_id)
    assert meta["state"] == "cancelled"
    assert meta["error"] is None


# ── Expiry ────────────────────────────────────────────────────────────────────


def test_expiry_of_an_active_upload(target, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")
    _stale(upload_id, days=6)
    assert _pass().expired == 0
    _stale(upload_id, days=8)
    stats = _pass()
    assert stats.expired == 1
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"] == "expired"
    assert meta["offset"] == 3
    assert not store.part_path(upload_id).exists()
    assert layer.records()[-1]["state"] == "failed"
    assert layer.records()[-1]["error"] == "expired"


def test_expiry_writes_the_metadata_before_removing_the_part(target, monkeypatch):
    upload_id = _new_upload(target, size=10, content=b"abc")
    real = store.update_metadata
    seen = []

    def spy(uid, **changes):
        seen.append(store.part_path(uid).exists())
        return real(uid, **changes)

    _stale(upload_id)
    monkeypatch.setattr(store, "update_metadata", spy)
    _pass()
    assert seen == [True]
    assert not store.part_path(upload_id).exists()


def test_expiry_disk_full_removes_the_part_first(target, monkeypatch, layer):
    upload_id = _stale(_new_upload(target, size=10, content=b"abc"))
    hits = _update_raising(monkeypatch, errno.ENOSPC, lambda c: c.get("state") == "failed", times=1)
    stats = _pass()
    assert len(hits) == 1
    assert stats.expired == 1
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"] == "expired"
    assert not store.part_path(upload_id).exists()
    assert [r["state"] for r in layer.records()] == ["failed"]


def test_expiry_second_write_failing_leaves_active_without_part(target, monkeypatch, layer):
    upload_id = _stale(_new_upload(target, size=10, content=b"abc"))
    hits = _update_raising(monkeypatch, errno.ENOSPC, lambda c: c.get("state") == "failed", times=2)
    stats = _pass()
    assert len(hits) == 2
    assert stats.expired == 0
    assert _meta(upload_id)["state"] == "active"
    assert not store.part_path(upload_id).exists()
    assert layer.records() == []
    # A later pass settles it: recovery of an active upload without ``.part``.
    _pass()
    assert _meta(upload_id)["state"] == "failed"
    assert _meta(upload_id)["error"] == "staging file lost"


def test_expiry_of_a_finalizing_upload_removes_the_empty_reservation(target, monkeypatch, layer):
    upload_id = _complete(target)
    reservation = target / "a.txt"
    reservation.write_bytes(b"")
    store.update_metadata(
        upload_id, state="finalizing", final_path=str(reservation), final_method="replace", final_source="tmp"
    )
    _temp(upload_id, target).write_bytes(DATA)
    _stale(upload_id)
    _recovery_failing(monkeypatch)

    stats = _pass()

    assert stats.expired == 1
    meta = _meta(upload_id)
    assert meta["state"] == "failed"
    assert meta["error"] == "recovery failed"
    assert not reservation.exists()
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()
    assert layer.records()[-1]["error"] == "recovery failed"


def test_expiry_of_a_finalizing_upload_whose_write_fails(target, monkeypatch, layer):
    upload_id = _complete(target)
    store.update_metadata(upload_id, state="finalizing")
    _stale(upload_id)
    version = _meta(upload_id)["version"]
    layer.sent.clear()
    _recovery_failing(monkeypatch)
    hits = _update_raising(monkeypatch, errno.ENOSPC, lambda c: c.get("error") == "recovery failed")

    stats = _pass()

    assert len(hits) == 1
    assert stats.expired == 0
    meta = _meta(upload_id)
    assert meta["state"] == "finalizing"
    assert meta["version"] == version
    assert store.part_path(upload_id).read_bytes() == DATA
    assert layer.records() == []


def test_expiry_of_a_finalizing_link_keeps_the_final_file(target, monkeypatch):
    upload_id = _complete(target)
    os.link(store.part_path(upload_id), target / "a.txt")
    store.update_metadata(upload_id, state="finalizing", final_path=str(target / "a.txt"), final_method="link")
    _stale(upload_id)
    _recovery_failing(monkeypatch)
    _pass()
    assert _meta(upload_id)["error"] == "recovery failed"
    assert (target / "a.txt").read_bytes() == DATA


# ── Terminal leftovers and tombstones ────────────────────────────────────────


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_leftovers_are_removed(target, state):
    upload_id = _complete(target)
    store.update_metadata(upload_id, state=state)
    _temp(upload_id, target).write_bytes(b"x")
    stats = _pass()
    assert stats.leftovers == 1
    assert not store.part_path(upload_id).exists()
    assert not _temp(upload_id, target).exists()
    assert store.metadata_path(upload_id).exists()


def test_tombstone_removal_and_lock_entry(target, layer):
    old = _new_upload(target)
    store.cancel_upload(old)
    layer.sent.clear()

    async def scenario():
        lock = locks.get_upload_lock(old)
        assert lock is not None
        stats = await janitor.run_cleanup_pass(first_pass=False, now=IN_25_HOURS)
        return stats, old in locks._current_locks().uploads, locks.get_upload_lock(old)

    stats, entry_kept, lock_after = _run(scenario())
    assert stats.tombstones == 1
    assert not store.metadata_path(old).exists()
    assert entry_kept is False
    assert lock_after is None
    assert layer.records() == []


def test_young_tombstone_is_kept(target):
    upload_id = _new_upload(target)
    store.cancel_upload(upload_id)
    stats = _pass(now=_clock(timedelta(hours=23)))
    assert stats.tombstones == 0
    assert store.metadata_path(upload_id).exists()


def test_tombstone_recheck_under_the_lock(target):
    """Selected from an old ``updated_at``, re-read fresh: nothing removed."""
    upload_id = _new_upload(target)
    store.cancel_upload(upload_id)
    meta = _meta(upload_id)
    meta["updated_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    store.atomic_write_json(store.metadata_path(upload_id), meta)

    def rewritten_meanwhile():
        meta["updated_at"] = datetime.now(UTC).isoformat()
        store.atomic_write_json(store.metadata_path(upload_id), meta)

    stats = _run(_pass_behind_lock(upload_id, rewritten_meanwhile))
    assert stats.tombstones == 0
    assert store.metadata_path(upload_id).exists()


# ── Orphans and unparsable metadata ───────────────────────────────────────────


def test_orphans_older_than_one_hour_are_removed(data_dir):
    staging = store.get_staging_dir()
    old_part = staging / f"{'a' * 32}.part"
    young_part = staging / f"{'b' * 32}.part"
    old_tmp = staging / f"{'c' * 32}.json.x1y2.tmp"
    young_tmp = staging / f"{'d' * 32}.json.x1y2.tmp"
    other = staging / "notes.txt"
    for path in (old_part, young_part, old_tmp, young_tmp, other):
        path.write_bytes(b"x")
    for path in (old_part, old_tmp, other):
        _age_file(path, timedelta(hours=2))

    stats = _pass()

    assert stats.orphans == 2
    assert not old_part.exists()
    assert not old_tmp.exists()
    assert young_part.exists()
    assert young_tmp.exists()
    assert other.exists()


def test_symlinks_and_directories_with_upload_names_are_never_removed(data_dir, tmp_path):
    staging = store.get_staging_dir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"not json")
    linked_part = staging / f"{'a' * 32}.part"
    linked_part.symlink_to(outside)
    linked_tmp = staging / f"{'b' * 32}.json.x1y2.tmp"
    linked_tmp.symlink_to(outside)
    linked_json = staging / f"{'c' * 32}.json"  # unparsable through the link
    linked_json.symlink_to(outside)
    dir_part = staging / f"{'d' * 32}.part"
    dir_part.mkdir()
    dir_json = staging / f"{'e' * 32}.json"
    dir_json.mkdir()
    for path in (linked_part, linked_tmp, linked_json, dir_part, dir_json):
        stamp = (datetime.now(UTC) - timedelta(days=3)).timestamp()
        os.utime(path, (stamp, stamp), follow_symlinks=False)
    _age_file(outside, timedelta(days=3))

    stats = _pass(now=_clock(timedelta(days=3)))

    assert stats.orphans == 0
    assert stats.unparsable == 0
    for path in (linked_part, linked_tmp, linked_json):
        assert path.is_symlink()
    assert dir_part.is_dir()
    assert dir_json.is_dir()
    assert outside.read_bytes() == b"not json"


def test_orphan_recheck_when_the_json_appears_after_the_snapshot(data_dir, monkeypatch):
    upload_id = "a" * 32
    part = store.get_staging_dir() / f"{upload_id}.part"
    part.write_bytes(b"x")
    _age_file(part, timedelta(hours=2))
    real = janitor.take_snapshot
    calls = []

    def snapshot_then_json():
        snapshot = real()
        calls.append(snapshot)
        if len(calls) == 2:  # the snapshot of the orphan phase
            (store.get_staging_dir() / f"{upload_id}.json").write_bytes(b"{}")
        return snapshot

    monkeypatch.setattr(janitor, "take_snapshot", snapshot_then_json)
    stats = _pass()
    assert upload_id in calls[1].parts
    assert upload_id not in calls[1].json_ids
    assert stats.orphans == 0
    assert part.exists()


def test_orphan_age_follows_the_clock(data_dir):
    part = store.get_staging_dir() / f"{'a' * 32}.part"
    part.write_bytes(b"x")
    assert _pass().orphans == 0
    assert _pass(now=IN_2_HOURS).orphans == 1
    assert not part.exists()


def test_unparsable_metadata_skipped_then_removed_after_24h(target, recoveries, layer):
    upload_id = _new_upload(target, size=10, content=b"abc")
    store.metadata_path(upload_id).write_bytes(b'{"truncated')

    stats = _pass(first_pass=True, now=_clock(timedelta(hours=23)))
    assert stats.unparsable == 0
    assert store.metadata_path(upload_id).exists()
    assert store.part_path(upload_id).exists()  # not an orphan: its ``.json`` exists

    async def scenario():
        stats = await janitor.run_cleanup_pass(first_pass=False, now=IN_25_HOURS)
        return stats, upload_id in locks._current_locks().uploads

    stats, entry_kept = _run(scenario())
    assert stats.unparsable == 1
    assert entry_kept is False
    assert not store.metadata_path(upload_id).exists()
    assert not store.part_path(upload_id).exists()
    assert recoveries == []
    assert layer.records() == []


def test_unparsable_metadata_repaired_meanwhile_is_kept(target):
    upload_id = _new_upload(target, size=10, content=b"abc")
    good = store.metadata_path(upload_id).read_bytes()
    store.metadata_path(upload_id).write_bytes(b"[]")
    _age_file(store.metadata_path(upload_id), timedelta(days=2))

    def repaired():
        store.metadata_path(upload_id).write_bytes(good)
        _age_file(store.metadata_path(upload_id), timedelta(days=2))

    stats = _run(_pass_behind_lock(upload_id, repaired))
    assert stats.unparsable == 0
    assert _meta(upload_id)["state"] == "active"


# ── Staging dir and the loop ─────────────────────────────────────────────────


def test_pass_without_staging_dir_creates_nothing(data_dir):
    stats = _pass(first_pass=True)
    assert stats == janitor.PassStats(0, 0, 0, 0, 0, 0)
    assert not (data_dir / "uploads").exists()


def _run_loop(monkeypatch, on_pass):
    """Run the janitor loop with tiny delays; *on_pass(first_pass, stop)* fakes each pass."""
    monkeypatch.setattr(janitor, "UPLOAD_CLEANUP_FIRST_DELAY", 0.01)
    monkeypatch.setattr(janitor, "UPLOAD_CLEANUP_INTERVAL", 0.01)

    async def scenario():
        stop = asyncio.Event()

        async def fake_pass(*, first_pass):
            on_pass(first_pass, stop)

        monkeypatch.setattr(janitor, "run_cleanup_pass", fake_pass)
        await asyncio.wait_for(janitor.start_upload_cleanup_task(stop), timeout=5)

    _run(scenario())


def test_loop_runs_a_first_pass_then_later_passes(monkeypatch):
    passes = []

    def on_pass(first_pass, stop):
        passes.append(first_pass)
        if len(passes) == 3:
            stop.set()

    _run_loop(monkeypatch, on_pass)
    assert passes == [True, False, False]


def test_loop_survives_a_failing_pass_and_stops_on_the_event(monkeypatch):
    passes = []

    def on_pass(first_pass, stop):
        passes.append(first_pass)
        if len(passes) == 2:
            stop.set()
        raise RuntimeError("boom")

    _run_loop(monkeypatch, on_pass)
    assert passes == [True, False]


def test_loop_stops_before_the_first_pass(monkeypatch):
    passes = []

    async def scenario():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(janitor.start_upload_cleanup_task(stop), timeout=5)

    monkeypatch.setattr(janitor, "run_cleanup_pass", lambda **kwargs: passes.append(kwargs))
    _run(scenario())
    assert passes == []
