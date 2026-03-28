<script setup>
import { computed } from 'vue'
import { applyStructuredPatch } from '../../../utils/patchUtils'
import ToolDiffViewer from './ToolDiffViewer.vue'

const props = defineProps({
    input: {
        type: Object,
        required: true
    },
    backendPatch: {
        type: Array,
        default: null
    },
    backendPatchLoading: {
        type: Boolean,
        default: false
    },
    originalFile: {
        type: String,
        default: null
    }
})

/**
 * Compute original and modified strings for the diff viewer.
 *
 * Priority:
 * 1. If originalFile + backendPatch available (extras loaded):
 *    original = originalFile, modified = applyStructuredPatch(originalFile, patch)
 * 2. Fallback: original = old_string, modified = new_string (fragment only)
 */
const diffData = computed(() => {
    if (props.originalFile != null && props.backendPatch) {
        const modified = applyStructuredPatch(props.originalFile, props.backendPatch)
        if (modified != null) {
            return {
                original: props.originalFile,
                modified,
            }
        }
        // applyPatch failed — fall through to fragment mode
    }

    // Fragment mode: use old_string / new_string directly
    return {
        original: props.input.old_string ?? '',
        modified: props.input.new_string ?? '',
    }
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch)
</script>

<template>
    <div class="edit-content">
        <div v-if="showSpinner" class="edit-loading">
            <wa-spinner></wa-spinner>
        </div>
        <ToolDiffViewer
            v-else
            mode="diff"
            :original="diffData.original"
            :modified="diffData.modified"
            :file-path="input.file_path"
        />
    </div>
</template>

<style scoped>
.edit-content {
    height: 23rem;
}
.edit-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}
</style>
