"""Codex Fast mode model gating and settings consistency."""

from __future__ import annotations

import asyncio

import pytest

import twicc.synced_settings as ss
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.helpers import CodexHelpers
from twicc.providers.helpers import AgentSettingCategory, AgentSettings


@pytest.fixture
def helpers() -> CodexHelpers:
    return CodexHelpers()


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.mark.parametrize(
    ("selected_model", "expected"),
    [
        ("gpt-sol", True),
        ("gpt-terra", True),
        ("gpt-luna", True),
        ("gpt", True),
        ("gpt-5.4", True),
        ("gpt-mini", False),
    ],
)
def test_selected_model_fast_support(helpers, selected_model, expected) -> None:
    assert helpers.selected_model_supports_fast(selected_model) is expected


def test_fast_mode_is_idle_setting_with_default(helpers) -> None:
    assert "fast_mode" in helpers.AGENT_SETTINGS_CATEGORIES[AgentSettingCategory.IDLE]
    assert helpers.AGENT_SETTINGS_FIELDS_MAPPING["fast_mode"] == "codexDefaultFastMode"
    assert helpers.SYNCED_SETTINGS_DEFAULTS["codexDefaultFastMode"] is False
    assert helpers.get_agent_settings_choices()["fast_mode"] == [True, False]


def test_fast_constraint_lists_only_supported_models(helpers) -> None:
    supported = set(helpers.get_agent_settings_constraints()["fast_mode"][True])
    assert supported == {
        "gpt-sol-5.6", "gpt-sol",
        "gpt-terra-5.6", "gpt-terra",
        "gpt-luna-5.6", "gpt-luna",
        "gpt-5.5", "gpt", "gpt-5.4",
    }


# Commented out on 2026-09-01, not deleted. Both tests below need a Codex model
# that does NOT support fast mode, and ``gpt-mini`` was the only one. It retired
# on 2026-08-31, so ``enforce_agent_settings_consistency`` now upgrades it to
# ``gpt-luna`` first — which does support fast mode — and the clamp never fires.
# Every remaining Codex model supports it, so there is no replacement to swap in.
#
# Kept as-is so the coverage comes back for free the day a Codex model ships
# without fast mode. That looks unlikely (OpenAI has enabled it on every new
# model), which is exactly why this is worth leaving in sight rather than
# deleting: if it ever happens, uncomment and swap ``gpt-mini`` for the new one.
#
# def test_consistency_disables_fast_mode_for_mini(helpers) -> None:
#     settings = AgentSettings(selected_model="gpt-mini", fast_mode=True)
#     assert helpers.enforce_agent_settings_consistency(settings).fast_mode is False


def test_consistency_keeps_fast_mode_for_supported_model(helpers) -> None:
    settings = AgentSettings(selected_model="gpt-sol", fast_mode=True)
    assert helpers.enforce_agent_settings_consistency(settings) is settings


# Commented out with the test above, same cause: no Codex model without fast
# mode is left to clamp against since ``gpt-mini`` retired on 2026-08-31.
#
# def test_synced_fast_mode_is_reclamped_when_written(helpers, temp_settings) -> None:
#     synced = {"codexDefaultModel": "gpt-mini", "codexDefaultFastMode": True}
#     helpers.enforce_synced_settings_consistency(synced, dict(synced))
#     assert synced["codexDefaultFastMode"] is False


class _SettingsThread:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def update_settings_with_policy(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_live_agent_persists_changed_fast_tier() -> None:
    agent = object.__new__(CodexAgent)
    agent.agent_settings = AgentSettings(fast_mode=False)
    agent._thread = _SettingsThread()

    asyncio.run(agent.apply_agent_settings(AgentSettings(fast_mode=True)))

    assert agent._thread.calls == [{"service_tier": "priority"}]
    assert agent.agent_settings.fast_mode is True
