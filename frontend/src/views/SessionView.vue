<script setup>
import { computed, watch, ref, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useDebounceFn } from '@vueuse/core'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT } from '../constants'
import SessionItem from '../components/SessionItem.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'

const route = useRoute()
const store = useDataStore()

// Reference to the DynamicScroller component
const scrollerRef = ref(null)

// Buffer: load N items before/after visible range
const LOAD_BUFFER = 20

// Debounce delay for scroll-triggered loading (ms)
const LOAD_DEBOUNCE_MS = 150

// Track pending range to load (accumulated during debounce)
const pendingLoadRange = ref(null)

// Current session from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId)

// Session data
const session = computed(() => store.getSession(sessionId.value))

// Session items
const items = computed(() => store.getSessionItems(sessionId.value))

// Loading and error states
const isLoading = computed(() => store.areSessionItemsLoading(sessionId.value))
const hasError = computed(() => store.didSessionItemsFailToLoad(sessionId.value))

/**
 * Load initial session items (first N and last N).
 * Used for initial load and retry after error.
 */
async function loadInitialItems(pId, sId, lastLine) {
    // Mark as fetched first (before async operations to avoid race conditions)
    if (!store.localState.sessions[sId]) {
        store.localState.sessions[sId] = {}
    }
    store.localState.sessions[sId].itemsFetched = true

    // Initialize items array with placeholders
    store.initSessionItems(sId, lastLine)

    // Load first N and last N items
    const ranges = []
    if (lastLine <= INITIAL_ITEMS_COUNT * 2) {
        // Small session: load everything
        ranges.push([1, lastLine])
    } else {
        // Large session: load first N and last N
        ranges.push([1, INITIAL_ITEMS_COUNT])
        ranges.push([lastLine - INITIAL_ITEMS_COUNT + 1, lastLine])
    }

    await store.loadSessionItemsRanges(pId, sId, ranges, { isInitialLoading: true })
}

// Load session items when session changes
watch([projectId, sessionId, session], async ([newProjectId, newSessionId, newSession]) => {
    if (!newProjectId || !newSessionId || !newSession) return

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    if (!store.areSessionItemsFetched(newSessionId)) {
        await loadInitialItems(newProjectId, newSessionId, lastLine)
    }

    // Always scroll to end of session (with retry until stable)
    await nextTick()
    scrollToBottomUntilStable()
}, { immediate: true })

// Retry loading session items after error
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

    await loadInitialItems(projectId.value, sessionId.value, lastLine)

    // Scroll to bottom after successful load
    await nextTick()
    scrollToBottomUntilStable()
}

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
 * Find ranges of placeholders (items without content) within a given range.
 * Returns an array of [start, end] ranges (1-based line_num).
 */
function findPlaceholderRanges(startIndex, endIndex) {
    const sessionItems = items.value
    if (!sessionItems?.length) return []

    const ranges = []
    let rangeStart = null

    // Clamp to valid indices (0-based)
    const start = Math.max(0, startIndex)
    const end = Math.min(sessionItems.length - 1, endIndex)

    for (let i = start; i <= end; i++) {
        const item = sessionItems[i]
        const isPlaceholder = !item.content

        if (isPlaceholder && rangeStart === null) {
            // Start a new range
            rangeStart = i
        } else if (!isPlaceholder && rangeStart !== null) {
            // End current range (convert to 1-based line_num)
            ranges.push([rangeStart + 1, i])
            rangeStart = null
        }
    }

    // Close any open range
    if (rangeStart !== null) {
        ranges.push([rangeStart + 1, end + 1])
    }

    return ranges
}

/**
 * Execute the pending load - called after debounce.
 */
async function executePendingLoad() {
    const range = pendingLoadRange.value
    if (!range) return

    const { startIndex, endIndex } = range
    pendingLoadRange.value = null

    // Find placeholder ranges within the buffered visible area
    const placeholderRanges = findPlaceholderRanges(startIndex, endIndex)

    if (placeholderRanges.length > 0 && projectId.value && sessionId.value) {
        // Check if we're at bottom BEFORE loading
        const el = scrollerRef.value?.$el
        const wasAtBottom = el
            ? (el.scrollHeight - el.scrollTop - el.clientHeight) <= 20
            : false

        await store.loadSessionItemsRanges(projectId.value, sessionId.value, placeholderRanges)

        // Re-scroll to bottom if we were at bottom before loading
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
 * Handle scroller update event - sets current visible range and triggers debounced load.
 * Indices from DynamicScroller are 0-based.
 * No accumulation: only load where user stops scrolling.
 */
function onScrollerUpdate(startIndex, endIndex, visibleStartIndex, visibleEndIndex) {
    // Add buffer around visible range
    const bufferedStart = Math.max(0, visibleStartIndex - LOAD_BUFFER)
    const bufferedEnd = visibleEndIndex + LOAD_BUFFER

    // Replace (not accumulate): only care about current visible range
    pendingLoadRange.value = {
        startIndex: bufferedStart,
        endIndex: bufferedEnd
    }

    debouncedLoad()
}

// Format mtime as local datetime
function formatDate(timestamp) {
    if (!timestamp) return '-'
    const date = new Date(timestamp * 1000)
    return date.toLocaleString()
}

// Truncate session ID for display in header
function truncateId(id) {
    if (!id || id.length <= 20) return id
    return id.slice(0, 8) + '...' + id.slice(-8)
}
</script>

<template>
    <div class="session-view">
        <!-- Header -->
        <header class="session-header" v-if="session">
            <div class="session-title">
                <h2 :title="sessionId">{{ truncateId(sessionId) }}</h2>
            </div>
            <div class="session-meta">
                <span class="meta-item">
                    <wa-icon name="list-numbers"></wa-icon>
                    {{ session.last_line }} lines
                </span>
                <span class="meta-item">
                    <wa-icon name="clock"></wa-icon>
                    {{ formatDate(session.mtime) }}
                </span>
            </div>
        </header>

        <wa-divider></wa-divider>

        <!-- Error state -->
        <FetchErrorPanel
            v-if="hasError"
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
            v-else-if="items.length > 0"
            :items="items"
            :min-item-size="80"
            :buffer="200"
            key-field="line_num"
            class="session-items"
            :emit-update="true"
            @update="onScrollerUpdate"
        >
            <template #default="{ item, index, active }">
                <DynamicScrollerItem
                    :item="item"
                    :active="active"
                    :size-dependencies="[item.content]"
                    :data-index="index"
                    class="item-wrapper"
                >
                    <!-- Placeholder (no content loaded yet) -->
                    <div v-if="!item.content" class="item-placeholder">
                        <div class="line-number">{{ item.line_num }}</div>
                        <wa-skeleton effect="sheen"></wa-skeleton>
                    </div>
                    <!-- Real item -->
                    <SessionItem
                        v-else
                        :content="item.content"
                        :line-num="item.line_num"
                    />
                </DynamicScrollerItem>
            </template>
        </DynamicScroller>

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
    flex-shrink: 0;
    padding: var(--wa-space-l);
}

.session-view > wa-divider {
    flex-shrink: 0;
}

.session-title h2 {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    font-family: var(--wa-font-mono);
    color: var(--wa-color-text);
}

.session-meta {
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
}

.item-wrapper {
    padding: var(--wa-space-s) var(--wa-space-l);
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
    display: flex;
    gap: var(--wa-space-m);
    padding: var(--wa-space-m);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-radius-m);
}

.item-placeholder .line-number {
    flex-shrink: 0;
    width: 40px;
    text-align: right;
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    user-select: none;
}

.item-placeholder wa-skeleton {
    flex: 1;
    height: 1.5em;
}
</style>
