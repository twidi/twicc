import test from 'node:test'
import assert from 'node:assert/strict'
import { effect, isReactive, reactive, toRaw } from 'vue'
import { getRootSessions } from './rootSessions.js'
import { observeStageRuns } from './stageRuns.js'
import { buildSidebarState, createShippedData, progressMessage, startupPhase } from '../../tests/helpers/sidebarFixture.js'

const root = (id) => ({ id, project_id: 'p', parent_session_id: null, mtime: 1 })
const subagent = (id, parent) => ({ id, project_id: 'p', parent_session_id: parent, mtime: 1 })
const ids = (sessions) => sessions.map(s => s.id)

/** A small owner: roots a, b, c with a subagent inserted after a. */
function buildOwner() {
    return reactive({
        sessions: { a: root('a'), 'a-sa0': subagent('a-sa0', 'a'), b: root('b'), c: root('c') },
        startupProgress: {},
    })
}

/** Keep the index subscribed, like a sidebar consumer, and count its runs. */
function subscribe(owner) {
    let current = null
    const runner = effect(() => { current = getRootSessions(owner) })
    let runs = 0
    const stopObserving = observeStageRuns(name => { if (name === 'rootSessions') runs++ })
    return {
        current: () => current,
        runs: () => runs,
        stop() { stopObserving(); runner.effect.stop() },
    }
}

test('roots keep insertion order and exclude subagents', () => {
    const owner = buildOwner()
    assert.deepEqual(ids(getRootSessions(owner)), ['a', 'b', 'c'])
    startupPhase(owner, true)
    assert.deepEqual(ids(getRootSessions(owner)), ['a', 'b', 'c'])
})

test('a subagent add or removal keeps the same array instance', () => {
    for (const active of [false, true]) {
        const owner = buildOwner()
        startupPhase(owner, active)
        const probe = subscribe(owner)
        const before = probe.current()
        owner.sessions['b-sa0'] = subagent('b-sa0', 'b')
        assert.strictEqual(getRootSessions(owner), before, `add, startup ${active}`)
        delete owner.sessions['a-sa0']
        assert.strictEqual(getRootSessions(owner), before, `removal, startup ${active}`)
        // Positive control: both writes reran the stage, so the identity comes from retention.
        assert.equal(probe.runs(), 2, `runs, startup ${active}`)
        probe.stop()
    }
})

test('a root add returns a new array with the root at the end', () => {
    for (const active of [false, true]) {
        const owner = buildOwner()
        startupPhase(owner, active)
        const probe = subscribe(owner)
        const before = probe.current()
        owner.sessions.d = root('d')
        const after = getRootSessions(owner)
        assert.notStrictEqual(after, before, `startup ${active}`)
        assert.deepEqual(ids(after), ['a', 'b', 'c', 'd'], `startup ${active}`)
        assert.strictEqual(probe.current(), after, `startup ${active}`)
        probe.stop()
    }
})

test('a root removal returns a new array without the root', () => {
    for (const active of [false, true]) {
        const owner = buildOwner()
        startupPhase(owner, active)
        const probe = subscribe(owner)
        const before = probe.current()
        delete owner.sessions.b
        const after = getRootSessions(owner)
        assert.notStrictEqual(after, before, `startup ${active}`)
        assert.deepEqual(ids(after), ['a', 'c'], `startup ${active}`)
        assert.strictEqual(probe.current(), after, `startup ${active}`)
        assert.equal(probe.runs(), 1, `runs, startup ${active}`)
        probe.stop()
    }
})

test('roots are reactive records also during startup', () => {
    const owner = buildOwner()
    startupPhase(owner, true)
    const roots = getRootSessions(owner)
    assert.equal(roots.length, 3)
    for (const session of roots) {
        assert.ok(isReactive(session), session.id)
        assert.strictEqual(session, owner.sessions[session.id], session.id)
    }
    // The same records after startup: the phase end keeps the array.
    startupPhase(owner, false)
    assert.strictEqual(getRootSessions(owner), roots)
})

test('during startup parent_session_id is read without tracking', () => {
    const owner = buildOwner()
    // Own setup: a counting accessor on the raw subagent record, before the observer.
    const raw = toRaw(owner.sessions)['a-sa0']
    let parent = raw.parent_session_id
    let reads = 0
    Object.defineProperty(raw, 'parent_session_id', {
        configurable: true, enumerable: true,
        get() { reads++; return parent },
        set(value) { parent = value },
    })
    startupPhase(owner, true)
    const probe = subscribe(owner)
    // The run read the field (so the probe can fail), but did not track it.
    assert.ok(reads > 0)
    const before = probe.current()
    const runsBefore = probe.runs()
    owner.sessions['a-sa0'].parent_session_id = null
    assert.equal(probe.runs(), runsBefore)
    assert.strictEqual(getRootSessions(owner), before)
    assert.deepEqual(ids(before), ['a', 'b', 'c'])
    // Refresh guarantee: the next progress message shows the untracked change.
    progressMessage(owner)
    assert.equal(probe.runs(), runsBefore + 1)
    assert.deepEqual(ids(probe.current()), ['a', 'a-sa0', 'b', 'c'])
    // Positive control: a root add runs the stage.
    owner.sessions.d = root('d')
    assert.equal(probe.runs(), runsBefore + 2)
    assert.deepEqual(ids(probe.current()), ['a', 'a-sa0', 'b', 'c', 'd'])
    probe.stop()
})

test('outside startup a subagent parent change reruns the index', () => {
    const owner = buildOwner()
    const probe = subscribe(owner)
    owner.sessions['a-sa0'].parent_session_id = null
    assert.equal(probe.runs(), 1)
    assert.deepEqual(ids(probe.current()), ['a', 'a-sa0', 'b', 'c'])
    // A field the index does not read reruns nothing.
    owner.sessions.b.mtime = 2
    assert.equal(probe.runs(), 1)
    probe.stop()
})

test('the shipped data facade exposes the index of its state', () => {
    const state = buildSidebarState({ roots: 20 })
    const { data } = createShippedData(state)
    assert.strictEqual(data.rootSessions, getRootSessions(state))
    assert.equal(data.rootSessions.length, 20)
    assert.ok(data.rootSessions.every(session => !session.parent_session_id))
})
