<script setup>
// ShortcutConfigDialog.vue - Dialog for configuring a terminal shortcut button
import { ref, computed, useId } from 'vue'

const props = defineProps({
    /** Current shortcut config for the slot being edited */
    shortcut: {
        type: Object,
        default: () => ({ label: '', sequence: '', showOnDesktop: false }),
    },
})

const emit = defineEmits(['save', 'close'])

const formId = useId()
const dialogRef = ref(null)
const captureAreaRef = ref(null)
const error = ref('')

// Editable state
const label = ref('')
const sequence = ref('')
const showOnDesktop = ref(false)
const isCapturing = ref(false)

/**
 * Convert a KeyboardEvent to a label + escape sequence.
 * Returns null if the key should be ignored (plain printable characters).
 */
function keyEventToShortcut(e) {
    const key = e.key
    const ctrl = e.ctrlKey
    const alt = e.altKey
    const shift = e.shiftKey

    // Special keys (allowed without modifiers)
    const specialKeys = {
        'Escape': { label: 'Esc', sequence: '\x1b' },
        'Tab': { label: 'Tab', sequence: '\x09' },
        'Enter': { label: 'Enter', sequence: '\r' },
        'Backspace': { label: 'Backspace', sequence: '\x7f' },
        'Delete': { label: 'Delete', sequence: '\x1b[3~' },
        'ArrowUp': { label: '↑', sequence: '\x1b[A' },
        'ArrowDown': { label: '↓', sequence: '\x1b[B' },
        'ArrowRight': { label: '→', sequence: '\x1b[C' },
        'ArrowLeft': { label: '←', sequence: '\x1b[D' },
        'Home': { label: 'Home', sequence: '\x1b[H' },
        'End': { label: 'End', sequence: '\x1b[F' },
        'PageUp': { label: 'PgUp', sequence: '\x1b[5~' },
        'PageDown': { label: 'PgDn', sequence: '\x1b[6~' },
    }

    // Check special keys (with optional modifiers for label)
    if (key in specialKeys) {
        const spec = specialKeys[key]
        const parts = []
        if (ctrl) parts.push('Ctrl')
        if (alt) parts.push('Alt')
        if (shift) parts.push('Shift')
        parts.push(spec.label)
        // For modified special keys, we still send the base sequence
        // (terminal apps typically handle the modifier separately)
        return { label: parts.join('+'), sequence: spec.sequence }
    }

    // Ctrl+letter (A-Z) → control characters \x01-\x1a
    if (ctrl && !alt && key.length === 1 && /[a-zA-Z]/.test(key)) {
        const upper = key.toUpperCase()
        const code = upper.charCodeAt(0) - 64  // A=1, B=2, ..., Z=26
        const parts = ['Ctrl']
        if (shift) parts.push('Shift')
        parts.push(upper)
        return { label: parts.join('+'), sequence: String.fromCharCode(code) }
    }

    // Alt+letter
    if (alt && !ctrl && key.length === 1 && /[a-zA-Z]/.test(key)) {
        const parts = ['Alt']
        if (shift) parts.push('Shift')
        parts.push(key.toUpperCase())
        return { label: parts.join('+'), sequence: `\x1b${key}` }
    }

    // Reject plain printable characters (no modifier)
    if (!ctrl && !alt && key.length === 1) {
        return null
    }

    // Reject modifier-only keys
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(key)) {
        return null
    }

    return null
}

function onKeyCapture(e) {
    if (!isCapturing.value) return

    e.preventDefault()
    e.stopPropagation()

    const result = keyEventToShortcut(e)
    if (!result) return

    label.value = result.label
    sequence.value = result.sequence
    isCapturing.value = false
    error.value = ''
}

function startCapture() {
    isCapturing.value = true
    error.value = ''
    captureAreaRef.value?.focus()
}

function handleSave() {
    if (!label.value.trim() && !sequence.value) {
        // Save as empty (clear the slot)
        emit('save', { label: '', sequence: '', showOnDesktop: false })
        return
    }
    if (!sequence.value) {
        error.value = 'Press a key combination to set the shortcut'
        return
    }
    emit('save', {
        label: label.value.trim(),
        sequence: sequence.value,
        showOnDesktop: showOnDesktop.value,
    })
}

function handleClear() {
    emit('save', { label: '', sequence: '', showOnDesktop: false })
}

function handleClose() {
    emit('close')
}

/** Display-friendly representation of the current sequence */
const sequenceDisplay = computed(() => {
    if (!sequence.value) return '—'
    return sequence.value.split('').map(c => {
        const code = c.charCodeAt(0)
        if (code < 32) return `\\x${code.toString(16).padStart(2, '0')}`
        if (code === 127) return '\\x7f'
        return c
    }).join('')
})

function open() {
    label.value = props.shortcut.label
    sequence.value = props.shortcut.sequence
    showOnDesktop.value = props.shortcut.showOnDesktop
    isCapturing.value = false
    error.value = ''
    dialogRef.value.open = true
}

function syncFormButton() {
    // wa-button doesn't expose the form attribute as a property
    const btn = dialogRef.value?.querySelector('[data-submit-btn]')
    if (btn) btn.setAttribute('form', formId)
}

function focusCapture() {
    captureAreaRef.value?.focus()
}

defineExpose({ open })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="shortcut.label ? 'Edit shortcut' : 'Add shortcut'"
        class="shortcut-dialog"
        @wa-show="syncFormButton"
        @wa-after-show="focusCapture"
        @wa-after-hide="handleClose"
    >
        <form :id="formId" @submit.prevent="handleSave">
            <wa-callout v-if="error" variant="danger" class="error-callout">
                {{ error }}
            </wa-callout>

            <!-- Key capture area -->
            <div class="field">
                <label>Shortcut key</label>
                <div
                    ref="captureAreaRef"
                    class="capture-area"
                    :class="{ capturing: isCapturing, 'has-value': !!sequence }"
                    tabindex="0"
                    @keydown="onKeyCapture"
                    @click="startCapture"
                    @blur="isCapturing = false"
                >
                    <template v-if="isCapturing">
                        <wa-icon name="keyboard" class="capture-icon"></wa-icon>
                        Press a key combination...
                    </template>
                    <template v-else-if="label">
                        <span class="captured-label">{{ label }}</span>
                        <span class="captured-sequence">{{ sequenceDisplay }}</span>
                    </template>
                    <template v-else>
                        <wa-icon name="circle-plus" class="capture-icon"></wa-icon>
                        Click to capture a key
                    </template>
                </div>
            </div>

            <!-- Show on desktop toggle -->
            <div class="field toggle-field">
                <wa-switch
                    :checked="showOnDesktop"
                    @change="showOnDesktop = $event.target.checked"
                >Show on desktop</wa-switch>
            </div>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button
                v-if="shortcut.label"
                variant="danger"
                appearance="plain"
                @click="handleClear"
            >Clear</wa-button>
            <span class="footer-spacer"></span>
            <wa-button appearance="plain" @click="dialogRef.open = false">Cancel</wa-button>
            <wa-button variant="brand" data-submit-btn type="submit">Save</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.shortcut-dialog {
    --width: min(400px, calc(100vw - 2rem));
}

.shortcut-dialog form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.error-callout {
    margin-bottom: var(--wa-space-xs);
}

.field {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.field label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-default);
}

.capture-area {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) var(--wa-space-m);
    border: 2px dashed var(--wa-color-border-default);
    border-radius: var(--wa-border-radius-m);
    cursor: pointer;
    font-size: var(--wa-font-size-m);
    color: var(--wa-color-text-subtle);
    outline: none;
    transition: border-color 0.15s, background 0.15s;
    min-height: 3rem;
}

.capture-area:hover {
    border-color: var(--wa-color-border-hover);
}

.capture-area:focus,
.capture-area.capturing {
    border-color: var(--wa-color-brand-default);
    border-style: solid;
    background: var(--wa-color-surface-alt);
}

.capture-area.has-value {
    border-style: solid;
    color: var(--wa-color-text-default);
}

.capture-icon {
    font-size: 1.2em;
    opacity: 0.6;
}

.captured-label {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-default);
}

.captured-sequence {
    font-family: monospace;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
    margin-left: auto;
}

.toggle-field {
    flex-direction: row;
    align-items: center;
}

.dialog-footer {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.footer-spacer {
    flex: 1;
}
</style>
