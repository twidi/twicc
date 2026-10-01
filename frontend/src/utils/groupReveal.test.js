import test from 'node:test'
import assert from 'node:assert/strict'
import { GROUP_REVEAL_SLIDE_MS, GROUP_REVEAL_WINDOW_MS, groupRevealEndMs, groupRevealSegmentMs, planGroupReveal } from './groupReveal.js'

const key = (x) => x.lineNum
const rows = (...lineNums) => lineNums.map((lineNum) => ({ lineNum }))

// Opening a group of hidden elements adds rows to the chat list: those rows enter, one after the other, in
// list order; the head's own item is the first (index 0), the rest of the list does not enter.
test('1. the rows that are new in the list enter, in list order, indexed from 1 (0 is the head item)', () => {
    const plan = planGroupReveal({ previousKeys: new Set([1, 2, 6]), items: rows(1, 2, 3, 4, 5, 6), getKey: key })
    assert.deepEqual(plan, { rows: [{ key: 3, index: 1 }, { key: 4, index: 2 }, { key: 5, index: 3 }], count: 4 })
})

test('2. nothing new, nothing enters (a collapse, or a toggle that changed no row)', () => {
    assert.deepEqual(planGroupReveal({ previousKeys: new Set([1, 2, 3]), items: rows(1, 3), getKey: key }), { rows: [], count: 1 })
})

test('3. a row\'s space opens in a segment of at most 60ms; a long group is capped so the curtain lasts at most 400ms', () => {
    assert.equal(groupRevealSegmentMs(2), 60)
    assert.equal(groupRevealSegmentMs(6), 60)
    assert.equal(groupRevealSegmentMs(20), 20)
    assert.equal(groupRevealSegmentMs(40) * 40, 400)
})

test('4. the end is the last row\'s start plus its slide', () => {
    assert.equal(groupRevealEndMs(1), GROUP_REVEAL_SLIDE_MS)
    assert.equal(groupRevealEndMs(5), 4 * 60 + GROUP_REVEAL_SLIDE_MS)
})

test('5. the window in which a removal counts as a collapse outlasts the exit animation', () => {
    assert.ok(GROUP_REVEAL_WINDOW_MS > 260)
})
