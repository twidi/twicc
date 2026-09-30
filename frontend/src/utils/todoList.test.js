import { test } from 'node:test'
import assert from 'node:assert/strict'
import { countTasks, findNewlyCompleted, tickRanks, TICK_MAX_RANK } from './todoList.js'

const todo = (content, status, activeForm) => ({ content, status, ...(activeForm ? { activeForm } : {}) })

test('findNewlyCompleted: a status change to completed pops', () => {
    const previous = [todo('A', 'completed'), todo('B', 'in_progress'), todo('C', 'pending')]
    const next = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'in_progress')]
    assert.deepEqual([...findNewlyCompleted(previous, next)], [1])
})

test('findNewlyCompleted: an already-completed item does not pop', () => {
    const list = [todo('A', 'completed'), todo('B', 'pending')]
    assert.deepEqual([...findNewlyCompleted(list, [todo('A', 'completed'), todo('B', 'pending')])], [])
})

test('findNewlyCompleted: an inserted item that shifts indices does not pop', () => {
    const previous = [todo('A', 'pending'), todo('B', 'completed')]
    const next = [todo('New', 'pending'), todo('A', 'completed'), todo('B', 'completed')]
    // Index 1 was 'B' (completed) and is now 'A': different identity, no pop.
    // Index 2 has no previous item: no pop.
    assert.deepEqual([...findNewlyCompleted(previous, next)], [])
    // Same identity at the same index still pops.
    const shifted = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'completed')]
    assert.deepEqual([...findNewlyCompleted([todo('A', 'pending'), todo('X', 'pending')], shifted)], [0])
})

test('findNewlyCompleted: items with only activeForm compare by activeForm', () => {
    const previous = [{ activeForm: 'Doing A', status: 'in_progress' }, { activeForm: 'Doing B', status: 'pending' }]
    const next = [{ activeForm: 'Doing A', status: 'completed' }, { activeForm: 'Doing X', status: 'completed' }]
    assert.deepEqual([...findNewlyCompleted(previous, next)], [0])
})

test('findNewlyCompleted: items without content nor activeForm compare by index alone', () => {
    assert.deepEqual([...findNewlyCompleted([{ status: 'pending' }], [{ status: 'completed' }])], [0])
})

test('findNewlyCompleted: null or undefined previous list gives an empty set', () => {
    const next = [todo('A', 'completed')]
    for (const previous of [null, undefined]) {
        const result = findNewlyCompleted(previous, next)
        assert.ok(result instanceof Set)
        assert.equal(result.size, 0)
    }
})

test('findNewlyCompleted: a shorter previous list only compares the shared indices', () => {
    const previous = [todo('A', 'pending')]
    const next = [todo('A', 'completed'), todo('B', 'completed')]
    assert.deepEqual([...findNewlyCompleted(previous, next)], [0])
})

test('TICK_MAX_RANK is 8', () => {
    assert.equal(TICK_MAX_RANK, 8)
})

test('tickRanks: two items completing together are ranked in list order', () => {
    const previous = [todo('A', 'completed'), todo('B', 'in_progress'), todo('C', 'pending'), todo('D', 'pending')]
    const next = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'pending'), todo('D', 'completed')]
    assert.deepEqual(tickRanks(previous, next), new Map([[1, 0], [3, 1]]))
})

test('tickRanks: a single item gets rank 0', () => {
    const previous = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'in_progress')]
    const next = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'completed')]
    assert.deepEqual(tickRanks(previous, next), new Map([[2, 0]]))
})

test('tickRanks: nothing newly completed gives an empty map', () => {
    const list = [todo('A', 'completed'), todo('B', 'pending')]
    assert.equal(tickRanks(list, [todo('A', 'completed'), todo('B', 'pending')]).size, 0)
    assert.equal(tickRanks(null, list).size, 0)
})

test('tickRanks: twelve items completing together are capped at TICK_MAX_RANK', () => {
    const names = Array.from({ length: 12 }, (_, i) => `T${i}`)
    const previous = names.map((n) => todo(n, 'pending'))
    const next = names.map((n) => todo(n, 'completed'))
    assert.deepEqual([...tickRanks(previous, next).values()], [0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 8, 8])
})

test('tickRanks: an inserted item that shifts indices gives an empty map', () => {
    const previous = [todo('A', 'pending'), todo('B', 'completed')]
    const next = [todo('New', 'pending'), todo('A', 'completed'), todo('B', 'completed')]
    assert.equal(tickRanks(previous, next).size, 0)
})

test('countTasks: null gives zeros', () => {
    assert.deepEqual(countTasks(null), { done: 0, total: 0, percent: 0 })
})

test('countTasks: deleted tasks are out of both numbers', () => {
    const list = [todo('A', 'completed'), todo('B', 'deleted'), todo('C', 'pending')]
    assert.deepEqual(countTasks(list), { done: 1, total: 2, percent: 50 })
})

test('countTasks: two of five done is 40 percent', () => {
    const list = [todo('A', 'completed'), todo('B', 'completed'), todo('C', 'in_progress'), todo('D', 'pending'), todo('E', 'pending')]
    assert.deepEqual(countTasks(list), { done: 2, total: 5, percent: 40 })
})

test('countTasks: all deleted gives total 0', () => {
    assert.deepEqual(countTasks([todo('A', 'deleted'), todo('B', 'deleted')]), { done: 0, total: 0, percent: 0 })
})
