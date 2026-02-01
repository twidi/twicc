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
 * Used in project list (home page) and project selector to quickly identify
 * which projects require attention.
 */
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { PROCESS_INDICATOR } from '../constants'

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
const settingsStore = useSettingsStore()

// Aggregated process state for this project (most important state)
const projectState = computed(() => dataStore.getProjectProcessState(props.projectId))

// Process indicator style from settings (dots or icons)
const indicatorStyle = computed(() => settingsStore.getProcessIndicator)

// Use icons mode display (spinner or icons)
const useIcons = computed(() => indicatorStyle.value === PROCESS_INDICATOR.ICONS)

/**
 * Get the icon name for a process state (icons mode).
 */
function getIconName(state) {
    switch (state) {
        case 'assistant_turn': return 'robot'
        case 'user_turn': return 'check'
        case 'dead': return 'triangle-exclamation'
        default: return null
    }
}
</script>

<template>
    <!-- Only render if there's an active process state for this project -->
    <div
        v-if="projectState"
        class="process-indicator-container"
        :class="`process-indicator-container--${size}`"
    >
        <!-- Dots mode: colored dot -->
        <span
            v-if="!useIcons"
            class="process-indicator process-indicator--dot"
            :class="`process-indicator--${projectState}`"
        ></span>

        <!-- Icons mode: spinner for starting -->
        <wa-spinner
            v-else-if="projectState === 'starting'"
            class="process-indicator process-indicator--icon process-indicator--starting"
        ></wa-spinner>

        <!-- Icons mode: icon for other states -->
        <wa-icon
            v-else
            class="process-indicator process-indicator--icon"
            :class="`process-indicator--${projectState}`"
            :name="getIconName(projectState)"
        ></wa-icon>
    </div>
</template>

<style scoped>
.process-indicator-container {
    display: flex;
    justify-content: center;
    align-items: center;
}

/* Dot indicator - base */
.process-indicator--dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
}

/* Dot sizes */
.process-indicator-container--small .process-indicator--dot {
    width: 10px;
    height: 10px;
}

.process-indicator-container--large .process-indicator--dot {
    width: 18px;
    height: 18px;
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

/* Icon indicator - base */
.process-indicator--icon {
    font-size: var(--wa-font-size-l);
}

/* Icon sizes */
.process-indicator-container--small .process-indicator--icon {
    font-size: var(--wa-font-size-s);
}

.process-indicator-container--large .process-indicator--icon {
    font-size: var(--wa-font-size-2xl);
}

/* Starting state uses wa-spinner component */
wa-spinner.process-indicator--starting {
    --size: 1.5em;
    --track-width: 3px;
    --indicator-color: var(--wa-color-warning-60);
}

.process-indicator-container--small wa-spinner.process-indicator--starting {
    --size: 1em;
    --track-width: 2px;
}

.process-indicator-container--large wa-spinner.process-indicator--starting {
    --size: 2em;
    --track-width: 4px;
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
</style>
