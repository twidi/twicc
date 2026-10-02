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
import { useVirtualScroll } from '../../composables/useVirtualScroll'
import { createRowVisibilityObserver } from '../../utils/rowVisibilityObserver.js'
import VirtualScrollerItem from './VirtualScrollerItem.vue'
import { RESIZE_OBSERVER_KEY, ROW_VISIBILITY_OBSERVER_KEY } from './virtualScrollerKeys.js'

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
    },
    /**
     * When true, disables the implicit "stay at bottom on resize" behavior.
     * Used for subagent sessions that should open at the top: prevents the
     * scroller from auto-scrolling to bottom as items expand from estimated
     * to real heights.
     *
     * NOTE: Captured at mount time. Changes after mount are not observed.
     */
    preventAutoScrollToBottom: {
        type: Boolean,
        default: false
    },
    /**
     * Optional per-item CSS min-height provider: (item) => px | null.
     * Used to bridge item swaps whose replacement re-renders its content
     * asynchronously (streaming-to-real): the floor keeps the DOM height
     * from dipping while the new content mounts.
     */
    itemMinHeight: {
        type: Function,
        default: null
    },
    /** Optional (item) => class value, bound on each row wrapper. */
    itemClass: {
        type: Function,
        default: null
    },
    /** Optional (item) => style object, bound on each row wrapper. */
    itemStyle: {
        type: Function,
        default: null
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
    'scroll',
    /**
     * Emitted when the container transitions from hidden (0 height) to visible.
     * Useful for deferring scroll operations that couldn't execute while hidden
     * (e.g., scrollToBottom on a hidden wa-tab-panel).
     */
    'became-visible',
])

// ═══════════════════════════════════════════════════════════════════════════
// Refs
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reference to the scroll container element.
 */
const containerRef = ref(null)

/**
 * Reference to the bottom sentinel element. Watched by an IntersectionObserver
 * (root = containerRef, rootMargin: '0px 0px 150px 0px') to maintain
 * isAtBottomRef and toggle the .at-bottom class on the container.
 *
 * That class re-enables native scroll anchoring only when the user is within
 * 150 px of the bottom — restricting the browser's pin-to-bottom behavior to
 * the sentinel as sole anchor candidate during streaming, and shutting it off
 * the rest of the time (so JS owns scroll exclusively when the user scrolls up).
 */
const sentinelRef = ref(null)

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
const measuredViewportHeight = ref(0)

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
    positions,
    scrollTop: composableScrollTop,
    totalHeight,
    renderRange,
    visibleRange,
    spacerBeforeHeight,
    spacerAfterHeight,
    batchUpdateItemHeights,
    handleScroll,
    scrollToIndex: composableScrollToIndex,
    scrollToKey: composableScrollToKey,
    scrollToTop: composableScrollToTop,
    scrollToBottom: composableScrollToBottom,
    scrollToEdge: composableScrollToEdge,
    getScrollState: composableGetScrollState,
    updateViewportHeight,
    syncScrollPosition,
    isAtBottom,
    isAtTop,
    isAtBottomRef,
    invalidateZeroHeights,
    getItemHeight: composableGetItemHeight,
    seedItemHeight: composableSeedItemHeight,
    preventAutoScrollToBottom: composablePreventAutoScrollToBottom,
    suspended: composableSuspended,
    suspend: composableSuspend,
    resume: composableResume,
    getScrollAnchor: composableGetScrollAnchor,
    scrollToAnchor: composableScrollToAnchor,
    setScrollTop: composableSetScrollTop,
} = useVirtualScroll({
    items: toRef(props, 'items'),
    itemKey: props.itemKey,
    minItemHeight: props.minItemHeight,
    buffer: props.buffer,
    unloadBuffer: props.unloadBuffer,
    containerRef,
    preventAutoScrollToBottom: props.preventAutoScrollToBottom,
})

// Publication observation has its own epoch. Retained rows become unknown on recovery.
const scrollerActive = computed(() => !composableSuspended.value && measuredViewportHeight.value > 0)
const visibilityRows = new Map()
let rowVisibilityObserver = null
watch([containerRef, scrollerActive], ([root, active]) => {
    rowVisibilityObserver?.disconnect()
    rowVisibilityObserver = null
    for (const registration of visibilityRows.values()) registration.onState('unknown')
    if (!root || !active) return
    rowVisibilityObserver = createRowVisibilityObserver({ root })
    for (const [element, registration] of visibilityRows) {
        registration.release = rowVisibilityObserver.observe(element, registration.onState)
    }
}, { flush: 'sync' })
provide(ROW_VISIBILITY_OBSERVER_KEY, {
    scrollerActive,
    observe(element, onState) {
        const registration = { onState, release: rowVisibilityObserver?.observe(element, onState) }
        visibilityRows.set(element, registration)
        return () => {
            if (visibilityRows.get(element) !== registration) return
            registration.release?.()
            visibilityRows.delete(element)
        }
    },
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

/**
 * IntersectionObserver watching the bottom sentinel. Maintains isAtBottomRef
 * (and therefore the .at-bottom class) so native scroll anchoring is only
 * active while the user is near the bottom.
 */
let sentinelObserver = null

onMounted(() => {
    if (containerRef.value) {
        // Initial sync of scroll position and viewport height
        syncScrollPosition()
        measuredViewportHeight.value = containerRef.value.clientHeight

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
                measuredViewportHeight.value = height

                updateViewportHeight(height)

                // Emit after updateViewportHeight so viewportHeight is correct
                // when the handler runs (e.g., for deferred scrollToBottom).
                if (wasHidden && isNowVisible && hasMounted) {
                    emit('became-visible')
                }
            })
            containerObserver.observe(containerRef.value)
        }

        // Setup IntersectionObserver on the bottom sentinel to maintain
        // isAtBottomRef. rootMargin extends the detection zone 150 px below the
        // visible area, so "near bottom" (within 150 px) still counts as
        // intersecting — matching the previous AUTO_SCROLL_THRESHOLD behavior.
        if (typeof IntersectionObserver !== 'undefined' && sentinelRef.value) {
            sentinelObserver = new IntersectionObserver(
                ([entry]) => {
                    // Ignore events while the scroller is suspended (KeepAlive
                    // deactivated, or auto-suspended for hidden tab panels).
                    // The container's clientHeight is 0 in those cases, which
                    // can spuriously toggle the sentinel out of intersection.
                    if (composableSuspended.value) return
                    if (containerRef.value?.clientHeight === 0) return
                    isAtBottomRef.value = entry?.isIntersecting ?? false
                },
                {
                    root: containerRef.value,
                    rootMargin: '0px 0px 150px 0px',
                    threshold: 0,
                }
            )
            sentinelObserver.observe(sentinelRef.value)
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
    rowVisibilityObserver?.disconnect()
    visibilityRows.clear()
    if (containerObserver) {
        containerObserver.disconnect()
        containerObserver = null
    }

    if (sentinelObserver) {
        sentinelObserver.disconnect()
        sentinelObserver = null
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
 * @param {'start' | 'center' | 'end' | 'nearest'} [options.align='start'] - Where to position
 *   the item. `nearest`: no scroll when it lies inside the viewport minus the margins,
 *   otherwise the minimal scroll to the nearer zone edge.
 * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
 * @param {number} [options.offset=0] - Pixels of room to leave before the item (not for `nearest`)
 * @param {number} [options.marginTop=0] - `nearest` only: the top band, px (may be negative)
 * @param {number} [options.marginBottom=0] - `nearest` only: the bottom band, px
 * @param {boolean} [options.allowSmooth=false] - `nearest` only: a short move scrolls
 *   smoothly (see useVirtualScroll.scrollToIndex); `behavior` is ignored for `nearest`
 * @returns {Promise<void> | null | undefined} `nearest`: the started smooth scroll's `done`, or null
 */
function scrollToIndex(index, options = {}) {
    return composableScrollToIndex(index, options)
}

/**
 * Scroll to the item with the given key, waiting for heights to stabilize.
 * @param {*} key - The item key to scroll to
 * @param {Object} [options] - See useVirtualScroll.scrollToKey for all options
 * @param {'start' | 'center' | 'end' | 'nearest'} [options.align='center'] - Where to position the item
 * @param {number} [options.marginTop=0] - `nearest` only: the top band, px (may be negative)
 * @param {number} [options.marginBottom=0] - `nearest` only: the bottom band, px
 * @param {Function} [options.isCurrent] - `nearest` only: () => boolean, false once the
 *   reveal no longer matters (the loop then stops)
 * @param {boolean} [options.allowSmooth=false] - `nearest` only: a short move scrolls
 *   smoothly; each attempt waits for the smooth scroll's end before its check
 * @returns {Promise<boolean>} true if the item is visible (`nearest`: in its zone) after scrolling
 */
function scrollToKey(key, options = {}) {
    return composableScrollToKey(key, options)
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
 * Scroll to the very top or bottom, retrying until the edge holds.
 * @param {'top' | 'bottom'} edge
 * @param {Object} [options] - See useVirtualScroll.scrollToEdge for options
 * @returns {Promise<boolean>} true if the container really sits at the edge
 */
function scrollToEdge(edge, options = {}) {
    return composableScrollToEdge(edge, options)
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

/**
 * Write an absolute scroll position (clamped; no-op when already there).
 * Used by parents restoring a scrollTop captured before a layout collapse.
 * @param {number} value
 */
function setScrollTop(value) {
    composableSetScrollTop(value)
}

/**
 * Read the cached measured height of an item, if any.
 * @param {*} key
 * @returns {number | undefined}
 */
function getItemHeight(key) {
    return composableGetItemHeight(key)
}

/**
 * Seed the height cache for an item before it first renders (see composable).
 * @param {*} key
 * @param {number} height
 */
function seedItemHeight(key, height) {
    composableSeedItemHeight(key, height)
}

// Expose methods for parent component access via ref
defineExpose({
    scrollToIndex,
    scrollToKey,
    scrollToTop,
    scrollToBottom,
    scrollToEdge,
    getScrollState,
    getVisibleRange,
    // Additional utility methods that may be useful
    isAtBottom,
    isAtTop,
    // Item geometry and scroll position, both reactive. Consumers that reason in
    // pixels rather than in item indices (chat navigation) read these.
    positions,
    scrollTop: composableScrollTop,
    // Prevent implicit "stay at bottom" on resize (writable ref)
    preventAutoScrollToBottom: composablePreventAutoScrollToBottom,
    // KeepAlive support (also called automatically via onActivated/onDeactivated)
    suspended: composableSuspended,
    suspend,
    resume,
    getScrollAnchor,
    scrollToAnchor,
    setScrollTop,
    getItemHeight,
    seedItemHeight,
})
</script>

<template>
    <div
        ref="containerRef"
        class="virtual-scroller"
        :class="{ 'at-bottom': isAtBottomRef }"
        @scroll.passive="onScroll"
    >
        <!-- Optional content placed before everything else (e.g. a list's gliding ink,
             which must be the root's first child) -->
        <slot name="before" />

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
            :min-height="itemMinHeight ? itemMinHeight(item) : null"
            :class="itemClass ? itemClass(item) : null"
            :style="itemStyle ? itemStyle(item) : null"
        >
            <slot :item="item" :index="index" />
        </VirtualScrollerItem>

        <!-- Spacer after rendered items -->
        <div
            class="virtual-scroller-spacer virtual-scroller-spacer-after"
            :style="{ height: spacerAfterHeight + 'px' }"
        />

        <!-- Scroll anchor sentinel: enables native pin-to-bottom via overflow-anchor.
             When visible (user at bottom), the browser keeps it in viewport as content
             grows above it, achieving pin-to-bottom without any JS scroll writes.
             When out of view (user scrolled up), anchoring naturally stops.
             Watched by IntersectionObserver to maintain the .at-bottom class. -->
        <div
            ref="sentinelRef"
            class="virtual-scroller-anchor"
            aria-hidden="true"
        />
    </div>
</template>

<style scoped>
.virtual-scroller {
    overflow-y: auto;
    /* Prevent scroll chaining to parent (e.g., pull-to-refresh on mobile) */
    overscroll-behavior: contain;
    /* Native scroll anchoring is disabled by default on the scroll container so
       Chrome can't interfere with our JS-based anchor correction. The .at-bottom
       class (toggled by the sentinel's IntersectionObserver) re-enables it, which
       lets the sentinel below act as the sole anchor candidate and drive
       passive pin-to-bottom while the user is near the bottom. */
    overflow-anchor: none;

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

/* When the user is near the bottom (sentinel intersecting), re-enable native
   scroll anchoring on the container. Combined with the descendant disqualification
   rules below, only the sentinel remains as a valid anchor candidate, giving
   passive pin-to-bottom behavior with no JS scroll writes during streaming. */
.virtual-scroller.at-bottom {
    overflow-anchor: auto;
}

.virtual-scroller-spacer {
    /* Spacers are purely for maintaining scroll position and scrollbar size.
       They should not create a new stacking context or interfere with layout.
       flex-shrink: 0 prevents spacers from being compressed in flex layout. */
    flex-shrink: 0;
    /* Disqualify spacers as scroll-anchor candidates so the browser only ever
       picks the sentinel below. */
    overflow-anchor: none;
}

/* Disqualify all rendered content from being a scroll-anchor candidate. Combined
   with the sentinel below, this restricts the browser's native scroll anchoring
   to a single, predictable target: pinning to the bottom when the sentinel is
   visible, and doing nothing when the user has scrolled away. */
.virtual-scroller :deep(*) {
    overflow-anchor: none;
}

.virtual-scroller-anchor {
    overflow-anchor: auto;
    /* Must have non-zero size to be a valid anchor candidate. */
    height: 1px;
    flex-shrink: 0;
}
</style>
