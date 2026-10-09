<script setup>
import { ref, computed, watch, useId, onBeforeUnmount } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import AppTooltip from '../ui/AppTooltip.vue'

// The caller owns the frame pool. Drag events suspend iframe pointer handling.
const props = defineProps({
    actions: { type: Array, default: () => [] },
    fullscreen: { type: Boolean, default: false },
    fullscreenDisabled: { type: Boolean, default: false },
    modeActive: { type: Boolean, default: false },
    resetKey: { type: String, default: '' },
    container: { type: Object, default: null },
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
const previewActionsCollapsible = computed(() => previewActionCount.value > 1)

// Fold back AND recenter on file switch — both are transient per-page state.
watch(() => props.resetKey, () => {
    previewActionsExpanded.value = false
    previewActionsPos.value = null
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
const previewActionsStyle = computed(() =>
    previewActionsPos.value
        ? { top: `${previewActionsPos.value.top}px`, left: `${previewActionsPos.value.left}px`, right: 'auto' }
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
    const top = previewActionsPos.value?.top ?? r.top - pr.top
    const left = previewActionsPos.value?.left ?? r.left - pr.left
    const spaceBelow = pr.height - top - r.height
    const spaceAbove = top
    openUpward.value = spaceBelow < needed && spaceAbove > spaceBelow
    // Horizontal: a left tooltip needs ~140px of room on the left of the group.
    previewTooltipPlacement.value = left < 140 ? 'right' : 'left'
}
// A real drag must not fire the tools button's fold/unfold click. Reset on
// each pointerdown so a drag that ends off-target (no click) can't wedge it.
let actionsDrag = null // { pointerId, startX, startY, baseLeft, baseTop, moved }
let suppressToolsClick = false

function clampActionsPos(left, top) {
    const el = previewActionsRef.value
    const parent = el?.offsetParent || el?.parentElement
    if (!el || !parent) return { left, top }
    const pr = parent.getBoundingClientRect()
    return {
        left: Math.max(0, Math.min(pr.width - el.offsetWidth, left)),
        top: Math.max(0, Math.min(pr.height - el.offsetHeight, top)),
    }
}

// A dragged position is absolute px in the offset parent, so any shrink of
// that parent can leave the group outside the visible area — unreachable, with
// no way to bring it back. Exiting full screen after dragging it to the bottom
// is the obvious case; a dock/window resize and a sub-toolbar appearing over
// the frame do it too. Re-clamp on every parent resize (one-way: shrinking then
// re-expanding does not restore the pre-clamp spot).
useResizeObserver(
    () => props.container ?? previewActionsRef.value?.parentElement,
    () => {
        if (!previewActionsPos.value) return // still on its default CSS corner
        const el = previewActionsRef.value
        const parent = el?.offsetParent || el?.parentElement
        // A 0-sized parent is transient (frame hidden, KeepAlive detach) —
        // clamping against it would slam the group into the top-left corner.
        if (!parent || parent.clientWidth < 1 || parent.clientHeight < 1) return
        previewActionsPos.value = clampActionsPos(previewActionsPos.value.left, previewActionsPos.value.top)
        computePreviewActionsGeometry() // drop side + tooltip side may need to flip
    }
)

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
    <div ref="previewActionsRef" class="preview-actions" :style="previewActionsStyle">
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
            <template v-for="action in actions" :key="action.id">
                <wa-button
                    :id="action.id" class="preview-action-btn floating-over-text"
                    :class="{ 'preview-action-btn--active': action.active }"
                    size="small" variant="neutral" appearance="filled"
                    :aria-label="action.label" :aria-pressed="action.active"
                    :href="action.href" :target="action.href ? '_blank' : undefined"
                    :rel="action.href ? 'noopener' : undefined"
                    @click="action.action?.()"
                ><wa-icon :name="action.icon"></wa-icon></wa-button>
                <AppTooltip :for="action.id" :placement="previewTooltipPlacement">{{ action.label }}</AppTooltip>
            </template>
            <wa-button
                :id="previewFullscreenButtonId" class="preview-action-btn floating-over-text"
                size="small" variant="neutral" appearance="filled"
                :aria-label="fullscreen ? 'Exit full screen' : 'Full screen'"
                :disabled="fullscreenDisabled && !fullscreen"
                @click="emit('toggle-fullscreen')"
            ><wa-icon :name="fullscreen ? 'compress' : 'expand'"></wa-icon></wa-button>
            <AppTooltip :for="previewFullscreenButtonId" :placement="previewTooltipPlacement">
                {{ fullscreen ? 'Exit full screen' : 'Full screen' }}
            </AppTooltip>
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
