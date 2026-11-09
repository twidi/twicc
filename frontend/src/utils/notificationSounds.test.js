import test from 'node:test'
import assert from 'node:assert/strict'
import { isAndroidApp, androidPushState } from './androidEnvironment.js'
import { createAndroidAppClient } from './androidAppClient.js'
import { createAndroidLocaleController } from '../i18n/androidLocale.js'

const contexts = [], timers = []
class AudioContext {
    constructor() { contexts.push(this); this.currentTime = 2; this.destination = {}; this.oscillators = [] }
    createOscillator() {
        const oscillator = { frequency: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {},
            start(time) { this.startTime = time }, stop(time) { this.stopTime = time } }
        this.oscillators.push(oscillator)
        return oscillator
    }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} } }
}
const originalWindow = globalThis.window, originalDocument = globalThis.document
const document = new EventTarget()
function page({ webview = false, acknowledge = false, reply } = {}) {
    const target = new EventTarget()
    target.location = { origin: 'https://twicc.example' }
    target.AudioContext = AudioContext
    target.setTimeout = (callback, delay) => timers.push({ callback, delay })
    function message(data) {
        const event = new Event('message')
        Object.assign(event, { source: target, origin: target.location.origin, data })
        target.dispatchEvent(event)
    }
    function send(request) {
        if (acknowledge && request.command === 'getState') message({ channel: 'twicc.android.environment', protocol: 1, id: request.id })
        const response = reply?.(request)
        if (response) {
            const frame = { protocol: 1, id: request.id, ...response }
            if (webview) target.TwiccAndroid.onmessage({ data: JSON.stringify(frame) })
            else message({ channel: 'twicc.android.response', response: frame })
        }
    }
    target.postMessage = data => send(data.request)
    if (webview) target.TwiccAndroid = { postMessage: text => send(JSON.parse(text)) }
    globalThis.window = target
    globalThis.document = document
    return target
}
page()
const { playNotificationSound, NOTIFICATION_SOUNDS } = await import('./notificationSounds.js')
test.after(() => { globalThis.window = originalWindow; globalThis.document = originalDocument })
test.beforeEach(() => { isAndroidApp.value = false; androidPushState.value = null; contexts.length = 0; timers.length = 0; page() })
test.afterEach(() => { isAndroidApp.value = false; androidPushState.value = null })

for (const state of [{ permission: 'granted', status: 'ready' }, { permission: 'denied', status: 'ready' }, { permission: 'granted', status: 'error' }]) {
    test(`Android creates no audio contexts for any notification sound (${state.permission}/${state.status})`, () => {
        isAndroidApp.value = true
        androidPushState.value = state
        for (const sound of Object.values(NOTIFICATION_SOUNDS)) playNotificationSound(sound)
        assert.equal(contexts.length, 0)
        assert.equal(timers.length, 0)
    })
}
test('ordinary browser schedules the selected sound and keeps the existing close timer', () => {
    playNotificationSound('chime')
    assert.equal(contexts.length, 1)
    assert.equal(contexts[0].oscillators.length, 3)
    assert.equal(contexts[0].oscillators[0].startTime, 2)
    assert.equal(contexts[0].oscillators[2].stopTime, 2.5)
    assert.equal(timers[0].delay, 550)
})
test('none stays silent in ordinary browsers', () => { playNotificationSound('none'); assert.equal(contexts.length, 0) })

for (const webview of [false, true]) {
    for (const languageFailure of ['incompatible', 'rejected']) {
        test(`transport silences sound before menu handshake with ${languageFailure} language (${webview ? 'WebView' : 'Gecko'})`, async () => {
            const commands = []
            const target = page({ webview, acknowledge: !webview, reply: request => {
                commands.push(request.command)
                if (request.command === 'getState') return { ok: true, state: { capabilities: { appLanguage: 1 },
                    appLanguage: languageFailure === 'incompatible' ? {} : { locale: 'en', revision: 1, supportedLocales: ['en'] } } }
                // No locale.get response (and therefore no menu handshake) exists yet.
                playNotificationSound('signal')
                assert.equal(contexts.length, 0)
                return { ok: false, error: 'Unavailable' }
            } })
            const client = createAndroidAppClient(target, { timeoutMs: 20 })
            const controller = createAndroidLocaleController({ client, document, applyLocale: async locale => locale, readBrowserLocale: () => 'en' })
            try {
                await controller.initialize()
                assert.deepEqual(commands, languageFailure === 'incompatible' ? ['getState'] : ['getState', 'locale.get'])
                playNotificationSound('signal')
                assert.equal(contexts.length, 0)
                assert.equal(isAndroidApp.value, true)
            } finally { controller.dispose(); client.close() }
        })
    }
    test(`native transport evidence survives a timed-out getState (${webview ? 'WebView' : 'Gecko'})`, async () => {
        const client = createAndroidAppClient(page({ webview, acknowledge: !webview }), { timeoutMs: 20 })
        try {
            await assert.rejects(client.request('getState'), /Could not contact/)
            playNotificationSound('signal')
            assert.equal(isAndroidApp.value, true)
            assert.equal(contexts.length, 0)
        } finally { client.close() }
    })
}
test('ordinary browser timeout keeps web playback', async () => {
    const client = createAndroidAppClient(window, { timeoutMs: 20 })
    try {
        await assert.rejects(client.request('getState'), /Could not contact/)
        playNotificationSound('ping')
        assert.equal(isAndroidApp.value, false)
        assert.equal(contexts.length, 1)
    } finally { client.close() }
})
