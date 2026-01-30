<script setup>
/**
 * VirtualScrollerItem - Wrapper for items in the VirtualScroller.
 *
 * This component wraps each rendered item and uses a shared ResizeObserver
 * (provided by the parent VirtualScroller) to detect size changes, emitting
 * @resized when the item's height changes.
 *
 * The shared ResizeObserver pattern is critical for performance - using a single
 * observer for all items is dramatically more efficient than one per item.
 * See: https://github.com/WICG/resize-observer/issues/59
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
// Events
// ═══════════════════════════════════════════════════════════════════════════

const emit = defineEmits([
    /**
     * Emitted when the item's height changes.
     * Payload: (newHeight: number, oldHeight: number | undefined)
     */
    'resized'
])

// ═══════════════════════════════════════════════════════════════════════════
// Refs and State
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reference to the item wrapper element.
 */
const itemRef = ref(null)

/**
 * Current measured height of the item.
 * Initialized as undefined per spec (no measurement yet).
 */
let currentHeight = undefined

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

    resizeObserverContext.register(itemRef.value, (entry) => {
        // Use borderBoxSize for accurate height including padding/border
        // Fallback to contentRect.height if borderBoxSize not available
        const newHeight = entry.borderBoxSize
            ? entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height
            : entry.contentRect.height

        // Ignore 0 height measurements - these occur when the item is in a hidden
        // parent (e.g., inactive wa-tab-panel with display: none). Caching 0 heights
        // corrupts the height cache and breaks rendering when the item becomes visible.
        // We skip the emit entirely so the parent doesn't cache invalid values.
        if (newHeight === 0) {
            return
        }

        if (newHeight !== currentHeight) {
            const oldHeight = currentHeight
            currentHeight = newHeight
            emit('resized', newHeight, oldHeight)
        }
    })
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
