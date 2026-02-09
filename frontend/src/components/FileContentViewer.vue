<script setup>
import { ref, watch, computed } from 'vue'
import { useMonaco } from '@guolao/vue-monaco-editor'
import { apiFetch } from '../utils/api'
import { useSettingsStore } from '../stores/settings'
import githubDark from '../assets/monaco-themes/github-dark.json'
import githubLight from '../assets/monaco-themes/github-light.json'

const props = defineProps({
    projectId: String,
    sessionId: String,
    filePath: String,  // absolute path
    isDraft: {
        type: Boolean,
        default: false,
    },
})

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

const settingsStore = useSettingsStore()

// Register GitHub themes once Monaco is loaded
const { monacoRef } = useMonaco()
watch(monacoRef, (monaco) => {
    if (!monaco) return
    // GitHub Dark with custom background to match our app's dark theme
    monaco.editor.defineTheme('github-dark', {
        ...githubDark,
        colors: {
            ...githubDark.colors,
            'editor.background': '#1b2733',  // var(--wa-color-surface-default) on dark mode
        },
    })
    monaco.editor.defineTheme('github-light', githubLight)
}, { immediate: true })

const fileContent = ref('')
const loading = ref(false)
const error = ref(null)
const isBinary = ref(false)
const fileSize = ref(0)

const monacoTheme = computed(() =>
    settingsStore.getEffectiveTheme === 'dark' ? 'github-dark' : 'github-light'
)

const editorOptions = computed(() => ({
    readOnly: true,
    automaticLayout: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderLineHighlight: 'none',
    fontSize: settingsStore.getFontSize,
    lineNumbers: 'on',
    folding: true,
    wordWrap: 'on',
}))

// Use the absolute path as Monaco model path for auto language detection
const monacoPath = computed(() => {
    if (!props.filePath) return undefined
    return props.filePath
})

watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        fileContent.value = ''
        error.value = null
        isBinary.value = false
        return
    }

    loading.value = true
    error.value = null
    isBinary.value = false

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/?path=${encodeURIComponent(newPath)}`
        )
        const data = await res.json()

        if (!res.ok) {
            error.value = data.error || 'Failed to load file'
            fileContent.value = ''
            return
        }

        if (data.binary) {
            isBinary.value = true
            fileSize.value = data.size
            fileContent.value = ''
            return
        }

        if (data.error) {
            error.value = data.error
            fileContent.value = ''
            return
        }

        fileContent.value = data.content
        fileSize.value = data.size
    } catch (err) {
        error.value = 'Network error: failed to load file'
        fileContent.value = ''
    } finally {
        loading.value = false
    }
}, { immediate: true })

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
    <div class="file-content-viewer">
        <!-- Loading state -->
        <div v-if="loading" class="viewer-placeholder">
            <wa-spinner></wa-spinner>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="viewer-placeholder viewer-error">
            <wa-icon name="circle-exclamation" style="font-size: 1.5rem; color: var(--wa-color-danger-600);"></wa-icon>
            <span>{{ error }}</span>
        </div>

        <!-- Binary file -->
        <div v-else-if="isBinary" class="viewer-placeholder">
            <wa-icon name="file-zipper" style="font-size: 1.5rem; opacity: 0.5;"></wa-icon>
            <span>Binary file ({{ formatSize(fileSize) }}) cannot be displayed</span>
        </div>

        <!-- Monaco editor -->
        <vue-monaco-editor
            v-else
            :value="fileContent"
            :path="monacoPath"
            :theme="monacoTheme"
            :options="editorOptions"
            :save-view-state="true"
        />
    </div>
</template>

<style scoped>
.file-content-viewer {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.viewer-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 100%;
    color: var(--wa-color-neutral-500);
    font-size: var(--wa-font-size-s);
}

.viewer-error {
    color: var(--wa-color-danger-600);
}
</style>
