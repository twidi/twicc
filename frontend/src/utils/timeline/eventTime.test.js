import assert from 'node:assert/strict'
import test from 'node:test'

import {
    ARROW, ESTIMATED_MARK, compareNewestFirst, cssStringOf, cursorOf, formatClock, formatFullTime, parseOccurredAt,
    shiftEventTime, splitFirstLine, timeColumnSamples, timeColumnSizer, truncateText,
} from './eventTime.js'

const event = (occurred_at, id) => ({ occurred_at, id })

test('shiftEventTime shifts integer milliseconds and preserves sub-millisecond precision', () => {
    assert.equal(shiftEventTime('2026-11-04T10:30:40.123456Z', -5000), '2026-11-04T10:30:35.123456Z')
    assert.equal(shiftEventTime('2026-11-04T00:00:00.000999Z', -1), '2026-11-03T23:59:59.999999Z')
    assert.equal(shiftEventTime('2026-12-31T23:59:59.999001Z', 1), '2027-01-01T00:00:00.000001Z')
    assert.equal(shiftEventTime('2026-11-04T10:30:40.123456Z', 0), '2026-11-04T10:30:40.123456Z')
})

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
    assert.equal(formatClock(ms, { locale: 'en-US', timeZone: 'UTC' }), '2:32 PM')
    assert.equal(formatClock(ms, { locale: 'fr', timeZone: 'UTC' }), '14:32')
    const morning = Date.UTC(2026, 9, 14, 9, 5)
    assert.equal(formatClock(morning, { locale: 'en', timeZone: 'UTC' }), '9:05 AM')
    assert.equal(formatClock(morning, { locale: 'fr', timeZone: 'UTC' }), '09:05')
    assert.equal(formatClock(morning, { locale: 'es', timeZone: 'UTC' }), '9:05')
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

test('the time column samples: one text per shape of the clock, with the estimated mark, in the locale', () => {
    // A 24-hour locale: every time has the same shape (tabular digits), one sample.
    assert.deepEqual(timeColumnSamples({ locale: 'en-GB', timeZone: 'UTC' }), ['~00:59'])
    // A 12-hour locale without leading zero: one or two hour digits, morning or afternoon, all sampled, so the sizer holds the widest.
    const twelve = timeColumnSamples({ locale: 'en-US', timeZone: 'UTC' })
    assert.equal(twelve.length, 4)
    assert.match(twelve[0], /^~12:59\sAM$/u)
    assert.match(twelve[1], /^~1:59\sAM$/u)
    assert.match(twelve[2], /^~12:59\sPM$/u)
    assert.match(twelve[3], /^~1:59\sPM$/u)
    assert.ok(twelve.every((text) => text.startsWith(ESTIMATED_MARK)))
    // Other digits count as one shape too.
    assert.equal(timeColumnSamples({ locale: 'ar-EG', timeZone: 'UTC' }).length, 4)
})

test('the time column sizer is a CSS string of the samples, one per line', () => {
    assert.equal(timeColumnSizer({ locale: 'en-GB', timeZone: 'UTC' }), '"~00:59"')
    const twelve = timeColumnSamples({ locale: 'en-US', timeZone: 'UTC' })
    assert.equal(timeColumnSizer({ locale: 'en-US', timeZone: 'UTC' }), `"${twelve.join('\\A ')}"`)
    // Quotes and backslashes of a locale string cannot end the CSS string.
    assert.equal(cssStringOf(['a"b\\c', 'd']), '"a\\"b\\\\c\\A d"')
})
