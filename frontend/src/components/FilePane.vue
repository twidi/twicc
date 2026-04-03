<script setup>
import { ref, watch, computed, nextTick, useId, inject } from 'vue'
import { apiFetch } from '../utils/api'
import { useSettingsStore } from '../stores/settings'
import MarkdownContent from './MarkdownContent.vue'
import AppTooltip from './AppTooltip.vue'
import CodeEditor from './CodeEditor.vue'
import DiffEditor from './DiffEditor.vue'

const props = defineProps({
    projectId: String,
    sessionId: String,
    filePath: String,  // absolute path (also used in diff mode for language detection and save)
    isDraft: {
        type: Boolean,
        default: false,
    },
    /** Git commit SHA (null = index/uncommitted, undefined = not a git context) */
    commitSha: { type: String, default: undefined },
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
    active: {
        type: Boolean,
        default: true,
    },
    // Optional overrides: when provided, used as initial value instead of settings store.
    // The parent can persist these across FilePane destruction/recreation (e.g. Git tab).
    initialWordWrap: {
        type: Boolean,
        default: null,
    },
    initialSideBySide: {
        type: Boolean,
        default: null,
    },
})

const emit = defineEmits(['revert', 'update:wordWrap', 'update:sideBySide'])

const prevChangeButtonId = useId()
const nextChangeButtonId = useId()
const markdownPreviewButtonId = useId()
const viewInFilesButtonId = useId()
const searchButtonId = useId()

// Injected from SessionView: function to switch to Files tab and reveal a file.
// null when FilePane is not inside a SessionView (or no Files tab available).
const viewFileInFilesTab = inject('viewFileInFilesTab', null)

// API prefix: project-level for drafts, session-level otherwise
const apiPrefix = computed(() => {
    if (props.isDraft) {
        return `/api/projects/${props.projectId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

const commentContext = computed(() => {
    if (!props.filePath) return null
    if (props.commitSha !== undefined) {
        // Git context
        return {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentSessionId: '',
            source: 'git',
            sourceRef: props.commitSha ?? '',
            filePath: props.filePath,
            toolLineNum: null,
        }
    }
    // Files context
    return {
        projectId: props.projectId,
        sessionId: props.sessionId,
        subagentSessionId: '',
        source: 'files',
        sourceRef: '',
        filePath: props.filePath,
        toolLineNum: null,
    }
})

const settingsStore = useSettingsStore()

// --- CodeMirror editor instances ---
const codeEditorRef = ref(null)
const diffEditorRef = ref(null)

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

// --- Word wrap state ---
// Initialize from parent override (if provided) or settings store.
const wordWrap = ref(props.initialWordWrap ?? settingsStore.isEditorWordWrap)

// --- Diff layout state ---
// Initialize from parent override (if provided) or settings store.
const sideBySide = ref(props.initialSideBySide ?? settingsStore.isDiffSideBySide)

// Auto-switch to unified when editor area is too narrow for side-by-side.
// Start at 0 so the default is unified (safe) until the first measurement arrives.
const SIDE_BY_SIDE_MIN_WIDTH = 900
const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
const widthMeasured = ref(false)
let resizeObserver = null

// Use a watcher on the template ref instead of onMounted so that the observer
// is connected as soon as the DOM element exists (handles conditional rendering).
watch(editorAreaRef, (el, _oldEl, onCleanup) => {
    resizeObserver?.disconnect()
    resizeObserver = null
    if (el) {
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                if (entry.contentRect.width > 0) {
                    editorAreaWidth.value = entry.contentRect.width
                    if (!widthMeasured.value) widthMeasured.value = true
                }
            }
        })
        resizeObserver.observe(el)
    }
    onCleanup(() => {
        resizeObserver?.disconnect()
        resizeObserver = null
    })
}, { flush: 'post' })

const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)
const effectiveSideBySide = computed(() => sideBySide.value && canSideBySide.value)

// Whether the editor content differs from the last saved/fetched content.
// Delegates to the CodeMirror editor's own dirty tracking.
const isDirty = computed(() => {
    if (props.diffMode) return diffEditorRef.value?.isDirty ?? false
    return codeEditorRef.value?.isDirty ?? false
})

// Extract filename from the absolute path for display in the header
const fileName = computed(() => {
    if (!props.filePath) return ''
    const parts = props.filePath.split('/')
    return parts[parts.length - 1]
})

// Whether the CodeMirror editor should be visible.
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
        // Reset dirty state after a tick so CodeMirror has processed the new content
        nextTick(() => codeEditorRef.value?.resetDirty())
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
        return
    }

    // Reset edit mode and markdown preview when switching files
    isEditing.value = false
    showMarkdownPreview.value = false

    // In diff mode, content is passed via props — don't fetch.
    if (props.diffMode) {
        currentContent.value = props.modifiedContent ?? ''
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
    currentContent.value = newContent ?? ''
})

// --- Edit mode handlers ---

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

    // Use currentContent which is kept in sync via v-model (normal mode)
    // or @update:modified (diff mode).
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

        // Success: reset dirty state as new baseline
        if (props.diffMode) {
            diffEditorRef.value?.resetDirty()
        } else {
            codeEditorRef.value?.resetDirty()
        }
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

function onWordWrapToggle(event) {
    wordWrap.value = event.target.checked
    emit('update:wordWrap', wordWrap.value)
}

function onSideBySideToggle(event) {
    sideBySide.value = event.target.checked
    emit('update:sideBySide', sideBySide.value)
}

/**
 * Reload the current file content from disk.
 * Safe to call at any time: skips reload in diff mode, when no file is
 * selected, or when the editor has unsaved changes (edit mode + dirty).
 */
async function reload() {
    if (props.diffMode || !props.filePath) return
    if (isEditing.value && isDirty.value) return
    await fetchFileContent(props.filePath, { isSwitch: true })
}

/**
 * Scroll the editor to a 1-based line number.
 * Delegates to the active editor (CodeEditor or DiffEditor).
 */
function scrollToLine(lineNum) {
    if (props.diffMode) {
        diffEditorRef.value?.scrollToLine(lineNum)
    } else {
        codeEditorRef.value?.scrollToLine(lineNum)
    }
}

// Whether the file content is currently being fetched (initial load or file switch)
const isLoading = computed(() => loading.value || switching.value)

// Expose dirty state, reload, scrollToLine, and loading state for parent components
defineExpose({ isDirty, isLoading, reload, scrollToLine })

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// --- CodeMirror editor event handlers ---

function onEditorReady({ view }) {
    // Post-mount setup if needed
}

function onDiffReady() {
    // Post-mount setup if needed
}

function onDiffModifiedChange(newContent) {
    currentContent.value = newContent
}

// --- Search (delegates to active editor) ---

function openSearch() {
    if (props.diffMode) {
        diffEditorRef.value?.openSearch()
    } else {
        codeEditorRef.value?.openSearch()
    }
}

// --- Diff navigation (delegates to DiffEditor) ---

function goToPreviousDiff() {
    diffEditorRef.value?.goToPreviousChunk()
}

function goToNextDiff() {
    diffEditorRef.value?.goToNextChunk()
}
</script>

<template>
    <div class="file-pane">
        <!-- Header toolbar (visible once a file has been loaded) -->
        <div v-if="showHeader" class="header">
            <div class="header-left">
                <wa-button
                    v-if="!showMarkdownPreview"
                    :id="searchButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="openSearch"
                >
                    <wa-icon name="magnifying-glass"></wa-icon>
                </wa-button>
                <AppTooltip :for="searchButtonId">Search in editor</AppTooltip>
                <!-- "View in Files tab" button: shown only in diff mode (Git tab context) -->
                <wa-button
                    v-if="viewFileInFilesTab && diffMode"
                    :id="viewInFilesButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="reduced-height"
                    @click="viewFileInFilesTab(filePath)"
                >
                    <wa-icon name="folder-open"></wa-icon>
                </wa-button>
                <AppTooltip :for="viewInFilesButtonId">View in Files tab</AppTooltip>
                <!-- Edit controls: hidden in read-only diff mode (commit diffs) -->
                <template v-if="!diffMode || !diffReadOnly">
                    <wa-switch
                        :checked="isEditing"
                        size="small"
                        @change="onEditToggle"
                    >Edit</wa-switch>
                    <template v-if="isEditing">
                        <wa-button
                            size="small"
                            variant="brand"
                            :disabled="saving || !isDirty"
                            class="reduced-height"
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
                            class="reduced-height"
                            @click="revert"
                        >Revert</wa-button>
                    </template>
                    <wa-button
                        v-else
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="header-spacer reduced-height"
                    >Spacer</wa-button>
                </template>
            </div>
            <div v-if="diffMode && !showMarkdownPreview" class="header-center">
                <div class="diff-nav-buttons">
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="prevChangeButtonId"
                        @click="goToPreviousDiff"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="prevChangeButtonId">Previous change</AppTooltip>
                    <wa-button
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="diff-nav-button reduced-height"
                        :id="nextChangeButtonId"
                        @click="goToNextDiff"
                    >
                        <wa-icon name="arrow-down"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="nextChangeButtonId">Next change</AppTooltip>
                </div>
            </div>
            <div class="header-right">
                <wa-spinner v-if="switching" class="header-spinner"></wa-spinner>
                <wa-switch
                    v-if="!showMarkdownPreview"
                    :checked="wordWrap"
                    size="small"
                    @change="onWordWrapToggle"
                >Wrap</wa-switch>
                <wa-switch
                    v-if="diffMode && !showMarkdownPreview && canSideBySide"
                    :checked="sideBySide"
                    size="small"
                    class="diff-layout-toggle"
                    @change="onSideBySideToggle"
                >Side by side</wa-switch>
                <!-- Markdown preview toggle: shown for .md files when not editing -->
                <wa-button
                    v-if="isMarkdownFile && !isEditing"
                    size="small"
                    variant="neutral"
                    :appearance="showMarkdownPreview ? 'filled' : 'outlined'"
                    :id="markdownPreviewButtonId"
                    class="reduced-height"
                    @click="toggleMarkdownPreview"
                >
                    <wa-icon name="eye"></wa-icon>
                </wa-button>
                <AppTooltip :for="markdownPreviewButtonId">Toggle markdown preview</AppTooltip>
            </div>
        </div>

        <!-- Save error message (overlays above the editor) -->
        <div v-if="saveError" class="editor-overlay">
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

            <!-- CodeMirror diff editor (diff mode) -->
            <DiffEditor
                v-if="diffMode && showEditor && !showMarkdownPreview && widthMeasured"
                ref="diffEditorRef"
                :original="originalContent ?? ''"
                :modified="modifiedContent ?? ''"
                :file-path="filePath"
                :read-only="diffReadOnly || !isEditing"
                :word-wrap="wordWrap"
                :side-by-side="effectiveSideBySide"
                :collapse-unchanged="true"
                :comment-context="commentContext"
                @update:modified="onDiffModifiedChange"
                @save="save"
                @ready="onDiffReady"
            />

            <!-- CodeMirror editor — mounted once, never destroyed on file switch -->
            <CodeEditor
                v-if="!diffMode"
                v-show="showEditor && !showMarkdownPreview"
                ref="codeEditorRef"
                v-model="currentContent"
                :file-path="filePath"
                :read-only="!isEditing"
                :word-wrap="wordWrap"
                :line-numbers="true"
                :save-view-state="false"
                :comment-context="commentContext"
                @save="save"
                @ready="onEditorReady"
            />

            <!-- Overlay: initial loading (before any file has been displayed) -->
            <div v-if="showOverlay" class="editor-overlay">
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
    border-bottom: 4px solid var(--wa-color-surface-border);
    min-height: 2.25rem;
    flex-shrink: 0;
    flex-wrap: wrap;
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

.editor-overlay {
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
