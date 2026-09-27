import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, effectScope, reactive } from 'vue'

import {
    controlCardAgentName,
    needsMoreRows,
    ownRunOpen,
    pendingCall,
    spawnAwaitingRun,
    spawnPending,
    startedBeforeCutoff,
    treeRootId,
} from './agentCardState.js'
import { effectiveAgentRun, runKey } from './agentLinkIndex.js'
import { getSessionCutoffMs } from './sessions.js'
import { genericCardPredicate, useToolResultFetch } from '../composables/useToolResultFetch.js'

// Design §8.3 (agent cards) and the §9 cases the Task 16 brief lists.

const T = minute => `2026-09-27T10:${String(minute).padStart(2, '0')}:00Z`
const MS = minute => Date.parse(T(minute))

// ---------------------------------------------------------------------------
// Pure rules
// ---------------------------------------------------------------------------

test('startedBeforeCutoff: a null start is before any cutoff > 0; a 0 cutoff applies none', () => {
    assert.equal(startedBeforeCutoff(T(1), 0), false)
    assert.equal(startedBeforeCutoff(null, 0), false)
    assert.equal(startedBeforeCutoff(null, MS(5)), true)
    assert.equal(startedBeforeCutoff(T(4), MS(5)), true)
    assert.equal(startedBeforeCutoff(T(5), MS(5)), false)
    assert.equal(startedBeforeCutoff(T(6), MS(5)), false)
})

test('treeRootId: run state root, then the card session link root, then the shared session, then the card root', () => {
    const all = { runStateRoot: 'r1', linkRoot: 'r2', sharedSessionId: 'r3', cardRootId: 'r4' }
    assert.equal(treeRootId(all), 'r1')
    assert.equal(treeRootId({ ...all, runStateRoot: null }), 'r2')
    assert.equal(treeRootId({ ...all, runStateRoot: undefined, linkRoot: undefined }), 'r3')
    assert.equal(treeRootId({ ...all, runStateRoot: null, linkRoot: null, sharedSessionId: null }), 'r4')
})

test('ownRunOpen: an open run of a run call, not started before the cutoff, with run states and not frozen', () => {
    const runState = { runs: { [runKey('owner', 'call')]: { open: true, startedAt: T(6) } } }
    const base = { runState, ownerSessionId: 'owner', toolUseId: 'call', isRunCall: true, cutoffMs: MS(5),
        runStatesAvailable: true, transcriptFrozen: false }
    assert.equal(ownRunOpen(base), true)
    assert.equal(ownRunOpen({ ...base, isRunCall: false }), false, 'a call that is not a run')
    assert.equal(ownRunOpen({ ...base, runStatesAvailable: false }), false)
    assert.equal(ownRunOpen({ ...base, transcriptFrozen: true }), false)
    assert.equal(ownRunOpen({ ...base, cutoffMs: MS(7) }), false, 'started before the root cutoff')
    assert.equal(ownRunOpen({ ...base, toolUseId: 'other' }), false, 'no entry')
    assert.equal(ownRunOpen({ ...base, runState: null }), false, 'no run state')
    const nullStart = { runs: { [runKey('owner', 'call')]: { open: true, startedAt: null } } }
    assert.equal(ownRunOpen({ ...base, runState: nullStart }), false, 'a null start is before a cutoff > 0')
    assert.equal(ownRunOpen({ ...base, runState: nullStart, cutoffMs: 0 }), true)
    const closed = { runs: { [runKey('owner', 'call')]: { open: false, startedAt: T(6) } } }
    assert.equal(ownRunOpen({ ...base, runState: closed }), false)
})

test('pendingCall: no result, no error, not frozen, not before the tree cutoff, and the owner gate', () => {
    const base = { count: 0, callError: null, transcriptFrozen: false, callAt: T(3), treeCutoffMs: MS(2),
        ownerIsSubagent: false, runStatesAvailable: true, ownerRunState: null, ownerRunning: false }
    assert.equal(pendingCall(base), true, 'a root-owned call has no owner gate')
    assert.equal(pendingCall({ ...base, count: 1 }), false)
    assert.equal(pendingCall({ ...base, callError: 'boom' }), false)
    assert.equal(pendingCall({ ...base, transcriptFrozen: true }), false)
    assert.equal(pendingCall({ ...base, treeCutoffMs: MS(4) }), false, 'the call predates the root cutoff')
    assert.equal(pendingCall({ ...base, treeCutoffMs: MS(4), callAt: null }), true, 'a null call time is never before')
    assert.equal(pendingCall({ ...base, treeCutoffMs: 0 }), true, 'a 0 cutoff passes')
})

test('pendingCall owner gate: run states unavailable, owner entry, no entry', () => {
    const owner = { running: true, runs: { a: { open: true, startedAt: T(2) } } }
    const base = { count: 0, callError: null, transcriptFrozen: false, callAt: T(3), treeCutoffMs: 0,
        ownerIsSubagent: true, runStatesAvailable: true, ownerRunState: owner, ownerRunning: true }
    assert.equal(pendingCall(base), true)
    assert.equal(pendingCall({ ...base, runStatesAvailable: false }), false, 'with an entry')
    assert.equal(pendingCall({ ...base, runStatesAvailable: false, ownerRunState: null }), false, 'without an entry')
    assert.equal(pendingCall({ ...base, ownerRunState: null, ownerRunning: false }), true, 'no entry skips the gate')
    assert.equal(pendingCall({ ...base, ownerRunning: false }), false, 'the owner stopped')
    const later = { runs: { a: { open: true, startedAt: T(4) } } }
    assert.equal(pendingCall({ ...base, ownerRunState: later }), false, 'the call predates the owner\'s open run')
    const closed = { runs: { a: { open: false, startedAt: T(2) } } }
    assert.equal(pendingCall({ ...base, ownerRunState: closed }), false, 'no open run')
    const two = { runs: { a: { open: true, startedAt: T(1) }, b: { open: true, startedAt: T(5) } } }
    assert.equal(pendingCall({ ...base, ownerRunState: two }), true, 'made during the older of two open runs')
    const nullStart = { runs: { a: { open: true, startedAt: null } } }
    assert.equal(pendingCall({ ...base, ownerRunState: nullStart }), true, 'a null run start passes')
    assert.equal(pendingCall({ ...base, ownerRunState: later, callAt: null }), true,
        'a null call time passes the owner-run comparison')
    assert.equal(pendingCall({ ...base, callAt: T(2) }), true, 'a call at the run start passes')
})

test('spawnAwaitingRun: an acknowledged, running spawn with no own run entry yet', () => {
    const base = { isTask: true, count: 1, hasOwnRunEntry: false, callError: null, runStatesAvailable: true,
        transcriptFrozen: false, callAt: T(3), treeCutoffMs: MS(2), helperRunning: true }
    assert.equal(spawnAwaitingRun(base), true)
    assert.equal(spawnAwaitingRun({ ...base, isTask: false }), false)
    assert.equal(spawnAwaitingRun({ ...base, count: 0 }), false)
    assert.equal(spawnAwaitingRun({ ...base, hasOwnRunEntry: true }), false)
    assert.equal(spawnAwaitingRun({ ...base, callError: 'Unknown error' }), false)
    assert.equal(spawnAwaitingRun({ ...base, runStatesAvailable: false }), false)
    assert.equal(spawnAwaitingRun({ ...base, transcriptFrozen: true }), false)
    assert.equal(spawnAwaitingRun({ ...base, treeCutoffMs: MS(4) }), false)
    assert.equal(spawnAwaitingRun({ ...base, helperRunning: false }), false)
})

test('spawnPending: no link, helper running, not before the cutoff; count 0 → pendingCall, else no error', () => {
    const base = { isTask: true, agentId: null, transcriptFrozen: false, callAt: T(3), treeCutoffMs: MS(2),
        helperRunning: true, count: 0, pendingCall: true, callError: null }
    assert.equal(spawnPending(base), true)
    assert.equal(spawnPending({ ...base, pendingCall: false }), false)
    assert.equal(spawnPending({ ...base, count: 1, pendingCall: false }), true)
    assert.equal(spawnPending({ ...base, count: 1, callError: 'Unknown error' }), false)
    assert.equal(spawnPending({ ...base, agentId: 'a' }), false)
    assert.equal(spawnPending({ ...base, isTask: false }), false)
    assert.equal(spawnPending({ ...base, transcriptFrozen: true }), false)
    assert.equal(spawnPending({ ...base, treeCutoffMs: MS(4) }), false)
    assert.equal(spawnPending({ ...base, helperRunning: false }), false)
})

test('needsMoreRows: count change, open own run short of rows (count > 0), pending call, awaiting run', () => {
    const base = { countChanged: false, count: 1, ownRunOpen: false, rowCount: 1, expectedCount: 2,
        pendingCall: false, spawnAwaitingRun: false }
    assert.equal(needsMoreRows(base), false)
    assert.equal(needsMoreRows({ ...base, countChanged: true }), true)
    assert.equal(needsMoreRows({ ...base, ownRunOpen: true }), true)
    assert.equal(needsMoreRows({ ...base, ownRunOpen: true, rowCount: 2 }), false)
    assert.equal(needsMoreRows({ ...base, ownRunOpen: true, count: 0, rowCount: 0 }), false,
        'with count 0 only pendingCall drives the card')
    assert.equal(needsMoreRows({ ...base, pendingCall: true }), true)
    assert.equal(needsMoreRows({ ...base, spawnAwaitingRun: true }), true)
})

test('controlCardAgentName: the agent display name, or Agent "<short id>"', () => {
    const store = {
        getAgentLinkInfo: id => ({ named: { displayName: 'Review the diff', ownerSessionId: 'root' } })[id] || null,
        getSession: () => null,
    }
    assert.equal(controlCardAgentName('named', store), 'Review the diff')
    assert.equal(controlCardAgentName('a1b2c3d4e5f6a7b8', store), 'Agent "a1b2c3d4"')
})

// ---------------------------------------------------------------------------
// The card's own glue (sliced from ToolUseContent.vue) composed with the
// result fetch pipeline, on a fake store, clock and fetch.
// ---------------------------------------------------------------------------

const source = readFileSync(new URL('../components/session/detail/items/ToolUseContent.vue', import.meta.url), 'utf8')
const START = '// --- Agent card state'
const END = '// --- End of agent card state'
assert.ok(source.includes(START) && source.includes(END), 'the card marks its agent card state block')
const cardCode = source.slice(source.indexOf(START), source.indexOf(END))
const CARD_PARAMS = [
    'computed', 'props', 'dataStore', 'toolHelpers', 'helperOptions', 'isTask', 'toolState', 'transcriptFrozen',
    'sharedSessionId', 'rootSessionId', 'isStaleToolUse', 'requiredDisplayCount', 'fetchToolResult',
    'providerHelpers', 'getSessionCutoffMs', 'genericCardPredicate', 'runKey', 'treeRootId', 'ownRunOpen',
    'pendingCall', 'spawnAwaitingRun', 'spawnPending', 'needsMoreRows', 'controlCardAgentName',
]
const buildCardGlue = new Function(...CARD_PARAMS, `${cardCode}
return { agentId, isAgentCard, isControlCard, treeCutoffMs, isOwnRunOpen, isCallPending, isSpawnAwaitingRun,
    resultFetchPredicate, isToolRunning, isAgentRunning, isAgentSpawnPending, agentRunStartedAt, showStopAgent,
    controlAgentName }`)

function flush() {
    return new Promise(resolve => setImmediate(resolve))
}

// Fake clock: intervals fire in time order on `advance` (Task 15's harness).
function fakeClock() {
    let now = 0
    let nextId = 1
    const intervals = new Map()
    return {
        setInterval(fn, ms) {
            const id = nextId++
            intervals.set(id, { fn, ms, next: now + ms })
            return id
        },
        clearInterval(id) { intervals.delete(id) },
        now: () => now,
        liveIntervals: () => intervals.size,
        async advance(ms) {
            const target = now + ms
            for (;;) {
                let due = null
                for (const entry of intervals.values()) {
                    if (entry.next <= target && (!due || entry.next < due.next)) due = entry
                }
                if (!due) break
                now = due.next
                due.next += due.ms
                due.fn()
                await flush()
            }
            now = target
            await flush()
        },
    }
}

// A fake store with the getters the card reads (the app store and the share
// shim expose the same ones). `isAgentRunning` applies the run state and its
// root's cutoff, as `applyAgentRunState` (app) and the shim both do.
function world({ runStatesAvailable = true } = {}) {
    const w = reactive({
        sessions: { root: {} },
        links: {},        // owner -> toolId -> link
        linkIndex: {},    // agentId -> link
        runStates: {},    // agentId -> run state
        interactions: {}, // owner -> toolId -> interaction
        toolStates: {},   // owner -> toolId -> tool state
        runStatesAvailable,
        frozen: false,
    })
    const dataStore = {
        getSession: id => w.sessions[id] || null,
        getAgentLink: (owner, toolId) => w.links[owner]?.[toolId],
        getAgentLinkInfo: id => w.linkIndex[id] || null,
        getAgentRunState: id => w.runStates[id] || null,
        getAgentInteraction: (owner, toolId) => w.interactions[owner]?.[toolId] || null,
        isAgentRunning: (id) => {
            const entry = w.runStates[id]
            return !!(w.runStatesAvailable && entry
                && effectiveAgentRun(entry, getSessionCutoffMs(w.sessions[entry.rootSessionId])).running)
        },
        get runStatesAvailable() { return w.runStatesAvailable },
    }
    return { w, dataStore }
}

function restartRoot(w, minute, root = 'root') {
    w.sessions[root] = { ...w.sessions[root], last_started_at: T(minute) }
}
function link(w, { owner = 'root', toolId = 'call', agentId = 'agent', root = 'root', displayName = null } = {}) {
    const entry = { agentId, ownerSessionId: owner, toolUseId: toolId, rootSessionId: root, displayName }
    ;(w.links[owner] ||= {})[toolId] = entry
    w.linkIndex[agentId] = entry
}
function runState(w, agentId, { root = 'root', running = true, runStartedAt = null, runBackground = true, runs = {} } = {}) {
    w.runStates[agentId] = { running, runStartedAt, runBackground, rootSessionId: root, runs }
}
function interaction(w, { owner = 'root', toolId = 'call', agentId = 'agent', opensRun = false, root = 'root' } = {}) {
    ;(w.interactions[owner] ||= {})[toolId] = { agentId, rootSessionId: root, kind: 'send_message', opensRun,
        ownerSessionId: owner, toolUseId: toolId }
}
function toolState(w, { owner = 'root', toolId = 'call', count, error = null }) {
    ;(w.toolStates[owner] ||= {})[toolId] = { resultCount: count, error }
}

// Mount one card: its glue (sliced) plus the pipeline. `h` is the provider
// helper the card consults (spawn tool or not, running, expected / display count).
async function mountCard(env, {
    sessionId = 'root', parentSessionId = null, toolId = 'call', timestamp = T(3),
    isTask = false, helperRunning = false, expected = 1, display = 1,
    open = true, active = true, sharedSessionId = null, fetchToolResult = null, clock = fakeClock(),
} = {}) {
    const { w, dataStore } = env
    const props = reactive({ name: isTask ? 'Agent' : 'SendMessage', input: {}, sessionId, parentSessionId, toolId, timestamp })
    const h = reactive({ isTask, running: helperRunning, expected, display })
    const ui = reactive({ open, active })
    const calls = []
    const scope = effectScope()
    const { card, api } = scope.run(() => {
        const toolStateRef = computed(() => w.toolStates[props.sessionId]?.[props.toolId] || null)
        const card = buildCardGlue(
            computed, props, dataStore,
            { value: { isToolRunning: () => h.running, getExpectedResultCount: () => h.expected } },
            { value: {} },
            computed(() => h.isTask),
            toolStateRef,
            computed(() => w.frozen),
            sharedSessionId,
            computed(() => props.parentSessionId || props.sessionId),
            { value: false },
            computed(() => h.display),
            fetchToolResult,
            { value: { canStopSubagent: () => true } },
            getSessionCutoffMs, genericCardPredicate, runKey, treeRootId, ownRunOpen,
            pendingCall, spawnAwaitingRun, spawnPending, needsMoreRows, controlCardAgentName,
        )
        const api = useToolResultFetch({
            fetchRows: () => new Promise((resolve, reject) => { calls.push({ resolve, reject }) }),
            open: () => ui.open,
            active: () => ui.active,
            count: () => toolStateRef.value?.resultCount ?? 0,
            displayCount: () => h.display,
            predicate: p => card.resultFetchPredicate(p),
            transcriptFrozen: () => w.frozen,
            connectionEpoch: () => 0,
            timers: clock,
        })
        api.start()
        return { card, api }
    })
    await flush()
    return { card, api, calls, clock, h, ui, props, unmount: () => scope.stop() }
}

// Rows of `n` results.
const rows = n => Array.from({ length: n }, (_, i) => ({ row: i + 1 }))

function showsNoResultAvailable(m) {
    const held = m.api.resultData.value?.length ?? 0
    return m.api.resultState.value === 'loaded' && held < Math.max(1, m.h.display) && !m.api.showsPending.value
}
function showsRows(m) {
    const held = m.api.resultData.value?.length ?? 0
    return m.api.resultState.value === 'loaded' && held >= Math.max(1, m.h.display)
}

// A control card with a run-opening interaction (expected 2, display 1).
function controlSetup(env, { runOpen = true, runStart = T(2), count = 1, owner = 'root', toolId = 'call' } = {}) {
    interaction(env.w, { owner, toolId, opensRun: true })
    runState(env.w, 'agent', { running: runOpen, runStartedAt: runStart,
        runs: { [runKey(owner, toolId)]: { open: runOpen, startedAt: runStart } } })
    toolState(env.w, { owner, toolId, count })
}
// A spawn card with its link (expected 2, display 2: Codex v2 ack + FINAL_ANSWER).
function spawnSetup(env, { runOpen = true, runStart = T(2), count = 1, owner = 'root', toolId = 'call', parent = null } = {}) {
    link(env.w, { owner, toolId })
    runState(env.w, 'agent', { running: runOpen, runStartedAt: runStart,
        runs: { [runKey(owner, toolId)]: { open: runOpen, startedAt: runStart } } })
    toolState(env.w, { owner, toolId, count })
    return { sessionId: owner, parentSessionId: parent, isTask: true, helperRunning: true, expected: 2, display: 2 }
}

test('§9 Own run and cutoff: an open run started before the root cutoff closes on the card, no new snapshot', async () => {
    // Control card: its row shows.
    const env = world()
    controlSetup(env)
    const control = await mountCard(env, { expected: 2, display: 1 })
    control.calls[0].resolve(rows(1))
    await flush()
    assert.equal(control.card.isOwnRunOpen.value, true)
    assert.equal(control.api.showsPending.value, true, 'the open run short of its rows polls')
    restartRoot(env.w, 5)
    await flush()
    assert.equal(control.card.isOwnRunOpen.value, false)
    assert.equal(control.api.showsPending.value, false)
    assert.equal(control.clock.liveIntervals(), 0)
    assert.ok(showsRows(control), 'the control card shows its rows')

    // Spawn card below its display count: "No result available".
    const env2 = world()
    const opts = spawnSetup(env2)
    restartRoot(env2.w, 5)
    const spawn = await mountCard(env2, opts)
    spawn.calls[0].resolve(rows(1))
    await flush()
    assert.equal(spawn.card.isOwnRunOpen.value, false)
    assert.equal(spawn.clock.liveIntervals(), 0)
    assert.ok(showsNoResultAvailable(spawn))
    await spawn.clock.advance(30000)
    assert.equal(spawn.calls.length, 1)
})

test('§9 Frozen share snapshot with a run open at the freeze: no own run, no polling', async () => {
    const env = world()
    env.w.frozen = true
    controlSetup(env)
    const control = await mountCard(env, { expected: 2, display: 1 })
    control.calls[0].resolve(rows(1))
    await flush()
    assert.equal(control.card.isOwnRunOpen.value, false)
    assert.equal(control.card.isAgentRunning.value, false)
    await control.clock.advance(30000)
    assert.equal(control.calls.length, 1)
    assert.ok(showsRows(control))

    const env2 = world()
    env2.w.frozen = true
    const spawn = await mountCard(env2, spawnSetup(env2))
    spawn.calls[0].resolve(rows(1))
    await flush()
    assert.equal(spawn.card.isOwnRunOpen.value, false)
    await spawn.clock.advance(30000)
    assert.equal(spawn.calls.length, 1)
    assert.ok(showsNoResultAvailable(spawn))
})

test('§9 Share viewer: the own-run cutoff comes from the agent\'s root, not the drawer\'s rootSessionId', async () => {
    // Share drawer: the card's session and parent are the agent `a1`; its nested spawn targets `agent`.
    const env = world()
    env.w.sessions.a1 = {}
    link(env.w, { owner: 'root', toolId: 'spawn-a1', agentId: 'a1' })
    const opts = spawnSetup(env, { owner: 'a1', parent: 'a1' })
    const card = await mountCard(env, { ...opts, sharedSessionId: 'root' })
    assert.equal(card.card.isOwnRunOpen.value, true)
    restartRoot(env.w, 5)
    await flush()
    assert.equal(card.card.treeCutoffMs.value, MS(5), 'the root\'s cutoff, not the drawer agent\'s (0)')
    assert.equal(card.card.isOwnRunOpen.value, false)

    // The run state's root comes first: with no other source of the root
    // (no link for the drawer agent, no shared session), it still applies.
    delete env.w.linkIndex.a1
    const bare = await mountCard(env, opts)
    assert.equal(bare.card.treeCutoffMs.value, MS(5))
    assert.equal(bare.card.isOwnRunOpen.value, false)
})

test('§9 Share viewer: a drawer opened from #agent= before the snapshot uses the shared session\'s cutoff', async () => {
    // No run state, no link yet: the only root known is the shared session.
    const env = world()
    env.w.sessions.a1 = {}
    restartRoot(env.w, 5)
    interaction(env.w, { owner: 'a1' })
    toolState(env.w, { owner: 'a1', count: 0 })
    const card = await mountCard(env, { sessionId: 'a1', parentSessionId: 'a1', sharedSessionId: 'root' })
    assert.equal(card.card.treeCutoffMs.value, MS(5))
    assert.equal(card.card.isCallPending.value, false, 'the call predates the root restart')
    assert.equal(card.card.isToolRunning.value, false)
    card.calls[0].resolve([])
    await flush()
    assert.equal(card.clock.liveIntervals(), 0)
})

test('§9 treeCutoff fallback in the app: a subagent card with no link info uses the root', async () => {
    const env = world()
    env.w.sessions.a1 = {}
    restartRoot(env.w, 5)
    toolState(env.w, { owner: 'a1', count: 0 })
    const card = await mountCard(env, { sessionId: 'a1', parentSessionId: 'root', isTask: true, helperRunning: true,
        expected: 2, display: 2 })
    assert.equal(card.card.treeCutoffMs.value, MS(5))
    assert.equal(card.card.isAgentSpawnPending.value, false, 'a pending nested spawn from before the restart stops')
    card.calls[0].resolve([])
    await flush()
    assert.equal(card.clock.liveIntervals(), 0)
    assert.ok(showsNoResultAvailable(card))
})

test('§9 Share drawer: a pending agent call and a pending nested spawn from before a root restart stop', async () => {
    const env = world()
    env.w.sessions.a1 = {}
    link(env.w, { owner: 'root', toolId: 'spawn-a1', agentId: 'a1' })
    interaction(env.w, { owner: 'a1', toolId: 'send' })
    toolState(env.w, { owner: 'a1', toolId: 'send', count: 0 })
    toolState(env.w, { owner: 'a1', toolId: 'nested', count: 0 })
    const control = await mountCard(env, { sessionId: 'a1', parentSessionId: 'a1', toolId: 'send', sharedSessionId: 'root' })
    const spawn = await mountCard(env, { sessionId: 'a1', parentSessionId: 'a1', toolId: 'nested', sharedSessionId: 'root',
        isTask: true, helperRunning: true, expected: 2, display: 2 })
    assert.equal(control.card.isToolRunning.value, true)
    assert.equal(spawn.card.isAgentSpawnPending.value, true)
    restartRoot(env.w, 5)
    await flush()
    for (const m of [control, spawn]) {
        m.calls[0].resolve([])
        await flush()
        assert.equal(m.api.showsPending.value, false)
        assert.equal(m.clock.liveIntervals(), 0)
    }
    assert.equal(control.card.isToolRunning.value, false)
    assert.equal(spawn.card.isAgentSpawnPending.value, false)
})

test('§9 Pending control card: the generic spinner and "Checking again shortly" show, then stop at the result', async () => {
    const env = world()
    interaction(env.w)
    toolState(env.w, { count: 0 })
    const m = await mountCard(env)
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.card.isAgentCard.value, true)
    assert.equal(m.card.isControlCard.value, true)
    assert.equal(m.card.isToolRunning.value, true)
    assert.equal(m.api.showsPending.value, true)
    toolState(env.w, { count: 1 })
    await flush()
    assert.equal(m.card.isToolRunning.value, false)
    m.calls[1].resolve(rows(1))
    await flush()
    assert.equal(m.api.showsPending.value, false)
    assert.equal(m.clock.liveIntervals(), 0)
    assert.ok(showsRows(m))
})

// A control call made by the subagent `a1` (owner) at minute 3, root cutoff 0.
function subagentOwnedCall(env, ownerRuns, { ownerRunning = true } = {}) {
    env.w.sessions.a1 = {}
    link(env.w, { owner: 'root', toolId: 'spawn-a1', agentId: 'a1' })
    runState(env.w, 'a1', { running: ownerRunning, runStartedAt: T(1), runs: ownerRuns })
    interaction(env.w, { owner: 'a1' })
    toolState(env.w, { owner: 'a1', count: 0 })
}

test('§9 Pending call made during the older of two open runs of its owner: still pending', async () => {
    const env = world()
    subagentOwnedCall(env, { r1: { open: true, startedAt: T(1) }, r2: { open: true, startedAt: T(5) } })
    const m = await mountCard(env, { sessionId: 'a1', parentSessionId: 'root' })
    assert.equal(m.card.isCallPending.value, true)
    assert.equal(m.card.isToolRunning.value, true)
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.api.showsPending.value, true)
})

test('§9 Pending call owned by a subagent then stopped while the root runs on; a later resume does not revive it', async () => {
    const env = world()
    subagentOwnedCall(env, { r1: { open: true, startedAt: T(1) } })
    const m = await mountCard(env, { sessionId: 'a1', parentSessionId: 'root' })
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.api.showsPending.value, true)
    runState(env.w, 'a1', { running: false, runStartedAt: T(1), runs: { r1: { open: false, startedAt: T(1) } } })
    await flush()
    assert.equal(m.card.isToolRunning.value, false, 'the spinner stops')
    assert.equal(m.api.showsPending.value, false, 'with the Result text')
    assert.equal(m.clock.liveIntervals(), 0)
    runState(env.w, 'a1', { running: true, runStartedAt: T(6),
        runs: { r1: { open: false, startedAt: T(1) }, r2: { open: true, startedAt: T(6) } } })
    await flush()
    assert.equal(m.card.isToolRunning.value, false, 'the call predates the new run')
    assert.equal(m.api.showsPending.value, false)
})

test('§9 Owner gate: a pending agent call in a Claude workflow agent tab (no owner entry) still polls', async () => {
    const env = world()
    interaction(env.w, { owner: 'wf:agent1' })
    toolState(env.w, { owner: 'wf:agent1', count: 0 })
    const m = await mountCard(env, { sessionId: 'wf:agent1', parentSessionId: 'root' })
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.card.isCallPending.value, true)
    assert.equal(m.api.showsPending.value, true)
    await m.clock.advance(3000)
    assert.equal(m.calls.length, 2)
})

test('§9 Control card with count 0 and an open own run whose subagent owner stops: both stop together', async () => {
    const env = world()
    subagentOwnedCall(env, { r1: { open: true, startedAt: T(1) } })
    interaction(env.w, { owner: 'a1', opensRun: true })
    runState(env.w, 'agent', { running: true, runStartedAt: T(3), runs: { [runKey('a1', 'call')]: { open: true, startedAt: T(3) } } })
    const m = await mountCard(env, { sessionId: 'a1', parentSessionId: 'root', expected: 2 })
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.card.isOwnRunOpen.value, true)
    assert.equal(m.card.isToolRunning.value, true)
    assert.equal(m.api.showsPending.value, true)
    runState(env.w, 'a1', { running: false, runStartedAt: T(1), runs: { r1: { open: false, startedAt: T(1) } } })
    await flush()
    assert.equal(m.card.isOwnRunOpen.value, true, 'the target run itself is still open')
    assert.equal(m.card.isToolRunning.value, false)
    assert.equal(m.api.showsPending.value, false)
    assert.equal(m.clock.liveIntervals(), 0)
})

test('§9 Link / run state after mount, and a spawn between agent_link_created and agent_run_state: keeps polling', async () => {
    const env = world()
    toolState(env.w, { count: 1 })
    const m = await mountCard(env, { isTask: true, helperRunning: true, expected: 2, display: 2 })
    m.calls[0].resolve(rows(1))
    await flush()
    assert.equal(m.card.isSpawnAwaitingRun.value, true, 'the ack landed before the link')
    assert.equal(m.api.showsPending.value, true)
    assert.ok(!showsNoResultAvailable(m))
    link(env.w)
    await flush()
    assert.equal(m.card.isSpawnAwaitingRun.value, true, 'the link landed, the run state not yet')
    assert.ok(!showsNoResultAvailable(m))
    await m.clock.advance(3000)
    assert.equal(m.calls.length, 2)
    m.calls[1].resolve(rows(1))
    await flush()
    runState(env.w, 'agent', { runStartedAt: T(3), runs: { [runKey('root', 'call')]: { open: true, startedAt: T(3) } } })
    await flush()
    assert.equal(m.card.isSpawnAwaitingRun.value, false)
    assert.equal(m.card.isOwnRunOpen.value, true)
    assert.equal(m.api.showsPending.value, true, 'the open run keeps it polling')
    assert.ok(!showsNoResultAvailable(m))
    assert.equal(m.card.isAgentRunning.value, true)
})

test('§9 Failed spawn: a Claude background Agent whose only result is "Unknown error" does not poll', async () => {
    const env = world()
    toolState(env.w, { count: 1, error: 'Unknown error' })
    // The Claude helper ignores the error for this count: it still reports running.
    const m = await mountCard(env, { isTask: true, helperRunning: true, expected: 2, display: 2 })
    assert.equal(m.card.isSpawnAwaitingRun.value, false)
    assert.equal(m.card.isAgentSpawnPending.value, false)
    m.calls[0].resolve(rows(1))
    await flush()
    assert.equal(m.api.showsPending.value, false)
    assert.equal(m.clock.liveIntervals(), 0)
})

test('§9 Spawn owner gate: a spawn by a subagent stopped before the ack and the link stops', async () => {
    const env = world()
    subagentOwnedCall(env, { r1: { open: true, startedAt: T(1) } })
    delete env.w.interactions.a1
    const m = await mountCard(env, { sessionId: 'a1', parentSessionId: 'root', isTask: true, helperRunning: true,
        expected: 2, display: 2 })
    m.calls[0].resolve([])
    await flush()
    assert.equal(m.card.isAgentSpawnPending.value, true)
    assert.equal(m.api.showsPending.value, true)
    runState(env.w, 'a1', { running: false, runStartedAt: T(1), runs: { r1: { open: false, startedAt: T(1) } } })
    await flush()
    assert.equal(m.card.isAgentSpawnPending.value, false)
    assert.equal(m.api.showsPending.value, false)
    assert.equal(m.clock.liveIntervals(), 0)
})

test('§9 Share include_subagents turned off live (card half): no robot, no run-state polling', async () => {
    // A running spawn with an open run: robot and own-run polling vanish.
    const env = world()
    const spawn = await mountCard(env, spawnSetup(env))
    spawn.calls[0].resolve(rows(1))
    await flush()
    assert.equal(spawn.card.isAgentRunning.value, true)
    assert.equal(spawn.api.showsPending.value, true)
    env.w.runStatesAvailable = false
    await flush()
    assert.equal(spawn.card.isAgentRunning.value, false, 'no robot')
    assert.equal(spawn.card.isOwnRunOpen.value, false)
    assert.equal(spawn.api.showsPending.value, false)

    // An acknowledged spawn with no run entry: no awaiting-run polling.
    const env2 = world({ runStatesAvailable: false })
    toolState(env2.w, { count: 1 })
    const awaiting = await mountCard(env2, { isTask: true, helperRunning: true, expected: 2, display: 2 })
    awaiting.calls[0].resolve(rows(1))
    await flush()
    assert.equal(awaiting.card.isSpawnAwaitingRun.value, false)
    assert.equal(awaiting.clock.liveIntervals(), 0)
    assert.equal(awaiting.card.isAgentSpawnPending.value, true, 'a root-owned spawn with no link keeps today\'s spinner')

    // A root-owned spawn with count 0 spins as today; a subagent-owned one stops.
    const env3 = world({ runStatesAvailable: false })
    toolState(env3.w, { count: 0 })
    toolState(env3.w, { owner: 'a1', count: 0 })
    runState(env3.w, 'a1', { running: true, runs: { r1: { open: true, startedAt: T(1) } } })
    const rootSpawn = await mountCard(env3, { isTask: true, helperRunning: true, expected: 2, display: 2 })
    const childSpawn = await mountCard(env3, { sessionId: 'a1', parentSessionId: 'a1', sharedSessionId: 'root',
        isTask: true, helperRunning: true, expected: 2, display: 2 })
    assert.equal(rootSpawn.card.isAgentSpawnPending.value, true)
    assert.equal(childSpawn.card.isAgentSpawnPending.value, false)

    // An interrupted spawn (run closed) with its section open does not start polling.
    const env4 = world({ runStatesAvailable: false })
    const interrupted = await mountCard(env4, spawnSetup(env4, { runOpen: false }))
    interrupted.calls[0].resolve(rows(1))
    await flush()
    assert.equal(interrupted.clock.liveIntervals(), 0)

    // A stopped subagent's pending call stops — with and without an owner entry.
    for (const withEntry of [true, false]) {
        const env5 = world({ runStatesAvailable: false })
        interaction(env5.w, { owner: 'a1' })
        toolState(env5.w, { owner: 'a1', count: 0 })
        if (withEntry) runState(env5.w, 'a1', { running: true, runs: { r1: { open: true, startedAt: T(1) } } })
        const call = await mountCard(env5, { sessionId: 'a1', parentSessionId: 'a1', sharedSessionId: 'root' })
        call.calls[0].resolve([])
        await flush()
        assert.equal(call.card.isToolRunning.value, false)
        assert.equal(call.api.showsPending.value, false)
    }
})

test('§9 Result fetch: rows after the run closed, control vs spawn short of rows, polling end', async () => {
    // A FINAL_ANSWER row landing after the run closed is still fetched.
    const env = world()
    const spawn = await mountCard(env, spawnSetup(env, { runOpen: false }))
    spawn.calls[0].resolve(rows(1))
    await flush()
    assert.equal(spawn.clock.liveIntervals(), 0, 'the fetch saw the count and the own run is closed')
    assert.ok(showsNoResultAvailable(spawn))
    toolState(env.w, { count: 2 })
    await flush()
    assert.equal(spawn.calls.length, 2, 'the count watcher fetches it')
    spawn.calls[1].resolve(rows(2))
    await flush()
    assert.ok(showsRows(spawn))

    // Own run closed short of its rows while the agent runs a later run.
    const later = { [runKey('root', 'later')]: { open: true, startedAt: T(6) } }
    const env2 = world()
    controlSetup(env2, { runOpen: false })
    env2.w.runStates.agent = { ...env2.w.runStates.agent, running: true, runStartedAt: T(6),
        runs: { ...env2.w.runStates.agent.runs, ...later } }
    const control = await mountCard(env2, { expected: 2, display: 1 })
    control.calls[0].resolve(rows(1))
    await flush()
    assert.equal(control.card.isAgentRunning.value, true)
    assert.ok(showsRows(control), 'a control card shows the rows it has')
    assert.equal(control.clock.liveIntervals(), 0)

    const env3 = world()
    const opts = spawnSetup(env3, { runOpen: false })
    env3.w.runStates.agent = { ...env3.w.runStates.agent, running: true, runStartedAt: T(6),
        runs: { ...env3.w.runStates.agent.runs, ...later } }
    const spawn2 = await mountCard(env3, opts)
    spawn2.calls[0].resolve(rows(1))
    await flush()
    assert.ok(showsNoResultAvailable(spawn2), 'a spawn card stops on "No result available"')
    assert.equal(spawn2.clock.liveIntervals(), 0)
})

test('§9 Codex v2 spawn card whose run closed before its FINAL_ANSWER: "No result available", then the answer', async () => {
    const env = world()
    const m = await mountCard(env, spawnSetup(env))
    m.calls[0].resolve(rows(1))
    await flush()
    assert.equal(m.api.showsPending.value, true)
    runState(env.w, 'agent', { running: false, runStartedAt: T(2),
        runs: { [runKey('root', 'call')]: { open: false, startedAt: T(2) } } })
    await flush()
    assert.ok(showsNoResultAvailable(m), 'never the raw ack')
    toolState(env.w, { count: 2 })
    await flush()
    m.calls.at(-1).resolve(rows(2))
    await flush()
    assert.ok(showsRows(m))
})

test('§9 needsMoreRows on every entry point: reopen, remount and reactivation fetch the second row', async () => {
    for (const entry of ['reopen', 'remount', 'reactivate']) {
        const env = world()
        const opts = spawnSetup(env)
        const m = await mountCard(env, opts)
        m.calls[0].resolve(rows(1))
        await flush()
        if (entry === 'reactivate') m.ui.active = false
        else m.ui.open = false
        await flush()
        toolState(env.w, { count: 2 })
        await flush()
        assert.equal(m.calls.length, 1, `${entry}: nothing fetched while not visible`)
        let target = m
        if (entry === 'remount') {
            m.unmount()
            target = await mountCard(env, opts)
        } else if (entry === 'reopen') {
            m.ui.open = true
        } else {
            m.ui.active = true
        }
        await flush()
        const last = target.calls.at(-1)
        assert.ok(last, `${entry}: fetched`)
        last.resolve(rows(2))
        await flush()
        assert.ok(showsRows(target), `${entry}: the second row shows`)
    }
})

test('§9 Reopen / reactivate with 0 displayable rows (agent card polling): pending at once', async () => {
    for (const key of ['open', 'active']) {
        const env = world()
        const m = await mountCard(env, spawnSetup(env))
        m.calls[0].resolve(rows(1))
        await flush()
        m.ui[key] = false
        await flush()
        m.ui[key] = true
        assert.equal(m.api.showsPending.value, true, `${key}: pending before any settle`)
        assert.ok(!showsNoResultAvailable(m))
    }
})

test('§9 Reactivation: a spawn whose run closed while deactivated sends nothing; an idle card fetches', async () => {
    const env = world()
    const m = await mountCard(env, spawnSetup(env))
    m.calls[0].resolve(rows(1))
    await flush()
    m.ui.active = false
    await flush()
    runState(env.w, 'agent', { running: false, runStartedAt: T(2),
        runs: { [runKey('root', 'call')]: { open: false, startedAt: T(2) } } })
    await flush()
    m.ui.active = true
    await flush()
    assert.equal(m.calls.length, 1, 'no request')
    assert.equal(m.clock.liveIntervals(), 0, 'no interval')
    assert.ok(showsNoResultAvailable(m))

    // Deactivated during its first request: 'idle' again, fetches on reactivation.
    const env2 = world()
    const idle = await mountCard(env2, spawnSetup(env2, { runOpen: false }))
    idle.ui.active = false
    await flush()
    assert.equal(idle.api.resultState.value, 'idle')
    idle.ui.active = true
    await flush()
    assert.equal(idle.calls.length, 2)
})

test('§9 Card type switch: a generic card polling when its interaction lands stops once its run closes', async () => {
    const env = world()
    toolState(env.w, { count: 1 })
    // A Codex followup_task still running for its helper, before its `interacted` event.
    const m = await mountCard(env, { helperRunning: true, expected: 2, display: 1 })
    m.calls[0].resolve(rows(1))
    await flush()
    assert.equal(m.card.isAgentCard.value, false)
    assert.equal(m.api.showsPending.value, true, 'the generic predicate: the tool runs')
    interaction(env.w, { opensRun: true })
    runState(env.w, 'agent', { runStartedAt: T(3), runs: { [runKey('root', 'call')]: { open: true, startedAt: T(3) } } })
    await flush()
    assert.equal(m.card.isControlCard.value, true)
    assert.equal(m.card.isToolRunning.value, false, 'no generic spinner once the call has a result')
    assert.equal(m.api.showsPending.value, true, 'the agent predicate: open run short of its rows')
    runState(env.w, 'agent', { running: false, runStartedAt: T(3), runs: { [runKey('root', 'call')]: { open: false, startedAt: T(3) } } })
    await flush()
    assert.equal(m.api.showsPending.value, false)
    await m.clock.advance(3000)
    assert.equal(m.clock.liveIntervals(), 0)
    assert.ok(showsRows(m))
})

test('§9 Own run closing while the Result section polls: the pending text stops at once', async () => {
    const env = world()
    controlSetup(env)
    const m = await mountCard(env, { expected: 2, display: 1 })
    m.calls[0].resolve(rows(1))
    await flush()
    await m.clock.advance(3000)
    assert.equal(m.calls.length, 2, 'polling')
    runState(env.w, 'agent', { running: false, runStartedAt: T(2), runs: { [runKey('root', 'call')]: { open: false, startedAt: T(2) } } })
    await flush()
    assert.equal(m.api.showsPending.value, false, 'at once, with a request in flight')
    m.calls[1].resolve(rows(1))
    await flush()
    assert.equal(m.clock.liveIntervals(), 0)
})

test('§9 Control card: agentId from the interaction; name via getAgentDisplay, or Agent "<short id>"', async () => {
    const env = world()
    interaction(env.w, { agentId: 'f00dcafe12345678' })
    toolState(env.w, { count: 1 })
    const m = await mountCard(env)
    assert.equal(m.card.agentId.value, 'f00dcafe12345678')
    assert.equal(m.card.controlAgentName.value, 'Agent "f00dcafe"', 'no display name: the short id')
    link(env.w, { toolId: 'spawn', agentId: 'f00dcafe12345678', displayName: 'Fix the tests' })
    await flush()
    assert.equal(m.card.controlAgentName.value, 'Fix the tests')
})

test('§8.3 Agent running for: the agent\'s run start in unix seconds, not the card\'s', async () => {
    const env = world()
    controlSetup(env, { runStart: T(4) })
    const m = await mountCard(env, { timestamp: T(3) })
    assert.equal(m.card.agentRunStartedAt.value, MS(4) / 1000)
    env.w.runStates.agent.runStartedAt = null
    assert.equal(m.card.agentRunStartedAt.value, null)
})
