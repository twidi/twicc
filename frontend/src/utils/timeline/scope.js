// frontend/src/utils/timeline/scope.js
//
// Which events a Timeline page asks for (display design section 4). Pure: the page hands in plain facts
// read from its stores, and gets back what to show and which project ids to send.

import { extractWorkspaceId, isWorkspaceProjectId } from '../workspaceIds.js'

/**
 * @typedef {Object} TimelineScope
 * @property {'ready'|'loading'|'empty'|'not-found'} state
 *   `loading`: the projects or the workspace are not known yet (no request). `empty`: a workspace with no
 *   project (no request). `not-found`: a project id that does not exist (no request).
 * @property {'global'|'workspace'|'project'|'worktree'} kind
 * @property {string|null} projectId - The project of a `project` or `worktree` page.
 * @property {string[]|null} projectIds - What `GET /api/timeline/` is sent: `[]` is the global scope (the
 *   server's convention), `null` means no request. A workspace or project scope never sends an empty list.
 */

/**
 * @param {Object} facts
 * @param {string} facts.pageId - The panel's id: `allProjectsId`, `ws:<id>` or a project id.
 * @param {string} facts.allProjectsId
 * @param {boolean} facts.projectsLoaded
 * @param {(workspaceId: string) => ({allProjectIds: string[]}|null)} facts.workspaceOf - The workspace with
 *   EVERY member and worktree, archived ones included (the "show archived" setting does not apply).
 * @param {(projectId: string) => ({worktree_of?: (string|null)}|null)} facts.projectOf
 * @param {(projectId: string) => string[]} facts.projectScopeIds - The project and its worktrees.
 * @returns {TimelineScope}
 */
export function resolveTimelineScope({ pageId, allProjectsId, projectsLoaded, workspaceOf, projectOf, projectScopeIds }) {
    if (pageId === allProjectsId) return { state: 'ready', kind: 'global', projectId: null, projectIds: [] }
    if (isWorkspaceProjectId(pageId)) {
        const workspace = workspaceOf(extractWorkspaceId(pageId))
        if (!projectsLoaded || !workspace) return { state: 'loading', kind: 'workspace', projectId: null, projectIds: null }
        if (!workspace.allProjectIds.length) return { state: 'empty', kind: 'workspace', projectId: null, projectIds: null }
        return { state: 'ready', kind: 'workspace', projectId: null, projectIds: [...workspace.allProjectIds] }
    }
    if (!projectsLoaded) return { state: 'loading', kind: 'project', projectId: pageId, projectIds: null }
    const project = projectOf(pageId)
    if (!project) return { state: 'not-found', kind: 'project', projectId: pageId, projectIds: null }
    const scopeIds = projectScopeIds(pageId)
    return {
        state: 'ready',
        kind: project.worktree_of ? 'worktree' : 'project',
        projectId: pageId,
        // An empty list would mean "every project" to the server: a project page always names itself.
        projectIds: scopeIds.length ? scopeIds : [pageId],
    }
}
