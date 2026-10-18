// The local-day tests need a time zone with a daylight-saving change.
process.env.TZ = 'Europe/Paris'

import assert from 'node:assert/strict'
import test from 'node:test'

import { formatDaySeparatorLabel } from '../date.js'
import { ROW_HEIGHT_ESTIMATES, buildTimelineRows, estimateRowHeight, isNearEnd } from './rows.js'

const GLOBAL = { kind: 'global' }
let nextId = 1
const ev = (occurred_at, session_id = 's1', fields = {}) => ({
    id: nextId++, kind: 'turn.started', occurred_at, session_id, project_id: 'p1', provider: 'codex', data: {}, ...fields,
})
const build = (events, extra = {}) => buildTimelineRows(events, { sessions: {}, scope: GLOBAL, ...extra })
const shape = (rows) => rows.map((row) => (row.type === 'day' ? `day:${row.dayKey}` : `${row.head ? 'H' : row.indented ? 'i' : row.instance ? 'I' : '-'}:${row.event.session_id ?? 'none'}`))

test('the list starts with a separator for the newest day and every row has a stable key', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z'), ev('2026-10-14T11:00:00.000000Z')]
    const rows = build(events)
    assert.deepEqual(rows.map((row) => row.key), ['daysep-2026-10-14', `event-${events[0].id}`, `event-${events[1].id}`])
    assert.equal(rows[0].type, 'day')
})

test('an empty list has no rows', () => {
    assert.deepEqual(build([]), [])
})

test('the group head carries the identity once; the following rows of the session are indented', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a'), ev('2026-10-14T12:00:02.000000Z', 'a'), ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a', 'i:a'])
    assert.ok(rows[1].identity && rows[2].identity === null && rows[3].identity === null)
})

test('another session starts a new group, and the first session starts again after it', () => {
    const rows = build([
        ev('2026-10-14T12:00:04.000000Z', 'a'), ev('2026-10-14T12:00:03.000000Z', 'b'),
        ev('2026-10-14T12:00:02.000000Z', 'b'), ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'H:b', 'i:b', 'H:a'])
})

test('an instance event is never indented nor a head, and it breaks the surrounding group', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a'), ev('2026-10-14T12:00:02.000000Z', null, { kind: 'backend.started', project_id: null }),
        ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'I:none', 'H:a'])
    assert.equal(rows[2].identity, null)
})

test('a day separator breaks the group: the row after it is a head again', () => {
    const rows = build([ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-13T12:00:00.000000Z', 'a')])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'day:2026-10-13', 'H:a'])
})

test('the separator labels the rows BELOW it, one per local day', () => {
    const rows = build([
        ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-14T08:00:00.000000Z', 'b'), ev('2026-10-12T08:00:00.000000Z', 'c'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'H:b', 'day:2026-10-12', 'H:c'])
})

test('the separator label is the chat one (formatDaySeparatorLabel) unless injected', () => {
    const at = '2026-10-14T12:00:00.000000Z'
    assert.equal(build([ev(at)])[0].label, formatDaySeparatorLabel(Date.parse(at)))
    const custom = build([ev(at)], { dayLabelOf: () => 'Today' })
    assert.equal(custom[0].label, 'Today')
})

test('day boundaries follow the local calendar at midnight', () => {
    const rows = build([
        ev('2026-10-24T22:00:00.000000Z'), // 00:00:00 on the 25th in Paris (UTC+2)
        ev('2026-10-24T21:59:59.999999Z'), // 23:59:59 on the 24th
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-10-25', '2026-10-24'])
})

test('day boundaries hold across the spring daylight-saving change (a 23-hour day)', () => {
    const rows = build([
        ev('2026-03-29T22:30:00.000000Z'), // 00:30 on the 30th (UTC+2)
        ev('2026-03-29T01:30:00.000000Z'), // 03:30 on the 29th, after the jump
        ev('2026-03-29T00:30:00.000000Z'), // 01:30 on the 29th, before the jump
        ev('2026-03-28T23:30:00.000000Z'), // 00:30 on the 29th (UTC+1)
        ev('2026-03-28T22:59:59.000000Z'), // 23:59:59 on the 28th
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-03-30', '2026-03-29', '2026-03-28'])
})

test('day boundaries hold across the autumn daylight-saving change (a 25-hour day)', () => {
    const rows = build([
        ev('2026-10-25T23:00:00.000000Z'), // 00:00 on the 26th (UTC+1)
        ev('2026-10-25T22:59:59.000000Z'), // 23:59:59 on the 25th
        ev('2026-10-25T01:30:00.000000Z'), // 03:30... still the 25th
        ev('2026-10-24T22:00:00.000000Z'), // 00:00 on the 25th (UTC+2)
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-10-26', '2026-10-25'])
})

test('a live insertion recomputes heads: a head can become a plain row and the reverse', () => {
    const a1 = ev('2026-10-14T12:00:01.000000Z', 'a')
    const b = ev('2026-10-14T12:00:00.000000Z', 'b')
    assert.deepEqual(shape(build([a1, b])), ['day:2026-10-14', 'H:a', 'H:b'])
    // A back-dated row of session b lands before the existing one: b now has a head and an indented row.
    const b2 = ev('2026-10-14T12:00:02.000000Z', 'b')
    assert.deepEqual(shape(build([b2, a1, b])), ['day:2026-10-14', 'H:b', 'H:a', 'H:b'])
    // A row of a lands between: the b group is split.
    const a2 = ev('2026-10-14T12:00:03.000000Z', 'a')
    assert.deepEqual(shape(build([a2, b2, a1, b])), ['day:2026-10-14', 'H:a', 'H:b', 'H:a', 'H:b'])
    assert.deepEqual(shape(build([b2, b])), ['day:2026-10-14', 'H:b', 'i:b'])
})

test('re-emissions of turn.ended are not collapsed: every event is its own row', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a', { kind: 'turn.ended', data: { background_work: null } }),
        ev('2026-10-14T12:00:02.000000Z', 'a', { kind: 'turn.ended', data: { background_work: null } }),
        ev('2026-10-14T12:00:01.000000Z', 'a', { kind: 'turn.ended', data: { background_work: { shells: 1 } } }),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a', 'i:a'])
})

test('building rows never mutates the events', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-14T11:00:00.000000Z', 'a')]
    const copy = JSON.parse(JSON.stringify(events))
    for (const event of events) Object.freeze(event)
    build(events)
    assert.deepEqual(events, copy)
})

test('the keys do not depend on the position: the same event keeps its key', () => {
    const e = ev('2026-10-14T12:00:00.000000Z', 'a')
    const other = ev('2026-10-14T13:00:00.000000Z', 'b')
    assert.equal(build([e]).find((row) => row.type === 'event').key, build([other, e]).find((row) => row.key === `event-${e.id}`).key)
})

test('an unreadable timestamp adds no separator and keeps the current day', () => {
    const rows = build([ev('2026-10-14T12:00:00.000000Z', 'a'), ev('garbage', 'a')])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a'])
})

test('the head identity follows the scope: project name on a workspace page, not on a worktree page', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z', 'a')]
    assert.equal(build(events, { scope: { kind: 'workspace' } })[1].identity.showProject, true)
    assert.equal(build(events, { scope: { kind: 'worktree', projectId: 'p1' } })[1].identity.showProject, false)
})

test('the last row of a session block carries the block end; instance rows and day separators never do', () => {
    const rows = build([
        ev('2026-10-14T12:00:06.000000Z', 'a'), ev('2026-10-14T12:00:05.000000Z', 'a'), // block a: head, then its end
        ev('2026-10-14T12:00:04.000000Z', 'b'), // block b: one row, head and end at once
        ev('2026-10-14T12:00:03.000000Z', null, { kind: 'backend.started', project_id: null }), // instance
        ev('2026-10-14T12:00:02.000000Z', 'c'), // block c, ended by the day separator below
        ev('2026-10-13T12:00:00.000000Z', 'c'), // a new block c on the previous day: the last row of the list
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a', 'H:b', 'I:none', 'H:c', 'day:2026-10-13', 'H:c'])
    assert.deepEqual(rows.map((row) => row.last ?? null), [null, false, true, true, false, true, null, true])
})

test('a live insertion recomputes the block end: the former last row of a block is not the end any more', () => {
    const a1 = ev('2026-10-14T12:00:01.000000Z', 'a')
    const a0 = ev('2026-10-14T12:00:00.000000Z', 'a')
    assert.equal(build([a1]).find((row) => row.key === `event-${a1.id}`).last, true)
    assert.equal(build([a1, a0]).find((row) => row.key === `event-${a1.id}`).last, false)
})

test('row height estimates exist per row type; an unknown type uses the event one', () => {
    assert.equal(estimateRowHeight({ type: 'day' }), ROW_HEIGHT_ESTIMATES.day)
    assert.equal(estimateRowHeight({ type: 'event' }), ROW_HEIGHT_ESTIMATES.event)
    assert.equal(estimateRowHeight({ type: 'event', head: false, indented: true, last: false }), ROW_HEIGHT_ESTIMATES.event)
    assert.equal(estimateRowHeight({ type: 'event', instance: true, head: false, last: false }), ROW_HEIGHT_ESTIMATES.event)
    assert.equal(estimateRowHeight({ type: 'nav' }), ROW_HEIGHT_ESTIMATES.nav)
    assert.equal(estimateRowHeight({ type: 'mystery' }), ROW_HEIGHT_ESTIMATES.event)
    assert.ok(ROW_HEIGHT_ESTIMATES.day !== ROW_HEIGHT_ESTIMATES.event)
})

test('a block head starts from a taller estimate (header line, top of the card); a block end adds the bottom of the card and the gap', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a'), ev('2026-10-14T12:00:02.000000Z', 'a'), ev('2026-10-14T12:00:01.000000Z', 'a'),
        ev('2026-10-14T12:00:00.000000Z', 'b'),
    ])
    const [, head, middle, end, single] = rows
    assert.equal(estimateRowHeight(head), ROW_HEIGHT_ESTIMATES.head)
    assert.equal(estimateRowHeight(middle), ROW_HEIGHT_ESTIMATES.event)
    assert.equal(estimateRowHeight(end), ROW_HEIGHT_ESTIMATES.event + ROW_HEIGHT_ESTIMATES.blockEnd)
    assert.equal(estimateRowHeight(single), ROW_HEIGHT_ESTIMATES.head + ROW_HEIGHT_ESTIMATES.blockEnd)
    assert.ok(ROW_HEIGHT_ESTIMATES.head > ROW_HEIGHT_ESTIMATES.event)
    assert.ok(ROW_HEIGHT_ESTIMATES.blockEnd > 0)
})

test('the next page is requested when the visible range is within ten rows of the end', () => {
    assert.equal(isNearEnd(100, 80), false)
    assert.equal(isNearEnd(100, 90), false)
    assert.equal(isNearEnd(100, 91), true)
    assert.equal(isNearEnd(5, 4), true)
    assert.equal(isNearEnd(0, 0), true)
    assert.equal(isNearEnd(100, 50, 60), true)
})
