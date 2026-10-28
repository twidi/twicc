/**
 * Cheap "still booting" probe used by hot getters (unread counts, sessions
 * list) to skip expensive work while any provider is still mid-phase.
 * Reads every phase entry, so a caller computed tracks them all. A missing
 * `startupProgress` means "not in startup".
 *
 * @param {?Object} startupProgress - `{ [phase]: { [provider]: { completed } } }`
 * @returns {boolean}
 */
export function hasActiveStartupPhase(startupProgress) {
    if (!startupProgress) return false
    for (const byProvider of Object.values(startupProgress)) {
        if (!byProvider) continue
        for (const entry of Object.values(byProvider)) {
            if (entry && !entry.completed) return true
        }
    }
    return false
}
