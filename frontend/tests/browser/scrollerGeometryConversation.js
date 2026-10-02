// Real production consumers. All substitutions stay in this fixture; mutation fetches remain blocked.
import { createApp, h, nextTick } from 'vue'
import SessionList from '../../src/components/session/list/SessionList.vue'
import { useSettingsStore } from '../../src/stores/settings'
import { DISPLAY_LEVEL, DISPLAY_MODE } from '../../src/constants'
import { waitForMarkdownCondition as waitFor } from './markdownRenderingHarness.js'
const reports = [], requests = []
let fixture, ready = false, busy = false
const controls = document.createElement('section')
controls.style.cssText = 'position:fixed;bottom:0;left:0;z-index:2000;background:Canvas;max-height:22vh;overflow:auto;padding:4px'
const status = document.createElement('strong'); status.id = 'scroller-conversation-status'; status.textContent = 'Startup pending'
const evidence = document.createElement('pre'); evidence.id = 'scroller-conversation-report'; evidence.hidden = true
controls.append(status, evidence); document.body.append(controls)
const buttons = []
function check(value, message) { if (!value) throw new Error(message) }
function diagnostics() {
    return { viewport: { width: innerWidth, height: innerHeight }, visibility: document.visibilityState,
        provider: fixture?.provider, moduleWarm: Boolean(fixture), feed: 'Seeded synthetic read-only data; bounded browser controls',
        geometry: fixture?.geometry(), requests: [...requests], snapshot: fixture?.snapshot() }
}
function add(label, action) {
    const button = document.createElement('button'); button.textContent = label; button.disabled = true
    button.onclick = async () => {
        if (!ready || busy) return
        busy = true; buttons.forEach(b => { b.disabled = true })
        try { reports.push({ name: label, status: 'passed', result: await action(), ...diagnostics() }) }
        catch (error) { reports.push({ name: label, status: 'failed/inconclusive', error: String(error), ...diagnostics() }) }
        finally { busy = false; buttons.forEach(b => { b.disabled = !ready }); evidence.textContent = JSON.stringify({ reports, ...diagnostics() }, null, 2) }
    }
    buttons.push(button); controls.append(button)
}
function list() {
    for (const element of document.querySelectorAll('#conversation .virtual-scroller')) {
        for (let instance = element.__vueParentComponent; instance; instance = instance.parent) {
            if (instance.type.__file?.endsWith('SessionItemsList.vue') && instance.props.sessionId === fixture.ids.mainId) return instance
        }
    }
}
function scroller() { return list()?.setupState.scrollerRef }
function content(text) {
    return fixture.provider === 'claude_code' ? { type: 'assistant', uuid: 'geometry-fixture', message: { id: 'geometry-fixture', role: 'assistant', content: [{ type: 'text', text }] } }
        : { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', content: [{ type: 'Text', text }] } } }
}
function seed(text, extra = {}) {
    const id = fixture.ids.mainId, line = fixture.store.sessionItems[id].length + 1
    const item = { line_num: line, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
        group_head: null, group_tail: null, content: JSON.stringify(content(text)), ...extra }
    fixture.store.addSessionItems(id, [item]); fixture.store.sessions[id].last_line = line
    return item
}
function row(line) { return list()?.proxy.$el.querySelector(`[data-line-num="${line}"]`) }
async function reveal(line, marker) {
    await nextTick()
    const result = await scroller()?.scrollToKey(line, { align: 'start', maxAttempts: 20 })
    check(result === true, 'scrollToKey fails')
    await waitFor(() => Boolean(row(line)))
    const present = Boolean(row(line))
    await waitFor(() => row(line)?.textContent.includes(marker))
    return { line, scrollToKey: result, rowPresent: present, componentContent: row(line).textContent }
}
async function initial() {
    await fixture.navigate('main'); fixture.clear()
    const item = seed('Scroller initial reveal marker.')
    return reveal(item.line_num, 'Scroller initial reveal marker.')
}
add('Initial reveal', initial)
add('Near-bottom growth', async () => {
    await fixture.runVisible('text')
    const geometry = fixture.geometry()
    check(geometry?.bottomGap <= 150, 'Native bottom-follow gap exceeds 150px')
    return { geometry, nativeBottomFollowing: true, source: 'Existing bounded runVisible feed and final-row assertions' }
})
add('Reading-above anchor', async () => { await fixture.navigate('main'); return fixture.readingAbove() })
add('Streaming retirement', async () => {
    await fixture.runVisible('text')
    check(!fixture.store.localState.streamingBlocks[fixture.ids.mainId], 'Streaming row does not retire')
    const line = fixture.store.sessions[fixture.ids.mainId].last_line
    return reveal(line, 'Visible update 23.')
})
add('Group expand/collapse', async () => {
    await fixture.navigate('main'); fixture.clear()
    const settings = useSettingsStore(fixture.store._p)
    settings.setDisplayMode(DISPLAY_MODE.SIMPLIFIED)
    try {
        const head = seed('Group head marker.')
        const child = seed('Group child marker.', { display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: head.line_num })
        fixture.store.addSessionItems(fixture.ids.mainId, [{ ...head, group_tail: child.line_num }])
        await nextTick()
        await reveal(head.line_num, 'Group head marker.')
        const before = fixture.store.getSessionVisualItems(fixture.ids.mainId).length
        // Use the component's real toggle handler, including its group-motion bookkeeping.
        check(typeof list()?.setupState.toggleGroup === 'function', 'Production group toggle is unavailable')
        list().setupState.toggleGroup(head.line_num)
        const expanded = fixture.store.getSessionVisualItems(fixture.ids.mainId).length
        check(expanded > before, 'Group expansion does not add visual rows')
        const revealed = await reveal(child.line_num, 'Group child marker.')
        list().setupState.toggleGroup(head.line_num)
        await nextTick()
        check(fixture.store.getSessionVisualItems(fixture.ids.mainId).length === before, 'Group collapse does not restore membership')
        return { before, expanded, revealed, collapsed: before }
    } finally { settings.setDisplayMode(DISPLAY_MODE.NORMAL) }
})
let itemRead = null, pageRead = null
function installReads() {
    const original = window.fetch
    window.fetch = async (input, options = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href)
        const method = (options.method || input.method || 'GET').toUpperCase()
        check(method === 'GET', `Fixture rejects mutation: ${method} ${url.pathname}`)
        if (itemRead && url.pathname === `/api/projects/invisible-stream-fixture-project/sessions/${fixture.ids.mainId}/items/`) {
            const entry = { path: url.pathname, query: url.search, method, substitute: 'same-index missing-content response', failed: itemRead.fail }
            requests.push(entry)
            return itemRead.fail ? Response.json({}, { status: 503 }) : Response.json([itemRead.item])
        }
        if (pageRead && url.pathname === '/api/projects/scroller-pagination-fixture/sessions/') {
            const index = pageRead.calls++
            requests.push({ path: url.pathname, query: url.search, method, substitute: 'filtered short page', index })
            check(index < 3, 'Pagination requests exceed the bounded three pages')
            return Response.json({ sessions: [{ id: `geometry-page-${index}`, project_id: 'scroller-pagination-fixture',
                title: index === 2 ? 'geometry-match' : 'filtered-out', provider: fixture.provider, mtime: 90 - index * 10,
                last_line: 0, type: 'session' }], has_more: index < 2 })
        }
        return original(input, options)
    }
}
add('Missing content recovery', async () => {
    await fixture.navigate('main'); fixture.clear()
    const item = seed('Before missing-content substitution.')
    await reveal(item.line_num, 'Before missing-content substitution.')
    const id = fixture.ids.mainId
    itemRead = { item: { ...item, content: JSON.stringify(content('Recovered same-index content marker.')) }, fail: true }
    const before = requests.length
    // Replace through production metadata/content APIs. Never inspect raw content or cache internals.
    const metadata = { line_num: item.line_num, kind: item.kind, display_level: item.display_level, group_head: null, group_tail: null }
    fixture.store.sessionItems[id][item.line_num - 1] = metadata
    fixture.store.recomputeVisualItems(id)
    await waitFor(() => requests.length > before)
    await new Promise(resolve => setTimeout(resolve, 350))
    const failedRequests = requests.length - before
    check(failedRequests === 1, 'Failed gap load loops or duplicates')
    itemRead.fail = false
    // A real KeepAlive recovery lifecycle supplies the next demand; no automatic retry policy is added.
    await fixture.navigate('other'); await fixture.navigate('main')
    const result = await reveal(item.line_num, 'Recovered same-index content marker.')
    itemRead = null
    return { failedRequests, requestsAfterRecovery: requests.length - before, result }
})
add('Filtered session pagination', async () => {
    check(!pageRead, 'Pagination acceptance runs once per fresh page')
    const projectId = 'scroller-pagination-fixture', store = fixture.store
    store.projects[projectId] = { id: projectId, name: 'Geometry pagination', directory: '/geometry-pagination', sessions_count: 3 }
    const state = store._ensureProjectLocalState(projectId)
    state.sessionsFetched = true; state.sessionsLoading = false; state.hasMoreSessions = true; state.oldestSessionMtime = 100
    pageRead = { calls: 0 }
    const host = document.createElement('div')
    host.id = 'geometry-pagination-viewport'; host.style.cssText = 'position:fixed;top:4px;right:4px;width:min(320px,90vw);height:260px;z-index:1800;background:Canvas;display:flex;flex-direction:column'
    document.body.append(host)
    const app = createApp({ render: () => h(SessionList, { projectId, searchQuery: 'geometry-match' }) })
    // Reuse the exact production fixture Pinia and router.
    app.use(fixture.store._p); app.use(fixture.router)
    app.mount(host)
    try {
        await waitFor(() => pageRead.calls === 3 && state.sessionsLoading === false)
        await waitFor(() => host.textContent.includes('geometry-match'))
        check(state.hasMoreSessions === false, 'Pagination does not exhaust')
        const viewport = host.querySelector('.virtual-scroller')
        check(viewport?.clientHeight > 0, 'Actual SessionList viewport is not measured')
        return { pages: pageRead.calls, canonicalIds: store.getProjectSessions(projectId).map(s => s.id),
            cursor: state.oldestSessionMtime, hasMore: state.hasMoreSessions,
            viewport: { width: viewport.clientWidth, height: viewport.clientHeight }, text: host.textContent }
    } finally { app.unmount(); host.remove() }
})
try {
    const query = new URLSearchParams(location.search)
    if (query.has('baseline') || query.has('publicationRateBaseline')) throw new Error('Production conversation entry rejects baseline adapters')
    await import('./invisibleStreaming.js')
    fixture = window.invisibleStreamingFixture
    await waitFor(() => document.querySelector('#fixture-status')?.textContent === 'Fixture ready')
    check(!fixture.baseline && !fixture.publicationRateBaseline, 'Production current components required')
    installReads(); ready = true; status.textContent = 'Scroller conversation ready'
    buttons.forEach(b => { b.disabled = false }); evidence.textContent = JSON.stringify({ status: 'ready', ...diagnostics() }, null, 2)
} catch (error) {
    status.textContent = 'Scroller conversation startup failed'
    evidence.textContent = JSON.stringify({ status: 'failed', error: String(error), ...diagnostics() }, null, 2)
}
