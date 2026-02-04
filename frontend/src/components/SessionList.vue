<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate, formatDuration } from '../utils/date'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES, SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'

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
const sessions = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID) {
        return store.getAllSessions
    }
    return store.getProjectSessions(props.projectId)
})

// Pagination state
const hasMore = computed(() => store.hasMoreSessions(props.projectId))
const isLoading = computed(() => store.areSessionsLoading(props.projectId))

// Local error state for "load more" failures (not initial load)
const loadMoreError = ref(false)

// Sentinel element ref for intersection observer
const sentinel = ref(null)
// Container ref for scroll reset
const listContainer = ref(null)
let observer = null

// Load more sessions when sentinel becomes visible
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

// Setup intersection observer
onMounted(() => {
    observer = new IntersectionObserver(
        (entries) => {
            if (entries[0].isIntersecting) {
                loadMore()
            }
        },
        { rootMargin: '200px' }  // Trigger 200px before reaching the bottom
    )
    if (sentinel.value) {
        observer.observe(sentinel.value)
    }
})

// Re-observe when sentinel ref changes or projectId changes
watch([sentinel, () => props.projectId], ([el]) => {
    if (observer) {
        observer.disconnect()
        if (el) {
            observer.observe(el)
        }
    }
    // Reset error state when project changes
    loadMoreError.value = false
})

// Reset scroll to top when project changes
watch(() => props.projectId, () => {
    if (listContainer.value) {
        listContainer.value.scrollTop = 0
    }
})

onUnmounted(() => {
    observer?.disconnect()
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
    <div ref="listContainer" class="session-list">
        <wa-button
            v-for="session in sessions"
            :key="session.id"
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

        <!-- Error state for load more -->
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

        <!-- Sentinel for infinite scroll (only when no error) -->
        <div
            v-else-if="hasMore"
            ref="sentinel"
            class="load-more-sentinel"
        >
            <wa-spinner v-if="isLoading" />
        </div>

        <div v-if="sessions.length === 0 && !isLoading" class="empty-state">
            No sessions
        </div>
    </div>
</template>

<style scoped>
.session-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    overflow: auto;
    overflow-x: hidden;
    overscroll-behavior: contain;
    padding: var(--wa-space-s);
    container-type: inline-size;
    container-name: session-list;
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
    grid-template-columns: 1fr 1fr 1fr;
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

@container session-list (width <= 230px) {
    /* Hide memory when container is too narrow */
    .process-memory {
        display: none;
    }
    /* Hide cost when container is too narrow */
    .session-cost {
        display: none;
    }
}

@container session-list (width <= 170px) {
    /* Hide duration when container is very narrow (keep only indicator) */
    .process-duration {
        display: none;
    }
    .process-indicator {
        justify-self: start;
    }
    /* Hide messages when container is very narrow (keep only mtime) */
    .session-messages {
        display: none;
    }
}

.load-more-sentinel {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-m);
    min-height: 40px;
}

.load-more-error {
    padding: var(--wa-space-s);
}

.load-more-error wa-callout {
    --wa-callout-padding: var(--wa-space-s);
}

.load-more-error wa-callout span {
    font-size: var(--wa-font-size-s);
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
