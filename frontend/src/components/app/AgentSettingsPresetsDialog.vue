<script setup>
// Provider-agnostic dialog to manage the agent settings presets of a given
// provider. Opens the list view by default; "Add" / "Edit" switch to a form
// built on the shared matrix design: the model × effort picker (matrix +
// task controls + switches, via ``AgentSettingsDefaultsPicker`` — with the provider-
// default cell dotted), then the permission selects, which keep their explicit
// "Default" sentinel (a preset's permission stays independently unset-able).
//
// Preset fields are NULLABLE (null = follow the provider default at apply
// time). The picker displays effective values (preset ?? default); a matrix
// pick or switch toggle writes a concrete value, and ``handleSave`` normalises
// any picker-owned value equal to the provider's current default back to null —
// so clicking the dotted default cell (or toggling a switch back to its
// default) un-forces the field, mirroring the popover's snapshot convention.
//
// Preset persistence is cross-provider: the dialog reads/writes through the
// shared ``useAgentSettingsPresetsStore`` (one keyed bucket per provider),
// not through each provider's own store. The on-disk format is the same
// for every provider — only the file path varies.

import { computed, nextTick, ref, watch } from 'vue'
import { getProviderHelpers } from '../../providers'
import { RESERVED_PRESET_NAMES, useAgentSettingsPresetsStore } from '../../stores/agentSettingsPresets'
import { presetSummaryParts } from '../../utils/presetFormat'
import { DEFAULT_SENTINEL } from '../../composables/useSessionAgentSettings'
import AgentSettingsDefaultsPicker from '../message/AgentSettingsDefaultsPicker.vue'
import AgentSettingsSummaryView from '../message/AgentSettingsSummaryView.vue'
import PermissionModeIcon from '../ui/PermissionModeIcon.vue'
import HelpTextLink from '../help/HelpTextLink.vue'

const props = defineProps({
    open: { type: Boolean, default: false },
    provider: { type: String, required: true },
    // Context-specific project defaults. The global settings surface omits
    // this prop, so the dialog reads the provider's global defaults instead.
    resolvedDefaults: { type: Object, default: null },
    // Null means the global management context, where both permission layers
    // are relevant. A session supplies its resolved trust context.
    untrusted: { type: Boolean, default: null },
})
const emit = defineEmits(['update:open'])

const providerHelpers = computed(() => getProviderHelpers(props.provider))
const presetsStore = useAgentSettingsPresetsStore()
const providerLabel = computed(() => providerHelpers.value?.constructor?.label ?? 'Agent')

// Preset records use historical key names (``model``, ``thinking``) while
// the helpers' rendering hooks are keyed on wire names
// (``selected_model``, ``thinking_enabled``). The form keeps wire names
// internally and we translate at the persistence boundary.
const WIRE_TO_PRESET_KEY = {
    selected_model: 'model',
    context_max: 'context_max',
    effort: 'effort',
    thinking_enabled: 'thinking',
    permission_mode: 'permission_mode',
    permission_mode_if_untrusted: 'permission_mode_if_untrusted',
    claude_in_chrome: 'claude_in_chrome',
    fast_mode: 'fast_mode',
}
const FIELD_ORDER = ['selected_model', 'context_max', 'effort', 'thinking_enabled', 'permission_mode', 'permission_mode_if_untrusted', 'claude_in_chrome', 'fast_mode']

const presetDefaults = computed(() => {
    if (props.resolvedDefaults) return props.resolvedDefaults
    const helpers = providerHelpers.value
    if (!helpers) return {}
    const defaults = {}
    for (const field of FIELD_ORDER) {
        const value = helpers.getDefaultValue(field)
        if (value !== null && value !== undefined) defaults[field] = value
    }
    return defaults
})

// Every list row shows the preset resolved in this dialog's context. The
// global settings surface supplies its defaults through the provider helpers.
function presetSummary(preset) {
    const parts = presetSummaryParts(preset, providerHelpers.value, {
        defaults: presetDefaults.value,
        untrusted: props.untrusted,
    })
    return parts.length ? parts : [{ text: 'all default' }]
}

const view = ref('list')
const editIndex = ref(null)
const formData = ref(emptyFormData())
const errorMessage = ref('')

const dialogRef = ref(null)
const nameInputRef = ref(null)
const submitButtonRef = ref(null)

const presets = computed(() => presetsStore.getPresets(props.provider))

const dialogLabel = computed(() => {
    if (view.value === 'list') return `${providerLabel.value} settings presets`
    return editIndex.value === null ? 'Add preset' : 'Edit preset'
})

// Fields owned by the shared picker (matrix + switches). Everything the picker
// shows is written concrete; ``handleSave`` nulls the ones equal to the current
// provider default (see the top comment).
const PICKER_FIELDS = ['selected_model', 'effort', 'context_max', 'thinking_enabled', 'claude_in_chrome', 'fast_mode']

// Permission fields keep their sentinel selects, below the picker.
const permissionFields = computed(() => {
    const helpers = providerHelpers.value
    if (!helpers) return []
    return ['permission_mode', 'permission_mode_if_untrusted'].filter(f => helpers.supportsAgentSetting(f))
})

// The picker reads the preset's RAW nullable values (null = follow default).
function presetValueFor(field) {
    return formData.value[field]
}

function onMatrixSelect({ model, effort }) {
    formData.value.selected_model = model
    formData.value.effort = effort
}

function onSwitchChange({ field, value }) {
    formData.value[field] = value
}

function emptyFormData() {
    return {
        name: '',
        selected_model: null,
        context_max: null,
        effort: null,
        thinking_enabled: null,
        permission_mode: null,
        permission_mode_if_untrusted: null,
        claude_in_chrome: null,
        fast_mode: null,
    }
}

function presetToFormData(preset) {
    const data = emptyFormData()
    data.name = preset.name ?? ''
    for (const wire of FIELD_ORDER) {
        const presetKey = WIRE_TO_PRESET_KEY[wire]
        data[wire] = preset[presetKey] ?? null
    }
    return data
}

function formDataToPreset(data) {
    const preset = { name: data.name }
    for (const wire of FIELD_ORDER) {
        const presetKey = WIRE_TO_PRESET_KEY[wire]
        preset[presetKey] = data[wire]
    }
    return preset
}

function toSentinel(value) {
    return value === null || value === undefined ? DEFAULT_SENTINEL : String(value)
}

// Per-mode glyph for the closed select — only when a concrete mode is forced
// (an unset/"Default" field inherits and shows no icon).
function permissionSelectIcon(field) {
    return providerHelpers.value?.getChoiceIcon(field, formData.value[field]) ?? null
}

// Reads the wa-select's current string back into the typed value used by
// the form. Falls back to the raw string if no choice matches (defensive —
// shouldn't happen with the static catalogues we ship today).
function fromSentinel(field, raw) {
    if (raw === DEFAULT_SENTINEL) return null
    const choices = providerHelpers.value?.getFieldChoices(field) ?? []
    const match = choices.find(opt => String(opt.value) === raw)
    return match ? match.value : raw
}

watch(
    () => view.value,
    async (newView) => {
        if (newView !== 'form') return
        await nextTick()
        const btn = submitButtonRef.value
        if (btn) btn.setAttribute?.('form', 'preset-form')
    },
)

function handleDelete(index) {
    presetsStore.remove(props.provider, index)
}

function handleDuplicate(index) {
    presetsStore.duplicate(props.provider, index)
}

function handleReorder(index, direction) {
    presetsStore.reorder(props.provider, index, direction)
}

function closeDialog() {
    emit('update:open', false)
}

function onAfterShow() {
    view.value = 'list'
    editIndex.value = null
    formData.value = emptyFormData()
    errorMessage.value = ''
}

function openAddForm() {
    formData.value = emptyFormData()
    editIndex.value = null
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function openEditForm(index) {
    const source = presets.value[index]
    if (!source) return
    formData.value = presetToFormData(source)
    editIndex.value = index
    errorMessage.value = ''
    view.value = 'form'
    focusNameInput()
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
}

async function focusNameInput() {
    await nextTick()
    const el = nameInputRef.value
    if (!el) return
    el.focus?.()
    if (typeof el.setSelectionRange === 'function') {
        const len = el.value?.length || 0
        el.setSelectionRange(len, len)
    }
}

function handleSave() {
    errorMessage.value = ''
    const trimmedName = formData.value.name.trim()
    if (!trimmedName) {
        errorMessage.value = 'Name is required'
        return
    }
    if (RESERVED_PRESET_NAMES.has(trimmedName)) {
        errorMessage.value = `"${trimmedName}" is a reserved name and cannot be used`
        return
    }
    if (presetsStore.findIndexByName(props.provider, trimmedName, editIndex.value) !== -1) {
        errorMessage.value = 'A preset with this name already exists'
        return
    }
    // Picker-owned fields have no explicit "Default" control: a value equal to
    // the provider's CURRENT default is stored as null ("follow default"), so
    // picking the dotted default cell / toggling a switch back un-forces the
    // field. Permission fields keep their explicit sentinel and are untouched.
    const normalized = { ...formData.value, name: trimmedName }
    for (const field of PICKER_FIELDS) {
        if (normalized[field] !== null && normalized[field] === providerHelpers.value.getDefaultValue(field)) {
            normalized[field] = null
        }
    }
    const payload = formDataToPreset(normalized)
    if (editIndex.value === null) {
        presetsStore.add(props.provider, payload)
    } else {
        presetsStore.update(props.provider, editIndex.value, payload)
    }
    view.value = 'list'
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        class="manage-presets-dialog"
        :label="dialogLabel"
        :open="props.open"
        @wa-after-show.self="onAfterShow"
        @wa-after-hide.self="closeDialog"
    >
        <div v-if="view === 'list'" class="dialog-content">
            <div v-if="presets.length === 0" class="empty-message">
                No presets yet. Add one to get started.
            </div>
            <div v-else class="preset-list">
                <div v-for="(preset, index) in presets" :key="index" class="preset-row">
                    <div class="reorder-arrows">
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === 0 }"
                            :disabled="index === 0"
                            title="Move up"
                            @click="handleReorder(index, -1)"
                        ><wa-icon name="chevron-up" /></button>
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === presets.length - 1 }"
                            :disabled="index === presets.length - 1"
                            title="Move down"
                            @click="handleReorder(index, 1)"
                        ><wa-icon name="chevron-down" /></button>
                    </div>
                    <div class="preset-display">
                        <span class="preset-name">{{ preset.name }}</span>
                        <AgentSettingsSummaryView
                            class="preset-summary"
                            :parts="presetSummary(preset)"
                        />
                    </div>
                    <div class="preset-actions">
                        <button class="action-btn" title="Edit" @click="openEditForm(index)">
                            <wa-icon name="pen-to-square" />
                        </button>
                        <button class="action-btn" title="Duplicate" @click="handleDuplicate(index)">
                            <wa-icon name="copy" />
                        </button>
                        <button class="action-btn action-btn-danger" title="Delete" @click="handleDelete(index)">
                            <wa-icon name="trash-can" />
                        </button>
                    </div>
                </div>
            </div>
        </div>
        <form v-else id="preset-form" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label" for="preset-name-input">
                    Name <span class="form-label-quiet">(mandatory)</span>
                </label>
                <wa-input
                    id="preset-name-input"
                    ref="nameInputRef"
                    :value="formData.name"
                    size="small"
                    @input="formData.name = $event.target.value"
                ></wa-input>
            </div>

            <!-- Model & effort picker (shared matrix + task controls + switches). The
                 provider-default cell keeps its dot here — a preset field left
                 on the default follows it at apply time. -->
            <div class="form-group">
                <div class="matrix-heading">
                    <label class="form-label">Model &amp; effort picker</label>
                    <HelpTextLink help-key="model-effort-score" label="What are those numbers?" />
                </div>
                <AgentSettingsDefaultsPicker
                    :provider="props.provider"
                    :value-for="presetValueFor"
                    show-default-dot
                    @select="onMatrixSelect"
                    @change="onSwitchChange"
                />
            </div>

            <!-- Permission rows: flat list of choices, explicit Default sentinel -->
            <div v-for="field in permissionFields" :key="field" class="form-group">
                <label class="form-label">{{ providerHelpers.getFieldLabel(field) }}</label>
                <wa-select
                    size="small"
                    :value.prop="toSentinel(formData[field])"
                    @change="formData[field] = fromSentinel(field, $event.target.value)"
                >
                    <PermissionModeIcon
                        v-if="permissionSelectIcon(field)"
                        slot="start"
                        :icon="permissionSelectIcon(field).icon"
                        :color="permissionSelectIcon(field).color"
                    />
                    <wa-option :value="DEFAULT_SENTINEL">Default</wa-option>
                    <small class="select-group-label">Force to:</small>
                    <wa-option
                        v-for="opt in providerHelpers.getFieldChoices(field)"
                        :key="String(opt.value)"
                        :value="String(opt.value)"
                        :label="opt.label"
                    >
                        <PermissionModeIcon
                            v-if="opt.icon"
                            :icon="opt.icon"
                            :color="opt.color"
                            class="permission-option-icon"
                        />
                        <span>{{ opt.label }}</span>
                        <span v-if="opt.description" class="option-description">{{ opt.description }}</span>
                    </wa-option>
                </wa-select>
            </div>

            <wa-callout v-if="errorMessage" variant="danger">{{ errorMessage }}</wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button variant="neutral" appearance="outlined" @click="closeDialog">Close</wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    <wa-icon slot="start" name="plus"></wa-icon>
                    Add preset
                </wa-button>
            </template>
            <template v-else>
                <wa-button variant="neutral" appearance="outlined" @click="cancelForm">Cancel</wa-button>
                <wa-button ref="submitButtonRef" variant="brand" type="submit">Save</wa-button>
            </template>
        </div>
    </wa-dialog>
</template>

<style scoped>
.manage-presets-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    button {
        box-shadow: none;
        margin: 0;
    }
}

.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
}

.preset-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.preset-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

.reorder-arrows {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
}

.reorder-btn {
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-2xs);
    cursor: pointer;
    transition: color 0.15s, background-color 0.15s;
}

.reorder-btn:hover:not(.disabled) {
    color: var(--wa-color-text-base);
    background: var(--wa-color-surface-alt);
}

.reorder-btn.disabled {
    opacity: 0.25;
    cursor: default;
}

.preset-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
}

.preset-name {
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.preset-summary {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.preset-actions {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.action-btn {
    background: none;
    border: none;
    font-size: var(--wa-font-size-m);
    padding: var(--wa-space-xs);
    cursor: pointer;
    line-height: 1;
    transition: background-color 0.15s, color 0.15s;
    color: var(--wa-color-text-quiet);
}

.action-btn:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-60);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-label-quiet {
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-normal);
}

.permission-option-icon {
    margin-right: 0.5em;
    vertical-align: -0.15em;
}

.select-group-label {
    display: block;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-style: italic;
}

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Heading above the picker ("Model & effort picker" + help link). */
.matrix-heading {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-3xs);
}
</style>
