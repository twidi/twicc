import test from 'node:test'
import assert from 'node:assert/strict'
import { mergeSessionEventRows, insertEventDaySeparators, isTranscriptLoadCandidate } from './rows.js'
process.env.TZ = 'UTC'
const event = (id, timestamp = '2026-10-27T00:00:00.000000Z') => ({ id, occurred_at: timestamp })
test('events at hidden source boundaries stay between visible portions without changing message flags', () => {
    const first = { lineNum: 1, isBlockStart: true }, last = { lineNum: 20, isBlockEnd: true, detailToggleFor: 1 }
    const rows = mergeSessionEventRows({ sourceItems: [{ line_num: 1 }, { line_num: 10 }, { line_num: 20 }],
        messageRows: [first, last], boundaries: [{ beforeLine: 10, events: [event(7)] }] })
    assert.deepEqual(rows.map(r => r.lineNum), [1, 'event:7', 20])
    assert.strictEqual(rows[0], first); assert.strictEqual(rows[2], last)
    assert.equal(rows[1].isBlockStart, undefined)
    assert.equal(isTranscriptLoadCandidate(rows[1]), false)
    assert.equal(isTranscriptLoadCandidate({ lineNum: -1 }), false)
    assert.equal(isTranscriptLoadCandidate({ lineNum: 12 }), true)
})
test('events-only history and trailing events use independent stable identities', () => {
    const rows = mergeSessionEventRows({ sourceItems: [], messageRows: [], boundaries: [{ beforeLine: null, events: [event(1), event(2)] }] })
    assert.deepEqual(rows.map(r => r.lineNum), ['event:1', 'event:2'])
})
test('calendar context follows visible portions across midnight inside one assistant block', () => {
    const rows = [{ lineNum: 1, timestamp: '2026-10-26T23:50:00Z', isBlockStart: true },
        { lineNum: 'event:7', isSessionEvent: true, timestamp: '2026-10-26T23:59:00Z' },
        { lineNum: 3, timestamp: '2026-10-27T00:02:00Z', isBlockEnd: true },
        { lineNum: 'event:8', isSessionEvent: true, timestamp: '2026-10-27T00:03:00Z' }]
    const result = insertEventDaySeparators(rows)
    assert.deepEqual(result.map(r => r.lineNum), [1, 'event:7', 'daysep-3', 3, 'event:8'])
    assert.equal(result[2].dayKey, '2026-10-27')
    assert.strictEqual(result[0], rows[0]); assert.strictEqual(result[3], rows[2])
})
