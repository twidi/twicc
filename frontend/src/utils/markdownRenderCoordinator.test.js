import test from 'node:test'
import assert from 'node:assert/strict'
import { createMarkdownRenderCoordinator, MARKDOWN_RENDER_CANCELLED } from './markdownRenderCoordinator.js'

const input = (source, theme = 'dark', slashTag = false) => ({ source, theme, slashTag })
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function harness(overrides = {}) {
    const starts = [], commits = [], errors = [], states = [], scheduled = [], operations = []
    let active = 0, peak = 0
    const coordinator = createMarkdownRenderCoordinator({
        schedule: callback => scheduled.push(callback),
        render: (snapshot, ownership) => {
            starts.push(snapshot)
            active++
            peak = Math.max(peak, active)
            return new Promise((resolve, reject) => operations.push({
                ownership,
                resolve: result => { active--; resolve(result ?? snapshot.source) },
                reject: error => { active--; reject(error) },
            }))
        },
        commit: (result, snapshot, ownership) => { assert.equal(ownership.isCurrent(), true); commits.push(snapshot) },
        onError: error => errors.push(error),
        onState: state => states.push(state.rendering),
        ...overrides,
    })
    const drain = () => { assert.equal(scheduled.length, 1); scheduled.shift()() }
    return { coordinator, starts, commits, errors, states, scheduled, operations, drain, peak: () => peak }
}

test('holds one active operation and replaces pending input', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    for (const source of ['B', 'C', 'D']) h.coordinator.request(input(source))
    assert.deepEqual(h.starts, [input('A')]); assert.equal(h.scheduled.length, 0)
    h.operations[0].resolve(); await settle(); h.drain()
    assert.deepEqual(h.starts, [input('A'), input('D')]); assert.deepEqual(h.commits, [])
    h.operations[1].resolve(); await settle()
    assert.deepEqual(h.commits, [input('D')]); assert.equal(h.peak(), 1)
    assert.deepEqual(h.states, [true, false])
})

test('A to B to A never revives the original revision', async () => {
    const h = harness(); h.coordinator.setEligible(true); h.coordinator.request(input('A')); h.drain()
    h.coordinator.request(input('B')); h.coordinator.request(input('A')); h.coordinator.request(input('A'))
    assert.equal(h.operations[0].ownership.isCurrent(), false)
    h.operations[0].resolve(); await settle(); h.drain(); h.operations[1].resolve(); await settle()
    assert.deepEqual(h.starts, [input('A'), input('A')]); assert.deepEqual(h.commits, [input('A')])
})

test('snapshots and coalesces source theme slash changes in one microtask', async () => {
    const h = harness(); h.coordinator.setEligible(true)
    const value = input('A'); h.coordinator.request(value); value.source = 'mutated'
    h.coordinator.request(input('B')); h.coordinator.request(input('B', 'default'))
    h.coordinator.request(input('B', 'default', true)); h.coordinator.request(input('B', 'default', true))
    h.drain(); assert.deepEqual(h.starts, [input('B', 'default', true)])
    h.coordinator.request(input('B', 'default', true)); assert.equal(h.operations[0].ownership.isCurrent(), true)
    h.operations[0].resolve(); await settle(); h.coordinator.request(input('B', 'default', true))
    assert.equal(h.scheduled.length, 0)
})

for (const showBeforeSettlement of [true, false]) test(`hidden updates resume latest; show before settlement=${showBeforeSettlement}`, async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.coordinator.setEligible(false); h.coordinator.request(input('B')); h.coordinator.request(input('C'))
    assert.equal(h.states.at(-1), false); assert.equal(h.scheduled.length, 0)
    if (showBeforeSettlement) h.coordinator.setEligible(true)
    h.operations[0].resolve(); await settle(); assert.deepEqual(h.commits, [])
    if (!showBeforeSettlement) { assert.equal(h.scheduled.length, 0); h.coordinator.setEligible(true) }
    h.drain(); h.operations[1].resolve(); await settle()
    assert.deepEqual(h.starts, [input('A'), input('C')]); assert.deepEqual(h.commits, [input('C')])
})

test('completed identical content resumes without work; invalidated unfinished content rerenders', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.operations[0].resolve(); await settle(); h.coordinator.setEligible(false); h.coordinator.setEligible(true)
    assert.equal(h.scheduled.length, 0)
    h.coordinator.request(input('B')); h.drain(); h.coordinator.setEligible(false); h.coordinator.request(input('A'))
    h.operations[1].resolve(); await settle(); h.coordinator.setEligible(true); h.drain()
    assert.deepEqual(h.starts, [input('A'), input('B'), input('A')])
})

test('dispose is idempotent and consumes obsolete rejection', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.coordinator.dispose(); h.coordinator.dispose(); h.coordinator.request(input('B')); h.coordinator.setEligible(true)
    h.operations[0].reject(new Error('obsolete')); await settle()
    assert.deepEqual(h.errors, []); assert.deepEqual(h.commits, []); assert.equal(h.scheduled.length, 0)
    assert.deepEqual(h.states, [true, false])
})

test('obsolete failures are silent and latest work progresses', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.coordinator.request(input('B')); h.operations[0].reject(new Error('obsolete')); await settle(); h.drain()
    h.operations[1].resolve(); await settle(); assert.deepEqual(h.errors, []); assert.deepEqual(h.commits, [input('B')])
})

test('throwing error sink releases current failure without retry loop', async () => {
    const reported = []; const h = harness({ onError: error => { reported.push(error); throw new Error('sink') } })
    h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    const error = new Error('render'); h.operations[0].reject(error); await settle()
    assert.deepEqual(reported, [error]); assert.equal(h.states.at(-1), false)
    h.coordinator.request(input('A')); assert.equal(h.scheduled.length, 0)
    h.coordinator.setEligible(false); h.coordinator.setEligible(true); h.drain()
    h.operations[1].resolve(); await settle(); assert.deepEqual(h.commits, [input('A')])
})

test('current cancellation ends unfinished state without report commit or automatic drain', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.operations[0].resolve(MARKDOWN_RENDER_CANCELLED); await settle()
    assert.deepEqual(h.commits, []); assert.deepEqual(h.errors, []); assert.deepEqual(h.states, [true, false])
    h.coordinator.request(input('A')); assert.equal(h.scheduled.length, 0)
    h.coordinator.request(input('B')); h.drain(); h.operations[1].resolve(); await settle()
    assert.deepEqual(h.starts, [input('A'), input('B')])
})

for (const action of ['request', 'hide', 'dispose', 'throw']) test(`reentrant commit ${action} preserves ownership`, async () => {
    let h; h = harness({ commit: () => {
        if (action === 'request') h.coordinator.request(input('B'))
        if (action === 'hide') h.coordinator.setEligible(false)
        if (action === 'dispose') h.coordinator.dispose()
        if (action === 'throw') throw new Error('commit')
    } })
    h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain(); h.operations[0].resolve(); await settle()
    if (action === 'request') { assert.deepEqual(h.states, [true]); h.drain(); assert.deepEqual(h.starts, [input('A'), input('B')]) }
    else { assert.equal(h.states.at(-1), false); assert.equal(h.scheduled.length, 0) }
    assert.equal(h.errors.length, action === 'throw' ? 1 : 0)
})

test('reentrant state callback changes input before the queued drain', async () => {
    let h; h = harness({ onState: ({ rendering }) => { h.states.push(rendering); if (rendering) h.coordinator.request(input('B')) } })
    h.coordinator.setEligible(true); h.coordinator.request(input('A')); h.drain()
    assert.deepEqual(h.starts, [input('B')]); h.operations[0].resolve(); await settle()
    assert.deepEqual(h.states, [true, false])
})

for (const action of ['hide', 'dispose']) test(`reentrant state ${action} prevents a start`, () => {
    let h; h = harness({ onState: ({ rendering }) => {
        h.states.push(rendering)
        if (rendering) {
            if (action === 'hide') h.coordinator.setEligible(false)
            else h.coordinator.dispose()
        }
    } })
    h.coordinator.request(input('A')); h.coordinator.setEligible(true)
    assert.deepEqual(h.states, [true, false]); assert.deepEqual(h.starts, []); assert.equal(h.scheduled.length, 0)
})

test('synchronous render failure releases slot and reports once', async () => {
    const failure = new Error('synchronous render')
    const h = harness({ render: () => { throw failure } })
    h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain(); await settle()
    assert.deepEqual(h.errors, [failure]); assert.deepEqual(h.states, [true, false]); assert.equal(h.scheduled.length, 0)
})

test('hidden failure is consumed and latest hidden snapshot resumes', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.coordinator.setEligible(false); h.coordinator.request(input('B')); h.operations[0].reject(new Error('hidden'))
    await settle(); assert.deepEqual(h.errors, []); assert.equal(h.scheduled.length, 0)
    h.coordinator.setEligible(true); h.drain(); assert.deepEqual(h.starts, [input('A'), input('B')])
})

test('current cancellation retries only after eligibility transitions', async () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true); h.drain()
    h.operations[0].resolve(MARKDOWN_RENDER_CANCELLED); await settle()
    h.coordinator.setEligible(false); h.coordinator.setEligible(true); h.drain()
    h.operations[1].resolve(); await settle()
    assert.deepEqual(h.starts, [input('A'), input('A')]); assert.deepEqual(h.commits, [input('A')])
})

test('queued drains become inert after disposal', () => {
    const h = harness(); h.coordinator.request(input('A')); h.coordinator.setEligible(true)
    h.coordinator.dispose(); h.drain()
    assert.deepEqual(h.starts, []); assert.deepEqual(h.states, [true, false]); assert.equal(h.scheduled.length, 0)
})

test('default scheduling uses a microtask', async () => {
    const starts = [], commits = []
    const coordinator = createMarkdownRenderCoordinator({
        render: async snapshot => { starts.push(snapshot); return snapshot.source },
        commit: result => commits.push(result),
    })
    coordinator.request(input('A')); coordinator.setEligible(true); coordinator.request(input('B'))
    assert.deepEqual(starts, []); await settle()
    assert.deepEqual(starts, [input('B')]); assert.deepEqual(commits, ['B'])
})
