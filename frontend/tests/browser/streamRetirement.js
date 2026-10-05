// Isolated controls over the mounted production conversation. No backend writes or provider launch.
import { nextTick, unref } from 'vue'
import { DISPLAY_LEVEL, SYNTHETIC_ITEM } from '../../src/constants.js'
import { isBufferActive } from '../../src/utils/streamingBuffer.js'
import { getParsedContent } from '../../src/utils/parsedContent.js'
import { installImportedScrollerQuarantine, runBoundedScrollerAction } from './scrollerConversationHarness.js'
import { waitForMarkdownCondition as waitFor } from './markdownRenderingHarness.js'
import { reasoningCompletion, runStreamRetirementScenario } from './streamRetirementHarness.js'

const quarantine = installImportedScrollerQuarantine(document)
const query = new URLSearchParams(location.search)
query.set('provider', 'codex'); query.delete('baseline'); query.delete('publicationRateBaseline')
history.replaceState(null, '', `${location.pathname}?${query}`)
const reports = [], requests = []
const controls = document.createElement('section')
controls.style.cssText = 'position:fixed;bottom:0;left:0;z-index:2000;background:Canvas;padding:6px;max-height:24vh;overflow:auto'
const status = document.createElement('strong')
status.id = 'stream-retirement-status'; status.textContent = 'Startup pending'
const evidence = document.createElement('pre'); evidence.id = 'stream-retirement-report'
controls.append(status, evidence); document.body.append(controls)
let fixture, ready = false, busy = false, locked = false, sequence = 0, rest = null
let retiredPairs = [], hookPairs = [], scrollHookReads = [], heightSeeds = [], retirementActive = false, restoreHook = () => {}
const buttons = []
function check(value, message) { if (!value) throw new Error(message) }
function list() {
    for (const el of document.querySelectorAll('#conversation .virtual-scroller')) {
        for (let instance = el.__vueParentComponent; instance; instance = instance.parent) {
            if (instance.type.__file?.endsWith('SessionItemsList.vue') && instance.props.sessionId === fixture.ids.mainId) return instance
        }
    }
}
function scroller() { return unref(list()?.setupState.scrollerRef) }
function row(line) { return list()?.proxy.$el.querySelector(`[data-line-num="${line}"]`) }
function details(line) { return row(line)?.querySelector('wa-details') }
function parsedText(item) {
    const parsed = getParsedContent(item)
    return parsed?.payload?.summary?.map(part => part.text).join('\n') ??
        parsed?.payload?.item?.content?.map(part => part.text).join('\n') ?? ''
}
function installHookObservation() {
    restoreHook()
    const entrance = list()?.setupState.entrance
    check(typeof entrance?.noteRetired === 'function', 'Mounted retirement hook is unavailable')
    const original = entrance.noteRetired
    const wrapped = pairs => { hookPairs.push(...pairs); return original.call(entrance, pairs) }
    entrance.noteRetired = wrapped
    const viewport = scroller()
    const getHeight = viewport.getItemHeight, seedHeight = viewport.seedItemHeight
    viewport.getItemHeight = line => {
        if (retirementActive) scrollHookReads.push(line)
        return getHeight.call(viewport, line)
    }
    viewport.seedItemHeight = (line, height) => {
        if (retirementActive) heightSeeds.push({ line, height })
        return seedHeight.call(viewport, line, height)
    }
    restoreHook = () => {
        if (entrance.noteRetired === wrapped) entrance.noteRetired = original
        viewport.getItemHeight = getHeight; viewport.seedItemHeight = seedHeight
    }
}
function installReads() {
    const original = window.fetch
    window.fetch = async (input, options = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href)
        const method = (options.method || input.method || 'GET').toUpperCase()
        check(method === 'GET', `Fixture rejects mutation: ${method} ${url.pathname}`)
        const base = `/api/projects/invisible-stream-fixture-project/sessions/${fixture.ids.mainId}/items/`
        if (rest && (url.pathname === base || url.pathname === `${base}metadata/`)) {
            const route = url.pathname === base ? 'content' : 'metadata'
            requests.push({ path: url.pathname, query: url.search, method, route, attempt: rest.attempt })
            if (route === 'metadata') return Response.json(rest.items.map(item => ({ line_num: item.line_num, kind: item.kind, display_level: item.display_level,
                group_head: item.group_head, group_tail: item.group_tail, timestamp: item.timestamp ?? null })))
            const owner = rest
            return new Promise(resolve => { owner.releases.push(resolve) })
        }
        return original(input, options)
    }
}
const adapter = {
    async prepare(options) {
        if (fixture.router.currentRoute.value.params.sessionId !== fixture.ids.mainId) await fixture.switchSession()
        await fixture.navigate('main'); fixture.clear(); rest = null
        fixture.store.sessions[fixture.ids.mainId].draft = true
        retiredPairs = []; hookPairs = []; scrollHookReads = []; heightSeeds = []
        const realLine = fixture.store.sessionItems[fixture.ids.mainId].length + 1
        const messageId = `retirement-fixture-${++sequence}`
        const summaryTexts = Array.from({ length: options.summaryParts ?? 1 }, (_, index) =>
            `Complete ${options.blockType} fixture text ${sequence}, part ${index + 1}.`)
        const text = summaryTexts.join('\n')
        const payload = options.blockType === 'thinking'
            ? reasoningCompletion(messageId, summaryTexts)
            : { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', id: messageId, content: [{ type: 'Text', text }] } } }
        const item = { line_num: realLine, kind: options.blockType === 'thinking' ? 'reasoning' : 'assistant_message',
            display_level: DISPLAY_LEVEL.ALWAYS, group_head: options.blockType === 'thinking' ? realLine : null,
            group_tail: options.blockType === 'thinking' ? realLine : null, content: JSON.stringify(payload) }
        installHookObservation()
        return { messageId, text, summaryTexts, realLine, streamingLine: SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum, item, blockType: options.blockType }
    },
    async start(s) { fixture.start(s.blockType, fixture.ids.mainId, s.messageId) },
    async feed(s) {
        fixture.feed(s.text, fixture.ids.mainId, s.messageId); await fixture.settle(450)
        await scroller().scrollToKey(s.streamingLine, { align: 'end', maxAttempts: 20 })
        await fixture.settle(150)
    },
    async open(s) {
        if (s.blockType !== 'thinking') return
        const detail = details(s.streamingLine)
        check(detail, 'Streaming thinking detail is missing')
        detail.show(); await fixture.settle(450)
        check(detail.open && fixture.store.isDetailOpen(fixture.ids.mainId, `line:${s.streamingLine}:0`), 'Streaming detail does not open')
        // Exercise the actual fake-group state transfer, independently from normal-mode visibility.
        fixture.store.toggleExpandedGroup(fixture.ids.mainId, s.streamingLine)
    },
    async position(s, options) {
        await fixture.settle(450)
        if (options.order === 'item-before-start' && s.blockType === 'thinking') {
            await scroller().scrollToKey(s.realLine, { align: 'end', maxAttempts: 20 })
            await waitFor(() => Boolean(details(s.realLine)))
            details(s.realLine).show(); await fixture.settle(450)
        }
        if (options.position === 'reading-above') scroller().setScrollTop(200)
        else await scroller().scrollToKey(options.order === 'item-before-start' ? s.realLine : s.streamingLine, { align: 'end', maxAttempts: 20 })
        await fixture.settle(450)
        return fixture.geometry()
    },
    async insert(s) {
        fixture.store.addSessionItems(fixture.ids.mainId, [s.item])
        fixture.store.sessions[fixture.ids.mainId].last_line = s.realLine
    },
    async end(s) {
        fixture.store.streamBlockStop(fixture.ids.mainId, s.messageId, 0)
        fixture.store.streamBlockEnd(fixture.ids.mainId, s.messageId, 0, s.messageId)
    },
    async startNewer(s) {
        const messageId = `${s.messageId}-B`
        fixture.start(s.blockType, fixture.ids.mainId, messageId)
        fixture.feed('Current B buffer text.', fixture.ids.mainId, messageId)
        await fixture.settle(450)
        await scroller().scrollToKey(s.streamingLine, { align: 'end', maxAttempts: 20 })
        if (s.blockType === 'thinking') {
            await waitFor(() => Boolean(details(s.streamingLine)))
            details(s.streamingLine).show()
        }
        await fixture.settle(450)
        const block = fixture.store.localState.streamingBlocks[fixture.ids.mainId]?.blocks[0]
        check(block, 'Current B block is missing')
        s.newer = { messageId, block, publicationIdentity: block.publicationIdentity }
    },
    async observeNewer(s) {
        await fixture.settle(100)
        const stream = fixture.store.localState.streamingBlocks[fixture.ids.mainId]
        const block = stream?.blocks[0]
        return { messageId: stream?.messageId, text: block?.text,
            bufferActive: isBufferActive(fixture.ids.mainId, s.newer.messageId, 0, s.newer.publicationIdentity),
            sameBlock: block === s.newer.block, samePublication: block?.publicationIdentity === s.newer.publicationIdentity,
            domText: row(s.streamingLine)?.innerText ?? '' }
    },
    async continueNewer(s) {
        const expectedText = `${s.newer.block.text} Continued B buffer text.`
        fixture.feed(' Continued B buffer text.', fixture.ids.mainId, s.newer.messageId)
        await fixture.settle(450)
        check(fixture.store.localState.streamingBlocks[fixture.ids.mainId]?.blocks[0]?.text.endsWith(' Continued B buffer text.'),
            'Current B buffer does not accept the next delta')
        return expectedText
    },
    async lateStart(s) { fixture.start(s.blockType, fixture.ids.mainId, s.messageId); await fixture.settle(100) },
    async settle(ms) { await fixture.settle(ms) },
    async beginRest(s) {
        // Interception is already installed. Each pending request owns an explicit deferred response.
        rest = { items: [...fixture.store.sessionItems[fixture.ids.mainId], s.item], releases: [], attempt: 1, requestStart: requests.length }
        await fixture.switchSession()
        fixture.store.sessions[fixture.ids.mainId].draft = false
        fixture.store.sessions[fixture.ids.mainId].last_line = s.realLine
        fixture.store.localState.sessions[fixture.ids.mainId].itemsFetched = false
        await fixture.switchSession()
        await waitFor(() => rest.releases.length > 0)
    },
    async pending(s, options) {
        await waitFor(() => requests.slice(rest.requestStart).some(request => request.route === 'metadata' && request.attempt === rest.attempt) && (options.stage === 'failed' || rest.releases.length > 0))
        const block = fixture.store.localState.streamingBlocks[fixture.ids.mainId]?.blocks[0]
        return { attempt: rest.attempt, metadataRequests: requests.slice(rest.requestStart).filter(r => r.route === 'metadata' && r.attempt === rest.attempt).length,
            contentRequests: requests.slice(rest.requestStart).filter(r => r.route === 'content' && r.attempt === rest.attempt).length,
            retainedText: block?.text, ended: block?.uuid === s.messageId, stopped: Boolean(block?.stopped),
            detailOpen: fixture.store.isDetailOpen(fixture.ids.mainId, `line:${s.streamingLine}:0`),
            groupOpen: fixture.store.localState.sessionExpandedGroups[fixture.ids.mainId]?.includes(s.streamingLine) ?? false }
    },
    async fail() {
        for (const release of rest.releases.splice(0)) release(Response.json({}, { status: 503 }))
        await waitFor(() => fixture.store.localState.sessions[fixture.ids.mainId].itemsLoadingError)
        await waitFor(() => Boolean(document.querySelector('#conversation .fetch-error-panel wa-button')))
    },
    async retry() {
        const retry = document.querySelector('#conversation .fetch-error-panel wa-button')
        check(retry?.textContent.includes('Retry') && !retry.disabled, 'Real Retry control is unavailable')
        rest.attempt++
        retry.click()
        await waitFor(() => rest.releases.length > 0)
    },
    async resolve() {
        for (const release of rest.releases.splice(0)) release(Response.json(rest.items))
        await waitFor(() => !fixture.store.localState.sessions[fixture.ids.mainId].itemsLoading && !fixture.store.localState.sessions[fixture.ids.mainId].itemsLoadingError)
        await nextTick(); await fixture.settle(700)
        // Keep subsequent fixture actions read-only when they change layout intention.
        fixture.store.sessions[fixture.ids.mainId].draft = true
        installHookObservation()
    },
    async observe(s, options) {
        await fixture.settle(100)
        const mounted = list(), viewport = scroller()
        const savedTop = fixture.geometry()?.scrollTop
        let reveal = null
        if (mounted && viewport && options.position === 'reading-above') {
            reveal = await viewport.scrollToKey(s.realLine, { align: 'start', maxAttempts: 20 })
            await fixture.settle(150)
        }
        const domRows = [...(mounted?.proxy.$el.querySelectorAll('[data-line-num]') ?? [])].map(el => Number(el.dataset.lineNum))
        const visibleText = row(s.realLine)?.innerText ?? ''
        const domDetailOpen = details(s.realLine)?.open ?? false
        if (options.position === 'reading-above' && viewport && savedTop != null) {
            viewport.setScrollTop(savedTop); await fixture.settle(150)
        }
        return { domObserved: Boolean(mounted && viewport?.$el?.clientHeight > 0), reveal,
            visualRows: fixture.store.getSessionVisualItems(fixture.ids.mainId).map(item => item.lineNum), domRows,
            persistedText: parsedText(fixture.store.sessionItems[fixture.ids.mainId][s.realLine - 1]), visibleText,
            detailOpen: fixture.store.isDetailOpen(fixture.ids.mainId, `line:${s.realLine}:0`), domDetailOpen,
            groupTransferred: !fixture.store.localState.sessionExpandedGroups[fixture.ids.mainId]?.includes(s.streamingLine) &&
                (fixture.store.localState.sessionExpandedGroups[fixture.ids.mainId]?.includes(s.realLine) ?? false),
            retiredPairs: [...retiredPairs], hookPairs: [...hookPairs], scrollHookReads: [...scrollHookReads], heightSeeds: [...heightSeeds], geometry: fixture.geometry(),
            errors: fixture.snapshot().errors, snapshot: fixture.snapshot(), requests: [...requests] }
    },
}
async function run(options = {}) {
    check(ready && !busy && !locked, 'Fixture is unavailable or busy')
    quarantine(); busy = true; buttons.forEach(button => { button.disabled = true })
    status.textContent = 'Scenario running'
    try {
        const report = await runStreamRetirementScenario(adapter, options)
        reports.push(report); locked ||= report.requiresReload
        status.textContent = `Scenario ${report.status}`
        evidence.textContent = JSON.stringify({ reports, requests }, null, 2)
        return report
    } finally { busy = false; quarantine(); buttons.forEach(button => { button.disabled = locked }) }
}
for (const blockType of ['text', 'thinking']) for (const order of ['item-before-end', 'end-before-item', 'item-before-start']) {
    const button = document.createElement('button'); button.textContent = `${blockType}: ${order}`; button.disabled = true
    button.onclick = () => run({ blockType, order }); buttons.push(button); controls.append(button)
}
for (const blockType of ['text', 'thinking']) {
    const button = document.createElement('button'); button.textContent = `${blockType}: late A preserves current B`; button.disabled = true
    button.onclick = () => run({ blockType, lateNewer: true }); buttons.push(button); controls.append(button)
}
{
    const button = document.createElement('button'); button.textContent = 'thinking: multiple summary parts'; button.disabled = true
    button.onclick = () => run({ blockType: 'thinking', summaryParts: 2 }); buttons.push(button); controls.append(button)
}
for (const loading of ['slow-rest', 'failed-rest-retry']) {
    const button = document.createElement('button'); button.textContent = `thinking: ${loading}`; button.disabled = true
    button.onclick = () => run({ blockType: 'thinking', loading }); buttons.push(button); controls.append(button)
}
window.streamRetirementFixture = { run, reports, requests, get ready() { return ready }, exportEvidence: () => JSON.stringify({ reports, requests }, null, 2) }
try {
    const startup = await runBoundedScrollerAction(async () => {
        await import('./invisibleStreaming.js'); quarantine()
        await waitFor(() => document.querySelector('#fixture-status')?.textContent === 'Fixture ready')
    })
    locked = startup.requiresReload
    check(startup.status === 'passed', startup.error)
    fixture = window.invisibleStreamingFixture
    check(fixture.provider === 'codex' && !fixture.baseline && !fixture.publicationRateBaseline, 'Current Codex production fixture is required')
    installReads()
    fixture.store.$onAction(({ name, args, after }) => {
        if (name !== '_retireStreamingBlocks' || args[0] !== fixture.ids.mainId) return
        retirementActive = true
        after(pairs => { retiredPairs.push(...pairs); retirementActive = false })
    })
    ready = true; status.textContent = 'Fixture ready'; buttons.forEach(button => { button.disabled = false })
} catch (error) { status.textContent = 'Startup failed/inconclusive'; evidence.textContent = String(error) }
