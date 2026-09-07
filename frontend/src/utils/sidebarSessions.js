// frontend/src/utils/sidebarSessions.js
//
// Shared logic for building the four session blocks that the sidebar
// (SessionList.vue) and the command palette's "Go to Session…" picker
// (staticCommands.js) render in the same order:
//
//   1. `extra`              — selected session that belongs to none of the
//                             other blocks (deep-linked / out-of-scope)
//   2. `crossFilterPinned`  — sessions whose pin mode brings them in from
//                             outside the current filter (`workspace`/`all`)
//   3. `crossFilterActive`  — sessions with a running process or unread
//                             content from outside the current filter
//                             (gated by `showActiveAcrossFilters`)
//   4. `natural`            — sessions that naturally belong to the filter
//                             (single project / workspace / all projects)
//
// The function is a pure computation: it reads from the stores and returns
// arrays. When invoked from inside a Vue `computed` (SessionList) or a
// dynamic `items()` closure (CommandPalette), Pinia reactivity still tracks
// every reactive access made inside.

import { sessionSortComparator, ALL_PROJECTS_ID } from '../stores/data'
import { isWorkspaceProjectId, extractWorkspaceId } from './workspaceIds'

/**
 * Build the archived-filter predicate. The currently-selected session is
 * always kept visible, even when the archived toggles are off — so a deep
 * link to an archived session or a session in an archived project stays
 * reachable.
 */
function makeArchiveFilter({ data, showArchived, showArchivedProjects, sessionId }) {
    const archivedProjectIds = showArchivedProjects
        ? null
        : new Set(data.getProjects.filter(p => p.archived).map(p => p.id))
    return (s) => (
        (showArchived || !s.archived || s.id === sessionId)
        && (!archivedProjectIds || !archivedProjectIds.has(s.project_id) || s.id === sessionId)
    )
}

/**
 * Split sidebar sessions into the four ordered blocks.
 *
 * @param {Object} opts
 * @param {Object} opts.data       - `useDataStore()` instance
 * @param {Object} opts.workspaces - `useWorkspacesStore()` instance
 * @param {string} opts.effectiveProjectId - Sidebar scope:
 *     real project id (single-project) | `ALL_PROJECTS_ID` | `workspace:<id>`
 * @param {?string} opts.activeWorkspaceId - Workspace active for cross-filter
 *     `workspace`-mode pins. In workspace mode this equals the workspace
 *     extracted from `effectiveProjectId`; in single-project mode it comes
 *     from the caller (e.g. `route.query.workspace`).
 * @param {?string} opts.sessionId - Selected session id (drives the `extra`
 *     block and the archived-filter exception).
 * @param {boolean} opts.showArchived
 * @param {boolean} opts.showArchivedProjects
 * @param {boolean} opts.showActiveAcrossFilters
 *
 * @returns {{
 *     extra: ?Object,
 *     crossFilterPinned: Object[],
 *     crossFilterActive: Object[],
 *     natural: Object[],
 * }}
 */
export function computeSidebarSessionBlocks({
    data,
    workspaces,
    effectiveProjectId,
    activeWorkspaceId,
    sessionId,
    showArchived,
    showArchivedProjects,
    showActiveAcrossFilters,
}) {
    const passesArchiveFilter = makeArchiveFilter({
        data, showArchived, showArchivedProjects, sessionId,
    })

    // 1. Natural scope — sessions that belong to the current filter.
    //    Already sorted by `sessionSortComparator` via the store getter.
    let natural
    if (isWorkspaceProjectId(effectiveProjectId)) {
        const wsId = extractWorkspaceId(effectiveProjectId)
        const visibleIds = new Set(workspaces.getVisibleProjectIds(wsId))
        natural = data.getAllSessions.filter(s => visibleIds.has(s.project_id))
    } else if (effectiveProjectId === ALL_PROJECTS_ID) {
        natural = data.getAllSessions
    } else if (effectiveProjectId) {
        // A main repo aggregates its worktrees' sessions (their content belongs
        // to the whole); a plain project shows only its own.
        const scopeIds = data.getProjectScopeIds(effectiveProjectId)
        if (scopeIds.length > 1) {
            const scopeSet = new Set(scopeIds)
            natural = data.getAllSessions.filter(s => scopeSet.has(s.project_id))
        } else {
            natural = data.getProjectSessions(effectiveProjectId)
        }
    } else {
        natural = data.getAllSessions
    }
    natural = natural.filter(passesArchiveFilter)
    const naturalIds = new Set(natural.map(s => s.id))

    // 2. Cross-filter pinned — `all` always, `workspace` when the active
    //    workspace contains the session's project.
    const activeWs = activeWorkspaceId ? workspaces.getWorkspaceById(activeWorkspaceId) : null
    const crossFilterPinned = Object.values(data.sessions).filter(s => {
        if (!s.pinned) return false
        if (s.parent_session_id) return false
        // Archived sessions only surface in the `natural` block; out-of-scope
        // archived sessions never get lifted into cross-filter (same rule in
        // `crossFilterActive`). Selected-session deep-link still works via `extra`.
        if (s.archived) return false
        if (naturalIds.has(s.id)) return false
        if (s.pinned === 'all') return true
        if (s.pinned === 'workspace' && activeWs) {
            // A worktree of a member counts as part of the workspace too.
            return workspaces.workspaceContainsProject(activeWorkspaceId, s.project_id)
        }
        return false
    })
        .filter(passesArchiveFilter)
        .sort(sessionSortComparator(data.processStates))
    const crossFilterPinnedIds = new Set(crossFilterPinned.map(s => s.id))

    // 3. Cross-filter active — running process OR unread content from any
    //    project, excluding sessions already covered by blocks above.
    //    "Unread" mirrors the DB-side check used by the backend's
    //    `/api/sessions/?unread=1` endpoint; the "user_turn only" refinement
    //    used for the *unread indicator* in SessionListItem is intentionally
    //    skipped here — a session mid-assistant-turn still deserves surfacing.
    let crossFilterActive = []
    if (showActiveAcrossFilters) {
        const processStates = data.processStates
        crossFilterActive = Object.values(data.sessions).filter(s => {
            if (s.parent_session_id) return false
            if (s.draft || s.ephemeral) return false
            if (s.archived) return false
            if (naturalIds.has(s.id) || crossFilterPinnedIds.has(s.id)) return false
            const ps = processStates[s.id]
            const hasProcess = ps != null
            const isUnread = !!s.last_new_content_at
                && (!s.last_viewed_at || s.last_new_content_at > s.last_viewed_at)
            return hasProcess || isUnread
        })
            .filter(passesArchiveFilter)
            .sort(sessionSortComparator(processStates))
    }
    const crossFilterActiveIds = new Set(crossFilterActive.map(s => s.id))

    // 4. Extra — the selected session when it belongs to none of the blocks
    //    above. Drafts and subagents are not "extra" material.
    let extra = null
    if (sessionId
        && !naturalIds.has(sessionId)
        && !crossFilterPinnedIds.has(sessionId)
        && !crossFilterActiveIds.has(sessionId)) {
        const s = data.sessions[sessionId]
        if (s && !s.parent_session_id) extra = s
    }

    return { extra, crossFilterPinned, crossFilterActive, natural }
}
