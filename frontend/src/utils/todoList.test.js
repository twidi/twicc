import { test } from 'node:test'
import assert from 'node:assert/strict'
import { findNewlyCompleted } from './todoList.js'

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
