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
    including the ``settings_test`` provider-home values a real load would drop,
    and the in-memory secrets (``{}`` under ``settings_test``).
    """
    saved_env = dict(os.environ)
    saved_loaded, saved_warnings = paths._ENV_LOADED, list(paths._ENV_WARNINGS)
    saved_undo = dict(paths._ENV_LOAD_UNDO)
    saved_secrets = dict(paths._ENV_SECRETS)
    os.environ["TWICC_DATA_DIR"] = str(tmp_path)
    # The settings_test isolation values (and a secret the developer's shell
    # exports) would otherwise be "inherited" here.
    for key in (*paths.PROVIDER_HOME_KEYS, *paths.ENV_SECRET_KEYS):
        os.environ.pop(key, None)
    paths._reset_env_loader()
    yield tmp_path / ".env"
    os.environ.clear()
    os.environ.update(saved_env)
    paths._ENV_LOADED = saved_loaded
    paths._ENV_WARNINGS[:] = saved_warnings
    paths._ENV_LOAD_UNDO.clear()
    paths._ENV_LOAD_UNDO.update(saved_undo)
    paths._ENV_SECRETS.clear()
    paths._ENV_SECRETS.update(saved_secrets)


def test_undo_gives_back_the_inherited_environment(env_loader):
    """What ``settings_test`` relies on to keep an instance's ``.env`` out of the tests."""
    os.environ["TWICC_TEST_FOO"] = "inherited"
    os.environ["CODEX_HOME"] = "/inherited/codex"
    os.environ.pop("TWICC_TEST_BAR", None)
    env_loader.write_text("TWICC_TEST_FOO=from-file\nTWICC_TEST_BAR=from-file\n")
    paths.ensure_env_loaded()
    assert os.environ["TWICC_TEST_FOO"] == "from-file"
    assert os.environ["TWICC_TEST_BAR"] == "from-file"
    assert "CODEX_HOME" not in os.environ  # .env-exclusive: the inherited value was dropped

    paths._undo_env_load()
    assert os.environ["TWICC_TEST_FOO"] == "inherited"
    assert "TWICC_TEST_BAR" not in os.environ
    assert os.environ["CODEX_HOME"] == "/inherited/codex"
    # Still marked as loaded: the file is never read again.
    paths.ensure_env_loaded()
    assert "TWICC_TEST_BAR" not in os.environ


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


def test_secret_key_is_held_in_memory_not_in_environ(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=sk-or-test\nTWICC_TEST_FOO=1\n")
    paths.ensure_env_loaded()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") == "sk-or-test"
    assert "TWICC_OPENROUTER_API_KEY" not in os.environ
    assert os.environ["TWICC_TEST_FOO"] == "1"


def test_inherited_secret_key_is_dropped_without_logging_its_value(env_loader):
    os.environ["TWICC_OPENROUTER_API_KEY"] = "sk-or-inherited"
    paths.ensure_env_loaded()
    assert "TWICC_OPENROUTER_API_KEY" not in os.environ
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None
    assert not any("sk-or-inherited" in w for w in paths.get_env_load_warnings())
    paths._undo_env_load()
    assert os.environ["TWICC_OPENROUTER_API_KEY"] == "sk-or-inherited"


def test_secret_in_file_wins_and_the_inherited_one_is_still_dropped(env_loader):
    os.environ["TWICC_OPENROUTER_API_KEY"] = "sk-or-inherited"
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=sk-or-file\n")
    paths.ensure_env_loaded()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") == "sk-or-file"
    assert "TWICC_OPENROUTER_API_KEY" not in os.environ
    assert not any(
        "sk-or-inherited" in w or "sk-or-file" in w for w in paths.get_env_load_warnings()
    )


def test_empty_secret_reads_as_absent(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=\n")
    paths.ensure_env_loaded()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None


def test_undo_forgets_the_secrets(env_loader):
    env_loader.write_text("TWICC_OPENROUTER_API_KEY=sk-or-test\n")
    paths.ensure_env_loaded()
    paths._undo_env_load()
    assert paths.get_env_secret("TWICC_OPENROUTER_API_KEY") is None


def test_the_test_settings_hold_no_secret():
    # Outside the env_loader fixture: the state settings_test left behind.
    assert paths._ENV_SECRETS == {}
