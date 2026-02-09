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

// --- Monaco editor instance ---
const editorRef = ref(null)      // Monaco IStandaloneCodeEditor instance
const savedVersionId = ref(null) // model.getAlternativeVersionId() at last save/fetch

// --- File content state ---
const currentContent = ref('')   // content currently in the editor
const loading = ref(false)       // true only for the very first load (no file displayed yet)
const switching = ref(false)     // true during file switch (editor stays visible)
const error = ref(null)
const isBinary = ref(false)
const fileSize = ref(0)

// Whether the editor has ever successfully displayed a file.
// Used to distinguish "initial load" (show spinner, hide editor)
// from "file switch" (keep editor visible, show subtle indicator).
const hasLoadedOnce = ref(false)

// --- Edit mode state ---
const isEditing = ref(false)
const saving = ref(false)
const saveError = ref(null)
const editSwitchRef = ref(null)

// Whether the editor content differs from the last saved/fetched content.
// Uses Monaco's alternativeVersionId which is undo-aware: if the user types
// then undoes everything, the file is no longer dirty.
// Falls back to string comparison when the editor isn't mounted yet.
const isDirty = computed(() => {
    if (editorRef.value && savedVersionId.value !== null) {
        // Accessing currentContent.value to ensure Vue tracks the dependency
        // (alternativeVersionId is not reactive, but currentContent updates on every change)
        void currentContent.value
        const model = editorRef.value.getModel()
        if (model) {
            return model.getAlternativeVersionId() !== savedVersionId.value
        }
    }
    return false
})

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
    // When the editor should not be shown (binary / error), return a stable
    // sentinel path so the library doesn't try to create a model for the real
    // file path with empty content (which would pollute the model cache).
    if (isBinary.value || error.value) return undefined
    return props.filePath
})

// Extract filename from the absolute path for display in the header
const fileName = computed(() => {
    if (!props.filePath) return ''
    const parts = props.filePath.split('/')
    return parts[parts.length - 1]
})

// Whether the Monaco editor should be visible.
// Hidden when: no file selected, initial load (never displayed yet), binary, or error.
const showEditor = computed(() => {
    if (!props.filePath) return false
    if (!hasLoadedOnce.value) return false  // initial load — show spinner instead
    if (isBinary.value) return false
    if (error.value) return false
    return true
})

// Whether the header toolbar should be visible.
const showHeader = computed(() => {
    return !!props.filePath && hasLoadedOnce.value
})

// Whether a full-area placeholder should be shown (loading spinner, error, binary).
// These overlay on top of the editor area.
const showOverlay = computed(() => {
    if (!props.filePath) return false
    if (loading.value) return true
    if (error.value) return true
    if (isBinary.value) return true
    return false
})

/**
 * Fetch file content from the backend.
 *
 * @param {string} filePath - absolute path to fetch
 * @param {Object} [options]
 * @param {boolean} [options.isSwitch=false] - true when switching between files
 *   (editor stays visible). false for the very first load.
 */
async function fetchFileContent(filePath, { isSwitch = false } = {}) {
    if (isSwitch) {
        switching.value = true
    } else {
        loading.value = true
    }
    error.value = null
    saveError.value = null
    isBinary.value = false

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/?path=${encodeURIComponent(filePath)}`
        )
        const data = await res.json()

        if (!res.ok) {
            error.value = data.error || 'Failed to load file'
            currentContent.value = ''
            return
        }

        if (data.binary) {
            isBinary.value = true
            fileSize.value = data.size
            currentContent.value = ''
            return
        }

        if (data.error) {
            error.value = data.error
            currentContent.value = ''
            return
        }

        currentContent.value = data.content
        fileSize.value = data.size
        hasLoadedOnce.value = true
        // Snapshot after a tick so Monaco has processed the new content
        nextTick(() => snapshotVersionId())
    } catch (err) {
        error.value = 'Network error: failed to load file'
        currentContent.value = ''
    } finally {
        loading.value = false
        switching.value = false
    }
}

watch(() => props.filePath, async (newPath) => {
    if (!newPath) {
        currentContent.value = ''
        error.value = null
        isBinary.value = false
        // Reset edit mode when file is deselected
        isEditing.value = false
        syncEditSwitch()
        return
    }

    // Reset edit mode when switching files
    isEditing.value = false
    syncEditSwitch()

    // If we've already displayed a file before, this is a "switch" —
    // the editor stays mounted and visible while we fetch.
    await fetchFileContent(newPath, { isSwitch: hasLoadedOnce.value })
}, { immediate: true })

// --- Monaco editor lifecycle ---

function onEditorMount(editor) {
    editorRef.value = editor
    snapshotVersionId()
}

/** Capture the current model version as the "clean" baseline. */
function snapshotVersionId() {
    if (editorRef.value) {
        const model = editorRef.value.getModel()
        if (model) {
            savedVersionId.value = model.getAlternativeVersionId()
        }
    }
}

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

        // Success: snapshot version as new baseline
        snapshotVersionId()
    } catch (err) {
        saveError.value = 'Network error: failed to save file'
    } finally {
        saving.value = false
    }
}

/**
 * Revert editor content by re-fetching the file from the backend.
 * Always fetches fresh content (the file may have changed on disk).
 * Uses isSwitch mode so the editor stays mounted — no flash.
 */
async function revert() {
    if (!props.filePath) return
    await fetchFileContent(props.filePath, { isSwitch: true })
}

// Sync wa-switch checked state (Web Component doesn't always pick up Vue reactive state)
function syncEditSwitch() {
    nextTick(() => {
        if (editSwitchRef.value && editSwitchRef.value.checked !== isEditing.value) {
            editSwitchRef.value.checked = isEditing.value
        }
    })
}

// Expose dirty state so parent components can check for unsaved changes
defineExpose({ isDirty })

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
    <div class="file-pane">
        <!-- Header toolbar (visible once a file has been loaded) -->
        <div v-if="showHeader" class="header">
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
                        :disabled="saving || !isDirty"
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
                <wa-spinner v-if="switching" class="header-spinner"></wa-spinner>
                <span class="header-filename" :title="filePath">{{ fileName }}</span>
            </div>
        </div>

        <!-- Save error message (overlays above the editor) -->
        <div v-if="saveError" class="monaco-placeholder">
            <wa-callout variant="danger" size="small">
                {{ saveError }}
            </wa-callout>
        </div>

        <!-- Content area: editor is always mounted once, overlays sit on top -->
        <div class="editor-area">
            <!-- Monaco editor — mounted once, never destroyed on file switch -->
            <vue-monaco-editor
                v-show="showEditor"
                :value="currentContent"
                :path="monacoPath"
                :theme="monacoTheme"
                :options="editorOptions"
                :save-view-state="true"
                @mount="onEditorMount"
                @change="onEditorChange"
            />

            <!-- Overlay: initial loading (before any file has been displayed) -->
            <div v-if="showOverlay" class="monaco-placeholder">
                <template v-if="loading">
                    <wa-spinner></wa-spinner>
                </template>
                <template v-else-if="error">
                    <wa-callout variant="danger" size="small">
                        {{ error }}
                    </wa-callout>
                </template>
                <template v-else-if="isBinary">
                    <wa-icon name="file-zipper" style="font-size: 1.5rem; opacity: 0.5;"></wa-icon>
                    <span>Binary file ({{ formatSize(fileSize) }}) cannot be displayed</span>
                </template>
            </div>
        </div>
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
    gap: var(--wa-space-xs);
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

.header-spinner {
    font-size: var(--wa-font-size-s);
    flex-shrink: 0;
}

.header-spacer {
    visibility: hidden;
    pointer-events: none;
}

.editor-area {
    flex: 1;
    position: relative;
    min-height: 0;
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
