"""twicc's own DJANGO_SETTINGS_MODULE must not reach what the server launches.

Agents, terminals and subprocesses inherit the server's ``os.environ``; a
leftover ``DJANGO_SETTINGS_MODULE=twicc.settings`` would hijack the settings
of any other Django project they work on. The in-process CLI commands (run by
the MCP server) set it again, so every launch point strips it too.
"""

import os

from twicc.providers.claude_code.agent.hybrid import tmux as hybrid_tmux
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.rpc import invoker
from twicc.server_env import DJANGO_SETTINGS_ENV, strip_django_settings_env
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
