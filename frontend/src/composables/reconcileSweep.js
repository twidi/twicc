// frontend/src/composables/reconcileSweep.js
//
// Post-reconnect coverage sweep over EVERY session whose items are loaded.
//
// The reconciliation's mtime-based change-set only guarantees the focused
// session; any other loaded session can slip through (see the baseline note
// in utils/syncBaseline.js). This sweep is the safety net that does not
// depend on that detection at all: for each loaded session, re-fetch its
// record (an authoritative `last_line`, which the coverage scan is bounded
// by) and then let ensureSessionItemsCoverage fill whatever is missing. The
// scan is a pure local pass when nothing is missing, so sweeping everything
// is cheap — exactly like refreshAllLoadedToolStates does for spinners.
//
// Pure function over a duck-typed store so it can be unit-tested without
// Pinia.

/**
 * @param {Object} store - data store (localState.sessions, sessions,
 *   refreshSessionRecord, ensureSessionItemsCoverage)
 * @returns {Promise<string[]>} ids of the sessions whose refresh or coverage
 *   fetch failed (their lines may still be missing).
 */
export async function sweepLoadedSessions(store) {
    const sessionIds = Object.entries(store.localState.sessions)
        .filter(([sessionId, local]) => local?.itemsFetched && store.sessions[sessionId])
        .map(([sessionId]) => sessionId)

    const failed = []
    await Promise.all(sessionIds.map(async (sessionId) => {
        let ok = true
        try {
            // A failed refresh is not fatal for the scan: the record may
            // already be current (refreshed by a live frame), and the scan
            // costs nothing when it finds no hole.
            ok = await store.refreshSessionRecord(sessionId)
        } catch {
            ok = false
        }
        try {
            ok = (await store.ensureSessionItemsCoverage(sessionId)) && ok
        } catch {
            ok = false
        }
        if (!ok) failed.push(sessionId)
    }))
    return failed
}
