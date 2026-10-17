"""No test may touch a live instance's data dir.

On 2026-10-17 a test run replaced the developer's ``~/.twicc/settings.json``
with defaults: one test cached the settings of an empty temp data dir, the
variable was restored, and a later test wrote that cache back through the real
path. These tests pin the three defences: the isolation set up by
``twicc.settings_test``, the guard in ``paths.get_data_dir``, and the caches
keyed by the file they were read from.
"""

import os
import subprocess
import sys
from pathlib import Path

import orjson
import pytest
from django.conf import settings

from twicc import paths, settings_test
from twicc.auth import tokens
from twicc.synced_settings import read_synced_settings, write_synced_settings


def _data_dir(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


# --- isolation set up by settings_test ----------------------------------------


def test_the_suite_runs_on_the_isolated_data_dir():
    assert paths.get_data_dir() == settings_test.TEST_DATA_DIR
    assert (Path.home() / ".twicc").resolve() in paths._FORBIDDEN_DATA_DIRS


def test_no_instance_configuration_is_inherited():
    """Neither the ``.env`` nor the launching environment configures the tests."""
    allowed = settings_test._TEST_SWITCHES | {"TWICC_DATA_DIR", "TWICC_BIN"}  # TWICC_BIN: set by twicc.settings
    assert sorted(key for key in os.environ if key.startswith("TWICC_") and key not in allowed) == []


def test_a_fresh_test_process_leaves_the_inherited_instance_alone(tmp_path):
    """End to end, in a new process: launched on a live instance's data dir (what an
    agent session inherits from the backend), the test settings read none of its
    configuration, forbid it and write nothing into it."""
    live = _data_dir(tmp_path, "live")
    (live / ".env").write_text("ISOLATION_PROBE_FROM_DOTENV=1\n")
    script = (
        "import json, os\n"
        "import twicc.settings_test as s\n"
        "from twicc import paths\n"
        "print(json.dumps({\n"
        "    'from_dotenv': os.environ.get('ISOLATION_PROBE_FROM_DOTENV'),\n"
        "    'inherited_twicc': os.environ.get('TWICC_PASSWORD_HASH'),\n"
        "    'switch': os.environ.get('TWICC_CODEX_INTEGRATION'),\n"
        "    'data_dir': str(paths.get_data_dir()),\n"
        "    'test_data_dir': str(s.TEST_DATA_DIR),\n"
        "    'forbidden': [str(path) for path in paths._FORBIDDEN_DATA_DIRS],\n"
        "}))\n"
    )
    env = {
        **os.environ,
        "TWICC_DATA_DIR": str(live),
        "TWICC_PASSWORD_HASH": "inherited",
        "TWICC_CODEX_INTEGRATION": "0",
        "DJANGO_SETTINGS_MODULE": "twicc.settings_test",
    }
    env.pop("ISOLATION_PROBE_FROM_DOTENV", None)
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    observed = orjson.loads(result.stdout.strip().splitlines()[-1])

    assert observed["from_dotenv"] is None
    assert observed["inherited_twicc"] is None
    assert observed["switch"] == "0"
    assert observed["data_dir"] == observed["test_data_dir"]
    assert str(live.resolve()) in observed["forbidden"]
    assert [path.name for path in live.iterdir()] == [".env"]


# --- the guard ----------------------------------------------------------------


def test_unsetting_the_data_dir_hits_the_guard(monkeypatch):
    monkeypatch.delenv("TWICC_DATA_DIR")
    with pytest.raises(RuntimeError, match="forbidden"):
        paths.get_data_dir()


def test_the_guard_covers_the_whole_tree(monkeypatch, tmp_path):
    forbidden = _data_dir(tmp_path, "live")
    monkeypatch.setattr(paths, "_FORBIDDEN_DATA_DIRS", (forbidden.resolve(),))
    for target in (forbidden, forbidden / "sub" / "deeper"):
        monkeypatch.setenv("TWICC_DATA_DIR", str(target))
        with pytest.raises(RuntimeError, match="forbidden"):
            paths.get_data_dir()
    # A sibling that only shares the name prefix is another directory.
    sibling = _data_dir(tmp_path, "live-other")
    monkeypatch.setenv("TWICC_DATA_DIR", str(sibling))
    assert paths.get_data_dir() == sibling.resolve()


# --- caches keyed by the file they were read from -------------------------------


def test_settings_cached_under_one_data_dir_are_never_written_under_another(monkeypatch, tmp_path):
    """The exact 2026-10-17 sequence: defaults cached from an empty data dir, then
    the connect-time changelog bookkeeping running against the real one."""
    from twicc.asgi import _resolve_changelog_versions

    empty = _data_dir(tmp_path, "empty")
    live = _data_dir(tmp_path, "live")
    user_settings = {
        "publicBaseUrl": "https://mine.example",
        "lastChangelogVersionSeen": settings.APP_VERSION,
        "previousLastChangelogVersionSeen": settings.APP_VERSION,
        "_version": 411,
    }
    (live / "settings.json").write_bytes(orjson.dumps(user_settings))

    monkeypatch.setenv("TWICC_DATA_DIR", str(empty))
    assert read_synced_settings()["publicBaseUrl"] == ""
    monkeypatch.setenv("TWICC_DATA_DIR", str(live))
    _resolve_changelog_versions()

    assert orjson.loads((live / "settings.json").read_bytes()) == user_settings
    assert read_synced_settings()["publicBaseUrl"] == "https://mine.example"
    assert not (empty / "settings.json").exists()


def test_a_write_after_a_data_dir_change_starts_from_the_new_file(monkeypatch, tmp_path):
    first = _data_dir(tmp_path, "first")
    second = _data_dir(tmp_path, "second")
    (first / "settings.json").write_bytes(orjson.dumps({"peerDisplayName": "first", "_version": 1}))
    (second / "settings.json").write_bytes(orjson.dumps({"peerDisplayName": "second", "_version": 7}))

    monkeypatch.setenv("TWICC_DATA_DIR", str(first))
    read_synced_settings()
    monkeypatch.setenv("TWICC_DATA_DIR", str(second))
    current = read_synced_settings()
    current["_version"] += 1
    write_synced_settings(current)

    written = orjson.loads((second / "settings.json").read_bytes())
    assert (written["peerDisplayName"], written["_version"]) == ("second", 8)
    assert orjson.loads((first / "settings.json").read_bytes()) == {"peerDisplayName": "first", "_version": 1}


def test_the_token_cache_is_keyed_by_the_store_path(monkeypatch, tmp_path):
    """Two stores with the same mtime are still two stores."""
    monkeypatch.setattr(tokens, "_cache", {"key": object(), "records": []})
    first = _data_dir(tmp_path, "first")
    second = _data_dir(tmp_path, "second")

    monkeypatch.setenv("TWICC_DATA_DIR", str(first))
    tokens.create_token("first")
    assert [record.name for record in tokens.load_tokens_cached()] == ["first"]

    monkeypatch.setenv("TWICC_DATA_DIR", str(second))
    tokens.create_token("second")
    first_stat = (first / "api-tokens.json").stat()
    os.utime(second / "api-tokens.json", ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
    assert [record.name for record in tokens.load_tokens_cached()] == ["second"]
