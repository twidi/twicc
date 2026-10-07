// frontend/src/utils/orchestrationActivity.js
//
// What the Orchestration tab's activity indicator reads: the live processes of
// the sessions under the current one, and whether one of its subagents runs.
// Pure (no store, no Vue) so the node:test suite covers it.

import { userTurnBackgroundShellCount } from './backgroundWork.js'

/**
 * The live process states of the sessions the current session spawned, at any
 * depth (children, grandchildren, ...), that are doing something.
 *
 * Out: the session itself, its ancestors and every other branch (the
 * backend's ``spawn_ancestors`` chain decides membership), synthetic subagent
 * states, ephemeral runs, hidden sessions (their activity must never surface
 * on its own — the backend already keeps them out of the broadcasts; the
 * guard covers rows explicitly loaded via show_hidden), and ``user_turn``
 * sessions (the indicator only says that something is being done) — except
 * those that still run a background shell behind their finished turn: a
 * process really is running, and the project badges show it. Crons stay out.
 *
 * @param {Object<string, Object>} processStates - Data store ``processStates``.
 * @param {Object<string, Object>} sessions - Data store ``sessions`` (rows).
 * @param {string} sessionId - The current session.
 * @returns {Object[]} The matching process states.
 */
export function descendantActiveProcessStates(processStates, sessions, sessionId) {
    const states = []
    for (const id of Object.keys(processStates)) {
        // Skip the owner before reading its state: replacement is unrelated activity.
        if (id === sessionId) continue
        const ps = processStates[id]
        if (ps.synthetic || ps.extra?.ephemeral) continue
        if (sessions[id]?.hidden) continue
        if (ps.state === 'user_turn' && userTurnBackgroundShellCount(ps) === 0) continue
        if (!ps.spawn_ancestors?.includes(sessionId)) continue
        states.push(ps)
    }
    return states
}

/**
 * Whether at least one agent of an agent tree (``buildAgentTree`` nodes, any
 * depth) is running.
 *
 * @param {Array<{id: string, children: Array}>} nodes
 * @param {function(string): boolean} isRunning
 * @returns {boolean}
 */
export function hasRunningAgent(nodes, isRunning) {
    return nodes.some(node => isRunning(node.id) || hasRunningAgent(node.children, isRunning))
}

/**
 * Which of the two Orchestration indicators have something to show.
 *
 * @param {{sessions: Object|null, subagentsRunning: boolean}|null} activity - Store ``getOrchestrationActivity``.
 * @param {'sessions'|'subagents'|null} [only] - Restrict to one indicator (the view switch's inactive segment).
 * @returns {{sessions: Object|null, subagents: boolean}}
 */
export function visibleOrchestrationIndicators(activity, only = null) {
    return {
        sessions: only === 'subagents' ? null : (activity?.sessions ?? null),
        subagents: only === 'sessions' ? false : !!activity?.subagentsRunning,
    }
}
