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
import { ref, onMounted, onUnmounted, inject } from 'vue'
import { RESIZE_OBSERVER_KEY } from './virtualScrollerKeys.js'

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
    }
})

// ═══════════════════════════════════════════════════════════════════════════
// Refs
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reference to the item wrapper element.
 */
const itemRef = ref(null)

// ═══════════════════════════════════════════════════════════════════════════
// Shared ResizeObserver (Injected from Parent)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Shared ResizeObserver context from VirtualScroller parent.
 * Provides register/unregister functions for observing elements.
 *
 * NOTE: Will be null if this component is used outside of a VirtualScroller.
 */
const resizeObserverContext = inject(RESIZE_OBSERVER_KEY, null)

// ═══════════════════════════════════════════════════════════════════════════
// Lifecycle
// ═══════════════════════════════════════════════════════════════════════════

onMounted(() => {
    if (!itemRef.value) return
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
