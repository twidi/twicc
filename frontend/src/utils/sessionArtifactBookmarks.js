import { i18n } from '../i18n/index.js'

/**
 * Resolve the artifact bookmarks visible from a real project context. Session
 * tabs and the dedicated Artifacts view share these inclusion rules.
 *
 * Visibility and presentation are deliberately separate: scope decides
 * whether a bookmark is present, independently of how a caller renders it.
 */
export function getArtifactBookmarkVisibilitySource({
    bookmark,
    localProjectIds,
    sharedWorkspaceIds,
    workspaceContainsProject,
}) {
    if (localProjectIds.has(bookmark.project_id)) return 'local'

    if (bookmark.scope === 'workspace' || bookmark.scope === 'all') {
        const sharesWorkspace = sharedWorkspaceIds.some(workspaceId =>
            workspaceContainsProject(workspaceId, bookmark.project_id)
        )
        if (sharesWorkspace) return 'workspace'
    }

    return bookmark.scope === 'all' ? 'all' : null
}

/**
 * Return visible bookmarks for a session tree, annotated with the visibility
 * source used to order their owning-project groups.
 */
export function computeSessionArtifactBookmarks({
    bookmarks,
    projectId,
    mainProjectId,
    projectScopeIds,
    workspaces,
    workspaceContainsProject,
}) {
    if (!projectId || !mainProjectId) return []

    const localProjectIds = new Set(projectScopeIds || [projectId])
    const sharedWorkspaceIds = (workspaces || [])
        .filter(ws => !ws.archived && workspaceContainsProject(ws.id, mainProjectId))
        .map(ws => ws.id)

    const rows = []
    for (const bookmark of Object.values(bookmarks || {})) {
        const visibilitySource = getArtifactBookmarkVisibilitySource({
            bookmark,
            localProjectIds,
            sharedWorkspaceIds,
            workspaceContainsProject,
        })
        if (!visibilitySource) continue

        rows.push({
            ...bookmark,
            _visibilitySource: visibilitySource,
        })
    }

    return rows.sort((a, b) => {
        if (a.updated_at < b.updated_at) return 1
        if (a.updated_at > b.updated_at) return -1
        return String(a.name || '').localeCompare(String(b.name || ''), i18n.global.locale.value)
    })
}
