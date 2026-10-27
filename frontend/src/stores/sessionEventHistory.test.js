import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, defineStore } from 'pinia'
import { createSessionEventHistoryActions } from '../utils/chatEvents/historyActions.js'
const tick = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }
const response = events => ({ events, sessions: {}, latest_id: 100, has_more: false, truncated: false })
let serial = 0
function fixture() {
    const requests = [], changes = [], target = new EventTarget()
    const actions = createSessionEventHistoryActions({ eventTarget: () => target,
        fetchPage: params => new Promise((resolve, reject) => requests.push({ ...params, resolve, reject })) })
    const makeStore = () => defineStore(`history-${++serial}`, { state: () => ({ sessions: { s: { id: 's', last_line: 0 } },
        localState: { sessionEventHistory: {}, sessionEventSegments: {}, sessionEventRetired: {} } }),
        actions: { ...actions, recomputeVisualItems(id) { changes.push(id) } } })(createPinia())
    return { store: makeStore(), makeStore, requests, changes, target }
}
test('persisted empty chats load once; a mode recompute does not restart traversal', async () => {
    const f = fixture(), a = f.store.ensureSessionEventHistory('s'), b = f.store.ensureSessionEventHistory('s')
    assert.strictEqual(a, b); await tick(); assert.equal(f.requests.length, 1)
    f.requests[0].resolve(response([{ id: 1, session_id: 's', kind: 'session.created', occurred_at: '2026-10-27T00:00:00.000000Z' }]))
    await tick(); f.requests[1].resolve(response([])); await a.idle()
    assert.equal(f.store.localState.sessionEventHistory.s.snapshot.events.length, 1)
    assert.deepEqual(f.changes, ['s'])
    f.store.clearSessionEventHistory('s')
})
test('drafts, unknown sessions and hidden sessions do not start requests', async () => {
    const f = fixture(); f.store.sessions.s.draft = true; f.store.ensureSessionEventHistory('s')
    f.store.sessions.h = { hidden: true }; f.store.ensureSessionEventHistory('h'); f.store.ensureSessionEventHistory('missing')
    await tick(); assert.equal(f.requests.length, 0)
})
test('store ownership is isolated and clear aborts stale requests before reloading', async () => {
    const f = fixture(), other = f.makeStore()
    const a = f.store.ensureSessionEventHistory('s'), b = other.ensureSessionEventHistory('s'); await tick()
    f.store.clearSessionEventHistory('s'); assert.equal(f.requests[0].signal.aborted, true)
    f.requests[0].resolve(response([])); await a.idle(); assert.equal(f.store.localState.sessionEventHistory.s, undefined)
    assert.equal(other.localState.sessionEventHistory.s.status, 'loading')
    other.clearSessionEventHistory('s'); f.requests[1].resolve(response([])); await b.idle()
    const next = f.store.ensureSessionEventHistory('s'); assert.notStrictEqual(next, a)
    await tick(); f.store.clearSessionEventHistory('s'); f.requests[2].resolve(response([])); await next.idle()
})
