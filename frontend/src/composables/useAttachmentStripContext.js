// frontend/src/composables/useAttachmentStripContext.js
// Context of a history attachment strip (spec 2026-10-03 §10.2): the explicit
// share mode (provided by the standalone share viewer) and the opening of a
// file chip in the Artifacts tab.
//
// The app router is imported lazily (anti-cycle pattern: utils/composables
// never import it statically); the share bundle aliases it to a stub, and
// share mode never navigates anyway.
import { inject } from 'vue'
import { useDataStore } from '../stores/data'
import { ATTACHMENT_SHARE_MODE, artifactNavigationTarget } from '../utils/attachmentStrip'
import { buildFilesRouteParams } from '../utils/granularRoutes'

/**
 * @param {() => string} getSessionId - the session that displays the message
 * @returns {{share: boolean, openArtifact: (request: {owner: string, relativePath: string}) => Promise<void>}}
 */
export function useAttachmentStripContext(getSessionId) {
    const share = inject(ATTACHMENT_SHARE_MODE, false) === true
    // Provided by SessionView: reveals a path of the session's own artifacts
    // dir in its Artifacts tab (existing navigation).
    const viewFileInFilesTab = inject('viewFileInFilesTab', null)
    const store = useDataStore()

    async function openArtifact({ owner, relativePath }) {
        if (share || !owner || !relativePath) return
        const sessionId = getSessionId()
        const session = store.getSession(sessionId)
        const target = artifactNavigationTarget(
            { owner, relativePath },
            { sessionId, artifactsDir: session?.artifacts_dir || null },
        )
        if (target.type === 'current' && viewFileInFilesTab) {
            await viewFileInFilesTab(target.absolutePath)
            return
        }
        // Another session's artifacts (a fork parent), or no in-session
        // handler: open that session's Artifacts tab on the file.
        const { default: router } = await import('../router')
        const route = router.currentRoute.value
        const allProjects = typeof route.name === 'string' && route.name.startsWith('projects-')
        const targetSessionId = target.type === 'current' ? sessionId : target.sessionId
        const projectId = store.getSession(targetSessionId)?.project_id || session?.project_id || route.params.projectId
        await router.push({
            name: allProjects ? 'projects-session-artifacts' : 'session-artifacts',
            params: {
                projectId,
                sessionId: targetSessionId,
                ...buildFilesRouteParams({ rootKey: 'artifacts', filePath: target.relativePath }),
            },
            query: route.query,
        })
    }

    return { share, openArtifact }
}
