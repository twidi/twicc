<script setup>
// TerminalRenameDialog.vue - Dialog for renaming a terminal tab
import { ref, computed, nextTick, useId } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const LABEL_MAX_LENGTH = 30

const emit = defineEmits(['save'])

const dialogRef = ref(null)
const inputRef = ref(null)
const saveButtonRef = ref(null)

const localLabel = ref('')
const currentIndex = ref(null)
const currentDefaultLabel = computed(() => currentIndex.value === 0
    ? t('terminal.tabs.main')
    : t('terminal.tabs.numbered', { number: currentIndex.value + 1 }))

const instanceId = useId()
const formId = `terminal-rename-form-${instanceId}`

/**
 * Set form attribute on save button when dialog opens.
 * wa-button doesn't expose `form` as a property, so we must use setAttribute.
 */
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

/**
 * Focus the input after the dialog opening animation completes,
 * with cursor at end of text.
 */
function focusInput() {
    const input = inputRef.value
    if (!input) return
    input.focus()
    const len = input.value?.length || 0
    input.setSelectionRange(len, len)
}

/**
 * Open the dialog for a given terminal tab.
 * @param {number} index - Terminal index
 * @param {string} currentLabel - Current display label (may be the default)
 * @param {string} defaultLabel - Default label for this tab (e.g. "Main", "Term 3")
 */
function open(index, currentLabel, defaultLabel) {
    currentIndex.value = index
    // If the current label is the default, start with empty input so the
    // placeholder shows the default and the user types a custom name from scratch
    localLabel.value = currentLabel === defaultLabel ? '' : currentLabel
    syncFormState()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function onInput(event) {
    localLabel.value = event.target.value
}

function handleSave() {
    const trimmed = localLabel.value.trim().slice(0, LABEL_MAX_LENGTH)
    emit('save', currentIndex.value, trimmed)
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="$t('terminal.rename.title')"
        class="terminal-rename-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusInput"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">{{ $t('terminal.rename.label') }}</label>
                <wa-input
                    ref="inputRef"
                    :value.prop="localLabel"
                    @input="onInput"
                    :placeholder="currentDefaultLabel"
                    :maxlength="LABEL_MAX_LENGTH"
                ></wa-input>
                <div class="form-hint">
                    {{ $t('terminal.rename.hint', { count: LABEL_MAX_LENGTH }) }}
                </div>
            </div>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close">
                {{ $t('common.actions.cancel') }}
            </wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand">
                {{ $t('common.actions.save') }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.terminal-rename-dialog {
    --width: min(400px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
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

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
