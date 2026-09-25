import { PROCESS_STATE } from '../../constants.js'

/**
 * Whether a Codex shell chain (`exec_command` family, code-mode `exec`) is
 * still running, from its aggregated tool state.
 *
 * The backend sets `extra.is_terminated` on the link that closes the chain:
 * the poll reporting the exit, or the process's own `CommandExecution` end
 * event rebound to the call that started it. Without it, the chain is
 * running — except in USER_TURN, where nothing polls it any more: an
 * interrupt mid-chain leaves the last chunk reporting a running process, and
 * no closing chunk will ever come. The exception to that exception is a
 * process the agent left running on purpose, whose end Codex reports on its
 * own: while the agent still counts background shells, an unterminated card
 * may be one of them and keeps spinning. Only an explicit USER_TURN is
 * trusted — null/unknown (process not yet synced, or dead/historical, which
 * `isStaleToolUse` handles) keeps the "assume running" behaviour.
 *
 * @param {Object} options
 * @param {string|Object|null} [options.extra] - `toolState.extra` (JSON string or object).
 * @param {string|null} [options.processState] - The session's process state.
 * @param {number} [options.backgroundShells] - `background_work_in_progress.shells`.
 * @returns {boolean}
 */
export function isExecChainRunning({ extra, processState, backgroundShells = 0 } = {}) {
    if (extra) {
        try {
            const parsed = typeof extra === 'string' ? JSON.parse(extra) : extra
            if (parsed?.is_terminated) return false
        } catch {
            // Malformed extra → fall through to the liveness gate.
        }
    }
    if (processState === PROCESS_STATE.USER_TURN) {
        return backgroundShells > 0
    }
    return true
}
