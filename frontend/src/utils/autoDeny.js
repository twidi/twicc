// frontend/src/utils/autoDeny.js
// Countdown of an auto-deniable pending request (design
// docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md §5).
//
// The backend sends `auto_deny_in_seconds`, the time left when it serialized
// the payload, not a deadline: the browser may run on another machine whose
// clock differs. The store turns it into a local deadline at reception and
// drops the wire key, so an unchanged request keeps comparing equal.

// Mirror of AUTO_DENY_DELAY_SECONDS in src/twicc/agent/auto_deny.py (the
// source of truth). Used only in the tooltip text.
export const AUTO_DENY_DELAY_SECONDS = 120

// The form refuses answers when less than this is left (the display then
// reads 0:00): the local deadline lags the backend one by the delivery delay,
// and the answer needs its own trip back.
export const AUTO_DENY_LOCK_MARGIN_MS = 1000

// A new stamp closer than this to the previous one keeps the previous one, so
// `jsonValuesEqual` sees no change and the store does not replace the whole
// `pending_requests` array on every process_state. Kept below the lock margin.
export const AUTO_DENY_KEEP_TOLERANCE_MS = 500

const WIRE_KEY = 'auto_deny_in_seconds'
const DEADLINE_KEY = 'autoDenyDeadlineMs'

/**
 * Turn the wire's relative auto-deny time into a local deadline.
 *
 * @param {Array<Object>} pendingRequests - Wire pending requests
 * @param {number} receivedAtMs - Local reception instant (Date.now())
 * @param {Array<Object>} [previousRequests] - The session's current store entries
 * @returns {Array<Object>} Entries with the wire key replaced by `autoDenyDeadlineMs`
 */
export function withAutoDenyDeadlines(pendingRequests, receivedAtMs, previousRequests = []) {
    const previousDeadlines = new Map()
    for (const request of previousRequests || []) {
        if (request?.[DEADLINE_KEY] != null) previousDeadlines.set(request.request_id, request[DEADLINE_KEY])
    }
    return pendingRequests.map(request => {
        if (!Object.hasOwn(request, WIRE_KEY)) return request
        const { [WIRE_KEY]: remainingSeconds, ...rest } = request
        let deadlineMs = receivedAtMs + remainingSeconds * 1000
        const previous = previousDeadlines.get(request.request_id)
        if (previous !== undefined && Math.abs(previous - deadlineMs) < AUTO_DENY_KEEP_TOLERANCE_MS) {
            deadlineMs = previous
        }
        return { ...rest, [DEADLINE_KEY]: deadlineMs }
    })
}

/**
 * True when the form must refuse answers: less than the lock margin is left.
 * @param {number} deadlineMs
 * @param {number} nowMs
 * @returns {boolean}
 */
export function isAutoDenyLocked(deadlineMs, nowMs) {
    return deadlineMs - nowMs < AUTO_DENY_LOCK_MARGIN_MS
}

/**
 * Remaining time as `m:ss`, rounded down, floored at `0:00` — so `0:00` is
 * shown exactly while `isAutoDenyLocked` is true.
 * @param {number} deadlineMs
 * @param {number} nowMs
 * @returns {string}
 */
export function formatAutoDenyRemaining(deadlineMs, nowMs) {
    const totalSeconds = Math.max(0, Math.floor((deadlineMs - nowMs) / 1000))
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${minutes}:${String(seconds).padStart(2, '0')}`
}

/**
 * The request without its deadline keys, for a hash that must not change
 * between two page loads (the local deadline differs on each load).
 * @param {Object} pendingRequest
 * @returns {Object}
 */
export function withoutAutoDenyKeys(pendingRequest) {
    if (!pendingRequest || (!Object.hasOwn(pendingRequest, WIRE_KEY) && !Object.hasOwn(pendingRequest, DEADLINE_KEY))) {
        return pendingRequest
    }
    const { [WIRE_KEY]: _wire, [DEADLINE_KEY]: _deadline, ...rest } = pendingRequest
    return rest
}
