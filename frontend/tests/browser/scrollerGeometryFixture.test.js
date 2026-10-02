import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, shallowRef } from 'vue'
import { useVirtualScroll } from '../../src/composables/useVirtualScroll.js'
import { readFileSync } from 'node:fs'

test('real composable fixture runs nine independent 60-change oracles', async () => {
    const { runGeometryCase } = await import('./scrollerGeometryHarness.js')
    for (const size of [500, 2000, 10000]) for (const position of ['head', 'middle', 'tail']) {
        const container = { clientHeight: 240, scrollTop: 0, addEventListener() {}, removeEventListener() {} }
        const scope = effectScope(); let result
        const warn = console.warn; console.warn = () => {}
        try { result = scope.run(() => runGeometryCase({ useVirtualScroll, effectScope, shallowRef, container, size, position })) }
        finally { scope.stop(); console.warn = warn }
        assert.equal(result.acceptedChanges, 60); assert.equal(result.attemptedChanges, 60); assert.equal(result.changes.length, 60); assert.equal(result.oraclePassed, true)
        assert.equal(result.changes.every(c => c.keyReads === 0), true)
        assert.equal(result.changes[0].rebuiltEntries, size - result.index)
        assert.ok(result.changes.every(c => c.elapsedMs >= 0))
    }
})
test('browser entries expose bounded controls, explicit readiness, and JSON evidence', () => {
    const source = readFileSync(new URL('./scrollerGeometry.js', import.meta.url), 'utf8')
    assert.match(source, /scroller-geometry-report/); assert.match(source, /scroller-geometry-status/)
    assert.match(readFileSync(new URL('./scrollerGeometry.html', import.meta.url), 'utf8'), /Run geometry matrix/); assert.match(source, /startup failed/)
    const conversation = readFileSync(new URL('./scrollerGeometryConversation.js', import.meta.url), 'utf8')
    assert.match(conversation, /import\('\.\/invisibleStreaming.js'\)/)
    assert.match(conversation, /query.has\('baseline'\)/)
    assert.match(conversation, /scroller-conversation-report/)
    for (const name of ['Initial reveal', 'Near-bottom growth', 'Reading-above anchor', 'Group expand\/collapse', 'Streaming retirement', 'Missing content recovery', 'Filtered session pagination']) assert.match(conversation, new RegExp(name))
})
test('pinned executable baseline uses the same independent oracle and reports full reconstruction', async () => {
    const { execFileSync } = await import('node:child_process')
    const vue = await import('vue')
    const nearest = await import('../../src/utils/nearestScroll.js')
    const { runGeometryCase } = await import('./scrollerGeometryHarness.js')
    const source = execFileSync('git', ['show', '56a1600a:frontend/src/composables/useVirtualScroll.js'], { encoding: 'utf8' })
    const code = source.replace(/import \{([^}]+)\} from '([^']+)'/g,
        (_, names, path) => `const {${names}} = ${path === 'vue' ? 'vue' : 'nearest'}`)
        .replace('export function useVirtualScroll', 'function useVirtualScroll')
    const baseline = new Function('vue', 'nearest', `${code}; return useVirtualScroll`)(vue, nearest)
    const scope = effectScope(); const warn = console.warn; console.warn = () => {}
    try {
        const report = scope.run(() => runGeometryCase({ useVirtualScroll: baseline, shallowRef,
            container: { clientHeight: 240, scrollTop: 0, addEventListener() {}, removeEventListener() {} }, size: 500, position: 'tail' }))
        assert.equal(report.oraclePassed, true); assert.equal(report.acceptedChanges, 60)
        assert.equal(report.changes.every(change => change.rebuiltEntries === 500 && change.keyReads >= 500), true)
    } finally { scope.stop(); console.warn = warn }
})
test('actual browser entry enables ready controls and publishes bounded action reports', async () => {
    const source = readFileSync(new URL('./scrollerGeometry.js', import.meta.url), 'utf8')
        .replace(/^import .*\n/gm, '').replace('await import(/* @vite-ignore */ path)', 'await load(path)')
    const nodes = Object.fromEntries(['scroller-geometry-status', 'scroller-geometry-report', 'run-geometry', 'geometry-viewport']
        .map(id => [id, { disabled: true, textContent: '', clientWidth: 320, clientHeight: 240 }]))
    const document = { querySelector: selector => nodes[selector.slice(1)], visibilityState: 'visible' }
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
    const launch = new AsyncFunction('document', 'location', 'innerWidth', 'innerHeight', 'effectScope', 'shallowRef', 'runGeometryCase', 'load', source)
    let calls = 0
    const run = () => { calls++; return { oraclePassed: true } }
    await launch(document, { search: '' }, 390, 844, effectScope, shallowRef, run, async () => ({ useVirtualScroll }))
    assert.equal(nodes['run-geometry'].disabled, false)
    assert.equal(nodes['scroller-geometry-status'].textContent, 'Scroller geometry ready')
    await nodes['run-geometry'].onclick()
    const report = JSON.parse(nodes['scroller-geometry-report'].textContent)
    assert.equal(calls, 9); assert.equal(report.cases.length, 9); assert.equal(report.status, 'passed')
    assert.deepEqual(report.measuredContainer, { width: 320, height: 240 })
    await launch(document, { search: '?baseline=1' }, 390, 844, effectScope, shallowRef, run,
        async () => { throw new Error('Absent baseline adapter') })
    assert.equal(nodes['scroller-geometry-status'].textContent, 'Scroller geometry startup failed')
    assert.equal(nodes['run-geometry'].disabled, true)
    assert.equal(JSON.parse(nodes['scroller-geometry-report'].textContent).status, 'failed')
    assert.equal(JSON.parse(nodes['scroller-geometry-report'].textContent).moduleWarm, false)
})
test('bounded conversation timeout stays inconclusive and ignores late settlement', async () => {
    const { runBoundedScrollerAction } = await import('./scrollerConversationHarness.js')
    let deadline, lateResolve, cleared = 0
    const clock = { now: () => 100, setTimeout: callback => { deadline = callback; return 1 }, clearTimeout: () => { cleared++ } }
    const promise = runBoundedScrollerAction(() => new Promise(resolve => { lateResolve = resolve }), { timeoutMs: 20000, clock })
    await Promise.resolve(); deadline()
    const report = await promise
    assert.equal(report.status, 'failed/inconclusive'); assert.equal(report.requiresReload, true)
    assert.equal(report.timedOut, true); assert.equal(report.timeoutMs, 20000)
    lateResolve('late success'); await Promise.resolve(); await Promise.resolve()
    assert.equal(report.status, 'failed/inconclusive'); assert.equal(report.result, undefined)
    assert.equal(cleared, 1)
})
test('bounded successful and rejected actions release deadlines without requiring reload', async () => {
    const { runBoundedScrollerAction } = await import('./scrollerConversationHarness.js')
    let cleared = 0
    const clock = { now: () => 100, setTimeout: () => 1, clearTimeout: () => { cleared++ } }
    const passed = await runBoundedScrollerAction(() => 'done', { clock })
    assert.equal(passed.status, 'passed'); assert.equal(passed.result, 'done'); assert.equal(passed.requiresReload, false)
    const failed = await runBoundedScrollerAction(() => { throw new Error('failure') }, { clock })
    assert.equal(failed.status, 'failed/inconclusive'); assert.match(failed.error, /failure/)
    assert.equal(failed.requiresReload, false); assert.equal(cleared, 2)
})
test('conversation recovery switches actual sessions without creating unsupported pane routes', async () => {
    const { recoverScrollerConversation } = await import('./scrollerConversationHarness.js')
    let switches = 0
    const fixture = { ids: { mainId: 'main', otherId: 'other' }, router: { currentRoute: { value: { params: { sessionId: 'main' } } } },
        navigate() { throw new Error('Pane navigation must not perform session recovery') },
        async switchSession() { switches++; fixture.router.currentRoute.value.params.sessionId = switches === 1 ? 'other' : 'main' } }
    const result = await recoverScrollerConversation(fixture)
    assert.equal(switches, 2); assert.deepEqual(result, { hiddenSessionId: 'other', restoredSessionId: 'main' })
})
test('actual conversation button locks after deadline and late success cannot publish a pass', async () => {
    const { runBoundedScrollerAction } = await import('./scrollerConversationHarness.js')
    const source = readFileSync(new URL('./scrollerGeometryConversation.js', import.meta.url), 'utf8')
    const body = source.slice(source.indexOf('function add('), source.indexOf('function list('))
    let deadline, lateResolve, calls = 0
    const clock = { now: () => 100, setTimeout: callback => { deadline = callback; return 1 }, clearTimeout() {} }
    const launch = new Function('runBoundedScrollerAction', 'action', `
        const quarantineImportedControls = () => {};
        const document = { createElement: () => ({}) }, controls = { append() {} };
        const status = {}, evidence = {}, reports = [], buttons = [];
        let ready = true, busy = false, locked = false, activeAction = null;
        const actionDeadlineMs = 20000;
        function diagnostics() { return { controls: { ready, busy, locked, activeAction } } }
        ${body}
        add('Native pending action', action);
        return { button: buttons[0], status, evidence, reports, state: () => ({ ready, busy, locked }) };
    `)
    const ui = launch((action, options) => runBoundedScrollerAction(action, { ...options, clock }),
        () => { calls++; return new Promise(resolve => { lateResolve = resolve }) })
    const pending = ui.button.onclick()
    assert.equal(ui.reports[0].status, 'pending'); assert.equal(ui.button.disabled, true)
    assert.equal(JSON.parse(ui.evidence.textContent).controls.busy, true)
    await Promise.resolve(); deadline(); await pending
    assert.deepEqual(ui.state(), { ready: false, busy: false, locked: true })
    assert.equal(ui.button.disabled, true); assert.match(ui.status.textContent, /reload required/)
    assert.equal(JSON.parse(ui.evidence.textContent).reports[0].deadlineAt, 20100)
    lateResolve('late success'); await Promise.resolve(); await Promise.resolve()
    await ui.button.onclick()
    assert.equal(calls, 1); assert.equal(ui.reports[0].status, 'failed/inconclusive')
})
test('actual imported control template is quarantined with the timed-out wrapper page', async () => {
    const { installImportedScrollerQuarantine, runBoundedScrollerAction } = await import('./scrollerConversationHarness.js')
    const { h, ref } = await import('vue')
    const { makeRenderer, descendants } = await import('../helpers/scrollerComponentHarness.js')
    const source = readFileSync(new URL('./invisibleStreaming.js', import.meta.url), 'utf8')
    const start = source.indexOf("h('div', { id: 'fixture-controls' }")
    const end = source.indexOf("    h('div', { id: 'conversation' }", start)
    const expression = source.slice(start, end).trim().replace(/,$/, '')
    let importedActions = 0
    const dependencies = { h, startupFailure: ref(null), fixtureReady: ref(true), rateBusy: ref(false), rateReadingAbove: ref(false), rateHideReturn: ref(false),
        provider: 'claude_code', baseline: false, publicationRateBaseline: false }
    for (const name of ['runPublicationRateScenario', 'readingAbove', 'runHiddenThinking', 'runVisible', 'navigate', 'maximizeDock',
        'restoreDock', 'switchSession', 'selectSubagent', 'toggleSecondary', 'runLargeHistorySuspension', 'runPendingRevealHide', 'runReconcileHideReturn']) {
        dependencies[name] = () => { importedActions++ }
    }
    const importedVNode = new Function(...Object.keys(dependencies), `return ${expression}`)(...Object.values(dependencies))
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => importedVNode }); app.mount(root)
    const imported = descendants(root, node => node.props.id === 'fixture-controls')[0]
    const importedButtons = descendants(imported, node => node.type === 'button')
    const importedInputs = descendants(imported, node => node.type === 'input')
    assert.ok(importedButtons.length >= 18); assert.equal(importedInputs.length, 2)
    const callbacks = new Map(), styles = []
    for (const node of descendants(imported, () => true)) node.closest = selector => selector === '#fixture-controls' ? imported : null
    imported.querySelectorAll = () => [...importedButtons, ...importedInputs]
    const document = { querySelector: selector => selector === '#fixture-controls' ? imported : null,
        createElement: () => ({}), head: { append: style => styles.push(style) },
        addEventListener(type, callback, capture) { assert.equal(capture, true); callbacks.set(type, callback) } }
    function dispatch(node, type = 'click') {
        const event = new Event(type, { cancelable: true }); Object.defineProperty(event, 'target', { value: node })
        callbacks.get(type)?.(event)
        if (!event.defaultPrevented && !node.disabled && !node.hidden && !imported.inert) node.props.onClick?.(event)
        return event
    }
    dispatch(importedButtons[0]); assert.equal(importedActions, 1)
    const quarantine = installImportedScrollerQuarantine(document); quarantine()
    assert.equal(imported.inert, true)
    assert.ok([...importedButtons, ...importedInputs].every(node => node.hidden && node.disabled))
    assert.match(styles[0].textContent, /#fixture-controls/)
    assert.ok(descendants(imported, node => node.props.id === 'fixture-status').length)
    // Even a later renderer write that re-enables a button cannot bypass the capture gate.
    importedButtons[0].disabled = false
    for (const button of importedButtons) assert.equal(dispatch(button).defaultPrevented, true)
    assert.equal(dispatch(importedInputs[0], 'change').defaultPrevented, true)
    assert.equal(importedActions, 1)
    const wrapperSource = readFileSync(new URL('./scrollerGeometryConversation.js', import.meta.url), 'utf8')
    const body = wrapperSource.slice(wrapperSource.indexOf('function add('), wrapperSource.indexOf('function list('))
    let deadline, lateResolve, ownCalls = 0
    const clock = { now: () => 100, setTimeout: callback => { deadline = callback; return 1 }, clearTimeout() {} }
    const launch = new Function('runBoundedScrollerAction', 'quarantineImportedControls', 'action', `
        const document = { createElement: () => ({}) }, controls = { append() {} }, status = {}, evidence = {}, reports = [], buttons = [];
        let ready = true, busy = false, locked = false, activeAction = null; const actionDeadlineMs = 20000;
        function diagnostics() { return { controls: { ready, busy, locked, activeAction } } }
        ${body}
        add('Pending wrapper', action); return { button: buttons[0], reports };
    `)
    const wrapper = launch((action, options) => runBoundedScrollerAction(action, { ...options, clock }), quarantine,
        () => { ownCalls++; return new Promise(resolve => { lateResolve = resolve }) })
    const pending = wrapper.button.onclick(); await Promise.resolve(); deadline(); await pending
    assert.equal(wrapper.button.disabled, true); assert.equal(wrapper.reports[0].status, 'failed/inconclusive')
    for (const button of importedButtons) dispatch(button)
    await wrapper.button.onclick(); lateResolve('late'); await Promise.resolve()
    assert.equal(importedActions, 1); assert.equal(ownCalls, 1); assert.equal(wrapper.reports[0].status, 'failed/inconclusive')
    app.unmount()
})
test('missing-content substitution uses the public cache-invalidating action and returns fresh response content', async () => {
    const { replaceScrollerContentWithMetadata, makeScrollerRecoveryItem } = await import('./scrollerConversationHarness.js')
    const { clearParsedContent, getParsedContent, hasContent } = await import('../../src/utils/parsedContent.js')
    const { computeVisualItems } = await import('../../src/utils/visualItems.js')
    const { DISPLAY_LEVEL, DISPLAY_MODE } = await import('../../src/constants.js')
    const source = readFileSync(new URL('../../src/stores/data.js', import.meta.url), 'utf8')
    const start = source.indexOf('        updateSessionItemsContent(sessionId, items) {')
    const end = source.indexOf('        // Tab management actions', start)
    const actions = new Function('clearParsedContent', `return { ${source.slice(start, end)} }`)(clearParsedContent)
    const item = { line_num: 1, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, content: JSON.stringify({ text: 'old content' }) }
    getParsedContent(item)
    const store = { sessionItems: { main: [item] }, visual: [], ...actions,
        clearOptimisticMessageIfMatched() {}, recomputeVisualItems(id) { this.visual = computeVisualItems(this.sessionItems[id], DISPLAY_MODE.NORMAL) },
        getSessionVisualItems() { return this.visual } }
    store.recomputeVisualItems('main')
    const fixture = { ids: { mainId: 'main' }, store }
    const response = makeScrollerRecoveryItem(item, JSON.stringify({ text: 'fresh content' }))
    assert.deepEqual(getParsedContent(response), { text: 'fresh content' })
    const result = replaceScrollerContentWithMetadata(fixture, item.line_num)
    assert.equal(store.sessionItems.main[0], item)
    assert.equal(hasContent(item), false); assert.equal(getParsedContent(item), null)
    assert.equal(hasContent(store.visual[0]), false); assert.equal(getParsedContent(store.visual[0]), null)
    assert.deepEqual(result, { rawHasContent: false, rawParsedAvailable: false, visualHasContent: false, visualParsedAvailable: false })
})
