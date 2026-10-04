<script setup>
import { ref } from 'vue'
import { apiFetch } from '../../utils/api'

const props = defineProps({
    apiPrefix: { type: String, required: true },
})

const emit = defineEmits(['deleted'])

const dialogRef = ref(null)

const targetPath = ref('')
const targetName = ref('')
const nodeType = ref('file')
const isDeleting = ref(false)
const errorMessage = ref('')

function open({ path, name, type }) {
    targetPath.value = path
    targetName.value = name
    nodeType.value = type
    errorMessage.value = ''
    isDeleting.value = false
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

async function handleDelete() {
    if (isDeleting.value) return

    isDeleting.value = true
    errorMessage.value = ''

    let response
    try {
        response = await apiFetch(`${props.apiPrefix}/file-delete/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: targetPath.value }),
        })
    } catch {
        errorMessage.value = 'Network error. Please try again.'
        isDeleting.value = false
        return
    }

    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
        errorMessage.value = data.error || `Failed to delete (HTTP ${response.status})`
        isDeleting.value = false
        return
    }

    emit('deleted', { path: targetPath.value })
    isDeleting.value = false
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="`Delete ${nodeType}`"
        class="delete-dialog"
    >
        <div class="dialog-content">
            <p class="confirm-message">
                Are you sure you want to delete
                <strong>{{ targetName }}</strong>?
            </p>
            <p v-if="nodeType === 'directory'" class="warning-message">
                <wa-icon name="triangle-exclamation"></wa-icon>
                This will permanently delete the directory and all its contents.
            </p>
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </div>
        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isDeleting">
                Cancel
            </wa-button>
            <wa-button variant="danger" :disabled="isDeleting" @click="handleDelete">
                <wa-spinner v-if="isDeleting" slot="start"></wa-spinner>
                Delete
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.delete-dialog {
    --width: min(450px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.confirm-message {
    margin: 0;
    font-size: var(--wa-font-size-m);
}

.confirm-message strong {
    word-break: break-all;
}

.warning-message {
    margin: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-warning-fill-loud);
    font-size: var(--wa-font-size-s);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
