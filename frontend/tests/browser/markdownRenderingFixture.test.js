import test from 'node:test'
import assert from 'node:assert/strict'

// Missing deadlines, wrong counters, and final-source substitutions must fail these checks.
test('feed uses 100 immutable snapshots at fixed deadlines despite slow feeds', async () => {
    const { feedMarkdownSnapshots } = await import('./markdownRenderingHarness.js')
    let now = 0
    const values = []
    const clock = { now: () => now, sleep: async delay => { now += delay } }
    const evidence = await feedMarkdownSnapshots(index => ({ source: `snapshot ${index}`, theme: 'default', slashTag: false }),
        tuple => { values.push(tuple.source); now += 3 }, clock)
    assert.equal(values.length, 100)
    assert.equal(values[99], 'snapshot 99')
    assert.equal(evidence[0].deadline, 0)
    assert.equal(evidence[99].deadline, 1980)
    assert.equal(evidence[99].at, 1980)
})
test('operation metrics record active overlap, actual sources, and successful tuples separately', async () => {
    const { createMarkdownMetrics } = await import('./markdownRenderingHarness.js')
    let now = 12
    const metrics = createMarkdownMetrics(() => now)
    const tuple = { source: 'A', theme: 'dark', slashTag: true }
    const first = metrics.sink('document-start', tuple)
    tuple.source = 'mutated'
    now = 16
    const second = metrics.sink('document-start', { source: 'B', theme: 'default', slashTag: false })
    metrics.sink('parse', 'B')
    metrics.sink('block', { source: 'B block' })
    metrics.sink('mermaid', { source: 'graph TD; A-->B' })
    metrics.sink('document-finish', first)
    metrics.sink('commit', { source: 'B', theme: 'default', slashTag: false })
    metrics.sink('emit', { source: 'B', theme: 'default', slashTag: false })
    metrics.sink('document-finish', second)
    assert.equal(metrics.report.active, 0)
    assert.equal(metrics.report.peakActive, 2)
    assert.equal(metrics.report.starts[0].source, 'A')
    assert.deepEqual(metrics.report.parsed.map(entry => entry.source), ['B'])
    assert.equal(metrics.report.blocks.length, 1)
    assert.equal(metrics.report.mermaid.length, 1)
    assert.equal(metrics.report.commits[0].source, 'B')
    assert.equal(metrics.report.emits[0].source, 'B')
})
test('source scenarios have independent final expectations and empty final remains empty', async () => {
    const { markdownScenarios } = await import('./markdownRenderingHarness.js')
    assert.deepEqual(Object.keys(markdownScenarios), ['paragraphs', 'code', 'mermaid', 'references', 'slash', 'empty', 'compatibility'])
    assert.equal(markdownScenarios.empty.snapshot(99).source, '')
    assert.notEqual(markdownScenarios.empty.snapshot(98).source, '')
    assert.match(markdownScenarios.references.snapshot(99).source, /https:\/\/example.com\/latest "latest title"/)
    assert.equal(markdownScenarios.slash.snapshot(99).slashTag, true)
    assert.equal(markdownScenarios.slash.snapshot(98).slashTag, false)
})
test('finite latest-render wait fails without synthesizing completion', async () => {
    const { waitForMarkdownCondition } = await import('./markdownRenderingHarness.js')
    let now = 0, checks = 0
    await assert.rejects(waitForMarkdownCondition(() => { checks++; return false },
        { timeout: 40, clock: { now: () => now, sleep: async delay => { now += delay } } }), /Timed out/)
    assert.ok(checks > 1)
    assert.equal(now, 40)
})
