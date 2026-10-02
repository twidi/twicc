import { effectScope, shallowRef } from 'vue'
import { runGeometryCase } from './scrollerGeometryHarness.js'
const status = document.querySelector('#scroller-geometry-status')
const report = document.querySelector('#scroller-geometry-report')
const button = document.querySelector('#run-geometry')
const container = document.querySelector('#geometry-viewport')
const baseline = new URLSearchParams(location.search).get('baseline') === '1'
let useVirtualScroll, moduleLoadedAt
function diagnostics() {
    return { mode: baseline ? 'baseline' : 'current', baselineCommit: '56a1600a', viewport: { width: innerWidth, height: innerHeight },
        measuredContainer: { width: container.clientWidth, height: container.clientHeight },
        moduleLoadedAt: moduleLoadedAt ?? null, moduleWarm: typeof useVirtualScroll === 'function', visibility: document.visibilityState }
}
try {
    // Exactly one implementation per page. Keep optional absent adapters outside Vite's static resolution.
    const path = baseline ? '../../src/composables/useVirtualScrollGeometryBaseline.js' : '../../src/composables/useVirtualScroll.js'
    useVirtualScroll = (await import(/* @vite-ignore */ path)).useVirtualScroll
    moduleLoadedAt = performance.now()
    status.textContent = 'Scroller geometry ready'; button.disabled = false
    report.textContent = JSON.stringify({ status: 'ready', ...diagnostics() }, null, 2)
} catch (error) {
    button.disabled = true
    status.textContent = 'Scroller geometry startup failed'
    report.textContent = JSON.stringify({ status: 'failed', error: String(error), ...diagnostics() }, null, 2)
}
button.onclick = async () => {
    button.disabled = true
    const cases = []
    try {
        for (const size of [500, 2000, 10000]) for (const position of ['head', 'middle', 'tail']) {
            const scope = effectScope()
            try { cases.push(scope.run(() => runGeometryCase({ useVirtualScroll, shallowRef, container, size, position }))) }
            finally { scope.stop() }
        }
        report.textContent = JSON.stringify({ status: 'passed', ...diagnostics(), cases }, null, 2)
        status.textContent = 'Scroller geometry passed'
    } catch (error) {
        report.textContent = JSON.stringify({ status: 'failed', error: String(error), ...diagnostics(), cases }, null, 2)
        status.textContent = 'Scroller geometry failed'
    } finally { button.disabled = false }
}
