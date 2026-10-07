"""The hybrid CLI's own "Bypass Permissions mode" confirmation is skipped: bypass is chosen in TwiCC, not in the CLI.

TwiCC drives the CLI in tmux, so the CLI's one-time dialog would leave the session stuck in "starting" until
the starting timeout stops it. It also appeared with another permission mode as soon as the opt-in flag
`--allow-dangerously-skip-permissions` was given. The setting is therefore passed in the inline `--settings`
JSON of the launch (scoped to the session, nothing is written to the user's own settings) whenever that opt-in
flag is. Untrusted projects withhold the opt-in and keep the CLI's default.
"""

import orjson
import pytest

from twicc.providers.claude_code.agent.hybrid import launch
from twicc.providers.helpers import AgentSettings

KEY = "skipDangerousModePermissionPrompt"


def settings_of(argv: list[str]) -> dict:
    """The inline `--settings` JSON of an argv (the launch passes exactly one)."""
    assert argv.count("--settings") == 1
    return orjson.loads(argv[argv.index("--settings") + 1])


def argv_for(permission_mode: str, *, untrusted: bool = False) -> list[str]:
    agent_settings = AgentSettings(
        selected_model="opus",
        effort="high",
        thinking_enabled=True,
        permission_mode=permission_mode,
        context_max=200_000,
        claude_in_chrome=False,
        fast_mode=False,
    )
    return launch.build_argv(
        session_id="11111111-1111-4111-8111-111111111111",
        settings=agent_settings,
        resume=False,
        temp_title="t",
        addendum_path=None,
        add_dirs=[],
        untrusted=untrusted,
    )


def test_hooks_settings_carry_the_skip_flag_only_when_asked():
    assert settings_of(["--settings", launch.build_hooks_settings("s", False, skip_bypass_dialog=True)])[KEY] is True
    assert KEY not in settings_of(["--settings", launch.build_hooks_settings("s", False)])
    assert KEY not in settings_of(["--settings", launch.build_hooks_settings("s", False, skip_bypass_dialog=False)])


def test_a_session_started_in_bypass_permissions_skips_the_cli_dialog():
    argv = argv_for("bypassPermissions")

    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert settings_of(argv)[KEY] is True
    # The hooks setting the launch always passes are still there, in the same single --settings.
    assert "hooks" in settings_of(argv)
    assert settings_of(argv)["fileCheckpointingEnabled"] is True


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan"])
def test_the_opt_in_flag_always_comes_with_the_skip_setting(mode):
    """The CLI dialog also showed up with another permission mode: the opt-in flag alone triggered it."""
    argv = argv_for(mode)

    assert argv[argv.index("--permission-mode") + 1] == mode
    assert "--allow-dangerously-skip-permissions" in argv
    assert settings_of(argv)[KEY] is True


def test_an_untrusted_project_never_skips_the_dialog():
    """bypassPermissions is clamped out in an untrusted project (security floor): no skip, no opt-in."""
    argv = argv_for("bypassPermissions", untrusted=True)

    assert argv[argv.index("--permission-mode") + 1] != "bypassPermissions"
    assert KEY not in settings_of(argv)
    assert "--allow-dangerously-skip-permissions" not in argv
