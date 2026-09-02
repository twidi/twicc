"""
Claude Code implementation of :class:`BaseProviderHelpers`.

Centralizes the per-provider surface that the core consumes through the
provider helpers registry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import orjson
from django.conf import settings

from twicc.core.enums import ItemKind, Provider
from twicc.pricing import FamilyPrices
from twicc.providers.helpers import (
    MAX_IMAGE_DIMENSION,
    AgentSettingCategory,
    AgentSettings,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    StatuspageConfig,
    UserMessage,
)

from .compute import extract_command, get_message_content, get_message_content_list, strip_markdown
from .constants import (
    PLANS_DIR,
    AGENT_SETTINGS_ALIASES as _AGENT_SETTINGS_ALIASES,
    AGENT_SETTINGS_CATEGORIES as _AGENT_SETTINGS_CATEGORIES,
    AGENT_SETTINGS_DESCRIPTIONS as _AGENT_SETTINGS_DESCRIPTIONS,
    AGENT_SETTINGS_FIELDS_MAPPING as _AGENT_SETTINGS_FIELDS_MAPPING,
    MODEL_VERSIONS as _MODEL_VERSIONS,
    OBSOLETE_SYNCED_SETTINGS_KEYS as _OBSOLETE_SYNCED_SETTINGS_KEYS,
    RENAMED_SYNCED_SETTINGS_KEYS as _RENAMED_SYNCED_SETTINGS_KEYS,
    SYNCED_SETTINGS_DEFAULTS as _SYNCED_SETTINGS_DEFAULTS,
    UNTRUSTED_PERMISSION_MODE_SYNCED_KEY as _UNTRUSTED_PERMISSION_MODE_SYNCED_KEY,
    UNTRUSTED_PERMISSION_MODES as _UNTRUSTED_PERMISSION_MODES,
    ClaudeCodeModelExtra,
)
from .pricing import CLAUDE_FAMILIES, extract_model_info
from .titles import protect_title, rename_session_in_jsonl

# Re-export for callers that historically imported these from
# ``providers.claude_code.helpers``. The canonical home is now
# ``providers.claude_code.constants``.
__all__ = ["ClaudeCodeHelpers", "ClaudeCodeModelExtra"]

if TYPE_CHECKING:
    from twicc.core.models import Session, SessionItem

logger = logging.getLogger(__name__)


def _extract_text_from_message_content(content: str | list | None) -> str:
    """Concatenate every text payload from Claude Code's message content shape.

    Claude Code messages carry ``content`` as either a plain string or a
    list of typed parts (``{"type": "text", "text": ...}``). This walks the
    list and joins every text part with newlines, used for full-text search
    indexing where we want every block (unlike ``extract_text_from_content``
    in ``compute.py`` which returns only the first text block). Returns the
    empty string when nothing extractable is found.
    """
    if content is None:
        return ""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        texts: list[str] = []
        for entry in content:
            if isinstance(entry, dict) and entry.get("type") == "text":
                text = entry.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts)

    return ""


AGENT_SETTINGS_CHOICES: dict[str, list] = {
    "effort": ["low", "medium", "high", "xhigh", "max"],
    "permission_mode": ["default", "auto", "acceptEdits", "plan", "dontAsk", "bypassPermissions"],
    "thinking_enabled": [True, False],
    "context_max": [200_000, 1_000_000],
    "claude_in_chrome": [True, False],
    "fast_mode": [True, False],
}


ATTACHMENT_SUPPORT: dict = {
    "images": True,
    "documents": True,
    "accepted_mime_types": [
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "application/pdf", "text/plain",
    ],
    "max_bytes_per_file": 5 * 1024 * 1024,
    "max_files_per_message": 100,
    "max_total_bytes": 32 * 1024 * 1024,
}


@lru_cache(maxsize=32)
def serialize_model(model: str | None) -> dict | None:
    """Serialize a Claude model identifier as ``{raw, family, version}``.

    Returns ``None`` for an empty input. Falls back to ``{raw, None, None}``
    when the format is not recognized.
    """
    if not model:
        return None

    info = extract_model_info(model)
    if not info:
        return {"raw": model, "family": None, "version": None}

    return {
        "raw": model,
        "family": info.family,
        "version": info.version,
    }


_SYSTEM_PROMPT_STATIC_ADDENDUM = """\
## Receiving messages while working

IMPORTANT: a hard requirement, not a suggestion.

If the user messages you mid-turn (between tool calls, during a long operation,
etc.), the SDK folds it in as an interrupt to the ongoing turn, and TwiCC's UI
does NOT show it as a separate user message — the user only sees your reply.

YOU MUST surface the interrupted message yourself: at the very top of your next
reply, before any answer, status, summary, or tool-result discussion, quote it
as a Markdown blockquote (``> ...``), then respond.

This is CRITICAL for conversation integrity: unquoted, the user's text vanishes
from the visible conversation (no transcript entry, no visual record) and your
reply looks ungrounded.

## Workflows

The live progress of a `Workflow` you launch — phases, per-agent status, cost —
shows in a dedicated **Workflows** tab, not in the conversation. Point the user
there with a clickable in-app Markdown link; never tell them to type
`/workflows` (no such command exists in TwiCC):

- a single workflow: `[open](/project/{project_id}/session/{session_id}/workflows/{run_id})`
- every workflow of this session: the same link without the trailing `{run_id}`.

That tab has no resume control, so an interrupted workflow can only be resumed by
you: when the user asks, re-invoke `Workflow` with `resumeFromRunId`.
"""


class ClaudeCodeHelpers(BaseProviderHelpers):
    """Helpers for sessions produced by the Claude Code CLI / SDK."""

    provider: ClassVar[Provider] = Provider.CLAUDE_CODE
    LABEL: ClassVar[str] = "Claude Code"
    SYSTEM_PROMPT_STATIC_ADDENDUM: ClassVar[str] = _SYSTEM_PROMPT_STATIC_ADDENDUM

    # Constants are defined in ``.constants`` (Django-free module) so the
    # CLI's ``--help`` enrichment can read them without ``django.setup()``.
    # Re-exposed as ClassVars to preserve the existing ``self.XXX`` access
    # patterns used throughout the codebase.
    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = _SYNCED_SETTINGS_DEFAULTS
    RENAMED_SYNCED_SETTINGS_KEYS: ClassVar[dict[str, str]] = _RENAMED_SYNCED_SETTINGS_KEYS
    OBSOLETE_SYNCED_SETTINGS_KEYS: ClassVar[tuple[str, ...]] = _OBSOLETE_SYNCED_SETTINGS_KEYS
    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = _AGENT_SETTINGS_CATEGORIES
    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = _AGENT_SETTINGS_FIELDS_MAPPING
    AGENT_SETTINGS_DESCRIPTIONS: ClassVar[dict[str, dict]] = _AGENT_SETTINGS_DESCRIPTIONS
    AGENT_SETTINGS_ALIASES: ClassVar[dict[str, dict[str, str]]] = _AGENT_SETTINGS_ALIASES

    # Claude Code permission modes that do not require interactive approval.
    NON_INTERACTIVE_PERMISSION_MODES: ClassVar[frozenset[str]] = frozenset({"bypassPermissions", "dontAsk"})

    # Permission modes allowed in untrusted projects + the synced key of the
    # untrusted default (see ``.constants`` for the rationale).
    UNTRUSTED_PERMISSION_MODES: ClassVar[frozenset[str]] = _UNTRUSTED_PERMISSION_MODES
    UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: ClassVar[str | None] = _UNTRUSTED_PERMISSION_MODE_SYNCED_KEY

    # Per-(field, value) capability flag in :class:`ClaudeCodeModelExtra`
    # gating the value. Values not listed here are universally available.
    CONSTRAINT_FLAG_MAPPING: ClassVar[dict[tuple[str, Any], str]] = {
        ("effort", "max"):            "supports_effort_max",
        ("effort", "xhigh"):          "supports_effort_xhigh",
        ("context_max", 1_000_000):   "supports_1m",
        ("fast_mode", True):          "supports_fast",
        ("permission_mode", "auto"):  "supports_permission_auto",
        ("thinking_enabled", False):  "supports_thinking_disabled",
    }

    USAGE_SYNC_INTERVAL: ClassVar[int | None] = 5 * 60

    # Anthropic's status page; the "Claude Code" component is the signal.
    STATUSPAGE: ClassVar[StatuspageConfig | None] = StatuspageConfig(
        components_url="https://status.claude.com/api/v2/components.json",
        component_name="Claude Code",
    )

    # Daily quota warm-up time lives in the synced settings under this key
    # ("HH:MM", empty = off). See :meth:`warm_up_quota`.
    QUOTA_WAKEUP_SETTING_KEY: ClassVar[str | None] = "claudeCodeQuotaWakeupTime"

    # Filesystem source for Claude Code session JSONL files. The CLI
    # writes one folder per project under here, with one JSONL per
    # session. Read by the initial sync and the watcher; not exposed
    # through the registry because it has no cross-provider meaning.
    PROJECTS_DIR: ClassVar[Path] = Path.home() / ".claude" / "projects"

    OPENROUTER_MODEL_PREFIX: ClassVar[str | None] = "anthropic/"

    # Default per-family prices (USD per million tokens) — fallback when no
    # ``ModelPrice`` row matches and no other version of the same family is
    # in the DB. Based on Anthropic's published pricing as of June 2026.
    DEFAULT_FAMILY_PRICES: ClassVar[dict[str, FamilyPrices]] = {
        "fable": FamilyPrices(  # Fable 5.1 pricing (the family's latest; Fable 5 reads cache at 1.00)
            input_price=Decimal("10.00"),
            output_price=Decimal("50.00"),
            cache_read_price=Decimal("0.25"),
            cache_write_5m_price=Decimal("12.50"),
            cache_write_1h_price=Decimal("20.00"),
        ),
        "opus": FamilyPrices(
            input_price=Decimal("5.00"),
            output_price=Decimal("25.00"),
            cache_read_price=Decimal("0.50"),
            cache_write_5m_price=Decimal("6.25"),
            cache_write_1h_price=Decimal("10.00"),
        ),
        "sonnet": FamilyPrices(
            input_price=Decimal("3.00"),
            output_price=Decimal("15.00"),
            cache_read_price=Decimal("0.30"),
            cache_write_5m_price=Decimal("3.75"),
            cache_write_1h_price=Decimal("6.00"),
        ),
        "haiku": FamilyPrices(
            input_price=Decimal("1.00"),
            output_price=Decimal("5.00"),
            cache_read_price=Decimal("0.10"),
            cache_write_5m_price=Decimal("1.25"),
            cache_write_1h_price=Decimal("2.00"),
        ),
    }

    @property
    def current_compute_version(self) -> int | None:
        """Return :data:`settings.CLAUDE_CODE_COMPUTE_VERSION`.

        Bumping that constant invalidates every Claude Code session and
        triggers the background compute task to recompute their metadata.
        """
        return settings.CLAUDE_CODE_COMPUTE_VERSION

    # ------------------------------------------------------------------
    # Plans — detection for the session view's *Plan* tab.
    # ------------------------------------------------------------------
    def resolve_plan_path(self, session: Session) -> Path | None:
        """``~/.claude/plans/<slug>.md``, or ``None`` when the session has no slug.

        The slug (``Session.slug``) is the plan filename; a session without one
        simply has no plan. Pure path resolution — existence is not checked here
        (see :meth:`session_has_plan` / the plan-content endpoint).
        """
        if not session.slug:
            return None
        return PLANS_DIR / f"{session.slug}.md"

    def session_has_plan(self, session: Session) -> bool:
        """True when ``~/.claude/plans/<slug>.md`` currently exists.

        In the live server this is O(1): it reads the plans watcher's in-memory
        slug set, kept accurate by the watch loop (which also powers the live
        ``plan_available`` / ``plan_gone`` transitions). Outside the server (the
        standalone CLI, background compute) the watcher never started and that set
        is empty, so we fall back to a direct on-disk check — a read, like the DB
        and JSONL reads these processes already do — rather than wrongly report
        ``False``. The fast path avoids stat-ing one file per session on every
        list serialization where a live cache already has the answer.
        """
        if not session.slug:
            return False
        from twicc.providers.claude_code.plans_watcher import get_claude_code_plans_watcher

        watcher = get_claude_code_plans_watcher()
        if watcher.is_active():
            return watcher.has_slug(session.slug)
        plan_path = self.resolve_plan_path(session)
        return plan_path is not None and plan_path.is_file()

    def extract_family_and_version(
        self, model_id: str,
    ) -> tuple[str | None, str | None]:
        """Parse a Claude OpenRouter ``model_id`` into ``(family, version)``.

        Handles every shape we have seen in OpenRouter's response:

        - new layout ``claude-<family>-<version>`` → e.g.
          ``claude-opus-4.7`` ⇒ ``("opus", "4.7")``;
        - legacy layout ``claude-<version>-<family>`` → e.g.
          ``claude-3.5-sonnet`` ⇒ ``("sonnet", "3.5")``;
        - variant suffix as a dash segment, e.g. ``claude-opus-4.6-fast``
          ⇒ ``("opus-fast", "4.6")``;
        - variant suffix after a colon, e.g.
          ``claude-3.7-sonnet:thinking`` ⇒ ``("sonnet-thinking", "3.7")``.

        Variants are folded into the family because they typically charge
        different prices (so they need their own pricing-equivalence
        bucket), while the version is whichever segment is purely numeric
        (or dotted-numeric). Returns ``(None, None)`` when no known
        family appears in the id.
        """
        prefix = "anthropic/claude-"
        if not model_id.startswith(prefix):
            return None, None
        suffix = model_id.removeprefix(prefix)

        # Optional trailing ``:variant`` (e.g. ``:thinking``)
        base, _, colon_variant = suffix.partition(":")
        parts = base.split("-") if base else []

        family: str | None = None
        version: str | None = None
        extras: list[str] = []
        for part in parts:
            if part in CLAUDE_FAMILIES:
                family = part
            elif part and all(ch.isdigit() or ch == "." for ch in part):
                if version is None:
                    version = part
            elif part:
                extras.append(part)

        if family is None:
            return None, None

        if colon_variant:
            extras.append(colon_variant)
        if extras:
            family = f"{family}-{'-'.join(extras)}"

        return family, version

    def compute_cache_write_1h_price(
        self,
        prompt_price: Decimal,
        cache_write_5m_price: Decimal,
    ) -> Decimal:
        """Anthropic bills the 1-hour cache write at ``prompt_price * 2``."""
        return prompt_price * 2

    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = _MODEL_VERSIONS

    def find_model(self, identifier: str) -> ModelVersion | None:
        """Look up a Claude Code model by its ``selected_model`` alias.

        Accepts both bare aliases (``"opus"``) and versioned aliases
        (``"opus-4.5"``). For bare aliases, returns the latest version
        of that family; for versioned aliases, the entry whose
        ``model`` and ``version`` match.
        """
        if "-" in identifier:
            model, version = identifier.split("-", 1)
            for mv in self.MODEL_VERSIONS:
                if mv.model == model and mv.version == version:
                    return mv
            return None
        for mv in self.MODEL_VERSIONS:
            if mv.model == identifier and mv.latest:
                return mv
        return None

    def _resolve_to_default_model_version(self) -> ModelVersion | None:
        """Return the :class:`ModelVersion` for the synced default model.

        Used by capability checks as a defensive fallback when the
        caller passes ``None`` or an unknown model. Returns ``None``
        if the synced default itself is missing or unknown.
        """
        from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS, read_synced_settings
        default_model = (
            read_synced_settings().get("claudeCodeDefaultModel")
            or SYNCED_SETTINGS_DEFAULTS.get("claudeCodeDefaultModel")
        )
        if not default_model:
            return None
        return self.find_model(default_model)

    def resolve_sdk_model(self, selected_model: str | None, context_max: int) -> str | None:
        """Resolve a ``selected_model`` + ``context_max`` to the SDK model string.

        - Latest aliases (``"opus"``, ``"sonnet"``) are passed through (the CLI resolves them).
        - Versioned aliases (``"opus-4.5"``) are resolved to their ``full_name``.
        - Appends ``"[1m]"`` when ``context_max`` is 1M and the model supports it.

        Returns ``None`` for an empty input.
        """
        if not selected_model:
            return None
        # Defense in depth: a disabled/retired model must never reach the SDK,
        # even if a call site skipped enforce_agent_settings_consistency.
        selected_model = self.sdk_model_safety_net(selected_model)
        mv = self.find_model(selected_model)
        if mv is None:
            logger.warning("Unknown model '%s', passing through to SDK", selected_model)
            base = selected_model
            supports_1m = True
        elif mv.latest:
            base = mv.model
            supports_1m = mv.provider_extra.supports_1m
        else:
            base = mv.full_name
            supports_1m = mv.provider_extra.supports_1m
        if context_max == 1_000_000 and supports_1m:
            return f"{base}[1m]"
        return base

    def selected_model_supports_1m(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or the synced default fallback) supports 1M context."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_1m)

    def selected_model_supports_effort_xhigh(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports the ``"xhigh"`` effort level."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_effort_xhigh)

    def selected_model_supports_effort_max(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports the ``"max"`` effort level."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_effort_max)

    def selected_model_supports_fast(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports fast mode."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_fast)

    def selected_model_supports_permission_auto(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model (or default fallback) supports ``permission_mode="auto"``.

        Anthropic gates auto mode on Opus 4.6+ and Sonnet 4.6+; older
        versions reject the option at the SDK / CLI level.
        """
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_permission_auto)

    def selected_model_supports_highres_images(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model ships images at native ``MAX_IMAGE_DIMENSION`` (2576 px).

        ``False`` models are downscaled client-side to 1568 px (the older
        Claude vision resolution). Reads the per-model capability flag rather
        than parsing the version string, so a new family opts in via the
        registry just like ``supports_1m`` & co.
        """
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_highres_images)

    def selected_model_supports_thinking_disabled(self, selected_model: str | None) -> bool:
        """Return ``True`` if the model lets you turn thinking off.

        ``False`` ⇒ adaptive thinking is always on and
        ``thinking:{type:disabled}`` is rejected by the API (Fable 5), so
        ``thinking_enabled`` is forced on for that model.
        """
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra.supports_thinking_disabled)

    def enforce_synced_settings_consistency(self, synced: dict, changes: dict) -> None:
        """Normalise ``claudeCodeDefault*`` keys when the default model changed.

        ``claudeCodeDefaultModel`` is the pivot: when the client picks
        a new default model in the UI, the front sends the related
        keys (``ContextMax``, ``Effort``) in the same update, so we
        only need to fire the rule then. Builds a transient
        :class:`AgentSettings` from the merged ``synced`` dict, runs
        it through :meth:`enforce_agent_settings_consistency`
        (auto-upgrade retired model + capability rules), and writes
        back only fields that were included in ``changes`` — never
        mutating a key the client didn't ask to touch.
        """
        if "claudeCodeDefaultModel" not in changes:
            return
        candidate = AgentSettings(
            selected_model=synced.get(
                "claudeCodeDefaultModel", self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultModel"],
            ),
            context_max=synced.get(
                "claudeCodeDefaultContextMax", self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultContextMax"],
            ),
            effort=synced.get("claudeCodeDefaultEffort"),
            fast_mode=synced.get(
                "claudeCodeDefaultFastMode", self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultFastMode"],
            ),
            permission_mode=synced.get(
                "claudeCodeDefaultPermissionMode",
                self.SYNCED_SETTINGS_DEFAULTS["claudeCodeDefaultPermissionMode"],
            ),
        )
        adjusted = self.enforce_agent_settings_consistency(candidate)
        if (
            "claudeCodeDefaultModel" in changes
            and adjusted.selected_model != candidate.selected_model
        ):
            synced["claudeCodeDefaultModel"] = adjusted.selected_model
        if (
            "claudeCodeDefaultContextMax" in changes
            and adjusted.context_max != candidate.context_max
        ):
            synced["claudeCodeDefaultContextMax"] = adjusted.context_max
        if (
            "claudeCodeDefaultEffort" in changes
            and adjusted.effort != candidate.effort
        ):
            synced["claudeCodeDefaultEffort"] = adjusted.effort
        if (
            "claudeCodeDefaultFastMode" in changes
            and adjusted.fast_mode != candidate.fast_mode
        ):
            synced["claudeCodeDefaultFastMode"] = adjusted.fast_mode
        if (
            "claudeCodeDefaultPermissionMode" in changes
            and adjusted.permission_mode != candidate.permission_mode
        ):
            synced["claudeCodeDefaultPermissionMode"] = adjusted.permission_mode

    def enforce_agent_settings_consistency(self, settings: AgentSettings) -> AgentSettings:
        """Substitute an unavailable model, then normalise capability rules.

        Pipeline:
        1. Delegates to :meth:`BaseProviderHelpers.enforce_agent_settings_consistency`
           to substitute an unavailable (disabled or retired)
           ``selected_model`` with the nearest-by-weight available model.
        2. Caps ``context_max`` to 200K when the (post-substitution) model
           doesn't support 1M context.
        3. Demotes ``effort == "max"`` to ``"xhigh"`` (or ``"high"`` if
           xhigh is also unsupported), then ``effort == "xhigh"`` to
           ``"high"`` when unsupported.
        4. Clears ``fast_mode`` when the model doesn't support it
           (fast mode is only available on supported Opus versions).
        5. Demotes ``permission_mode == "auto"`` to ``"default"`` when
           the model doesn't support auto (Opus 4.5 / Sonnet 4.5 and
           earlier are rejected by the SDK / CLI).
        6. Forces ``thinking_enabled`` on when the model can't disable
           thinking (Fable 5: adaptive thinking is always on and
           ``thinking:{type:disabled}`` is rejected by the API).
        """
        settings = super().enforce_agent_settings_consistency(settings)

        model = settings.selected_model
        context_max = settings.context_max
        effort = settings.effort
        fast_mode = settings.fast_mode
        permission_mode = settings.permission_mode
        thinking_enabled = settings.thinking_enabled

        if context_max == 1_000_000 and not self.selected_model_supports_1m(model):
            context_max = 200_000

        if effort == "max" and not self.selected_model_supports_effort_max(model):
            effort = "xhigh" if self.selected_model_supports_effort_xhigh(model) else "high"
        if effort == "xhigh" and not self.selected_model_supports_effort_xhigh(model):
            effort = "high"

        if fast_mode and not self.selected_model_supports_fast(model):
            fast_mode = False

        if permission_mode == "auto" and not self.selected_model_supports_permission_auto(model):
            permission_mode = "default"

        if thinking_enabled is False and not self.selected_model_supports_thinking_disabled(model):
            thinking_enabled = True

        if (
            context_max == settings.context_max
            and effort == settings.effort
            and fast_mode == settings.fast_mode
            and permission_mode == settings.permission_mode
            and thinking_enabled == settings.thinking_enabled
        ):
            return settings
        return settings._replace(
            context_max=context_max,
            effort=effort,
            fast_mode=fast_mode,
            permission_mode=permission_mode,
            thinking_enabled=thinking_enabled,
        )

    def get_agent_settings_choices(self) -> dict[str, list]:
        return AGENT_SETTINGS_CHOICES

    def get_attachment_support(self) -> dict:
        return ATTACHMENT_SUPPORT

    def get_effective_image_dimension(
        self, model: str | None, num_images: int
    ) -> int:
        """Return the long-edge cap (in px) for outgoing images.

        Mirrors ``ClaudeCodeHelpers.getEffectiveImageDimension`` in
        ``frontend/src/providers/claude_code/helpers.js``.

        Anthropic vision rules in play:

        - Models flagged ``supports_highres_images`` accept native
          ``MAX_IMAGE_DIMENSION`` (2576 px); we ship at full resolution.
        - Older Claude models downscale server-side to 1568 px; we cap
          there ahead of time to save bandwidth and keep token usage
          predictable.
        - Any request carrying more than 20 images is further capped by
          Anthropic to 2000 px regardless of model; apply the tighter
          of {model cap, 2000} in that case.

        A retired ``model`` is upgraded to its successor first (matching
        what ``enforce_agent_settings_consistency`` would do) so the
        capability check uses the post-upgrade alias.
        """
        upgraded = self._upgrade_retired_model(model) or model
        highres = self.selected_model_supports_highres_images(upgraded)
        cap = MAX_IMAGE_DIMENSION if highres else 1568
        if num_images > 20:
            cap = min(cap, 2000)
        return cap

    def _upgrade_retired_model(self, model: str | None) -> str | None:
        """Return the successor of ``model`` if it is past retirement.

        Mirrors ``getRetiredModelUpgrade`` in
        ``frontend/src/providers/claude_code/helpers.js``. Returns ``None``
        when the input is missing, current, or already the latest in its
        family.
        """
        if not model:
            return None
        today = date.today()
        entry = next(
            (mv for mv in self.MODEL_VERSIONS
             if self.selected_model_value(mv) == model),
            None,
        )
        if entry is None or entry.latest:
            return None
        if entry.retirement_date is None or entry.retirement_date >= today:
            return None
        family = sorted(
            (mv for mv in self.MODEL_VERSIONS if mv.model == entry.model),
            key=lambda mv: tuple(int(p) for p in mv.version.split(".")),
        )
        current_parts = tuple(int(p) for p in entry.version.split("."))
        for candidate in family:
            candidate_parts = tuple(int(p) for p in candidate.version.split("."))
            if candidate_parts > current_parts:
                return self.selected_model_value(candidate)
        return None

    def serialize_model_extra(self, mv: ModelVersion) -> dict:
        """Expose Claude Code's :class:`ClaudeCodeModelExtra` flags on the wire."""
        return mv.provider_extra._asdict()

    def extract_indexable_text(self, item: SessionItem) -> str:
        try:
            parsed = orjson.loads(item.content)
        except (orjson.JSONDecodeError, TypeError):
            return ""
        text = _extract_text_from_message_content(get_message_content(parsed))
        if not text:
            return ""
        # Slash commands ship as <command-*> XML — unwrap to "name [args]"
        # so FTS hits the real words and the history picker reads naturally.
        if command := extract_command(text):
            if command.args:
                return f"{command.name} {strip_markdown(command.args)}"
            return command.name
        return text

    def get_user_messages(
        self,
        items: Iterable[SessionItem],
        limit: int | None = None,
    ) -> list[UserMessage]:
        out: list[UserMessage] = []
        for item in items:
            if limit is not None and len(out) >= limit:
                break
            text = self.extract_indexable_text(item)
            if text:
                out.append(UserMessage(
                    line_num=item.line_num,
                    timestamp=item.timestamp,
                    text=text,
                ))
        return out

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        out: list[IndexableMessage] = []
        for item in items:
            text = self.extract_indexable_text(item)
            if text:
                from_role = "user" if item.kind == ItemKind.USER_MESSAGE else "assistant"
                out.append(IndexableMessage(
                    line_num=item.line_num,
                    text=text,
                    from_role=from_role,
                    timestamp=item.timestamp,
                ))
        return out

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        results: list[dict] = []
        for item in items:
            try:
                parsed = orjson.loads(item.content)
            except orjson.JSONDecodeError:
                continue
            content_list = get_message_content_list(parsed, "user")
            if not content_list:
                continue
            for entry in content_list:
                if entry.get("type") == "tool_result" and entry.get("tool_use_id") == tool_use_id:
                    results.append(entry)
        return results

    def serialize_model(self, model: str | None) -> dict | None:
        return serialize_model(model)

    def validate_usage_file_payload(self, payload: dict) -> tuple[bool, str]:
        """Check that ``payload`` matches the Anthropic OAuth usage API shape.

        Required top-level keys: ``five_hour`` and ``seven_day``. Other
        fields are tolerated (the API may add new ones).
        """
        from .usage import USAGE_REQUIRED_KEYS

        missing = USAGE_REQUIRED_KEYS - payload.keys()
        if missing:
            return False, f"Missing required keys: {', '.join(sorted(missing))}"
        return True, "Valid usage file"

    async def generate_title(self, prompt: str, system_prompt: str) -> str | None:
        """Run a short Haiku SDK query to suggest a title for ``prompt``."""
        from .title_suggest import generate_title as _generate_title

        return await _generate_title(prompt, system_prompt)

    async def warm_up_quota(self) -> bool | None:
        """Open the Claude 5-hour window via the existing auth-probe throwaway turn.

        ``probe_auth_via_sdk`` already runs exactly the minimal turn we want —
        a one-token Haiku ``"ping"`` over the subscription OAuth credentials,
        no session persisted — and reports whether the API accepted it, which
        is precisely the warm-up signal. Reusing it keeps a single throwaway
        path rather than a near-duplicate.
        """
        from .auth import probe_auth_via_sdk

        return await probe_auth_via_sdk()

    async def rename_session(self, session_id: str, title: str) -> None:
        """Append the title to the JSONL and mark it protected against CLI stale re-appends.

        ``protect_title`` runs in a ``finally`` so the protection is
        registered even when the JSONL write fails — the DB row already
        holds the new title and we still want to block any out-of-date
        title the CLI might re-append. ``rename_session_in_jsonl`` does
        FS I/O (SDK kernel-level atomic append), so it hops to a worker
        thread; ``protect_title`` is a dict mutation, safe inline.

        Hybrid sessions with a LIVE claude TUI take a different route: a
        pasted ``/rename`` (the CLI applies it instantly and writes the
        custom-title lines itself). Writing the JSONL line directly under
        a live claude would be clobbered by its own state re-appends on
        exit. Dead/non-hybrid sessions keep the direct write (the CLI
        reads the line on its next ``--resume`` tail-scan).
        """
        import asyncio

        from twicc.providers.claude_code.agent.manager import get_claude_code_agent_manager

        try:
            if await get_claude_code_agent_manager().rename_hybrid_if_live(session_id, title):
                return
            await asyncio.to_thread(rename_session_in_jsonl, session_id, title)
        finally:
            protect_title(session_id, title)

    # Env vars set by Claude Code (CLI / SDK) that, when inherited by
    # a subprocess, make a fresh ``claude`` invocation think it's
    # already inside an SDK session. Each var name *starts with* one
    # of these prefixes (e.g. ``CLAUDE_CODE_ENTRYPOINT``,
    # ``CLAUDECODE_DEBUG``).
    _ENV_VAR_PREFIXES: ClassVar[tuple[str, ...]] = ("CLAUDE_CODE", "CLAUDECODE")

    def purge_env_vars(self, env: dict) -> None:
        """Strip Claude Code env vars from ``env`` in place.

        See :attr:`_ENV_VAR_PREFIXES` for the affected names. Called
        before TwiCC spawns any subprocess (login shell, tmux server,
        the CLI itself) that must start with a clean Claude
        environment.
        """
        for key in list(env):
            if key.startswith(self._ENV_VAR_PREFIXES):
                del env[key]

    async def enrich_agent_state(self, message: dict, session_id: str) -> None:
        """Attach ``active_crons`` for ``session_id`` to ``message``.

        Claude Code crons live in :class:`SessionCron` and are queried
        by ``(session_id, provider)``. We can scope the query with our
        own :attr:`provider` ClassVar — the dispatcher in
        :mod:`twicc.asgi` already routed us here based on the session's
        provider, so there is nothing else to read from ``message``.
        """
        from asgiref.sync import sync_to_async

        from twicc.core.models import SessionCron

        crons = await sync_to_async(
            lambda: [c.serialize() for c in SessionCron.active_for_session(session_id, self.provider)]
        )()
        if crons:
            message["active_crons"] = crons

    def should_keep_dead_process_run(
        self,
        process_run,
        *,
        agent=None,
    ) -> bool:
        """Keep a DEAD :class:`ProcessRun` only if it still has attached crons.

        Runtime shortcuts override the cron check when an explicit ``agent``
        context is supplied:

        - ``kill_reason`` in :data:`~twicc.agent.states.DELIBERATE_STOP_REASONS`
          — a human explicitly stopped the session (Stop, hard kill, or
          archive, from the UI or the CLI/MCP). We discard the row even if
          crons exist, so neither the runtime restart in
          :meth:`ClaudeCodeAgentManager._on_state_change` nor the boot-time
          one in :mod:`.cron_restart` can bring the session back. Deaths
          nobody asked for (``shutdown``, ``apply-settings``, crashes) keep
          the row — restoring their crons is exactly the point.
        - ``not _first_user_turn_reached`` — the agent died before reaching
          USER_TURN (failed cron restart, early crash), so its crons are
          partial or absent; we discard the row to clear any partial state
          and let an earlier surviving row drive a retry.

        Boot cleanup calls without ``agent`` and only the cron-existence
        check runs.
        """
        from twicc.agent.states import DELIBERATE_STOP_REASONS

        if agent is not None:
            if getattr(agent, "kill_reason", None) in DELIBERATE_STOP_REASONS:
                return False
            if not getattr(agent, "_first_user_turn_reached", False):
                return False
        return process_run.crons.exists()

    async def try_handle_async_job(self, job, settle_async_job) -> bool:
        """Route Claude Code-specific async-queue jobs to their handler.

        Currently :class:`PrepareCronRestartsJob` only (the boot-time
        ProcessRun / SessionCron cleanup, see :mod:`.cron_restart`). Lives
        here rather than in db_writer.py so the job type, its sync handler,
        and the producer all stay co-located in this provider's subpackage.
        """
        from .cron_restart import PrepareCronRestartsJob, _apply_prepare_cron_restarts_job

        if isinstance(job, PrepareCronRestartsJob):
            await settle_async_job(
                job, _apply_prepare_cron_restarts_job, "cron restart prepare",
            )
            return True
        return False
