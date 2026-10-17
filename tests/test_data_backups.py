"""Versioned backups of the data dir root files (``twicc.data_backups``).

Design: docs/plans/2026-10-17-data-dir-file-backups-design.md.
"""

import os
import stat
from datetime import UTC, datetime, timedelta

import orjson
import pytest

from twicc import data_backups, paths

pytestmark = pytest.mark.usefixtures("fresh_data_dir")

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _root():
    return paths.get_data_dir()


def _tokens(name: str, last_used_at: str) -> bytes:
    return orjson.dumps({"version": 1, "tokens": [{
        "id": "tok_1", "name": name, "digest": "d", "created_at": "2026-01-01", "last_used_at": last_used_at,
    }]})


# --- version store and comparison ---------------------------------------------


def test_covered_names():
    for name in ("settings.json", "workspaces.json", "layouts.json", "message-snippets.json",
                 "terminal-config.json", "codex-settings-presets.json", "claude_code-settings-presets.json",
                 "seen-tips.json", "seen-help.json", "api-tokens.json", ".env", "secret-key"):
        assert data_backups.is_covered(name), name
    for name in ("providers-status.json", "telemetry.json", "twicc.info.json", "twicc.heartbeat",
                 "settings.json.lock", "settings.json.ab12.tmp", "tmpab12.tmp", "secret-key.tmp", "backups"):
        assert not data_backups.is_covered(name), name


def test_first_change_stores_the_raw_bytes():
    raw = b'{"publicBaseUrl": "https://x", "_version": 3}'
    (_root() / "settings.json").write_bytes(raw)
    assert data_backups.take_version("settings.json", now=T0) is True
    [version] = data_backups.list_versions("settings.json")
    assert version.read_bytes() == raw
    assert version.parent == _root() / "backups" / "settings.json"
    assert version.name == "2026-10-17T12-00-00-000000Z.json"
    assert data_backups.version_time(version) == T0


def test_same_content_stores_nothing():
    (_root() / "settings.json").write_bytes(b'{"a": 1, "_version": 1}')
    data_backups.take_version("settings.json", now=T0)
    # Same data, other key order and another write counter.
    (_root() / "settings.json").write_bytes(b'{"_version": 9, "a": 1}')
    assert data_backups.take_version("settings.json", now=T0 + timedelta(seconds=1)) is False
    assert len(data_backups.list_versions("settings.json")) == 1


def test_token_last_used_at_is_ignored():
    store = _root() / "api-tokens.json"
    store.write_bytes(_tokens("cli", "2026-10-17T10:00:00Z"))
    assert data_backups.take_version("api-tokens.json", now=T0) is True
    store.write_bytes(_tokens("cli", "2026-10-17T11:00:00Z"))
    assert data_backups.take_version("api-tokens.json", now=T0 + timedelta(seconds=1)) is False
    store.write_bytes(_tokens("renamed", "2026-10-17T11:00:00Z"))
    assert data_backups.take_version("api-tokens.json", now=T0 + timedelta(seconds=2)) is True
    assert len(data_backups.list_versions("api-tokens.json")) == 2


def test_invalid_json_and_raw_files_compare_as_bytes():
    settings = _root() / "settings.json"
    stored = []
    for i, raw in enumerate([b"{not json", b"{not json", b"", b"[1]", b"[1]"]):
        settings.write_bytes(raw)
        stored.append(data_backups.take_version("settings.json", now=T0 + timedelta(seconds=i)))
    assert stored == [True, False, True, True, False]
    assert [v.read_bytes() for v in data_backups.list_versions("settings.json")] == [b"{not json", b"", b"[1]"]

    env = _root() / ".env"
    env.write_bytes(b"A=1\n")
    assert data_backups.take_version(".env", now=T0) is True
    [version] = data_backups.list_versions(".env")
    assert version.parent == _root() / "backups" / "env"
    assert version.name == "2026-10-17T12-00-00-000000Z"
    env.write_bytes(b"A=2\n")
    assert data_backups.take_version(".env", now=T0 + timedelta(seconds=1)) is True
    assert len(data_backups.list_versions(".env")) == 2


def test_missing_file_stores_nothing_and_keeps_versions():
    (_root() / "layouts.json").write_bytes(b'{"layouts": []}')
    data_backups.take_version("layouts.json", now=T0)
    (_root() / "layouts.json").unlink()
    assert data_backups.take_version("layouts.json", now=T0 + timedelta(seconds=1)) is False
    assert len(data_backups.list_versions("layouts.json")) == 1


def test_same_instant_never_overwrites():
    target = _root() / "workspaces.json"
    target.write_bytes(b'{"workspaces": [1]}')
    assert data_backups.take_version("workspaces.json", now=T0) is True
    target.write_bytes(b'{"workspaces": [2]}')
    assert data_backups.take_version("workspaces.json", now=T0) is True
    versions = data_backups.list_versions("workspaces.json")
    assert len({v.name for v in versions}) == 2
    assert [v.read_bytes() for v in versions] == [b'{"workspaces": [1]}', b'{"workspaces": [2]}']


def test_permissions_are_owner_only():
    target = _root() / "message-snippets.json"
    target.write_bytes(b"{}")
    target.chmod(0o664)
    data_backups.take_version("message-snippets.json", now=T0)
    [version] = data_backups.list_versions("message-snippets.json")
    assert stat.S_IMODE(os.stat(_root() / "backups").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(version.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(version).st_mode) == 0o600


# --- retention ------------------------------------------------------------------


def _store(name: str, when: datetime, index: int) -> None:
    """Write a distinct content to ``name`` and take its version at ``when``."""
    (_root() / name).write_bytes(orjson.dumps({"n": index}))
    assert data_backups.take_version(name, now=when) is True


def _kept(name: str) -> list[datetime]:
    return [data_backups.version_time(path) for path in data_backups.list_versions(name)]


def test_keeps_the_30_newest():
    times = [T0 + timedelta(minutes=i) for i in range(40)]
    for i, when in enumerate(times):
        _store("layouts.json", when, i)
    assert _kept("layouts.json") == times[-30:]


def test_keeps_the_newest_of_each_of_the_30_most_recent_days_with_a_version():
    # 45 days with one version each, one day skipped between them.
    days = [T0 - timedelta(days=200) + timedelta(days=2 * i) for i in range(45)]
    for i, when in enumerate(days):
        _store("layouts.json", when, i)
    burst = [T0 + timedelta(minutes=i) for i in range(40)]
    for i, when in enumerate(burst):
        _store("layouts.json", when, 100 + i)
    # The burst day counts as one of the 30 days: 29 earlier days remain.
    assert _kept("layouts.json") == days[-29:] + burst[-30:]


def test_a_burst_never_removes_an_old_day():
    old = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    _store("workspaces.json", old, 0)
    for i in range(200):
        _store("workspaces.json", T0 + timedelta(seconds=i), 1 + i)
    kept = _kept("workspaces.json")
    assert kept[0] == old
    assert len(kept) == 31


def test_days_without_versions_do_not_count():
    times = [T0 - timedelta(days=days) for days in (360, 270, 180, 90, 0)]
    for i, when in enumerate(times):
        _store("terminal-config.json", when, i)
    assert _kept("terminal-config.json") == times


def test_days_are_local_calendar_days(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "Europe/Paris")  # CEST (UTC+2) on 2026-10-17
    time.tzset()
    monkeypatch.setattr(data_backups, "KEEP_NEWEST", 1)
    first = datetime(2026, 10, 17, 21, 0, tzinfo=UTC)  # 23:00 local, 17 Oct
    second = datetime(2026, 10, 17, 21, 30, tzinfo=UTC)  # 23:30 local, 17 Oct
    third = datetime(2026, 10, 17, 22, 30, tzinfo=UTC)  # 00:30 local, 18 Oct
    for i, when in enumerate((first, second, third)):
        _store("seen-tips.json", when, i)
    assert _kept("seen-tips.json") == [second, third]


# --- startup scan -----------------------------------------------------------------


def test_scan_takes_the_first_version_of_each_covered_file_then_nothing():
    for name in ("settings.json", "workspaces.json", ".env", "codex-settings-presets.json"):
        (_root() / name).write_bytes(b"{}" if name.endswith(".json") else b"X=1\n")
    (_root() / "telemetry.json").write_bytes(b"{}")
    (_root() / "db").mkdir()
    assert data_backups.scan_all(now=T0) == 4
    assert data_backups.scan_all(now=T0 + timedelta(seconds=1)) == 0
    assert sorted(path.name for path in (_root() / "backups").iterdir()) == [
        "codex-settings-presets.json", "env", "settings.json", "workspaces.json",
    ]


def test_scan_catches_a_change_made_while_stopped():
    (_root() / "workspaces.json").write_bytes(b'{"workspaces": []}')
    data_backups.scan_all(now=T0)
    (_root() / "workspaces.json").write_bytes(b'{"workspaces": [{"id": "w"}]}')
    assert data_backups.scan_all(now=T0 + timedelta(seconds=1)) == 1


def test_unusable_backups_dir_never_raises():
    (_root() / "settings.json").write_bytes(b"{}")
    (_root() / "backups").write_bytes(b"not a dir")
    assert data_backups.scan_all(now=T0) == 0
    assert (_root() / "settings.json").read_bytes() == b"{}"
