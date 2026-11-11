"""twicc's own DJANGO_SETTINGS_MODULE and Python environment must not reach what the server launches.

Agents, terminals and subprocesses inherit the server's ``os.environ``; a
leftover ``DJANGO_SETTINGS_MODULE=twicc.settings`` would hijack the settings
of any other Django project they work on. The in-process CLI commands (run by
the MCP server) set it again, so every launch point strips it too.
"""

import os

from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.rpc import invoker
from twicc.server_env import (
    DJANGO_SETTINGS_ENV,
    UV_RUN_RECURSION_DEPTH_ENV,
    VIRTUAL_ENV_ENV,
    strip_django_settings_env,
    strip_own_python_env,
)
from twicc.terminal import sanitize_terminal_env


def test_strip_removes_the_variable_and_keeps_the_rest():
    env = {DJANGO_SETTINGS_ENV: "twicc.settings", "PATH": "/bin"}
    strip_django_settings_env(env)
    assert env == {"PATH": "/bin"}


def test_strip_is_a_noop_when_absent():
    env = {"PATH": "/bin"}
    strip_django_settings_env(env)
    assert env == {"PATH": "/bin"}


def test_provider_purge_strips_it():
    env = {DJANGO_SETTINGS_ENV: "twicc.settings"}
    get_provider_helpers_registry().purge_env_vars(env)
    assert DJANGO_SETTINGS_ENV not in env


def test_terminal_sanitation_strips_it():
    env = {DJANGO_SETTINGS_ENV: "twicc.settings"}
    sanitize_terminal_env(env)
    assert env == {}


def test_hybrid_launch_command_unsets_it():
    command = hybrid_tmux.launch_command(["claude"])
    assert f"-u {DJANGO_SETTINGS_ENV}" in command


class _FakeCommand:
    def main(self, **kwargs):
        os.environ.setdefault(DJANGO_SETTINGS_ENV, "twicc.settings")


def test_invoke_leaves_an_absent_variable_absent(monkeypatch):
    monkeypatch.delenv(DJANGO_SETTINGS_ENV, raising=False)
    monkeypatch.setattr(invoker, "_command", _FakeCommand())
    invoker.invoke(["anything"])
    assert DJANGO_SETTINGS_ENV not in os.environ


def test_invoke_restores_a_present_variable(monkeypatch):
    monkeypatch.setenv(DJANGO_SETTINGS_ENV, "twicc.settings_test")
    monkeypatch.setattr(invoker, "_command", _FakeCommand())
    invoker.invoke(["anything"])
    assert os.environ[DJANGO_SETTINGS_ENV] == "twicc.settings_test"


# ── TwiCC's own Python environment ──────────────────────────────────────────

VENV = "/opt/app/.venv"
BASE = "/usr"


def _strip(env, *, prefix=VENV, base_prefix=BASE):
    strip_own_python_env(env, prefix=prefix, base_prefix=base_prefix)
    return env


def test_python_env_removes_own_venv_from_path_and_virtual_env():
    env = _strip({
        "PATH": f"{VENV}/bin:/usr/bin:{VENV}/bin:/home/u/.local/bin",
        VIRTUAL_ENV_ENV: VENV,
        UV_RUN_RECURSION_DEPTH_ENV: "2",
        "HOME": "/home/u",
    })
    assert env == {"PATH": "/usr/bin:/home/u/.local/bin", "HOME": "/home/u"}


def test_python_env_keeps_parent_sibling_and_other_venv_dirs():
    path = f"{VENV}:{VENV}/bin/extra:/opt/app/bin:/other/.venv/bin:/usr/bin"
    env = _strip({"PATH": path})
    assert env["PATH"] == path


def test_python_env_keeps_a_foreign_virtual_env():
    env = _strip({VIRTUAL_ENV_ENV: "/other/.venv", "PATH": "/usr/bin"})
    assert env[VIRTUAL_ENV_ENV] == "/other/.venv"


def test_python_env_keeps_empty_path_entries():
    env = _strip({"PATH": f":{VENV}/bin:/usr/bin"})
    assert env["PATH"] == ":/usr/bin"


def test_python_env_matches_through_symlinks(tmp_path):
    real = tmp_path / "real-venv"
    (real / "bin").mkdir(parents=True)
    link = tmp_path / "link-venv"
    link.symlink_to(real)
    env = _strip(
        {"PATH": f"{link}/bin:/usr/bin", VIRTUAL_ENV_ENV: str(link)},
        prefix=str(real),
    )
    assert env == {"PATH": "/usr/bin"}


def test_python_env_outside_a_venv_only_drops_the_uv_marker():
    # Without a venv, sys.prefix is e.g. /usr: /usr/bin must never go.
    env = _strip(
        {"PATH": "/usr/bin:/bin", VIRTUAL_ENV_ENV: "/usr", UV_RUN_RECURSION_DEPTH_ENV: "1"},
        prefix=BASE,
    )
    assert env == {"PATH": "/usr/bin:/bin", VIRTUAL_ENV_ENV: "/usr"}


def test_python_env_without_path_is_fine():
    assert _strip({VIRTUAL_ENV_ENV: VENV}) == {}
