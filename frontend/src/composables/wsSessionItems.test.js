import assert from 'node:assert/strict'
import test from 'node:test'

import { applySessionItemsAdded } from './wsSessionItems.js'

function makeStore(session) {
    const calls = []
    return {
        calls,
        getSession: () => session,
        areSessionItemsFetched: () => true,
        markItemsLive: (...args) => calls.push(['live', ...args]),
        addSessionItems: (...args) => calls.push(['add', ...args]),
    }
}

const message = {
    session_id: 'session-1',
    items: [{ line_num: 4 }],
    updated_metadata: [{ line_num: 3 }],
}

test('obsolete sessions ignore the complete item event', () => {
    const store = makeStore({ compute_version_up_to_date: false })

    assert.equal(applySessionItemsAdded(store, message), false)
    assert.deepEqual(store.calls, [])
})

for (const [name, session] of [
    ['ready sessions', { compute_version_up_to_date: true }],
    ['unknown sessions', undefined],
]) {
    test(`${name} keep the existing item behavior`, () => {
        const store = makeStore(session)

        assert.equal(applySessionItemsAdded(store, message), true)
        assert.deepEqual(store.calls, [
            ['live', 'session-1', [4]],
            ['add', 'session-1', message.items, message.updated_metadata],
        ])
    })
}
