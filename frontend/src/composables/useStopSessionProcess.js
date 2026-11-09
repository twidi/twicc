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
 *
 * An archive request of a session with spawned sessions first goes through
 * `archiveFlow`, which can open the single <ArchiveDescendantsDialog> mounted
 * in App.vue (`archiveDialogState`) in place of the confirmation.
 */
import { ref, shallowRef } from 'vue'
import { useDataStore } from '../stores/data'
import { killProcess } from './useWebSocket'
import { toast } from './useToast'
import { PROCESS_STATE } from '../constants'
import { localUiMessage } from '../i18n/uiMessages.js'
import { apiFetch } from '../utils/api'
import { ARCHIVE_TREE_LOADING_DELAY_MS, createArchiveFlow } from '../utils/archiveFlow.js'
import { getStopConfirmation } from '../utils/backgroundWork'

// Module-scoped: shared across every consumer of the composable.
// null when no confirmation dialog is requested.
// Shape: { sessionId, projectId, mode: 'stop' | 'archive', cronCount, shellCount, onConfirmed? }
export const pendingConfirmation = ref(null)

// null when the archive dialog is closed.
// Shape: { sessionId, projectId, status, targetSession, rows, cronCounts }
// Shallow: the dialog resets its checkboxes when the `rows` reference changes.
export const archiveDialogState = shallowRef(null)

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
 *
 * @returns {boolean} Whether the kill ran: false when a stop is already in progress.
 */
function doKill(store, sessionId, { archive = false, projectId = null } = {}) {
    if (store.isSessionStopping(sessionId)) return false  // debounce re-entrance
    store.setSessionStopping(sessionId)
    killProcess(sessionId)
    if (archive) {
        const pid = projectId ?? store.getSession(sessionId)?.project_id
        if (pid) store.setSessionArchived(pid, sessionId, true)
    }
    return true
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
 * @returns {boolean} Whether the stop or the archive proceeded.
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
            return true
        }
        return false
    }

    return doKill(store, sessionId, { archive, projectId })
}

/**
 * Stop the process of a session, with the crons / background shells confirmation.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false]
 * @param {function(): void} [options.onConfirmed] - Runs when the archive proceeds:
 *   at once, or when the user confirms.
 */
function stopWithConfirmation(sessionId, { archive = false, onConfirmed = null } = {}) {
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
            onConfirmed,
        }
        return
    }

    if (stopSessionProcessUnconfirmed(sessionId, { archive })) onConfirmed?.()
}

async function readTopology(projectId, sessionId) {
    const response = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/topology/`)
    if (!response.ok) throw new Error(`Topology read failed with HTTP ${response.status}`)
    return response.json()
}

async function archiveHidden(projectId, sessionId, archivedIds) {
    const response = await apiFetch(
        `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/archive-hidden-descendants/`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ archived_ids: archivedIds }),
        },
    )
    if (!response.ok) throw new Error(`Hidden descendants archive failed with HTTP ${response.status}`)
    return response.json()
}

/**
 * Stop a session if it is stoppable, then archive it. Unlike `doKill`, a stop
 * already in progress does not skip the archive: the user confirmed it.
 */
async function archiveOne({ id, projectId, pinned }) {
    const store = useDataStore()
    if (isStoppable(store.getProcessState(id)) && !store.isSessionStopping(id)) {
        store.setSessionStopping(id)
        killProcess(id)
    }
    await store.setSessionArchived(projectId, id, true, { knownPinned: pinned })
}

const archiveFlow = createArchiveFlow({
    getSession: (id) => useDataStore().getSession(id),
    readTopology,
    archiveHidden,
    archiveOne,
    runTodayPath: (id, options) => stopWithConfirmation(id, { archive: true, ...options }),
    setDialog: (state) => { archiveDialogState.value = state },
    reportFailures: (count) => toast.error(localUiMessage('session.archiveDescendants.failed', { count }, count)),
    loadingDelayMs: ARCHIVE_TREE_LOADING_DELAY_MS,
})

/**
 * Stop the process of a session. Handles the confirmation (active crons that
 * would be lost, background shells that would be killed),
 * the archive variant, and the "no process running" no-op.
 *
 * @param {string} sessionId
 * @param {Object} [options]
 * @param {boolean} [options.archive=false] - Also archive the session after stop.
 *   If `archive` is true and no process is running, archives the session outright.
 *   A session with spawned sessions can open the archive dialog instead.
 */
export function stopSessionProcess(sessionId, { archive = false } = {}) {
    if (archive) {
        archiveFlow.request(sessionId)
        return
    }
    stopWithConfirmation(sessionId)
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
    const archive = (mode ?? pending.mode) === 'archive'
    const proceeded = doKill(store, pending.sessionId, { archive, projectId: pending.projectId })
    if (archive && proceeded) pending.onConfirmed?.()
}

/**
 * Called by the global StopProcessConfirmDialog when the user dismisses.
 */
export function cancelPendingStop() {
    pendingConfirmation.value = null
}

/**
 * Called by the global ArchiveDescendantsDialog when the user confirms.
 *
 * @param {{ checkedIds: string[], targetOnly: boolean }} payload
 */
export function confirmArchiveDialog(payload) {
    return archiveFlow.confirm(payload)
}

/**
 * Called by the global ArchiveDescendantsDialog when the user dismisses.
 */
export function cancelArchiveDialog() {
    archiveFlow.cancel()
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
        confirmArchiveDialog,
        cancelArchiveDialog,
        archiveDialogState,
    }
}
