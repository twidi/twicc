import test from 'node:test'
import assert from 'node:assert/strict'
import { retainSessionArray, retainIds, retainIdSet } from './retain.js'

test('retainSessionArray keeps the previous array only for the same records in the same order', () => {
    const a = { id: 'a' }, b = { id: 'b' }
    const previous = [a, b]
    assert.strictEqual(retainSessionArray(previous, [a, b]), previous)
    const reordered = [b, a]
    assert.strictEqual(retainSessionArray(previous, reordered), reordered)
    const sameIdsOtherRecords = [{ id: 'a' }, b]
    assert.strictEqual(retainSessionArray(previous, sameIdsOtherRecords), sameIdsOtherRecords)
    const first = [a]
    assert.strictEqual(retainSessionArray(undefined, first), first)
})

test('retainIds keeps the previous array only for the same ids in the same order', () => {
    const previous = ['a', 'b']
    assert.strictEqual(retainIds(previous, ['a', 'b']), previous)
    const reordered = ['b', 'a']
    assert.strictEqual(retainIds(previous, reordered), reordered)
    const longer = ['a', 'b', 'c']
    assert.strictEqual(retainIds(previous, longer), longer)
})

test('retainIds accepts null on either side', () => {
    assert.equal(retainIds(null, null), null)
    const next = ['a']
    assert.strictEqual(retainIds(null, next), next)
    assert.strictEqual(retainIds(undefined, next), next)
    assert.equal(retainIds(['a'], null), null)
})

test('retainIdSet keeps the previous set only for the same members', () => {
    const previous = new Set(['a', 'b'])
    assert.strictEqual(retainIdSet(previous, new Set(['b', 'a'])), previous)
    const other = new Set(['a', 'c'])
    assert.strictEqual(retainIdSet(previous, other), other)
    const larger = new Set(['a', 'b', 'c'])
    assert.strictEqual(retainIdSet(previous, larger), larger)
    const first = new Set(['a'])
    assert.strictEqual(retainIdSet(undefined, first), first)
})
