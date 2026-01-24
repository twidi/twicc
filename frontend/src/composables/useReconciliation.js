// frontend/src/composables/useReconciliation.js

import { useDataStore } from '../stores/data'
import { INITIAL_ITEMS_COUNT } from '../constants'

const MAX_RETRIES = 5

let isReconciling = false
let needsReconcileAfter = false

export function useReconciliation() {
    const store = useDataStore()

    // ═══════════════════════════════════════════════════════════════════════════
    // Public API
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Called when WebSocket reconnects.
     * Orchestrates the reconciliation process with retry logic.
     * @param {string|null} currentProjectId - Current project being viewed
     * @param {string|null} currentSessionId - Current session being viewed
     */
    async function onReconnected(currentProjectId, currentSessionId) {
        if (isReconciling) {
            // A reconciliation is already in progress, mark that we need another one after
            needsReconcileAfter = true
            return
        }

        isReconciling = true
        try {
            await reconcileWithRetry(currentProjectId, currentSessionId)

            // If a reconnection happened while we were reconciling, do it again
            while (needsReconcileAfter) {
                needsReconcileAfter = false
                await reconcileWithRetry(currentProjectId, currentSessionId)
            }
        } finally {
            isReconciling = false
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Internal logic
    // ═══════════════════════════════════════════════════════════════════════════

    /**
     * Retry reconciliation up to MAX_RETRIES times.
     * After all retries, unload any data that still failed to sync.
     */
    async function reconcileWithRetry(currentProjectId, currentSessionId) {
        let lastResult = null

        for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            const groupName = attempt === 1 ? 'Reconciliation' : `Reconciliation, try ${attempt}`
            console.group(groupName)

            try {
                lastResult = await reconcile(currentProjectId, currentSessionId)

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
    async function reconcile(currentProjectId, currentSessionId) {
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

        if (changedProjectIds.size === 0 && !currentProjectHasError && !currentSessionHasError) {
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
            remainingProjectIds.has(currentProjectId) || currentProjectHasError || currentSessionHasError
        )

        if (currentProjectNeedsUpdate) {
            const hasCurrentSession = currentSessionId != null
            const groupName = hasCurrentSession ? 'Current project/session' : 'Current project'
            console.group(groupName)

            try {
                console.log('Updating current project sessions')
                const changedSessionIds = await store.loadSessions(currentProjectId, { force: true })
                remainingProjectIds.delete(currentProjectId)

                // If current session changed OR has error, load its items immediately
                const currentSessionNeedsUpdate = currentSessionId && (
                    changedSessionIds.has(currentSessionId) || currentSessionHasError
                )
                if (currentSessionNeedsUpdate) {
                    try {
                        console.log('Updating current session')
                        await loadNewItems(currentProjectId, currentSessionId)
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
                    loadNewItems(projectId, sessionId)
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
     * Load new items for a session that has changed or had a loading error.
     * Only loads the last INITIAL_ITEMS_COUNT items to avoid fetching too much.
     * The virtual scroller will load more if the user scrolls.
     */
    async function loadNewItems(projectId, sessionId) {
        const session = store.getSession(sessionId)
        if (!session) return

        const serverLastLine = session.last_line
        const items = store.sessionItems[sessionId] || []
        const hasError = store.didSessionItemsFailToLoad(sessionId)

        // Items array is ordered by line_num, so last item has the highest line_num
        const lastItem = items.length > 0 ? items[items.length - 1] : null
        const localLastLine = lastItem?.line_num || 0

        // Nothing new and no error to retry
        if (serverLastLine <= localLastLine && !hasError) return

        // Virtual scroller will load what's missing
        const rangeStart = Math.max(localLastLine + 1, serverLastLine - INITIAL_ITEMS_COUNT + 1)

        await store.loadSessionItemsRanges(projectId, sessionId, [[rangeStart, null]])
    }

    return {
        onReconnected,
        // Expose for debugging/testing
        get isReconciling() { return isReconciling }
    }
}
