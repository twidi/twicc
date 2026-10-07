import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, effectScope, watch, nextTick } from 'vue'
import { PROCESS_STATE } from '../constants.js'
import { backgroundWorkStatusKey } from '../utils/backgroundWork.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import { getProjectActivityIndex } from '../utils/projectProcessActivity.js'
import { useRailActiveSessions } from '../composables/useRailActiveSessions.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const start = source.indexOf('        _patchProcessState(') >= 0
    ? source.indexOf('        _patchProcessState(') : source.indexOf('        setSessionStopping(')
const end = source.indexOf('        // ── Streaming blocks', start)
const syntheticStart = source.indexOf('        setSyntheticProcessState(')
const syntheticEnd = source.indexOf('        /**', syntheticStart)
const deps = { PROCESS_STATE, backgroundWorkStatusKey, jsonValuesEqual,
    destroyAllBuffers() {}, sweepPendingRequestDrafts: () => Promise.resolve(), liveDraftKey: (id, key) => `${id}:${key}` }
const actions = new Function(...Object.keys(deps), `return { ${source.slice(start, end)} ${source.slice(syntheticStart, syntheticEnd)} }`)(...Object.values(deps))
const useSnapshots = defineStore('process-snapshot-tests', {
    state: () => ({ processStates: {}, sessions: {}, sessionItems: {}, recomputes: [],
        localState: { streamingBlocks: {}, sessionVisualItems: {}, agentRunStates: {} } }),
    actions: { ...actions, recomputeVisualItems(id) { this.recomputes.push(id) },
        _dropProcessState(id) { delete this.processStates[id] }, _dropOrphanedStreamingBlocks() {},
        getSessionProvider: () => 'claude_code', setSessionArchived(_, id, value) { this.sessions[id].archived = value },
        applyAgentRunState() {},
    },
})
const fixture = () => useSnapshots(createPinia())
const extra = { provider: 'claude_code', started_at: 100, state_changed_at: 110, memory: 1000,
    pending_requests: [{ request_id: 'r', tool_input: { value: 1 } }],
    active_crons: [{ id: 'cron', next_fire: 200 }], extra: { mode: 'hybrid' },
    spawn_ancestors: ['root'], background_work_in_progress: { shells: 1 } }
const copy = value => structuredClone(value)

test('equivalent process snapshots retain the record and all equivalent nested values', () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    const before = s.processStates.s, requests = before.pending_requests, crons = before.active_crons
    const work = before.background_work_in_progress, bag = before.extra, ancestors = before.spawn_ancestors
    s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    assert.strictEqual(s.processStates.s, before)
    assert.strictEqual(before.pending_requests, requests); assert.strictEqual(before.active_crons, crons)
    assert.strictEqual(before.background_work_in_progress, work); assert.strictEqual(before.extra, bag)
    assert.strictEqual(before.spawn_ancestors, ancestors)
})

test('memory-only updates do not invalidate activity or process ordering computations', async () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    let calls = 0
    const activity = computed(() => { calls++; return [s.processStates.s.state, s.processStates.s.started_at, s.processStates.s.pending_requests.length] })
    const before = activity.value; const stop = watch(activity, () => {})
    s.setProcessState('s', 'p', 'assistant_turn', { ...copy(extra), memory: 2000 })
    await nextTick(); assert.strictEqual(activity.value, before); assert.equal(calls, 1)
    assert.equal(s.processStates.s.memory, 2000); stop()
})

test('snapshots preserve current tools during the same turn and clear them on a transition', () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    s.processStates.s.tools = [{ id: 'tool' }]; s.processStates.s.lastStartedToolId = 'tool'
    const tools = s.processStates.s.tools
    s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    assert.strictEqual(s.processStates.s.tools, tools); assert.equal(s.processStates.s.lastStartedToolId, 'tool')
    s.setProcessState('s', 'p', 'user_turn', copy(extra))
    assert.deepEqual(s.processStates.s.tools, []); assert.equal(s.processStates.s.lastStartedToolId, null)
})

test('a restarted process or turn does not retain tools from its previous lifetime', () => {
    for (const field of ['started_at', 'state_changed_at']) {
        const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
        s.processStates.s.tools = [{ id: 'old-tool' }]; s.processStates.s.lastStartedToolId = 'old-tool'
        s.setProcessState('s', 'p', 'assistant_turn', { ...copy(extra), [field]: 200 })
        assert.deepEqual(s.processStates.s.tools, [], field)
        assert.equal(s.processStates.s.lastStartedToolId, null, field)
    }
})

test('explicit tool snapshots replace the current tools even during the same turn', () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    s.processStates.s.tools = [{ id: 'old-tool' }]; s.processStates.s.lastStartedToolId = 'old-tool'
    s.setProcessState('s', 'p', 'assistant_turn', { ...copy(extra), active_tools: [], last_started_tool_id: null })
    assert.deepEqual(s.processStates.s.tools, [])
    assert.equal(s.processStates.s.lastStartedToolId, null)
})

test('changed pending request details and removed snapshot values reach their consumers', () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    const before = s.processStates.s
    const changed = copy(extra); changed.pending_requests[0].tool_input.value = 2
    s.setProcessState('s', 'p', 'assistant_turn', changed)
    assert.equal(before.pending_requests[0].tool_input.value, 2)
    s.setProcessState('s', 'p', 'assistant_turn', { provider: 'claude_code' })
    assert.equal(before.memory, null); assert.equal(before.extra, null)
    assert.deepEqual(before.pending_requests, []); assert.equal(before.active_crons, null)
})

test('stopping and synthetic refreshes retain record identity', () => {
    const s = fixture(); s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    const before = s.processStates.s; s.setSessionStopping('s')
    assert.strictEqual(s.processStates.s, before); assert.equal(before.stopping, true)
    s.setProcessState('s', 'p', 'assistant_turn', copy(extra)); assert.equal(before.stopping, true)
    s.setSyntheticProcessState('child', 's', 'p', 120)
    const child = s.processStates.child; s.setSyntheticProcessState('child', 's', 'p', 120)
    assert.strictEqual(s.processStates.child, child)
    s.setProcessState('child', 'p', 'assistant_turn', copy(extra))
    assert.strictEqual(s.processStates.child, child); assert.equal(child.synthetic, undefined)
})

test('reconnect reconciles records without replacing the map and removes absent processes', () => {
    const s = fixture(); const snapshot = { session_id: 's', project_id: 'p', state: 'assistant_turn', ...copy(extra) }
    s.localState.sessionVisualItems.s = []
    s.setActiveProcesses([snapshot]); const map = s.processStates, before = map.s, requests = before.pending_requests
    s.setProcessState('gone', 'p', 'user_turn')
    s.setActiveProcesses([copy(snapshot)])
    assert.strictEqual(s.processStates, map); assert.strictEqual(s.processStates.s, before)
    assert.strictEqual(before.pending_requests, requests); assert.equal(s.processStates.gone, undefined)
    assert.deepEqual(s.localState.streamingBlocks, {}); assert.ok(s.recomputes.includes('s'))
})

test('real state changes still recompute rows, unarchive starts, and remove dead processes', () => {
    const s = fixture(); s.sessions.s = { archived: true }
    s.setProcessState('s', 'p', 'starting', copy(extra)); assert.equal(s.sessions.s.archived, false)
    s.recomputes = []; s.setProcessState('s', 'p', 'assistant_turn', copy(extra))
    assert.deepEqual(s.recomputes, ['s'])
    s.setProcessState('s', 'p', 'dead'); assert.equal(s.processStates.s, undefined)
})

test('Claude and Codex updates preserve rail and aggregate subscribers while genuine changes remain visible', async t => {
    const s = fixture(), scope = effectScope()
    t.after(() => scope.stop())
    for (const [id, provider] of [['claude', 'claude_code'], ['codex', 'codex']]) {
        s.sessions[id] = { id, project_id: 'p', provider, title: id }
        s.sessionItems[id] = []; s.localState.sessionVisualItems[id] = []
        s.setProcessState(id, 'p', 'assistant_turn', { ...copy(extra), provider })
    }
    s.setSyntheticProcessState('child', 'claude', 'p', 120)
    const { rows, summary } = scope.run(() => ({
        rows: useRailActiveSessions(s).rows,
        summary: computed(previous => getProjectActivityIndex(s).summary(['p'], previous)),
    }))
    const beforeRows = rows.value, beforeSummary = summary.value
    let updates = 0
    scope.run(() => watch([rows, summary], () => updates++))
    for (const [id, provider] of [['claude', 'claude_code'], ['codex', 'codex']]) {
        const snapshot = { ...copy(extra), provider, memory: 2000 }
        snapshot.pending_requests[0].tool_input.value = 2
        s.setProcessState(id, 'p', 'assistant_turn', snapshot)
    }
    await nextTick()
    assert.strictEqual(rows.value, beforeRows); assert.strictEqual(summary.value, beforeSummary)
    assert.equal(updates, 0); assert.equal(summary.value.processCount, 2)
    assert.deepEqual(rows.value.map(row => row.session.id).sort(), ['claude', 'codex'])
    assert.equal(s.processStates.codex.pending_requests[0].tool_input.value, 2)
    s.processStates.claude.label = 'Waiting for subagent'
    await nextTick(); assert.equal(updates, 0)
    s.setSessionStopping('codex'); assert.equal(s.processStates.codex.stopping, true)
    s.setProcessState('claude', 'p', 'user_turn', { ...copy(extra), pending_requests: [] })
    assert.equal(summary.value.pendingRequestCount, 1)
    assert.equal(rows.value.find(row => row.session.id === 'claude').userTurnBackgroundShells, 1)
    assert.ok(s.recomputes.includes('claude'))
    s.setProcessState('codex', 'p', 'dead')
    assert.equal(summary.value.hasAssistantTurn, false)
    s.sessions.claude.last_new_content_at = '2026-10-07T10:00:00Z'
    assert.equal(summary.value.unreadCount, 1)
    assert.equal(rows.value[0].hasUnread, true)
    const process = s.processStates.claude, map = s.processStates
    s.setActiveProcesses([{ session_id: 'claude', project_id: 'p', state: 'user_turn',
        ...copy(extra), provider: 'claude_code', pending_requests: [] }])
    assert.strictEqual(s.processStates.claude, process); assert.strictEqual(s.processStates, map)
    assert.equal(summary.value.unreadCount, 1)
    assert.equal(rows.value[0].session.id, 'claude')
})
