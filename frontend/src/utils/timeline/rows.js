// frontend/src/utils/timeline/rows.js
//
// The rows of the list, derived from the ordered events at render time (display design section 5.2):
// day separators, group heads and indentation. Nothing here is ever stored on the events, so a live
// insertion or a page append recomputes it and a row's head status can change. Pure.

import { formatDayKey, formatDaySeparatorLabel } from '../date.js'
import { parseOccurredAt } from './eventTime.js'
import { groupHeadIdentity } from './sessions.js'

/**
 * Estimated heights (px) the scroller starts from, per row type, before it measures. `head` is an event row
 * that opens a session block: its header line (title, project) sits above its event line.
 */
export const ROW_HEIGHT_ESTIMATES = { day: 40, event: 34, head: 66, nav: 96 }

/**
 * @typedef {Object} DayRow
 * @property {string} key - `daysep-<dayKey>`
 * @property {'day'} type
 * @property {string} dayKey
 * @property {string} label
 *
 * @typedef {Object} EventRow
 * @property {string} key - `event-<id>`
 * @property {'event'} type
 * @property {Object} event
 * @property {boolean} instance - A `backend.*` event: no session, never indented, never a head.
 * @property {boolean} head - First event row of its group: it carries the session identity (the block header).
 * @property {boolean} indented - A following row of a group.
 * @property {Object|null} identity - `groupHeadIdentity` for a head, else null.
 */

/**
 * @param {Array<Object>} events - Newest first (`occurred_at` descending, then `id` descending).
 * @param {Object} options
 * @param {Object<string, Object>} options.sessions - The side table.
 * @param {{kind: string, projectId?: string|null}} options.scope
 * @param {(id: string) => boolean} [options.isSubagent]
 * @param {(ms: number) => string} [options.dayKeyOf] - Local calendar day of an instant.
 * @param {(ms: number) => string} [options.dayLabelOf]
 * @returns {Array<DayRow|EventRow>}
 */
export function buildTimelineRows(events, { sessions, scope, isSubagent, dayKeyOf = formatDayKey, dayLabelOf = formatDaySeparatorLabel }) {
    const rows = []
    let currentDay = null
    let groupSession = null
    for (const event of events) {
        const ms = parseOccurredAt(event.occurred_at)
        const dayKey = Number.isFinite(ms) ? dayKeyOf(ms) : null
        if (dayKey !== null && dayKey !== currentDay) {
            // A separator labels the rows BELOW it and breaks the group.
            rows.push({ key: `daysep-${dayKey}`, type: 'day', dayKey, label: dayLabelOf(ms) })
            currentDay = dayKey
            groupSession = null
        }
        const instance = !event.session_id
        const sameGroup = !instance && event.session_id === groupSession
        const head = !instance && !sameGroup
        groupSession = instance ? null : event.session_id
        rows.push({
            key: `event-${event.id}`,
            type: 'event',
            event,
            instance,
            head,
            indented: sameGroup,
            identity: head ? groupHeadIdentity(event, sessions, scope, { isSubagent }) : null,
        })
    }
    return rows
}

/**
 * @param {DayRow|EventRow|{type: 'nav'}} row
 * @returns {number} The height the scroller assumes until it measures the row.
 */
export function estimateRowHeight(row) {
    if (row.type === 'event' && row.head) return ROW_HEIGHT_ESTIMATES.head
    return ROW_HEIGHT_ESTIMATES[row.type] ?? ROW_HEIGHT_ESTIMATES.event
}

/**
 * Whether the list should ask for the next page: the visible range is within `threshold` rows of the end.
 *
 * @param {number} rowCount
 * @param {number} visibleEndIndex
 * @param {number} [threshold=10]
 * @returns {boolean}
 */
export function isNearEnd(rowCount, visibleEndIndex, threshold = 10) {
    return rowCount - visibleEndIndex < threshold
}
