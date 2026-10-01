import test from 'node:test'
import assert from 'node:assert/strict'
import { LIST_EXIT_DURATION_MS, pickExits, mergeExits } from './listExit.js'

const key = (x) => x.id
const row = (id) => ({ id })
const prev = ['a', 'b', 'c', 'd', 'e'].map(row)

// A row that leaves the list (archived) stays on screen for the length of its exit animation, in
// its place; only a row that was on screen, and only when the list's scope did not change.
test('1. an eligible row that was on screen and is gone from the next list exits, after the row that preceded it', () => {
    const exits = pickExits({ previousItems: prev, nextKeys: new Set(['a', 'c', 'd', 'e']), getKey: key, range: { start: 0, end: 5 }, isEligible: () => true })
    assert.deepEqual(exits, [{ item: row('b'), key: 'b', afterKey: 'a' }])
})

test('2. a row off screen (outside the visible range, end exclusive) just disappears', () => {
    const exits = pickExits({ previousItems: prev, nextKeys: new Set(['a', 'b', 'd', 'e']), getKey: key, range: { start: 0, end: 2 }, isEligible: () => true })
    assert.deepEqual(exits, [])
    const edge = pickExits({ previousItems: prev, nextKeys: new Set(['a', 'b', 'd', 'e']), getKey: key, range: { start: 2, end: 3 }, isEligible: () => true })
    assert.equal(edge.length, 1)
})

test('3. a row that is not eligible (not an archive) just disappears', () => {
    const exits = pickExits({ previousItems: prev, nextKeys: new Set(['a', 'c', 'd', 'e']), getKey: key, range: { start: 0, end: 5 }, isEligible: () => false })
    assert.deepEqual(exits, [])
})

test('4. the first row has no row before it: afterKey is null; consecutive rows share the anchor, in order', () => {
    const exits = pickExits({ previousItems: prev, nextKeys: new Set(['d', 'e']), getKey: key, range: { start: 0, end: 5 }, isEligible: () => true })
    assert.deepEqual(exits.map((e) => [e.key, e.afterKey]), [['a', null], ['b', null], ['c', null]])
})

test('5. a row already exiting is not picked again (its key is skipped)', () => {
    const exits = pickExits({ previousItems: prev, nextKeys: new Set(['a', 'c', 'd', 'e']), getKey: key, range: { start: 0, end: 5 }, isEligible: () => true, skipKeys: new Set(['b']) })
    assert.deepEqual(exits, [])
})

test('6. mergeExits puts each exiting row back after its anchor, keeping their order', () => {
    const next = ['a', 'd', 'e'].map(row)
    const exits = [{ item: row('b'), key: 'b', afterKey: 'a' }, { item: row('c'), key: 'c', afterKey: 'a' }]
    assert.deepEqual(mergeExits(next, exits, key).map(key), ['a', 'b', 'c', 'd', 'e'])
})

test('7. mergeExits: a null anchor means the top of the list; an anchor that left too means the top as well', () => {
    const next = ['c', 'd'].map(row)
    assert.deepEqual(mergeExits(next, [{ item: row('a'), key: 'a', afterKey: null }], key).map(key), ['a', 'c', 'd'])
    assert.deepEqual(mergeExits(next, [{ item: row('b'), key: 'b', afterKey: 'zzz' }], key).map(key), ['b', 'c', 'd'])
})

test('8. no exit: the same array comes back (no needless re-render)', () => {
    const next = ['a', 'b'].map(row)
    assert.equal(mergeExits(next, [], key), next)
})

test('9. the exit lasts 260ms', () => {
    assert.equal(LIST_EXIT_DURATION_MS, 260)
})
