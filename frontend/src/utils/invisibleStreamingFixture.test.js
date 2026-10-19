import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolveLayout } from './layoutResolver.js'
import { DISPLAY_LEVEL } from '../constants.js'
const fixture = readFileSync(new URL('../../tests/browser/invisibleStreaming.js', import.meta.url), 'utf8')
const data = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
const seedStart = fixture.indexOf('function seedSession('), seedEnd = fixture.indexOf('\nseedSession(mainId)', seedStart)
const getterStart = data.indexOf('        getSessionTasks:'), getterEnd = data.indexOf('        },', getterStart) + 10
assert.ok(seedStart >= 0 && seedEnd > seedStart && getterStart >= 0 && getterEnd > getterStart)
const { getSessionTasks } = new Function(`return { ${data.slice(getterStart, getterEnd)} }`)()
for (const provider of ['claude_code', 'codex']) test(`${provider}: fixture seeds a real Tasks presence snapshot and rendered right-top dock`, () => {
    const store = { sessions: {}, localState: { sessions: {}, agentLoaded: {} }, processStates: {},
        initSessionItemsFromMetadata() {}, updateSessionItemsContent() {} }
    const seed = new Function('store', 'provider', 'projectId', 'DISPLAY_LEVEL', 'finalContent',
        `${fixture.slice(seedStart, seedEnd)}; return seedSession`)(store, provider, 'fixture-project', DISPLAY_LEVEL, () => ({}))
    for (const id of ['main', 'other', 'agent']) {
        seed(id)
        const tasks = getSessionTasks(store)(id)
        assert.ok(tasks, 'Missing tasks makes SessionView remove the Tasks tab')
        assert.equal(tasks.provider, provider)
        assert.equal(tasks.items.length, 1)
        assert.equal(tasks.items[0].status, 'pending')
        const render = resolveLayout({ tabs: [{ id: 'main', fixedCenter: true }, { id: 'tasks' }],
            assignment: store.sessions[id].layout.assignment, viewport: { w: 1680, h: 1000 }, collapsed: [] })
        assert.ok(render.regions.some(region => region.slots.some(slot => slot.dockId === 'right-top' && slot.tabs.some(tab => tab.id === 'tasks'))))
        const maximized = resolveLayout({ tabs: [{ id: 'main', fixedCenter: true }, { id: 'tasks' }],
            assignment: store.sessions[id].layout.assignment, viewport: { w: 1680, h: 1000 }, collapsed: [], maximized: ['right-top'] })
        assert.ok(maximized.regions.some(region => region.kind === 'maximized' && region.slots.some(slot => slot.dockId === 'right-top')))
    }
})

test('suspension fixture uses production SessionView, KeepAlive, and scroller diagnostics', () => {
    assert.match(fixture, /component: SessionView/)
    assert.match(fixture, /h\(RouterView,.*h\(KeepAlive/)
    assert.match(fixture, /function scrollerDiagnostics\(/)
    assert.match(fixture, /\.setupState\.scrollerRef/)
    assert.match(fixture, /getScrollAnchor\(\)/)
    assert.match(fixture, /unref\(scroller\.positions\)/)
    assert.match(fixture, /unref\(scroller\.suspended\)/)
})

test('suspension fixture exposes seeded history, comparison, and actual hide controls', () => {
    assert.match(fixture, /async function runLargeHistorySuspension\(/)
    assert.match(fixture, /seedHistory\(mainId, 2000\)/)
    assert.match(fixture, /replacements = 60/)
    assert.match(fixture, /secondaryActive\.value/)
    assert.match(fixture, /await switchSession\(\)/)
    assert.match(fixture, /await selectSubagent\(\)/)
    assert.match(fixture, /await maximizeDock\(\)/)
})

test('large-history seed adds the requested rows through the session store', () => {
    const start = fixture.indexOf('function seedHistory(')
    const end = fixture.indexOf('\nconst originalDrain', start)
    assert.ok(start >= 0 && end > start)
    const added = []
    const store = { sessionItems: { main: Array.from({ length: 100 }, (_, index) => ({ line_num: index + 1 })) },
        sessions: { main: { last_line: 100 } },
        addSessionItems(id, rows) { added.push(...rows); this.sessionItems[id].push(...rows) } }
    const seed = new Function('store', 'DISPLAY_LEVEL', 'finalContent',
        `${fixture.slice(start, end)}; return seedHistory`)(store, DISPLAY_LEVEL, text => ({ text }))
    seed('main', 2000)
    assert.equal(added.length, 1900)
    assert.equal(added[0].line_num, 101)
    assert.equal(added.at(-1).line_num, 2000)
    assert.equal(store.sessions.main.last_line, 2000)
    seed('main', 2000)
    assert.equal(added.length, 1900)
})

test('suspension fixture exposes pending reveal and immediate reconciliation hide', () => {
    assert.match(fixture, /async function runPendingRevealHide\(/)
    assert.match(fixture, /scrollToKey\(/)
    assert.match(fixture, /async function runReconcileHideReturn\(/)
    assert.match(fixture, /store\.addSessionItems\(/)
    assert.match(fixture, /await switchSession\(\)/)
    assert.match(fixture, /window\.invisibleStreamingFixture = [\s\S]*runLargeHistorySuspension[\s\S]*runPendingRevealHide[\s\S]*runReconcileHideReturn/)
})

test('reconciliation fixture swaps a measured visible stream and checks the returned anchor before navigation', () => {
    const start = fixture.indexOf('async function runReconcileHideReturn(')
    const end = fixture.indexOf('\nasync function runHiddenThinking(', start)
    assert.ok(start >= 0 && end > start)
    const scenario = fixture.slice(start, end)
    assert.match(scenario, /scrollToKey\(lineNum/)
    assert.match(scenario, /getItemHeight\(lineNum\)/)
    assert.match(scenario, /before\.savedAnchor\?\.key === lineNum/)
    assert.match(scenario, /getItemHeight\(realLine\)/)
    assert.match(scenario, /const hidePromise = router\.push\(/)
    assert.match(scenario, /writesAtHideStart < 8/)
    assert.match(scenario, /writesAtReturnStart < 8/)
    assert.match(scenario, /restoreWrites >= 8/)
    assert.match(scenario, /after\.savedAnchor\?\.key === realLine/)
    assert.ok(scenario.indexOf('getItemHeight(lineNum)') < scenario.indexOf('store.addSessionItems(mainId,'))
    assert.ok(scenario.indexOf('const hidePromise = router.push(') < scenario.indexOf('const after = scrollerDiagnostics(target)'))
    assert.doesNotMatch(scenario, /scrollToKey\(realLine/)
    assert.ok(scenario.indexOf('after.savedAnchor?.key === realLine') < scenario.indexOf('querySelector(`[data-line-num='))
})

test('suspension diagnostics include viewport inputs and latest rows', () => {
    assert.match(fixture, /function viewportDiagnostics\(/)
    assert.match(fixture, /window\.innerWidth/)
    assert.match(fixture, /window\.innerHeight/)
    assert.match(fixture, /expectedViewport = null/)
    assert.match(fixture, /requireViewport\(expectedViewport\)/)
    assert.match(fixture, /function sessionRows\(/)
    assert.match(fixture, /function scenarioReport\(/)
})

test('rate fixture records execution, changed publications, fixed timed source, and visible reports', () => {
    assert.match(fixture, /counters.bufferExecutions\+\+/)
    assert.match(fixture, /async function runPublicationRateScenario\(/)
    assert.match(fixture, /chunkSize: 200, intervalMs: 20, deliveries: 100, observationMs: 400/)
    assert.match(fixture, /regularPublications/)
    assert.match(fixture, /canonicalEqualsSource/)
    assert.match(fixture, /displayedEqualsSource/)
    assert.match(fixture, /Publication rate: plain text/)
    assert.match(fixture, /Publication rate: fenced code/)
    assert.match(fixture, /Publication rate: open thinking/)
    assert.match(fixture, /id: 'fixture-report'/)
})

test('rate sources contain exactly 100 equal chunks and a complete fenced code block', () => {
    const start = fixture.indexOf('const publicationRateSchedule =')
    const end = fixture.indexOf('\nfunction finalItemText(', start)
    const { publicationRateSource, publicationRateSchedule } = new Function(`${fixture.slice(start, end)}; return { publicationRateSource, publicationRateSchedule }`)()
    assert.deepEqual(publicationRateSchedule, { chunkSize: 200, intervalMs: 20, deliveries: 100, observationMs: 400 })
    for (const kind of ['plain', 'code']) {
        const source = publicationRateSource(kind)
        assert.equal(source.length, 20000)
        const chunks = Array.from({ length: 100 }, (_, index) => source.slice(index * 200, (index + 1) * 200))
        assert.ok(chunks.every(chunk => chunk.length === 200))
        assert.equal(chunks.join(''), source)
        assert.equal(publicationRateSource(kind), source)
        if (kind === 'code') { assert.ok(source.startsWith('```javascript\n')); assert.ok(source.endsWith('\n```')) }
    }
})

test('fixture seeds both provider gates only in its local Pinia state', () => {
    assert.match(fixture, /settings.disabledProviders = \[\]/)
    assert.match(fixture, /store.providerStates = \{ claude_code: 'running', codex: 'running' \}/)
    assert.doesNotMatch(fixture, /initSettings\(|sendSyncedSettings\(/)
    assert.match(fixture, /if \(method !== 'GET'\) throw new Error/)
})

test('rate reports distinguish document visibility, registry activity, and actual frame opportunities', () => {
    assert.match(fixture, /documentVisibility: document.visibilityState/)
    assert.match(fixture, /documentHidden: document.hidden/)
    assert.match(fixture, /bufferActive: isBufferActive\(mainId, store\.localState\.streamingBlocks\[mainId\]\?\.messageId, 0\)/)
    assert.match(fixture, /viewActive: listInstance\(mainId\)\?\.\$props.viewActive/)
    assert.match(fixture, /visibilityChanges/)
})

test('startup keeps rate controls disabled until preflight succeeds and retains failure state', async () => {
    assert.match(fixture, /disabled: !fixtureReady.value \|\| rateBusy.value/g)
    assert.match(fixture, /id: 'fixture-status'/)
    const start = fixture.indexOf('async function initializeFixture(')
    assert.ok(start > 0)
    const code = fixture.slice(start, fixture.indexOf('\nawait initializeFixture()', start))
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
    for (const failed of [false, true]) {
        const ready = { value: false }, failure = { value: null }, reports = { value: [] }, errors = []
        let release
        const pending = new Promise(resolve => { release = resolve })
        const initialize = new AsyncFunction('fixtureReady', 'startupFailure', 'reports', 'errors', 'settle', 'viewInstance',
            'runDockPreflight', 'viewportDiagnostics', 'useDataStore', 'pinia', 'store', 'snapshot',
            `${code}; return initializeFixture()`)
        const work = initialize(ready, failure, reports, errors, async () => {},
            () => ({ $: { setupState: { layout: { render: { value: { mode: 'dock' } } } } } }),
            async () => { await pending; if (failed) throw Error('preflight failed') }, () => ({}), () => 1, {}, 1, () => ({}))
        await Promise.resolve()
        assert.equal(ready.value, false)
        release()
        await work
        assert.equal(ready.value, !failed)
        assert.equal(failure.value, failed ? 'Error: preflight failed' : null)
        assert.equal(reports.value.at(-1).fixtureReady, !failed)
        assert.deepEqual(errors, failed ? ['Error: preflight failed'] : [])
    }
})
test('replacement evidence rejects missing, stale, or disconnected production rendering', () => {
    const start = fixture.indexOf('function replacementEvidence(')
    assert.ok(start > 0)
    const end = fixture.indexOf('\nasync function runPublicationRateScenario(', start)
    const evidence = new Function('getParsedContent', 'finalItemText', 'assert', 'setParsedContent',
        `${fixture.slice(start, end)}; return replacementEvidence`)(item => item.parsed, item => item.parsed.text,
        (value, message) => assert.ok(value, message), (item, parsed) => { item.parsed = parsed })
    const element = { isConnected: true, innerText: 'final body' }
    const component = { $el: element, $props: { content: { text: 'final body' } } }
    const visual = { parsed: { text: 'final body' } }
    assert.equal(evidence('final body', visual, component, element).finalRenderingEqualsSource, true)
    assert.throws(() => evidence('final body', null, component, element), /visual/)
    assert.throws(() => evidence('final body', { parsed: { text: 'stale' } }, component, element), /visual/)
    assert.throws(() => evidence('final body', visual, { ...component, $props: { content: { text: 'stale' } } }, element), /component/)
    assert.throws(() => evidence('final body', visual, component, { ...element, isConnected: false }), /rendered/)
})
test('rate errors exclude earlier scenario errors while export keeps the global list', () => {
    assert.match(fixture, /const errorStartIndex = errors.length/)
    assert.match(fixture, /result.errors.push\(\.\.\.errors.slice\(errorStartIndex\)\)/)
    assert.match(fixture, /exportEvidence\(\).*reports: reports.value, requests, errors, lifetimes/)
})

test('Codex fixture history and final replacement provide valid canonical AgentMessage text', async () => {
    const { agentMessageText } = await import('../providers/codex/canonical.js')
    const start = fixture.indexOf('function finalContent(')
    const end = fixture.indexOf('\nfunction seedSession(', start)
    const finalContent = new Function('provider', `${fixture.slice(start, end)}; return finalContent`)('codex')
    for (const [name, text] of [
        ['history', 'History 1. Readable history content.'],
        ['final plain text', 'Complete final plain text.\n\nSecond paragraph.'],
        ['final fenced code', '```javascript\nconst value = "final";\n```'],
    ]) {
        const content = finalContent(text, 'text', 'fixture-message', `fixture-${name}`)
        assert.equal(agentMessageText(content), text, `${name} must reach the production Codex renderer`)
        assert.equal(content.payload.item.content[0].type, 'Text')
    }
    const thinking = finalContent('Complete reasoning.', 'thinking', 'fixture-message', 'fixture-thinking')
    assert.equal(thinking.payload.summary[0].type, 'summary_text')
    assert.equal(thinking.payload.summary[0].text, 'Complete reasoning.')
})
