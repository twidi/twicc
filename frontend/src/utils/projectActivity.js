/**
 * Index effective activity without changing project objects or raw mtimes.
 * Only top-level projects inherit activity from their direct worktrees.
 * Cached worktrees contribute regardless of visibility or archived state.
 */
export function buildProjectActivityIndex(projects) {
    const projectsById = new Map(projects.map(project => [project.id, project]))
    const activityById = new Map(projects.map(project => [
        project.id,
        Number.isFinite(project.mtime) ? project.mtime : 0,
    ]))
    for (const project of projects) {
        const parent = projectsById.get(project.worktree_of)
        if (!parent || parent.worktree_of) continue
        activityById.set(parent.id, Math.max(activityById.get(parent.id), activityById.get(project.id)))
    }
    return activityById
}

/** Recent activity first; equal activity uses project IDs for stable ordering. */
export function createProjectActivityComparator(activityById) {
    return (a, b) => {
        const difference = (activityById.get(b.id) ?? 0) - (activityById.get(a.id) ?? 0)
        if (difference) return difference
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    }
}
