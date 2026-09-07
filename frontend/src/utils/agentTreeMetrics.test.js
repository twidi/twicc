import test from 'node:test'
import assert from 'node:assert/strict'
import { agentCost, agentSubtreeCost, agentForestCost } from './agentTreeMetrics.js'

const storeWith = (rows = {}) => ({ getSession: id => rows[id] })
const node = (id, cost, children = []) => ({ id, entry: { metrics: cost == null ? undefined : { totalCost: cost } }, children })

test('a loaded session row wins over the snapshot metrics', () => {
    const store = storeWith({ a: { total_cost: '2.5' } })
    assert.equal(agentCost(store, 'a', { totalCost: 1 }), 2.5)
    assert.equal(agentCost(store, 'b', { totalCost: 1 }), 1)
    assert.equal(agentCost(store, 'b', undefined), null)
})
test('a subtree sums every depth exactly once', () => {
    const tree = node('root-child', 1, [node('d2', 0.5, [node('d3', 0.25)])])
    assert.equal(agentSubtreeCost(storeWith(), tree), 1.75)
    assert.equal(agentForestCost(storeWith(), [tree, node('other', 2)]), 3.75)
})
test('an unknown cost stays null instead of reading as free', () => {
    assert.equal(agentSubtreeCost(storeWith(), node('a', null)), null)
    assert.equal(agentForestCost(storeWith(), []), null)
    // A known child still surfaces, even under an unknown parent.
    assert.equal(agentSubtreeCost(storeWith(), node('a', null, [node('b', 3)])), 3)
})
