import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { agentLinkState, beginAgentFetch, applyAgentSnapshot, markAgentStopped, markAgentIdle, staleSyntheticAgentIds } from '../utils/agentLinkIndex.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function action(name) {
    const start = source.indexOf(`        ${name}`)
    const end = source.indexOf('        /**', start)
    return source.slice(start, end)
}
function storeWithFetch(response) {
    const methods = new Function('beginAgentFetch', 'applyAgentSnapshot', 'apiFetch', 'getSessionCutoffMs', `return { ${action('async fetchSubagentsState(')} }`)(beginAgentFetch, applyAgentSnapshot, async () => ({ ok: true, json: async () => response }), s => s?.cutoff || 0)
    return {
        ...methods, localState: agentLinkState(), sessions: { root: {} }, processStates: {},
        removeSyntheticProcessState(id) { delete this.processStates[id] },
        setSyntheticProcessState(id) { this.processStates[id] = { synthetic: true } },
    }
}
const agent = { agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 'spawn', running: true, started_at: '2026-09-07T01:00:00Z' }

test('real fetch action removes a stale synthetic when server running=false', async () => {
    const store = storeWithFetch([{ ...agent, running: false }])
    store.processStates.child = { synthetic: true }
    await store.fetchSubagentsState('p', 'root')
    assert.equal(store.processStates.child, undefined)
    assert.equal(store.localState.agentLinks.launcher.spawn.rootSessionId, 'root')
})
test('real fetch action cannot recreate synthetic after an intervening stop', async () => {
    let resolve
    const response = new Promise(done => { resolve = done })
    const store = storeWithFetch(response)
    const work = store.fetchSubagentsState('p', 'root')
    markAgentStopped(store.localState, 'child', '2026-09-07T01:01:00Z', 'root')
    resolve([agent])
    await work
    assert.equal(store.processStates.child, undefined)
})
test('real cleanup action ignores launcher idle but applies root cutoff to unloaded owners', () => {
    const methods = new Function('staleSyntheticAgentIds', 'getSessionCutoffMs', `return { ${action('_cleanStaleChildSynthetics(')} }`)(staleSyntheticAgentIds, s => s.cutoff)
    const store = storeWithFetch([])
    Object.assign(store, methods)
    applyAgentSnapshot(store.localState, 'root', [agent], beginAgentFetch(store.localState, 'root'))
    store.processStates.child = { synthetic: true, started_at: Date.parse(agent.started_at) / 1000 }
    store._cleanStaleChildSynthetics({ id: 'launcher', parent_session_id: 'root', cutoff: Date.parse('2026-09-07T01:01:00Z') })
    assert.ok(store.processStates.child)
    store._cleanStaleChildSynthetics({ id: 'root', cutoff: Date.parse('2026-09-07T01:01:00Z') })
    assert.equal(store.processStates.child, undefined)
})

test('real fetch action keeps first-link wake evidence and restores synthetic activity', async () => {
    let resolve
    const store = storeWithFetch(new Promise(done => { resolve = done }))
    const work = store.fetchSubagentsState('p', 'root')
    markAgentIdle(store.localState, 'child', null)
    resolve([{ ...agent, running: false, agent_stopped_at: '2026-09-07T01:01:00Z' }])
    await work
    assert.equal(store.localState.agentLinkIndex.child.agentStoppedAt, null)
    assert.ok(store.processStates.child?.synthetic)
})
