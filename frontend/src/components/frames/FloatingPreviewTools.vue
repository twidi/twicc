<script setup>
import { ref, computed, watch, useId, onMounted, onBeforeUnmount } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import AppTooltip from '../ui/AppTooltip.vue'

// The caller owns the frame pool. Drag events suspend iframe pointer handling.
const props = defineProps({
    actions: { type: Array, default: () => [] },
    visible: { type: Boolean, default: true },
    fullscreen: { type: Boolean, default: false },
    fullscreenDisabled: { type: Boolean, default: false },
    // Full screen leads the actions instead of closing them (inline artifacts).
    fullscreenFirst: { type: Boolean, default: false },
    modeActive: { type: Boolean, default: false },
    resetKey: { type: String, default: '' },
    container: { type: Object, default: null },
    // Both rectangles use viewport coordinates. No frame pool dependency.
    frameRect: { type: Object, default: null },
    visibleBounds: { type: Object, default: null },
})
const emit = defineEmits(['toggle-fullscreen', 'drag-start', 'drag-end'])
const previewToolsButtonId = `preview-tools-${useId()}`
const previewFullscreenButtonId = `preview-fullscreen-${useId()}`
// --- Floating preview actions: collapsed behind a round "tools" toggle ------
// With two or more actions offered, the floating row folds behind a single
// round screwdriver-wrench button (click to unfold) so it doesn't crowd the
// rendered page. A preview offering only Full screen (markdown, PDF, media…)
// keeps its direct button — a toggle hiding one action would just add a click.
const previewActionsExpanded = ref(false)

const previewActionCount = computed(() => props.actions.length + 1)

// Full screen joins the caller's actions in one ordered list, so the keyboard order
// follows the visual order wherever it sits.
const orderedButtons = computed(() => {
    const fullscreenButton = {
        id: previewFullscreenButtonId,
        icon: props.fullscreen ? 'compress' : 'expand',
        label: props.fullscreen ? 'Exit full screen' : 'Full screen',
        disabled: props.fullscreenDisabled && !props.fullscreen,
        action: () => emit('toggle-fullscreen'),
    }
    return props.fullscreenFirst ? [fullscreenButton, ...props.actions] : [...props.actions, fullscreenButton]
})
const previewActionsCollapsible = computed(() => previewActionCount.value > 1)

// Fold back AND recenter on file switch — both are transient per-page state.
watch(() => props.resetKey, () => {
    previewActionsExpanded.value = false
    previewActionsPos.value = null
    clippedDefaultPos.value = null
    defaultOffsets = null
    openUpward.value = false
    previewTooltipPlacement.value = 'left'
})

// The floating actions can overlap the rendered page; let the user drag them
// (mouse / touch) out of the way by grabbing the round tools button. Position
// is null = default (CSS top-right) or an explicit { top, left } in px within
// the actions' offset parent (the frame-overlay cell when teleported over the
// pooled HTML frame, else .file-pane-preview). Per-pane, in-memory.
const previewActionsRef = ref(null)
const previewActionsPos = ref(null)
const clippedDefaultPos = ref(null)
let defaultOffsets = null
const effectiveActionsPos = computed(() => previewActionsPos.value ?? clippedDefaultPos.value)
const previewActionsStyle = computed(() =>
    effectiveActionsPos.value
        ? { top: `${effectiveActionsPos.value.top}px`, left: `${effectiveActionsPos.value.left}px`, right: 'auto' }
        : null
)

// The unfolded actions drop as a column below the tools button, or above it
// when the button sits too low for the column to fit below (the button never
// moves — only the drop direction flips). Recomputed when the menu opens and
// after a drag. ~34px per action button is a rough row height, enough to pick
// a side; exactness doesn't matter.
const openUpward = ref(false)
// Tooltips point inward (left) so they don't run off the right edge where the
// group lives by default; flipped to the right only when the group is dragged
// too close to the container's left edge for a left tooltip to fit.
const previewTooltipPlacement = ref('left')

function computePreviewActionsGeometry() {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) {
        openUpward.value = false
        previewTooltipPlacement.value = 'left'
        return
    }
    const r = el.getBoundingClientRect() // the tools-button box (collapsible layout)
    const pr = parent.getBoundingClientRect()
    // Vertical: drop the column up when it wouldn't fit below.
    const needed = previewActionCount.value * 34 + 8
    // A drag or resize can update position before Vue patches the DOM.
    // Use that position immediately, keeping DOM geometry for the default corner.
    const bounds = actionsBounds()
    const top = effectiveActionsPos.value?.top ?? r.top - pr.top
    const left = effectiveActionsPos.value?.left ?? r.left - pr.left
    const spaceBelow = bounds.bottom - top - r.height
    const spaceAbove = top - bounds.top
    openUpward.value = spaceBelow < needed && spaceAbove > spaceBelow
    // Horizontal: a left tooltip needs ~140px of room on the left of the group.
    previewTooltipPlacement.value = left - bounds.left < 140 ? 'right' : 'left'
}
// A real drag must not fire the tools button's fold/unfold click. Reset on
// each pointerdown so a drag that ends off-target (no click) can't wedge it.
let actionsDrag = null // { pointerId, startX, startY, baseLeft, baseTop, moved }
let suppressToolsClick = false

// Clip changes can move the visible region without resizing the overlay parent.
// Keep the intersection calculation here for both Files and inline callers.
function actionsBounds() {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!parent) return null
    const pr = parent.getBoundingClientRect()
    const frame = props.frameRect ?? { x: pr.left, y: pr.top, width: pr.width, height: pr.height }
    const clip = props.visibleBounds
    return {
        left: clip ? Math.max(0, clip.x - frame.x) : 0,
        top: clip ? Math.max(0, clip.y - frame.y) : 0,
        right: clip ? Math.min(frame.width, clip.x + clip.width - frame.x) : frame.width,
        bottom: clip ? Math.min(frame.height, clip.y + clip.height - frame.y) : frame.height,
    }
}

function clampActionsPos(left, top) {
    const el = previewActionsRef.value
    const bounds = actionsBounds()
    if (!el || !bounds || bounds.right <= bounds.left || bounds.bottom <= bounds.top) return { left, top }
    return {
        left: Math.max(bounds.left, Math.min(bounds.right - el.offsetWidth, left)),
        top: Math.max(bounds.top, Math.min(bounds.bottom - el.offsetHeight, top)),
    }
}

function reclampActions() {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    // Hidden or detached cached frames must not reset their placement.
    if (!props.visible || !el || !parent || el.offsetWidth < 1 || el.offsetHeight < 1
        || parent.clientWidth < 1 || parent.clientHeight < 1) return
    const bounds = actionsBounds()
    if (bounds.right <= bounds.left || bounds.bottom <= bounds.top) return
    if (previewActionsPos.value) {
        previewActionsPos.value = clampActionsPos(previewActionsPos.value.left, previewActionsPos.value.top)
    } else if (props.frameRect || props.visibleBounds) {
        const pr = parent.getBoundingClientRect()
        if (!defaultOffsets) {
            const r = el.getBoundingClientRect()
            defaultOffsets = { top: r.top - pr.top, right: pr.width - (r.left - pr.left) - el.offsetWidth }
        }
        const desired = { left: pr.width - defaultOffsets.right - el.offsetWidth, top: defaultOffsets.top }
        const clamped = clampActionsPos(desired.left, desired.top)
        clippedDefaultPos.value = clamped.left === desired.left && clamped.top === desired.top ? null : clamped
    } else {
        clippedDefaultPos.value = null
        defaultOffsets = null
    }
    computePreviewActionsGeometry()
}

// A resize clamps dragged positions. A moving clip also clamps the CSS corner.
useResizeObserver(() => props.container ?? previewActionsRef.value?.parentElement, reclampActions)
onMounted(reclampActions)
watch(() => [props.frameRect?.x, props.frameRect?.y, props.frameRect?.width, props.frameRect?.height,
    props.visibleBounds?.x, props.visibleBounds?.y, props.visibleBounds?.width, props.visibleBounds?.height,
    props.visible, props.fullscreen, props.resetKey, props.container], reclampActions, { flush: 'post' })

function onToolsPointerDown(event) {
    if (event.button != null && event.button > 0) return // left / touch / pen only
    suppressToolsClick = false
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) return
    const r = el.getBoundingClientRect()
    const pr = parent.getBoundingClientRect()
    actionsDrag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        baseLeft: r.left - pr.left,
        baseTop: r.top - pr.top,
        moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
}

function onToolsPointerMove(event) {
    if (!actionsDrag || event.pointerId !== actionsDrag.pointerId) return
    const dx = event.clientX - actionsDrag.startX
    const dy = event.clientY - actionsDrag.startY
    if (!actionsDrag.moved) {
        if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return // below the tap/drag threshold
        actionsDrag.moved = true
        // Kill pointer-events on pooled iframes for the drag (same reason as the
        // viewport handles: an iframe swallows moves the pointer crosses).
        emit('drag-start')
    }
    previewActionsPos.value = clampActionsPos(actionsDrag.baseLeft + dx, actionsDrag.baseTop + dy)
}

function onToolsPointerUp(event) {
    if (!actionsDrag || event.pointerId !== actionsDrag.pointerId) return
    if (actionsDrag.moved) {
        emit('drag-end')
        suppressToolsClick = true // swallow the click this drag would synthesize
        computePreviewActionsGeometry() // drop side + tooltip side may need to flip
    }
    actionsDrag = null
}

function onToolsClick() {
    if (suppressToolsClick) {
        suppressToolsClick = false
        return
    }
    if (!previewActionsExpanded.value) computePreviewActionsGeometry() // decide the drop side
    previewActionsExpanded.value = !previewActionsExpanded.value
}

onBeforeUnmount(() => {
    if (actionsDrag?.moved) emit('drag-end')
})
</script>

<template>
    <div v-show="visible" ref="previewActionsRef" class="preview-actions" :style="previewActionsStyle">
        <template v-if="previewActionsCollapsible">
            <span class="preview-tools-wrap">
                <wa-button
                    :id="previewToolsButtonId"
                    class="preview-action-btn preview-tools-btn floating-over-text"
                    :class="{ 'preview-action-btn--active': previewActionsExpanded }"
                    size="small" variant="neutral" appearance="filled"
                    :aria-label="previewActionsExpanded ? 'Hide tools' : 'Tools'"
                    :aria-expanded="previewActionsExpanded"
                    @click="onToolsClick"
                    @pointerdown="onToolsPointerDown"
                    @pointermove="onToolsPointerMove"
                    @pointerup="onToolsPointerUp"
                    @pointercancel="onToolsPointerUp"
                    @lostpointercapture="onToolsPointerUp"
                >
                    <wa-icon name="screwdriver-wrench"></wa-icon>
                </wa-button>
                <span v-if="!previewActionsExpanded && modeActive" class="preview-tools-dot"></span>
            </span>
            <AppTooltip :for="previewToolsButtonId" :placement="previewTooltipPlacement">
                {{ previewActionsExpanded ? 'Hide tools' : 'Tools' }}
            </AppTooltip>
        </template>
        <div v-if="!previewActionsCollapsible || previewActionsExpanded" class="preview-actions-list"
            :class="{ 'preview-actions-list--menu': previewActionsCollapsible, 'preview-actions-list--up': openUpward }">
            <template v-for="action in orderedButtons" :key="action.id">
                <wa-button
                    :id="action.id" class="preview-action-btn floating-over-text"
                    :class="{ 'preview-action-btn--active': action.active }"
                    size="small" variant="neutral" appearance="filled"
                    :aria-label="action.label" :aria-pressed="action.active"
                    :disabled="action.disabled"
                    :href="action.href" :target="action.href ? '_blank' : undefined"
                    :rel="action.href ? 'noopener' : undefined"
                    @click="action.action?.()"
                ><wa-icon :name="action.icon"></wa-icon></wa-button>
                <AppTooltip :for="action.id" :placement="previewTooltipPlacement">{{ action.label }}</AppTooltip>
            </template>
        </div>
    </div>
</template>

<style scoped>
/* Floating expand/compress toggle, pinned to the preview's top-right corner,
   above the preview content so it stays clickable over an iframe. Subtle at
   rest, solid on hover. */
.preview-actions {
    position: absolute;
    top: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 2;
    display: flex;
    gap: var(--wa-space-2xs);
    /* Re-enable inside the inert frame-overlay layer when teleported over the
       pooled HTML frame (the layer is pointer-events:none by contract; no
       generic `> *` re-enable there, to avoid a specificity tie). No-op when
       rendered in place over a non-pooled preview. */
    pointer-events: auto;
}

/* The actions themselves. Inline row for a single-action preview (no tools
   toggle). When collapsible, they drop as a vertical column anchored to the
   tools button's right edge — below by default, above when `--up` (the button
   never moves; only the column flips). */
.preview-actions-list {
    display: flex;
    gap: var(--wa-space-2xs);
}
.preview-actions-list--menu {
    position: absolute;
    right: 0;
    top: calc(100% + var(--wa-space-2xs));
    flex-direction: column;
    align-items: flex-end;
}
.preview-actions-list--menu.preview-actions-list--up {
    top: auto;
    bottom: calc(100% + var(--wa-space-2xs));
    flex-direction: column-reverse;
}

/* Every one of them also carries the shared .floating-over-text class
   (styles/transcript-tokens.css): they sit over a document being read, so their
   surface is a translucent tint instead of an opaque fill. */
.preview-action-btn {
    opacity: 0.6;
    transition: opacity 0.15s ease;
}
.preview-action-btn:hover,
:global(.file-pane-preview:hover) .preview-action-btn,
/* When teleported into the pooled HTML frame's overlay layer, the hover
   target is that layer, not .file-pane-preview. */
:global(.frame-overlay-layer:hover) .preview-action-btn {
    opacity: 1;
}

/* Mode toggles (responsive / select): fully opaque + brand icon while active. */
.preview-action-btn--active {
    opacity: 1;
}
.preview-action-btn--active wa-icon {
    color: var(--wa-color-brand-fill-loud);
}

/* The round "tools" toggle: a circle (square box, no inline padding — the
   flex part centers the icon). Also the drag handle for the whole floating
   row: grab cursor, and touch-action:none so a touch-drag moves it instead
   of scrolling the page. */
.preview-tools-btn {
    cursor: grab;
    touch-action: none;
}
.preview-tools-btn:active {
    cursor: grabbing;
}
.preview-tools-btn::part(base) {
    border-radius: 50%;
    aspect-ratio: 1;
    padding: 0;
}

/* Active-sub-mode indicator: a small brand dot on the folded tools button's
   top-right corner, with a 1s opacity pulse. The dot sits on the wrapper (not
   the button), so the button's own rest opacity doesn't dim it. */
.preview-tools-wrap {
    position: relative;
    display: inline-flex;
}

.preview-tools-dot {
    position: absolute;
    top: -1px;
    right: -1px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--wa-color-brand-fill-loud);
    pointer-events: none;
    animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

</style>
