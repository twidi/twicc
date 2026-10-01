import { backgroundWorkStatusKey } from '../utils/backgroundWork.js'

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
    processState.background_work_in_progress = message.background_work_in_progress || null
    return backgroundWorkStatusKey(processState, nowSeconds) !== previousKey
}
