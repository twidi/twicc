"""Django-free constants for the Claude Code provider.

Holds the pure data tables (settings defaults, field-to-key mapping,
settings categories, model version registry) so they can be imported by
lightweight callers — such as ``twicc create-session`` for its ``--help``
enrichment — without triggering ``django.setup()`` via the sibling
``helpers.py``.

The :class:`ClaudeCodeHelpers` class re-exposes these as ``ClassVar`` for
backward compatibility with existing ``self.SYNCED_SETTINGS_DEFAULTS`` /
``self.AGENT_SETTINGS_FIELDS_MAPPING`` / ``self.MODEL_VERSIONS`` access
patterns.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion, assert_unique_weights


# Filesystem source for Claude Code plan files. The CLI writes one markdown
# plan per session here, named after the session slug (``<slug>.md``). Watched
# by the plans watcher; its presence drives the session view's *Plan* tab.
# Single source of truth shared by the helper (path resolution) and the watcher.
PLANS_DIR = Path.home() / ".claude" / "plans"


class ClaudeCodeModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Claude Code."""
    supports_1m: bool
    supports_effort_xhigh: bool
    supports_effort_max: bool
    supports_fast: bool
    supports_permission_auto: bool
    # Native vision resolution: True ⇒ ship images at MAX_IMAGE_DIMENSION
    # (2576 px); False ⇒ downscale client-side to 1568 px. Replaces the old
    # ``_is_opus_47_plus`` family/version hardcode.
    supports_highres_images: bool
    # Whether the model allows turning thinking off. False ⇒ adaptive
    # thinking is always on and ``thinking:{type:disabled}`` is rejected by
    # the API (Fable 5), so the ``thinking_enabled=False`` choice is
    # constrained out and the toggle is forced on.
    supports_thinking_disabled: bool


# ⚠ When ADDING a key here, check whether the settings CLI needs it too:
# `twicc settings provider <p>` only surfaces keys it explicitly handles —
# agent-defaults flow through ``AGENT_SETTINGS_FIELDS_MAPPING`` automatically,
# but anything else (usage files, ``QuotaWakeupTime``, …) needs an explicit flag
# in ``twicc/cli/settings/provider.py`` (``build_provider_patch``) AND a line in
# ``build_provider_show`` to appear in the bare read. Mirror the change in the
# Codex constants.
SYNCED_SETTINGS_DEFAULTS: dict = {
    "claudeCodeDefaultPermissionMode": "default",
    "claudeCodeDefaultUntrustedPermissionMode": "default",
    "claudeCodeDefaultModel": "opus",
    "claudeCodeDefaultEffort": "medium",
    "claudeCodeDefaultThinking": True,
    "claudeCodeDefaultClaudeInChrome": True,
    "claudeCodeDefaultFastMode": False,
    "claudeCodeDefaultContextMax": 200_000,
    "claudeCodeUsageReadFileEnabled": False,
    "claudeCodeUsageReadFilePath": "",
    "claudeCodeUsageDumpFileEnabled": False,
    "claudeCodeUsageDumpFilePath": "",
    # Daily quota warm-up time as "HH:MM" in the server's local wall clock,
    # empty = disabled. See twicc.quota_wakeup_task.
    "claudeCodeQuotaWakeupTime": "",
}


RENAMED_SYNCED_SETTINGS_KEYS: dict[str, str] = {
    "defaultPermissionMode": "claudeCodeDefaultPermissionMode",
    "defaultModel": "claudeCodeDefaultModel",
    "defaultEffort": "claudeCodeDefaultEffort",
    "defaultThinking": "claudeCodeDefaultThinking",
    "defaultClaudeInChrome": "claudeCodeDefaultClaudeInChrome",
    "defaultContextMax": "claudeCodeDefaultContextMax",
    "usageJsonFileEnabled": "claudeCodeUsageReadFileEnabled",
    "usageJsonFilePath": "claudeCodeUsageReadFilePath",
    "usageDumpFileEnabled": "claudeCodeUsageDumpFileEnabled",
    "usageDumpFilePath": "claudeCodeUsageDumpFilePath",
}


OBSOLETE_SYNCED_SETTINGS_KEYS: tuple[str, ...] = (
    "alwaysApplyDefaultPermissionMode",
    "alwaysApplyDefaultModel",
    "alwaysApplyDefaultEffort",
    "alwaysApplyDefaultThinking",
    "alwaysApplyDefaultClaudeInChrome",
    "alwaysApplyDefaultContextMax",
)


AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: ["permission_mode"],
    AgentSettingCategory.IDLE: ["selected_model", "context_max"],
    AgentSettingCategory.STARTUP: [
        "effort",
        "thinking_enabled",
        "claude_in_chrome",
        "fast_mode",
        "question_widget",
    ],
}


# Hybrid CLI mode classification. Differs from the SDK's: the TUI has no
# reliable way to set permission_mode externally (Shift+Tab cycling is
# stateful), so it becomes STARTUP. Model, effort and fast mode are IDLE
# via pasted commands — /model <name>, /effort <level> and /fast on|off
# all apply instantly in their ARGUMENT form (verified on 2.1.172,
# 2026-06-11). The bare forms open interactive dialogs and must never be
# pasted. thinking COULD become live later via the Tab toggle — kept
# STARTUP in V1 (design doc §2.5/§5.3).
HYBRID_AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: [],
    AgentSettingCategory.IDLE: ["selected_model", "context_max", "effort", "fast_mode"],
    AgentSettingCategory.STARTUP: [
        "permission_mode",
        "thinking_enabled",
        "claude_in_chrome",
        "question_widget",
    ],
}


# Permission modes a session may use in an UNTRUSTED (or unknown-trust) project:
# every mode that keeps at least one structural guardrail (permission prompt,
# read-only, auto-deny, or the CLI's safety checks for ``auto``). Only
# ``bypassPermissions`` — no guardrail at all — is excluded. The untrusted
# default seeds sessions created in untrusted projects and is the fallback the
# backend trust clamp applies to out-of-set values.
# See docs/plans/2026-06-09-project-trust-design.md §13.2.
UNTRUSTED_PERMISSION_MODES: frozenset[str] = frozenset({"default", "auto", "acceptEdits", "plan", "dontAsk"})
UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: str = "claudeCodeDefaultUntrustedPermissionMode"


AGENT_SETTINGS_FIELDS_MAPPING: dict[str, str] = {
    "permission_mode": "claudeCodeDefaultPermissionMode",
    "selected_model": "claudeCodeDefaultModel",
    "effort": "claudeCodeDefaultEffort",
    "thinking_enabled": "claudeCodeDefaultThinking",
    "claude_in_chrome": "claudeCodeDefaultClaudeInChrome",
    "fast_mode": "claudeCodeDefaultFastMode",
    "context_max": "claudeCodeDefaultContextMax",
}


# Per-(field, value) human-readable description. Mirrors the
# ``description`` field of ``AGENT_SETTINGS_CHOICES`` in
# ``frontend/src/providers/claude_code/helpers.js`` — keep in sync.
# Surfaced by ``twicc info agent-settings``; absent values are silently
# omitted from the output.
AGENT_SETTINGS_DESCRIPTIONS: dict[str, dict] = {
    "permission_mode": {
        "default": "Prompts for permission on first use of each tool",
        "auto": "Auto-approves tools, with safety checks blocking risky actions",
        "acceptEdits": "Auto-accepts file edit permissions",
        "plan": "Read-only: Claude can analyze but not modify files",
        "dontAsk": "Auto-denies tools unless pre-approved via permission rules",
        "bypassPermissions": "Skips all permission prompts",
    },
    "fast_mode": {
        True: "Faster generation, billed on extra usage credits. Tokens cost 6x more (only 2x since 4.8).",
    },
}


# Aliases the CLI / skills accept in place of a concrete agent-settings
# value, resolved against this provider before the request leaves the client.
# Shape ``{field: {alias: concrete_value}}``. Only the ordered fields take
# aliases; the booleans (thinking_enabled, fast_mode, claude_in_chrome,
# question_widget) are passed through literally. Resolution is native-first: a
# value Claude Code already accepts verbatim (e.g. effort "max") is never
# reinterpreted as an alias, so an alias only fires when it is NOT a native
# value. ``context_max`` targets are token-count strings so they flow through
# ``parse_context_max`` exactly like a literal ``--context-max 1m``. ``auto``
# needs no entry — it is already a native permission_mode for this provider.
AGENT_SETTINGS_ALIASES: dict[str, dict[str, str]] = {
    "selected_model": {
        "min": "sonnet", "fastest": "sonnet", "cheapest": "sonnet",
        "medium": "opus", "balanced": "opus",
        "max": "fable", "strongest": "fable",
    },
    "effort": {
        "min": "low", "max": "max",
    },
    "context_max": {
        "min": "200k", "max": "1m",
    },
    "permission_mode": {
        "min": "dontAsk", "strict": "dontAsk", "safe": "dontAsk",
        "max": "bypassPermissions", "full": "bypassPermissions",
        "open": "bypassPermissions", "yolo": "bypassPermissions",
        "bypass": "bypassPermissions",
    },
    # Untrusted projects use a restricted permission-mode set (``bypassPermissions``
    # removed — see ``UNTRUSTED_PERMISSION_MODES``). These aliases resolve only to
    # values inside that set, so ``min``/``safe``/``max`` stay meaningful when a
    # session is created in an untrusted project. ``max`` is the most permissive
    # mode still allowed there: ``acceptEdits`` (auto-accepts edits, always
    # available) — not ``auto``, which is model-gated and would not always resolve.
    "permission_mode_if_untrusted": {
        "min": "dontAsk", "safe": "dontAsk",
        "max": "acceptEdits",
    },
}


# ------------------------------------------------------------------
# Model registry — supported model versions
#
# The ``selected_model`` value stored in settings and session DB
# fields uses:
# - bare alias for latest: ``"fable"``, ``"opus"``, ``"sonnet"``
# - versioned alias for non-latest: ``"opus-4.5"``, ``"sonnet-4.5"``
#
# When communicating with the SDK, latest aliases are passed as-is
# (the CLI resolves them), while versioned aliases are resolved to
# their ``full_name``.
#
# Deprecations: https://platform.claude.com/docs/en/about-claude/model-deprecations
# ------------------------------------------------------------------

MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="fable", version="5.1", full_name="claude-fable-5-1",
        retirement_date=None,
        latest=True,
        weight=210,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=False, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=False,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="fable", version="5", full_name="claude-fable-5",
        retirement_date=None,
        latest=False,
        weight=200,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=False, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=False,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="5", full_name="claude-opus-5",
        retirement_date=None,
        latest=True,
        weight=100,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=True, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.8", full_name="claude-opus-4-8",
        retirement_date=None,
        latest=False,
        weight=90,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=True, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.7", full_name="claude-opus-4-7",
        retirement_date=date(2027, 4, 16),
        latest=False,
        weight=80,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=True, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.6", full_name="claude-opus-4-6",
        retirement_date=date(2027, 2, 5),
        latest=False,
        weight=70,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            supports_fast=True, supports_permission_auto=True,
            supports_highres_images=False, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="opus", version="4.5", full_name="claude-opus-4-5-20251101",
        retirement_date=date(2026, 11, 24),
        latest=False,
        weight=60,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            supports_fast=False, supports_permission_auto=False,
            supports_highres_images=False, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="sonnet", version="5", full_name="claude-sonnet-5",
        retirement_date=None,
        latest=True,
        weight=30,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=True, supports_effort_max=True,
            supports_fast=False, supports_permission_auto=True,
            supports_highres_images=True, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="sonnet", version="4.6", full_name="claude-sonnet-4-6",
        retirement_date=date(2027, 2, 17),
        latest=False,
        weight=20,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=True, supports_effort_xhigh=False, supports_effort_max=True,
            supports_fast=False, supports_permission_auto=True,
            supports_highres_images=False, supports_thinking_disabled=True,
        ),
    ),
    ModelVersion(
        provider=Provider.CLAUDE_CODE,
        model="sonnet", version="4.5", full_name="claude-sonnet-4-5-20250929",
        retirement_date=date(2026, 9, 29),
        latest=False,
        weight=10,
        provider_extra=ClaudeCodeModelExtra(
            supports_1m=False, supports_effort_xhigh=False, supports_effort_max=False,
            supports_fast=False, supports_permission_auto=False,
            supports_highres_images=False, supports_thinking_disabled=True,
        ),
    ),
]

assert_unique_weights(MODEL_VERSIONS)
