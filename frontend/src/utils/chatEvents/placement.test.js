import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeChatTimestamp, placeSessionEvents, orderCompactItems } from './placement.js'
const time = n => `2026-10-27T00:00:00.${String(n).padStart(6, '0')}Z`
const source = (line_num, n) => ({ line_num, timestamp: n == null ? null : time(n) })
const event = (id, n) => ({ id, occurred_at: time(n) })
const boundaries = (items, events) => placeSessionEvents(items, events).map(b => [b.beforeLine, b.events.map(e => e.id)])

test('microseconds and offsets stay precise without treating invalid timestamps as zero', () => {
    assert.equal(normalizeChatTimestamp(time(1)) + 1n, normalizeChatTimestamp('2026-10-27T01:00:00.000002+01:00'))
    assert.equal(normalizeChatTimestamp('2026-10-27T00:00:00.123Z'), normalizeChatTimestamp(time(123000)))
    for (const value of [null, '', 'invalid']) assert.equal(normalizeChatTimestamp(value), null)
})
test('events precede equal timestamps, preserve ID ties and occupy leading/trailing boundaries', () => {
    assert.deepEqual(boundaries([source(10, 10), source(11, 20)], [event(8, 20), event(7, 20), event(6, 1), event(9, 30)]), [[10, [6]], [11, [7, 8]], [null, [9]]])
})
test('placement uses metadata before filtering and is invariant under hydration and internal content', () => {
    const items = [source(1, 1), { ...source(2, 2), display_level: 2 }, source(3, 3)]
    const before = boundaries(items, [event(7, 2)])
    items[1].content = JSON.stringify({ message: { content: [{ type: 'tool_use' }, { type: 'text', text: 'answer' }] } })
    assert.deepEqual(boundaries(items, [event(7, 2)]), before)
    assert.deepEqual(before, [[2, [7]]])
})
test('missing and regressing timestamps keep source order through a monotonic fallback', () => {
    const items = [source(1, null), source(2, 10), source(3, null), source(4, 5), source(5, 20)]
    assert.deepEqual(boundaries(items, [event(1, 5), event(2, 15)]), [[1, [1]], [5, [2]]])
    assert.deepEqual(boundaries([source(1, null), source(2, null)], [event(1, 20)]), [[1, [1]]])
    assert.deepEqual(boundaries([], [event(1, 20)]), [[null, [1]]])
})
test('synthetic rows reconcile deterministically without duplicating events', () => {
    assert.deepEqual(boundaries([source(1, 10), source(-1, null)], [event(1, 15)]), [[-1, [1]]])
    assert.deepEqual(boundaries([source(1, 10), source(2, 20)], [event(1, 15)]), [[2, [1]]])
})
test('events later than every real line bind to the first synthetic row, whatever its time', () => {
    const live = [source(1, 10), source(2, 20), source(-3000, null), source(-2000, null), source(-500, null)]
    assert.deepEqual(boundaries(live, [event(1, 15), event(2, 30), event(3, 40)]), [[2, [1]], [-3000, [2, 3]]])
    // Events bound to real lines keep their place.
    assert.deepEqual(boundaries(live, [event(1, 5), event(2, 15)]), [[1, [1]], [2, [2]]])
    // A timed synthetic row (an ephemeral prompt or result) never takes an event past another one.
    assert.deepEqual(boundaries([source(1, 10), source(-2000, 20), source(-400, 30)], [event(1, 25), event(2, 35)]), [[-2000, [1, 2]]])
    assert.deepEqual(boundaries([source(-2000, null), source(-500, null)], [event(1, 15)]), [[-2000, [1]]])
})
test('compact correction copies only reordered arrays and preserves source identities', () => {
    const summary = { ...source(2, 20), kind: 'compact_summary' }
    const command = { ...source(3, 10), kind: 'user_message', content: JSON.stringify({ message: { content: '<command-name>/compact</command-name>' } }) }
    const items = Object.freeze([source(1, 1), summary, command])
    const ordered = orderCompactItems(items, true)
    assert.deepEqual(ordered.map(i => i.line_num), [1, 3, 2]); assert.strictEqual(ordered[1], command)
    assert.deepEqual(items.map(i => i.line_num), [1, 2, 3]); assert.strictEqual(orderCompactItems(items, false), items)
    assert.strictEqual(orderCompactItems(ordered, true), ordered)
})

test('placement parses timestamps once and invalidates changed source timestamps', () => {
    const items = [source(1, 10), source(2, 20), source(3, 30)]
    const events = [event(1, 25)]
    const originalParse = Date.parse
    let parses = 0
    Date.parse = (...args) => { parses++; return originalParse(...args) }
    try {
        assert.deepEqual(boundaries(items, events), [[3, [1]]])
        assert.ok(parses <= 4, `each source/event timestamp is parsed once; parsed ${parses}`)
        parses = 0
        assert.deepEqual(boundaries(items, events), [[3, [1]]])
        assert.equal(parses, 0, 'unchanged timestamps reuse their normalized values')
        items[1].timestamp = time(26)
        assert.deepEqual(boundaries(items, events), [[2, [1]]])
        assert.equal(parses, 1)
        events[0].occurred_at = time(28)
        assert.deepEqual(boundaries(items, events), [[3, [1]]])
        assert.equal(parses, 2)
    } finally { Date.parse = originalParse }
})
test('with monotonic times, loading a DEBUG_ONLY stub moves no event past a displayed row', () => {
    const debugLine = n => ({ ...source(2, n), display_level: 3 })
    const events = [event(1, 11), event(2, 15), event(3, 25)]
    // Each event -> the first displayed line it renders before (line 2 is hidden outside the debug view).
    const shown = items => Object.fromEntries(placeSessionEvents(items, events)
        .flatMap(b => b.events.map(e => [e.id, b.beforeLine === 2 ? 3 : b.beforeLine])))
    const stub = shown([source(1, 10), debugLine(null), source(3, 20), source(4, 30)])
    assert.deepEqual(stub, { 1: 3, 2: 3, 3: 4 })
    assert.deepEqual(shown([source(1, 10), debugLine(12), source(3, 20), source(4, 30)]), stub)
})
