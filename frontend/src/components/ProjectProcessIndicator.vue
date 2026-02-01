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
 * Only assistant_turn has pulse animation (user_turn and dead are static).
 *
 * Used in project list (home page) and project selector to quickly identify
 * which projects require attention.
 */
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
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

// Only assistant_turn should animate in this context
const animateStates = ['assistant_turn']
</script>

<template>
    <!-- Only render if there's an active process state for this project -->
    <ProcessIndicator
        v-if="projectState"
        :state="projectState"
        :size="size"
        :animate-states="animateStates"
    />
</template>
