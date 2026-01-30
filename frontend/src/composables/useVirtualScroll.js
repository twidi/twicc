// frontend/src/composables/useVirtualScroll.js

import { ref, computed, reactive, watch, watchEffect, onUnmounted } from 'vue'

/**
 * Default minimum item height used for items that haven't been measured yet.
 * Matches the MIN_ITEM_SIZE constant used elsewhere in the codebase.
 */
const DEFAULT_MIN_ITEM_HEIGHT = 24

/**
 * Default buffer in pixels for preloading items before they enter the viewport.
 */
const DEFAULT_BUFFER = 500

/**
 * Default buffer in pixels before unloading items after they leave the viewport.
 * This is larger than the load buffer to prevent thrashing (constant load/unload cycles).
 */
const DEFAULT_UNLOAD_BUFFER = 1000

/**
 * Composable for virtual scrolling with variable-height items.
 *
 * Handles:
 * - Height cache for measured items
 * - Cumulative position calculation (computed)
 * - Binary search to find visible indices
 * - Scroll event handling (RAF-throttled)
 * - Render range calculation with asymmetric buffers
 * - ScrollTop correction when item heights change above viewport
 *
 * @param {Object} options - Configuration options
 * @param {import('vue').Ref<Array>} options.items - Reactive array of items to virtualize
 * @param {Function} options.itemKey - Function to extract unique key from item: (item) => key
 * @param {number} [options.minItemHeight=24] - Minimum/estimated height for unmeasured items
 * @param {number} [options.buffer=500] - Buffer in pixels for loading items
 * @param {number} [options.unloadBuffer=1000] - Buffer in pixels before unloading items
 * @param {import('vue').Ref<HTMLElement|null>} options.containerRef - Ref to the scroll container element
 * @returns {Object} Virtual scroll state and methods
 */
export function useVirtualScroll(options) {
    const {
        items,
        itemKey,
        minItemHeight = DEFAULT_MIN_ITEM_HEIGHT,
        buffer = DEFAULT_BUFFER,
        unloadBuffer = DEFAULT_UNLOAD_BUFFER,
        containerRef,
    } = options

    // ═══════════════════════════════════════════════════════════════════════════
    // Internal State
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Cache of measured heights: Map<itemKey, height>
     * Using reactive() to make the Map reactive for Vue's dependency tracking.
     */
    const heightCache = reactive(new Map())

    /**
     * Current scroll position of the container.
     */
    const scrollTop = ref(0)

    /**
     * Current viewport height of the container.
     */
    const viewportHeight = ref(0)

    /**
     * RAF handle for scroll throttling.
     */
    let rafId = null

    /**
     * Flag to track if we're in programmatic scroll mode (to avoid scroll correction).
     */
    let isProgrammaticScroll = false

    // ═══════════════════════════════════════════════════════════════════════════
    // Computed: Positions
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Computed array of item positions with cumulative top offset.
     * Each entry contains: { index, key, top, height }
     *
     * Performance note: This recomputes when items change OR when heightCache changes.
     * For large lists, consider memoization strategies if this becomes a bottleneck.
     */
    const positions = computed(() => {
        let top = 0
        return items.value.map((item, index) => {
            const key = itemKey(item)
            const height = heightCache.get(key) ?? minItemHeight
            const pos = { index, key, top, height }
            top += height
            return pos
        })
    })

    /**
     * Total height of all items (for scroll container sizing).
     */
    const totalHeight = computed(() => {
        const posArray = positions.value
        if (posArray.length === 0) return 0
        const last = posArray[posArray.length - 1]
        return last.top + last.height
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Binary Search
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Binary search to find the index of the first item whose bottom edge
     * is at or past the target position.
     *
     * Returns the index in the positions array, or -1 if targetTop is beyond all items.
     *
     * @param {number} targetTop - The scroll position to find
     * @returns {number} Index of the item at or just before this position
     */
    function findIndexAtPosition(targetTop) {
        const posArray = positions.value
        if (posArray.length === 0) return -1
        if (targetTop <= 0) return 0
        if (targetTop >= totalHeight.value) return posArray.length - 1

        let low = 0
        let high = posArray.length - 1

        while (low < high) {
            const mid = Math.floor((low + high) / 2)
            const pos = posArray[mid]
            const bottom = pos.top + pos.height

            if (bottom <= targetTop) {
                // Target is past this item's bottom, search in upper half
                low = mid + 1
            } else {
                // Target is within or before this item
                high = mid
            }
        }

        return low
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Render Range with Hysteresis
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * The range of items to render with asymmetric buffers (hysteresis).
     * This is a ref updated by watchEffect rather than a computed, because
     * the hysteresis logic requires tracking the previous range as state.
     *
     * Using watchEffect is the proper Vue pattern for this scenario:
     * - It automatically tracks all reactive dependencies (scrollTop, viewportHeight, positions)
     * - Side effects (updating previousRange) are explicit and expected
     * - The ref remains reactive for consumers
     */
    const renderRange = ref({ start: 0, end: 0 })

    /**
     * Previous render range, used to implement hysteresis.
     * Items inside this range use unloadBuffer threshold to leave.
     * Items outside this range use buffer threshold to enter.
     */
    let previousRange = { start: 0, end: 0 }

    /**
     * watchEffect that calculates the render range with hysteresis.
     *
     * Implements asymmetric buffers to prevent thrashing:
     * - To ENTER the render range: item must be within `buffer` (500px) of viewport
     * - To LEAVE the render range: item must be outside `unloadBuffer` (1000px) of viewport
     *
     * This means an item at 600px from viewport:
     * - Won't be loaded initially (outside 500px buffer)
     * - Won't be unloaded if already loaded (inside 1000px unloadBuffer)
     */
    watchEffect(() => {
        const posArray = positions.value
        if (posArray.length === 0) {
            previousRange = { start: 0, end: 0 }
            renderRange.value = { start: 0, end: 0 }
            return
        }

        // Calculate zones with both buffers
        const loadTop = Math.max(0, scrollTop.value - buffer)
        const loadBottom = scrollTop.value + viewportHeight.value + buffer
        const unloadTop = Math.max(0, scrollTop.value - unloadBuffer)
        const unloadBottom = scrollTop.value + viewportHeight.value + unloadBuffer

        // Find indices for load zone (items that SHOULD be loaded)
        const loadStart = findIndexAtPosition(loadTop)
        const loadEnd = findIndexAtPosition(loadBottom)

        // Find indices for unload zone (items that CAN stay loaded)
        const unloadStart = findIndexAtPosition(unloadTop)
        const unloadEnd = findIndexAtPosition(unloadBottom)

        // Apply hysteresis:
        // - For start: use min of (loadStart, previousStart) but never below unloadStart
        // - For end: use max of (loadEnd, previousEnd) but never above unloadEnd
        //
        // This ensures:
        // - Items enter the range when they cross the load buffer threshold
        // - Items leave the range only when they cross the unload buffer threshold

        let newStart, newEnd

        // For start index (top of render range):
        // - Keep items that are still within unload buffer (don't unload too early)
        // - But always load items that enter the load buffer
        if (previousRange.start < loadStart) {
            // Previous range started higher (smaller index)
            // Keep items that are still within unload buffer
            newStart = Math.max(unloadStart, previousRange.start)
        } else {
            // We're expanding upward or staying same - use load buffer
            newStart = loadStart
        }

        // For end index (bottom of render range):
        // - Keep items that are still within unload buffer (don't unload too early)
        // - But always load items that enter the load buffer
        if (previousRange.end > loadEnd + 1) {
            // Previous range extended further (larger index)
            // Keep items that are still within unload buffer
            newEnd = Math.min(unloadEnd + 1, previousRange.end)
        } else {
            // We're expanding downward or staying same - use load buffer
            newEnd = loadEnd + 1
        }

        // Ensure valid bounds
        newStart = Math.max(0, newStart)
        newEnd = Math.min(posArray.length, newEnd)

        // Store for next computation and update the reactive ref
        previousRange = { start: newStart, end: newEnd }
        renderRange.value = { start: newStart, end: newEnd }
    })

    /**
     * The range of items actually visible in the viewport (without buffer).
     * Useful for the @update event to tell the parent what's truly visible.
     */
    const visibleRange = computed(() => {
        const posArray = positions.value
        if (posArray.length === 0) {
            return { start: 0, end: 0 }
        }

        const visibleTop = scrollTop.value
        const visibleBottom = scrollTop.value + viewportHeight.value

        const start = findIndexAtPosition(visibleTop)
        const end = findIndexAtPosition(visibleBottom)

        return {
            start: Math.max(0, start),
            end: Math.min(posArray.length, end + 1),
        }
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Computed: Spacers
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Height of the spacer before rendered items.
     * Sum of all item heights before renderRange.start.
     */
    const spacerBeforeHeight = computed(() => {
        const posArray = positions.value
        const { start } = renderRange.value

        if (start <= 0 || posArray.length === 0) return 0
        // The top of the first rendered item IS the total height of items before it
        return posArray[start].top
    })

    /**
     * Height of the spacer after rendered items.
     * Sum of all item heights after renderRange.end.
     */
    const spacerAfterHeight = computed(() => {
        const posArray = positions.value
        const { end } = renderRange.value

        if (end >= posArray.length || posArray.length === 0) return 0
        // Total height minus the bottom position of the last rendered item
        const lastRenderedPos = posArray[end - 1]
        const lastRenderedBottom = lastRenderedPos.top + lastRenderedPos.height
        return totalHeight.value - lastRenderedBottom
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Height Update with Scroll Correction
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Update the height of an item in the cache.
     * If the item is above the viewport and its height changed, adjust scrollTop
     * to maintain the visual position of content.
     *
     * @param {*} key - The item key
     * @param {number} newHeight - The new measured height
     * @returns {{ key: any, height: number, oldHeight: number | undefined }} Change info for event emission
     */
    function updateItemHeight(key, newHeight) {
        // Reject 0 height values - these occur when items are measured while hidden
        // (e.g., in inactive wa-tab-panel with display: none). Such measurements
        // would corrupt the height cache and break rendering.
        if (newHeight === 0) {
            return null
        }

        const oldHeight = heightCache.get(key)

        // No change, no action needed
        if (oldHeight === newHeight) {
            return null
        }

        // Update the cache
        heightCache.set(key, newHeight)

        // Scroll correction: only if item existed before and is above viewport
        // and we're not in a programmatic scroll operation
        if (oldHeight !== undefined && !isProgrammaticScroll) {
            const container = containerRef.value
            if (container) {
                // Find this item's position to check if it's above the viewport
                const posArray = positions.value
                const pos = posArray.find(p => p.key === key)

                if (pos && pos.top < scrollTop.value) {
                    const delta = newHeight - oldHeight
                    container.scrollTop += delta
                    // Also update our local scrollTop ref to match
                    scrollTop.value = container.scrollTop
                }
            }
        }

        return { key, height: newHeight, oldHeight }
    }

    /**
     * Remove an item's height from the cache (when item is removed from the list).
     *
     * @param {*} key - The item key to remove
     */
    function removeItemHeight(key) {
        heightCache.delete(key)
    }

    /**
     * Clear all heights from the cache.
     * Useful when the items array is completely replaced.
     */
    function clearHeightCache() {
        heightCache.clear()
    }

    /**
     * Get the cached height for an item, or undefined if not cached.
     *
     * @param {*} key - The item key
     * @returns {number | undefined}
     */
    function getItemHeight(key) {
        return heightCache.get(key)
    }

    /**
     * Invalidate (remove) all items in the height cache that have zero height.
     * This is used when the scroller becomes visible after being hidden,
     * as items measured while hidden (display: none) report 0 height.
     * Removing these entries allows them to be re-measured with correct values.
     */
    function invalidateZeroHeights() {
        for (const [key, height] of heightCache.entries()) {
            if (height === 0) {
                heightCache.delete(key)
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Scroll Event Handler
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Handle scroll events with RAF throttling.
     * Updates scrollTop ref which triggers recomputation of render range.
     *
     * @param {Event} event - The scroll event
     */
    function handleScroll(event) {
        if (rafId !== null) {
            cancelAnimationFrame(rafId)
        }

        rafId = requestAnimationFrame(() => {
            rafId = null
            const target = event?.target || containerRef.value
            if (target) {
                scrollTop.value = target.scrollTop
            }
        })
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Navigation Methods
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
        const { align = 'start', behavior = 'auto' } = options
        const container = containerRef.value
        if (!container) return

        const posArray = positions.value
        if (index < 0 || index >= posArray.length) return

        const pos = posArray[index]
        let targetScrollTop

        switch (align) {
            case 'center':
                // Center the item in the viewport
                targetScrollTop = pos.top - (viewportHeight.value / 2) + (pos.height / 2)
                break
            case 'end':
                // Position the item at the bottom of the viewport
                targetScrollTop = pos.top + pos.height - viewportHeight.value
                break
            case 'start':
            default:
                // Position the item at the top of the viewport
                targetScrollTop = pos.top
                break
        }

        // Clamp to valid scroll range
        const maxScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)
        targetScrollTop = Math.max(0, Math.min(targetScrollTop, maxScrollTop))

        // Mark as programmatic to avoid scroll correction during this scroll
        isProgrammaticScroll = true

        if (behavior === 'smooth') {
            container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
            // Reset programmatic flag after smooth scroll completes (estimate)
            setTimeout(() => {
                isProgrammaticScroll = false
            }, 500)
        } else {
            container.scrollTop = targetScrollTop
            scrollTop.value = targetScrollTop
            isProgrammaticScroll = false
        }
    }

    /**
     * Scroll to the top of the list.
     *
     * @param {Object} [options] - Scroll options
     * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
     */
    function scrollToTop(options = {}) {
        const { behavior = 'auto' } = options
        const container = containerRef.value
        if (!container) return

        isProgrammaticScroll = true

        if (behavior === 'smooth') {
            container.scrollTo({ top: 0, behavior: 'smooth' })
            setTimeout(() => {
                isProgrammaticScroll = false
            }, 500)
        } else {
            container.scrollTop = 0
            scrollTop.value = 0
            isProgrammaticScroll = false
        }
    }

    /**
     * Scroll to the bottom of the list.
     *
     * @param {Object} [options] - Scroll options
     * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
     */
    function scrollToBottom(options = {}) {
        const { behavior = 'auto' } = options
        const container = containerRef.value
        if (!container) return

        const targetScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)

        isProgrammaticScroll = true

        if (behavior === 'smooth') {
            container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
            setTimeout(() => {
                isProgrammaticScroll = false
            }, 500)
        } else {
            container.scrollTop = targetScrollTop
            scrollTop.value = targetScrollTop
            isProgrammaticScroll = false
        }
    }

    /**
     * Get the current scroll state of the container.
     *
     * @returns {{ scrollTop: number, scrollHeight: number, clientHeight: number }}
     */
    function getScrollState() {
        const container = containerRef.value
        if (!container) {
            return { scrollTop: 0, scrollHeight: 0, clientHeight: 0 }
        }
        return {
            scrollTop: container.scrollTop,
            scrollHeight: container.scrollHeight,
            clientHeight: container.clientHeight,
        }
    }

    /**
     * Check if the scroller is currently at or near the bottom.
     *
     * @param {number} [threshold=5] - Pixel threshold for "at bottom" detection
     * @returns {boolean}
     */
    function isAtBottom(threshold = 5) {
        const container = containerRef.value
        if (!container) return true
        const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
        return distanceFromBottom <= threshold
    }

    /**
     * Check if the scroller is currently at or near the top.
     *
     * @param {number} [threshold=5] - Pixel threshold for "at top" detection
     * @returns {boolean}
     */
    function isAtTop(threshold = 5) {
        return scrollTop.value <= threshold
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Viewport Height Update
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Update the viewport height. Called by the component's ResizeObserver.
     *
     * @param {number} height - The new viewport height
     */
    function updateViewportHeight(height) {
        viewportHeight.value = height
    }

    /**
     * Sync the scroll position from the container (useful after mount or resize).
     */
    function syncScrollPosition() {
        const container = containerRef.value
        if (container) {
            scrollTop.value = container.scrollTop
            viewportHeight.value = container.clientHeight
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Cleanup
    // ═══════════════════════════════════════════════════════════════════════════

    onUnmounted(() => {
        if (rafId !== null) {
            cancelAnimationFrame(rafId)
            rafId = null
        }
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Watch for items array replacement
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * When the items array reference changes completely, we need to clean up
     * height cache entries for items that no longer exist.
     * However, we preserve heights for items that still exist (same key).
     */
    watch(
        () => items.value,
        (newItems) => {
            // Build a set of current item keys
            const currentKeys = new Set(newItems.map(item => itemKey(item)))

            // Remove cached heights for items that no longer exist
            for (const key of heightCache.keys()) {
                if (!currentKeys.has(key)) {
                    heightCache.delete(key)
                }
            }
        },
        { flush: 'post' }
    )

    // ═══════════════════════════════════════════════════════════════════════════
    // Public API
    // ═══════════════════════════════════════════════════════════════════════════

    return {
        // State (readonly refs/computed)
        positions,
        totalHeight,
        renderRange,
        visibleRange,
        spacerBeforeHeight,
        spacerAfterHeight,
        scrollTop,
        viewportHeight,

        // Height management
        updateItemHeight,
        removeItemHeight,
        clearHeightCache,
        getItemHeight,
        invalidateZeroHeights,

        // Event handler
        handleScroll,

        // Navigation
        scrollToIndex,
        scrollToTop,
        scrollToBottom,
        getScrollState,
        isAtBottom,
        isAtTop,

        // Viewport management
        updateViewportHeight,
        syncScrollPosition,
    }
}
