import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import * as agentLinks from '../utils/agentLinkIndex.js'
import * as visualItems from '../utils/visualItems.js'
import * as parsed from '../utils/parsedContent.js'
import * as constants from '../constants.js'
import { getSessionCutoffMs } from '../utils/sessions.js'
import { useFramePoolStore } from '../stores/framePool.js'
import { INLINE_ARTIFACT_CONTEXT } from '../inline-artifacts/context.js'
import { createInlineArtifactRuntime } from '../inline-artifacts/runtime.js'
import { createInlineTextContext, inlineArtifactPlacement } from '../inline-artifacts/rendering.js'
import { makeShareInlineAdapter } from './inlineAdapter.js'
import { createShareInlineCompletion } from './inlineCompletion.js'
import * as shareApi from './shims/shareApi.js'
import { connectShareLive } from './shims/shareLive.js'
import { compileComponent, makeRenderer, descendants, flush, deferred } from '../../tests/helpers/scrollerComponentHarness.js'
import * as chatBlocks from '../utils/chatBlocks.js'
import { splitMarkdownBlocks } from '../utils/markdown.js'

const navigationSource = readFileSync(new URL('../composables/useChatNavigation.js', import.meta.url), 'utf8')
    .replace(/^import .+ from .+\n/gm, '').replace('export function', 'function')
const useChatNavigation = new Function('computed', ...Object.keys(chatBlocks), `${navigationSource}; return useChatNavigation`)(
    Vue.computed, ...Object.values(chatBlocks))

const empty = { setup: () => () => null }
const tag = id => `<twicc:inline-artifact id="${id}" src="inline-artifacts/${id}/index.html" title="${id}" height="360" />`
const item = (line, text, kind = 'assistant_message') => ({ line_num: line, kind,
    display_level: 1, group_head: null, group_tail: null, timestamp: null,
    content: JSON.stringify({ type: kind === 'user_message' ? 'user' : 'assistant',
        message: { content: [{ type: 'text', text }], stop_reason: 'end_turn' } }) })
const meta = line => ({ session_id: 'root', provider: 'claude_code', mode: 'snapshot',
    last_line: line, ready: true, include_subagents: false, include_inline_artifacts: true,
    inline_artifacts_supported: true, show_timestamps: false, max_display_mode: 'normal' })
const manifest = line => ({ enabled: true, revision: line, artifacts: ['calculator', 'preferences'].map(id => ({
    source_session_id: 'root', artifact_id: id, publication: ['root', id === 'calculator' ? 2 : line, 0, 0],
    title: id, height: 360, entry_filename: 'index.html', status: 'ready', code_revision: id === 'calculator' ? 1 : line,
})) })

async function waitFor(predicate) {
    for (let attempt = 0; attempt < 100 && !predicate(); attempt++) {
        await new Promise(resolve => setTimeout(resolve, 5))
        await flush()
    }
    assert.ok(predicate(), 'expected asynchronous reconnect state')
}

function realStore(settings) {
    const source = readFileSync(new URL('./shims/dataStoreShim.js', import.meta.url), 'utf8')
    const body = source.replace(/^import .+ from .+\n/gm, '').replace('export const useDataStore', 'const useDataStore')
    const deps = { ...agentLinks, ...visualItems, ...parsed, ...constants, defineStore,
        markRaw: Vue.markRaw, getSessionCutoffMs, shareApi: shareApi.shareApi, useSettingsStore: () => settings }
    return new Function(...Object.keys(deps), `${body}; return useDataStore`)(...Object.values(deps))
}

async function mount(t, { initialManifest = manifest(263), mode = 'snapshot' } = {}) {
    const previous = { window: globalThis.window, location: globalThis.location, history: globalThis.history,
        fetch: globalThis.fetch, getComputedStyle: globalThis.getComputedStyle, WebSocket: globalThis.WebSocket }
    const events = new Map(), sockets = []
    globalThis.WebSocket = class {
        constructor(url) { this.url = url; sockets.push(this) }
        close() { this.closed = true; this.onclose?.() }
        sendMessage(message) { this.onmessage({ data: JSON.stringify(message) }) }
    }
    globalThis.window = { addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) }
    globalThis.location = { origin: 'http://share.test', pathname: '/share/test/', search: '', hash: '' }
    globalThis.history = { replaceState() {} }
    globalThis.getComputedStyle = () => ({ scrollPaddingTop: '0' })
    const requests = [], items = [item(2, tag('calculator')), item(10, 'History marker 010', 'user_message'), item(263, tag('preferences'))]
    let boundary = 263, metadataReply = null, metaReply = null, manifestReply = null
    globalThis.fetch = async (url, options = {}) => {
        requests.push(String(url))
        if (options.method === 'HEAD') return new Response(null, { status: 200 })
        if (url.endsWith('/api/meta/')) {
            const reply = metaReply ? await metaReply(options) : { ...meta(boundary), mode }
            return reply instanceof Response ? reply : Response.json(reply)
        }
        if (url.endsWith('/api/inline-artifacts/')) {
            const reply = manifestReply ? await manifestReply() : boundary === 263 ? initialManifest : manifest(boundary)
            return reply instanceof Response ? reply : Response.json(reply)
        }
        if (url.endsWith('/items/metadata/')) {
            const rows = metadataReply ? await metadataReply(options) : items
            if (rows instanceof Response) return rows
            return Response.json(rows.map(({ content, ...row }) => row))
        }
        if (url.includes('/api/items/?')) {
            const ranges = new URL(url, 'http://share.test').searchParams.getAll('range').map(range => range.split(':').map(Number))
            return Response.json(items.filter(row => ranges.some(([lo, hi = lo]) => row.line_num >= lo && row.line_num <= hi)))
        }
        if (url.endsWith('/tool-states/')) return Response.json({ tools: {} })
        throw new Error(`Unexpected fixture request ${url}`)
    }
    const pinia = createPinia(); setActivePinia(pinia)
    const settings = Vue.reactive({ areMessageTimestampsShown: false, displayMode: 'normal',
        get getDisplayMode() { return this.displayMode }, setDisplayMode(mode) { this.displayMode = mode } })
    const useDataStore = realStore(settings), store = useDataStore()
    const scroller = { mounts: 0, top: Vue.ref(3310), node: null }
    const Scroller = { props: ['items'], setup(props, { slots, expose }) {
        scroller.mounts++
        const bottom = () => 3310 + Math.max(0, props.items.length - 3) * 40
        expose({ suspended: false, scrollTop: scroller.top,
            isAtTop: () => scroller.top.value === 0, isAtBottom: () => scroller.top.value >= bottom(),
            getScrollState: () => ({ clientHeight: 140, scrollTop: scroller.top.value }),
            scrollToEdge: edge => { scroller.top.value = edge === 'bottom' ? bottom() : 0 },
        })
        return () => Vue.h('section', { ref: node => { scroller.node = node }, 'data-scroller': true },
            props.items.map(row => Vue.h('div', { key: row.lineNum }, slots.default?.({ item: row }))))
    } }
    let runtime
    const SessionRow = { props: ['content', 'lineNum'], setup(props) {
        const context = Vue.inject(INLINE_ARTIFACT_CONTEXT)
        runtime = context.runtime
        const textContext = Vue.computed(() => {
            const content = props.content
            const text = content?.message?.content?.[0]?.text
            return text ? { source: text, ...createInlineTextContext({ publicationAllowed: store.getSession('root').type === 'session',
                finalized: true, sessionId: 'root', lineNum: props.lineNum }, [{ textBlockIndex: 0, text }]) } : null
        })
        return () => {
            const context = textContext.value
            const blocks = context ? splitMarkdownBlocks(context.source, { inlineArtifacts: true, ...context }).blocks : []
            const placements = blocks.filter(block => block.type === 'inline-artifact')
                .map(block => inlineArtifactPlacement(context, block.span, runtime))
            return Vue.h('p', { 'data-line': props.lineNum }, placements.map(placement => Vue.h('span', {
                'data-publication': placement.publicationKey, 'data-status': placement.status,
            }, placement.title)))
        }
    } }
    const list = compileComponent(new URL('./ShareItemsList.vue', import.meta.url), {
        '../components/virtual-scroller/VirtualScroller.vue': Scroller,
        '../components/session/detail/SessionItem.vue': SessionRow,
        '../components/session/detail/GroupToggle.vue': empty,
        '../components/session/detail/ChatNavToolbar.vue': { props: ['canGoBottom'], setup(props) {
            return () => Vue.h('button', { 'data-last': true, disabled: !props.canGoBottom })
        } },
        '../components/session/detail/items/DaySeparator.vue': empty,
        '../components/session/detail/ChatSkeleton.vue': empty,
        '../composables/useChatNavigation': { useChatNavigation },
        '../composables/useChatReveal.js': { useChatReveal: () => ({ hidden: Vue.ref(false), skeletonShown: Vue.ref(false),
            startedAt: Vue.ref(null), phaseId: Vue.ref(0) }) },
        '../stores/data': { useDataStore }, '../stores/settings': { useSettingsStore: () => settings },
        '../utils/parsedContent': parsed,
        '../utils/scrollerLoadWindow.js': await import('../utils/scrollerLoadWindow.js'),
        './shims/shareApi': shareApi,
        '../utils/attachmentStrip': await import('../utils/attachmentStrip.js'),
    })
    const component = compileComponent(new URL('./ShareSessionApp.vue', import.meta.url), {
        './ShareItemsList.vue': list,
        '../components/frames/FrameHost.vue': empty, '../inline-artifacts/InlineArtifactRuntimeHost.vue': empty,
        '../stores/framePool.js': { useFramePoolStore }, '../inline-artifacts/context.js': { INLINE_ARTIFACT_CONTEXT },
        '../inline-artifacts/runtime.js': { createInlineArtifactRuntime }, './inlineAdapter.js': { makeShareInlineAdapter },
        './inlineCompletion.js': { createShareInlineCompletion }, './SharedSubagentView.vue': empty,
        '../components/media/GlobalMediaPreview.vue': empty, './ShareFooter.vue': empty,
        '../stores/data': { useDataStore }, '../stores/settings': { useSettingsStore: () => settings },
        '../providers': { getProviderIcon: () => null }, '../components/ui/ProviderIcon.vue': empty,
        './shims/shareApi': shareApi, './shims/shareLive': { connectShareLive },
        './viewerPrefs': { loadViewerPrefs: () => ({ showTimestamps: false }) },
    })
    const { renderer, root } = makeRenderer(140, node => { node.style = {} })
    const app = renderer.createApp(component, { tokenPath: '/share/test/', meta: { ...meta(263), mode } })
    t.after(() => { app.unmount(); Object.assign(globalThis, previous) })
    app.use(pinia); app.mount(root)
    await flush()
    await store.loadSessionItemsRanges('share', 'root', [[2, 10]])
    await flush()
    requests.length = 0
    return { store, requests, scroller, app, root, runtime: () => runtime,
        focus: () => events.get('focus')(),
        open: () => sockets.at(-1).onopen(),
        message: message => sockets.at(-1).sendMessage(message),
        async reconnect() {
            sockets.at(-1).onclose()
            await new Promise(resolve => setTimeout(resolve, 1010))
            sockets.at(-1).onopen()
            await flush()
        },
        advance(line = 267, id = 'preferences') { boundary = line; items.push(item(line, tag(id))) },
        metadata(fn) { metadataReply = fn }, meta(fn) { metaReply = fn }, manifest(fn) { manifestReply = fn },
    }
}

test('snapshot focus extends real transcript metadata and reaches the latest typed placement without remounting', async t => {
    const f = await mount(t)
    const marker = f.store.getSessionVisualItems('root').find(row => row.lineNum === 10)
    const content = f.store.getSessionItem('root', 10)
    const scrollerNode = f.scroller.node
    const key = '["root","calculator"]', runtime = f.runtime()
    runtime.attach(key, '["root",2,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
    runtime.setVisible(key, true)
    await flush()
    const entry = runtime.entries.get(key), frame = useFramePoolStore().frames[entry.frameId]
    assert.ok(frame)
    frame.fixtureMemory = 'unsaved calculator state'
    assert.equal(descendants(f.root, node => node.props['data-last'])[0].props.disabled, true)
    f.advance()
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 267)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 267), 'new source row must be reachable')
    assert.equal(descendants(f.root, node => node.props['data-last'])[0].props.disabled, false, 'GoLast must reach the extended transcript')
    await f.store.loadSessionItemsRanges('share', 'root', [[267, 267]])
    await flush()
    assert.equal(descendants(f.root, node => node.props['data-publication'] === '["root",267,0,0]' && node.props['data-status'] === 'ready').length, 1)
    assert.equal(descendants(f.root, node => node.props['data-publication'] === '["root",263,0,0]' && node.props['data-status'] === 'ready').length, 0)
    assert.equal(f.store.getSessionVisualItems('root').find(row => row.lineNum === 10), marker)
    assert.equal(f.store.getSessionItem('root', 10), content)
    assert.equal(f.scroller.mounts, 1)
    assert.equal(f.scroller.node, scrollerNode)
    assert.equal(f.scroller.top.value, 3310)
    assert.equal(useFramePoolStore().frames[entry.frameId], frame)
    assert.equal(frame.fixtureMemory, 'unsaved calculator state')
    f.requests.length = 0
    await f.focus(); await flush()
    assert.equal(f.requests.filter(url => url.endsWith('/items/metadata/')).length, 0, 'unchanged boundary must keep its cached transcript')
})

test('failed snapshot metadata refresh keeps the cached boundary and retries on the next focus', async t => {
    const f = await mount(t)
    f.advance()
    f.metadata(() => new Response(null, { status: 503 }))
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, 0)
    f.metadata(null)
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 267)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 267))
})

test('snapshot exclusion hides retained frames before changed-boundary metadata settles or fails', async t => {
    const f = await mount(t), pending = deferred(), runtime = f.runtime()
    const key = '["root","calculator"]'
    runtime.attach(key, '["root",2,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
    runtime.setVisible(key, true)
    await flush()
    const entry = runtime.entries.get(key), frame = useFramePoolStore().frames[entry.frameId]
    assert.ok(frame)
    assert.equal(runtime.active.value, true)
    f.advance()
    f.meta(() => ({ ...meta(267), include_inline_artifacts: false }))
    f.metadata(() => pending.promise)
    const focus = f.focus(); await flush()
    const activeBeforeMetadata = runtime.active.value
    pending.reject(Object.assign(new Error('metadata unavailable'), { status: 503 }))
    await focus; await flush()
    assert.equal(runtime.active.value, false, 'fresh exclusion must survive metadata failure')
    assert.equal(activeBeforeMetadata, false, 'exclusion must apply while metadata is still pending')
    assert.equal(frame.visible, false)
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.store.getSessionItem('root', 267), null)
    assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, 0)
    f.metadata(() => { throw Object.assign(new Error('metadata unavailable'), { status: 503 }) })
    await f.focus(); await flush()
    assert.equal(runtime.active.value, false, 'repeated metadata failure must not undo exclusion')
    assert.equal(f.store.getSession('root').last_line, 263)
    f.metadata(null)
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 267)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 267))
    assert.equal(runtime.active.value, false, 'metadata recovery must preserve authoritative exclusion')
})

test('snapshot reactivation waits for transcript rows before resuming retained pending completion', async t => {
    const initialManifest = manifest(263)
    initialManifest.artifacts[1].status = 'pending'
    initialManifest.artifacts[1].code_revision = null
    const f = await mount(t, { initialManifest }), pending = deferred(), runtime = f.runtime()
    const key = '["root","preferences"]'
    const entry = runtime.entries.get(key)
    const bindingGeneration = entry.generation
    f.meta(() => ({ ...meta(263), include_inline_artifacts: false }))
    f.manifest(() => new Response(null, { status: 503 }))
    await f.focus(); await flush()
    assert.equal(runtime.active.value, false, 'exclusion must remain immediate')
    assert.equal(entry.descriptor.status, 'pending', 'manifest503 must retain the pending descriptor')
    f.advance()
    f.meta(() => meta(267))
    f.manifest(null)
    f.metadata(() => pending.promise)
    f.requests.length = 0
    const focus = f.focus(); await flush()
    await new Promise(resolve => setTimeout(resolve, 1100)); await flush()
    const publicationBeforeRows = entry.descriptor.publicationKey
    const generationBeforeRows = entry.generation
    const boundaryBeforeRows = f.store.getSession('root').last_line
    pending.reject(Object.assign(new Error('metadata unavailable'), { status: 503 }))
    await focus; await flush()
    assert.equal(boundaryBeforeRows, 263)
    assert.equal(publicationBeforeRows, '["root",263,0,0]', 'completion must keep the old binding while rows are pending')
    assert.equal(generationBeforeRows, bindingGeneration, 'completion must not invalidate the old binding')
    await new Promise(resolve => setTimeout(resolve, 1100)); await flush()
    assert.equal(entry.descriptor.publicationKey, '["root",263,0,0]', 'failed metadata must not release newer placement')
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.store.getSessionItem('root', 267), null)
    assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, 0)
    f.metadata(null)
    let completionRequests = 0
    f.manifest(() => {
        assert.equal(f.store.getSession('root').last_line, 267, 'successful boundary must precede every manifest request')
        assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 267))
        const reply = manifest(267)
        if (++completionRequests === 1) {
            reply.artifacts[1].status = 'pending'
            reply.artifacts[1].code_revision = null
        } else reply.revision = 268
        return reply
    })
    await f.focus(); await flush()
    assert.equal(entry.descriptor.publicationKey, '["root",267,0,0]')
    assert.equal(entry.descriptor.status, 'pending')
    assert.equal(runtime.active.value, true)
    await new Promise(resolve => setTimeout(resolve, 1100)); await flush()
    assert.equal(entry.descriptor.status, 'ready', 'successful metadata must resume automatic pending completion')
    assert.equal(completionRequests, 2)
    await f.store.loadSessionItemsRanges('share', 'root', [[267, 267]]); await flush()
    assert.equal(descendants(f.root, node => node.props['data-publication'] === '["root",267,0,0]' && node.props['data-status'] === 'ready').length, 1)
})

test('failed snapshot meta fetch preserves completion at the reconciled transcript boundary', async t => {
    const initialManifest = manifest(263)
    initialManifest.artifacts[1].status = 'pending'
    initialManifest.artifacts[1].code_revision = null
    const f = await mount(t, { initialManifest }), runtime = f.runtime()
    f.meta(() => { throw Object.assign(new Error('meta unavailable'), { status: 503 }) })
    f.manifest(() => ({ ...manifest(263), revision: 264 }))
    await f.focus(); await flush()
    await new Promise(resolve => setTimeout(resolve, 1100)); await flush()
    const descriptor = runtime.entries.get('["root","preferences"]').descriptor
    assert.equal(descriptor.status, 'ready', 'meta503 must preserve completion while the known boundary stays reconciled')
    assert.equal(descriptor.publicationKey, '["root",263,0,0]')
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(runtime.active.value, true)
    assert.equal(f.requests.filter(url => url.endsWith('/items/metadata/')).length, 0)
})

test('unmounted snapshot ignores delayed root metadata and inline refresh', async t => {
    const f = await mount(t), pending = deferred()
    f.advance(); f.metadata(() => pending.promise)
    const focus = f.focus(); await flush()
    f.app.unmount()
    pending.resolve([item(267, tag('preferences'))]); await focus; await flush()
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.store.getSessionItem('root', 267), null)
    assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, 0)
})

test('a later snapshot focus owns metadata when an older request settles last', async t => {
    const f = await mount(t), pending = deferred()
    f.advance(); let calls = 0
    f.metadata(() => ++calls === 1 ? pending.promise : Promise.resolve([item(268, tag('preferences'))]))
    const older = f.focus(); await flush()
    f.advance(268)
    await f.focus(); await flush()
    pending.resolve([item(267, tag('preferences'))]); await older; await flush()
    assert.equal(f.store.getSession('root').last_line, 268)
    assert.equal(f.store.getSessionItem('root', 267), null)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 268))
})

test('live reconnect restores missed root rows before accepting HTTP and concurrent WS placements', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime()
    f.open(); await flush()
    const key = '["root","calculator"]'
    runtime.attach(key, '["root",2,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
    runtime.setVisible(key, true); await flush()
    const entry = runtime.entries.get(key), frame = useFramePoolStore().frames[entry.frameId]
    const src = frame.src, generation = entry.generation, marker = f.store.getSessionItem('root', 10)
    frame.fixtureMemory = 'unsaved reconnect input'
    const pending = deferred()
    f.advance(269, 'calculator')
    f.metadata(() => pending.promise)
    await f.reconnect()
    const corrected = manifest(269)
    corrected.revision = 270
    corrected.artifacts[0].publication = ['root', 269, 0, 0]
    corrected.artifacts[0].code_revision = 2
    corrected.artifacts[1].publication = ['root', 263, 0, 0]
    corrected.artifacts[1].code_revision = 263
    f.manifest(() => ({ ...corrected, revision: 269 }))
    f.message({ type: 'share_inline_artifacts', manifest: corrected })
    await flush()
    assert.equal(entry.descriptor.publicationKey, '["root",2,0,0]', 'reconnect must retain old placement until its replacement row exists')
    assert.equal(entry.generation, generation)
    assert.equal(frame.src, src)
    assert.equal(frame.fixtureMemory, 'unsaved reconnect input')
    pending.resolve([item(269, tag('calculator'))])
    await waitFor(() => entry.descriptor.publicationKey === '["root",269,0,0]')
    assert.equal(f.store.getSession('root').last_line, 269, 'reconnect must advance the successful transcript boundary')
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 269))
    assert.equal(entry.descriptor.publicationKey, '["root",269,0,0]')
    assert.equal(descendants(f.root, node => node.props['data-last'])[0].props.disabled, false)
    assert.equal(f.store.getSessionItem('root', 10), marker)
    assert.equal(f.scroller.mounts, 1)
    assert.equal(f.scroller.top.value, 3310)
    await f.store.loadSessionItemsRanges('share', 'root', [[269, 269]])
    runtime.attach(key, '["root",269,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
    runtime.setVisible(key, true)
    await waitFor(() => frame.src !== src)
    assert.equal(descendants(f.root, node => node.props['data-publication'] === '["root",269,0,0]').length, 1)
    assert.equal(useFramePoolStore().frames[entry.frameId], frame)
    assert.notEqual(frame.src, src, 'changed code must navigate the retained frame once')
    assert.equal(entry.generation, generation + 1, 'HTTP and WS must coalesce into one code replacement')
    const correctedSrc = frame.src
    f.message({ type: 'share_inline_artifacts', manifest: corrected })
    f.message({ type: 'share_inline_artifacts', manifest: { ...corrected, revision: 271 } })
    await flush()
    assert.equal(frame.src, correctedSrc, 'duplicate and saved-data-only revisions must not reload code')
})

test('live reconnect HTTP placement waits for rows and retains unchanged frame memory', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime(), pending = deferred()
    f.open(); await flush()
    const key = '["root","calculator"]'
    runtime.attach(key, '["root",2,0,0]', { placeholderEl: {}, clipEl: {}, isSuppressed: () => false })
    runtime.setVisible(key, true); await flush()
    const frame = useFramePoolStore().frames[runtime.entries.get(key).frameId], src = frame.src
    frame.fixtureMemory = 'unsaved calculator'
    f.advance(269); f.metadata(() => pending.promise)
    f.manifest(() => {
        assert.equal(f.store.getSession('root').last_line, 269, 'rows must precede the reconnect HTTP manifest')
        return manifest(269)
    })
    await f.reconnect()
    assert.equal(runtime.entries.get('["root","preferences"]').descriptor.publicationKey, '["root",263,0,0]')
    pending.resolve([item(269, tag('preferences'))])
    await waitFor(() => runtime.entries.get('["root","preferences"]').descriptor.publicationKey === '["root",269,0,0]')
    assert.equal(f.store.getSession('root').last_line, 269)
    assert.equal(runtime.entries.get('["root","preferences"]').descriptor.publicationKey, '["root",269,0,0]')
    assert.equal(useFramePoolStore().frames[runtime.entries.get(key).frameId], frame)
    assert.equal(frame.src, src)
    assert.equal(frame.fixtureMemory, 'unsaved calculator')
})

test('live reconnect retries failed metadata without releasing a concurrent newer placement', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime()
    f.open(); await flush()
    f.advance(269)
    f.metadata(() => new Response(null, { status: 503 }))
    await f.reconnect()
    f.message({ type: 'share_inline_artifacts', manifest: manifest(269) })
    await flush()
    const entry = runtime.entries.get('["root","preferences"]'), generation = entry.generation
    assert.equal(entry.descriptor.publicationKey, '["root",263,0,0]')
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.store.getSessionItem('root', 269), null)
    f.metadata(null)
    await new Promise(resolve => setTimeout(resolve, 1100))
    await waitFor(() => entry.descriptor.publicationKey === '["root",269,0,0]')
    assert.equal(f.store.getSession('root').last_line, 269)
    assert.equal(entry.generation, generation + 1)
})

test('live reconnect retries when a concurrent manifest is newer than the HTTP transcript snapshot', async t => {
    const f = await mount(t, { mode: 'live' }), pending = deferred(), runtime = f.runtime()
    f.open(); await flush()
    f.advance(269); f.metadata(() => pending.promise)
    await f.reconnect()
    f.advance(270)
    f.message({ type: 'share_inline_artifacts', manifest: manifest(270) })
    f.manifest(() => manifest(269))
    pending.resolve([item(269, tag('preferences'))]); await flush()
    await waitFor(() => f.store.getSession('root').last_line === 269)
    const entry = runtime.entries.get('["root","preferences"]'), generation = entry.generation
    assert.equal(entry.descriptor.publicationKey, '["root",263,0,0]', 'a newer publication must wait for its own source row')
    f.metadata(null)
    await new Promise(resolve => setTimeout(resolve, 1100))
    await waitFor(() => entry.descriptor.publicationKey === '["root",270,0,0]')
    assert.equal(f.store.getSession('root').last_line, 270)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 270))
    assert.equal(entry.generation, generation + 1, 'only the newest coalesced publication replaces the old binding')
})

test('live reconnect retains its placement through manifest503 and retries automatically', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime()
    f.open(); await flush()
    f.advance(269)
    let calls = 0
    f.manifest(() => ++calls === 1 ? new Response(null, { status: 503 }) : manifest(269))
    await f.reconnect()
    await waitFor(() => calls === 1)
    assert.equal(f.store.getSession('root').last_line, 269)
    assert.equal(runtime.entries.get('["root","preferences"]').descriptor.publicationKey, '["root",263,0,0]')
    await new Promise(resolve => setTimeout(resolve, 1100))
    await waitFor(() => runtime.entries.get('["root","preferences"]').descriptor.publicationKey === '["root",269,0,0]')
    assert.equal(calls, 2)
})

test('live exclusion stays immediate while an older reconnect meta response is pending', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime(), pending = deferred()
    f.open(); await flush()
    f.advance(269); f.meta(() => pending.promise)
    await f.reconnect()
    f.message({ type: 'share_meta', meta: { ...meta(269), mode: 'live', include_inline_artifacts: false } })
    assert.equal(runtime.active.value, false)
    const disabled = { ...manifest(270), enabled: false }
    f.message({ type: 'share_inline_artifacts', manifest: disabled })
    assert.equal(runtime.entries.get('["root","preferences"]').descriptor.status, 'not_included')
    pending.resolve({ ...meta(269), mode: 'live' })
    await waitFor(() => f.store.getSession('root').last_line === 269)
    await flush()
    assert.equal(runtime.active.value, false, 'older HTTP metadata must not undo newer channel exclusion')
    assert.equal(runtime.entries.get('["root","preferences"]').descriptor.status, 'not_included')
})

test('new live connection owns root metadata when an older reconnect response settles last', async t => {
    const f = await mount(t, { mode: 'live' }), pending = deferred()
    f.open(); await flush()
    f.advance(269)
    let calls = 0, oldSignal
    f.metadata(options => {
        if (++calls === 1) { oldSignal = options.signal; return pending.promise }
        return [item(270, tag('preferences'))]
    })
    await f.reconnect()
    f.advance(270)
    await f.reconnect()
    await waitFor(() => f.store.getSession('root').last_line === 270)
    assert.equal(oldSignal.aborted, true)
    pending.resolve([item(269, tag('preferences'))]); await flush()
    assert.equal(f.store.getSession('root').last_line, 270)
    assert.equal(f.store.getSessionItem('root', 269), null)
    assert.equal(f.runtime().entries.get('["root","preferences"]').descriptor.publicationKey, '["root",270,0,0]')
})

for (const action of ['unmount', 'share_closed']) {
    test(`live reconnect ignores delayed metadata after ${action}`, async t => {
        const f = await mount(t, { mode: 'live' }), pending = deferred()
        f.open(); await flush()
        f.advance(269)
        let signal
        f.metadata(options => { signal = options.signal; return pending.promise })
        await f.reconnect()
        const manifests = f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length
        if (action === 'unmount') f.app.unmount()
        else f.message({ type: 'share_closed' })
        assert.equal(signal.aborted, true)
        pending.resolve([item(269, tag('preferences'))]); await flush()
        assert.equal(f.store.getSession('root').last_line, 263)
        assert.equal(f.store.getSessionItem('root', 269), null)
        assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, manifests)
        if (action === 'share_closed') assert.equal(f.runtime().active.value, false)
    })
}

test('live reconnect authorization failure closes retained access and does not retry', async t => {
    const f = await mount(t, { mode: 'live' }), runtime = f.runtime()
    f.open(); await flush()
    f.meta(() => new Response(null, { status: 401 }))
    await f.reconnect()
    await waitFor(() => !runtime.active.value)
    const requests = f.requests.length
    await new Promise(resolve => setTimeout(resolve, 1100)); await flush()
    assert.equal(f.requests.length, requests)
    assert.equal(f.store.getSession('root').last_line, 263)
})

test('a newer reconnect replaces an aborted manifest request without waiting for its old response', async t => {
    const f = await mount(t, { mode: 'live' }), pending = deferred()
    f.open(); await flush()
    f.advance(269)
    let calls = 0
    f.manifest(() => ++calls === 1 ? pending.promise : manifest(270))
    await f.reconnect()
    await waitFor(() => calls === 1)
    f.advance(270)
    await f.reconnect()
    await waitFor(() => f.runtime().entries.get('["root","preferences"]').descriptor.publicationKey === '["root",270,0,0]')
    pending.resolve(manifest(269)); await flush()
    assert.equal(f.store.getSession('root').last_line, 270)
    assert.equal(f.runtime().entries.get('["root","preferences"]').descriptor.publicationKey, '["root",270,0,0]')
})
