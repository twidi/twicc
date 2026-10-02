// Real production consumers. All substitutions stay in this fixture; mutation fetches remain blocked.
import { createApp, h, nextTick, unref } from 'vue'
import SessionList from '../../src/components/session/list/SessionList.vue'
import { useSettingsStore } from '../../src/stores/settings'
import { DISPLAY_LEVEL, DISPLAY_MODE } from '../../src/constants'
import { waitForMarkdownCondition as waitFor } from './markdownRenderingHarness.js'
import { runBoundedScrollerAction, recoverScrollerConversation, installImportedScrollerQuarantine, replaceScrollerContentWithMetadata, makeScrollerRecoveryItem } from './scrollerConversationHarness.js'
import { getParsedContent, hasContent } from '../../src/utils/parsedContent.js'
const quarantineImportedControls = installImportedScrollerQuarantine(document)
const reports = [], requests = []
let fixture, ready = false, busy = false, locked = false, activeAction = null
const actionDeadlineMs = 20000
let loadingProbe = null
const controls = document.createElement('section')
controls.style.cssText = 'position:fixed;bottom:0;left:0;z-index:2000;background:Canvas;max-height:22vh;overflow:auto;padding:4px'
const status = document.createElement('strong'); status.id = 'scroller-conversation-status'; status.textContent = 'Startup pending'
const evidence = document.createElement('pre'); evidence.id = 'scroller-conversation-report'; evidence.hidden = true
controls.append(status, evidence); document.body.append(controls)
const buttons = []
function check(value, message) { if (!value) throw new Error(message) }
function diagnostics() {
    const imported = document.querySelector('#fixture-controls')
    const importedActions = [...(imported?.querySelectorAll('button,input,select,textarea') ?? [])]
    return { viewport: { width: innerWidth, height: innerHeight }, visibility: document.visibilityState,
        controls: { ready, busy, locked, actionDeadlineMs, activeAction },
        importedControls: { present: Boolean(imported), inert: imported?.inert ?? null, actionCount: importedActions.length,
            hiddenActions: importedActions.filter(action => action.hidden).length, disabledActions: importedActions.filter(action => action.disabled).length },
        provider: fixture?.provider, moduleWarm: Boolean(fixture), feed: 'Seeded synthetic read-only data; bounded browser controls',
        geometry: fixture?.geometry(), loading: loadingDiagnostics(), requests: [...requests], snapshot: fixture?.snapshot() }
}
function loadingDiagnostics() {
    if (!loadingProbe || !fixture) return null
    const { line } = loadingProbe, id = fixture.ids.mainId
    const state = list()?.setupState
    const raw = fixture.store.sessionItems[id]?.[line - 1]
    const visualItems = fixture.store.getSessionVisualItems(id)
    const index = visualItems.findIndex(item => item.lineNum === line)
    const visual = visualItems[index]
    return { ...loadingProbe, rawHasContent: raw ? hasContent(raw) : null, rawParsedAvailable: raw ? getParsedContent(raw) !== null : null,
        visualIndex: index, visualHasContent: visual ? hasContent(visual) : null, visualParsedAvailable: visual ? getParsedContent(visual) !== null : null,
        gapReady: Boolean(unref(state?.gapReady)), gapMounted: Boolean(unref(state?.gapMounted)), gapActive: Boolean(unref(state?.gapActive)),
        sessionActive: Boolean(unref(state?.sessionActive)), viewActive: list()?.props.viewActive ?? null,
        gapRange: unref(state?.gapRange) ?? null, missingLines: [...(unref(state?.missingLines) ?? [])],
        scrollerSuspended: Boolean(unref(scroller()?.suspended)), scrollState: scroller()?.getScrollState() ?? null,
        renderedRowPresent: Boolean(row(line)), fixtureReadCount: requests.length - loadingProbe.requestStart }
}
function loadingStage(stage, line) {
    loadingProbe = { stage, line, requestStart: loadingProbe?.line === line ? loadingProbe.requestStart : requests.length }
    if (activeAction) activeAction.stage = stage
    evidence.textContent = JSON.stringify({ reports, ...diagnostics() }, null, 2)
}
function add(label, action) {
    const button = document.createElement('button'); button.textContent = label; button.disabled = true
    button.onclick = async () => {
        if (!ready || busy || locked) return
        quarantineImportedControls()
        busy = true
        activeAction = { name: label, startedAt: performance.now(), deadlineMs: actionDeadlineMs }
        activeAction.deadlineAt = activeAction.startedAt + actionDeadlineMs
        buttons.forEach(b => { b.disabled = true })
        const report = { name: label, status: 'pending', ...diagnostics() }
        reports.push(report)
        status.textContent = `Scroller conversation running: ${label}`
        evidence.textContent = JSON.stringify({ reports, ...diagnostics() }, null, 2)
        try {
            const outcome = await runBoundedScrollerAction(action, { timeoutMs: actionDeadlineMs })
            Object.assign(report, outcome)
            if (outcome.requiresReload) { locked = true; ready = false }
        } catch (error) {
            Object.assign(report, { status: 'failed/inconclusive', error: String(error) })
        } finally {
            quarantineImportedControls()
            busy = false; activeAction = null
            buttons.forEach(b => { b.disabled = !ready || locked })
            status.textContent = locked ? 'Scroller conversation inconclusive; reload required'
                : `Scroller conversation ${report.status}: ${label}`
            Object.assign(report, diagnostics())
            evidence.textContent = JSON.stringify({ reports, ...diagnostics() }, null, 2)
        }
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
    loadingStage('waiting-for-initial-row-and-content', item.line_num)
    await reveal(item.line_num, 'Before missing-content substitution.')
    itemRead = { item: makeScrollerRecoveryItem(item, JSON.stringify(content('Recovered same-index content marker.'))), fail: true }
    const before = requests.length
    // This public action clears the cached parsed source before production visual recomputation.
    const substitution = replaceScrollerContentWithMetadata(fixture, item.line_num)
    loadingStage('waiting-for-failed-read', item.line_num)
    await waitFor(() => requests.length > before)
    await new Promise(resolve => setTimeout(resolve, 350))
    const failedRequests = requests.length - before
    check(failedRequests === 1, 'Failed gap load loops or duplicates')
    loadingStage('failed-read-settled', item.line_num)
    itemRead.fail = false
    // A real KeepAlive recovery lifecycle supplies the next demand; no automatic retry policy is added.
    loadingStage('waiting-for-KeepAlive-recovery', item.line_num)
    const lifecycle = await recoverScrollerConversation(fixture)
    loadingStage('waiting-for-recovered-row-and-content', item.line_num)
    const result = await reveal(item.line_num, 'Recovered same-index content marker.')
    itemRead = null
    loadingStage('recovered-content-verified', item.line_num)
    return { substitution, lifecycle, failedRequests, requestsAfterRecovery: requests.length - before, result }
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
    busy = true
    activeAction = { name: 'startup', startedAt: performance.now(), deadlineMs: actionDeadlineMs }
    activeAction.deadlineAt = activeAction.startedAt + actionDeadlineMs
    evidence.textContent = JSON.stringify({ status: 'pending', ...diagnostics() }, null, 2)
    const startup = await runBoundedScrollerAction(async () => {
        await import('./invisibleStreaming.js')
        quarantineImportedControls()
        const loaded = window.invisibleStreamingFixture
        await waitFor(() => document.querySelector('#fixture-status')?.textContent === 'Fixture ready')
        check(!loaded.baseline && !loaded.publicationRateBaseline, 'Production current components required')
        return loaded
    }, { timeoutMs: actionDeadlineMs })
    if (startup.status !== 'passed') {
        locked = startup.requiresReload
        reports.push({ name: 'startup', ...startup })
        throw new Error(startup.error)
    }
    fixture = startup.result
    busy = false; activeAction = null
    installReads(); ready = true; status.textContent = 'Scroller conversation ready'
    buttons.forEach(b => { b.disabled = false }); evidence.textContent = JSON.stringify({ status: 'ready', ...diagnostics() }, null, 2)
} catch (error) {
    busy = false; activeAction = null
    status.textContent = locked ? 'Scroller conversation startup inconclusive; reload required' : 'Scroller conversation startup failed'
    evidence.textContent = JSON.stringify({ status: 'failed/inconclusive', error: String(error), reports, ...diagnostics() }, null, 2)
}
