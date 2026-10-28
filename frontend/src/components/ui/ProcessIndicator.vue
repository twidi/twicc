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
 * In user_turn, the check gives way to what still runs behind the finished
 * turn: a terminal icon when `backgroundShells` > 0 (a shell the agent left
 * running), else a clock icon when `hasActiveCrons` is
 * true (scheduled cron work is pending). Neither is animated: the agent is
 * not working.
 *
 * The `unread` prop adds the unread eye in user_turn (ignored in other states): the eye
 * alone, breathing, when nothing runs behind the turn; else the eye and the background
 * work icon (terminal or clock) share one cell and swap by opacity. The parent can tint
 * the eye with `--process-unread-color`.
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
    /**
     * Number of background shells still running behind the agent
     * (`background_work_in_progress.shells`).
     * - user_turn: shows a terminal icon instead of the check (wins over crons)
     */
    backgroundShells: {
        type: Number,
        default: 0,
    },
    /**
     * Whether the session is unread (only meaningful in user_turn).
     * - without background work: the eye replaces the check
     * - with background work: the eye alternates with the terminal / clock icon
     */
    unread: {
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
 * user_turn + background shells: terminal replaces check.
 * user_turn + active crons: clock replaces check.
 */
const effectiveIconName = computed(() => {
    if (props.state === 'user_turn' && props.backgroundShells > 0) return 'terminal'
    if (props.state === 'user_turn' && props.hasActiveCrons) return 'clock'
    return baseIconName.value
})

/**
 * The background work icon (shells win over crons), or null when nothing runs behind
 * the turn. Same rule as effectiveIconName.
 */
const workIconName = computed(() => {
    if (props.backgroundShells > 0) return 'terminal'
    if (props.hasActiveCrons) return 'clock'
    return null
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

        <!-- Unread with background work: the eye and the work icon alternate in one cell -->
        <span
            v-else-if="unread && state === 'user_turn' && workIconName"
            class="process-indicator__alternate"
        >
            <wa-icon class="process-indicator__alt--eye" name="eye"></wa-icon>
            <wa-icon class="process-indicator__alt--work" :name="workIconName"></wa-icon>
        </span>

        <!-- Unread alone: the breathing eye -->
        <wa-icon
            v-else-if="unread && state === 'user_turn'"
            class="process-indicator__icon process-indicator__icon--unread"
            name="eye"
        ></wa-icon>

        <!-- Icon for other states -->
        <wa-icon
            v-else
            class="process-indicator__icon"
            :class="{
                'robot-working': shouldAnimate(state),
                'process-indicator__icon--shell': effectiveIconName === 'terminal',
            }"
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

/* A shell the agent left running breathes like the unread eye (opacity only: kept under
   reduced motion). */
.process-indicator__icon--shell {
    animation: motion-status-pulse-deep 2.4s ease-in-out infinite;
}

/* Unread eye alone (same look as the unread eye elsewhere). Declared after the base icon
   rule: same specificity, the order decides. */
.process-indicator__icon--unread {
    color: var(--process-unread-color, var(--wa-color-warning-60));
    animation: motion-status-pulse 2.4s ease-in-out infinite;
}

/* Eye and work icon stacked in one cell, swapping by opacity (opacity only: kept under
   reduced motion). */
.process-indicator__alternate {
    display: inline-grid;
}

.process-indicator__alternate > * {
    grid-area: 1 / 1;
}

.process-indicator__alt--eye {
    color: var(--process-unread-color, var(--wa-color-warning-60));
    animation: motion-alternate-eye 2.4s ease-in-out infinite;
}

.process-indicator__alt--work {
    color: var(--process-color);
    animation: motion-alternate-work 2.4s ease-in-out infinite;
}
</style>
