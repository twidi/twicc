import * as temporaryGeneration from '../../../composables/useTemporarySummaryGeneration.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../../tests/helpers/scrollerComponentHarness.js'
import * as touch from '../../../utils/touchTapFallback.js'
import * as ttsConfig from '../../../utils/ttsConfig.js'
import { PROVIDER } from '../../../constants.js'
import * as selectionRead from '../../../utils/selectionRead.js'
const read = file => readFileSync(new URL(file, import.meta.url), 'utf8')

const selectionWidgetDeps = (store, settings, speech, body) => ({
    '../../../stores/summaries': { useSummariesStore: () => store },
    '../../../stores/codeComments': { formatComment: () => '' },
    '../../../stores/settings': { useSettingsStore: () => settings },
    '../../../composables/useMediaPreview': { isOpen: Vue.ref(false), openMediaPreview() {} },
    '../../../composables/useToast': { toast: { error() {}, success() {} } },
    '../../../stores/textToSpeech': { useTextToSpeechStore: () => speech },
    '../../../utils/ttsConfig': ttsConfig,
    '../../../composables/useTemporarySummaryGeneration': temporaryGeneration,
    '../../../utils/selectionRead': selectionRead, '../../../utils/touchTapFallback': touch,
    './SelectionSummaryBody.vue': body,
})
function fakeBrowser() {
    const originalWindow = globalThis.window, originalDocument = globalThis.document
    globalThis.window = { innerWidth: 1000, innerHeight: 800, addEventListener() {}, removeEventListener() {}, visualViewport: null }
    globalThis.document = { addEventListener() {}, removeEventListener() {} }
    return () => { globalThis.document = originalDocument; globalThis.window = originalWindow }
}

const CAPTURED = 'Captured selection. '.repeat(30)
test('selection summary opens in the panel, captures the text and keeps the selection', async t => {
    const calls = []
    const body = { props: ['text'], setup: p => { calls.push(['body', p.text]); return () => Vue.h('summary-body') } }
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url),
        selectionWidgetDeps({}, { isTouchDevice: true, summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }, { available: false, statusForSelection: () => 'idle' }, body))
    const restoreBrowser = fakeBrowser()
    const props = Vue.reactive({ selectedText: 'Captured selection. '.repeat(30), readOrigin: 'rendered', position: { left: 10, top: 20 }, clearSourceSelection() { calls.push(['clear']) } })
    const { renderer, root } = makeRenderer(140, node => { node.focus = () => {} }); const app = renderer.createApp({ render: () => Vue.h(Component, { ...props, onClose: () => calls.push(['close']) }) }); app.mount(root); t.after(() => { app.unmount(); restoreBrowser() })
    const button = descendants(root, node => node.type === 'wa-button' && node.props['aria-label'] === 'Summarize selection')[0]
    assert.ok(button); button.props.onClick({ stopPropagation() {} }); await flush()
    assert.deepEqual(calls, [['body', CAPTURED]])
    props.selectedText = 'Another selection. '.repeat(30); await flush()
    assert.equal(descendants(root, node => node.type === 'summary-body').length, 1)
    assert.equal(descendants(root, node => node.type === 'wa-textarea').length, 0, 'no comment field in the summary panel')
    assert.equal(descendants(root, node => String(node.props.class).includes('tsc-quote')).length, 0, 'the highlighted selection replaces the quote')
    assert.deepEqual(calls, [['body', CAPTURED]], 'the summarized text is the one of the click')
})
test('summary action is limited to rendered/file selections and uses touch fallback', () => {
    const source = read('./TextSelectionComment.vue')
    assert.match(source, /!props\.persistent/)
    assert.match(source, /\['rendered', 'file'\]\.includes\(props\.readOrigin\)/)
    assert.match(source, /@pointerdown="tapFallback\.down\('summary', \$event\)"/)
    assert.match(source, /@pointerup="onTapUp\('summary', \$event\)"/)
})
test('no dialog remains and no unused summary content component', () => {
    assert.doesNotMatch(read('../../../App.vue'), /SelectionSummaryDialog/)
})

const bodyDeps = (store, speech, settings, Button) => ({
    '../../../stores/summaries': { useSummariesStore: () => store },
    '../../../stores/settings': { useSettingsStore: () => settings },
    '../../../composables/useTemporarySummaryGeneration': temporaryGeneration,
    '../../../stores/textToSpeech': { useTextToSpeechStore: () => speech },
    '../../../composables/useToast': { toast: { error() {}, success() {} } },
    '../../ui/MarkdownContent.vue': { props: ['source'], setup: p => () => Vue.h('markdown', p.source) },
    '../../ui/SummaryGenerationLinks.vue': compileComponent(new URL('../../ui/SummaryGenerationLinks.vue', import.meta.url)),
    '../../ui/TextToSpeechButton.vue': Button,
})

test('the panel binds the acquired reference at render and stops only its previous temporary reading on replacement', async t => {
    const first = Promise.resolve({ id: 'first' }), second = Promise.resolve({ id: 'second' })
    const entry = { state: 'running', text: 'Streamed summary' }
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Selection one' }, entry },
        openTemporary() {}, temporaryReference() { return this.temporary.id === 'one' ? first : second },
        closeTemporary() { this.temporary = null },
    })
    let playing = { selectionId: 'one', variant: 'summary' }, stops = 0
    const speech = { statusForSummary(identity) { return identity.selectionId === playing?.selectionId ? 'loading' : 'idle' }, stop() { stops++; playing = null } }
    const Button = { props: ['summaryRef', 'summaryIdentity'], setup: props => () => Vue.h('speech', props) }
    const Component = compileComponent(new URL('./SelectionSummaryBody.vue', import.meta.url),
        bodyDeps(store, speech, { summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }, Button))
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, { text: 'Selection one' }) }); app.mount(root)
    const button = descendants(root, node => node.type === 'speech')[0]
    const captured = button.props.summaryRef
    assert.equal(captured, first)
    store.temporary = { id: 'two', source: { text: 'Selection two' }, entry }
    assert.equal(stops, 1)
    await Vue.nextTick()
    assert.equal(descendants(root, node => node.type === 'speech')[0].props.summaryRef, second)
    assert.deepEqual(await captured, { id: 'first' })
    app.unmount()
    assert.equal(store.temporary, null, 'closing the panel discards the temporary summary')
})

const textOf = node => node.type === '#comment' ? '' : `${node.text || ''}${node.children.map(textOf).join('')}`
async function mountBody(t, store, speech, settings = { summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }) {
    const Button = compileComponent(new URL('../../ui/TextToSpeechButton.vue', import.meta.url), {
        '../../stores/textToSpeech': { useTextToSpeechStore: () => speech }, '../../composables/useToast': { toast: { error() {}, success() {} } },
    })
    const Component = compileComponent(new URL('./SelectionSummaryBody.vue', import.meta.url), bodyDeps(store, speech, settings, Button))
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, { text: store.temporary?.source.text ?? 'Independent captured selection' }) }); app.mount(root)
    t.after(() => { if (root.children.length) app.unmount() })
    return { root, app }
}
for (const state of ['failed', 'cancelled']) test(`the panel retains actual Stop after committed partial summary becomes ${state}`, async t => {
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Captured source' }, entry: { state: 'running', text: 'Committed sentence.' } },
        openTemporary() {}, temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() { this.temporary = null },
    })
    const speech = Vue.reactive({ available: true, status: 'playing', stops: 0,
        statusForSummary() { return this.status }, statusForText: () => 'idle', stop() { this.stops++; this.status = 'idle' },
    })
    const { root } = await mountBody(t, store, speech)
    store.temporary.entry.state = state; store.temporary.entry.error = state === 'failed' ? 'stalled' : null
    await Vue.nextTick()
    const stop = descendants(root, n => n.type === 'wa-button' && n.props.title === 'Stop reading')[0]
    assert.ok(stop, 'buffered audio retains its matching Stop control')
    await stop.props.onClick(); assert.equal(speech.stops, 1)
})

test('Cancel retains the captured source and the panel for Regenerate; closing disposes it', async t => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useSummariesStore } = await import('../../../stores/summaries.js')
    setActivePinia(createPinia()); const store = useSummariesStore(), requests = [], releases = []
    store._fetch = async (url, options) => {
        if (options?.method === 'DELETE') { releases.push(url); return { ok: true } }
        if (url === '/api/summaries/') { requests.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ id: `h${requests.length}`, descriptor: null }) } }
        return { ok: true, body: new ReadableStream({}) }
    }
    const speech = Vue.reactive({ available: true, status: 'playing', stops: 0,
        statusForSummary() { return this.status }, statusForText: () => 'idle', stop() { this.stops++; this.status = 'idle' },
    })
    const { root, app } = await mountBody(t, store, speech)
    await store.temporary.entry; await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick()
    descendants(root, n => n.type === 'a' && textOf(n) === 'Cancel')[0].props.onClick({ preventDefault() {} })
    await Vue.nextTick()
    assert.ok(store.temporary, 'Cancel retains the panel')
    assert.equal(store.temporary.source.text, 'Independent captured selection')
    assert.equal(store.temporary.entry.state, 'cancelled'); assert.equal(speech.stops, 1)
    assert.equal(releases.length, 1)
    descendants(root, n => n.type === 'a' && textOf(n) === 'Regenerate')[0].props.onClick({ preventDefault() {} })
    await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick()
    assert.equal(requests.length, 2); assert.equal(requests[1].source.text, 'Independent captured selection')
    app.unmount()
    assert.equal(store.temporary, null); assert.equal(releases.length, 2)
})

const LONG = 'Long selection. '.repeat(40), NETWORK = 'Network source. '.repeat(40)
test('selection generation action stays visible but disables with a reason when no engine can run', async t => {
    const settings = Vue.reactive({ isTouchDevice: true, summaryModel: 'auto', enabledProviders: [] }), calls = [], lookups = []
    let networkAttempts = 0
    const speech = Vue.reactive({ keyPresent: true, available: false, statusForSelection: () => 'idle' })
    const store = { resolve: async sources => { lookups.push(sources); if (sources[0].text === NETWORK && ++networkAttempts === 1) throw new Error('Network unavailable'); return [{ openrouter_eligible: sources[0].text !== LONG }] } }
    const body = { props: ['text'], setup: p => { calls.push(p.text); return () => null } }
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url), selectionWidgetDeps(store, settings, speech, body))
    const restoreBrowser = fakeBrowser()
    const props = Vue.reactive({ selectedText: 'Captured selection. '.repeat(30), readOrigin: 'file', position: { left: 10, top: 20 }, clearSourceSelection() {} })
    const widget = Vue.ref(0) // The owner mounts a new widget for each selection.
    const { renderer, root } = makeRenderer(140, node => { node.focus = () => {} }); const app = renderer.createApp({ render: () => Vue.h(Component, { ...props, key: widget.value }) })
    app.mount(root); t.after(() => { app.unmount(); restoreBrowser() })
    const action = () => descendants(root, n => n.type === 'wa-button' && ['Summarize selection', 'Retry summary availability'].includes(n.props['aria-label']))[0]
    assert.equal(action().props.disabled, true); assert.match(action().props.title, /No summary engine/)
    action().props.onClick({ stopPropagation() {} }); assert.deepEqual(calls, [])
    props.readOrigin = null; await flush(); settings.summaryModel = 'openrouter'; await flush()
    assert.equal(lookups.length, 0, 'excluded selection contexts do not send temporary source text')
    props.readOrigin = 'file'; await flush(); assert.equal(action().props.disabled, false)
    action().props.onClick({ stopPropagation() {} }); await flush(); assert.deepEqual(calls, [CAPTURED])
    widget.value++; props.selectedText = LONG; await flush()
    assert.equal(action().props.disabled, true); assert.match(action().props.title, /source.*OpenRouter|OpenRouter.*source/)
    action().props.onClick({ stopPropagation() {} }); assert.deepEqual(calls, [CAPTURED])
    props.selectedText = NETWORK; await flush()
    assert.equal(action().props['aria-label'], 'Retry summary availability')
    assert.equal(action().props.disabled, false); assert.match(action().props.title, /Network unavailable/)
    action().props.onClick({ stopPropagation() {} }); await flush()
    assert.equal(networkAttempts, 2); assert.deepEqual(calls, [CAPTURED])
    assert.equal(action().props['aria-label'], 'Summarize selection')
    action().props.onClick({ stopPropagation() {} }); await flush(); assert.deepEqual(calls, [CAPTURED, NETWORK])
})
test('a selection shorter than a message minimum offers no summary action', async t => {
    const body = { props: ['text'], render: () => null }
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url),
        selectionWidgetDeps({}, { isTouchDevice: true, summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }, { available: false, statusForSelection: () => 'idle' }, body))
    const restoreBrowser = fakeBrowser()
    const props = Vue.reactive({ selectedText: 'x'.repeat(499), readOrigin: 'rendered', position: { left: 10, top: 20 } })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, props) }); app.mount(root); t.after(() => { app.unmount(); restoreBrowser() })
    const action = () => descendants(root, n => n.type === 'wa-button' && n.props['aria-label'] === 'Summarize selection')[0]
    assert.equal(action(), undefined)
    props.selectedText = 'x'.repeat(500); await flush(); assert.ok(action())
    props.selectedText = ('word ' + '\n ').repeat(300); await flush(); assert.ok(action(), 'whitespace runs count as one character')
})

const linkNamed = (root, name) => descendants(root, n => n.type === 'a' && textOf(n) === name)[0]

test('selection Regenerate disappears without an engine while the captured panel remains open', async t => {
    const settings = Vue.reactive({ summaryModel: 'auto', enabledProviders: [] })
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Captured' }, entry: { state: 'failed', text: 'Partial', error: 'stalled' } },
        openTemporary() {}, temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() {}, retryTemporary() {},
    })
    const { root } = await mountBody(t, store, { available: false, keyPresent: false, statusForSummary: () => 'idle', statusForText: () => 'idle' }, settings)
    assert.equal(linkNamed(root, 'Regenerate'), undefined); assert.ok(store.temporary)
})

test('captured selection retries a failed availability lookup before enabling generation', async t => {
    const settings = Vue.reactive({ summaryModel: 'openrouter', enabledProviders: [] }), lookups = []
    let retries = 0
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Captured selection', purpose: 'selection' }, entry: { state: 'failed', text: 'Retained partial', error: 'stalled' } },
        resolve(sources, options) { return new Promise((resolve, reject) => lookups.push({ sources, signal: options.signal, resolve, reject })) },
        openTemporary() {}, temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() {}, retryTemporary() { retries++ },
    })
    const { root } = await mountBody(t, store, { available: false, keyPresent: true, statusForSummary: () => 'idle', statusForText: () => 'idle' }, settings)
    lookups[0].reject(new Error('Network unavailable')); await flush()
    assert.equal(linkNamed(root, 'Regenerate'), undefined)
    const recheck = linkNamed(root, 'Retry availability check')
    assert.ok(recheck, 'the captured panel offers explicit availability recovery')
    recheck.props.onClick({ preventDefault() {} }); await flush()
    assert.equal(lookups.length, 2); assert.equal(lookups[0].signal.aborted, true)
    assert.equal(linkNamed(root, 'Regenerate'), undefined)
    assert.equal(store.temporary.source.text, 'Captured selection'); assert.equal(store.temporary.entry.text, 'Retained partial')
    lookups[1].resolve([{ openrouter_eligible: true }]); await flush()
    linkNamed(root, 'Regenerate').props.onClick({ preventDefault() {} }); assert.equal(retries, 1)
})

test('a recovered availability check that confirms an unavailable source keeps selection Regenerate away', async t => {
    let lookups = 0
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Long captured source', purpose: 'selection' }, entry: { state: 'failed', text: 'Retained' } },
        async resolve() { if (++lookups === 1) throw new Error('Offline'); return [{ openrouter_eligible: false }] },
        openTemporary() {}, temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() {}, retryTemporary() {},
    })
    const { root } = await mountBody(t, store, { available: false, keyPresent: true, statusForSummary: () => 'idle', statusForText: () => 'idle' },
        { summaryModel: 'openrouter', enabledProviders: [] })
    await flush()
    const recheck = linkNamed(root, 'Retry availability check')
    assert.ok(recheck); recheck.props.onClick({ preventDefault() {} }); await flush()
    assert.equal(linkNamed(root, 'Regenerate'), undefined)
    assert.equal(lookups, 2); assert.equal(store.temporary.source.text, 'Long captured source')
    assert.equal(linkNamed(root, 'Retry availability check'), undefined)
})
