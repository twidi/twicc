import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, computed, watch, nextTick } from 'vue'
import { getProjectActivityIndex } from './projectProcessActivity.js'

function fixture() {
    const store = reactive({ isStartupInProgress: false,
        sessions: { a: { id: 'a', project_id: 'p', last_new_content_at: '2026-10-07T12:00:00Z' },
            b: { id: 'b', project_id: 'q', last_new_content_at: '2026-10-07T12:00:00Z' } },
        processStates: { a: { state: 'assistant_turn', project_id: 'p', pending_requests: [], active_crons: [] } },
    })
    return { store, index: getProjectActivityIndex(store) }
}

test('shared indexes scan unread session fields once across multiple consumers', () => {
    const { store, index } = fixture(); let reads = 0
    Object.defineProperty(store.sessions.b, 'last_new_content_at', { configurable: true, get() { reads++; return '2026-10-07T12:00:00Z' } })
    assert.equal(index.summary(['q']).unreadCount, 1)
    const before = reads
    index.summary(['p']); index.summary(['p', 'q']); index.totalUnread()
    assert.equal(reads, before); assert.strictEqual(getProjectActivityIndex(store), index)
})

test('memory and labels do not recompute activity consumers', async () => {
    const { store, index } = fixture(); let calls = 0
    const summary = computed(previous => { calls++; return index.summary(['p'], previous) })
    const before = summary.value; const stop = watch(summary, () => {})
    store.processStates.a.memory = 123; store.processStates.a.label = 'Working'
    await nextTick(); assert.strictEqual(summary.value, before); assert.equal(calls, 1); stop()
})

test('state requests crons shells and process removal update the canonical counts', () => {
    const { store, index } = fixture()
    assert.deepEqual(index.summary(['p', 'q', 'p']), { processCount: 1, pendingRequestCount: 0,
        hasAssistantTurn: true, activeCronCount: 0, backgroundShellCount: 0, unreadCount: 1 })
    store.processStates.a.pending_requests = [{ id: 'r' }]
    store.processStates.a.active_crons = [{ id: 'cron' }]
    store.processStates.a.background_work_in_progress = { shells: 2 }
    store.processStates.a.state = 'user_turn'
    assert.deepEqual(index.summary(['p']), { processCount: 1, pendingRequestCount: 1,
        hasAssistantTurn: false, activeCronCount: 1, backgroundShellCount: 2, unreadCount: 1 })
    delete store.processStates.a
    assert.equal(index.summary(['p']).processCount, 0); assert.equal(index.totalUnread(), 2)
})

test('read mute archive draft ephemeral subagent and hidden changes exclude unread sessions', () => {
    for (const change of [s => { s.last_viewed_at = s.last_new_content_at }, s => { s.mute_on_user_turn = true },
        s => { s.archived = true }, s => { s.draft = true }, s => { s.ephemeral = true },
        s => { s.parent_session_id = 'root' }, s => { s.hidden = true }]) {
        const { store, index } = fixture(); assert.equal(index.summary(['q']).unreadCount, 1)
        change(store.sessions.b); assert.equal(index.summary(['q']).unreadCount, 0)
    }
})

test('startup guard and project reassignment preserve canonical unread and process exclusions', () => {
    const { store, index } = fixture()
    store.isStartupInProgress = true; assert.equal(index.totalUnread(), 0)
    store.isStartupInProgress = false; assert.equal(index.totalUnread(), 1)
    store.sessions.b.project_id = 'p'; assert.equal(index.summary(['p']).unreadCount, 1)
    store.processStates.a.synthetic = true; assert.equal(index.summary(['p']).processCount, 0)
    delete store.processStates.a.synthetic; store.sessions.a.hidden = true
    assert.equal(index.summary(['p']).processCount, 0)
    delete store.sessions.b; assert.equal(index.totalUnread(), 0)
})

test('unrelated projects retain aggregate references when a different project changes', () => {
    const { store, index } = fixture(); const before = index.summary(['q'])
    store.processStates.a.pending_requests.push({ id: 'r' })
    assert.strictEqual(index.summary(['q'], before), before)
    store.sessions.b.last_viewed_at = store.sessions.b.last_new_content_at
    assert.notStrictEqual(index.summary(['q'], before), before)
})
