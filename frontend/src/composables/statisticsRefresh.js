/** Coalesce queued signals, preserving one refresh after signals during a fetch. */
export function createStatisticsRefresh(fetchCurrent) {
    let running = null
    let pending = false
    return function refresh() {
        pending = true
        if (!running) {
            running = Promise.resolve().then(async () => {
                while (pending) {
                    pending = false
                    await fetchCurrent()
                }
            }).finally(() => { running = null })
        }
        return running
    }
}

const homeRefreshes = new WeakMap()
const sessionRefreshes = new WeakMap()

export function refreshStatisticsHome(store) {
    if (!homeRefreshes.has(store)) {
        homeRefreshes.set(store, createStatisticsRefresh(() => store._fetchHomeData()))
    }
    return homeRefreshes.get(store)()
}

export function refreshStatisticsSessions(store, rootIds) {
    if (!sessionRefreshes.has(store)) sessionRefreshes.set(store, new Map())
    const targets = sessionRefreshes.get(store)
    return Promise.all(rootIds.map(rootId => {
        if (!targets.has(rootId)) {
            targets.set(rootId, createStatisticsRefresh(async () => {
                const root = store.sessions[rootId]
                if (!root) return
                if (store.localState.agentLoaded[rootId]) {
                    await store.fetchSubagentsState(root.project_id, rootId)
                }
                const belongs = session => {
                    const seen = new Set()
                    while (session?.parent_session_id && !seen.has(session.id)) {
                        seen.add(session.id)
                        if (session.parent_session_id === rootId) return true
                        session = store.sessions[session.parent_session_id]
                    }
                    return false
                }
                await Promise.all(Object.values(store.sessions)
                    .filter(belongs)
                    .map(session => store.refreshSessionRecord(session.id)))
            }))
        }
        return targets.get(rootId)()
    }))
}
