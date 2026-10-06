"""The CLI's one-time Chrome intro dialog state is read from its global config file, which TwiCC never writes.

Without it the dialog would block the tmux session in "starting" until the starting timeout, so the hybrid
agent launches without Chrome (and records `claude_in_chrome=False` on the session) until it is completed.
"""

import asyncio

import orjson
import pytest

from twicc.providers.claude_code.agent.hybrid import launch


@pytest.fixture
def global_config(tmp_path, monkeypatch):
    path = tmp_path / ".claude.json"
    monkeypatch.setattr("twicc.provider_homes.claude_global_config_path", lambda: path)
    return path


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (orjson.dumps({"hasCompletedClaudeInChromeOnboarding": True}), True),
        (orjson.dumps({"hasCompletedClaudeInChromeOnboarding": False}), False),
        (orjson.dumps({}), False),
        (b"not json", False),
        (b"[]", False),
        (None, False),
    ],
)
def test_onboarding_state_is_read_from_the_cli_global_config(global_config, content, expected):
    if content is not None:
        global_config.write_bytes(content)
    assert launch.chrome_onboarding_completed() is expected


@pytest.mark.django_db(transaction=True)
def test_withholding_chrome_is_recorded_on_the_session(monkeypatch):
    from twicc.core.models import Project, Session
    from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent
    from twicc.providers.helpers import AgentSettings

    async def direct(operation):
        return await operation()

    monkeypatch.setattr("twicc.providers.db_writer.run_under_db_write_lock", direct)
    project = Project.objects.create(id="chrome-project")
    Session.objects.create(id="chrome-session", project=project, provider="claude_code", claude_in_chrome=True)
    agent = HybridClaudeAgent(
        "chrome-session", "chrome-project", "/tmp", AgentSettings(permission_mode="default", claude_in_chrome=True),
    )

    asyncio.run(agent._withhold_chrome())

    assert agent.agent_settings.claude_in_chrome is False
    assert Session.objects.get(id="chrome-session").claude_in_chrome is False
