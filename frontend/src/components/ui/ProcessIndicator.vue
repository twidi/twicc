<script setup>
/**
 * ProcessIndicator - Unified visual indicator for an agent's process state.
 *
 * Displays different indicators based on process state:
 * - starting: spinner (yellow)
 * - assistant_turn: robot icon (blue)
 * - user_turn: check icon (green)
 * - dead: warning triangle (red)
 *
 * When `hasActiveCrons` is true and state is user_turn, shows a clock icon
 * instead of the check to indicate scheduled cron work is pending.
 *
 * The `animateStates` prop controls which states are animated (the working
 * robot is animated, see styles/robot-working.css).
 */
import { computed } from 'vue'
import { PROCESS_STATE, PROCESS_STATE_COLORS } from '../../constants'

const props = defineProps({
    /**
     * The process state to display.
     */
    state: {
        type: String,
        required: true,
        validator: (value) => ['starting', 'assistant_turn', 'user_turn', 'dead'].includes(value)
    },
    /**
     * Size of the indicator: 'small' | 'medium' | 'large'
     */
    size: {
        type: String,
        default: 'medium',
        validator: (value) => ['small', 'medium', 'large'].includes(value)
    },
    /**
     * Which states should be animated.
     * Default: only 'assistant_turn' is animated.
     */
    animateStates: {
        type: Array,
        default: () => ['assistant_turn'],
        validator: (value) => value.every(s => ['starting', 'assistant_turn', 'user_turn', 'dead'].includes(s))
    },
    /**
     * Whether the process has active cron jobs.
     * - user_turn: shows clock icon instead of check
     * - assistant_turn: alternates between robot and clock
     */
    hasActiveCrons: {
        type: Boolean,
        default: false,
    },
})

/**
 * Get the base icon name for a process state (without cron consideration).
 */
const baseIconName = computed(() => {
    switch (props.state) {
        case 'assistant_turn': return 'robot'
        case 'user_turn': return 'check'
        case 'dead': return 'triangle-exclamation'
        default: return null
    }
})

/**
 * The effective icon to display.
 * user_turn + active crons: clock replaces check.
 */
const effectiveIconName = computed(() => {
    if (props.state === 'user_turn' && props.hasActiveCrons) return 'clock'
    return baseIconName.value
})

/**
 * Check if the current state should be animated.
 */
function shouldAnimate(state) {
    return props.animateStates.includes(state)
}

/**
 * Get the color for the current state.
 */
const stateColor = computed(() => PROCESS_STATE_COLORS[props.state] || PROCESS_STATE_COLORS[PROCESS_STATE.DEAD])
</script>

<template>
    <div
        class="process-indicator"
        :class="`process-indicator--${size}`"
        :style="{ '--process-color': stateColor }"
    >
        <!-- Spinner for starting -->
        <wa-spinner
            v-if="state === 'starting'"
            class="process-indicator__spinner"
        ></wa-spinner>

        <!-- Icon for other states -->
        <wa-icon
            v-else
            class="process-indicator__icon"
            :class="{ 'robot-working': shouldAnimate(state) }"
            :name="effectiveIconName"
        ></wa-icon>
    </div>
</template>

<style scoped>
.process-indicator {
    display: flex;
    justify-content: center;
    align-items: center;
}

/* Size variants */
.process-indicator--small {
    font-size: var(--wa-font-size-s);
}

.process-indicator--small .process-indicator__spinner {
    --size: 1em;
    --track-width: 2px;
}

.process-indicator--medium {
    font-size: var(--wa-font-size-l);
}

.process-indicator--medium .process-indicator__spinner {
    --size: 1.5em;
    --track-width: 3px;
}

.process-indicator--large {
    font-size: var(--wa-font-size-2xl);
}

.process-indicator--large .process-indicator__spinner {
    --size: 2em;
    --track-width: 4px;
}

/* Spinner (starting state) - uses --process-color from parent */
.process-indicator__spinner {
    --indicator-color: var(--process-color);
}

/* Icon color - uses --process-color from parent */
.process-indicator__icon {
    color: var(--process-color);
}


</style>
