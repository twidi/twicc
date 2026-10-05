import { toValue } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { ensureProjectTrust } from './useTrustGate'

/** Create a trusted draft and preserve the current sidebar filter and query. */
export function useNewSessionCreation({ projectId = null, allProjects = true } = {}) {
    const store = useDataStore()
    const route = useRoute()
    const router = useRouter()

    return async function handleNewSession(targetProjectId = null) {
        const projectIdToUse = targetProjectId || toValue(projectId)
        if (!projectIdToUse) return
        const gate = await ensureProjectTrust(projectIdToUse)
        if (!gate) return
        const newSessionId = store.createDraftSession(projectIdToUse, gate.state)
        const isAllProjectsMode = toValue(allProjects)
        router.push({
            name: isAllProjectsMode ? 'projects-session' : 'session',
            params: {
                projectId: isAllProjectsMode ? projectIdToUse : toValue(projectId),
                sessionId: newSessionId,
            },
            query: route.query,
        })
    }
}
