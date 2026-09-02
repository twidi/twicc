import { BaseProviderHelpers, formatRetirementDate } from '../baseHelpers'
import { PROVIDER, SYNTHETIC_ITEM } from '../../constants'
import { CONTEXT_MAX, EFFORT, PERMISSION_MODE, UNTRUSTED_PERMISSION_MODES } from './constants'
import { useClaudeCodeStore } from './store'
import { getTwiccLaunchPrefix } from '../../utils/twiccLaunch'
import {
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_TEXT_TYPES,
} from '../../utils/fileUtils'

// Claude API per-file ceiling (5 MB) — applies before resize. Beyond this
// the server rejects the request, so the frontend enforces it client-side
// to fail fast on the toast surface instead of waiting for an SDK error.
const CLAUDE_MAX_FILE_BYTES = 5 * 1024 * 1024

// Claude CLI's built-in commands (invoked with ``/``). Hardcoded here because
// the CLI never exposes the list programmatically; entries are sourced from
// the CLI documentation. Surface order matches the CLI's own.
//
// ``is_builtin`` is a frontend-only sentinel the picker tag uses to render
// the ``(built-in)`` label — these rows never reach the backend, so it
// doesn't need a matching column on ``Command``. ``is_workflow`` is the same
// kind of sentinel: set on ``deep-research`` (a workflow shipped with Claude)
// so the picker tags it ``built-in workflow``. The flag also exists as a real
// ``Command`` column for workflows discovered on disk.
const BUILTIN_COMMANDS = [
    { name: 'compact', plugin_name: null, is_builtin: true, is_global: true, description: 'Clear conversation history but keep a summary in context', argument_hint: '[instructions for summarization]' },
    { name: 'cost', plugin_name: null, is_builtin: true, is_global: true, description: 'Show the cost of the current session', argument_hint: null },
    { name: 'context', plugin_name: null, is_builtin: true, is_global: true, description: 'Show the current context window usage', argument_hint: null },
    { name: 'init', plugin_name: null, is_builtin: true, is_global: true, description: 'Initialize a new CLAUDE.md file with codebase documentation', argument_hint: null },
    { name: 'loop', plugin_name: null, is_builtin: true, is_global: true, description: "Run a prompt or slash command on a recurring interval until the session ends (e.g. /loop 5m /foo, defaults to 10m)", argument_hint: '[interval] [command or prompt]' },
    { name: 'goal', plugin_name: null, is_builtin: true, is_global: true, description: "Set a completion condition Claude keeps working toward across turns until an evaluator agent confirms it's met; run with no argument to show status, or 'clear' to cancel", argument_hint: '[condition | clear]' },
    { name: 'deep-research', plugin_name: null, is_builtin: true, is_workflow: true, is_global: true, description: 'Research a question across many web sources, cross-verify claims, and return a cited report', argument_hint: '<question>' },
]

// Map of usage-file field names (cross-provider) → Claude Code store
// getter/setter. Mirrors ``FIELD_TO_DEFAULT_STORE_BINDING``: lets the
// generic ``getUsageFileSetting`` / ``setUsageFileSetting`` hooks proxy
// to this provider's own ``useClaudeCodeStore`` refs, so the on-disk
// shape and synced settings stay Claude Code-specific while the UI
// stays provider-agnostic.
const USAGE_FILE_FIELD_TO_STORE_BINDING = {
    read_enabled: { getter: 'usageReadFileEnabled', setter: 'setUsageReadFileEnabled' },
    read_path:    { getter: 'usageReadFilePath',    setter: 'setUsageReadFilePath' },
    dump_enabled: { getter: 'usageDumpFileEnabled', setter: 'setUsageDumpFileEnabled' },
    dump_path:    { getter: 'usageDumpFilePath',    setter: 'setUsageDumpFilePath' },
}

// Map of agent-setting wire names → store getter/setter for the persisted
// default. Used by ``getDefaultValue`` / ``setDefaultValue`` so generic
// surfaces (palette, settings popover) can read/write defaults without
// knowing the field-specific store property names.
const FIELD_TO_DEFAULT_STORE_BINDING = {
    selected_model:   { getter: 'defaultModel',           setter: 'setDefaultModel' },
    effort:           { getter: 'defaultEffort',          setter: 'setDefaultEffort' },
    thinking_enabled: { getter: 'defaultThinking',        setter: 'setDefaultThinking' },
    permission_mode:  { getter: 'defaultPermissionMode',  setter: 'setDefaultPermissionMode' },
    permission_mode_if_untrusted: { getter: 'defaultUntrustedPermissionMode', setter: 'setDefaultUntrustedPermissionMode' },
    context_max:      { getter: 'defaultContextMax',      setter: 'setDefaultContextMax' },
    claude_in_chrome: { getter: 'defaultClaudeInChrome',  setter: 'setDefaultClaudeInChrome' },
    fast_mode:        { getter: 'defaultFastMode',        setter: 'setDefaultFastMode' },
}

// Map of synced setting keys (the wire/storage names) → store action that
// applies the value. Used by both ``applySyncedSettings`` (input) and
// ``getSyncedSettings`` (output) so the two sides can never drift apart.
const SYNCED_SETTING_KEYS_TO_STORE = {
    claudeCodeDefaultPermissionMode: { setter: 'setDefaultPermissionMode', getter: 'defaultPermissionMode' },
    claudeCodeDefaultUntrustedPermissionMode: { setter: 'setDefaultUntrustedPermissionMode', getter: 'defaultUntrustedPermissionMode' },
    claudeCodeDefaultModel:          { setter: 'setDefaultModel',          getter: 'defaultModel' },
    claudeCodeDefaultContextMax:     { setter: 'setDefaultContextMax',     getter: 'defaultContextMax' },
    claudeCodeDefaultEffort:         { setter: 'setDefaultEffort',         getter: 'defaultEffort' },
    claudeCodeDefaultThinking:       { setter: 'setDefaultThinking',       getter: 'defaultThinking' },
    claudeCodeDefaultClaudeInChrome: { setter: 'setDefaultClaudeInChrome', getter: 'defaultClaudeInChrome' },
    claudeCodeDefaultFastMode:       { setter: 'setDefaultFastMode',       getter: 'defaultFastMode' },
    claudeCodeUsageReadFileEnabled:  { setter: 'setUsageReadFileEnabled',  getter: 'usageReadFileEnabled' },
    claudeCodeUsageReadFilePath:     { setter: 'setUsageReadFilePath',     getter: 'usageReadFilePath' },
    claudeCodeUsageDumpFileEnabled:  { setter: 'setUsageDumpFileEnabled',  getter: 'usageDumpFileEnabled' },
    claudeCodeUsageDumpFilePath:     { setter: 'setUsageDumpFilePath',     getter: 'usageDumpFilePath' },
    claudeCodeQuotaWakeupTime:       { setter: 'setQuotaWakeupTime',       getter: 'quotaWakeupTime' },
}

// Statuspage display map: Atlassian status values → footer rendering.
// Matches the status strings broadcast by the backend statuspage task.
const ANTHROPIC_STATUS_DISPLAY = {
    operational:          { label: 'Operational',      modifier: 'ok' },
    degraded_performance: { label: 'Degraded',         modifier: 'warning' },
    partial_outage:       { label: 'Partial outage',   modifier: 'warning' },
    major_outage:         { label: 'Major outage',     modifier: 'error' },
    under_maintenance:    { label: 'Maintenance',      modifier: 'info' },
}

// Per-field choice catalogue for the Claude Code provider.
// Field names match the snake_case wire names (``thinking_enabled``, not
// ``thinking``); the preset shape uses ``thinking`` and is translated at
// the lookup boundary by callers.
//
// Values are stored in their natural type (string for permission/effort,
// boolean for thinking/chrome, integer for context_max). UI components
// stringify when binding to ``<wa-select>`` and re-parse on change.
//
// ``selected_model`` is intentionally absent: the model list is served via
// the model registry (see ``getModelRegistry``) which carries additional
// metadata (latest, retirement_date, capability flags).
const AGENT_SETTINGS_CHOICES = {
    // ``icon``/``color`` drive the per-mode glyph shown in the permission
    // selects (see utils/permissionModeIcon.js + PermissionModeIcon.vue). Modes
    // are grouped into six cross-provider tiers, each with one glyph + hue;
    // Codex mirrors the same tiers (codex/helpers.js). Order = severity ramp,
    // most restrictive → most permissive (plan/purple, then blue → green →
    // yellow → orange → red); this drives the select option order everywhere.
    permission_mode: [
        {
            value: PERMISSION_MODE.PLAN,
            label: 'Plan',
            description: 'Read-only: Claude can analyze but not modify files',
            icon: 'clipboard-list',
            color: 'var(--wa-color-purple-60)',
        },
        {
            value: PERMISSION_MODE.DONT_ASK,
            label: "Don't Ask",
            description: 'Auto-denies tools unless pre-approved via permission rules',
            icon: 'shield-halved',
            color: 'var(--wa-color-blue-60)',
        },
        {
            value: PERMISSION_MODE.DEFAULT,
            label: 'Default',
            description: 'Prompts for permission on first use of each tool',
            icon: 'eye',
            color: 'var(--wa-color-green-60)',
        },
        {
            value: PERMISSION_MODE.ACCEPT_EDITS,
            label: 'Accept Edits',
            description: 'Auto-accepts file edit permissions',
            icon: 'pen-to-square',
            color: 'var(--wa-color-yellow-60)',
        },
        {
            value: PERMISSION_MODE.AUTO,
            label: 'Auto',
            description: 'Auto-approves tools, with safety checks blocking risky actions',
            icon: 'shield-check',
            color: 'var(--wa-color-orange-60)',
        },
        {
            value: PERMISSION_MODE.BYPASS,
            label: 'Bypass permissions',
            description: 'Skips all permission prompts',
            icon: 'triangle-exclamation',
            color: 'var(--wa-color-red-60)',
        },
    ],
    effort: [
        { value: EFFORT.LOW,    label: 'Low',    display_label: 'Low effort' },
        { value: EFFORT.MEDIUM, label: 'Medium', display_label: 'Medium effort' },
        { value: EFFORT.HIGH,   label: 'High',   display_label: 'High effort' },
        { value: EFFORT.X_HIGH, label: 'xHigh',  display_label: 'xHigh effort' },
        { value: EFFORT.MAX,    label: 'Max',    display_label: 'Max effort' },
    ],
    thinking_enabled: [
        { value: true,  label: 'Adaptive', display_label: 'Thinking' },
        { value: false, label: 'Disabled', display_label: 'No thinking' },
    ],
    claude_in_chrome: [
        { value: true,  label: 'Enabled',  display_label: 'Chrome MCP' },
        { value: false, label: 'Disabled', display_label: 'No Chrome MCP' },
    ],
    fast_mode: [
        {
            value: true,
            label: 'Enabled',
            display_label: 'Fast mode',
            description: 'Faster generation, billed on extra usage credits. Costs 2x the tokens (6x before 4.8).',
        },
        { value: false, label: 'Disabled', display_label: 'No fast mode' },
    ],
    context_max: [
        { value: CONTEXT_MAX.DEFAULT,  label: '200K' },
        { value: CONTEXT_MAX.EXTENDED, label: '1M' },
    ],
}

export class ClaudeCodeHelpers extends BaseProviderHelpers {
    static provider = PROVIDER.CLAUDE_CODE
    static label = 'Claude'
    static icon = 'claude'
    static iconColor = 'var(--wa-color-orange-70)'
    static serviceProductLabel = 'Claude Code'
    static serviceVendorLabel = 'Anthropic'
    static serviceStatusUrl = 'https://status.claude.com/'

    canSendMessage() {
        return useClaudeCodeStore().authenticated !== false
    }

    canInterruptTurn() {
        // The SDK runtime supports a soft interrupt (ESC-equivalent control
        // request). Hybrid (tmux-driven) sessions are excluded at the call
        // site — they have no SDK client to interrupt in place.
        return true
    }

    getCommandActivationChars() {
        return ['/']
    }

    getPlaceholderAssistantTurnNote() {
        return 'Note: it will not appear in the conversation history.'
    }

    getBuiltInCommands(activationChar) {
        return activationChar === '/' ? BUILTIN_COMMANDS : []
    }

    buildOptimisticUserMessageContent(text, attachments) {
        // Claude Code's user_message JSONL shape: a transport-style envelope
        // ``{ type: 'user', message: { role, content: [...] } }`` whose
        // ``content`` is the same block array the SDK accepts. Images /
        // documents come in already-SDK-shaped, so we just concatenate.
        const content = []
        if (attachments?.images?.length) content.push(...attachments.images)
        if (attachments?.documents?.length) content.push(...attachments.documents)
        // Attachments alone are a valid message: emit no text block at all
        // then, exactly like the backend does when building the SDK prompt.
        if (text) content.push({ type: 'text', text })
        return {
            type: 'user',
            syntheticKind: SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind,
            message: { role: 'user', content },
        }
    }

    extractUserMessageText(parsed) {
        const content = parsed?.message?.content
        if (typeof content === 'string') {
            return content.trim() || null
        }
        if (!Array.isArray(content)) return null

        const text = content
            .filter(block => block?.type === 'text' && typeof block.text === 'string')
            .map(block => block.text)
            .join('\n')
            .trim()
        return text || null
    }

    extractUserMessageAttachmentCount(parsed) {
        const content = parsed?.message?.content
        if (!Array.isArray(content)) return 0
        return content.filter(
            block => block?.type === 'image' || block?.type === 'document',
        ).length
    }

    getAuthState() {
        return () => useClaudeCodeStore().authenticated
    }

    getAuthLoginCommand() {
        return `${getTwiccLaunchPrefix()} claude auth login`
    }

    async requestAuthRecheck() {
        // Lazy import: ``./ws`` pulls ``useWebSocket``, which imports back into
        // ``providers/index`` (which imports this module). The lazy form breaks
        // the cycle so HMR can keep doing hot updates.
        const { sendCheckAuth } = await import('./ws')
        sendCheckAuth()
    }

    tracksUsage() {
        return true
    }

    getUsageExternalLink() {
        return { url: 'https://claude.ai/settings/usage', label: 'View usage on claude.ai' }
    }

    supportsUsageRefresh() {
        return true
    }

    async requestUsageRefresh() {
        // Lazy import to break the helpers ↔ ws cycle (see requestAuthRecheck).
        const { sendCheckUsage } = await import('./ws')
        sendCheckUsage()
    }

    getUsageFileSetting(field) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return null
        return useClaudeCodeStore()[binding.getter]
    }

    setUsageFileSetting(field, value) {
        const binding = USAGE_FILE_FIELD_TO_STORE_BINDING[field]
        if (!binding) return
        useClaudeCodeStore()[binding.setter](value)
    }

    supportsQuotaWakeup() {
        return true
    }

    getQuotaWakeupTime() {
        return useClaudeCodeStore().quotaWakeupTime || ''
    }

    setQuotaWakeupTime(value) {
        useClaudeCodeStore().setQuotaWakeupTime(value)
    }

    getServiceStatus() {
        return () => useClaudeCodeStore().anthropicStatus
    }

    getServiceStatusDisplay(status) {
        const entry = ANTHROPIC_STATUS_DISPLAY[status] ?? { label: status, modifier: 'ok' }
        return {
            ...entry,
            url: this.constructor.serviceStatusUrl,
            tooltip: "Claude Code status on Anthropic's side",
        }
    }

    applyServiceStatus(status) {
        if (typeof status === 'string' && status) useClaudeCodeStore().setAnthropicStatus(status)
    }

    getUntrustedPermissionModes() {
        return UNTRUSTED_PERMISSION_MODES
    }

    getDefaultValue(field) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return null
        return useClaudeCodeStore()[binding.getter]
    }

    setDefaultValue(field, value) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return
        const store = useClaudeCodeStore()
        store[binding.setter](value)
        // Re-enforce cross-field consistency when the default model changes:
        // the new model may not support the persisted context_max / effort /
        // fast_mode / permission_mode, so they get rolled back to values
        // the model accepts.
        if (field === 'selected_model') {
            const adjusted = this.enforceAgentSettingsConsistency({
                selectedModel: store.defaultModel,
                contextMax: store.defaultContextMax,
                effort: store.defaultEffort,
                fastMode: store.defaultFastMode,
                permissionMode: store.defaultPermissionMode,
            })
            if (adjusted.contextMax !== store.defaultContextMax) store.setDefaultContextMax(adjusted.contextMax)
            if (adjusted.effort !== store.defaultEffort) store.setDefaultEffort(adjusted.effort)
            if (adjusted.fastMode !== store.defaultFastMode) store.setDefaultFastMode(adjusted.fastMode)
            if (adjusted.permissionMode !== store.defaultPermissionMode) store.setDefaultPermissionMode(adjusted.permissionMode)
        }
    }

    getSyncedSettingsKeys() {
        return Object.keys(SYNCED_SETTING_KEYS_TO_STORE)
    }

    applySyncedSettings(settings) {
        if (!settings || typeof settings !== 'object') return
        const store = useClaudeCodeStore()
        for (const [key, { setter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            if (key in settings) store[setter](settings[key])
        }
    }

    getSyncedSettings() {
        const store = useClaudeCodeStore()
        const result = {}
        for (const [key, { getter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            result[key] = store[getter]
        }
        return result
    }

    getAgentSettingsCategories() {
        return useClaudeCodeStore().agentSettingsCategories
    }

    getAgentSettingsChoices() {
        return AGENT_SETTINGS_CHOICES
    }

    /**
     * Build a human-friendly label for a Claude Code ``selected_model`` value.
     * "opus" → "Opus", "opus-4.5" → "Opus 4.5", "sonnet" → "Sonnet"
     */
    getModelLabel(selectedModel) {
        if (!selectedModel) return ''
        if (selectedModel.includes('-')) {
            const [model, version] = selectedModel.split('-', 2)
            return `${model.charAt(0).toUpperCase() + model.slice(1)} ${version}`
        }
        return selectedModel.charAt(0).toUpperCase() + selectedModel.slice(1)
    }

    // ─── Model registry & capability flags ───────────────────────────────
    //
    // Wrap reads against the per-provider model registry held by the store.
    // Mirrors the backend ``selected_model_supports_*`` helpers: when the
    // explicit ``selectedModel`` is unknown to the registry, fall back to the
    // current synced default model. The conservative last-resort answer is
    // ``false`` so optional features aren't silently advertised when the
    // registry hasn't been seeded yet.

    getModelRegistry() {
        return useClaudeCodeStore().modelRegistry
    }

    _resolveRegistryEntry(selectedModel) {
        const store = useClaudeCodeStore()
        const registry = store.modelRegistry
        let entry = selectedModel ? registry.find(e => e.selected_model === selectedModel) : undefined
        if (!entry) {
            const defaultModel = store.defaultModel
            if (defaultModel) entry = registry.find(e => e.selected_model === defaultModel)
        }
        return entry
    }

    modelSupports1m(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_1m : false
    }

    modelSupportsEffortXhigh(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_effort_xhigh : false
    }

    modelSupportsEffortMax(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_effort_max : false
    }

    modelSupportsFast(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_fast : false
    }

    modelSupportsPermissionAuto(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_permission_auto : false
    }

    modelSupportsHighresImages(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_highres_images : false
    }

    modelSupportsThinkingDisabled(selectedModel) {
        // Default ``true`` (allow disabling) when the registry isn't seeded
        // yet, so we never lock the toggle on a model we can't resolve.
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? entry.provider_extra.supports_thinking_disabled : true
    }

    /**
     * Pipeline mirroring the backend ``ClaudeCodeHelpers.enforce_agent_settings_consistency``:
     *
     * 1. Auto-upgrade ``selectedModel`` when retired.
     * 2. Cap ``contextMax`` to ``DEFAULT`` when the (post-upgrade) model
     *    doesn't support 1M context.
     * 3. Demote ``effort``: ``MAX`` → ``X_HIGH`` (or ``HIGH`` if xhigh is
     *    also unsupported), then ``X_HIGH`` → ``HIGH`` when unsupported.
     * 4. Clear ``fastMode`` when the (post-upgrade) model doesn't support it
     *    (only supported on Opus 4.6+).
     * 5. Demote ``permissionMode === AUTO`` to ``DEFAULT`` when the
     *    (post-upgrade) model doesn't support auto (only Opus 4.6+ /
     *    Sonnet 4.6+).
     * 6. Force ``thinkingEnabled`` on when the (post-upgrade) model can't
     *    disable thinking (Fable 5: adaptive thinking is always on).
     *
     * Fields not in the input are left absent in the output.
     * ``claudeInChrome`` is passed through.
     */
    enforceAgentSettingsConsistency(settings) {
        const result = { ...settings }

        if ('selectedModel' in result) {
            result.selectedModel = this.resolveToAvailableModel(result.selectedModel)
        }
        const model = result.selectedModel

        if (result.contextMax === CONTEXT_MAX.EXTENDED && !this.modelSupports1m(model)) {
            result.contextMax = CONTEXT_MAX.DEFAULT
        }

        if (result.effort === EFFORT.MAX && !this.modelSupportsEffortMax(model)) {
            result.effort = this.modelSupportsEffortXhigh(model) ? EFFORT.X_HIGH : EFFORT.HIGH
        }
        if (result.effort === EFFORT.X_HIGH && !this.modelSupportsEffortXhigh(model)) {
            result.effort = EFFORT.HIGH
        }

        if (result.fastMode && !this.modelSupportsFast(model)) {
            result.fastMode = false
        }

        if (result.permissionMode === PERMISSION_MODE.AUTO && !this.modelSupportsPermissionAuto(model)) {
            result.permissionMode = PERMISSION_MODE.DEFAULT
        }

        if (result.thinkingEnabled === false && !this.modelSupportsThinkingDisabled(model)) {
            result.thinkingEnabled = true
        }

        return result
    }

    /**
     * The auto-promote rule itself: at 200K, with a model that supports 1M,
     * once the session has burned past 85% of the 200K window, promote to 1M.
     * Stateless and parameterised — callers pass the ``contextMax`` they want
     * to evaluate the rule against (the persisted value, or the user's live
     * selection in the popover).
     */
    isContextMaxAutoPromoted(session, contextMax, model) {
        return (
            contextMax === CONTEXT_MAX.DEFAULT
            && this.modelSupports1m(model)
            && (session?.context_usage ?? 0) > CONTEXT_MAX.DEFAULT * 0.85
        )
    }

    isContextMaxLocked(session, model) {
        // Locked whenever the session would auto-promote from 200K — i.e. usage
        // exceeds 85% of 200K on a 1M-capable model. Value-independent, so it
        // stays locked even when the session is already stored at 1M (which is
        // why it can't just reuse the value-sensitive ``isContextMaxForced``).
        return this.isContextMaxAutoPromoted(session, CONTEXT_MAX.DEFAULT, model)
    }

    /**
     * Resolve a session's effective ``context_max``: the persisted value (or
     * the provider default when null), bumped to 1M by the auto-promote rule
     * when applicable. Single source of truth for the header progress ring
     * and the value the popover sends to the backend.
     */
    getEffectiveContextMax(session, overrideModel = undefined) {
        const store = useClaudeCodeStore()
        const baseValue = session?.context_max ?? store.defaultContextMax
        const model = overrideModel !== undefined ? overrideModel : (session?.selected_model ?? store.defaultModel)
        return this.isContextMaxAutoPromoted(session, baseValue, model) ? CONTEXT_MAX.EXTENDED : baseValue
    }

    // ─── Popover/summary rendering hooks ────────────────────────────────

    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') {
            const resolved = this.resolveToAvailableModel(value)
            const entry = this.getModelRegistry().find(e => e.selected_model === resolved)
            if (entry?.latest) return `${this.getModelLabel(resolved)} (latest: ${entry.version})`
            return this.getModelLabel(resolved)
        }
        return super.getDefaultValueLabel(field, value)
    }

    isChoiceDisabled(field, choiceValue, context) {
        if (super.isChoiceDisabled(field, choiceValue, context)) return true
        if (field === 'effort') {
            if (choiceValue === EFFORT.X_HIGH) return !this.modelSupportsEffortXhigh(context?.effectiveModel)
            if (choiceValue === EFFORT.MAX) return !this.modelSupportsEffortMax(context?.effectiveModel)
        }
        if (field === 'permission_mode' && choiceValue === PERMISSION_MODE.AUTO) {
            return !this.modelSupportsPermissionAuto(context?.effectiveModel)
        }
        if (field === 'thinking_enabled' && choiceValue === false) {
            return !this.modelSupportsThinkingDisabled(context?.effectiveModel)
        }
        // Gate the "on" value so ``fieldHasChoice`` (hence the switch's
        // visibility) is false on models without fast mode — the switch is
        // hidden rather than shown disabled.
        if (field === 'fast_mode' && choiceValue === true) {
            return !this.modelSupportsFast(context?.effectiveModel)
        }
        return false
    }

    isFieldDisabled(field, context) {
        if (field === 'context_max') {
            if (context?.isStarting) return true
            if (context?.isContextMaxForced) return true
            if (!this.modelSupports1m(context?.effectiveModel)) return true
            return false
        }
        if (field === 'fast_mode') {
            if (!this.modelSupportsFast(context?.effectiveModel)) return true
            return false
        }
        return super.isFieldDisabled(field, context)
    }

    fieldHasChoice(field, context) {
        // 1M context is gated on the model (see isFieldDisabled), not per
        // option, so the base per-option count would wrongly see two choices.
        if (field === 'context_max') return this.modelSupports1m(context?.effectiveModel)
        return super.fieldHasChoice(field, context)
    }

    getFieldHelpText(field, context) {
        if (field === 'context_max') {
            if (context?.isContextMaxForced) return 'Forced to 1M: context usage exceeds 85% of 200K.'
            if (!this.modelSupports1m(context?.effectiveModel)) return '1M not available for this model version.'
            return null
        }
        if (field === 'fast_mode') {
            if (!this.modelSupportsFast(context?.effectiveModel)) return 'Fast mode is only available on supported Opus versions.'
            // When fast mode is available, the cost note lives under the
            // ``Enabled`` option (see ``AGENT_SETTINGS_CHOICES.fast_mode``)
            // so the field-level help stays empty to avoid duplication.
            return null
        }
        if (field === 'thinking_enabled') {
            if (!this.modelSupportsThinkingDisabled(context?.effectiveModel)) {
                return 'Always on for this model — thinking cannot be disabled.'
            }
            return null
        }
        if (field === 'permission_mode' && context?.isHybrid) {
            // Hybrid classification differs from the SDK's: permission_mode
            // is STARTUP there (no reliable external setter in the TUI).
            // Chain super so the trust-clamp note still wins when forced.
            return super.getFieldHelpText(field, context)
                ?? 'Hybrid CLI: applied on the next message by restarting the CLI.'
        }
        return super.getFieldHelpText(field, context)
    }

    getFieldNotice(field, context) {
        // Context auto-promoted to 1M (usage > 85% of 200K): the switch is shown
        // on + disabled with a warning icon explaining the forced state.
        if (field === 'context_max' && context?.isContextMaxForced) {
            return {
                icon: 'triangle-exclamation',
                variant: 'warning',
                text: 'Forced to 1M: context usage exceeds 85% of 200K.',
            }
        }
        // Fast mode (only shown on supported models): a brand info icon (circle
        // exclamation) when off, escalating to a warning icon while it's ON and
        // actively costing more. Same extra-cost tooltip either way.
        if (field === 'fast_mode') {
            const on = (context?.selectedValue ?? context?.defaultValue) === true
            const enabled = AGENT_SETTINGS_CHOICES.fast_mode.find(c => c.value === true)
            return {
                icon: on ? 'triangle-exclamation' : 'circle-exclamation',
                variant: on ? 'warning' : 'brand',
                text: enabled?.description ?? '',
            }
        }
        return super.getFieldNotice(field, context)
    }

    getDisplayedSelectValue(field, selectedValue, context) {
        if (field === 'context_max' && context?.isContextMaxForced) {
            return String(CONTEXT_MAX.EXTENDED)
        }
        return super.getDisplayedSelectValue(field, selectedValue, context)
    }

    // Fold the extended-context window into the summary model label as a
    // "[1m]" suffix (e.g. "Opus 4.8[1m]") rather than a standalone part.
    getSummaryModelSuffix(state) {
        const sel = state?.selected ?? {}
        const def = state?.defaults ?? {}
        const effectiveContextMax = sel.context_max ?? def.context_max
        return effectiveContextMax === CONTEXT_MAX.EXTENDED ? '[1m]' : ''
    }

    isSummaryContextForced(state) {
        const sel = state?.selected ?? {}
        const def = state?.defaults ?? {}
        return sel.context_max !== null && sel.context_max !== undefined && sel.context_max !== def.context_max
    }

    buildModelSelectGroups(registry) {
        const list = registry ?? []
        return [
            {
                entries: list.filter(e => e.latest).map(e => this.buildModelOption(
                    e, `${this.getModelLabel(e.selected_model)} (latest: ${e.version})`,
                )),
            },
            {
                // An older version usually carries an end-of-service date, but
                // not always — a freshly superseded model can sit here with
                // ``retirement_date: null`` until Anthropic announces one, and
                // formatting that would print "(until Invalid Date)".
                entries: list.filter(e => !e.latest).map(e => this.buildModelOption(
                    e,
                    e.retirement_date
                        ? `${this.getModelLabel(e.selected_model)} (until ${formatRetirementDate(e.retirement_date)})`
                        : this.getModelLabel(e.selected_model),
                )),
            },
        ]
    }

    /**
     * Claude Code supports images, PDF, and plain-text uploads via the
     * SDK's content-block protocol. Images are resized at upload time to
     * the shared ``MAX_IMAGE_DIMENSION`` (2576 px, Opus 4.7's native
     * resolution — the most generous supported by any model we target),
     * not to Sonnet/Haiku's tighter 1568 px ceiling. The actual send-
     * time re-resize (down to 1568, 2000, or 2576 px) is decided per
     * (model, num_images) by ``MessageInput.handleSend`` so a stored
     * blob can serve any model without losing the option to use Opus
     * 4.7 at full resolution.
     */
    getAttachmentSupport() {
        return {
            images: true,
            documents: true,
            maxBytes: CLAUDE_MAX_FILE_BYTES,
            acceptedMimeTypes: [
                ...SUPPORTED_IMAGE_TYPES,
                ...SUPPORTED_DOCUMENT_TYPES,
                ...SUPPORTED_TEXT_TYPES,
            ],
            resizeImages: true,
        }
    }

    /**
     * Send-time long-edge cap for Claude. The rules track Anthropic's
     * published limits (see the vision docs):
     *
     *   - Models without ``supports_highres_images`` (Sonnet / Haiku /
     *     older Opus): native resolution is 1568 px, so anything larger
     *     is downscaled server-side anyway. Resize client-side to save
     *     bandwidth and keep token usage predictable.
     *   - Models with ``supports_highres_images`` (Fable, Opus 4.7+):
     *     native resolution is 2576 px (the storage dimension). Ship
     *     as-is.
     *   - Whenever the request carries more than 20 images, Anthropic
     *     caps every image at 2000 px regardless of model. Apply the
     *     tighter of {model cap, 2000} in that case.
     */
    getEffectiveImageDimension({ model, numImages } = {}) {
        const resolved = this.resolveToAvailableModel(model)
        const highres = this.modelSupportsHighresImages(resolved)
        let cap = highres ? null : 1568
        if ((numImages ?? 0) > 20) {
            cap = cap === null ? 2000 : Math.min(cap, 2000)
        }
        return cap
    }
}

export const claudeCodeHelpers = new ClaudeCodeHelpers()
