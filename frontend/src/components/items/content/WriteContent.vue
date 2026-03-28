<script setup>
import { computed } from 'vue'
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
 * Determine display mode and content.
 *
 * - If originalFile is a non-empty string (file update with extras):
 *   diff mode with original = originalFile, modified = input.content
 * - Otherwise (new file, or no extras):
 *   full mode showing input.content in a read-only CodeEditor
 */
const viewerMode = computed(() => {
    if (typeof props.originalFile === 'string' && props.originalFile.length > 0) {
        return 'diff'
    }
    return 'full'
})

const original = computed(() => {
    return viewerMode.value === 'diff' ? props.originalFile : ''
})

const modified = computed(() => {
    return props.input.content ?? ''
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch && viewerMode.value !== 'full')
</script>

<template>
    <div class="write-content">
        <div v-if="showSpinner" class="write-loading">
            <wa-spinner></wa-spinner>
        </div>
        <ToolDiffViewer
            v-else
            :mode="viewerMode"
            :original="original"
            :modified="modified"
            :file-path="input.file_path"
        />
    </div>
</template>

<style scoped>
.write-content {
    height: 23rem;
}

.write-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}
</style>
