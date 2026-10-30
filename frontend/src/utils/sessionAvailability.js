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
