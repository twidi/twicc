"""The settings TwiCC forces on every Claude launch reach both the SDK agent and the hybrid CLI.

``FORCED_CLI_SETTINGS`` holds what the user's own settings.json must not be able to change: today, idle
compaction off (the CLI compacts a 200K+ token conversation left idle for about 54 minutes, and a compaction
nobody asked for must not appear in an idle session). Two launch paths build their own ``--settings`` JSON, so
each is pinned here; whether the installed CLI still reads the key is the post-bump diagnostic's job
(``scripts/diagnose_claude_sdk.py``), not a unit test's.
"""

import orjson
import pytest

from twicc.providers.claude_code.agent.agent import build_sdk_flag_settings
from twicc.providers.claude_code.agent.hybrid import launch
from twicc.providers.claude_code.constants import FORCED_CLI_SETTINGS
from twicc.providers.helpers import AgentSettings


def test_idle_compaction_is_forced_off():
    assert FORCED_CLI_SETTINGS["idleCompaction"] is False


def test_forced_settings_leave_fast_mode_to_the_session():
    """``fastMode`` is the per-session choice each launch path passes itself; the shared table must not shadow it."""
    assert "fastMode" not in FORCED_CLI_SETTINGS


@pytest.mark.parametrize("fast_mode", [True, False])
def test_sdk_settings_carry_the_forced_settings_and_the_fast_mode_choice(fast_mode):
    settings = orjson.loads(build_sdk_flag_settings(fast_mode))
    for key, value in FORCED_CLI_SETTINGS.items():
        assert settings[key] is value
    assert settings["fastMode"] is fast_mode


@pytest.mark.parametrize("fast_mode", [True, False])
@pytest.mark.parametrize("skip_bypass_dialog", [True, False])
def test_hybrid_settings_carry_the_forced_settings_and_the_fast_mode_choice(fast_mode, skip_bypass_dialog):
    settings = orjson.loads(launch.build_hooks_settings("sid", fast_mode, skip_bypass_dialog=skip_bypass_dialog))
    for key, value in FORCED_CLI_SETTINGS.items():
        assert settings[key] is value
    assert settings["fastMode"] is fast_mode


def test_hybrid_launch_argv_passes_the_forced_settings():
    agent_settings = AgentSettings(
        selected_model="opus",
        effort="high",
        thinking_enabled=True,
        permission_mode="default",
        context_max=200_000,
        claude_in_chrome=False,
        fast_mode=False,
    )
    argv = launch.build_argv(
        session_id="11111111-1111-4111-8111-111111111111",
        settings=agent_settings,
        resume=False,
        temp_title="t",
        addendum_path=None,
        add_dirs=[],
        untrusted=False,
    )
    assert argv.count("--settings") == 1
    settings = orjson.loads(argv[argv.index("--settings") + 1])
    for key, value in FORCED_CLI_SETTINGS.items():
        assert settings[key] is value
