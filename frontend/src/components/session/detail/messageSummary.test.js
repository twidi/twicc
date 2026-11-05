import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useSummariesStore, summarySourceKey } from '../../../stores/summaries.js'
import { messageSummaryModule } from '../../../../tests/helpers/messageSummaryHarness.js'
import { PROVIDER } from '../../../constants.js'
import { useTextToSpeechStore } from '../../../stores/textToSpeech.js'
import { compileComponent, descendants, flush, makeRenderer, deferred } from '../../../../tests/helpers/scrollerComponentHarness.js'

const descriptor = (fields = {}) => ({ session_id: 's', source_key: 'native', source_hash: 'hash', policy_key: 'p', first_line: 12, text_lines: [12, 14], eligible: true, saved: null, previous: null, ...fields })
const textOf = node => node.type === '#comment' ? '' : `${node.text || ''}${node.children.map(textOf).join('')}`
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
async function setup(t, initial = descriptor()) {
    const settings = Vue.reactive({ summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] })
    const module = messageSummaryModule(settings)
    setActivePinia(createPinia()); const store = useSummariesStore(), calls = [], gate = deferred()
    let current = initial
    store._fetch = async (url, options) => {
        calls.push([url, options?.body && JSON.parse(options.body)])
        if (url.includes('resolve')) return { ok: true, json: async () => ({ sources: JSON.parse(options.body).sources.map(source => source.session_id === 'other' ? descriptor({ session_id: 'other', source_key: 'other-native', source_hash: 'other-hash', first_line: 2, text_lines: [2] }) : current) }) }
        if (url.includes('/saved/')) return { ok: true, json: async () => ({ id: 7, text: 'Saved A', result_hash: 'result-a', source_key: 'native', source_hash: 'hash', policy_key: 'p' }) }
        if (options?.method === 'DELETE') return { ok: true }
        if (url === '/api/summaries/') return { ok: true, json: async () => ({ id: 'handle', descriptor: current, state: 'running' }) }
        return { ok: true, body: new ReadableStream({ async start(controller) { await gate.promise; controller.enqueue(new TextEncoder().encode(JSON.stringify({ state: 'complete', text: 'Generated', saved_id: 7, result_hash: 'generated' }) + '\n')); controller.close() } }) }
    }
    const visit = module.createMessageSummaryVisit((...args) => calls.push(['reveal', ...args]))
    const bindings = [], sources = Vue.reactive([{ session_id: 's', line_num: 12 }, { session_id: 's', line_num: 14 }])
    const visible = Vue.ref(true)
    const Summary = compileComponent(new URL('./items/MessageSummaryView.vue', import.meta.url), {
        './MessageSummaryControls.vue': compileComponent(new URL('./items/MessageSummaryControls.vue', import.meta.url)),
        './TextContent.vue': { props: ['text'], setup: p => () => Vue.h('summary-text', p.text) },
    })
    const Row = { props: ['index'], setup(p) {
        const summary = module.useMessageSummary(() => sources[p.index], visit); bindings[p.index] = summary
        return () => Vue.h('row', [summary.isHost && summary.hasSummary ? Vue.h(Summary, { summary }) : null,
            !summary.hidden ? Vue.h('original', { copy: `block-${p.index}`, speech: `block-${p.index}` }, `block-${p.index}`) : null])
    } }
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => Vue.h('chat', [visible.value ? Vue.h(Row, { index: 0 }) : null, Vue.h('tool', 'tool output'), Vue.h('thinking', 'reasoning'), Vue.h('media', 'image'), Vue.h(Row, { index: 1 })]) })
    app.mount(root); t.after(() => { gate.resolve(); app.unmount() }); await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    return { module, settings, store, calls, visit, bindings, sources, visible, root, gate, setDescriptor(value) { current = value }, async invalidate() { for (const source of sources) { const key = summarySourceKey(source); store.descriptors[key] = null; store.lookupVersions[key] = (store.lookupVersions[key] || 0) + 1 }; await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush() } }
}

test('two rows share one canonical host; a later action targets the native message and retains unrelated content', async t => {
    const { bindings, root, calls } = await setup(t)
    assert.deepEqual(descendants(root, n => n.type === 'original').map(n => [n.props.copy, n.props.speech]), [['block-0', 'block-0'], ['block-1', 'block-1']])
    await bindings[1].summarize(); await flush()
    assert.equal(descendants(root, n => n.props.class === 'message-summary-controls').length, 1)
    assert.equal(descendants(root, n => n.type === 'original').length, 0)
    for (const type of ['tool', 'thinking', 'media']) assert.equal(descendants(root, n => n.type === type).length, 1)
    assert.deepEqual(calls.find(([url]) => url === '/api/summaries/')[1].source, { session_id: 's', line_num: 14 })
    assert.deepEqual(calls.find(([url]) => url === 'reveal').slice(1), [12])
    bindings[0].showOriginal(); await flush(); assert.equal(descendants(root, n => n.type === 'original').length, 2)
})

test('saved summaries load without generation; original is default and background work does not select Summary', async t => {
    const { bindings, calls, gate } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    assert.equal(bindings[0].view, 'original')
    await bindings[1].summarize(); assert.equal(bindings[0].entry.text, 'Saved A')
    assert.equal(calls.filter(([url]) => url === '/api/summaries/').length, 0)
    bindings[0].showOriginal(); gate.resolve(); await flush(); assert.equal(bindings[0].view, 'original')
})

test('view survives virtualized unmount; recycled coordinates and source replacement reset bindings immediately', async t => {
    const { bindings, visible, sources, store, root, setDescriptor } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    await bindings[1].summarize(); visible.value = false; await flush(); visible.value = true; await flush()
    assert.equal(bindings[0].view, 'summary')
    sources[1] = { session_id: 'other', line_num: 2 }; await flush()
    assert.equal(bindings[1].view, 'original')
    setDescriptor(descriptor({ source_key: 'new-history', source_hash: 'new-hash' }))
    store.invalidate({ type: 'session_updated', session_id: 's' })
    assert.equal(bindings[0].view, 'summary', 'an unanswered lookup never changes the view')
    await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(bindings[0].view, 'original'); assert.doesNotMatch(textOf(root), /Saved A/)
})

test('session events never swap a displayed summary back to the original', async t => {
    const { bindings, root, store, sources } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    await bindings[0].summarize(); await flush()
    const key = summarySourceKey(sources[0]); store.descriptors[key] = null; store.lookupVersions[key]++
    assert.equal(bindings[0].view, 'summary'); assert.equal(bindings[0].hidden, true); assert.equal(bindings[0].isHost, true)
    assert.equal(bindings[0].available, true); assert.equal(descendants(root, n => n.type === 'original').length, 0)
    await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(bindings[0].view, 'summary'); assert.equal(bindings[0].entry.text, 'Saved A')
})

test('a session event during the saved summary load keeps the loaded text', async t => {
    const { bindings, invalidate } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    const loading = bindings[0].summarize(); await invalidate(); await loading; await flush()
    assert.equal(bindings[0].view, 'summary'); assert.equal(bindings[0].entry.text, 'Saved A')
})

test('the summary switch stays available in the original view and reloads on demand', async t => {
    const { bindings } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    assert.equal(bindings[0].view, 'original'); assert.equal(bindings[0].hasSummary, true)
    await bindings[0].summarize(); bindings[0].showOriginal(); await flush()
    assert.equal(bindings[0].view, 'original'); assert.equal(bindings[0].hasSummary, true)
    await bindings[0].summarize(); assert.equal(bindings[0].view, 'summary'); assert.equal(bindings[0].entry.text, 'Saved A')
})

test('leaving the summary holds the scroll position, once', async t => {
    const { bindings, visit } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    const views = []
    visit.holdScroll = () => views.push(bindings[0].view)
    await bindings[0].summarize(); await flush()
    bindings[0].showOriginal(); bindings[0].showOriginal(); await flush()
    assert.deepEqual(views, ['summary'], 'held before the view changes, and only for a real change')
})

test('a message without any summary offers no switch', async t => {
    const { bindings } = await setup(t)
    assert.equal(bindings[0].available, true); assert.equal(bindings[0].hasSummary, false)
})

test('mounted ineligible source refreshes after completion and repeated null invalidation', async t => {
    const { bindings, setDescriptor, invalidate } = await setup(t, descriptor({ eligible: false }))
    assert.equal(bindings[0].available, false)
    setDescriptor(descriptor()); await invalidate(); assert.equal(bindings[0].available, true)
})

test('previous policy remains visible during failed regeneration and saved mismatch refresh never plays', async t => {
    const { bindings, store } = await setup(t, descriptor({ policy_key: 'new-policy', previous: { id: 7, result_hash: 'result-a' } }))
    await bindings[0].summarize(); assert.equal(bindings[0].entry.previousPolicy, true)
    const failed = Vue.reactive({ state: 'failed', text: '', error: 'Generation failed' })
    store.request = () => ({ entry: failed, ready: Promise.resolve(), release() {} })
    await bindings[0].regenerate(); assert.equal(bindings[0].entry.text, 'Saved A'); assert.equal(bindings[0].generation.error, 'Generation failed')
    await bindings[0].refreshSaved(7); assert.equal(bindings[0].entry.text, 'Saved A')
    assert.deepEqual(bindings[0].playbackReference(), { saved_id: 7, result_hash: 'result-a' })
})

test('provider boundary opts in persisted assistant rows only and Markdown keeps block-scoped original controls', () => {
    const item = read('./SessionItem.vue'), text = read('./items/TextContent.vue'), markdown = read('../../ui/MarkdownContent.vue')
    assert.match(item, /useMessageSummary/); assert.match(item, /props\.kind !== 'assistant_message'/); assert.match(item, /props\.syntheticKind/)
    assert.equal((item.match(/<MessageSummaryView\b/g) || []).length, 1)
    assert.match(text, /MESSAGE_SUMMARY_CONTEXT/)
    assert.match(markdown, /summaryContext/); assert.match(markdown, /:text="source"/); assert.match(markdown, /@click="copySource"/)
    assert.match(markdown, /block\.type === 'inline-artifact'/)
    const list = read('./SessionItemsList.vue'); assert.match(list, /createMessageSummaryVisit/); assert.match(list, /scrollToLineNum\(lineNum, \{ align: 'start', highlight: false \}\)/)
})

test('a subscription getter rebinds authoritatively, and completion never switches an original view', async t => {
    const { bindings, store } = await setup(t)
    const placeholder = Vue.reactive({ state: 'running', text: '', descriptor: null })
    const authoritative = Vue.reactive({ state: 'running', text: 'Streamed sentence', descriptor: descriptor() })
    const selected = Vue.shallowRef(placeholder)
    store.request = () => ({ get entry() { return selected.value }, ready: Promise.resolve(), release() {} })
    await bindings[1].summarize(); assert.equal(bindings[0].entry, placeholder)
    selected.value = authoritative; await flush(); assert.equal(bindings[0].entry.text, 'Streamed sentence')
    bindings[0].showOriginal(); authoritative.state = 'complete'; await flush(); assert.equal(bindings[0].view, 'original')
})

test('policy changes retain the selected completed body with its old policy while detaching obsolete interest', async t => {
    const { bindings, store, setDescriptor, invalidate } = await setup(t)
    let releases = 0
    const completed = Vue.reactive({ state: 'complete', text: 'Previous generated body', saved_id: 7, result_hash: 'old-body', descriptor: descriptor() })
    store.request = () => ({ entry: completed, ready: Promise.resolve(), release() { releases++ } })
    await bindings[0].summarize(); await flush()
    setDescriptor(descriptor({ policy_key: 'new-policy', previous: { id: 7, result_hash: 'old-body' } })); await invalidate()
    assert.equal(bindings[0].entry.text, 'Previous generated body')
    assert.equal(bindings[0].entry.previousPolicy, true)
    assert.deepEqual(bindings[0].playbackReference(), { saved_id: 7, result_hash: 'old-body' })
    assert.equal(releases, 1)
})

test('a manual activation joins a background stream and its release preserves the persistent manual intent', async t => {
    const { bindings, store, gate, visible } = await setup(t)
    const subscription = store.request({ session_id: 's', line_num: 12 }, { manual: false })
    await subscription.ready
    assert.equal(bindings[0].view, 'original')
    await bindings[0].summarize(); visible.value = false; await flush(); subscription.release()
    assert.equal(bindings[1].entry.state, 'running')
    gate.resolve(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(bindings[1].entry.text, 'Generated')
})

test('thinking/tool native prefix cannot host the summary before the first text row', async t => {
    const { bindings, sources } = await setup(t)
    sources[0] = { session_id: 's', line_num: 10 }; await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(bindings[0].isHost, false)
    assert.equal(bindings[0].hidden, false)
})

test('a pending Read cannot substitute an authoritatively acquired different source', async t => {
    const { bindings, store } = await setup(t)
    const ready = deferred(), selected = Vue.shallowRef(Vue.reactive({ state: 'running', text: '', descriptor: null }))
    store.request = () => ({ get entry() { return selected.value }, id: 'changed-handle', ready: ready.promise, release() {} })
    const activation = bindings[0].summarize(); await flush()
    const reference = bindings[0].playbackReference()
    selected.value = Vue.reactive({ state: 'running', text: 'Different source', descriptor: descriptor({ source_hash: 'changed-source' }) })
    ready.resolve(); await activation
    await assert.rejects(reference, /message changed/i)
})

test('Codex before/plan/after segments consume one row context and create no additional summary host', async t => {
    const module = messageSummaryModule()
    const rendering = await import('../../../inline-artifacts/rendering.js')
    const markdown = await import('../../../utils/markdown.js')
    const plan = await import('../../../providers/codex/proposedPlan.js')
    const details = await import('../../../composables/useDetailsClosing.js')
    const context = Vue.reactive({ available: true, hidden: false, summarize() {} })
    const Markdown = { props: ['source', 'summaryContext'], setup: p => () => Vue.h('markdown', { source: p.source, context: p.summaryContext, hidden: p.summaryContext?.hidden }) }
    const Text = compileComponent(new URL('./items/TextContent.vue', import.meta.url), {
        '../../../../composables/useMessageSummary.js': module, '../../../ui/MarkdownContent.vue': Markdown,
        '../../../../inline-artifacts/rendering.js': rendering, '../../../../utils/markdown': markdown,
    })
    const Assistant = compileComponent(new URL('./items/codex/AssistantMessage.vue', import.meta.url), {
        '../../../../../composables/useMessageSummary.js': module,
        '../../../../../stores/data': { useDataStore: () => ({ isDetailOpen: () => false, setDetailOpen() {} }) },
        '../../../../../composables/useDetailsClosing': details, '../../../../../inline-artifacts/rendering.js': rendering,
        '../../../../../providers/codex/proposedPlan': plan, '../TextContent.vue': Text, '../../../../ui/MarkdownContent.vue': Markdown,
    })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ setup() { Vue.provide(module.MESSAGE_SUMMARY_CONTEXT, context); return () => Vue.h(Assistant, { text: 'Before\n\n<proposed_plan>\nPlan\n</proposed_plan>\n\nAfter', sessionId: 's', lineNum: 12 }) } })
    app.mount(root); t.after(() => app.unmount()); await flush()
    const segments = descendants(root, n => n.type === 'markdown')
    assert.deepEqual(segments.map(n => n.props.source.trim()), ['Before', 'Plan', 'After'])
    assert.ok(segments.every(n => n.props.context === context))
    context.hidden = true; await flush(); assert.ok(segments.every(n => n.props.hidden))
    assert.equal(descendants(root, n => n.type === 'section').length, 0)
})

test('the actual SessionItem source gate excludes synthetic, user, tool, reasoning and compact rows', async () => {
    const module = messageSummaryModule()
    const source = read('./SessionItem.vue'), start = source.indexOf('const summaryVisit ='), end = source.indexOf('// Only persisted assistant rows', start)
    const props = { kind: 'assistant_message', sessionId: 's', lineNum: 12, content: null, syntheticKind: null }
    let visit = module.createMessageSummaryVisit()
    const sourceFor = () => new Function('props', 'inject', 'provide', 'MESSAGE_SUMMARY_VISIT', 'MESSAGE_SUMMARY_CONTEXT', 'useMessageSummary', source.slice(start, end) + '\nreturn summary;')(
        props, () => visit, () => {}, module.MESSAGE_SUMMARY_VISIT, module.MESSAGE_SUMMARY_CONTEXT, getter => getter())
    assert.deepEqual(sourceFor(), { session_id: 's', line_num: 12 })
    for (const kind of ['user_message', 'tool_use', 'reasoning', 'compact_summary', 'content_items']) { props.kind = kind; assert.equal(sourceFor(), null) }
    props.kind = 'assistant_message'; props.syntheticKind = 'streaming_block'; assert.equal(sourceFor(), null)
    props.syntheticKind = null; props.content = { syntheticKind: 'streaming_block' }; assert.equal(sourceFor(), null)
    props.content = null; props.lineNum = -1; assert.equal(sourceFor(), null)
    props.lineNum = 12; visit = null; assert.equal(sourceFor(), null, 'public shared snapshots have no authenticated chat visit')
})

test('active rows observe repeated invalidation while descriptors are null and reject the obsolete lookup', async t => {
    const { bindings, store } = await setup(t)
    const lookups = []
    store._fetch = async (_url, options) => { const held = deferred(); lookups.push({ ...held, sources: JSON.parse(options.body).sources }); return held.promise }
    store.invalidate({ type: 'process_state', session_id: 's' }); await flush()
    assert.equal(bindings[0].available, true, 'the toolbar keeps its button during the lookup'); assert.equal(lookups.length, 1)
    store.invalidate({ type: 'session_updated', session_id: 's' }); await flush()
    assert.equal(lookups.length, 2)
    lookups[1].resolve({ ok: true, json: async () => ({ sources: lookups[1].sources.map(() => descriptor({ eligible: false })) }) }); await flush()
    lookups[0].resolve({ ok: true, json: async () => ({ sources: lookups[0].sources.map(() => descriptor()) }) }); await flush()
    assert.equal(bindings[0].available, false)
})

test('the current result wins over a previous-policy one, which a regeneration keeps visible', async t => {
    const { bindings, store } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'current' }, previous: { id: 8, result_hash: 'previous' } }))
    store.loadSaved = async id => ({ id, state: 'complete', text: id === 8 ? 'Previous policy body' : 'Current policy body', result_hash: id === 8 ? 'previous' : 'current', source_key: 'native', source_hash: 'hash', policy_key: id === 8 ? 'old-policy' : 'p' })
    await bindings[0].summarize()
    assert.equal(bindings[0].entry.text, 'Current policy body')
    await bindings[0].regenerate()
    assert.equal(bindings[0].generation.state, 'running'); assert.equal(bindings[0].entry.text, 'Current policy body')
})

test('message generation and Regenerate disappear without an engine while saved display and speech stay available', async t => {
    const { settings, bindings, calls, root } = await setup(t, descriptor({ saved: { id: 7, result_hash: 'result-a' } }))
    settings.enabledProviders = []; await flush()
    assert.equal(bindings[0].generationAvailable, false)
    await bindings[0].summarize(); await flush()
    assert.equal(bindings[0].entry.text, 'Saved A')
    assert.deepEqual(bindings[0].playbackReference(), { saved_id: 7, result_hash: 'result-a' })
    assert.equal(descendants(root, n => n.type === 'a' && textOf(n) === 'Regenerate').length, 0)
    await bindings[0].regenerate()
    assert.equal(calls.filter(([url]) => url === '/api/summaries/').length, 0)
})
test('message controller uses requested model routing and key state only for new generation', async t => {
    const { settings, bindings, calls } = await setup(t)
    settings.enabledProviders = []; await flush()
    assert.equal(bindings[0].canSummarize, false)
    await bindings[0].summarize(); assert.equal(bindings[0].view, 'original')
    assert.equal(calls.filter(([url]) => url === '/api/summaries/').length, 0)
    const speech = useTextToSpeechStore(); speech.keyPresent = true; await flush()
    assert.equal(bindings[0].generationAvailable, false, 'Auto does not select OpenRouter')
    settings.summaryModel = 'openrouter'; await flush()
    assert.equal(bindings[0].generationAvailable, true)
    speech.keyPresent = false; settings.enabledProviders = [PROVIDER.CODEX]; settings.summaryModel = 'haiku'; await flush()
    assert.equal(bindings[0].generationAvailable, true, 'unavailable Haiku falls back to enabled Luna')
})

test('an over-limit OpenRouter source disables generation without agents but keeps saved access', async t => {
    const { settings, bindings, calls } = await setup(t, descriptor({ openrouter_eligible: false, saved: { id: 7, result_hash: 'result-a' } }))
    settings.summaryModel = 'openrouter'; settings.enabledProviders = []; useTextToSpeechStore().keyPresent = true
    await flush()
    assert.equal(bindings[0].generationAvailable, false)
    assert.match(bindings[0].generationUnavailableReason, /source.*OpenRouter|OpenRouter.*source/)
    await bindings[0].summarize(); assert.equal(bindings[0].entry.text, 'Saved A')
    await bindings[0].regenerate(); assert.equal(calls.filter(([url]) => url === '/api/summaries/').length, 0)
    settings.enabledProviders = [PROVIDER.CODEX]; await flush()
    assert.equal(bindings[0].generationAvailable, true, 'the existing enabled agent handles the long source')
})

test('the actual Markdown toolbar disables new generation and preserves saved-summary access', async t => {
    const { parse, compileTemplate } = await import('@vue/compiler-sfc')
    const { settings, bindings, store, sources } = await setup(t)
    const compiled = compileTemplate({ source: parse(read('../../ui/MarkdownContent.vue')).descriptor.template.content,
        filename: 'MarkdownContent.vue', id: 'summary-toolbar', compilerOptions: { isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const code = compiled.code.replace(/import \{([^}]+)\} from "vue"/g, (_, names) => `const {${names.replace(/ as /g, ': ')}} = Vue`)
        .replace('export function render', 'function render')
    const render = new Function('Vue', `${code}; return render`)(Vue)
    const { renderer, root } = makeRenderer(140, node => { node.style = {} })
    const app = renderer.createApp({ render, setup: () => ({ summaryContext: bindings[0], showToolbar: true, toolbarOpen: false,
        showTocDetails: false, showRaw: false, speakable: false, summaryActive: false, summaryHidden: false,
        summaryButtonTitle: Vue.computed(() => !bindings[0].canSummarize ? bindings[0].generationUnavailableReason
            : bindings[0].entry?.text || bindings[0].descriptor?.saved || bindings[0].descriptor?.previous ? 'Show summary' : 'Summarize message'), source: 'Original block', blocks: [], highlightTerms: [],
        toggleToolbar() {}, toggleRaw() {}, copySource() {}, handleLinkClick() {},
    }) })
    app.component('TextToSpeechButton', { render: () => null }); app.component('InlineArtifactBlock', { render: () => null })
    app.directive('highlight', {}); app.mount(root); t.after(() => app.unmount())
    settings.enabledProviders = []; await flush()
    const action = () => descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0]
    assert.equal(action().props.disabled, true); assert.match(action().props.title, /No summary engine/)
    store.descriptors[summarySourceKey(sources[0])] = descriptor({ saved: { id: 7, result_hash: 'result-a' } }); await flush()
    assert.equal(action().props.disabled, false); assert.equal(action().props.title, 'Show summary')
    await action().props.onClick(); await flush(); assert.equal(bindings[0].entry.text, 'Saved A')
})
