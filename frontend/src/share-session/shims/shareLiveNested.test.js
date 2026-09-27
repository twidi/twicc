import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { connectShareLive } from './shareLive.js'
import { agentLinkState, setAgentLink, markAgentStopped, beginAgentFetch, applyAgentSnapshot, runStateFromPayload, interactionFromPayload, setAgentRunState, setAgentInteraction, effectiveAgentRun } from '../../utils/agentLinkIndex.js'
import { getSessionCutoffMs } from '../../utils/sessions.js'

test('live share routes nested launch and queue-only completion before delayed REST', () => {
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentLink: link => setAgentLink(state, link.owner_session_id, link.tool_use_id, { agentId: link.agent_id, rootSessionId: 'root' }),
            onAgentStopped: msg => markAgentStopped(state, msg.agent_session_id, msg.stopped_at, msg.root_session_id),
        })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_link', link: { agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't' } }) })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_stopped', agent_session_id: 'child', root_session_id: 'root', stopped_at: '2026-09-07T01:01:00Z' }) })
        applyAgentSnapshot(state, 'root', [{ agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't', running: true }], token)
        // A stop records the stop time only; it invents no idle time.
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, undefined)
        assert.equal(state.agentLinks.launcher.t.stoppedAt, '2026-09-07T01:01:00Z')
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})

test('live share dispatches Codex idle and null wake-up without manufacturing a completion', async () => {
    const { markAgentIdle } = await import('../../utils/agentLinkIndex.js')
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    const state = agentLinkState()
    setAgentLink(state, 'launcher', 't', { agentId: 'child', rootSessionId: 'root' })
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentIdle: msg => markAgentIdle(state, msg.agent_session_id, msg.agent_stopped_at),
        })
        const emit = at => socket.onmessage({ data: JSON.stringify({ type: 'share_agent_idle', agent_session_id: 'child', root_session_id: 'root', agent_stopped_at: at }) })
        emit('2026-09-07T01:01:00Z')
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, '2026-09-07T01:01:00Z')
        assert.equal(state.agentLinks.launcher.t.stoppedAt, null)
        emit(null)
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, null)
        assert.equal(state.agentLinks.launcher.t.stoppedAt, null)
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})

test('live share duplicate link preserves cold snapshot state through the real shim action', () => {
    const source = readFileSync(new URL('./dataStoreShim.js', import.meta.url), 'utf8')
    const action = source.slice(source.indexOf('        addAgentLink(root, link)'), source.indexOf('        // Live: root session'))
    const state = Object.assign(agentLinkState(), new Function('setAgentLink', `return { ${action} }`)(setAgentLink))
    const stopped = '2026-09-07T01:01:00Z'
    const link = { agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't', running: false, agent_stopped_at: stopped }
    applyAgentSnapshot(state, 'root', [link], beginAgentFetch(state, 'root'))
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentLink: entry => state.addAgentLink('root', entry),
        })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_link', link: { ...link, running: undefined, agent_stopped_at: null } }) })
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, stopped)
        assert.equal(state.agentLinks.launcher.t.stoppedAt, null)
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})

// ── Agent run states (design §7.3, §8.3) ─────────────────────────────────
// The shim's run-state getters and actions are object members: cut them out by
// their group markers (first pair = getters, second pair = actions).
function shimRunGroups() {
    const source = readFileSync(new URL('./dataStoreShim.js', import.meta.url), 'utf8')
    const start = '// ── Agent runs (share) ──', end = '// ── end agent runs ──'
    const getterStart = source.indexOf(start), getterEnd = source.indexOf(end, getterStart)
    const actionStart = source.indexOf(start, getterEnd), actionEnd = source.indexOf(end, actionStart)
    assert.ok(getterStart > 0 && getterEnd > getterStart && actionStart > getterEnd && actionEnd > actionStart)
    return { getters: source.slice(getterStart, getterEnd), actions: source.slice(actionStart, actionEnd) }
}
function makeShimStore({ runStatesAvailable = true, sessions = {} } = {}) {
    const { getters, actions } = shimRunGroups()
    const helpers = { agentLinkState, setAgentLink, beginAgentFetch, applyAgentSnapshot, runStateFromPayload, interactionFromPayload,
        setAgentRunState, setAgentInteraction, effectiveAgentRun, getSessionCutoffMs }
    const names = Object.keys(helpers)
    const make = body => new Function(...names, `return { ${body} }`)(...names.map(n => helpers[n]))
    const store = Object.assign(agentLinkState(), { runStatesAvailable, connectionEpoch: 0, sessions }, make(actions))
    for (const [name, getter] of Object.entries(make(getters))) {
        Object.defineProperty(store, name, { get: () => getter(store) })
    }
    return store
}
const runStateMsg = (over = {}) => ({ type: 'share_agent_run_state', root_session_id: 'root', agent_session_id: 'child',
    running: true, run_started_at: '2026-09-07T01:00:00Z', run_background: false,
    runs: [{ owner_session_id: 'root', tool_use_id: 't', open: true, started_at: '2026-09-07T01:00:00Z' }], ...over })
const interactionMsg = (over = {}) => ({ type: 'share_agent_interaction', root_session_id: 'root', owner_session_id: 'root',
    agent_session_id: 'child', tool_use_id: 'send', tool_use_line_num: 12, kind: 'send', opens_run: true,
    started_at: '2026-09-07T01:02:00Z', ...over })
function withFakeSocket(fn) {
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    const sockets = []
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { sockets.push(this) } close() {} }
    try { return fn(sockets) } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
}

test('live share routes agent run states and interactions to their callbacks', () => withFakeSocket((sockets) => {
    const runStates = [], interactions = []
    const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
        onAgentRunState: msg => runStates.push(msg), onAgentInteraction: msg => interactions.push(msg) })
    const run = runStateMsg(), call = interactionMsg()
    sockets[0].onmessage({ data: JSON.stringify(run) })
    sockets[0].onmessage({ data: JSON.stringify(call) })
    assert.deepEqual(runStates, [run])
    assert.deepEqual(interactions, [call])
    disconnect()
}))

test('share shim: isAgentRunning follows the run state, the root cutoff and runStatesAvailable', () => {
    const store = makeShimStore({ sessions: { root: { id: 'root', last_started_at: '2026-09-07T00:00:00Z', last_stopped_at: null } } })
    store.setAgentRunState(runStateMsg())
    assert.equal(store.getAgentRunState('child').running, true)
    assert.equal(store.agentRunRevisions.child > 0, true, 'a live event stamps its revision')
    assert.equal(store.isAgentRunning('child'), true)
    assert.equal(store.isAgentRunning('unknown'), false)
    // The root restarted after the newest run began: that run cannot be running.
    store.sessions.root = { ...store.sessions.root, last_started_at: '2026-09-07T02:00:00Z' }
    assert.equal(store.isAgentRunning('child'), false)
    store.sessions.root = { ...store.sessions.root, last_started_at: '2026-09-07T00:00:00Z' }
    // Stored whatever runStatesAvailable says, but never read while it is false.
    const off = makeShimStore({ runStatesAvailable: false, sessions: store.sessions })
    off.setAgentRunState(runStateMsg())
    assert.equal(off.getAgentRunState('child').running, true)
    assert.equal(off.isAgentRunning('child'), false)
})

test('share shim: getAgentInteraction returns the stored call, null for an unknown one', () => {
    const store = makeShimStore({ runStatesAvailable: false })
    store.setAgentInteraction(interactionMsg())
    const entry = store.getAgentInteraction('root', 'send')
    assert.equal(entry.agentId, 'child')
    assert.equal(entry.kind, 'send')
    assert.equal(entry.opensRun, true)
    assert.equal(entry.toolUseLineNum, 12)
    assert.ok(store.agentInteractionRevisions['root:send'] > 0)
    // A call above the display ceiling is never relayed nor listed: generic card.
    assert.equal(store.getAgentInteraction('root', 'hidden'), null)
    assert.equal(store.getAgentInteraction('other', 'send'), null)
})

test('share shim: disableRunStates turns run states off and discards the in-flight setup snapshot', () => {
    const store = makeShimStore({ sessions: { root: { id: 'root', last_started_at: null } } })
    const token = beginAgentFetch(store, 'root')
    store.disableRunStates('root')
    assert.equal(store.runStatesAvailable, false)
    const applied = applyAgentSnapshot(store, 'root', [{ agent_id: 'child', owner_session_id: 'root', tool_use_id: 't',
        running: true, run_started_at: '2026-09-07T01:00:00Z', runs: [], interactions: [] }], token)
    assert.deepEqual(applied, [])
    assert.equal(store.getAgentRunState('child'), null)
    assert.equal(store.agentLinks.root, undefined)
    assert.equal(store.isAgentRunning('child'), false)
})

test('share viewer: include_subagents turned off live disables run states for good', () => {
    const source = readFileSync(new URL('../ShareSessionApp.vue', import.meta.url), 'utf8')
    const from = source.indexOf('onMeta: (m) => {'), to = source.indexOf('onToolState:', from)
    assert.ok(from > 0 && to > from)
    const store = makeShimStore({ sessions: { root: { id: 'root', last_started_at: null } } })
    store.getSession = id => store.sessions[id] || null
    store.setSession = session => { store.sessions[session.id] = session }
    const meta = { session_id: 'root', include_subagents: true }
    const { onMeta } = new Function('meta', 'store', 'settings', 'clampMode', `return { ${source.slice(from, to)} }`)(
        meta, store, { setDisplayMode() {}, displayMode: 'normal' }, m => m)
    const token = beginAgentFetch(store, 'root')
    onMeta({ include_subagents: true, last_started_at: '2026-09-07T00:00:00Z', last_stopped_at: null })
    assert.equal(store.runStatesAvailable, true)
    assert.equal(store.sessions.root.last_started_at, '2026-09-07T00:00:00Z', 'onMeta refreshes the root cutoff')
    onMeta({ include_subagents: false })
    assert.equal(store.runStatesAvailable, false)
    assert.deepEqual(applyAgentSnapshot(store, 'root', [{ agent_id: 'child', owner_session_id: 'root', tool_use_id: 't', running: true }], token), [])
    onMeta({ include_subagents: true })
    assert.equal(store.runStatesAvailable, false, 'nothing turns run states back on before a reload')
})

test('share viewer: a socket reopen re-fetches nothing and fires no callback', (t) => withFakeSocket((sockets) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const fired = []
    const record = name => (...args) => fired.push([name, ...args])
    const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
        onItems: record('items'), onMeta: record('meta'), onToolState: record('tool'), onProcessState: record('process'),
        onAgentLink: record('link'), onAgentStopped: record('stopped'), onAgentIdle: record('idle'),
        onAgentRunState: record('run'), onAgentInteraction: record('interaction'), onClosed: record('closed') })
    sockets[0].onopen()
    sockets[0].onclose()
    assert.equal(sockets.length, 1, 'the reopen waits for the backoff')
    t.mock.timers.tick(1000)
    assert.equal(sockets.length, 2)
    sockets[1].onopen()
    assert.deepEqual(fired, [])
    sockets[1].onmessage({ data: JSON.stringify(runStateMsg()) })
    assert.deepEqual(fired.map(f => f[0]), ['run'])
    disconnect()
}))
