<script setup>
/**
 * AggregatedProcessIndicator - Shows aggregated process state or unread count
 * for one or more projects.
 *
 * Mirrors per-session indicator logic at the project/workspace level:
 * - No active process: shows unread indicator (eye + count) if any, otherwise nothing.
 * - Active process in user_turn + unread sessions: shows unread indicator instead.
 * - Active process (any other state, or user_turn without unread): shows process indicator.
 *
 * Process state priority: dead > user_turn > starting > assistant_turn.
 * Only assistant_turn has pulse animation.
 *
 * Used in project cards, workspace cards, detail panels, and project selectors
 * to quickly identify which projects/workspaces require attention.
 */
import { computed, useId } from 'vue'
import { useDataStore } from '../stores/data'
import AppTooltip from './AppTooltip.vue'
import ProcessIndicator from './ProcessIndicator.vue'

const props = defineProps({
    /**
     * List of project IDs to aggregate process states for.
     * Pass a single-element array for a single project.
     */
    projectIds: {
        type: Array,
        required: true,
    },
    /**
     * Size of the indicator: 'small' | 'medium' | 'large'
     */
    size: {
        type: String,
        default: 'small',
        validator: (value) => ['small', 'medium', 'large'].includes(value)
    }
})

const dataStore = useDataStore()

// Priority order (higher = more important)
const STATE_PRIORITY = {
    assistant_turn: 1,
    starting: 2,
    user_turn: 3,
    dead: 4,
}

/** Set of project IDs for fast lookup. */
const projectIdSet = computed(() => new Set(props.projectIds))

/** Aggregated most-important process state across all projects. */
const aggregatedState = computed(() => {
    let mostImportantState = null
    let highestPriority = 0

    for (const processState of Object.values(dataStore.processStates)) {
        if (!projectIdSet.value.has(processState.project_id)) continue
        const p = STATE_PRIORITY[processState.state] || 0
        if (p > highestPriority) {
            highestPriority = p
            mostImportantState = processState.state
        }
    }

    return mostImportantState
})

/** Total count of active processes across all projects. */
const processCount = computed(() => {
    let count = 0
    for (const processState of Object.values(dataStore.processStates)) {
        if (projectIdSet.value.has(processState.project_id)) count++
    }
    return count
})

/** Whether any session across the projects has active cron jobs. */
const hasActiveCrons = computed(() => {
    for (const processState of Object.values(dataStore.processStates)) {
        if (!projectIdSet.value.has(processState.project_id)) continue
        if (processState.active_crons?.length > 0) return true
    }
    return false
})

/** Total number of active cron jobs across all projects. */
const activeCronCount = computed(() => {
    let count = 0
    for (const processState of Object.values(dataStore.processStates)) {
        if (!projectIdSet.value.has(processState.project_id)) continue
        count += processState.active_crons?.length || 0
    }
    return count
})

/** Number of sessions with unread content across all projects. */
const unreadCount = computed(() => {
    let count = 0
    for (const session of Object.values(dataStore.sessions)) {
        if (!projectIdSet.value.has(session.project_id)) continue
        if (session.draft || session.archived || session.parent_session_id) continue
        if (!session.last_new_content_at) continue
        if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) continue
        // If process is running, only count when in user_turn
        const processState = dataStore.processStates[session.id]
        if (processState && processState.state !== 'user_turn') continue
        count++
    }
    return count
})

/**
 * Show unread indicator instead of process/nothing when:
 * - No active process and there are unread sessions, OR
 * - Active process in user_turn and there are unread sessions
 * (mirrors per-session logic: unread replaces user_turn, but not other states)
 */
const showUnread = computed(() =>
    unreadCount.value > 0 && (!aggregatedState.value || aggregatedState.value === 'user_turn')
)

// Tooltip text with count
const tooltipText = computed(() => {
    const count = processCount.value
    let text = `${count} active Claude Code session${count !== 1 ? 's' : ''}`
    const cronCount = activeCronCount.value
    if (cronCount > 0) {
        text += ` (${cronCount} active cron${cronCount > 1 ? 's' : ''})`
    }
    return text
})

// Unique ID for this instance (avoids collisions when multiple instances exist)
const indicatorId = useId()

// Only assistant_turn should animate in this context
const animateStates = ['assistant_turn']
</script>

<template>
    <!-- Unread indicator: replaces user_turn or stands alone when no process -->
    <template v-if="showUnread">
        <span :id="indicatorId" class="unread-indicator" :class="`unread-indicator--${size}`">
            <wa-icon name="eye"></wa-icon>
        </span>
        <AppTooltip :for="indicatorId">{{ unreadCount }} unread session{{ unreadCount !== 1 ? 's' : '' }}{{ aggregatedState ? ` · ${tooltipText}` : '' }}</AppTooltip>
    </template>
    <!-- Process indicator: active process without unread sessions -->
    <template v-else-if="aggregatedState">
        <ProcessIndicator
            :id="indicatorId"
            :state="aggregatedState"
            :size="size"
            :animate-states="animateStates"
            :has-active-crons="hasActiveCrons"
        />
        <AppTooltip :for="indicatorId">{{ tooltipText }}</AppTooltip>
    </template>
</template>

<style scoped>
.unread-indicator {
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-warning-60);
}

.unread-indicator--small {
    font-size: var(--wa-font-size-s);
}

.unread-indicator--medium {
    font-size: var(--wa-font-size-l);
}

.unread-indicator--large {
    font-size: var(--wa-font-size-2xl);
}
</style>
