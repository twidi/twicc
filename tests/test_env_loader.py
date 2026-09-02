"""``twicc.paths.ensure_env_loaded``: the once-per-process ``.env`` loader.

Design: docs/plans/2026-09-02-provider-home-dirs-design.md (sections 3.2, 4).
"""

import os

import pytest

from twicc import paths


@pytest.fixture
def env_loader(tmp_path):
    """Run the loader again against ``<tmp_path>/.env``; restore everything after.

    The loader mutates ``os.environ`` directly (not through monkeypatch), so the
    whole environment and the loader's state are snapshotted and put back —
    including the ``settings_test`` provider-home values a real load would drop.
    """
    saved_env = dict(os.environ)
    saved_loaded, saved_warnings = paths._ENV_LOADED, list(paths._ENV_WARNINGS)
    os.environ["TWICC_DATA_DIR"] = str(tmp_path)
    # The settings_test isolation values would otherwise be "inherited" here.
    for key in paths.PROVIDER_HOME_KEYS:
        os.environ.pop(key, None)
    paths._reset_env_loader()
    yield tmp_path / ".env"
    os.environ.clear()
    os.environ.update(saved_env)
    paths._ENV_LOADED = saved_loaded
    paths._ENV_WARNINGS[:] = saved_warnings


def test_loads_once_per_process(env_loader):
    env_loader.write_text("TWICC_TEST_FOO=1\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_TEST_FOO"] == "1"
    env_loader.write_text("TWICC_TEST_FOO=2\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_TEST_FOO"] == "1"


def test_missing_env_file_is_fine(env_loader):
    assert not env_loader.exists()
    paths.ensure_env_loaded()
    assert paths.get_env_load_warnings() == []


def test_file_wins_over_inherited_value(env_loader):
    os.environ["TWICC_TEST_FOO"] = "inherited"
    env_loader.write_text("TWICC_TEST_FOO=file\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_TEST_FOO"] == "file"


def test_non_provider_key_absent_from_file_is_kept(env_loader):
    os.environ["TWICC_TEST_FOO"] = "inherited"
    env_loader.write_text("TWICC_PORT=3500\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_TEST_FOO"] == "inherited"
    assert paths.get_env_load_warnings() == []


@pytest.mark.parametrize("key", paths.PROVIDER_HOME_KEYS)
def test_inherited_provider_key_absent_from_file_is_dropped(env_loader, key):
    os.environ[key] = "/inherited"
    env_loader.write_text("TWICC_PORT=3500\n")
    paths.ensure_env_loaded()
    assert key not in os.environ
    assert paths.get_env_load_warnings() == [
        f"Ignoring inherited {key}='/inherited': not set in {env_loader}",
    ]


def test_inherited_provider_key_with_no_file_is_dropped(env_loader):
    os.environ["CODEX_HOME"] = "/inherited"
    paths.ensure_env_loaded()
    assert "CODEX_HOME" not in os.environ
    assert len(paths.get_env_load_warnings()) == 1


def test_bare_key_line_counts_as_absent(env_loader):
    os.environ["CODEX_HOME"] = "/inherited"
    env_loader.write_text("CODEX_HOME\n")
    paths.ensure_env_loaded()
    assert "CODEX_HOME" not in os.environ
    assert paths.get_env_load_warnings()[0].startswith("Ignoring inherited CODEX_HOME=")


def test_empty_value_counts_as_defined(env_loader):
    os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = "/inherited"
    env_loader.write_text("CLAUDE_SECURESTORAGE_CONFIG_DIR=\n")
    paths.ensure_env_loaded()
    assert os.environ["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == ""
    assert paths.get_env_load_warnings() == []


def test_provider_key_in_file_is_loaded(env_loader):
    os.environ["CLAUDE_CONFIG_DIR"] = "/inherited"
    env_loader.write_text("CLAUDE_CONFIG_DIR=/from-file\nCODEX_HOME=/codex-from-file\n")
    paths.ensure_env_loaded()
    assert os.environ["CLAUDE_CONFIG_DIR"] == "/from-file"
    assert os.environ["CODEX_HOME"] == "/codex-from-file"
    assert paths.get_env_load_warnings() == []


def test_twicc_data_dir_in_file_is_skipped(env_loader, tmp_path):
    env_loader.write_text("TWICC_DATA_DIR=/elsewhere\nTWICC_PORT=3500\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_DATA_DIR"] == str(tmp_path)
    assert os.environ["TWICC_PORT"] == "3500"
    assert paths.get_env_load_warnings() == [
        f"Ignoring TWICC_DATA_DIR in {env_loader}: environment-only",
    ]
    assert paths.get_data_dir() == tmp_path.resolve()


def test_warnings_are_a_copy(env_loader):
    os.environ["CODEX_HOME"] = "/inherited"
    paths.ensure_env_loaded()
    warnings = paths.get_env_load_warnings()
    warnings.clear()
    assert len(paths.get_env_load_warnings()) == 1
