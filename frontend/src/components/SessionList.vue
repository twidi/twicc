<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate, formatDuration } from '../utils/date'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES, SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'
import VirtualScroller from './VirtualScroller.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    },
    showProjectName: {
        type: Boolean,
        default: false
    },
    searchQuery: {
        type: String,
        default: ''
    }
})

const store = useDataStore()
const settingsStore = useSettingsStore()

// Session time format setting
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

// Sessions are already sorted by mtime desc in the getter
const allSessions = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID) {
        return store.getAllSessions
    }
    return store.getProjectSessions(props.projectId)
})

/**
 * Check if a query matches a text using subsequence matching.
 * All characters from query must appear in text, in order, but not necessarily consecutive.
 * Case-insensitive.
 *
 * Examples:
 *   matchSubsequence("vs", "virtual scroller") -> true (v...irtual s...croller)
 *   matchSubsequence("vscr", "virtual scroller") -> true (v...irtual scr...oller)
 *   matchSubsequence("xyz", "virtual scroller") -> false
 *
 * @param {string} query - The search query
 * @param {string} text - The text to search in
 * @returns {boolean} True if query is a subsequence of text
 */
function matchSubsequence(query, text) {
    const lowerQuery = query.toLowerCase()
    const lowerText = text.toLowerCase()

    let queryIndex = 0
    for (let i = 0; i < lowerText.length && queryIndex < lowerQuery.length; i++) {
        if (lowerText[i] === lowerQuery[queryIndex]) {
            queryIndex++
        }
    }
    return queryIndex === lowerQuery.length
}

// Filtered sessions based on search query (subsequence matching on title)
const sessions = computed(() => {
    const query = props.searchQuery.trim()
    if (!query) return allSessions.value

    return allSessions.value.filter(session => {
        const displayName = (session.draft && !session.title)
            ? 'New session'
            : (session.title || session.id)
        return matchSubsequence(query, displayName)
    })
})

// Pagination state
const hasMore = computed(() => store.hasMoreSessions(props.projectId))
const isLoading = computed(() => store.areSessionsLoading(props.projectId))

// Local error state for "load more" failures (not initial load)
const loadMoreError = ref(false)

// Virtual scroller configuration
// Session items have relatively uniform height (~80-100px)
const MIN_SESSION_HEIGHT = 70
const SCROLLER_BUFFER = 300

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Load more sessions when approaching the end of the list
async function loadMore() {
    if (isLoading.value || !hasMore.value || loadMoreError.value) return

    try {
        loadMoreError.value = false
        await store.loadSessions(props.projectId)
    } catch {
        // Only show error if we already have some sessions (not initial load)
        if (sessions.value.length > 0) {
            loadMoreError.value = true
        }
    }
}

// Retry after error
async function handleRetry() {
    loadMoreError.value = false
    await loadMore()
}

/**
 * Handle virtual scroller update event.
 * Triggers loading more sessions when user scrolls near the end.
 */
function onScrollerUpdate({ visibleEndIndex }) {
    // Load more when within 10 items of the end
    if (hasMore.value && !isLoading.value && sessions.value.length - visibleEndIndex < 10) {
        loadMore()
    }
}

// Reset scroll to top when project changes
watch(() => props.projectId, () => {
    loadMoreError.value = false
    if (scrollerRef.value) {
        scrollerRef.value.scrollToTop()
    }
})

// Get display name for session (title if available, "New session" for drafts, otherwise ID)
function getSessionDisplayName(session) {
    // For draft sessions without a title, show "New session"
    if (session.draft && !session.title) {
        return 'New session'
    }
    return session.title || session.id
}

// Format cost as USD string (e.g., "$0.42")
function formatCost(cost) {
    if (cost == null) return null
    return `${cost.toFixed(2)}`
}

const emit = defineEmits(['select'])

function handleSelect(session) {
    emit('select', session)
}

/**
 * Get process state for a session.
 * @param {string} sessionId
 * @returns {{ state: string, started_at?: number, state_changed_at?: number, memory?: number, error?: string } | null}
 */
function getProcessState(sessionId) {
    return store.getProcessState(sessionId)
}

/**
 * Get the color for a process state.
 * @param {string} state
 * @returns {string} CSS color variable
 */
function getProcessColor(state) {
    return PROCESS_STATE_COLORS[state] || PROCESS_STATE_COLORS[PROCESS_STATE.DEAD]
}

/**
 * Format memory in bytes to a human-readable string.
 * - KB: no decimal (e.g., "512 KB")
 * - MB < 10: 1 decimal (e.g., "4.6 MB")
 * - MB >= 10: no decimal (e.g., "420 MB")
 * - GB: 1 decimal (e.g., "1.5 GB")
 * @param {number|null} bytes
 * @returns {string}
 */
function formatMemory(bytes) {
    if (bytes == null) return ''

    const kb = bytes / 1024
    const mb = kb / 1024
    const gb = mb / 1024

    if (gb >= 1) {
        return `${gb.toFixed(1)} GB`
    }
    if (mb >= 10) {
        return `${Math.round(mb)} MB`
    }
    if (mb >= 1) {
        return `${mb.toFixed(1)} MB`
    }
    return `${Math.round(kb)} KB`
}

// Only assistant_turn should animate in session list
const animateStates = ['assistant_turn']

// Timer for updating state durations
const now = ref(Date.now() / 1000)  // Current time in seconds
let durationTimer = null

onMounted(() => {
    // Update every second for duration display
    durationTimer = setInterval(() => {
        now.value = Date.now() / 1000
    }, 1000)
})

onUnmounted(() => {
    if (durationTimer) {
        clearInterval(durationTimer)
    }
})

/**
 * Calculate state duration for a process.
 * @param {object} processState
 * @returns {number} Duration in seconds
 */
function getStateDuration(processState) {
    if (!processState?.state_changed_at) return 0
    return Math.max(0, Math.floor(now.value - processState.state_changed_at))
}

/**
 * Convert Unix timestamp (seconds) to Date object for wa-relative-time.
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {Date}
 */
function timestampToDate(timestamp) {
    if (!timestamp) return new Date()
    return new Date(timestamp * 1000)
}
</script>

<template>
    <div class="session-list-container">
        <!-- Empty state: no sessions at all -->
        <div v-if="allSessions.length === 0 && !isLoading" class="empty-state">
            No sessions
        </div>

        <!-- Empty state: no matching sessions (search returned nothing) -->
        <div v-else-if="sessions.length === 0 && !isLoading" class="empty-state">
            No matching sessions
        </div>

        <!-- Session list with virtual scroller -->
        <VirtualScroller
            v-else
            ref="scrollerRef"
            :key="projectId"
            :items="sessions"
            :item-key="session => session.id"
            :min-item-height="MIN_SESSION_HEIGHT"
            :buffer="SCROLLER_BUFFER"
            :unload-buffer="SCROLLER_BUFFER * 1.5"
            class="session-list"
            @update="onScrollerUpdate"
        >
            <template #default="{ item: session }">
                <wa-button
                    :appearance="session.id === sessionId ? 'outlined' : 'plain'"
                    :variant="session.id === sessionId ? 'brand' : 'neutral'"
                    class="session-item"
                    :class="{ 'session-item--active': session.id === sessionId }"
                    @click="handleSelect(session)"
                >
                    <div class="session-name-row">
                        <wa-tag v-if="session.draft" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
                        <span :id="`session-name-${session.id}`" class="session-name">{{ getSessionDisplayName(session) }}</span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-name-${session.id}`">{{ session.title || session.id }}</wa-tooltip>
                    </div>
                    <ProjectBadge v-if="showProjectName" :project-id="session.project_id" class="session-project" />
                    <!-- Process info row (only shown when process is active and not draft) -->
                    <div
                        v-if="!session.draft && getProcessState(session.id)"
                        class="process-info"
                        :style="{ color: getProcessColor(getProcessState(session.id).state) }"
                    >
                        <span :id="`process-memory-${session.id}`" class="process-memory">
                            <template v-if="getProcessState(session.id).memory">
                                {{ formatMemory(getProcessState(session.id).memory) }}
                            </template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`process-memory-${session.id}`">Claude Code memory usage</wa-tooltip>

                        <span :id="`process-duration-${session.id}`" class="process-duration">
                            <template v-if="getProcessState(session.id).state === PROCESS_STATE.ASSISTANT_TURN && getProcessState(session.id).state_changed_at">
                                {{ formatDuration(getStateDuration(getProcessState(session.id))) }}
                            </template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`process-duration-${session.id}`">Assistant turn duration</wa-tooltip>

                        <ProcessIndicator
                            :id="`process-indicator-${session.id}`"
                            :state="getProcessState(session.id).state"
                            size="small"
                            :animate-states="animateStates"
                        />
                        <wa-tooltip v-if="tooltipsEnabled" :for="`process-indicator-${session.id}`">Claude Code state: {{ PROCESS_STATE_NAMES[getProcessState(session.id).state] }}</wa-tooltip>
                    </div>
                    <!-- Meta row (not shown for draft sessions) -->
                    <div v-if="!session.draft" class="session-meta">
                        <span :id="`session-messages-${session.id}`" class="session-messages"><wa-icon auto-width name="comment" variant="regular"></wa-icon>{{ session.message_count ?? '??' }}</span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-messages-${session.id}`">Number of user and assistant messages</wa-tooltip>

                        <span :id="`session-cost-${session.id}`" class="session-cost"><wa-icon auto-width name="dollar-sign" variant="classic"></wa-icon>{{ session.total_cost != null ? formatCost(session.total_cost) : '-' }}</span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-cost-${session.id}`">Total session cost</wa-tooltip>

                        <span :id="`session-mtime-${session.id}`" class="session-mtime">
                            <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                            <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(session.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                            <template v-else>{{ formatDate(session.mtime, { smart: true }) }}</template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-mtime-${session.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(session.mtime, { smart: true })}` : 'Last activity' }}</wa-tooltip>
                    </div>
                </wa-button>
            </template>
        </VirtualScroller>

        <!-- Error state for load more (shown after the scroller) -->
        <div v-if="loadMoreError" class="load-more-error">
            <wa-callout variant="danger">
                <span>Failed to load more sessions</span>
                <wa-button
                    slot="footer"
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :loading="isLoading"
                    @click="handleRetry"
                >
                    <wa-icon name="arrow-rotate-right" slot="prefix"></wa-icon>
                    Retry
                </wa-button>
            </wa-callout>
        </div>

        <!-- Loading indicator (shown at bottom when loading more) -->
        <div v-if="isLoading && sessions.length > 0" class="load-more-indicator">
            <wa-spinner></wa-spinner>
        </div>
    </div>
</template>

<style scoped>
.session-list-container {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
    container-type: inline-size;
    container-name: session-list;
}

.session-list {
    flex: 1;
    min-height: 0;
    padding: var(--wa-space-s);
}

.session-item {
    width: 100%;
    /* Small gap between items */
    margin-block: var(--wa-space-3xs);
}

.session-item::part(base) {
    padding: var(--wa-space-xs);
    height: auto;
    margin-bottom: var(--wa-shadow-offset-y-s);  /* default if border, enforce for non active items to avoid movement */
}
.session-item::part(label) {
    width: 100%;
    text-align: left;
}

.session-name-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.draft-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
}

.session-name {
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-3xs);
    max-width: 100%;
}

/* Process info row - shows memory, duration, and indicator */
.process-info {
    display: grid;
    grid-template-columns: 1fr auto 0.6fr;
    align-items: center;
    justify-items: end;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-body);
    margin-top: var(--wa-space-3xs);
    overflow: hidden;
}

.process-memory {
    justify-self: start;
}

.process-duration {
    justify-self: center;
}

.session-meta {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: center;
    justify-items: start;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-2xs);
    overflow: hidden;
}

.session-meta > span {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.session-mtime {
    justify-self: end;
}
.session-cost {
    justify-self: center;
}

@container session-list (width <= 12rem) {
    /* Hide memory when container is too narrow */
    .process-info {
        grid-template-columns: auto 0.6fr;
    }
    .process-duration {
        justify-self: start;
    }
    .process-memory {
        display: none !important;
    }
    /* Hide cost when container is too narrow */
    .session-meta {
        grid-template-columns: 1fr 1fr;
    }
    .session-cost {
        display: none !important;
    }
}

@container session-list (width <= 9rem) {
    /* Hide duration when container is very narrow (keep only indicator) */
    .process-info {
        grid-template-columns: 1fr;
    }
    .process-duration {
        display: none !important;
    }
    /* Hide messages when container is very narrow (keep only mtime) */
    .session-meta {
        grid-template-columns: 1fr;
    }
    .session-messages {
        display: none !important;
    }
}

.load-more-indicator {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error {
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error wa-callout {
    --wa-callout-padding: var(--wa-space-s);
}

.load-more-error wa-callout span {
    font-size: var(--wa-font-size-s);
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
