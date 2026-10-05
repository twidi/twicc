import test from 'node:test'
import assert from 'node:assert/strict'
import { runStreamRetirementScenario } from './streamRetirementHarness.js'

function adapter(overrides = {}) {
    const calls = []
    const final = { visualRows: [101], domRows: [101], persistedText: 'Complete fixture text.',
        visibleText: 'Complete fixture text.', domObserved: true, retiredPairs: [{ streamingLineNum: -100, realLineNum: 101 }],
        hookPairs: [{ streamingLineNum: -100, realLineNum: 101 }], detailOpen: true, domDetailOpen: true,
        groupTransferred: true, geometry: { bottomGap: 0, anchor: '10', anchorOffset: 0 }, errors: [] }
    const result = { calls, final,
        async prepare() { calls.push('prepare'); return { text: 'Complete fixture text.', realLine: 101, streamingLine: -100 } },
        async start() { calls.push('start') }, async feed() { calls.push('feed') },
        async open() { calls.push('open') }, async position() { calls.push('position'); return final.geometry },
        async insert() { calls.push('insert') }, async end() { calls.push('end') },
        async lateStart() { calls.push('lateStart') }, async settle() {},
        async observe() { return final },
        async beginRest() { calls.push('beginRest') },
        async pending() { calls.push('pending'); return { metadataRequests: 1, contentRequests: 1,
            retainedText: final.persistedText, detailOpen: true, groupOpen: true } },
        async fail() { calls.push('fail') }, async retry() { calls.push('retry') },
        async resolve() { calls.push('resolve') }, ...overrides }
    return result
}
for (const [order, sequence] of Object.entries({
    'item-before-end': ['start', 'feed', 'open', 'position', 'insert', 'end'],
    'end-before-item': ['start', 'feed', 'open', 'position', 'end', 'insert'],
    'item-before-start': ['insert', 'start', 'end', 'position'],
})) test(`${order} drives the required controls`, async () => {
    const a = adapter()
    const report = await runStreamRetirementScenario(a, { order, blockType: 'thinking' })
    assert.equal(report.status, 'passed')
    assert.deepEqual(a.calls, ['prepare', ...sequence, 'lateStart'])
})
for (const [name, change] of Object.entries({
    'retained synthetic visual row': { visualRows: [101, -100] },
    'retained synthetic DOM row': { domRows: [101, -100] },
    'wrong persisted text': { persistedText: 'Wrong' },
    'missing DOM observation': { domObserved: false },
    'closed thinking detail': { domDetailOpen: false },
    'missing scroll hook evidence': { hookPairs: [] },
})) test(`${name} fails acceptance`, async () => {
    const a = adapter(); Object.assign(a.final, change)
    const report = await runStreamRetirementScenario(a, { blockType: 'thinking', timeoutMs: 15 })
    assert.equal(report.status, 'failed/inconclusive')
})
test('timeout never reports success', async () => {
    const a = adapter({ observe: () => new Promise(() => {}) })
    const report = await runStreamRetirementScenario(a, { timeoutMs: 10 })
    assert.equal(report.status, 'failed/inconclusive'); assert.equal(report.timedOut, true)
})
test('failed REST retry stays deferred until retry resolution', async () => {
    let release, reached
    const reachedRetry = new Promise(resolve => { reached = resolve })
    const deferred = new Promise(resolve => { release = resolve })
    const a = adapter({ async retry() { a.calls.push('retry'); reached(); await deferred } })
    const result = runStreamRetirementScenario(a, { blockType: 'thinking', loading: 'failed-rest-retry' })
    await reachedRetry
    assert.equal(a.calls.includes('resolve'), false)
    release(); const report = await result
    assert.equal(report.status, 'passed')
    assert.deepEqual(a.calls.slice(-8), ['beginRest', 'pending', 'fail', 'pending', 'retry', 'pending', 'resolve', 'lateStart'])
})
test('REST without both route observations fails', async () => {
    const a = adapter({ async pending() { return { metadataRequests: 0, contentRequests: 1 } } })
    const report = await runStreamRetirementScenario(a, { loading: 'slow-rest', timeoutMs: 15 })
    assert.equal(report.status, 'failed/inconclusive'); assert.equal(a.calls.includes('resolve'), false)
})

test('persisted text rejection identifies the persisted source', async () => {
    const a = adapter(); a.final.persistedText = 'Wrong persisted source'
    const report = await runStreamRetirementScenario(a, { timeoutMs: 15 })
    assert.match(report.acceptanceError, /Persisted text differs/)
})
test('reading-above requires the mounted scroll hook and preserves its anchor', async () => {
    const a = adapter(); a.final.scrollHookReads = [-100]
    const report = await runStreamRetirementScenario(a, { position: 'reading-above' })
    assert.equal(report.status, 'passed')
    a.final.scrollHookReads = []
    const missing = await runStreamRetirementScenario(a, { position: 'reading-above', timeoutMs: 15 })
    assert.equal(missing.status, 'failed/inconclusive')
    assert.match(missing.acceptanceError, /Mounted scroll retirement hook/)
})
test('failure state loses retained text before Retry and fails', async () => {
    let failed = false
    const a = adapter({ async fail() { failed = true }, async pending() {
        return { metadataRequests: 1, contentRequests: 1, retainedText: failed ? '' : 'Complete fixture text.', detailOpen: true, groupOpen: true }
    } })
    const report = await runStreamRetirementScenario(a, { blockType: 'thinking', loading: 'failed-rest-retry' })
    assert.equal(report.status, 'failed/inconclusive')
    assert.equal(a.calls.includes('retry'), false)
})
