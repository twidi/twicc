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

/**
 * Display name of a project, the single rule shared by the UI and the
 * alphabetical order: user-defined name, else last directory segment, else
 * the last dash-separated part of the id.
 */
export function computeProjectDisplayName(project) {
    if (project.name) return project.name
    if (project.directory) {
        const parts = project.directory.split('/')
        return parts[parts.length - 1] || project.directory
    }
    const parts = project.id.split('-')
    return parts[parts.length - 1] || project.id
}

// Case- and accent-insensitive, numeric-aware ("Seer2" before "Seer10").
const nameCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Display name A→Z (natural order); equal names use project IDs for stable ordering. */
export function createProjectNameComparator() {
    const names = new Map()
    const nameOf = project => {
        let name = names.get(project)
        if (name === undefined) {
            name = computeProjectDisplayName(project)
            names.set(project, name)
        }
        return name
    }
    return (a, b) => {
        const difference = nameCollator.compare(nameOf(a), nameOf(b))
        if (difference) return difference
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    }
}

/** The comparator for the configured project order (see PROJECT_SORT_ORDER). */
export function createProjectListComparator(order, activityById) {
    return order === 'alphabetical'
        ? createProjectNameComparator()
        : createProjectActivityComparator(activityById)
}
