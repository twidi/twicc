// Full production conversation fixture. No main.js bootstrap or live WebSocket.
import { createApp, h, KeepAlive, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'
import SessionItemsList from '../../src/components/session/detail/SessionItemsList.vue'
import SessionView from '../../src/views/SessionView.vue'
import { useDataStore } from '../../src/stores/data'
import { useSettingsStore } from '../../src/stores/settings'
import { getParsedContent } from '../../src/utils/parsedContent'
import { DISPLAY_LEVEL, DISPLAY_MODE, SYNTHETIC_ITEM } from '../../src/constants'
import { streamPublicationRegistry } from '../../src/utils/streamPublicationRegistry.js'
import { destroySessionBuffers } from '../../src/utils/streamingBuffer.js'
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
const projectId = 'invisible-stream-fixture-project'
const mainId = 'invisible-stream-fixture-main', otherId = 'invisible-stream-fixture-other', agentId = 'invisible-stream-fixture-agent'
const ids = new Set([mainId, otherId, agentId])
const lineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum
const errors = [], requests = [], lifetimes = [], instances = new Map()
const counters = { bufferFrames: 0, drains: 0, envelopes: 0, transitions: 0, bootstraps: 0 }
const reports = ref([])
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
window.requestAnimationFrame = callback => {
    if (new Error().stack?.includes('streamingBuffer.js') || new Error().stack?.includes('invisibleStreamingBaselineBuffer.js')) counters.bufferFrames++
    return nativeRAF(callback)
}
const originalBind = streamPublicationRegistry.bindBlock
streamPublicationRegistry.bindBlock = (identity, callbacks) => originalBind(identity, {
    setActive(active) { counters.transitions++; callbacks.setActive(active) },
    snapshot() { counters.bootstraps++; callbacks.snapshot() },
})
const pinia = createPinia()
// Baseline must define data first in a fresh Pinia. All production components retrieve this same ID.
const dataFactory = baseline
    ? (await import(/* @vite-ignore */ '../../src/stores/invisibleStreamingBaselineData.js')).useDataStore
    : useDataStore
const store = dataFactory(pinia)
if (useDataStore(pinia) !== store) throw new Error('Production components do not share the selected data store')
const settings = useSettingsStore(pinia)
settings.setDisplayMode(DISPLAY_MODE.NORMAL)
store.projects[projectId] = { id: projectId, directory: '/invisible-stream-fixture', name: 'Invisible stream fixture', sessions_count: 3 }
store.projectsLoaded = true
function finalContent(text, blockType, messageId, uuid) {
    if (provider === 'claude_code') return { type: 'assistant', uuid, message: { id: messageId, role: 'assistant', content: [blockType === 'thinking' ? { type: 'thinking', thinking: text } : { type: 'text', text }] } }
    if (blockType === 'thinking') return { type: 'response_item', payload: { type: 'reasoning', summary: [{ type: 'summary_text', text }] } }
    return { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', content: [{ type: 'text', text }] } } }
}
function seedSession(id, parent = null) {
    const history = Array.from({ length: 100 }, (_, index) => ({
        line_num: index + 1, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, timestamp: '2026-10-02T12:00:00Z',
        content: JSON.stringify(finalContent(`History ${index + 1}. ${'Readable history content. '.repeat(8)}`, 'text', 'history', `history-${index}`)),
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
const originalDrain = store._onBufferDrain
store._onBufferDrain = (id, index, text) => {
    counters.drains++
    const row = store.localState.visualItemCache[id]?.get(lineNum - index)
    const before = row && getParsedContent(row)
    originalDrain(id, index, text)
    if (row && before !== getParsedContent(row)) counters.envelopes++
}
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
    h('div', { id: 'fixture-controls' }, [h('strong', `${provider} / ${baseline ? 'baseline aeaf1838' : 'implementation'}`),
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
        h('button', { onClick: toggleSecondary }, 'Show/hide second main Chat')]),
    h('div', { id: 'conversation' }, [h(RouterView, {}, { default: ({ Component, route }) => h(KeepAlive, {}, () => h(Component, { key: route.params.sessionId })) }),
        secondaryMounted.value ? h('aside', { style: { display: secondaryActive.value ? 'flex' : 'none', width: '38%', minHeight: 0, borderLeft: '1px solid gray' } },
            [h(SessionItemsList, { sessionId: mainId, projectId, viewActive: secondaryActive.value })]) : null]),
    h('pre', { id: 'fixture-report' }, JSON.stringify(reports.value, null, 2)),
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
    return { provider, baseline, counters: { ...counters }, geometry: geometry(), text: row()?.innerText || '',
        rows: lifetimes.filter(entry => entry.lineNum === lineNum), requests: [...requests], errors: [...errors] }
}
function resetCounters() { for (const key of Object.keys(counters)) counters[key] = 0 }
function assert(condition, message) { if (!condition) throw new Error(message) }
function report(name, before, extra = {}) { const result = { name, before, after: snapshot(), ...extra }; reports.value.push(result); return result }
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
    destroySessionBuffers(mainId)
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
        const uuid = `final-${messageId}`
        store.streamBlockStop(id, messageId, 0)
        store.streamBlockEnd(id, messageId, 0, uuid)
        const realLine = store.sessionItems[id].length + 1
        store.addSessionItems(id, [{ line_num: realLine, kind: blockType === 'thinking' && provider === 'codex' ? 'reasoning' : 'assistant_message',
            display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
            stream_uuid: provider === 'codex' ? uuid : undefined, content: JSON.stringify(finalContent(text, blockType, messageId, uuid)) }])
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
window.invisibleStreamingFixture = { store, router, provider, baseline, ids: { mainId, otherId, agentId }, counters,
    start, feed, settle, snapshot, geometry, resetCounters, navigate, switchSession, selectSubagent,
    maximizeDock, restoreDock, runDockPreflight, toggleSecondary, secondaryActive, runHiddenThinking, runVisible, readingAbove, displayMode, reconnectSameMessage,
    clear(id = currentId()) { destroySessionBuffers(id); delete store.localState.streamingBlocks[id]; store.recomputeVisualItems(id) },
    exportEvidence() { return JSON.stringify({ reports: reports.value, requests, errors, lifetimes }, null, 2) },
}
await settle(450)
await runDockPreflight()
reports.value.push({ fixtureReady: true, storeShared: useDataStore(pinia) === store, ...snapshot() })
