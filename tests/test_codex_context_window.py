"""Tests for the per-model Codex context window.

The window is a fixed property of the model (``CodexModelExtra.context_window``),
not a user choice: ``enforce_agent_settings_consistency`` pins ``context_max``
to the resolved model's window in both directions, the synced defaults are
re-clamped against the default model, and ``extract_runtime_fields`` recovers
the values from the JSONL ``task_started`` lines (published as 95% of the
nominal input window).

NOTE: ``GPT_56_CONTEXT_WINDOW_TEMPORARILY_REDUCED`` is currently on — OpenAI
rolled the GPT-5.6 tiers back to the pre-5.6 272K, so every model resolves to
272K here. The 372K plumbing stays wired up (catalogue value, ``372k`` alias,
95%-recovery of a 353_400 published window) and is still exercised below; only
the live per-model resolution reflects the rollback. Flip the switch back and
these 272K expectations for the 5.6 tiers revert to 372K.
"""

import pytest

import twicc.synced_settings as ss
from twicc.providers.codex.helpers import CodexHelpers
from twicc.providers.helpers import AgentSettings


@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    """Isolate synced settings so the default-model fallback is deterministic."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


@pytest.fixture
def helpers():
    return CodexHelpers()


# ─── selected_model_context_window ──────────────────────────────────────────

@pytest.mark.parametrize(
    ("selected_model", "expected"),
    [
        ("gpt-astra", 272_000),
        # GPT-5.6 tiers temporarily rolled back to 272K (see module docstring).
        ("gpt-sol", 272_000),
        ("gpt-terra", 272_000),
        ("gpt-luna", 272_000),
        ("gpt", 272_000),        # latest of family gpt = gpt-5.5
        ("gpt-5.4", 272_000),
        ("gpt-mini", 272_000),
    ],
)
def test_selected_model_context_window(helpers, selected_model, expected):
    assert helpers.selected_model_context_window(selected_model) == expected


def test_astra_alias_resolves_to_the_codex_model(helpers):
    assert helpers.resolve_sdk_model("gpt-astra") == "gpt-6-astra"
    constraints = helpers.get_agent_settings_constraints()["effort"]
    assert "gpt-astra" in constraints["max"]
    assert "gpt-astra" not in constraints["ultra"]


def test_context_window_unknown_model_falls_back_to_default(helpers, temp_settings):
    # Synced default model is gpt-terra (SYNCED_SETTINGS_DEFAULTS); its window is
    # temporarily 272K while the GPT-5.6 rollback switch is on.
    assert helpers.selected_model_context_window("no-such-model") == 272_000
    assert helpers.selected_model_context_window(None) == 272_000


# ─── enforce_agent_settings_consistency ─────────────────────────────────────

def test_enforce_pins_window_down_for_56_model_during_rollback(helpers):
    # While the GPT-5.6 window is rolled back to 272K, a stale 372K value on a
    # 5.6 tier is pinned back down to the temporary window.
    s = AgentSettings(selected_model="gpt-terra", context_max=372_000)
    assert helpers.enforce_agent_settings_consistency(s).context_max == 272_000


def test_enforce_pins_window_down_for_pre56_model(helpers):
    # Bidirectional, unlike Claude's 1M cap: a 372K value on a pre-5.6 model
    # is replaced too.
    s = AgentSettings(selected_model="gpt", context_max=372_000)
    assert helpers.enforce_agent_settings_consistency(s).context_max == 272_000


def test_enforce_leaves_none_context_max_untouched(helpers):
    s = AgentSettings(selected_model="gpt-terra", context_max=None)
    assert helpers.enforce_agent_settings_consistency(s).context_max is None


def test_enforce_matching_window_returns_same_instance(helpers):
    # gpt-terra resolves to 272K during the rollback, so a matching value is a no-op.
    s = AgentSettings(selected_model="gpt-terra", context_max=272_000)
    assert helpers.enforce_agent_settings_consistency(s) is s


def test_enforce_combines_effort_demotion_and_window_pin(helpers):
    s = AgentSettings(selected_model="gpt", effort="ultra", context_max=372_000)
    adjusted = helpers.enforce_agent_settings_consistency(s)
    assert adjusted.effort == "xhigh"
    assert adjusted.context_max == 272_000


# ─── enforce_synced_settings_consistency ────────────────────────────────────

def test_synced_context_reclamped_against_default_model(helpers, temp_settings):
    synced = {"codexDefaultModel": "gpt", "codexDefaultContextMax": 372_000}
    helpers.enforce_synced_settings_consistency(synced, dict(synced))
    assert synced["codexDefaultContextMax"] == 272_000


def test_synced_context_not_written_back_when_absent_from_changes(helpers, temp_settings):
    # Base contract: never mutate a key the client didn't send.
    synced = {"codexDefaultModel": "gpt", "codexDefaultContextMax": 372_000}
    helpers.enforce_synced_settings_consistency(synced, {"codexDefaultModel": "gpt"})
    assert synced["codexDefaultContextMax"] == 372_000


# ─── choices / constraints catalogue ────────────────────────────────────────

def test_context_max_choices_derived_from_live_windows(helpers):
    # Derived from the registry: during the GPT-5.6 rollback every model is at
    # 272K, so 372K drops out of the catalogue entirely (it returns on revert).
    assert helpers.get_agent_settings_choices()["context_max"] == [272_000]


def test_constraints_map_each_window_to_its_models(helpers):
    # During the GPT-5.6 rollback every model resolves to 272K and 372K is no
    # longer a catalogue value, so only the 272K bucket is present.
    constraints = helpers.get_agent_settings_constraints()["context_max"]
    assert set(constraints[272_000]) == {
        "gpt-astra-6", "gpt-astra",
        "gpt-5.5", "gpt", "gpt-5.4", "gpt-mini-5.4", "gpt-mini",
        "gpt-sol-5.6", "gpt-sol", "gpt-terra-5.6", "gpt-terra", "gpt-luna-5.6", "gpt-luna",
    }
    assert 372_000 not in constraints


# ─── extract_runtime_fields (task_started window recovery) ──────────────────

@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (258_400, 272_000),  # pre-5.6: 272K x 0.95
        (353_400, 372_000),  # GPT-5.6 tiers: 372K x 0.95
    ],
)
def test_runtime_window_recovered_from_task_started(published, expected):
    from twicc.providers.codex.compute import CodexSessionCompute

    line = {
        "type": "event_msg",
        "payload": {"type": "task_started", "model_context_window": published},
    }
    assert CodexSessionCompute().extract_runtime_fields(line)["context_max"] == expected


# ─── CLI alias resolution (--context-max) ───────────────────────────────────

class _FakeBootstrap:
    """Duck-typed ProviderBootstrap built from the live Codex catalogue."""

    def __init__(self, helpers):
        self.agent_settings_categories = {
            k.value if hasattr(k, "value") else k: v
            for k, v in helpers.AGENT_SETTINGS_CATEGORIES.items()
        }
        self.agent_settings_choices = helpers.get_agent_settings_choices()
        self.agent_settings_aliases = helpers.AGENT_SETTINGS_ALIASES
        self.model_registry = helpers.serialize_model_registry()
        self.untrusted_permission_modes = list(helpers.UNTRUSTED_PERMISSION_MODES)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("min", 272_000),
        ("max", 372_000),
        ("272k", 272_000),
        ("372k", 372_000),
    ],
)
def test_cli_context_max_alias_resolution(helpers, raw, expected):
    from twicc.cli._drop_request.aliases import resolve_overrides

    resolved, errors = resolve_overrides({"context_max": raw}, _FakeBootstrap(helpers))
    assert errors == []
    assert resolved["context_max"] == expected
