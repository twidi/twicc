import test from 'node:test'
import assert from 'node:assert/strict'

import { getSessionUnavailableReason } from './sessionAvailability.js'

function getterOf(sessions) {
    return id => sessions[id]
}

test('a session without a reason is available', () => {
    const get = getterOf({ s: { id: 's', unavailable_reason: null } })
    assert.equal(getSessionUnavailableReason(get, 's'), null)
})

test('any reason blocks the session, whatever its value', () => {
    for (const reason of ['rollout_missing', 'codex_migration_failed:x', 'a_future_reason']) {
        const get = getterOf({ s: { id: 's', unavailable_reason: reason } })
        assert.equal(getSessionUnavailableReason(get, 's'), reason)
    }
})

test('a subagent follows its parent', () => {
    const get = getterOf({
        p: { id: 'p', unavailable_reason: 'rollout_missing' },
        a: { id: 'a', unavailable_reason: null },
    })
    assert.equal(getSessionUnavailableReason(get, 'a', 'p'), 'rollout_missing')
    assert.equal(getSessionUnavailableReason(get, 'a'), null)
})

test('an unknown session is not blocked', () => {
    assert.equal(getSessionUnavailableReason(getterOf({}), 'missing', 'also-missing'), null)
})
