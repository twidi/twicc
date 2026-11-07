import { i18n } from '../i18n/index.js'

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
 * @param {{sessions: number, shells: number}} [unreadBackground] - Among the unread
 *   sessions, those with a shell or a cron (`sessions`) and those with a shell (`shells`).
 * @returns {{processCount: number, pendingRequestCount: number, hasAssistantTurn: boolean,
 *   activeCronCount: number, backgroundShellCount: number, unreadCount: number,
 *   unreadBackgroundCount: number, unreadShellCount: number}}
 */
export function summarizeProcessActivity(processStates, unreadCount = 0, unreadBackground = { sessions: 0, shells: 0 }) {
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

    return {
        processCount,
        pendingRequestCount,
        hasAssistantTurn,
        activeCronCount,
        backgroundShellCount,
        unreadCount,
        unreadBackgroundCount: unreadBackground.sessions,
        unreadShellCount: unreadBackground.shells,
    }
}

/**
 * Priority cascade: pending_request > unread_background / unread > assistant_turn > background_shells > crons > active_process > nothing.
 * Shells beat crons, as in ProcessIndicator. `unread_background` replaces `unread`
 * when every unread session has a shell or a cron.
 *
 * @param {ReturnType<typeof summarizeProcessActivity>} summary
 * @returns {'pending_request'|'unread_background'|'unread'|'assistant_turn'|'background_shells'|'crons'|'active_process'|null}
 */
export function processActivityDisplayMode(summary) {
    if (summary.pendingRequestCount > 0) return 'pending_request'
    if (summary.unreadCount > 0) {
        return summary.unreadBackgroundCount === summary.unreadCount ? 'unread_background' : 'unread'
    }
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
    const sessionLabel = i18n.global.t('session.activity.activeCount', { count: summary.processCount }, summary.processCount)
    const backgroundSuffix = aggregatedBackgroundSuffix({
        shells: summary.backgroundShellCount,
        crons: summary.activeCronCount,
    })

    if (mode === 'pending_request') {
        const pendingLabel = i18n.global.t('session.activity.pendingCount', { count: summary.pendingRequestCount }, summary.pendingRequestCount)
        return `${pendingLabel} · ${sessionLabel}${backgroundSuffix}`
    }

    if (mode === 'unread' || mode === 'unread_background') {
        const unreadLabel = i18n.global.t('session.activity.unreadCount', { count: summary.unreadCount }, summary.unreadCount)
        return summary.processCount > 0
            ? `${unreadLabel} · ${sessionLabel}${backgroundSuffix}`
            : unreadLabel
    }

    return `${sessionLabel}${backgroundSuffix}`
}
