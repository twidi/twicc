import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, nextTick, watch } from 'vue'
import { descendantActiveProcessStates, hasRunningAgent } from '../utils/orchestrationActivity.js'
import { agentLinkState, setAgentLink, buildAgentTree } from '../utils/agentLinkIndex.js'
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


const runningStart = source.indexOf('        hasRunningSubagent:')
const runningEnd = source.indexOf('        /**', runningStart)
const runningGetters = new Function('buildAgentTree', 'hasRunningAgent',
    `return { ${source.slice(runningStart, runningEnd)} }`)(buildAgentTree, hasRunningAgent)
const useRunning = defineStore('running-orchestration-test', {
    state: () => ({ localState: agentLinkState(), processStates: {} }), getters: runningGetters,
})

test('running subagent activity includes nested and unanchored agents but excludes other roots and real processes', () => {
    const store = useRunning(createPinia())
    const add = (id, owner, root = 'root') => setAgentLink(store.localState, owner, `spawn-${id}`, {
        agentId: id, rootSessionId: root, startedAt: '2026-10-07T10:00:00Z',
    })
    add('parent', 'root'); add('nested', 'parent'); add('detached', 'missing')
    add('cycle-a', 'cycle-b'); add('cycle-b', 'cycle-a'); add('other', 'root', 'elsewhere'); add('root', 'root')
    assert.equal(store.hasRunningSubagent('root'), false)
    for (const id of ['nested', 'detached', 'cycle-a']) {
        store.processStates[id] = { synthetic: true }
        assert.equal(store.hasRunningSubagent('root'), true)
        delete store.processStates[id]
    }
    store.processStates.other = { synthetic: true }
    store.processStates.root = { synthetic: true }
    store.processStates.parent = { synthetic: false }
    assert.equal(store.hasRunningSubagent('root'), false)
})

test('subagent activity does not depend on tree order or timestamps', () => {
    const store = useRunning(createPinia())
    for (const id of ['a', 'b']) setAgentLink(store.localState, 'root', `spawn-${id}`, {
        agentId: id, rootSessionId: 'root', startedAt: '2026-10-07T10:00:00Z',
    })
    let reads = 0
    const active = computed(() => { reads++; return store.hasRunningSubagent('root') })
    assert.equal(active.value, false)
    store.localState.agentLinkIndex.a.startedAt = '2026-10-07T10:00:01Z'
    assert.equal(active.value, false)
    assert.equal(reads, 1)
    store.processStates.b = { synthetic: true }
    assert.equal(active.value, true)
})
