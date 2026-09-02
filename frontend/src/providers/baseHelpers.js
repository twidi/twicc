import { effortIconSrc } from '../utils/effortIcon'

/**
 * Render a registry entry's ``retirement_date`` (an ISO day string) as a short
 * localised date, for the "(until DATE)" decoration in the model pickers.
 * Shared because every provider that dates its models needs the same wording.
 */
export function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}

/**
 * Today in the local timezone as an ISO day string. Registry retirement dates
 * are ISO days too, so comparing the two is a plain lexicographic compare —
 * no ``Date`` arithmetic, hence no timezone drift around midnight.
 */
function todayIsoDay() {
    const now = new Date()
    const month = String(now.getMonth() + 1).padStart(2, '0')
    const day = String(now.getDate()).padStart(2, '0')
    return `${now.getFullYear()}-${month}-${day}`
}

/**
 * Base class for per-provider frontend helpers.
 *
 * Mirrors the backend `BaseProviderHelpers` pattern: each provider ships a
 * subclass that overrides only the behaviours that differ from the neutral
 * defaults defined here. Generic frontend code resolves the right instance
 * via the registry in `providers/index.js` and never branches on provider
 * identity itself.
 */
export class BaseProviderHelpers {
    static provider = null
    static label = null
    static icon = null
    // Brand tint for the provider icon (any CSS colour). null → inherit.
    static iconColor = null
    // Upstream service status, for the "<vendor> status update" toast and the
    // Settings footer. ``serviceProductLabel`` names what is down from the
    // user's side ("Claude Code" — ``label`` alone may be shorter),
    // ``serviceVendorLabel`` who runs it ("Anthropic"), ``serviceStatusUrl``
    // the public status page. All null → the provider publishes no status.
    static serviceProductLabel = null
    static serviceVendorLabel = null
    static serviceStatusUrl = null

    /**
     * Whether the current frontend state allows sending a message to a
     * session of this provider. Default: always allowed. Providers override
     * to gate on auth, quota, or any other prerequisite.
     */
    canSendMessage() {
        return true
    }

    /**
     * Whether the host can ask the backend to stop a running background
     * subagent of this provider — gates the Stop button next to ``View
     * Agent`` on the spawning tool card AND the Stop button in the
     * subagent's session header. A provider opts out (returning
     * ``false``) when it has not (or cannot) wired the matching
     * ``BaseAgentManager.stop_subagent`` hook yet, so the UI doesn't
     * dispatch a ``stop_subagent`` request that the backend would
     * silently drop. Default: ``true`` (Claude Code's ``Task`` ships
     * with a working stop path).
     */
    canStopSubagent() {
        return true
    }

    /**
     * Whether the host can ask the backend to interrupt this provider's
     * current turn WITHOUT killing the session — gates the interrupt button
     * shown next to the Stop button in the session header during
     * ``assistant_turn``. A provider opts in (returning ``true``) only once
     * it has wired the matching ``BaseAgent.soft_interrupt`` hook, so the UI
     * doesn't dispatch an ``interrupt_session`` request the backend would
     * silently drop. Default: ``false`` (only the Claude Code SDK runtime
     * ships a working soft-interrupt today).
     */
    canInterruptTurn() {
        return false
    }

    /**
     * Activation prefixes this provider supports, in the order the snippets
     * bar should render their buttons. Each char drives one ``open-command``
     * button and one ``/api/projects/<id>/commands/?activation_char=<char>``
     * fetch when the picker opens. Providers that don't expose a command
     * vocabulary return ``[]`` (default). For example Claude Code returns
     * ``['/']``; Codex will eventually return ``['/', '$']``.
     */
    getCommandActivationChars() {
        return []
    }

    /**
     * Provider-specific note appended to the placeholder shown during
     * ``assistant_turn`` (after the generic "you can send a message now…"
     * sentence). Returns null when no note is needed. Example: Claude Code
     * appends a warning that the message won't appear in the conversation
     * history (the SDK queues it as in-band signal rather than persisting
     * it); providers that persist mid-turn messages (Codex via
     * ``turn/steer``) leave this null.
     */
    getPlaceholderAssistantTurnNote() {
        return null
    }

    /**
     * Built-in commands hardcoded by the provider's runtime/CLI for a given
     * activation prefix. Merged with the user-defined commands fetched from
     * the backend by the picker. The ``activationChar`` argument lets a
     * provider that exposes several prefixes (e.g. Codex's ``/`` and ``$``)
     * return a different list per prefix. Default: no built-ins.
     */
    getBuiltInCommands(/* activationChar */) {
        return []
    }

    /**
     * Build the parsed-content payload for the synthetic "optimistic" user
     * message — the bubble displayed in the chat the instant a user clicks
     * Send, before any backend round-trip. Provider-specific because the
     * item dispatcher in ``SessionItem.vue`` hands off to the provider's
     * own message renderer, which expects its own native JSONL shape (e.g.
     * Claude Code reads ``data.message.content[]``, Codex reads
     * ``data.payload.message``).
     *
     * Implementations return a plain object suitable for ``setParsedContent``
     * — it must include the ``syntheticKind`` marker
     * (``SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind``) so visual-item
     * stabilization and cleanup (``recomputeVisualItems`` /
     * ``computeVisualItems``) treat it correctly.
     *
     * @param {string} text - The text the user just submitted (already trimmed).
     * @param {{ images?: Array, documents?: Array } | undefined} attachments
     *        Attachment blocks in SDK format. Providers that don't support
     *        attachments may safely ignore the argument.
     */
    buildOptimisticUserMessageContent(/* text, attachments */) {
        throw new Error(
            `buildOptimisticUserMessageContent() not implemented for provider ${this.constructor.provider}`,
        )
    }

    /**
     * Extract the user-facing text from a parsed user_message item, using
     * this provider's native JSONL shape. Used by generic frontend surfaces
     * that need to compare or preview user messages without knowing the
     * provider protocol.
     *
     * Providers that render user_message items or support optimistic messages
     * should override this. Return ``null`` when the parsed item is not a
     * user message or contains no text.
     *
     * @param {Object} parsed - Parsed session item content.
     * @returns {string|null}
     */
    extractUserMessageText(/* parsed */) {
        return null
    }

    /**
     * Count the attachments (images, documents) carried by a parsed
     * user_message item, in this provider's native JSONL shape. A message
     * made only of attachments has no text, so text comparison cannot
     * identify it — the count is what the generic surfaces match on instead
     * (optimistic bubble replacement, in-flight send resolution).
     *
     * @param {Object} parsed - Parsed session item content.
     * @returns {number}
     */
    extractUserMessageAttachmentCount(/* parsed */) {
        return 0
    }

    // ─── Authentication ──────────────────────────────────────────────────
    //
    // Some providers gate sending on an external authentication step (e.g.
    // a CLI login). The frontend surfaces that state via a persistent toast
    // and the sidebar callout; both consume the hooks below. A provider
    // that doesn't authenticate returns ``null`` from ``getAuthState`` and
    // the surface skips it entirely.

    /**
     * Getter function that returns the current auth state for this provider:
     * ``true`` (authenticated), ``false`` (not authenticated) or ``null``
     * (still unknown — no backend push received yet). Returned as a getter
     * (not a ``ref``) so callers can hand it straight to ``watch``; Pinia
     * unwraps refs read through the store, so the getter form is the only
     * way to keep reactivity across the helpers boundary.
     * Default: ``null`` (provider doesn't gate on auth — return ``null`` for
     * the helper itself, not a getter).
     */
    getAuthState() {
        return null
    }

    /**
     * Shell command the user must run to authenticate with this provider,
     * already prefixed by the configured TwiCC launch prefix. Used by the
     * persistent auth toast and the sidebar callout. Default: ``null``.
     */
    getAuthLoginCommand() {
        return null
    }

    /**
     * Ask the backend to re-check this provider's auth state right now,
     * bypassing the periodic poll. Bound to the toast / sidebar "Check
     * again" buttons. Default: no-op.
     */
    requestAuthRecheck() {}

    // ─── Usage quota surface ─────────────────────────────────────────────
    //
    // Providers that expose a quota / usage payload (consumed by the
    // sidebar quota block, the usage graph dialog, …) opt in by returning
    // ``true`` from ``tracksUsage`` and pointing ``getUsageExternalLink``
    // at their canonical external dashboard.

    /**
     * Whether this provider exposes a usage payload via its store. The
     * sidebar's rotation only includes providers that return ``true``.
     * Default: not tracked.
     */
    tracksUsage() {
        return false
    }

    /**
     * External link (provider's own usage dashboard) surfaced in the
     * stale-data tooltip of the sidebar quota block. Returns
     * ``{ url, label }`` or ``null`` when the provider doesn't ship one.
     * Default: ``null``.
     */
    getUsageExternalLink() {
        return null
    }

    /**
     * Whether this provider offers a user-initiated usage refresh — the
     * "Refresh now" button in the sidebar quota block's stale-data tooltip.
     * Providers that return ``true`` must also override
     * ``requestUsageRefresh``. Default: not supported.
     */
    supportsUsageRefresh() {
        return false
    }

    /**
     * Ask the backend to refresh this provider's usage snapshot right now,
     * allowing the on-demand OAuth token refresh that the macOS background
     * loop skips. Bound to the "Refresh now" button. Default: no-op.
     */
    async requestUsageRefresh() {}

    // ─── Usage read/dump file settings ───────────────────────────────────
    //
    // Providers that track usage may also support sourcing the data from
    // a JSON file (read mode) or persisting the API response to one
    // (dump mode). The Settings popover loops over every provider that
    // ``tracksUsage()`` and reads/writes those fields through the hooks
    // below — each provider proxies them onto its own store, so the on-
    // disk format and synced settings stay namespaced per provider while
    // the UI is generic.
    //
    // Field names: ``read_enabled`` / ``read_path`` / ``dump_enabled`` /
    // ``dump_path``. Read and dump are mutually exclusive: a setter that
    // switches one to ``true`` must clear the other (the provider's
    // store enforces this).

    /**
     * Read this provider's persisted value for a usage-file field.
     * Returns ``null`` when the provider doesn't support usage files.
     * Default: not supported.
     */
    getUsageFileSetting(/* field */) {
        return null
    }

    /**
     * Write this provider's persisted value for a usage-file field.
     * Type validation is the store's responsibility — invalid values
     * are silently ignored. Default: no-op.
     */
    setUsageFileSetting(/* field, value */) {}

    // ─── Daily quota warm-up ─────────────────────────────────────────────
    //
    // Providers with a rolling 5-hour subscription quota can warm it up at a
    // user-chosen daily time (see ``twicc.quota_wakeup_task``). The Settings
    // popover renders the hour/minute selectors only for providers that
    // return ``true`` from ``supportsQuotaWakeup`` and reads/writes the
    // ``"HH:MM"`` value (empty = disabled) through the hooks below.

    /** Whether this provider supports the daily quota warm-up. Default: no. */
    supportsQuotaWakeup() {
        return false
    }

    /** Read the warm-up time as ``"HH:MM"`` (empty = disabled). Default: ``''``. */
    getQuotaWakeupTime() {
        return ''
    }

    /** Persist the warm-up time as ``"HH:MM"`` (empty = disabled). Default: no-op. */
    setQuotaWakeupTime(/* value */) {}

    // ─── Service status surface (Statuspage / health) ───────────────────
    //
    // Providers whose backend service publishes a public status (e.g.
    // Anthropic's statuspage for Claude Code) opt in by returning a
    // reactive getter from ``getServiceStatus`` and a display descriptor
    // from ``getServiceStatusDisplay``. The Settings popover footer
    // rotates across providers that expose one (same pattern as the
    // sidebar usage quota rotation).

    /**
     * Getter function returning the current service status string for
     * this provider, or ``null`` (still unknown). Returned as a getter
     * (not a ``ref``) so callers can hand it straight to ``watch`` /
     * ``computed`` while keeping reactivity across the helpers boundary.
     * Default: ``null`` — provider has no service status surface.
     */
    getServiceStatus() {
        return null
    }

    /**
     * Render-side descriptor for a status value. Returns
     * ``{ label, modifier, url, tooltip }`` or ``null`` when the provider
     * doesn't expose a status surface or doesn't recognise the value.
     *
     * - ``label``: human-readable status name (e.g. "Operational")
     * - ``modifier``: one of ``ok`` / ``warning`` / ``error`` / ``info``,
     *   used by the footer to colour the dot
     * - ``url``: external link to the public status page
     * - ``tooltip``: hover text (e.g. "Claude Code status on Anthropic's side")
     *
     * The provider identifier itself is shown via ``getProviderIcon`` in
     * ``providers/index.js`` (rendered as ``<wa-icon>``), so the descriptor
     * does not carry a per-provider label here.
     *
     * Default: ``null``.
     */
    getServiceStatusDisplay(/* status */) {
        return null
    }

    /**
     * Store the bare upstream status value pushed by the backend's
     * ``providers_status_updated`` frame, for ``getServiceStatus`` to expose.
     * Default: no-op — provider has no service status surface.
     */
    applyServiceStatus(/* status */) {}

    // ─── Per-provider default values for agent settings ──────────────────
    //
    // Generic surfaces that need to read or write the provider-scoped
    // default for a given agent setting field (the Settings popover's
    // provider section, the static palette commands, …) go through these
    // hooks instead of poking the provider store directly. Each provider
    // overrides them with its own field → store-binding mapping.

    /**
     * Read this provider's persisted default for ``field`` (the wire-name,
     * e.g. ``selected_model``). Returns ``null`` when the provider doesn't
     * own the field. Default: no defaults exposed.
     */
    getDefaultValue(/* field */) {
        return null
    }

    /**
     * Write this provider's persisted default for ``field``. Validation
     * (type, enum membership) is the store's responsibility — invalid
     * values are silently ignored. Default: no-op.
     */
    setDefaultValue(/* field, value */) {}

    // ─── Synced settings ownership ───────────────────────────────────────
    //
    // The settings store hosts a single localStorage blob and a single
    // outgoing payload to the backend, but each provider declares which
    // keys it owns. The orchestrator dispatches incoming/outgoing values
    // through ``applySyncedSettings`` / ``getSyncedSettings`` so the actual
    // state lives next to the rest of the provider's state.

    /**
     * Synced setting keys this provider owns. The orchestrator filters
     * incoming localStorage / WS payloads down to this set before
     * delegating. Default: provider owns no synced setting.
     */
    getSyncedSettingsKeys() {
        return []
    }

    /**
     * Apply a subset of synced settings (matching ``getSyncedSettingsKeys``)
     * to this provider's state. The dict may contain extra keys that are
     * not owned by this provider — they should be ignored. Default: no-op.
     */
    applySyncedSettings(/* settings */) {}

    /**
     * Read this provider's current synced settings as a flat dict, ready
     * to be merged into the localStorage blob and the outgoing payload.
     * Default: no settings exposed.
     */
    getSyncedSettings() {
        return {}
    }

    // ─── Per-session agent settings classification ───────────────────────
    //
    // The agent settings bundle is a CLOSED set of fields shared by every
    // provider: ``selected_model``, ``effort``, ``thinking_enabled``,
    // ``permission_mode``, ``context_max``, ``claude_in_chrome``,
    // ``fast_mode``. The
    // bundle — and the matching ``Session`` row, the WS payload, and the
    // localStorage synced settings — has the same shape regardless of
    // which provider owns the running session. Each provider declares
    // which fields it actually uses by listing them in
    // ``getAgentSettingsCategories``; every other field is silently
    // ignored by that provider. New provider-specific session-level flags
    // follow the same pattern: add the column to ``Session`` and classify
    // it in the owning provider's categories — never split off into a
    // per-provider side table. See the matching backend comment on
    // ``Session.claude_in_chrome`` in ``src/twicc/core/models.py``.
    //
    // Each provider classifies its per-session agent settings (model,
    // effort, etc.) into ``live`` / ``idle`` / ``startup`` buckets so the
    // agent manager knows when a change can be applied to a running
    // process. Mirrors the backend ``BaseProviderHelpers.AGENT_SETTINGS_CATEGORIES``.

    /**
     * Classification of this provider's per-session agent setting fields.
     * Shape: ``{ live: [...], idle: [...], startup: [...] }``. Default:
     * no classified fields.
     */
    getAgentSettingsCategories() {
        return { live: [], idle: [], startup: [] }
    }

    /**
     * Classify the diff between two settings dicts, grouping each changed
     * key under its category. Returns ``{ live, idle, startup }``.
     */
    classifyAgentSettingsChanges(current, requested) {
        const result = { live: [], idle: [], startup: [] }
        for (const [category, fields] of Object.entries(this.getAgentSettingsCategories())) {
            for (const field of fields) {
                if (current[field] !== requested[field]) result[category].push(field)
            }
        }
        return result
    }

    // ─── Agent settings consistency ──────────────────────────────────────
    //
    // Single point of truth for the rules that keep an agent settings bundle
    // self-consistent: the model can rule out certain ``contextMax`` /
    // ``effort`` values, retired models get auto-upgraded, etc. Mirrors the
    // backend ``BaseProviderHelpers.enforce_agent_settings_consistency``.
    // Generic call sites pass an effective settings dict (defaults already
    // applied) and apply the diff between input and output.

    /**
     * Return ``settings`` normalised against this provider's rules.
     *
     * The argument is a plain object using the frontend's camelCase field
     * names: ``selectedModel``, ``contextMax``, ``effort``, ``thinkingEnabled``,
     * ``claudeInChrome``, ``permissionMode`` (any subset). Returns a new
     * object with the same shape — call sites compare to detect what changed.
     *
     * Default: substitute an unavailable (disabled or retired)
     * ``selectedModel`` with the nearest-by-weight available model (see
     * ``resolveToAvailableModel``). Providers with capability flags override
     * to chain ``super`` then demote other fields against the resolved model.
     */
    enforceAgentSettingsConsistency(settings) {
        const result = { ...settings }
        if ('selectedModel' in result) {
            result.selectedModel = this.resolveToAvailableModel(result.selectedModel)
        }
        return result
    }

    /**
     * Whether a model registry entry is past its retirement date. Mirrors the
     * backend ``is_model_retired``: the model stays usable *through* the whole
     * retirement day, so the comparison is strict.
     *
     * Distinct from ``enabled === false`` on purpose. A disabled model is
     * unavailable for now and may come back (Fable 5 was), so the pickers keep
     * it listed and greyed out. A retired one never returns, so they drop it.
     */
    isModelRetired(entry) {
        if (!entry?.retirement_date) return false
        return todayIsoDay() > entry.retirement_date
    }

    /**
     * Whether a model registry entry is usable: enabled and not past its
     * retirement date. Mirrors the backend ``_model_available``.
     */
    isModelAvailable(entry) {
        if (!entry) return false
        if (entry.enabled === false) return false
        return !this.isModelRetired(entry)
    }

    /**
     * Resolve ``selectedModel`` to the closest available model by weight,
     * mirroring the backend ``resolve_to_available_model``. An available (or
     * unknown) model is returned unchanged. Otherwise the nearest available
     * model above and below by ``weight`` are compared by absolute distance,
     * the closer wins, and an exact tie favours the higher-weight model. This
     * single rule covers both disabled and retired models.
     */
    resolveToAvailableModel(selectedModel) {
        if (!selectedModel) return selectedModel
        const registry = this.getModelRegistry?.() ?? []
        const mv = registry.find(e => e.selected_model === selectedModel)
        if (!mv || this.isModelAvailable(mv)) return selectedModel
        let above = null
        let below = null
        for (const cand of registry) {
            if (cand.provider !== mv.provider || !this.isModelAvailable(cand)) continue
            if (cand.weight > mv.weight) {
                if (!above || cand.weight < above.weight) above = cand
            } else if (cand.weight < mv.weight) {
                if (!below || cand.weight > below.weight) below = cand
            }
        }
        if (!above && !below) return selectedModel
        let pick
        if (!above) pick = below
        else if (!below) pick = above
        else pick = (above.weight - mv.weight) <= (mv.weight - below.weight) ? above : below
        return pick.selected_model
    }

    // ─── Per-field choice catalogue ──────────────────────────────────────
    //
    // Each provider declares the valid values + human labels for the agent
    // setting fields it owns (everything except ``selected_model``, which is
    // served separately via the model registry). Choice entries follow the
    // shape ``{ value, label, display_label?, description? }``. Generic call
    // sites pull lists from ``getFieldChoices(field)`` and lookups from
    // ``getChoiceLabel`` / ``getChoiceDisplayLabel``.

    /**
     * Map from agent setting field name (snake_case wire name) to its list
     * of choice entries. Default: empty (provider declares no choices).
     */
    getAgentSettingsChoices() {
        return {}
    }

    /**
     * Permission modes allowed for sessions in an UNTRUSTED (or
     * unknown-trust) project. Empty means the provider has no trust-based
     * restriction (and the ``permission_mode_if_untrusted`` pseudo-field is
     * unsupported). Mirrors the backend ``UNTRUSTED_PERMISSION_MODES``.
     */
    getUntrustedPermissionModes() {
        return []
    }

    /**
     * Return the list of choice entries for ``field``, or ``[]`` when the
     * provider doesn't declare any. Order is preserved (used to drive the
     * select option order).
     *
     * ``permission_mode_if_untrusted`` is a default-shaping pseudo-field (not
     * part of the closed bundle / Session columns): it reuses the
     * ``permission_mode`` catalogue filtered down to the untrusted-allowed
     * set. See docs/plans/2026-06-09-project-trust-design.md §13.1.
     */
    getFieldChoices(field) {
        if (field === 'permission_mode_if_untrusted') {
            const allowed = this.getUntrustedPermissionModes()
            return (this.getAgentSettingsChoices().permission_mode ?? [])
                .filter(choice => allowed.includes(choice.value))
        }
        return this.getAgentSettingsChoices()[field] ?? []
    }

    /**
     * Return the ``label`` of the choice for ``field`` whose ``value``
     * matches ``value`` strictly (``===``). Returns ``null`` when nothing
     * matches — call sites can fall back to a stringified value if they
     * want to surface the raw value.
     */
    getChoiceLabel(field, value) {
        const choice = this.getFieldChoices(field).find(c => c.value === value)
        return choice?.label ?? null
    }

    /**
     * Like ``getChoiceLabel`` but prefers ``display_label`` when present —
     * used by compact summary strips where ``label`` would be too long.
     */
    getChoiceDisplayLabel(field, value) {
        const choice = this.getFieldChoices(field).find(c => c.value === value)
        return choice?.display_label ?? choice?.label ?? null
    }

    /**
     * Return ``{ icon, color }`` for the choice of ``field`` matching
     * ``value``, or ``null`` when the choice has no icon (or nothing matches).
     * Drives the per-mode glyph in the permission selects; generic so any
     * future icon-bearing choice field works the same way.
     */
    getChoiceIcon(field, value) {
        const choice = this.getFieldChoices(field).find(c => c.value === value)
        return choice?.icon ? { icon: choice.icon, color: choice.color ?? null } : null
    }

    /**
     * Whether this provider supports the agent setting ``field``. Derived
     * from ``getAgentSettingsCategories``: a field listed in any category
     * (live/idle/startup) is supported. UI components branch on this to
     * hide selects/commands the provider doesn't own.
     */
    supportsAgentSetting(field) {
        if (field === 'permission_mode_if_untrusted') {
            return this.supportsAgentSetting('permission_mode')
                && this.getUntrustedPermissionModes().length > 0
        }
        const categories = this.getAgentSettingsCategories()
        for (const fields of Object.values(categories)) {
            if (fields.includes(field)) return true
        }
        return false
    }

    // ─── Model label formatter ───────────────────────────────────────────
    //
    // Generic call sites (toasts, preset summaries, command palette) need a
    // human-readable label for a ``selected_model`` value. Each provider
    // overrides with its own formatting; the neutral default returns the raw
    // value (or ``''`` when unset) so a missing override still renders
    // something readable.

    getModelLabel(selectedModel) {
        return selectedModel ?? ''
    }

    /**
     * Short model label for compact grid rows (the agent-settings matrix): the
     * bare tier name without provider noise. Defaults to ``getModelLabel``;
     * providers whose full label repeats the family (Codex "GPT sol") override
     * to strip it ("Sol").
     */
    getModelShortLabel(selectedModel) {
        return this.getModelLabel(selectedModel)
    }

    /**
     * Version-qualified model label — always includes the version, even for a
     * latest bare alias whose ``getModelLabel`` omits it (e.g. "opus" → "Opus
     * 4.8"). Used where the user needs the exact resolved model, notably the
     * unavailable-model fallback callout.
     */
    getModelLabelWithVersion(selectedModel) {
        if (!selectedModel) return ''
        const entry = (this.getModelRegistry?.() ?? []).find(e => e.selected_model === selectedModel)
        const base = this.getModelLabel(selectedModel)
        return entry?.latest ? `${base} ${entry.version}` : base
    }

    /**
     * Warning text shown above a model select when ``storedModel`` is no longer
     * available (disabled or retired) and resolves to a different model. Names
     * both the unavailable model and the effective fallback, each with its
     * version, so the user knows exactly which model went away and what will
     * run instead. Returns null when the model is available, empty, or unknown
     * (nothing to warn about).
     */
    getModelFallbackNotice(storedModel) {
        if (!storedModel) return null
        const resolved = this.resolveToAvailableModel(storedModel)
        if (resolved === storedModel) return null
        return `${this.getModelLabelWithVersion(storedModel)} is no longer available `
            + `— falling back to ${this.getModelLabelWithVersion(resolved)}.`
    }

    // ─── Runtime context-max resolution ──────────────────────────────────
    //
    // Unlike ``enforceAgentSettingsConsistency`` (which is rules-only over a
    // settings dict), this hook can read live session state — typically
    // ``context_usage`` — so a provider can promote the effective window
    // when the persisted value would be too small. ``overrideModel`` lets
    // callers preview the value for a model that hasn't been saved yet.
    //
    // Default: return the session's persisted ``context_max`` as-is.

    getEffectiveContextMax(session, /* overrideModel */ _overrideModel) {
        return session?.context_max ?? null
    }

    // Whether the provider's auto-promote rule would kick in for the given
    // (session, contextMax, model) triple. Distinct from
    // ``getEffectiveContextMax`` because callers (notably the popover) need to
    // evaluate the rule against the user's current SELECTION, not against the
    // persisted ``session.context_max``. Default: no auto-promote.

    isContextMaxAutoPromoted(/* session */ _session, /* contextMax */ _contextMax, /* model */ _model) {
        return false
    }

    // Whether context is LOCKED to the larger window because the session can no
    // longer fit in the base one — independent of the currently selected value.
    // The popover keeps the context switch disabled + on (with a warning) in
    // this state, so it can't be toggled down. Default: never locked.
    isContextMaxLocked(/* session */ _session, /* model */ _model) {
        return false
    }

    // ─── Agent settings popover/summary rendering hooks ──────────────────
    //
    // The popover (per-session selects) and the summary strip share a single
    // generic Vue template. Whenever a provider needs to inject behaviour
    // that doesn't fit the catalogue (auto-promote, capability-based
    // disabling, "(latest: vX)" suffixes, etc.) it overrides one of the
    // hooks below — each one has a sensible default so providers only
    // implement what's specific.
    //
    // ``context`` is a plain object the popover assembles per render. Common
    // fields callers may set: ``effectiveModel``, ``isStarting``,
    // ``isContextMaxForced``, ``selectedValue``, ``defaultValue``,
    // ``processStateName``, ``hasMessageText``, ``hasCrons``. Hooks should
    // ignore keys they don't need.

    /**
     * Human label for a setting field — used as the row's ``<label>`` in the
     * popover. Default carries sensible English names for the fields the
     * Claude provider uses; providers override individual entries when their
     * terminology differs.
     */
    getFieldLabel(field) {
        const defaults = {
            selected_model: 'Model',
            permission_mode: 'Permission',
            permission_mode_if_untrusted: 'Permission (untrusted)',
            effort: 'Effort',
            thinking_enabled: 'Thinking',
            claude_in_chrome: 'Claude built-in Chrome MCP',
            fast_mode: 'Fast mode',
            context_max: 'Context',
        }
        return defaults[field] ?? field
    }

    /**
     * Label of the "Default: …" pseudo-option of a setting's wa-select.
     * Default: model fields use ``getModelLabel``, everything else uses
     * ``getChoiceLabel``. Providers override to e.g. append a "(latest: vX)"
     * suffix on the model.
     */
    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') return this.getModelLabel(value)
        return this.getChoiceLabel(field, value) ?? String(value ?? '')
    }

    /**
     * Whether a single choice option of a select should be disabled. The
     * popover surfaces a "(not available)" suffix on disabled options so
     * the user understands why. Default: only the trust clamp — in an
     * untrusted project (``context.untrusted``) permission modes outside the
     * untrusted-allowed set are disabled. Providers overriding this should
     * chain ``super.isChoiceDisabled(...)``.
     */
    isChoiceDisabled(field, choiceValue, context) {
        if (field === 'permission_mode' && context?.untrusted) {
            const allowed = this.getUntrustedPermissionModes()
            if (allowed.length && !allowed.includes(choiceValue)) return true
        }
        return false
    }

    /**
     * Explanation shown as the per-option help text (the option's
     * ``description``) on a disabled choice, so the user understands *why* it
     * reads "(not available)". Default: only the trust clamp on
     * ``permission_mode`` (the mode is removed on untrusted projects). Returns a
     * string or null. Providers overriding this should chain
     * ``super.getChoiceDisabledReason(...)``.
     */
    getChoiceDisabledReason(field, choiceValue, context) {
        if (field === 'permission_mode' && context?.untrusted) {
            const allowed = this.getUntrustedPermissionModes()
            if (allowed.length && !allowed.includes(choiceValue)) {
                return 'Unavailable on untrusted projects.'
            }
        }
        return null
    }

    /**
     * Whether the entire wa-select for ``field`` should be disabled.
     * Default: disabled while the process is starting. Providers override
     * to also disable when a runtime override is in effect (e.g. Claude's
     * auto-promote-to-1M rule grays the context_max select out).
     */
    isFieldDisabled(field, context) {
        return !!context?.isStarting
    }

    /**
     * Whether ``field`` offers a real choice (more than one selectable option),
     * so a select worth showing. Default counts the choices not disabled by
     * ``isChoiceDisabled`` (e.g. Codex's context, where every option but the
     * model's window is disabled → one choice). Providers override where
     * availability is gated at the field level rather than per option. Lets the
     * popover hide a select that could only ever show its single value.
     */
    fieldHasChoice(field, context) {
        return this.getFieldChoices(field).filter(
            c => !this.isChoiceDisabled(field, c.value, context),
        ).length > 1
    }

    /**
     * Help text rendered under a wa-select (between select and reset link).
     * Returns a string or null. Default: only the trust-clamp explanation on
     * ``permission_mode`` (see ``getDisplayedSelectValue``). Providers
     * overriding this should chain ``super.getFieldHelpText(...)`` to keep it.
     */
    getFieldHelpText(field, context) {
        if (field === 'permission_mode' && context?.permissionModeForced
            && context?.clampedPermissionMode != null) {
            const label = this.getChoiceLabel('permission_mode', context.clampedPermissionMode)
                ?? String(context.clampedPermissionMode)
            return `Clamped to ${label} — untrusted project.`
        }
        return null
    }

    /**
     * Optional notice shown as an icon + tooltip beside a switch/toggle — a
     * warning or info the compact switch has no room to spell out inline.
     * Returns ``{ icon, variant, text }`` (``variant`` is 'warning' or 'brand',
     * driving the icon colour) or null. Providers override per field.
     */
    getFieldNotice(field, context) {
        return null
    }

    /**
     * Value the wa-select should display, given the user's persisted
     * selection. Most fields surface the selection as-is; some providers
     * override to show a runtime override instead (e.g. Claude shows
     * ``EXTENDED`` in the context_max select while the auto-promote rule is
     * active, even if the user's persisted value is null/200K).
     *
     * Returns the same string the matching ``<wa-option :value>`` exposes —
     * the popover uses it directly via ``:value.prop``. The sentinel for the
     * "Default: X" option is the literal string ``'__default__'``.
     *
     * Snapshot model: a stored value equal to the current resolved default
     * (``context.defaultValue``) renders as that sentinel — i.e. "unforced" —
     * even though it is stored concrete. Only a value that diverges from the
     * resolved default shows as an explicit "Force to: …" pick (which is what
     * surfaces the per-field reset link). ``null`` (legacy "follow") also maps
     * to the sentinel.
     */
    getDisplayedSelectValue(field, selectedValue, context) {
        // Trust clamp (trust design §13.4): when the stored mode is outside
        // the untrusted-allowed set, the backend runs the session on the
        // clamped value — show THAT, not the inert stored one (same pattern
        // as Claude's auto-promoted context_max).
        if (field === 'permission_mode' && context?.permissionModeForced
            && context?.clampedPermissionMode != null) {
            return String(context.clampedPermissionMode)
        }
        if (selectedValue === null || selectedValue === context?.defaultValue) return '__default__'
        return String(selectedValue)
    }

    /**
     * Build the parts list rendered by the inline summary in the popover
     * trigger button. Each entry is either a text part ``{ text, forced }``
     * or an icon part ``{ icon, iconFamily?, on, forced, label }`` — the
     * shared ``AgentSettingsSummaryView`` renders icons for boolean flags
     * (thinking, Chrome MCP), dimmed when off. ``forced=true`` adds a dashed
     * underline so the user notices a non-default choice.
     *
     * ``state`` shape:
     * ```
     * { selected: { selected_model, effort, …}, defaults: {…} }
     * ```
     *
     * Layout: model and effort collapse into one ``{model} × {effort}`` part
     * (the model uses the compact matrix label, see ``getSummaryModelLabel``),
     * immediately followed by the thinking icon (grouped with no separator, as
     * model + effort + thinking read as one cluster), then permission mode as
     * text, and fast mode / Chrome MCP as icons when applicable.
     * ``context_max`` is never shown as its own part;
     * providers fold it into the model label via ``getSummaryModelSuffix``
     * (Claude's "[1m]"). Providers tweak the model rendering through the
     * ``getSummaryModel*`` hooks rather than overriding this whole method.
     */
    getSummaryParts(state) {
        const sel = state?.selected ?? {}
        const def = state?.defaults ?? {}
        const eff = (field) => sel[field] ?? def[field]
        const forced = (field) => sel[field] !== null && sel[field] !== undefined && sel[field] !== def[field]
        const parts = []

        // Model + effort — a single part. The model carries the compact matrix
        // label plus any provider suffix (Claude's "[1m]"); the effort renders
        // as a 5-bar level icon glued after the model (see getEffortIconSrc).
        const modelText = this.getSummaryModelLabel(eff('selected_model')) + this.getSummaryModelSuffix(state)
        const modelForced = forced('selected_model') || this.isSummaryContextForced(state)
        if (this.supportsAgentSetting('effort')) {
            const effortValue = eff('effort')
            parts.push({
                text: modelText,
                effortSrc: this.getEffortIconSrc(effortValue),
                effortLabel: this.getChoiceDisplayLabel('effort', effortValue) ?? this.getChoiceLabel('effort', effortValue) ?? String(effortValue ?? ''),
                forced: modelForced || forced('effort'),
            })
        } else {
            parts.push({ text: modelText, forced: modelForced })
        }

        // Thinking — grouped with model + effort (no separator before it), as
        // the three form one "how the model reasons" cluster. Rendered as a
        // coloured icon (brain-bulb) from AGENT_SETTING_ICONS, dimmed + struck
        // when off. ``groupWithPrevious`` suppresses the summary separator.
        if (this.supportsAgentSetting('thinking_enabled')) {
            const on = eff('thinking_enabled') === true
            parts.push({ field: 'thinking_enabled', on, forced: forced('thinking_enabled'), label: on ? 'Thinking' : 'No thinking', groupWithPrevious: true })
        }

        // Permission mode — label prefixed by the coloured mode glyph (same
        // icon/tint as the permission selects, from getChoiceIcon).
        if (this.supportsAgentSetting('permission_mode')) {
            const permissionMode = eff('permission_mode')
            const iconInfo = this.getChoiceIcon('permission_mode', permissionMode)
            parts.push({
                text: this.getChoiceLabel('permission_mode', permissionMode) ?? '',
                permissionIcon: iconInfo?.icon ?? null,
                permissionColor: iconInfo?.color ?? null,
                forced: forced('permission_mode'),
            })
        }

        // The remaining boolean flags render as a grouped icon cluster (bolt,
        // chrome), coloured when on and dimmed + struck when off. The glyph
        // and tint per field live in AGENT_SETTING_ICONS; parts carry only ``field``.

        // Fast mode — only when on.
        if (this.supportsAgentSetting('fast_mode') && eff('fast_mode')) {
            parts.push({ field: 'fast_mode', on: true, forced: forced('fast_mode'), label: 'Fast mode' })
        }

        // Chrome MCP — kept last.
        if (this.supportsAgentSetting('claude_in_chrome')) {
            const on = eff('claude_in_chrome') === true
            parts.push({ field: 'claude_in_chrome', on, forced: forced('claude_in_chrome'), label: on ? 'Chrome MCP' : 'No Chrome MCP' })
        }

        return parts
    }

    /**
     * Compact model label for the inline summary, based on the agent-settings
     * matrix short tier label (``getModelShortLabel``). The registry version
     * is appended for older aliases only — the latest model of a family stays
     * versionless (e.g. "opus" → "Opus", "gpt-terra" → "Terra", but
     * "opus-4.7" → "Opus 4.7").
     */
    getSummaryModelLabel(selectedModel) {
        if (!selectedModel) return ''
        const short = this.getModelShortLabel(selectedModel)
        const entry = (this.getModelRegistry?.() ?? []).find(e => e.selected_model === selectedModel)
        if (!entry || entry.latest === true) return short
        const version = entry.version != null ? String(entry.version) : ''
        return version && !short.endsWith(version) ? `${short} ${version}` : short
    }

    /**
     * Path to the 5-bar effort-level icon for the inline summary. Always five
     * bars; the first N (matching the effort rank low→max) are brand-coloured,
     * the rest currentColor. Returns null for an unknown effort value.
     */
    getEffortIconSrc(effort) {
        return effortIconSrc(effort)
    }

    /**
     * Provider-specific suffix appended to the summary model label. Base adds
     * nothing; Claude appends "[1m]" when context_max is extended.
     */
    getSummaryModelSuffix(_state) {
        return ''
    }

    /**
     * Whether the context choice diverges from the default in a way that
     * should mark the (context-carrying) model part as forced. Base never
     * surfaces context in the summary, so it never forces.
     */
    isSummaryContextForced(_state) {
        return false
    }

    /**
     * Copy of the warning shown in the popover when applying the pending
     * changes will require a process stop/restart. ``context`` carries
     * ``processStateName`` ('assistant_turn' / 'user_turn' / etc.),
     * ``hasMessageText`` (boolean) and ``hasCrons`` (boolean). Default uses
     * the provider's ``label`` for the agent's display name.
     */
    getStartupWarningText(context) {
        const label = this.constructor.label ?? 'Agent'
        const prefix = context?.processStateName === 'assistant_turn'
            ? `Once ${label} finishes its current work, the`
            : 'The'
        if (context?.hasCrons) {
            const suffix = context.hasMessageText ? ', after which your message will be sent.' : '.'
            return `${prefix} ${label} process will be stopped to apply these settings, then resumed to restart the current cron jobs${suffix}`
        }
        const suffix = context?.hasMessageText
            ? 'Your message will be sent after the process restarts.'
            : 'Your next message will resume the session.'
        return `${prefix} ${label} process will be stopped to apply these settings. ${suffix}`
    }

    /**
     * Copy of the info shown in the popover when the pending changes are
     * idle-only and the agent is currently in ASSISTANT_TURN: no stop or
     * restart, but the new values only kick in once the agent's current
     * turn ends. ``context`` carries ``hasMessageText`` (boolean). Default
     * uses the provider's ``label`` for the agent's display name.
     */
    getIdleWarningText(context) {
        const label = this.constructor.label ?? 'Agent'
        return context?.hasMessageText
            ? `Once ${label} finishes its current work, your changes will take effect and your message will be sent.`
            : `Once ${label} finishes its current work, the new settings will take effect.`
    }

    /**
     * Option groups rendered by the model wa-select, retired models removed.
     *
     * The "latest / older" split every provider builds is about *families* —
     * older means "still valid, but no longer its family's latest". A retired
     * model belongs to neither group: it cannot be selected at all, so it is
     * dropped here rather than in each provider's grouping.
     *
     * Do NOT override this — override ``buildModelSelectGroups``, which
     * receives the already-filtered registry.
     */
    getModelSelectGroups(registry) {
        return this.buildModelSelectGroups((registry ?? []).filter(e => !this.isModelRetired(e)))
    }

    /**
     * Shape the (already filtered) model registry into the option groups
     * rendered by the model wa-select. Each group is ``{ entries: [{ value,
     * label }] }`` — adjacent groups are separated by a wa-divider. Default:
     * a single group flattening the registry. Claude and Codex override to
     * split latest vs older with a divider in between.
     */
    buildModelSelectGroups(registry) {
        return [{
            entries: (registry ?? []).map(e => this.buildModelOption(e, this.getModelLabel(e.selected_model))),
        }]
    }

    /**
     * Shape one model registry entry into a wa-option descriptor for the
     * model picker. ``baseLabel`` is the provider-formatted label (the
     * caller adds any "(latest: vX)" / "(until DATE)" decoration). A disabled
     * model stays in the list, greyed out: ``disabled`` flags the option,
     * ``labelWithSuffix`` appends "(not available)", and ``description``
     * carries the reason shown under the option (mirrors the per-option
     * description used by the other agent-setting selects).
     */
    buildModelOption(entry, baseLabel) {
        const disabled = entry.enabled === false
        return {
            value: entry.selected_model,
            label: baseLabel,
            labelWithSuffix: disabled ? `${baseLabel} (not available)` : baseLabel,
            disabled,
            description: disabled ? (entry.disable_reason || null) : null,
        }
    }

    // ─── Attachment capabilities ─────────────────────────────────────────
    //
    // Consumed by ``MessageInput.vue`` and ``addAttachment`` to gate the
    // file picker, the paste handler, and the per-file processing pipeline
    // (resize, max bytes) against what this provider can actually carry.
    // Each provider declares its own ceiling; generic code never branches
    // on the provider id itself.
    //
    // Shape:
    //   {
    //     images:            boolean,    // accept images at all?
    //     documents:         boolean,    // accept PDF / TXT?
    //     maxBytes:          number,     // hard per-file size cap (bytes)
    //     acceptedMimeTypes: string[],   // exact list, used both for the
    //                                    // <input accept> attribute and
    //                                    // for MIME validation
    //     resizeImages:      boolean,    // client-side downscale to
    //                                    // MAX_IMAGE_DIMENSION before
    //                                    // base64 encoding
    //   }

    /**
     * Attachment capabilities for this provider's send pipeline. Default:
     * provider accepts nothing — frontend hides the paperclip and refuses
     * all file picks / pastes. Providers override with the formats and
     * limits their runtime actually supports.
     */
    getAttachmentSupport() {
        return {
            images: false,
            documents: false,
            maxBytes: 0,
            acceptedMimeTypes: [],
            resizeImages: false,
        }
    }

    /**
     * Long-edge pixel cap to apply to images at send time for this
     * provider / model / batch-size combination, or ``null`` to leave
     * the stored images at their upload-time resolution.
     *
     * Images are stored at the shared ``MAX_IMAGE_DIMENSION`` (2576 px,
     * Opus 4.7's native resolution) regardless of the provider. The
     * send-time pipeline calls this hook with the (model, numImages)
     * context of the active turn so the provider can demand a tighter
     * cap — typically because the target model downscales below 2576
     * (Claude Sonnet/Haiku → 1568 px), or because Anthropic enforces a
     * 2000 px ceiling once a request carries more than 20 images.
     *
     * Returning ``null`` means "ship the stored blob as-is": correct
     * for Codex (its CLI does its own resize at 2048 px) and for
     * Claude Opus 4.7+ at ≤20 images.
     *
     * @param {{model?: string|null, numImages?: number}} _context
     * @returns {number|null} Pixel cap on the long edge, or null
     */
    getEffectiveImageDimension(/* { model, numImages } */) {
        return null
    }

}

/**
 * Base class for per-provider tool-rendering helpers consumed by the generic
 * `items/ToolUseContent.vue` shell. Mirrors the `BaseProviderHelpers` pattern:
 * each provider ships a subclass that overrides only the behaviours that
 * differ from the neutral defaults defined here. The shell never branches on
 * provider identity — it asks normalized questions and renders the answers.
 *
 * Two return shapes are used by the rendering hooks:
 *
 *   { component: VueComponent, props: object }   — explicit descriptor
 *   null                                          — fall back to JsonHumanView
 *
 * The `ctx` argument passed to `getInputRendering` / `getResultRendering`
 * carries the shell's runtime state. Common fields:
 *   - ``isSubagent``   — boolean; whether this tool_use lives in a subagent
 *   - ``backendPatch`` / ``originalFile`` / ``backendPatchLoading`` — for
 *     Edit/Write rendering when the backend has computed structured patches
 *   - ``sessionId`` / ``toolId`` — for input renderers that need to scan
 *     the store for a matching tool_result row on their own (e.g. Codex's
 *     ``apply_patch`` reading the linked ``patch_apply_end`` event)
 */
export class BaseToolHelpers {
    static provider = null

    // ─── Summary surface (header + working-assistant inline) ─────────────────────────────
    //
    // Producers: ToolUseContent.vue (header displayName branch),
    // WorkingAssistantMessage.vue (status line). Returned shape:
    //   { displayName: { name, namespace } | null,
    //     inline:      string | null }
    //
    // Variant-specific summary rendering (file/skill/grep/glob/web/todo)
    // flows through ``getSummaryRendering`` instead — that returns a
    // ``{ component, props }`` descriptor for the shell to mount.

    /** Build the summary descriptor for a tool_use. Default: minimal stub. */
    computeToolSummary(/* name, input, baseDir */) {
        return { displayName: null, inline: null }
    }

    /**
     * Convert a tool name + input to a gerund form for the
     * "{provider} is …ing" status line. Default: ``null`` (no verb known).
     */
    getVerb(/* name, input */) {
        return null
    }

    /**
     * Override for the rich card header label when the bare tool name is
     * not the right thing to display (e.g. a tool called ``TodoWrite``
     * should appear as ``Todo``). Returns a string, or ``null`` to let the
     * shell render the raw tool name (``name.replaceAll('__', ' ')``).
     *
     * The optional ``input`` argument lets providers compute a label that
     * depends on the tool_use's input — e.g. Codex's ``exec_command`` is
     * relabeled ``Read`` / ``List files`` / ``Grep`` / ``Exec`` based on
     * a parse of ``input.command``. The optional ``options`` carries
     * provider-specific extras (Codex uses ``{ wrapperType,
     * toolResultPayload }`` so its helper can prefer the official
     * ``parsed_cmd`` from the ``event_msg.*_end`` event when it has
     * arrived). Default helper ignores both.
     *
     * Note: this is for per-name static (or per-input) overrides; dynamic
     * overrides driven by complex input shapes (e.g. Task ``subagent_type``
     * with namespace) still flow through
     * ``computeToolSummary().displayName`` and the ``isTask && displayName``
     * template branch. Default: no override.
     */
    getHeaderLabel(/* name, input, options */) {
        return null
    }

    // ─── Input / Result rendering ────────────────────────────────────────

    /**
     * Return ``{ component, props }`` for the tool's input area, or ``null``
     * to fall back to ``JsonHumanView``. ``ctx`` carries the shell's runtime
     * state (see class docstring). Default: always fall back.
     */
    getInputRendering(/* name, input, ctx */) {
        return null
    }

    /**
     * Return ``{ component, props }`` for the tool's Result area, or ``null``
     * to fall back to ``JsonHumanView``. Called when the tool result has been
     * fetched and is non-empty. Default: always fall back.
     */
    getResultRendering(/* name, result, input, ctx */) {
        return null
    }

    /**
     * Return ``{ component, props }`` for the tool's summary description (the
     * text shown after the tool name and the em-dash separator in the rich
     * card header), or ``null`` to render no description.
     *
     * The optional ``options`` carries provider-specific extras (Codex
     * passes ``{ wrapperType, toolResultPayload }`` so its helper can
     * prefer the official ``parsed_cmd`` from the matching
     * ``event_msg.*_end`` event when available). Default helper ignores it.
     */
    getSummaryRendering(/* name, input, baseDir, options */) {
        return null
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the input
     * fallback (e.g. force ``Bash.command`` to render as a code block).
     * ``input`` lets helpers scope the overrides when one tool name
     * covers several display shapes (Codex's code-mode ``exec``).
     * Default: no overrides.
     */
    getInputOverrides(/* name, input */) {
        return {}
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the result
     * fallback. Default: no overrides.
     */
    getResultOverrides(/* name */) {
        return {}
    }

    // ─── Tool input value extraction ─────────────────────────────────────
    //
    // Hooks the shell uses to read provider-specific values out of an opaque
    // tool input. Each provider knows the shape of its own input; the shell
    // only reads the answers and never branches on field names itself.

    /**
     * Path of the file this tool acts on, or ``null`` when the tool's input
     * doesn't carry one. Powers the per-tool code-comments grouping and is
     * the building block for the View-in-Files button (see
     * ``getOpenInFilesTarget``). Default: no file path.
     */
    getFilePath(/* name, input */) {
        return null
    }

    /**
     * Target for the View-in-Files button: ``{ filePath, lineHint }`` or
     * ``null`` when the button shouldn't surface for this tool. ``ctx`` may
     * carry runtime state inferred by the shell — currently
     * ``firstModifiedLine`` (computed from the backend patch for Edit/Write).
     * Default: derive from ``getFilePath`` with no line hint, so any provider
     * that overrides ``getFilePath`` automatically gets a working button.
     */
    getOpenInFilesTarget(name, input /*, ctx */) {
        const filePath = this.getFilePath(name, input)
        return filePath ? { filePath, lineHint: null } : null
    }

    /**
     * Input object the JsonHumanView fallback should render, or ``null`` when
     * the input is empty / fully consumed by other surfaces. Providers strip
     * fields they've already surfaced elsewhere (e.g. Claude Code's
     * ``description`` is rendered in the summary header). Default: returns
     * ``input`` unchanged when non-empty, ``null`` when empty.
     */
    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        return input
    }

    /**
     * Number of tool_result events the shell should expect before the tool
     * is considered done. Most tools emit a single result; some emit two
     * (e.g. Claude Code's Bash with ``run_in_background``, or Codex tools
     * that have both a ``function_call_output`` and a richer
     * ``event_msg.*_end`` paired by ``call_id``). The shell uses the
     * answer to drive the running spinner.
     *
     * @param {string} name - Tool name as it appears in the tool_use.
     * @param {Object} input - Parsed tool input object.
     * @param {Object} [options] - Provider-specific extras. Codex passes
     *   ``{ wrapperType }`` (one of ``function_call``, ``custom_tool_call``,
     *   ``local_shell_call``, ``web_search_call``, ``image_generation_call``)
     *   so the helper can branch on the wrapper without re-parsing the
     *   raw JSONL line. Helpers that don't care just ignore it.
     * @returns {number} Default: 1.
     */
    getExpectedResultCount(/* name, input, options */) {
        return 1
    }

    /**
     * Number of tool_result rows the Result section needs to have in
     * memory before it can render anything useful. Distinct from
     * ``getExpectedResultCount``: the latter drives the *running
     * spinner* (was the tool ever marked complete?), while this one
     * gates the *Result rendering* (do we already have what we need
     * to show a meaningful body?).
     *
     * Most tools render their result as soon as a single row arrives,
     * so the default is 1 — including Claude Code's Bash with
     * ``run_in_background``: there, the first result already carries
     * the canonical "Command running in background" notice and the
     * second one is just a completion stub. Codex's ``apply_patch``
     * still raises it to 2 so the rich ``patch_apply_end`` row is
     * always part of what's displayed; ``exec_command`` returns 1 and
     * relies on :meth:`isToolRunning` to keep the spinner spinning
     * across the chain of polled chunks.
     *
     * Returning ``> resultData.length`` makes the shell:
     *   - render the "Result not yet available — checking again
     *     shortly…" message + spinner
     *   - keep polling the ``/tool-results/`` endpoint at the regular
     *     interval until the threshold is reached.
     *
     * @returns {number} Default: 1.
     */
    getRequiredResultCountForDisplay(/* name, input, options */) {
        return 1
    }

    /**
     * Whether the tool is still running (spinner ON, polling kept alive).
     *
     * Default: count-based — the tool is considered running until
     * ``toolState.resultCount`` reaches ``getExpectedResultCount``.
     * Providers can override to inspect richer signals when the result
     * count is variable. Codex's ``exec_command`` family overrides this
     * to read ``toolState.extra.is_terminated`` (set by
     * ``compute_link_extra`` on the closing chunk of the chain) since
     * the number of polling chunks is not known up-front.
     *
     * @param {string} name - Tool name as it appears in the tool_use.
     * @param {Object} input - Parsed tool input object.
     * @param {Object} [options] - Same shape as the other tool helpers.
     *   ``options.toolState`` is the per-tool aggregated state from the
     *   data store (notably ``resultCount`` and ``extra``).
     * @returns {boolean} Default: ``resultCount < expectedCount``.
     */
    isToolRunning(name, input, options) {
        const resultCount = options?.toolState?.resultCount ?? 0
        const expected = this.getExpectedResultCount(name, input, options)
        return resultCount < expected
    }

    /**
     * Whether the Result section should aggregate output across multiple
     * tool_result rows for this tool (chained ``function_call_output``
     * chunks for Codex's ``exec_command`` family) before handing the
     * concatenated body to the rendering helper.
     *
     * Default: ``false`` (the renderer receives the single row from
     * ``displayResult`` as-is). Providers that own a chained-result
     * tool override this to ``true`` so the shell precomputes the
     * aggregated payload and exposes it to ``getResultRendering`` via
     * ``options.aggregatedExecOutput``.
     *
     * @returns {boolean} Default: false.
     */
    shouldAggregateExecOutput(/* name */) {
        return false
    }

    /**
     * Compute the aggregated payload to feed ``getResultRendering`` for
     * a chained-result tool. Only invoked by the shell when
     * :meth:`shouldAggregateExecOutput` returned ``true`` for this
     * tool, so the default implementation can stay null.
     *
     * Providers walk their result chain (e.g. Codex iterates the
     * ``toolState.toolResultLineNums`` to concatenate every
     * ``function_call_output``'s body and parse the trailing status)
     * and return the aggregate ready for rendering. The shape is
     * provider-specific — the shell only exposes it via
     * ``options.aggregatedExecOutput``.
     *
     * @param {string} _name - tool name (e.g. ``"exec_command"``,
     *   ``"local_shell_call"``). Providers that handle several chained
     *   tools with different output dialects branch on this.
     * @param {string} _toolId - tool_use_id whose chain to aggregate.
     * @param {Object} _options - same shape as the other helpers; in
     *   particular carries ``getSessionItem`` / ``getToolState`` so the
     *   helper can reach the chain rows in the data store.
     * @returns {*} Default: ``null``.
     */
    getAggregatedExecOutput(/* name, toolId, options */) {
        return null
    }

    /**
     * Pick / transform the value passed to ``getResultRendering`` and
     * ``JsonHumanView`` from the raw array of ToolResultLink rows that
     * the shell loaded for this tool. Returning ``undefined`` leaves the
     * default behaviour in place (single → object, multiple → array);
     * any other return value (including ``null``) overrides it.
     *
     * Use case: tools that accumulate several ToolResultLinks for the
     * same call_id but where only one of them carries the user-facing
     * payload. Codex's MCP tools, for example, get both a
     * ``function_call_output`` (LLM-facing text) and an
     * ``event_msg.mcp_tool_call_end`` (parsed result with
     * ``structuredContent``) — only the latter is worth showing in the
     * Result section. The hook lets the provider helper pick it out
     * without changing how ``ToolResultLink`` rows are stored or counted.
     *
     * @param {string} _name - Tool name.
     * @param {Array} _resultData - Array of parsed result rows (same
     *   shape as ``data.results`` from the ``/tool-results/`` endpoint).
     * @param {Object} _options - Provider-specific extras.
     * @returns {*} ``undefined`` to keep default; any other value to
     *   override.
     */
    transformDisplayResult(/* name, resultData, options */) {
        return undefined
    }

    // ─── Capability flags driving shell-level features ───────────────────

    /**
     * Whether this tool modifies a file (so the shell shows ``+N -N`` stats
     * in the header and may auto-open it for live diffs). Default: false.
     */
    isFileChangeTool(/* name */) {
        return false
    }

    /**
     * Whether this tool is an "Agent / Task" spawner (the shell shows the
     * View-Agent button + spinner / robot icon). Default: false.
     */
    isAgentTool(/* name */) {
        return false
    }

    /**
     * Whether a subagent going idle in its own transcript ends the spawn
     * tool's "agent running" state, on top of the usual rule (the spawn tool
     * collected all of its expected results).
     *
     * Default false: on Claude Code the tool_result IS the completion, and a
     * subagent resumed through SendMessage carries an older stop timestamp
     * that must not stop the fresh run. Codex multi-agent v2 overrides it —
     * a subagent that answers its parent through ``send_message`` stays alive
     * and never produces the second result the rule waits for, so its own
     * ``task_complete`` is the only thing that ever says "done".
     */
    agentRunEndsOnSubagentIdle() {
        return false
    }

    /**
     * Whether the shell should auto-open the details for this tool when the
     * item arrives live via WebSocket and the user has ``settings.showDiffs``
     * enabled. Default: false.
     */
    shouldAutoOpenLive(/* name, input */) {
        return false
    }

    /**
     * Whether the Result section should remain visible when this tool
     * errored, even though there is no specialized input renderer claiming
     * it. Used for tools whose error message is too terse on its own (e.g.
     * Bash's "Exit code N" — the user wants to see the full stdout/stderr
     * via the Result fallback). The shell also falls back to showing the
     * Result for the special ``'Unknown error'`` text regardless of what
     * this method returns. Default: false.
     */
    showsResultOnError(/* name */) {
        return false
    }

    /**
     * Whether the Result section should be shown when this tool has a
     * specialized input renderer AND the error is the special
     * ``'Unknown error'`` text. Used to surface diagnostic detail for tools
     * whose specialized input UI alone isn't enough (Edit/Write opt in;
     * TodoWrite stays hidden). Default: false.
     */
    showsResultOnUnknownError(/* name */) {
        return false
    }

    /**
     * Whether the error text for this tool should be rendered as Markdown
     * (via ``MarkdownContent``) instead of plain text. Used for tools whose
     * error message is itself markdown content (e.g. ExitPlanMode emits a
     * markdown-formatted plan that the user wants to read rendered).
     * Default: false (render as plain text).
     */
    errorIsMarkdown(/* name */) {
        return false
    }

    /**
     * Whether the shell should fully suppress the Result section for this
     * tool, even though no specialized input renderer is claiming it.
     * Used for tools whose tool_result carries no extra signal beyond what
     * the input + summary already convey (e.g. Claude Code's ``TaskCreate``
     * / ``TaskUpdate`` / ``TaskGet`` — the canonical task payload is
     * already spliced into the tool_use by the backend). Default: false.
     */
    hidesResult(/* name */) {
        return false
    }

    /**
     * Whether the tool's result should be rendered **inline** — directly in
     * the tool card body, in place of the collapsible "Result" disclosure —
     * and fetched as soon as the card opens (rather than on a separate click
     * to expand "Result"). Used for tools whose result *is* the content the
     * user came to see, with no useful intermediate "Result" step (e.g.
     * Codex's ``view_image``, whose result is the image itself). The input
     * JSON is unaffected — it still renders above, as usual. Default: false.
     */
    rendersResultInline(/* name, input */) {
        return false
    }

    // ─── File-change stats (header ``+N -N``) ────────────────────────────
    //
    // The shell does the layout (``+N -N``); the helper provides the values.
    // ``toolState`` is the dataStore.getToolState(...) entry (carries
    // ``extra`` JSON when the backend computed stats). ``isSubagent`` flips
    // the source — backend stats for the main session, computed-from-input
    // for subagents.

    /**
     * Compute ``{ lines_added, lines_removed? }`` for the ``+N -N`` header,
     * or ``null`` when this tool doesn't carry stats. Default: null.
     */
    computeFileChangeStats(/* name, input, toolState, isSubagent */) {
        return null
    }

    // ─── Backend-patch fetcher (used by Edit/Write to draw full-file diffs)
    //
    // Some providers post-process the tool_result item to attach a
    // structured patch and the original file content. The shell handles
    // the fetch (the parsed item is read from the store or via
    // ``loadSessionItemsRanges``); the helper decides whether the fetch is
    // needed and how to extract the data.

    /**
     * Whether the shell should fetch the tool_result item and pass extracted
     * data through ``ctx`` to ``getInputRendering``. Default: false.
     */
    needsBackendPatchFetch(/* name */) {
        return false
    }

    /**
     * Extract ``{ patch, originalFile }`` from a parsed tool_result item, or
     * ``null`` when the data is absent. Default: never extracts.
     */
    extractBackendPatchData(/* parsedToolResult */) {
        return null
    }
}
