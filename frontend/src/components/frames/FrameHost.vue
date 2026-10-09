<script setup>
// Host side of the persistent-frame system (see stores/framePool.js and
// PersistentFrame.vue). Mounted ONCE in ProjectView's .main-content, after the
// KeepAlive branches. Renders every registered iframe absolutely positioned
// over its placeholder rect. The v-for relies on the store's append-only key
// order — iframe nodes must never move in the DOM (that reloads them).
import { computed, ref, watch } from 'vue'
import { useElementBounding } from '@vueuse/core'
import { useFramePoolStore } from '../../stores/framePool'
import { frameClipPath, frameFlushCorners, visibleRect } from '../../utils/panelInsets'

// NOTE: pool.hostMounted is owned by ProjectView (set in its setup, cleared in
// its onUnmounted), NOT here — panes can mount before this component does on
// cold-load deep links, and the flag must already be true for them.
const props = defineProps({
    // Standalone public viewers select tiers below their subagent drawers.
    // Private pane frames keep their existing default tiers.
    zTiers: { type: Object, default: () => ({}) },
})
const pool = useFramePoolStore()
const hostEl = ref(null)
const hostRect = useElementBounding(hostEl)

const ids = computed(() => Object.keys(pool.frames))

const Z_TIERS = {
    base: 2,        // above pane content, below the docking overlay backdrop (8)
    overlay: 11,    // docking overlay panel level (host renders later in DOM → wins the tie)
    fullscreen: 1001, // above the fullscreen preview wrapper (1000)
}

function cellStyle(frame) {
    const { x, y, width, height } = frame.rect
    const tiers = { ...Z_TIERS, ...props.zTiers }
    const zIndex = tiers[frame.zTier] ?? tiers.base
    const style =
        frame.zTier === 'fullscreen'
            ? {
                  // The fullscreen wrapper is position:fixed while .main-content drops
                  // its container-type (main-content--preview-expanded), so viewport
                  // coordinates are correct AND escape .main-content's overflow clip.
                  position: 'fixed',
                  left: `${x}px`,
                  top: `${y}px`,
                  width: `${width}px`,
                  height: `${height}px`,
                  zIndex,
              }
            : {
                  position: 'absolute',
                  left: `${x - hostRect.x.value}px`,
                  top: `${y - hostRect.y.value}px`,
                  width: `${width}px`,
                  height: `${height}px`,
                  zIndex,
              }
    // Clip away the parts that scrolled out of the owner's clip container
    // (Browser pane responsive mode): the frame paints above pane content,
    // so an unclipped overhang would cover the pane's own chrome. Both rects
    // are viewport-based, so the deltas hold for either positioning branch —
    // clip-path resolves against the element's own border box.
    // Plus rounded corners where the visible frame sits in its card's corners
    // (floating panels): the card's radius does not clip pooled frames, which
    // are not its DOM descendants. Never for fullscreen frames.
    const corners = frame.zTier !== 'fullscreen' && frame.cardRect
        ? frameFlushCorners(visibleRect(frame.rect, frame.clipRect), frame.cardRect)
        : null
    const clipPath = frameClipPath(frame.rect, frame.clipRect, corners)
    if (clipPath) style.clipPath = clipPath
    return style
}

watch(() => pool.geometryEpoch, () => hostRect.update(), { flush: 'post' })
</script>

<template>
    <div ref="hostEl" class="frame-host" :class="{ 'frame-host--dragging': pool.isDividerDragging }">
        <div
            v-for="id in ids"
            :key="id"
            class="frame-cell"
            :class="{ 'frame-cell--hidden': !pool.frames[id].visible, 'frame-cell--overlay': pool.frames[id].zTier === 'overlay' }"
            :style="cellStyle(pool.frames[id])"
        >
            <iframe
                :key="pool.frames[id].remountKey"
                :src="pool.frames[id].src"
                v-bind="pool.frames[id].attrs"
                class="frame-iframe"
                :ref="(el) => pool.setFrameEl(id, el)"
                @load="pool.frames[id].onLoad && pool.frames[id].onLoad($event)"
            ></iframe>
            <div class="frame-overlay-layer" :ref="(el) => pool.setOverlayEl(id, el)"></div>
        </div>
    </div>
</template>

<style scoped>
/* The host itself is inert glass over .main-content; only cells take events. */
.frame-host {
    position: absolute;
    inset: 0;
    pointer-events: none;
    /* A cell is sized to its placeholder and trimmed only by clip-path; in a Browser pane's
       responsive mode it can extend far past the viewport. .main-content now clips with a
       margin, so the host clips at .main-content's padding box itself. Fixed (fullscreen)
       cells escape: this is not their containing block. */
    overflow: clip;
}

.frame-cell {
    pointer-events: auto;
}

/* visibility (not display): the page keeps rendering/running while hidden, so
   a cached session's dev server stays exactly where the user left it. */
.frame-cell--hidden {
    visibility: hidden;
    pointer-events: none;
}

.frame-iframe {
    width: 100%;
    height: 100%;
    border: none;
    /* Most pages assume a light default background (same rationale as the old
       in-pane .browser-frame / .html-preview rules). */
    background: #fff;
}

/* Inline artifacts blend into the chat. A page that does not declare the dark
   scheme still gets an opaque white canvas from the browser in dark mode. In
   full screen the frame covers the conversation, so it falls back to the
   app surface when the page keeps a transparent background. */
.frame-iframe[data-twicc-display="inline"] {
    background: transparent;
}

.frame-iframe[data-twicc-display="fullscreen"] {
    background: var(--wa-color-surface-default);
}

/* Owner chrome teleported over the iframe (preview actions, route callout).
   NO generic `> *` re-enable rule here: it would tie (0-2-0 specificity, bundle
   source order decides) with owner rules like FilesPanel's
   `.pane-callout-overlay { pointer-events: none }` and could turn a full-size
   teleported wrapper into a click shield over the iframe. Each teleported
   piece opts back in itself (owner-scoped rules reach teleported nodes). */
.frame-overlay-layer {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

/* While a divider drag is in progress the iframes must not swallow pointer
   events (an iframe is a separate browsing context) — same rationale as
   SessionLayout's `.resizing :deep(iframe)` rule, which no longer reaches
   pooled frames. */
.frame-host--dragging .frame-iframe {
    pointer-events: none;
}
</style>
