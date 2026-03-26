<script setup>
/**
 * ProjectProcessIndicator - Shows aggregated process state for a project.
 *
 * Aggregates all active processes across sessions of a project and displays
 * the most important state based on priority:
 * - dead (highest): something needs attention
 * - user_turn: Claude is waiting for user input
 * - starting: a process is starting up
 * - assistant_turn (lowest): Claude is working
 *
 * When the aggregated state would show user_turn and there are unread sessions,
 * an eye icon replaces the process indicator to signal new content to read.
 *
 * Only assistant_turn has pulse animation (user_turn and dead are static).
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
 * Show unread indicator instead of process indicator when:
 * - The aggregated state is user_turn (the state that would show check/clock icon)
 * - There is at least one unread session
 */
const showUnread = computed(() =>
    projectState.value === 'user_turn' && unreadCount.value > 0
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

<!-- Only render if there's an active process state for this project -->
<template v-if="projectState">
    <!-- Unread indicator: replaces process indicator when user_turn + unread sessions -->
    <template v-if="showUnread">
        <span :id="indicatorId" class="unread-indicator" :class="`unread-indicator--${size}`">
            <wa-icon name="eye"></wa-icon>
            <span class="unread-count">{{ unreadCount }}</span>
        </span>
        <AppTooltip :for="indicatorId">{{ unreadCount }} unread session{{ unreadCount !== 1 ? 's' : '' }} · {{ tooltipText }}</AppTooltip>
    </template>
    <!-- Normal process indicator -->
    <template v-else>
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
    gap: 0.15em;
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

.unread-count {
    font-size: 0.7em;
    font-weight: 700;
    line-height: 1;
}
</style>
