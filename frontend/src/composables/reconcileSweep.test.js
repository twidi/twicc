import assert from 'node:assert/strict'
import test from 'node:test'

import { sweepLoadedSessions } from './reconcileSweep.js'

/**
 * Fake store: `loaded` lists the sessions whose items are fetched, `known`
 * the sessions present in the store. `refreshResults` / `coverageResults`
 * map a session id to the boolean its action resolves with (default true).
 */
function makeStore({ loaded, known, refreshResults = {}, coverageResults = {} }) {
    const calls = []
    return {
        calls,
        localState: {
            sessions: Object.fromEntries(loaded.map(id => [id, { itemsFetched: true }])),
        },
        sessions: Object.fromEntries(known.map(id => [id, { id }])),
        async refreshSessionRecord(id) {
            calls.push(['refresh', id])
            return refreshResults[id] ?? true
        },
        async ensureSessionItemsCoverage(id) {
            calls.push(['coverage', id])
            return coverageResults[id] ?? true
        },
    }
}

test('refreshes the record then checks coverage for every loaded session', async () => {
    const store = makeStore({ loaded: ['a', 'b'], known: ['a', 'b', 'c'] })
    // 'c' is known but its items are not loaded: nothing to heal there.
    store.localState.sessions.c = { itemsFetched: false }

    const failed = await sweepLoadedSessions(store)

    assert.deepEqual(failed, [])
    const perSession = (id) => store.calls.filter(([, sid]) => sid === id).map(([kind]) => kind)
    assert.deepEqual(perSession('a'), ['refresh', 'coverage'])
    assert.deepEqual(perSession('b'), ['refresh', 'coverage'])
    assert.deepEqual(perSession('c'), [])
})

test('skips a loaded session that is no longer in the store', async () => {
    const store = makeStore({ loaded: ['a', 'gone'], known: ['a'] })

    await sweepLoadedSessions(store)

    assert.deepEqual(store.calls, [['refresh', 'a'], ['coverage', 'a']])
})

test('a failed record refresh still runs the coverage check and is reported', async () => {
    // The record may already be current (refreshed by a live frame); the
    // coverage scan is cheap and must not be skipped on a transient fetch error.
    const store = makeStore({ loaded: ['a', 'b'], known: ['a', 'b'], refreshResults: { a: false } })

    const failed = await sweepLoadedSessions(store)

    assert.deepEqual(failed, ['a'])
    assert.ok(store.calls.some(([kind, id]) => kind === 'coverage' && id === 'a'))
})

test('a failed coverage fetch is reported', async () => {
    const store = makeStore({ loaded: ['a', 'b'], known: ['a', 'b'], coverageResults: { b: false } })

    const failed = await sweepLoadedSessions(store)

    assert.deepEqual(failed, ['b'])
})

test('a throwing action counts as a failure without aborting the other sessions', async () => {
    const store = makeStore({ loaded: ['a', 'b'], known: ['a', 'b'] })
    store.refreshSessionRecord = async (id) => {
        store.calls.push(['refresh', id])
        if (id === 'a') throw new Error('network')
        return true
    }

    const failed = await sweepLoadedSessions(store)

    assert.deepEqual(failed, ['a'])
    assert.deepEqual(store.calls.filter(([, id]) => id === 'b'), [['refresh', 'b'], ['coverage', 'b']])
})
