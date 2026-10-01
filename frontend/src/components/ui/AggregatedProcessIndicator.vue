<script setup>
/**
 * AggregatedProcessIndicator - Shows aggregated process state or unread count
 * for one or more projects.
 *
 * Priority cascade (highest → lowest):
 * 1. Pending request: hand icon (waiting for user response)
 * 2. Unread sessions: eye icon
 * 3. Assistant turn: robot icon (the agent is actively working)
 * 4. Background shells in a user_turn session: terminal icon (turn over, but
 *    a shell the agent left running still runs)
 * 5. Active crons: clock icon
 * 6. Active processes (none of the above): green check
 * 7. Nothing active: no indicator
 *
 * Used in project cards, workspace cards, detail panels, and project selectors
 * to quickly identify which projects/workspaces require attention.
 */
import { computed, useId } from 'vue'
import { useDataStore } from '../../stores/data'
import { isSessionUnread } from '../../utils/sessions'
import { aggregatedBackgroundSuffix, userTurnBackgroundShellCount } from '../../utils/backgroundWork'
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

/**
 * Worktree-expanded set of project IDs for fast lookup. Every id passed in is
 * expanded to its badge scope (the project plus its visible worktrees), so a
 * project's badge aggregates its worktrees one level down — like a workspace
 * aggregates its members. Expansion is idempotent (a worktree expands to
 * itself; an already-listed worktree dedupes), so callers that pass an
 * already-expanded workspace set stay correct. Aggregations below test
 * membership against this Set, so a duplicate id never double-counts.
 */
const projectIdSet = computed(() => {
    const set = new Set()
    for (const id of props.projectIds) {
        for (const scopeId of dataStore.getProjectIndicatorScopeIds(id)) {
            set.add(scopeId)
        }
    }
    return set
})

/**
 * Aggregated process info: scans all process states for relevant projects.
 * Only real session processes count: synthetic subagent states are display
 * plumbing (a subagent is not a session), and hidden sessions must stay out
 * of every user-facing counter (the backend already keeps them out of the
 * process broadcasts; the guard covers hidden sessions explicitly loaded
 * into the store via show_hidden).
 */
const processInfo = computed(() => {
    let processCount = 0
    let pendingRequestCount = 0
    let hasAssistantTurn = false
    let activeCronCount = 0
    let backgroundShellCount = 0

    for (const [sessionId, ps] of Object.entries(dataStore.processStates)) {
        if (ps.synthetic || dataStore.sessions[sessionId]?.hidden) continue
        if (!projectIdSet.value.has(ps.project_id)) continue
        processCount++
        pendingRequestCount += ps.pending_requests?.length || 0
        if (ps.state === 'assistant_turn') hasAssistantTurn = true
        activeCronCount += ps.active_crons?.length || 0
        // Only user_turn shells: in any other state the session is working
        // (or starting) anyway, which the robot already says.
        backgroundShellCount += userTurnBackgroundShellCount(ps)
    }

    return { processCount, pendingRequestCount, hasAssistantTurn, activeCronCount, backgroundShellCount }
})

/** Number of sessions with unread content across all projects. */
const unreadCount = computed(() => {
    // Skip expensive iteration during background compute — unread counts are
    // meaningless while metadata is being recomputed, and iterating all sessions
    // on every addSession (thousands of times) causes O(n²) CPU usage.
    if (dataStore.isStartupInProgress) return 0
    let count = 0
    for (const session of Object.values(dataStore.sessions)) {
        if (session.hidden) continue
        if (!projectIdSet.value.has(session.project_id)) continue
        if (isSessionUnread(session, dataStore.processStates[session.id])) count++
    }
    return count
})

/**
 * Priority cascade: pending_request > unread > assistant_turn > background_shells > crons > active_process > nothing.
 * Shells beat crons, as in ProcessIndicator.
 */
const displayMode = computed(() => {
    const info = processInfo.value
    if (info.pendingRequestCount > 0) return 'pending_request'
    if (unreadCount.value > 0) return 'unread'
    if (info.hasAssistantTurn) return 'assistant_turn'
    if (info.backgroundShellCount > 0) return 'background_shells'
    if (info.activeCronCount > 0) return 'crons'
    if (info.processCount > 0) return 'active_process'
    return null
})

/** State to pass to ProcessIndicator for the three process-based display modes. */
const processIndicatorState = computed(() => {
    if (displayMode.value === 'assistant_turn') return 'assistant_turn'
    return 'user_turn' // background_shells, crons and active_process render as user_turn variants
})

// Tooltip text
const tooltipText = computed(() => {
    const info = processInfo.value
    const mode = displayMode.value

    const sessionLabel = `${info.processCount} active session${info.processCount !== 1 ? 's' : ''}`
    const backgroundSuffix = aggregatedBackgroundSuffix({
        shells: info.backgroundShellCount,
        crons: info.activeCronCount,
    })

    if (mode === 'pending_request') {
        const pendingLabel = info.pendingRequestCount === 1
            ? 'Pending request'
            : `${info.pendingRequestCount} pending requests`
        return `${pendingLabel} · ${sessionLabel}${backgroundSuffix}`
    }

    if (mode === 'unread') {
        const unreadLabel = `${unreadCount.value} unread session${unreadCount.value !== 1 ? 's' : ''}`
        return info.processCount > 0
            ? `${unreadLabel} · ${sessionLabel}${backgroundSuffix}`
            : unreadLabel
    }

    return `${sessionLabel}${backgroundSuffix}`
})

// Unique ID for this instance
const indicatorId = useId()

// Only assistant_turn should animate in this context
const animateStates = ['assistant_turn']
</script>

<template>
    <span v-if="displayMode" class="aggregated-indicator-wrapper">
        <!-- Pending request: hand icon (highest priority) -->
        <template v-if="displayMode === 'pending_request'">
            <span :id="indicatorId" class="pending-indicator" :class="`pending-indicator--${size}`">
                <wa-icon name="hand"></wa-icon>
            </span>
            <AppTooltip :for="indicatorId">{{ tooltipText }}</AppTooltip>
        </template>
        <!-- Unread sessions: eye icon -->
        <template v-else-if="displayMode === 'unread'">
            <span :id="indicatorId" class="unread-indicator" :class="`unread-indicator--${size}`">
                <wa-icon name="eye"></wa-icon>
            </span>
            <AppTooltip :for="indicatorId">{{ tooltipText }}</AppTooltip>
        </template>
        <!-- Process states: assistant_turn / background_shells / crons / active_process -->
        <template v-else>
            <ProcessIndicator
                :id="indicatorId"
                :state="processIndicatorState"
                :size="size"
                :animate-states="animateStates"
                :has-active-crons="displayMode === 'crons'"
                :background-shells="displayMode === 'background_shells' ? processInfo.backgroundShellCount : 0"
            />
            <AppTooltip :for="indicatorId">{{ tooltipText }}</AppTooltip>
        </template>
    </span>
</template>

<style scoped>
/* Wrapper: inline-flex so it inherits attrs (class) from parent without layout disruption */
.aggregated-indicator-wrapper {
    display: inline-flex;
    align-items: center;
}

/* Pending request indicator — orange hand icon with pulse */
.pending-indicator {
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-warning-60);
    animation: pending-pulse 1.5s ease-in-out infinite;
}

.pending-indicator--small {
    font-size: var(--wa-font-size-s);
}

.pending-indicator--medium {
    font-size: var(--wa-font-size-l);
}

.pending-indicator--large {
    font-size: var(--wa-font-size-2xl);
}

/* Unread indicator — orange eye icon */
.unread-indicator {
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-warning-60);
    /* The unread eye breathes (opacity only: kept under reduced motion). */
    animation: motion-status-pulse 2.4s ease-in-out infinite;
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

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}
</style>
