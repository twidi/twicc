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
