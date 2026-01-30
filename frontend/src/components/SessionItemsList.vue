<script setup>
import { computed, watch, ref, nextTick } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT } from '../constants'
import VirtualScroller from './VirtualScroller.vue'
import SessionItem from './SessionItem.vue'
import FetchErrorPanel from './FetchErrorPanel.vue'
import GroupToggle from './GroupToggle.vue'

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
    }
})

const store = useDataStore()

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Buffer: load N items before/after visible range
const LOAD_BUFFER = 50

// Debounce delay for scroll-triggered loading (ms)
const LOAD_DEBOUNCE_MS = 150

// Minimum item size for the virtual scroller (in pixels)
const MIN_ITEM_SIZE = 24

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
 * Load session data: metadata (all items) + initial content (first N and last N).
 * Fetches both in parallel for faster loading.
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
        // Build ranges for initial content
        const ranges = []
        if (lastLine <= INITIAL_ITEMS_COUNT * 2) {
            // Small session: load everything
            ranges.push([1, lastLine])
        } else {
            // Large session: load first N and last N
            ranges.push([1, INITIAL_ITEMS_COUNT])
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

    // Don't load if computation is pending
    if (newSession.compute_version_up_to_date === false) {
        return
    }

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    if (!store.areSessionItemsFetched(newSessionId)) {
        await loadSessionData(lastLine)
    }

    // Always scroll to end of session (with retry until stable)
    await nextTick()
    scrollToBottomUntilStable()
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
    await nextTick()
    scrollToBottomUntilStable()
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

    await nextTick()
    scrollToBottomUntilStable()
}

// Watch for session compute completion
watch(() => session.value?.compute_version_up_to_date, (newValue, oldValue) => {
    // Transition from false (or undefined) to true
    if (newValue === true && oldValue !== true) {
        onComputeCompleted()
    }
})

/**
 * Scroll to bottom repeatedly until the scroll position stabilizes.
 * This handles the case where items are rendered with dynamic heights
 * that change the total scroll height after scrollToBottom is called.
 */
async function scrollToBottomUntilStable() {
    const scroller = scrollerRef.value
    if (!scroller) return

    const maxAttempts = 10
    const delayBetweenAttempts = 50 // ms

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        scroller.scrollToBottom({ behavior: 'auto' })
        await new Promise(resolve => setTimeout(resolve, delayBetweenAttempts))

        const state = scroller.getScrollState()
        const distanceFromBottom = state.scrollHeight - state.scrollTop - state.clientHeight
        if (distanceFromBottom <= 5) break
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
            :buffer="500"
            :unload-buffer="1000"
            class="session-items"
            @update="onScrollerUpdate"
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

        <!-- Filtered empty state (all items hidden by current mode) -->
        <div v-else-if="visualItems.length === 0 && items.length > 0" class="empty-state">
            <wa-icon name="filter"></wa-icon>
            <span>No items to display in this mode</span>
        </div>

        <!-- Empty state -->
        <div v-else class="empty-state">
            No items in this session
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
}

.session-items {
    flex: 1;
    min-height: 0;
    padding-bottom: var(--wa-space-2xl);
}

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}

.compute-pending-state {
    padding: var(--wa-space-l);
}

.compute-pending-state wa-callout {
    max-width: 500px;
    margin: 0 auto;
}

.group-expanded {
    display: flex;
    flex-direction: column;
}

.group-expanded .group-toggle {
    margin-bottom: 0;
}

.group-expanded .session-item {
    border-top-left-radius: 0;
    border-top-right-radius: 0;
}
</style>
