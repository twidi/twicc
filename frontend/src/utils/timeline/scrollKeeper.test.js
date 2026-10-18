import assert from 'node:assert/strict'
import test from 'node:test'

import { TOP_EPSILON, chooseAnchor, createScrollKeeper, planScrollRestore } from './scrollKeeper.js'

function fakeScroller({ scrollTop = 0, anchor = null } = {}) {
    const calls = []
    return {
        calls,
        getScrollState: () => ({ scrollTop }),
        getScrollAnchor: () => anchor,
        scrollToAnchor: (value) => calls.push(['anchor', value]),
        scrollToTop: () => calls.push(['top']),
    }
}

const anchor = { key: 'event-7', index: 3, offset: 12 }

const indexes = (...keys) => new Map(keys.map((key, index) => [key, index]))

test('at the top nothing is restored: new rows appear at the top', () => {
    const scroller = fakeScroller({ scrollTop: 0, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    assert.equal(snapshot.atTop, true)
    keeper.restore(snapshot, indexes('a', 'b', 'c', 'event-7'))
    assert.deepEqual(scroller.calls, [])
})

test('a few pixels from the top still counts as the top', () => {
    const scroller = fakeScroller({ scrollTop: TOP_EPSILON, anchor })
    assert.equal(createScrollKeeper(() => scroller).capture().atTop, true)
    assert.equal(createScrollKeeper(() => fakeScroller({ scrollTop: TOP_EPSILON + 1, anchor })).capture().atTop, false)
})

test('scrolled down, rows inserted above: the first visible row is put back where it was', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    keeper.restore(snapshot, indexes('n1', 'n2', 'event-9', 'event-8', 'event-7')) // event-7 was at index 3, now 4
    assert.deepEqual(scroller.calls, [['anchor', anchor]])
})

test('scrolled down, rows added below (an older page): the scroll is left alone', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    keeper.restore(snapshot, indexes('a', 'b', 'c', 'event-7', 'older-1', 'older-2'))
    assert.deepEqual(scroller.calls, [])
})

test('the row being read is gone (another scope): back to the top', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    keeper.restore(keeper.capture(), indexes('event-100'))
    assert.deepEqual(scroller.calls, [['top']])
})

test('no anchor (an empty list) or no scroller: nothing happens', () => {
    const empty = fakeScroller({ scrollTop: 500, anchor: null })
    const keeper = createScrollKeeper(() => empty)
    keeper.restore(keeper.capture(), indexes('event-1'))
    assert.deepEqual(empty.calls, [])
    const none = createScrollKeeper(() => null)
    assert.equal(none.capture(), null)
    assert.doesNotThrow(() => none.restore(null, new Map()))
})

test('planScrollRestore', () => {
    assert.equal(planScrollRestore(null, new Map()), 'none')
    assert.equal(planScrollRestore({ atTop: true, anchor }, indexes('a', 'b', 'c', 'event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor: null }, indexes('event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor }, indexes('a', 'b', 'c', 'd', 'event-7')), 'anchor')
    assert.equal(planScrollRestore({ atTop: false, anchor }, indexes('a', 'b', 'c', 'event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor }, new Map()), 'top')
})

// Rows that never move on an insertion: the top room (index 0) and a day separator whose new rows land below it.
// Anchored on them, the restore would see "same index" and let the events on screen shift down.
const positions = [
    { key: 'timeline-top', top: 0, height: 49 },
    { key: 'daysep-2026-10-14', top: 49, height: 40 },
    { key: 'event-7', top: 89, height: 34 },
    { key: 'event-6', top: 123, height: 34 },
]
const isEvent = (key) => key.startsWith('event-')

test('an anchor on a row that is not an event moves to the next event row, at the same scroll position', () => {
    const onRoom = { key: 'timeline-top', index: 0, offset: 30 }
    assert.deepEqual(chooseAnchor(onRoom, positions, isEvent), { key: 'event-7', index: 2, offset: 30 - 89 })
    const onDay = { key: 'daysep-2026-10-14', index: 1, offset: 5 }
    assert.deepEqual(chooseAnchor(onDay, positions, isEvent), { key: 'event-7', index: 2, offset: 5 - 40 })
    // An event anchor, no positions, or no event row below: the anchor is kept.
    assert.equal(chooseAnchor(anchor, positions, isEvent), anchor)
    assert.equal(chooseAnchor(onRoom, undefined, isEvent), onRoom)
    assert.equal(chooseAnchor(onRoom, positions.slice(0, 2), isEvent), onRoom)
    assert.equal(chooseAnchor(null, positions, isEvent), null)
})

test('scrolled within the top room, a live insertion still puts the visible events back', () => {
    const scroller = { ...fakeScroller({ scrollTop: 30, anchor: { key: 'timeline-top', index: 0, offset: 30 } }), positions }
    const keeper = createScrollKeeper(() => scroller, { isAnchorKey: isEvent })
    const snapshot = keeper.capture()
    // A new event lands above event-7: the room and the separator keep their index, event-7 moves.
    keeper.restore(snapshot, indexes('timeline-top', 'daysep-2026-10-14', 'event-8', 'event-7', 'event-6'))
    assert.deepEqual(scroller.calls, [['anchor', { key: 'event-7', index: 2, offset: -59 }]])
})
