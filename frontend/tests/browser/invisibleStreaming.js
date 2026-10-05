// Full production conversation fixture. No main.js bootstrap or live WebSocket.
import { createApp, h, KeepAlive, nextTick, ref, unref } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import SessionItemsList from '../../src/components/session/detail/SessionItemsList.vue'
import SessionView from '../../src/views/SessionView.vue'
import { useDataStore } from '../../src/stores/data'
import { useSettingsStore } from '../../src/stores/settings'
import { getParsedContent, setParsedContent } from '../../src/utils/parsedContent'
import { DISPLAY_LEVEL, DISPLAY_MODE, SYNTHETIC_ITEM } from '../../src/constants'
import { streamPublicationRegistry } from '../../src/utils/streamPublicationRegistry.js'
import { destroySessionBuffers, isBufferActive } from '../../src/utils/streamingBuffer.js'
import { installDetailsMotion } from '../../src/utils/detailsMotion'
import { installWaMotionStyles } from '../../src/utils/waMotionStyles'
import '../../src/utils/brandRobotIcon'
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '../../src/styles/neutral-tint.css'
import '../../src/styles/transcript-tokens.css'
import '../../src/styles/depth.css'
import '../../src/styles/glass.css'
import '../../src/styles/motion.css'
import '../../src/styles/glow.css'
import '../../src/styles/group-reveal.css'
import '../../src/styles/surfaces.css'
import '../../src/styles/sidebar-rows.css'
import '../../src/styles/option-cards.css'
import '../../src/styles/session-state-icons.css'
import '../../src/styles/callouts.css'
import '../../src/styles/tags.css'
import '../../src/styles/quote-card.css'
import '../../src/styles/tool-cards.css'
import '../../src/styles/scrollbars.css'
import '../../src/styles/scroll-shadow.css'
import '@awesome.me/webawesome/dist/components/badge/badge.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/button-group/button-group.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/card/card.js'
import '@awesome.me/webawesome/dist/components/comparison/comparison.js'
import '@awesome.me/webawesome/dist/components/divider/divider.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/progress-bar/progress-bar.js'
import '@awesome.me/webawesome/dist/components/progress-ring/progress-ring.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/radio-group/radio-group.js'
import '@awesome.me/webawesome/dist/components/radio/radio.js'
import '@awesome.me/webawesome/dist/components/skeleton/skeleton.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/split-panel/split-panel.js'
import '@awesome.me/webawesome/dist/components/switch/switch.js'
import '@awesome.me/webawesome/dist/components/tag/tag.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/tab/tab.js'
import '@awesome.me/webawesome/dist/components/tab-group/tab-group.js'
import '@awesome.me/webawesome/dist/components/tab-panel/tab-panel.js'
import '@awesome.me/webawesome/dist/components/popover/popover.js'
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import '@awesome.me/webawesome/dist/components/slider/slider.js'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/dropdown/dropdown.js'
import '@awesome.me/webawesome/dist/components/dropdown-item/dropdown-item.js'
import '@awesome.me/webawesome/dist/components/input/input.js'
import '@awesome.me/webawesome/dist/components/color-picker/color-picker.js'
import '@awesome.me/webawesome/dist/components/textarea/textarea.js'
import '@awesome.me/webawesome/dist/components/checkbox/checkbox.js'
import '@awesome.me/webawesome/dist/components/relative-time/relative-time.js'
import '@awesome.me/webawesome/dist/components/popup/popup.js'
import '../../src/styles/toast-motion.css'
import '../../src/styles/codemirror-search.css'
import '../../src/styles/robot-working.css'
installDetailsMotion()
installWaMotionStyles()
const query = new URLSearchParams(location.search)
const provider = query.get('provider') === 'codex' ? 'codex' : 'claude_code'
const baseline = query.get('baseline') === '1'
const publicationRateBaseline = query.get('publicationRateBaseline') === '1'
if (baseline && publicationRateBaseline) throw new Error('Fixture baseline=1 and publicationRateBaseline=1 are mutually exclusive')
const projectId = 'invisible-stream-fixture-project'
const mainId = 'invisible-stream-fixture-main', otherId = 'invisible-stream-fixture-other', agentId = 'invisible-stream-fixture-agent'
const ids = new Set([mainId, otherId, agentId])
const lineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum
const errors = [], requests = [], lifetimes = [], instances = new Map()
const counters = { bufferFrames: 0, bufferExecutions: 0, drains: 0, envelopes: 0, transitions: 0, bootstraps: 0 }
const reports = ref([])
const rateReadingAbove = ref(false), rateHideReturn = ref(false), rateBusy = ref(false)
const latestRateReport = ref(null)
const fixtureReady = ref(false), startupFailure = ref(null)
const secondaryMounted = ref(false), secondaryActive = ref(false)
function toggleSecondary() { secondaryMounted.value = true; secondaryActive.value = !secondaryActive.value }
window.addEventListener('error', event => errors.push(String(event.error || event.message)))
window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)))
// This responder accepts synthetic read routes only. Unexpected reads and all mutations fail closed.
window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href)
    const method = (options.method || input.method || 'GET').toUpperCase()
    const path = url.pathname
    const entry = { path, method, substitute: null }
    requests.push(entry)
    if (method !== 'GET') throw new Error(`Fixture rejects mutation: ${method} ${path}`)
    const match = path.match(/^\/api\/projects\/invisible-stream-fixture-project\/sessions\/(invisible-stream-fixture-(?:main|other|agent))\/(subagents|tool-states|workflow-links)\/$/)
    if (match && ids.has(match[1])) {
        entry.substitute = 'Seeded synthetic session has no server tool or agent links'
        return Response.json(match[2] === 'tool-states' ? {} : [])
    }
    throw new Error(`Fixture rejects unexpected read: ${path}`)
}
// Count only buffer RAF. The scroller and motion RAF are separate work.
const nativeRAF = window.requestAnimationFrame.bind(window)
let rateRun = null
let executingBufferRAF = false
window.requestAnimationFrame = callback => {
    const stack = new Error().stack || ''
    const buffer = ['streamingBuffer.js', 'invisibleStreamingBaselineBuffer.js', 'publicationRateBaselineBuffer.js']
        .some(name => stack.includes(name))
    if (buffer) counters.bufferFrames++
    return nativeRAF(time => {
        if (!buffer) return callback(time)
        counters.bufferExecutions++
        if (rateRun) rateRun.rafOpportunities.push(performance.now() - rateRun.startedAt)
        executingBufferRAF = true
        try { callback(time) } finally { executingBufferRAF = false }
    })
}
const originalBind = streamPublicationRegistry.bindBlock
streamPublicationRegistry.bindBlock = (identity, callbacks) => originalBind(identity, {
    setActive(active) { counters.transitions++; withPublicationBoundary('activation', () => callbacks.setActive(active)) },
    snapshot() { counters.bootstraps++; withPublicationBoundary('bootstrap', () => callbacks.snapshot()) },
})
const pinia = createPinia()
// Baseline must define data first in a fresh Pinia. All production components retrieve this same ID.
let dataFactory = useDataStore
let destroyFixtureSessionBuffers = destroySessionBuffers
let isFixtureBufferActive = isBufferActive
if (baseline || publicationRateBaseline) {
    // Runtime paths keep Vite from resolving absent optional adapters in normal mode.
    const dataAdapterPath = publicationRateBaseline
        ? '../../src/stores/publicationRateBaselineData.js' : '../../src/stores/invisibleStreamingBaselineData.js'
    const bufferAdapterPath = publicationRateBaseline
        ? '../../src/utils/publicationRateBaselineBuffer.js' : '../../src/utils/invisibleStreamingBaselineBuffer.js'
    dataFactory = (await import(/* @vite-ignore */ dataAdapterPath)).useDataStore
    const bufferAdapter = await import(/* @vite-ignore */ bufferAdapterPath)
    destroyFixtureSessionBuffers = bufferAdapter.destroySessionBuffers
    isFixtureBufferActive = bufferAdapter.isBufferActive
}
const store = dataFactory(pinia)
if (useDataStore(pinia) !== store) throw new Error('Production components do not share the selected data store')
const settings = useSettingsStore(pinia)
// No settings watchers or WebSocket bootstrap run here. These gates stay in fixture memory.
settings.disabledProviders = []
store.providerStates = { claude_code: 'running', codex: 'running' }
settings.setDisplayMode(DISPLAY_MODE.NORMAL)
store.projects[projectId] = { id: projectId, directory: '/invisible-stream-fixture', name: 'Invisible stream fixture', sessions_count: 3 }
store.projectsLoaded = true
function finalContent(text, blockType, messageId, uuid) {
    if (provider === 'claude_code') return { type: 'assistant', uuid, message: { id: messageId, role: 'assistant', content: [blockType === 'thinking' ? { type: 'thinking', thinking: text } : { type: 'text', text }] } }
    if (blockType === 'thinking') return { type: 'response_item', payload: { type: 'reasoning', id: messageId, summary: [{ type: 'summary_text', text }] } }
    return { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', id: messageId, content: [{ type: 'Text', text }] } } }
}
function seedSession(id, parent = null) {
    const history = Array.from({ length: 100 }, (_, index) => ({
        line_num: index + 1, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, timestamp: '2026-10-02T12:00:00Z',
        content: JSON.stringify(finalContent(`History ${index + 1}. ${'Readable history content. '.repeat(8)}`, 'text', `history-${index}`, `history-${index}`)),
    }))
    store.sessions[id] = { id, project_id: projectId, provider, title: id, last_line: history.length,
        parent_session_id: parent, type: parent ? 'subagent' : 'session', compute_version_up_to_date: true,
        // Draft prevents layout intention persistence from making a backend write. Content stays fully seeded.
        draft: true, layout: { assignment: { tasks: 'right-top' } }, permission_mode: 'default',
        tasks: { provider, source: provider === 'codex' ? 'update_plan' : 'TodoWrite', line: history.length,
            updated_at: '2026-10-02T12:00:00Z', explanation: '',
            items: [{ status: 'pending', content: 'Check streaming publications' }] } }
    store.localState.sessions[id] = { itemsFetched: true, itemsLoading: false, itemsLoadingError: false }
    store.localState.agentLoaded[id] = true
    store.initSessionItemsFromMetadata(id, history)
    store.updateSessionItemsContent(id, history)
    store.processStates[id] = { state: 'assistant_turn', tools: [], pending_requests: [] }
}
seedSession(mainId); seedSession(otherId); seedSession(agentId, mainId)
function seedHistory(id, count) {
    const existing = store.sessionItems[id].length
    if (existing >= count) return
    const added = Array.from({ length: count - existing }, (_, index) => {
        const number = existing + index + 1
        return { line_num: number, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
            group_head: null, group_tail: null, timestamp: '2026-10-02T12:00:00Z',
            content: JSON.stringify(finalContent(`History ${number}. ${'Readable history content. '.repeat(8)}`,
                'text', `history-${number}`, `history-${number}`)) }
    })
    store.addSessionItems(id, added)
    store.sessions[id].last_line = count
}
function withPublicationBoundary(label, callback) {
    if (!rateRun) return callback()
    const previous = rateRun.boundary
    rateRun.boundary = label
    try { return callback() } finally { rateRun.boundary = previous }
}
const originalDrain = store._onBufferDrain
store._onBufferDrain = (id, index, text) => {
    counters.drains++
    const row = store.localState.visualItemCache[id]?.get(lineNum - index)
    const before = row && getParsedContent(row)
    const previousText = store.localState.streamingBlocks[id]?.blocks.find(block => block.blockIndex === index)?.displayedText
    originalDrain(id, index, text)
    if (rateRun && id === mainId && index === 0 && previousText !== text) {
        const at = performance.now() - rateRun.startedAt
        const label = rateRun.boundary || (executingBufferRAF ? 'regular' : 'explicit snapshot')
        rateRun.publications.push({ at, label, phase: rateRun.phase, text, length: text.length,
            canonicalLength: store.localState.streamingBlocks[id]?.blocks[0]?.text.length ?? null })
    }
    if (row && before !== getParsedContent(row)) counters.envelopes++
}
document.addEventListener('visibilitychange', () => {
    if (rateRun) rateRun.visibilityChanges.push({ at: performance.now() - rateRun.startedAt, state: document.visibilityState, hidden: document.hidden })
})
const noop = { render: () => null }
const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/project/:projectId/session/:sessionId', name: 'session', component: SessionView,
    children: [
        { path: 'subagent/:subagentId', name: 'session-subagent', component: noop },
        ...Object.entries({ files: 'files/:rootKey?/:filePath?', artifacts: 'artifacts/:rootKey?/:filePath?', git: 'git/:rootKey?/:commitRef?/:filePath?', terminal: 'terminal/:termIndex?', orchestration: 'orchestration', plan: 'plan/:docPath?', tasks: 'tasks', workflows: 'workflows/:runId?', browser: 'browser' }).map(([tab, path]) => ({ path, name: `session-${tab}`, component: noop })),
    ],
}, { path: '/project/:projectId', name: 'project', component: noop },
    { path: '/projects', name: 'projects-all', component: noop }, { path: '/', name: 'home', component: noop }] })
await router.push({ name: 'session', params: { projectId, sessionId: mainId } })
await router.isReady()
const style = document.createElement('style')
style.textContent = `html,body,#app{margin:0;height:100%;font:16px sans-serif}#fixture-controls{height:90px;overflow:auto;padding:8px;box-sizing:border-box}#conversation{height:calc(100% - 90px);display:flex}#conversation>.session-view{flex:1;min-width:0;min-height:0}button{margin:3px}#fixture-report{position:fixed;right:5px;top:95px;max-height:30vh;max-width:45vw;overflow:auto;z-index:1000;background:#fff;color:#111;white-space:pre-wrap}`
document.head.append(style)
function tag(instance) {
    const file = instance.$options.__file || ''
    if (!file.endsWith('SessionView.vue') && !file.endsWith('SessionItemsList.vue') && !file.endsWith('SessionItem.vue') && !file.endsWith('VirtualScrollerItem.vue')) return null
    return { file, uid: instance.$.uid, sessionId: instance.$props.sessionId, lineNum: instance.$props.lineNum ?? instance.$props.itemKey }
}
const app = createApp({ setup: () => () => [
    h('div', { id: 'fixture-controls' }, [h('span', { id: 'fixture-status' }, startupFailure.value ? `Startup failed: ${startupFailure.value}` : fixtureReady.value ? 'Fixture ready' : 'Fixture startup pending'), h('strong', `${provider} / ${baseline ? 'baseline aeaf1838' : publicationRateBaseline ? 'rate baseline 1f7d16ef' : 'implementation'}`),
        h('label', [h('input', { type: 'checkbox', checked: rateReadingAbove.value, disabled: !fixtureReady.value || rateBusy.value, onChange: event => { rateReadingAbove.value = event.target.checked } }), 'Rate: read above bottom']),
        h('label', [h('input', { type: 'checkbox', checked: rateHideReturn.value, disabled: !fixtureReady.value || rateBusy.value, onChange: event => { rateHideReturn.value = event.target.checked } }), 'Rate: route hide and return']),
        h('button', { disabled: !fixtureReady.value || rateBusy.value, onClick: () => runPublicationRateScenario({ blockType: 'text', sourceKind: 'plain' }) }, 'Publication rate: plain text'),
        h('button', { disabled: !fixtureReady.value || rateBusy.value, onClick: () => runPublicationRateScenario({ blockType: 'text', sourceKind: 'code' }) }, 'Publication rate: fenced code'),
        h('button', { disabled: !fixtureReady.value || rateBusy.value, onClick: () => runPublicationRateScenario({ blockType: 'thinking', sourceKind: 'plain' }) }, 'Publication rate: open thinking'),
        h('button', { onClick: () => readingAbove() }, 'Read above bottom'),
        h('button', { onClick: () => runHiddenThinking() }, 'Closed thinking: 1000 hidden deltas'),
        h('button', { onClick: () => runVisible('text') }, 'Visible text'),
        h('button', { onClick: () => runVisible('thinking') }, 'Visible thinking'),
        h('button', { onClick: () => runVisible('text', true) }, 'Proposed plan'),
        h('button', { onClick: () => navigate('tasks') }, 'Tasks route'),
        h('button', { onClick: () => navigate('main') }, 'Main Chat'),
        h('button', { onClick: () => maximizeDock() }, 'Maximize task dock'),
        h('button', { onClick: () => restoreDock() }, 'Restore center'),
        h('button', { onClick: () => switchSession() }, 'Switch session'),
        h('button', { onClick: () => selectSubagent() }, 'Select subagent'),
        h('button', { onClick: toggleSecondary }, 'Show/hide second main Chat'),
        h('button', { onClick: () => runLargeHistorySuspension() }, 'Suspension: 2000 rows / 60 replacements'),
        h('button', { onClick: () => runPendingRevealHide() }, 'Suspension: pending reveal / hide'),
        h('button', { onClick: () => runReconcileHideReturn() }, 'Suspension: final / hide / return')]),
    h('div', { id: 'conversation' }, [h(RouterView, {}, { default: ({ Component, route }) => h(KeepAlive, {}, () => h(Component, { key: route.params.sessionId })) }),
        secondaryMounted.value ? h('aside', { style: { display: secondaryActive.value ? 'flex' : 'none', width: '38%', minHeight: 0, borderLeft: '1px solid gray' } },
            [h(SessionItemsList, { sessionId: mainId, projectId, viewActive: secondaryActive.value })]) : null]),
    h('pre', { id: 'fixture-report' }, JSON.stringify(reports.value, null, 2)),
    h('pre', { id: 'publication-rate-report', style: { display: 'none' } }, JSON.stringify(latestRateReport.value, null, 2)),
] })
app.mixin({ mounted() { const entry = tag(this); if (entry) { instances.set(entry.uid, this); lifetimes.push({ ...entry, event: 'mounted' }) } },
    unmounted() { const entry = tag(this); if (entry) { instances.delete(entry.uid); lifetimes.push({ ...entry, event: 'unmounted' }) } } })
app.config.errorHandler = error => errors.push(String(error))
app.use(pinia).use(router).mount('#app')
const settle = async (ms = 240) => {
    await nextTick()
    await new Promise(resolve => nativeRAF(() => nativeRAF(resolve)))
    await new Promise(resolve => setTimeout(resolve, ms))
}
const currentId = () => router.currentRoute.value.params.sessionId
function listInstance(id = currentId()) {
    return [...instances.values()].find(instance => instance.$options.__file?.endsWith('SessionItemsList.vue') && instance.$props.sessionId === id)
}
function container(id = currentId()) { return listInstance(id)?.$el.querySelector('.virtual-scroller') }
function row(id = currentId(), index = 0) { return listInstance(id)?.$el.querySelector(`[data-line-num="${lineNum - index}"]`) }
function viewInstance() { return [...instances.values()].find(instance => instance.$options.__file?.endsWith('SessionView.vue') && instance.$.setupState.sessionId === currentId()) }
function geometry(id = currentId()) {
    const root = container(id)
    if (!root) return null
    const rect = root.getBoundingClientRect()
    const anchor = [...root.querySelectorAll('.virtual-scroller-item')].find(element => {
        const box = element.getBoundingClientRect()
        return box.bottom > rect.top && box.top < rect.bottom && Number(element.dataset.itemKey) > 0
    })
    return { scrollTop: root.scrollTop, scrollHeight: root.scrollHeight, clientHeight: root.clientHeight,
        bottomGap: root.scrollHeight - root.clientHeight - root.scrollTop,
        anchor: anchor?.dataset.itemKey ?? null, anchorOffset: anchor ? anchor.getBoundingClientRect().top - rect.top : null }
}
function snapshot() {
    return { provider, baseline, publicationRateBaseline, counters: { ...counters }, geometry: geometry(), text: row()?.innerText || '',
        rows: lifetimes.filter(entry => entry.lineNum === lineNum), requests: [...requests], errors: [...errors] }
}
function viewportDiagnostics() {
    return { width: window.innerWidth, height: window.innerHeight,
        mobile: window.matchMedia('(max-width: 767px)').matches }
}
function requireViewport(expectedViewport) {
    if (expectedViewport == null) return
    assert(expectedViewport === 'mobile' || expectedViewport === 'desktop', 'Expected viewport must be mobile or desktop')
    assert(viewportDiagnostics().mobile === (expectedViewport === 'mobile'),
        `Expected ${expectedViewport} viewport; check the browser width before this scenario`)
}
function sessionRows(id = mainId) {
    const items = store.sessionItems[id] || []
    const last = items.at(-1)
    return { raw: items.length, visual: store.getSessionVisualItems(id).length,
        lastLine: last?.line_num ?? null, lastContent: last ? JSON.stringify(getParsedContent(last)) : null }
}
function scrollerDiagnostics(list) {
    const scroller = list?.$.setupState.scrollerRef
    if (!scroller) return null
    const root = scroller.$el
    const positions = unref(scroller.positions)
    const rect = root?.getBoundingClientRect()
    const anchor = rect && [...root.querySelectorAll('.virtual-scroller-item')].find(element => {
        const box = element.getBoundingClientRect()
        return box.bottom > rect.top && box.top < rect.bottom && Number(element.dataset.itemKey) > 0
    })
    return { suspended: unref(scroller.suspended), positions: positions.length,
        totalHeight: positions.length ? positions.at(-1).top + positions.at(-1).height : 0,
        visibleRange: scroller.getVisibleRange(),
        savedAnchor: scroller.getScrollAnchor(), scrollState: scroller.getScrollState(),
        renderedAnchor: anchor?.dataset.itemKey ?? null,
        renderedOffset: anchor ? anchor.getBoundingClientRect().top - rect.top : null }
}
function scenarioReport(name, phases, inputs = {}) {
    const result = { name, provider, viewport: viewportDiagnostics(), inputs,
        phases, rows: sessionRows(), requests: [...requests], errors: [...errors] }
    reports.value.push(result)
    return result
}
function resetCounters() { for (const key of Object.keys(counters)) counters[key] = 0 }
function assert(condition, message) { if (!condition) throw new Error(message) }
function report(name, before, extra = {}) { const result = { name, before, after: snapshot(), ...extra }; reports.value.push(result); return result }
const publicationRateSchedule = Object.freeze({ chunkSize: 200, intervalMs: 20, deliveries: 100, observationMs: 400 })
function publicationRateSource(sourceKind) {
    const length = publicationRateSchedule.chunkSize * publicationRateSchedule.deliveries
    if (sourceKind === 'code') {
        const opening = '```javascript\n', closing = '\n```'
        const body = 'const value = "Stable code publication"; // Read the growing code.\n'
        return opening + body.repeat(Math.ceil(length / body.length)).slice(0, length - opening.length - closing.length) + closing
    }
    const paragraph = 'Stable plain text publication. Read the growing paragraph.\n\n'
    return paragraph.repeat(Math.ceil(length / paragraph.length)).slice(0, length)
}
function finalItemText(item) {
    const parsed = getParsedContent(item)
    if (parsed.message) return parsed.message.content.map(block => block.text ?? block.thinking ?? '').join('')
    if (parsed.payload?.summary) return parsed.payload.summary.map(block => block.text ?? '').join('')
    return parsed.payload?.item?.content?.map(block => block.text ?? '').join('') ?? ''
}
function replacementEvidence(source, visualItem, component, element) {
    assert(visualItem, 'Final production visual item is missing')
    const finalVisualText = finalItemText(visualItem)
    assert(finalVisualText === source, 'Final production visual item loses source text')
    const componentItem = {}
    if (component) setParsedContent(componentItem, component.$props.content)
    assert(component && finalItemText(componentItem) === source,
        'Final production component loses source text')
    assert(element?.isConnected && component.$el === element && element.innerText.trim().length > 0,
        'Final replacement component is not rendered in the production DOM')
    return { finalVisualText, finalVisualEqualsSource: true, finalRenderingEqualsSource: true,
        finalComponentContent: component.$props.content, finalDOMText: element.innerText }
}
async function runPublicationRateScenario({ blockType = 'text', sourceKind = 'plain',
    readingAbove = rateReadingAbove.value, hideReturn = rateHideReturn.value } = {}) {
    assert(fixtureReady.value && !startupFailure.value, 'Fixture startup is not ready')
    assert(!rateBusy.value, 'A publication rate scenario already runs')
    const errorStartIndex = errors.length
    assert(blockType === 'text' || blockType === 'thinking', 'Invalid publication block type')
    assert(sourceKind === 'plain' || sourceKind === 'code', 'Invalid publication source kind')
    rateBusy.value = true
    latestRateReport.value = { status: 'running', provider, blockType, sourceKind, publicationRateBaseline, readingAbove, hideReturn }
    const timers = [], routeWork = []
    const result = { name: 'publication rate', status: 'running', provider,
        mode: publicationRateBaseline ? 'rate baseline 1f7d16ef' : baseline ? 'priority-2 baseline aeaf1838' : 'implementation',
        blockType, sourceKind, readingAbove, hideReturn, viewport: viewportDiagnostics(),
        schedule: publicationRateSchedule, source: publicationRateSource(sourceKind),
        deliveries: [], publications: [], rafOpportunities: [], phases: [], visibilityChanges: [], errors: [] }
    const mark = phase => {
        result.phase = phase
        result.phases.push({ phase, at: performance.now() - result.startedAt, geometry: geometry(mainId),
            documentVisibility: document.visibilityState, documentHidden: document.hidden,
            route: router.currentRoute.value.name, currentSession: currentId(),
            viewActive: listInstance(mainId)?.$props.viewActive,
            bufferActive: isFixtureBufferActive(mainId, store.localState.streamingBlocks[mainId]?.messageId, 0),
            rowRect: row(mainId)?.getBoundingClientRect().toJSON() ?? null,
            thinkingOpen: row(mainId)?.querySelector('wa-details')?.open ?? null })
    }
    try {
        // Reset every fixture session through its selected buffer map before clearing store state.
        for (const id of ids) {
            destroyFixtureSessionBuffers(id)
            delete store.localState.streamingBlocks[id]
            seedSession(id, id === agentId ? mainId : null)
            store.recomputeVisualItems(id)
        }
        secondaryActive.value = false
        await router.push({ name: 'session', params: { projectId, sessionId: mainId } })
        viewInstance()?.$.setupState.layout.restoreMaximized()
        await settle()
        resetCounters()
        result.startedAt = performance.now()
        rateRun = result
        mark('setup')
        const messageId = start(blockType, mainId)
        await settle(100)
        const scroller = listInstance(mainId)?.$.setupState.scrollerRef
        assert(scroller, 'Main production scroller is missing')
        await scroller.scrollToEdge('bottom', { maxAttempts: 12 })
        await settle(100)
        if (blockType === 'thinking') {
            const details = row(mainId)?.querySelector('wa-details')
            assert(details, 'Production thinking details is missing')
            details.show()
            await settle(450)
            assert(details.open, 'Production thinking details does not open')
        }
        if (readingAbove) { scroller.setScrollTop(200); await settle(100) }
        result.geometryBefore = geometry(mainId)
        result.scrollerBefore = scrollerDiagnostics(listInstance(mainId))
        result.feedStartedAt = performance.now() - result.startedAt
        mark('feed')
        if (hideReturn) {
            // Navigation runs independently of feed timers. It never changes scheduled delivery deadlines.
            for (const [delay, sessionId, phase] of [[800, otherId, 'route hide'], [1200, mainId, 'route return']]) {
                timers.push(setTimeout(() => {
                    mark(phase)
                    routeWork.push(router.push({ name: 'session', params: { projectId, sessionId } })
                        .catch(error => result.errors.push(String(error))))
                }, delay))
            }
        }
        for (let index = 0; index < publicationRateSchedule.deliveries; index++) {
            const scheduledAt = result.feedStartedAt + (index + 1) * publicationRateSchedule.intervalMs
            await new Promise(resolve => setTimeout(resolve, Math.max(0, result.startedAt + scheduledAt - performance.now())))
            const chunk = result.source.slice(index * publicationRateSchedule.chunkSize, (index + 1) * publicationRateSchedule.chunkSize)
            const deliveredAt = performance.now() - result.startedAt
            result.deliveries.push({ index, scheduledAt, deliveredAt, text: chunk, length: chunk.length })
            feed(chunk, mainId, messageId)
        }
        await Promise.all(routeWork)
        mark('observe backlog')
        await new Promise(resolve => setTimeout(resolve, publicationRateSchedule.observationMs))
        const stream = store.localState.streamingBlocks[mainId]
        const block = stream?.blocks[0]
        result.canonicalText = block?.text ?? null
        result.displayedText = block?.displayedText ?? null
        result.canonicalEqualsSource = result.canonicalText === result.source
        result.displayedEqualsSource = result.displayedText === result.source
        const streamingRow = store.localState.visualItemCache[mainId]?.get(lineNum)
        const parsed = streamingRow ? getParsedContent(streamingRow) : null
        const content = parsed?.message?.content?.[0]
        result.parsedDisplayedText = content?.text ?? content?.thinking ?? null
        result.parsedDisplayedEqualsSource = result.parsedDisplayedText === result.source
        result.geometryBeforeRetirement = geometry(mainId)
        result.scrollerBeforeRetirement = scrollerDiagnostics(listInstance(mainId))
        mark('terminal retirement')
        withPublicationBoundary('terminal', () => {
            const uuid = provider === 'codex' ? messageId : `final-${messageId}`
            store.streamBlockStop(mainId, messageId, 0)
            store.streamBlockEnd(mainId, messageId, 0, uuid)
            const realLine = store.sessionItems[mainId].length + 1
            store.addSessionItems(mainId, [{ line_num: realLine,
                kind: blockType === 'thinking' && provider === 'codex' ? 'reasoning' : 'assistant_message',
                display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,

                content: JSON.stringify(finalContent(result.source, blockType, messageId, uuid)) }])
            store.sessions[mainId].last_line = realLine
            result.realLine = realLine
        })
        await settle(700)
        result.finalText = finalItemText(store.sessionItems[mainId].at(-1))
        result.finalTextEqualsSource = result.finalText === result.source
        result.retired = !store.localState.streamingBlocks[mainId]
        result.finalDOMText = listInstance(mainId)?.$el.querySelector(`[data-line-num="${result.realLine}"]`)?.innerText ?? null
        result.geometryAfter = geometry(mainId)
        result.scrollerAfter = scrollerDiagnostics(listInstance(mainId))
        // Capture retirement geometry before the explicit rendering probe changes a reading-above viewport.
        result.renderProbeMovesViewport = readingAbove
        if (readingAbove) {
            await scroller.scrollToKey(result.realLine, { align: 'start', maxAttempts: 12 })
            await settle(450)
        }
        const finalVisualItem = store.getSessionVisualItems(mainId).find(item => item.lineNum === result.realLine)
        const finalElement = listInstance(mainId)?.$el.querySelector(`.session-item[data-line-num="${result.realLine}"]`)
        const finalComponent = [...instances.values()].find(instance =>
            instance.$options.__file?.endsWith('SessionItem.vue') && instance.$props.sessionId === mainId &&
            instance.$props.lineNum === result.realLine && instance.$el === finalElement)
        Object.assign(result, replacementEvidence(result.source, finalVisualItem, finalComponent, finalElement))
        assert(result.canonicalEqualsSource, 'Canonical text loses feed characters')
        assert(result.finalTextEqualsSource && result.retired, 'Final retirement loses source text or keeps streaming state')
        if (!readingAbove) assert(result.displayedEqualsSource && result.parsedDisplayedEqualsSource, 'Visible displayed text does not complete before retirement')
        result.status = 'complete'
    } catch (error) {
        result.status = 'failed'
        result.errors.push(String(error))
    } finally {
        for (const timer of timers) clearTimeout(timer)
        await Promise.all(routeWork)
        const regular = result.publications.filter(publication => publication.label === 'regular')
        const exceptions = result.publications.filter(publication => publication.label !== 'regular')
        result.regularPublications = regular.length
        result.exceptionalPublications = exceptions.length
        result.exceptionCounts = Object.fromEntries([...new Set(exceptions.map(publication => publication.label))]
            .map(label => [label, exceptions.filter(publication => publication.label === label).length]))
        result.regularPublicationGaps = regular.slice(1).map((publication, index) => publication.at - regular[index].at)
        result.minimumRegularGapMs = result.regularPublicationGaps.length ? Math.min(...result.regularPublicationGaps) : null
        result.backlogCompletionTimes = regular.filter(publication => publication.length === publication.canonicalLength)
            .map(publication => publication.at)
        result.counters = { ...counters }
        result.bufferRAFExecutions = counters.bufferExecutions
        result.parsedEnvelopeChanges = counters.envelopes
        result.errors.push(...errors.slice(errorStartIndex))
        destroyFixtureSessionBuffers(mainId)
        if (store.localState.streamingBlocks[mainId]) {
            delete store.localState.streamingBlocks[mainId]
            store.recomputeVisualItems(mainId)
        }
        rateRun = null
        rateBusy.value = false
        latestRateReport.value = result
        reports.value.push(result)
    }
    return result
}
let messageSequence = 0
function start(blockType = 'text', id = currentId(), messageId = `fixture-message-${++messageSequence}`) {
    store.streamBlockStart(id, messageId, 0, blockType)
    return messageId
}
function feed(text, id = currentId(), messageId = store.localState.streamingBlocks[id]?.messageId) {
    store.streamBlockDelta(id, messageId, 0, text)
}
async function navigate(tab) {
    await router.push({ name: tab === 'main' ? 'session' : `session-${tab}`, params: { projectId, sessionId: currentId() } })
    await settle()
}
function tasksDockRegion(layout) {
    return layout.render.value.regions.find(region =>
        region.slots.some(slot => slot.dockId === 'right-top' && slot.tabs.some(tab => tab.id === 'tasks')))
}
function assertTasksDock(layout) {
    assert(store.getSessionTasks(currentId())?.items?.length > 0, 'Tasks presence snapshot is missing')
    assert(layout.measured.value && layout.render.value.mode !== 'tabs', 'Use a docking-capable viewport for Tasks checks')
    const region = tasksDockRegion(layout)
    assert(region, 'Tasks is absent from the right-top rendered region')
    const dock = document.querySelector(`.dock-region[data-rid="${region.id}"]`)
    assert(dock?.querySelector('wa-tab[panel="tasks"]'), 'The rendered right-top dock has no Tasks tab')
    return region
}
async function runDockPreflight() {
    await navigate('main')
    const layout = viewInstance()?.$.setupState.layout
    assert(layout, 'SessionView layout is missing')
    assertTasksDock(layout)
    await navigate('tasks')
    assert(router.currentRoute.value.name === 'session-tasks', 'Tasks route redirected to Chat')
    assert(layout.routeActiveTabId.value === 'tasks', 'Tasks does not own the route')
    assert(layout.centerVisible.value && listInstance(mainId)?.$props.viewActive,
        'The Tasks route hides center Chat')
    assertTasksDock(layout)

    const messageId = start('text', mainId)
    feed('Dock preflight starts visible. ', mainId, messageId)
    await listInstance(mainId).$.setupState.scrollerRef.scrollToEdge('bottom', { maxAttempts: 12 })
    await settle(700)
    assert(row(mainId)?.innerText.includes('Dock preflight starts visible.'), 'The active Chat block is not visible')
    const block = store.localState.streamingBlocks[mainId].blocks[0]
    await maximizeDock()
    assert(layout.maximizedRegion.value?.slots.some(slot => slot.dockId === 'right-top'),
        'The actual Tasks dock did not maximize')
    assert(!layout.centerVisible.value && !listInstance(mainId)?.$props.viewActive,
        'Maximizing Tasks leaves center Chat active')
    assert(document.querySelector('.dock-region.maximized wa-tab[panel="tasks"]'),
        'The maximized dock does not render Tasks')
    feed('Dock preflight resumes this block.', mainId, messageId)
    await settle(700)
    const transitionsBeforeRestore = counters.transitions
    await restoreDock()
    assert(layout.centerVisible.value && listInstance(mainId)?.$props.viewActive,
        'Restoring Tasks does not show center Chat')
    assert(store.localState.streamingBlocks[mainId].messageId === messageId &&
        store.localState.streamingBlocks[mainId].blocks[0] === block,
    'Restoring Tasks changed the current streaming block')
    const resumedText = row(mainId)?.innerText || ''
    assert(resumedText.split('Dock preflight resumes this block.').length - 1 === 1,
        'Restoring Tasks does not show the current block exactly once')
    if (!baseline) assert(counters.transitions - transitionsBeforeRestore === 1,
        'Restoring Tasks resumes the current block more than once')
    assertTasksDock(layout)
    await navigate('main')
    destroyFixtureSessionBuffers(mainId)
    delete store.localState.streamingBlocks[mainId]
    store.recomputeVisualItems(mainId)
    await settle()
    resetCounters()
}
async function switchSession() {
    await router.push({ name: 'session', params: { projectId, sessionId: currentId() === mainId ? otherId : mainId } })
    await settle()
}
async function selectSubagent() {
    store.addSessionTab(mainId, `agent-${agentId}`)
    await router.push({ name: 'session-subagent', params: { projectId, sessionId: mainId, subagentId: agentId } })
    await settle()
}
async function maximizeDock() { viewInstance().$.setupState.layout.maximize(['right-top']); await settle() }
async function restoreDock() { viewInstance().$.setupState.layout.restoreMaximized(); await settle() }
async function hideMain(mode) {
    if (mode === 'keepAlive') await switchSession()
    else if (mode === 'tab') await selectSubagent()
    else if (mode === 'dock') await maximizeDock()
    else throw new Error(`Unknown hide mode: ${mode}`)
}
async function showMain(mode) {
    if (mode === 'keepAlive') await switchSession()
    else if (mode === 'tab') await navigate('main')
    else await restoreDock()
}
async function runLargeHistorySuspension({ mode = 'keepAlive', replacements = 60, expectedViewport = null } = {}) {
    requireViewport(expectedViewport)
    await navigate('main')
    seedHistory(mainId, 2000)
    await settle()
    const target = listInstance(mainId)
    const scroller = target?.$.setupState.scrollerRef
    assert(scroller, 'Main production scroller is missing')
    await scroller.scrollToKey(1000, { align: 'start' })
    await settle()
    secondaryMounted.value = true
    secondaryActive.value = true
    await settle()
    const comparison = [...instances.values()].find(instance => instance !== target &&
        instance.$options.__file?.endsWith('SessionItemsList.vue') && instance.$props.sessionId === mainId)
    assert(comparison?.$.setupState.scrollerRef, 'Visible comparison scroller is missing')
    const before = scrollerDiagnostics(target)
    await hideMain(mode)
    assert(unref(scroller.suspended), `Main scroller did not suspend through ${mode}`)
    assert(!unref(comparison.$.setupState.scrollerRef.suspended), 'Comparison scroller also suspended')
    const frozenPositions = unref(scroller.positions)
    const hiddenStart = scrollerDiagnostics(target)
    for (let index = 0; index < replacements; index++) {
        const text = `History 1000 replacement ${String(index).padStart(2, '0')}. ${'Readable history content. '.repeat(8)}`
        store.updateSessionItemsContent(mainId, [{ line_num: 1000,
            content: JSON.stringify(finalContent(text, 'text', `history-replacement-${index}`, `history-replacement-${index}`)) }])
        await nextTick()
        await new Promise(resolve => nativeRAF(resolve))
        assert(unref(scroller.positions) === frozenPositions, 'Suspended geometry changed identity')
    }
    const hiddenEnd = scrollerDiagnostics(target)
    const comparisonHidden = scrollerDiagnostics(comparison)
    assert(hiddenEnd.suspended, 'Main scroller resumed during hidden replacements')
    await showMain(mode)
    await settle()
    const after = scrollerDiagnostics(target)
    assert(!after.suspended, 'Main scroller did not resume')
    assert(store.sessionItems[mainId][999].content.includes(`replacement ${String(replacements - 1).padStart(2, '0')}`),
        'Latest history replacement is missing')
    return scenarioReport('large history suspension', { before, hiddenStart, hiddenEnd,
        comparisonHidden, after }, { mode, history: 2000, replacements, expectedViewport })
}
async function runPendingRevealHide({ expectedViewport = null } = {}) {
    requireViewport(expectedViewport)
    await navigate('main')
    seedHistory(mainId, 2000)
    await settle()
    const target = listInstance(mainId)
    const scroller = target.$.setupState.scrollerRef
    scroller.setScrollTop(0)
    await settle()
    const before = scrollerDiagnostics(target)
    const pending = scroller.scrollToKey(1900, { align: 'nearest', maxAttempts: 12 })
    await switchSession()
    const hidden = scrollerDiagnostics(target)
    const revealResult = await pending
    assert(revealResult === false, 'Pending reveal completed after suspension')
    await switchSession()
    await settle()
    return scenarioReport('pending reveal then hide', { before, hidden, after: scrollerDiagnostics(target), revealResult },
        { expectedViewport })
}
async function runReconcileHideReturn({ expectedViewport = null } = {}) {
    requireViewport(expectedViewport)
    await navigate('main')
    seedHistory(mainId, 2000)
    await settle()
    const target = listInstance(mainId)
    const scroller = target.$.setupState.scrollerRef
    if (store.localState.streamingBlocks[mainId]) {
        destroyFixtureSessionBuffers(mainId)
        delete store.localState.streamingBlocks[mainId]
        store.recomputeVisualItems(mainId)
    }
    const messageId = start('text', mainId)
    const marker = 'Final reconciliation survives rapid hide and return.'
    const tallText = Array.from({ length: 180 }, (_, index) =>
        `Streamed paragraph ${index + 1}. ${'Measured content stays tall. '.repeat(4)}`).join('\n\n') + `\n\n${marker}`
    feed(tallText, mainId, messageId)
    await settle(700)
    const streamVisible = await scroller.scrollToKey(lineNum, { align: 'start', maxAttempts: 12 })
    assert(streamVisible, 'The streamed row did not enter the rendered window')
    await settle()
    const measuredHeight = scroller.getItemHeight(lineNum)
    assert(measuredHeight > scroller.$el.clientHeight + 400, 'The streamed row has no tall measured height')
    const streamPosition = unref(scroller.positions).find(position => position.key === lineNum)
    assert(streamPosition, 'The streamed row has no live geometry')
    scroller.setScrollTop(streamPosition.top + 200)
    await settle()
    const before = scrollerDiagnostics(target)
    assert(before.savedAnchor?.key === lineNum && before.savedAnchor.offset > 0,
        'The reading anchor is not inside the streamed row')
    assert(before.scrollState.scrollHeight - before.scrollState.clientHeight - before.scrollState.scrollTop > 150,
        'The reader is still near the bottom of the streamed row')
    assert(!scroller.isAtBottom(), 'The parent would skip its reading-position restore')
    const stream = store.localState.streamingBlocks[mainId]
    const text = stream.blocks[0].text
    const uuid = provider === 'codex' ? messageId : `final-${messageId}`
    store.streamBlockStop(mainId, messageId, 0)
    store.streamBlockEnd(mainId, messageId, 0, uuid)
    const realLine = store.sessionItems[mainId].length + 1
    const originalSetScrollTop = scroller.setScrollTop
    let restoreWrites = 0
    scroller.setScrollTop = value => {
        if (Math.abs(value - before.scrollState.scrollTop) <= 0.5) restoreWrites++
        return originalSetScrollTop(value)
    }
    try {
        store.addSessionItems(mainId, [{ line_num: realLine, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
            group_head: null, group_tail: null,
            content: JSON.stringify(finalContent(text, 'text', messageId, uuid)) }])
        store.sessions[mainId].last_line = realLine
        assert(scroller.getItemHeight(realLine) === measuredHeight, 'The parent did not seed the final row height')
        // The parent seeds the height synchronously, then awaits nextTick and eight RAFs.
        // Start real KeepAlive routing before that restore can finish.
        const writesAtHideStart = restoreWrites
        const hidePromise = router.push({ name: 'session', params: { projectId, sessionId: otherId } })
        assert(writesAtHideStart < 8, 'The parent restore finished before the hide route started')
        await hidePromise
        await nextTick()
        assert(currentId() === otherId, 'The KeepAlive hide route did not activate')
        const hidden = scrollerDiagnostics(target)
        assert(hidden.suspended, 'The main scroller did not suspend on the hide route')
        const writesAtReturnStart = restoreWrites
        const returnPromise = router.push({ name: 'session', params: { projectId, sessionId: mainId } })
        assert(writesAtReturnStart < 8, 'The parent restore finished before the return route started')
        await returnPromise
        await settle(700)
        const after = scrollerDiagnostics(target)
        assert(restoreWrites >= 8, 'The parent restore did not complete eight frame writes')
        assert(!store.localState.streamingBlocks[mainId], 'Final reconciliation retains the streaming block')
        assert(store.sessionItems[mainId][realLine - 1].content.includes(text), 'Final JSONL row loses the latest text')
        assert(!after.suspended, 'Main scroller remains suspended after rapid return')
        assert(after.savedAnchor?.key === realLine, 'Return does not anchor inside the final row')
        assert(Math.abs(after.savedAnchor.offset - before.savedAnchor.offset) <= 8,
            'Return moves the reading anchor more than 8px within the final row')
        assert(Math.abs(after.scrollState.scrollTop - before.scrollState.scrollTop) <= 8,
            'Return moves the reading position more than 8px')
        assert(target.$el.querySelector(`[data-line-num="${realLine}"]`)?.innerText.includes(marker),
            'Final row is absent from the restored rendered window')
        return scenarioReport('reconciliation then rapid hide and return', { before, hidden, after },
            { realLine, measuredHeight, writesAtHideStart, writesAtReturnStart, restoreWrites, expectedViewport })
    } finally {
        scroller.setScrollTop = originalSetScrollTop
    }
}
async function runHiddenThinking() {
    try {
        await navigate('main')
        start('thinking')
        await settle(450)
        const details = row()?.querySelector('wa-details')
        if (details?.open) { details.hide(); await settle(450) }
        resetCounters()
        const before = snapshot()
        for (let index = 0; index < 1000; index++) feed('x')
        await settle()
        const block = store.localState.streamingBlocks[currentId()].blocks[0]
        assert(block.text === 'x'.repeat(1000), 'Canonical hidden text loses characters')
        if (!baseline) {
            assert(counters.bufferFrames === 0, 'Hidden thinking schedules buffer RAF')
            assert(counters.drains === 0, 'Hidden thinking publishes body text')
            assert(counters.envelopes === 0, 'Hidden thinking replaces parsed envelopes')
        }
        report('closed thinking: 1000 deltas', before)
        details.show(); await settle(450)
        assert(row().innerText.includes('x'.repeat(50)), 'Reopening thinking does not show canonical text')
        report('thinking reopen catch-up', snapshot())
    } catch (error) { errors.push(String(error)); reports.value.push({ failure: String(error), ...snapshot() }); throw error }
}
async function runVisible(blockType, proposed = false) {
    try {
        await navigate('main')
        start(blockType)
        await settle(450)
        // Real exposed scroller navigation, followed by actual geometry. No forced scrollHeight assignment.
        const scroller = listInstance().$.setupState.scrollerRef
        await scroller.scrollToEdge('bottom', { maxAttempts: 12 })
        if (blockType === 'thinking') { row()?.querySelector('wa-details')?.show(); await settle(450) }
        await settle()
        resetCounters()
        const before = snapshot()
        const chunks = proposed ? ['Introduction.\n', '<proposed_plan>\n', '# Proposed plan\n\n', 'Growing plan details.\n', '</proposed_plan>']
            : Array.from({ length: 24 }, (_, index) => `Visible update ${index}. ${'Wrapping content remains current. '.repeat(3)}\n`)
        for (const chunk of chunks) { feed(chunk); await settle(80) }
        await settle(700)
        const atBottom = geometry()
        assert(atBottom.bottomGap <= 150, `Bottom following exceeds 150px: ${atBottom.bottomGap}`)
        const text = store.localState.streamingBlocks[currentId()].blocks[0].text
        assert(row().innerText.includes(proposed ? 'Growing plan details.' : 'Visible update 23.'), 'Visible body misses final content')
        const id = currentId(), messageId = store.localState.streamingBlocks[id].messageId
        const uuid = provider === 'codex' ? messageId : `final-${messageId}`
        store.streamBlockStop(id, messageId, 0)
        store.streamBlockEnd(id, messageId, 0, uuid)
        const realLine = store.sessionItems[id].length + 1
        store.addSessionItems(id, [{ line_num: realLine, kind: blockType === 'thinking' && provider === 'codex' ? 'reasoning' : 'assistant_message',
            display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
            content: JSON.stringify(finalContent(text, blockType, messageId, uuid)) }])
        store.sessions[id].last_line = realLine
        await settle(700)
        assert(!store.localState.streamingBlocks[id], 'Final reconciliation retains synthetic block')
        assert(geometry().bottomGap <= 150, 'Final replacement loses bottom following')
        report(`${blockType}${proposed ? ' proposed plan' : ''}: visible and final replacement`, before, { atBottom })
    } catch (error) { errors.push(String(error)); reports.value.push({ failure: String(error), ...snapshot() }); throw error }
}
async function readingAbove() {
    if (!store.localState.streamingBlocks[currentId()]) { start('text'); feed('Initial visible body'); await settle(700) }
    const root = container()
    const scroller = listInstance().$.setupState.scrollerRef
    scroller.setScrollTop(200)
    await settle()
    const before = geometry(), beforeSnapshot = snapshot()
    feed('Additional text while the reader stays above. '.repeat(60))
    await settle(700)
    const after = geometry()
    assert(after.anchor === before.anchor && Math.abs(after.anchorOffset - before.anchorOffset) <= 2, 'Reading-above anchor moves more than 2px')
    assert(Math.abs(root.scrollTop - before.scrollTop) <= 2, 'Reading-above scroll position moves more than 2px')
    return report('reading above', beforeSnapshot, { anchorTolerancePx: 2, beforeGeometry: before, afterGeometry: after })
}
async function displayMode(mode) {
    settings.setDisplayMode(mode)
    // main.js normally installs the settings watcher. Fixture has no application bootstrap.
    for (const id of ids) store.recomputeVisualItems(id)
    await settle()
}
async function reconnectSameMessage() {
    const id = currentId(), stream = store.localState.streamingBlocks[id]
    const { messageId } = stream, blockType = stream.blocks[0].blockType
    const previous = stream.blocks[0].publicationIdentity
    store.setActiveProcesses([{ session_id: id, project_id: projectId, provider, state: 'assistant_turn', pending_requests: [] }])
    store.streamBlockStart(id, messageId, 0, blockType)
    await settle()
    const next = store.localState.streamingBlocks[id].blocks[0].publicationIdentity
    if (!baseline) assert(previous !== next, 'Reconnect keeps old block generation')
    feed('Text after reconnect')
    await settle(700)
    return report('same-field reconnect', snapshot())
}
// Browser control can exercise the remaining production controls and save these diagnostics.
window.invisibleStreamingFixture = { store, router, provider, baseline, publicationRateBaseline, ids: { mainId, otherId, agentId }, counters,
    start, feed, settle, snapshot, geometry, resetCounters, navigate, switchSession, selectSubagent,
    maximizeDock, restoreDock, runDockPreflight, toggleSecondary, secondaryActive, runHiddenThinking, runVisible, readingAbove, displayMode, reconnectSameMessage,
    runPublicationRateScenario, viewportDiagnostics, sessionRows, scrollerDiagnostics, runLargeHistorySuspension, runPendingRevealHide, runReconcileHideReturn,
    clear(id = currentId()) { destroyFixtureSessionBuffers(id); delete store.localState.streamingBlocks[id]; store.recomputeVisualItems(id) },
    exportEvidence() { return JSON.stringify({ reports: reports.value, requests, errors, lifetimes }, null, 2) },
}
async function initializeFixture() {
    try {
        await settle(450)
        if (viewInstance()?.$.setupState.layout.render.value.mode !== 'tabs') await runDockPreflight()
        else reports.value.push({ dockPreflightSkipped: true, viewport: viewportDiagnostics() })
        fixtureReady.value = true
        reports.value.push({ fixtureReady: true, storeShared: useDataStore(pinia) === store, ...snapshot() })
    } catch (error) {
        startupFailure.value = String(error)
        errors.push(startupFailure.value)
        reports.value.push({ fixtureReady: false, startupFailure: startupFailure.value, ...snapshot() })
    }
}
await initializeFixture()
