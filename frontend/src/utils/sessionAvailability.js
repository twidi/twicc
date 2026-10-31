import { toRaw } from 'vue'

/**
 * The frontend side of the "unavailable history" rule
 * (backend: `src/twicc/core/session_availability.py`).
 *
 * A session whose `unavailable_reason` is set can be read nowhere and written
 * nowhere: no items, no composer, no title suggestion. The rule depends only on
 * the field being set, never on its value. A subagent follows its parent.
 */

/**
 * The reason that blocks a session, or null.
 * @param {(id: string) => Object|undefined} getSession - store getter (`store.getSession`)
 * @param {string} sessionId
 * @param {string|null} [parentSessionId] - set for a subagent: its parent's reason applies too
 * @returns {string|null}
 */
export function getSessionUnavailableReason(getSession, sessionId, parentSessionId = null) {
    const own = getSession(sessionId)?.unavailable_reason
    if (own != null) return own
    if (!parentSessionId) return null
    return getSession(parentSessionId)?.unavailable_reason ?? null
}

// Sequence number of the last server update that asserted each session row's
// `unavailable_reason` (keyed by the row object, which `updateSession` keeps).
// A refusal read that started before such an update is older than what the row
// says: it must not overwrite it.
let reasonUpdateSeq = 0
const reasonUpdatedAt = new WeakMap()

/**
 * Record that an update just set `row.unavailable_reason` (a server snapshot
 * carries the field, even when null). Called by the store's `updateSession`.
 * @param {Object|undefined} row - the store's session row
 */
export function noteUnavailableReasonUpdate(row) {
    if (row) reasonUpdatedAt.set(toRaw(row), ++reasonUpdateSeq)
}

/**
 * Capture, before a content read, what a refusal of that read needs: where to
 * find the rows, and the point in time the read started.
 * @param {(id: string) => Object|undefined} getSession - store getter
 * @param {string} sessionId - the session the read targets
 * @returns {{getSession: Function, sessionId: string, seq: number}}
 */
export function captureRefusalTarget(getSession, sessionId) {
    return { getSession, sessionId, seq: reasonUpdateSeq }
}

/**
 * Handle a content response that the backend refused because the session is
 * unavailable: HTTP 409 with `code: "session_unavailable"`
 * (`arefuse_unavailable` in `src/twicc/core/session_availability.py`).
 *
 * Such a refusal is the unavailable state, not an error. The store can learn
 * of it this way only: its session row came before the reason was set. The
 * reason goes on the row of the session that carries it
 * (`unavailable_session_id`: the session itself, or the parent a subagent
 * follows), else on the targeted row. Every guard then stops reading the
 * session and the unavailable view shows. The caller stops its load and logs
 * nothing.
 *
 * When an update of that row's reason landed during the read, the row wins:
 * nothing is written, and the refusal counts only if the row now has a reason.
 * @param {Response} response - a response that is not `ok`
 * @param {{getSession: Function, sessionId: string, seq: number}} target - from `captureRefusalTarget`, before the read
 * @returns {Promise<string|null>} the reason that now applies, else null (the body stays unread)
 */
export async function applyUnavailableRefusal(response, target) {
    if (response?.status !== 409) return null
    let body
    try {
        body = await response.clone().json()
    } catch {
        return null
    }
    const reason = body?.code === 'session_unavailable' ? body.unavailable_reason : null
    if (reason == null) return null
    const carrierId = body.unavailable_session_id
    const row = (carrierId && target.getSession(carrierId)) || target.getSession(target.sessionId)
    if (!row) return reason
    if ((reasonUpdatedAt.get(toRaw(row)) || 0) > target.seq) return row.unavailable_reason ?? null
    if (row.unavailable_reason == null) row.unavailable_reason = reason
    return reason
}
