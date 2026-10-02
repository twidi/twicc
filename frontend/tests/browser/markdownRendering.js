// Isolated real MarkdownContent comparisons. Production conversation checks use the separate entry.
import { createApp, defineComponent, h, nextTick, provide, ref } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import MarkdownContent from '../../src/components/ui/MarkdownContent.vue'
import { useSettingsStore } from '../../src/stores/settings'
import { createMarkdownMetrics, feedMarkdownSnapshots, inspectMarkdownOutput, markdownScenarios, waitForMarkdownCondition } from './markdownRenderingHarness.js'
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/button-group/button-group.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '../../src/styles/transcript-tokens.css'
import '../../src/styles/quote-card.css'

const query = new URLSearchParams(location.search)
const counted = query.get('comparison') === '1'
const selectedRenderer = query.get('renderer') || 'current'
const ready = ref(false), startupFailure = ref(null), busy = ref(false), reports = ref([])
const source = ref(''), slashTag = ref(false)
const errors = [], requests = [], visibility = []
const panels = []
window.addEventListener('error', event => errors.push({ at: performance.now(), error: String(event.error || event.message) }))
window.addEventListener('unhandledrejection', event => errors.push({ at: performance.now(), error: String(event.reason) }))
document.addEventListener('visibilitychange', () => visibility.push({ at: performance.now(), state: document.visibilityState }))
window.fetch = async (input, options = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname
    const method = (options.method || input.method || 'GET').toUpperCase()
    requests.push({ path, method })
    throw new Error(`Isolated Markdown fixture rejects ${method === 'GET' ? 'unexpected read' : 'mutation'}: ${method} ${path}`)
}
const pinia = createPinia()
const settings = useSettingsStore(pinia)
settings._effectiveColorScheme = 'light'
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }] })
const styles = document.createElement('style')
styles.textContent = 'body{margin:0;font:16px sans-serif}#markdown-controls{position:sticky;top:0;z-index:10;background:Canvas;padding:8px}button{margin:4px}.markdown-panels{display:flex;gap:16px;padding:16px}.markdown-panel{flex:1;min-width:0}.markdown-output{border:1px solid #888;padding:12px}#markdown-report{white-space:pre-wrap;overflow:auto;padding:12px;max-height:35vh}@media(max-width:767px){.markdown-panels{flex-direction:column}}'
document.head.append(styles)
function Panel(component, name) {
    const panel = { name, metrics: null, root: null, lastEmitted: null, renderedEvents: [] }
    panels.push(panel)
    return defineComponent({ name, setup() {
        panel.metrics = createMarkdownMetrics()
        provide('markdownRenderingMetrics', panel.metrics.sink)
        provide('markdownFileLinks', {
            classifyHref(href) { return href === 'src/example.py:12' ? { kind: 'file', candidates: ['src/example.py'], lineNum: 12 } : { kind: 'external' } },
            openFile(candidates, options) { reports.value.push({ fileLinkClick: { candidates, options } }) },
        })
        provide('rewriteContentMediaUrl', url => url.endsWith('/image.png') ? '/share/fixture/media/image.png' : null)
        return () => h('section', { class: 'markdown-panel', 'data-renderer': name }, [h('h2', name),
            h('div', { class: 'markdown-output', ref: element => { panel.root = element } }, [h(component, {
                source: source.value, tagSlashCommand: slashTag.value, showToolbar: true,
                onRendered() {
                    panel.lastEmitted = { source: source.value, theme: settings._effectiveColorScheme === 'dark' ? 'dark' : 'default', slashTag: slashTag.value }
                    panel.renderedEvents.push({ at: performance.now(), ...panel.lastEmitted })
                },
            })])])
    } })
}
function tupleMatches(a, b) { return a && a.source === b.source && a.theme === b.theme && a.slashTag === b.slashTag }
function snapshot() {
    return { counted, selectedRenderer, visibility: document.visibilityState, viewport: { width: innerWidth, height: innerHeight },
        requests: [...requests], errors: [...errors], visibilityChanges: [...visibility] }
}
async function runScenario(name) {
    if (!ready.value || busy.value) throw new Error('Fixture is not ready or another scenario is active')
    if (counted && panels.some(panel => panel.metrics.report.active > 0)) throw new Error('Previous document work is still active; wait for settlement before another scenario')
    const scenario = markdownScenarios[name]
    if (!scenario) throw new Error(`Unknown scenario ${name}`)
    busy.value = true
    const startedAt = performance.now(), errorStart = errors.length
    // A fresh component instance gives each scenario independent cache/tool state and counters.
    source.value = ''; slashTag.value = false
    mountRevision.value++
    await nextTick()
    try {
        await waitForMarkdownCondition(() => panels.every(panel => panel.lastEmitted?.source === ''))
        const feedTimes = await feedMarkdownSnapshots(scenario.snapshot, tuple => {
            settings._effectiveColorScheme = tuple.theme === 'dark' ? 'dark' : 'light'
            source.value = tuple.source
            slashTag.value = tuple.slashTag
        })
        const finalTuple = scenario.snapshot(99)
        const results = await Promise.all(panels.map(async panel => {
            try {
                await waitForMarkdownCondition(async () => {
                    if (!tupleMatches(panel.lastEmitted, finalTuple)) return false
                    if (counted && (!tupleMatches(panel.metrics.report.commits.at(-1), finalTuple) || panel.metrics.report.active !== 0)) return false
                    await nextTick()
                    return true
                })
                const output = inspectMarkdownOutput(panel.root.querySelector('.markdown-body'), name)
                if (counted && panel.name === 'current' && panel.metrics.report.peakActive > 1) throw new Error('Current renderer overlaps document operations')
                return { renderer: panel.name, status: 'passed', output, metrics: counted ? panel.metrics.report : null, renderedEvents: panel.renderedEvents }
            } catch (error) {
                return { renderer: panel.name, status: 'failed/inconclusive', error: String(error),
                    metrics: counted ? panel.metrics.report : null, renderedEvents: panel.renderedEvents,
                    finalDOM: panel.root.querySelector('.markdown-body')?.innerHTML }
            }
        }))
        const report = { scenario: name, startedAt, finishedAt: performance.now(), feedTimes, finalTuple, results,
            runErrors: errors.slice(errorStart), ...snapshot() }
        if (report.runErrors.length) report.status = 'failed/inconclusive'
        else report.status = results.every(result => result.status === 'passed') ? 'passed' : 'failed/inconclusive'
        reports.value.push(report)
        return report
    } finally { busy.value = false }
}
const mountRevision = ref(0)
let app
try {
    const components = []
    if (counted) {
        // Runtime variable imports keep optional generated files out of normal mode.
        if (!['current', 'baseline'].includes(selectedRenderer)) throw new Error('Renderer must be current or baseline')
        const adapterPath = selectedRenderer === 'baseline'
            ? '../../src/components/ui/MarkdownRenderingBaseline.vue' : '../../src/components/ui/MarkdownRenderingCurrent.vue'
        components.push(Panel((await import(/* @vite-ignore */ adapterPath)).default, selectedRenderer === 'baseline' ? 'baseline f5d52630' : 'current'))
    } else components.push(Panel(MarkdownContent, 'production current'))
    app = createApp({ setup: () => () => [
        h('div', { id: 'markdown-controls' }, [h('strong', { id: 'markdown-status' }, startupFailure.value ? `Startup failed: ${startupFailure.value}` : ready.value ? 'Fixture ready' : 'Startup pending'),
            ...Object.keys(markdownScenarios).map(name => h('button', { disabled: !ready.value || busy.value, onClick: () => runScenario(name).catch(error => errors.push({ error: String(error) })) }, name)),
            h('button', { disabled: !ready.value || busy.value, onClick: () => { settings._effectiveColorScheme = settings._effectiveColorScheme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.colorScheme = settings._effectiveColorScheme } }, 'Toggle theme'),
            h('a', { href: './markdownRendering.html?comparison=1&renderer=current' }, 'Counted current'), ' / ',
            h('a', { href: './markdownRendering.html?comparison=1&renderer=baseline' }, 'Counted baseline'), ' / ',
            h('a', { href: './markdownRenderingConversation.html?provider=claude_code', target: '_blank' }, 'Claude conversation'), ' / ',
            h('a', { href: './markdownRenderingConversation.html?provider=codex', target: '_blank' }, 'Codex conversation')]),
        h('div', { class: 'markdown-panels' }, components.map(component => h(component, { key: `${component.name}-${mountRevision.value}` }))),
        h('pre', { id: 'markdown-report' }, JSON.stringify(reports.value, null, 2)),
    ] })
    app.config.errorHandler = error => errors.push({ at: performance.now(), error: String(error) })
    app.use(pinia).use(router).mount('#app')
    await router.isReady()
    await waitForMarkdownCondition(() => panels.every(panel => panel.lastEmitted !== null))
    ready.value = true
} catch (error) {
    startupFailure.value = String(error)
    errors.push({ at: performance.now(), startupFailure: startupFailure.value })
    if (!app) document.querySelector('#app').textContent = `Startup failed: ${startupFailure.value}`
}
window.markdownRenderingFixture = { counted, selectedRenderer, runScenario, snapshot, reports, settings,
    ready: () => ready.value, startupFailure: () => startupFailure.value,
    exportEvidence: () => JSON.stringify({ reports: reports.value, ...snapshot() }, null, 2),
    teardown() { app?.unmount() },
}
