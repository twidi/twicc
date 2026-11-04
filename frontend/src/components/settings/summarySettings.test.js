import * as temporaryGeneration from '../../composables/useTemporarySummaryGeneration.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as ttsConfig from '../../utils/ttsConfig.js'
import * as constants from '../../constants.js'

function mount(settings, tts, summaries = { request() { throw new Error('No automatic test') } }, parent = false) {
    const SegmentedControl = { props: ['modelValue', 'options'], setup: props => () => Vue.h('picker', props) }
    const Component = compileComponent(new URL('./SummarySettings.vue', import.meta.url), {
        '../../stores/settings': { useSettingsStore: () => settings },
        '../../stores/textToSpeech': { useTextToSpeechStore: () => tts },
        '../../stores/summaries': { useSummariesStore: () => summaries },
        '../../utils/ttsConfig': ttsConfig,
        '../../composables/useTemporarySummaryGeneration': temporaryGeneration, '../../constants': constants,
        '../ui/SegmentedControl.vue': SegmentedControl, '../ui/SummaryContent.vue': compileComponent(new URL('../ui/SummaryContent.vue', import.meta.url)),
    })
    const activeSection = Vue.ref('summaries')
    const popover = readFileSync(new URL('../app/SettingsPopover.vue', import.meta.url), 'utf8')
    assert.match(popover, /<SummarySettings v-if="activeSection === 'summaries'"/)
    const hide = popover.match(/function onPopoverAfterHide\(\) \{[\s\S]*?\n\}/)[0]
    const close = new Function('activeSection', 'benchmarkTaskStore', 'applyAnchor', `let popoverOpen = true; ${hide}; onPopoverAfterHide()`)
    const Host = { render: () => activeSection.value === 'summaries' ? Vue.h(Component) : null }
    const { renderer, root } = makeRenderer(); const app = renderer.createApp(parent ? Host : Component); app.mount(root)
    return { app, root, close() { close(activeSection, { resetTransientControls() {} }, () => {}) }, reopen() { activeSection.value = 'summaries' } }
}
test('summary settings use synced keys and preserve unavailable stored preferences', async t => {
    const settings = Vue.reactive({ summaryModel: 'openrouter', timelineSummaryEnabled: true, enabledProviders: [constants.PROVIDER.CODEX], setSummaryModel(value) { this.summaryModel = value }, setTimelineSummaryEnabled(value) { this.timelineSummaryEnabled = value } })
    const tts = Vue.reactive({ keyPresent: false, available: false })
    const { app, root } = mount(settings, tts); t.after(() => app.unmount())
    let picker = descendants(root, node => node.type === 'picker')[0]
    assert.equal(picker.props.modelValue, 'luna')
    assert.equal(settings.summaryModel, 'openrouter')
    assert.equal(picker.props.options.some(option => option.value === 'off'), false)
    assert.equal(picker.props.options.find(option => option.value === 'haiku').disabled, true)
    assert.equal(picker.props.options.find(option => option.value === 'openrouter').disabled, true)
    tts.keyPresent = true; await flush(); picker = descendants(root, node => node.type === 'picker')[0]
    assert.equal(picker.props.modelValue, 'openrouter')
    const toggle = descendants(root, node => node.type === 'wa-switch')[0]
    assert.equal(toggle.props.checked, true); toggle.props.onChange({ target: { checked: false } })
    assert.equal(settings.timelineSummaryEnabled, false)
})
test('auto remains selected with its default agent available and no audio config', t => {
    const settings = Vue.reactive({ summaryModel: 'auto', timelineSummaryEnabled: true, enabledProviders: [constants.PROVIDER.CLAUDE_CODE], setSummaryModel() {}, setTimelineSummaryEnabled() {} })
    const { app, root } = mount(settings, { keyPresent: false, available: false }); t.after(() => app.unmount())
    const picker = descendants(root, node => node.type === 'picker')[0]
    assert.equal(picker.props.modelValue, 'auto'); assert.equal(picker.props.options[0].value, 'auto')
    assert.match(picker.props.options[0].label, /Auto/)
})
test('both keys join the existing schema, validators, setters and synced collection', () => {
    const source = readFileSync(new URL('../../stores/settings.js', import.meta.url), 'utf8')
    assert.match(source, /summaryModel:\s*null/); assert.match(source, /timelineSummaryEnabled:\s*null/)
    assert.match(source, /setSummaryModel\(/); assert.match(source, /setTimelineSummaryEnabled\(/)
    assert.match(source, /summaryModel:\s*store\.summaryModel/); assert.match(source, /timelineSummaryEnabled:\s*store\.timelineSummaryEnabled/)
    assert.equal(constants.SYNCED_SETTINGS_KEYS.has('summaryModel'), true); assert.equal(constants.SYNCED_SETTINGS_KEYS.has('timelineSummaryEnabled'), true)
})

const textOf = node => node.type === '#comment' ? '' : `${node.text || ''}${node.children.map(textOf).join('')}`
const button = (root, label) => descendants(root, n => n.type === 'wa-button' && textOf(n) === label)[0]
const settingsForTest = () => Vue.reactive({ summaryModel: 'haiku', timelineSummaryEnabled: true, enabledProviders: [constants.PROVIDER.CLAUDE_CODE], setSummaryModel(value) { this.summaryModel = value } })
async function startTest(root) {
    descendants(root, n => n.type === 'wa-textarea')[0].props.onInput({ target: { value: 'Private temporary test' } })
    await flush(); button(root, 'Test summary').props.onClick(); await flush()
}
test('closing the actual Settings popover handler unmounts the real temporary owner and clears its test', async t => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useSummariesStore } = await import('../../stores/summaries.js')
    setActivePinia(createPinia()); const summaries = useSummariesStore(), released = []
    summaries._fetch = async (url, options) => {
        if (options?.method === 'DELETE') { released.push(url); return { ok: true } }
        if (url === '/api/summaries/') return { ok: true, json: async () => ({ id: 'settings-test', descriptor: null }) }
        return { ok: true, body: new ReadableStream({}) }
    }
    const settings = settingsForTest(), mounted = mount(settings, { keyPresent: false }, summaries, true)
    t.after(() => mounted.app.unmount()); await startTest(mounted.root)
    mounted.close(); await flush()
    assert.deepEqual(released, ['/api/summaries/settings-test/'])
    assert.equal(settings.summaryModel, 'haiku'); assert.equal(settings.timelineSummaryEnabled, true)
    mounted.reopen(); await flush()
    assert.equal(descendants(mounted.root, n => n.type === 'wa-textarea')[0].props.value, '')
    assert.doesNotMatch(textOf(mounted.root), /Generating summary/)
})
test('settings test displays actual engine, timings and fallback from the stream', async t => {
    const entry = Vue.reactive({ state: 'running', text: '', engine: null })
    const { app, root } = mount(settingsForTest(), { keyPresent: true }, { request: () => ({ entry, release() {} }) })
    t.after(() => app.unmount()); await startTest(root)
    Object.assign(entry, { state: 'complete', text: 'Actual result', engine: 'luna', first_text_ms: 125, total_ms: 250, fallback_reason: 'stalled' })
    await flush()
    assert.match(textOf(root), new RegExp(constants.TITLE_SUGGESTION_MODEL_LABELS.luna.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    assert.match(textOf(root), /Duration: 250 ms/); assert.match(textOf(root), /First text: 125 ms/)
    assert.match(textOf(root), /Fallback: stalled/)
})
test('settings explains a disabled stored preference and its effective engine without changing the preference', t => {
    const settings = settingsForTest(); settings.enabledProviders = [constants.PROVIDER.CODEX]
    const { app, root } = mount(settings, { keyPresent: false }); t.after(() => app.unmount())
    assert.ok(textOf(root).includes(`Stored preference: ${constants.TITLE_SUGGESTION_MODEL_LABELS.haiku}`))
    assert.ok(textOf(root).includes(`Effective engine: ${constants.TITLE_SUGGESTION_MODEL_LABELS.luna}`))
    assert.equal(settings.summaryModel, 'haiku')
})


test('settings Retry disables when providers and requested OpenRouter become unavailable', async t => {
    const settings = settingsForTest(), tts = Vue.reactive({ keyPresent: false })
    let requests = 0
    const { app, root } = mount(settings, tts, { request() { requests++; return { entry: Vue.reactive({ state: 'complete', text: 'Saved test result' }), release() {} } } })
    t.after(() => app.unmount()); await startTest(root)
    settings.enabledProviders = []; await flush()
    assert.equal(button(root, 'Retry').props.disabled, true); assert.match(button(root, 'Retry').props.title, /No summary engine/)
    button(root, 'Retry').props.onClick(); assert.equal(requests, 1)
})

test('OpenRouter-only temporary tests use source lookup, abort obsolete reads, and never cache private text', async t => {
    const { createPinia, setActivePinia } = await import('pinia')
    const { useSummariesStore } = await import('../../stores/summaries.js')
    setActivePinia(createPinia()); const summaries = useSummariesStore(), lookups = []
    summaries._fetch = async (url, options) => {
        assert.equal(url, '/api/summaries/resolve/')
        return new Promise(resolve => lookups.push({ source: JSON.parse(options.body).sources[0], signal: options.signal,
            finish(openrouter_eligible) { resolve({ ok: true, json: async () => ({ sources: [{ eligible: true, openrouter_eligible }] }) }) } }))
    }
    const settings = settingsForTest(); settings.summaryModel = 'openrouter'; settings.enabledProviders = []
    const { app, root } = mount(settings, { keyPresent: true }, summaries); t.after(() => app.unmount())
    const textarea = descendants(root, n => n.type === 'wa-textarea')[0]
    textarea.props.onInput({ target: { value: 'Short private source' } }); await flush()
    assert.equal(button(root, 'Test summary').props.disabled, true, 'wait for source evidence')
    assert.equal(lookups.length, 1); lookups[0].finish(true); await flush()
    assert.equal(button(root, 'Test summary').props.disabled, false)
    textarea.props.onInput({ target: { value: 'Long private source' } }); await flush()
    assert.equal(lookups[0].signal.aborted, true)
    assert.equal(button(root, 'Test summary').props.disabled, true)
    lookups[1].finish(false); await flush()
    assert.equal(button(root, 'Test summary').props.disabled, true)
    assert.match(button(root, 'Test summary').props.title, /source.*OpenRouter|OpenRouter.*source/)
    assert.deepEqual(summaries.descriptors, {}); assert.deepEqual(summaries.entries, {})
    textarea.props.onInput({ target: { value: 'Third private source' } }); await flush()
    app.unmount(); assert.equal(lookups[2].signal.aborted, true)
    lookups[2].finish(true); await flush(); assert.deepEqual(summaries.descriptors, {})
})
