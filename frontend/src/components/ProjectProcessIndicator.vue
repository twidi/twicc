<script setup>
/**
 * ProjectProcessIndicator - Shows aggregated process state or unread count for a project.
 *
 * Mirrors per-session indicator logic at the project level:
 * - No active process: shows unread indicator (eye + count) if any, otherwise nothing.
 * - Active process in user_turn + unread sessions: shows unread indicator instead.
 * - Active process (any other state, or user_turn without unread): shows process indicator.
 *
 * Process state priority: dead > user_turn > starting > assistant_turn.
 * Only assistant_turn has pulse animation.
 *
 * Used in project list (home page) and project selector to quickly identify
 * which projects require attention.
 */
import { computed, useId } from 'vue'
import { useDataStore } from '../stores/data'
import AppTooltip from './AppTooltip.vue'
import ProcessIndicator from './ProcessIndicator.vue'

const props = defineProps({
    /**
     * The project ID to aggregate process states for.
     */
    projectId: {
        type: String,
        required: true
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
// Aggregated process state for this project (most important state)
const projectState = computed(() => dataStore.getProjectProcessState(props.projectId))

// Count of active processes for this project
const processCount = computed(() => dataStore.getProjectProcessCount(props.projectId))

/** Whether any session in the project has active cron jobs. */
const hasActiveCrons = computed(() => dataStore.getProjectHasActiveCrons(props.projectId))

/** Total number of active cron jobs across all sessions in the project (for tooltip). */
const activeCronCount = computed(() => dataStore.getProjectActiveCronCount(props.projectId))

/** Number of sessions with unread content in this project. */
const unreadCount = computed(() => dataStore.getProjectUnreadCount(props.projectId))

/**
 * Show unread indicator instead of process/nothing when:
 * - No active process and there are unread sessions, OR
 * - Active process in user_turn and there are unread sessions
 * (mirrors per-session logic: unread replaces user_turn, but not other states)
 */
const showUnread = computed(() =>
    unreadCount.value > 0 && (!projectState.value || projectState.value === 'user_turn')
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

// Unique ID for this instance (avoids collisions when multiple instances exist for the same project)
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
        <AppTooltip :for="indicatorId">{{ unreadCount }} unread session{{ unreadCount !== 1 ? 's' : '' }}{{ projectState ? ` · ${tooltipText}` : '' }}</AppTooltip>
    </template>
    <!-- Process indicator: active process without unread sessions -->
    <template v-else-if="projectState">
        <ProcessIndicator
            :id="indicatorId"
            :state="projectState"
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
