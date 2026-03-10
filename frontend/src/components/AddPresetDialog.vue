<script setup>
/**
 * AddPresetDialog - Dialog for adding a custom preset file.
 *
 * Shows a file browser (standalone directory tree API) to select a JSON file,
 * plus a name field. Validates the file contains valid presets before submitting.
 */

import { ref, nextTick, watch } from 'vue'
import { apiFetch } from '../utils/api'
import FileTreePanel from './FileTreePanel.vue'

const props = defineProps({
    projectId: {
        type: String,
        default: null,
    },
})

const emit = defineEmits(['added'])

const dialogRef = ref(null)
const fileTreePanelRef = ref(null)

// ─── Form state ──────────────────────────────────────────────────────────────

const selectedPath = ref('')
const presetName = ref('')
const error = ref(null)
const submitting = ref(false)

// ─── Tree state ──────────────────────────────────────────────────────────────

const tree = ref(null)
const loading = ref(false)
const treeError = ref(null)
const rootPath = ref(null)
const showHidden = ref(false)

async function fetchTree(dirPath) {
    const qs = showHidden.value ? '&show_hidden=1' : ''
    const res = await apiFetch(
        `/api/directory-tree/?path=${encodeURIComponent(dirPath)}${qs}`
    )
    if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || 'Failed to load directory')
    }
    return await res.json()
}

async function lazyLoadDir(path) {
    return await fetchTree(path)
}

async function fetchHomePath() {
    try {
        const res = await apiFetch('/api/home-directory/')
        if (res.ok) {
            const data = await res.json()
            return data.path
        }
    } catch { /* fall through */ }
    return '/'
}

// ─── Navigate up ─────────────────────────────────────────────────────────────

async function navigateUp() {
    if (!rootPath.value || rootPath.value === '/') return
    const parentPath = rootPath.value.replace(/\/[^/]+$/, '') || '/'
    loading.value = true
    treeError.value = null
    try {
        const data = await fetchTree(parentPath)
        rootPath.value = parentPath
        tree.value = data
    } catch (e) {
        treeError.value = e.message || 'Failed to load directory'
    } finally {
        loading.value = false
    }
}

// ─── Open / close ────────────────────────────────────────────────────────────

async function open(startDirectory) {
    selectedPath.value = ''
    presetName.value = ''
    error.value = null
    submitting.value = false
    treeError.value = null

    dialogRef.value.open = true

    // Load initial tree
    loading.value = true
    try {
        let startDir = startDirectory
        if (!startDir) startDir = await fetchHomePath()
        const data = await fetchTree(startDir)
        rootPath.value = startDir
        tree.value = data
    } catch (e) {
        treeError.value = e.message
        const homePath = await fetchHomePath()
        try {
            const data = await fetchTree(homePath)
            rootPath.value = homePath
            tree.value = data
        } catch (e2) {
            treeError.value = e2.message
        }
    } finally {
        loading.value = false
    }
}

function close() {
    dialogRef.value.open = false
}

// Reload tree when show_hidden changes
watch(showHidden, async () => {
    if (!rootPath.value) return
    loading.value = true
    treeError.value = null
    try {
        tree.value = await fetchTree(rootPath.value)
    } catch (e) {
        treeError.value = e.message
    } finally {
        loading.value = false
    }
})

// ─── File selection ──────────────────────────────────────────────────────────

function onFileSelect(pathOrRelative) {
    let absolutePath
    if (pathOrRelative.startsWith('/')) {
        absolutePath = pathOrRelative
    } else {
        absolutePath = rootPath.value === '/'
            ? `/${pathOrRelative}`
            : `${rootPath.value}/${pathOrRelative}`
    }

    // Only allow JSON files
    if (!absolutePath.endsWith('.json')) {
        error.value = 'Only JSON files can be selected'
        return
    }

    selectedPath.value = absolutePath
    error.value = null

    // Auto-fill name from filename (without extension)
    if (!presetName.value) {
        const filename = absolutePath.split('/').pop() || ''
        const nameWithoutExt = filename.replace(/\.[^.]+$/, '')
        presetName.value = nameWithoutExt
    }

    // Focus the name input after selection
    nextTick(() => {
        const nameInput = dialogRef.value?.querySelector('.name-input')
        if (nameInput) {
            nameInput.focus()
            const len = presetName.value.length
            nameInput.setSelectionRange(len, len)
        }
    })
}

// ─── Submit ──────────────────────────────────────────────────────────────────

async function handleAdd() {
    const path = selectedPath.value.trim()
    const name = presetName.value.trim()

    if (!path || !name) {
        error.value = 'Both file and name are required'
        return
    }
    if (!props.projectId) {
        error.value = 'No project context'
        return
    }

    submitting.value = true
    error.value = null

    try {
        const res = await apiFetch(`/api/projects/${props.projectId}/custom-presets/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path }),
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok) {
            error.value = data.error || `Error ${res.status}`
            return
        }
        emit('added')
        close()
    } catch (e) {
        error.value = e.message || 'Network error'
    } finally {
        submitting.value = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog ref="dialogRef" label="Add preset file" class="add-preset-dialog">
        <div class="dialog-body">
            <!-- Selected file display -->
            <div class="selected-file">
                <label class="field-label">Selected file</label>
                <div class="file-path-display" :class="{ empty: !selectedPath }">
                    {{ selectedPath || 'Select a JSON file below...' }}
                </div>
            </div>

            <!-- Name input -->
            <div class="name-field">
                <label class="field-label">Display name</label>
                <wa-input
                    :value="presetName"
                    placeholder="Name for this preset source"
                    size="small"
                    class="name-input"
                    @input="presetName = $event.target.value"
                    @keydown.enter.prevent="handleAdd"
                ></wa-input>
            </div>

            <!-- Error -->
            <wa-callout v-if="error" variant="danger" appearance="outlined" size="small">
                {{ error }}
            </wa-callout>

            <!-- File browser -->
            <div class="tree-container">
                <div class="tree-header">
                    <wa-button
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        :disabled="!rootPath || rootPath === '/'"
                        @click="navigateUp"
                    >
                        <wa-icon name="arrow-up" label="Go to parent directory"></wa-icon>
                    </wa-button>
                    <span class="tree-path" :title="rootPath">{{ rootPath || '...' }}</span>
                    <label class="hidden-toggle">
                        <wa-switch
                            size="small"
                            :checked="showHidden"
                            @change="showHidden = $event.target.checked"
                        ></wa-switch>
                        <span class="hidden-toggle-label">Hidden</span>
                    </label>
                </div>
                <FileTreePanel
                    ref="fileTreePanelRef"
                    :tree="tree"
                    :loading="loading"
                    :error="treeError"
                    :root-path="rootPath"
                    :lazy-load-fn="lazyLoadDir"
                    :show-refresh="false"
                    :compact-folders="false"
                    :show-shared-options="false"
                    mode="files"
                    @file-select="onFileSelect"
                />
            </div>
        </div>

        <wa-button
            slot="footer"
            variant="neutral"
            appearance="outlined"
            @click="close"
        >Cancel</wa-button>
        <wa-button
            slot="footer"
            variant="brand"
            :disabled="!selectedPath || !presetName.trim() || submitting"
            :loading="submitting"
            @click="handleAdd"
        >Add</wa-button>
    </wa-dialog>
</template>

<style scoped>
.add-preset-dialog {
    --width: min(500px, calc(100vw - 2rem));
}

.dialog-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.field-label {
    display: block;
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    color: var(--wa-color-text-subtle);
    margin-bottom: var(--wa-space-2xs);
}

.selected-file {
    display: flex;
    flex-direction: column;
}

.file-path-display {
    font-size: var(--wa-font-size-s);
    font-family: var(--wa-font-family-mono, monospace);
    padding: var(--wa-space-xs) var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-default);
    word-break: break-all;
}

.file-path-display.empty {
    color: var(--wa-color-text-subtle);
    font-style: italic;
    font-family: inherit;
}

.name-field {
    display: flex;
    flex-direction: column;
}

.name-input {
    width: 100%;
}

.tree-container {
    display: flex;
    flex-direction: column;
    height: 300px;
    border: 1px solid var(--wa-color-border-default);
    border-radius: var(--wa-border-radius-s);
    overflow: hidden;
}

.tree-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-default);
    flex-shrink: 0;
    background: var(--wa-color-surface-alt);
}

.tree-path {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-family-mono, monospace);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
}

.hidden-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    cursor: pointer;
    flex-shrink: 0;
}

.hidden-toggle-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-subtle);
}
</style>
