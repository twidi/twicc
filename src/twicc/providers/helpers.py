"""
Per-provider helpers and their cross-provider registry.

Defines the abstract surface that every backend provider implements
(``BaseProviderHelpers``) and the singleton registry that exposes one
helpers instance per provider (``get_provider_helpers``). Mirrors the
role of ``BaseAgentManager`` (process management) and the WebSocket
handlers: when the core needs to do something whose details depend on
which provider produced a session — parse the session content,
serialize a model identifier, persist a new title, contribute fields to
the bootstrap payload — it calls into ``BaseProviderHelpers`` and the
registry routes the call to the right implementation.

Concrete providers live in ``providers/<name>/helpers.py`` and subclass
``BaseProviderHelpers``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from twicc.core.enums import Provider
from twicc.pricing import FamilyPrices, TokenUsage

logger = logging.getLogger(__name__)


# Shared long-edge cap (in pixels) for outgoing images. Matches
# ``MAX_IMAGE_DIMENSION`` in ``frontend/src/utils/fileUtils.js`` — Opus 4.7's
# native resolution and the default cap for every provider that doesn't
# override :meth:`BaseProviderHelpers.get_effective_image_dimension`.
MAX_IMAGE_DIMENSION = 2576


class AgentSettingCategory(StrEnum):
    """When a per-agent setting can be applied to a running process.

    Each provider classifies its own keys per category in
    :attr:`BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES`.

    - ``LIVE``: applicable at any time (USER_TURN or ASSISTANT_TURN)
    - ``IDLE``: applicable only during USER_TURN
    - ``STARTUP``: applicable only at process creation (requires restart)
    """
    LIVE = "live"
    IDLE = "idle"
    STARTUP = "startup"

if TYPE_CHECKING:
    from pathlib import Path

    from twicc.core.models import Session, SessionItem


class TitleValidationResult(NamedTuple):
    """Outcome of validating a user-supplied session title.

    ``title`` is the normalized value when valid, ``None`` otherwise.
    ``error`` is a human-readable message when invalid, ``None`` otherwise.
    """
    title: str | None
    error: str | None


class UserMessage(NamedTuple):
    """One indexable user message in a session, produced by ``get_user_messages``."""
    line_num: int
    timestamp: datetime | None
    text: str


class IndexableMessage(NamedTuple):
    """One indexable message (user or assistant) for full-text search indexing.

    Produced by ``get_indexable_messages``; consumed by the search reindex path.
    """
    line_num: int
    text: str
    from_role: str  # "user" or "assistant"
    timestamp: datetime | None


class AgentSettings(NamedTuple):
    """The compact bundle of per-session agent settings, shared across providers.

    Each field corresponds to a column on the ``Session`` ORM model and, in
    raw form, may be ``None`` to mean "not overridden by the user — fall
    back to the synced (global) default". After being passed through
    :meth:`BaseProviderHelpers.resolve_agent_settings`, all fields hold
    concrete values ready to be handed to a provider's agent factory.

    The set of fields is intentionally generic: every provider receives
    the full bundle and decides what to do with each — for example,
    ``claude_in_chrome`` is meaningful for Claude Code only, but the
    field still travels in the bundle so its absence does not require a
    provider-specific signature.

    The list is closed (no surprise extra fields) and grows only by
    explicit edit of this class. Mutate via :meth:`_replace`.
    """
    permission_mode: str | None = None
    selected_model: str | None = None
    effort: str | None = None
    thinking_enabled: bool | None = None
    claude_in_chrome: bool | None = None
    fast_mode: bool | None = None
    context_max: int | None = None
    question_widget: bool | None = None

    @classmethod
    def from_session(cls, session: Session) -> AgentSettings:
        """Build an :class:`AgentSettings` from a ``Session`` row's columns.

        ``None`` values are preserved (they mean "use the synced default"
        downstream, in :meth:`BaseProviderHelpers.resolve_agent_settings`).
        """
        return cls(
            permission_mode=session.permission_mode,
            selected_model=session.selected_model,
            effort=session.effort,
            thinking_enabled=session.thinking_enabled,
            claude_in_chrome=session.claude_in_chrome,
            fast_mode=session.fast_mode,
            context_max=session.context_max,
            question_widget=session.question_widget,
        )


# Agent settings fields that must never be exposed to the frontend, regardless
# of provider. Fields listed here are:
# - filtered out of the per-provider ``agent_settings_categories`` map in the
#   HTTP bootstrap response — see :func:`twicc.views.bootstrap` (so the frontend
#   never knows the field exists);
# - omitted from the serialized session payload — see
#   :func:`twicc.core.serializers.serialize_session` (so existing values do
#   not leak via REST or WebSocket broadcasts);
# - stripped from inbound WebSocket payloads — see
#   :func:`agent_settings_kwargs_from_frontend_payload` and the preset
#   handler in :mod:`twicc.asgi` (so the frontend cannot write them even by
#   accident; defense in depth).
#
# Such fields remain part of the closed ``AgentSettings`` bundle and can still
# be classified in a provider's ``AGENT_SETTINGS_CATEGORIES`` (so the backend
# treats them consistently with other settings of the same category). The
# in-process bootstrap (used by the CLI via ``load_local_bootstrap``) does
# NOT filter them — the CLI is a backend-side entry point and must be able
# to set hidden fields via its own flags. Filtering happens only at the
# backend → frontend boundary (HTTP bootstrap response, session serializer,
# inbound WS message handlers).
AGENT_SETTINGS_HIDDEN_FROM_FRONTEND: frozenset[str] = frozenset({"question_widget"})


def agent_settings_kwargs_from_frontend_payload(payload: dict) -> dict[str, Any]:
    """Read AgentSettings field values from a frontend-authored payload, dropping hidden fields.

    Use this for any payload coming from a WebSocket message the frontend
    can author — it filters out fields listed in
    :data:`AGENT_SETTINGS_HIDDEN_FROM_FRONTEND` so the frontend cannot
    write them even by accident. Returns a kwargs dict suitable for
    ``AgentSettings(**...)`` or for ``**`` spreading into a larger payload.

    Do NOT use for CLI drop-file payloads (see
    :func:`twicc.core.services.session_creation.create_session_from_payload`) —
    the CLI is a backend-side entry point and can legitimately set hidden fields.
    """
    return {
        field: payload.get(field)
        for field in AgentSettings._fields
        if field not in AGENT_SETTINGS_HIDDEN_FROM_FRONTEND
    }


class ModelVersion(NamedTuple):
    """A single supported model version, contributed by one provider.

    ``provider`` identifies the provider that owns the entry. ``model``
    is the family alias (e.g. ``"opus"``), ``version`` the short version
    string (e.g. ``"4.7"``), ``full_name`` the full SDK identifier
    (e.g. ``"claude-opus-4-7"``). ``retirement_date`` is ``None`` for
    the current default of its family. ``latest`` is ``True`` for the
    one entry per ``(provider, model)`` line currently considered the
    default. ``provider_extra`` is a provider-specific NamedTuple
    carrying capability flags or other metadata that doesn't generalise
    cross-provider — for Claude Code this is
    :class:`ClaudeCodeModelExtra`.

    ``weight`` is a cross-family strength ranking *within a provider*
    (higher = stronger), unique per provider. It drives two things: the
    order models appear in every model picker (sorted descending), and
    the fallback chosen when a model becomes unavailable — see
    :meth:`BaseProviderHelpers.resolve_to_available_model`. ``enabled``
    is ``False`` to take a model out of service without removing its
    entry; ``disable_reason`` is the human-readable explanation shown
    (greyed out) in the model pickers. A disabled model — like a retired
    one — is *unavailable* and resolves to the nearest-by-weight
    available model.
    """
    provider: Provider
    model: str
    version: str
    full_name: str
    retirement_date: date | None
    latest: bool
    provider_extra: Any
    weight: int
    enabled: bool = True
    disable_reason: str | None = None


def assert_unique_weights(model_versions: list[ModelVersion]) -> None:
    """Fail loudly at import time if two models share a ``weight``.

    Weights drive both the model-picker ordering and the fallback
    resolution (:meth:`BaseProviderHelpers.resolve_to_available_model`),
    where an exact distance tie is broken by the higher weight — so two
    equal weights would make ordering and tie-breaking ambiguous. Each
    provider calls this once, right after declaring its
    ``MODEL_VERSIONS``, so a duplicate is caught the moment the module
    is imported (at startup) rather than silently mis-ordering pickers.
    """
    seen: dict[tuple[Provider, int], str] = {}
    for mv in model_versions:
        key = (mv.provider, mv.weight)
        if key in seen:
            raise ValueError(
                f"Duplicate model weight {mv.weight} for provider "
                f"{mv.provider.value}: {seen[key]} and {mv.full_name}"
            )
        seen[key] = mv.full_name


class BaseProviderHelpers:
    """Abstract per-provider helpers."""

    provider: ClassVar[Provider]

    # Human-readable display name of the provider (e.g. "Claude Code",
    # "Codex"). Surfaced by ``twicc info`` and any other discovery
    # surface that needs a label distinct from the wire key. Each
    # provider sets its own value; the base default is intentionally
    # empty so a freshly-added provider that forgets to declare it
    # fails visibly.
    LABEL: ClassVar[str] = ""

    # Provider-specific block appended to the shared TwiCC system-prompt
    # addendum by :func:`twicc.agent.system_prompt.compose_addendum`,
    # inserted before the dynamic ``## Live environment`` section. Use it
    # for guidance that only makes sense to agents of this provider (e.g.
    # SDK quirks the agent must work around). Default is empty so a
    # provider with nothing extra to say contributes nothing.
    SYSTEM_PROMPT_STATIC_ADDENDUM: ClassVar[str] = ""

    # Provider-specific entries to merge into ``SYNCED_SETTINGS_DEFAULTS``.
    # Keys must be namespaced (e.g. ``claudeCodeDefault*``) to avoid clashes
    # between providers and with the cross-provider generic defaults.
    SYNCED_SETTINGS_DEFAULTS: ClassVar[dict] = {}

    # Provider-specific legacy → current key renames applied at read time so
    # old ``settings.json`` files keep their values across renames.
    RENAMED_SYNCED_SETTINGS_KEYS: ClassVar[dict[str, str]] = {}

    # Provider-specific legacy keys to drop unconditionally on read (no longer
    # used). Aggregated with each other provider's contribution and with the
    # cross-provider generic list during the settings migration.
    OBSOLETE_SYNCED_SETTINGS_KEYS: ClassVar[tuple[str, ...]] = ()

    # Per-agent settings classified by when they can be applied to a running
    # process. Each provider defines its own keys per :class:`AgentSettingCategory`.
    AGENT_SETTINGS_CATEGORIES: ClassVar[dict[AgentSettingCategory, list[str]]] = {}

    # Per-agent setting field name → corresponding key in the synced settings
    # used as fallback when the session-level value is unset. Drives
    # :meth:`resolve_agent_settings`. Each provider defines its own mapping.
    AGENT_SETTINGS_FIELDS_MAPPING: ClassVar[dict[str, str]] = {}

    # Supported model versions for this provider. Each provider exposes
    # its own list; the cross-provider registry aggregates them via the
    # :class:`ProviderHelpersRegistry`.
    MODEL_VERSIONS: ClassVar[list[ModelVersion]] = []

    # Map ``(field, value) -> provider_extra_flag`` for agent-settings values
    # whose applicability depends on the selected model. Drives
    # :meth:`get_agent_settings_constraints`. Empty by default — providers
    # without contextual constraints (e.g. Codex) inherit the empty mapping.
    CONSTRAINT_FLAG_MAPPING: ClassVar[dict[tuple[str, Any], str]] = {}

    # Per-(field, value) human-readable description. Surfaced by
    # ``twicc info agent-settings``; values without an entry are silently
    # omitted. Each provider redeclares the catalogue from its own
    # ``.constants`` module so it stays Django-free.
    AGENT_SETTINGS_DESCRIPTIONS: ClassVar[dict[str, dict]] = {}

    # Per-field aliases the CLI/skills accept in place of a concrete
    # agent-settings value (``max`` → the family flagship, ``open`` → the most
    # permissive non-interactive mode, ...). Shape ``{field: {alias: value}}``.
    # Each provider redeclares its own table from its ``.constants`` module so
    # it stays Django-free and importable by the CLI. Empty by default — a
    # provider with no aliases accepts only literal values.
    AGENT_SETTINGS_ALIASES: ClassVar[dict[str, dict[str, str]]] = {}

    # Polling interval (seconds) for this provider's usage sync task, or
    # ``None`` when the provider has no usage tracking. The actual loop
    # lives in each provider's own orchestrator module; this ClassVar is
    # the declarative source of truth read by the loop and by tools that
    # iterate the registry (e.g. the ``twicc usage`` CLI).
    USAGE_SYNC_INTERVAL: ClassVar[int | None] = None

    # Synced-settings key holding this provider's daily "quota warm-up"
    # wall-clock time (``"HH:MM"``, empty = disabled), or ``None`` when the
    # provider has no rolling-window quota to warm up. The cross-provider
    # warm-up task (:mod:`twicc.quota_wakeup_task`) reads this key and, at
    # that local time each day, calls :meth:`warm_up_quota` unless a window
    # is already running. Only providers with a subscription 5-hour window
    # (those that also track usage) set it.
    QUOTA_WAKEUP_SETTING_KEY: ClassVar[str | None] = None

    # OpenRouter ``model_id`` prefix used both to filter the pricing API
    # response and to recognise rows that belong to this provider. E.g.
    # ``"anthropic/"`` for Claude Code, ``"openai/"`` for OpenAI. When
    # ``None``, the provider is excluded from the cross-provider
    # OpenRouter price sync (see :mod:`twicc.pricing_task`).
    OPENROUTER_MODEL_PREFIX: ClassVar[str | None] = None

    # Per-family default prices (USD per million tokens) — fallback when
    # no :class:`ModelPrice` row matches and no other version of the
    # same family is in the DB. Keys must match what
    # :meth:`extract_family_and_version` produces for this provider.
    DEFAULT_FAMILY_PRICES: ClassVar[dict[str, FamilyPrices]] = {}

    # Maximum length (characters) of a user-supplied session title, used
    # by the default :meth:`validate_title`. Providers may override if
    # their backing store imposes a stricter cap; the frontend mirrors
    # the same constant so client-side validation matches.
    MAX_TITLE_LENGTH: ClassVar[int] = 200

    # ``permission_mode`` values acceptable for a hidden session. Hidden
    # sessions cannot rely on the user clicking through interactive
    # approval prompts, so the helper must declare which provider-specific
    # ``permission_mode`` values run without prompting. Empty default
    # means the provider does NOT support hidden sessions at all.
    NON_INTERACTIVE_PERMISSION_MODES: ClassVar[frozenset[str]] = frozenset()

    # ``permission_mode`` values allowed for sessions in an UNTRUSTED (or
    # unknown-trust) project, and the synced-settings key holding the default
    # mode used to seed/clamp such sessions. Empty set means the provider has
    # no trust-based restriction. See
    # docs/plans/2026-06-09-project-trust-design.md §13.2.
    UNTRUSTED_PERMISSION_MODES: ClassVar[frozenset[str]] = frozenset()
    UNTRUSTED_PERMISSION_MODE_SYNCED_KEY: ClassVar[str | None] = None

    # ------------------------------------------------------------------
    # Compute version
    # ------------------------------------------------------------------

    @property
    def current_compute_version(self) -> int | None:
        """Return the provider's current compute version, or ``None`` if it has no compute pipeline.

        The compute version travels next to the ``Session.compute_version``
        column: a session is considered up-to-date when ``session.compute_version
        == helpers.current_compute_version``. Each provider exposes its own
        version (typically read from a dedicated ``settings.<PROVIDER>_COMPUTE_VERSION``
        constant) so that bumping the rules of one provider does not invalidate
        sessions of another.

        ``None`` declares "this provider has no compute pipeline yet" — sessions
        keep their default ``compute_version=NULL`` and the equality check
        (``None == None``) reports them as up-to-date, so the front displays
        them normally without waiting for a recompute that never comes.

        The base implementation returns ``None`` so a freshly-added provider
        is treated as compute-less by default; providers with a compute
        pipeline override this to return their settings constant.
        """
        return None

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------
    # Detection seam for the session view's *Plan* tab. The serializer field
    # ``has_plan`` and the whole frontend are provider-agnostic; each provider
    # that has a plan concept implements these two methods (and ships a watcher
    # that keeps ``session_has_plan`` live). The base returns "no plan".

    def resolve_plan_path(self, session: Session) -> Path | None:
        """Absolute path of the session's plan file, or ``None`` if it has none.

        Pure path resolution — does not check existence. Read by the
        ``/api/sessions/<id>/plan/`` endpoint to serve the markdown. The base
        provider has no plan concept.
        """
        return None

    def session_has_plan(self, session: Session) -> bool:
        """Whether the session's plan file currently exists.

        Read by ``serialize_session`` for every session, so it must be cheap: in
        the live server providers back it with their plans watcher's in-memory
        set (O(1), no stat per session). When that watcher isn't running (the
        standalone CLI, background compute) they fall back to a direct on-disk
        check so the flag stays accurate outside the server. The base provider
        has no plan concept.
        """
        return False

    # ------------------------------------------------------------------
    # Per-provider data files
    # ------------------------------------------------------------------

    def get_settings_presets_path(self):
        """Return the per-provider agent settings presets file path.

        File layout: ``<data_dir>/<provider>-settings-presets.json``. The
        path is fully derived from the provider key — every provider gets
        its own file with no override needed. Used by the agent settings
        presets read/write functions of each provider.
        """
        from twicc.paths import get_data_dir

        return get_data_dir() / f"{self.provider.value}-settings-presets.json"

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    def extract_family_and_version(
        self, model_id: str,
    ) -> tuple[str | None, str | None]:
        """Split a ``model_id`` into ``(family, version)``.

        ``family`` is the *pricing-equivalence* key — the identifier used
        to find fallback prices among siblings (e.g. for Claude Code:
        ``"opus"`` / ``"sonnet"`` / ``"haiku"``; for OpenAI: ``"gpt"``,
        ``"gpt-mini"``, ``"gpt-codex"``, …). Two ``model_id`` values
        sharing the same ``family`` must charge similar prices, so the
        cross-provider fallback logic can substitute one for the other.

        ``version`` is the numeric portion (e.g. ``"4.7"``, ``"5.4"``).
        It must be parsable as a dotted tuple of ints by the generic
        fallback ordering. Both values are ``None`` when the format is
        not recognised.

        Each provider implements this against its own naming convention.
        """
        return None, None

    def compute_cache_write_1h_price(
        self,
        prompt_price: Decimal,
        cache_write_5m_price: Decimal,
    ) -> Decimal:
        """Return the per-million 1-hour cache-write price.

        Default: identical to the 5-minute cache-write price (most
        providers don't differentiate). Claude overrides to
        ``prompt_price * 2`` per Anthropic's published billing rule.
        """
        return cache_write_5m_price

    def calculate_line_cost(
        self,
        usage: TokenUsage,
        model_id: str,
        line_date: date,
    ) -> Decimal | None:
        """Compute the per-line cost for this provider.

        Thin wrapper that injects ``self.provider`` and delegates to
        :func:`twicc.pricing.calculate_line_cost` so call sites in
        provider-specific code don't have to thread the enum value
        themselves.
        """
        from twicc.pricing import calculate_line_cost

        return calculate_line_cost(self.provider, usage, model_id, line_date)

    # ------------------------------------------------------------------
    # Usage file (read mode) format validation
    # ------------------------------------------------------------------

    def validate_usage_file_payload(self, payload: dict) -> tuple[bool, str]:
        """Validate the format of a usage file payload for this provider.

        Called by the cross-provider read-mode flow after the file has
        been opened, parsed as JSON, and asserted to be a top-level
        object. The helper is responsible only for checking that the
        payload matches the shape this provider expects from its
        own usage source (e.g. for Claude Code, the Anthropic OAuth
        usage API response with ``five_hour`` / ``seven_day`` blocks).

        Returns ``(valid, message)``. The default refuses any payload
        with a generic message — providers that support the read mode
        must override.
        """
        return False, "This provider does not support reading usage from a file"

    # ------------------------------------------------------------------
    # Quota warm-up
    # ------------------------------------------------------------------

    async def warm_up_quota(self) -> bool | None:
        """Start this provider's rolling quota window with a throwaway call.

        Sent by :mod:`twicc.quota_wakeup_task` at the user-configured daily
        time so the subscription's 5-hour window opens early in the day —
        the earlier the first request, the more 5-hour windows are usable
        before the user stops working. The call goes through the same
        subscription OAuth credentials as a normal session, so it consumes
        the very window it opens (a one-token reply we discard). Providers
        reuse their existing minimal throwaway turn.

        Returns ``True`` when the call completed (window started), ``False``
        when the provider rejected it (e.g. auth failed — nothing warmed),
        ``None`` when inconclusive (timeout/network) or unsupported.
        """
        return None

    def resolve_agent_settings(self, source: AgentSettings) -> AgentSettings:
        """Return the effective per-agent settings, with global fallbacks.

        For each field of :class:`AgentSettings`, the fallback order is:

        1. the explicit override in ``source`` (wins if non-``None``);
        2. the global synced settings default (looked up via
           :attr:`AGENT_SETTINGS_FIELDS_MAPPING`), with the helper's own
           :attr:`SYNCED_SETTINGS_DEFAULTS` as a last-resort fallback.

        Fields not listed in the mapping (i.e. unsupported by this provider) are
        returned as ``None``.

        Per-project defaults are NOT resolved here: they are a creation-time
        concern, materialized into concrete values before the session exists.
        The frontend resolves the project chain when a draft is created and
        pre-fills the draft (``frontend/src/utils/projectAgentDefaults.js``);
        the CLI does the same at ``create-session`` time (via
        :mod:`twicc.project_agent_defaults`). Either way a launched session
        carries no ``None`` for a supported field. A ``None`` reaching this
        method therefore comes from a legacy session and falls through to the
        global synced default. See
        ``docs/plans/2026-06-09-project-agent-defaults-design.md``.
        """
        from twicc.synced_settings import read_synced_settings

        synced = read_synced_settings()
        resolved: dict = {}
        for field in AgentSettings._fields:
            value = getattr(source, field)
            if value is None:
                default_key = self.AGENT_SETTINGS_FIELDS_MAPPING.get(field)
                if default_key is not None:
                    value = synced.get(default_key, self.SYNCED_SETTINGS_DEFAULTS.get(default_key))
            resolved[field] = value
        return AgentSettings(**resolved)

    def classify_agent_settings_changes(
        self,
        current: AgentSettings,
        requested: AgentSettings,
        categories: dict[AgentSettingCategory, list[str]] | None = None,
    ) -> dict[AgentSettingCategory, list[str]]:
        """Return per-category lists of fields that differ between ``current`` and ``requested``.

        Default implementation derives the per-category diff from
        :attr:`AGENT_SETTINGS_CATEGORIES`. Categories with no changes return an
        empty list. ``categories`` overrides the classification table for
        alternative agent flavors of the same provider (e.g. Claude Code's
        hybrid CLI mode, where the TUI changes what can be applied live).
        """
        if categories is None:
            categories = self.AGENT_SETTINGS_CATEGORIES
        result: dict[AgentSettingCategory, list[str]] = {
            category: [] for category in categories
        }
        for category, fields in categories.items():
            for field in fields:
                if getattr(current, field) != getattr(requested, field):
                    result[category].append(field)
        return result

    def get_agent_settings_choices(self) -> dict[str, list]:
        """Return the valid choices per agent-settings field for this provider.

        Keys are field names (subset of `AgentSettings._fields`, never including
        `selected_model` which is covered by `model_registry`). Values are lists
        of valid raw values (strings, bools, or ints depending on the field).

        Used by both the CLI (for pre-flight validation) and the front-end
        bootstrap (to populate select widgets).
        """
        raise NotImplementedError

    def get_agent_settings_aliases(self) -> dict[str, dict[str, str]]:
        """Return the per-field aliases for this provider.

        Shape ``{field: {alias: concrete_value}}``. Used by the CLI/skills to
        resolve semantic aliases (``max``, ``open`` ...) into concrete,
        provider-specific values before a request is written. Empty for
        providers that declare no aliases.
        """
        return self.AGENT_SETTINGS_ALIASES

    def get_agent_settings_descriptions(self) -> dict[str, dict]:
        """Return the per-value human-readable descriptions.

        Shape: ``{field: {value: description}}``. Drives the
        ``description`` field of ``twicc info agent-settings``. Empty
        for providers that don't declare any.
        """
        return self.AGENT_SETTINGS_DESCRIPTIONS

    def get_agent_settings_constraints(self) -> dict[str, dict]:
        """Return the contextual constraints on agent-settings values.

        Shape: ``{field: {value: [supported_identifiers]}}``. Only fields
        and values that have a constraint appear in the output; values
        with no constraint are absent. Each ``supported_identifiers``
        list is the exhaustive set of model identifiers — for every
        :class:`ModelVersion` whose flag (per :attr:`CONSTRAINT_FLAG_MAPPING`)
        is true, the canonical ``<family>-<version>`` form is included,
        plus the bare alias ``<family>`` when the entry is the latest of
        its family.

        Providers without :attr:`CONSTRAINT_FLAG_MAPPING` entries inherit
        an empty result, signalling "no contextual constraints".
        """
        result: dict[str, dict] = {}
        for (field, value), flag in self.CONSTRAINT_FLAG_MAPPING.items():
            supported: list = []
            for mv in self.MODEL_VERSIONS:
                if getattr(mv.provider_extra, flag, False):
                    supported.append(f"{mv.model}-{mv.version}")
                    if mv.latest:
                        supported.append(mv.model)
            result.setdefault(field, {})[value] = supported
        return result

    def get_attachment_support(self) -> dict:
        """Return the attachment capabilities of this provider.

        Returned dict shape:
            {
                "images": bool,
                "documents": bool,
                "accepted_mime_types": list[str],
                "max_bytes_per_file": int,
                "max_files_per_message": int,
                "max_total_bytes": int,
            }
        """
        raise NotImplementedError

    def get_effective_image_dimension(
        self, model: str | None, num_images: int
    ) -> int:
        """Return the long-edge dimension cap (in px) for outgoing images.

        The CLI applies a single resize per image to this cap before
        base64-encoding. The default implementation caps at the shared
        :data:`MAX_IMAGE_DIMENSION` so every provider gets a sane "no
        bigger than X" rule. Providers with model-specific caps (e.g.
        Claude Code: 1568 for older models, 2000 with >20 images)
        override this method.
        """
        return MAX_IMAGE_DIMENSION

    def get_user_messages(
        self,
        items: Iterable[SessionItem],
        limit: int | None = None,
    ) -> list[UserMessage]:
        """Extract user messages with text from ``items``.

        ``items`` are session items already filtered to user messages and
        ordered by line number; the caller owns that fetch (it's a generic
        DB lookup), the helper owns the ``content`` parsing. ``limit``
        caps the number of returned entries when not ``None`` — useful
        e.g. to fetch only the first user message for title suggestion
        via ``get_user_messages(items, limit=1)``.
        """
        raise NotImplementedError

    def get_first_user_message(self, session_id: str) -> str | None:
        """Return the text of the first user message of ``session_id``, or ``None``.

        Combines the generic DB lookup (USER_MESSAGE filter, ordered by
        ``line_num``) with the provider-specific parsing exposed via
        :meth:`get_user_messages` (called with ``limit=1`` so we only
        pay for the first row). Sync method — wrap in
        :func:`sync_to_async` at the call site if needed.
        """
        from twicc.core.enums import ItemKind
        from twicc.core.models import SessionItem

        items = list(
            SessionItem.objects
            .filter(session_id=session_id, kind=ItemKind.USER_MESSAGE)
            .order_by("line_num")[:1]
        )
        messages = self.get_user_messages(items, limit=1)
        return messages[0].text if messages else None

    def get_indexable_messages(self, items: Iterable[SessionItem]) -> list[IndexableMessage]:
        """Extract indexable messages from ``items``.

        ``items`` are session items already filtered to user/assistant
        messages and ordered by line number.
        """
        raise NotImplementedError

    def extract_indexable_text(self, item: SessionItem) -> str:
        """Return the plain-text payload of ``item`` for full-text search indexing.

        The empty string is returned when the item is not a chat message,
        cannot be parsed, or carries no extractable text. Each provider
        parses its own native message shape (the ``content`` layout, the
        list-of-text-parts vs. plain-string convention, ...) and produces
        a single concatenated text string. Used both by the watcher's
        live indexing path and by the building blocks of
        :meth:`get_user_messages` / :meth:`get_indexable_messages`.
        """
        raise NotImplementedError

    def get_tool_results(
        self,
        items: Iterable[SessionItem],
        tool_use_id: str,
    ) -> list[dict]:
        """Return the tool_result payload entries for ``tool_use_id`` across ``items``.

        ``items`` are the session items already filtered to those known to
        carry the relevant tool_result lines (typically resolved by the
        caller via :class:`ToolResultLink`).
        """
        raise NotImplementedError

    def enrich_live_items_payload(self, session_id: str, items: list[dict]) -> None:
        """Mutate a freshly built ``session_items_added`` items list in place.

        Default is a no-op. Providers override this when they need to add
        wire-only metadata on top of the DB-derived serialization — e.g.
        Codex stamps ``stream_uuid`` so the frontend can retire its
        streaming placeholder, using an in-memory FIFO populated by the
        live agent. Wire-only because the value has no meaning outside
        the live broadcast (post-reload there is no placeholder to
        retire) and shouldn't pollute the DB.

        Called exactly once per WS broadcast in
        :func:`twicc.providers.sessions_watcher.BaseSessionsWatcher`'s
        item-broadcast path, after :func:`serialize_session_item`.
        """

    def serialize_model(self, model: str | None) -> dict | None:
        """Serialize a raw model identifier into ``{raw, family, version}``.

        Returns ``None`` for an empty input.
        """
        raise NotImplementedError

    async def generate_title(self, prompt: str, system_prompt: str) -> str | None:
        """Generate a session title suggestion from ``prompt`` and ``system_prompt``.

        ``system_prompt`` contains a ``{text}`` placeholder that the
        implementation replaces with ``prompt``. Default implementation
        returns ``None`` (provider has no title generation surface);
        providers override to call their own model — Claude Code runs
        a short Haiku query and Codex runs a short gpt-5.6-luna query,
        both via their respective SDKs.
        """
        return None

    def validate_title(self, title: str | None) -> TitleValidationResult:
        """Validate and normalize a user-supplied session title.

        Default implementation: trim, reject empty, cap at
        :attr:`MAX_TITLE_LENGTH` characters. Providers may override to
        apply their own format rules or stricter limits (the constant is
        a class attribute so a subclass needs only to rebind it for a
        different cap).
        """
        if title is None:
            return TitleValidationResult(title=None, error="Title cannot be empty")
        title = title.strip()
        if not title:
            return TitleValidationResult(title=None, error="Title cannot be empty")
        if len(title) > self.MAX_TITLE_LENGTH:
            return TitleValidationResult(
                title=None,
                error=f"Title must be {self.MAX_TITLE_LENGTH} characters or less",
            )
        return TitleValidationResult(title=title, error=None)

    async def rename_session(self, session_id: str, title: str) -> None:
        """Persist a new title in the provider's session storage.

        Writes to the provider-specific backing store (e.g. JSONL for
        Claude Code) and applies any provider-specific anti-stale-write
        protection so a CLI reappend cannot overwrite the user's choice.
        The DB row is handled by the caller.
        """
        raise NotImplementedError

    async def verify_session_title(self, session_id: str, expected_title: str) -> None:
        """Confirm the provider's store still holds ``expected_title``; re-set if not.

        Called by :meth:`BaseAgentManager._verify_pending_title_after_delay`
        a few seconds after a successful :meth:`rename_session`, as a guard
        against silent background overwrites by the provider's own process.

        The default implementation is a no-op: most providers either
        accept ``thread/name/set``-style writes atomically and never
        revisit them, or have an in-process anti-stale mechanism (Claude
        Code: :func:`protect_title` registered inside
        :meth:`ClaudeCodeHelpers.rename_session`). Codex needs a real
        implementation because its app-server can re-flush the
        ``threads.title`` row from an in-memory value derived from
        ``first_user_message`` shortly after our explicit set.

        Implementations should be idempotent and side-effect-free when the
        title is already correct.
        """
        return

    def purge_env_vars(self, env: dict) -> None:
        """Strip provider-specific env vars from ``env`` in place.

        Called when TwiCC is about to spawn a subprocess (login shell,
        tmux server, the CLI itself) that should not inherit a parent
        process's provider-specific environment — e.g. Claude Code's
        ``CLAUDE_CODE_*`` markers, which would make a freshly-launched
        instance think it's already inside an SDK session.

        The default is a no-op so a provider only pays for the keys it
        actually contributes; subclasses override.
        """
        return

    async def enrich_agent_state(self, message: dict, session_id: str) -> None:
        """Augment a serialised ``process_state`` / ``active_processes`` entry.

        Called by :mod:`twicc.asgi` for each agent record being broadcast,
        scoped to the provider that owns ``session_id`` (the dispatcher
        looks up the right helper from
        :class:`ProviderHelpersRegistry`). The default is a no-op so a
        provider only pays for the keys it actually contributes.

        Claude Code overrides this to attach ``active_crons`` from
        :class:`SessionCron`; other providers can hook their own
        provider-specific decoration here as the multi-provider surface
        grows.
        """
        return

    def should_keep_dead_process_run(
        self,
        process_run: Any,
        *,
        agent: Any = None,
    ) -> bool:
        """Decide whether a ``DEAD`` :class:`ProcessRun` row should survive cleanup.

        Synchronous because the typical implementation reads from the DB
        (e.g. ``process_run.crons.exists()``) — async callers wrap it in
        :func:`asyncio.to_thread`. Called from two sites:

        - **Runtime**, from :meth:`BaseAgentManager._on_state_change` when an
          agent transitions to ``DEAD`` (``agent`` is provided).
        - **Boot cleanup**, from :mod:`twicc.agent.process_run_cleanup`
          to consolidate stale rows from a previous TwiCC instance
          (``agent`` is ``None`` — only the row is available).

        The default returns ``False`` (always delete on death). Providers
        that want some rows to survive (Claude Code keeps DEAD rows that
        still have :class:`SessionCron` rows attached so the boot cron
        restart can pick them up) override to encode their rule.

        ``agent`` is typed ``Any`` so this signature does not pull
        :mod:`twicc.agent` into this module's import graph; providers that
        need to look at agent attributes (``kill_reason``,
        ``_first_user_turn_reached``, ...) cast or duck-type as needed.
        """
        return False

    async def try_handle_async_job(self, job, settle_async_job) -> bool:
        """Provider hook to handle a provider-specific async-queue job.

        Called by the DB writer's dispatch fallback (in
        :func:`twicc.providers.db_writer._dispatch_async_job`) when none of
        the generic job types matched. Iterates over every helper in the
        :class:`ProviderHelpersRegistry`; the first one that returns ``True``
        wins (the DB writer moves on), the others are skipped.

        ``settle_async_job`` is :func:`db_writer._settle_async_job` — the
        DB-writer helper that runs the sync apply in ``transaction.atomic``
        on a worker thread and resolves the job's ``future`` with the
        result, or with the raised exception so the producer sees real
        failures. Provider-specific job types are expected to follow the
        same shape as the generic R17 ones: a NamedTuple carrying a
        ``future: asyncio.Future`` field and a ``provider: Provider`` field
        (read for logging in ``_settle_async_job``).

        The default is a no-op so a provider only pays for the jobs it
        actually contributes. Override in the provider's helper to register
        its own job types, typically with an ``isinstance(job, X): await
        settle_async_job(...); return True`` ladder.
        """
        return False

    def get_bootstrap_data(self) -> dict:
        """Return provider-specific keys merged into the ``/api/bootstrap/`` payload.

        Default implementation contributes:

        - ``agent_settings_presets`` (cross-provider): the persisted
          presets file for this provider, read via
          :func:`twicc.agent_settings_presets.read_agent_settings_presets`.
          Always present — every provider has its own file (even if
          empty), so the front can render the presets dialog without
          guarding on the field's existence.
        - ``agent_settings_categories``: the live/idle/startup
          classification, used by the frontend to decide whether a
          settings change can be applied to a running process.
        - ``model_registry``: serialised :attr:`MODEL_VERSIONS` so the
          model dropdowns render uniformly across providers.
        - The usage block (``tracks_usage`` / ``usage``) when this
          provider declares a ``USAGE_SYNC_INTERVAL``:

          - ``tracks_usage``: ``True`` when the provider has a usage sync
            loop, so the front knows to allocate a slot for it even before
            a snapshot exists (auth not configured yet, first fetch still
            pending, etc.).
          - ``usage``: the latest serialised ``UsageSnapshot`` for this
            provider, or ``None`` when none exists yet. Mirrors the inner
            shape of the ``usage_updated`` WS payload.

        Subclasses with extra keys override and merge:
        ``super().get_bootstrap_data() | {"my_key": ...}``.
        """
        from twicc.agent_settings_presets import read_agent_settings_presets

        data: dict = {
            "agent_settings_presets": read_agent_settings_presets(self.provider),
            "agent_settings_categories": {
                category.value: keys
                for category, keys in self.AGENT_SETTINGS_CATEGORIES.items()
            },
            "agent_settings_choices": self.get_agent_settings_choices(),
            "agent_settings_aliases": self.get_agent_settings_aliases(),
            "attachment_support": self.get_attachment_support(),
            "model_registry": self.serialize_model_registry(),
        }

        if self.USAGE_SYNC_INTERVAL is None:
            return data

        from twicc.core.models import UsageSnapshot
        from twicc.core.serializers import serialize_usage_snapshot
        from twicc.usage import compute_period_costs
        from twicc.usage_task import _build_reference_snapshots

        snapshot = (
            UsageSnapshot.objects
            .filter(provider=self.provider.value)
            .first()  # ordered by -fetched_at
        )
        return data | {
            "tracks_usage": True,
            "usage": serialize_usage_snapshot(
                snapshot,
                period_costs=compute_period_costs(snapshot),
                references=_build_reference_snapshots(snapshot),
            ) if snapshot else None,
        }

    # ------------------------------------------------------------------
    # Model registry
    # ------------------------------------------------------------------

    def find_model(self, identifier: str) -> ModelVersion | None:
        """Look up a :class:`ModelVersion` in :attr:`MODEL_VERSIONS` by ``identifier``.

        Default implementation matches against ``ModelVersion.full_name``.
        Providers whose user-facing identifier diverges from the SDK
        full name (e.g. Claude Code uses aliases like ``"opus"`` and
        ``"opus-4.5"``) override this to handle their own semantics.
        """
        for mv in self.MODEL_VERSIONS:
            if mv.full_name == identifier:
                return mv
        return None

    def is_model_version_retired(self, mv: ModelVersion) -> bool:
        """Return ``True`` when ``mv`` is past its retirement date.

        The comparison is strict: a model stays usable *through* the whole
        retirement day and only dies the day after. Every surface that hides
        retired models — the pickers, ``info models``, ``info agent-settings``,
        the ``--model`` help and its validation — must agree on that boundary.
        """
        return mv.retirement_date is not None and date.today() > mv.retirement_date

    def is_model_retired(self, identifier: str) -> bool:
        """Return ``True`` when the model identified by ``identifier`` is past its retirement date."""
        mv = self.find_model(identifier)
        return mv is not None and self.is_model_version_retired(mv)

    def _model_available(self, mv: ModelVersion) -> bool:
        """Return ``True`` when ``mv`` is usable: enabled and not retired."""
        if not mv.enabled:
            return False
        return not self.is_model_version_retired(mv)

    def resolve_to_available_model(self, identifier: str) -> str:
        """Resolve ``identifier`` to the closest available model by weight.

        A model is *available* when it is enabled and not past its
        retirement date. If ``identifier`` is already available — or is
        unknown to this provider — it is returned unchanged. Otherwise
        we pick the available model whose ``weight`` is closest to the
        unavailable model's weight: the nearest one above and the
        nearest one below are compared by absolute weight distance, the
        closer wins, and an exact tie is broken in favour of the
        higher-weight (stronger) model.

        This single rule subsumes both retirement (a model retires on a
        date) and explicit disabling (``enabled=False``): both make a
        model unavailable, and both fall through to the same
        nearest-by-weight substitution, which may cross model families.
        """
        if not identifier:
            return identifier
        mv = self.find_model(identifier)
        if mv is None or self._model_available(mv):
            return identifier
        above: ModelVersion | None = None
        below: ModelVersion | None = None
        for cand in self.MODEL_VERSIONS:
            if cand.provider != mv.provider or not self._model_available(cand):
                continue
            if cand.weight > mv.weight:
                if above is None or cand.weight < above.weight:
                    above = cand
            elif cand.weight < mv.weight:
                if below is None or cand.weight > below.weight:
                    below = cand
        if above is None and below is None:
            logger.warning("No available fallback for unavailable model '%s'", identifier)
            return identifier
        if above is None:
            pick = below
        elif below is None:
            pick = above
        else:
            pick = above if (above.weight - mv.weight) <= (mv.weight - below.weight) else below
        return self.selected_model_value(pick)

    def sdk_model_safety_net(self, selected_model: str) -> str:
        """Last-resort substitution right before a model reaches the SDK.

        Every caller is expected to have run
        :meth:`enforce_agent_settings_consistency` first, so an unavailable
        (disabled or retired) model is normally already substituted. This is
        defense in depth for the one boundary that must never leak — the
        actual ``model=`` argument handed to the provider SDK/CLI: a
        provider's ``resolve_sdk_model`` calls this so a disabled/retired
        model can never be sent verbatim, even from a future call site that
        forgot to normalise. When the net fires it logs a warning, because a
        substitution here means an upstream call site built the SDK model
        without normalising — a contract gap worth surfacing.
        """
        resolved = self.resolve_to_available_model(selected_model)
        if resolved != selected_model:
            logger.warning(
                "SDK model resolution received unavailable model '%s' (not "
                "normalised upstream); substituting '%s'",
                selected_model, resolved,
            )
        return resolved

    def enforce_synced_settings_consistency(self, synced: dict, changes: dict) -> None:
        """Apply this provider's rules to the merged synced settings dict.

        Called once per provider after the WS handler has merged a
        client update into the synced settings store. ``synced`` is
        the full merged dict, mutated in place. ``changes`` is the
        subset of keys the client just sent in this request.

        Implementations must:

        - Use ``changes`` as the trigger to decide whether to fire
          (typically: only run when the provider's pivotal key — the
          one that, when changed, can invalidate sibling fields — is
          in ``changes``).
        - Only write back keys that are present in ``changes``.
          Overwriting a key the client did not include in the update
          would silently mutate state the client didn't ask to touch
          and is unsafe under optimistic concurrency.

        The base implementation is a no-op; providers that own
        synced-settings rules override.
        """
        return

    def enforce_agent_settings_consistency(self, settings: AgentSettings) -> AgentSettings:
        """Return ``settings`` normalised against this provider's rules.

        Generic call sites (consumer, cron restart, retirement task)
        invoke this once on a settings bundle to make every field
        mutually valid given the chosen model. The base implementation
        only enforces the cross-provider rule that's always meaningful:
        if ``selected_model`` is unavailable (disabled or retired),
        substitute it with the nearest-by-weight available model via
        :meth:`resolve_to_available_model`. Providers whose models carry
        capability flags override this to add their own rules
        (typically: call ``super()`` for the substitution, then demote
        ``context_max`` / ``effort`` against the resolved model's
        capabilities).
        """
        selected = settings.selected_model
        if selected:
            resolved = self.resolve_to_available_model(selected)
            if resolved != selected:
                settings = settings._replace(selected_model=resolved)
        return settings

    def selected_model_value(self, mv: ModelVersion) -> str:
        """Return the round-trip identifier for ``mv``.

        This is the value carried in ``Session.selected_model``, in WS
        ``send_message`` payloads, and in the front's model dropdowns.
        Each provider's helpers can override to fit its own naming
        convention; the default — ``mv.model`` for the latest version
        of a family, ``"{model}-{version}"`` otherwise — is what most
        providers will want.

        The point of having a single ``selected_model`` field on every
        serialised entry is that the front consumes the registry the
        same way regardless of provider.
        """
        return mv.model if mv.latest else f"{mv.model}-{mv.version}"

    def serialize_model_extra(self, mv: ModelVersion) -> dict:
        """Return the serialised form of ``mv.provider_extra``.

        Default implementation returns an empty dict (provider has no
        extra fields). Providers whose ``provider_extra`` carries
        capability flags or other metadata override to expose them on
        the wire — e.g. Claude Code returns the ``supports_*`` flags.
        """
        return {}

    def serialize_model_registry(self) -> list[dict]:
        """Serialize :attr:`MODEL_VERSIONS` for the bootstrap payload.

        Each entry is the ``_asdict()`` of the :class:`ModelVersion``
        (so ``weight`` / ``enabled`` / ``disable_reason`` ride along
        automatically) with ``retirement_date`` rendered as an ISO date
        string. ``provider_extra`` is replaced by the dict produced by
        :meth:`serialize_model_extra` so each provider controls the
        wire shape of its own extras. A ``selected_model`` field — the
        round-trip identifier produced by :meth:`selected_model_value`
        — is added on every entry so the front can consume the
        registry uniformly across providers. ``provider`` is kept on
        each entry so consumers can treat the entry as self-describing
        even when it travels outside the per-provider nesting.

        Entries are sorted by ``weight`` descending — strongest first —
        the natural order for a model-picker dropdown. The front may
        re-group (e.g. latest vs. older) but always preserves this
        within-group order.
        """
        entries: list[dict] = []
        for mv in self.MODEL_VERSIONS:
            entry = mv._asdict()
            entry["provider"] = mv.provider.value
            entry["retirement_date"] = (
                mv.retirement_date.isoformat() if mv.retirement_date else None
            )
            entry["provider_extra"] = self.serialize_model_extra(mv)
            entry["selected_model"] = self.selected_model_value(mv)
            entries.append(entry)
        entries.sort(key=lambda e: -e["weight"])
        return entries


class ProviderHelpersRegistry:
    """Singleton holding one :class:`BaseProviderHelpers` per provider.

    Mirrors :class:`twicc.agent.registry.AgentManagerRegistry` and
    ``twicc.asgi.WSConsumer.PROVIDER_HANDLERS``: providers are declared
    statically as a class attribute and instantiated once when the
    singleton is created.
    """

    PROVIDER_HELPERS: ClassVar[dict[Provider, type[BaseProviderHelpers]]]

    def __init__(self) -> None:
        # Imported here to avoid a circular import at module load time:
        # each provider helpers module imports from this one.
        from twicc.providers.claude_code.helpers import ClaudeCodeHelpers
        from twicc.providers.codex.helpers import CodexHelpers

        self.PROVIDER_HELPERS = {
            Provider.CLAUDE_CODE: ClaudeCodeHelpers,
            Provider.CODEX: CodexHelpers,
        }
        self._helpers: dict[Provider, BaseProviderHelpers] = {
            key: cls() for key, cls in self.PROVIDER_HELPERS.items()
        }

    def get(self, provider: Provider) -> BaseProviderHelpers:
        """Return the helpers instance for ``provider``."""
        return self._helpers[provider]

    def items(self) -> list[tuple[Provider, BaseProviderHelpers]]:
        """Return ``(provider, helpers)`` pairs for every registered provider."""
        return list(self._helpers.items())

    def values(self) -> list[BaseProviderHelpers]:
        """Return the helpers instances for every registered provider."""
        return list(self._helpers.values())

    def enforce_synced_settings_consistency(self, synced: dict, changes: dict) -> None:
        """Run every provider's :meth:`BaseProviderHelpers.enforce_synced_settings_consistency`.

        ``synced`` is the merged synced settings dict (mutated in place by each
        provider that has rules to apply). ``changes`` is the subset of keys the
        client sent in this update so each provider can short-circuit when none
        of its keys changed.
        """
        for helpers in self._helpers.values():
            helpers.enforce_synced_settings_consistency(synced, changes)

    def purge_env_vars(self, env: dict) -> None:
        """Strip every provider's provider-specific env vars from ``env`` in place.

        Called by the CLI before its own ``django.setup()`` and by the
        terminal spawner before exec-ing a shell or tmux: the goal is
        that no subprocess inherits provider markers from the parent
        TwiCC process, so a newly launched provider CLI starts clean.
        Each provider's helper decides what its markers are.
        """
        for helpers in self._helpers.values():
            helpers.purge_env_vars(env)


_registry: ProviderHelpersRegistry | None = None


def get_provider_helpers_registry() -> ProviderHelpersRegistry:
    """Return the global :class:`ProviderHelpersRegistry` (lazy-initialized)."""
    global _registry
    if _registry is None:
        _registry = ProviderHelpersRegistry()
    return _registry


def get_provider_helpers(provider: Provider | str) -> BaseProviderHelpers:
    """Return the :class:`BaseProviderHelpers` for ``provider``.

    Accepts either a :class:`Provider` enum value or its string form (the
    ``Session.provider`` field is stored as a string).
    """
    if isinstance(provider, str):
        provider = Provider(provider)
    return get_provider_helpers_registry().get(provider)
