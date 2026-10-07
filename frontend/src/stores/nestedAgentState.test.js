import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
    agentLinkState, beginAgentFetch, applyAgentSnapshot, markAgentStopped, markAgentIdle, staleSyntheticAgentIds,
    setAgentLink, clearAgentLinks, handleAgentEvent, runStateFromPayload, interactionFromPayload, setAgentRunState,
    setAgentInteraction, dropRootAgentState, effectiveAgentRun,
} from '../utils/agentLinkIndex.js'
import { getSessionCutoffMs } from '../utils/sessions.js'
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'

// The real store actions, sliced from data.js (node cannot import the store:
// extensionless imports). A slice runs from the action name to the next JSDoc.
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const start = source.indexOf(`        ${name}`)
    assert.notEqual(start, -1, `action ${name} not found`)
    const end = source.indexOf('        /**', start)
    return source.slice(start, end)
}
const ACTIONS = [
    'applyAgentRunState(', 'setAgentRunState(', 'setAgentInteraction(', 'async fetchSubagentsState(',
    'setSyntheticProcessState(', 'removeSyntheticProcessState(', '_cleanStaleChildSynthetics(', 'unloadSession(',
    'updateSession(', 'setActiveProcesses(', 'async refreshAllLoadedToolStates(', 'markAgentStopped(', 'unloadProject(',
]
const PROCESS_STATE = { STARTING: 'starting', ASSISTANT_TURN: 'assistant_turn', USER_TURN: 'user_turn', DEAD: 'dead' }

const T0 = '2026-09-07T01:00:00Z'
const T1 = '2026-09-07T01:01:00Z'
const T2 = '2026-09-07T01:02:00Z'
const T3 = '2026-09-07T01:03:00Z'
const unix = at => Date.parse(at) / 1000
const SUBAGENTS_URL = '/api/projects/p/sessions/root/subagents/'

function deferred() {
    let resolve, reject
    const promise = new Promise((ok, ko) => { resolve = ok; reject = ko })
    return { promise, resolve, reject }
}
const okResponse = agents => ({ ok: true, json: async () => agents })

/**
 * A fake store: the sliced actions bound to a plain ``this``. ``fetches`` is a
 * queue of responses (or functions returning one) served in order to ``apiFetch``.
 */
function makeStore({ fetches = [] } = {}) {
    const urls = []
    const apiFetch = async url => {
        urls.push(url)
        const next = fetches.shift()
        if (next === undefined) throw new Error(`unexpected fetch ${url}`)
        return typeof next === 'function' ? next() : next
    }
    const deps = {
        cacheAgentRunState: setAgentRunState, cacheAgentInteraction: setAgentInteraction, cacheAgentStop: markAgentStopped,
        runStateFromPayload, interactionFromPayload, effectiveAgentRun, dropRootAgentState, beginAgentFetch,
        applyAgentSnapshot, staleSyntheticAgentIds, markAgentIdle, getSessionCutoffMs, jsonValuesEqual, apiFetch, PROCESS_STATE,
        isLaunchedEphemeral: () => false, destroyAllBuffers: () => {}, backgroundWorkStatusKey: () => null,
        sweepPendingRequestDrafts: async () => {}, liveDraftKey: (a, b) => `${a}:${b}`, getToolHelpers: () => null,
    }
    const methods = new Function(...Object.keys(deps), `return { ${ACTIONS.map(action).join('')} }`)(...Object.values(deps))
    const store = Object.assign(methods, {
        urls,
        connectionEpoch: 0,
        localState: {
            ...agentLinkState(),
            agentFetchStarts: {},
            sessions: { root: { itemsFetched: true } },
            sessionExpandedGroups: {}, sessionDebugOverride: {}, sessionInternalExpandedGroups: {}, sessionVisualItems: {},
            visualItemCache: {}, optimisticMessages: {}, workflowLinks: {}, toolStates: {}, liveItems: {}, openDetails: {},
            streamingBlocks: {},
        },
        sessions: { root: { id: 'root', project_id: 'p', provider: 'claude_code', last_started_at: T0, last_stopped_at: null } },
        processStates: {},
        sessionItems: {},
        $patch(patch) {
            if (typeof patch === 'function') { patch(this); return }
            for (const [id, session] of Object.entries(patch.sessions || {})) this.sessions[id] = { ...this.sessions[id], ...session }
        },
        getSessionProvider(id) { return this.sessions[id]?.provider ?? null },
        clearAgentLinks(sessionId) { clearAgentLinks(this.localState, sessionId) },
        setAgentLink(owner, tool, agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId) {
            return setAgentLink(this.localState, owner, tool, { agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId })
        },
        getAgentLink(owner, tool) { return this.localState.agentLinks[owner]?.[tool] },
        recomputeVisualItems() {},
        _hydrateSessionLayoutFromPersisted() {},
        tryFinalizePendingBinding() {},
        _tryLinkPeerDelivery() {},
        refreshSessionToolStates: async () => {},
    })
    return store
}
const runMsg = (agent, { running = true, runStartedAt = T1, runs = [], root = 'root' } = {}) => ({
    type: 'agent_run_state', project_id: 'p', root_session_id: root, agent_session_id: agent,
    running, run_started_at: runStartedAt, run_background: true, runs,
})
const interactionMsg = (agent, tool, { owner = 'root', root = 'root' } = {}) => ({
    type: 'agent_interaction', project_id: 'p', root_session_id: root, owner_session_id: owner, agent_session_id: agent,
    tool_use_id: tool, tool_use_line_num: 90, kind: 'send_message', opens_run: true, started_at: T2,
})
const snapshotEntry = (agent, { owner = 'root', running = true, runStartedAt = T1, interactions = [] } = {}) => ({
    agent_id: agent, owner_session_id: owner, tool_use_id: `spawn-${agent}`, tool_use_line_num: 10, started_at: T1,
    is_background: true, running, run_started_at: runStartedAt, run_background: true,
    runs: [{ owner_session_id: owner, tool_use_id: `spawn-${agent}`, started_at: runStartedAt, open: running, closed_at: null }],
    stopped_at: null, interactions,
})
const synthetic = (store, id) => !!store.processStates[id]?.synthetic

test('applyAgentRunState: running / not running / run before the cutoff; started_at is the newest run', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child', { runStartedAt: T2, runs: [
        { owner_session_id: 'root', tool_use_id: 'spawn-child', started_at: T1, open: false, closed_at: T1 },
        { owner_session_id: 'root', tool_use_id: 'send', started_at: T2, open: true, closed_at: null },
    ] }))
    assert.equal(store.processStates.child.state, 'assistant_turn')
    assert.equal(store.processStates.child.started_at, unix(T2))
    assert.equal(store.processStates.child.project_id, 'p')
    assert.equal(store.processStates.child.provider, 'claude_code')
    store.setAgentRunState(runMsg('child', { running: false }))
    assert.equal(store.processStates.child, undefined)
    store.sessions.root.last_started_at = T2
    store.setAgentRunState(runMsg('child', { runStartedAt: T1 }))
    assert.equal(store.processStates.child, undefined)
})
test('an agent_run_state message re-creates the synthetic state after a stop', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    assert.ok(synthetic(store, 'child'))
    store.markAgentStopped('child', T2, 'root')
    assert.ok(synthetic(store, 'child'))
    assert.equal(store.localState.agentStops.child.stoppedAt, T2)
    store.setAgentRunState(runMsg('child', { running: false }))
    assert.equal(store.processStates.child, undefined)
    store.setAgentRunState(runMsg('child', { runStartedAt: T3 }))
    assert.ok(synthetic(store, 'child'))
    assert.equal(store.processStates.child.started_at, unix(T3))
})
test('Reconnect: setActiveProcesses re-applies run states, also after the snapshot, never over a real process', () => {
    const store = makeStore({ fetches: [okResponse([snapshotEntry('child'), snapshotEntry('real')])] })
    store.setAgentRunState(runMsg('live'))
    store.setActiveProcesses([])
    assert.ok(synthetic(store, 'live'))
    return store.fetchSubagentsState('p', 'root').then(() => {
        assert.ok(synthetic(store, 'child'))
        store.setActiveProcesses([{ session_id: 'real', project_id: 'p', state: 'user_turn' }])
        assert.ok(synthetic(store, 'child'))
        // The snapshot, fetched after ``live``'s stamp, no longer lists it.
        assert.equal(store.processStates.live, undefined)
        assert.equal(store.processStates.real.state, 'user_turn')
        assert.equal(store.processStates.real.synthetic, undefined)
    })
})
test('Store rules: a root restart removes the robot of a run started before the cutoff and keeps a later one', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('old', { runStartedAt: T1 }))
    store.setAgentRunState(runMsg('new', { runStartedAt: T3 }))
    store.updateSession({ ...store.sessions.root, last_started_at: T2 })
    assert.equal(store.processStates.old, undefined)
    assert.ok(synthetic(store, 'new'))
})
test('Store rules: a snapshot that no longer lists an agent removes its synthetic state', async () => {
    const store = makeStore({ fetches: [okResponse([snapshotEntry('child')]), okResponse([])] })
    await store.fetchSubagentsState('p', 'root')
    assert.ok(synthetic(store, 'child'))
    await store.fetchSubagentsState('p', 'root')
    assert.equal(store.localState.agentRunStates.child, undefined)
    assert.equal(store.processStates.child, undefined)
})
test('Root cutoff change: applyAgentRunState runs after the $patch (a run between the old and new cutoff loses its robot)', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('between', { runStartedAt: T1 }))
    assert.ok(synthetic(store, 'between'))
    store.updateSession({ ...store.sessions.root, last_started_at: T2 })
    assert.equal(store.processStates.between, undefined)
    assert.equal(store.sessions.root.last_started_at, T2)
})
test('Root cutoff change re-applies entries: a run state stored under the old cutoff gets its robot back when the cutoff drops', () => {
    const store = makeStore()
    store.sessions.root.last_started_at = T2
    store.setAgentRunState(runMsg('child', { runStartedAt: T1 }))
    assert.equal(store.processStates.child, undefined)
    store.updateSession({ ...store.sessions.root, last_started_at: null })
    assert.ok(synthetic(store, 'child'))
})

for (const [label, failure] of [
    ['HTTP error', () => ({ ok: false, json: async () => [] })],
    ['network error', () => { throw new Error('offline') }],
]) {
    test(`Root first load with a failed snapshot (${label}): the stored run states of that root are applied`, async () => {
        const store = makeStore({ fetches: [failure] })
        const originalError = console.error
        console.error = () => {}
        try {
            store.localState.sessions.root.itemsFetched = false
            store.setAgentRunState(runMsg('child'))
            assert.equal(store.processStates.child, undefined)
            store.localState.sessions.root.itemsFetched = true
            await store.fetchSubagentsState('p', 'root')
        } finally {
            console.error = originalError
        }
        assert.ok(synthetic(store, 'child'))
        assert.equal(store.localState.agentLoaded.root, true)
        assert.deepEqual(store.urls, [SUBAGENTS_URL])
    })
}
test('Post-snapshot apply of every entry: kept live entry gets its robot, a deleted entry loses its own', async () => {
    const response = deferred()
    const store = makeStore({ fetches: [okResponse([snapshotEntry('gone')]), () => response.promise] })
    // ``gone`` was stored by an earlier snapshot and still shows a leftover robot.
    await store.fetchSubagentsState('p', 'root')
    assert.ok(synthetic(store, 'gone'))
    store.localState.sessions.root.itemsFetched = false
    const work = store.fetchSubagentsState('p', 'root')
    store.setAgentRunState(runMsg('late'))
    assert.ok(store.localState.agentRunRevisions.late > 0)
    assert.equal(store.processStates.late, undefined)
    store.localState.sessions.root.itemsFetched = true
    response.resolve(okResponse([]))
    await work
    assert.ok(synthetic(store, 'late'))
    assert.equal(store.localState.agentRunStates.gone, undefined)
    assert.equal(store.processStates.gone, undefined)
})
test('Reconnect: a snapshot discarded by a generation bump is re-issued once and applies', async () => {
    const first = deferred()
    const store = makeStore({ fetches: [() => first.promise, okResponse([snapshotEntry('child')])] })
    const work = store.fetchSubagentsState('p', 'root')
    clearAgentLinks(store.localState, 'unrelated')
    first.resolve(okResponse([]))
    await work
    assert.deepEqual(store.urls, [SUBAGENTS_URL, SUBAGENTS_URL])
    assert.ok(store.localState.agentRunStates.child)
    assert.ok(synthetic(store, 'child'))
})
test('Overlapping fetches of one root: the older is discarded, not re-issued; the newer applies (2 requests)', async () => {
    const older = deferred(), newer = deferred()
    const store = makeStore({ fetches: [() => older.promise, () => newer.promise] })
    const first = store.fetchSubagentsState('p', 'root')
    const second = store.fetchSubagentsState('p', 'root')
    older.resolve(okResponse([snapshotEntry('old')]))
    await new Promise(done => setTimeout(done, 0))
    newer.resolve(okResponse([snapshotEntry('new')]))
    await Promise.all([first, second])
    assert.deepEqual(store.urls, [SUBAGENTS_URL, SUBAGENTS_URL])
    assert.ok(store.localState.agentRunStates.new)
    assert.equal(store.localState.agentRunStates.old, undefined)
    assert.ok(synthetic(store, 'new'))
})
test('Reconnect: a second discard applies the root stored run states', async () => {
    const first = deferred(), second = deferred()
    const store = makeStore({ fetches: [() => first.promise, () => second.promise] })
    store.localState.sessions.root.itemsFetched = false
    store.setAgentRunState(runMsg('child'))
    store.localState.sessions.root.itemsFetched = true
    const work = store.fetchSubagentsState('p', 'root')
    clearAgentLinks(store.localState, 'unrelated')
    first.resolve(okResponse([]))
    await new Promise(done => setTimeout(done, 0))
    clearAgentLinks(store.localState, 'unrelated')
    second.resolve(okResponse([]))
    await work
    assert.equal(store.urls.length, 2)
    assert.ok(store.localState.agentRunStates.child)
    assert.ok(synthetic(store, 'child'))
})
test('Reconnect: a snapshot in flight when unloadSession(root) runs is not re-issued and refills nothing', async () => {
    const response = deferred()
    const store = makeStore({ fetches: [() => response.promise] })
    store.setAgentRunState(runMsg('child'))
    const work = store.fetchSubagentsState('p', 'root')
    store.unloadSession('root')
    response.resolve(okResponse([snapshotEntry('child'), snapshotEntry('other')]))
    await work
    assert.deepEqual(store.urls, [SUBAGENTS_URL])
    assert.deepEqual(store.localState.agentRunStates, {})
    assert.deepEqual(store.localState.agentLinkIndex, {})
    assert.deepEqual(store.processStates, {})
})

test('Store lifetime: unloadSession(root) drops the root run states and interactions', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    store.setAgentRunState(runMsg('foreign', { root: 'root2' }))
    store.setAgentInteraction(interactionMsg('child', 'send-1'))
    store.setAgentInteraction(interactionMsg('foreign', 'send-2', { owner: 'root2', root: 'root2' }))
    assert.equal(store.localState.agentInteractions.root['send-1'].agentId, 'child')
    assert.ok(store.localState.agentInteractionRevisions['root:send-1'] > 0)
    store.unloadSession('root')
    assert.deepEqual(Object.keys(store.localState.agentRunStates), ['foreign'])
    assert.deepEqual(Object.keys(store.localState.agentInteractions), ['root2'])
})
test('Store lifetime: applyAgentRunState for a root whose items are not loaded removes any synthetic state and sets none', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    assert.ok(synthetic(store, 'child'))
    store.localState.sessions.root.itemsFetched = false
    store.applyAgentRunState('child')
    assert.equal(store.processStates.child, undefined)
    store.setAgentRunState(runMsg('child', { runStartedAt: T3 }))
    assert.equal(store.processStates.child, undefined)
    assert.equal(store.localState.agentRunStates.child.runStartedAt, T3)
})
test('unloadSession of a root removes the synthetic states of all its run-state entries, even with no session row', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    store.setAgentRunState(runMsg('nested'))
    assert.equal(store.sessions.child, undefined)
    store.unloadSession('root')
    assert.equal(store.processStates.child, undefined)
    assert.equal(store.processStates.nested, undefined)
})
test('unloadSession of an agent with a running entry keeps its robot and its interactions', async () => {
    const owned = { owner_session_id: 'child', tool_use_id: 'send-1', tool_use_line_num: 90, kind: 'send_message', opens_run: true, started_at: T2 }
    const store = makeStore({ fetches: [okResponse([snapshotEntry('child'), snapshotEntry('nested', { owner: 'child', interactions: [owned] })])] })
    store.sessions.child = { id: 'child', project_id: 'p', parent_session_id: 'root', provider: 'claude_code' }
    store.localState.sessions.child = { itemsFetched: true }
    store.setAgentRunState(runMsg('child'))
    store.setAgentInteraction(interactionMsg('nested', 'send-1', { owner: 'child' }))
    store.unloadSession('child')
    assert.ok(synthetic(store, 'child'))
    assert.equal(store.localState.agentInteractions.child['send-1'].agentId, 'nested')
    assert.equal(store.localState.sessions.child.itemsFetched, false)
    // The root snapshot re-fetch (root items loaded) lists the owned call again.
    await new Promise(done => setTimeout(done, 0))
    assert.ok(synthetic(store, 'child'))
    assert.equal(store.localState.agentInteractions.child['send-1'].agentId, 'nested')
})
test('unloadSession of an agent whose root stays loaded re-fetches the root snapshot and restores its owned links', async () => {
    const response = deferred()
    const nested = snapshotEntry('nested', { owner: 'child' })
    const store = makeStore({ fetches: [okResponse([snapshotEntry('child'), nested]), () => response.promise] })
    store.sessions.child = { id: 'child', project_id: 'p', parent_session_id: 'root', provider: 'claude_code' }
    await store.fetchSubagentsState('p', 'root')
    assert.equal(store.localState.agentLinks.child['spawn-nested'].agentId, 'nested')
    store.unloadSession('child')
    assert.equal(store.localState.agentLinks.child, undefined)
    assert.deepEqual(store.urls, [SUBAGENTS_URL, SUBAGENTS_URL])
    response.resolve(okResponse([snapshotEntry('child'), nested]))
    await new Promise(done => setTimeout(done, 0))
    assert.equal(store.localState.agentLinks.child['spawn-nested'].agentId, 'nested')
    assert.ok(synthetic(store, 'nested'))
})
test('unloadProject unloads the root first: its agents issue no /subagents/ request', () => {
    const store = makeStore()
    // Agents listed before their root, so insertion order alone would unload them first.
    for (const id of ['a1', 'a2', 'a3']) {
        store.sessions[id] = { id, project_id: 'p', parent_session_id: 'root', provider: 'claude_code' }
        store.localState.sessions[id] = { itemsFetched: true }
        store.setAgentRunState(runMsg(id))
    }
    const root = store.sessions.root
    delete store.sessions.root
    store.sessions.root = root
    store.localState.projects = { p: { sessionsFetched: true } }
    store.unloadProject('p')
    assert.deepEqual(store.urls, [])
    assert.deepEqual(store.sessions, {})
    assert.deepEqual(store.localState.agentRunStates, {})
    assert.deepEqual(store.processStates, {})
    assert.equal(store.localState.projects.p.sessionsFetched, false)
})
test('unloadSession of an agent whose root items are not loaded fetches nothing', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    store.localState.sessions.root.itemsFetched = false
    store.unloadSession('child')
    assert.deepEqual(store.urls, [])
})

test('§8.1 reversals: agent_stopped, a child session_updated and agent_link_created leave the synthetic state alone', () => {
    const store = makeStore()
    store.setAgentRunState(runMsg('child'))
    handleAgentEvent(store, { type: 'agent_stopped', agent_session_id: 'child', root_session_id: 'root', stopped_at: T2 })
    assert.ok(synthetic(store, 'child'))
    store.updateSession({ id: 'child', project_id: 'p', parent_session_id: 'root', last_stopped_at: T3 })
    assert.ok(synthetic(store, 'child'))
    assert.equal(store.localState.agentIdle.child.stoppedAt, T3)
    handleAgentEvent(store, { type: 'agent_link_created', agent_session_id: 'fresh', parent_session_id: 'root', root_session_id: 'root',
        tool_use_id: 'spawn-fresh', started_at: T3, project_id: 'p' })
    assert.equal(store.localState.agentLinkIndex.fresh.ownerSessionId, 'root')
    assert.equal(store.processStates.fresh, undefined)
})
test('refreshAllLoadedToolStates bumps connectionEpoch once, after the refreshes settle', async () => {
    const store = makeStore()
    store.sessions.other = { id: 'other', project_id: 'p' }
    store.localState.sessions.other = { itemsFetched: true }
    const pending = { root: deferred(), other: deferred() }
    store.refreshSessionToolStates = (_projectId, sessionId) => pending[sessionId].promise
    const work = store.refreshAllLoadedToolStates()
    pending.root.resolve()
    await new Promise(done => setTimeout(done, 0))
    assert.equal(store.connectionEpoch, 0)
    pending.other.reject(new Error('boom'))
    await work
    assert.equal(store.connectionEpoch, 1)
})
