// frontend/src/utils/sessionRoute.js
//
// Single source of truth for "where does navigating to a session go", given the
// current route context. Every "go to this session" affordance routes through
// here — the sidebar row link, the session switcher, the finished/pending toast,
// the "View Agent" button, the orchestration tree, the command palette, and the
// no-filter search result — so they all keep the user's current visual frame
// (the `project` vs `projects` prefix, the current project filter, the active
// workspace) and change only the session id (plus an optional subagent suffix).

import { buildSessionBaseRouteName, buildSubagentRouteName, buildTabRouteName } from './granularRoutes.js'

/**
 * Build the router location for a session, honouring the current route context.
 *
 * - In all-projects mode (`projects-*` routes) the path carries the target
 *   session's own project (the "All projects / workspace" sidebar shows it).
 * - In single-project mode the path keeps the current `route.params.projectId`
 *   (the current sidebar filter); the session renders cross-filter if it lives
 *   elsewhere. Falls back to the target's project when the current route has no
 *   project (e.g. home).
 * - The active `workspace` query param is carried explicitly, which keeps it
 *   even toward a project outside the workspace (the router guard's early-return
 *   branch), matching how a cross-filter session stays on its workspace.
 *
 * @param {{ id: string, project_id: string }} target - the session to open
 * @param {import('vue-router').RouteLocationNormalized} route - the current route
 * @param {{ subagentId?: string, tab?: string }} [options] - append a subagent suffix, or point to a tool tab of the session (e.g. 'orchestration')
 * @returns {import('vue-router').RouteLocationRaw}
 */
export function sessionRouteLocation(target, route, options = {}) {
    const isAllProjects = route.name?.startsWith('projects-')
    const projectId = isAllProjects
        ? target.project_id
        : (route.params.projectId || target.project_id)
    const name = options.tab
        ? buildTabRouteName({ isAllProjectsMode: isAllProjects, isSessionRoute: true, tab: options.tab })
        : options.subagentId
            ? buildSubagentRouteName(isAllProjects)
            : buildSessionBaseRouteName(isAllProjects)
    const params = { projectId, sessionId: target.id }
    if (options.subagentId) params.subagentId = options.subagentId
    const location = { name, params }
    if (route.query.workspace) {
        location.query = { workspace: route.query.workspace }
    }
    return location
}
