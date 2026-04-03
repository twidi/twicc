<script setup>
import { ref, computed, watch, useId, inject } from 'vue'
import DiffEditor from '../../DiffEditor.vue'
import CodeEditor from '../../CodeEditor.vue'
import AppTooltip from '../../AppTooltip.vue'
import { useSettingsStore } from '../../../stores/settings'

const SIDE_BY_SIDE_MIN_WIDTH = 900

const props = defineProps({
    /** 'diff' = show DiffEditor, 'full' = show CodeEditor read-only */
    mode: { type: String, default: 'diff', validator: v => ['diff', 'full'].includes(v) },
    /** Original content (for diff mode) */
    original: { type: String, default: '' },
    /** Modified content (diff mode) or full content (full mode) */
    modified: { type: String, default: '' },
    /** File path for language detection */
    filePath: { type: String, default: null },
})

const toolContext = inject('codeCommentToolContext', null)

const commentContext = computed(() => {
    if (!toolContext || !props.filePath) return null
    return {
        projectId: toolContext.projectId,
        sessionId: toolContext.sessionId,
        subagentSessionId: toolContext.subagentSessionId || '',
        source: 'tool',
        sourceRef: toolContext.toolUseId,
        filePath: props.filePath,
        toolLineNum: toolContext.lineNum ?? null,
        subagentToolLineNum: toolContext.subagentToolLineNum ?? null,
    }
})

const searchButtonId = useId()
const diffEditorRef = ref(null)
const codeEditorRef = ref(null)
const searchPanelEl = ref(null)

const settingsStore = useSettingsStore()

// Toggle state — initialized from tool diff settings
const wordWrap = ref(settingsStore.isToolDiffWordWrap)
const sideBySide = ref(settingsStore.isToolDiffSideBySide)

// Container width tracking for side-by-side availability.
// editorReady gates the rendering of DiffEditor/CodeEditor: we defer mounting
// until the first ResizeObserver measurement so that effectiveSideBySide has its
// correct value from the start. Without this, DiffEditor mounts with sideBySide=false
// (width not yet known), then a watcher flips it to true while the async createView
// is still pending, resulting in two editor instances in the DOM.
const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
const editorReady = ref(false)

watch(editorAreaRef, (el, _, onCleanup) => {
    if (!el) return
    const observer = new ResizeObserver(entries => {
        const w = entries[0]?.contentRect?.width
        if (w > 0) {
            editorAreaWidth.value = w
            editorReady.value = true
        }
    })
    observer.observe(el)
    onCleanup(() => observer.disconnect())
}, { flush: 'post' })

const canSideBySide = computed(() => editorAreaWidth.value > SIDE_BY_SIDE_MIN_WIDTH)
const effectiveSideBySide = computed(() => sideBySide.value && canSideBySide.value)

function onWordWrapToggle(event) {
    wordWrap.value = event.target.checked
}
function onSideBySideToggle(event) {
    sideBySide.value = event.target.checked
}

function openSearch() {
    if (props.mode === 'diff') {
        diffEditorRef.value?.openSearch()
    } else {
        codeEditorRef.value?.openSearch()
    }
}
</script>

<template>
    <div class="tool-diff-viewer">
        <div class="tool-diff-header">
            <div class="tool-diff-header-left">
                <wa-button
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
            </div>
            <div class="tool-diff-header-right">
                <wa-switch
                    :checked="wordWrap"
                    size="small"
                    @change="onWordWrapToggle"
                >Wrap</wa-switch>
                <wa-switch
                    v-if="mode === 'diff' && canSideBySide"
                    :checked="sideBySide"
                    size="small"
                    class="side-by-side-toggle"
                    @change="onSideBySideToggle"
                >Side by side</wa-switch>
            </div>
        </div>
        <div ref="editorAreaRef" class="tool-diff-body">
            <template v-if="editorReady">
                <DiffEditor
                    v-if="mode === 'diff'"
                    ref="diffEditorRef"
                    :original="original"
                    :modified="modified"
                    :file-path="filePath"
                    :read-only="true"
                    :word-wrap="wordWrap"
                    :side-by-side="effectiveSideBySide"
                    :collapse-unchanged="true"
                    :panel-container="searchPanelEl"
                    :comment-context="commentContext"
                />
                <CodeEditor
                    v-else
                    ref="codeEditorRef"
                    :model-value="modified"
                    :file-path="filePath"
                    :read-only="true"
                    :word-wrap="wordWrap"
                    :comment-context="commentContext"
                />
            </template>
        </div>
        <!-- Search panel container: outside .tool-diff-body so it's not inside
             the scrollable area where sticky positioning fails -->
        <div ref="searchPanelEl" class="tool-diff-search-panel"></div>
    </div>
</template>

<style scoped>
.tool-diff-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-xs);
    padding-bottom: var(--wa-space-xs);
}

.tool-diff-header-left,
.tool-diff-header-right {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.side-by-side-toggle {
    flex-shrink: 0;
}

/* Height is constrained on the outer container rather than on individual
   CM editors (via EditorView.theme). This preserves MergeView's built-in
   scroll synchronization in side-by-side mode, which breaks when each
   editor has its own independent maxHeight scroll container. */
.tool-diff-body {
    max-height: 20rem;
    overflow: auto;
    border-radius: var(--wa-border-radius-m);
}

/* Let editors size to content — the outer container clips and scrolls */
.tool-diff-body :deep(.diff-editor),
.tool-diff-body :deep(.code-editor),
.tool-diff-body :deep(.cm-mergeView),
.tool-diff-body :deep(.cm-editor) {
    height: auto;
}
</style>
