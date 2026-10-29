"""Tests for terminal env sanitation (twicc.terminal).

A human terminal PTY inherits the backend process environment. Agent
harnesses / CI set env vars that force non-interactive behaviour (a rebase
that never opens an editor, a dead pager, a swallowed credential prompt);
``sanitize_terminal_env`` strips those on top of the provider purge, while
leaving the user's own config, model overrides and backend wiring untouched.
"""

from twicc.terminal import (
    _TERMINAL_ENV_STRIP_NAMES,
    purge_tmux_global_env,
    sanitize_terminal_env,
)


def test_strips_git_non_interactive_vars():
    env = {
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": ":",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/echo",
        "GIT_CONFIG_GLOBAL": "/x",
        "GIT_CONFIG_SYSTEM": "/y",
        "GIT_CONFIG_COUNT": "1",
    }
    sanitize_terminal_env(env)
    assert env == {}


def test_strips_agent_and_ci_markers():
    env = {
        "AI_AGENT": "claude-code_2-1-181_agent",
        "CLAUDE_AGENT_SDK_VERSION": "0.2.111",
        "CLAUDE_EFFORT": "xhigh",
        "CI": "true",
        "CONTINUOUS_INTEGRATION": "1",
        "DEBIAN_FRONTEND": "noninteractive",
    }
    sanitize_terminal_env(env)
    assert env == {}


def test_strips_indexed_git_config_family():
    env = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Z",
        "GIT_CONFIG_KEY_1": "core.pager", "GIT_CONFIG_VALUE_1": "cat",
    }
    sanitize_terminal_env(env)
    assert env == {}


def test_preserves_user_and_backend_vars():
    """User prefs, backend wiring and provider config must survive."""
    kept = {
        # user preferences — git falls back onto these after the strip
        "EDITOR": "vim", "VISUAL": "nvim", "PAGER": "less",
        "NO_COLOR": "1", "SSH_ASKPASS": "/usr/bin/ksshaskpass",
        # backend wiring (TWICC_*) — kept by explicit decision
        "TWICC_DATA_DIR": "/d", "TWICC_PORT": "3500", "TWICC_SESSION_COOKIE": "c",
        # provider config incl. model overrides and secrets (ANTHROPIC_*) — kept
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-sonnet-4-6",
        "ANTHROPIC_API_KEY": "secret",
        "PATH": "/bin", "HOME": "/home/u",
    }
    env = dict(kept)
    sanitize_terminal_env(env)
    assert env == kept


def test_narrow_list_not_blanket_prefix():
    """The purge is a named list, never a blanket CLAUDE_/ANTHROPIC_ wipe.

    A CLAUDE_-prefixed var that is not explicitly listed must be left alone
    (same narrow-purge rationale as the Codex helper).
    """
    env = {
        "CLAUDE_EFFORT": "xhigh",          # listed → removed
        "CLAUDE_CONFIG_DIR": "/home/u/.claude",  # not listed → kept
        "ANTHROPIC_BASE_URL": "https://api",     # not listed → kept
    }
    sanitize_terminal_env(env)
    assert env == {
        "CLAUDE_CONFIG_DIR": "/home/u/.claude",
        "ANTHROPIC_BASE_URL": "https://api",
    }


def test_noop_on_clean_env():
    env = {"PATH": "/bin", "HOME": "/home/u", "TERM": "xterm-256color"}
    before = dict(env)
    sanitize_terminal_env(env)
    assert env == before


def test_empty_env_does_not_raise():
    env = {}
    sanitize_terminal_env(env)
    assert env == {}


def test_strip_names_are_disjoint_from_kept_families():
    """Guard: the named strip list must not accidentally target kept families."""
    assert not any(
        name.startswith(("TWICC_", "ANTHROPIC_")) for name in _TERMINAL_ENV_STRIP_NAMES
    )
    assert {"EDITOR", "VISUAL", "PAGER"}.isdisjoint(_TERMINAL_ENV_STRIP_NAMES)


def test_sanitize_strips_the_openrouter_key():
    env = {"TWICC_OPENROUTER_API_KEY": "x", "TWICC_DEBUG": "1"}
    sanitize_terminal_env(env)
    assert env == {"TWICC_DEBUG": "1"}


def test_purge_tmux_global_env_noop_without_tmux(monkeypatch):
    """When tmux is not installed, the global-env purge is a silent no-op."""
    monkeypatch.setattr("twicc.terminal.get_tmux_path", lambda: None)
    # Must not raise and must not attempt any subprocess call.
    purge_tmux_global_env("twicc")
