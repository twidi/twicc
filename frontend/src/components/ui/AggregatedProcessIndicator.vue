<script setup>
/**
 * AggregatedProcessIndicator - Shows aggregated process state or unread count
 * for one or more projects.
 *
 * Collects the project set's live sessions and unread count, then hands them
 * to ProcessActivityIndicator, which owns the priority cascade (pending
 * request > unread > assistant turn > background shells > crons > active
 * process > nothing) shared with the Orchestration tab's sessions indicator.
 *
 * Used in project cards, workspace cards, detail panels, and project selectors
 * to quickly identify which projects/workspaces require attention.
 */
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import ProcessActivityIndicator from './ProcessActivityIndicator.vue'

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

// Shared indexes scan process/session data once per relevant change.
const summary = computed(previous => dataStore.getProjectActivitySummary(projectIdSet.value, previous))
</script>

<template>
    <ProcessActivityIndicator :summary="summary" :size="size" />
</template>
