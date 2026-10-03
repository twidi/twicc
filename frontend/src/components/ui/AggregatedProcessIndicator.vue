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
import { isSessionUnread } from '../../utils/sessions'
import { summarizeProcessActivity } from '../../utils/processActivity'
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

/**
 * The live processes of the project set. Only real session processes count:
 * synthetic subagent states are display plumbing (a subagent is not a
 * session), and hidden sessions must stay out of every user-facing counter
 * (the backend already keeps them out of the process broadcasts; the guard
 * covers hidden sessions explicitly loaded into the store via show_hidden).
 */
const projectProcessStates = computed(() => {
    const states = []
    for (const [sessionId, ps] of Object.entries(dataStore.processStates)) {
        if (ps.synthetic || dataStore.sessions[sessionId]?.hidden) continue
        if (!projectIdSet.value.has(ps.project_id)) continue
        states.push(ps)
    }
    return states
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

const summary = computed(() => summarizeProcessActivity(projectProcessStates.value, unreadCount.value))
</script>

<template>
    <ProcessActivityIndicator :summary="summary" :size="size" />
</template>
