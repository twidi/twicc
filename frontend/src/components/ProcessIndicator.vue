<script setup>
/**
 * ProcessIndicator - Unified visual indicator for Claude's process state.
 *
 * Displays different indicators based on process state:
 * - starting: spinner (yellow)
 * - assistant_turn: robot icon (blue)
 * - user_turn: check icon (green)
 * - dead: warning triangle (red)
 *
 * The `animateStates` prop controls which states have a pulse animation.
 */
import { computed } from 'vue'
import { PROCESS_STATE, PROCESS_STATE_COLORS } from '../constants'

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
     * Which states should have pulse animation.
     * Default: only 'assistant_turn' pulses.
     */
    animateStates: {
        type: Array,
        default: () => ['assistant_turn'],
        validator: (value) => value.every(s => ['starting', 'assistant_turn', 'user_turn', 'dead'].includes(s))
    }
})

/**
 * Get the icon name for a process state.
 */
function getIconName(state) {
    switch (state) {
        case 'assistant_turn': return 'robot'
        case 'user_turn': return 'check'
        case 'dead': return 'triangle-exclamation'
        default: return null
    }
}

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
            :class="{ 'process-indicator--animate': shouldAnimate(state) }"
            :name="getIconName(state)"
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

/* Pulse animation (applied when shouldAnimate is true) */
.process-indicator--animate {
    animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
</style>
