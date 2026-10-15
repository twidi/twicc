import assert from 'node:assert/strict'
import test from 'node:test'

import { TOP_EPSILON, createScrollKeeper, planScrollRestore } from './scrollKeeper.js'

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
