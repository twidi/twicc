import { backgroundWorkStatusKey } from '../utils/backgroundWork.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'

/**
 * Apply a `process_background_work` message to the process-state map.
 *
 * What still runs behind an agent changed (a background shell started or
 * ended, a subagent finished, …) without any state transition. Patched in
 * place: a `process_state` rebuild would drop the live tool list. A session
 * with no known process is left alone — its next snapshot carries the value.
 *
 * @param {Object} processStates - The store's `processStates` map.
 * @param {Object} message - `{session_id, background_work_in_progress}`.
 * @param {number} [nowSeconds] - Current epoch time in seconds.
 * @returns {boolean} Whether the session's USER_TURN bottom status line
 *   (background shells, active crons) changed, i.e. its visual items need a
 *   recompute. False when no process state was patched.
 */
export function applyBackgroundWork(processStates, message, nowSeconds = Date.now() / 1000) {
    const processState = processStates[message.session_id]
    if (!processState) return false
    const previousKey = backgroundWorkStatusKey(processState, nowSeconds)
    const next = message.background_work_in_progress || null
    if (!jsonValuesEqual(processState.background_work_in_progress, next)) processState.background_work_in_progress = next
    return backgroundWorkStatusKey(processState, nowSeconds) !== previousKey
}

/**
 * Whether a session leaving the store (`session_removed`: it went hidden) takes
 * its process state along.
 *
 * A real process state must go: the backend never broadcasts a hidden
 * session's state again (not even `dead`), and with the row gone nothing could
 * tell it is hidden, so every aggregated badge would keep counting it. A
 * synthetic subagent state is the run model's, cleaned up by `unloadSession`.
 *
 * @param {Object} processStates - The store's `processStates` map.
 * @param {string} sessionId
 * @returns {boolean}
 */
export function dropsProcessStateOnRemoval(processStates, sessionId) {
    const processState = processStates[sessionId]
    return !!processState && !processState.synthetic
}

/**
 * Whether a `process_state` message may notify (toast, sound, browser
 * notification). A `resync` (a session made visible again mid-run) restores a
 * state the client dropped: it is no transition, so it notifies nothing.
 *
 * @param {Object} message - The `process_state` message.
 * @returns {boolean}
 */
export function shouldNotifyProcessState(message) {
    return !message.resync
}
