<script setup>
// Per-project agent settings defaults, edited inside ProjectEditDialog (edit
// mode only). Lets a project declare a default provider + per-provider agent
// settings bundles that seed a new session's settings, inherited up the project
// hierarchy (resolution mirrored in utils/projectAgentDefaults.js; the backend
// re-resolves the same chain). NULL/absent fields inherit from parent projects,
// then the global defaults.
//
// Tabs cover ALL registered providers — including currently-disabled ones, since
// a deactivation may be temporary and the project should keep its defaults.
//
// Field rows reuse the same provider rendering hooks as AgentSettingsPopover /
// AgentSettingsPresetsDialog (getFieldLabel / getFieldChoices /
// getModelSelectGroups), so a new provider needs zero template changes here.
//
// This component owns its local draft state and exposes getChangedFields() to
// the parent, which folds the result into its existing PUT /api/projects/<id>/.
//
// Class prefix: ``agset-``, never ``ad-``. Content blockers ship generic cosmetic
// filters on advertising class names, and ``.ad-field`` is one of them — under
// uBlock Origin (Firefox Android) every settings row was hidden while its
// siblings rendered. Keep any new class here away from ad/ads/banner/sponsor.

import { ref, computed, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers, getProviderStore, getProviderOptions, getProviderLabel } from '../../providers'
import ProviderIcon from '../ui/ProviderIcon.vue'
import { useAgentSettingsPresetsStore } from '../../stores/agentSettingsPresets'
import { ancestorChain, resolveProjectAgentDefaults } from '../../utils/projectAgentDefaults'
import { bundleSummaryParts } from '../../utils/presetFormat'
import AgentSettingsSummaryView from '../message/AgentSettingsSummaryView.vue'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import { effortIconSrc } from '../../utils/effortIcon'
import { AGENT_SETTING_ICONS } from '../../utils/agentSettingIcons'
import SettingFlagIcon from '../ui/SettingFlagIcon.vue'
import PermissionModeIcon from '../ui/PermissionModeIcon.vue'
import TabBar from '../ui/TabBar.vue'

const props = defineProps({
    project: { type: Object, required: true },
})

const store = useDataStore()
const settingsStore = useSettingsStore()
const presetsStore = useAgentSettingsPresetsStore()

// Canonical wire field names (the same order/shape as the preset form).
// ``permission_mode_if_untrusted`` is the default-shaping pseudo-field used
// when the project resolves untrusted (trust design §13.1) — stored in the
// bundle like any other field, but never a Session column.
const FIELD_ORDER = ['selected_model', 'context_max', 'effort', 'thinking_enabled', 'permission_mode', 'permission_mode_if_untrusted', 'claude_in_chrome', 'fast_mode']
// Preset records use historical key names; default_agent_settings uses wire names.
const PRESET_TO_WIRE = {
    model: 'selected_model',
    context_max: 'context_max',
    effort: 'effort',
    thinking: 'thinking_enabled',
    permission_mode: 'permission_mode',
    permission_mode_if_untrusted: 'permission_mode_if_untrusted',
    claude_in_chrome: 'claude_in_chrome',
    fast_mode: 'fast_mode',
}

// All registered providers (enabled + disabled).
const providers = computed(() => getProviderOptions())
const enabledProviders = computed(() => new Set(settingsStore.enabledProviders))

// ─── Local draft state ───────────────────────────────────────────────────────
// localProvider: '' = inherit, else a provider value.
// localSettings: { <provider>: { <wire field>: value } } — sparse (absent = inherit).
const localProvider = ref('')
const localSettings = ref({})
const activeTab = ref('')

function initLocal() {
    localProvider.value = props.project?.default_provider ?? ''
    localSettings.value = JSON.parse(JSON.stringify(props.project?.default_agent_settings ?? {}))
    // Open on the project's default provider when it's enabled, else the first
    // enabled provider — no point landing on a disabled tab. Fall back to the
    // very first provider only if every provider is disabled.
    const preferred = props.project?.default_provider
    activeTab.value = (preferred && isEnabled(preferred))
        ? preferred
        : (providers.value.find(p => isEnabled(p.value))?.value ?? providers.value[0]?.value ?? '')
}
watch(() => props.project, initLocal, { immediate: true })

// ─── Inherited default provider (shown when the choice is "inherit") ──────────
const inheritedProvider = computed(() => {
    for (const node of ancestorChain(props.project.id, store.projects)) {
        if (node.id === props.project.id) continue  // skip self — we show the fallback
        if (node.default_provider) return node.default_provider
    }
    return settingsStore.defaultProvider || null
})
const inheritedProviderLabel = computed(() =>
    inheritedProvider.value ? getProviderLabel(inheritedProvider.value) : 'none'
)

// ─── Per-provider field helpers ──────────────────────────────────────────────
function helpersFor(provider) {
    return getProviderHelpers(provider)
}
function supportedFields(provider) {
    const h = helpersFor(provider)
    if (!h) return []
    return FIELD_ORDER.filter(f => h.supportsAgentSetting(f))
}
function modelGroups(provider) {
    const h = helpersFor(provider)
    if (!h) return []
    return h.getModelSelectGroups(h.getModelRegistry())
}
function fieldLabel(provider, field) {
    return helpersFor(provider)?.getFieldLabel(field) ?? field
}
function fieldChoices(provider, field) {
    return helpersFor(provider)?.getFieldChoices(field) ?? []
}
function fieldValue(provider, field) {
    const v = localSettings.value[provider]?.[field] ?? null
    // A stored default model may have become unavailable (disabled/retired);
    // show its effective substitute so the select isn't left blank (a wa-select
    // can't display a disabled option as selected). Mirrors the session popover.
    if (field === 'selected_model' && v) return helpersFor(provider)?.resolveToAvailableModel(v) ?? v
    return v
}
// Warning shown above the model select when this project's explicitly-set
// default model has become unavailable and resolves to a different model.
function modelFallbackNotice(provider) {
    const raw = localSettings.value[provider]?.selected_model ?? null
    return helpersFor(provider)?.getModelFallbackNotice(raw) ?? null
}
function isEnabled(provider) {
    return enabledProviders.value.has(provider)
}

function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}
// Effort-level icon for the closed select of the effort field — shown only when
// a concrete effort is forced (not "Inherit").
function effortSelectIcon(provider, field) {
    if (field !== 'effort') return null
    if (toSentinel(fieldValue(provider, field)) === DEFAULT_SENTINEL) return null
    return effortIconSrc(fieldValue(provider, field))
}
// Boolean flag fields (thinking / fast mode / Chrome MCP) carry an on/off icon.
const isFlagField = (field) => field in AGENT_SETTING_ICONS
// Show the flag icon in the closed select only when a concrete value is forced.
function flagSelectShown(provider, field) {
    return isFlagField(field) && toSentinel(fieldValue(provider, field)) !== DEFAULT_SENTINEL
}
const flagValueOn = (provider, field) => fieldValue(provider, field) === true
// Permission-mode fields carry a per-mode glyph.
const isPermissionField = (field) => field === 'permission_mode' || field === 'permission_mode_if_untrusted'
// Permission icon for the closed select — only when a concrete mode is forced
// (not "Inherit"). Mirrors effortSelectIcon.
function permissionSelectIcon(provider, field) {
    if (!isPermissionField(field)) return null
    if (toSentinel(fieldValue(provider, field)) === DEFAULT_SENTINEL) return null
    return helpersFor(provider)?.getChoiceIcon(field, fieldValue(provider, field)) ?? null
}
function fromSentinel(provider, field, raw) {
    if (raw === DEFAULT_SENTINEL) return null
    if (field === 'selected_model') return raw
    const match = fieldChoices(provider, field).find(opt => String(opt.value) === raw)
    return match ? match.value : raw
}

// Immutable nested update so reactivity tracks the change; null = inherit, which
// drops the key to keep the stored bundle sparse.
function setField(provider, field, rawValue) {
    const value = fromSentinel(provider, field, rawValue)
    const next = { ...(localSettings.value[provider] || {}) }
    if (value === null) delete next[field]
    else next[field] = value
    localSettings.value = { ...localSettings.value, [provider]: next }
}

// ─── "Load from…" (named presets + ancestor projects' bundles) ────────────────
// Resolve a source key to its wire-named bundle — shared by the picker's value
// summary and the actual load, so the two never drift.
function sourceBundle(provider, key) {
    if (key === 'inherit-all') return {}  // clear every field → inherit
    if (key.startsWith('preset:')) {
        const index = Number(key.slice('preset:'.length))
        const preset = (presetsStore.getPresets(provider) || [])[index]
        return preset ? presetToBundle(preset) : {}
    }
    if (key.startsWith('ancestor:')) {
        const nodeId = key.slice('ancestor:'.length)
        return { ...(store.getProject(nodeId)?.default_agent_settings?.[provider] || {}) }
    }
    if (key === 'global') return globalBundleFor(provider)
    return {}
}
function loadSources(provider) {
    const h = helpersFor(provider)
    const sources = []
    // "Reset all to inherit": clear every field for this provider. Only shown
    // when at least one field is currently set, since otherwise it's a no-op.
    if (Object.keys(localSettings.value[provider] || {}).length) {
        sources.push({ key: 'inherit-all', label: 'Reset all to inherit', summaryParts: [{ text: 'every field inherits' }] })
    }
    // Ancestor projects that define their own defaults for this provider.
    for (const node of ancestorChain(props.project.id, store.projects)) {
        if (node.id === props.project.id) continue
        const bundle = node.default_agent_settings?.[provider]
        if (bundle && Object.keys(bundle).length) {
            const name = node.name || node.directory || node.id
            sources.push({ key: `ancestor:${node.id}`, label: `Project: ${name}` })
        }
    }
    // The provider's global (synced) defaults.
    sources.push({ key: 'global', label: 'Global defaults' })
    // Named presets.
    const presets = presetsStore.getPresets(provider) || []
    presets.forEach((preset, index) => {
        sources.push({ key: `preset:${index}`, label: `Preset: ${preset.name}` })
    })
    // Resolve every partial source against the current project's inherited
    // baseline. The summary therefore matches the concrete settings a new
    // session would receive after the source is loaded.
    return h
        ? sources.map(s => {
            if (s.summaryParts !== undefined) return s
            const parts = bundleSummaryParts(sourceBundle(provider, s.key), h, {
                defaults: inheritedDefaultsFor(provider),
            })
            return { ...s, summaryParts: parts.length ? parts : [{ text: 'all default' }] }
        })
        : sources
}
function presetToBundle(preset) {
    const bundle = {}
    for (const [presetKey, wire] of Object.entries(PRESET_TO_WIRE)) {
        if (preset[presetKey] != null) bundle[wire] = preset[presetKey]
    }
    return bundle
}
// The provider's global (synced) defaults as a wire-named bundle.
function globalBundleFor(provider) {
    const s = getProviderStore(provider)
    if (!s) return {}
    return {
        selected_model: s.defaultModel,
        context_max: s.defaultContextMax,
        effort: s.defaultEffort,
        thinking_enabled: s.defaultThinking,
        permission_mode: s.defaultPermissionMode,
        permission_mode_if_untrusted: s.defaultUntrustedPermissionMode,
        claude_in_chrome: s.defaultClaudeInChrome,
        fast_mode: s.defaultFastMode,
    }
}

// The baseline for sparse values edited on this project. Skip the project's
// current bundle, then resolve its parent/worktree/path chain over the global
// provider defaults.
function inheritedDefaultsFor(provider) {
    return {
        ...globalBundleFor(provider),
        ...resolveProjectAgentDefaults(props.project.id, provider, store.projects, {
            includeSelf: false,
        }),
    }
}
function loadFrom(provider, event) {
    const key = event.target.value
    event.target.value = ''  // reset back to the placeholder
    if (!key) return
    const bundle = sourceBundle(provider, key)
    const clean = {}
    for (const field of FIELD_ORDER) {
        if (bundle[field] != null) clean[field] = bundle[field]
    }
    localSettings.value = { ...localSettings.value, [provider]: clean }
}

// ─── Exposed to the parent dialog ────────────────────────────────────────────
// Normalize to sparse storage (drop null fields + empty bundles → null), so
// comparisons and what we send match the backend's own normalization.
function normalizeSettings(raw) {
    const out = {}
    for (const provider of Object.keys(raw || {})) {
        const bundle = {}
        for (const field of FIELD_ORDER) {
            if (raw[provider]?.[field] != null) bundle[field] = raw[provider][field]
        }
        if (Object.keys(bundle).length) out[provider] = bundle
    }
    return Object.keys(out).length ? out : null
}

// Returns only the fields that actually changed vs the project, so the parent's
// PUT leaves untouched fields alone (the backend only updates keys it receives).
function getChangedFields() {
    const changed = {}
    const newProvider = localProvider.value || null
    if (newProvider !== (props.project.default_provider ?? null)) {
        changed.default_provider = newProvider
    }
    const newSettings = normalizeSettings(localSettings.value)
    const oldSettings = normalizeSettings(props.project.default_agent_settings)
    if (JSON.stringify(newSettings) !== JSON.stringify(oldSettings)) {
        changed.default_agent_settings = newSettings
    }
    return changed
}

defineExpose({ getChangedFields, reset: initLocal })
</script>

<template>
    <div class="form-group">
        <label class="form-label">{{ $t('project.agentDefaults.providerLabel') }}</label>
        <p class="agset-intro">
            {{ $t('project.agentDefaults.providerHint') }}
        </p>
        <wa-select
            :value.prop="localProvider"
            @change="localProvider = $event.target.value"
            size="small"
            class="agset-provider-select"
        >
            <ProviderIcon
                v-if="localProvider"
                slot="start"
                :provider="localProvider"
            />
            <wa-option value="" :label="$t('common.options.inherit')">{{ $t('common.options.inherit') }}</wa-option>
            <wa-option
                v-for="p in providers"
                :key="p.value"
                :value="p.value"
                :label="isEnabled(p.value) ? getProviderLabel(p.value) : $t('project.agentDefaults.disabledProvider', { provider: getProviderLabel(p.value) })"
            >
                <ProviderIcon :provider="p.value" class="provider-option-icon" />
                {{ isEnabled(p.value) ? getProviderLabel(p.value) : $t('project.agentDefaults.disabledProvider', { provider: getProviderLabel(p.value) }) }}
            </wa-option>
        </wa-select>
        <div v-if="localProvider === ''" class="form-hint">
            <i18n-t keypath="project.agentDefaults.inheritedProvider" scope="global"><template #provider><strong>{{ inheritedProviderLabel }}</strong></template></i18n-t>
        </div>
    </div>

    <div class="form-group">
        <label class="form-label">{{ $t('project.agentDefaults.settingsLabel') }}</label>
        <p class="agset-intro">
            {{ $t('project.agentDefaults.settingsHint') }}
        </p>
        <!-- .stop on the tab events so our per-provider tabs never bubble up to
             the dialog's main "Project / Agent settings" tab-group; the handler
             keeps activeTab in sync so the controlled :active never snaps back. -->
        <TabBar
            :active="activeTab"
            @wa-tab-show.stop="activeTab = $event.detail?.name ?? activeTab"
            @wa-tab-hide.stop
        >
            <wa-tab
                v-for="p in providers"
                :key="p.value"
                slot="nav"
                :panel="p.value"
            >
                <ProviderIcon :provider="p.value" class="agset-tab-icon" />
                {{ getProviderLabel(p.value) }}<span v-if="!isEnabled(p.value)" class="agset-disabled-badge">{{ $t('project.agentDefaults.disabled') }}</span>
            </wa-tab>

            <wa-tab-panel v-for="p in providers" :key="p.value" :name="p.value">
                <wa-select
                    v-if="loadSources(p.value).length"
                    value=""
                    @change="loadFrom(p.value, $event)"
                    :placeholder="$t('project.agentDefaults.loadFrom')"
                    size="small"
                    class="agset-load-select"
                >
                    <wa-option v-for="src in loadSources(p.value)" :key="src.key" :value="src.key" :label="src.label">
                        <span>{{ src.label }}</span>
                        <AgentSettingsSummaryView
                            v-if="src.summaryParts?.length"
                            class="agset-option-summary"
                            :parts="src.summaryParts"
                        />
                    </wa-option>
                </wa-select>

                <template v-for="field in supportedFields(p.value)" :key="field">
                    <!-- Model row: registry-driven groups -->
                    <div v-if="field === 'selected_model'" class="agset-field">
                        <label class="agset-field-label">{{ fieldLabel(p.value, 'selected_model') }}</label>
                        <wa-callout v-if="modelFallbackNotice(p.value)" variant="warning" class="model-fallback-callout">
                            {{ modelFallbackNotice(p.value) }}
                        </wa-callout>
                        <wa-select
                            size="small"
                            :value.prop="toSentinel(fieldValue(p.value, 'selected_model'))"
                            @change="setField(p.value, 'selected_model', $event.target.value)"
                        >
                            <wa-option :value="DEFAULT_SENTINEL">{{ $t('common.options.inherit') }}</wa-option>
                            <small class="agset-group-label">{{ $t('project.agentDefaults.forceTo') }}</small>
                            <template v-for="(group, gi) in modelGroups(p.value)" :key="gi">
                                <wa-divider v-if="gi > 0 && group.entries.length"></wa-divider>
                                <wa-option
                                    v-for="entry in group.entries"
                                    :key="entry.value"
                                    :value="entry.value"
                                    :label="entry.labelWithSuffix"
                                    :disabled="entry.disabled"
                                >
                                    <span>{{ entry.labelWithSuffix }}</span>
                                    <span v-if="entry.description" class="agset-option-description">{{ entry.description }}</span>
                                </wa-option>
                            </template>
                        </wa-select>
                    </div>
                    <!-- Generic row: flat choices -->
                    <div v-else class="agset-field">
                        <label class="agset-field-label">{{ fieldLabel(p.value, field) }}</label>
                        <wa-select
                            size="small"
                            :value.prop="toSentinel(fieldValue(p.value, field))"
                            @change="setField(p.value, field, $event.target.value)"
                        >
                            <wa-icon
                                v-if="effortSelectIcon(p.value, field)"
                                slot="start"
                                auto-width
                                :src="effortSelectIcon(p.value, field)"
                            ></wa-icon>
                            <SettingFlagIcon
                                v-if="flagSelectShown(p.value, field)"
                                slot="start"
                                :field="field"
                                :on="flagValueOn(p.value, field)"
                            />
                            <PermissionModeIcon
                                v-if="permissionSelectIcon(p.value, field)"
                                slot="start"
                                :icon="permissionSelectIcon(p.value, field).icon"
                                :color="permissionSelectIcon(p.value, field).color"
                            />
                            <wa-option :value="DEFAULT_SENTINEL">{{ $t('common.options.inherit') }}</wa-option>
                            <small class="agset-group-label">{{ $t('project.agentDefaults.forceTo') }}</small>
                            <wa-option
                                v-for="opt in fieldChoices(p.value, field)"
                                :key="String(opt.value)"
                                :value="String(opt.value)"
                                :label="opt.label"
                            >
                                <wa-icon
                                    v-if="field === 'effort' && effortIconSrc(opt.value)"
                                    auto-width
                                    :src="effortIconSrc(opt.value)"
                                    class="agset-effort-option-icon"
                                ></wa-icon>
                                <SettingFlagIcon
                                    v-if="isFlagField(field)"
                                    :field="field"
                                    :on="opt.value === true"
                                    class="agset-flag-option-icon"
                                />
                                <PermissionModeIcon
                                    v-if="isPermissionField(field) && opt.icon"
                                    :icon="opt.icon"
                                    :color="opt.color"
                                    class="agset-flag-option-icon"
                                />
                                <span>{{ opt.label }}</span>
                                <span v-if="opt.description" class="agset-option-description">{{ opt.description }}</span>
                            </wa-option>
                        </wa-select>
                    </div>
                </template>

                <div v-if="!supportedFields(p.value).length" class="form-hint">
                    {{ $t('project.agentDefaults.noSettings') }}
                </div>
            </wa-tab-panel>
        </TabBar>
    </div>
</template>

<style scoped>
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.agset-intro {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.agset-provider-select {
    max-width: 280px;
}

.provider-option-icon {
    margin-right: 0.5em;
}

.agset-tab-icon {
    margin-right: var(--wa-space-2xs);
}

.agset-load-select {
    max-width: 280px;
    margin-bottom: var(--wa-space-s);
}

.agset-disabled-badge {
    margin-left: var(--wa-space-2xs);
    padding: 0 var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
}

.agset-field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    margin-bottom: var(--wa-space-s);
}

.agset-field-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.agset-group-label {
    display: block;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-style: italic;
}

.agset-effort-option-icon,
.agset-flag-option-icon {
    margin-right: 0.5em;
    vertical-align: -0.15em;
}

.agset-option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* The shared agent-settings icon summary keeps its own flex layout — only tint
   and shrink it to the quiet option-description size. */
.agset-option-summary {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.model-fallback-callout {
    display: block;
    font-size: var(--wa-font-size-s);
    margin-bottom: var(--wa-space-xs);
}
</style>
