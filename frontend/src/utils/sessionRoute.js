// frontend/src/utils/sessionRoute.js
//
// Single source of truth for "where does navigating to a session go", given the
// current route context. Every "go to this session" affordance routes through
// here — the sidebar row link, the session switcher, the finished/pending toast,
// the "View Agent" button, the orchestration tree, the command palette, the
// artifacts' "Open in session" buttons, the share creator badge, and the
// no-filter search result — so they all keep the user's current visual frame
// (the `project` vs `projects` prefix, the current project filter, the active
// workspace) and change only the session id (plus an optional subagent suffix).
// A link written as a full path (a Markdown link in a message) goes through
// `sessionLinkLocation`, which drops the frame the path carries.

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
 * @param {{ subagentId?: string, tab?: string, params?: Object }} [options] - append a subagent suffix, or point to a tool tab of the session (e.g. 'orchestration'); `params` adds the tab's own route params (e.g. `rootKey`, `filePath`)
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
    const params = { ...options.params, projectId, sessionId: target.id }
    if (options.subagentId) params.subagentId = options.subagentId
    const location = { name, params }
    if (route.query.workspace) {
        location.query = { workspace: route.query.workspace }
    }
    return location
}

/**
 * Rebuild a resolved link to a session so that it keeps the current frame.
 *
 * For a link whose path is written in full (an agent's Markdown link such as
 * `/project/<its project>/session/<id>/workflows/<run>`): its `project` vs
 * `projects` prefix, its project and its `workspace` query describe the frame
 * of whoever wrote it, not the user's. Only the session, its subagent or tool
 * tab, the tab's own params, the other query params and the hash are kept; the
 * frame comes from {@link sessionRouteLocation}.
 *
 * @param {import('vue-router').RouteLocationResolved|null} resolved - `router.resolve(href)`
 * @param {import('vue-router').RouteLocationNormalized} route - the current route
 * @returns {import('vue-router').RouteLocationRaw|null} null when the link is not a session route
 */
export function sessionLinkLocation(resolved, route) {
    const name = typeof resolved?.name === 'string' ? resolved.name : ''
    const baseName = name.startsWith('projects-session') ? name.slice('projects-'.length) : name
    if (baseName !== 'session' && !baseName.startsWith('session-')) return null
    const { projectId, sessionId } = resolved.params || {}
    if (!sessionId) return null
    const frame = sessionRouteLocation({ id: sessionId, project_id: projectId }, route)
    const location = {
        name: frame.name + baseName.slice('session'.length),
        params: { ...resolved.params, ...frame.params },
    }
    const query = { ...resolved.query }
    delete query.workspace
    if (frame.query?.workspace) query.workspace = frame.query.workspace
    if (Object.keys(query).length) location.query = query
    if (resolved.hash) location.hash = resolved.hash
    return location
}
