<script setup>
// One-line text (a title, a name). With a mouse it is cut with a native ellipsis (the caller adds a tooltip).
// On a touch screen it scrolls under the finger, with an ellipsis on each side that hides
// some text, and goes back to its start shortly after the finger is up (utils/titleScroll.js).
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createTitleScrollReturn, hiddenEdges } from '../../utils/titleScroll'

const props = defineProps({
    id: { type: String, default: undefined },
    text: { type: String, required: true },
    // The element holding the text: a heading in a header, a span in a list row.
    tag: { type: String, default: 'span' },
})

const scroller = ref(null)
const edges = reactive({ start: false, end: false })
let scrollReturn = null
let resizeObserver = null

function updateEdges() {
    const el = scroller.value
    if (!el) return
    const next = hiddenEdges(el)
    edges.start = next.start
    edges.end = next.end
}

function onScroll() {
    updateEdges()
    scrollReturn?.onScroll()
}

onMounted(() => {
    scrollReturn = createTitleScrollReturn(scroller.value)
    resizeObserver = new ResizeObserver(updateEdges)
    resizeObserver.observe(scroller.value)
    updateEdges()
})

onBeforeUnmount(() => {
    resizeObserver?.disconnect()
    scrollReturn?.destroy()
})

// A new text starts at its beginning. Its width does not change the box, so no resize event.
watch(() => props.text, async () => {
    scrollReturn?.reset()
    await nextTick()
    updateEdges()
})
</script>

<template>
    <div class="scrollable-text-root" :class="{ 'has-start': edges.start, 'has-end': edges.end }">
        <component
            :is="tag"
            class="scrollable-text"
            :id="id"
            ref="scroller"
            @scroll.passive="onScroll"
            @touchstart.passive="scrollReturn?.onTouchStart()"
            @touchend.passive="scrollReturn?.onTouchEnd()"
            @touchcancel.passive="scrollReturn?.onTouchEnd()"
        >{{ text }}</component>
        <span class="edge-ellipsis edge-ellipsis--start" aria-hidden="true">…</span>
        <span class="edge-ellipsis edge-ellipsis--end" aria-hidden="true">…</span>
    </div>
</template>

<style scoped>
.scrollable-text-root {
    position: relative;
    min-width: 0;
    /* The typography, the margins and the flex sizing come from the caller (class on this root). */
}

.scrollable-text {
    /* A span must be a block for its overflow to apply. */
    display: block;
    margin: 0;
    font: inherit;
    letter-spacing: inherit;
    color: inherit;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.edge-ellipsis {
    display: none;
}

@media (pointer: coarse) {
    .scrollable-text-root {
        /* The width of an ellipsis, and the fade that hides the text under it. */
        --ellipsis-width: 0.9em;
        --ellipsis-fade: 0.5em;
        --start-clear: 0px;
        --start-full: 0px;
        --end-clear: 0px;
        --end-full: 0px;
    }
    .scrollable-text-root.has-start {
        --start-clear: var(--ellipsis-width);
        --start-full: calc(var(--ellipsis-width) + var(--ellipsis-fade));
    }
    .scrollable-text-root.has-end {
        --end-clear: var(--ellipsis-width);
        --end-full: calc(var(--ellipsis-width) + var(--ellipsis-fade));
    }

    .scrollable-text {
        overflow-x: auto;
        /* The ellipses are drawn by the component, on the side that hides text. */
        text-overflow: clip;
        scrollbar-width: none;
        overscroll-behavior-x: contain;
        -webkit-mask-image: linear-gradient(to right,
            transparent var(--start-clear), #000 var(--start-full),
            #000 calc(100% - var(--end-full)), transparent calc(100% - var(--end-clear)));
        mask-image: linear-gradient(to right,
            transparent var(--start-clear), #000 var(--start-full),
            #000 calc(100% - var(--end-full)), transparent calc(100% - var(--end-clear)));
    }
    .scrollable-text::-webkit-scrollbar {
        display: none;
    }

    .edge-ellipsis {
        position: absolute;
        inset-block: 0;
        display: flex;
        align-items: center;
        pointer-events: none;
        opacity: 0;
    }
    .edge-ellipsis--start {
        inset-inline-start: 0;
    }
    .edge-ellipsis--end {
        inset-inline-end: 0;
    }
    .has-start > .edge-ellipsis--start,
    .has-end > .edge-ellipsis--end {
        opacity: 1;
    }
}
</style>
