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

test('selection summary captures text before clearing and survives source unmount without TTS', t => {
    const calls = []
    const store = { openTemporary: text => calls.push(['open', text]) }
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url), {
        '../../../stores/summaries': { useSummariesStore: () => store },
        '../../../stores/codeComments': { formatComment: () => '' },
        '../../../stores/settings': { useSettingsStore: () => ({ isTouchDevice: true, summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }) },
        '../../../composables/useMediaPreview': { isOpen: Vue.ref(false), openMediaPreview() {} },
        '../../../composables/useToast': { toast: { error() {}, success() {} } },
        '../../../stores/textToSpeech': { useTextToSpeechStore: () => ({ available: false, statusForSelection: () => 'idle' }) },
        '../../../utils/ttsConfig': ttsConfig,
        '../../../composables/useTemporarySummaryGeneration': temporaryGeneration,
        '../../../utils/selectionRead': selectionRead, '../../../utils/touchTapFallback': touch,
    })
    const originalWindow = globalThis.window
    globalThis.window = { removeEventListener() {}, visualViewport: null }
    const originalDocument = globalThis.document
    globalThis.document = { addEventListener() {}, removeEventListener() {} }
    t.after(() => { globalThis.document = originalDocument; globalThis.window = originalWindow })
    const props = Vue.reactive({ selectedText: 'Captured before clear', readOrigin: 'rendered', position: { left: 10, top: 20 }, clearSourceSelection() { calls.push(['clear']); props.selectedText = '' } })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, { ...props, onClose: () => calls.push(['close']) }) }); app.mount(root)
    const button = descendants(root, node => node.type === 'wa-button' && node.props['aria-label'] === 'Summarize selection')[0]
    assert.ok(button); button.props.onClick({ stopPropagation() {} }); app.unmount()
    assert.deepEqual(calls, [['open', 'Captured before clear'], ['clear'], ['close']])
})
test('summary action is limited to rendered/file selections and uses touch fallback', () => {
    const source = read('./TextSelectionComment.vue')
    assert.match(source, /!props\.persistent/)
    assert.match(source, /\['rendered', 'file'\]\.includes\(props\.readOrigin\)/)
    assert.match(source, /@pointerdown="tapFallback\.down\('summary', \$event\)"/)
    assert.match(source, /@pointerup="onTapUp\('summary', \$event\)"/)
})
test('dialog mounts once in App and shared content renders plain paragraphs with an explicit previous-policy label', () => {
    const app = read('../../../App.vue')
    assert.equal((app.match(/<SelectionSummaryDialog\b/g) ?? []).length, 1)
    const dialog = read('../../ui/SelectionSummaryDialog.vue'), content = read('../../ui/SummaryContent.vue')
    assert.match(dialog, /playSummary|TextToSpeechButton/)
    assert.match(content, /<p\b/); assert.doesNotMatch(content, /MarkdownContent|v-html/)
    assert.match(content, /Previous policy/)
})

test('dialog binds the acquired reference at render and stops only its previous temporary reading on replacement', async t => {
    const first = Promise.resolve({ id: 'first' }), second = Promise.resolve({ id: 'second' })
    const entry = { state: 'running', text: 'Streamed summary' }
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Selection one' }, entry },
        temporaryReference() { return this.temporary.id === 'one' ? first : second },
        closeTemporary() { this.temporary = null }, retryTemporary() {},
    })
    let playing = { selectionId: 'one', variant: 'summary' }, stops = 0
    const speech = { statusForSummary(identity) { return identity.selectionId === playing?.selectionId ? 'loading' : 'idle' }, stop() { stops++; playing = null } }
    const Component = compileComponent(new URL('../../ui/SelectionSummaryDialog.vue', import.meta.url), {
        '../../stores/summaries': { useSummariesStore: () => store },
        '../../stores/settings': { useSettingsStore: () => ({ summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }) },
        '../../utils/ttsConfig': ttsConfig,
        '../../composables/useTemporarySummaryGeneration': temporaryGeneration,
        '../../stores/textToSpeech': { useTextToSpeechStore: () => speech },
        '../../composables/useToast': { toast: { error() {}, success() {} } },
        './SummaryContent.vue': { props: ['entry'], render: () => null },
        './TextToSpeechButton.vue': { props: ['summaryRef', 'summaryIdentity'], setup: props => () => Vue.h('speech', props) },
    })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp(Component); app.mount(root); t.after(() => app.unmount())
    const button = descendants(root, node => node.type === 'speech')[0]
    const captured = button.props.summaryRef
    assert.equal(captured, first)
    store.temporary = { id: 'two', source: { text: 'Selection two' }, entry }
    assert.equal(stops, 1)
    await Vue.nextTick()
    assert.equal(descendants(root, node => node.type === 'speech')[0].props.summaryRef, second)
    assert.deepEqual(await captured, { id: 'first' })
    // A nested Web Awesome hide event cannot close the app-level dialog.
    const dialog = descendants(root, node => node.type === 'wa-dialog')[0]
    dialog.props.onWaHide({ target: {}, currentTarget: { open: false } })
    assert.equal(store.temporary.id, 'two')
    const own = { open: false }; dialog.props.onWaHide({ target: own, currentTarget: own })
    assert.equal(store.temporary, null); assert.equal(stops, 1)
})

const textOf = node => node.type === '#comment' ? '' : `${node.text || ''}${node.children.map(textOf).join('')}`
async function mountDialog(t, store, speech, settings = { summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] }) {
    const toast = { toast: { error() {}, success() {} } }
    const Content = compileComponent(new URL('../../ui/SummaryContent.vue', import.meta.url))
    const Button = compileComponent(new URL('../../ui/TextToSpeechButton.vue', import.meta.url), {
        '../../stores/textToSpeech': { useTextToSpeechStore: () => speech }, '../../composables/useToast': toast,
    })
    const Component = compileComponent(new URL('../../ui/SelectionSummaryDialog.vue', import.meta.url), {
        '../../stores/summaries': { useSummariesStore: () => store },
        '../../stores/settings': { useSettingsStore: () => settings },
        '../../utils/ttsConfig': ttsConfig,
        '../../composables/useTemporarySummaryGeneration': temporaryGeneration,
        '../../stores/textToSpeech': { useTextToSpeechStore: () => speech }, '../../composables/useToast': toast,
        './SummaryContent.vue': Content, './TextToSpeechButton.vue': Button,
    })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp(Component); app.mount(root)
    t.after(() => app.unmount())
    return root
}
for (const state of ['failed', 'cancelled']) test(`dialog retains actual Stop after committed partial summary becomes ${state}`, async t => {
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Captured source' }, entry: { state: 'running', text: 'Committed sentence.' } },
        temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() { this.temporary = null },
    })
    const speech = Vue.reactive({ available: true, status: 'playing', stops: 0,
        statusForSummary() { return this.status }, statusForText: () => 'idle', stop() { this.stops++; this.status = 'idle' },
    })
    const root = await mountDialog(t, store, speech)
    store.temporary.entry.state = state; store.temporary.entry.error = state === 'failed' ? 'stalled' : null
    await Vue.nextTick()
    const stop = descendants(root, n => n.type === 'wa-button' && n.props.title === 'Stop reading')[0]
    assert.ok(stop, 'buffered audio retains its matching Stop control')
    await stop.props.onClick(); assert.equal(speech.stops, 1)
})

test('dialog Cancel retains captured source and panel for Retry; Close disposes it', async t => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useSummariesStore } = await import('../../../stores/summaries.js')
    setActivePinia(createPinia()); const store = useSummariesStore(), requests = [], releases = []
    store._fetch = async (url, options) => {
        if (options?.method === 'DELETE') { releases.push(url); return { ok: true } }
        if (url === '/api/summaries/') { requests.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ id: `h${requests.length}`, descriptor: null }) } }
        return { ok: true, body: new ReadableStream({}) }
    }
    const first = store.openTemporary('Independent captured selection'); await first.ready
    const speech = Vue.reactive({ available: true, status: 'playing', stops: 0,
        statusForSummary() { return this.status }, statusForText: () => 'idle', stop() { this.stops++; this.status = 'idle' },
    })
    const root = await mountDialog(t, store, speech)
    descendants(root, n => n.type === 'wa-button' && textOf(n) === 'Cancel')[0].props.onClick()
    await Vue.nextTick()
    assert.ok(store.temporary, 'Cancel retains the panel')
    assert.equal(store.temporary.source.text, 'Independent captured selection')
    assert.equal(store.temporary.entry.state, 'cancelled'); assert.equal(speech.stops, 1)
    assert.equal(releases.length, 1)
    descendants(root, n => n.type === 'wa-button' && textOf(n) === 'Retry')[0].props.onClick()
    await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick()
    assert.equal(requests.length, 2); assert.equal(requests[1].source.text, 'Independent captured selection')
    descendants(root, n => n.type === 'wa-button' && textOf(n) === 'Close')[0].props.onClick()
    assert.equal(store.temporary, null); assert.equal(releases.length, 2)
})

test('shared summary content identifies interrupted partial text and a restarted fallback revision', async t => {
    const Component = compileComponent(new URL('../../ui/SummaryContent.vue', import.meta.url))
    const entry = Vue.reactive({ state: 'failed', text: 'Usable partial sentence.', error: 'stalled', revision: 1 })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, { entry }) })
    app.mount(root); t.after(() => app.unmount())
    assert.match(textOf(root), /Interrupted/); assert.match(textOf(root), /Usable partial sentence/)
    Object.assign(entry, { state: 'running', text: 'Replacement text', error: null, revision: 2, fallback_reason: 'stalled' })
    await Vue.nextTick()
    assert.match(textOf(root), /Summary restarted/); assert.match(textOf(root), /Replacement text/)
    assert.doesNotMatch(textOf(root), /Interrupted|Usable partial sentence/)
})


test('selection generation action stays visible but disables with a reason when no engine can run', async t => {
    const settings = Vue.reactive({ isTouchDevice: true, summaryModel: 'auto', enabledProviders: [] }), calls = [], lookups = []
    const speech = Vue.reactive({ keyPresent: true, available: false, statusForSelection: () => 'idle' })
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url), {
        '../../../stores/summaries': { useSummariesStore: () => ({ openTemporary: text => calls.push(text), resolve: async sources => { lookups.push(sources); return [{ openrouter_eligible: sources[0].text !== 'Long selection' }] } }) },
        '../../../stores/codeComments': { formatComment: () => '' }, '../../../stores/settings': { useSettingsStore: () => settings },
        '../../../composables/useMediaPreview': { isOpen: Vue.ref(false), openMediaPreview() {} },
        '../../../composables/useToast': { toast: { error() {}, success() {} } },
        '../../../stores/textToSpeech': { useTextToSpeechStore: () => speech },
        '../../../utils/ttsConfig': ttsConfig,
        '../../../composables/useTemporarySummaryGeneration': temporaryGeneration, '../../../utils/selectionRead': selectionRead, '../../../utils/touchTapFallback': touch,
    })
    const originalWindow = globalThis.window, originalDocument = globalThis.document
    globalThis.window = { removeEventListener() {}, visualViewport: null }
    globalThis.document = { addEventListener() {}, removeEventListener() {} }
    const props = Vue.reactive({ selectedText: 'Captured selection', readOrigin: 'file', position: { left: 10, top: 20 }, clearSourceSelection() {} })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Component, props) })
    app.mount(root); t.after(() => { app.unmount(); globalThis.window = originalWindow; globalThis.document = originalDocument })
    const action = () => descendants(root, n => n.type === 'wa-button' && n.props['aria-label'] === 'Summarize selection')[0]
    assert.equal(action().props.disabled, true); assert.match(action().props.title, /No summary engine/)
    action().props.onClick({ stopPropagation() {} }); assert.deepEqual(calls, [])
    props.readOrigin = null; await flush(); settings.summaryModel = 'openrouter'; await flush()
    assert.equal(lookups.length, 0, 'excluded selection contexts do not send temporary source text')
    props.readOrigin = 'file'; await flush(); assert.equal(action().props.disabled, false)
    action().props.onClick({ stopPropagation() {} }); assert.deepEqual(calls, ['Captured selection'])
    props.selectedText = 'Long selection'; await flush()
    assert.equal(action().props.disabled, true); assert.match(action().props.title, /source.*OpenRouter|OpenRouter.*source/)
    action().props.onClick({ stopPropagation() {} }); assert.deepEqual(calls, ['Captured selection'])
})
test('selection Retry disables without an engine while the captured panel remains open', async t => {
    let retries = 0
    const settings = Vue.reactive({ summaryModel: 'auto', enabledProviders: [] })
    const store = Vue.reactive({ temporary: { id: 'one', source: { text: 'Captured' }, entry: { state: 'failed', text: 'Partial', error: 'stalled' } },
        temporaryReference: () => Promise.resolve({ id: 'handle' }), closeTemporary() {}, retryTemporary() { retries++ },
    })
    const root = await mountDialog(t, store, { available: false, keyPresent: false, statusForSummary: () => 'idle', statusForText: () => 'idle' }, settings)
    const retry = descendants(root, n => n.type === 'wa-button' && textOf(n) === 'Retry')[0]
    assert.equal(retry.props.disabled, true); assert.match(retry.props.title, /No summary engine/)
    retry.props.onClick(); assert.equal(retries, 0); assert.ok(store.temporary)
})
