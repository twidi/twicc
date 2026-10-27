import test from 'node:test'
import assert from 'node:assert/strict'
import { captureEventScroll, planEventScrollRestore, eventStructureChanged } from './scroll.js'
const row = lineNum => ({ lineNum, ...(typeof lineNum === 'string' && lineNum.startsWith('event:') ? { isSessionEvent: true } : {}) })
function capture(keys = [1, 2, 3], { anchor = { key: 2, index: 1, offset: 12 }, bottom = false } = {}) {
    const rows = keys.map(row), positions = keys.map((key, i) => ({ key, top: i * 100, height: 100 }))
    const snapshot = captureEventScroll({ rows, intent: 1, scroller: { positions, getScrollAnchor: () => anchor,
        isAtBottom: () => bottom, getScrollRevision: () => 4 } })
    return snapshot
}
function decide(snapshot, keys, extra = {}) {
    return planEventScrollRestore({ snapshot, rows: keys.map(row), positions: keys.map((key, i) => ({ key, top: i * 100, height: 100 })),
        intent: 1, revision: 4, ...extra })
}
for (const label of ['initial publication', 'historical live insert', 'truncated recovery', 'tool split']) {
    test(`${label} retains the source key and pixel offset`, () => {
        const result = decide(capture(), [1, 'event:7', 2, 3])
        assert.equal(result.action, 'anchor'); assert.equal(result.anchor.key, 2); assert.equal(result.anchor.offset, 12)
    })
}
test('same-index geometry change restores; unchanged geometry and detail-only updates do not', () => {
    const snapshot = capture()
    assert.equal(decide(snapshot, [1, 2, 3]).action, 'none')
    assert.equal(decide(snapshot, ['event:7', 2, 3], { positions: [{ key: 'event:7', top: 0 }, { key: 2, top: 250 }, { key: 3, top: 350 }] }).action, 'anchor')
    const old = [row('event:7'), { ...row(1), suffixExpanded: false }]
    assert.equal(eventStructureChanged(old, [{ ...old[0], event: { title: 'new' } }, old[1]]), false)
    assert.equal(eventStructureChanged(old, [old[0], { ...old[1], suffixExpanded: true }]), true)
})
test('new user or navigation intent and newer scroller revision cancel before scrolling', () => {
    const snapshot = capture()
    assert.equal(decide(snapshot, [1, 'event:7', 2, 3], { intent: 2 }).action, 'none')
    assert.equal(decide(snapshot, [1, 'event:7', 2, 3], { revision: 5 }).action, 'none')
    assert.equal(decide(capture([1, 2], { bottom: true }), [1, 'event:7', 2]).action, 'bottom')
})
test('removed anchor uses the next surviving row at its previous screen offset', () => {
    const result = decide(capture(), [1, 'event:7', 3])
    assert.deepEqual(result, { action: 'anchor', anchor: { key: 3, index: 2, offset: -88 } })
    assert.equal(decide(capture(), ['event:7']).action, 'none')
})
test('top and events-only anchors remain valid; a day heading yields to the next content row', () => {
    const top = capture([1, 2], { anchor: { key: 1, index: 0, offset: 0 } })
    assert.equal(decide(top, ['event:7', 1, 2]).action, 'anchor')
    const events = capture(['event:1', 'event:2'], { anchor: { key: 'event:1', index: 0, offset: 4 } })
    assert.equal(decide(events, ['event:0', 'event:1', 'event:2']).anchor.key, 'event:1')
    const rows = [{ lineNum: 'daysep-1', isDaySeparator: true }, row(1)]
    const snapshot = captureEventScroll({ rows, intent: 0, scroller: { positions: [{ key: 'daysep-1', top: 0 }, { key: 1, top: 30 }],
        getScrollAnchor: () => ({ key: 'daysep-1', index: 0, offset: 0 }), getScrollRevision: () => 1, isAtBottom: () => false } })
    assert.deepEqual(snapshot.anchor, { key: 1, index: 1, offset: -30 })
})
