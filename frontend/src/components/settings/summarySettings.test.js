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
        '../../stores/summaries': { useSummariesStore: () => ({ request() { throw new Error('No automatic test') } }) },
        '../../utils/ttsConfig': ttsConfig, '../../constants': constants,
        '../ui/SegmentedControl.vue': SegmentedControl, '../ui/SummaryContent.vue': { render: () => null },
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
