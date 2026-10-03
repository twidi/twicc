// frontend/src/composables/useVirtualScroll.js

import { ref, computed, reactive, toRaw, watch, watchEffect, onUnmounted } from 'vue'
import { createVirtualScrollGeometry } from '../utils/virtualScrollGeometry.js'
import { nearestScrollTop, revealBehavior, startSmoothScroll } from '../utils/nearestScroll.js'
import { isReducedMotion } from '../utils/reducedMotion.js'

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
 * Minimum height delta (px) for a ResizeObserver measurement to count as a real change.
 * Firefox reports fractional borderBox heights that wobble sub-pixel between frames (e.g.
 * 976.70001 → 976.70003); without this guard every wobble is treated as a change and re-emits
 * item-resized in an endless loop that never lets the scroll-stability debounce settle, leaving
 * the chat permanently hidden after it was loaded while its tab panel was display:none. Mirrors
 * the 0.5px guard already used on the anchor scroll correction in batchUpdateItemHeights.
 */
const HEIGHT_CHANGE_EPSILON_PX = 0.5

/**
 * The container input events that mean the user scrolls it (wheel, touch, a scrollbar drag),
 * and the keys that scroll it. See userScrollSeq.
 */
const USER_SCROLL_EVENTS = ['wheel', 'touchstart', 'touchmove', 'pointerdown']
const SCROLL_KEYS = new Set(['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' '])
const USER_SCROLL_LISTENER_OPTIONS = { passive: true }


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
 * @param {(item: Object) => string|number} options.itemKey - Extract a unique String/Number key.
 * VirtualScrollerItem supports this key contract.
 * @param {number} [options.minItemHeight=24] - Minimum/estimated height for unmeasured items
 * @param {number} [options.buffer=500] - Buffer in pixels for loading items
 * @param {number} [options.unloadBuffer=1000] - Buffer in pixels before unloading items
 * @param {import('vue').Ref<HTMLElement|null>} options.containerRef - Ref to the scroll container element
 * @param {Function} [options.onUserScroll] - Called synchronously for navigation input
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

    /**
     * Reactive "near bottom" state, maintained by VirtualScroller via an
     * IntersectionObserver on the bottom sentinel (with a 150px rootMargin).
     * Single source of truth for the question "is the user close enough to
     * the bottom to follow new content?". Drives the .at-bottom CSS class
     * (which re-enables native scroll anchoring for passive pin-to-bottom)
     * and is consumed by the auto-scroll watchers in SessionItemsList.
     *
     * Defaults to true: before the IntersectionObserver has had a chance to
     * report, sessions usually open at the bottom and we want auto-follow
     * behavior to be active by default.
     */
    const isAtBottomRef = ref(true)

    // ═══════════════════════════════════════════════════════════════════════════
    // Internal State
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Cache of measured heights: Map<itemKey, height>
     * Using reactive() to make the Map reactive for Vue's dependency tracking.
     */
    const heightCache = reactive(new Map())
    // The sync watcher records only the newest replacement. The post watcher owns
    // active cleanup; resume consumes it first when that watcher has not run yet.
    let latestReplacement = items.value
    let lastCleanedItems = items.value
    let pendingZeroHeightInvalidation = false

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
    let lifecycleGeneration = 0
    let disposed = false
    const pendingHeightWaits = new Set()

    /**
     * Flag to track if we're in programmatic scroll mode (to avoid scroll correction).
     */
    let isProgrammaticScroll = false
    let programmaticToken = 0
    let programmaticResetTimer = null

    /**
     * When true, the implicit "stay at bottom on resize" behavior in
     * batchUpdateItemHeights is disabled. This is used for subagent sessions
     * that should open at the top: without this flag, items initially fit in
     * the viewport (estimated height < viewportHeight), so isAtBottom() returns
     * true, and every subsequent resize scrolls to bottom automatically.
     *
     * Initialized from the `preventAutoScrollToBottom` option so that the flag
     * is set before any items render or resize events fire.
     */
    const preventAutoScrollToBottom = ref(options.preventAutoScrollToBottom ?? false)

    /**
     * Suspended state for KeepAlive support.
     * When the scroller's container is detached from the DOM (e.g., by Vue's KeepAlive),
     * the browser resets scrollTop to 0. We save the scroll anchor (visible item + offset)
     * before this happens and freeze all reactive recalculations to prevent renderRange/spacer
     * corruption.
     *
     * The anchor-based approach is more robust than saving raw scrollTop because:
     * - It survives viewport size changes while suspended (e.g., window resize)
     * - It doesn't depend on exact pixel positions that may shift if items are re-measured
     *
     * - `suspended`: reactive ref — true when the scroller is in a detached/hidden state.
     *   MUST be a ref (not a plain let) because the renderRange watchEffect reads it:
     *   if it were a plain variable, Vue wouldn't track it as a dependency, and
     *   setting suspended=false in resume() would NOT re-trigger the watchEffect,
     *   leaving renderRange frozen and items unrendered.
     * - `savedAnchors`: [{ index, key, offset }] — the item at the top of the viewport and the
     *   few above it, each with its OWN offset measured from the same scrollTop. Several
     *   candidates because the items array can be recomputed shorter while suspended (a
     *   completed agent turn folds items into a collapsible group, conversation mode drops
     *   the previous assistant message, a synthetic item is retired…): whichever candidate
     *   survived restores the exact same position, since all offsets share one origin.
     */
    const suspended = ref(false)
    let suspendedSnapshot = null
    let savedAnchors = null
    let pendingResume = false
    // When the container is hidden by a parent (e.g., wa-tab-panel display:none),
    // the scroller auto-suspends to prevent scrollTop corruption from browser reset.
    // This flag distinguishes auto-suspension (tab switch) from manual suspension
    // (KeepAlive deactivation), so we know to auto-resume when the container
    // becomes visible again.
    let autoSuspended = false

    /**
     * RAF handle of a pending anchor re-application (see restoreSavedAnchor).
     */
    let resumeRetryHandle = null

    /**
     * Bumped by every explicit navigation (scrollToIndex/scrollToTop/scrollToBottom/
     * scrollToAnchor/setScrollTop; not by a `nearest` scrollToIndex that does not scroll).
     * A pending anchor restore compares it across frames and gives up when it changed: an
     * explicit scroll is a newer intent than the position being restored.
     */
    let explicitScrollSeq = 0

    /**
     * Bumped by the user's scroll input on the container (USER_SCROLL_EVENTS, a SCROLL_KEYS
     * keydown). A `nearest` scrollToKey ends when it changed: the user moved the list. Input,
     * not scroll events: the anchor correction, the anchor restores, native pin-to-bottom
     * and the browser's own clamp all scroll without the programmatic flag.
     */
    let userScrollSeq = 0

    /**
     * Bumped once at the start of each `nearest` scrollToKey: a loop ends when a newer
     * reveal started, even one that did not scroll (its row already in the zone).
     * explicitScrollSeq is not bumped for that: a no-write reveal would cancel a pending
     * anchor restore.
     */
    let revealSeq = 0

    /**
     * The element the user-input listeners are on (attached by the containerRef watcher,
     * removed at unmount, when containerRef is already null).
     */
    let listenedEl = null

    /**
     * The smooth `nearest` reveal in flight: { target, token, done, cancel } (see
     * scrollToIndex's allowSmooth), null otherwise. While set, the reveal computations take
     * its target as the current scroll, and isProgrammaticScroll holds until its end. Ended
     * early by endSmooth(). `smoothToken` guards the end: an older smooth scroll's end never
     * clears a newer one.
     */
    let smoothTo = null
    let smoothToken = 0

    /**
     * How many frames the anchor restore keeps re-applying while the DOM refuses it.
     */
    const RESUME_RETRY_FRAMES = 8

    /**
     * How many fallback anchor candidates are captured on suspend (see savedAnchors).
     */
    const ANCHOR_CANDIDATES = 5

    // ═══════════════════════════════════════════════════════════════════════════
    // Computed: Positions
    // ═══════════════════════════════════════════════════════════════════════════

    // Keys depend only on topology and reactive fields accessed by itemKey.
    // This computed stays lazy while hidden, including triggerRef invalidation.
    const orderedKeys = computed((previous) => {
        const keys = items.value.map(item => itemKey(item))
        if (previous && previous.length === keys.length
            && keys.every((key, index) => Object.is(key, previous[index]))) return previous
        return Object.freeze(keys)
    })
    const geometry = createVirtualScrollGeometry({ minItemHeight })
    const geometryRevision = ref(0)
    const rawHeightCache = toRaw(heightCache)

    function setCachedHeight(key, height) {
        const oldHeight = rawHeightCache.get(key) ?? minItemHeight
        const changed = oldHeight !== height
        if (changed) geometry.invalidateKey(key)
        heightCache.set(key, height)
        if (changed) geometryRevision.value++
    }

    function deleteCachedHeight(key) {
        const changed = rawHeightCache.has(key) && rawHeightCache.get(key) !== minItemHeight
        if (changed) geometry.invalidateKey(key)
        heightCache.delete(key)
        if (changed) geometryRevision.value++
    }

    const livePositions = computed(() => {
        geometryRevision.value
        return geometry.reconcile(orderedKeys.value, key => rawHeightCache.get(key))
    })

    const positions = computed(() => suspended.value ? suspendedSnapshot.positions : livePositions.value)

    function totalHeightForPositions(posArray) {
        if (posArray.length === 0) return 0
        const last = posArray[posArray.length - 1]
        return last.top + last.height
    }

    /**
     * Total height of all items (for scroll container sizing).
     */
    const totalHeight = computed(() => {
        if (suspended.value) return suspendedSnapshot.totalHeight
        return totalHeightForPositions(positions.value)
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
    function findIndexAtPosition(targetTop, posArray = positions.value) {
        if (posArray.length === 0) return -1
        if (targetTop <= 0) return 0
        if (targetTop >= totalHeightForPositions(posArray)) return posArray.length - 1

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
    function updateRenderRange(posArray) {
        if (posArray.length === 0) {
            previousRange = { start: 0, end: 0 }
            if (renderRange.value.start !== 0 || renderRange.value.end !== 0) {
                renderRange.value = { start: 0, end: 0 }
            }
            return
        }

        // Calculate zones with both buffers
        const loadTop = Math.max(0, scrollTop.value - buffer)
        const loadBottom = scrollTop.value + viewportHeight.value + buffer
        const unloadTop = Math.max(0, scrollTop.value - unloadBuffer)
        const unloadBottom = scrollTop.value + viewportHeight.value + unloadBuffer

        // Find indices for load zone (items that SHOULD be loaded)
        const loadStart = findIndexAtPosition(loadTop, posArray)
        const loadEnd = findIndexAtPosition(loadBottom, posArray)

        // Find indices for unload zone (items that CAN stay loaded)
        const unloadStart = findIndexAtPosition(unloadTop, posArray)
        const unloadEnd = findIndexAtPosition(unloadBottom, posArray)

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
        if (renderRange.value.start !== newStart || renderRange.value.end !== newEnd) {
            renderRange.value = { start: newStart, end: newEnd }
        }
    }

    watchEffect(() => {
        // Avoid subscribing this effect to live positions while hidden. It must also
        // ignore an empty replacement until the scroller resumes.
        if (suspended.value) return
        updateRenderRange(positions.value)
    })

    /**
     * The range of items actually visible in the viewport (without buffer).
     * Useful for the @update event to tell the parent what's truly visible.
     */
    function visibleRangeFor(posArray, currentScrollTop, currentViewportHeight) {
        if (posArray.length === 0) {
            return { start: 0, end: 0 }
        }

        const visibleTop = currentScrollTop
        const visibleBottom = currentScrollTop + currentViewportHeight

        const start = findIndexAtPosition(visibleTop, posArray)
        const end = findIndexAtPosition(visibleBottom, posArray)

        return {
            start: Math.max(0, start),
            end: Math.min(posArray.length, end + 1),
        }
    }

    const visibleRange = computed((previous) => {
        if (suspended.value) return suspendedSnapshot.visibleRange
        const next = visibleRangeFor(positions.value, scrollTop.value, viewportHeight.value)
        return previous && previous.start === next.start && previous.end === next.end ? previous : next
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Computed: Spacers
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Height of the spacer before rendered items.
     * Sum of all item heights before renderRange.start.
     */
    function spacerBeforeFor(posArray, { start }) {
        if (start <= 0 || start >= posArray.length || posArray.length === 0) return 0
        // The top of the first rendered item IS the total height of items before it
        return posArray[start].top
    }

    const spacerBeforeHeight = computed(() => {
        if (suspended.value) return suspendedSnapshot.spacerBeforeHeight
        return spacerBeforeFor(positions.value, renderRange.value)
    })

    /**
     * Height of the spacer after rendered items.
     * Sum of all item heights after renderRange.end.
     */
    function spacerAfterFor(posArray, { end }, height) {
        if (end <= 0 || end >= posArray.length || posArray.length === 0) return 0
        // Total height minus the bottom position of the last rendered item
        const lastRenderedPos = posArray[end - 1]
        const lastRenderedBottom = lastRenderedPos.top + lastRenderedPos.height
        return height - lastRenderedBottom
    }

    const spacerAfterHeight = computed(() => {
        if (suspended.value) return suspendedSnapshot.spacerAfterHeight
        const posArray = positions.value
        return spacerAfterFor(posArray, renderRange.value, totalHeight.value)
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Height Update with Scroll Correction
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Update the height of an item in the cache (single item version).
     * For better performance with multiple items, use batchUpdateItemHeights.
     *
     * @param {*} key - The item key
     * @param {number} newHeight - The new measured height
     * @returns {{ key: any, height: number, oldHeight: number | undefined } | null} Change info for event emission
     */
    function updateItemHeight(key, newHeight) {
        // Delegate to batch function for single item
        const results = batchUpdateItemHeights(new Map([[key, newHeight]]))
        return results.length > 0 ? results[0] : null
    }

    /**
     * Update multiple item heights at once with anchor-based scroll preservation.
     * This is the core function for handling batched ResizeObserver updates.
     *
     * ANCHOR-BASED SCROLLING:
     * Instead of correcting scrollTop after changes (which causes jumps),
     * we anchor on a visible item and maintain its visual position.
     *
     * Algorithm:
     * 1. Capture the "anchor" - the first item visible at the top of viewport
     * 2. Calculate offset = how far scrollTop is into that anchor item
     * 3. Apply all height changes to the cache
     * 4. Restore: newScrollTop = newAnchorTop + offset
     *
     * @param {Map<any, number>} updates - Map of itemKey → newHeight
     * @returns {Array<{key: any, height: number, oldHeight: number | undefined} | null>} Array of changes
     */
    function batchUpdateItemHeights(updates) {
        const container = containerRef.value
        const results = []

        // When suspended (container detached by KeepAlive), skip all updates.
        // The height cache retains its valid values, and any 0-height measurements
        // from detached items would be filtered out anyway.
        if (suspended.value) {
            return results
        }

        // Filter out zero heights and unchanged values
        const actualUpdates = new Map()
        for (const [key, newHeight] of updates) {
            // Reject 0 height values - these occur when items are measured while hidden
            if (newHeight === 0) {
                continue
            }
            const oldHeight = heightCache.get(key)
            // Ignore sub-pixel wobble (see HEIGHT_CHANGE_EPSILON_PX): only react when the height
            // changed by at least the epsilon. The first measurement (oldHeight === undefined) and
            // any genuine change still go through; the precise newHeight is cached so positions stay exact.
            if (oldHeight === undefined || Math.abs(oldHeight - newHeight) >= HEIGHT_CHANGE_EPSILON_PX) {
                actualUpdates.set(key, { newHeight, oldHeight })
            }
        }

        // If no actual changes, return empty
        if (actualUpdates.size === 0) {
            return []
        }

        // If no container or programmatic scroll, just apply without anchor logic
        if (!container || isProgrammaticScroll) {
            for (const [key, { newHeight, oldHeight }] of actualUpdates) {
                setCachedHeight(key, newHeight)
                results.push({ key, height: newHeight, oldHeight })
            }
            return results
        }

        // ══════════════════════════════════════════════════════════════════
        // ANCHOR-BASED SCROLLING
        //
        // Pin-to-bottom is handled by native browser scroll anchoring on the
        // sentinel element rendered at the end of the scroller (see
        // VirtualScroller.vue). This block only handles preserving scroll
        // position relative to a visible item when items above the viewport
        // change height (e.g., a code block far above the viewport renders).
        // ══════════════════════════════════════════════════════════════════

        const currentScrollTop = container.scrollTop
        const posArray = positions.value

        // Edge case: empty list or no positions
        if (posArray.length === 0) {
            for (const [key, { newHeight, oldHeight }] of actualUpdates) {
                setCachedHeight(key, newHeight)
                results.push({ key, height: newHeight, oldHeight })
            }
            return results
        }

        // 1. CAPTURE THE ANCHOR before any modifications
        // Find the first item whose bottom is past scrollTop (i.e., visible at top)
        const anchorIndex = findIndexAtPosition(currentScrollTop)
        const anchorItem = posArray[anchorIndex]

        // Safety check
        if (!anchorItem) {
            for (const [key, { newHeight, oldHeight }] of actualUpdates) {
                setCachedHeight(key, newHeight)
                results.push({ key, height: newHeight, oldHeight })
            }
            return results
        }

        const anchorKey = anchorItem.key
        const anchorOriginalTop = anchorItem.top

        // 2. APPLY all height changes to the cache
        for (const [key, { newHeight, oldHeight }] of actualUpdates) {
            setCachedHeight(key, newHeight)
            results.push({ key, height: newHeight, oldHeight })
        }

        // 3. RESTORE scroll position relative to anchor
        // The positions computed will auto-recompute due to reactivity
        const newPosArray = positions.value

        // Find the anchor in the new positions
        const newAnchorItem = newPosArray.find(p => p.key === anchorKey)

        if (newAnchorItem) {
            const heightDelta = newAnchorItem.top - anchorOriginalTop
            if (Number.isFinite(heightDelta) && Math.abs(heightDelta) > 0.5) {
                // Firefox's compositor can already be farther into a touch gesture
                // than the main-thread scrollTop. An absolute write discards that
                // movement. Apply only the height delta to the browser's position.
                if (typeof container.scrollBy === 'function') {
                    container.scrollBy({ top: heightDelta, behavior: 'instant' })
                } else {
                    const maxScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)
                    container.scrollTop = Math.max(0, Math.min(currentScrollTop + heightDelta, maxScrollTop))
                }
                // The browser can clamp the correction at a scroll boundary.
                scrollTop.value = container.scrollTop
            }
        }

        return results
    }

    /**
     * Remove an item's height from the cache (when item is removed from the list).
     *
     * @param {*} key - The item key to remove
     */
    function removeItemHeight(key) {
        deleteCachedHeight(key)
    }

    /**
     * Clear all heights from the cache.
     * Useful when the items array is completely replaced.
     */
    function clearHeightCache() {
        let changed = false
        for (const [key, height] of rawHeightCache) {
            if (height === minItemHeight) continue
            geometry.invalidateKey(key)
            changed = true
        }
        heightCache.clear()
        if (changed) geometryRevision.value++
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
     * Seed the height cache for an item BEFORE it first renders (raw write, no
     * anchor logic). Used across the streaming-to-real item swap: the real item
     * inherits the streamed item's measured height so positions and scrollHeight
     * never dip while it mounts and re-renders its markdown. Must stay
     * consistent with the DOM (the caller also floors the rendered element),
     * otherwise the anchor correction in batchUpdateItemHeights would operate
     * on positions that disagree with what the user actually sees.
     *
     * @param {*} key - The item key to seed
     * @param {number} height - Height in pixels
     */
    function seedItemHeight(key, height) {
        if (!Number.isFinite(height) || height <= 0) return
        setCachedHeight(key, height)
    }

    /**
     * Invalidate (remove) all items in the height cache that have zero height.
     * This is used when the scroller becomes visible after being hidden,
     * as items measured while hidden (display: none) report 0 height.
     * Removing these entries allows them to be re-measured with correct values.
     */
    function invalidateZeroHeights() {
        if (suspended.value) {
            pendingZeroHeightInvalidation = true
            return
        }
        for (const [key, height] of heightCache.entries()) {
            if (height === 0) {
                deleteCachedHeight(key)
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
        // Ignore scroll events while suspended (container is detached).
        // The browser may fire a scroll event when resetting scrollTop to 0 during detach.
        if (suspended.value || disposed) return

        if (rafId !== null) {
            cancelAnimationFrame(rafId)
        }

        const generation = lifecycleGeneration
        const handle = requestAnimationFrame(() => {
            if (generation !== lifecycleGeneration || rafId !== handle || disposed) return
            rafId = null
            if (suspended.value) return
            const target = event?.target || containerRef.value
            if (target) {
                // When the container is hidden (e.g., parent wa-tab-panel set to
                // display:none), the browser resets scrollTop to 0 and fires a
                // scroll event. Updating scrollTop.value here would corrupt the
                // saved scroll position. Instead, auto-suspend to freeze state.
                //
                // The ResizeObserver on the container does NOT fire when a *parent*
                // element gets display:none, so updateViewportHeight(0) may never
                // be called. This check in handleScroll acts as a fallback
                // detection: if the container has no height, it's hidden.
                if (target.clientHeight === 0 && viewportHeight.value > 0) {
                    enterSuspension('automatic')
                    return
                }
                scrollTop.value = target.scrollTop
            }
        })
        rafId = handle
    }

    // A scrolling gesture the browser performs itself cancels a smooth reveal's animation:
    // it ends it (endSmooth), so the anchor correction works again at once. A click or a tap
    // on a row does not cancel the animation: a child's pointerdown and a touchstart do not.
    function noteUserScroll(event) {
        if (suspended.value || disposed) return
        userScrollSeq++
        options.onUserScroll?.(event)
        if (event.type === 'wheel' || (event.type === 'pointerdown' && event.target === containerRef.value)) {
            endSmooth()
        }
    }

    // Arrow / Home / End / PageUp / PageDown are handled by the list (defaultPrevented, often
    // no scroll at all): only a key the browser scrolls ends a smooth reveal.
    function noteScrollKey(event) {
        if (suspended.value || disposed) return
        if (!SCROLL_KEYS.has(event.key)) return
        userScrollSeq++
        options.onUserScroll?.(event)
        if (!event.defaultPrevented) endSmooth()
    }

    // The browser took the touch for panning (a touchmove also fires for a tap's jitter).
    function noteTouchPan(event) {
        if (suspended.value || disposed) return
        if (event.pointerType === 'touch') endSmooth()
    }

    function addUserScrollListeners(el) {
        for (const type of USER_SCROLL_EVENTS) el.addEventListener(type, noteUserScroll, USER_SCROLL_LISTENER_OPTIONS)
        el.addEventListener('keydown', noteScrollKey, USER_SCROLL_LISTENER_OPTIONS)
        el.addEventListener('pointercancel', noteTouchPan, USER_SCROLL_LISTENER_OPTIONS)
    }

    function removeUserScrollListeners(el) {
        for (const type of USER_SCROLL_EVENTS) el.removeEventListener(type, noteUserScroll, USER_SCROLL_LISTENER_OPTIONS)
        el.removeEventListener('keydown', noteScrollKey, USER_SCROLL_LISTENER_OPTIONS)
        el.removeEventListener('pointercancel', noteTouchPan, USER_SCROLL_LISTENER_OPTIONS)
    }

    // Sync: the listeners are on the container as soon as it is set, before any reveal.
    watch(containerRef, (el) => {
        if (listenedEl) removeUserScrollListeners(listenedEl)
        if (el) addUserScrollListeners(el)
        listenedEl = el ?? null
    }, { immediate: true, flush: 'sync' })

    // ═══════════════════════════════════════════════════════════════════════════
    // Navigation Methods
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * The `nearest` target of an item (see nearestScrollTop): the minimal scroll that brings
     * it inside the viewport minus the margins, clamped to the container's real scroll range.
     * The viewport is the container's clientHeight, read now: viewportHeight holds the
     * content-box height after a resize but the clientHeight after a mount or a resume.
     * Equals `current` when the item is already inside the zone.
     *
     * @param {HTMLElement} container - The scroll container
     * @param {{ top: number, height: number }} pos - The item's position
     * @param {number} marginTop - px (may be negative)
     * @param {number} marginBottom - px
     * @param {number} [current=scrollTop.value] - The scroll the zone is taken at
     * @returns {number}
     */
    function nearestTarget(container, pos, marginTop, marginBottom, current = scrollTop.value) {
        const viewport = container.clientHeight
        return nearestScrollTop({
            top: pos.top,
            height: pos.height,
            scrollTop: current,
            viewport,
            scrollMax: container.scrollHeight - viewport,
            marginTop,
            marginBottom,
        })
    }

    /**
     * End the smooth reveal in flight, if any: invalidate its token, clear its destination,
     * release its programmatic hold and resolve its `done`. Called on a scrolling gesture the
     * browser performs itself (it cancels the animation) and before every other programmatic
     * write.
     */
    function endSmooth() {
        if (!smoothTo) return
        const { cancel, programmaticOwner } = smoothTo
        smoothTo = null
        smoothToken++
        releaseProgrammaticScroll(programmaticOwner)
        cancel()
    }

    function beginProgrammaticScroll() {
        if (programmaticResetTimer !== null) {
            clearTimeout(programmaticResetTimer)
            programmaticResetTimer = null
        }
        isProgrammaticScroll = true
        return ++programmaticToken
    }

    function releaseProgrammaticScroll(owner) {
        if (owner === programmaticToken) isProgrammaticScroll = false
    }

    function scheduleProgrammaticReset(owner) {
        const generation = lifecycleGeneration
        const handle = setTimeout(() => {
            if (generation !== lifecycleGeneration || owner !== programmaticToken || programmaticResetTimer !== handle) return
            programmaticResetTimer = null
            releaseProgrammaticScroll(owner)
        }, 500)
        programmaticResetTimer = handle
    }

    /**
     * Start a smooth reveal to `target`, a running one ended first. isProgrammaticScroll
     * holds until its end, so the anchor correction cannot write mid-scroll (a write would
     * cancel the animation); only this scroll's own end releases it.
     *
     * @param {HTMLElement} container - The scroll container
     * @param {number} target - The clamped target
     * @returns {Promise<void>} Its `done`
     */
    function startSmoothReveal(container, target) {
        endSmooth()
        const token = ++smoothToken
        const programmaticOwner = beginProgrammaticScroll()
        const generation = lifecycleGeneration
        const { done, cancel } = startSmoothScroll(container, target)
        smoothTo = { target, token, done, cancel, programmaticOwner }
        done.then(() => {
            if (generation !== lifecycleGeneration || smoothTo?.token !== token) return
            smoothTo = null
            releaseProgrammaticScroll(programmaticOwner)
        })
        return done
    }

    /**
     * Whether a smooth scroll up to `target` mounts only measured items: every index the
     * render range will add on the way, [findIndexAtPosition(max(0, target - buffer)),
     * renderRange.start), has a cached height. An item mounting unmeasured would step the
     * visible rows mid-animation once measured (the anchor correction is held).
     *
     * @param {number} target - The smooth scroll's target
     * @returns {boolean}
     */
    function upwardItemsMeasured(target) {
        const posArray = positions.value
        const from = findIndexAtPosition(Math.max(0, target - buffer))
        for (let i = Math.max(0, from); i < renderRange.value.start; i++) {
            if (!heightCache.has(posArray[i].key)) return false
        }
        return true
    }

    /**
     * Scroll to a specific item index.
     *
     * @param {number} index - The item index to scroll to
     * @param {Object} [options] - Scroll options
     * @param {'start' | 'center' | 'end' | 'nearest'} [options.align='start'] - Where to position
     *   the item. `nearest`: no scroll when it lies inside the viewport minus the margins,
     *   otherwise the minimal scroll that brings it to the nearer zone edge (nearestScrollTop).
     * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior. Ignored by `nearest`.
     * @param {number} [options.offset=0] - Pixels of room to leave before the item,
     *   i.e. how far short of it to stop. Clamped away at the top of the list. Ignored by `nearest`.
     * @param {number} [options.marginTop=0] - `nearest` only: the top band, px (may be negative)
     * @param {number} [options.marginBottom=0] - `nearest` only: the bottom band, px
     * @param {boolean} [options.allowSmooth=false] - `nearest` only: a short move (at most
     *   min(clientHeight, buffer), and going up only over measured items) scrolls smoothly,
     *   unless the user prefers reduced motion
     * @returns {Promise<void> | null | undefined} `nearest`: the started smooth scroll's `done`,
     *   or null (an instant write or no write)
     */
    function scrollToIndex(index, options = {}) {
        if (suspended.value || disposed) return null
        const {
            align = 'start',
            behavior = 'auto',
            offset = 0,
            marginTop = 0,
            marginBottom = 0,
            allowSmooth = false,
        } = options
        const container = containerRef.value
        if (!container) return null

        const posArray = positions.value
        if (index < 0 || index >= posArray.length) return null

        const pos = posArray[index]
        let targetScrollTop

        if (align === 'nearest') {
            // The current scroll is where a smooth reveal in flight is going: a second reveal
            // is judged against its destination, not the position mid-way.
            const current = smoothTo ? smoothTo.target : scrollTop.value
            // Clamped to the container's real range by nearestTarget. Inside the zone: no
            // write (a running smooth scroll finishes), and no explicit-scroll bump (it would
            // cancel a pending anchor restore).
            targetScrollTop = nearestTarget(container, pos, marginTop, marginBottom, current)
            if (targetScrollTop === current) return null
            explicitScrollSeq++
            const smooth = revealBehavior({
                distance: Math.abs(targetScrollTop - current),
                viewport: Math.min(container.clientHeight, buffer),
                reduced: isReducedMotion(),
                allowed: allowSmooth && (targetScrollTop >= current || upwardItemsMeasured(targetScrollTop)),
            }) === 'smooth'
            if (smooth) return startSmoothReveal(container, targetScrollTop)
            writeProgrammaticScroll(container, targetScrollTop, 'auto')
            return null
        }

        explicitScrollSeq++

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
        targetScrollTop = Math.max(0, Math.min(targetScrollTop - offset, maxScrollTop))

        writeProgrammaticScroll(container, targetScrollTop, behavior)
    }

    /**
     * Write scrollToIndex's target, marked as programmatic to avoid the anchor correction
     * during this scroll.
     *
     * @param {HTMLElement} container - The scroll container
     * @param {number} targetScrollTop - The clamped target
     * @param {'auto' | 'smooth'} behavior - Scroll behavior
     */
    function writeProgrammaticScroll(container, targetScrollTop, behavior) {
        if (behavior !== 'smooth') endSmooth()
        const owner = beginProgrammaticScroll()

        if (behavior === 'smooth') {
            container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
            // Reset programmatic flag after smooth scroll completes (estimate)
            scheduleProgrammaticReset(owner)
        } else {
            container.scrollTop = targetScrollTop
            scrollTop.value = targetScrollTop
            releaseProgrammaticScroll(owner)
        }
    }

    /**
     * Scroll to the top of the list.
     *
     * @param {Object} [options] - Scroll options
     * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
     */
    function scrollToTop(options = {}) {
        if (suspended.value || disposed) return
        const { behavior = 'auto' } = options
        const container = containerRef.value
        if (!container) return

        endSmooth()
        explicitScrollSeq++
        const owner = beginProgrammaticScroll()

        if (behavior === 'smooth') {
            container.scrollTo({ top: 0, behavior: 'smooth' })
            scheduleProgrammaticReset(owner)
        } else {
            container.scrollTop = 0
            scrollTop.value = 0
            releaseProgrammaticScroll(owner)
        }
    }

    /**
     * Scroll to the bottom of the list.
     *
     * @param {Object} [options] - Scroll options
     * @param {'auto' | 'smooth'} [options.behavior='auto'] - Scroll behavior
     */
    function scrollToBottom(options = {}) {
        if (suspended.value || disposed) return
        const { behavior = 'auto' } = options
        const container = containerRef.value
        if (!container) return

        const targetScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)

        endSmooth()
        explicitScrollSeq++
        const owner = beginProgrammaticScroll()

        if (behavior === 'smooth') {
            container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
            scheduleProgrammaticReset(owner)
        } else {
            container.scrollTop = targetScrollTop
            scrollTop.value = targetScrollTop
            releaseProgrammaticScroll(owner)
        }
    }

    /**
     * Scroll to the very top or the very bottom, retrying until the edge holds.
     *
     * One scroll is not enough: items outside the viewport are placed from
     * estimated heights, and reaching an edge loads content that then measures
     * taller or shorter, moving the edge out from under us. Same shape as
     * scrollToKey — jump, wait for the measurements to settle, check, repeat.
     *
     * @param {'top' | 'bottom'} edge
     * @param {Object} [options]
     * @param {number} [options.settleMs=150] - Debounce time to wait for height stability
     * @param {number} [options.maxAttempts=4] - Max number of jump-settle-check cycles
     * @returns {Promise<boolean>} true if the container really sits at the edge
     */
    async function scrollToEdge(edge, options = {}) {
        if (suspended.value || disposed) return false
        const generation = lifecycleGeneration
        const { settleMs = 150, maxAttempts = 4 } = options
        const jump = edge === 'top' ? scrollToTop : scrollToBottom

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            if (generation !== lifecycleGeneration || suspended.value || disposed) return false
            jump({ behavior: 'auto' })
            await waitForHeightStability(settleMs)
            if (generation !== lifecycleGeneration || suspended.value || disposed) return false

            // Read the container rather than the cached state: the settle we just
            // awaited is exactly when both drift apart.
            const container = containerRef.value
            if (!container) return false

            if (edge === 'top') {
                if (container.scrollTop <= 1) return true
            } else {
                const maxScrollTop = container.scrollHeight - container.clientHeight
                if (container.scrollTop >= maxScrollTop - 1) return true
            }
        }

        // Out of attempts: land on the edge with whatever the current heights say.
        if (generation !== lifecycleGeneration || suspended.value || disposed) return false
        jump({ behavior: 'auto' })
        return false
    }

    /** Navigation ownership changes on input and explicit scrolls, not height compensation. */
    function getScrollRevision() {
        return userScrollSeq + explicitScrollSeq
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
     * Whether the scroller is currently at or near the bottom (within ~150 px).
     * Reads the reactive flag maintained by VirtualScroller's IntersectionObserver
     * on the bottom sentinel. This is the single source of truth for "near bottom".
     *
     * @returns {boolean}
     */
    function isAtBottom() {
        return isAtBottomRef.value
    }

    /**
     * Check if the scroller is currently at or near the top.
     *
     * @param {number} [threshold=150] - Pixel threshold for "at top" detection
     * @returns {boolean}
     */
    function isAtTop(threshold = 150) {
        return scrollTop.value <= threshold
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Viewport Height Update
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Update the viewport height. Called by the component's ResizeObserver.
     *
     * Handles three scenarios:
     * 1. Normal resize: update viewportHeight to trigger renderRange recomputation.
     * 2. Container hidden (height=0): auto-suspend the scroller to preserve scroll
     *    state before the browser's scroll event corrupts scrollTop.
     * 3. Container visible again (height>0 while suspended): auto-resume or complete
     *    a deferred KeepAlive resume.
     *
     * @param {number} height - The new viewport height
     */
    function updateViewportHeight(height) {
        if (disposed) return
        // While suspended, ignore all height changes — UNLESS we need to
        // complete a deferred resume (container was hidden when resume() was called)
        // or auto-resume (container was hidden by tab switch and is now visible again).
        if (suspended.value) {
            if (height > 0) {
                const container = containerRef.value
                if (!container) return

                if (pendingResume || autoSuspended) {
                    // Container is visible again — perform the deferred/auto resume.
                    performResume(container)
                }
            }
            return
        }

        // Auto-suspend: container just became hidden (e.g., wa-tab-panel display:none).
        // Save the scroll anchor NOW while scrollTop.value is still valid (the
        // ResizeObserver fires synchronously on layout change, before the browser's
        // scroll event resets scrollTop to 0 via handleScroll/RAF).
        // Only auto-suspend if the container WAS visible (viewportHeight > 0).
        // This avoids spurious suspend/resume cycles during initial mount of
        // hidden tab panels (Files, Git, Terminal) that start with height=0.
        if (height === 0 && viewportHeight.value > 0) {
            enterSuspension('automatic')
            return
        }

        // Pin-to-bottom is delegated to native browser scroll anchoring on the
        // sentinel element rendered at the end of the scroller (see
        // VirtualScroller.vue). When the viewport shrinks (e.g., textarea
        // growing), the browser will keep the sentinel visible if it was
        // visible before, achieving the same effect without a manual scrollTop
        // assignment.
        viewportHeight.value = height
    }

    /**
     * Suspend the scroller: freeze renderRange and save the scroll anchor.
     *
     * Called in two situations:
     * 1. KeepAlive deactivation (via onDeactivated) — before the browser detaches
     *    the DOM and resets scrollTop to 0.
     * 2. Auto-suspension (via updateViewportHeight) — when the container becomes
     *    hidden (e.g., wa-tab-panel switching to display:none). The ResizeObserver
     *    fires synchronously on layout change, BEFORE the browser's scroll event
     *    resets scrollTop, so we can still capture the correct scroll position.
     *
     * If already suspended (e.g., auto-suspended from tab switch), a subsequent
     * KeepAlive suspend clears the autoSuspended flag so that a manual resume()
     * is required instead of an automatic resume from updateViewportHeight().
     */
    function enterSuspension(origin) {
        if (disposed) return
        // A restore from the previous cycle is moot: this suspension re-captures the anchor.
        cancelResumeRetry()

        if (origin === 'manual') {
            pendingResume = false
            autoSuspended = false
        } else {
            autoSuspended = true
        }

        if (suspended.value) {
            return
        }

        const posArray = livePositions.value
        updateRenderRange(posArray)
        const currentScrollTop = scrollTop.value
        const currentViewportHeight = viewportHeight.value
        const capturedRange = renderRange.value
        const capturedTotalHeight = totalHeightForPositions(posArray)
        suspendedSnapshot = {
            positions: posArray,
            totalHeight: capturedTotalHeight,
            renderRange: capturedRange,
            visibleRange: visibleRangeFor(posArray, currentScrollTop, currentViewportHeight),
            spacerBeforeHeight: spacerBeforeFor(posArray, capturedRange),
            spacerAfterHeight: spacerAfterFor(posArray, capturedRange, capturedTotalHeight),
            scrollTop: currentScrollTop,
            viewportHeight: currentViewportHeight,
        }
        if (posArray.length > 0) {
            const anchorIndex = findIndexAtPosition(currentScrollTop, posArray)
            if (anchorIndex >= 0) {
                // The item at the top of the viewport, then the ones above it: an item that
                // gets folded into a collapsed group has its group head above it, so walking
                // upwards is what maximizes the chance of finding a survivor.
                const candidates = []
                for (let i = anchorIndex; i >= 0 && candidates.length < ANCHOR_CANDIDATES; i--) {
                    const item = posArray[i]
                    if (!item) continue
                    candidates.push({
                        index: i,
                        key: item.key,
                        offset: currentScrollTop - item.top,
                    })
                }
                if (candidates.length > 0) savedAnchors = candidates
            }
        }

        lifecycleGeneration++
        disposeOwnedWork()
        suspended.value = true
    }

    function suspend() {
        enterSuspension('manual')
    }

    /**
     * Resume the scroller (called after KeepAlive reattaches the DOM).
     *
     * Restores the scroll position from the saved anchor: finds the anchor
     * item in the (possibly recomputed) positions array and scrolls to
     * anchorTop + offset.
     *
     * If the container is not yet visible (e.g., inside a hidden wa-tab-panel),
     * the resume is deferred: the scroller stays suspended and savedAnchors are
     * preserved. The actual resume will happen when updateViewportHeight()
     * receives a positive height (triggered by the ResizeObserver when the
     * tab panel becomes visible).
     */
    function resume() {
        if (disposed) return
        if (!suspended.value) return

        const container = containerRef.value
        if (!container) return

        // If the container is hidden (e.g., inactive wa-tab-panel with display:none),
        // defer the resume until the container becomes visible. Setting scrollTop
        // on a hidden container is silently ignored by the browser, so we must wait.
        if (container.clientHeight === 0) {
            pendingResume = true
            return
        }

        // Container is visible — perform the actual resume
        performResume(container)
    }

    /**
     * Cancel a pending anchor re-application.
     */
    function cancelResumeRetry() {
        if (resumeRetryHandle !== null) {
            cancelAnimationFrame(resumeRetryHandle)
            resumeRetryHandle = null
        }
    }

    function disposeOwnedWork() {
        if (rafId !== null) {
            cancelAnimationFrame(rafId)
            rafId = null
        }
        cancelResumeRetry()
        endSmooth()
        if (programmaticResetTimer !== null) {
            clearTimeout(programmaticResetTimer)
            programmaticResetTimer = null
        }
        programmaticToken++
        isProgrammaticScroll = false
        for (const cancel of [...pendingHeightWaits]) cancel()
    }

    /**
     * Resolve saved anchor candidates against the current positions.
     *
     * Keys win over indices: a surviving key restores the exact position (every candidate
     * carries its own offset from the same origin), whereas an index only means anything as
     * long as the list did not shift. The index of the primary candidate is the fallback, and
     * the end of the list the last resort — reached when the items array was recomputed
     * shorter than the saved index, which otherwise restores nothing at all and leaves the
     * scroller at the top.
     *
     * @param {Array<{ index: number, key: any, offset: number }>} anchors - Candidates, primary first
     * @returns {{ item: Object, offset: number } | null}
     */
    function resolveAnchors(anchors) {
        const posArray = positions.value
        if (posArray.length === 0) return null

        for (const anchor of anchors) {
            const item = posArray.find(p => p.key === anchor.key)
            if (item) return { item, offset: anchor.offset }
        }

        const primary = anchors[0]
        if (primary.index < posArray.length) {
            return { item: posArray[primary.index], offset: primary.offset }
        }
        return { item: posArray[posArray.length - 1], offset: primary.offset }
    }

    /**
     * Write the position of the first resolvable anchor candidate to the container.
     *
     * `scrollTop.value` takes the INTENDED position rather than whatever the container
     * ended up at: the render range derives from it, so the next render produces the
     * right window — and therefore a tall enough DOM — even when the write was clamped.
     * The caller realigns the reactive state on the DOM once the restore is settled.
     *
     * @param {HTMLElement} container - The scroller container element
     * @param {Array<{ index: number, key: any, offset: number }>} anchors - Candidates, primary first
     * @returns {boolean | null} true when the container accepted the position, false when
     *   the browser clamped it, null when no candidate could be resolved.
     */
    function writeAnchor(container, anchors) {
        const resolved = resolveAnchors(anchors)
        if (!resolved) return null

        const targetScrollTop = resolved.item.top + resolved.offset
        if (!Number.isFinite(targetScrollTop)) return null

        const maxScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)
        const wanted = Math.max(0, Math.min(targetScrollTop, maxScrollTop))

        endSmooth()
        container.scrollTop = wanted
        scrollTop.value = wanted

        return Math.abs(container.scrollTop - wanted) <= 1
    }

    /**
     * Restore the saved anchors, re-applying them over the next frames until the DOM accepts.
     *
     * A single write is not enough. performResume runs before Vue re-renders the scroller,
     * so the container's scrollHeight is still whatever the previous render left — from
     * slightly short (stale item heights) to fully collapsed (scrollHeight === clientHeight
     * right after a KeepAlive reattach). The browser silently clamps a scrollTop write to the
     * current maximum, 0 in the collapsed case: that is how a session comes back scrolled to
     * the top. The anchor is therefore released only once the container really sits where we
     * asked — or once the attempts run out.
     *
     * @param {HTMLElement} container - The scroller container element
     * @param {number} [attempt=0] - Current attempt (0-based)
     */
    function restoreSavedAnchor(container, attempt = 0) {
        cancelResumeRetry()
        if (!savedAnchors) return

        const restored = writeAnchor(container, savedAnchors)
        if (restored === true || restored === null) {
            savedAnchors = null
            return
        }

        if (attempt + 1 >= RESUME_RETRY_FRAMES) {
            // Give up on the DOM, but keep the INTENDED position in the reactive state:
            // aligning it on the container here would tell the render range the scroller
            // sits at whatever the clamp left (0 in the worst case), which re-renders the
            // top of the list and makes the loss permanent.
            savedAnchors = null
            return
        }

        // Retry on the next frame, by which point Vue has flushed the render and the
        // spacers describe the real total height.
        const seqAtSchedule = explicitScrollSeq
        const generation = lifecycleGeneration
        const handle = requestAnimationFrame(() => {
            if (generation !== lifecycleGeneration || resumeRetryHandle !== handle || disposed) return
            resumeRetryHandle = null
            const el = containerRef.value
            if (!el) return
            // A new suspend/resume cycle owns the anchor now — leave it to it.
            if (suspended.value) return
            // An explicit scroll landed in between (auto-scroll to bottom, scrollToKey, …):
            // that is the newer intent, drop the restore.
            if (explicitScrollSeq !== seqAtSchedule) {
                savedAnchors = null
                return
            }
            restoreSavedAnchor(el, attempt + 1)
        })
        resumeRetryHandle = handle
    }

    /**
     * Perform the actual resume: unsuspend, update viewport, restore scroll.
     * Called either directly from resume() when the container is visible,
     * or deferred from updateViewportHeight() when the container becomes visible.
     *
     * @param {HTMLElement} container - The scroller container element
     */
    function performResume(container) {
        pendingResume = false
        autoSuspended = false
        flushDeferredMaintenance()
        const latestPositions = livePositions.value
        suspended.value = false

        // Update viewportHeight from the now-visible container
        viewportHeight.value = container.clientHeight
        updateRenderRange(latestPositions)

        restoreSavedAnchor(container)
        suspendedSnapshot = null
    }

    /**
     * Get the current scroll anchor (the first visible item and pixel offset into it).
     * Returns null if there are no items.
     *
     * This is used by parent components (like SessionItemsList) to save scroll state
     * before the VirtualScroller might be destroyed and recreated (e.g., by v-if/v-else-if
     * conditions inside a KeepAlive boundary).
     *
     * @returns {{ index: number, key: any, offset: number } | null}
     */
    function getScrollAnchor() {
        const posArray = positions.value
        if (posArray.length === 0) return null

        const currentScrollTop = suspended.value ? suspendedSnapshot.scrollTop : scrollTop.value
        const anchorIndex = findIndexAtPosition(currentScrollTop, posArray)
        const anchorItem = posArray[anchorIndex]
        if (!anchorItem) return null

        return {
            index: anchorIndex,
            key: anchorItem.key,
            offset: currentScrollTop - anchorItem.top,
        }
    }

    /**
     * Scroll to a previously saved anchor position.
     * The anchor is resolved by key first, then by index as fallback.
     *
     * This is used by parent components to restore scroll state after the
     * VirtualScroller has been recreated.
     *
     * @param {{ index: number, key: any, offset: number }} anchor - The anchor to restore
     */
    function scrollToAnchor(anchor) {
        if (suspended.value || disposed) return
        if (!anchor) return

        const container = containerRef.value
        if (!container) return

        explicitScrollSeq++

        const posArray = positions.value

        // Try to find the anchor by key first (more reliable if items shifted)
        let anchorItem = posArray.find(p => p.key === anchor.key)

        // Fallback to index if key not found (item may have been removed)
        if (!anchorItem && anchor.index < posArray.length) {
            anchorItem = posArray[anchor.index]
        }

        if (anchorItem) {
            const targetScrollTop = anchorItem.top + anchor.offset
            const maxScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)
            const clampedScrollTop = Math.max(0, Math.min(targetScrollTop, maxScrollTop))

            endSmooth()
            container.scrollTop = clampedScrollTop
            scrollTop.value = clampedScrollTop
        }
    }

    /**
     * Write an absolute scroll position, clamped to the valid range.
     *
     * No-op when the container already sits within 0.5px of the target, so a
     * caller can "restore the position if it moved" without special-casing the
     * unmoved path (e.g. the user was reading far above a collapsed region and
     * the browser never clamped scrollTop).
     *
     * @param {number} value - Target scrollTop in pixels
     */
    function setScrollTop(value) {
        if (suspended.value || disposed) return
        const container = containerRef.value
        if (!container || !Number.isFinite(value)) return

        const maxScrollTop = Math.max(0, totalHeight.value - viewportHeight.value)
        const clamped = Math.max(0, Math.min(value, maxScrollTop))
        if (Math.abs(container.scrollTop - clamped) <= 0.5) return

        explicitScrollSeq++
        endSmooth()
        container.scrollTop = clamped
        scrollTop.value = clamped
    }

    /**
     * Scroll to the item with the given key, waiting for heights to stabilize.
     *
     * Algorithm ("jump, settle, correct"):
     * 1. Find the index of the target item by key
     * 2. scrollToIndex (positions may be inaccurate for distant items)
     * 3. Wait for ResizeObserver measurements to settle (no height changes for settleMs)
     * 4. Check if the target item is actually visible in the viewport
     * 5. If not, scrollToIndex again (positions are now more accurate) and re-settle
     * 6. Repeat up to maxAttempts times
     *
     * The caller is responsible for ensuring the item's content is loaded and the item
     * exists in the items array before calling this method.
     *
     * With `align: 'nearest'` the check is stricter: an attempt succeeds when the `nearest`
     * target recomputed from the current positions equals the scroll within 1px (the first
     * jump uses estimated heights; once measured, the anchor correction lets the target
     * drift out of the zone). And any newer intent ends the loop after a settle, without
     * checking or re-jumping: a user scroll input on the container, an explicit scroll, a
     * newer `nearest` reveal, or `isCurrent()` turning false. With `allowSmooth`, an attempt
     * that started a smooth scroll awaits its end before the settle and the check; one that
     * did not write while a smooth scroll runs awaits that scroll's end too.
     *
     * @param {*} key - The item key (e.g., lineNum) to scroll to
     * @param {Object} [options]
     * @param {'start' | 'center' | 'end' | 'nearest'} [options.align='center'] - Where to position the item
     * @param {number} [options.settleMs=150] - Debounce time to wait for height stability
     * @param {number} [options.maxAttempts=3] - Max number of jump-settle-correct cycles
     * @param {number} [options.offset=0] - Pixels of room to leave before the item (not for `nearest`)
     * @param {number} [options.marginTop=0] - `nearest` only: the top band, px (may be negative)
     * @param {number} [options.marginBottom=0] - `nearest` only: the bottom band, px
     * @param {Function} [options.isCurrent] - `nearest` only: () => boolean, false once the
     *   reveal no longer matters (the loop then returns)
     * @param {boolean} [options.allowSmooth=false] - `nearest` only: see scrollToIndex
     * @param {Function} [options.onHeightChange] - Callback invoked on each height change (for abort detection)
     * @returns {Promise<boolean>} true if the item is visible in the viewport (`nearest`: in
     *   its zone) after scrolling
     */
    async function scrollToKey(key, options = {}) {
        if (suspended.value || disposed) return false
        const generation = lifecycleGeneration
        const {
            align = 'center',
            settleMs = 150,
            maxAttempts = 3,
            offset = 0,
            marginTop = 0,
            marginBottom = 0,
            isCurrent = null,
            allowSmooth = false,
            onHeightChange = null,
        } = options
        const nearest = align === 'nearest'
        const ownReveal = nearest ? ++revealSeq : 0

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            if (generation !== lifecycleGeneration || suspended.value || disposed) return false
            // Find the item index by key
            const index = items.value.findIndex(item => itemKey(item) === key)
            if (index === -1) return false

            // Jump to the item
            const smoothDone = scrollToIndex(index, { align, behavior: 'auto', offset, marginTop, marginBottom, allowSmooth })
            // Read right after the jump (or no-jump): its own write is in the baseline.
            const userSeqAtJump = userScrollSeq
            const explicitSeqAtJump = explicitScrollSeq

            // A smooth scroll: never check a position still in flight. Its own, or (no write)
            // the one already running — a plain wait: its hold and token stay with it.
            if (nearest && smoothDone) await smoothDone
            else if (nearest && smoothTo) await smoothTo.done
            if (generation !== lifecycleGeneration || suspended.value || disposed) return false

            // Wait for heights to settle (no ResizeObserver changes for settleMs)
            await waitForHeightStability(settleMs, onHeightChange)
            if (generation !== lifecycleGeneration || suspended.value || disposed) return false

            // A newer intent: the user scrolled, an explicit scroll or a newer reveal
            // happened, or the target is no longer wanted. Leave the list where it is.
            if (nearest && (
                userScrollSeq !== userSeqAtJump
                || explicitScrollSeq !== explicitSeqAtJump
                || revealSeq !== ownReveal
                || (isCurrent && !isCurrent())
            )) {
                return false
            }

            // Check if the target item is now visible in the viewport
            const posArray = positions.value
            const targetPos = posArray[index]
            if (!targetPos) return false

            if (nearest) {
                const container = containerRef.value
                if (!container) return false
                // The list may have been reordered during the settle: check the row where
                // it is now, not the row now at its old index.
                const current = items.value.findIndex(item => itemKey(item) === key)
                if (current === -1) return false
                const currentPos = posArray[current]
                if (!currentPos) return false
                if (Math.abs(nearestTarget(container, currentPos, marginTop, marginBottom) - scrollTop.value) <= 1) return true
                continue
            }

            const vpTop = scrollTop.value
            const vpBottom = vpTop + viewportHeight.value
            const itemTop = targetPos.top
            const itemBottom = itemTop + targetPos.height

            // Item is visible if it overlaps significantly with the viewport
            const isVisible = itemTop < vpBottom - 10 && itemBottom > vpTop + 10
            if (isVisible) return true

            // Not visible yet — next iteration will re-scroll with updated positions
        }

        return false
    }

    /**
     * Wait for item height measurements to stabilize.
     * Resolves when no height changes occur for the given duration.
     *
     * Works by temporarily hooking into heightCache changes via a MutationObserver-like
     * pattern: we poll the heightCache's size and watch for changes. Since heightCache
     * is a reactive Map modified by batchUpdateItemHeights, we use a watchEffect that
     * re-triggers on any heightCache change.
     *
     * @param {number} ms - Debounce time: resolve after this many ms with no height changes
     * @param {Function} [onHeightChange] - Optional callback on each height change
     * @returns {Promise<void>}
     */
    function waitForHeightStability(ms, onHeightChange = null) {
        return new Promise(resolve => {
            let timeoutId = null
            const generation = lifecycleGeneration
            let settled = false
            let stopWatch = null

            const finish = () => {
                if (settled) return
                settled = true
                if (timeoutId !== null) clearTimeout(timeoutId)
                timeoutId = null
                if (stopWatch) stopWatch()
                pendingHeightWaits.delete(finish)
                resolve()
            }
            pendingHeightWaits.add(finish)

            const startTimer = () => {
                if (generation !== lifecycleGeneration || settled || suspended.value || disposed) return
                if (timeoutId !== null) clearTimeout(timeoutId)
                const handle = setTimeout(() => {
                    if (generation !== lifecycleGeneration || settled || timeoutId !== handle) return
                    finish()
                }, ms)
                timeoutId = handle
            }

            // Watch the heightCache for any change (it's a reactive Map)
            stopWatch = watch(
                () => {
                    // Access heightCache.size to create a reactive dependency.
                    // Also access each entry to detect value changes (not just additions/removals).
                    let hash = heightCache.size
                    for (const h of heightCache.values()) {
                        hash += h
                    }
                    return hash
                },
                () => {
                    if (generation !== lifecycleGeneration || settled || suspended.value || disposed) return
                    if (onHeightChange) onHeightChange()
                    if (generation !== lifecycleGeneration || settled || suspended.value || disposed) return
                    startTimer()
                },
                { flush: 'post' }
            )

            // Start the initial timer (if no height changes happen, resolve after ms)
            startTimer()
        })
    }

    /**
     * Sync the scroll position from the container (useful after mount or resize).
     */
    function syncScrollPosition() {
        if (suspended.value || disposed) return
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
        lifecycleGeneration++
        disposed = true
        disposeOwnedWork()
        suspendedSnapshot = null
        savedAnchors = null
        latestReplacement = null
        lastCleanedItems = null
        // containerRef is already null here: the listeners are removed from listenedEl.
        if (listenedEl) removeUserScrollListeners(listenedEl)
        listenedEl = null
    })

    // ═══════════════════════════════════════════════════════════════════════════
    // Watch for items array replacement
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * When the items array reference changes completely, we need to clean up
     * height cache entries for items that no longer exist.
     * However, we preserve heights for items that still exist (same key).
     */
    function cleanupHeightCache(currentItems) {
        const currentKeys = new Set(currentItems.map(item => itemKey(item)))
        for (const [key, height] of heightCache.entries()) {
            if (!currentKeys.has(key) || (pendingZeroHeightInvalidation && height === 0)) {
                deleteCachedHeight(key)
            }
        }
        lastCleanedItems = currentItems
        pendingZeroHeightInvalidation = false
    }

    function flushDeferredMaintenance() {
        if (latestReplacement !== lastCleanedItems) {
            cleanupHeightCache(latestReplacement)
        } else if (pendingZeroHeightInvalidation) {
            for (const [key, height] of heightCache.entries()) {
                if (height === 0) deleteCachedHeight(key)
            }
            pendingZeroHeightInvalidation = false
        }
    }

    watch(() => items.value, (currentItems) => {
        latestReplacement = currentItems
    }, { flush: 'sync' })

    watch(() => items.value, () => {
        if (!suspended.value) flushDeferredMaintenance()
    }, { flush: 'post' })

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
        suspended,

        // Height management
        updateItemHeight,
        batchUpdateItemHeights,
        removeItemHeight,
        clearHeightCache,
        getItemHeight,
        invalidateZeroHeights,

        // Event handler
        handleScroll,

        // Navigation
        scrollToIndex,
        scrollToKey,
        scrollToTop,
        scrollToBottom,
        scrollToEdge,
        getScrollState,
        getScrollRevision,
        isAtBottom,
        isAtTop,
        // Reactive "near bottom" flag — written by VirtualScroller's
        // IntersectionObserver, read by isAtBottom() and consumers.
        isAtBottomRef,

        // Prevent implicit "stay at bottom" on resize
        preventAutoScrollToBottom,

        // Viewport management
        updateViewportHeight,
        syncScrollPosition,

        // KeepAlive support
        suspend,
        resume,
        getScrollAnchor,
        scrollToAnchor,

        // Streaming-item swap support
        setScrollTop,
        seedItemHeight,
    }
}
