// Run with: node --test src/utils/listCascade.test.js (from the frontend dir)
// Sidebar list cascade (visual refresh step 5c, docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §4.2):
// the pure rules. visibleIndexRange: docs/plans/2026-09-29-accent-glow-design.md §17.4.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    LIST_CASCADE_MAX_INDEX,
    listCascadeEndMs,
    pickLiveEntrances,
    planListCascade,
    visibleIndexRange,
} from './listCascade.js'

const keys = (n) => Array.from({ length: n }, (_, i) => `k${i}`)

test('1. planListCascade: the rows of the range, indexed from its start', () => {
    const plan = planListCascade({ keys: keys(10), visibleStart: 3, visibleEnd: 7 })
    assert.deepEqual([...plan.entries()], [['k3', 0], ['k4', 1], ['k5', 2], ['k6', 3]])
    for (const key of ['k0', 'k1', 'k2', 'k7', 'k8', 'k9']) assert.equal(plan.has(key), false, key)
})

test('2. planListCascade: the index is capped at LIST_CASCADE_MAX_INDEX', () => {
    assert.equal(LIST_CASCADE_MAX_INDEX, 20)
    const plan = planListCascade({ keys: keys(30), visibleStart: 0, visibleEnd: 30 })
    assert.equal(plan.get('k25'), 20)
    assert.equal(plan.get('k20'), 20)
    assert.equal(plan.get('k19'), 19)
    assert.equal(plan.size, 30)
    const custom = planListCascade({ keys: keys(10), visibleStart: 0, visibleEnd: 10, maxIndex: 3 })
    assert.equal(custom.get('k9'), 3)
})

test('3. planListCascade: an empty or invalid range plans nothing; the end is clamped', () => {
    assert.equal(planListCascade({ keys: keys(10), visibleStart: 5, visibleEnd: 5 }).size, 0)
    assert.equal(planListCascade({ keys: keys(10), visibleStart: 8, visibleEnd: 3 }).size, 0)
    assert.equal(planListCascade({ keys: keys(10), visibleStart: 10, visibleEnd: 12 }).size, 0)
    assert.equal(planListCascade({ keys: [], visibleStart: 0, visibleEnd: 4 }).size, 0)
    const clamped = planListCascade({ keys: keys(4), visibleStart: 2, visibleEnd: 9 })
    assert.deepEqual([...clamped.entries()], [['k2', 0], ['k3', 1]])
})

test('4. pickLiveEntrances: noted live, absent before, present now, in list order', () => {
    const picked = pickLiveEntrances({
        previousKeys: new Set(['a', 'b']),
        keys: ['n2', 'a', 'n1', 'b', 'quiet'],
        liveKeys: new Set(['n1', 'a', 'gone', 'n2']),
    })
    // n1 and n2: live and new (list order); a: present before; gone: not in the list;
    // quiet: new but not live.
    assert.deepEqual(picked, ['n2', 'n1'])
    assert.deepEqual(pickLiveEntrances({ previousKeys: new Set(), keys: ['x'], liveKeys: new Set() }), [])
})

test('5. listCascadeEndMs: stagger × index + duration + margin', () => {
    assert.equal(listCascadeEndMs(0), 420)
    assert.equal(listCascadeEndMs(20), 860)
})

/** Rows of `height` px stacked from `top`, as { top, bottom } rects. */
const rows = (n, height = 40, top = 0) =>
    Array.from({ length: n }, (_, i) => ({ top: top + i * height, bottom: top + (i + 1) * height }))

test('6. visibleIndexRange: the rows whose rect crosses the view, end exclusive', () => {
    // All visible.
    assert.deepEqual(visibleIndexRange(rows(4), { top: 0, bottom: 200 }), { start: 0, end: 4 })
    // Scrolled: row 1 is cut at the top, row 5 at the bottom; both count.
    assert.deepEqual(visibleIndexRange(rows(10), { top: 60, bottom: 220 }), { start: 1, end: 6 })
    // A row that only touches an edge is not visible.
    assert.deepEqual(visibleIndexRange(rows(10), { top: 80, bottom: 160 }), { start: 2, end: 4 })
    // The view past the rows' end: the last rows only.
    assert.deepEqual(visibleIndexRange(rows(5), { top: 150, bottom: 400 }), { start: 3, end: 5 })
})

test('7. visibleIndexRange: no row crosses the view, or no row at all', () => {
    assert.deepEqual(visibleIndexRange(rows(3), { top: 500, bottom: 700 }), { start: 0, end: 0 })
    assert.deepEqual(visibleIndexRange(rows(3, 40, 500), { top: 0, bottom: 200 }), { start: 0, end: 0 })
    assert.deepEqual(visibleIndexRange([], { top: 0, bottom: 200 }), { start: 0, end: 0 })
})
