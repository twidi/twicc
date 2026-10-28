import { computed, reactive, toRaw } from 'vue'
import { retainIds, retainIdSet } from './retain.js'
import { noteStageRun } from './stageRuns.js'

// Activity-free project inputs of the sidebar
// (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.6).
// None of them reads a project `mtime`, so a `project_updated` that changes
// only project activity reruns none of them, nor anything that reads them.
// One lazily created computed per raw `projects` object; each computed reads
// the reactive proxy of that object, whoever called first.

const archivedSets = new WeakMap()
const worktreeIndexes = new WeakMap()
const EMPTY = Object.freeze([])

function cachedComputed(cache, owner, evaluate) {
    const raw = toRaw(owner.projects)
    let entry = cache.get(raw)
    if (!entry) {
        const projects = reactive(raw)
        entry = computed(previous => evaluate(projects, previous))
        cache.set(raw, entry)
    }
    return entry.value
}

/**
 * The ids of the archived projects. Reads `archived` only, never `mtime`.
 * Retains the previous `Set` when the members are the same.
 *
 * @param {{ projects: Object }} owner - a store or its state
 * @returns {Set<string>}
 */
export function getArchivedProjectIds(owner) {
    return cachedComputed(archivedSets, owner, (projects, previous) => {
        const next = new Set()
        for (const id of Object.keys(projects)) {
            if (projects[id].archived) next.add(id)
        }
        return retainIdSet(previous, next)
    })
}

/** Keep `previous` when every parent maps to the same ids; else keep each unchanged id array. */
function retainWorktreeIndex(previous, next) {
    if (!previous) return next
    let same = previous.size === next.size
    for (const [parentId, ids] of next) {
        const kept = retainIds(previous.get(parentId) ?? null, ids)
        if (kept === ids) same = false
        else next.set(parentId, kept)
    }
    return same ? previous : next
}

/**
 * Parent project id → ids of its git worktrees, sorted by project id. Reads
 * `worktree_of` only, never `mtime`. Retains the previous `Map` (and each
 * unchanged id array) when the content is the same.
 *
 * @param {{ projects: Object }} owner - a store or its state
 * @returns {Map<string, string[]>}
 */
export function getWorktreeIdsByParent(owner) {
    return cachedComputed(worktreeIndexes, owner, (projects, previous) => {
        const next = new Map()
        for (const id of Object.keys(projects)) {
            const parentId = projects[id].worktree_of
            if (!parentId) continue
            const ids = next.get(parentId)
            if (ids) ids.push(id)
            else next.set(parentId, [id])
        }
        for (const ids of next.values()) ids.sort()
        return retainWorktreeIndex(previous, next)
    })
}

/**
 * Workspace membership without project activity: the member projects of a
 * workspace in their stored order, each immediately followed by its worktrees
 * in project-id order, without duplicates.
 *
 * - `visible(workspaceId)` skips a project (member or worktree) that is
 *   unknown, or archived while `isShowArchived()` is false; worktrees join
 *   only after a kept member.
 * - `all(workspaceId)` keeps every member, known or not, and all its worktrees.
 *
 * Every parameter is a function, called on each evaluation, so a caller that
 * reads a result inside a computed tracks what the evaluation read. Each mode
 * holds one computed per workspace id: it calls `noteStageRun('workspace:visible')`
 * or `noteStageRun('workspace:all')` on each run and retains the previous
 * array while the ids are the same.
 *
 * @param {Object} options
 * @param {(workspaceId: string) => ?{ projectIds: string[] }} options.getWorkspaceById
 * @param {(projectId: string) => ?{ archived?: boolean }} options.getProject
 * @param {() => Map<string, string[]>} options.getWorktreeIdsByParent
 * @param {() => boolean} options.isShowArchived
 * @returns {{ visible: (workspaceId: string) => string[], all: (workspaceId: string) => string[] }}
 */
export function createWorkspaceMembership({ getWorkspaceById, getProject, getWorktreeIdsByParent, isShowArchived }) {
    function collect(workspaceId, passes) {
        const ws = getWorkspaceById(workspaceId)
        if (!ws) return []
        const worktreesByParent = getWorktreeIdsByParent()
        const result = []
        const seen = new Set()
        const add = (id) => {
            if (!seen.has(id)) {
                seen.add(id)
                result.push(id)
            }
        }
        for (const projectId of ws.projectIds) {
            if (!passes(projectId)) continue
            add(projectId)
            for (const worktreeId of worktreesByParent.get(projectId) ?? EMPTY) {
                if (passes(worktreeId)) add(worktreeId)
            }
        }
        return result
    }

    const evaluateVisible = (workspaceId) => {
        const showArchived = isShowArchived()
        return collect(workspaceId, (projectId) => {
            const project = getProject(projectId)
            return !!project && (showArchived || !project.archived)
        })
    }
    const evaluateAll = (workspaceId) => collect(workspaceId, () => true)

    function resolver(stage, evaluate) {
        const byWorkspace = new Map()
        return (workspaceId) => {
            let entry = byWorkspace.get(workspaceId)
            if (!entry) {
                entry = computed(previous => {
                    noteStageRun(stage)
                    return retainIds(previous ?? null, evaluate(workspaceId))
                })
                byWorkspace.set(workspaceId, entry)
            }
            return entry.value
        }
    }

    return {
        visible: resolver('workspace:visible', evaluateVisible),
        all: resolver('workspace:all', evaluateAll),
    }
}
