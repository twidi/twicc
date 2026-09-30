// Run with: node --test src/utils/liveStates.test.js (from the frontend dir)
// Live states (visual refresh step 6b, docs/plans/2026-09-30-live-states-design.md §7.1).
import test from 'node:test'
import assert from 'node:assert/strict'

import { isContextRingLive } from './liveStates.js'

const request = { request_id: 'r1', request_type: 'tool_approval' }

test('1. live while the agent works and waits for nobody', () => {
    assert.equal(isContextRingLive({ state: 'assistant_turn' }, []), true)
    assert.equal(isContextRingLive({ state: 'assistant_turn' }, undefined), true)
})

test('2. calm while a request waits for the user', () => {
    assert.equal(isContextRingLive({ state: 'assistant_turn' }, [request]), false)
})

test('3. not live outside an assistant turn', () => {
    for (const state of ['starting', 'user_turn']) {
        assert.equal(isContextRingLive({ state }, []), false, state)
    }
    assert.equal(isContextRingLive(null, []), false, 'no process')
})
