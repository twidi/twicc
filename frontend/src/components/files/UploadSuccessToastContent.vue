<script setup>
/**
 * UploadSuccessToastContent — rich content for the "Uploaded <name>" toast.
 *
 * Shows the target directory and a "Copy path" button that copies the full
 * path of the uploaded file.
 */
import { toast } from '../../composables/useToast'

const props = defineProps({
    /** Notivue item reference — passed by CustomNotification (unused here, but standard signature) */
    item: {
        type: Object,
        default: null,
    },
    /** Directory the file was uploaded to. */
    targetDir: {
        type: String,
        default: '',
    },
    /** Full path of the uploaded file. */
    path: {
        type: String,
        required: true,
    },
})

async function copyPath() {
    try {
        await navigator.clipboard.writeText(props.path)
        toast.success('Path copied to clipboard', { duration: 2000 })
    } catch {
        toast.error('Could not copy the path', { duration: 2000 })
    }
}
</script>

<template>
    <div class="upload-toast-content">
        <span v-if="targetDir" class="upload-toast-dir">{{ targetDir }}</span>
        <div class="upload-toast-actions">
            <wa-button size="small" variant="brand" appearance="outlined" @click="copyPath">
                <wa-icon slot="start" name="copy"></wa-icon>
                Copy path
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.upload-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.upload-toast-dir {
    overflow-wrap: anywhere;
}

.upload-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
