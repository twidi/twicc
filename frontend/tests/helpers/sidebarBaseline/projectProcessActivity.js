import { computed } from 'vue'
import { isSessionUnread } from '../../../src/utils/sessions.js'
import { unreadBackgroundKind, userTurnBackgroundShellCount } from '../../../src/utils/backgroundWork.js'

const indexes = new WeakMap()
const EMPTY = Object.freeze({ processCount: 0, pendingRequestCount: 0, hasAssistantTurn: false,
    activeCronCount: 0, backgroundShellCount: 0, unreadCount: 0,
    unreadBackgroundCount: 0, unreadShellCount: 0 })
const NO_UNREAD = Object.freeze({ count: 0, background: 0, shells: 0 })

function sameSummary(a, b) {
    return a && Object.keys(EMPTY).every(key => a[key] === b[key])
}

function sameUnread(a, b) {
    return a && a.count === b.count && a.background === b.background && a.shells === b.shells
}

function retainMap(previous, next, equal = (a, b) => a === b) {
    for (const [key, value] of next) {
        if (equal(previous?.get(key), value)) next.set(key, previous.get(key))
    }
    return previous?.size === next.size && [...next].every(([key, value]) => value === previous.get(key))
        ? previous : next
}

/** One lazily evaluated index per store. Consumers combine project totals only. */
export function getProjectActivityIndex(store) {
    if (indexes.has(store)) return indexes.get(store)
    const processCounts = computed(previous => {
        const next = new Map()
        for (const [id, ps] of Object.entries(store.processStates)) {
            if (ps.synthetic || store.sessions[id]?.hidden) continue
            let summary = next.get(ps.project_id)
            if (!summary) next.set(ps.project_id, summary = { ...EMPTY })
            summary.processCount++
            summary.pendingRequestCount += ps.pending_requests?.length || 0
            summary.hasAssistantTurn ||= ps.state === 'assistant_turn'
            summary.activeCronCount += ps.active_crons?.length || 0
            summary.backgroundShellCount += userTurnBackgroundShellCount(ps)
        }
        return retainMap(previous, next, sameSummary)
    })
    const unreadCounts = computed(previous => {
        const next = new Map()
        if (!store.isStartupInProgress) {
            for (const session of Object.values(store.sessions)) {
                const ps = store.processStates[session.id]
                if (!isSessionUnread(session, ps)) continue
                let unread = next.get(session.project_id)
                if (!unread) next.set(session.project_id, unread = { count: 0, background: 0, shells: 0 })
                unread.count++
                const kind = unreadBackgroundKind(ps)
                if (kind) unread.background++
                if (kind === 'shell') unread.shells++
            }
        }
        return retainMap(previous, next, sameUnread)
    })
    const index = {
        summary(projectIds, previous) {
            const result = { ...EMPTY }
            for (const id of new Set(projectIds)) {
                const counts = processCounts.value.get(id) || EMPTY
                for (const key of Object.keys(EMPTY)) {
                    if (key === 'hasAssistantTurn') result[key] ||= counts[key]
                    else result[key] += counts[key]
                }
                const unread = unreadCounts.value.get(id) || NO_UNREAD
                result.unreadCount += unread.count
                result.unreadBackgroundCount += unread.background
                result.unreadShellCount += unread.shells
            }
            return sameSummary(previous, result) ? previous : result
        },
        unread(projectIds) {
            return [...new Set(projectIds)].reduce((total, id) => total + (unreadCounts.value.get(id)?.count || 0), 0)
        },
        /** Sum over all projects, or only those accepted by `includeProject(projectId)`. */
        totalUnread(includeProject) {
            let total = 0
            for (const [id, unread] of unreadCounts.value) {
                if (!includeProject || includeProject(id)) total += unread.count
            }
            return total
        },
    }
    indexes.set(store, index)
    return index
}
