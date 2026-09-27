<script setup>
/**
 * HoverInfoPanel — a single, generic floating info panel positioned by the
 * parent (which owns all the geometry: cursor-follow, edge-aware left/right
 * flip, viewport clamping). Because there is one persistent instance, moving
 * between targets updates the content in place with no hide/show flicker.
 *
 * Rendered in the browser TOP LAYER via the native Popover API (``popover`` +
 * ``showPopover()``), so it sits above wa-popover / wa-dialog, which also live
 * in the top layer and would otherwise win over any z-index. Teleported to
 * <body> so ``showPopover`` runs against a connected element, ``position: fixed``
 * so it escapes scroll/overflow clipping, and ``pointer-events: none`` so it
 * never steals hover from the elements beneath it. Its element is exposed so the
 * parent can measure the rendered width for the flip/clamp maths. Colours follow
 * the normal surface palette (a popover-like card), NOT the tooltip palette.
 */
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'

const props = defineProps({
    visible: { type: Boolean, default: false },
    left: { type: Number, default: 0 },
    top: { type: Number, default: 0 },
})

const rootEl = ref(null)
defineExpose({ rootEl })

function isOpen(el) {
    try { return el.matches(':popover-open') } catch { return false }
}

// Drive the top-layer state off ``visible``. showPopover/hidePopover throw when
// already in the requested state (or unsupported) — guard + swallow.
function sync() {
    const el = rootEl.value
    if (!el || typeof el.showPopover !== 'function') return
    const open = isOpen(el)
    if (props.visible && !open) {
        try { el.showPopover() } catch { /* already open / detached */ }
    } else if (!props.visible && open) {
        try { el.hidePopover() } catch { /* already closed */ }
    }
}

watch(() => props.visible, () => nextTick(sync))

onBeforeUnmount(() => {
    const el = rootEl.value
    if (el && isOpen(el)) { try { el.hidePopover() } catch { /* noop */ } }
})
</script>

<template>
    <Teleport to="body">
        <div
            ref="rootEl"
            popover="manual"
            class="hover-info-panel glass-surface"
            :style="{ left: `${left}px`, top: `${top}px` }"
        >
            <slot />
        </div>
    </Teleport>
</template>

<style scoped>
.hover-info-panel {
    /* A [popover] renders in the top layer (above popovers/dialogs regardless of
       z-index). Reset the UA centering (inset/margin) so we can place it freely,
       and keep it non-interactive. */
    position: fixed;
    inset: auto;
    margin: 0;
    max-width: 20rem;
    padding: var(--wa-space-s) var(--wa-space-m);
    border-radius: var(--wa-border-radius-m);
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-s);
    pointer-events: none;
    overflow: visible;
}
</style>
