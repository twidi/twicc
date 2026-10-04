import test from 'node:test'
import assert from 'node:assert/strict'
import { settleTools } from './toolStatusDelay.js'

test('a new tool is hidden until it has run for the delay', () => {
    const seen = new Map()
    const tools = [{ id: 'a', name: 'Read' }]
    assert.deepEqual(settleTools(tools, seen, 1000, 500), { tools: [], nextCheckInMs: 500 })
    assert.deepEqual(settleTools(tools, seen, 1300, 500), { tools: [], nextCheckInMs: 200 })
    assert.deepEqual(settleTools(tools, seen, 1500, 500), { tools, nextCheckInMs: null })
})

test('only the tools old enough are shown; the check targets the youngest wait', () => {
    const seen = new Map()
    const a = { id: 'a', name: 'Read' }
    const b = { id: 'b', name: 'Grep' }
    settleTools([a], seen, 0, 500)
    const result = settleTools([a, b], seen, 600, 500)
    assert.deepEqual(result.tools, [a])
    assert.equal(result.nextCheckInMs, 500)
})

test('a finished tool is forgotten, so the same id starts over', () => {
    const seen = new Map()
    const a = { id: 'a', name: 'Read' }
    settleTools([a], seen, 0, 500)
    settleTools([], seen, 100, 500)
    assert.equal(seen.size, 0)
    assert.deepEqual(settleTools([a], seen, 900, 500).tools, [])
})

test('a zero delay shows tools at once', () => {
    assert.deepEqual(settleTools([{ id: 'a' }], new Map(), 0, 0).tools, [{ id: 'a' }])
})
