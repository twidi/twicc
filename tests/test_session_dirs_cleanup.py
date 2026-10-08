"""Tests for delayed daily session-directory session_dirs_cleanup_task.

Covers:
- The pure :func:`_is_stale` boundary logic (None, recent, exactly the
  threshold, well past it).
- :func:`_prune_stale_session_dirs` over a temp data dir: empty vs non-empty,
  old vs recent sessions, the ``last_started_at`` race guard, sessions with no
  usable timestamp, and orphan artifact directories dated by filesystem mtime.
- Recursive scratch expiry, shared ownership, active agents, and symlinks.
- Initial delay, daily cadence, disabling, shutdown, and start serialization.

The data dir is redirected to a ``tmp_path`` by monkeypatching
``twicc.paths.get_data_dir`` (every downstream helper reads from it), so the
tests never touch the real data directory.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest
from django.utils import timezone

from twicc import paths
from twicc.core.models import Project, Session, SessionType
from twicc.session_dirs_cleanup_task import (
    STALE_SESSION_DIR_AGE,
    _is_stale,
    _prune_stale_session_dirs,
)


# ---------------------------------------------------------------------------
# _is_stale — pure function
# ---------------------------------------------------------------------------


def test_is_stale_none_reference_is_never_stale():
    now = datetime.now(UTC)
    assert _is_stale(None, now) is False


def test_is_stale_recent_is_not_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - timedelta(days=5), now) is False


def test_is_stale_exactly_at_threshold_is_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - STALE_SESSION_DIR_AGE, now) is True


def test_is_stale_well_past_threshold_is_stale():
    now = datetime.now(UTC)
    assert _is_stale(now - timedelta(days=40), now) is True


# ---------------------------------------------------------------------------
# _prune_stale_session_dirs — filesystem + ORM
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Redirect the data dir to a temp path and pre-create both roots."""
    data_dir = tmp_path / "data"
    artifacts = data_dir / "artifacts"
    scratch = data_dir / "scratch"
    artifacts.mkdir(parents=True)
    scratch.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return artifacts, scratch


@pytest.fixture
def project(db):
    return Project.objects.create(
        id="-tmp-session-dirs-cleanup",
        directory="/tmp/twicc-session-dirs-cleanup",
    )


def _make_session(project, session_id, *, last_updated=None, last_started=None, created=None):
    return Session.objects.create(
        id=session_id,
        project=project,
        provider="claude_code",
        file_path=f"{session_id}.jsonl",
        type=SessionType.SESSION,
        title=session_id,
        last_updated_at=last_updated,
        last_started_at=last_started,
        created_at=created,
    )


def _session_dir(root: Path, session_id: str, *, empty: bool = True, mtime_days_ago: int | None = None) -> Path:
    """Create ``root/session_id``; optionally add a file and back-date its mtime."""
    d = root / session_id
    d.mkdir()
    if not empty:
        (d / "keep.txt").write_text("x")
    if mtime_days_ago is not None:
        ts = (timezone.now() - timedelta(days=mtime_days_ago)).timestamp()
        os.utime(d, (ts, ts))
    return d


def test_prunes_empty_dir_of_old_session(data_root, project):
    artifacts, scratch = data_root
    old = timezone.now() - timedelta(days=40)
    _make_session(project, "old-sess", last_updated=old, last_started=old, created=old)
    a = _session_dir(artifacts, "old-sess")
    s = _session_dir(scratch, "old-sess")

    assert _prune_stale_session_dirs() == (1, 1)
    assert not a.exists()
    assert not s.exists()


def test_keeps_empty_dir_of_recent_session(data_root, project):
    artifacts, _ = data_root
    recent = timezone.now() - timedelta(days=5)
    _make_session(project, "recent-sess", last_updated=recent, last_started=recent, created=recent)
    a = _session_dir(artifacts, "recent-sess")

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_keeps_non_empty_dir_of_old_session(data_root, project):
    artifacts, _ = data_root
    old = timezone.now() - timedelta(days=40)
    _make_session(project, "old-busy", last_updated=old, last_started=old, created=old)
    a = _session_dir(artifacts, "old-busy", empty=False)

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()
    assert (a / "keep.txt").exists()


def test_recent_last_started_protects_old_last_updated(data_root, project):
    """A session resumed today (no new content yet) keeps its empty dir."""
    artifacts, _ = data_root
    _make_session(
        project,
        "resumed",
        last_updated=timezone.now() - timedelta(days=40),
        last_started=timezone.now() - timedelta(days=1),
        created=timezone.now() - timedelta(days=50),
    )
    a = _session_dir(artifacts, "resumed")

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_session_without_timestamps_falls_back_to_mtime(data_root, project):
    artifacts, scratch = data_root
    _make_session(project, "no-ts")  # all lifecycle timestamps null
    old = _session_dir(artifacts, "no-ts", mtime_days_ago=40)
    fresh = _session_dir(scratch, "no-ts", mtime_days_ago=2)

    assert _prune_stale_session_dirs() == (1, 0)
    assert not old.exists()
    assert fresh.exists()


def test_orphan_dir_pruned_when_old(data_root, project):
    artifacts, _ = data_root  # no Session row for this id
    a = _session_dir(artifacts, "orphan-old", mtime_days_ago=40)

    assert _prune_stale_session_dirs() == (1, 0)
    assert not a.exists()


def test_orphan_dir_kept_when_recent(data_root, project):
    artifacts, _ = data_root
    a = _session_dir(artifacts, "orphan-fresh", mtime_days_ago=2)

    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists()


def test_no_roots_is_noop(tmp_path, monkeypatch):
    """Missing artifacts/scratch roots must not raise."""
    monkeypatch.setattr(paths, "get_data_dir", lambda: tmp_path / "absent")
    assert _prune_stale_session_dirs() == (0, 0)


@pytest.mark.parametrize("flag", ["archived", "hidden"])
def test_removes_expired_scratch_recursively(data_root, project, flag):
    _, scratch = data_root
    session = _make_session(project, "expired", last_updated=timezone.now() - timedelta(days=40))
    setattr(session, flag, True)
    session.save(update_fields=[flag])
    directory = _session_dir(scratch, session.id, empty=False)
    (directory / "nested").mkdir()
    (directory / "nested" / "file").write_text("temporary")

    assert _prune_stale_session_dirs() == (0, 1)
    assert not directory.exists()


def test_removes_fresh_nonempty_orphan_scratch(data_root, project):
    _, scratch = data_root
    directory = _session_dir(scratch, "orphan", empty=False)
    assert _prune_stale_session_dirs() == (0, 1)
    assert not directory.exists()


@pytest.mark.parametrize("flag", ["archived", "hidden", "visible"])
def test_preserves_nonempty_protected_scratch(data_root, project, flag):
    _, scratch = data_root
    session = _make_session(project, "protected", last_updated=timezone.now() - timedelta(days=5))
    if flag != "visible":
        setattr(session, flag, True)
        session.save(update_fields=[flag])
    directory = _session_dir(scratch, session.id, empty=False)
    assert _prune_stale_session_dirs() == (0, 0)
    assert (directory / "keep.txt").read_text() == "x"


def test_visible_old_session_keeps_nonempty_scratch(data_root, project):
    _, scratch = data_root
    session = _make_session(project, "visible", last_updated=timezone.now() - timedelta(days=40))
    directory = _session_dir(scratch, session.id, empty=False)
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_missing_timestamps_never_authorize_recursive_removal(data_root, project):
    _, scratch = data_root
    session = _make_session(project, "unknown-age")
    session.hidden = True
    session.save(update_fields=["hidden"])
    directory = _session_dir(scratch, session.id, empty=False, mtime_days_ago=40)
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_recent_stop_protects_both_roots(data_root, project):
    artifacts, scratch = data_root
    session = _make_session(project, "stopped", last_updated=timezone.now() - timedelta(days=40))
    session.last_stopped_at = timezone.now()
    session.hidden = True
    session.save(update_fields=["last_stopped_at", "hidden"])
    a = _session_dir(artifacts, session.id)
    s = _session_dir(scratch, session.id, empty=False)
    assert _prune_stale_session_dirs() == (0, 0)
    assert a.exists() and s.exists()


@pytest.mark.parametrize("reference", ["spawn_root", "annotation", "nested_annotation"])
def test_shared_scratch_waits_for_protected_child(data_root, project, reference):
    _, scratch = data_root
    old = timezone.now() - timedelta(days=40)
    root = _make_session(project, "root", last_updated=old)
    root.archived = True
    root.save(update_fields=["archived"])
    child = _make_session(project, "child", last_updated=old)
    directory = _session_dir(scratch, root.id, empty=False)
    if reference == "spawn_root":
        child.spawn_root = root
    else:
        child.annotations = {"scratch_dir": str(directory / "nested" if reference == "nested_annotation" else directory)}
    child.save()

    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()
    child.hidden = True
    child.save(update_fields=["hidden"])
    assert _prune_stale_session_dirs() == (0, 1)
    assert not directory.exists()


def test_referenced_orphan_scratch_is_protected(data_root, project):
    _, scratch = data_root
    directory = _session_dir(scratch, "shared-orphan", empty=False)
    session = _make_session(project, "owner", last_updated=timezone.now())
    session.annotations = {"scratch_dir": str(directory)}
    session.save(update_fields=["annotations"])
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_recursive_removal_does_not_follow_symlinks(data_root, project, tmp_path):
    _, scratch = data_root
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep").write_text("safe")
    directory = _session_dir(scratch, "orphan", empty=False)
    (directory / "link").symlink_to(target, target_is_directory=True)
    (scratch / "top-link").symlink_to(target, target_is_directory=True)
    assert _prune_stale_session_dirs() == (0, 1)
    assert not directory.exists()
    assert (target / "keep").read_text() == "safe"
    assert (scratch / "top-link").is_symlink()


def test_active_agent_protects_scratch(data_root, project, monkeypatch):
    from types import SimpleNamespace
    from twicc.agent.registry import get_agent_manager_registry

    _, scratch = data_root
    session = _make_session(project, "active", last_updated=timezone.now() - timedelta(days=40))
    session.hidden = True
    session.save(update_fields=["hidden"])
    directory = _session_dir(scratch, session.id, empty=False)
    monkeypatch.setattr(get_agent_manager_registry(), "get_active_agents", lambda: [SimpleNamespace(session_id=session.id)])
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_loop_waits_thirty_minutes_then_daily(monkeypatch, settings):
    import asyncio
    from twicc import session_dirs_cleanup_task

    settings.SESSION_DIRS_CLEANUP_ENABLED = True
    waits = []
    passes = []
    stop = asyncio.Event()

    async def wait_for(awaitable, timeout):
        awaitable.close()
        waits.append(timeout)
        if len(waits) <= 2:
            raise TimeoutError
        stop.set()

    async def run_pass():
        passes.append(len(waits))

    monkeypatch.setattr(session_dirs_cleanup_task.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(session_dirs_cleanup_task, "_run_cleanup_pass", run_pass, raising=False)
    # No actual filesystem cleanup is allowed in this scheduler test.
    monkeypatch.setattr(session_dirs_cleanup_task, "_prune_stale_session_dirs", lambda: None)
    asyncio.run(session_dirs_cleanup_task.start_session_dirs_cleanup_task(stop))
    assert waits == [1800, 86400, 86400]
    assert passes == [1, 2]


def test_active_unsynced_agent_protects_shared_work_folder(data_root, project, monkeypatch):
    from types import SimpleNamespace
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.agent.states import AgentState

    _, scratch = data_root
    directory = _session_dir(scratch, "shared-with-unsynced", empty=False)
    registry = get_agent_manager_registry()
    manager = registry.items()[0][1]
    agent = SimpleNamespace(session_id="not-in-db", state=AgentState.STARTING, _work_dirs=[str(directory)])
    monkeypatch.setattr(manager, "_agents", {agent.session_id: agent})
    monkeypatch.setattr(registry, "get_active_agents", lambda: [SimpleNamespace(session_id=agent.session_id)])
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_annotation_for_scratch_root_protects_contained_folders(data_root, project):
    _, scratch = data_root
    directory = _session_dir(scratch, "orphan", empty=False)
    session = _make_session(project, "owner", last_updated=timezone.now())
    session.annotations = {"scratch_dir": str(scratch)}
    session.save(update_fields=["annotations"])
    assert _prune_stale_session_dirs() == (0, 0)
    assert directory.exists()


def test_directory_removal_failure_does_not_stop_other_removals(data_root, project, monkeypatch):
    import shutil

    _, scratch = data_root
    failed = _session_dir(scratch, "failed", empty=False)
    removed = _session_dir(scratch, "removed", empty=False)
    real_rmtree = shutil.rmtree

    def rmtree(path):
        if path == failed:
            raise PermissionError("not removable")
        real_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", rmtree)
    assert _prune_stale_session_dirs() == (0, 1)
    assert failed.exists() and not removed.exists()


def test_shutdown_during_initial_wait_does_not_run_cleanup(monkeypatch, settings):
    import asyncio
    from twicc import session_dirs_cleanup_task

    settings.SESSION_DIRS_CLEANUP_ENABLED = True
    passes = []
    stop = asyncio.Event()

    async def wait_for(awaitable, timeout):
        awaitable.close()
        stop.set()

    async def run_pass():
        passes.append(True)

    monkeypatch.setattr(session_dirs_cleanup_task.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(session_dirs_cleanup_task, "_run_cleanup_pass", run_pass)
    asyncio.run(session_dirs_cleanup_task.start_session_dirs_cleanup_task(stop))
    assert passes == []


def test_disabled_cleanup_does_not_wait_or_run(monkeypatch, settings):
    import asyncio
    from twicc import session_dirs_cleanup_task

    settings.SESSION_DIRS_CLEANUP_ENABLED = False

    async def unexpected(*args, **kwargs):
        pytest.fail("Disabled cleanup must not schedule work")

    monkeypatch.setattr(session_dirs_cleanup_task.asyncio, "wait_for", unexpected)
    monkeypatch.setattr(session_dirs_cleanup_task, "_run_cleanup_pass", unexpected)
    asyncio.run(session_dirs_cleanup_task.start_session_dirs_cleanup_task(asyncio.Event()))


def test_cleanup_excludes_agent_starts_until_worker_finishes(monkeypatch):
    import asyncio
    import threading
    from twicc import session_dirs_cleanup_task
    from twicc.agent.registry import get_agent_manager_registry

    registry = get_agent_manager_registry()
    worker_entered = threading.Event()
    release_worker = threading.Event()

    def prune():
        worker_entered.set()
        assert release_worker.wait(timeout=5)

    monkeypatch.setattr(session_dirs_cleanup_task, "_prune_stale_session_dirs", prune)

    async def scenario():
        task = asyncio.create_task(session_dirs_cleanup_task._run_cleanup_pass())
        try:
            assert await asyncio.to_thread(worker_entered.wait, 5)
            assert all(manager._lock.locked() for _, manager in registry.items())
            task.cancel()
            await asyncio.sleep(0)
            assert all(manager._lock.locked() for _, manager in registry.items())
        finally:
            release_worker.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert all(not manager._lock.locked() for _, manager in registry.items())

    asyncio.run(scenario())
