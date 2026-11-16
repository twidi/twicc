import { i18n } from '../../i18n/index.js'
import { matchesStreamingBlock } from './streamMatching.js'
import { buildEphemeralResultContent } from '../ephemeralContent.js'
import { BaseProviderHelpers, formatRetirementDate } from '../baseHelpers'
import { PROVIDER, SYNTHETIC_ITEM } from '../../constants'
import { EFFORT, PERMISSION_MODE, UNTRUSTED_PERMISSION_MODES } from './constants'
import { useGeminiStore } from './store'
import { buildUserRecord, userMessageAttachmentCount, userMessageText } from './records'

// Map of agent-setting wire names → store getter/setter for the persisted
// default. Used by ``getDefaultValue`` / ``setDefaultValue`` so generic
// surfaces (palette, settings popover) can read/write defaults without
// knowing the field-specific store property names.
const FIELD_TO_DEFAULT_STORE_BINDING = {
    selected_model:  { getter: 'defaultModel',          setter: 'setDefaultModel' },
    effort:          { getter: 'defaultEffort',         setter: 'setDefaultEffort' },
    permission_mode: { getter: 'defaultPermissionMode', setter: 'setDefaultPermissionMode' },
    permission_mode_if_untrusted: { getter: 'defaultUntrustedPermissionMode', setter: 'setDefaultUntrustedPermissionMode' },
}

// Map of synced setting keys (the wire/storage names) → store action that
// applies the value. Used by both ``applySyncedSettings`` (input) and
// ``getSyncedSettings`` (output) so the two sides can never drift apart.
const SYNCED_SETTING_KEYS_TO_STORE = {
    geminiDefaultModel:          { setter: 'setDefaultModel',          getter: 'defaultModel' },
    geminiDefaultEffort:         { setter: 'setDefaultEffort',         getter: 'defaultEffort' },
    geminiDefaultPermissionMode: { setter: 'setDefaultPermissionMode', getter: 'defaultPermissionMode' },
    geminiDefaultUntrustedPermissionMode: { setter: 'setDefaultUntrustedPermissionMode', getter: 'defaultUntrustedPermissionMode' },
}

// Per-field choice catalogue for the Gemini provider. A function so the
// i18n labels re-evaluate on locale change. ``selected_model`` is absent:
// the model list is served via the model registry (see ``getModelRegistry``).
const AGENT_SETTINGS_CHOICES = () => ({
    // ``icon``/``color`` drive the per-mode glyph shown in the permission
    // selects (utils/permissionModeIcon.js). Modes follow the cross-provider
    // tiers documented in codex/helpers.js: plan↔plan (purple, safest),
    // default↔default (green, asks), auto_edit↔acceptEdits (yellow),
    // yolo↔bypass (red, no guardrail). Order = severity ramp, most
    // restrictive → most permissive.
    permission_mode: [
        {
            value: PERMISSION_MODE.PLAN,
            label: i18n.global.t('agentSettings.permission.plan'),
            description: i18n.global.t('agentSettings.gemini.planDescription'),
            icon: 'clipboard-list',
            color: 'var(--wa-color-purple-60)',
        },
        {
            value: PERMISSION_MODE.DEFAULT,
            label: i18n.global.t('agentSettings.permission.default'),
            description: i18n.global.t('agentSettings.gemini.defaultDescription'),
            icon: 'eye',
            color: 'var(--wa-color-green-60)',
        },
        {
            value: PERMISSION_MODE.AUTO_EDIT,
            label: i18n.global.t('agentSettings.gemini.autoEdit'),
            description: i18n.global.t('agentSettings.gemini.autoEditDescription'),
            icon: 'pen-to-square',
            color: 'var(--wa-color-yellow-60)',
        },
        {
            value: PERMISSION_MODE.YOLO,
            label: 'YOLO',
            description: i18n.global.t('agentSettings.gemini.yoloDescription'),
            icon: 'triangle-exclamation',
            color: 'var(--wa-color-red-60)',
        },
    ],
    // The Gemini CLI has no effort control: a single level keeps the
    // model × effort matrix clickable (see constants.js).
    effort: [
        { value: EFFORT.MEDIUM, label: i18n.global.t('agentSettings.effort.medium'), display_label: i18n.global.t('agentSettings.effort.mediumDisplay') },
    ],
})

export class GeminiHelpers extends BaseProviderHelpers {
    matchesStreamingBlock(parsed, itemKind, messageId, block) {
        return matchesStreamingBlock(parsed, itemKind, messageId, block)
    }

    static provider = PROVIDER.GEMINI
    static label = 'Gemini'
    static icon = 'google'
    static iconColor = 'var(--wa-color-blue-60)'
    static serviceProductLabel = 'Gemini CLI'
    static serviceVendorLabel = 'Google'
    static serviceStatusUrl = null

    canStopSubagent() {
        // Gemini subagent sessions are out of scope in v1: there is no
        // subagent to stop.
        return false
    }

    buildEphemeralResultContent(text) {
        return buildEphemeralResultContent(PROVIDER.GEMINI, text)
    }

    buildOptimisticUserMessageContent(text) {
        // Attachments are not supported by the Gemini provider in v1: the
        // optimistic bubble carries the text only, like the record the CLI
        // will write.
        const content = buildUserRecord(text)
        content.syntheticKind = SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.kind
        return content
    }

    extractUserMessageText(parsed) {
        return userMessageText(parsed).trim() || null
    }

    extractUserMessageAttachmentCount(parsed) {
        return userMessageAttachmentCount(parsed)
    }

    getUntrustedPermissionModes() {
        return UNTRUSTED_PERMISSION_MODES
    }

    getDefaultValue(field) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return null
        return useGeminiStore()[binding.getter]
    }

    setDefaultValue(field, value) {
        const binding = FIELD_TO_DEFAULT_STORE_BINDING[field]
        if (!binding) return
        useGeminiStore()[binding.setter](value)
    }

    getSyncedSettingsKeys() {
        return Object.keys(SYNCED_SETTING_KEYS_TO_STORE)
    }

    applySyncedSettings(settings) {
        if (!settings || typeof settings !== 'object') return
        const store = useGeminiStore()
        for (const [key, { setter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            if (key in settings) store[setter](settings[key])
        }
    }

    getSyncedSettings() {
        const store = useGeminiStore()
        const result = {}
        for (const [key, { getter }] of Object.entries(SYNCED_SETTING_KEYS_TO_STORE)) {
            result[key] = store[getter]
        }
        return result
    }

    getAgentSettingsCategories() {
        return useGeminiStore().agentSettingsCategories
    }

    getAgentSettingsChoices() {
        return AGENT_SETTINGS_CHOICES()
    }

    /**
     * The model's context window, from the registry (``provider_extra``).
     * Gemini has no ``context_max`` setting, so ``session.context_max`` stays
     * null: without this, the context ring would divide by null.
     */
    getEffectiveContextMax(session, overrideModel = undefined) {
        const model = overrideModel !== undefined ? overrideModel : session?.selected_model
        const registry = this.getModelRegistry()
        const entry = (model && registry.find(e => e.selected_model === model))
            || registry.find(e => e.provider_extra?.context_window)
        return entry?.provider_extra?.context_window ?? session?.context_max ?? null
    }

    getModelRegistry() {
        return useGeminiStore().modelRegistry
    }

    /**
     * Human-friendly label for a Gemini ``selected_model`` value:
     * "gemini-flash" → "Gemini Flash", "gemini-flash-lite" → "Gemini Flash
     * Lite", "gemini-pro-3.5" → "Gemini Pro 3.5".
     */
    getModelLabel(selectedModel) {
        if (!selectedModel) return ''
        return selectedModel
            .split('-')
            .map(part => (/^[a-z]/i.test(part) ? part.charAt(0).toUpperCase() + part.slice(1) : part))
            .join(' ')
    }

    /** Short grid-row label: drop the "Gemini" family ("Flash Lite"). */
    getModelShortLabel(selectedModel) {
        if (!selectedModel) return ''
        if (!selectedModel.startsWith('gemini-')) return this.getModelLabel(selectedModel)
        return this.getModelLabel(selectedModel.slice('gemini-'.length))
    }

    /**
     * Registry entry label with the end-of-service date when the model has
     * one, otherwise the family version for a family's latest, otherwise
     * nothing (same rule as Codex).
     */
    _modelLabelWithMetadata(entry, model) {
        if (entry?.retirement_date) return i18n.global.t('agentSettings.model.until', { model, date: formatRetirementDate(entry.retirement_date) })
        if (entry?.latest) return i18n.global.t('agentSettings.model.latest', { model, version: entry.version })
        return model
    }

    getDefaultValueLabel(field, value) {
        if (field === 'selected_model') {
            const resolved = this.resolveToAvailableModel(value)
            const entry = this.getModelRegistry().find(e => e.selected_model === resolved)
            return this._modelLabelWithMetadata(entry, this.getModelLabel(resolved))
        }
        return super.getDefaultValueLabel(field, value)
    }

    buildModelSelectGroups(registry) {
        const list = registry ?? []
        const option = e => this.buildModelOption(
            e, this._modelLabelWithMetadata(e, this.getModelLabel(e.selected_model)),
        )
        return [
            { entries: list.filter(e => e.latest).map(option) },
            { entries: list.filter(e => !e.latest).map(option) },
        ]
    }
}

export const geminiHelpers = new GeminiHelpers()
