import { computed, reactive, toRaw } from 'vue'
import { ALL_PROJECTS_ID } from '../constants.js'
import { sessionSortComparator } from './sessionSort.js'
import { retainSessionArray } from './retain.js'
import { getRootSessions } from './rootSessions.js'
import { hasActiveStartupPhase } from './startupPhase.js'
import { noteStageRun } from './stageRuns.js'
import { isWorkspaceProjectId, extractWorkspaceId } from './workspaceIds.js'

// Kept for the existing importers of this module.
export { retainSessionArray }

// Per-scope stable session lists (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md,
// 5.2 and 5.3). One entry per state and scope key, created lazily.
const listsByState = new WeakMap()

const scopeKey = (cursorId, projectIds) => `${cursorId}|${projectIds ? [...projectIds].sort().join(',') : '*'}`

/**
 * The scope of a sidebar filter: its project-id set (null for all projects)
 * and the pagination cursor entry it uses. A single project uses its own
 * cursor; any workspace, a main repository whose scope holds more than one id,
 * all projects and a falsy id use the all-projects cursor.
 *
 * @param {Object} options
 * @param {?string} options.effectiveProjectId - a project id, a `ws:` workspace id, `ALL_PROJECTS_ID` or falsy
 * @param {(projectId: string) => string[]} options.getProjectScopeIds
 * @param {(workspaceId: string) => string[]} options.getVisibleProjectIds
 * @returns {{ key: string, projectIds: ?string[], cursorId: string }}
 */
export function resolveSessionScope({ effectiveProjectId, getProjectScopeIds, getVisibleProjectIds }) {
    let projectIds = null
    let cursorId = ALL_PROJECTS_ID
    if (isWorkspaceProjectId(effectiveProjectId)) {
        projectIds = getVisibleProjectIds(extractWorkspaceId(effectiveProjectId))
    } else if (effectiveProjectId && effectiveProjectId !== ALL_PROJECTS_ID) {
        const scopeIds = getProjectScopeIds(effectiveProjectId)
        if (scopeIds.length > 1) {
            projectIds = scopeIds
        } else {
            projectIds = [effectiveProjectId]
            cursorId = effectiveProjectId
        }
    }
    return { key: scopeKey(cursorId, projectIds), projectIds, cursorId }
}

/** During a startup phase a stage reads session fields through the raw record. */
const reader = (startup) => startup ? toRaw : (session) => session

function createScopeList(state, { projectIds, cursorId }) {
    const inScope = projectIds ? new Set(projectIds) : null

    // 1. Roots of the scope's projects, not hidden. Reads `project_id` and `hidden`.
    const candidates = computed(previous => {
        noteStageRun('scope:candidates')
        // The phase is read from the `startupProgress` object on each run, so an
        // untracked change shows up at the next progress message (5.3).
        const read = reader(hasActiveStartupPhase(state.startupProgress))
        const next = []
        for (const root of getRootSessions(state)) {
            const session = read(root)
            if ((!inScope || inScope.has(session.project_id)) && !session.hidden) next.push(session)
        }
        return retainSessionArray(previous, next)
    })

    // 2. The candidates above the pagination cutoff, plus running sessions in scope.
    // A new session can have mtime 0 until the watcher ingests its rollout.
    const members = computed(previous => {
        noteStageRun('scope:members')
        const read = reader(hasActiveStartupPhase(state.startupProgress))
        const cursor = state.localState.projects[cursorId]
        const oldestMtime = cursor?.hasMoreSessions ? cursor.oldestSessionMtime : null
        const list = candidates.value
        const next = oldestMtime == null ? list : list.filter(session =>
            read(session).mtime >= oldestMtime || state.processStates[read(session).id] != null)
        return retainSessionArray(previous, next)
    })

    // 3. The members in display order.
    const ordered = computed(previous => {
        noteStageRun('scope:ordered')
        const startup = hasActiveStartupPhase(state.startupProgress)
        const processes = startup ? toRaw(state.processStates) : state.processStates
        // The members are raw records during startup, so the sort reads them without tracking.
        const next = [...members.value].sort(sessionSortComparator(processes))
        return retainSessionArray(previous, next)
    })

    return Object.freeze({
        get members() { return members.value },
        get ordered() { return ordered.value },
    })
}

/**
 * The stable session list of a scope (see `resolveSessionScope`): `members`
 * in store order (the membership) and `ordered` (display order). Each stage
 * keeps its previous array when its content is equal. During a startup phase
 * the stages read session fields without tracking, rerun on each progress
 * message and hold raw records; after it they hold the reactive records.
 *
 * @param {Object} state - the data store state (`sessions`, `processStates`, `localState`, `startupProgress`)
 * @param {{ key: string, projectIds: ?string[], cursorId: string }} scope
 * @returns {{ readonly members: Object[], readonly ordered: Object[] }} one stable object per scope key
 */
export function getScopeSessionList(state, scope) {
    const owner = toRaw(state)
    let lists = listsByState.get(owner)
    if (!lists) listsByState.set(owner, lists = new Map())
    let list = lists.get(scope.key)
    // The list reads the reactive state whatever reference the first caller
    // passes, so a raw first caller still gets a list that tracks.
    if (!list) lists.set(scope.key, list = createScopeList(reactive(owner), scope))
    return list
}

/** Ordered list of a single-project scope (`scopeId`) or of all projects (`scopeId === allProjectsId`). */
export function getStableSessionList(state, scopeId, allProjectsId) {
    const projectIds = scopeId === allProjectsId ? null : [scopeId]
    return getScopeSessionList(state, { key: scopeKey(scopeId, projectIds), projectIds, cursorId: scopeId }).ordered
}
