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
