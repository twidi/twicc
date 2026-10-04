<script setup>
import { ref, useId, nextTick } from 'vue'
import { apiFetch } from '../../utils/api'

const props = defineProps({
    apiPrefix: { type: String, required: true },
})

const emit = defineEmits(['created'])

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)
const formId = `create-form-${useId()}`

const localName = ref('')
const parentDir = ref('')
const kind = ref('file')
const isSaving = ref(false)
const errorMessage = ref('')

function open({ path, createKind }) {
    parentDir.value = path
    kind.value = createKind
    localName.value = ''
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
    nameInputRef.value?.focus()
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

    isSaving.value = true
    errorMessage.value = ''

    let response
    try {
        response = await apiFetch(`${props.apiPrefix}/file-create/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parent_dir: parentDir.value, name: trimmed, kind: kind.value }),
        })
    } catch {
        errorMessage.value = 'Network error. Please try again.'
        isSaving.value = false
        return
    }

    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
        errorMessage.value = data.error || `Failed to create (HTTP ${response.status})`
        isSaving.value = false
        return
    }

    emit('created', { parentDir: parentDir.value, newPath: data.new_path, kind: kind.value })
    isSaving.value = false
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="kind === 'directory' ? 'New folder' : 'New file'"
        class="create-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusInput"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">Name</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="localName"
                    @input="localName = $event.target.value"
                    :placeholder="kind === 'directory' ? 'folder-name' : 'filename.ext'"
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
                Create
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.create-dialog {
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
