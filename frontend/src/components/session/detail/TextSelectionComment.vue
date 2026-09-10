<script setup>
// TextSelectionComment.vue — Ephemeral floating widget for commenting on selected
// text in the session view. Shows a small button on text selection; clicking it
// expands a panel with a textarea + "Add to message" action. Nothing is persisted
// to IndexedDB — the user must send the message right away.

import { ref, inject, nextTick, computed, onMounted, onBeforeUnmount } from 'vue'
import { formatComment } from '../../../stores/codeComments'
import { useSettingsStore } from '../../../stores/settings'
import { isOpen as mediaPreviewOpen, openMediaPreview } from '../../../composables/useMediaPreview'
import { toast } from '../../../composables/useToast'

const props = defineProps({
    /** The text the user selected in the session view. */
    selectedText: { type: String, required: true },
    /** Viewport-relative position: { top, left, above } — anchored at selection edge. */
    position: { type: Object, required: true },
    /** When true, skip the collapsed button and open the panel immediately on mount. */
    autoExpand: { type: Boolean, default: false },
    /** Optional source suffix for the formatted output (e.g. "from terminal"). */
    sourceLabel: { type: String, default: '' },
    /** What the quoted block is in the formatted output (e.g. "selected area"). */
    subject: { type: String, default: 'selected text' },
    /** Optional source metadata used to enrich the formatted comment.
     *  Shape: { filePath: string, lineFrom: number, lineTo: number,
     *  quoteMode: 'code'|'quote', lang: string }. The last two override the
     *  `quoteMode` prop when the consumer decides per selection (the chat, where
     *  the same container holds prose and code views). */
    metadata: { type: Object, default: null },
    /** How the selection is wrapped in the formatted output: 'code' (fenced block,
     *  for anything verbatim) or 'quote' (blockquote, for prose). */
    quoteMode: { type: String, default: 'code' },
    /** Function called when the widget needs to dismiss the source selection
     *  (on Comment/Copy clicks). Defaults to clearing the DOM selection, which is
     *  enough for normal HTML; consumers (e.g. FilePane) override it to also clear
     *  the internal CodeMirror selection so the behavior is consistent there. */
    clearSourceSelection: { type: Function, default: () => window.getSelection()?.removeAllRanges() },
    /** Optional async () => dataUrl. When provided, an "Include screenshot"
     *  switch appears: toggling it on calls this to capture an image (the
     *  BrowserPane wires it to the companion). Absent → no switch. */
    captureScreenshot: { type: Function, default: null },
    /** Optional async (dataUrl) => void, run on "Add to message" to persist the
     *  captured screenshot as a draft attachment. Required alongside
     *  captureScreenshot for the screenshot to actually reach the message. */
    attachScreenshot: { type: Function, default: null },
    /** When true, "Add to message" focuses the composer (caret after the inserted
     *  text). Only the chat consumer sets it: the composer lives in that same pane,
     *  so it is visible and focusing it chains ⌘↵ (add) → ⌘↵ (send). Panes where
     *  the composer may sit behind an overlay (browser, files, terminal) leave it
     *  false. On touch, honoured for ⌘↵ only, not for a tap — see addToMessage(). */
    focusComposerOnAdd: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const insertTextAtCursor = inject('insertTextAtCursor', null)
const settingsStore = useSettingsStore()

const placeholderText = computed(() => {
    if (settingsStore.isTouchDevice) return 'Optional comment...'
    const keys = settingsStore.isMac ? '⌘↵ or Ctrl↵' : 'Ctrl↵ or Meta↵'
    return `Optional comment... (${keys} to add to message)`
})

// The help hint (discard-on-click-outside note + drag tip) is dismissible: once
// the user clicks "Dismiss", it stays hidden for good (persisted to localStorage).
const showHelpHint = computed(() => !settingsStore.selectionCommentHintDismissed)

function dismissHelpHint() {
    settingsStore.setSelectionCommentHintDismissed(true)
}

const expanded = ref(false)
const commentText = ref('')
const textareaRef = ref(null)
const rootRef = ref(null)
const isDragging = ref(false)

// ── Screenshot capture (opt-in, only when captureScreenshot is provided) ──
const includeScreenshot = ref(false)
const screenshotLoading = ref(false)   // capture round-trip in flight
const screenshotDataUrl = ref(null)    // the captured PNG, shown as a thumbnail
const submitting = ref(false)          // attaching + inserting on "Add to message"

// Combined pixel offset (drag + clamp corrections) from the base position.
const panelOffset = ref({ dx: 0, dy: 0 })
const SELECTION_GAP = 16

const rootStyle = computed(() => {
    const above = props.position.above
    const base = {
        left: props.position.left + 'px',
        top: (props.position.top + (above ? -SELECTION_GAP : SELECTION_GAP)) + 'px',
    }
    if (expanded.value) {
        const { dx, dy } = panelOffset.value
        const translateY = above ? `calc(-100% + ${dy}px)` : `${dy}px`
        base.transform = `translate(calc(-50% + ${dx}px), ${translateY})`
    } else {
        base.transform = above ? 'translate(-50%, -100%)' : 'translateX(-50%)'
    }
    return base
})

const canAdd = computed(() => !!insertTextAtCursor)

// ─── Viewport clamping ─────────────────────────────────────────────

/**
 * Measure the panel's rendered rect and nudge it back into the visible viewport.
 * Adds a correction delta to the current panelOffset (preserves user drag).
 * Uses visualViewport when available so it accounts for the mobile keyboard.
 */
function clampToViewport() {
    const el = rootRef.value
    if (!el) return

    const rect = el.getBoundingClientRect()
    const margin = 8
    const vv = window.visualViewport
    const viewportLeft = vv?.offsetLeft ?? 0
    const viewportTop = vv?.offsetTop ?? 0
    const viewportRight = viewportLeft + (vv?.width ?? window.innerWidth)
    const viewportBottom = viewportTop + (vv?.height ?? window.innerHeight)

    let dx = 0
    let dy = 0

    if (rect.left < viewportLeft + margin) dx = (viewportLeft + margin) - rect.left
    else if (rect.right > viewportRight - margin) dx = (viewportRight - margin) - rect.right

    if (rect.bottom > viewportBottom - margin) dy = (viewportBottom - margin) - rect.bottom
    if (rect.top + dy < viewportTop + margin) dy = (viewportTop + margin) - rect.top

    if (dx || dy) {
        panelOffset.value = {
            dx: panelOffset.value.dx + dx,
            dy: panelOffset.value.dy + dy,
        }
    }
}

let viewportClampScheduled = false

/** Re-clamp once per frame when the mobile keyboard changes the visible viewport. */
function onVisualViewportChange() {
    if (!expanded.value || viewportClampScheduled) return
    viewportClampScheduled = true
    window.requestAnimationFrame(() => {
        viewportClampScheduled = false
        if (expanded.value) clampToViewport()
    })
}

function addViewportListeners() {
    const vv = window.visualViewport
    if (vv) {
        vv.addEventListener('resize', onVisualViewportChange)
        vv.addEventListener('scroll', onVisualViewportChange)
    } else {
        window.addEventListener('resize', onVisualViewportChange)
    }
}

function removeViewportListeners() {
    const vv = window.visualViewport
    if (vv) {
        vv.removeEventListener('resize', onVisualViewportChange)
        vv.removeEventListener('scroll', onVisualViewportChange)
    } else {
        window.removeEventListener('resize', onVisualViewportChange)
    }
}

// ─── Drag (panel background as handle) ────────────────────────────

let dragStart = null

function onDragPointerDown(e) {
    // Only primary button / single touch
    if (e.button !== 0) return
    // Don't drag when interacting with quote (scrollable), textareas, buttons,
    // or the screenshot controls (switch + clickable thumbnail).
    const target = e.target
    if (target.closest('.tsc-quote, wa-textarea, wa-button, textarea, button, a, .tsc-shot')) return
    e.preventDefault()
    isDragging.value = true
    dragStart = { x: e.clientX, y: e.clientY, ...panelOffset.value }
    document.addEventListener('pointermove', onDragPointerMove)
    document.addEventListener('pointerup', onDragPointerUp)
}

function onDragPointerMove(e) {
    if (!dragStart) return
    panelOffset.value = {
        dx: dragStart.dx + (e.clientX - dragStart.x),
        dy: dragStart.dy + (e.clientY - dragStart.y),
    }
}

function onDragPointerUp() {
    dragStart = null
    isDragging.value = false
    document.removeEventListener('pointermove', onDragPointerMove)
    document.removeEventListener('pointerup', onDragPointerUp)
    clampToViewport()
}

// ─── Expand / close ────────────────────────────────────────────────

function expand() {
    panelOffset.value = { dx: 0, dy: 0 }
    expanded.value = true
    // Dismiss the source selection so the highlight goes away. In plain HTML the
    // browser already drops the page selection when the textarea below gains focus,
    // but for CodeMirror sources the internal selection persists — the consumer's
    // override clears it for consistency.
    props.clearSourceSelection?.()
    addViewportListeners()
    nextTick(() => {
        clampToViewport()
        textareaRef.value?.focus()
    })
}

function close() {
    removeViewportListeners()
    emit('close')
}

// Toggle the "Include screenshot" switch. Turning it on captures once (the
// result is cached in screenshotDataUrl); turning it off drops the capture.
async function onIncludeToggle(event) {
    const wantOn = event.target.checked
    includeScreenshot.value = wantOn
    if (!wantOn) {
        screenshotDataUrl.value = null
        return
    }
    if (screenshotDataUrl.value || !props.captureScreenshot) return
    screenshotLoading.value = true
    try {
        const dataUrl = await props.captureScreenshot()
        if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/')) {
            throw new Error('no image returned')
        }
        screenshotDataUrl.value = dataUrl
    } catch (e) {
        includeScreenshot.value = false
        toast.error(`Screenshot failed: ${e?.message || e}`)
    } finally {
        screenshotLoading.value = false
    }
}

// Enlarge the captured screenshot in the shared media dialog. It mounts
// outside this widget, so handleDocumentMousedown would otherwise treat the
// click as "outside" and dismiss the whole comment — the mediaPreviewOpen
// guard there keeps the widget alive while the preview is up.
function openShotPreview() {
    if (!screenshotDataUrl.value) return
    openMediaPreview([{ type: 'image', src: screenshotDataUrl.value, name: 'Screenshot' }])
}

/** @param {{fromKeyboard?: boolean}} [options] - `fromKeyboard` when ⌘↵ triggered
 *  the add, as opposed to a tap/click on the button. See the focus rule below. */
async function addToMessage({ fromKeyboard = false } = {}) {
    if (!canAdd.value || screenshotLoading.value) return

    submitting.value = true
    // Attach first: a rejected attachment (size cap, etc.) must not leave the
    // text inserted with no image — keep the widget open so the user can react.
    try {
        if (screenshotDataUrl.value && props.attachScreenshot) {
            await props.attachScreenshot(screenshotDataUrl.value)
        }
    } catch (e) {
        submitting.value = false
        toast.error(`Couldn't attach screenshot: ${e?.message || e}`)
        return
    }

    const formatted = formatComment(
        {
            lineText: props.selectedText,
            content: commentText.value,
            filePath: props.metadata?.filePath,
            lineFrom: props.metadata?.lineFrom,
            lineTo: props.metadata?.lineTo,
        },
        {
            isSelectedText: true,
            sourceLabel: props.sourceLabel,
            subject: props.subject,
            quoteMode: props.metadata?.quoteMode ?? props.quoteMode,
            lang: props.metadata?.lang ?? null,
        },
    )
    // Focus only in the chat pane: the composer is right there, and keeping the
    // focus lets the user send with a second ⌘↵. Elsewhere the composer may sit
    // behind a docked-pane overlay. The text lands in the draft either way.
    // On touch, a tap does not focus — that would pop the virtual keyboard and get
    // in the way of a follow-up selection. A ⌘↵ does, whatever the device: the
    // keystroke proves a physical keyboard, so chaining ⌘↵ (add) → ⌘↵ (send) is
    // exactly what the user is doing.
    const focusComposer = props.focusComposerOnAdd && (fromKeyboard || !settingsStore.isTouchDevice)
    insertTextAtCursor(formatted + '\n', { focus: focusComposer })
    submitting.value = false
    close()
}

function copy() {
    navigator.clipboard.writeText(props.selectedText)
    toast.success('Copied to clipboard', { duration: 2000 })
    props.clearSourceSelection?.()
    close()
}

function handleKeydown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        addToMessage({ fromKeyboard: true })
        return
    }
    if (e.key === 'Escape') {
        e.stopPropagation()
        close()
    }
}

// Close on any mousedown outside the widget — unless the enlarged-screenshot
// preview is open (it mounts elsewhere in the DOM, so a click inside it reads
// as "outside" here; dismissing the comment then would be wrong).
function handleDocumentMousedown(e) {
    if (rootRef.value?.contains(e.target)) return
    if (mediaPreviewOpen.value) return
    close()
}

onMounted(() => {
    document.addEventListener('mousedown', handleDocumentMousedown, true)
    if (props.autoExpand) expand()
})

onBeforeUnmount(() => {
    document.removeEventListener('mousedown', handleDocumentMousedown, true)
    document.removeEventListener('pointermove', onDragPointerMove)
    document.removeEventListener('pointerup', onDragPointerUp)
    removeViewportListeners()
})

defineExpose({ isExpanded: expanded })
</script>

<template>
    <div
        ref="rootRef"
        class="text-selection-comment"
        :class="{ expanded }"
        :style="rootStyle"
    >
        <!-- Collapsed: comment + copy buttons -->
        <div v-if="!expanded" class="tsc-collapsed">
            <wa-button
                class="tsc-trigger"
                variant="brand"
                appearance="filled-outlined"
                size="small"
                @mousedown.prevent
                @click.stop="expand"
            >
                <wa-icon name="comment" variant="regular"></wa-icon>
            </wa-button>
            <wa-button
                class="tsc-trigger"
                variant="neutral"
                appearance="filled"
                size="small"
                @mousedown.prevent
                @click.stop="copy"
            >
                <wa-icon name="copy" variant="regular"></wa-icon>
            </wa-button>
        </div>

        <!-- Expanded: comment panel (background is the drag handle) -->
        <div
            v-else
            class="tsc-panel"
            :class="{ dragging: isDragging }"
            @keydown="handleKeydown"
            @pointerdown="onDragPointerDown"
        >
            <!-- Selected text preview (scrollable) -->
            <div class="tsc-quote">{{ selectedText }}</div>

            <wa-textarea
                ref="textareaRef"
                :value="commentText"
                @input="commentText = $event.target.value"
                :placeholder="placeholderText"
                size="small"
                rows="3"
            ></wa-textarea>

            <!-- Optional screenshot capture (only when a capture handler is
                 provided, i.e. the Browser pane). -->
            <div v-if="captureScreenshot" class="tsc-shot">
                <div class="tsc-shot-row">
                    <wa-switch
                        size="small"
                        :checked="includeScreenshot"
                        :disabled="screenshotLoading || submitting"
                        @change="onIncludeToggle"
                    >Include screenshot</wa-switch>
                    <wa-spinner v-if="screenshotLoading"></wa-spinner>
                </div>
                <div
                    v-if="screenshotDataUrl"
                    class="tsc-shot-thumb"
                    @click="openShotPreview"
                >
                    <img :src="screenshotDataUrl" alt="Screenshot" />
                </div>
            </div>

            <div v-if="showHelpHint" class="tsc-help">
                <strong>Note:</strong> Clicking outside this dialog will discard the selection and any comment entered. Drag here to move the dialog.
                <a class="tsc-help-dismiss" href="#" @click.prevent="dismissHelpHint">Dismiss</a>
            </div>

            <div class="tsc-actions">
                <wa-button size="small" variant="neutral" appearance="outlined" :disabled="submitting" @click="close">
                    Cancel
                </wa-button>
                <wa-button
                    size="small"
                    variant="brand"
                    appearance="outlined"
                    :disabled="!canAdd || screenshotLoading || submitting"
                    @click="addToMessage()"
                >
                    Add to message
                </wa-button>
            </div>
        </div>
    </div>
</template>

<style scoped>
.text-selection-comment {
    position: fixed;
    z-index: 10000;
    /* transform is set dynamically in rootStyle based on selection direction */
}

/* ── Collapsed: side-by-side comment + copy buttons ──────────────── */

.tsc-collapsed {
    display: flex;
    gap: var(--wa-space-xs);
}

/* ── Panel ───────────────────────────────────────────────────────── */

.tsc-panel {
    width: 20rem;
    max-width: calc(100vw - 2rem);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-default);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    box-shadow: var(--wa-shadow-l);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    cursor: grab;
    touch-action: none; /* prevent scroll while dragging on mobile */
}

.tsc-panel.dragging {
    cursor: grabbing;
}

/* ── Selected text quote ─────────────────────────────────────────── */

.tsc-quote {
    max-height: 6.4em; /* ~4 lines */
    padding: var(--wa-space-xs) var(--wa-space-xs);
    border-left: 3px solid var(--wa-color-brand);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-lowered);
    font-size: var(--wa-font-size-s);
    line-height: 1.4;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    cursor: auto;
    touch-action: auto; /* allow native scroll within quote */
}

/* ── Screenshot capture ──────────────────────────────────────────── */

.tsc-shot {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.tsc-shot-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

/* Thumbnail matches the composer's attachment thumbnails (see
   MediaThumbnailGroup): square, rounded, cover-fit, click to enlarge. */
.tsc-shot-thumb {
    width: 96px;
    height: 96px;
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
    overflow: hidden;
    cursor: pointer;
}

.tsc-shot-thumb:hover {
    border-color: var(--wa-color-border-primary);
}

.tsc-shot-thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}

/* ── Help text ───────────────────────────────────────────────────── */

.tsc-help {
    font-size: var(--wa-font-size-s);
    line-height: 1.3;
    color: var(--wa-color-text-quiet);
}

/* Inline "Dismiss" link that permanently hides the hint above. */
.tsc-help-dismiss {
    color: var(--wa-color-brand);
    text-decoration: underline;
    cursor: pointer;
}

.tsc-help-dismiss:hover {
    text-decoration: none;
}

/* ── Actions ─────────────────────────────────────────────────────── */

.tsc-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-xs);
}
</style>
