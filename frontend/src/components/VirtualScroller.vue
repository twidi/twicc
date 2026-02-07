<script setup>
/**
 * VirtualScroller - A custom virtual scroller for variable-height items.
 *
 * Renders only the visible items plus a configurable buffer, using spacer
 * elements to maintain scroll position and scrollbar appearance.
 *
 * Features:
 * - Variable item heights (measured via ResizeObserver on VirtualScrollerItem)
 * - Asymmetric buffers (load/unload hysteresis to prevent thrashing)
 * - Scroll position correction when item heights change above viewport
 * - Navigation methods (scrollToIndex, scrollToTop, scrollToBottom)
 *
 * This component is agnostic to the data it displays - it receives items
 * and a key extractor function, and renders item content via a scoped slot.
 *
 * IMPORTANT: The parent container MUST provide an explicit height for this
 * component to function correctly. The scroller uses `height: 100%` which
 * requires the parent to have a defined height.
 *
 * NOTE: This component is client-only (SSR is not supported due to
 * ResizeObserver and DOM measurement requirements).
 */
import { ref, computed, watch, onMounted, onUnmounted, onActivated, onDeactivated, toRef, nextTick, provide } from 'vue'
import { useVirtualScroll } from '../composables/useVirtualScroll'
import VirtualScrollerItem from './VirtualScrollerItem.vue'
import { RESIZE_OBSERVER_KEY } from './virtualScrollerKeys.js'

// ═══════════════════════════════════════════════════════════════════════════
// Props
// ═══════════════════════════════════════════════════════════════════════════

const props = defineProps({
    /**
     * Array of items to display in the virtual scroller.
     * Can be null/undefined momentarily during data loading.
     */
    items: {
        type: Array,
        required: true
    },
    /**
     * Function to extract a unique key from each item.
     * Signature: (item) => uniqueKey
     *
     * NOTE: Captured at mount time. Changes after mount are not observed.
     */
    itemKey: {
        type: Function,
        required: true
    },
    /**
     * Minimum/estimated height for items that haven't been measured yet.
     * Should match the smallest expected item height.
     *
     * NOTE: Captured at mount time. Changes after mount are not observed.
     */
    minItemHeight: {
        type: Number,
        default: 24
    },
    /**
     * Buffer in pixels for preloading items before they enter the viewport.
     * Items within this distance will be rendered.
     *
     * NOTE: Captured at mount time. Changes after mount are not observed.
     */
    buffer: {
        type: Number,
        default: 500
    },
    /**
     * Buffer in pixels before unloading items after they leave the viewport.
     * This is larger than buffer to prevent thrashing (constant load/unload cycles).
     *
     * NOTE: Captured at mount time. Changes after mount are not observed.
     */
    unloadBuffer: {
        type: Number,
        default: 1000
    }
})

// ═══════════════════════════════════════════════════════════════════════════
// Events
// ═══════════════════════════════════════════════════════════════════════════

const emit = defineEmits([
    /**
     * Emitted when the visible/rendered range changes.
     * Payload: { startIndex, endIndex, visibleStartIndex, visibleEndIndex }
     *
     * startIndex/endIndex: indices of items being rendered (with buffer)
     * visibleStartIndex/visibleEndIndex: indices of items actually visible (no buffer)
     */
    'update',
    /**
     * Emitted when an item changes size (after initial render or expand/collapse).
     * Payload: { key, height, oldHeight }
     */
    'item-resized',
    /**
     * Emitted on scroll events (passive).
     * Payload: native scroll Event
     */
    'scroll'
])

// ═══════════════════════════════════════════════════════════════════════════
// Refs
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reference to the scroll container element.
 */
const containerRef = ref(null)

/**
 * Flag to track whether the initial @update event has been emitted.
 * Used to defer the first emission until after mount.
 */
let hasMounted = false

/**
 * Track the last known viewport height to detect visibility changes.
 * When the container goes from 0 height (hidden) to positive (visible),
 * we need to trigger re-measurement of items.
 */
let lastKnownViewportHeight = 0

// ═══════════════════════════════════════════════════════════════════════════
// Shared ResizeObserver for Items (Provide/Inject Pattern)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Map of observed elements to their item keys.
 * Using a single ResizeObserver is dramatically more performant than creating
 * one per item (25% of animation time vs excellent performance).
 * See: https://github.com/WICG/resize-observer/issues/59
 */
const itemKeys = new Map()

/**
 * Single ResizeObserver instance shared by all VirtualScrollerItem children.
 * Created lazily when items register (only on client-side).
 *
 * This observer batches all size changes and applies them together using
 * anchor-based scrolling to prevent visual jumps.
 */
let itemObserver = null

/**
 * Creates the shared ResizeObserver lazily with batched processing.
 * This ensures we don't create the observer during SSR.
 *
 * The observer collects all size changes from a single frame and applies
 * them together via batchUpdateItemHeights for smooth anchor-based scrolling.
 */
function getItemObserver() {
    if (itemObserver) return itemObserver
    if (typeof ResizeObserver === 'undefined') return null

    itemObserver = new ResizeObserver((entries) => {
        // Collect all height updates from this batch
        const updates = new Map()

        for (const entry of entries) {
            const key = itemKeys.get(entry.target)
            if (key === undefined) continue

            // Use borderBoxSize for accurate height including padding/border
            const newHeight = entry.borderBoxSize
                ? entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height
                : entry.contentRect.height

            // Collect all updates (filtering of 0 heights is done in batchUpdateItemHeights)
            updates.set(key, newHeight)
        }

        if (updates.size > 0) {
            // Apply all updates at once with anchor-based scroll preservation
            const changes = batchUpdateItemHeights(updates)

            // Emit events for each actual change
            for (const change of changes) {
                if (change) {
                    emit('item-resized', {
                        key: change.key,
                        height: change.height,
                        oldHeight: change.oldHeight,
                    })
                }
            }
        }
    })

    return itemObserver
}

/**
 * Register an element to be observed for size changes.
 *
 * @param {HTMLElement} element - The element to observe
 * @param {*} key - The item key for this element
 */
function registerItemObserver(element, key) {
    const observer = getItemObserver()
    if (!observer) return

    itemKeys.set(element, key)
    observer.observe(element, { box: 'border-box' })
}

/**
 * Unregister an element from observation.
 *
 * @param {HTMLElement} element - The element to stop observing
 */
function unregisterItemObserver(element) {
    if (!itemObserver) return

    itemKeys.delete(element)
    itemObserver.unobserve(element)
}

// Provide the observer registration functions to child items
provide(RESIZE_OBSERVER_KEY, {
    register: registerItemObserver,
    unregister: unregisterItemObserver
})

// ═══════════════════════════════════════════════════════════════════════════
// Virtual Scroll Composable
// ═══════════════════════════════════════════════════════════════════════════

// NOTE: itemKey, minItemHeight, buffer, and unloadBuffer are captured at
// mount time by the composable. They are passed as plain values (not refs)
// because the composable design reads them once during initialization.
// Making them reactive would require significant refactoring of the composable.

const {
    totalHeight,
    renderRange,
    visibleRange,
    spacerBeforeHeight,
    spacerAfterHeight,
    batchUpdateItemHeights,
    handleScroll,
    scrollToIndex: composableScrollToIndex,
    scrollToTop: composableScrollToTop,
    scrollToBottom: composableScrollToBottom,
    getScrollState: composableGetScrollState,
    updateViewportHeight,
    syncScrollPosition,
    isAtBottom,
    isAtTop,
    invalidateZeroHeights,
    enableStickToBottom: composableEnableStickToBottom,
    disableStickToBottom: composableDisableStickToBottom,
    suspend: composableSuspend,
    resume: composableResume,
    getScrollAnchor: composableGetScrollAnchor,
    scrollToAnchor: composableScrollToAnchor,
} = useVirtualScroll({
    items: toRef(props, 'items'),
    itemKey: props.itemKey,
    minItemHeight: props.minItemHeight,
    buffer: props.buffer,
    unloadBuffer: props.unloadBuffer,
    containerRef,
})

// ═══════════════════════════════════════════════════════════════════════════
// Computed: Rendered Items
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Array of items to render with their metadata.
 * Each entry contains: { item, index, key }
 *
 * Includes defensive null check for items that may be momentarily null/undefined.
 */
const renderedItems = computed(() => {
    if (!props.items?.length) return []
    const { start, end } = renderRange.value
    return props.items.slice(start, end).map((item, i) => ({
        item,
        index: start + i,
        key: props.itemKey(item),
    }))
})

// ═══════════════════════════════════════════════════════════════════════════
// Event Emission
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Watch renderRange and visibleRange to emit @update event.
 * We watch both to ensure we capture all changes.
 *
 * The initial emission is deferred until after mount using nextTick to ensure
 * the container has been measured and ranges are accurate. Before mount,
 * viewportHeight is 0 which produces incorrect ranges.
 */
watch(
    [renderRange, visibleRange],
    ([render, visible]) => {
        // Skip emission before mount - ranges are incorrect without viewport measurements
        if (!hasMounted) return

        emit('update', {
            startIndex: render.start,
            endIndex: render.end,
            visibleStartIndex: visible.start,
            visibleEndIndex: visible.end,
        })
    },
    { immediate: true }
)

// NOTE: Item resize handling is now done directly in the ResizeObserver callback
// (getItemObserver function) which batches all updates and emits events.
// The VirtualScrollerItem no longer emits @resized events.

// ═══════════════════════════════════════════════════════════════════════════
// ResizeObserver for Container
// ═══════════════════════════════════════════════════════════════════════════

/**
 * ResizeObserver to track container viewport height changes.
 *
 * NOTE: This is a plain `let` variable (not a ref) because it's only used
 * client-side for cleanup in onUnmounted. SSR is not supported for this
 * component due to DOM measurement requirements.
 */
let containerObserver = null

onMounted(() => {
    if (containerRef.value) {
        // Initial sync of scroll position and viewport height
        syncScrollPosition()

        // Setup ResizeObserver for viewport height changes
        // We observe a single element, so we use entries[0] directly
        if (typeof ResizeObserver !== 'undefined') {
            containerObserver = new ResizeObserver((entries) => {
                const entry = entries[0]
                if (!entry) return
                // Use contentBoxSize if available, fallback to contentRect
                const height = entry.contentBoxSize
                    ? entry.contentBoxSize[0]?.blockSize ?? entry.contentRect.height
                    : entry.contentRect.height

                // Detect visibility recovery: container was hidden (0 height) and is now visible.
                // This happens when switching tabs with wa-tab-panel (display: none -> flex).
                // When this occurs, items may have been measured at 0px while hidden,
                // so we need to invalidate those measurements and trigger re-measurement.
                const wasHidden = lastKnownViewportHeight === 0
                const isNowVisible = height > 0
                if (wasHidden && isNowVisible && hasMounted) {
                    // Invalidate any cached 0-height measurements
                    invalidateZeroHeights()
                }
                lastKnownViewportHeight = height

                updateViewportHeight(height)
            })
            containerObserver.observe(containerRef.value)
        }

        // Mark as mounted and emit initial @update event on next tick
        // This ensures viewportHeight has been measured correctly
        nextTick(() => {
            hasMounted = true
            // Manually trigger the initial @update emission now that we're ready
            emit('update', {
                startIndex: renderRange.value.start,
                endIndex: renderRange.value.end,
                visibleStartIndex: visibleRange.value.start,
                visibleEndIndex: visibleRange.value.end,
            })
        })
    }
})

onUnmounted(() => {
    if (containerObserver) {
        containerObserver.disconnect()
        containerObserver = null
    }

    // Cleanup shared item observer
    if (itemObserver) {
        itemObserver.disconnect()
        itemObserver = null
    }
    itemKeys.clear()
})

// ═══════════════════════════════════════════════════════════════════════════
// KeepAlive Lifecycle
// ═══════════════════════════════════════════════════════════════════════════

/**
 * When KeepAlive deactivates this component, the DOM is about to be detached.
 * We must save the scroll anchor NOW, while scrollTop is still valid.
 * After detachment, the browser resets scrollTop to 0.
 */
onDeactivated(() => {
    composableSuspend()
})

/**
 * When KeepAlive reactivates this component, the DOM has been reattached.
 * Restore the scroll position from the saved anchor.
 */
onActivated(() => {
    composableResume()
})

// ═══════════════════════════════════════════════════════════════════════════
// Exposed Methods
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Scroll to a specific item index.
 *
 * @param {number} index - The item index to scroll to
 * @param {Object} [options] - Scroll options
 * @param {'start' | 'center' | 'end'} [options.align='start'] - Where to position the item
 * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
 */
function scrollToIndex(index, options = {}) {
    composableScrollToIndex(index, options)
}

/**
 * Scroll to the top of the list.
 *
 * @param {Object} [options] - Scroll options
 * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
 */
function scrollToTop(options = {}) {
    composableScrollToTop(options)
}

/**
 * Scroll to the bottom of the list.
 *
 * @param {Object} [options] - Scroll options
 * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
 */
function scrollToBottom(options = {}) {
    composableScrollToBottom(options)
}

/**
 * Get the current scroll state of the container.
 *
 * @returns {{ scrollTop: number, scrollHeight: number, clientHeight: number }}
 */
function getScrollState() {
    return composableGetScrollState()
}

/**
 * Get the visible range of items (indices actually visible in viewport, without buffer).
 * @returns {{ start: number, end: number }}
 */
function getVisibleRange() {
    return visibleRange.value
}

/**
 * Handle scroll events: update internal state and emit to parent.
 * @param {Event} event - Native scroll event
 */
function onScroll(event) {
    handleScroll(event)
    emit('scroll', event)
}

/**
 * Enable "stick to bottom" mode.
 * While enabled, any height changes will automatically scroll to bottom.
 */
function enableStickToBottom() {
    composableEnableStickToBottom()
}

/**
 * Disable "stick to bottom" mode.
 */
function disableStickToBottom() {
    composableDisableStickToBottom()
}

/**
 * Suspend the scroller (save scroll anchor before KeepAlive detachment).
 * Called automatically by onDeactivated, but can also be called manually.
 */
function suspend() {
    composableSuspend()
}

/**
 * Resume the scroller (restore scroll position after KeepAlive reattachment).
 * Called automatically by onActivated, but can also be called manually.
 */
function resume() {
    composableResume()
}

/**
 * Get the current scroll anchor (first visible item + offset).
 * Used by parent components to save scroll state before VirtualScroller destruction.
 * @returns {{ index: number, key: any, offset: number } | null}
 */
function getScrollAnchor() {
    return composableGetScrollAnchor()
}

/**
 * Scroll to a previously saved anchor position.
 * Used by parent components to restore scroll state after VirtualScroller recreation.
 * @param {{ index: number, key: any, offset: number }} anchor
 */
function scrollToAnchor(anchor) {
    composableScrollToAnchor(anchor)
}

// Expose methods for parent component access via ref
defineExpose({
    scrollToIndex,
    scrollToTop,
    scrollToBottom,
    getScrollState,
    getVisibleRange,
    // Additional utility methods that may be useful
    isAtBottom,
    isAtTop,
    // Stick to bottom mode control
    enableStickToBottom,
    disableStickToBottom,
    // KeepAlive support (also called automatically via onActivated/onDeactivated)
    suspend,
    resume,
    getScrollAnchor,
    scrollToAnchor,
})
</script>

<template>
    <div
        ref="containerRef"
        class="virtual-scroller"
        @scroll.passive="onScroll"
    >
        <!-- Spacer before rendered items -->
        <div
            class="virtual-scroller-spacer virtual-scroller-spacer-before"
            :style="{ height: spacerBeforeHeight + 'px' }"
        />

        <!-- Rendered items -->
        <VirtualScrollerItem
            v-for="{ item, index, key } in renderedItems"
            :key="key"
            :item-key="key"
        >
            <slot :item="item" :index="index" />
        </VirtualScrollerItem>

        <!-- Spacer after rendered items -->
        <div
            class="virtual-scroller-spacer virtual-scroller-spacer-after"
            :style="{ height: spacerAfterHeight + 'px' }"
        />
    </div>
</template>

<style scoped>
.virtual-scroller {
    overflow-y: auto;
    /* Disable browser's native scroll anchoring - we manage scroll position manually
       when item heights change above the viewport */
    overflow-anchor: none;
    /* Prevent scroll chaining to parent (e.g., pull-to-refresh on mobile) */
    overscroll-behavior: contain;

    /* IMPORTANT: Parent must provide explicit height for scrolling to work.
       This fallback only works when the parent has a defined height. */
    height: 100%;

    /* Positioning context for absolute positioning of children if needed */
    position: relative;

    /* Layout optimization: inform browser that content and layout are contained,
       allowing rendering optimizations. We don't use `size` because we need
       the container to respond to parent sizing. */
    contain: layout style;

    /* Use flex column layout for proper spacer behavior */
    display: flex;
    flex-direction: column;
}

.virtual-scroller-spacer {
    /* Spacers are purely for maintaining scroll position and scrollbar size.
       They should not create a new stacking context or interfere with layout.
       flex-shrink: 0 prevents spacers from being compressed in flex layout. */
    flex-shrink: 0;
}
</style>
