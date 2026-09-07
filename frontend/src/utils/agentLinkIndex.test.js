import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, computed } from 'vue'
import { agentLinkState, setAgentLink, clearAgentLinks, markAgentStopped, markAgentIdle, beginAgentFetch, applyAgentSnapshot, rootAgentToolLine, staleSyntheticAgentIds, handleAgentEvent, buildAgentTree, hasTreeAgents } from './agentLinkIndex.js'

const start = '2026-09-07T01:00:00Z'
const stop = '2026-09-07T01:01:00Z'
const api = (id = 'child', owner = 'launcher', running = true) => ({ agent_id: id, owner_session_id: owner, tool_use_id: `spawn-${id}`, tool_use_line_num: 60, started_at: start, is_background: true, running })
function snapshot(state, links) { return applyAgentSnapshot(state, 'root', links, beginAgentFetch(state, 'root')) }

test('root cleanup reaches links whose launcher session is not loaded', () => {
    const state = agentLinkState()
    snapshot(state, [api('launcher', 'root'), api()])
    assert.deepEqual(staleSyntheticAgentIds(state, 'root', { child: { synthetic: true, started_at: Date.parse(start) / 1000 } }, Date.parse(stop)), ['child'])
})
test('delayed running REST cannot overwrite a newer completion', () => {
    const state = agentLinkState()
    snapshot(state, [api()])
    const token = beginAgentFetch(state, 'root')
    markAgentStopped(state, 'child', stop, 'root')
    assert.deepEqual(applyAgentSnapshot(state, 'root', [api()], token), [])
    assert.equal(state.agentLinkIndex.child.stoppedAt, stop)
    assert.equal(state.agentLinkIndex.child.running, false)
})
test('completion before link survives initial fetch and background upgrade', () => {
    const state = agentLinkState()
    markAgentStopped(state, 'child', stop, 'root')
    snapshot(state, [api()])
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', isBackground: true })
    assert.equal(state.agentLinkIndex.child.stoppedAt, stop)
})
test('latest fetch wins and an authoritative empty snapshot removes obsolete links', () => {
    const state = agentLinkState()
    const old = beginAgentFetch(state, 'root')
    snapshot(state, [api()])
    assert.deepEqual(applyAgentSnapshot(state, 'root', [], old), [])
    assert.ok(state.agentLinkIndex.child)
    snapshot(state, [])
    assert.equal(state.agentLinkIndex.child, undefined)
})
test('live creation during a fetch survives an empty response', () => {
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    setAgentLink(state, 'launcher', 't', { agentId: 'child', rootSessionId: 'root' })
    applyAgentSnapshot(state, 'root', [], token)
    assert.ok(state.agentLinkIndex.child)
})
test('replacement and eviction remove reverse entries and invalidate pending fetches', () => {
    const state = agentLinkState()
    setAgentLink(state, 'root', 't', { agentId: 'first', rootSessionId: 'root' })
    setAgentLink(state, 'root', 't', { agentId: 'second', rootSessionId: 'root' })
    assert.equal(state.agentLinkIndex.first, undefined)
    const token = beginAgentFetch(state, 'root')
    clearAgentLinks(state, 'root')
    assert.equal(state.agentLinkIndex.second, undefined)
    assert.deepEqual(applyAgentSnapshot(state, 'root', [api()], token), [])
})
test('comment anchor reacts to delayed links and reaches the root beyond depth 32', () => {
    const state = reactive(agentLinkState())
    const line = computed(() => rootAgentToolLine(state, 'root', 'n40'))
    assert.equal(line.value, null)
    for (let i = 0; i <= 40; i++) setAgentLink(state, i ? `n${i - 1}` : 'root', `t${i}`, {
        agentId: `n${i}`, rootSessionId: 'root', toolUseLineNum: i ? 60 : 115,
    })
    assert.equal(line.value, 115)
    state.agentLinkIndex.n0.ownerSessionId = 'n40'
    assert.equal(line.value, null)
})
test('executable WS handler routes owner/root and does not restart a stopped child', () => {
    const state = agentLinkState(), calls = []
    const store = {
        setAgentLink(owner, tool, agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId) {
            setAgentLink(state, owner, tool, { agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId })
        },
        getAgentLink: (owner, tool) => state.agentLinks[owner]?.[tool],
        markAgentStopped: (id, at, root) => markAgentStopped(state, id, at, root),
        setSyntheticProcessState: (...args) => calls.push(args),
    }
    const msg = { type: 'agent_link_created', agent_session_id: 'child', parent_session_id: 'launcher', root_session_id: 'root', tool_use_id: 't', started_at: start, project_id: 'p' }
    handleAgentEvent(store, msg)
    assert.equal(state.agentLinkIndex.child.ownerSessionId, 'launcher')
    assert.equal(calls[0][1], 'root')
    handleAgentEvent(store, { type: 'agent_stopped', agent_session_id: 'child', root_session_id: 'root', stopped_at: stop })
    handleAgentEvent(store, msg)
    assert.equal(calls.length, 1)
})

for (const idle of [null, stop]) {
    test(`idle evidence (${idle}) before first link survives an older snapshot and live upgrade`, () => {
        const state = agentLinkState()
        const token = beginAgentFetch(state, 'root')
        markAgentIdle(state, 'child', idle)
        applyAgentSnapshot(state, 'root', [{ ...api(), running: false, agent_stopped_at: start }], token)
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, idle)
        assert.equal(state.agentLinkIndex.child.running, undefined)
        setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', isBackground: true })
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, idle)
        assert.equal(state.agentLinkIndex.child.stoppedAt, null)
        snapshot(state, [{ ...api(), agent_stopped_at: start, running: false }])
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, start)
        assert.equal(state.agentLinkIndex.child.running, false)
    })
}
test('idle evidence before first live link is retained without inventing completion', () => {
    const state = agentLinkState()
    markAgentIdle(state, 'child', stop)
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root' })
    assert.equal(state.agentLinkIndex.child.agentStoppedAt, stop)
    assert.equal(state.agentLinkIndex.child.stoppedAt, null)
    assert.equal(state.agentLinkIndex.child.running, undefined)
})

test('duplicate live link retains cold REST idle and server stop without creating activity', () => {
    const state = agentLinkState(), calls = []
    snapshot(state, [{ ...api(), agent_stopped_at: stop, running: false }])
    const store = {
        setAgentLink(owner, tool, agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId) {
            setAgentLink(state, owner, tool, { agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId })
        },
        getAgentLink: (owner, tool) => state.agentLinks[owner]?.[tool],
        setSyntheticProcessState: (...args) => calls.push(args),
    }
    const msg = { type: 'agent_link_created', agent_session_id: 'child', parent_session_id: 'launcher',
        root_session_id: 'root', tool_use_id: 'spawn-child', is_background: true, started_at: start, project_id: 'p' }
    handleAgentEvent(store, msg)
    assert.equal(state.agentLinkIndex.child.agentStoppedAt, stop)
    assert.equal(state.agentLinkIndex.child.running, false)
    assert.equal(calls.length, 0)
    markAgentIdle(state, 'child', null)
    handleAgentEvent(store, msg)
    assert.equal(state.agentLinkIndex.child.agentStoppedAt, null)
    assert.equal(state.agentLinkIndex.child.running, undefined)
    assert.equal(calls.length, 1)
})

test('agent tree nests by launcher ownership at any depth', () => {
    const state = agentLinkState()
    snapshot(state, [api('depth1', 'root'), api('depth2', 'depth1'), api('depth3', 'depth2')])
    const tree = buildAgentTree(state, 'root')
    assert.deepEqual(tree.map(n => n.id), ['depth1'])
    assert.deepEqual(tree[0].children.map(n => n.id), ['depth2'])
    assert.deepEqual(tree[0].children[0].children.map(n => n.id), ['depth3'])
    assert.equal(hasTreeAgents(state, 'root'), true)
    assert.equal(hasTreeAgents(state, 'other'), false)
})
test('agent tree re-anchors an unreachable owner chain on the root', () => {
    const state = agentLinkState()
    // ``orphan``'s launcher is not in the tree; ``loopA``/``loopB`` own each other.
    snapshot(state, [api('orphan', 'gone'), api('loopA', 'loopB'), api('loopB', 'loopA')])
    const tree = buildAgentTree(state, 'root')
    assert.deepEqual(tree.map(n => n.id).sort(), ['loopA', 'loopB', 'orphan'])
    assert.deepEqual(tree.flatMap(n => n.children), [])
})
