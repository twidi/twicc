// frontend/src/utils/syncBaseline.js
//
// Pre-outage mtime snapshot for the reconnect reconciliation.
//
// The reconciliation decides what to reload by comparing each project's and
// session's local `mtime` against the fresh REST payload. But those local
// values are also overwritten by the live `project_updated` /
// `session_updated` frames — and the reopened socket delivers such frames
// WHILE the reconciliation's fetches are in flight. A frame that lands first
// makes the row read as "unchanged", and everything written during the outage
// is silently left out (the items broadcasts for it were lost with the
// socket).
//
// The baseline freezes the comparison point at the moment the socket closed:
// nothing received afterwards can move it. Captured on CLOSED, consumed by
// the change detection, cleared once the reconciliation has settled.

export function createSyncBaseline() {
    let captured = null // { projects: Map<id, mtime>, sessions: Map<id, mtime> } | null

    const snapshotMtimes = (rows) => {
        const map = new Map()
        for (const [id, row] of Object.entries(rows || {})) {
            map.set(id, row?.mtime)
        }
        return map
    }

    const lookup = (map, id, fallback) => (map.has(id) ? map.get(id) : fallback)

    return {
        /** True while a captured baseline is waiting for a reconciliation. */
        get pending() {
            return captured !== null
        },

        /**
         * Freeze the current mtimes. Ignored while a baseline is pending: a
         * second disconnect during the reconciliation must keep the ORIGINAL
         * pre-outage values, not mtimes that live frames already refreshed.
         * @returns {boolean} true if this call captured the baseline.
         */
        capture({ projects, sessions }) {
            if (captured) return false
            captured = { projects: snapshotMtimes(projects), sessions: snapshotMtimes(sessions) }
            return true
        },

        clear() {
            captured = null
        },

        /**
         * The project's mtime as it was at capture time, else `current` (no
         * baseline pending, or the project was unknown at capture time).
         */
        projectMtime(id, current) {
            return captured ? lookup(captured.projects, id, current) : current
        },

        /** Same as projectMtime, for sessions. */
        sessionMtime(id, current) {
            return captured ? lookup(captured.sessions, id, current) : current
        },
    }
}

/** App-wide instance, shared by the data store and the reconciliation. */
export const syncBaseline = createSyncBaseline()
