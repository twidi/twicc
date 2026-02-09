<script setup>
import { ref, watch, computed, nextTick } from 'vue'
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

// --- File content state ---
const originalContent = ref('')  // content as fetched from server
const currentContent = ref('')   // content currently in the editor
const loading = ref(false)
const error = ref(null)
const isBinary = ref(false)
const fileSize = ref(0)

// --- Edit mode state ---
const isEditing = ref(false)
const saving = ref(false)
const saveError = ref(null)
const editSwitchRef = ref(null)

const monacoTheme = computed(() =>
    settingsStore.getEffectiveTheme === 'dark' ? 'github-dark' : 'github-light'
)

const editorOptions = computed(() => ({
    readOnly: !isEditing.value,
    automaticLayout: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderLineHighlight: isEditing.value ? 'line' : 'none',
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

// Extract filename from the absolute path for display in the header
const fileName = computed(() => {
    if (!props.filePath) return ''
    const parts = props.filePath.split('/')
    return parts[parts.length - 1]
})

watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        originalContent.value = ''
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        // Reset edit mode when file is deselected
        isEditing.value = false
        syncEditSwitch()
        return
    }

    loading.value = true
    error.value = null
    saveError.value = null
    isBinary.value = false
    // Reset edit mode when switching files
    isEditing.value = false
    syncEditSwitch()

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/?path=${encodeURIComponent(newPath)}`
        )
        const data = await res.json()

        if (!res.ok) {
            error.value = data.error || 'Failed to load file'
            originalContent.value = ''
            currentContent.value = ''
            return
        }

        if (data.binary) {
            isBinary.value = true
            fileSize.value = data.size
            originalContent.value = ''
            currentContent.value = ''
            return
        }

        if (data.error) {
            error.value = data.error
            originalContent.value = ''
            currentContent.value = ''
            return
        }

        originalContent.value = data.content
        currentContent.value = data.content
        fileSize.value = data.size
    } catch (err) {
        error.value = 'Network error: failed to load file'
        originalContent.value = ''
        currentContent.value = ''
    } finally {
        loading.value = false
    }
}, { immediate: true })

// --- Edit mode handlers ---

function onEditorChange(value) {
    currentContent.value = value
}

function onEditToggle(event) {
    const checked = event.target.checked
    if (checked) {
        isEditing.value = true
        saveError.value = null
    } else {
        // Revert silently when leaving edit mode
        revert()
        isEditing.value = false
    }
}

async function save() {
    if (saving.value || !props.filePath) return

    saving.value = true
    saveError.value = null

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: props.filePath,
                    content: currentContent.value,
                }),
            }
        )
        const data = await res.json()

        if (!res.ok || data.error) {
            saveError.value = data.error || 'Failed to save file'
            return
        }

        // Success: update original content to match saved content
        originalContent.value = currentContent.value
    } catch (err) {
        saveError.value = 'Network error: failed to save file'
    } finally {
        saving.value = false
    }
}

function revert() {
    currentContent.value = originalContent.value
    saveError.value = null
}

// Sync wa-switch checked state (Web Component doesn't always pick up Vue reactive state)
function syncEditSwitch() {
    nextTick(() => {
        if (editSwitchRef.value && editSwitchRef.value.checked !== isEditing.value) {
            editSwitchRef.value.checked = isEditing.value
        }
    })
}

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
    <div class="file-pane">
        <!-- Header toolbar (only when a file is loaded) -->
        <div v-if="filePath && !loading" class="header">
            <div class="header-left">
                <wa-switch
                    ref="editSwitchRef"
                    size="small"
                    @change="onEditToggle"
                >Edit</wa-switch>
                <template v-if="isEditing">
                    <wa-button
                        size="small"
                        variant="brand"
                        :disabled="saving"
                        @click="save"
                    >
                        <wa-icon v-if="saving" slot="prefix" name="spinner" spin></wa-icon>
                        Save
                    </wa-button>
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        :disabled="saving"
                        @click="revert"
                    >Revert</wa-button>
                </template>
                <wa-button
                    v-else
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="header-spacer"
                >Spacer</wa-button>
            </div>
            <div class="header-right">
                <span class="header-filename" :title="filePath">{{ fileName }}</span>
            </div>
        </div>

        <!-- Save error message -->
        <div v-if="saveError" class="monaco-placeholder">
            <wa-callout variant="danger" size="small">
                {{ saveError }}
            </wa-callout>
        </div>

        <!-- Loading state -->
        <div v-if="loading" class="monaco-placeholder">
            <wa-spinner></wa-spinner>
        </div>

        <!-- Error state -->
        <div v-else-if="error" class="monaco-placeholder">
            <wa-callout variant="danger" size="small">
                {{ error }}
            </wa-callout>
        </div>

        <!-- Binary file -->
        <div v-else-if="isBinary" class="monaco-placeholder">
            <wa-icon name="file-zipper" style="font-size: 1.5rem; opacity: 0.5;"></wa-icon>
            <span>Binary file ({{ formatSize(fileSize) }}) cannot be displayed</span>
        </div>

        <!-- Monaco editor -->
        <vue-monaco-editor
            v-else
            :value="currentContent"
            :path="monacoPath"
            :theme="monacoTheme"
            :options="editorOptions"
            :save-view-state="true"
            @change="onEditorChange"
        />
    </div>
</template>

<style scoped>
.file-pane {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-neutral-200);
    min-height: 2.25rem;
    flex-shrink: 0;
}

.header-left {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.header-right {
    display: flex;
    align-items: center;
    min-width: 0;
    margin-left: var(--wa-space-s);
}

.header-filename {
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    color: var(--wa-color-neutral-700);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.header-spacer {
    visibility: hidden;
    pointer-events: none;
}

.monaco-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 100%;
    color: var(--wa-color-neutral-500);
    font-size: var(--wa-font-size-s);
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
}
</style>
