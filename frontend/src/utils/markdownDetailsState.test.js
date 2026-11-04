import test from 'node:test'
import assert from 'node:assert/strict'

import {
    detailsScopes, detailsKey, rememberDetailsOpen, recalledDetailsOpen, resetDetailsStateForTests,
} from './markdownDetailsState.js'

test('a scope depends only on the blocks before it, so a growing stream keeps it', () => {
    const streaming = detailsScopes(['a', 'b', 'c'])
    const final = detailsScopes(['a', 'b', 'c', 'd'])
    assert.deepEqual(streaming, final.slice(0, 3))
    assert.equal(new Set(final).size, 4)
})

test('same title, other message: other scope', () => {
    assert.notEqual(detailsScopes(['x', 'd'])[1], detailsScopes(['y', 'd'])[1])
})

test('the remembered state is per key and undefined until toggled', () => {
    resetDetailsStateForTests()
    const key = detailsKey('s', 'Title', 0)
    assert.equal(recalledDetailsOpen(key), undefined)
    rememberDetailsOpen(key, true)
    assert.equal(recalledDetailsOpen(key), true)
    rememberDetailsOpen(key, false)
    assert.equal(recalledDetailsOpen(key), false)
    assert.equal(recalledDetailsOpen(detailsKey('s', 'Title', 1)), undefined)
})

test('the oldest entry goes first once the cap is reached', () => {
    resetDetailsStateForTests()
    for (let i = 0; i < 501; i++) rememberDetailsOpen(detailsKey('s', 't', i), true)
    assert.equal(recalledDetailsOpen(detailsKey('s', 't', 0)), undefined)
    assert.equal(recalledDetailsOpen(detailsKey('s', 't', 500)), true)
})
