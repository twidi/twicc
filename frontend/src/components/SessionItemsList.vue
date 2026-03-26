<script setup>
import { computed, watch, ref, provide, nextTick, inject, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT, DISPLAY_MODE } from '../constants'
import { useSettingsStore } from '../stores/settings'
import { isSupportedMimeType, MAX_FILE_SIZE } from '../utils/fileUtils'
import { toast } from '../composables/useToast'
import { apiFetch } from '../utils/api'
import { getParsedContent, hasContent } from '../utils/parsedContent'
import { pendingSessionSearch } from '../utils/pendingSearch'
import VirtualScroller from './VirtualScroller.vue'
import SessionItem from './SessionItem.vue'
import SessionSearchBar from './SessionSearchBar.vue'
import FetchErrorPanel from './FetchErrorPanel.vue'
import GroupToggle from './GroupToggle.vue'
import MessageInput from './MessageInput.vue'
import PendingRequestForm from './PendingRequestForm.vue'
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
})

const store = useDataStore()

const emit = defineEmits(['needs-title'])

// KeepAlive active state (provided by SessionView)
const sessionActive = inject('sessionActive', ref(true))

// Track whether items were added while the session was inactive,
// and whether the user was near the bottom before deactivation.
// Used on reactivation to decide if we should auto-scroll to bottom.
let itemCountAtDeactivation = null
let wasNearBottomAtDeactivation = false

// Saved scroll anchor for KeepAlive restore.
// Acts as a safety net: the VirtualScroller's composable handles suspend/resume
// internally via v-show, but SessionItemsList also captures the anchor in case
// the VirtualScroller needs to be restored from this level (e.g., if items changed
// while inactive and the composable's anchor is stale).
let savedScrollAnchor = null

// Pending scroll-to-bottom operation deferred because the scroller container was
// hidden (e.g., chat tab panel has display:none when navigating directly to /files).
// Set when scrollToBottomUntilStable would be called but the container has 0 height.
// Consumed by onScrollerBecameVisible when the container first gets a positive height.
let pendingScrollToBottom = null

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// In-session search bar state
const sessionSearchRef = ref(null)
const showSessionSearch = ref(false)

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

// Track pending range to load (accumulated during debounce)
const pendingLoadRange = ref(null)

// Drag and drop state
const dragOverType = ref(null)  // null | 'files' | 'text'
let dragCounter = 0  // Track enter/leave events for nested elements
const messageInputRef = ref(null)

// Session data
const session = computed(() => store.getSession(props.sessionId))

// Whether the session is stale (JSONL files deleted, history preserved as read-only)
const isStale = computed(() => session.value?.stale === true)

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

// Pending request for this session (tool approval or ask user question)
const pendingRequest = computed(() => store.getPendingRequest(props.sessionId))

// Whether any pending request is active (hides MessageInput, shows PendingRequestForm)
const hasPendingRequest = computed(() => pendingRequest.value != null)

/**
 * Whether the VirtualScroller should be visible.
 * Uses v-show (not v-if) to keep the component alive across KeepAlive cycles,
 * preserving the composable's height cache and scroll state.
 *
 * The scroller is shown when:
 * - Not in compute pending state
 * - No loading error
 * - Not currently loading
 * - There are items to display
 */
const showVirtualScroller = computed(() => {
    return !isComputePending.value && !hasError.value && !isLoading.value && (visualItems.value?.length > 0)
})

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
// Guarded: skip timer creation when inactive (KeepAlive deactivated)
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

    // Skip timer creation when inactive (DOM is detached)
    if (!sessionActive.value) return

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

// Cleanup timers when deactivated (KeepAlive moves DOM to detached storage)
onDeactivated(() => {
    if (temporaryIndicatorTimer) {
        clearTimeout(temporaryIndicatorTimer)
        temporaryIndicatorTimer = null
    }
    if (stabilityTimeoutId) {
        clearTimeout(stabilityTimeoutId)
        stabilityTimeoutId = null
    }

    // Capture state for reactivation: track item count and scroll position
    itemCountAtDeactivation = visualItems.value?.length ?? null
    const scroller = scrollerRef.value
    wasNearBottomAtDeactivation = scroller ? scroller.isAtBottom(AUTO_SCROLL_THRESHOLD) : false

    // Save scroll anchor as safety net for restoration after reactivation
    savedScrollAnchor = scroller ? scroller.getScrollAnchor() : null

    // Clear pending scroll — KeepAlive deactivation takes over via handlePostResume
    pendingScrollToBottom = null
})

// On reactivation: handle scroll restoration after KeepAlive reattaches the DOM.
//
// The VirtualScroller composable handles anchor-based scroll restoration internally
// via suspend/resume. If the container is hidden (e.g., inactive wa-tab-panel),
// resume is deferred until the container becomes visible.
//
// SessionItemsList adds one override on top: if items were added while inactive
// and the user was near bottom, scroll to bottom instead of restoring the anchor.
// This must also be deferred if the container is not yet visible.
onActivated(() => {
    handlePostResume()
})

// Watch for deferred resume completion: when the scroller's suspended state
// transitions from true to false, the actual resume just happened (possibly
// deferred because the container was hidden). Apply any post-resume overrides.
watch(
    () => scrollerRef.value?.suspended?.value,
    (newVal, oldVal) => {
        if (oldVal === true && newVal === false) {
            nextTick(() => handlePostResume())
        }
    },
)

/**
 * Apply post-resume logic: scroll to bottom if items were added while inactive
 * and the user was near bottom. Only acts if the scroller is not suspended
 * (i.e., the composable has completed its resume and the container is visible).
 */
function handlePostResume() {
    const scroller = scrollerRef.value
    if (!scroller || scroller.suspended?.value) return

    if (
        itemCountAtDeactivation !== null
        && visualItems.value?.length > itemCountAtDeactivation
        && wasNearBottomAtDeactivation
    ) {
        scrollToBottomUntilStable()
    }

    // Clear saved state now that we've handled reactivation
    itemCountAtDeactivation = null
    wasNearBottomAtDeactivation = false
    savedScrollAnchor = null
}

/**
 * Handle VirtualScroller becoming visible after being hidden (e.g., switching
 * from Files/Git tab to Chat tab when the session was loaded while Chat was hidden).
 *
 * Executes any deferred scrollToBottomUntilStable that couldn't run while the
 * scroller container had 0 height.
 */
function onScrollerBecameVisible() {
    if (pendingScrollToBottom) {
        const options = pendingScrollToBottom
        pendingScrollToBottom = null
        scrollToBottomUntilStable(options)
    }
}

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
        const response = await apiFetch(url)
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
        // Build range for initial content.
        // Parent sessions open at the bottom → load last N items.
        // Subagent sessions open at the top → load first N items.
        const ranges = []
        if (lastLine <= INITIAL_ITEMS_COUNT) {
            // Small session: load everything
            ranges.push([1, lastLine])
        } else if (props.parentSessionId) {
            // Subagent: load first N items (opens at the top)
            ranges.push([1, INITIAL_ITEMS_COUNT])
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

        // Fetch tool states first (needed by fetchSubagentsState to determine agent running status)
        await store.fetchToolStates(props.projectId, newSessionId)

        // For parent sessions, fetch all subagent states.
        // Populates the agent link cache (tool_use_id → agent_id) for View Agent buttons,
        // and creates synthetic process states for agents still running.
        if (!props.parentSessionId) {
            store.fetchSubagentsState(props.projectId, newSessionId)
        }
    }

    // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated)
    if (!sessionActive.value) return

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

    // Always scroll to end of session (with retry until stable)
    // Mark as initial scroll to hide scroller until positioned (only on first load)
    // When returning to an already-loaded session, items are already sized so no resize events will fire
    await nextTick()

    // Check if the scroller container is visible (chat tab panel is active).
    // When navigating directly to a non-chat tab (e.g., /files), the chat panel
    // has display:none and scrollToBottom has no effect. In that case, defer the
    // scroll until the chat tab becomes visible (handled by onScrollerBecameVisible).
    const scroller = scrollerRef.value
    const scrollState = scroller?.getScrollState()
    if (scrollState && scrollState.clientHeight === 0) {
        pendingScrollToBottom = { isInitial: isFirstLoad }
        if (isFirstLoad) {
            isInitialScrolling.value = true
        }
    } else {
        pendingScrollToBottom = null
        scrollToBottomUntilStable({ isInitial: isFirstLoad })
    }
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

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

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

    // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated)
    if (!sessionActive.value) return

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

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

        // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated).
        // The reactivation handler will check if items were added and scroll if needed.
        if (!sessionActive.value) return

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
        if (visualItem && !hasContent(visualItem)) {
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
 * Get the scroller element for scroll compensation.
 * @returns {HTMLElement|null}
 */
function getScrollerElement() {
    return scrollerRef.value?.$el ?? null
}

// =============================================================================
// Drag and Drop Handlers
// =============================================================================

/**
 * Handle dragenter event.
 * Uses a counter to properly handle nested elements.
 */
function onDragEnter(event) {
    const types = event.dataTransfer?.types
    // Only handle file or text drops (not internal browser drags like link/bookmark)
    const hasFiles = types?.includes('Files')
    const hasText = types?.includes('text/plain')
    if (!hasFiles && !hasText) return
    event.preventDefault()
    dragCounter++
    if (dragCounter === 1) {
        // Files take precedence (a file drop may also carry text/plain)
        dragOverType.value = hasFiles ? 'files' : 'text'
        // Listen for drag cancellation (Escape key, drop outside window, etc.)
        // dragend fires on the source element when the drag ends without a successful drop.
        document.addEventListener('dragend', onDragEnd, true)
    }
}

/**
 * Handle dragend event (fires when drag is cancelled, e.g. by Escape).
 * This is the only reliable way to detect drag cancellation, since the browser
 * consumes the first Escape keypress to cancel the native drag before it
 * reaches keydown listeners.
 */
function onDragEnd() {
    dragCounter = 0
    dragOverType.value = null
    document.removeEventListener('dragend', onDragEnd, true)
}

/**
 * Handle dragleave event.
 * Uses a counter to properly handle nested elements.
 */
function onDragLeave(event) {
    const types = event.dataTransfer?.types
    if (!types?.includes('Files') && !types?.includes('text/plain')) return
    event.preventDefault()
    dragCounter--
    if (dragCounter === 0) {
        dragOverType.value = null
        document.removeEventListener('dragend', onDragEnd, true)
    }
}

/**
 * Handle dragover event - required to allow drop.
 */
function onDragOver(event) {
    const types = event.dataTransfer?.types
    if (!types?.includes('Files') && !types?.includes('text/plain')) return
    event.preventDefault()
}

/**
 * Handle drop event - process dropped files or insert dropped text.
 */
async function onDrop(event) {
    event.preventDefault()
    dragCounter = 0
    dragOverType.value = null
    document.removeEventListener('dragend', onDragEnd, true)

    const dataTransfer = event.dataTransfer
    const hasFiles = dataTransfer?.types?.includes('Files')

    if (hasFiles) {
        const files = dataTransfer.files
        if (!files || files.length === 0) return

        // Process each file
        for (const file of files) {
            await processDroppedFile(file)
        }
    } else {
        // Dropped text — insert into the message textarea
        const text = dataTransfer?.getData('text/plain')
        if (text && messageInputRef.value) {
            messageInputRef.value.insertTextAtCursor(text)
        }
    }
}

/**
 * Process a single dropped file.
 * Validates and adds to attachments if valid.
 */
async function processDroppedFile(file) {
    // Validate MIME type
    if (!isSupportedMimeType(file.type)) {
        const extension = file.name.split('.').pop()?.toLowerCase() || 'unknown'
        toast.error(`Unsupported file type: .${extension}`, {
            title: 'Cannot attach file'
        })
        return
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
        const sizeMB = (file.size / 1024 / 1024).toFixed(1)
        toast.error(`File too large: ${sizeMB} MB (max 5 MB)`, {
            title: 'Cannot attach file'
        })
        return
    }

    try {
        await store.addAttachment(props.sessionId, file)
    } catch (error) {
        toast.error(error.message || 'Failed to process file', {
            title: 'Cannot attach file'
        })
    }
}

// =============================================================================
// In-session search (Ctrl+F)
// =============================================================================

// Search highlight terms provided to child components (MarkdownContent uses them)
const searchHighlightTerms = ref([])
provide('searchHighlightTerms', searchHighlightTerms)

// Provide a function for child components (e.g., ToolUseContent) to request
// scroll-to-bottom when they are about to expand (auto-open diffs, etc.).
// This enables stickToBottom mode so the expansion animation doesn't break
// the auto-scroll by drifting away from the bottom frame by frame.
provide('requestScrollToBottomIfNeeded', () => {
    if (props.parentSessionId) return // Subagent sessions don't auto-scroll
    const scroller = scrollerRef.value
    if (!scroller) return
    if (isAutoScrollingToBottom.value || scroller.isAtBottom(AUTO_SCROLL_THRESHOLD)) {
        scrollToBottomUntilStable()
    }
})

function handleSearchTerms(terms) {
    searchHighlightTerms.value = terms
}

/**
 * Toggle the in-session search bar.
 * Only responds when this is a main session (not subagent) and is currently active.
 *
 * When opening: sets e.detail.handled = true so App.vue blocks the native browser Find.
 * When closing: leaves handled = false so the native Ctrl+F passes through to the browser.
 */
function handleToggleSessionSearch(e) {
    // Only respond for the main chat tab (not subagent views)
    if (props.parentSessionId) return
    // Only respond when this session is active (KeepAlive)
    if (!sessionActive.value) return

    if (showSessionSearch.value) {
        closeSessionSearch()
        // Don't set handled — let the browser open its native Find bar
    } else {
        showSessionSearch.value = true
        nextTick(() => sessionSearchRef.value?.open())
        e.detail.handled = true
    }
}

function closeSessionSearch() {
    showSessionSearch.value = false
    sessionSearchRef.value?.reset()
    searchHighlightTerms.value = []
}

/**
 * Handle global keyboard shortcuts for in-session search.
 * When the search bar is visible:
 * - F3 / Shift+F3: navigate to next/previous match
 * - Escape: close the search bar
 * These work regardless of where focus is within the session.
 */
function handleSessionSearchKeydown(e) {
    if (!showSessionSearch.value) return
    if (props.parentSessionId) return
    if (!sessionActive.value) return

    if (e.key === 'F3') {
        e.preventDefault()
        if (e.shiftKey) {
            sessionSearchRef.value?.goToPrevious()
        } else {
            sessionSearchRef.value?.goToNext()
        }
    } else if (e.key === 'Escape') {
        e.preventDefault()
        closeSessionSearch()
    }
}

onMounted(() => {
    window.addEventListener('twicc:toggle-session-search', handleToggleSessionSearch)
    window.addEventListener('keydown', handleSessionSearchKeydown)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:toggle-session-search', handleToggleSessionSearch)
    window.removeEventListener('keydown', handleSessionSearchKeydown)
})

// Watch for pending search from the global SearchOverlay.
// When the user clicks a session result in the overlay, the query is stored in
// pendingSessionSearch. This watcher picks it up once the target session's
// SessionItemsList is active and opens the in-session search bar with that query.
watch(pendingSessionSearch, (pending) => {
    if (!pending) return
    if (pending.sessionId !== props.sessionId) return
    if (props.parentSessionId) return  // Only main session, not subagent
    if (!sessionActive.value) return

    // Consume the pending search
    const q = pending.query
    pendingSessionSearch.value = null

    // Open the search bar with the query
    showSessionSearch.value = true
    nextTick(() => {
        sessionSearchRef.value?.openWithQuery(q)
    })
}, { immediate: true })

// =============================================================================
// Scroll to line number (generic, used by search navigation and future features)
// =============================================================================

// Counter to detect stale scroll operations (when user clicks next/prev rapidly)
let scrollToLineNumGeneration = 0

/**
 * Scroll the virtual scroller to make the item at the given lineNum visible.
 *
 * Handles:
 * - Conversation mode: expands the block if the item is hidden (non-last assistant message)
 * - Content loading: ensures the target item's content is loaded before scrolling
 * - Pre-loading: fetches a buffer of items around the target to reduce placeholder flicker
 * - Jump-settle-correct: delegates to VirtualScroller.scrollToKey for stable positioning
 *
 * @param {number} lineNum - The line number to scroll to
 * @returns {Promise<boolean>} true if the item was successfully scrolled into view
 */
async function scrollToLineNum(lineNum) {
    const generation = ++scrollToLineNumGeneration
    const scroller = scrollerRef.value
    if (!scroller) return false

    const settingsStore = useSettingsStore()
    const visItems = visualItems.value

    // Step 1: Check if the item is already in the visual items list
    let found = visItems.some(vi => vi.lineNum === lineNum)

    // Step 2: If not found and in conversation mode, expand the block
    if (!found && settingsStore.getDisplayMode === DISPLAY_MODE.CONVERSATION) {
        const rawItems = items.value
        const rawItem = rawItems.find(ri => ri.line_num === lineNum)

        if (rawItem) {
            // Find the blockId: walk backwards from this item to find the last user_message
            let blockId = null
            for (let i = rawItems.indexOf(rawItem) - 1; i >= 0; i--) {
                if (rawItems[i].kind === 'user_message') {
                    blockId = rawItems[i].line_num
                    break
                }
            }

            if (blockId !== null) {
                store.ensureBlockDetailed(props.sessionId, blockId)
                await nextTick()
                found = visualItems.value.some(vi => vi.lineNum === lineNum)
            }
        }
    }

    if (!found) return false
    if (generation !== scrollToLineNumGeneration) return false  // Stale

    // Step 3: Ensure the target item's content is loaded (plus a buffer around it)
    const visItems2 = visualItems.value
    const targetIndex = visItems2.findIndex(vi => vi.lineNum === lineNum)
    if (targetIndex === -1) return false

    // Collect lineNums that need loading in a buffer around the target
    const bufferSize = LOAD_BUFFER
    const startIdx = Math.max(0, targetIndex - bufferSize)
    const endIdx = Math.min(visItems2.length - 1, targetIndex + bufferSize)
    const lineNumsToLoad = []

    for (let i = startIdx; i <= endIdx; i++) {
        if (!hasContent(visItems2[i])) {
            lineNumsToLoad.push(visItems2[i].lineNum)
        }
    }

    if (lineNumsToLoad.length > 0) {
        const ranges = lineNumsToRanges(lineNumsToLoad)
        await store.loadSessionItemsRanges(
            props.projectId,
            props.sessionId,
            ranges,
            props.parentSessionId
        )
        if (generation !== scrollToLineNumGeneration) return false  // Stale
        await nextTick()
    }

    // Step 4: Scroll to the item via the virtual scroller's jump-settle-correct
    const visible = await scroller.scrollToKey(lineNum, { align: 'center' })
    if (!visible) return false
    if (generation !== scrollToLineNumGeneration) return false  // Stale

    // Step 5: If the item is tall, scroll to the first search highlight within it
    await nextTick()  // Let v-highlight directive apply marks
    scrollToFirstHighlight(lineNum)

    return true
}

/**
 * Scroll the virtual scroller container so the first <mark class="search-highlight">
 * inside the given item is visible. Useful when an item is taller than the viewport
 * and the highlight is out of view after the initial scroll-to-item.
 */
function scrollToFirstHighlight(lineNum) {
    const scrollerEl = scrollerRef.value?.$el
    if (!scrollerEl) return

    const itemEl = scrollerEl.querySelector(`.session-item[data-line-num="${lineNum}"]`)
    if (!itemEl) return

    const mark = itemEl.querySelector('mark.search-highlight')
    if (!mark) return

    // Check if the mark is already visible in the scroller viewport
    const scrollerRect = scrollerEl.getBoundingClientRect()
    const markRect = mark.getBoundingClientRect()
    const isMarkVisible = markRect.top >= scrollerRect.top && markRect.bottom <= scrollerRect.bottom
    if (isMarkVisible) return

    // Scroll the mark into view within the scroller container
    mark.scrollIntoView({ block: 'center', behavior: 'instant' })
}

/**
 * Handle navigate event from the search bar.
 */
function handleSearchNavigate(lineNum) {
    scrollToLineNum(lineNum)
}

// Expose methods for parent components
defineExpose({
    getScrollerElement
})
</script>

<template>
    <div
        class="session-items-list"
        :class="{ 'drag-over': dragOverType }"
        @dragenter="onDragEnter"
        @dragleave="onDragLeave"
        @dragover="onDragOver"
        @drop="onDrop"
    >
        <!-- In-session search bar (Ctrl+F) -->
        <SessionSearchBar
            v-if="showSessionSearch"
            ref="sessionSearchRef"
            :session-id="sessionId"
            @close="closeSessionSearch"
            @navigate="handleSearchNavigate"
            @update:terms="handleSearchTerms"
        />

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

        <!-- Draft session empty state -->
        <div v-else-if="session?.draft && !visualItems.length" class="empty-state">
        </div>

        <!-- Empty state (no items and not a special state above) -->
        <div v-else-if="!visualItems.length" class="empty-state">
            Nothing to show yet
        </div>

        <!--
            Items list (virtualized).
            IMPORTANT: Uses v-show instead of v-if/v-else-if to keep the VirtualScroller
            mounted across KeepAlive deactivation/activation cycles. Without this, the
            v-else-if chain causes the VirtualScroller to be destroyed and recreated,
            losing the composable's height cache and scroll state.
            See spec: "Problems Encountered > VirtualScroller Scroll Position Loss"
        -->
        <VirtualScroller
            ref="scrollerRef"
            v-show="showVirtualScroller"
            :items="visualItems"
            :item-key="item => item.lineNum"
            :min-item-height="MIN_ITEM_SIZE"
            :buffer="1000"
            :unload-buffer="1500"
            :prevent-auto-scroll-to-bottom="!!parentSessionId"
            class="session-items"
            :class="{ 'initial-scrolling': isInitialScrolling }"
            @update="onScrollerUpdate"
            @item-resized="onItemResized"
            @became-visible="onScrollerBecameVisible"
        >
            <template #default="{ item, index }">
                <!-- Placeholder (no content loaded yet) -->
                <div v-if="!hasContent(item)" :style="{ minHeight: MIN_ITEM_SIZE + 'px' }"></div>

                <!-- Group head: show toggle (+ item content if expanded) -->
                <template v-else-if="item.isGroupHead">
                    <GroupToggle
                        :expanded="item.isExpanded"
                        :item-count="item.groupSize"
                        @toggle="toggleGroup(item.lineNum)"
                    />
                    <SessionItem
                        v-if="item.isExpanded"
                        :content="getParsedContent(item)"
                        :kind="item.kind"
                        :synthetic-kind="item.syntheticKind || null"
                        :project-id="projectId"
                        :session-id="sessionId"
                        :parent-session-id="parentSessionId"
                        :line-num="item.lineNum"
                    />
                </template>

                <!-- Regular item (including ALWAYS with prefix/suffix): show item content -->
                <SessionItem
                    v-else
                    :content="getParsedContent(item)"
                    :kind="item.kind"
                    :synthetic-kind="item.syntheticKind || null"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :parent-session-id="parentSessionId"
                    :line-num="item.lineNum"
                    :group-head="item.groupHead"
                    :group-tail="item.groupTail"
                    :prefix-expanded="item.prefixExpanded || false"
                    :suffix-expanded="item.suffixExpanded || false"
                    :detail-toggle-for="item.detailToggleFor ?? null"
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)"
                />
            </template>
        </VirtualScroller>

        <div class="session-footer">
            <!-- Stale session banner (replaces message input for stale main sessions) -->
            <div v-if="isStale && !parentSessionId" class="stale-banner">
                <wa-callout variant="warning" appearance="outlined">
                    <wa-icon slot="icon" name="clock-rotate-left"></wa-icon>
                    <div class="stale-banner-content">
                        <strong>Read-only session</strong>
                        <span>The session files were cleaned up by Claude Code. The conversation history has been preserved for reference.</span>
                    </div>
                </wa-callout>
            </div>
            <!-- Pending request form (replaces MessageInput when Claude requests approval or asks a question) -->
            <PendingRequestForm
                v-else-if="hasPendingRequest"
                :session-id="sessionId"
                :pending-request="pendingRequest"
            />
            <!-- Message input (only for main sessions, not subagents, hidden during pending requests) -->
            <MessageInput
                ref="messageInputRef"
                v-else-if="!parentSessionId && !hasPendingRequest"
                :session-id="sessionId"
                :project-id="projectId"
                @needs-title="emit('needs-title')"
            />
        </div>

        <!-- Drop zone overlay -->
        <div v-if="dragOverType" class="drop-overlay">
            <div class="drop-overlay-content">
                <wa-icon :name="dragOverType === 'files' ? 'cloud-upload' : 'text-indent-left'" style="font-size: 3rem;"></wa-icon>
                <span>{{ dragOverType === 'files' ? 'Drop files here' : 'Drop text into message' }}</span>
            </div>
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

/* Drop zone visual feedback */
.session-items-list.drag-over {
    outline: 3px dashed var(--wa-color-primary);
    outline-offset: -3px;
}

.drop-overlay {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    pointer-events: none;
}

.drop-overlay-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    color: white;
    font-size: var(--wa-font-size-xl);
    font-weight: 500;
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
.session-footer:has(.pending-request-form.expanded) {
    position: static;
}

.stale-banner {
    padding: var(--wa-space-s);
}

.stale-banner-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

</style>
