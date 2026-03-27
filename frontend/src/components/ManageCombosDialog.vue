<script setup>
// ManageCombosDialog.vue - Dialog for managing custom key combos
import { ref, computed, nextTick, useId } from 'vue'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import { formatCombo, formatComboNotation } from '../utils/comboNotation'

const terminalConfigStore = useTerminalConfigStore()

// ── Dialog refs ──────────────────────────────────────────────────────
const dialogRef = ref(null)
const saveButtonRef = ref(null)
const labelInputRef = ref(null)

const instanceId = useId()
const formId = `manage-combos-form-${instanceId}`

// ── View state ───────────────────────────────────────────────────────
const view = ref('list') // 'list' or 'form'
const editIndex = ref(null) // null = adding, number = editing
const formData = ref(null) // { label: '', steps: [{ modifiers: [], key: '' }] }
const errorMessage = ref('')
const warningMessage = ref('')

// ── Key picker constants ─────────────────────────────────────────────
const KEY_DISPLAY_MAP = {
    Escape: 'ESC',
    Tab: 'TAB',
    Enter: 'ENTER',
    ArrowLeft: '←',
    ArrowUp: '↑',
    ArrowDown: '↓',
    ArrowRight: '→',
    Home: 'HOME',
    End: 'END',
    PageUp: 'PGUP',
    PageDown: 'PGDN',
    Delete: 'DEL',
    Insert: 'INS',
}

const PICKER_KEYS = [
    // Row 1: from Essentials
    [
        { label: 'ESC', key: 'Escape' }, { label: 'TAB', key: 'Tab' }, { label: 'ENTER', key: 'Enter', wide: true },
        { label: '←', key: 'ArrowLeft' }, { label: '↑', key: 'ArrowUp' },
        { label: '↓', key: 'ArrowDown' }, { label: '→', key: 'ArrowRight' },
        { label: '-', key: '-' }, { label: '/', key: '/' },
        { label: '|', key: '|' }, { label: '~', key: '~' },
    ],
    // Row 2: from More
    [
        { label: 'HOME', key: 'Home', wide: true }, { label: 'END', key: 'End', wide: true },
        { label: 'PGUP', key: 'PageUp', wide: true }, { label: 'PGDN', key: 'PageDown', wide: true },
        { label: 'DEL', key: 'Delete' }, { label: 'INS', key: 'Insert' },
        { label: '\\', key: '\\' }, { label: '_', key: '_' },
        { label: '*', key: '*' }, { label: '&', key: '&' },
        { label: '.', key: '.' }, { label: '+', key: '+' },
    ],
    // Row 3: F-keys
    Array.from({ length: 12 }, (_, i) => ({ label: `F${i + 1}`, key: `F${i + 1}` })),
]

const MODIFIERS = ['ctrl', 'alt', 'shift']

// ── Computed ─────────────────────────────────────────────────────────
const dialogLabel = computed(() => {
    if (view.value === 'list') return 'Manage Combos'
    if (editIndex.value !== null) return 'Edit Combo'
    return 'Add Combo'
})

// ── Key display helper ───────────────────────────────────────────────
function displayKeyLabel(key) {
    if (!key) return ''
    if (KEY_DISPLAY_MAP[key]) return KEY_DISPLAY_MAP[key]
    if (/^F\d{1,2}$/.test(key)) return key
    if (key.length === 1) return key.toUpperCase()
    return key.toUpperCase()
}

// ── Form helpers ─────────────────────────────────────────────────────
function openAddForm() {
    editIndex.value = null
    formData.value = { label: '', steps: [{ modifiers: [], key: '' }] }
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openEditForm(index) {
    editIndex.value = index
    formData.value = JSON.parse(JSON.stringify(terminalConfigStore.combos[index]))
    // Ensure modifiers arrays exist
    formData.value.steps.forEach(s => { if (!s.modifiers) s.modifiers = [] })
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function openDuplicateForm(index) {
    editIndex.value = null // null = creates new on save
    formData.value = JSON.parse(JSON.stringify(terminalConfigStore.combos[index]))
    formData.value.steps.forEach(s => { if (!s.modifiers) s.modifiers = [] })
    errorMessage.value = ''
    warningMessage.value = ''
    view.value = 'form'
    nextTick(() => syncFormState())
}

function cancelForm() {
    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
}

// ── Modifier toggle ──────────────────────────────────────────────────
function toggleModifier(stepIndex, mod) {
    const mods = formData.value.steps[stepIndex].modifiers
    const idx = mods.indexOf(mod)
    if (idx >= 0) {
        mods.splice(idx, 1)
    } else {
        mods.push(mod)
    }
}

// ── Key picker ───────────────────────────────────────────────────────
function pickKey(stepIndex, key) {
    formData.value.steps[stepIndex].key = key
}

// Keys that should be ignored when capturing from keyboard (modifiers are buttons)
const IGNORED_KEYS = new Set(['Control', 'Alt', 'Shift', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock'])

/**
 * Capture a key pressed on the keyboard in the key-display field.
 * Ignores modifier-only presses (those are handled by the toggle buttons).
 */
function handleKeyCapture(event, stepIndex) {
    if (IGNORED_KEYS.has(event.key)) return
    event.preventDefault()
    event.stopPropagation()
    formData.value.steps[stepIndex].key = event.key
}

// ── Step management ──────────────────────────────────────────────────
const addStepBtnRef = ref(null)

function addStep() {
    formData.value.steps.push({ modifiers: [], key: '' })
    nextTick(() => {
        addStepBtnRef.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    })
}

function removeStep(stepIndex) {
    formData.value.steps.splice(stepIndex, 1)
}

// ── Duplicate detection ──────────────────────────────────────────────
function stepsMatch(a, b) {
    if (a.length !== b.length) return false
    for (let i = 0; i < a.length; i++) {
        if (a[i].key !== b[i].key) return false
        const modsA = [...(a[i].modifiers || [])].sort()
        const modsB = [...(b[i].modifiers || [])].sort()
        if (modsA.length !== modsB.length) return false
        if (modsA.some((m, j) => m !== modsB[j])) return false
    }
    return true
}

// ── Validation & save ────────────────────────────────────────────────
function handleSave() {
    errorMessage.value = ''
    warningMessage.value = ''

    if (!formData.value.steps || formData.value.steps.length === 0) {
        errorMessage.value = 'At least one step is required.'
        return
    }

    // Every step must have a key
    const emptyStep = formData.value.steps.findIndex(s => !s.key)
    if (emptyStep >= 0) {
        errorMessage.value = `Step ${emptyStep + 1} must have a key selected.`
        return
    }

    // Build cleaned data
    const cleanedData = {
        label: formData.value.label.trim(),
        steps: formData.value.steps.map(s => ({
            modifiers: s.modifiers.length > 0 ? [...s.modifiers] : [],
            key: s.key,
        })),
    }
    // Drop empty label
    if (!cleanedData.label) delete cleanedData.label

    // Check for duplicate (warn but allow save on second submit)
    const isDuplicate = terminalConfigStore.combos.some((combo, i) => {
        if (editIndex.value === i) return false // skip self
        return stepsMatch(combo.steps, cleanedData.steps)
    })
    if (isDuplicate && !warningMessage.value) {
        warningMessage.value = 'A combo with identical steps already exists. Submit again to save anyway.'
        return
    }

    // Save
    if (editIndex.value !== null) {
        terminalConfigStore.updateCombo(editIndex.value, cleanedData)
    } else {
        terminalConfigStore.addCombo(cleanedData)
    }

    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
}

// ── Dialog lifecycle ─────────────────────────────────────────────────
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

function focusFirstInput() {
    if (view.value === 'form' && labelInputRef.value) {
        labelInputRef.value.focus()
    }
}

function open() {
    view.value = 'list'
    errorMessage.value = ''
    warningMessage.value = ''
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogLabel"
        class="manage-combos-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusFirstInput"
    >
        <!-- ═══ LIST VIEW ═══ -->
        <div v-if="view === 'list'" class="dialog-content">
            <div v-if="terminalConfigStore.combos.length === 0" class="empty-message">
                No custom combos yet. Add one to get started.
            </div>

            <div v-else class="combo-list">
                <div
                    v-for="(combo, index) in terminalConfigStore.combos"
                    :key="index"
                    class="combo-row"
                >
                    <!-- Reorder arrows -->
                    <div class="reorder-arrows">
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === 0 }"
                            :disabled="index === 0"
                            @click="terminalConfigStore.reorderCombo(index, index - 1)"
                            title="Move up"
                        ><wa-icon name="chevron-up" /></button>
                        <button
                            class="reorder-btn"
                            :class="{ disabled: index === terminalConfigStore.combos.length - 1 }"
                            :disabled="index === terminalConfigStore.combos.length - 1"
                            @click="terminalConfigStore.reorderCombo(index, index + 1)"
                            title="Move down"
                        ><wa-icon name="chevron-down" /></button>
                    </div>

                    <!-- Display text -->
                    <div class="combo-display">
                        <span class="combo-text">{{ formatCombo(combo) }}</span>
                        <span v-if="combo.label" class="combo-notation">{{ formatComboNotation(combo) }}</span>
                    </div>

                    <!-- Action buttons -->
                    <div class="combo-actions">
                        <button class="action-btn" @click="openEditForm(index)" title="Edit">
                            <wa-icon name="pen-to-square" />
                        </button>
                        <button class="action-btn" @click="openDuplicateForm(index)" title="Duplicate">
                            <wa-icon name="copy" />
                        </button>
                        <button class="action-btn action-btn-danger" @click="terminalConfigStore.deleteCombo(index)" title="Delete">
                            <wa-icon name="trash-can" />
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ FORM VIEW ═══ -->
        <form v-else :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <!-- Label field -->
            <div class="form-group">
                <label class="form-label">Label</label>
                <wa-input
                    ref="labelInputRef"
                    :value="formData.label"
                    @input="formData.label = $event.target.value"
                    placeholder='e.g. "tmux:new"'
                    size="small"
                />
                <div class="form-hint">Optional — replaces notation on button</div>
            </div>

            <!-- Steps -->
            <div class="form-group">
                <label v-if="formData.steps.length > 1" class="form-label">Steps</label>

                <div
                    v-for="(step, stepIndex) in formData.steps"
                    :key="stepIndex"
                    class="step-wrapper"
                >
                    <!-- Arrow separator between steps -->
                    <div v-if="stepIndex > 0" class="step-separator">
                        <wa-icon name="arrow-down" />
                    </div>

                    <div class="step-card">
                        <!-- Step header (only when 2+ steps) -->
                        <div v-if="formData.steps.length > 1" class="step-header">
                            <span class="step-title">Step {{ stepIndex + 1 }}</span>
                            <button
                                type="button"
                                class="step-remove-btn"
                                @click="removeStep(stepIndex)"
                                title="Remove step"
                            ><wa-icon name="xmark" /></button>
                        </div>

                        <!-- Modifier toggles -->
                        <div class="modifier-row">
                            <button
                                v-for="mod in MODIFIERS"
                                :key="mod"
                                type="button"
                                class="modifier-btn"
                                :class="{ active: step.modifiers.includes(mod) }"
                                @click="toggleModifier(stepIndex, mod)"
                            >{{ mod.toUpperCase() }}</button>
                        </div>

                        <!-- Key capture input (real input to trigger mobile keyboard) -->
                        <div class="key-input-row">
                            <input
                                class="key-capture-input"
                                :class="{ empty: !step.key }"
                                type="text"
                                maxlength="1"
                                :value="step.key ? displayKeyLabel(step.key) : ''"
                                :placeholder="'Pick below or click and press a key...'"
                                autocomplete="off"
                                autocorrect="off"
                                autocapitalize="off"
                                spellcheck="false"
                                @keydown="handleKeyCapture($event, stepIndex)"
                                @input="$event.target.value = step.key ? displayKeyLabel(step.key) : ''"
                            />
                        </div>

                        <!-- Key picker grid -->
                        <div class="key-picker">
                            <div v-for="(row, rowIndex) in PICKER_KEYS" :key="rowIndex" class="key-picker-row">
                                <button
                                    v-for="pickerKey in row"
                                    :key="pickerKey.key"
                                    type="button"
                                    class="picker-key"
                                    :class="{ selected: step.key === pickerKey.key, wide: pickerKey.wide }"
                                    @click="pickKey(stepIndex, pickerKey.key)"
                                >{{ pickerKey.label }}</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Add step button -->
                <button ref="addStepBtnRef" type="button" class="add-step-btn" @click="addStep">+ Add step</button>
            </div>

            <!-- Warning (duplicate) -->
            <wa-callout v-if="warningMessage" variant="warning" size="small">
                {{ warningMessage }}
            </wa-callout>

            <!-- Error -->
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <!-- ═══ FOOTER ═══ -->
        <div slot="footer" class="dialog-footer">
            <template v-if="view === 'list'">
                <wa-button variant="neutral" appearance="outlined" @click="close">
                    Close
                </wa-button>
                <wa-button variant="brand" @click="openAddForm">
                    + Add combo
                </wa-button>
            </template>
            <template v-else>
                <wa-button variant="neutral" appearance="outlined" @click="cancelForm">
                    Cancel
                </wa-button>
                <wa-button ref="saveButtonRef" type="submit" variant="brand">
                    Save
                </wa-button>
            </template>
        </div>
    </wa-dialog>
</template>

<style scoped>
.manage-combos-dialog {
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

/* ── Empty state ──────────────────────────────────────────────────── */
.empty-message {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l) 0;
}

/* ── Combo list ───────────────────────────────────────────────────── */
.combo-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.combo-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

/* ── Reorder arrows ───────────────────────────────────────────────── */
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

/* ── Combo display ────────────────────────────────────────────────── */
.combo-display {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: baseline;
    xgap: var(--wa-space-xs);
}

.combo-text {
    font-size: var(--wa-font-size-s);
    font-family: var(--wa-font-family-code);
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.combo-notation {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-family: var(--wa-font-family-code);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* ── Action buttons ───────────────────────────────────────────────── */
.combo-actions {
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
    color: var(--wa-color-danger-text);
}

/* ── Form ─────────────────────────────────────────────────────────── */
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

/* ── Step wrapper & separator ─────────────────────────────────────── */
.step-wrapper {
    display: flex;
    flex-direction: column;
    align-items: center;
}

.step-separator {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-text-quiet);
    padding: var(--wa-space-2xs) 0;
    opacity: 0.5;
}

/* ── Step card ────────────────────────────────────────────────────── */
.step-card {
    width: 100%;
    border: 1px solid var(--wa-color-border-base);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-color-surface-alt);
    padding: var(--wa-space-s);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.step-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.step-title {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    color: var(--wa-color-text-quiet);
}

.step-remove-btn {
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    cursor: pointer;
    padding: var(--wa-space-2xs);
    transition: color 0.15s, background-color 0.15s;
}

.step-remove-btn:hover {
    color: var(--wa-color-danger-text);
}

/* ── Modifier toggles (matches ExtraKeysBar styling) ─────────────── */
.modifier-row {
    display: flex;
    gap: var(--wa-space-2xs);
}

.modifier-btn {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-brand-border-quiet);
    color: var(--wa-color-brand-on-quiet);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    height: 1.75rem;
    min-width: 3rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s, color 0.1s;
    user-select: none;
}

.modifier-btn:hover {
    background: var(--wa-color-brand-fill-quiet);
}

.modifier-btn.active {
    background: var(--wa-color-brand-fill-normal);
    border-color: var(--wa-color-brand-border-normal);
    color: var(--wa-color-brand-on-normal);
    box-shadow: 0 0 var(--wa-space-xs) var(--wa-color-brand-fill-quiet);
}

/* ── Key capture input ────────────────────────────────────────────── */
.key-input-row {
    display: flex;
}


.key-capture-input {
    flex: 1;
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-normal);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    height: 1.75rem;
    padding: 0 var(--wa-space-s);
    text-align: center;
    outline: none;
    transition: border-color 0.15s;
    caret-color: transparent;
    box-shadow: none;
    font-weight: bold;
}

.key-capture-input:focus {
    border-color: var(--wa-color-focus);
    box-shadow: 0 0 var(--wa-space-2xs) var(--wa-color-focus);
}

.key-capture-input::placeholder {
    color: var(--wa-color-text-quiet);
    font-family: inherit;
}

/* ── Key picker grid (matches ExtraKeysBar styling) ──────────────── */
.key-picker {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.key-picker-row {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
}

.picker-key {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    color: var(--wa-color-text-normal);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    height: 1.75rem;
    min-width: 2rem;
    padding: 0 var(--wa-space-2xs);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s;
    user-select: none;
    font-weight: normal;
}

.picker-key:hover {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
}

.picker-key:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    transform: scale(0.95);
}

.picker-key.selected {
    background: var(--wa-color-brand-fill-quiet);
    border-color: var(--wa-color-brand-border-normal);
    color: var(--wa-color-brand-on-normal);
    box-shadow: 0 0 var(--wa-space-2xs) var(--wa-color-brand-fill-quiet);
}
/* Wide keys */
.picker-key.wide {
    min-width: 3rem;
}
/* ── Add step button ──────────────────────────────────────────────── */
.add-step-btn {
    background: none;
    border: 1px dashed var(--wa-color-border-base);
    border-radius: var(--wa-border-radius-m);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    padding: var(--wa-space-xs) var(--wa-space-s);
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
    width: 100%;
}

.add-step-btn:hover {
    border-color: var(--wa-color-brand-base);
    color: var(--wa-color-text-base);
}

/* ── Footer ───────────────────────────────────────────────────────── */
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
