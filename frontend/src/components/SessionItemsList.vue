<script setup>
import { computed, watch, ref, nextTick, onUnmounted } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT } from '../constants'
import VirtualScroller from './VirtualScroller.vue'
import SessionItem from './SessionItem.vue'
import FetchErrorPanel from './FetchErrorPanel.vue'
import GroupToggle from './GroupToggle.vue'
import MessageInput from './MessageInput.vue'
import ProcessIndicator from './ProcessIndicator.vue'

// All states should animate for the bottom process indicator
const BOTTOM_INDICATOR_ANIMATE_STATES = ['assistant_turn', 'user_turn', 'dead']

// Duration to show temporary indicators (user_turn, dead) in milliseconds
const TEMPORARY_INDICATOR_DURATION = 10000

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    projectId: {
        type: String,
        required: true
    },
    /**
     * Whether to track scroll direction for auto-hide header feature.
     * When false, scroll direction detection is skipped for performance.
     */
    trackScrollDirection: {
        type: Boolean,
        default: false
    },
    /**
     * Whether the footer (message input) is hidden (for auto-hide on small viewports).
     * When true, the footer slides down and out of view.
     * The ProcessIndicator remains visible as it's positioned above the footer.
     */
    footerHidden: {
        type: Boolean,
        default: false
    }
})

const store = useDataStore()

const emit = defineEmits(['needs-title', 'scroll-direction'])

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Flag to track if we're currently auto-scrolling to bottom
// Used to handle new items arriving during the scroll retry loop
const isAutoScrollingToBottom = ref(false)

// Flag to track if we're in the initial scroll phase (scroller hidden until positioned)
// This prevents visible jumping when the scroller first appears at top then scrolls to bottom
const isInitialScrolling = ref(false)

// Callback to resolve when scroll stabilizes (set by scrollToBottomUntilStable)
let onStabilizedCallback = null

// Timeout ID for the stability debounce
let stabilityTimeoutId = null

// Promise that resolves when current scroll-to-bottom operation completes
// Used to prevent concurrent calls to scrollToBottomUntilStable
let scrollToBottomPromise = null

// Delay in ms to wait for no more resize events before considering stable
const STABILITY_DEBOUNCE_MS = 100

// Threshold in pixels for "near bottom" detection for auto-scroll
const AUTO_SCROLL_THRESHOLD = 150

// Buffer: load N items before/after visible range
const LOAD_BUFFER = 50

// Debounce delay for scroll-triggered loading (ms)
const LOAD_DEBOUNCE_MS = 150

// Minimum item size for the virtual scroller (in pixels)
const MIN_ITEM_SIZE = 50

// Track last emitted direction to avoid duplicate emissions
let lastEmittedDirection = null

// Track pending range to load (accumulated during debounce)
const pendingLoadRange = ref(null)

// Session data
const session = computed(() => store.getSession(props.sessionId))

// Session items (raw, with metadata + content)
const items = computed(() => store.getSessionItems(props.sessionId))

// Visual items (filtered by display mode and expanded groups)
const visualItems = computed(() => store.getSessionVisualItems(props.sessionId))

// Check if session computation is pending
const isComputePending = computed(() => {
    const sess = store.getSession(props.sessionId)
    return sess && sess.compute_version_up_to_date === false
})

// Loading and error states
const isLoading = computed(() => store.areSessionItemsLoading(props.sessionId))
const hasError = computed(() => store.didSessionItemsFailToLoad(props.sessionId))

// Process state for this session (starting, assistant_turn, user_turn, dead)
const processState = computed(() => store.getProcessState(props.sessionId))

// Timer for temporary indicator display (user_turn, dead)
let temporaryIndicatorTimer = null
const showTemporaryIndicator = ref(false)

// Computed: should we show the bottom process indicator?
// - Always show for starting/assistant_turn
// - Show for user_turn/dead only for TEMPORARY_INDICATOR_DURATION seconds
const shouldShowProcessIndicator = computed(() => {
    if (!processState.value) return false
    const state = processState.value.state
    if (state === 'starting' || state === 'assistant_turn') return true
    if ((state === 'user_turn' || state === 'dead') && showTemporaryIndicator.value) return true
    return false
})

// Watch process state changes to manage temporary indicator
// Only show user_turn/dead when the state actually CHANGES (not on initial mount)
watch(processState, (newState, oldState) => {
    // Clear any existing timer
    if (temporaryIndicatorTimer) {
        clearTimeout(temporaryIndicatorTimer)
        temporaryIndicatorTimer = null
    }

    if (!newState) {
        showTemporaryIndicator.value = false
        return
    }

    const state = newState.state
    const oldStateValue = oldState?.state

    if (state === 'user_turn' || state === 'dead') {
        // Only show if state actually changed (not on initial mount when already in this state)
        if (oldState && oldStateValue !== state) {
            showTemporaryIndicator.value = true
            temporaryIndicatorTimer = setTimeout(() => {
                showTemporaryIndicator.value = false
                temporaryIndicatorTimer = null
            }, TEMPORARY_INDICATOR_DURATION)
        } else {
            // Initial mount or same state - don't show temporary indicator
            showTemporaryIndicator.value = false
        }
    } else {
        showTemporaryIndicator.value = false
    }
}, { immediate: true })

// Cleanup timers on unmount
onUnmounted(() => {
    if (temporaryIndicatorTimer) {
        clearTimeout(temporaryIndicatorTimer)
        temporaryIndicatorTimer = null
    }
    if (stabilityTimeoutId) {
        clearTimeout(stabilityTimeoutId)
        stabilityTimeoutId = null
    }
})

// Build base URL for API calls (handles subagent case)
const apiBaseUrl = computed(() => {
    if (props.parentSessionId) {
        return `/api/projects/${props.projectId}/sessions/${props.parentSessionId}/subagent/${props.sessionId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

/**
 * Load subagent session details from API and add to store.
 * Called when opening a subagent tab if the session is not already in the store.
 * This handles the case of direct URL access before WebSocket has delivered the session.
 */
async function loadSubagentSession() {
    try {
        const url = `${apiBaseUrl.value}/`
        const response = await fetch(url)
        if (!response.ok) {
            console.error('Failed to load subagent session:', response.status)
            return null
        }
        const sessionData = await response.json()
        store.addSession(sessionData)
        return sessionData
    } catch (error) {
        console.error('Failed to load subagent session:', error)
        return null
    }
}

/**
 * Load session data: metadata (all items) + initial content (last N items).
 * Fetches both in parallel for faster loading.
 *
 * We only load the last N items initially since sessions open at the bottom.
 * Items at the top will be lazy-loaded when the user scrolls up.
 */
async function loadSessionData(lastLine) {
    const sId = props.sessionId

    // Mark as fetched first (before async operations to avoid race conditions)
    if (!store.localState.sessions[sId]) {
        store.localState.sessions[sId] = {}
    }
    store.localState.sessions[sId].itemsFetched = true
    store.localState.sessions[sId].itemsLoading = true

    try {
        // Build range for initial content: only load last N items
        // Sessions open at the bottom, so we don't need the first items initially
        const ranges = []
        if (lastLine <= INITIAL_ITEMS_COUNT) {
            // Small session: load everything
            ranges.push([1, lastLine])
        } else {
            // Large session: load only last N items
            ranges.push([lastLine - INITIAL_ITEMS_COUNT + 1, lastLine])
        }

        // Build range params for items endpoint
        const params = new URLSearchParams()
        for (const [min, max] of ranges) {
            params.append('range', `${min}:${max}`)
        }

        // Fetch BOTH in parallel
        const [metadataResult, itemsResult] = await Promise.all([
            store.loadSessionMetadata(props.projectId, sId, props.parentSessionId),
            fetch(`${apiBaseUrl.value}/items/?${params}`)
                .then(res => res.ok ? res.json() : null)
                .catch(() => null)
        ])

        // Check for errors
        if (!metadataResult || !itemsResult) {
            store.localState.sessions[sId].itemsLoadingError = true
            return
        }

        // Process results
        store.initSessionItemsFromMetadata(sId, metadataResult)
        store.updateSessionItemsContent(sId, itemsResult)

        // Success
        store.localState.sessions[sId].itemsLoadingError = false

    } catch (error) {
        console.error('Failed to load session data:', error)
        store.localState.sessions[sId].itemsLoadingError = true
    } finally {
        store.localState.sessions[sId].itemsLoading = false
    }
}

// Load session data when session changes
watch([() => props.sessionId, session], async ([newSessionId, newSession]) => {
    if (!newSessionId) return

    // If session is not in store and this is a subagent, load it first
    // (handles direct URL access before WebSocket delivers the session)
    if (!newSession && props.parentSessionId) {
        const loadedSession = await loadSubagentSession()
        if (!loadedSession) return
        // The watch will re-trigger with the loaded session
        return
    }

    if (!newSession) return

    // Don't load data for draft sessions (they have no items yet)
    if (newSession.draft) {
        return
    }

    // Don't load if computation is pending
    if (newSession.compute_version_up_to_date === false) {
        return
    }

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    const isFirstLoad = !store.areSessionItemsFetched(newSessionId)
    if (isFirstLoad) {
        await loadSessionData(lastLine)
    }

    // Always scroll to end of session (with retry until stable)
    // Mark as initial scroll to hide scroller until positioned (only on first load)
    // When returning to an already-loaded session, items are already sized so no resize events will fire
    await nextTick()
    scrollToBottomUntilStable({ isInitial: isFirstLoad })
}, { immediate: true })

// Retry loading session data after error
async function handleRetry() {
    if (!session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    const sId = props.sessionId

    // Reset fetched state to allow reload
    if (store.localState.sessions[sId]) {
        store.localState.sessions[sId].itemsFetched = false
    }
    // Clear existing items
    delete store.sessionItems[sId]
    delete store.sessionVisualItems[sId]

    await loadSessionData(lastLine)

    // Scroll to bottom after successful load
    // Mark as initial scroll to hide scroller until positioned
    await nextTick()
    scrollToBottomUntilStable({ isInitial: true })
}

/**
 * Called when session becomes ready (compute completed).
 * Triggered by watching compute_version_up_to_date transition.
 */
async function onComputeCompleted() {
    if (!session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    await loadSessionData(lastLine)

    // Mark as initial scroll to hide scroller until positioned
    await nextTick()
    scrollToBottomUntilStable({ isInitial: true })
}

// Watch for session compute completion
watch(() => session.value?.compute_version_up_to_date, (newValue, oldValue) => {
    // Transition from false (or undefined) to true
    if (newValue === true && oldValue !== true) {
        onComputeCompleted()
    }
})

/**
 * Watch for new items being added to the session.
 * Auto-scrolls to bottom if user was near bottom (or already auto-scrolling).
 *
 * Uses a pre-flush watcher to capture "wasNearBottom" state BEFORE Vue updates the DOM,
 * then scrolls after the DOM update if needed.
 */
watch(
    () => visualItems.value?.length,
    async (newLength, oldLength) => {
        // Only handle additions (not initial load or removals)
        if (!newLength || !oldLength || newLength <= oldLength) return

        const scroller = scrollerRef.value
        if (!scroller) return

        // Check if we should auto-scroll:
        // 1. We're currently in the middle of an auto-scroll operation, OR
        // 2. User was near the bottom before the new items arrived
        const shouldAutoScroll = isAutoScrollingToBottom.value || scroller.isAtBottom(AUTO_SCROLL_THRESHOLD)

        if (shouldAutoScroll) {
            // Wait for Vue to render the new items
            await nextTick()
            scrollToBottomUntilStable()
        }
    }
)

/**
 * Handle item resize events from VirtualScroller.
 * Used to detect when items have finished resizing for scroll stability detection.
 */
function onItemResized() {
    // If we're waiting for stability, reset the debounce timer
    if (onStabilizedCallback) {
        // Clear existing timeout
        if (stabilityTimeoutId) {
            clearTimeout(stabilityTimeoutId)
        }

        // Set new timeout - if no more resizes happen within STABILITY_DEBOUNCE_MS,
        // we consider it stable
        // Note: The actual scrolling to bottom is handled by stickToBottom mode in the composable
        stabilityTimeoutId = setTimeout(() => {
            stabilityTimeoutId = null
            if (onStabilizedCallback) {
                const callback = onStabilizedCallback
                onStabilizedCallback = null
                callback()
            }
        }, STABILITY_DEBOUNCE_MS)
    }
}

/**
 * Scroll to bottom and wait until the scroll position stabilizes.
 *
 * Algorithm:
 * 1. Enable "stick to bottom" mode - any height changes will automatically scroll to bottom
 * 2. Scroll to bottom immediately
 * 3. Wait for item-resized events to stop (debounced)
 * 4. Disable "stick to bottom" mode
 *
 * The key insight: instead of trying to scroll after each resize event,
 * we let the composable's stickToBottom mode handle it automatically.
 * This ensures we stay at the bottom even as items resize.
 *
 * Sets isAutoScrollingToBottom flag during the operation so that if new
 * items arrive while scrolling, we know to continue scrolling.
 *
 * @param {Object} [options] - Options for the scroll operation
 * @param {boolean} [options.isInitial=false] - Whether this is the initial scroll after session load
 *   When true, the scroller is kept invisible until scroll is stable to prevent visual jumping
 */
async function scrollToBottomUntilStable(options = {}) {
    const { isInitial = false } = options
    const scroller = scrollerRef.value
    if (!scroller) return

    // If a scroll operation is already in progress, wait for it to complete
    // This prevents concurrent calls from interfering with each other
    if (scrollToBottomPromise) {
        await scrollToBottomPromise
        // After waiting, the previous operation already scrolled to bottom,
        // so we can return early unless this is an initial scroll that needs visibility handling
        if (!isInitial) return
    }

    // Create a new promise for this operation and store it
    let resolveScrollPromise
    scrollToBottomPromise = new Promise(resolve => {
        resolveScrollPromise = resolve
    })

    try {
        isAutoScrollingToBottom.value = true

        // For initial scroll, hide the scroller until we're positioned
        if (isInitial) {
            isInitialScrolling.value = true
        }

        // Enable stick to bottom mode - the composable will automatically
        // scroll to bottom whenever heights change
        scroller.enableStickToBottom()

        // Scroll to bottom immediately
        scroller.scrollToBottom({ behavior: 'auto' })

        // Wait for stability: no more resize events for STABILITY_DEBOUNCE_MS
        await new Promise(resolve => {
            onStabilizedCallback = resolve

            // IMPORTANT: Don't start the timer immediately!
            // We need to wait for Vue to render and ResizeObserver to fire.
            // Use requestAnimationFrame + setTimeout to ensure we start AFTER
            // the initial batch of resize events has a chance to arrive.
            requestAnimationFrame(() => {
                setTimeout(() => {
                    // Start the stability timer only after giving resize events a chance to fire
                    // If no resize events have arrived by now and reset this timer,
                    // we're already stable
                    if (!stabilityTimeoutId) {
                        stabilityTimeoutId = setTimeout(() => {
                            stabilityTimeoutId = null
                            if (onStabilizedCallback) {
                                const callback = onStabilizedCallback
                                onStabilizedCallback = null
                                callback()
                            }
                        }, STABILITY_DEBOUNCE_MS)
                    }
                }, 0)
            })
        })

        // Disable stick to bottom mode now that we're stable
        scroller.disableStickToBottom()

        // Final scroll to bottom to ensure we're at the very bottom
        scroller.scrollToBottom({ behavior: 'auto' })

        isAutoScrollingToBottom.value = false

        // Reveal the scroller now that we're positioned at the bottom
        if (isInitial) {
            isInitialScrolling.value = false
        }
    } finally {
        // Clear the promise and resolve it so any waiters can proceed
        scrollToBottomPromise = null
        resolveScrollPromise()
    }
}

/**
 * Convert an array of line numbers to ranges for API calls.
 * e.g., [1, 2, 3, 5, 6, 10] -> [[1, 3], [5, 6], [10, 10]]
 */
function lineNumsToRanges(lineNums) {
    if (lineNums.length === 0) return []

    const sorted = [...lineNums].sort((a, b) => a - b)
    const ranges = []
    let rangeStart = sorted[0]
    let rangeEnd = sorted[0]

    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === rangeEnd + 1) {
            rangeEnd = sorted[i]
        } else {
            ranges.push([rangeStart, rangeEnd])
            rangeStart = sorted[i]
            rangeEnd = sorted[i]
        }
    }

    ranges.push([rangeStart, rangeEnd])
    return ranges
}

/**
 * Execute the pending load - called after debounce.
 * Loads specific line numbers instead of ranges of indices.
 */
async function executePendingLoad() {
    const range = pendingLoadRange.value
    if (!range || !range.lineNums || range.lineNums.length === 0) return

    pendingLoadRange.value = null

    const ranges = lineNumsToRanges(range.lineNums)

    if (ranges.length > 0) {
        const scroller = scrollerRef.value
        const wasAtBottom = scroller?.isAtBottom?.() ?? false

        await store.loadSessionItemsRanges(
            props.projectId,
            props.sessionId,
            ranges,
            props.parentSessionId
        )

        if (scroller && wasAtBottom) {
            const state = scroller.getScrollState()
            const distanceFromBottom = state.scrollHeight - state.scrollTop - state.clientHeight
            if (distanceFromBottom > 5) {
                await nextTick()
                scrollToBottomUntilStable()
            }
        }
    }
}

const debouncedLoad = useDebounceFn(executePendingLoad, LOAD_DEBOUNCE_MS)

/**
 * Handle scroller update event - triggers lazy loading for visible items.
 * Works with visualItems (filtered list) and maps to actual line numbers.
 *
 * @param {{ startIndex: number, endIndex: number, visibleStartIndex: number, visibleEndIndex: number }} payload
 *   - startIndex/endIndex: indices of items being rendered (with buffer)
 *   - visibleStartIndex/visibleEndIndex: indices of items actually visible (no buffer)
 */
function onScrollerUpdate({ startIndex, endIndex, visibleStartIndex, visibleEndIndex }) {
    const visItems = visualItems.value
    if (!visItems || visItems.length === 0) return

    // Add buffer around visible range
    const bufferedStart = Math.max(0, visibleStartIndex - LOAD_BUFFER)
    const bufferedEnd = Math.min(visItems.length - 1, visibleEndIndex + LOAD_BUFFER)

    // Collect line numbers that need content loading
    const lineNumsToLoad = []
    for (let i = bufferedStart; i <= bufferedEnd; i++) {
        const visualItem = visItems[i]
        if (visualItem && !visualItem.content) {
            lineNumsToLoad.push(visualItem.lineNum)
        }
    }

    if (lineNumsToLoad.length > 0) {
        pendingLoadRange.value = { lineNums: lineNumsToLoad }
        debouncedLoad()
    }
}

/**
 * Toggle a group's expanded state.
 * Called when clicking on a GroupToggle component.
 */
function toggleGroup(groupHeadLineNum) {
    store.toggleExpandedGroup(props.sessionId, groupHeadLineNum)
}

/**
 * Handle physical scroll input (wheel or touch).
 * Only reacts to user-initiated scrolls, not programmatic scrolls.
 * Emits 'scroll-direction' event with 'up' or 'down' when direction changes.
 * @param {'up' | 'down'} direction - The scroll direction
 */
function onPhysicalScroll(direction) {
    // Skip direction detection if not needed (performance optimization)
    if (!props.trackScrollDirection) return

    // Only emit if direction actually changed
    if (direction !== lastEmittedDirection) {
        lastEmittedDirection = direction
        emit('scroll-direction', direction)
    }
}

/**
 * Handle wheel events for scroll direction detection.
 * @param {WheelEvent} event
 */
function onWheel(event) {
    if (!props.trackScrollDirection) return
    // deltaY > 0 means scrolling down, < 0 means scrolling up
    if (Math.abs(event.deltaY) > 0) {
        onPhysicalScroll(event.deltaY > 0 ? 'down' : 'up')
    }
}

// Track touch position for direction detection
let lastTouchY = 0

/**
 * Handle touch start for scroll direction detection.
 * @param {TouchEvent} event
 */
function onTouchStart(event) {
    if (!props.trackScrollDirection) return
    if (event.touches.length > 0) {
        lastTouchY = event.touches[0].clientY
    }
}

// Minimum touch movement threshold to detect direction (in pixels)
const TOUCH_DIRECTION_THRESHOLD = 10

/**
 * Handle touch move for scroll direction detection.
 * Compares current position to previous position to detect direction.
 * @param {TouchEvent} event
 */
function onTouchMove(event) {
    if (!props.trackScrollDirection) return
    if (event.touches.length > 0) {
        const touchY = event.touches[0].clientY
        const delta = lastTouchY - touchY
        // Only trigger if movement exceeds threshold
        if (Math.abs(delta) >= TOUCH_DIRECTION_THRESHOLD) {
            // Update lastTouchY for next comparison
            lastTouchY = touchY
            // delta > 0 means finger moved up = scrolling down
            // delta < 0 means finger moved down = scrolling up
            onPhysicalScroll(delta > 0 ? 'down' : 'up')
        }
    }
}

/**
 * Get the scroller element for scroll compensation.
 * @returns {HTMLElement|null}
 */
function getScrollerElement() {
    return scrollerRef.value?.$el ?? null
}

// Expose methods for parent components
defineExpose({
    getScrollerElement
})
</script>

<template>
    <div class="session-items-list">
        <!-- Compute pending state -->
        <div v-if="isComputePending" class="compute-pending-state">
            <wa-callout variant="warning">
                <wa-icon slot="icon" name="hourglass"></wa-icon>
                <span>Session is being prepared, please wait...</span>
            </wa-callout>
        </div>

        <!-- Error state -->
        <FetchErrorPanel
            v-else-if="hasError"
            :loading="isLoading"
            @retry="handleRetry"
        >
            Failed to load session content
        </FetchErrorPanel>

        <!-- Loading state -->
        <div v-else-if="isLoading" class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading...</span>
        </div>

        <!-- Items list (virtualized) -->
        <VirtualScroller
            :key="sessionId"
            ref="scrollerRef"
            v-else-if="visualItems.length > 0"
            :items="visualItems"
            :item-key="item => item.lineNum"
            :min-item-height="MIN_ITEM_SIZE"
            :buffer="1000"
            :unload-buffer="1500"
            class="session-items"
            :class="{ 'initial-scrolling': isInitialScrolling }"
            @update="onScrollerUpdate"
            @item-resized="onItemResized"
            @wheel="onWheel"
            @touchstart="onTouchStart"
            @touchmove="onTouchMove"
        >
            <template #default="{ item, index }">
                <!-- Placeholder (no content loaded yet) -->
                <div v-if="!item.content" :style="{ minHeight: MIN_ITEM_SIZE + 'px' }"></div>

                <!-- Group head: show toggle (+ item content if expanded) -->
                <template v-else-if="item.isGroupHead">
                    <GroupToggle
                        :expanded="item.isExpanded"
                        :item-count="item.groupSize"
                        @toggle="toggleGroup(item.lineNum)"
                    />
                    <SessionItem
                        v-if="item.isExpanded"
                        :content="item.content"
                        :kind="item.kind"
                        :project-id="projectId"
                        :session-id="sessionId"
                        :parent-session-id="parentSessionId"
                        :line-num="item.lineNum"
                    />
                </template>

                <!-- Regular item (including ALWAYS with prefix/suffix): show item content -->
                <SessionItem
                    v-else
                    :content="item.content"
                    :kind="item.kind"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :parent-session-id="parentSessionId"
                    :line-num="item.lineNum"
                    :group-head="item.groupHead"
                    :group-tail="item.groupTail"
                    :prefix-expanded="item.prefixExpanded || false"
                    :suffix-expanded="item.suffixExpanded || false"
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)"
                />
            </template>
        </VirtualScroller>

        <!-- Draft session empty state -->
        <div v-else-if="session?.draft" class="empty-state">
        </div>

        <!-- Empty state -->
        <div v-else class="empty-state">
            Nothing to show yet
        </div>

        <div class="session-footer" :class="{ 'auto-hide-hidden': footerHidden }">
            <!-- Process indicator (fixed at bottom of list, visible while scrolling) -->
            <ProcessIndicator
                v-if="shouldShowProcessIndicator"
                :state="processState.state"
                size="large"
                :animate-states="BOTTOM_INDICATOR_ANIMATE_STATES"
                class="bottom-process-indicator"
            />

            <!-- Message input (only for main sessions, not subagents) -->
            <wa-divider></wa-divider>
            <MessageInput
                v-if="!parentSessionId"
                :key="sessionId"
                :session-id="sessionId"
                :project-id="projectId"
                @needs-title="emit('needs-title')"
            />
        </div>
    </div>
</template>

<style scoped>
.session-items-list {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
    position: relative;
}

.session-items {
    flex: 1;
    min-height: 0;
    padding-bottom: var(--wa-space-2xl);
}

/* Hide scroller during initial scroll to bottom to prevent visible jumping.
   Using visibility:hidden keeps the element in the layout and scrollable,
   but invisible until we're positioned at the bottom. */
.session-items.initial-scrolling {
    visibility: hidden;
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    min-height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

.compute-pending-state {
    flex: 1;
    padding: var(--wa-space-l);
    display: flex;
    align-items: center;
    justify-content: center;
}

.compute-pending-state wa-callout {
    max-width: 500px;
}

.session-footer {
    position: relative;
    > wa-divider {
        --width: 4px;
        --spacing: 0;
    }
}
.bottom-process-indicator {
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%) translateY(calc(-100% - var(--wa-space-2xs)));
}

/* Auto-hide footer on small viewport heights */
@media (max-height: 800px) {
    .session-footer {
        transition: transform 0.3s ease;
    }

    /* Apply opacity transition only to divider and message input, not ProcessIndicator */
    .session-footer > wa-divider,
    .session-footer > :deep(.message-input) {
        transition: opacity 0.3s ease;
    }

    .session-footer.auto-hide-hidden {
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        z-index: 10;
        transform: translateY(100%);
        pointer-events: none;
    }

    .session-footer.auto-hide-hidden > wa-divider,
    .session-footer.auto-hide-hidden > :deep(.message-input) {
        opacity: 0;
    }

    /* Keep the process indicator visible when footer is hidden */
    .session-footer.auto-hide-hidden .bottom-process-indicator {
        transform: translateX(-50%) translateY(calc(-100% - var(--wa-space-2xs) - 100%));
        pointer-events: auto;
    }
}

</style>
