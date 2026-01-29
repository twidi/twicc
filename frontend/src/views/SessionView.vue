<script setup>
import { computed, watch, ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useDebounceFn, useThrottleFn } from '@vueuse/core'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT, DISPLAY_MODE } from '../constants'
import SessionItem from '../components/SessionItem.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import GroupToggle from '../components/GroupToggle.vue'

const route = useRoute()
const store = useDataStore()

// Reference to the DynamicScroller component
const scrollerRef = ref(null)

// Buffer: load N items before/after visible range
const LOAD_BUFFER = 50

// Debounce delay for scroll-triggered loading (ms)
const LOAD_DEBOUNCE_MS = 150

// Throttle delay for DOM order sorting (ms)
const SORT_THROTTLE_MS = 150

// Minimum item size for the virtual scroller (in pixels)
const MIN_ITEM_SIZE = 24

// Track pending range to load (accumulated during debounce)
const pendingLoadRange = ref(null)

// Current session from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId)

// Session data
const session = computed(() => store.getSession(sessionId.value))

// Session items (raw, with metadata + content)
const items = computed(() => store.getSessionItems(sessionId.value))

// Visual items (filtered by display mode and expanded groups)
const visualItems = computed(() => store.getSessionVisualItems(sessionId.value))

// Display mode (global, from store)
const displayMode = computed(() => store.getDisplayMode)

// Check if session computation is pending
const isComputePending = computed(() => {
    const sess = store.getSession(sessionId.value)
    return sess && sess.compute_version_up_to_date === false
})

// Loading and error states
const isLoading = computed(() => store.areSessionItemsLoading(sessionId.value))
const hasError = computed(() => store.didSessionItemsFailToLoad(sessionId.value))

/**
 * Load session data: metadata (all items) + initial content (first N and last N).
 * Fetches both in parallel for faster loading.
 */
async function loadSessionData(pId, sId, lastLine) {
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
            store.loadSessionMetadata(pId, sId),
            fetch(`/api/projects/${pId}/sessions/${sId}/items/?${params}`)
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
watch([projectId, sessionId, session], async ([newProjectId, newSessionId, newSession]) => {
    if (!newProjectId || !newSessionId || !newSession) return

    // Don't load if computation is pending
    if (newSession.compute_version_up_to_date === false) {
        return
    }

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    if (!store.areSessionItemsFetched(newSessionId)) {
        await loadSessionData(newProjectId, newSessionId, lastLine)
    }

    // Always scroll to end of session (with retry until stable)
    await nextTick()
    scrollToBottomUntilStable()
}, { immediate: true })

// Retry loading session data after error
async function handleRetry() {
    if (!projectId.value || !sessionId.value || !session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    // Reset fetched state to allow reload
    if (store.localState.sessions[sessionId.value]) {
        store.localState.sessions[sessionId.value].itemsFetched = false
    }
    // Clear existing items
    delete store.sessionItems[sessionId.value]
    delete store.sessionVisualItems[sessionId.value]

    await loadSessionData(projectId.value, sessionId.value, lastLine)

    // Scroll to bottom after successful load
    await nextTick()
    scrollToBottomUntilStable()
}

/**
 * Called when session becomes ready (compute completed).
 * Triggered by watching compute_version_up_to_date transition.
 */
async function onComputeCompleted() {
    if (!projectId.value || !sessionId.value || !session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    await loadSessionData(projectId.value, sessionId.value, lastLine)

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

    const el = scroller.$el
    if (!el) return

    const maxAttempts = 10
    const delayBetweenAttempts = 50 // ms

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        scroller.scrollToBottom()
        await new Promise(resolve => setTimeout(resolve, delayBetweenAttempts))

        const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
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

    if (ranges.length > 0 && projectId.value && sessionId.value) {
        const el = scrollerRef.value?.$el
        const wasAtBottom = el
            ? (el.scrollHeight - el.scrollTop - el.clientHeight) <= 20
            : false

        await store.loadSessionItemsRanges(projectId.value, sessionId.value, ranges)

        if (el && wasAtBottom) {
            const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
            if (distanceFromBottom > 5) {
                await nextTick()
                scrollToBottomUntilStable()
            }
        }
    }
}

const debouncedLoad = useDebounceFn(executePendingLoad, LOAD_DEBOUNCE_MS)

/**
 * Force DOM order to match visual order.
 * Called on scroller update and throttled on scroll events.
 */
function sortScrollerViews() {
    const recycleScroller = scrollerRef.value?.$refs?.scroller
    if (recycleScroller?.sortViews) {
        recycleScroller.sortViews()
    }
}

// Throttled version for scroll events (independent of scroller update)
const throttledSortViews = useThrottleFn(sortScrollerViews, SORT_THROTTLE_MS)

/**
 * Handle scroll event on the scroller element.
 * Triggers throttled DOM sorting independently of scroller update.
 */
function onScrollerScroll() {
    throttledSortViews()
}

// Setup and cleanup scroll listener
onMounted(() => {
    const el = scrollerRef.value?.$el
    if (el) {
        el.addEventListener('scroll', onScrollerScroll, { passive: true })
    }
})

onBeforeUnmount(() => {
    const el = scrollerRef.value?.$el
    if (el) {
        el.removeEventListener('scroll', onScrollerScroll)
    }
})

/**
 * Handle scroller update event - triggers lazy loading for visible items.
 * Works with visualItems (filtered list) and maps to actual line numbers.
 */
function onScrollerUpdate(startIndex, endIndex, visibleStartIndex, visibleEndIndex) {
    // Force DOM order to match visual order (for CSS sibling selectors)
    sortScrollerViews()

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

// Format mtime as local datetime
function formatDate(timestamp) {
    if (!timestamp) return '-'
    const date = new Date(timestamp * 1000)
    return date.toLocaleString()
}

// Get display name for session header (title if available, otherwise ID)
function getSessionDisplayName() {
    return session.value?.title || sessionId.value
}

/**
 * Handle display mode change from the selector.
 */
function onModeChange(event) {
    const newMode = event.target.value
    store.setDisplayMode(newMode)
}

/**
 * Toggle a group's expanded state.
 * Called when clicking on a GroupToggle component.
 */
function toggleGroup(groupHeadLineNum) {
    store.toggleExpandedGroup(sessionId.value, groupHeadLineNum)
}
</script>

<template>
    <div class="session-view">
        <!-- Header -->
        <header class="session-header" v-if="session">
            <div class="session-title">
                <h2 :title="session.title || sessionId">{{ getSessionDisplayName() }}</h2>
            </div>

            <!-- Mode selector -->
            <div class="session-controls">
                <wa-select
                    :value="displayMode"
                    @change="onModeChange"
                    size="small"
                    class="mode-selector"
                >
                    <wa-option :value="DISPLAY_MODE.DEBUG">Debug</wa-option>
                    <wa-option :value="DISPLAY_MODE.NORMAL">Normal</wa-option>
                    <wa-option :value="DISPLAY_MODE.SIMPLIFIED">Simplified</wa-option>
                </wa-select>
            </div>

            <div class="session-meta">
                <span class="meta-item">
                    <wa-icon name="comment" variant="regular"></wa-icon>
                    {{ session.message_count ?? '??' }} ({{ session.last_line }} lines)
                </span>
                <span class="meta-item">
                    <wa-icon name="clock"></wa-icon>
                    {{ formatDate(session.mtime) }}
                </span>
            </div>
        </header>

        <wa-divider></wa-divider>

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
        <DynamicScroller
            :key="sessionId"
            ref="scrollerRef"
            v-else-if="visualItems.length > 0"
            :items="visualItems"
            :min-item-size="MIN_ITEM_SIZE"
            :buffer="500"
            key-field="lineNum"
            class="session-items"
            :emit-update="true"
            @update="onScrollerUpdate"
        >
            <template #default="{ item, index, active }">
                <DynamicScrollerItem
                    :item="item"
                    :active="active"
                    :size-dependencies="[item.isExpanded, item.prefixExpanded, item.suffixExpanded]"
                    :data-index="index"
                    class="item-wrapper"
                >
                    <!-- Placeholder (no content loaded yet) -->
                    <!-- <wa-skeleton v-if="!item.content" effect="sheen" class="item-placeholder"></wa-skeleton> -->
                    <div v-if="!item.content" ></div>

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
                        :line-num="item.lineNum"
                        :group-head="item.groupHead"
                        :group-tail="item.groupTail"
                        :prefix-expanded="item.prefixExpanded || false"
                        :suffix-expanded="item.suffixExpanded || false"
                        @toggle-suffix="toggleGroup(item.suffixGroupHead)"
                    />
                </DynamicScrollerItem>
            </template>
        </DynamicScroller>

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
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
}

.session-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
}

.session-view > wa-divider {
    flex-shrink: 0;
}

.session-title {
    flex: 1;
    min-width: 0;  /* Allow text truncation */
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-controls {
    flex-shrink: 0;
}

.mode-selector {
    min-width: 120px;
}

.session-meta {
    width: 100%;
    display: flex;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.session-items {
    flex: 1;
    min-height: 0;
    padding-bottom: var(--wa-space-2xl);
}

.item-wrapper {
    /* display: flow-root creates a Block Formatting Context so that child margins
       (e.g. wa-card margins in user messages) don't collapse through and are
       included in offsetHeight — which DynamicScrollerItem reads for sizing.
       Unlike overflow:hidden, flow-root won't clip overflowing content
       (tooltips, dropdowns, etc.). */
    display: flow-root;
}

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-subtle);
    font-size: var(--wa-font-size-m);
}

.item-placeholder {
    padding-block: 2px;
    height: v-bind(MIN_ITEM_SIZE + 'px');
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
