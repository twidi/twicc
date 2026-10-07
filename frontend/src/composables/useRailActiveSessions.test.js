import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, nextTick, reactive, ref, watch } from 'vue'
import { useRailActiveSessions } from './useRailActiveSessions.js'

const idle = { state: 'user_turn', project_id: 'p', provider: 'claude', session_title: 'Snapshot' }

test('a replaced session record remains the reactive metadata source for rail rows', async () => {
    const f = fixture()
    f.store.sessions.s = { id: 's', project_id: 'p', title: 'Session' }
    f.store.processStates.s = { ...idle }
    await flush()
    assert.strictEqual(f.api.rows.value[0].session, f.store.sessions.s)
    f.store.sessions.s = { ...f.store.sessions.s }
    await flush()
    assert.strictEqual(f.api.rows.value[0].session, f.store.sessions.s)
    f.store.sessions.s.title = 'Updated title'
    assert.equal(f.api.rows.value[0].session.title, 'Updated title')
    f.scope.stop()
})

test('changed request details retain unchanged rail rows and their subscribers', async () => {
    const f = fixture()
    f.store.sessions.s = { id: 's', project_id: 'p', title: 'Session' }
    f.store.processStates.s = { ...idle, pending_requests: [{ request_id: 'r', tool_input: 'before' }] }
    await flush()
    const before = f.api.rows.value, row = before[0]
    let updates = 0
    const stop = watch(f.api.rows, () => updates++)
    f.store.processStates.s.pending_requests = [{ request_id: 'r', tool_input: 'after' }]
    await flush()
    assert.strictEqual(f.api.rows.value, before); assert.strictEqual(f.api.rows.value[0], row)
    assert.equal(updates, 0)
    f.store.processStates.s.pending_requests = []
    await flush()
    assert.equal(f.api.rows.value[0].pendingRequest, false)
    assert.equal(updates, 1)
    stop(); f.scope.stop()
})

test('memory and labels do not invalidate a loaded rail list', async () => {
    const f = fixture()
    f.store.sessions.s = { id: 's', project_id: 'p', title: 'Session' }
    f.store.processStates.s = { ...idle }
    await flush(); const before = f.api.rows.value
    f.store.processStates.s.memory = 123; f.store.processStates.s.label = 'Working'
    await flush(); assert.strictEqual(f.api.rows.value, before)
    f.scope.stop()
})

test('assistant content timestamps do not rebuild unchanged rail rows', async () => {
    const f = fixture()
    let reads = 0
    f.store.sessions.s = { id: 's', project_id: 'p', last_new_content_at: '2026-10-07T12:00:00Z' }
    Object.defineProperty(f.store.sessions.s, 'id', { configurable: true, get() { reads++; return 's' } })
    f.store.processStates.s = { ...idle, state: 'assistant_turn' }
    await flush()
    const rows = f.api.rows.value, before = reads
    f.store.sessions.s.last_new_content_at = '2026-10-07T12:01:00Z'
    await flush()
    assert.strictEqual(f.api.rows.value, rows)
    assert.equal(reads, before)
    f.store.processStates.s.state = 'user_turn'
    await flush()
    assert.equal(f.api.rows.value[0].unread, true)
    f.scope.stop()
})
const unread = { id: 's', project_id: 'p', provider: 'claude', title: 'Loaded', last_new_content_at: '2026-10-05' }
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
function fixture() {
    const requests = []
    const store = reactive({
        sessions: {}, processStates: {},
        loadSessionById(id) {
            return new Promise((resolve, reject) => requests.push({ id, reject, resolve: session => {
                if (session) store.sessions[id] = session
                resolve(session)
            } }))
        },
    })
    const scope = effectScope()
    const current = ref(null)
    const api = scope.run(() => useRailActiveSessions(store, () => current.value))
    return { store, requests, scope, current, api }
}

test('loads absent real process metadata once and updates unread, pending, title and hidden reactively', async () => {
    const { store, requests, scope, api, current } = fixture()
    store.sessions.loaded = { id: 'loaded' }
    store.sessions.hidden = { id: 'hidden', hidden: true }
    store.processStates = { s: { ...idle }, loaded: idle, hidden: idle, synthetic: { ...idle, synthetic: true }, dead: { ...idle, state: 'dead' } }
    await flush()
    assert.deepEqual(requests.map(r => r.id), ['s'])
    let row = api.rows.value.find(r => r.session.id === 's')
    assert.equal(row.session.title, 'Snapshot')
    assert.equal(row.metadataStatus, 'loading')
    store.processStates.s.pending_requests = [{ id: 'permission' }]
    await flush()
    assert.equal(api.rows.value.find(r => r.session.id === 's').indicatorKind, 'pending')
    assert.equal(requests.length, 1)
    requests[0].resolve({ ...unread })
    await flush()
    row = api.rows.value.find(r => r.session.id === 's')
    assert.equal(row.session.title, 'Loaded')
    assert.equal(row.indicatorKind, 'unread')
    assert.equal(row.metadataStatus, 'loaded')
    current.value = 's'
    assert.equal(api.rows.value.find(r => r.session.id === 's').indicatorKind, 'pending')
    store.sessions.s.hidden = true
    assert.deepEqual(api.rows.value.map(r => r.session.id), ['loaded'])
    scope.stop()
})

test('loads cron metadata before exclusion and reveals unread or shell rows', async () => {
    const { store, requests, scope, api } = fixture()
    store.processStates.s = { ...idle, active_crons: [{ id: 'cron' }] }
    await flush()
    assert.equal(api.rows.value.length, 0)
    assert.equal(requests.length, 1)
    requests[0].resolve({ ...unread })
    await flush()
    assert.equal(api.rows.value[0].indicatorKind, 'unread')
    store.sessions.s.mute_on_user_turn = true
    assert.equal(api.rows.value.length, 0)
    store.processStates.s.background_work_in_progress = { shells: 2 }
    assert.equal(api.rows.value[0].userTurnBackgroundShells, 2)
    scope.stop()
})

for (const outcome of ['missing', 'error']) {
    test(`keeps a fallback after ${outcome} without retry loops; a new process presence can retry`, async () => {
        const { store, requests, scope, api } = fixture()
        store.processStates.s = { ...idle }
        await flush()
        if (outcome === 'missing') requests[0].resolve(null)
        else requests[0].reject(new Error('offline'))
        await flush()
        assert.equal(api.rows.value[0].metadataStatus, outcome)
        store.processStates.s.memory = 123
        await flush()
        assert.equal(requests.length, 1)
        delete store.processStates.s
        await flush()
        assert.equal(api.rows.value.length, 0)
        store.processStates.s = { ...idle }
        await flush()
        assert.equal(requests.length, 2)
        scope.stop()
        requests[1].resolve(null)
        await flush()
    })
}

test('shares in-flight loads across consumers and stop/reentry without phantom processes', async () => {
    const { store, requests, scope, api } = fixture()
    store.processStates.s = { ...idle }
    await flush()
    const second = effectScope()
    const other = second.run(() => useRailActiveSessions(store))
    await flush()
    assert.equal(requests.length, 1)
    delete store.processStates.s
    await flush()
    assert.equal(api.rows.value.length, 0)
    store.processStates.s = { ...idle }
    await flush()
    assert.equal(requests.length, 1)
    delete store.processStates.s
    await flush()
    requests[0].resolve({ ...unread })
    await flush()
    assert.equal(api.rows.value.length, 0)
    assert.equal(other.rows.value.length, 0)
    assert.deepEqual(store.processStates, {})
    scope.stop()
    second.stop()
})

test('unmount stops new loads and ignores late completion in local metadata state', async () => {
    const { store, requests, scope, api } = fixture()
    store.processStates.s = { ...idle }
    await flush()
    scope.stop()
    requests[0].resolve(null)
    store.processStates.new = { ...idle }
    await flush()
    assert.equal(requests.length, 1)
    assert.equal(api.rows.value.find(r => r.session.id === 's').metadataStatus, 'loading')
})

test('keeps All Projects ordering through snapshot rebuilds and reactive pin changes', async () => {
    const { store, requests, scope, api } = fixture()
    store.sessions = { z: { id: 'z' }, a: { id: 'a' }, m: { id: 'm' } }
    store.processStates = { a: { ...idle, started_at: 20 }, m: { ...idle, started_at: 30 }, z: { ...idle, started_at: 20 } }
    await flush()
    assert.deepEqual(api.rows.value.map(r => r.session.id), ['m', 'z', 'a'])
    store.processStates = { z: { ...idle, started_at: 20, state: 'assistant_turn' }, a: { ...idle, started_at: 20 }, m: { ...idle, started_at: 30 } }
    assert.deepEqual(api.rows.value.map(r => r.session.id), ['m', 'z', 'a'])
    store.sessions.a.pinned = 'workspace'
    assert.deepEqual(api.rows.value.map(r => r.session.id), ['a', 'm', 'z'])
    assert.equal(requests.length, 0)
    scope.stop()
})
