// frontend/src/utils/accountsDisplay.js
//
// Pure display helpers of the multi-user UI (unit-tested under node:test).

/** The access levels a grant may carry, weakest first ('' = no grant). */
export const ACCESS_LEVELS = Object.freeze(['view', 'operate'])

/** Initials for an avatar placeholder (display name, else email). */
export function userInitials(user) {
    const source = (user?.display_name || user?.email || '?').trim()
    const words = source.split(/[\s@._-]+/).filter(Boolean)
    const letters = words.length >= 2 ? words[0][0] + words[1][0] : source.slice(0, 2)
    return letters.toUpperCase()
}

/** The stronger of two levels ('' / null = none). */
export function strongerLevel(a, b) {
    if (a === 'operate' || b === 'operate') return 'operate'
    if (a === 'view' || b === 'view') return 'view'
    return ''
}

/**
 * Build the per-user access matrix shown in the admin panel.
 *
 * Groups the (non-worktree) projects by workspace — a project in several
 * workspaces appears in each — then a final group of projects in no workspace.
 * For every project row: its direct level, the level it inherits from the
 * workspace grants that contain it, and the effective (stronger) one.
 *
 * @param {object} args
 * @param {Array<{id, name, archived, projectIds}>} args.workspaces
 * @param {Array<{id, worktree_of}>} args.projects
 * @param {Record<string, string>} args.projectLevels   projectId → 'view'|'operate'
 * @param {Record<string, string>} args.workspaceLevels workspaceId → 'view'|'operate'
 * @returns {{ groups: Array<{workspace: object|null, level: string, rows: Array}> }}
 */
export function buildAccessMatrix({ workspaces, projects, projectLevels, workspaceLevels }) {
    const topLevel = projects.filter(project => !project.worktree_of)
    const byId = new Map(topLevel.map(project => [project.id, project]))
    const inheritedOf = (projectId) => {
        let level = ''
        for (const ws of workspaces) {
            if ((ws.projectIds || []).includes(projectId)) level = strongerLevel(level, workspaceLevels[ws.id])
        }
        return level
    }
    const row = (project) => {
        const direct = projectLevels[project.id] || ''
        const inherited = inheritedOf(project.id)
        return { project, direct, inherited, effective: strongerLevel(direct, inherited) }
    }
    const placed = new Set()
    const groups = workspaces.map(ws => {
        const rows = (ws.projectIds || []).filter(id => byId.has(id)).map(id => {
            placed.add(id)
            return row(byId.get(id))
        })
        return { workspace: ws, level: workspaceLevels[ws.id] || '', rows }
    })
    const orphans = topLevel.filter(project => !placed.has(project.id)).map(row)
    if (orphans.length) groups.push({ workspace: null, level: '', rows: orphans })
    return { groups }
}

/** Turn the matrix's level maps into the PUT /grants/ payload (no-grant entries dropped). */
export function grantsPayload(projectLevels, workspaceLevels) {
    const pick = (map, key) => Object.entries(map)
        .filter(([, level]) => ACCESS_LEVELS.includes(level))
        .map(([id, level]) => ({ [key]: id, level }))
    return {
        project_grants: pick(projectLevels, 'project_id'),
        workspace_grants: pick(workspaceLevels, 'workspace_id'),
    }
}
