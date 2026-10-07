import { PROCESS_STATE } from '../constants.js'
import { activeCronCount, userTurnBackgroundShellCount } from './backgroundWork.js'
import { isSessionUnread } from './sessions.js'
import { sessionSortComparator } from './sessionSort.js'

/** Match the global activity indicators: real, live and visible processes only. */
export function isRailSessionProcess(processState, session) {
    return !!processState && processState.state !== PROCESS_STATE.DEAD
        && !processState.synthetic && !session?.hidden
}

/**
 * Global rail rows, independent of project filters and sidebar mode.
 * Metadata stays authoritative; a process snapshot supplies only the fallback.
 * Use the All Projects comparator and its stable session collection order.
 */
export function selectRailActiveSessions(processStates, sessions, currentSessionId = null, sessionOrder = null) {
    const rows = []
    // Object.values(store.sessions) supplies the stable order in getAllSessions.
    // Append missing snapshot records until their metadata joins that collection.
    const order = sessionOrder || new Map(Object.keys(sessions).map((id, index) => [id, index]))
    const sessionIds = Object.keys(processStates)
    for (const sessionId of sessionIds) {
        const processState = processStates[sessionId]
        const storedSession = sessions[sessionId]
        if (!isRailSessionProcess(processState, storedSession)) continue
        const session = storedSession || {
            id: sessionId,
            title: processState.session_title || sessionId,
            project_id: processState.project_id,
            provider: processState.provider,
        }
        const unread = isSessionUnread(storedSession, processState)
        const hasUnread = unread && sessionId !== currentSessionId
        const pendingRequest = (processState.pending_requests?.length || 0) > 0
        const userTurnBackgroundShells = userTurnBackgroundShellCount(processState)
        const hasActiveCrons = activeCronCount(processState) > 0
        // Exclude only entries whose visible indicator would be the cron clock.
        if (processState.state === PROCESS_STATE.USER_TURN && hasActiveCrons
            && !hasUnread && !pendingRequest && !userTurnBackgroundShells) continue
        rows.push({
            session, processState, unread, hasUnread, pendingRequest,
            indicatorKind: hasUnread ? 'unread' : pendingRequest ? 'pending' : 'process',
            userTurnBackgroundShells, hasActiveCrons,
        })
    }
    const compareSessions = sessionSortComparator(processStates)
    return rows.sort((a, b) => compareSessions(a.session, b.session)
        || ((order.get(a.session.id) ?? Infinity) - (order.get(b.session.id) ?? Infinity)) || 0)
}
