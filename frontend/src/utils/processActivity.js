// frontend/src/utils/processActivity.js
//
// The aggregated activity badge of a set of sessions: which single indicator
// sums up their live process states (hand, eye, robot, terminal, clock, check),
// and its tooltip. One cascade for every surface that aggregates sessions —
// the project/workspace badges (AggregatedProcessIndicator) and the
// Orchestration tab's sessions indicator — so they can never drift apart.
// Pure (no store, no Vue) so the node:test suite covers it.

import { aggregatedBackgroundSuffix, userTurnBackgroundShellCount } from './backgroundWork.js'

/**
 * Fold a set of live process states into the counts the cascade reads.
 *
 * The caller picks the set: only real session processes belong in it (no
 * synthetic subagent state, no hidden session).
 *
 * @param {Iterable<Object>} processStates - Data store `processStates` entries.
 * @param {number} [unreadCount=0] - Unread sessions of the same set.
 * @returns {{processCount: number, pendingRequestCount: number, hasAssistantTurn: boolean,
 *   activeCronCount: number, backgroundShellCount: number, unreadCount: number}}
 */
export function summarizeProcessActivity(processStates, unreadCount = 0) {
    let processCount = 0
    let pendingRequestCount = 0
    let hasAssistantTurn = false
    let activeCronCount = 0
    let backgroundShellCount = 0

    for (const ps of processStates) {
        processCount++
        pendingRequestCount += ps.pending_requests?.length || 0
        if (ps.state === 'assistant_turn') hasAssistantTurn = true
        activeCronCount += ps.active_crons?.length || 0
        // Only user_turn shells: in any other state the session is working
        // (or starting) anyway, which the robot already says.
        backgroundShellCount += userTurnBackgroundShellCount(ps)
    }

    return { processCount, pendingRequestCount, hasAssistantTurn, activeCronCount, backgroundShellCount, unreadCount }
}

/**
 * Priority cascade: pending_request > unread > assistant_turn > background_shells > crons > active_process > nothing.
 * Shells beat crons, as in ProcessIndicator.
 *
 * @param {ReturnType<typeof summarizeProcessActivity>} summary
 * @returns {'pending_request'|'unread'|'assistant_turn'|'background_shells'|'crons'|'active_process'|null}
 */
export function processActivityDisplayMode(summary) {
    if (summary.pendingRequestCount > 0) return 'pending_request'
    if (summary.unreadCount > 0) return 'unread'
    if (summary.hasAssistantTurn) return 'assistant_turn'
    if (summary.backgroundShellCount > 0) return 'background_shells'
    if (summary.activeCronCount > 0) return 'crons'
    if (summary.processCount > 0) return 'active_process'
    return null
}

/**
 * Tooltip of the badge: "2 pending requests · 3 active sessions (1 background shell)".
 *
 * @param {ReturnType<typeof summarizeProcessActivity>} summary
 * @param {string|null} mode - `processActivityDisplayMode(summary)`.
 * @returns {string}
 */
export function processActivityTooltip(summary, mode) {
    const sessionLabel = `${summary.processCount} active session${summary.processCount !== 1 ? 's' : ''}`
    const backgroundSuffix = aggregatedBackgroundSuffix({
        shells: summary.backgroundShellCount,
        crons: summary.activeCronCount,
    })

    if (mode === 'pending_request') {
        const pendingLabel = summary.pendingRequestCount === 1
            ? 'Pending request'
            : `${summary.pendingRequestCount} pending requests`
        return `${pendingLabel} · ${sessionLabel}${backgroundSuffix}`
    }

    if (mode === 'unread') {
        const unreadLabel = `${summary.unreadCount} unread session${summary.unreadCount !== 1 ? 's' : ''}`
        return summary.processCount > 0
            ? `${unreadLabel} · ${sessionLabel}${backgroundSuffix}`
            : unreadLabel
    }

    return `${sessionLabel}${backgroundSuffix}`
}
