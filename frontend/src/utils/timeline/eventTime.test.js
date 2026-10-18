import assert from 'node:assert/strict'
import test from 'node:test'

import {
    ARROW, compareNewestFirst, cursorOf, formatClock, formatFullTime, parseOccurredAt, splitFirstLine, truncateText,
} from './eventTime.js'

const event = (occurred_at, id) => ({ occurred_at, id })

test('newest first: the later time sorts first, the higher id breaks ties', () => {
    const rows = [
        event('2026-10-14T10:00:00.000000Z', 1),
        event('2026-10-14T10:00:05.000000Z', 2),
        event('2026-10-14T10:00:05.000000Z', 3),
        event('2026-10-14T09:59:59.999999Z', 4),
    ]
    assert.deepEqual(rows.sort(compareNewestFirst).map((row) => row.id), [3, 2, 1, 4])
})

test('the comparison keeps the microseconds that Date.parse would lose', () => {
    // The later row (by microseconds) has the LOWER id: an ordering that merged the two rows into one
    // millisecond would fall back to the id and put the wrong row first.
    const later = event('2026-10-14T10:00:00.000900Z', 1)
    const earlier = event('2026-10-14T10:00:00.000100Z', 2)
    assert.equal(Date.parse(later.occurred_at), Date.parse(earlier.occurred_at), 'the same millisecond')
    assert.ok(compareNewestFirst(later, earlier) < 0 && compareNewestFirst(earlier, later) > 0)
    assert.deepEqual([earlier, later].sort(compareNewestFirst).map((row) => row.id), [1, 2])
})

test('the cursor is the exact server string and the id', () => {
    assert.equal(cursorOf(event('2026-10-14T10:00:00.123456Z', 42)), '2026-10-14T10:00:00.123456Z,42')
})

test('parseOccurredAt reads the wire format and returns NaN for garbage', () => {
    assert.equal(parseOccurredAt('2026-10-14T10:00:00.500000Z'), Date.UTC(2026, 9, 14, 10, 0, 0, 500))
    assert.ok(Number.isNaN(parseOccurredAt('nope')) && Number.isNaN(parseOccurredAt(null)))
})

test('clock time is hour and minute, the tooltip time has the date and the seconds', () => {
    const ms = Date.UTC(2026, 9, 14, 14, 32, 7)
    assert.equal(formatClock(ms, { locale: 'en-GB', timeZone: 'UTC' }), '14:32')
    const full = formatFullTime(ms, { locale: 'en-GB', timeZone: 'UTC' })
    assert.match(full, /14 Oct 2026/)
    assert.match(full, /14:32:07/)
    assert.equal(formatClock(NaN), '')
    assert.equal(formatFullTime(NaN), '')
})

test('truncateText cuts near the limit with an ellipsis and says so', () => {
    assert.deepEqual(truncateText('short', 10), { text: 'short', truncated: false })
    const cut = truncateText('a'.repeat(100), 80)
    assert.equal(cut.truncated, true)
    assert.equal(cut.text.length, 80)
    assert.ok(cut.text.endsWith('…'))
    assert.deepEqual(truncateText(null, 5), { text: '', truncated: false })
})

test('truncateText never leaves a lone surrogate when the cut falls inside an emoji', () => {
    const lone = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/
    const text = 'a'.repeat(78) + '😀' + 'b'.repeat(20)
    const cut = truncateText(text, 80)
    assert.equal(cut.truncated, true)
    assert.ok(!lone.test(cut.text), JSON.stringify(cut.text))
    assert.equal(cut.text, `${'a'.repeat(78)}😀…`)
    assert.equal(truncateText('😀'.repeat(50), 10).text, `${'😀'.repeat(9)}…`)
    assert.deepEqual(truncateText('😀'.repeat(10), 10), { text: '😀'.repeat(10), truncated: false })
})

test('splitFirstLine separates the first line from the rest', () => {
    assert.deepEqual(splitFirstLine('one'), { first: 'one', rest: '' })
    assert.deepEqual(splitFirstLine('one\ntwo\nthree'), { first: 'one', rest: 'two\nthree' })
})

test('the arrow of the plain-text forms (tooltips) is one constant', () => {
    assert.equal(ARROW, ' → ')
})
