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

function realStore(settings) {
    const source = readFileSync(new URL('./shims/dataStoreShim.js', import.meta.url), 'utf8')
    const body = source.replace(/^import .+ from .+\n/gm, '').replace('export const useDataStore', 'const useDataStore')
    const deps = { ...agentLinks, ...visualItems, ...parsed, ...constants, defineStore,
        markRaw: Vue.markRaw, getSessionCutoffMs, shareApi: shareApi.shareApi, useSettingsStore: () => settings }
    return new Function(...Object.keys(deps), `${body}; return useDataStore`)(...Object.values(deps))
}

async function mount(t) {
    const previous = { window: globalThis.window, location: globalThis.location, history: globalThis.history,
        fetch: globalThis.fetch, getComputedStyle: globalThis.getComputedStyle }
    const events = new Map()
    globalThis.window = { addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) }
    globalThis.location = { pathname: '/share/test/', search: '', hash: '' }
    globalThis.history = { replaceState() {} }
    globalThis.getComputedStyle = () => ({ scrollPaddingTop: '0' })
    const requests = [], items = [item(2, tag('calculator')), item(10, 'History marker 010', 'user_message'), item(263, tag('preferences'))]
    let boundary = 263, metadataReply = null, metaReply = null
    globalThis.fetch = async (url, options = {}) => {
        requests.push(String(url))
        if (options.method === 'HEAD') return new Response(null, { status: 200 })
        if (url.endsWith('/api/meta/')) return Response.json(metaReply ? await metaReply() : meta(boundary))
        if (url.endsWith('/api/inline-artifacts/')) return Response.json(manifest(boundary))
        if (url.endsWith('/items/metadata/')) {
            const rows = metadataReply ? await metadataReply() : items
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
        './shims/shareApi': shareApi, './shims/shareLive': { connectShareLive() { throw Error('Snapshot must not connect live') } },
        './viewerPrefs': { loadViewerPrefs: () => ({ showTimestamps: false }) },
    })
    const { renderer, root } = makeRenderer(140, node => { node.style = {} })
    const app = renderer.createApp(component, { tokenPath: '/share/test/', meta: meta(263) })
    t.after(() => { app.unmount(); Object.assign(globalThis, previous) })
    app.use(pinia); app.mount(root)
    await flush()
    await store.loadSessionItemsRanges('share', 'root', [[2, 10]])
    await flush()
    requests.length = 0
    return { store, requests, scroller, app, root, runtime: () => runtime,
        focus: () => events.get('focus')(),
        advance(line = 267) { boundary = line; items.push(item(line, tag('preferences'))) },
        metadata(fn) { metadataReply = fn }, meta(fn) { metaReply = fn },
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
    f.metadata(() => { throw Object.assign(new Error('metadata unavailable'), { status: 503 }) })
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 263)
    assert.equal(f.requests.filter(url => url.endsWith('/api/inline-artifacts/')).length, 0)
    f.metadata(null)
    await f.focus(); await flush()
    assert.equal(f.store.getSession('root').last_line, 267)
    assert.ok(f.store.getSessionVisualItems('root').some(row => row.lineNum === 267))
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
