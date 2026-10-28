import test from 'node:test'
import assert from 'node:assert/strict'
import { effect, isReactive, toRaw } from 'vue'
import { getUnreadMemos } from './unreadMemos.js'
import { getProjectActivityIndex } from './projectProcessActivity.js'
import { observeStageRuns } from './stageRuns.js'
import { createBaselineActivityIndex } from '../../tests/helpers/sidebarBaseline/projectInputs.js'
import { FIXTURE_IDS as ID, FIXTURE_UPDATE_SEQUENCE, applyStep, buildSidebarState, createBaselineData,
    createShippedData, startupPhase, tick } from '../../tests/helpers/sidebarFixture.js'

// Unread index over the roots through the per-session memos
// (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.5 and 7.1 #5, #6, #9, #10).
// Every test except the parity test reads `summary(['p0'])` and
// `summary(<visible ids of w>)`.

const UNREAD_FIELDS = ['unreadCount', 'unreadBackgroundCount', 'unreadShellCount']
const stepNamed = (name) => FIXTURE_UPDATE_SEQUENCE.find(step => step.name === name)
const unreadOf = (summary) => UNREAD_FIELDS.map(key => summary[key])

function setup(state = buildSidebarState({ roots: 40 })) {
    const shipped = createShippedData(state)
    return { state, ...shipped, index: getProjectActivityIndex(shipped.data) }
}

/** The two scopes of the Task 4 tests, read live (`w` follows its visible projects). */
function scopesOf(workspaces) {
    return { p0: ['p0'], 'ws:w': workspaces.getVisibleProjectIds('w') }
}

/**
 * Keep both scope summaries (through the facade getter of the project badges)
 * and the total (the favicon) subscribed, and count the stage runs per name
 * from now on.
 */
function subscribe({ data, index, workspaces }) {
    let summaries = {}
    let totalUnread = 0
    const runner = effect(() => {
        const next = {}
        for (const [scope, projectIds] of Object.entries(scopesOf(workspaces))) {
            next[scope] = data.getProjectActivitySummary(projectIds, summaries[scope])
        }
        summaries = next
        totalUnread = index.totalUnread()
    })
    const runs = {}
    const stopObserving = observeStageRuns(name => { runs[name] = (runs[name] || 0) + 1 })
    return {
        summaries: () => summaries,
        totalUnread: () => totalUnread,
        runs: (name) => runs[name] || 0,
        reset() { for (const name of Object.keys(runs)) delete runs[name] },
        stop() { stopObserving(); runner.effect.stop() },
    }
}

/** Install a counting getter on `field` of every subagent record. */
function countSubagentReads(state, fields) {
    let reads = 0
    for (const session of Object.values(toRaw(state.sessions))) {
        if (!session.parent_session_id) continue
        for (const field of fields) {
            let value = session[field]
            Object.defineProperty(session, field, {
                configurable: true, enumerable: true,
                get() { reads++; return value },
                set(next) { value = next },
            })
        }
    }
    return () => reads
}

test('a timestamp change that does not flip the result does not rerun unreadCounts', () => {
    const context = setup()
    const { state } = context
    // unread-1 (p4) is an out-of-scope root without a process that the fixture makes unread.
    assert.equal(getUnreadMemos(state).unread(state.sessions[ID.unread1]), true)
    const probe = subscribe(context)
    const before = probe.summaries()
    assert.equal(probe.totalUnread(), 2)

    tick(state, ID.unread1)
    assert.equal(probe.runs('memo:unread'), 1, 'the timestamps reach the memo')
    assert.equal(probe.runs('unread:counts'), 0)
    assert.deepEqual(probe.summaries(), before)
    for (const scope of Object.keys(before)) assert.strictEqual(probe.summaries()[scope], before[scope], scope)
    assert.equal(probe.totalUnread(), 2)

    // Positive control: a root add reruns the index.
    applyStep(state, stepNamed('root add'))
    assert.ok(probe.runs('unread:counts') >= 1)
    probe.stop()
})

test('an open tick of an assistant_turn session does not rerun unreadCounts', () => {
    const context = setup()
    const { state, data } = context
    // The open session's unread content flips on, then off, inside one tick
    // (`last_viewed_at` follows); its `unread` memo stays false (assistant_turn).
    let content = null
    const contentReader = effect(() => { content = data.hasUnreadContentMemo(state.sessions[ID.processAssistant]) })
    const probe = subscribe(context)
    const before = probe.summaries()

    tick(state, ID.processAssistant, { open: true })
    assert.ok(probe.runs('memo:unreadContent') >= 2, 'the content memo follows both writes')
    assert.equal(content, false)
    assert.equal(probe.runs('unread:counts'), 0)
    for (const scope of Object.keys(before)) assert.strictEqual(probe.summaries()[scope], before[scope], scope)

    // Positive control: a root add reruns the index.
    applyStep(state, stepNamed('root add'))
    assert.ok(probe.runs('unread:counts') >= 1)
    probe.stop()
    contentReader.effect.stop()
})

test('a flip updates unreadCount and the background and shell counts', () => {
    const context = setup()
    const { state } = context
    const probe = subscribe(context)
    const before = Object.fromEntries(Object.entries(probe.summaries()).map(([scope, s]) => [scope, unreadOf(s)]))

    // Both p0 user_turn sessions are read in the fixture; a tick flips them to unread.
    tick(state, ID.processShell)
    assert.ok(probe.runs('unread:counts') >= 1)
    for (const [scope, [count, background, shells]] of Object.entries(before)) {
        assert.deepEqual(unreadOf(probe.summaries()[scope]), [count + 1, background + 1, shells + 1], `shell ${scope}`)
    }
    tick(state, ID.processCron)
    for (const [scope, [count, background, shells]] of Object.entries(before)) {
        assert.deepEqual(unreadOf(probe.summaries()[scope]), [count + 2, background + 2, shells + 1], `cron ${scope}`)
    }
    // Back to read: the counts return to their start values.
    state.sessions[ID.processShell].last_viewed_at = state.sessions[ID.processShell].last_new_content_at
    state.sessions[ID.processCron].last_viewed_at = state.sessions[ID.processCron].last_new_content_at
    for (const [scope, values] of Object.entries(before)) {
        assert.deepEqual(unreadOf(probe.summaries()[scope]), values, `read ${scope}`)
    }
    probe.stop()
})

test('an unread flip reads no field of a subagent', () => {
    const context = setup()
    const { state } = context
    // `hidden` (the first field `isSessionUnread` reads) and `pinned` are probed
    // from the first evaluation on: a memo built for a subagent would read
    // `hidden` once and then answer from its cache. `parent_session_id` is
    // probed after it: the root index reads it on its first run.
    const fieldReads = countSubagentReads(state, ['hidden', 'pinned'])
    const probe = subscribe(context)
    const before = Object.fromEntries(Object.entries(probe.summaries()).map(([scope, s]) => [scope, s.unreadCount]))
    const parentReads = countSubagentReads(state, ['parent_session_id'])
    const reads = () => fieldReads() + parentReads()

    tick(state, ID.p0Recent)  // a read p0 root: the tick flips it to unread
    for (const [scope, count] of Object.entries(before)) {
        assert.equal(probe.summaries()[scope].unreadCount, count + 1, scope)
    }
    state.sessions[ID.p0Recent].last_viewed_at = state.sessions[ID.p0Recent].last_new_content_at
    for (const [scope, count] of Object.entries(before)) {
        assert.equal(probe.summaries()[scope].unreadCount, count, scope)
    }
    // Positive control: both flips reran the index.
    assert.ok(probe.runs('unread:counts') >= 2)
    assert.equal(reads(), 0)
    probe.stop()
})

test('a subagent add or removal does not rerun unreadCounts', () => {
    const context = setup()
    const { state } = context
    const probe = subscribe(context)
    const before = probe.summaries()

    applyStep(state, stepNamed('subagent add'))
    applyStep(state, stepNamed('subagent removal'))
    assert.ok(probe.runs('rootSessions') >= 2, 'both writes reach the root index')
    assert.equal(probe.runs('unread:counts'), 0)
    for (const scope of Object.keys(before)) assert.strictEqual(probe.summaries()[scope], before[scope], scope)

    // Positive control: a root add reruns the index.
    applyStep(state, stepNamed('root add'))
    assert.ok(probe.runs('unread:counts') >= 1)
    probe.stop()
})

test('a memo first evaluated with a raw record during startup still updates after startup', () => {
    const context = setup()
    const { state, data } = context
    const memos = getUnreadMemos(state)
    const raw = toRaw(state.sessions[ID.p0Recent])
    startupPhase(state, true)
    assert.ok(data.isStartupInProgress)
    assert.ok(!isReactive(raw))
    assert.equal(memos.unread(raw), false)
    assert.equal(memos.unreadContent(raw), false)
    startupPhase(state, false)

    const probe = subscribe(context)
    const before = Object.fromEntries(Object.entries(probe.summaries()).map(([scope, s]) => [scope, s.unreadCount]))
    tick(state, ID.p0Recent)  // a read p0 root: the tick flips it to unread
    assert.equal(memos.unread(raw), true)
    assert.equal(memos.unreadContent(raw), true)
    assert.equal(memos.unread(state.sessions[ID.p0Recent]), true, 'the reactive lookup finds the same memo')
    for (const [scope, count] of Object.entries(before)) {
        assert.equal(probe.summaries()[scope].unreadCount, count + 1, scope)
    }
    probe.stop()
})

test('removing a session drops it from the counts at once', () => {
    const context = setup()
    const { state } = context
    // Own setup: p0-recent becomes unread before the observer.
    tick(state, ID.p0Recent)
    const probe = subscribe(context)
    const before = Object.fromEntries(Object.entries(probe.summaries()).map(([scope, s]) => [scope, s.unreadCount]))
    for (const count of Object.values(before)) assert.ok(count >= 1)

    delete state.sessions[ID.p0Recent]
    for (const [scope, count] of Object.entries(before)) {
        assert.equal(probe.summaries()[scope].unreadCount, count - 1, scope)
    }
    assert.ok(probe.runs('unread:counts') >= 1)
    probe.stop()
})

test('the summary equals the frozen baseline index along an update sequence', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const shipped = getProjectActivityIndex(createShippedData(state).data)
    const baseline = createBaselineActivityIndex(createBaselineData(state).data)
    const read = (index) => ({
        projects: Object.fromEntries(Object.keys(state.projects).map(id => [id, index.summary([id])])),
        totalUnread: index.totalUnread(),
    })
    // Keep both indexes subscribed, as in the app.
    let latest = {}
    const runner = effect(() => { latest = { shipped: read(shipped), baseline: read(baseline) } })
    assert.deepEqual(latest.shipped, latest.baseline, 'initial state')
    for (const step of FIXTURE_UPDATE_SEQUENCE) {
        applyStep(state, step)
        assert.deepEqual(read(shipped), read(baseline), step.name)
        assert.deepEqual(latest.shipped, latest.baseline, `${step.name} (subscribed)`)
    }
    runner.effect.stop()
})
