"""Django-free constants for the Gemini CLI provider.

Mirrors :mod:`twicc.providers.codex.constants`: everything here must stay
importable without Django so lightweight callers (``twicc create-session
--help``) can read the catalogue and the defaults.
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion, assert_unique_weights


# ⚠ When ADDING a key here, check whether the settings CLI needs it too (see
# the matching note in ``twicc.providers.codex.constants``).
SYNCED_SETTINGS_DEFAULTS: dict = {
    "geminiDefaultModel": "gemini-flash",
    "geminiDefaultEffort": "medium",
    "geminiDefaultPermissionMode": "default",
    "geminiDefaultUntrustedPermissionMode": "default",
}


# Every Gemini setting is applied between turns over ACP: ``session/set_model``
# and ``session/set_mode`` act on the live process, so nothing needs a restart.
# ``effort`` has a single fixed value (the CLI exposes no reasoning-effort
# control over ACP); it is listed so the shared model matrix, whose columns are
# efforts, has a clickable cell for every Gemini model.
AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: [],
    AgentSettingCategory.IDLE: [
        "selected_model",
        "effort",
        "permission_mode",
    ],
    AgentSettingCategory.STARTUP: [],
}


# The Gemini CLI approval modes (``--approval-mode`` / ACP ``session/set_mode``).
# TwiCC stores the CLI spelling; ``ACP_MODE_IDS`` maps it to the ACP mode id.
PERMISSION_MODES: tuple[str, ...] = ("default", "auto_edit", "plan", "yolo")
ACP_MODE_IDS: dict[str, str] = {
    "default": "default",
    "auto_edit": "autoEdit",
    "plan": "plan",
    "yolo": "yolo",
}

# The single effort level (see ``AGENT_SETTINGS_CATEGORIES``).
EFFORTS: tuple[str, ...] = ("medium",)

# Every mode that keeps a guardrail. ``yolo`` approves everything, so it is
# refused in untrusted projects (docs/archives/plans/2026-06-09-project-trust-design.md).
UNTRUSTED_PERMISSION_MODES: frozenset[str] = frozenset({"default", "auto_edit", "plan"})
UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: str = "geminiDefaultUntrustedPermissionMode"

# Modes that never stop to ask the user (required by hidden sessions).
NON_INTERACTIVE_PERMISSION_MODES: frozenset[str] = frozenset({"yolo"})


AGENT_SETTINGS_FIELDS_MAPPING: dict[str, str] = {
    "selected_model": "geminiDefaultModel",
    "effort": "geminiDefaultEffort",
    "permission_mode": "geminiDefaultPermissionMode",
}


# Mirrors the ``description`` of the permission choices in
# ``frontend/src/providers/gemini/helpers.js``. Surfaced by ``twicc info
# agent-settings``.
AGENT_SETTINGS_DESCRIPTIONS: dict[str, dict] = {
    "permission_mode": {
        "default": "Asks before file edits, shell commands and other side effects.",
        "auto_edit": "Approves file edits automatically; asks for the rest.",
        "plan": "Read-only: plans without modifying anything.",
        "yolo": "Approves every tool call automatically.",
    },
    "effort": {
        "medium": "Gemini CLI default reasoning (no effort control).",
    },
}


# Aliases the CLI / skills accept in place of a concrete value (see the
# rationale in ``twicc.providers.claude_code.constants``).
AGENT_SETTINGS_ALIASES: dict[str, dict[str, str]] = {
    "selected_model": {
        "min": "gemini-flash-lite", "fastest": "gemini-flash-lite", "cheapest": "gemini-flash-lite",
        "medium": "gemini-flash", "balanced": "gemini-flash",
        "max": "gemini-pro", "strongest": "gemini-pro",
    },
    "effort": {
        "min": "medium", "max": "medium",
    },
    "permission_mode": {
        "min": "plan", "safe": "plan", "strict": "plan",
        "auto": "auto_edit",
        "max": "yolo", "full": "yolo", "open": "yolo", "bypass": "yolo",
    },
    "permission_mode_if_untrusted": {
        "min": "plan", "safe": "plan", "strict": "plan",
        "auto": "auto_edit",
        "max": "auto_edit",
    },
}


class GeminiModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Gemini.

    ``context_window`` is informational (the window is a model property the
    CLI does not let the user change).
    """
    context_window: int


# Models offered by Gemini CLI 0.68 (cross-checked against the ``models`` list
# of ACP ``session/new``). ``full_name`` is the id passed to ``-m`` /
# ``session/set_model``; ``model`` is the family alias stored on the session
# (``selected_model`` holds at most 30 characters, so preview suffixes stay in
# ``full_name``). Weights are laid out by tier block, newest version on top, so
# a retired model falls back to its successor (see the Codex catalogue).
MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.GEMINI,
        model="gemini-pro",
        version="3.1",
        full_name="gemini-3.1-pro-preview",
        retirement_date=None,
        latest=True,
        weight=310,
        provider_extra=GeminiModelExtra(context_window=1_048_576),
    ),
    ModelVersion(
        provider=Provider.GEMINI,
        model="gemini-pro",
        version="2.5",
        full_name="gemini-2.5-pro",
        retirement_date=None,
        latest=False,
        weight=250,
        provider_extra=GeminiModelExtra(context_window=1_048_576),
    ),
    ModelVersion(
        provider=Provider.GEMINI,
        model="gemini-flash",
        version="3.8",
        full_name="gemini-3.8-flash",
        retirement_date=None,
        latest=True,
        weight=150,
        provider_extra=GeminiModelExtra(context_window=1_048_576),
    ),
    ModelVersion(
        provider=Provider.GEMINI,
        model="gemini-flash",
        version="3",
        full_name="gemini-3-flash-preview",
        retirement_date=None,
        latest=False,
        weight=140,
        provider_extra=GeminiModelExtra(context_window=1_048_576),
    ),
    ModelVersion(
        provider=Provider.GEMINI,
        model="gemini-flash-lite",
        version="3.5",
        full_name="gemini-3.5-flash-lite",
        retirement_date=None,
        latest=True,
        weight=50,
        provider_extra=GeminiModelExtra(context_window=1_048_576),
    ),
]

assert_unique_weights(MODEL_VERSIONS)
