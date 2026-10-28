// Frozen copy of the baseline project inputs of the sidebar derivations
// (`stores/data.js` and `stores/workspaces.js` at 5cf064fb4), as functions over
// a state and a workspace list instead of Pinia getters. Each Pinia getter is a
// `computed` read through a property getter, so it caches like the store does.
// Only the store plumbing changed: `this.X` / `useDataStore().X` read the local
// computeds, and the settings store read is `isShowArchived()`.

import { computed } from 'vue'
import { buildProjectActivityIndex, createProjectActivityComparator } from '../../../src/utils/projectActivity.js'

// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'

// Cheap "still booting" probe used by hot getters (unread counts, sessions
// list) to skip expensive work while any provider is still mid-phase.
export function hasActiveStartupPhase(startupProgress) {
    for (const byProvider of Object.values(startupProgress)) {
        if (!byProvider) continue
        for (const entry of Object.values(byProvider)) {
            if (entry && !entry.completed) return true
        }
    }
    return false
}

/**
 * @param {Object} state - reactive store state holding `projects`
 * @param {Object[]} workspaces - workspace records `{ id, projectIds, ... }`
 * @param {() => boolean} [isShowArchived] - the "show archived projects" setting
 * @returns {{ projectActivityById, projectActivityComparator, getProjects, getWorktreesOf,
 *     getVisibleProjectIds, getAllProjectIds }}
 */
export function baselineProjectInputs(state, workspaces, isShowArchived = () => state.settings.isShowArchivedProjects) {
    const getWorkspaceById = (id) => workspaces.find(w => w.id === id) || null
    const getProject = (id) => state.projects[id]

    // Data getters: derived activity keeps raw mtimes intact for synchronization.
    const projectActivityById = computed(() => buildProjectActivityIndex(Object.values(state.projects)))
    const projectActivityComparator = computed(() => createProjectActivityComparator(projectActivityById.value))
    // Includes every raw project object for aggregates and uniqueness checks.
    const getProjects = computed(() => Object.values(state.projects).sort(projectActivityComparator.value))
    // Worktree rows retain their own activity, ordered most recent first.
    const getWorktreesOf = computed(() => {
        const comparator = projectActivityComparator.value
        return (projectId) => Object.values(state.projects)
            .filter(project => project.worktree_of === projectId)
            .sort(comparator)
    })

    const getVisibleProjectIds = computed(() => (workspaceId) => {
        const ws = getWorkspaceById(workspaceId)
        if (!ws) return []
        const showArchived = isShowArchived()
        const passes = (p) => p && (showArchived || !p.archived)
        const result = []
        const seen = new Set()
        const add = (id) => {
            if (!seen.has(id)) {
                seen.add(id)
                result.push(id)
            }
        }
        for (const pid of ws.projectIds) {
            if (!passes(getProject(pid))) continue
            add(pid)
            for (const wt of getWorktreesOf.value(pid)) {
                if (passes(wt)) add(wt.id)
            }
        }
        return result
    })

    const getAllProjectIds = computed(() => (workspaceId) => {
        const ws = getWorkspaceById(workspaceId)
        if (!ws) return []
        const result = []
        const seen = new Set()
        const add = (id) => {
            if (!seen.has(id)) {
                seen.add(id)
                result.push(id)
            }
        }
        for (const pid of ws.projectIds) {
            add(pid)
            for (const wt of getWorktreesOf.value(pid)) add(wt.id)
        }
        return result
    })

    return {
        get projectActivityById() { return projectActivityById.value },
        get projectActivityComparator() { return projectActivityComparator.value },
        get getProjects() { return getProjects.value },
        get getWorktreesOf() { return getWorktreesOf.value },
        get getVisibleProjectIds() { return getVisibleProjectIds.value },
        get getAllProjectIds() { return getAllProjectIds.value },
    }
}
