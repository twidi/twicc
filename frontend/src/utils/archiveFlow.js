/**
 * Flow of "archive a session": read the descendants, open the dialog when some
 * are listed, then archive what the user confirmed.
 * Every effect is injected: this module must load under plain `node --test`.
 *
 * Spec: docs/plans/2026-11-09-archive-with-descendants-spec.md
 */
import { buildArchiveTree } from './archiveTree.js'

export const ARCHIVE_TREE_LOADING_DELAY_MS = 250

/** Number of failures of a call to the hidden descendants endpoint. */
async function hiddenFailures(deps, projectId, sessionId, archivedIds) {
    try {
        const { failed } = await deps.archiveHidden(projectId, sessionId, archivedIds)
        return failed.length
    } catch (error) {
        console.error('Failed to archive the hidden descendants of', sessionId, error)
        return 1
    }
}

/** Rows and cron counts of the dialog. `cronCounts` includes the target. */
function readDialogData(topology, sessionId) {
    const rows = buildArchiveTree(topology, sessionId)
    const nodeCrons = new Map(topology.nodes.map((node) => [node.id, node.active_cron_count]))
    const cronCounts = Object.fromEntries([sessionId, ...rows.map((row) => row.id)].map((id) => [id, nodeCrons.get(id) ?? 0]))
    return { rows, cronCounts }
}

/**
 * @param {Object} deps
 * @param {(id: string) => ?Object} deps.getSession - Store record of a session.
 * @param {(projectId: string, id: string) => Promise<Object>} deps.readTopology
 * @param {(projectId: string, id: string, archivedIds: string[]) => Promise<{ archived: string[], failed: string[] }>} deps.archiveHidden
 * @param {(item: { id: string, projectId: string, pinned?: ?string }) => Promise<void>} deps.archiveOne
 * @param {(id: string, options?: { onConfirmed?: () => void }) => void} deps.runTodayPath - The archive
 *   with no dialog. `onConfirmed` runs when the archive proceeds.
 * @param {(state: ?Object) => void} deps.setDialog
 * @param {(count: number) => void} deps.reportFailures
 * @param {number} deps.loadingDelayMs
 * @returns {{
 *   request: (sessionId: string) => Promise<void>,
 *   confirm: (choice: { checkedIds: string[], targetOnly: boolean }) => Promise<void>,
 *   cancel: () => void,
 * }}
 */
export function createArchiveFlow(deps) {
    // The archive in progress, from its request to the end of its archives.
    // Identity check: an awaited read is stale when `run` is no longer its own.
    let run = null
    // Sessions requested during a run: only one dialog can be open.
    const waiting = []

    function showDialog(current, status, data = { rows: [], cronCounts: {} }) {
        const { sessionId, projectId, targetSession } = current
        current.dialog = { sessionId, projectId, status, targetSession, ...data }
        deps.setDialog(current.dialog)
    }

    function closeDialog(current) {
        current.dialog = null
        deps.setDialog(null)
    }

    function finishRun() {
        run = null
        // A queued session can have been archived while it waited.
        while (waiting.length > 0) {
            const next = waiting.shift()
            if (!deps.getSession(next)?.archived) {
                request(next)
                return
            }
        }
    }

    async function request(sessionId) {
        const session = deps.getSession(sessionId)
        if (!session?.spawn_root) {
            deps.runTodayPath(sessionId)
            return
        }
        if (run?.sessionId === sessionId || waiting.includes(sessionId)) return
        if (run) {
            waiting.push(sessionId)
            return
        }

        const projectId = session.project_id
        const current = { sessionId, projectId, targetSession: session, dialog: null, archiving: false }
        run = current

        const loadingTimer = setTimeout(() => {
            if (run === current) showDialog(current, 'loading')
        }, deps.loadingDelayMs)

        let data = null
        try {
            data = readDialogData(await deps.readTopology(projectId, sessionId), sessionId)
        } catch (error) {
            console.error('Failed to read the descendants of', sessionId, error)
        } finally {
            clearTimeout(loadingTimer)
        }
        if (run !== current) return

        if (!data) {
            showDialog(current, 'error')
        } else if (data.rows.length > 0) {
            showDialog(current, 'ready', data)
        } else {
            if (current.dialog) closeDialog(current)
            try {
                deps.runTodayPath(sessionId, {
                    onConfirmed: async () => {
                        const failures = await hiddenFailures(deps, projectId, sessionId, [sessionId])
                        if (failures > 0) deps.reportFailures(failures)
                    },
                })
            } finally {
                finishRun()
            }
        }
    }

    async function confirm({ checkedIds = [], targetOnly = false } = {}) {
        const current = run
        if (!current?.dialog) return
        const { sessionId, projectId, rows } = current.dialog
        // Synchronous: the double-click guard of the dialog relies on `open` going false here.
        closeDialog(current)
        current.archiving = true

        try {
            const checked = new Set(targetOnly ? [] : checkedIds)
            const items = [
                { id: sessionId, projectId },
                ...rows
                    .filter((row) => checked.has(row.id))
                    .map((row) => ({ id: row.id, projectId: row.session.project_id, pinned: row.session.pinned })),
            ]
            // Together: the hidden call kills agents one by one and can take seconds,
            // and the listed sessions must not wait for it. The backend walk accepts
            // `archived_ids` that are already archived.
            const [hiddenFailureCount, results] = await Promise.all([
                checked.size > 0 ? hiddenFailures(deps, projectId, sessionId, [sessionId, ...checked]) : 0,
                Promise.allSettled(items.map(async (item) => deps.archiveOne(item))),
            ])
            let failures = hiddenFailureCount
            for (const [index, result] of results.entries()) {
                if (result.status === 'rejected') {
                    console.error('Failed to archive', items[index].id, result.reason)
                    failures++
                }
            }
            if (failures > 0) deps.reportFailures(failures)
        } finally {
            finishRun()
        }
    }

    function cancel() {
        if (!run || run.archiving) return
        if (run.dialog) closeDialog(run)
        finishRun()
    }

    return { request, confirm, cancel }
}
