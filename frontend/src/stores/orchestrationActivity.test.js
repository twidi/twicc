import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, nextTick, watch } from 'vue'
import { descendantActiveProcessStates } from '../utils/orchestrationActivity.js'
import { summarizeProcessActivity } from '../utils/processActivity.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const start = source.indexOf('        getOrchestrationActivity()')
const end = source.indexOf('        // Get cached agent link', start)
assert.ok(start >= 0 && end > start)
const getters = new Function('summarizeProcessActivity', `return { ${source.slice(start, end)} }`)(summarizeProcessActivity)
const useActivity = defineStore('orchestration-activity-test', {
    state: () => ({ processStates: { root: { state: 'assistant_turn' }, child: { state: 'assistant_turn', spawn_ancestors: ['root'] } },
        sessions: {}, runningAgent: false }),
    getters: { ...getters,
        getDescendantActiveProcessStates: state => id => descendantActiveProcessStates(state.processStates, state.sessions, id),
        hasRunningSubagent: state => () => state.runningAgent },
})
const fixture = () => useActivity(createPinia())

test('equivalent process snapshots preserve activity and subscribers', async () => {
    const store = fixture(), activity = computed(() => store.getOrchestrationActivity('root')), before = activity.value
    let updates = 0
    const stop = watch(activity, () => updates++)
    try {
        store.processStates.root = { state: 'assistant_turn', memory: 12345 }
        store.processStates.child = { state: 'assistant_turn', spawn_ancestors: ['root'], memory: 6789 }
        await nextTick()
        assert.strictEqual(activity.value, before)
        assert.equal(updates, 0)
        assert.equal(activity.value.sessions.processCount, 1)
    } finally { stop() }
})

test('main process replacement does not recompute descendant membership', () => {
    const store = fixture()
    let calls = 0
    const activity = computed(() => { calls++; return store.getOrchestrationActivity('root') })
    void activity.value
    store.processStates.root = { state: 'assistant_turn', memory: 12345 }
    void activity.value
    assert.equal(calls, 1)
})

test('descendant start stop reparent and removal remain visible', () => {
    const store = fixture()
    assert.equal(store.getOrchestrationActivity('root').sessions.processCount, 1)
    store.processStates.child.state = 'user_turn'
    assert.equal(store.getOrchestrationActivity('root').sessions, null)
    store.processStates.child.state = 'assistant_turn'
    assert.equal(store.getOrchestrationActivity('root').sessions.hasAssistantTurn, true)
    store.processStates.child.spawn_ancestors = ['other']
    assert.equal(store.getOrchestrationActivity('root').sessions, null)
    store.processStates.child.spawn_ancestors = ['root']
    assert.equal(store.getOrchestrationActivity('root').sessions.processCount, 1)
    delete store.processStates.child
    assert.equal(store.getOrchestrationActivity('root').sessions, null)
})

test('pending requests and subagent activity replace the summary', () => {
    const store = fixture(), before = store.getOrchestrationActivity('root')
    store.processStates.child.pending_requests = [{ id: 'request' }]
    const pending = store.getOrchestrationActivity('root')
    assert.notStrictEqual(pending, before)
    assert.equal(pending.sessions.pendingRequestCount, 1)
    store.runningAgent = true
    assert.equal(store.getOrchestrationActivity('root').subagentsRunning, true)
})
