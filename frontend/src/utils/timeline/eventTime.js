// frontend/src/utils/timeline/eventTime.js
//
// Time and text helpers of the timeline display (docs/plans/2026-10-15-timeline-display-design.md).
// Pure (no Vue, no store, no DOM) so the node:test suite covers them.
//
// The server sends `occurred_at` in ONE fixed format, `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC, six
// fractional digits). The string therefore orders like the time, microseconds included, which
// `Date.parse` (milliseconds) would lose. Ordering and cursors use the string; only DISPLAY
// converts to a Date.

/** The arrow between an old and a new value in the PLAIN-TEXT forms (tooltips), kept in one place. The row itself
 *  renders an arrow part (parts.js) as an icon. */
export const ARROW = ' → '

/**
 * Newest-first comparator `(occurred_at, id)` descending: negative when `a` sorts before `b`.
 *
 * @param {{occurred_at: string, id: number}} a
 * @param {{occurred_at: string, id: number}} b
 * @returns {number}
 */
export function compareNewestFirst(a, b) {
    if (a.occurred_at !== b.occurred_at) return a.occurred_at < b.occurred_at ? 1 : -1
    return b.id - a.id
}

/**
 * The `before` cursor of an event: the exact server string and the id.
 *
 * @param {{occurred_at: string, id: number}} event
 * @returns {string}
 */
export function cursorOf(event) {
    return `${event.occurred_at},${event.id}`
}

/**
 * Epoch milliseconds of a wire timestamp, NaN when unreadable.
 *
 * @param {string} text
 * @returns {number}
 */
export function parseOccurredAt(text) {
    return typeof text === 'string' ? Date.parse(text) : NaN
}

/**
 * Clock time in the browser locale, hour and minute ("14:32").
 *
 * @param {number} ms
 * @param {{locale?: string, timeZone?: string}} [options] - Tests pin both; the app passes nothing.
 * @returns {string}
 */
export function formatClock(ms, { locale, timeZone } = {}) {
    if (!Number.isFinite(ms)) return ''
    return new Date(ms).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', timeZone })
}

/**
 * Full date and time with seconds in the browser locale (the tooltip of the time).
 *
 * @param {number} ms
 * @param {{locale?: string, timeZone?: string}} [options]
 * @returns {string}
 */
export function formatFullTime(ms, { locale, timeZone } = {}) {
    if (!Number.isFinite(ms)) return ''
    return new Date(ms).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'medium', timeZone })
}

/**
 * Cut `text` to about `max` characters, ending with an ellipsis. The full text is meant for the tooltip.
 *
 * @param {string} text
 * @param {number} max
 * @returns {{text: string, truncated: boolean}}
 */
export function truncateText(text, max) {
    const value = typeof text === 'string' ? text : String(text ?? '')
    if (value.length <= max) return { text: value, truncated: false }
    // Cut on code points: a UTF-16 slice could split a surrogate pair and leave a lone surrogate.
    const points = Array.from(value)
    if (points.length <= max) return { text: value, truncated: false }
    return { text: `${points.slice(0, max - 1).join('').trimEnd()}…`, truncated: true }
}

/**
 * Split a multi-line text into its first line and the rest.
 *
 * @param {string} text
 * @returns {{first: string, rest: string}}
 */
export function splitFirstLine(text) {
    const value = typeof text === 'string' ? text : String(text ?? '')
    const newline = value.indexOf('\n')
    if (newline === -1) return { first: value, rest: '' }
    return { first: value.slice(0, newline), rest: value.slice(newline + 1).trim() }
}
