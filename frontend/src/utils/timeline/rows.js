// frontend/src/utils/timeline/rows.js
//
// The rows of the list, derived from the ordered events at render time (display design section 5.2):
// day separators, group heads, indentation and block ends. Nothing here is ever stored on the events, so a
// live insertion or a page append recomputes it and a row's head or end status can change. Pure.

import { formatDayKey, formatDaySeparatorLabel } from '../date.js'
import { parseOccurredAt } from './eventTime.js'
import { groupHeadIdentity } from './sessions.js'

/**
 * Estimated heights (px, at the default 16px root size) the scroller starts from, before it measures.
 *   - `event`: one event line (2rem) and a little more.
 *   - `head`: an event row that opens a session block: half of the gap between two blocks (0.5rem), the top of
 *     the card (1px border, 0.5rem padding) and the header line (title, project, 1.75rem) above its event line.
 *   - `blockEnd`: ADDED to the row that ends a session block: the bottom of the card (0.5rem padding, 1px
 *     border) and the other half of the gap (0.5rem).
 *   - `band`, `nav`: the room the top row of the list keeps for the "Timeline" band and, in compact-height
 *     mode, for the navigation list above it (both are drawn over that room, see TimelinePanel.vue).
 * rows.test.js reads `head - event` and `blockEnd` from the rules of TimelineEventRow.vue: change them together.
 */
export const ROW_HEIGHT_ESTIMATES = { day: 40, event: 34, head: 79, blockEnd: 17, band: 48, nav: 96 }

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
 * @property {boolean} last - Last event row of its group: it closes the session block (never for an instance row).
 * @property {Object|null} identity - `groupHeadIdentity` for a head, else null.
 */

/** Whether a row key is an event row's (`event-<id>`), not a day separator nor the top room of the list. */
export function isEventRowKey(key) {
    return typeof key === 'string' && key.startsWith('event-')
}

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
            // Known once the next row is: set below.
            last: false,
            identity: head ? groupHeadIdentity(event, sessions, scope, { isSubagent }) : null,
        })
    }
    // A session row ends its block unless the next row continues the same group (an indented row).
    for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index]
        if (row.type !== 'event' || row.instance) continue
        const next = rows[index + 1]
        row.last = !(next?.type === 'event' && next.indented)
    }
    return rows
}

/**
 * @param {DayRow|EventRow|{type: 'top', withNav: boolean}} row
 * @returns {number} The height the scroller assumes until it measures the row.
 */
export function estimateRowHeight(row) {
    if (row.type === 'top') return ROW_HEIGHT_ESTIMATES.band + (row.withNav ? ROW_HEIGHT_ESTIMATES.nav : 0)
    if (row.type === 'event') {
        const base = row.head ? ROW_HEIGHT_ESTIMATES.head : ROW_HEIGHT_ESTIMATES.event
        return row.last ? base + ROW_HEIGHT_ESTIMATES.blockEnd : base
    }
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
