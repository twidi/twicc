// frontend/src/composables/useReconciliation.js

import { useDataStore } from '../stores/data'
import { sweepLoadedSessions } from './reconcileSweep'

const MAX_RETRIES = 5

let isReconciling = false
let needsReconcileAfter = false

export function useReconciliation() {
    const store = useDataStore()

    // ═══════════════════════════════════════════════════════════════════════════
    // Public API
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Called when WebSocket connects or reconnects.
     * Orchestrates the reconciliation process with retry logic.
     * @param {string|null} currentProjectId - Current project being viewed
     * @param {string|null} currentSessionId - Current session being viewed
     * @param {boolean} isReconnection - True for a real reconnect (was connected,
     *   socket dropped), false on the first connect. Gates auto-opening the
     *   focused session's outage edits: on first connect the "new" tail is just
     *   the initial load, which must NOT auto-open every historical diff.
     */
    async function onReconnected(currentProjectId, currentSessionId, isReconnection = false) {
        if (isReconciling) {
            // A reconciliation is already in progress, mark that we need another one after
            needsReconcileAfter = true
            return
        }

        isReconciling = true
        try {
            await reconcileWithRetry(currentProjectId, currentSessionId, isReconnection)

            // If a reconnection happened while we were reconciling, do it again
            while (needsReconcileAfter) {
                needsReconcileAfter = false
                await reconcileWithRetry(currentProjectId, currentSessionId, isReconnection)
            }
        } finally {
            isReconciling = false
            // The pre-outage mtimes have served every pass; the next disconnect
            // captures a fresh set (store.captureSyncBaseline on CLOSED).
            store.clearSyncBaseline()
        }

        // The passes above only GUARANTEE the focused session: every other
        // loaded session went through the mtime change-set, and the baseline
        // above cannot cover a session loaded during the outage or a fetch
        // that failed all retries. Sweep every loaded session — refresh its
        // record, then heal its holes — so no open pane is left stale until
        // the user happens to re-activate it.
        const sweepFailed = await sweepLoadedSessions(store)
        if (sweepFailed.length) {
            console.warn('Reconciliation sweep: coverage still incomplete for', sweepFailed)
        }

        // Items are back in sync, but the tool_state / agent_link broadcasts
        // that stop tool spinners were dropped while the socket was down and the
        // watcher never replays a line it already processed. Re-pull the link
        // caches for EVERY loaded session (the only ones that can show a
        // spinner), not just the focused / mtime-changed ones, so
        // officially-finished tools stop spinning.
        await store.refreshAllLoadedToolStates()

        // Items are now in sync — audit in-flight sends whose error frame
        // may have been lost while the WebSocket was down (send-failure
        // recovery: resolve the delivered ones, surface the lost ones).
        store.auditAllLoadedInflightSends()
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Internal logic
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Retry reconciliation up to MAX_RETRIES times.
     * After all retries, unload any data that still failed to sync.
     */
    async function reconcileWithRetry(currentProjectId, currentSessionId, isReconnection = false) {
        let lastResult = null

        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            const groupName = attempt === 1 ? 'Reconciliation' : `Reconciliation, try ${attempt}`
            console.group(groupName)

            try {
                lastResult = await reconcile(currentProjectId, currentSessionId, isReconnection)

                if (!lastResult.hasErrors) {
                    console.groupEnd()
                    return // Success, all done
                }

                // Log failures for this attempt
                logFailures(lastResult)
            } finally {
                console.groupEnd()
            }

            // On next attempt, the mtime comparisons will only retry what failed
            // because successful updates changed the local mtime
        }

        // After all retries, unload what still failed (from the LAST attempt only)
        // BUT: don't unload current project/session to avoid showing empty page to user

        console.group('Reconciliation unloading')
        const unloadedProjectIds = new Set()

        // Unload failed projects first (this also unloads all their sessions)
        for (const projectId of lastResult.failedProjectIds) {
            if (projectId === currentProjectId) {
                console.log(`Skipping unload of current project ${projectId} to preserve user view`)
                continue
            }
            store.unloadProject(projectId)
            unloadedProjectIds.add(projectId)
            console.log(`Unloaded project ${projectId}`)
        }

        // Unload failed sessions (skip if their project was already unloaded)
        for (const { projectId, sessionId } of lastResult.failedSessions) {
            if (sessionId === currentSessionId) {
                console.log(`Skipping unload of current session ${sessionId} to preserve user view`)
                continue
            }
            if (unloadedProjectIds.has(projectId)) {
                // Project was already unloaded, which unloaded all its sessions
                continue
            }
            store.unloadSession(sessionId)
            console.log(`Unloaded session ${sessionId}`)
        }
        console.groupEnd()
    }

    /**
     * Log failures from a reconciliation attempt.
     */
    function logFailures(result) {
        if (!result.hasErrors) return

        console.group('Failures')
        for (const projectId of result.failedProjectIds) {
            console.log(`Failed to load sessions for project ${projectId}`)
        }
        for (const { projectId, sessionId } of result.failedSessions) {
            console.log(`Failed to load items for session ${sessionId} of project ${projectId}`)
        }
        console.groupEnd()
    }

    /**
     * Main reconciliation logic.
     * Loads changed data with priority to current view.
     * @returns {Promise<{hasErrors: boolean, failedProjectIds: string[], failedSessions: Array<{projectId, sessionId}>}>}
     */
    async function reconcile(currentProjectId, currentSessionId, isReconnection = false) {
        let hasErrors = false
        const failedProjectIds = []
        const failedSessions = [] // [{ projectId, sessionId }, ...]

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 1: Load all projects
        // ═══════════════════════════════════════════════════════════════════════
        console.log('Updating projects')
        let changedProjectIds
        try {
            changedProjectIds = await store.loadProjects()
        } catch (error) {
            console.error('Failed to load projects:', error)
            return { hasErrors: true, failedProjectIds: [], failedSessions: [] }
        }

        const currentProjectHasError = currentProjectId && store.didSessionsFailToLoad(currentProjectId)
        const currentSessionHasError = currentSessionId && store.didSessionItemsFailToLoad(currentSessionId)

        // Defensive: always re-check the focused session on reconnect, even when
        // no project mtime changed. A project's mtime can read stale at the
        // instant we poll (watcher lag), and items written during the outage had
        // their WS broadcast dropped — so relying solely on the mtime delta can
        // silently miss the current session. Reloading its project's sessions is
        // cheap and makes the visible conversation the one thing we never leave
        // behind.
        const hasCurrentSession = currentSessionId != null

        if (changedProjectIds.size === 0 && !currentProjectHasError && !currentSessionHasError && !hasCurrentSession) {
            console.log('Nothing to update')
            return { hasErrors: false, failedProjectIds: [], failedSessions: [] }
        }

        const remainingProjectIds = new Set(changedProjectIds)
        const remainingSessions = [] // [{ projectId, sessionId }, ...]

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 2: Priority chain (current project → current session)
        // Also retry if current project/session has a loading error
        // ═══════════════════════════════════════════════════════════════════════
        const currentProjectNeedsUpdate = currentProjectId && (
            remainingProjectIds.has(currentProjectId) || currentProjectHasError || currentSessionHasError || hasCurrentSession
        )

        if (currentProjectNeedsUpdate) {
            const groupName = hasCurrentSession ? 'Current project/session' : 'Current project'
            console.group(groupName)

            try {
                console.log('Updating current project sessions')
                const changedSessionIds = await store.loadSessions(currentProjectId, { force: true })
                remainingProjectIds.delete(currentProjectId)

                // ALWAYS re-check the focused session's items, not only when its
                // mtime differs. The mtime comparison races with the reconnected
                // WebSocket stream: any session_updated received while this
                // reconciliation was in flight (watcher broadcast for a working
                // session, session_viewed echo from the wake-up presence ping…)
                // already refreshed the local mtime to the server value — the
                // session then reads as "unchanged" while the items written
                // during the outage were never loaded. loadNewItems is cheap
                // when there is nothing to do.
                const currentSessionNeedsUpdate = !!currentSessionId
                if (currentSessionNeedsUpdate) {
                    try {
                        console.log('Updating current session')
                        // markNewLive only on a real reconnect and only for the
                        // focused session, so edits written during the outage
                        // auto-open (auto-open-diffs) like real-time ones — but
                        // only where the user is looking, and never on first
                        // connect (which would open every historical diff).
                        await loadNewItems(currentSessionId, isReconnection)
                    } catch (error) {
                        console.error(`Failed to load items for current session:`, error)
                        failedSessions.push({ projectId: currentProjectId, sessionId: currentSessionId })
                        hasErrors = true
                    }
                    changedSessionIds.delete(currentSessionId)
                }

                // Other sessions from current project go to parallel batch
                for (const sessionId of changedSessionIds) {
                    remainingSessions.push({ projectId: currentProjectId, sessionId })
                }
            } catch (error) {
                console.error(`Failed to load sessions for current project:`, error)
                failedProjectIds.push(currentProjectId)
                hasErrors = true
            }

            console.groupEnd()
        }

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 3: Remaining projects (in parallel)
        // ═══════════════════════════════════════════════════════════════════════
        if (remainingProjectIds.size > 0) {
            console.group('Updating sessions lists')

            const projectIdsArray = [...remainingProjectIds]
            for (const projectId of projectIdsArray) {
                console.log(`Project ${projectId}`)
            }

            const results = await Promise.allSettled(
                projectIdsArray.map(projectId =>
                    store.loadSessions(projectId, { force: true })
                        .then(changedIds => ({ projectId, changedIds, success: true }))
                        .catch(error => ({ projectId, error, success: false }))
                )
            )

            for (const result of results) {
                if (result.status === 'fulfilled') {
                    const { projectId, changedIds, success } = result.value
                    if (success) {
                        for (const sessionId of changedIds) {
                            remainingSessions.push({ projectId, sessionId })
                        }
                    } else {
                        failedProjectIds.push(projectId)
                        hasErrors = true
                    }
                } else {
                    // Promise itself rejected (shouldn't happen with our .catch, but defensive)
                    hasErrors = true
                }
            }

            console.groupEnd()
        }

        // ═══════════════════════════════════════════════════════════════════════
        // STEP 4: Remaining sessions (in parallel)
        // ═══════════════════════════════════════════════════════════════════════
        if (remainingSessions.length === 0) {
            console.log('No sessions to update')
        } else {
            console.group('Updating sessions')

            for (const { projectId, sessionId } of remainingSessions) {
                console.log(`Session ${sessionId} of project ${projectId}`)
            }

            const results = await Promise.allSettled(
                remainingSessions.map(({ projectId, sessionId }) =>
                    loadNewItems(sessionId)
                        .then(() => ({ projectId, sessionId, success: true }))
                        .catch(error => ({ projectId, sessionId, error, success: false }))
                )
            )

            for (const result of results) {
                if (result.status === 'fulfilled' && !result.value.success) {
                    const { projectId, sessionId } = result.value
                    failedSessions.push({ projectId, sessionId })
                    hasErrors = true
                }
            }

            console.groupEnd()
        }

        return { hasErrors, failedProjectIds, failedSessions }
    }

    /**
     * Bring a session's items up to date with the server after a disconnect.
     *
     * Delegates gap detection to store.ensureSessionItemsCoverage: it loads the
     * missing tail-window lines (with content) and restores metadata over bare
     * placeholders (holes left by broadcasts lost during the outage) so the
     * scroller's gap-fill can take over. A plain "server last_line vs our last
     * item" check is NOT enough — a live item received right after reconnect
     * extends the items array over the gap, hiding the missing lines.
     *
     * Throws when a fetch failed, so the retry logic re-runs it.
     *
     * Tool / agent / workflow link caches are NOT refreshed here: that runs in a
     * single pass over every loaded session once items are settled
     * (store.refreshAllLoadedToolStates() in onReconnected). Doing it per-item
     * here would only cover the focused + mtime-changed sessions and miss other
     * open panes whose dropped tool_state broadcasts also stranded spinners.
     *
     * @param {boolean} markNewLive - Flag the not-yet-recovered tail lines as
     *   live (store.markNewTailItemsLive) so auto-open-diffs opens edits made
     *   during the outage. Set only on a real reconnect, for the focused session.
     */
    async function loadNewItems(sessionId, markNewLive = false) {
        const session = store.getSession(sessionId)
        if (!session) return

        // Flag the not-yet-recovered tail lines as "live" BEFORE the load, so
        // auto-open-diffs opens edits written during the outage — even if a
        // concurrent gap-heal coalesces the coverage call below (see
        // store.markNewTailItemsLive). Active session only (caller's flag).
        if (markNewLive) store.markNewTailItemsLive(sessionId)

        const ok = await store.ensureSessionItemsCoverage(sessionId)
        if (!ok) {
            throw new Error(`Failed to load missing items for session ${sessionId}`)
        }
    }

    return {
        onReconnected,
        // Expose for debugging/testing
        get isReconciling() { return isReconciling }
    }
}
