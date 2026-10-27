import test from 'node:test'
import assert from 'node:assert/strict'
import { createSessionEventHistory, createSessionEventHistoryState } from './history.js'

const event = (id, time = id) => ({ id, session_id: 's', kind: 'title.changed', occurred_at: `2026-10-01T00:${String(Math.floor(time / 60)).padStart(2, '0')}:${String(time % 60).padStart(2, '0')}.000000Z`, data: {} })
const body = (events = [], extra = {}) => ({ events, sessions: {}, latest_id: 400, has_more: false, truncated: false, hidden_sessions: [], shown_sessions: [], ...extra })
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
function fixture() {
    const requests = [], published = [], state = createSessionEventHistoryState()
    const controller = createSessionEventHistory({ sessionId: 's', state, onSnapshot: value => published.push(value),
        fetchPage(params) { return new Promise((resolve, reject) => requests.push({ ...params, resolve, reject })) } })
    return { requests, published, state, controller }
}
async function initial(f, events = [event(1)]) {
    f.controller.start(); await tick(); f.requests.at(-1).resolve(body(events)); await tick()
    f.requests.at(-1).resolve(body()); await f.controller.idle()
}

test('complete pagination publishes once and catches late backdated writes from the first watermark', async () => {
    const f = fixture(), rows = Array.from({ length: 201 }, (_, i) => event(i + 1))
    f.controller.start(); await tick()
    f.requests[0].resolve(body(rows.slice(101).reverse(), { has_more: true })); await tick()
    assert.equal(f.state.snapshot, null); assert.equal(f.state.status, 'loading')
    f.controller.notifyLatest(471)
    f.requests[1].resolve(body(rows.slice(1, 101).reverse(), { has_more: true, latest_id: 450 })); await tick()
    assert.equal(f.published.length, 0)
    f.requests[2].resolve(body([rows[0]], { latest_id: 470 })); await tick()
    assert.equal(f.requests[3].afterId, 400)
    f.requests[3].resolve(body([event(471, 1), event(1)], { latest_id: 471 })); await f.controller.idle()
    assert.equal(f.published.length, 1); assert.equal(f.state.snapshot.events.length, 202)
    assert.deepEqual(f.state.snapshot.events.slice(0, 3).map(e => e.id), [1, 471, 2])
    assert.equal(new Set(f.state.snapshot.events.map(e => e.id)).size, 202)
})

for (const phase of ['first', 'before', 'live']) test(`${phase} failure is visible, retains snapshot and retries safely`, async () => {
    const f = fixture()
    if (phase === 'live') { await initial(f); f.controller.refresh() }
    else f.controller.start()
    await tick()
    if (phase === 'before') { f.requests.at(-1).resolve(body([event(2)], { has_more: true })); await tick() }
    const previous = f.state.snapshot
    f.requests.at(-1).reject(new Error('offline')); await f.controller.idle()
    assert.equal(f.state.status, 'error'); assert.strictEqual(f.state.snapshot, previous)
    f.controller.retry(); await tick()
    f.requests.at(-1).resolve(body([event(3)])); await tick()
    if (phase !== 'live') { f.requests.at(-1).resolve(body()); await tick() }
    await f.controller.idle(); assert.equal(f.state.status, 'ready')
    assert.ok(f.state.snapshot.events.some(e => e.id === 3))
})

test('truncated recovery retains the old snapshot through failed history and a successful retry', async () => {
    const f = fixture(); await initial(f); const old = f.state.snapshot
    f.controller.refresh(); await tick(); f.requests.at(-1).resolve(body([], { truncated: true })); await tick()
    assert.equal(f.state.status, 'recovering'); assert.strictEqual(f.state.snapshot, old)
    f.requests.at(-1).reject(new Error('offline')); await f.controller.idle()
    assert.strictEqual(f.state.snapshot, old)
    f.controller.retry(); await tick(); f.requests.at(-1).resolve(body([event(2), event(1)])); await tick()
    f.requests.at(-1).resolve(body()); await f.controller.idle()
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [1, 2])
    assert.strictEqual(f.state.snapshot.events[0], old.events[0])
})

test('empty history completes and unchanged live fetch preserves the snapshot', async () => {
    const f = fixture(); await initial(f, []); const old = f.state.snapshot
    assert.deepEqual(old.events, [])
    f.controller.refresh(); await tick(); f.requests.at(-1).resolve(body()); await f.controller.idle()
    assert.strictEqual(f.state.snapshot, old); assert.equal(f.published.length, 1)
})

test('an invalidation during live fetch schedules another fetch and excludes other targets and turns', async () => {
    const f = fixture(); await initial(f)
    f.controller.notifyLatest(401); await tick(); f.controller.notifyLatest(405)
    f.requests.at(-1).resolve(body([{ ...event(401), session_id: 'other' }, { ...event(402), kind: 'turn.started' }], { latest_id: 402 })); await tick()
    assert.equal(f.requests.at(-1).afterId, 402)
    f.requests.at(-1).resolve(body([event(405)], { latest_id: 405 })); await f.controller.idle()
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [1, 405])
})

test('shown target triggers full recovery, hidden target clears history and aborts publication', async () => {
    const f = fixture(); await initial(f)
    f.controller.refresh(); await tick(); f.requests.at(-1).resolve(body([], { shown_sessions: ['s'] })); await tick()
    assert.equal(f.requests.at(-1).afterId, null)
    f.requests.at(-1).resolve(body([event(2)])); await tick()
    f.requests.at(-1).resolve(body([], { hidden_sessions: ['s'] })); await f.controller.idle()
    assert.equal(f.state.snapshot, null)
    const count = f.requests.length; f.controller.refresh(); await tick(); assert.equal(f.requests.length, count)
})

test('dispose rejects stale completion and aborts the pending request', async () => {
    const f = fixture(); f.controller.start(); await tick(); const request = f.requests[0]
    f.controller.dispose(); assert.equal(request.signal.aborted, true)
    request.resolve(body([event(1)])); await f.controller.idle()
    assert.equal(f.published.length, 0); assert.equal(f.state.snapshot, null)
})

test('pagination that does not advance fails instead of looping', async () => {
    const f = fixture(); f.controller.start(); await tick()
    f.requests[0].resolve(body([event(1)], { has_more: true })); await tick()
    f.requests[1].resolve(body([event(1)], { has_more: true })); await f.controller.idle()
    assert.equal(f.state.status, 'error'); assert.equal(f.requests.length, 2)
})

test('the creation, the turns and the start of a new session have no row; a resume keeps its row', async () => {
    const f = fixture(), kind = (id, k, data = {}) => ({ ...event(id), kind: k, data })
    await initial(f, [kind(1, 'session.created'), kind(2, 'process.started', { cause: 'new' }), kind(3, 'turn.started'),
        kind(4, 'turn.ended'), kind(5, 'process.started', { cause: 'resume' }), kind(6, 'title.changed')])
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [5, 6])
})

test('a session older than the timeline: its first recorded start is a resume and keeps its row', async () => {
    const f = fixture(), kind = (id, k, data = {}) => ({ ...event(id), kind: k, data })
    await initial(f, [kind(1, 'session.created'), kind(2, 'process.started', { cause: 'resume' })])
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [2])
})

test('pausing aborts a live request and rapid resume ignores its late response', async () => {
    const f = fixture(); await initial(f)
    f.controller.refresh(); await tick(); const stale = f.requests.at(-1)
    f.controller.setActive(false); assert.equal(stale.signal.aborted, true)
    f.controller.setActive(true)
    stale.resolve(body([event(2)], { latest_id: 401 })); await tick()
    assert.equal(f.requests.at(-1).afterId, 400)
    f.requests.at(-1).resolve(body([event(3)], { latest_id: 402 })); await f.controller.idle()
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [1, 3])
    assert.equal(f.state.status, 'ready')
})

test('pausing an incomplete history restarts pagination without publishing partial rows', async () => {
    const f = fixture(); f.controller.start(); await tick()
    f.requests[0].resolve(body([event(2)], { has_more: true })); await tick()
    f.controller.setActive(false)
    f.requests.at(-1).reject(new Error('aborted')); await f.controller.idle()
    assert.equal(f.state.snapshot, null); assert.notEqual(f.state.status, 'error')
    f.controller.notifyLatest(500); await tick(); assert.equal(f.requests.length, 2)
    f.controller.setActive(true); await tick()
    assert.equal(f.requests.at(-1).before, null)
    f.requests.at(-1).resolve(body([event(3), event(2), event(1)], { latest_id: 500 })); await tick()
    f.requests.at(-1).resolve(body([], { latest_id: 500 })); await f.controller.idle()
    assert.deepEqual(f.state.snapshot.events.map(e => e.id), [1, 2, 3])
    assert.equal(f.published.length, 1)
})
