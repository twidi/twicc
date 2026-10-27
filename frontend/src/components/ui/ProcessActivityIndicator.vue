<script setup>
/**
 * ProcessActivityIndicator - Renders the aggregated activity of a set of
 * sessions: the single indicator the cascade of `utils/processActivity.js`
 * picks, with its tooltip.
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
 * The caller owns the set (AggregatedProcessIndicator: projects; the
 * Orchestration tab: the current session's descendants) and hands over its
 * `summarizeProcessActivity` result.
 */
import { computed, useId } from 'vue'
import { processActivityDisplayMode, processActivityTooltip } from '../../utils/processActivity'
import AppTooltip from './AppTooltip.vue'
import ProcessIndicator from './ProcessIndicator.vue'

const props = defineProps({
    /**
     * `summarizeProcessActivity` result for the set of sessions.
     */
    summary: {
        type: Object,
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

const displayMode = computed(() => processActivityDisplayMode(props.summary))

/** State to pass to ProcessIndicator for the three process-based display modes. */
const processIndicatorState = computed(() => {
    if (displayMode.value === 'assistant_turn') return 'assistant_turn'
    return 'user_turn' // background_shells, crons and active_process render as user_turn variants
})

const tooltipText = computed(() => processActivityTooltip(props.summary, displayMode.value))

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
                :background-shells="displayMode === 'background_shells' ? summary.backgroundShellCount : 0"
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
