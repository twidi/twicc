import test from 'node:test'
import assert from 'node:assert/strict'
import { h, reactive } from 'vue'
import { compileComponent, makeRenderer, descendants, flush } from '../../../tests/helpers/scrollerComponentHarness.js'
import { i18n, loadLocaleMessages } from '../../i18n/index.js'
import * as messages from '../../i18n/uiMessages.js'
import * as configUtils from '../../utils/ttsConfig.js'
import * as constants from '../../constants.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const error = messages.localUiMessage('backend.tts.failed')
const failure = { ok: false, error: 'Text-to-speech failed', uiMessage: error }
const stub = { setup: () => () => h('stub') }

test('speech button sends retained descriptors for start and ending errors', async () => {
    const notices = []
    let result = failure
    const store = { available: true, statusForText: () => 'idle', play: async () => result }
    const Button = compileComponent(new URL('./TextToSpeechButton.vue', import.meta.url), {
        '../../stores/textToSpeech': { useTextToSpeechStore: () => store },
        '../../composables/useToast': { toast: { error: value => notices.push(value) } },
    })
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => h(Button, { text: 'Hello' }) })
    app.mount(root)
    try {
        const button = descendants(root, node => node.type === 'wa-button')[0]
        await button.props.onClick()
        result = { ok: true, ended: Promise.resolve(failure) }
        await button.props.onClick()
        await flush()
        assert.equal(notices.length, 2)
        assert.equal(notices[0], error)
        assert.equal(notices[1], error)
        i18n.global.locale.value = 'pt-BR'
        assert.equal(messages.resolveUiMessage(notices[0]), 'Falha na leitura em voz alta')
    } finally { app.unmount(); i18n.global.locale.value = 'en' }
})

for (const stage of ['start', 'ending']) {
    test(`speech settings retain ${stage} error metadata across locale changes`, async () => {
        const config = { id: 'script', kind: 'script', command: 'voice', audio_format: 'mp3', name: 'Voice' }
        const store = reactive({ configs: [config], playback: {}, status: 'idle', models: [], adapt: 'off',
            keyPresent: false, testText: 'Hello', modelsStatus: 'ready', loadModels() {},
            async playWith() { return stage === 'start' ? failure : { ok: true, ended: Promise.resolve(failure) } },
        })
        const Settings = compileComponent(new URL('../settings/TextToSpeechSettings.vue', import.meta.url), {
            '../../stores/textToSpeech': { useTextToSpeechStore: () => store },
            '../../stores/settings': { useSettingsStore: () => ({ enabledProviders: [] }) },
            '../../i18n/uiMessages.js': messages, '../../constants': constants, '../../utils/ttsConfig': configUtils,
            '../help/HelpFeatureLink.vue': stub, '../ui/FilterableSelect.vue': stub, '../ui/SegmentedControl.vue': stub,
        })
        const { renderer, root } = makeRenderer()
        const app = renderer.createApp(Settings)
        app.mount(root)
        try {
            const button = descendants(root, node => node.type === 'wa-button' && node.props.appearance === 'outlined')[0]
            await button.props.onClick()
            await flush()
            const displayed = () => descendants(root, node => node.props.class === 'tts-test-error')[0]?.text
            assert.equal(displayed(), 'Text-to-speech failed')
            i18n.global.locale.value = 'pt-BR'
            await flush()
            assert.equal(displayed(), 'Falha na leitura em voz alta')
        } finally { app.unmount(); i18n.global.locale.value = 'en' }
    })
}
