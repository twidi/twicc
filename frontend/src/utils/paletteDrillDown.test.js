// frontend/src/utils/paletteDrillDown.test.js
//
// Run with:  node --test src/utils/paletteDrillDown.test.js   (from the frontend dir)

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { splitDrillDownQuery } from './paletteDrillDown.js'

const PARENT = 'Go to Session…'

test('whole query consumed by the parent leaves an empty tail', () => {
    const split = splitDrillDownQuery('sess', PARENT)
    assert.equal(split.head, 'sess')
    assert.equal(split.tail, '')
})

test('the remainder after the parent match becomes the tail', () => {
    const split = splitDrillDownQuery('sess tw', PARENT)
    assert.equal(split.head, 'sess')
    assert.equal(split.tail, 'tw')
})

test('no separator needed: the boundary is computed', () => {
    const split = splitDrillDownQuery('sesstw', PARENT)
    assert.equal(split.head, 'sess')
    assert.equal(split.tail, 'tw')
})

test('the longest matching prefix wins over a shorter one with a tail', () => {
    // "sessi" fully matches "Session"; it must not become "sess" + "i".
    const split = splitDrillDownQuery('sessi', PARENT)
    assert.equal(split.head, 'sessi')
    assert.equal(split.tail, '')
})

test('a multi-word head is kept whole', () => {
    const split = splitDrillDownQuery('go sess twicc', PARENT)
    assert.equal(split.head, 'go sess')
    assert.equal(split.tail, 'twicc')
})

test('the parent must consume at least two characters', () => {
    // "t" alone would fuzzy-match the "t" of "to"; the floor rejects it so a
    // child never surfaces on a query that only really matches the child.
    assert.equal(splitDrillDownQuery('twicc', PARENT), null)
})

test('a parent that does not match at all yields null', () => {
    assert.equal(splitDrillDownQuery('xyz', PARENT), null)
})

test('an empty query yields null', () => {
    assert.equal(splitDrillDownQuery('', PARENT), null)
    assert.equal(splitDrillDownQuery('   ', PARENT), null)
})

test('the head match carries the fuzzy result for highlighting', () => {
    const split = splitDrillDownQuery('sess tw', PARENT)
    assert.equal(split.headMatch.match, true)
    assert.deepEqual(split.headMatch.ranges, [[6, 9]])
})
