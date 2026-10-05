import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, computed } from 'vue'
import {
    agentLinkState, setAgentLink, clearAgentLinks, markAgentStopped, markAgentIdle, beginAgentFetch, applyAgentSnapshot,
    rootAgentToolLine, staleSyntheticAgentIds, handleAgentEvent, buildAgentTree, hasTreeAgents,
    runKey, runStateFromPayload, interactionFromPayload, setAgentRunState, setAgentInteraction, dropRootAgentState,
    effectiveAgentRun,
} from './agentLinkIndex.js'

const start = '2026-09-07T01:00:00Z'
const stop = '2026-09-07T01:01:00Z'
const resume = '2026-09-07T01:02:00Z'
const api = (id = 'child', owner = 'launcher') => ({ agent_id: id, owner_session_id: owner, tool_use_id: `spawn-${id}`, tool_use_line_num: 60, started_at: start, is_background: true })
function snapshot(state, links, root = 'root') { return applyAgentSnapshot(state, root, links, beginAgentFetch(state, root)) }
// A snapshot entry that carries the run-state fields of design §7.2.
function withRun(link, { running = true, runStartedAt = start, runs = [], interactions = [] } = {}) {
    return { ...link, running, run_started_at: runStartedAt, run_background: true, runs, stopped_at: null, interactions }
}
const interaction = (tool, { owner = 'root', opensRun = false, kind = 'send_message' } = {}) => ({
    owner_session_id: owner, tool_use_id: tool, tool_use_line_num: 80, kind, opens_run: opensRun, started_at: resume,
})
const liveRun = (agentId, running, root = 'root', runStartedAt = start) => runStateFromPayload({ running, run_started_at: runStartedAt, run_background: false, runs: [] }, root)

test('applyAgentSnapshot fills agentRunStates and agentInteractions; runs follow open and startedAt, closed_at is not stored', () => {
    const state = agentLinkState()
    const applied = snapshot(state, [withRun(api(), {
        runStartedAt: resume,
        runs: [
            { owner_session_id: 'launcher', tool_use_id: 'spawn-child', started_at: start, open: false, closed_at: stop },
            { owner_session_id: 'root', tool_use_id: 'send-1', started_at: resume, open: true, closed_at: null },
        ],
        interactions: [interaction('send-1', { opensRun: true })],
    })])
    assert.equal(applied.length, 1)
    assert.deepEqual(state.agentRunStates.child, {
        running: true, runStartedAt: resume, runBackground: true, rootSessionId: 'root',
        runs: {
            [runKey('launcher', 'spawn-child')]: { open: false, startedAt: start },
            [runKey('root', 'send-1')]: { open: true, startedAt: resume },
        },
    })
    assert.equal(runKey('root', 'send-1'), 'root:send-1')
    assert.deepEqual(state.agentInteractions.root['send-1'], {
        agentId: 'child', rootSessionId: 'root', kind: 'send_message', opensRun: true, startedAt: resume,
        toolUseLineNum: 80, ownerSessionId: 'root', toolUseId: 'send-1',
    })
    // Snapshot writes stamp nothing.
    assert.deepEqual(state.agentRunRevisions, {})
    assert.deepEqual(state.agentInteractionRevisions, {})
})
test('snapshot entries without run fields still get a not-running entry with no runs', () => {
    const state = agentLinkState()
    snapshot(state, [api()])
    assert.deepEqual(state.agentRunStates.child, { running: false, runStartedAt: null, runBackground: false, rootSessionId: 'root', runs: {} })
    assert.deepEqual(state.agentInteractions, {})
})
test('agent_run_state payload converts to the same entry shape', () => {
    const entry = runStateFromPayload({ type: 'agent_run_state', project_id: 'p', root_session_id: 'root', agent_session_id: 'child',
        running: true, run_started_at: start, run_background: false,
        runs: [{ owner_session_id: 'root', tool_use_id: 't', started_at: start, open: true, closed_at: null }] }, 'root')
    assert.deepEqual(entry, { running: true, runStartedAt: start, runBackground: false, rootSessionId: 'root', runs: { 'root:t': { open: true, startedAt: start } } })
    assert.deepEqual(interactionFromPayload({ owner_session_id: 'o', tool_use_id: 't', tool_use_line_num: 3, kind: 'wait', opens_run: false, started_at: start }, 'a', 'root'),
        { agentId: 'a', rootSessionId: 'root', kind: 'wait', opensRun: false, startedAt: start, toolUseLineNum: 3, ownerSessionId: 'o', toolUseId: 't' })
})

test('Ordering: an agent_run_state stamped after the fetch token wins over that snapshot', () => {
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    setAgentRunState(state, 'child', liveRun('child', false))
    assert.equal(state.agentRunRevisions.child, state.agentRevision)
    applyAgentSnapshot(state, 'root', [withRun(api())], token)
    assert.equal(state.agentRunStates.child.running, false)
})
test('Ordering: an agent_run_state stamped before the fetch token loses to that snapshot', () => {
    const state = agentLinkState()
    setAgentRunState(state, 'child', liveRun('child', false))
    snapshot(state, [withRun(api())])
    assert.equal(state.agentRunStates.child.running, true)
})
test('Ordering: a markAgentIdle during the fetch does not block the snapshot run state', () => {
    const state = agentLinkState()
    snapshot(state, [withRun(api(), { running: false })])
    const token = beginAgentFetch(state, 'root')
    markAgentIdle(state, 'child', stop)
    applyAgentSnapshot(state, 'root', [withRun(api(), { runStartedAt: resume })], token)
    assert.equal(state.agentRunStates.child.running, true)
    assert.equal(state.agentRunStates.child.runStartedAt, resume)
})
test('Ordering: a run state arriving before its link is kept', () => {
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    setAgentRunState(state, 'child', liveRun('child', true))
    applyAgentSnapshot(state, 'root', [], token)
    assert.equal(state.agentLinkIndex.child, undefined)
    assert.equal(state.agentRunStates.child.running, true)
})
test('a snapshot deletes the absent run states of its root only when not stamped after the token', () => {
    const state = agentLinkState()
    snapshot(state, [withRun(api())])
    setAgentRunState(state, 'other', liveRun('other', true, 'root2'))
    snapshot(state, [])
    assert.equal(state.agentRunStates.child, undefined)
    assert.ok(state.agentRunStates.other)
})

test('Interactions ordering: an agent_interaction stamped after the token wins over that snapshot', () => {
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    setAgentInteraction(state, interactionFromPayload(interaction('send-1', { opensRun: true }), 'child', 'root'))
    assert.equal(state.agentInteractionRevisions['root:send-1'], state.agentRevision)
    applyAgentSnapshot(state, 'root', [withRun(api(), { interactions: [interaction('send-1', { opensRun: false })] })], token)
    assert.equal(state.agentInteractions.root['send-1'].opensRun, true)
})
test('Interactions ordering: an interaction the snapshot no longer lists is deleted when its stamp is not newer', () => {
    const state = agentLinkState()
    setAgentInteraction(state, interactionFromPayload(interaction('send-1'), 'child', 'root'))
    setAgentInteraction(state, interactionFromPayload(interaction('send-2', { owner: 'other-root' }), 'x', 'root2'))
    const token = beginAgentFetch(state, 'root')
    setAgentInteraction(state, interactionFromPayload(interaction('send-3'), 'child', 'root'))
    applyAgentSnapshot(state, 'root', [withRun(api())], token)
    assert.equal(state.agentInteractions.root['send-1'], undefined)
    assert.ok(state.agentInteractions.root['send-3'])
    assert.ok(state.agentInteractions['other-root']['send-2'])
})

test('§8.1 reversals: setAgentLink carries no running field', () => {
    const state = agentLinkState()
    snapshot(state, [withRun(api())])
    assert.equal('running' in state.agentLinkIndex.child, false)
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', isBackground: true })
    assert.equal('running' in state.agentLinkIndex.child, false)
    markAgentIdle(state, 'child', stop)
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root' })
    assert.equal('running' in state.agentLinkIndex.child, false)
    assert.equal(state.agentLinkIndex.child.agentStoppedAt, stop)
})
test('§8.1 reversals: markAgentStopped records stoppedAt only, markAgentIdle agentStoppedAt only', () => {
    const state = agentLinkState()
    snapshot(state, [withRun(api())])
    markAgentStopped(state, 'child', stop, 'root')
    assert.equal(state.agentLinkIndex.child.stoppedAt, stop)
    assert.equal('running' in state.agentLinkIndex.child, false)
    assert.equal(state.agentRunStates.child.running, true)
    markAgentIdle(state, 'child', null)
    assert.equal(state.agentLinkIndex.child.agentStoppedAt, null)
    assert.equal('running' in state.agentLinkIndex.child, false)
})
test('delayed snapshot keeps a newer completion as display time', () => {
    const state = agentLinkState()
    snapshot(state, [api()])
    const token = beginAgentFetch(state, 'root')
    markAgentStopped(state, 'child', stop, 'root')
    assert.deepEqual(applyAgentSnapshot(state, 'root', [api()], token), [])
    assert.equal(state.agentLinkIndex.child.stoppedAt, stop)
})
test('completion before link survives initial fetch and background upgrade', () => {
    const state = agentLinkState()
    markAgentStopped(state, 'child', stop, 'root')
    snapshot(state, [api()])
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', isBackground: true })
    assert.equal(state.agentLinkIndex.child.stoppedAt, stop)
})
for (const idle of [null, stop]) {
    test(`idle evidence (${idle}) before first link survives an older snapshot and live upgrade`, () => {
        const state = agentLinkState()
        const token = beginAgentFetch(state, 'root')
        markAgentIdle(state, 'child', idle)
        applyAgentSnapshot(state, 'root', [{ ...api(), agent_stopped_at: start }], token)
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, idle)
        setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', isBackground: true })
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, idle)
        assert.equal(state.agentLinkIndex.child.stoppedAt, null)
        snapshot(state, [{ ...api(), agent_stopped_at: start }])
        assert.equal(state.agentLinkIndex.child.agentStoppedAt, start)
    })
}

test('effectiveAgentRun applies the root cutoff to the newest run start', () => {
    const at = value => Date.parse(value)
    assert.deepEqual(effectiveAgentRun({ running: true, runStartedAt: resume }, 0), { running: true, startedAtUnix: at(resume) / 1000 })
    assert.deepEqual(effectiveAgentRun({ running: false, runStartedAt: resume }, 0), { running: false, startedAtUnix: at(resume) / 1000 })
    assert.equal(effectiveAgentRun({ running: true, runStartedAt: start }, at(stop)).running, false)
    assert.equal(effectiveAgentRun({ running: true, runStartedAt: resume }, at(stop)).running, true)
    assert.equal(effectiveAgentRun({ running: true, runStartedAt: stop }, at(stop)).running, true)
    assert.deepEqual(effectiveAgentRun({ running: true, runStartedAt: null }, at(stop)), { running: false, startedAtUnix: null })
    assert.deepEqual(effectiveAgentRun({ running: true, runStartedAt: null }, 0), { running: true, startedAtUnix: null })
})

test('staleSyntheticAgentIds walks the root run states, with or without a link', () => {
    const state = agentLinkState()
    setAgentRunState(state, 'child', liveRun('child', true))
    setAgentRunState(state, 'fresh', liveRun('fresh', true, 'root', resume))
    setAgentRunState(state, 'elsewhere', liveRun('elsewhere', true, 'root2'))
    const synthetic = at => ({ synthetic: true, started_at: Date.parse(at) / 1000 })
    const processStates = { child: synthetic(start), fresh: synthetic(resume), elsewhere: synthetic(start) }
    assert.deepEqual(staleSyntheticAgentIds(state, 'root', processStates, Date.parse(stop)), ['child'])
})

test('dropRootAgentState drops only the given root entries and revisions', () => {
    const state = agentLinkState()
    setAgentRunState(state, 'child', liveRun('child', true))
    setAgentRunState(state, 'other', liveRun('other', true, 'root2'))
    setAgentInteraction(state, interactionFromPayload(interaction('send-1'), 'child', 'root'))
    setAgentInteraction(state, interactionFromPayload(interaction('send-2', { owner: 'child' }), 'nested', 'root'))
    setAgentInteraction(state, interactionFromPayload(interaction('send-3', { owner: 'root2' }), 'other', 'root2'))
    assert.deepEqual(dropRootAgentState(state, 'root'), ['child'])
    assert.deepEqual(Object.keys(state.agentRunStates), ['other'])
    assert.deepEqual(Object.keys(state.agentRunRevisions), ['other'])
    assert.deepEqual(Object.keys(state.agentInteractions), ['root2'])
    assert.deepEqual(Object.keys(state.agentInteractionRevisions), ['root2:send-3'])
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

test('handleAgentEvent: a link event sets the link only; run state and interaction events reach the store', () => {
    const state = agentLinkState(), calls = []
    const store = {
        setAgentLink(owner, tool, agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId) {
            setAgentLink(state, owner, tool, { agentId, isBackground, toolUseLineNum, slug, stoppedAt, startedAt, agentStoppedAt, rootSessionId })
        },
        getAgentLink: (owner, tool) => state.agentLinks[owner]?.[tool],
        markAgentStopped: (...args) => calls.push(['markAgentStopped', ...args]),
        setSyntheticProcessState: (...args) => calls.push(['setSyntheticProcessState', ...args]),
        setAgentRunState: msg => calls.push(['setAgentRunState', msg]),
        setAgentInteraction: msg => calls.push(['setAgentInteraction', msg]),
    }
    handleAgentEvent(store, { type: 'agent_link_created', agent_session_id: 'child', parent_session_id: 'launcher', root_session_id: 'root', tool_use_id: 't', started_at: start, project_id: 'p' })
    assert.equal(state.agentLinkIndex.child.ownerSessionId, 'launcher')
    assert.equal(state.agentLinkIndex.child.rootSessionId, 'root')
    assert.deepEqual(calls, [])
    handleAgentEvent(store, { type: 'agent_stopped', agent_session_id: 'child', root_session_id: 'root', stopped_at: stop })
    const runMsg = { type: 'agent_run_state', project_id: 'p', root_session_id: 'root', agent_session_id: 'child', running: true, run_started_at: start, run_background: false, runs: [] }
    const interactionMsg = { type: 'agent_interaction', project_id: 'p', root_session_id: 'root', agent_session_id: 'child', ...interaction('send-1') }
    handleAgentEvent(store, runMsg)
    handleAgentEvent(store, interactionMsg)
    assert.deepEqual(calls, [
        ['markAgentStopped', 'child', stop, 'root'],
        ['setAgentRunState', runMsg],
        ['setAgentInteraction', interactionMsg],
    ])
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

test('applyAgentSnapshot carries the agent model and a live link event keeps it', () => {
    const state = agentLinkState()
    const model = { raw: 'claude-opus-4-5-20251101', family: 'opus', version: '4.5' }
    snapshot(state, [{ ...api(), model }])
    assert.deepEqual(state.agentLinkIndex.child.model, model)

    // A live event carries identity only: it must not erase what the snapshot learned.
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', startedAt: start })
    assert.deepEqual(state.agentLinkIndex.child.model, model)
})

test('a snapshot entry without a model stores null', () => {
    const state = agentLinkState()
    snapshot(state, [api()])
    assert.equal(state.agentLinkIndex.child.model, null)
})

test('setAgentLink keeps the stored object when the entry is unchanged, and still stamps the live revision', () => {
    const state = agentLinkState()
    const first = setAgentLink(state, 'root', 't', { agentId: 'child', rootSessionId: 'root', startedAt: start, model: { raw: 'm', family: 'f', version: '1' }, metrics: { totalCost: 1, userMessageCount: 2, contextUsage: 3 } }, false)
    const again = setAgentLink(state, 'root', 't', { agentId: 'child', rootSessionId: 'root', startedAt: start, model: { raw: 'm', family: 'f', version: '1' }, metrics: { totalCost: 1, userMessageCount: 2, contextUsage: 3 } })
    assert.equal(again, first)
    assert.equal(state.agentLinks.root.t, first)
    assert.equal(state.agentLinkIndex.child, first)
    assert.equal(state.agentRevisions.child, state.agentRevision)
    assert.ok(state.agentRevision > 0)
})

test('setAgentLink replaces the stored object when a field, the model or the metrics change', () => {
    const state = agentLinkState()
    const base = { agentId: 'child', rootSessionId: 'root', startedAt: start, model: { raw: 'm' }, metrics: { totalCost: 1 } }
    const first = setAgentLink(state, 'root', 't', base, false)
    const stopped = setAgentLink(state, 'root', 't', { ...base, stoppedAt: stop }, false)
    assert.notEqual(stopped, first)
    assert.equal(stopped.stoppedAt, stop)
    const model = setAgentLink(state, 'root', 't', { ...base, stoppedAt: stop, model: { raw: 'other' } }, false)
    assert.notEqual(model, stopped)
    const metrics = setAgentLink(state, 'root', 't', { ...base, stoppedAt: stop, model: { raw: 'other' }, metrics: { totalCost: 2 } }, false)
    assert.notEqual(metrics, model)
    assert.equal(state.agentLinkIndex.child, metrics)
})

test('a repeated snapshot keeps every link reference; a changed agent gets a new one', () => {
    const state = agentLinkState()
    const links = [withRun(api('a', 'root')), withRun(api('b', 'root'))]
    snapshot(state, links)
    const a = state.agentLinks.root['spawn-a'], b = state.agentLinks.root['spawn-b']
    snapshot(state, links)
    assert.equal(state.agentLinks.root['spawn-a'], a)
    assert.equal(state.agentLinks.root['spawn-b'], b)
    snapshot(state, [links[0], withRun({ ...api('b', 'root'), is_background: false })])
    assert.equal(state.agentLinks.root['spawn-a'], a)
    assert.notEqual(state.agentLinks.root['spawn-b'], b)
    assert.equal(state.agentLinkIndex.b, state.agentLinks.root['spawn-b'])
})
