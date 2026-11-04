import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, makeRenderer } from '../../../../tests/helpers/scrollerComponentHarness.js'
import * as touch from '../../../utils/touchTapFallback.js'
import * as selectionRead from '../../../utils/selectionRead.js'
const read = file => readFileSync(new URL(file, import.meta.url), 'utf8')

test('selection summary captures text before clearing and survives source unmount without TTS', t => {
    const calls = []
    const store = { openTemporary: text => calls.push(['open', text]) }
    const Component = compileComponent(new URL('./TextSelectionComment.vue', import.meta.url), {
        '../../../stores/summaries': { useSummariesStore: () => store },
        '../../../stores/codeComments': { formatComment: () => '' },
        '../../../stores/settings': { useSettingsStore: () => ({ isTouchDevice: true }) },
        '../../../composables/useMediaPreview': { isOpen: Vue.ref(false), openMediaPreview() {} },
        '../../../composables/useToast': { toast: { error() {}, success() {} } },
        '../../../stores/textToSpeech': { useTextToSpeechStore: () => ({ available: false, statusForSelection: () => 'idle' }) },
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
