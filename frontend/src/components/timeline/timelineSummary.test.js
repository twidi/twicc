import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import { PROVIDER } from '../../constants.js'
import { messageSummaryModule } from '../../../tests/helpers/messageSummaryHarness.js'
import * as snapshot from '../../utils/timeline/messageSnapshot.js'
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
test('timeline summary expands directly, preserves view through collapse, and resets on another event', async t => {
    const Message = compileComponent(new URL('./TimelineMessage.vue', import.meta.url), {
        '../../utils/timeline/messageSnapshot': snapshot,
        '../ui/MarkdownContent.vue': { props: ['source'], setup: p => () => Vue.h('original', p.source) },
        '../session/detail/items/MessageSummaryControls.vue': { setup: () => () => Vue.h('summary-controls') },
    })
    const summary = Vue.reactive({ view: 'summary', hasSummary: true, entry: { text: 'Short summary' }, showOriginal() { this.view = 'original' } })
    const props = Vue.reactive({ eventId: 1, kind: 'assistant_message.sent', data: { text: 'Original snapshot' }, expanded: true, summary })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Message, { ...props, 'onUpdate:expanded': v => { props.expanded = v } }) }); app.mount(root); t.after(() => app.unmount()); await flush()
    assert.equal(descendants(root, n => n.type === 'summary-controls').length, 1)
    assert.deepEqual(descendants(root, n => n.type === 'original').map(n => n.text), ['Short summary'], 'the summary takes the place of the original')
    props.expanded = false; await flush(); props.expanded = true; await flush(); assert.equal(summary.view, 'summary')
    props.eventId = 2; await flush(); assert.equal(props.expanded, false)
})
test('timeline assistant label keeps whole-message Summarize beside active-variant TTS, including collapsed rows', () => {
    const source = read('./TimelineEventRow.vue')
    assert.match(source, /useMessageSummary/)
    assert.match(source, /event_id: props\.row\.event\.id/)
    assert.match(source, /summary\.summarize/)
    assert.match(source, /:summary-ref=/)
    assert.match(source, /:summary-identity=/)
    assert.match(source, /:original-text="row\.event\.data\.text"/)
    assert.match(source, /@summary-changed="summary\.refreshSaved"/)
    assert.match(source, /messageExpanded\.value = true/)
})

const ORIGINAL = 'Original snapshot. '.repeat(30), ANOTHER = 'Another snapshot. '.repeat(30), UNSUMMARIZED = 'Unsummarized snapshot. '.repeat(30)
test('the actual row opens Summary from collapsed, passes exact saved references, and resets on recycled event', async t => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useSummariesStore } = await import('../../stores/summaries.js')
    const settings = Vue.reactive({ summaryModel: 'auto', enabledProviders: [PROVIDER.CLAUDE_CODE] })
    const module = messageSummaryModule(settings)
    setActivePinia(createPinia()); const store = useSummariesStore(), calls = [], busy = Vue.ref(false)
    let holdSaved = null, finishSaved, withSaved = true
    store._fetch = async (url, options) => {
        calls.push(url)
        if (url.includes('resolve')) return { ok: true, json: async () => ({ sources: JSON.parse(options.body).sources.map(() => ({ session_id: 's', source_key: 'native', source_hash: 'hash', policy_key: 'p', eligible: true, saved: withSaved ? { id: 7, result_hash: 'body-hash' } : null })) }) }
        if (holdSaved) await holdSaved
        return { ok: true, json: async () => ({ id: 7, source_key: 'native', source_hash: 'hash', policy_key: 'p', result_hash: 'body-hash', text: 'Saved summary' }) }
    }
    const Message = compileComponent(new URL('./TimelineMessage.vue', import.meta.url), {
        '../../utils/timeline/messageSnapshot': snapshot,
        '../ui/MarkdownContent.vue': { props: ['source'], setup: p => () => Vue.h('original', p.source) },
        '../session/detail/items/MessageSummaryControls.vue': { setup: () => () => Vue.h('summary-controls') },
    })
    const Row = compileComponent(new URL('./TimelineEventRow.vue', import.meta.url), {
        '../../composables/useMessageSummary.js': module,
        '../../stores/textToSpeech': { useTextToSpeechStore: () => ({ statusForText: () => busy.value ? 'playing' : 'idle', statusForSummary: () => 'idle' }) },
        'vue-router': { RouterLink: { render: () => null } },
        '../../constants': { SUMMARY_MIN_CHARS: 500 }, '../../stores/data': { useDataStore: () => ({ getSession: () => null, getProject: () => null }) },
        '../../utils/timeline/describeEvent': { describeEvent: () => ({ severity: 'info', kind: 'assistant_message.sent', parts: [{ text: 'Sent a message' }] }) },
        '../project/ProjectBadge.vue': { render: () => null }, './TimelineSessionMention.vue': { render: () => null },
        './TimelineEventLine.vue': { setup: (_, { slots }) => () => Vue.h('event-line', slots.wording?.()) },
        './TimelineMessage.vue': Message,
        '../ui/TextToSpeechButton.vue': { props: ['text', 'summaryRef', 'summaryIdentity', 'originalText'], emits: ['summary-changed'], setup: (p, { emit }) => () => Vue.h('speech', { ...p, changed: id => emit('summary-changed', id) }) },
    })
    const props = Vue.reactive({ row: { event: { id: 1, kind: 'assistant_message.sent', session_id: 's', data: { text: ORIGINAL } } }, context: {} })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp({ render: () => Vue.h(Row, props) }); app.mount(root); t.after(() => app.unmount())
    await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(descendants(root, n => n.type === 'original').length, 0)
    const summarize = descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0]
    assert.ok(summarize); await summarize.props.onClick(); await flush()
    assert.equal(descendants(root, n => n.type === 'original')[0].text, 'Saved summary')
    const speech = descendants(root, n => n.type === 'speech')[0]
    assert.deepEqual(speech.props.summaryRef(), { saved_id: 7, result_hash: 'body-hash' })
    assert.equal(speech.props.originalText, ORIGINAL)
    assert.equal(calls.filter(url => url === '/api/summaries/').length, 0)
    speech.props.changed(7); await flush(); assert.equal(calls.filter(url => url.includes('/saved/')).length, 2)
    assert.equal(descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0].props['aria-pressed'], true)
    await summarize.props.onClick(); await flush(); assert.equal(descendants(root, n => n.type === 'original')[0].text, ORIGINAL)
    assert.equal(descendants(root, n => n.type === 'speech')[0].props.summaryRef, null)
    await summarize.props.onClick(); await flush()
    props.row = { event: { id: 2, kind: 'assistant_message.sent', session_id: 's', data: { text: ANOTHER } } }
    await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    assert.equal(descendants(root, n => n.type === 'summary-controls').length, 0)
    assert.equal(descendants(root, n => n.type === 'speech')[0].props.text, ANOTHER)
    busy.value = true
    holdSaved = new Promise(resolve => { finishSaved = resolve })
    const pending = descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0].props.onClick()
    await flush()
    assert.equal(descendants(root, n => n.type === 'speech')[0].props.disabled, false, 'an original reading retains Stop while the saved body loads')
    finishSaved(); await pending
    settings.enabledProviders = []; await flush()
    const savedAction = descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0]
    assert.equal(savedAction.props.disabled, false, 'saved access stays enabled without a generation engine')
    assert.deepEqual(descendants(root, n => n.type === 'speech')[0].props.summaryRef(), { saved_id: 7, result_hash: 'body-hash' })
    withSaved = false
    props.row = { event: { id: 3, kind: 'assistant_message.sent', session_id: 's', data: { text: UNSUMMARIZED } } }
    await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush()
    const action = descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0]
    assert.equal(action.props.disabled, false, 'only the server knows whether a summary exists')
    await action.props.onClick(); await flush(); assert.equal(calls.filter(url => url === '/api/summaries/').length, 0)
    const refused = descendants(root, n => n.type === 'wa-button' && n.props['aria-pressed'] !== undefined)[0]
    assert.equal(refused.props.disabled, true); assert.match(refused.props.title, /No summary engine/)
})
