"""devctl's provider-home handling (section 9 of the provider-home design)."""

import importlib.util
from pathlib import Path

import pytest


def _load_devctl():
    path = Path(__file__).resolve().parents[1] / "devctl.py"
    spec = importlib.util.spec_from_file_location("devctl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def devctl(tmp_path, monkeypatch):
    module = _load_devctl()
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(module, "is_git_worktree", lambda: False)
    return module


def test_purge_provider_home_vars(devctl):
    env = {"CLAUDE_CONFIG_DIR": "/a", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "", "CODEX_HOME": "/b", "PATH": "/bin"}
    devctl.purge_provider_home_vars(env)
    assert env == {"PATH": "/bin"}


def test_provider_home_env_only_defined_keys(devctl):
    assert devctl.provider_home_env({"CODEX_HOME": "/b", "TWICC_PORT": "3500"}) == {"CODEX_HOME": "/b"}
    assert devctl.provider_home_env({"CLAUDE_SECURESTORAGE_CONFIG_DIR": ""}) == {"CLAUDE_SECURESTORAGE_CONFIG_DIR": ""}
    assert devctl.provider_home_env({}) == {}


def test_build_process_env_keeps_no_inherited_home_and_readds_the_env_ones(devctl, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/inherited-claude")
    monkeypatch.setenv("CODEX_HOME", "/inherited-codex")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    devctl.ENV_FILE.write_text("CODEX_HOME=/mine\nTWICC_PORT=3500\n")
    env = devctl.build_process_env({"env": {"TWICC_DEBUG": "1"}})
    assert "CLAUDE_CONFIG_DIR" not in env
    assert env["CODEX_HOME"] == "/mine"
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["TWICC_DEBUG"] == "1"


def test_build_process_env_without_env_file(devctl, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "/inherited-codex")
    env = devctl.build_process_env({})
    assert "CODEX_HOME" not in env


def test_worktree_codex_plugin_flag_only_without_own_home(devctl, monkeypatch):
    monkeypatch.setattr(devctl, "is_git_worktree", lambda: True)
    config = devctl.get_process_config(3501, 5174)
    backend_env = config["back"]["env"]
    assert backend_env["TWICC_NO_CODEX_PLUGIN"] == "1"
    assert "TWICC_NO_TMUX_CLEANUP" not in backend_env

    devctl.ENV_FILE.write_text("CODEX_HOME=/mine\n")
    config = devctl.get_process_config(3501, 5174)
    assert "TWICC_NO_CODEX_PLUGIN" not in config["back"]["env"]


def test_describe_provider_homes(devctl):
    home = Path.home()
    assert devctl.describe_provider_homes({}) == [
        f"Claude Code home: {home / '.claude'} (default)",
        f"Codex home: {home / '.codex'} (default)",
    ]
    assert devctl.describe_provider_homes({
        "CLAUDE_CONFIG_DIR": "/x", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "", "CODEX_HOME": "/y",
    }) == [
        "Claude Code home: /x (CLAUDE_CONFIG_DIR from .env)",
        f"Claude Code credentials: {home / '.claude'} (CLAUDE_SECURESTORAGE_CONFIG_DIR empty)",
        "Codex home: /y (CODEX_HOME from .env)",
    ]
    assert devctl.describe_provider_homes({"CLAUDE_SECURESTORAGE_CONFIG_DIR": "/z"})[1] == (
        "Claude Code credentials: /z (CLAUDE_SECURESTORAGE_CONFIG_DIR from .env)"
    )


def test_line_warnings_for_non_plain_lines(devctl):
    devctl.ENV_FILE.write_text(
        "export CLAUDE_CONFIG_DIR=/x\n"
        "CODEX_HOME=${HOME}/codex\n"
        "CLAUDE_SECURESTORAGE_CONFIG_DIR=/y # keep\n"
        "TWICC_PORT=3500 # unrelated key, not checked\n"
        "# CODEX_HOME=/commented\n"
    )
    warnings = devctl.provider_home_line_warnings()
    assert len(warnings) == 3
    assert "CLAUDE_CONFIG_DIR" in warnings[0] and "'export ' prefix" in warnings[0]
    assert "CODEX_HOME" in warnings[1] and "interpolation" in warnings[1]
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" in warnings[2] and "inline '#' comment" in warnings[2]


def test_line_warnings_plain_lines_are_silent(devctl):
    devctl.ENV_FILE.write_text("CLAUDE_CONFIG_DIR=/x\nCODEX_HOME=/y\nCLAUDE_SECURESTORAGE_CONFIG_DIR=\n")
    assert devctl.provider_home_line_warnings() == []


def test_unused_home_hints(devctl, tmp_path):
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "tmp").mkdir()  # what Codex creates on its first run — no signal
    claude = tmp_path / "claude"
    claude.mkdir()
    hints = devctl.provider_home_hints({"CODEX_HOME": str(codex), "CLAUDE_CONFIG_DIR": str(claude)})
    assert hints == [
        f'CODEX_HOME={codex} looks unused: run "twicc codex login" from this instance\'s terminal',
        f"CLAUDE_CONFIG_DIR={claude} looks unused: log in from a Claude session of this instance",
    ]
    (codex / "auth.json").write_text("{}")
    (claude / "projects").mkdir()
    assert devctl.provider_home_hints({"CODEX_HOME": str(codex), "CLAUDE_CONFIG_DIR": str(claude)}) == []
    # Credentials elsewhere → the Claude check is skipped
    (claude / "projects").rmdir()
    assert devctl.provider_home_hints({
        "CLAUDE_CONFIG_DIR": str(claude), "CLAUDE_SECURESTORAGE_CONFIG_DIR": "",
    }) == []
    assert devctl.provider_home_hints({}) == []


def test_kill_tmux_refuses_on_the_default_data_dir(devctl, monkeypatch):
    monkeypatch.setattr(devctl, "DATA_DIR", devctl.DEFAULT_DATA_DIR)
    with pytest.raises(SystemExit) as exc:
        devctl.kill_tmux()
    assert exc.value.code == 1


def test_kill_tmux_targets_both_suffixed_sockets(devctl, monkeypatch):
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(devctl.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(devctl.subprocess, "run", lambda argv, **kw: calls.append(argv) or Result())
    devctl.kill_tmux()
    terminal_socket, hybrid_socket = devctl.tmux_socket_names()
    assert terminal_socket != "twicc"
    assert calls == [
        ["/usr/bin/tmux", "-L", terminal_socket, "kill-server"],
        ["/usr/bin/tmux", "-L", hybrid_socket, "kill-server"],
    ]
