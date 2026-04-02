<!-- frontend/src/components/DiffEditor.vue -->
<!-- Wraps @codemirror/merge as a Vue 3 component, supporting both side-by-side (MergeView)
     and unified (EditorView + unifiedMergeView extension) diff modes. -->
<template>
    <div class="diff-editor">
        <div ref="diffEl" class="diff-editor-content"></div>
        <div ref="panelContainerEl" class="diff-panel-container"></div>
    </div>
</template>

<script setup>
import { ref, nextTick, watch, inject, onMounted, onBeforeUnmount } from 'vue'
import { EditorView, keymap, panels } from '@codemirror/view'
import { EditorSelection } from '@codemirror/state'
import { MergeView, unifiedMergeView, goToNextChunk, goToPreviousChunk } from '@codemirror/merge'
import { openSearchPanel, getSearchQuery, setSearchQuery, searchPanelOpen, SearchQuery } from '@codemirror/search'
import { resolveLanguage, useCodeMirrorExtensions, useSettingsWatcher, toggleSearchPanel } from '../composables/useCodeMirror'
import { createCodeCommentsExtension, syncCommentsEffect } from '../extensions/codeComments'
import { smartCollapseUnchanged } from '../extensions/smartCollapseUnchanged'
import { useSettingsStore } from '../stores/settings'
import { useCodeCommentsStore, formatComment, formatAllComments } from '../stores/codeComments'

// ─── Props ───────────────────────────────────────────────────────────────────

const props = defineProps({
    original: { type: String, default: '' },
    modified: { type: String, default: '' },
    filePath: { type: String, default: null },
    language: { type: String, default: null },
    readOnly: { type: Boolean, default: true },
    wordWrap: { type: Boolean, default: false },
    sideBySide: { type: Boolean, default: true },
    collapseUnchanged: { type: Boolean, default: true },
    collapseStep: { type: Number, default: 20 },
    extensions: { type: Array, default: () => [] },
    /** Optional external DOM element for search/replace panels (side-by-side mode).
     *  When provided, panels are redirected there instead of the internal container.
     *  Useful when the DiffEditor is inside a scrollable container where sticky fails. */
    panelContainer: { type: Object, default: null },
    /** Comment context for inline annotations. Null = comments disabled. */
    commentContext: { type: Object, default: null },
})

// ─── Emits ───────────────────────────────────────────────────────────────────

const emit = defineEmits(['update:modified', 'save', 'ready'])

// ─── Template ref & state ────────────────────────────────────────────────────

const diffEl = ref(null)
const panelContainerEl = ref(null)

/** True when the document has unsaved local edits (since last external update). */
const isDirty = ref(false)

/** Flag to break the echo loop: set true when we emit an update, cleared next tick. */
let _internalUpdate = false

/** The current view instance: MergeView (side-by-side) or EditorView (unified). */
let currentView = null

/** Current mode: 'side-by-side' | 'unified' */
let currentMode = null

/** Cleanup function returned by useSettingsWatcher. */
let _stopSettingsWatcher = null

/** Generation counter: incremented on each create/destroy to abort stale async creations. */
let _createGeneration = 0

// ─── Extension compartments ───────────────────────────────────────────────────
// We manage two sets of compartments: one for the original side (a) and one for
// the modified side (b). For unified mode, only cmB is used (the single EditorView).

const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)
const initialSettings = { initialTheme: settingsStore.getEffectiveTheme, initialFontSize: settingsStore.getFontSize }

// Original side (a) — always read-only
const cmA = useCodeMirrorExtensions({
    readOnly: { value: true },
    wordWrap: { value: props.wordWrap },
}, initialSettings)

// Modified side (b) — read-only based on prop
const cmB = useCodeMirrorExtensions({
    readOnly: { value: props.readOnly },
    wordWrap: { value: props.wordWrap },
}, initialSettings)

// ─── Diff config ─────────────────────────────────────────────────────────────
// Override the default scanLimit (500) to produce more accurate diffs on large,
// heavily-changed files. The timeout acts as a safety net to avoid blocking the
// main thread on pathological inputs.
const diffConfig = { scanLimit: 10000, timeout: 2000 }

/** Build the smart collapse extension array (empty if collapse is disabled). */
function buildCollapseExtension() {
    if (!props.collapseUnchanged) return []
    return [smartCollapseUnchanged({ margin: 3, minSize: 4, step: props.collapseStep })]
}

function buildCommentExtension() {
    if (!props.commentContext) return []
    const ctx = props.commentContext
    const existingComments = codeCommentsStore.getCommentsForContext(ctx)
    return createCodeCommentsExtension({
        initialComments: existingComments.map(c => ({ lineNumber: c.lineNumber, content: c.content, lineText: c.lineText || '' })),
        onAdd: (lineNumber, lineText) => codeCommentsStore.addComment(ctx, lineNumber, lineText),
        onUpdate: (lineNumber, content) => codeCommentsStore.updateComment(ctx, lineNumber, content),
        onRemove: (lineNumber) => codeCommentsStore.removeComment(ctx, lineNumber),
        onAddToMessage: insertTextAtCursor ? (lineNumber) => {
            const comments = codeCommentsStore.getCommentsForContext(ctx)
            const comment = comments.find(c => c.lineNumber === lineNumber)
            if (comment) {
                insertTextAtCursor(formatComment(comment) + '\n')
                codeCommentsStore.removeComment(ctx, lineNumber)
            }
        } : null,
        onAddAllToMessage: insertTextAtCursor ? () => {
            const allComments = codeCommentsStore.getCommentsBySession(ctx.projectId, ctx.sessionId)
            if (allComments.length > 0) {
                insertTextAtCursor(formatAllComments(allComments) + '\n')
                codeCommentsStore.removeAllSessionComments(ctx.projectId, ctx.sessionId)
            }
        } : null,
        getSessionCommentCount: () => codeCommentsStore.getCommentsBySession(ctx.projectId, ctx.sessionId)
                .filter(c => c.content.trim()).length,
    })
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Returns the EditorView for the "modified" side, regardless of mode.
 * For side-by-side: MergeView.b
 * For unified: the single EditorView
 */
function getModifiedView() {
    if (!currentView) return null
    if (currentMode === 'side-by-side') return currentView.b
    return currentView
}

/**
 * Returns the EditorView for the "original" side.
 * Only meaningful in side-by-side mode.
 */
function getOriginalView() {
    if (!currentView || currentMode !== 'side-by-side') return null
    return currentView.a
}

// ─── Update listener & save keymap ───────────────────────────────────────────

function buildUpdateListener() {
    return EditorView.updateListener.of((update) => {
        if (update.docChanged) {
            isDirty.value = true
            _internalUpdate = true
            emit('update:modified', update.state.doc.toString())
            nextTick(() => { _internalUpdate = false })
        }
    })
}

function buildSaveKeymap() {
    return keymap.of([{
        key: 'Mod-s',
        run: () => {
            if (!props.readOnly && isDirty.value) emit('save')
            // Always return true to prevent browser's native Save dialog
            return true
        },
    }])
}

// ─── Settings watcher setup ───────────────────────────────────────────────────

function setupSettingsWatcher() {
    // Stop any existing watcher before setting up a new one
    if (_stopSettingsWatcher) {
        _stopSettingsWatcher()
        _stopSettingsWatcher = null
    }

    if (currentMode === 'side-by-side') {
        // In side-by-side mode, we need to reconfigure both EditorViews.
        // We register one watcher that updates both.
        _stopSettingsWatcher = useSettingsWatcher(
            () => getModifiedView(),
            cmB,
        )
        // Also watch for changes to update the original side (cmA)
        // We do this by wrapping: patch the stop function to also handle cmA
        const stopA = useSettingsWatcher(
            () => getOriginalView(),
            cmA,
        )
        const stopB = _stopSettingsWatcher
        _stopSettingsWatcher = () => { stopA(); stopB() }
    } else {
        // Unified mode: single EditorView managed by cmB
        _stopSettingsWatcher = useSettingsWatcher(
            () => getModifiedView(),
            cmB,
        )
    }
}

// ─── Create side-by-side (MergeView) ─────────────────────────────────────────

async function createSideBySideView() {
    const gen = ++_createGeneration
    const langExtension = await resolveLanguage(props.filePath, props.language)
    if (gen !== _createGeneration) return  // a destroy/create happened during the await

    const updateListener = buildUpdateListener()
    const saveKeymap = buildSaveKeymap()

    // Redirect search/replace panels to an external container so they stay
    // visible at the bottom instead of being clipped by MergeView's overflow.
    const panelsExt = panels({ bottomContainer: props.panelContainer || panelContainerEl.value })

    // Original side (a): always read-only, no save keymap, no update listener
    const aExtensions = [
        ...cmA.extensions,
        panelsExt,
        ...(langExtension ? [langExtension] : []),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]

    // Modified side (b): read-only based on prop, plus save keymap and update listener
    const bExtensions = [
        ...cmB.extensions,
        panelsExt,
        ...(langExtension ? [langExtension] : []),
        saveKeymap,
        updateListener,
        ...buildCommentExtension(),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]

    currentView = new MergeView({
        a: { doc: props.original, extensions: aExtensions },
        b: { doc: props.modified, extensions: bExtensions },
        parent: diffEl.value,
        root: document, // Force styles into document head, not WA shadow root
        collapseUnchanged: undefined,
        mergeControls: false,
        diffConfig,
    })

    currentMode = 'side-by-side'

    setupSettingsWatcher()
}

// ─── Create unified (EditorView + unifiedMergeView) ──────────────────────────

async function createUnifiedView() {
    const gen = ++_createGeneration
    const langExtension = await resolveLanguage(props.filePath, props.language)
    if (gen !== _createGeneration) return  // a destroy/create happened during the await

    const updateListener = buildUpdateListener()
    const saveKeymap = buildSaveKeymap()

    const unifiedExt = unifiedMergeView({
        original: props.original,
        highlightChanges: true,
        gutter: true,
        mergeControls: false,
        diffConfig,
    })

    const allExtensions = [
        ...cmB.extensions,
        ...(langExtension ? [langExtension] : []),
        unifiedExt,
        saveKeymap,
        updateListener,
        ...buildCommentExtension(),
        ...buildCollapseExtension(),
        ...props.extensions,
    ]

    currentView = new EditorView({
        doc: props.modified,
        extensions: allExtensions,
        parent: diffEl.value,
        root: document, // Force styles into document head, not WA shadow root
    })

    currentMode = 'unified'

    setupSettingsWatcher()
}

// ─── Search state preservation across mode switches ─────────────────────────

/** Saved search state to restore after a mode switch (unified ↔ side-by-side). */
let _savedSearchState = null

/**
 * Capture the current search query and panel-open state from the modified view.
 * Called before destroying the view so it can be restored on the new one.
 */
function saveSearchState() {
    const v = getModifiedView()
    if (!v) { _savedSearchState = null; return }
    const panelOpen = searchPanelOpen(v.state)
    if (!panelOpen) { _savedSearchState = null; return }
    _savedSearchState = getSearchQuery(v.state)
}

/**
 * Restore a previously saved search state on the modified view.
 * Opens the search panel and injects the saved query (search text, replace,
 * case sensitivity, regexp, whole word).
 */
function restoreSearchState() {
    if (!_savedSearchState) return
    const v = getModifiedView()
    if (!v) return
    const spec = _savedSearchState
    _savedSearchState = null
    // Open the panel first, then inject the query
    openSearchPanel(v)
    const query = new SearchQuery(spec)
    v.dispatch({ effects: setSearchQuery.of(query) })
}

// ─── Destroy ─────────────────────────────────────────────────────────────────

function destroyCurrentView() {
    _createGeneration++  // invalidate any in-progress async creation
    if (_stopSettingsWatcher) {
        _stopSettingsWatcher()
        _stopSettingsWatcher = null
    }
    if (currentView) {
        currentView.destroy()
        currentView = null
    }
    currentMode = null
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

onMounted(async () => {
    if (props.sideBySide) {
        await createSideBySideView()
    } else {
        await createUnifiedView()
    }
    emit('ready')
})

onBeforeUnmount(() => {
    destroyCurrentView()
})

// ─── Watchers ────────────────────────────────────────────────────────────────

// Mode switch: destroy + recreate in the other mode
watch(() => props.sideBySide, async (newSideBySide) => {
    saveSearchState()
    destroyCurrentView()
    isDirty.value = false
    if (newSideBySide) {
        await createSideBySideView()
    } else {
        await createUnifiedView()
    }
    restoreSearchState()
})

// Content changed (file/commit switch): original or modified prop changed.
// Destroy and recreate in both modes — in-place updates in unified mode are fragile
// (two separate dispatches for original + modified can desync the diff engine).
watch([() => props.original, () => props.modified], async () => {
    if (_internalUpdate) return

    const wasMode = currentMode
    destroyCurrentView()
    if (wasMode === 'side-by-side') {
        await createSideBySideView()
    } else {
        await createUnifiedView()
    }
    isDirty.value = false
})

// readOnly toggle — only affects the modified side (b)
watch(() => props.readOnly, (newReadOnly) => {
    const view = getModifiedView()
    if (!view) return
    cmB.reconfigure(view, 'readOnly', newReadOnly)
})

// wordWrap toggle — affects both sides
watch(() => props.wordWrap, (newWordWrap) => {
    const modView = getModifiedView()
    if (modView) cmB.reconfigure(modView, 'wordWrap', newWordWrap)

    const origView = getOriginalView()
    if (origView) cmA.reconfigure(origView, 'wordWrap', newWordWrap)
})

// File path change — re-resolve language and reconfigure both sides
watch(() => props.filePath, async () => {
    const langExtension = await resolveLanguage(props.filePath, props.language)
    const modView = getModifiedView()
    if (modView) cmB.reconfigure(modView, 'language', langExtension)
    const origView = getOriginalView()
    if (origView) cmA.reconfigure(origView, 'language', langExtension)
})

// Explicit language override change
watch(() => props.language, async () => {
    const langExtension = await resolveLanguage(props.filePath, props.language)
    const modView = getModifiedView()
    if (modView) cmB.reconfigure(modView, 'language', langExtension)
    const origView = getOriginalView()
    if (origView) cmA.reconfigure(origView, 'language', langExtension)
})

// Code comments: sync decorations when store changes (handles late hydration)
watch(
    () => props.commentContext ? codeCommentsStore.getCommentsForContext(props.commentContext) : null,
    (comments) => {
        const v = getModifiedView()
        if (!v || !comments) return
        v.dispatch({
            effects: syncCommentsEffect.of(
                comments.map(c => ({ lineNumber: c.lineNumber, content: c.content, lineText: c.lineText || '' }))
            ),
        })
    },
)

// Code comments: broadcast session-wide "with content" count changes to CM6 widgets.
watch(
    () => {
        if (!props.commentContext) return 0
        return codeCommentsStore.getCommentsBySession(
            props.commentContext.projectId, props.commentContext.sessionId
        ).filter(c => c.content.trim()).length
    },
    (newCount) => {
        const v = getModifiedView()
        if (!v) return
        v.dom.dispatchEvent(new CustomEvent(
            'code-comment-count-changed', { detail: { count: newCount } }
        ))
    },
)

// ─── Diff navigation ─────────────────────────────────────────────────────────

function goToNext() {
    const v = getModifiedView()
    if (v) {
        v.focus()
        goToNextChunk(v)
    }
}

function goToPrev() {
    const v = getModifiedView()
    if (v) {
        v.focus()
        goToPreviousChunk(v)
    }
}

// ─── Exposed API ─────────────────────────────────────────────────────────────

/**
 * Scroll the modified-side editor so that the given 1-based line number
 * is visible, placing it near the center of the viewport and making it the active line.
 */
function scrollToLine(lineNum) {
    const v = getModifiedView()
    if (!v) return
    const lineCount = v.state.doc.lines
    const clampedLine = Math.max(1, Math.min(lineNum, lineCount))
    const line = v.state.doc.line(clampedLine)
    v.dispatch({
        selection: EditorSelection.cursor(line.from),
        effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    })
}

defineExpose({
    goToNextChunk: goToNext,
    goToPreviousChunk: goToPrev,
    scrollToLine,
    isDirty,
    resetDirty() { isDirty.value = false },
    openSearch() { toggleSearchPanel(getModifiedView()) },
})
</script>

<style scoped>
.diff-editor {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}

.diff-editor-content {
    flex: 1;
    min-height: 0;
}

.diff-editor-content :deep(.cm-editor),
.diff-editor-content :deep(.cm-mergeView) {
    height: 100%;
}

.diff-panel-container {
    flex-shrink: 0;
    /* Stick to the bottom of the nearest scroll ancestor so the search panel
       stays visible even when the diff content is taller than the viewport
       (e.g. inside ToolDiffViewer's constrained max-height container). */
    position: sticky;
    bottom: 0;
    z-index: 300;
}
</style>

<style>
/* ── Diff highlighting ────────────────────────────────────────────────── */
/* Colors are defined as CSS variables in App.vue (:root / .wa-dark)     */
    
.diff-editor .cm-content {

	.cm-changedLine {
		--diff-changeLineBackground: transparent;
	    background: var(--diff-changeLineBackground);
        --diff-changeLineBackground: var(--diff-insertedLineBackground);
		&:has(.cm-deletedLine) {
			--diff-changeLineBackground: var(--diff-removedLineBackground);
		}
	}
	.cm-deletedChunk {
	    background: var(--diff-removedLineBackground);
	}

	.cm-insertedLine, .cm-deletedLine {
	    background: transparent;
        &::selection, ::selection {
             background: var(--diff-selectionBackground) !important;
        }
	}

	.cm-line {
        .cm-changedText, .cm-deletedText {
	        border-bottom: none;
	        display: inline-block;
            --diff-textBackground: transparent;
	        background: var(--diff-textBackground);
            &::selection, ::selection {
                 background: var(--diff-selectionBackground) !important;
            }
        }
	}

	.cm-insertedLine .cm-changedText {
        --diff-textBackground: var(--diff-insertedTextBackground);
	}
	.cm-deletedLine {
		.cm-changedText, .cm-deletedText {
	    	--diff-textBackground: var(--diff-removedTextBackground);
	    }
	}

    .cm-mergeSpacer {
        --stripe-width: 5px;
        background: repeating-linear-gradient(
            -45deg,
            transparent,
            transparent 4px,
            var(--wa-color-surface-lowered) 4px,
            var(--wa-color-surface-lowered) calc(4px + var(--stripe-width))
        );
    }

}

.diff-editor .cm-merge-a .cm-changedLine {
    --diff-changeLineBackground: var(--diff-removedLineBackground);
}

/* Force background in dark mode */
html.wa-dark {
  .cm-editor, .cm-gutters {
      background: var(--wa-color-surface-default) !important;
  }
}

/* Better active line gutter in dark mode */
html.wa-dark {
    .cm-editor .cm-activeLineGutter {
      background: var(--wa-color-surface-lowered) !important;
    }
}

  
/* Collapsed unchanged lines separator (dark mode only, unscoped for .wa-dark ancestor) */
html.wa-dark .diff-editor .cm-collapsedLines {
    background: var(--wa-color-surface-lowered);
    color: var(--wa-color-text-quiet)
}
html.wa-dark .diff-editor .cm-collapsedLines .cm-collapsedLines-action:hover {
    color: var(--wa-color-text-default);
}
</style>
