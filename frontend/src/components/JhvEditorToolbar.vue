<script setup>
/**
 * JhvEditorToolbar — Toolbar wrapper for CodeMirror editors inside JsonHumanView.
 *
 * Provides a small header with:
 *   - Search button (always)
 *   - Word wrap toggle (always)
 *   - Side-by-side toggle (diff mode only, when container is wide enough)
 *
 * Renders the editor via a default slot. For diff mode, includes a ResizeObserver
 * gate so the DiffEditor only mounts once the container width is known (avoids
 * the double-create race when effectiveSideBySide flips after mount).
 *
 * Usage:
 *   <JhvEditorToolbar mode="diff" :editor-ref="myDiffEditorRef">
 *       <DiffEditor ref="myDiffEditorRef" :side-by-side="..." :word-wrap="..." ... />
 *   </JhvEditorToolbar>
 *
 *   <JhvEditorToolbar mode="code" :editor-ref="myCodeEditorRef">
 *       <CodeEditor ref="myCodeEditorRef" :word-wrap="..." ... />
 *   </JhvEditorToolbar>
 */
import { ref, computed, watch, useId } from 'vue'
import AppTooltip from './AppTooltip.vue'
import { useSettingsStore } from '../stores/settings'

const SIDE_BY_SIDE_MIN_WIDTH = 900

const props = defineProps({
    /** 'diff' for DiffEditor, 'code' for CodeEditor */
    mode: {
        type: String,
        default: 'code',
        validator: v => ['diff', 'code'].includes(v),
    },
    /** Ref to the editor component (CodeEditor or DiffEditor). Used to call openSearch(). */
    editorRef: {
        type: Object,
        default: null,
    },
})

const settingsStore = useSettingsStore()

// ============================================================================
// Toggle state
// ============================================================================

const wordWrap = ref(settingsStore.isToolDiffWordWrap)
const sideBySide = ref(settingsStore.isToolDiffSideBySide)

function onWordWrapToggle(event) {
    wordWrap.value = event.target.checked
}
function onSideBySideToggle(event) {
    sideBySide.value = event.target.checked
}

// ============================================================================
// Container width tracking (for side-by-side availability in diff mode)
// ============================================================================

const editorAreaRef = ref(null)
const editorAreaWidth = ref(0)
const editorReady = ref(false)

watch(editorAreaRef, (el, _, onCleanup) => {
    if (!el) return
    if (props.mode !== 'diff') {
        // Code mode: no resize gate needed
        editorReady.value = true
        return
    }
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

// ============================================================================
// Search
// ============================================================================

const searchButtonId = useId()

function openSearch() {
    props.editorRef?.openSearch()
}

// ============================================================================
// Expose state so parent can bind editor props
// ============================================================================

defineExpose({
    wordWrap,
    sideBySide,
    effectiveSideBySide,
    editorReady,
})
</script>

<template>
    <div class="jhv-toolbar">
        <div class="jhv-toolbar-header">
            <div class="jhv-toolbar-left">
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
                <AppTooltip :for="searchButtonId">Search</AppTooltip>
            </div>
            <div class="jhv-toolbar-right">
                <wa-switch
                    :checked="wordWrap"
                    size="small"
                    @change="onWordWrapToggle"
                >Wrap</wa-switch>
                <wa-switch
                    v-if="mode === 'diff' && canSideBySide"
                    :checked="sideBySide"
                    size="small"
                    class="jhv-toolbar-sbs"
                    @change="onSideBySideToggle"
                >Side by side</wa-switch>
            </div>
        </div>
        <div ref="editorAreaRef" class="jhv-toolbar-body">
            <slot
                :word-wrap="wordWrap"
                :side-by-side="effectiveSideBySide"
                :editor-ready="editorReady"
            />
        </div>
    </div>
</template>

<style scoped>
.jhv-toolbar {
    display: flex;
    flex-direction: column;
    /* Fill parent height when the parent has a fixed height (e.g. .jhv-edit-diff).
       Harmless when the parent has no explicit height (computes to auto). */
    height: 100%;
}

.jhv-toolbar-header {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-xs);
    padding-bottom: var(--wa-space-3xs);
}

.jhv-toolbar-left,
.jhv-toolbar-right {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.jhv-toolbar-sbs {
    flex-shrink: 0;
}

.jhv-toolbar-body {
    flex: 1;
    min-height: 0;
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}
</style>
