import assert from 'node:assert/strict'
import test from 'node:test'

import { isExecChainRunning } from './execRunning.js'

test('a closing link ends the chain whatever the state', () => {
    for (const extra of ['{"is_terminated":true}', { is_terminated: true }]) {
        assert.equal(isExecChainRunning({ extra, processState: 'assistant_turn' }), false)
        assert.equal(isExecChainRunning({ extra, processState: 'user_turn', backgroundShells: 2 }), false)
    }
})

test('without a closing link the chain runs while the agent works', () => {
    assert.equal(isExecChainRunning({ extra: null, processState: 'assistant_turn' }), true)
    assert.equal(isExecChainRunning({ extra: 'not json', processState: 'assistant_turn' }), true)
})

test('in user turn it stops, unless background shells still run', () => {
    assert.equal(isExecChainRunning({ extra: null, processState: 'user_turn' }), false)
    assert.equal(isExecChainRunning({ extra: null, processState: 'user_turn', backgroundShells: 0 }), false)
    assert.equal(isExecChainRunning({ extra: null, processState: 'user_turn', backgroundShells: 1 }), true)
})

test('an unknown state keeps assuming it runs', () => {
    assert.equal(isExecChainRunning({ extra: null, processState: null }), true)
    assert.equal(isExecChainRunning(), true)
})
