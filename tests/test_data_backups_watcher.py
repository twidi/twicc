"""The data dir root watcher of the data dir backups (``twicc.data_backups_watcher``).

Every scenario runs the real ``watchfiles`` watcher on the test's own data dir.
Except for the catch-up test, each one first waits for the version of a
sentinel file, taken by the catch-up scan: that scan is then over and cannot
overlap the writes the test makes.
"""

import asyncio
import os
import tempfile
import time

import orjson
import pytest

from twicc import data_backups, paths
from twicc.data_backups_watcher import run_data_backups_watcher

pytestmark = pytest.mark.usefixtures("fresh_data_dir")

FAST = {"step_ms": 10, "debounce_ms": 100, "rust_timeout_ms": 20}


def _root():
    return paths.get_data_dir()


def _versions(name: str) -> list:
    return data_backups.list_versions(name)


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def _start_synced(**options) -> asyncio.Task:
    """Start the watcher and return once its catch-up scan is over."""
    (_root() / "seen-help.json").write_bytes(b'{"seen": []}')
    task = asyncio.create_task(run_data_backups_watcher(**{**FAST, **options}))
    await _wait_for(lambda: len(_versions("seen-help.json")) == 1)
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _atomic_write(name: str, content: bytes) -> None:
    """Write like the backend does: a unique temp file renamed over the target."""
    fd, tmp = tempfile.mkstemp(dir=_root(), prefix=name + ".", suffix=".tmp")
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    os.replace(tmp, _root() / name)


def test_a_change_takes_one_version():
    async def scenario():
        task = await _start_synced()
        (_root() / "settings.json").write_bytes(b'{"publicBaseUrl": "https://x"}')
        await _wait_for(lambda: len(_versions("settings.json")) == 1)
        await asyncio.sleep(0.1)
        assert len(_versions("settings.json")) == 1
        await _stop(task)

    asyncio.run(scenario())


def test_a_burst_takes_one_version():
    async def scenario():
        task = await _start_synced(step_ms=40, debounce_ms=100)
        for i in range(4):  # one write every 20 ms: longer than a quiet window
            _atomic_write("settings.json", orjson.dumps({"n": i}))
            await asyncio.sleep(0.02)
        await _wait_for(lambda: len(_versions("settings.json")) >= 1)
        await asyncio.sleep(0.15)
        [version] = _versions("settings.json")
        assert orjson.loads(version.read_bytes()) == {"n": 3}
        await _stop(task)

    asyncio.run(scenario())


def test_uncovered_files_and_temp_files_are_ignored():
    async def scenario():
        task = await _start_synced()
        for name in ("telemetry.json", "twicc.heartbeat", "settings.json.ab12.tmp"):
            (_root() / name).write_bytes(b"{}")
        (_root() / "sub").mkdir()
        (_root() / "sub" / "settings.json").write_bytes(b"{}")
        (_root() / "workspaces.json").write_bytes(b'{"workspaces": []}')
        await _wait_for(lambda: len(_versions("workspaces.json")) == 1)
        assert sorted(path.name for path in (_root() / "backups").iterdir()) == ["seen-help.json", "workspaces.json"]
        await _stop(task)

    asyncio.run(scenario())


def test_catch_up_scan_covers_writes_before_the_watch_started():
    async def scenario():
        (_root() / "workspaces.json").write_bytes(b'{"workspaces": []}')
        task = asyncio.create_task(run_data_backups_watcher(**FAST))
        await _wait_for(lambda: len(_versions("workspaces.json")) == 1)
        await _stop(task)

    asyncio.run(scenario())


def test_cancel_compares_a_pending_change():
    async def scenario():
        task = await _start_synced()
        (_root() / "layouts.json").write_bytes(b'{"layouts": []}')
        task.cancel()  # no await in between: the loop cannot see the change
        started = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 2
        assert len(_versions("layouts.json")) == 1

    asyncio.run(scenario())


def test_events_reported_under_the_canonical_path_are_watched(tmp_path):
    """The default data dir (``~/.twicc``) is not resolved, and a platform may
    report events under the canonical path (macOS FSEvents): a symlinked data
    dir must still match."""
    from twicc.data_backups_watcher import is_watched

    real = tmp_path / "real-data"
    real.mkdir()
    link = tmp_path / "linked-data"
    link.symlink_to(real)
    assert is_watched(str(real / "settings.json"), link)
    assert is_watched(str(link / "settings.json"), link)
    assert not is_watched(str(real / "sub" / "settings.json"), link)
    assert not is_watched(str(real / "telemetry.json"), link)
