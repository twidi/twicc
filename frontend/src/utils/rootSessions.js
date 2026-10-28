import { computed, toRaw } from 'vue'
import { hasActiveStartupPhase } from './startupPhase.js'
import { retainSessionArray } from './retain.js'
import { noteStageRun } from './stageRuns.js'

// Root-session index (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.1).
// One lazily created computed per raw `sessions` object. The computed keeps the
// first owner seen with that object and reads its `startupProgress`. This is
// correct because the store and its state share one raw `sessions` and one
// `startupProgress`; a second owner with its own `startupProgress` and the same
// `sessions` would read the first owner's.
const indexes = new WeakMap()

/**
 * The session records with no `parent_session_id`, in `Object.values(owner.sessions)`
 * order, always the reactive records. Reads `parent_session_id` only; during a
 * startup phase it reads it without tracking, and reruns on each progress
 * message (the phase is read from `owner.startupProgress` on each run).
 * Retains the previous array when the members are the same.
 *
 * @param {{ sessions: Object, startupProgress?: ?Object }} owner - a store or its state
 * @returns {Object[]}
 */
export function getRootSessions(owner) {
    const sessions = owner.sessions
    const key = toRaw(sessions)
    let index = indexes.get(key)
    if (!index) {
        index = computed(previous => {
            noteStageRun('rootSessions')
            let next
            if (hasActiveStartupPhase(owner.startupProgress)) {
                // Track membership only: the compute flood must not subscribe to every record.
                const raw = toRaw(sessions)
                next = []
                for (const id of Object.keys(sessions)) {
                    if (!raw[id].parent_session_id) next.push(sessions[id])
                }
            } else {
                next = Object.values(sessions).filter(session => !session.parent_session_id)
            }
            return retainSessionArray(previous, next)
        })
        indexes.set(key, index)
    }
    return index.value
}
