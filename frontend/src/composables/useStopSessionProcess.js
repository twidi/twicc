/**
 * useStopSessionProcess - Centralized "stop a session's process" flow.
 *
 * Single entry point for every UI that can stop a session's Claude Code
 * process (header stop button, sidebar dropdown, triple-Escape shortcut).
 *
 * Exposes module-scoped reactive state (`pendingConfirmation`) consumed by a
 * single <StopProcessConfirmDialog> mounted globally in App.vue. Components
 * only call `stopSessionProcess(sessionId, { archive? })` and forget about
 * the crons / background shells / confirmation / flag mechanics. Batch flows (session list
 * multi-select) provide their own aggregated confirmation and call
 * `stopSessionProcessUnconfirmed` instead.
 */
import { ref } from 'vue'
import { useDataStore } from '../stores/data'
import { killProcess } from './useWebSocket'
import { PROCESS_STATE } from '../constants'
import { getStopConfirmation } from '../utils/backgroundWork'

// Module-scoped: shared across every consumer of the composable.
// null when no confirmation dialog is requested.
// Shape: { sessionId, projectId, mode: 'stop' | 'archive', cronCount, shellCount }
export const pendingConfirmation = ref(null)

/**
 * Whether a session's process is stoppable right now.
 *
 * Mirrors `canStopProcess` from SessionHeader (which also excludes synthetic
 * process states — used for subagent visual indicators, not real killable
 * processes). The sidebar's legacy `canStop` does not check `synthetic`
 * because in practice the session list only renders real user sessions,
 * so the effective behavior is identical.
 */
export function isStoppable(processState) {
    return Boolean(
        processState
        && !processState.synthetic
        && processState.state
        && processState.state !== PROCESS_STATE.DEAD
    )
}

/**
 * Execute the actual kill + optional archive for a session.
 * Also sets the per-session "stopping" flag in the store for UI feedback.
 */
function doKill(store, sessionId, { archive = false, projectId = null } = {}) {
    if (store.isSessionStopping(sessionId)) return  // debounce re-entrance
    store.setSessionStopping(sessionId)
    killProcess(sessionId)
    if (archive) {
        const pid = projectId ?? store.getSession(sessionId)?.project_id
        if (pid) store.setSessionArchived(pid, sessionId, true)
    }
}

/**
 * Stop the process of a session WITHOUT the crons / background shells confirmation.
 *
 * Used by batch flows (session list multi-select) that show ONE aggregated
 * confirmation dialog for the whole batch and must not trigger the
 * single-session `pendingConfirmation` dialog per session. Handles the
 * archive variant and the "no process running" archive-only path.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false] - Also archive the session after stop.
 *   If `archive` is true and no process is running, archives the session outright.
 */
export function stopSessionProcessUnconfirmed(sessionId, { archive = false } = {}) {
    const store = useDataStore()
    const processState = store.getProcessState(sessionId)
    const session = store.getSession(sessionId)
    const projectId = session?.project_id ?? null

    if (!isStoppable(processState)) {
        // Archive-only path: no running process, just archive if requested.
        if (archive && projectId && session && !session.archived) {
            store.setSessionArchived(projectId, sessionId, true)
        }
        return
    }

    doKill(store, sessionId, { archive, projectId })
}

/**
 * Stop the process of a session. Handles the confirmation (active crons that
 * would be lost, background shells that would be killed),
 * the archive variant, and the "no process running" no-op.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false] - Also archive the session after stop.
 *   If `archive` is true and no process is running, archives the session outright.
 */
export function stopSessionProcess(sessionId, { archive = false } = {}) {
    const store = useDataStore()
    const processState = store.getProcessState(sessionId)

    // The isStoppable guard is load-bearing: the non-stoppable early-return now
    // lives in the delegate, so without it a dead/synthetic state carrying stale
    // active_crons would trigger a confirmation where the old code archived/no-oped.
    const confirmation = isStoppable(processState) ? getStopConfirmation(processState) : null
    if (confirmation) {
        const session = store.getSession(sessionId)
        pendingConfirmation.value = {
            sessionId,
            projectId: session?.project_id ?? null,
            mode: archive ? 'archive' : 'stop',
            cronCount: confirmation.cronCount,
            shellCount: confirmation.shellCount,
        }
        return
    }

    stopSessionProcessUnconfirmed(sessionId, { archive })
}

/**
 * Hard-kill a session's process: SIGKILL the process tree now, no grace window.
 *
 * Deliberately skips the crons / background shells confirmation (the triggering gesture —
 * Shift, or escalating an in-flight stop — is already explicit) and the
 * re-entrance debounce (you can force-kill *while* a soft stop is running).
 * Sets the "stopping" flag so the spinner reacts immediately; the backend is
 * authoritative from there.
 */
export function hardKillSessionProcess(sessionId) {
    const store = useDataStore()
    store.setSessionStopping(sessionId)
    killProcess(sessionId, { force: true })
}

/**
 * Called by the global StopProcessConfirmDialog when the user confirms.
 * The `mode` carried by the dialog payload overrides the pending mode
 * (they are always the same today, but honoring the payload is defensive).
 */
export function confirmPendingStop({ mode } = {}) {
    const pending = pendingConfirmation.value
    if (!pending) return
    pendingConfirmation.value = null
    const store = useDataStore()
    doKill(store, pending.sessionId, {
        archive: (mode ?? pending.mode) === 'archive',
        projectId: pending.projectId,
    })
}

/**
 * Called by the global StopProcessConfirmDialog when the user dismisses.
 */
export function cancelPendingStop() {
    pendingConfirmation.value = null
}

/**
 * Composable wrapper — returns the module-scoped refs and functions so
 * callers can use the standard `const { stopSessionProcess } = useStopSessionProcess()`
 * pattern if they prefer. (Direct named imports also work.)
 */
export function useStopSessionProcess() {
    return {
        stopSessionProcess,
        stopSessionProcessUnconfirmed,
        hardKillSessionProcess,
        isStoppable,
        confirmPendingStop,
        cancelPendingStop,
        pendingConfirmation,
    }
}
