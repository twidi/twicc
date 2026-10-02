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
test('latest counted acceptance rejects stale emits and uncleared events', async () => {
    const { latestMarkdownRenderMatches, resetMarkdownPanelEvents } = await import('./markdownRenderingHarness.js')
    const latest = { source: 'latest', theme: 'dark', slashTag: true }
    const panel = { lastEmitted: latest, renderedEvents: [{ source: 'earlier' }], metrics: { report: {
        active: 0, commits: [latest], emits: [{ ...latest, source: 'obsolete' }],
    } } }
    assert.equal(latestMarkdownRenderMatches(panel, latest, true), false)
    panel.metrics.report.emits.push(latest)
    assert.equal(latestMarkdownRenderMatches(panel, latest, true), true)
    resetMarkdownPanelEvents(panel)
    assert.equal(panel.lastEmitted, null)
    assert.deepEqual(panel.renderedEvents, [])
    assert.equal(latestMarkdownRenderMatches(panel, latest, true), false)
})
test('restore acceptance waits for observed completion before any reveal', async () => {
    const { waitForMarkdownRestoreThenReveal } = await import('./markdownRenderingHarness.js')
    let now = 0, reveals = 0
    const clock = { now: () => now, sleep: async delay => { now += delay } }
    const result = await waitForMarkdownRestoreThenReveal(() => ({ observable: true, pending: now < 160, savedScrollTop: now < 160 ? 321 : null }),
        () => { reveals++; return 'actual reveal result' }, { clock, timeout: 1000 })
    assert.equal(now, 160)
    assert.equal(reveals, 1)
    assert.equal(result.reveal, 'actual reveal result')
    assert.equal(result.restoreChecks.at(-1).pending, false)
})
test('pending or unobservable restore stays inconclusive without revealing', async () => {
    const { waitForMarkdownRestoreThenReveal } = await import('./markdownRenderingHarness.js')
    let now = 0, reveals = 0
    const clock = { now: () => now, sleep: async delay => { now += delay } }
    const reveal = () => { reveals++ }
    await assert.rejects(waitForMarkdownRestoreThenReveal(() => ({ observable: true, pending: true }), reveal,
        { clock, timeout: 40 }), error => {
            assert.match(error.message, /Timed out/)
            assert.equal(error.restoreChecks.at(-1).pending, true)
            return true
        })
    await assert.rejects(waitForMarkdownRestoreThenReveal(() => ({ observable: false }), reveal,
        { clock, timeout: 40 }), /inconclusive/)
    assert.equal(reveals, 0)
})
test('actual KeepAlive control uses direct routes and waits before return scrollToKey', async () => {
    const { readFileSync } = await import('node:fs')
    const { waitForMarkdownRestoreThenReveal } = await import('./markdownRenderingHarness.js')
    const source = readFileSync(new URL('./markdownRenderingConversation.js', import.meta.url), 'utf8')
    const body = source.slice(source.indexOf('function restoreState()'), source.indexOf('\nasync function openThinking()'))
    let now = 0
    const reveals = [], routes = []
    const state = { streamSwapSavedScrollTop: null, scrollerRef: { suspended: false }, isAutoScrollingToBottom: false }
    const fixture = { ids: { mainId: 'main', otherId: 'other' }, store: { sessions: { main: { project_id: 'project' } } },
        navigate: async () => {},
        switchSession: () => { throw new Error('Legacy fixed-settle switch must not run') },
        router: { push: async route => { routes.push(route.params.sessionId); if (route.params.sessionId === 'main') state.streamSwapSavedScrollTop = 77 } },
    }
    const clock = { now: () => now, sleep: async delay => { now += delay; if (now >= 160) state.streamSwapSavedScrollTop = null } }
    const factory = new Function('fixture', 'findList', 'unref', 'seed', 'replace', 'reveal', 'nextTick', 'waitForMarkdownRestoreThenReveal',
        `${body}; return switchAndReturn`)
    const run = factory(fixture, () => ({ setupState: state }), value => value, () => {}, () => {},
        async marker => { reveals.push({ marker, at: now, pending: state.streamSwapSavedScrollTop }); return { scrollToKey: true } },
        async () => {}, (read, reveal) => waitForMarkdownRestoreThenReveal(read, reveal, { clock, timeout: 1000 }))
    const result = await run()
    assert.deepEqual(routes, ['other', 'main'])
    assert.equal(reveals[1].at, 160)
    assert.equal(reveals[1].pending, null)
    assert.equal(result.returned.reveal.scrollToKey, true)
})

test('composer control waits for production wa-textarea and edits its shadow textarea value without sending', async () => {
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(new URL('./markdownRenderingConversation.js', import.meta.url), 'utf8')
    const body = source.slice(source.indexOf('async function typeComposer()'), source.indexOf('\nasync function themeAndTools()'))
    let ready = false, selected = false, focused = false, ticks = 0
    const textarea = { value: 'old draft', focus() { focused = true },
        setSelectionRange(start, end) { assert.equal(start, 0); assert.equal(end, this.value.length); selected = true } }
    const host = { updateComplete: Promise.resolve().then(() => { ready = true }),
        shadowRoot: { querySelector(selector) { assert.equal(selector, 'textarea'); return ready ? textarea : null } } }
    const document = { querySelector(selector) { assert.equal(selector, '#conversation .message-input wa-textarea'); return host },
        execCommand(command, unused, text) { assert.equal(command, 'insertText'); assert.ok(focused && selected); textarea.value = text; return true } }
    const requests = [{ method: 'GET' }]
    const fixture = { navigate: async () => {}, snapshot: () => ({ requests }) }
    const wait = async check => { for (let i = 0; i < 5; i++) { if (check()) return; await Promise.resolve() } throw Error('readiness fails') }
    const run = new Function('fixture', 'document', 'nextTick', 'waitForMarkdownCondition', 'requireCheck',
        `${body}; return typeComposer`)(fixture, document, async () => { ticks++ }, wait,
        (value, message) => { if (!value) throw Error(message) })
    const result = await run()
    assert.equal(result.text, 'Fixture composer typing without send.')
    assert.equal(result.mutations, 0)
    assert.ok(ticks > 0)
    assert.deepEqual(requests, [{ method: 'GET' }])
    const edit = document.execCommand
    document.execCommand = (...args) => {
        const result = edit(...args)
        requests.push({ method: 'POST' })
        return result
    }
    await assert.rejects(run(), /Composer typing attempts a mutation request/)
})
