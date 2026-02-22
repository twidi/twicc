<script setup>
import { ref, watch, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useMonaco } from '@guolao/vue-monaco-editor'
import { apiFetch } from '../utils/api'
import { useSettingsStore } from '../stores/settings'
import githubDark from '../assets/monaco-themes/github-dark.json'
import githubLight from '../assets/monaco-themes/github-light.json'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps({
    projectId: String,
    sessionId: String,
    filePath: String,  // absolute path (also used in diff mode for language detection and save)
    isDraft: {
        type: Boolean,
        default: false,
    },
    // --- Diff mode props ---
    diffMode: {
        type: Boolean,
        default: false,
    },
    originalContent: {
        type: String,
        default: null,  // left side (null = new file, everything shows as added)
    },
    modifiedContent: {
        type: String,
        default: null,  // right side (null = deleted file, everything shows as removed)
    },
    diffReadOnly: {
        type: Boolean,
        default: true,  // true for commit diffs, false for index (editable)
    },
})

const emit = defineEmits(['revert'])

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

// --- Monaco editor instances ---
const editorRef = ref(null)        // Monaco IStandaloneCodeEditor instance (normal mode)
const diffEditorRef = ref(null)    // Monaco IStandaloneDiffEditor instance (diff mode)
const savedVersionId = ref(null)   // model.getAlternativeVersionId() at last save/fetch

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

// --- Markdown preview state ---
const isMarkdownFile = computed(() => {
    if (!props.filePath) return false
    return /\.(?:md|markdown|mdown|mkd|mkdn)$/i.test(props.filePath)
})
const showMarkdownPreview = ref(false)

// --- Edit mode state ---
const isEditing = ref(false)
const saving = ref(false)
const saveError = ref(null)
const editSwitchRef = ref(null)
const sideBySideSwitchRef = ref(null)

// --- Diff navigation ---
// Monaco's goToDiff() and setPosition() on the diff sub-editor freeze the
// browser due to hideUnchangedRegions' onDidChangeCursorPosition handler
// triggering an infinite loop (ensureModifiedLineIsVisible → viewZones update
// → layout adjust → cursor change → loop). We use getLineChanges() to get
// the diff list and setScrollTop to navigate safely.
const currentDiffIndex = ref(0)

function navigateDiff(direction) {
    const editor = diffEditorRef.value
    if (!editor) return

    const changes = editor.getLineChanges?.()
    if (!changes?.length) return

    const modifiedEditor = editor.getModifiedEditor()

    let idx = currentDiffIndex.value
    if (direction === 'next') {
        idx = (idx + 1) % changes.length
    } else {
        idx = idx <= 0 ? changes.length - 1 : idx - 1
    }
    currentDiffIndex.value = idx

    const line = changes[idx].modifiedStartLineNumber
    setTimeout(() => {
        // getTopForLineNumber returns the absolute pixel offset of the line
        // in the document. Setting scrollTop to this value places the line
        // at the very top of the viewport.
        const top = modifiedEditor.getTopForLineNumber(line)
        modifiedEditor.setScrollTop(top)
    }, 0)
}

function goToPreviousDiff() {
    navigateDiff('previous')
}

function goToNextDiff() {
    navigateDiff('next')
}

// --- Diff layout state ---
// Initialize from settings store; local ref allows per-session override via the toggle.
const sideBySide = ref(settingsStore.isDiffSideBySide)

// Monaco switches to inline when the diff editor width <= 900px
// (renderSideBySideInlineBreakpoint default). We track the editor area width
// with a ResizeObserver so we can hide the toggle when side-by-side wouldn't fit.
const SIDE_BY_SIDE_MIN_WIDTH = 900
const editorAreaRef = ref(null)        // template ref on .editor-area
const editorAreaWidth = ref(Infinity)  // current width in px
let resizeObserver = null

onMounted(() => {
    if (editorAreaRef.value) {
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                editorAreaWidth.value = entry.contentRect.width
            }
        })
        resizeObserver.observe(editorAreaRef.value)
    }
})

onBeforeUnmount(() => {
    resizeObserver?.disconnect()
    resizeObserver = null
})

// Whether the editor area is wide enough for side-by-side mode
const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)

// Responsive thresholds for minimap and compact mode
const COMPACT_MODE_MAX_WIDTH = 600       // diff editor compact mode below this width
const MINIMAP_EDITOR_MIN_WIDTH = 1200    // show minimap in editor above this

// Whether the editor content differs from the last saved/fetched content.
// Uses Monaco's alternativeVersionId which is undo-aware: if the user types
// then undoes everything, the file is no longer dirty.
// Supports both normal and diff editor modes.
const isDirty = computed(() => {
    const editor = props.diffMode
        ? diffEditorRef.value?.getModifiedEditor()
        : editorRef.value
    if (editor && savedVersionId.value !== null) {
        // Accessing currentContent.value to ensure Vue tracks the dependency
        // (alternativeVersionId is not reactive, but currentContent updates on every change)
        void currentContent.value
        const model = editor.getModel()
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
    minimap: { enabled: editorAreaWidth.value > MINIMAP_EDITOR_MIN_WIDTH },
    scrollBeyondLastLine: false,
    renderLineHighlight: isEditing.value ? 'line' : 'none',
    fontSize: settingsStore.getFontSize,
    lineNumbers: 'on',
    folding: true,
    wordWrap: 'on',
}))

const diffEditorOptions = computed(() => ({
    // Commit diffs: always read-only. Index diffs: editable when edit mode is on.
    readOnly: props.diffReadOnly || !isEditing.value,
    originalEditable: false,
    renderSideBySide: sideBySide.value,
    enableSplitViewResizing: true,
    automaticLayout: true,
    compactMode: editorAreaWidth.value <= COMPACT_MODE_MAX_WIDTH,
    minimap: { enabled: editorAreaWidth.value > MINIMAP_EDITOR_MIN_WIDTH },
    scrollBeyondLastLine: false,
    fontSize: settingsStore.getFontSize,
    wordWrap: 'on',
    hideUnchangedRegions: {
        enabled: true,
    },
    experimental: {
        useTrueInlineView: true,
        showMoves: true,
    }
}))

// Detect language from the file extension via Monaco's language registry.
// Needed for the diff editor because vue-monaco-diff-editor defaults to "text"
// when no language prop is provided, which prevents syntax highlighting.
// The normal editor doesn't need this because its library passes "" as language
// fallback, which lets Monaco auto-detect from the model URI.
const diffLanguage = computed(() => {
    if (!props.filePath || !monacoRef.value) return undefined
    const ext = props.filePath.includes('.')
        ? '.' + props.filePath.split('.').pop()
        : ''
    if (!ext) return undefined
    const langs = monacoRef.value.languages.getLanguages()
    const match = langs.find(l => l.extensions?.includes(ext))
    return match?.id
})

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
// In diff mode, content is passed via props so hasLoadedOnce is not relevant.
const showEditor = computed(() => {
    if (!props.filePath) return false
    if (props.diffMode) {
        // In diff mode, show if we have at least one side of the diff
        return props.originalContent !== null || props.modifiedContent !== null
    }
    if (!hasLoadedOnce.value) return false  // initial load — show spinner instead
    if (isBinary.value) return false
    if (error.value) return false
    return true
})

// Whether the header toolbar should be visible.
const showHeader = computed(() => {
    if (props.diffMode) return !!props.filePath
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

    // Reset edit mode, markdown preview and diff navigation index when switching files
    isEditing.value = false
    showMarkdownPreview.value = false
    syncEditSwitch()
    currentDiffIndex.value = 0

    // In diff mode, content is passed via props — don't fetch.
    if (props.diffMode) {
        currentContent.value = props.modifiedContent ?? ''
        syncSideBySideSwitch()
        return
    }

    // If we've already displayed a file before, this is a "switch" —
    // the editor stays mounted and visible while we fetch.
    await fetchFileContent(newPath, { isSwitch: hasLoadedOnce.value })
}, { immediate: true })

// In diff mode, when the parent re-fetches diff data (e.g. refresh),
// the modifiedContent prop changes. Reset edit mode and sync currentContent.
watch(() => props.modifiedContent, (newContent) => {
    if (!props.diffMode) return
    isEditing.value = false
    syncEditSwitch()
    currentContent.value = newContent ?? ''
})

// --- Monaco editor lifecycle ---

/**
 * Register Ctrl+S as a save action on a Monaco editor instance.
 * The action is always registered but only triggers save() when in edit mode and dirty.
 */
function registerSaveAction(editor) {
    editor.addAction({
        id: 'file-pane-save',
        label: 'Save File',
        keybindings: [
            // Monaco keybinding: CtrlCmd + S (Ctrl on Windows/Linux, Cmd on Mac)
            monacoRef.value.KeyMod.CtrlCmd | monacoRef.value.KeyCode.KeyS,
        ],
        run() {
            if (isEditing.value && isDirty.value && !saving.value) {
                save()
            }
        },
    })
}

function onEditorMount(editor) {
    editorRef.value = editor
    snapshotVersionId()
    registerSaveAction(editor)
}

let _contentChangeGuard = false
function onDiffEditorMount(editor) {
    diffEditorRef.value = editor
    snapshotVersionId()
    // Register Ctrl+S on the modified side of the diff editor
    registerSaveAction(editor.getModifiedEditor())
    // Listen for changes on the modified side for reactivity (dirty detection)
    const modifiedEditor = editor.getModifiedEditor()
    modifiedEditor.onDidChangeModelContent(() => {
        if (_contentChangeGuard) return  // Prevent re-entrant updates
        _contentChangeGuard = true
        currentContent.value = modifiedEditor.getValue()
        _contentChangeGuard = false
    })
}


/** Capture the current model version as the "clean" baseline. */
function snapshotVersionId() {
    const editor = props.diffMode
        ? diffEditorRef.value?.getModifiedEditor()
        : editorRef.value
    if (editor) {
        const model = editor.getModel()
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
        showMarkdownPreview.value = false  // exit preview when entering edit mode
        saveError.value = null
    } else {
        // Revert silently when leaving edit mode
        revert()
        isEditing.value = false
    }
}

function toggleMarkdownPreview() {
    showMarkdownPreview.value = !showMarkdownPreview.value
}

async function save() {
    if (saving.value || !props.filePath) return

    saving.value = true
    saveError.value = null

    // Use currentContent which is kept in sync via @change (normal mode)
    // or onDidChangeModelContent (diff mode). Avoid calling getValue()
    // directly on the diff editor as the model may have been disposed.
    const content = currentContent.value

    try {
        const res = await apiFetch(
            `${apiPrefix.value}/file-content/`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: props.filePath,
                    content,
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
 * In diff mode, emits 'revert' so the parent can re-fetch the diff.
 * In normal mode, fetches fresh content directly.
 */
async function revert() {
    if (!props.filePath) return
    if (props.diffMode) {
        emit('revert')
        return
    }
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

function syncSideBySideSwitch() {
    nextTick(() => {
        if (sideBySideSwitchRef.value && sideBySideSwitchRef.value.checked !== sideBySide.value) {
            sideBySideSwitchRef.value.checked = sideBySide.value
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
                <!-- Edit controls: hidden in read-only diff mode (commit diffs) -->
                <template v-if="!diffMode || !diffReadOnly">
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
                </template>
            </div>
            <div v-if="diffMode && !showMarkdownPreview" class="header-center">
                <div class="diff-nav-buttons">
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button"
                        title="Previous change"
                        @click="goToPreviousDiff"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button"
                        title="Next change"
                        @click="goToNextDiff"
                    >
                        <wa-icon name="arrow-down"></wa-icon>
                    </wa-button>
                </div>
            </div>
            <div class="header-right">
                <wa-spinner v-if="switching" class="header-spinner"></wa-spinner>
                <wa-switch
                    ref="sideBySideSwitchRef"
                    v-if="diffMode && !showMarkdownPreview && canSideBySide"
                    size="small"
                    class="diff-layout-toggle"
                    @change="sideBySide = $event.target.checked; settingsStore.setDiffSideBySide($event.target.checked)"
                >Side by side</wa-switch>
                <!-- Markdown preview toggle: shown for .md files when not editing -->
                <wa-button
                    v-if="isMarkdownFile && !isEditing"
                    size="small"
                    variant="neutral"
                    :appearance="showMarkdownPreview ? 'filled' : 'outlined'"
                    title="Toggle markdown preview"
                    @click="toggleMarkdownPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
            </div>
        </div>

        <!-- Save error message (overlays above the editor) -->
        <div v-if="saveError" class="monaco-placeholder">
            <wa-callout variant="danger" size="small">
                {{ saveError }}
            </wa-callout>
        </div>

        <!-- Content area: editor is always mounted once, overlays sit on top -->
        <div ref="editorAreaRef" class="editor-area">
            <!-- Markdown preview (when toggled on for .md files) -->
            <div v-if="showMarkdownPreview && isMarkdownFile" class="markdown-preview-container">
                <MarkdownContent
                    :source="diffMode ? (modifiedContent ?? '') : currentContent"
                />
            </div>

            <!-- Monaco diff editor (diff mode) -->
            <vue-monaco-diff-editor
                v-if="diffMode && showEditor && !showMarkdownPreview"
                :original="originalContent ?? ''"
                :modified="modifiedContent ?? ''"
                :language="diffLanguage"
                :original-model-path="monacoPath ? monacoPath + '.original' : undefined"
                :modified-model-path="monacoPath"
                :theme="monacoTheme"
                :options="diffEditorOptions"
                @mount="onDiffEditorMount"
            />

            <!-- Monaco editor — mounted once, never destroyed on file switch (normal mode) -->
            <vue-monaco-editor
                v-if="!diffMode"
                v-show="showEditor && !showMarkdownPreview"
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

.header-center {
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
}

.diff-nav-buttons {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
}

.diff-nav-button::part(base) {
    padding: var(--wa-space-3xs) var(--wa-space-2xs);
}

.diff-layout-toggle {
    flex-shrink: 0;
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

.markdown-preview-container {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    padding: var(--wa-space-m) var(--wa-space-l);
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
