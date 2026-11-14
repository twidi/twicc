import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as ttsConfig from '../../utils/ttsConfig.js'
import * as constants from '../../constants.js'

function mount(settings, tts) {
    const SegmentedControl = { props: ['modelValue', 'options'], setup: props => () => Vue.h('picker', props) }
    const Component = compileComponent(new URL('./SummarySettings.vue', import.meta.url), {
        '../../stores/settings': { useSettingsStore: () => settings },
        '../../stores/textToSpeech': { useTextToSpeechStore: () => tts },
        '../../utils/ttsConfig': ttsConfig, '../../constants': constants,
        '../ui/SegmentedControl.vue': SegmentedControl,
        '../help/HelpFeatureLink.vue': { render: () => null },
    })
    const { renderer, root } = makeRenderer(); const app = renderer.createApp(Component); app.mount(root)
    return { app, root }
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
test('no stored choice shows the engine that runs, without an Auto option', t => {
    const settings = Vue.reactive({ summaryModel: 'auto', timelineSummaryEnabled: true, enabledProviders: [constants.PROVIDER.CLAUDE_CODE], setSummaryModel() {}, setTimelineSummaryEnabled() {} })
    const { app, root } = mount(settings, { keyPresent: false, available: false }); t.after(() => app.unmount())
    const picker = descendants(root, node => node.type === 'picker')[0]
    assert.equal(picker.props.modelValue, 'haiku'); assert.equal(settings.summaryModel, 'auto')
    assert.deepEqual(picker.props.options.map(option => option.value), ['haiku', 'luna', 'openrouter'])
})
test('OpenRouter names its model in its tooltip, or the missing key when it is unavailable', async t => {
    const settings = Vue.reactive({ summaryModel: 'auto', timelineSummaryEnabled: true, enabledProviders: [constants.PROVIDER.CLAUDE_CODE], setSummaryModel() {}, setTimelineSummaryEnabled() {} })
    const tts = Vue.reactive({ keyPresent: false })
    const { app, root } = mount(settings, tts); t.after(() => app.unmount())
    const option = () => descendants(root, node => node.type === 'picker')[0].props.options.find(o => o.value === 'openrouter')
    assert.equal(option().disabled, true); assert.equal(option().title, 'OpenRouter: its key is not set')
    tts.keyPresent = true; await flush()
    assert.equal(option().disabled, undefined); assert.equal(option().title, `OpenRouter: ${constants.OPENROUTER_MODEL_DESCRIPTION}`)
})
test('the settings have no temporary test', () => {
    const source = readFileSync(new URL('./SummarySettings.vue', import.meta.url), 'utf8')
    assert.doesNotMatch(source, /wa-textarea|Test summary|SummaryContent/)
})
test('both keys join the existing schema, validators, setters and synced collection', () => {
    const source = readFileSync(new URL('../../stores/settings.js', import.meta.url), 'utf8')
    assert.match(source, /summaryModel:\s*null/); assert.match(source, /timelineSummaryEnabled:\s*null/)
    assert.match(source, /setSummaryModel\(/); assert.match(source, /setTimelineSummaryEnabled\(/)
    assert.match(source, /summaryModel:\s*store\.summaryModel/); assert.match(source, /timelineSummaryEnabled:\s*store\.timelineSummaryEnabled/)
    assert.equal(constants.SYNCED_SETTINGS_KEYS.has('summaryModel'), true); assert.equal(constants.SYNCED_SETTINGS_KEYS.has('timelineSummaryEnabled'), true)
})
