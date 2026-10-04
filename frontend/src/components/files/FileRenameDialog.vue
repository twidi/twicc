<script setup>
import { ref, useId, nextTick } from 'vue'
import { apiFetch } from '../../utils/api'

const props = defineProps({
    apiPrefix: { type: String, required: true },
})

const emit = defineEmits(['renamed'])

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)
const formId = `rename-form-${useId()}`

const localName = ref('')
const originalPath = ref('')
const originalName = ref('')
const nodeType = ref('file')
const isSaving = ref(false)
const errorMessage = ref('')

function open({ path, name, type }) {
    originalPath.value = path
    originalName.value = name
    nodeType.value = type
    localName.value = name
    errorMessage.value = ''
    isSaving.value = false
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

function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

function focusInput() {
    const input = nameInputRef.value
    if (!input) return
    input.focus()
    const dotIndex = localName.value.lastIndexOf('.')
    if (nodeType.value === 'file' && dotIndex > 0) {
        input.setSelectionRange(0, dotIndex)
    } else {
        const len = localName.value.length
        input.setSelectionRange(0, len)
    }
}

async function handleSave() {
    if (isSaving.value) return

    const trimmed = localName.value.trim()
    if (!trimmed) {
        errorMessage.value = 'Name cannot be empty'
        return
    }
    if (trimmed.includes('/') || trimmed.includes('\\')) {
        errorMessage.value = 'Name cannot contain slashes'
        return
    }
    if (trimmed === originalName.value) {
        close()
        return
    }

    isSaving.value = true
    errorMessage.value = ''

    let response
    try {
        response = await apiFetch(`${props.apiPrefix}/file-rename/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: originalPath.value, new_name: trimmed }),
        })
    } catch {
        errorMessage.value = 'Network error. Please try again.'
        isSaving.value = false
        return
    }

    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
        errorMessage.value = data.error || `Failed to rename (HTTP ${response.status})`
        isSaving.value = false
        return
    }

    emit('renamed', { oldPath: originalPath.value, newPath: data.new_path, newName: trimmed })
    isSaving.value = false
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="`Rename ${nodeType}`"
        class="rename-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusInput"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">New name</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="localName"
                    @input="localName = $event.target.value"
                ></wa-input>
            </div>
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isSaving">
                Cancel
            </wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                <wa-spinner v-if="isSaving" slot="start"></wa-spinner>
                Rename
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.rename-dialog {
    --width: min(450px, calc(100vw - 2rem));
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

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
