<script setup>
/**
 * VirtualScrollerItem - Wrapper for items in the VirtualScroller.
 *
 * This component wraps each rendered item and registers with the shared
 * ResizeObserver (provided by the parent VirtualScroller) for size tracking.
 *
 * The shared ResizeObserver pattern is critical for performance - using a single
 * observer for all items is dramatically more efficient than one per item.
 * See: https://github.com/WICG/resize-observer/issues/59
 *
 * Size changes are handled directly by the parent VirtualScroller's ResizeObserver
 * callback, which batches all updates for anchor-based scroll preservation.
 *
 * IMPORTANT: The parent container MUST be a VirtualScroller that provides the
 * shared observer via Vue's provide/inject mechanism.
 *
 * NOTE: This component is client-only (SSR is not supported due to
 * ResizeObserver and DOM measurement requirements).
 */
import { computed, ref, nextTick, onMounted, onUnmounted, inject, provide } from 'vue'
import { RESIZE_OBSERVER_KEY, ROW_VISIBILITY_OBSERVER_KEY } from './virtualScrollerKeys.js'

import { STREAMING_ROW_CONTEXT } from '../../composables/streamPublicationKeys.js'

// ═══════════════════════════════════════════════════════════════════════════
// Props
// ═══════════════════════════════════════════════════════════════════════════

const props = defineProps({
    /**
     * Unique key identifying this item.
     * Used for height cache lookups in the parent scroller and as a
     * data-attribute for debugging purposes.
     */
    itemKey: {
        type: [String, Number],
        required: true
    },
    /**
     * Optional CSS min-height (px) applied to the wrapper. Bridges item swaps
     * whose new content renders asynchronously (see VirtualScroller's
     * itemMinHeight prop).
     */
    /** Cached or estimated geometry while a child has not published its first render. */
    estimatedHeight: { type: Number, default: 50 },
    minHeight: {
        type: Number,
        default: null
    }
})

// ═══════════════════════════════════════════════════════════════════════════
// Refs
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reference to the item wrapper element.
 */
const resizeObserverContext = inject(RESIZE_OBSERVER_KEY, null)

const itemRef = ref(null)
const intersection = ref('unknown')
const visibilityContext = inject(ROW_VISIBILITY_OBSERVER_KEY, null)
let releaseVisibility = null
const pendingInitialRenders = ref(0)
// Child setup can read layout before its readiness registration rerenders this wrapper.
const initialMount = ref(true)
function reserveInitialHeight() {
    pendingInitialRenders.value++
    let released = false
    return () => {
        if (released) return
        released = true
        pendingInitialRenders.value--
    }
}
const heightFloor = computed(() => initialMount.value || pendingInitialRenders.value > 0
    ? Math.max(resizeObserverContext?.getItemHeight?.(props.itemKey) ?? props.estimatedHeight, props.minHeight ?? 0)
    : props.minHeight)
provide(STREAMING_ROW_CONTEXT, {
    intersection,
    scrollerActive: visibilityContext?.scrollerActive || ref(false),
    reserveInitialHeight,
})

// ═══════════════════════════════════════════════════════════════════════════
// Lifecycle
// ═══════════════════════════════════════════════════════════════════════════

onMounted(() => {
    nextTick(() => { initialMount.value = false })
    if (!itemRef.value) return
    releaseVisibility = visibilityContext?.observe(itemRef.value, state => { intersection.value = state })
    if (!resizeObserverContext) {
        console.warn(
            '[VirtualScrollerItem] No shared ResizeObserver context found. ' +
            'This component must be used inside a VirtualScroller.'
        )
        return
    }

    // Register with the shared observer, passing our key
    // The parent VirtualScroller handles all resize callbacks and batches updates
    resizeObserverContext.register(itemRef.value, props.itemKey)
})

onUnmounted(() => {
    releaseVisibility?.()
    if (itemRef.value && resizeObserverContext) {
        resizeObserverContext.unregister(itemRef.value)
    }
})
</script>

<template>
    <div
        ref="itemRef"
        class="virtual-scroller-item"
        :data-item-key="props.itemKey"
        :style="heightFloor != null ? { minHeight: heightFloor + 'px' } : null"
    >
        <slot />
    </div>
</template>

<style scoped>
.virtual-scroller-item {
    /* Allow content to define its own height.
       Using flow-root creates a Block Formatting Context so child margins
       don't collapse through, ensuring accurate height measurement. */
    display: flow-root;

    /* Prevent item from shrinking in parent's flex layout. */
    flex-shrink: 0;
}
</style>
