import { isLaunchedEphemeral } from './ephemeralSessions.js'

/**
 * Sort sessions by display priority:
 * 1. Pinned sessions first (top-level split — any non-null pin mode counts).
 * 2. Within each pin group: sessions with active process first (by started_at
 *    descending for stable ordering).
 * 3. Remaining sessions within each pin group: by mtime descending.
 *
 * @param {Object} processStates - Map of sessionId -> processState
 * @returns {function} Comparator function for Array.sort()
 */
export function sessionSortComparator(processStates) {
    return (a, b) => {
        const aEphemeral = isLaunchedEphemeral(a)
        const bEphemeral = isLaunchedEphemeral(b)
        if (aEphemeral !== bEphemeral) return aEphemeral ? -1 : 1
        if (aEphemeral) return (Date.parse(b.ephemeralStartedAt) || 0) - (Date.parse(a.ephemeralStartedAt) || 0)
        // 1. Pinned sessions first (regardless of mode).
        //    `pinned` is a string ('project'/'workspace'/'all') or null — any truthy
        //    value means pinned.
        const aPinned = !!a.pinned
        const bPinned = !!b.pinned
        if (aPinned !== bPinned) return aPinned ? -1 : 1

        // 2. Within the same pin group: sessions with active process first.
        const aProcess = processStates[a.id]
        const bProcess = processStates[b.id]
        const aHasProcess = aProcess != null
        const bHasProcess = bProcess != null
        if (aHasProcess !== bHasProcess) return aHasProcess ? -1 : 1

        // 3. Among active sessions: sort by started_at descending (most recently started first).
        //    This gives a stable order since started_at never changes during process lifetime,
        //    avoiding rapid swapping when multiple sessions update frequently.
        if (aHasProcess && bHasProcess) {
            return (bProcess.started_at || 0) - (aProcess.started_at || 0)
        }

        // 4. Non-active sessions: sort by mtime descending.
        return b.mtime - a.mtime
    }
}
