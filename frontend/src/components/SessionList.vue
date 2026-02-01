<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { PROCESS_INDICATOR } from '../constants'
import ProjectBadge from './ProjectBadge.vue'

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

// Process indicator style from settings
const processIndicator = computed(() => settingsStore.getProcessIndicator)

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

// Get display name for session (title if available, otherwise ID)
function getSessionDisplayName(session) {
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
 * @returns {{ state: string, error?: string } | null}
 */
function getProcessState(sessionId) {
    return store.getProcessState(sessionId)
}

/**
 * Get the icon name for a process state.
 * @param {string} state - 'starting' | 'assistant_turn' | 'user_turn' | 'dead'
 * @returns {string} Icon name for wa-icon
 */
function getProcessIcon(state) {
    switch (state) {
        case 'assistant_turn': return 'robot'
        case 'user_turn': return 'check'
        case 'dead': return 'triangle-exclamation'
        // 'starting' uses wa-spinner component instead of icon
        default: return null
    }
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
                <span class="session-name" :title="session.title || session.id">{{ getSessionDisplayName(session) }}</span>
                <!-- Process indicator (dots mode) -->
                <span
                    v-if="getProcessState(session.id) && processIndicator === PROCESS_INDICATOR.DOTS"
                    class="process-indicator process-indicator--dot"
                    :class="`process-indicator--${getProcessState(session.id).state}`"
                    :title="getProcessState(session.id).state"
                ></span>
                <!-- Process indicator (icons mode - spinner for starting) -->
                <wa-spinner
                    v-if="getProcessState(session.id) && processIndicator === PROCESS_INDICATOR.ICONS && getProcessState(session.id).state === 'starting'"
                    class="process-indicator process-indicator--icon process-indicator--starting"
                    title="starting"
                ></wa-spinner>
                <!-- Process indicator (icons mode - icon for other states) -->
                <wa-icon
                    v-if="getProcessState(session.id) && processIndicator === PROCESS_INDICATOR.ICONS && getProcessState(session.id).state !== 'starting'"
                    class="process-indicator process-indicator--icon"
                    :class="`process-indicator--${getProcessState(session.id).state}`"
                    :name="getProcessIcon(getProcessState(session.id).state)"
                    :title="getProcessState(session.id).state"
                ></wa-icon>
            </div>
            <ProjectBadge v-if="showProjectName" :project-id="session.project_id" class="session-project" />
            <div class="session-meta">
                <span class="session-messages"><wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ session.message_count ?? '??' }}</span>
                <span class="session-mtime"><wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ formatDate(session.mtime, { smart: true }) }}</span>
                <span class="session-cost"><wa-icon auto-width name="dollar-sign" variant="classic"></wa-icon> {{ session.total_cost != null ? formatCost(session.total_cost) : '-' }}</span>
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
    min-width: 200px;
    overflow: auto;
    overflow-x: hidden;
    overscroll-behavior: contain;
    padding: var(--wa-space-s);
}

.session-item::part(base) {
    padding-block: var(--wa-space-xs);
    height: auto;
    margin-bottom: var(--wa-shadow-offset-y-s);  /* default if border, enforce for non active items to avoid movement */
}
.session-item::part(label) {
    width: 100%;
    text-align: left;
}

.session-name-row {
    position: relative;
    padding-right: 20px; /* Reserve space for indicator */
}

.session-name {
    display: block;
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* Process indicator - common styles */
.process-indicator {
    position: absolute;
    right: 0;
    top: 50%;
    transform: translateY(-50%);
}

/* Dot indicator */
.process-indicator--dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
}

.process-indicator--dot.process-indicator--starting {
    background-color: var(--wa-color-warning-60);
    animation: pulse 1.5s ease-in-out infinite;
}

.process-indicator--dot.process-indicator--assistant_turn {
    background-color: var(--wa-color-brand-60);
    animation: pulse 1s ease-in-out infinite;
}

.process-indicator--dot.process-indicator--user_turn {
    background-color: var(--wa-color-success-60);
}

.process-indicator--dot.process-indicator--dead {
    background-color: var(--wa-color-danger-60);
}

/* Icon indicator */
.process-indicator--icon {
    font-size: var(--wa-font-size-s);
}

/* Starting state uses wa-spinner component, style it */
wa-spinner.process-indicator--starting {
    --size: 1em;
    --track-width: 2px;
    --indicator-color: var(--wa-color-warning-60);
}

.process-indicator--icon.process-indicator--assistant_turn {
    color: var(--wa-color-brand-60);
    animation: pulse 1s ease-in-out infinite;
}

.process-indicator--icon.process-indicator--user_turn {
    color: var(--wa-color-success-60);
}

.process-indicator--icon.process-indicator--dead {
    color: var(--wa-color-danger-60);
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}


.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: var(--wa-space-3xs);
}

.session-meta {
    display: flex;
    justify-content: space-between;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-2xs);
    overflow: hidden;
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
