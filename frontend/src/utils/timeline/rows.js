// frontend/src/utils/timeline/rows.js
//
// The rows of the list, derived from the ordered events at render time (display design section 5.2):
// day separators, group heads, indentation and block ends. Nothing here is ever stored on the events, so a
// live insertion or a page append recomputes it and a row's head or end status can change. Pure.

import { formatDayKey, formatDaySeparatorLabel } from '../date.js'
import { authorRunKey, authorRunPosition } from './authorLabel.js'
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
 *   - `turnEnd`, `turnStart`: the two ends of a turn drawn as a rail (a sentence line with room above it, a short curved stub with room under it): they
 *     replace the event line (the same card padding applies around them).
 *   - `state`: the one item that replaces the event rows while the list is loading, empty, not found, failed or
 *     matches nothing: the padding of the block (2.5rem above and below) and one or two lines of content.
 * rows.test.js reads `head - event` and `blockEnd` from the rules of TimelineEventRow.vue: change them together.
 */
export const ROW_HEIGHT_ESTIMATES = { day: 40, event: 34, head: 79, blockEnd: 17, band: 48, nav: 96, state: 128, turnEnd: 30, turnStart: 21 }

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
 * @property {TurnRail|null} rail - The place of the row in a turn drawn as a rail (`turnRail` option), else null.
 * @property {null|'single'|'first'|'middle'|'last'} authorRun - Place in its author run inside the session block
 *   (`authorRunPosition`): the "by ..." label shows once, under the run's last row. Null for an instance row.
 */

/**
 * @typedef {Object} TurnRail
 * @property {'end'|'inside'|'start'} role - `end`: the `turn.ended` row (the top of the turn, drawn as a sentence);
 *   `start`: the `turn.started` row (the bottom, drawn as a short curved stub); `inside`: any other event of the
 *   session between the two.
 * @property {null|'solid'|'dashed'} up - The line above the row: solid when the row above, in the same block,
 *   belongs to the turn; dashed when the turn goes on above in another block; null when the turn starts here.
 * @property {null|'solid'|'dashed'} down - The same below. A turn whose start is not loaded yet ends dashed.
 * @property {boolean} open - The turn has no `turn.ended` row: it is running. Its end row is the synthetic one.
 * @property {boolean} beforeStart - The row under this one, in the same block, is the start row of the turn.
 * @property {number|null} durationMs - From the start row to the end row, null when one of them is missing.
 * @property {number|null} startedAtMs - The start row's time (the clock of an open turn), null when not loaded.
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
 * @param {boolean} [options.turnRail] - Draw the turns as rails (the filters keep both turn boundaries, see
 *   `showsTurnRail`). Off: every `rail` is null and the turn boundaries are plain event rows.
 * @param {Object<string, (number|null)>} [options.turnsBelow] - By session: when the turn its oldest rows belong to
 *   started below the loaded rows (ms), else null (`state.turnsBelow` of the feed, from the server).
 * @returns {Array<DayRow|EventRow>}
 */
export function buildTimelineRows(events, { sessions, scope, isSubagent, dayKeyOf = formatDayKey, dayLabelOf = formatDaySeparatorLabel, turnRail = false, turnsBelow = null }) {
    const rows = []
    let currentDay = null
    let groupSession = null
    for (const event of turnRail ? withRunningTurns(events, turnsBelow) : events) {
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
            // Known once the block is: set below.
            authorRun: null,
            rail: null,
        })
    }
    // A session row ends its block unless the next row continues the same group (an indented row).
    for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index]
        if (row.type !== 'event' || row.instance) continue
        const next = rows[index + 1]
        row.last = !(next?.type === 'event' && next.indented)
    }
    // Author runs never cross a block edge.
    const keys = rows.map((row) => (row.type === 'event' && !row.instance ? authorRunKey(row.event) : null))
    for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index]
        if (row.type !== 'event') continue
        row.authorRun = row.instance ? null : authorRunPosition(
            row.indented ? keys[index - 1] : null, keys[index], row.last ? null : keys[index + 1])
    }
    if (turnRail) annotateTurns(rows, turnsBelow)
    return rows
}

const TURN_STARTED = 'turn.started'
const TURN_ENDED = 'turn.ended'
/** The kind of the synthetic row that tops a running turn (it has no `turn.ended` row yet): never stored. */
export const TURN_RUNNING = 'turn.running'

/**
 * The events with a synthetic `turn.running` event added above the newest event of each session that is running a
 * turn: a running turn has no end row, and its top line ("Turn running for 2m 30s") needs one. A session runs a turn
 * when its newest turn boundary on the page is a start, or when the page holds no boundary of it at all (the page is
 * the newest events, so a turn that ended would have its end on the page) and `turnsBelow` says its rows continue a
 * turn that started below the page.
 * The synthetic event copies the event it sits above (session, project, provider, time), so it lands in the same
 * block and the same day, and it has no author. Its `data.started_at_ms` is the start of the turn. Pure: the input
 * is not changed.
 */
function withRunningTurns(events, turnsBelow) {
    const newestBoundary = new Map() // session id -> its newest turn boundary event
    const newestIndex = new Map() // session id -> index of its newest event
    events.forEach((event, index) => {
        const sessionId = event.session_id
        if (!sessionId) return
        if (!newestIndex.has(sessionId)) newestIndex.set(sessionId, index)
        if (!newestBoundary.has(sessionId) && (event.kind === TURN_STARTED || event.kind === TURN_ENDED)) newestBoundary.set(sessionId, event)
    })
    const synthetic = new Map() // index -> the event to put before it
    for (const [sessionId, index] of newestIndex) {
        const boundary = newestBoundary.get(sessionId)
        let startedAtMs = null
        if (boundary) {
            if (boundary.kind === TURN_STARTED) startedAtMs = parseOccurredAt(boundary.occurred_at)
        } else {
            startedAtMs = turnsBelow?.[sessionId] ?? null
        }
        if (!Number.isFinite(startedAtMs)) continue
        synthetic.set(index, {
            ...events[index], id: `turn-running-${sessionId}`, kind: TURN_RUNNING, data: { started_at_ms: startedAtMs },
            precision: 'estimated', actor: null, via: null, actor_ref: null, actor_name: null,
        })
    }
    if (!synthetic.size) return events
    return events.flatMap((event, index) => (synthetic.has(index) ? [synthetic.get(index), event] : [event]))
}

/**
 * Sets `row.rail` on the rows of the turns of the page.
 *
 * A running turn is topped by a synthetic row (`withRunningTurns`).
 *
 * A turn of a session is everything the session does from its `turn.started` to its `turn.ended`: that holds the
 * boundary rows and every other event of the session between them (what the session does outside a turn, such as
 * archiving or stopping, is not in it). The sessions run in parallel, so the rows of a turn can be cut by the rows of
 * other sessions: the line then goes dashed at the cut. Walks the page from the oldest row to the newest, one state
 * per session: a session whose oldest turn row is an end started its turn before this page, and a start with no end
 * is running.
 */
function annotateTurns(rows, turnsBelow) {
    const turns = new Map() // session id -> the turn the session is in
    const bySession = new Map() // session id -> its oldest turn row kind, to know the state at the bottom of the page
    for (let index = rows.length - 1; index >= 0; index -= 1) {
        const row = rows[index]
        if (row.type !== 'event' || row.instance || bySession.has(row.event.session_id)) continue
        if (row.event.kind === TURN_STARTED || row.event.kind === TURN_ENDED) bySession.set(row.event.session_id, row.event.kind)
    }
    const newTurn = (cutAbove) => ({ startRow: null, endRow: null, cutAbove, indexes: [] })
    for (const [sessionId, kind] of bySession) if (kind === TURN_ENDED) turns.set(sessionId, newTurn(true))
    // A running turn whose start is not on the page: the session has a synthetic row and no boundary at all.
    for (const row of rows) {
        if (row.type === 'event' && row.event.kind === TURN_RUNNING && !bySession.has(row.event.session_id)) turns.set(row.event.session_id, newTurn(true))
    }

    for (let index = rows.length - 1; index >= 0; index -= 1) {
        const row = rows[index]
        if (row.type !== 'event' || row.instance) continue
        const sessionId = row.event.session_id
        let role = null
        let turn = turns.get(sessionId) ?? null
        if (row.event.kind === TURN_STARTED) {
            role = 'start'
            // A start in a running turn (its end was lost) replaces it.
            turn = newTurn(false)
            turn.startRow = row
            turns.set(sessionId, turn)
        } else if (row.event.kind === TURN_ENDED || row.event.kind === TURN_RUNNING) {
            role = 'end'
            if (!turn) turn = newTurn(false)
            turn.endRow = row
            turns.delete(sessionId)
        } else if (turn) {
            role = 'inside'
        }
        if (!role) continue
        turn.indexes.push(index)
        row.rail = { role, turn, up: null, down: null, open: false, beforeStart: false, durationMs: null, startedAtMs: null }
    }

    for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index]
        if (!row.rail) continue
        const { turn } = row.rail
        // `indexes` was filled from the oldest row up: the first is the bottom of the turn, the last its top.
        const bottom = turn.indexes[0]
        const top = turn.indexes[turn.indexes.length - 1]
        // A turn whose start is not on the page: the server knows when it started (`turnsBelow`).
        const startMs = turn.startRow ? parseOccurredAt(turn.startRow.event.occurred_at) : (turnsBelow?.[row.event.session_id] ?? NaN)
        const endMs = turn.endRow ? parseOccurredAt(turn.endRow.event.occurred_at) : NaN
        // Solid when the neighbour is in the turn AND in the same block (an indented row follows its block's row).
        const link = (other) => (rows[other]?.rail?.turn === turn && (other < index ? row.indented : rows[other].indented) ? 'solid' : 'dashed')
        row.rail.up = index > top ? link(index - 1) : null
        // The bottom row of a turn with no loaded start continues on the next page.
        row.rail.down = index < bottom ? link(index + 1) : (turn.startRow ? null : 'dashed')
        row.rail.open = turn.endRow?.event.kind === TURN_RUNNING
        row.rail.beforeStart = row.rail.down === 'solid' && rows[index + 1].rail?.role === 'start'
        row.rail.durationMs = !row.rail.open && Number.isFinite(startMs) && Number.isFinite(endMs) ? Math.max(0, endMs - startMs) : null
        const startedAtMs = row.rail.open ? turn.endRow.event.data.started_at_ms : startMs
        row.rail.startedAtMs = Number.isFinite(startedAtMs) ? startedAtMs : null
    }
    for (const row of rows) if (row.rail) delete row.rail.turn
}

/**
 * @param {DayRow|EventRow|{type: 'top', withNav: boolean}|{type: 'state', state: string}} row
 * @returns {number} The height the scroller assumes until it measures the row.
 */
export function estimateRowHeight(row) {
    if (row.type === 'top') return ROW_HEIGHT_ESTIMATES.band + (row.withNav ? ROW_HEIGHT_ESTIMATES.nav : 0)
    if (row.type === 'event') {
        // A turn end or start replaces the event line; a head keeps its header above it.
        const line = row.rail?.role === 'end' ? ROW_HEIGHT_ESTIMATES.turnEnd : row.rail?.role === 'start' ? ROW_HEIGHT_ESTIMATES.turnStart : ROW_HEIGHT_ESTIMATES.event
        const base = row.head ? ROW_HEIGHT_ESTIMATES.head - ROW_HEIGHT_ESTIMATES.event + line : line
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
