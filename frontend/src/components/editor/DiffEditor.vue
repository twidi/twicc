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
import { EditorView, keymap, lineNumbers, panels } from '@codemirror/view'
import { EditorSelection, Transaction } from '@codemirror/state'
import { MergeView, unifiedMergeView, goToNextChunk, goToPreviousChunk, getChunks } from '@codemirror/merge'
import { openSearchPanel, getSearchQuery, setSearchQuery, searchPanelOpen, SearchQuery } from '@codemirror/search'
import { resolveLanguage, useCodeMirrorExtensions, useSettingsWatcher, toggleSearchPanel } from '../../composables/useCodeMirror'
import { createCodeCommentsExtension, syncCommentsEffect } from '../../extensions/codeComments'
import { smartCollapseUnchanged } from '../../extensions/smartCollapseUnchanged'
import { patchEllipsis } from '../../extensions/patchEllipsis'
import { useSettingsStore } from '../../stores/settings'
import { useCodeCommentsStore, formatComment, formatAllComments } from '../../stores/codeComments'

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
    /** Line number map for the original side (patch-only mode). null entries = ellipsis separators. */
    originalLineMap: { type: Array, default: null },
    /** Line number map for the modified side (patch-only mode). null entries = ellipsis separators. */
    modifiedLineMap: { type: Array, default: null },
})

// ─── Emits ───────────────────────────────────────────────────────────────────

const emit = defineEmits(['update:modified', 'save', 'ready', 'cm-update'])

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
const initialSettings = { initialTheme: settingsStore.getEffectiveColorScheme, initialFontSize: settingsStore.getFontSize }

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

/** Whether we're in patch-only mode (have line maps but no full file). */
const isPatchOnly = () => !!(props.originalLineMap || props.modifiedLineMap)

/**
 * Build a lineNumbers extension with formatNumber that maps doc line numbers
 * to real file line numbers using a lineMap.  null entries show empty string
 * (the ellipsis widget already covers the separator visually).
 */
function buildLineNumbers(lineMap) {
    if (!lineMap) return lineNumbers()
    return lineNumbers({
        formatNumber: (docLineNumber) => {
            const realLine = lineMap[docLineNumber - 1]
            return realLine == null ? '' : String(realLine)
        },
    })
}

/**
 * Build the collapse/ellipsis extension array.
 * - Full-file mode: smartCollapseUnchanged (interactive expand/collapse)
 * - Patch-only mode: patchEllipsis (static "···" separators at null lineMap entries)
 * - Collapse disabled: empty
 */
function buildCollapseOrEllipsis(lineMap) {
    if (!props.collapseUnchanged) return []
    if (lineMap) return [patchEllipsis(lineMap)]
    return [smartCollapseUnchanged({ margin: 3, minSize: 4, step: props.collapseStep })]
}

function buildCommentExtension() {
    if (!props.commentContext) return []
    const ctx = props.commentContext
    const lineMap = props.modifiedLineMap
    const existingComments = codeCommentsStore.getCommentsForContext(ctx)
    return createCodeCommentsExtension({
        initialComments: existingComments.map(c => ({ lineNumber: c.lineNumber, content: c.content, lineText: c.lineText || '' })),
        getContent: (lineNumber) => {
            const comments = codeCommentsStore.getCommentsForContext(ctx)
            const comment = comments.find(c => c.lineNumber === lineNumber)
            return comment?.content ?? ''
        },
        onAdd: (lineNumber, lineText) => {
            // In patch-only mode, translate doc line to real file line for display
            const displayLineNumber = lineMap ? (lineMap[lineNumber - 1] ?? null) : null
            codeCommentsStore.addComment(ctx, lineNumber, lineText, displayLineNumber)
        },
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

// ─── Cursor tracking (for "View in Files" line targeting) ────────────────────
// Records the last line the *user* put the cursor on in the modified side.
// CodeMirror always has a selection (a cursor sits at line 1 from creation), so
// reading the selection alone can't tell "user clicked line 1" from "never
// clicked". Tracking only user-driven moves lets getViewTargetLine() fall back
// to the first changed line when the user never interacted with the diff.
let _userCursorLine = null

/** Map a modified-side doc line to its real file line (patch-only mode), else identity. */
function toRealLine(docLine) {
    if (docLine == null) return null
    if (props.modifiedLineMap) return props.modifiedLineMap[docLine - 1] ?? null
    return docLine
}

/** Update listener (added to the modified side only) recording user cursor moves. */
function buildCursorTracker() {
    return EditorView.updateListener.of((update) => {
        if (!update.selectionSet) return
        // Only count selection changes caused by user interaction (click,
        // keyboard, typing) — CM tags those with a userEvent. The initial
        // selection and our own programmatic dispatches (scrollToLine) carry none.
        if (!update.transactions.some(tr => tr.annotation(Transaction.userEvent) != null)) return
        _userCursorLine = toRealLine(update.state.doc.lineAt(update.state.selection.main.head).number)
    })
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
        // Surface selection/focus changes so consumers (e.g. FilePane's text
        // selection widget) can re-read the DOM selection on demand. Works
        // around Firefox cases where `selectionchange` isn't fired when CM
        // finalizes the DOM selection.
        if (update.selectionSet || update.focusChanged) {
            emit('cm-update')
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
        ...buildCollapseOrEllipsis(props.originalLineMap),
        ...props.extensions,
    ]

    // Modified side (b): read-only based on prop, plus save keymap and update listener
    const bExtensions = [
        ...cmB.extensions,
        panelsExt,
        ...(langExtension ? [langExtension] : []),
        saveKeymap,
        updateListener,
        buildCursorTracker(),
        ...buildCommentExtension(),
        ...buildCollapseOrEllipsis(props.modifiedLineMap),
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

    // In patch-only mode, reconfigure line numbers to show real file line numbers
    if (props.originalLineMap) {
        currentView.a.dispatch({
            effects: cmA.lineNumbersCompartment.reconfigure(buildLineNumbers(props.originalLineMap)),
        })
    }
    if (props.modifiedLineMap) {
        currentView.b.dispatch({
            effects: cmB.lineNumbersCompartment.reconfigure(buildLineNumbers(props.modifiedLineMap)),
        })
    }

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
        buildCursorTracker(),
        ...buildCommentExtension(),
        ...buildCollapseOrEllipsis(props.modifiedLineMap),
        ...props.extensions,
    ]

    currentView = new EditorView({
        doc: props.modified,
        extensions: allExtensions,
        parent: diffEl.value,
        root: document, // Force styles into document head, not WA shadow root
    })

    // In patch-only mode, reconfigure line numbers to show real file line numbers
    if (props.modifiedLineMap) {
        currentView.dispatch({
            effects: cmB.lineNumbersCompartment.reconfigure(buildLineNumbers(props.modifiedLineMap)),
        })
    }

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
    _userCursorLine = null  // new file/commit ⇒ forget the previous cursor
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

/**
 * Line to target when opening this diff's file in the Files tab:
 *  - the line the user last placed the cursor on (modified side), if any;
 *  - else the first changed line (top of the first diff chunk);
 *  - else null (no changes — caller opens at the top of the file).
 */
function getViewTargetLine() {
    if (_userCursorLine != null) return _userCursorLine
    const v = getModifiedView()
    if (!v) return null
    const chunks = getChunks(v.state)?.chunks
    if (!chunks || !chunks.length) return null
    return toRealLine(v.state.doc.lineAt(chunks[0].fromB).number)
}

defineExpose({
    goToNextChunk: goToNext,
    goToPreviousChunk: goToPrev,
    scrollToLine,
    getViewTargetLine,
    isDirty,
    resetDirty() { isDirty.value = false },
    openSearch() { toggleSearchPanel(getModifiedView()) },
    focus() { getModifiedView()?.focus() },
    getModifiedView,
    getOriginalView,
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

/* Inside a card (the Files, Git and Artifacts tabs) the editor and its gutters show the card, in light
   as in dark: the CodeMirror light theme paints its own white, and the dark rule above only gets there
   through the surface token, which is transparent in a card. */
.panel-card .cm-editor,
.panel-card .cm-gutters {
    background: transparent !important;
}

/* The active line's gutter cell: the accent highlight of the app's highlighted rows, in both schemes
   (it was the theme's pale blue in light and the lowered surface in dark). */
.cm-editor .cm-activeLineGutter {
    background: var(--glass-item-highlight) !important;
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
