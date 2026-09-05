"""Django-free constants for the Codex provider.

Mirrors :mod:`twicc.providers.claude_code.constants`. See that module for
the motivation (Django-free re-use by lightweight callers like
``twicc create-session --help``).
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettingCategory, ModelVersion, assert_unique_weights


# ⚠ When ADDING a key here, check whether the settings CLI needs it too:
# `twicc settings provider <p>` only surfaces keys it explicitly handles —
# agent-defaults flow through ``AGENT_SETTINGS_FIELDS_MAPPING`` automatically,
# but anything else (usage files, ``QuotaWakeupTime``, …) needs an explicit flag
# in ``twicc/cli/settings/provider.py`` (``build_provider_patch``) AND a line in
# ``build_provider_show`` to appear in the bare read. Mirror the change in the
# Claude Code constants.
SYNCED_SETTINGS_DEFAULTS: dict = {
    "codexDefaultModel": "gpt-terra",
    "codexDefaultEffort": "medium",
    "codexDefaultPermissionMode": "read_only",
    "codexDefaultUntrustedPermissionMode": "read_only",
    "codexDefaultFastMode": False,
    # Matches ``codexDefaultModel``'s window (gpt-terra). Mostly inert: the
    # window is a per-model property and ``enforce_agent_settings_consistency``
    # re-pins it against whichever model actually runs. Temporarily 272K while
    # GPT_56_CONTEXT_WINDOW_TEMPORARILY_REDUCED is on (gpt-terra rolled back to
    # 272K); restore to 372_000 alongside that switch.
    "codexDefaultContextMax": 272_000,
    "codexUsageReadFileEnabled": False,
    "codexUsageReadFilePath": "",
    "codexUsageDumpFileEnabled": False,
    "codexUsageDumpFilePath": "",
    # Daily quota warm-up time as "HH:MM" in the server's local wall clock,
    # empty = disabled. See twicc.quota_wakeup_task.
    "codexQuotaWakeupTime": "",
}


# ``question_widget`` is STARTUP because it rides the per-thread ``config``
# patch (``features.default_mode_request_user_input`` +
# ``tools.experimental_request_user_input``), which Codex reads at
# ``thread_start`` / ``thread_resume`` only — ``thread/settings/update`` carries
# sandbox / collaboration mode / service tier and nothing else. Unlike Claude
# Code, the Codex manager runs no STARTUP reconciliation: a change on a live
# session is persisted but takes effect only once the process starts again
# (stop the session, then send a message to resume it).
AGENT_SETTINGS_CATEGORIES: dict[AgentSettingCategory, list[str]] = {
    AgentSettingCategory.LIVE: [],
    AgentSettingCategory.IDLE: [
        "selected_model",
        "effort",
        "permission_mode",
        "context_max",
        "fast_mode",
    ],
    AgentSettingCategory.STARTUP: ["question_widget"],
}


# Permission modes a session may use in an UNTRUSTED (or unknown-trust) project:
# every mode that keeps at least one structural guardrail (permission prompt,
# read-only, workspace-write sandbox, or automatic approval review). Only
# ``yolo`` — no guardrail at all — is excluded. The untrusted default seeds
# sessions created in untrusted projects and is the fallback the backend trust
# clamp applies to out-of-set values.
# See docs/plans/2026-06-09-project-trust-design.md §13.2.
UNTRUSTED_PERMISSION_MODES: frozenset[str] = frozenset({
    "read_only", "strict", "auto", "autonomous", "auto_review",
})
UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: str = "codexDefaultUntrustedPermissionMode"


AGENT_SETTINGS_FIELDS_MAPPING: dict[str, str] = {
    "selected_model": "codexDefaultModel",
    "effort": "codexDefaultEffort",
    "permission_mode": "codexDefaultPermissionMode",
    "context_max": "codexDefaultContextMax",
    "fast_mode": "codexDefaultFastMode",
}


# Per-(field, value) human-readable description. Mirrors the
# ``description`` field of ``AGENT_SETTINGS_CHOICES`` in
# ``frontend/src/providers/codex/helpers.js`` — keep in sync. Surfaced
# by ``twicc info agent-settings``; absent values are silently omitted
# from the output.
AGENT_SETTINGS_DESCRIPTIONS: dict[str, dict] = {
    "permission_mode": {
        "read_only": "Read-only. Any write requires confirmation.",
        "strict": "Read-only. Writes are refused silently (no prompt).",
        "auto": "Writes freely in the project; asks to step outside.",
        "autonomous": "Writes in the project; rejects requests to step outside.",
        "auto_review": "Writes in the project; automatically reviews requests to step outside.",
        "yolo": "No restrictions.",
    },
    "fast_mode": {
        True: "Faster generation — 2x on GPT-6 Astra, 2.5x on GPT-5.6, 1.5x before; uses credits at 2.5x.",
    },
}


# Aliases the CLI / skills accept in place of a concrete agent-settings
# value, resolved against Codex before the request leaves the client. See the
# matching table in ``twicc.providers.claude_code.constants`` for the full
# rationale (native-first resolution, token-count strings for ``context_max``).
# ``strict``, ``yolo`` and ``auto`` need no entry — they are already native
# Codex permission modes, so native-first keeps them as-is.
AGENT_SETTINGS_ALIASES: dict[str, dict[str, str]] = {
    # ``min``/``fastest``/``cheapest`` point at Luna, not at the 5.4 mini: the
    # 5.6 price cut took Luna below it ($0.20/$1.20 per Mtok against
    # $0.75/$4.50), so Luna is now the cheapest model of the catalogue outright
    # — and the mini retires on 2026-08-31 anyway.
    "selected_model": {
        "min": "gpt-luna", "fastest": "gpt-luna", "cheapest": "gpt-luna",
        "medium": "gpt-terra", "balanced": "gpt-terra",
        "max": "gpt-astra", "strongest": "gpt-astra",
    },
    # ``max`` is a native effort since GPT-5.6, so native-first keeps it as-is
    # and this entry is a no-op kept for symmetry (same shape as Claude Code).
    # ``ultra`` sits above it but is reached only by naming it explicitly.
    "effort": {
        "min": "low", "max": "max",
    },
    "context_max": {
        "min": "272k", "max": "372k",
    },
    "permission_mode": {
        "min": "strict", "safe": "strict",
        "max": "yolo", "full": "yolo", "open": "yolo", "bypass": "yolo",
    },
    # Untrusted projects use a restricted set (``yolo`` removed — see
    # ``UNTRUSTED_PERMISSION_MODES``). These aliases resolve only to values
    # inside that set, so ``min``/``safe``/``max`` stay meaningful when a session
    # is created in an untrusted project. ``max`` is the most permissive mode
    # still allowed there: ``auto_review`` (workspace sandbox + automatic reviewer).
    "permission_mode_if_untrusted": {
        "min": "strict", "safe": "strict",
        "max": "auto_review",
    },
}


class CodexModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Codex.

    GPT-5.6 added two reasoning-effort levels above ``xhigh``: ``max`` (deepest
    single-agent reasoning) and ``ultra`` (subagent parallelisation). They are
    NOT uniform across the family, and not Sol-only as the launch coverage
    claimed — the CLI is the source of truth and reports the per-model set in
    ``model/list`` under ``supportedReasoningEfforts``. Astra, Sol, and Terra
    expose both, Luna exposes ``max`` only, and every pre-5.6 model exposes
    neither. Mirrors ``claude_code.constants.ClaudeCodeModelExtra``.

    ``supports_fast`` mirrors the model catalog's ``serviceTiers`` list: the
    six frontier models expose the ``priority`` tier, while GPT-5.4 mini does
    not. Keeping it in the registry lets every settings surface use the same
    model gate without guessing from a model name.

    ``context_window`` is the model's nominal INPUT window when run inside
    Codex — a fixed property of the model, not a user choice (unlike Claude's
    1M opt-in). It is well below the API-advertised window: Codex reserves
    128K for output and publishes 95% of the input part in
    ``task_started.model_context_window`` (see ``compute.py``'s
    ``_TASK_STARTED_WINDOW_HEADROOM_FACTOR``). Empirically: 272K for the
    Astra and the pre-5.6 models (400K total = 272K input + 128K output,
    published as 258_400) and 372K for the GPT-5.6 tiers (published as 353_400).
    ``enforce_agent_settings_consistency`` pins ``context_max`` to this value,
    so the stored/displayed window always matches what Codex actually runs.
    """
    supports_effort_max: bool
    supports_effort_ultra: bool
    supports_fast: bool
    context_window: int


# Temporary product-wide kill switch for the ``ultra`` reasoning effort
# (2026-07-14). Astra, Sol, and Terra natively expose ``ultra`` (see
# ``CodexModelExtra`` and the CLI ``model/list``) and their entries below keep
# ``supports_effort_ultra=True`` as the real, documented capability. While this
# is ``True``, the post-processing step just after ``MODEL_VERSIONS`` forces the
# flag off across the whole registry, so no model offers ``ultra`` and any
# stored ``ultra`` effort demotes to ``max``/``xhigh`` via
# ``enforce_agent_settings_consistency``. Re-enable ``ultra`` by setting this
# back to ``False`` (also un-comment the ``Ultra`` effort row in the frontend
# ``codex/helpers.js`` and restore the skill docs).
ULTRA_EFFORT_TEMPORARILY_DISABLED = True


# Temporary rollback of the GPT-5.6 Codex input window (2026-07-21). OpenAI cut
# the 5.6 tiers back down to the pre-5.6 272K, matching every older model, so the
# larger 372K window is temporarily gone. The 5.6 entries below keep their real
# ``context_window=372_000`` (the whole 372K plumbing — catalogue value, aliases,
# frontend option, pinning — stays in place); while this switch is on, the
# post-processing step just after ``MODEL_VERSIONS`` overrides only the 5.6 tiers'
# window down to 272K, so ``enforce_agent_settings_consistency`` pins their
# ``context_max`` at 272K and the 372K option offers no model. The catalogue and
# both frontend/backend pickers are derived from the live windows, so they follow
# automatically; only the prose docs are manual. Re-enable the 372K window when
# OpenAI restores it by setting this back to ``False`` AND restoring the ``372k``
# mentions in the ``twicc-create-session`` / ``twicc-update-session`` skill docs,
# the root ``SKILLS-AND-CLI.md`` ``--context-max`` lines, and the ``context_max``
# help text in the frontend ``codex/helpers.js``.
GPT_56_CONTEXT_WINDOW_TEMPORARILY_REDUCED = True


# Codex CLI models the bundled binary accepts, cross-checked against the CLI's
# own ``model/list`` response. ``selected_model_value`` returns the bare alias
# for ``latest=True`` entries (``"gpt"``, ``"gpt-sol"``, ``"gpt-mini"``) and the
# versioned alias for the rest (``"gpt-5.4"``), matching the Claude Code
# convention of bare-alias-for-latest / versioned-alias.
#
# With GPT-5.6 the name denotes a durable capability tier (Sol/Terra/Luna)
# rather than a size suffix, so each tier is its own family here. That family is
# also the pricing-equivalence key ``extract_model_info`` derives from the
# ``full_name`` (``gpt-5.6-sol`` → family ``gpt-sol``), which keeps the registry
# and the price table in agreement without a second mapping.
#
# ``weight`` is laid out by *tier block*, not by generation — the same shape as
# Claude Code, where Sonnet 5 sits below Opus 4.5. The 5.6 tiers each open a
# block and the pre-5.6 models fall in behind the tier they belong to. That
# placement is what makes the nearest-by-weight fallback land on the right
# successor when a model retires, with no explicit successor mapping:
# ``gpt-5.4-mini`` → ``gpt-luna``, matching OpenAI's own migration guidance.
# ``gpt-5.5`` sits between Terra and ``gpt-5.4``: a retiring ``gpt-5.4`` lands
# on it rather than on Terra, which is the intended behaviour — someone still
# on 5.4 declined the newer generations, so the substitution moves them by the
# smallest possible step.
MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-astra",
        version="6",
        full_name="gpt-6-astra",
        retirement_date=None,
        latest=True,
        weight=300,
        provider_extra=CodexModelExtra(
            supports_effort_max=True,
            supports_effort_ultra=True,
            supports_fast=True,
            context_window=272_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-sol",
        version="5.6",
        full_name="gpt-5.6-sol",
        retirement_date=None,
        latest=True,
        weight=200,
        provider_extra=CodexModelExtra(
            supports_effort_max=True,
            supports_effort_ultra=True,
            supports_fast=True,
            context_window=372_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-terra",
        version="5.6",
        full_name="gpt-5.6-terra",
        retirement_date=None,
        latest=True,
        weight=120,
        provider_extra=CodexModelExtra(
            supports_effort_max=True,
            supports_effort_ultra=True,
            supports_fast=True,
            context_window=372_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt",
        version="5.5",
        full_name="gpt-5.5",
        retirement_date=None,
        latest=True,
        weight=110,
        provider_extra=CodexModelExtra(
            supports_effort_max=False,
            supports_effort_ultra=False,
            supports_fast=True,
            context_window=272_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt",
        version="5.4",
        full_name="gpt-5.4",
        # Retires from Codex with ChatGPT sign-in on 2026-08-31 (the OpenAI API
        # and Codex authenticated with an API key are unaffected). Announced
        # replacement is gpt-5.6-terra; see the weight comment above for why we
        # let the fallback land on gpt-5.5 instead.
        retirement_date=date(2026, 8, 31),
        latest=False,
        weight=100,
        provider_extra=CodexModelExtra(
            supports_effort_max=False,
            supports_effort_ultra=False,
            supports_fast=True,
            context_window=272_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-luna",
        version="5.6",
        full_name="gpt-5.6-luna",
        retirement_date=None,
        latest=True,
        weight=30,
        provider_extra=CodexModelExtra(
            supports_effort_max=True,
            supports_effort_ultra=False,
            supports_fast=True,
            context_window=372_000,
        ),
    ),
    ModelVersion(
        provider=Provider.CODEX,
        model="gpt-mini",
        version="5.4",
        full_name="gpt-5.4-mini",
        # Retires alongside gpt-5.4 on 2026-08-31, same ChatGPT-sign-in scope.
        # The weight puts it right under gpt-5.6-luna, so the fallback lands on
        # Luna — OpenAI's announced replacement, and now the cheapest model of
        # the catalogue.
        retirement_date=date(2026, 8, 31),
        latest=True,
        weight=20,
        provider_extra=CodexModelExtra(
            supports_effort_max=False,
            supports_effort_ultra=False,
            supports_fast=False,
            context_window=272_000,
        ),
    ),
]

# See ``ULTRA_EFFORT_TEMPORARILY_DISABLED`` above: while the switch is on, strip
# ``ultra`` support from every entry so it is unreachable product-wide, keeping
# the literal per-model capability flags intact for a one-line revert.
if ULTRA_EFFORT_TEMPORARILY_DISABLED:
    MODEL_VERSIONS = [
        mv._replace(provider_extra=mv.provider_extra._replace(supports_effort_ultra=False))
        if mv.provider_extra is not None
        else mv
        for mv in MODEL_VERSIONS
    ]

# See ``GPT_56_CONTEXT_WINDOW_TEMPORARILY_REDUCED`` above: while the switch is on,
# clamp every GPT-5.6 tier's window back down to 272K so it matches the temporary
# OpenAI rollback, keeping the literal ``context_window=372_000`` in the entries
# for a one-line revert.
if GPT_56_CONTEXT_WINDOW_TEMPORARILY_REDUCED:
    MODEL_VERSIONS = [
        mv._replace(provider_extra=mv.provider_extra._replace(context_window=272_000))
        if mv.provider_extra is not None and mv.version == "5.6"
        else mv
        for mv in MODEL_VERSIONS
    ]

assert_unique_weights(MODEL_VERSIONS)
