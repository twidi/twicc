"""Plan target resolution: effective settings, live 1M context, Claude platform (spec §6.2)."""

import asyncio
from pathlib import Path

import orjson
import pytest

from twicc import provider_homes, synced_settings
from twicc.core.models import Project
from twicc.core.services.attachments.target import PLATFORM_FLAGS, resolve_plan_target
from twicc.core.services.attachments.types import PlanTarget
from twicc.providers.helpers import AgentSettings

pytestmark = pytest.mark.django_db(transaction=True)

ONE_M = 1_000_000
TWO_HUNDRED_K = 200_000
SIX_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_USE_MANTLE",
)


class FakeAgent:
    def __init__(self, settings: AgentSettings):
        self.agent_settings = settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, provider_home):
    monkeypatch.setenv("TWICC_DATA_DIR", str(tmp_path / "data"))
    # A new data dir does not invalidate settings cached by earlier tests.
    monkeypatch.setattr(synced_settings, "_cache", {})
    monkeypatch.setattr(synced_settings, "_routing_settings_available", True)
    for flag in SIX_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    return provider_home


@pytest.fixture
def project_dir(tmp_path) -> Path:
    directory = tmp_path / "project"
    (directory / ".claude").mkdir(parents=True)
    return directory


def claude(model="opus", context_max=TWO_HUNDRED_K) -> AgentSettings:
    return AgentSettings(selected_model=model, context_max=context_max, effort="medium", permission_mode="default")


def resolve(*, provider="claude_code", settings=None, directory="/nonexistent", hybrid=False, ephemeral=False,
            live_agent=None) -> PlanTarget:
    return asyncio.run(
        resolve_plan_target(
            provider=provider,
            effective_settings=settings if settings is not None else claude(),
            directory=str(directory),
            hybrid=hybrid,
            ephemeral=ephemeral,
            live_agent=live_agent,
        )
    )


def write_settings(path: Path, env) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps({"env": env}))


# ── Target fields ──


def test_the_six_platform_flags():
    assert tuple(PLATFORM_FLAGS) == SIX_FLAGS


def test_claude_target_fields():
    target = resolve(settings=claude("sonnet"), hybrid=True, ephemeral=False)
    assert target == PlanTarget("claude_code", True, False, "sonnet", False, "first_party")


def test_ephemeral_is_carried():
    assert resolve(ephemeral=True).ephemeral is True


def test_codex_target_fields():
    settings = AgentSettings(selected_model="gpt-sol", context_max=272_000, effort="medium", fast_mode=False)
    target = resolve(provider="codex", settings=settings, ephemeral=True)
    assert target == PlanTarget("codex", False, True, "gpt-sol", False, "first_party")


def test_requested_settings_are_resolved_and_enforced_before_the_target():
    # Unset fields fall back to the synced defaults (model "opus", 200K context).
    target = resolve(settings=AgentSettings())
    assert (target.model, target.context_1m) == ("opus", False)


# ── Live 1M context ──


@pytest.mark.parametrize(
    ("requested", "live", "expected"),
    [
        (claude("opus", ONE_M), None, True),
        (claude("opus", TWO_HUNDRED_K), None, False),
        (claude("opus-4.5", ONE_M), None, False),  # not 1M-capable: enforced down to 200K
        (claude("opus", ONE_M), claude("opus", ONE_M), True),
        (claude("opus", ONE_M), claude("opus", TWO_HUNDRED_K), False),
        (claude("opus", TWO_HUNDRED_K), claude("opus", ONE_M), False),
        (claude("opus", ONE_M), claude("opus-4.5", ONE_M), False),  # live model not 1M-capable
        (claude("opus", ONE_M), claude("sonnet", ONE_M), True),
    ],
)
def test_context_1m_needs_requested_and_live_settings(requested, live, expected):
    agent = FakeAgent(live) if live is not None else None
    assert resolve(settings=requested, live_agent=agent).context_1m is expected


def test_codex_never_has_context_1m():
    settings = AgentSettings(selected_model="gpt-sol", context_max=ONE_M)
    assert resolve(provider="codex", settings=settings).context_1m is False


# ── Claude platform from settings files ──


@pytest.mark.parametrize("flag", SIX_FLAGS)
def test_each_flag_in_user_settings_means_third_party(isolated, flag):
    write_settings(isolated.claude / "settings.json", {flag: "1"})
    assert resolve().platform == "third_party"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "On", "\ttrue\n", True, 1])
def test_truthy_values(isolated, value):
    write_settings(isolated.claude / "settings.json", {"CLAUDE_CODE_USE_BEDROCK": value})
    assert resolve().platform == "third_party"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "2", "y", "enabled", "truthy", False, 0, None, []])
def test_other_values_are_false(isolated, value):
    write_settings(isolated.claude / "settings.json", {"CLAUDE_CODE_USE_BEDROCK": value})
    assert resolve().platform == "first_party"


def test_other_keys_are_ignored(isolated):
    write_settings(isolated.claude / "settings.json", {"ANTHROPIC_BASE_URL": "https://x", "CLAUDE_CODE_USE_OTHER": "1"})
    assert resolve().platform == "first_party"


@pytest.mark.parametrize("content", [b"not json", b"[1]", b'{"env": "CLAUDE_CODE_USE_BEDROCK=1"}', b'{"env": null}'])
def test_malformed_settings_are_ignored(isolated, content):
    (isolated.claude / "settings.json").write_bytes(content)
    assert resolve().platform == "first_party"


@pytest.mark.parametrize("flag", SIX_FLAGS)
def test_inherited_environment_is_ignored(monkeypatch, flag):
    monkeypatch.setenv(flag, "1")
    assert resolve().platform == "first_party"


def test_user_settings_follow_the_resolved_provider_home_at_call_time(tmp_path, monkeypatch, isolated):
    other = tmp_path / "other-claude-home"
    write_settings(other / "settings.json", {"CLAUDE_CODE_USE_VERTEX": "true"})
    assert resolve().platform == "first_party"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    provider_homes.reset_cache()
    assert resolve().platform == "third_party"


@pytest.mark.parametrize("filename", ["settings.json", "settings.local.json"])
@pytest.mark.parametrize("flag", SIX_FLAGS)
def test_trusted_project_settings_are_read(project_dir, filename, flag):
    Project.objects.create(id="trusted-project", directory=str(project_dir), trust=True)
    write_settings(project_dir / ".claude" / filename, {flag: "yes"})
    assert resolve(directory=project_dir).platform == "third_party"


@pytest.mark.parametrize("trust", [False, None])
@pytest.mark.parametrize("filename", ["settings.json", "settings.local.json"])
def test_untrusted_project_settings_are_ignored(project_dir, trust, filename):
    Project.objects.create(id="untrusted-project", directory=str(project_dir), trust=trust)
    write_settings(project_dir / ".claude" / filename, {"CLAUDE_CODE_USE_BEDROCK": "1"})
    assert resolve(directory=project_dir).platform == "first_party"


def test_project_settings_without_a_project_row_are_ignored(project_dir):
    write_settings(project_dir / ".claude" / "settings.json", {"CLAUDE_CODE_USE_BEDROCK": "1"})
    assert resolve(directory=project_dir).platform == "first_party"


def test_trust_is_inherited_from_an_ancestor_project(tmp_path):
    parent = tmp_path / "repo"
    child = parent / "sub"
    (child / ".claude").mkdir(parents=True)
    Project.objects.create(id="repo", directory=str(parent), trust=True, trust_propagation=True)
    Project.objects.create(id="repo-sub", directory=str(child))
    write_settings(child / ".claude" / "settings.json", {"CLAUDE_CODE_USE_MANTLE": "on"})
    assert resolve(directory=child).platform == "third_party"


def test_codex_ignores_claude_settings(isolated):
    write_settings(isolated.claude / "settings.json", {"CLAUDE_CODE_USE_BEDROCK": "1"})
    settings = AgentSettings(selected_model="gpt-sol", context_max=272_000)
    assert resolve(provider="codex", settings=settings).platform == "first_party"
