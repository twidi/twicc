"""The configured provider homes reach every launched process and survive every purge.

Design: docs/plans/2026-09-02-provider-home-dirs-design.md (sections 4, 7, 7.1).
"""

import ast
import importlib
import os
from pathlib import Path

import pytest

from twicc.core.enums import Provider
from twicc.provider_homes import provider_env_overlay
from twicc.providers.helpers import get_provider_helpers, get_provider_helpers_registry
from twicc.terminal import _tmux_client_argv, sanitize_terminal_env

pytestmark = pytest.mark.django_db


@pytest.fixture
def overlay(provider_home):
    values = provider_env_overlay()
    assert values["CLAUDE_CONFIG_DIR"] == str(provider_home.claude)
    assert values["CODEX_HOME"] == str(provider_home.codex)
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" in values  # settings_test sets it to a path
    return values


def _polluted(overlay: dict[str, str]) -> dict[str, str]:
    return {
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
        "CLAUDECODE": "1",
        "CODEX_SANDBOX": "seatbelt",
        "CODEX_NETWORK_DISABLED": "1",
        "GIT_EDITOR": "true",
        "PATH": "/bin",
        **overlay,
    }


class TestPurgesKeepTheOverlay:
    def test_claude_helpers_purge(self, overlay):
        env = _polluted(overlay)
        get_provider_helpers(Provider.CLAUDE_CODE).purge_env_vars(env)
        assert "CLAUDE_CODE_ENTRYPOINT" not in env
        for key, value in overlay.items():
            assert env[key] == value

    def test_codex_helpers_purge(self, overlay):
        env = _polluted(overlay)
        get_provider_helpers(Provider.CODEX).purge_env_vars(env)
        assert "CODEX_SANDBOX" not in env
        for key, value in overlay.items():
            assert env[key] == value

    def test_registry_purge_keeps_and_reapplies(self, overlay):
        env = _polluted(overlay)
        get_provider_helpers_registry().purge_env_vars(env)
        assert "CLAUDE_CODE_ENTRYPOINT" not in env and "CODEX_SANDBOX" not in env
        for key, value in overlay.items():
            assert env[key] == value
        # Re-applied even when a caller stripped them beforehand
        env = {"PATH": "/bin"}
        get_provider_helpers_registry().purge_env_vars(env)
        for key, value in overlay.items():
            assert env[key] == value

    def test_terminal_sanitizer(self, overlay):
        env = _polluted(overlay)
        sanitize_terminal_env(env)
        assert "GIT_EDITOR" not in env
        for key, value in overlay.items():
            assert env[key] == value

    def test_hybrid_purged_names_never_list_the_overlay(self, overlay, monkeypatch):
        from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux

        for key, value in overlay.items():
            monkeypatch.setenv(key, value)
        assert not set(overlay) & set(hybrid_tmux._purged_env_names())

    def test_devctl_purge(self, overlay):
        devctl = _load_devctl()
        env = _polluted(overlay)
        devctl.purge_claude_code_vars(env)
        assert "CLAUDE_CODE_ENTRYPOINT" not in env
        for key, value in overlay.items():
            assert env[key] == value


class TestLaunchPoints:
    def test_codex_env_contains_the_overlay(self, overlay, monkeypatch, tmp_path):
        from twicc.providers.codex import bin as codex_bin

        monkeypatch.setattr(codex_bin, "codex_path_dir", lambda: tmp_path / "codex-path")
        env = codex_bin._codex_env()
        assert env["PATH"].startswith(str(tmp_path / "codex-path"))
        for key, value in overlay.items():
            assert env[key] == value

    def test_hybrid_launch_command(self, overlay, monkeypatch):
        from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux

        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
        command = hybrid_tmux.launch_command(["claude", "--resume", "abc"])
        assert command.startswith("exec env ")
        assert "-u CLAUDE_CODE_ENTRYPOINT" in command
        assert "CLAUDE_CODE_NO_FLICKER=1" in command
        for key, value in overlay.items():
            assert f"{key}={value}" in command
            assert f"-u {key}" not in command
        assert command.endswith("claude --resume abc")

    def test_tmux_new_session_argv_carries_e_flags(self, overlay):
        argv = _tmux_client_argv(
            socket="twicc-abc", config_arg="/dev/null", name="twicc-global",
            attach_only=False, env_overlay=overlay,
        )
        assert argv[:8] == ["tmux", "-L", "twicc-abc", "-f", "/dev/null", "new-session", "-A", "-s"]
        assert argv[8] == "twicc-global"
        tail = argv[9:]
        assert tail == [item for key, value in overlay.items() for item in ("-e", f"{key}={value}")]

    def test_tmux_attach_argv_has_no_e_flags(self, overlay):
        argv = _tmux_client_argv(
            socket="twicc-hybrid-abc", config_arg="/dev/null", name="twicc-hybrid-x",
            attach_only=True, env_overlay=overlay,
        )
        assert argv == ["tmux", "-L", "twicc-hybrid-abc", "-f", "/dev/null", "attach-session", "-t", "=twicc-hybrid-x"]

    def test_twicc_claude_exec_sees_the_overlay(self, overlay, monkeypatch):
        cli_claude = importlib.import_module("twicc.cli.claude")  # not the typer command of the same name

        seen: dict[str, str] = {}

        def fake_execvp(binary, argv):
            seen.update({key: os.environ.get(key, "<missing>") for key in overlay})
            seen["argv"] = argv

        monkeypatch.setattr(cli_claude, "resolve_bundled_binary", lambda: Path("/fake/claude"))
        monkeypatch.setattr(cli_claude.os, "execvp", fake_execvp)
        cli_claude.main(["--version"])
        assert seen["argv"] == ["/fake/claude", "--version"]
        for key, value in overlay.items():
            assert seen[key] == value

    def test_twicc_codex_exec_sees_the_overlay_and_creates_the_home(self, overlay, provider_home, monkeypatch):
        cli_codex = importlib.import_module("twicc.cli.codex")  # not the typer command of the same name

        provider_home.codex.rmdir()
        seen: dict[str, str] = {}

        def fake_execvp(binary, argv):
            seen.update({key: os.environ.get(key, "<missing>") for key in overlay})

        monkeypatch.setattr(cli_codex, "ensure_codex_runtime_sync", lambda: None)
        monkeypatch.setattr(cli_codex, "resolve_bundled_binary", lambda: Path("/fake/codex"))
        monkeypatch.setattr(cli_codex.os, "execvp", fake_execvp)
        cli_codex.main(["login", "status"])
        assert provider_home.codex.is_dir()
        for key, value in overlay.items():
            assert seen[key] == value


def test_cli_package_loads_the_env_before_any_command_module():
    """``cli/__init__.py`` calls ``ensure_env_loaded()`` before importing ``twicc.cli.*``."""
    import twicc.cli as cli_package

    tree = ast.parse(Path(cli_package.__file__).read_text())
    load_index = first_cli_import_index = None
    for index, node in enumerate(tree.body):
        if (
            load_index is None
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "ensure_env_loaded"
        ):
            load_index = index
        if (
            first_cli_import_index is None
            and isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("twicc.cli")
        ):
            first_cli_import_index = index
    assert load_index is not None
    assert first_cli_import_index is not None
    assert load_index < first_cli_import_index


def _load_devctl():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "devctl.py"
    spec = importlib.util.spec_from_file_location("devctl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
