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
 * @returns {boolean} Whether a process state was patched.
 */
export function applyBackgroundWork(processStates, message) {
    const processState = processStates[message.session_id]
    if (!processState) return false
    processState.background_work_in_progress = message.background_work_in_progress || null
    return true
}
