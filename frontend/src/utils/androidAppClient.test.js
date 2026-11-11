import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createAndroidAppClient } from './androidAppClient.js'
import { i18n, loadLocaleMessages } from '../i18n/index.js'

await loadLocaleMessages('fr')

function fixture(webview = false) {
    const listeners = new Set(), sent = []
    const window = { location: { origin: 'https://twicc.example' },
        addEventListener(type, fn) { listeners.add(fn) }, removeEventListener(type, fn) { listeners.delete(fn) },
        postMessage(data) { sent.push(data.request) },
    }
    if (webview) window.__twiccNativeTtsDocument = 'committed-document'
    if (webview) window.TwiccAndroid = { postMessage: text => sent.push(JSON.parse(text)) }
    const client = createAndroidAppClient(window, { timeoutMs: 30 })
    function reply(response, { origin = window.location.origin, source = window } = {}) {
        if (webview) window.__twiccNativeTtsDocument = 'committed-document'
    if (webview) window.TwiccAndroid.onmessage({ data: JSON.stringify(response) })
        else for (const fn of listeners) fn({ source, origin, data: { channel: 'twicc.android.response', response } })
    }
    return { window, client, sent, reply, environment(data, { source = window, origin = window.location.origin } = {}) {
        for (const fn of listeners) fn({ source, origin, data })
    } }
}
for (const webview of [false, true]) {
    test(`clipboard diagnostics require the latest native capability (${webview ? 'WebView' : 'Gecko'})`, async () => {
        const f = fixture(webview)
        assert.equal(f.client.supports('terminalClipboardDiagnostics'), false)
        for (const capabilities of [{ terminalClipboardDiagnostics: 1 }, {}, { terminalClipboardDiagnostics: true }]) {
            const pending = f.client.request('getState')
            f.reply({ protocol: 1, id: f.sent.at(-1).id, ok: true, state: { capabilities } })
            await pending
            assert.equal(f.client.supports('terminalClipboardDiagnostics'), capabilities.terminalClipboardDiagnostics === 1)
        }
        const other = f.client.request('tts.state')
        f.reply({ protocol: 1, id: f.sent.at(-1).id, ok: true,
            state: { capabilities: { terminalClipboardDiagnostics: 1 } } })
        await other
        assert.equal(f.client.supports('terminalClipboardDiagnostics'), false)
        const enable = f.client.request('getState')
        f.reply({ protocol: 1, id: f.sent.at(-1).id, ok: true,
            state: { capabilities: { terminalClipboardDiagnostics: 1 } } })
        await enable
        assert.equal(f.client.supports('terminalClipboardDiagnostics'), true)
        f.client.close()
        assert.equal(f.client.supports('terminalClipboardDiagnostics'), false)
    })
    test(`native state changes arrive without a request or heartbeat (${webview ? 'WebView' : 'Gecko'})`, () => {
        const f = fixture(webview)
        let changes = 0
        const stop = f.client.onStateChanged(() => changes++)
        f.reply({ protocol: 2, event: 'stateChanged' })
        f.reply({ protocol: 1, event: 'other' })
        if (!webview) {
            f.reply({ protocol: 1, event: 'stateChanged' }, { origin: 'https://other.example' })
            f.reply({ protocol: 1, event: 'stateChanged' }, { source: {} })
        }
        assert.equal(changes, 0)
        f.reply({ protocol: 1, event: 'stateChanged' })
        assert.equal(changes, 1)
        assert.equal(f.sent.length, 0)
        stop()
        f.reply({ protocol: 1, event: 'stateChanged' })
        assert.equal(changes, 1)
        f.client.onStateChanged(() => changes++)
        f.client.close()
        if (!webview) f.reply({ protocol: 1, event: 'stateChanged' })
        assert.equal(changes, 1)
    })
    test(`client correlates replies and propagates native errors (${webview ? 'WebView' : 'Gecko'})`, async () => {
        const f = fixture(webview)
        const first = f.client.request('getState'), second = f.client.request('switchInstance', { instanceId: 'other' })
        assert.equal(f.sent[1].instanceId, 'other')
        f.reply({ protocol: 1, id: f.sent[1].id, ok: false, error: 'Instance is unavailable.' })
        await assert.rejects(second, /Instance is unavailable/)
        f.reply({ protocol: 1, id: f.sent[0].id, ok: true, state: { engine: 'test' } })
        assert.deepEqual(await first, { engine: 'test' })
        f.client.close()
    })
}
test('client ignores iframe, foreign-origin, and unsupported-protocol responses', async () => {
    const f = fixture()
    const pending = f.client.request('getState')
    const response = { protocol: 1, id: f.sent[0].id, ok: true, state: 'untrusted' }
    f.reply(response, { source: {} })
    f.reply(response, { origin: 'https://other.example' })
    f.reply({ ...response, protocol: 2 })
    await assert.rejects(pending, /Could not contact the Android app/)
    f.client.close()
})
test('ordinary browser times out and closing a client releases pending requests', async () => {
    const f = fixture()
    await assert.rejects(f.client.request('getState'), /Could not contact the Android app/)
    const pending = f.client.request('getState')
    f.client.close()
    await assert.rejects(pending, /closed/i)
})
for (const webview of [false, true]) test(`UTF8 command bounds preserve large TTS and explicitly reject large menu frames (${webview})`, async () => {
    const f = fixture(webview)
    const pending = f.client.request('tts.manual', { request: { text: 'é'.repeat(20000) } })
    assert.equal(f.sent[0].request.text.length, 20000)
    f.reply({ protocol: 1, id: f.sent[0].id, ok: true, state: { accepted: true } }); await pending
    await assert.rejects(f.client.request('menuVisibility', { padding: 'é'.repeat(8192) }), /too much data/)
    await assert.rejects(f.client.request('tts.manual', { request: { text: 'é'.repeat(6 * 1024 * 1024) } }), /too much data/)
    assert.equal(f.sent.length, 1); f.client.close()
})

test('WebView waits for committed injection then freezes its document token across later state replies', async () => {
    const f = fixture(true)
    delete f.window.__twiccNativeTtsDocument
    const pending = f.client.request('tts.attach')
    assert.equal(f.sent.length, 0)
    f.window.__twiccNativeTtsDocument = 'original-document'
    await new Promise(resolve => setTimeout(resolve, 25))
    assert.equal(f.sent[0].documentToken, 'original-document')
    f.reply({ protocol: 1, id: f.sent[0].id, ok: true, state: { accepted: true, documentToken: 'untrusted-replacement' } })
    await pending
    f.window.__twiccNativeTtsDocument = 'new-document'
    const next = f.client.request('tts.state')
    assert.equal(f.sent[1].documentToken, 'original-document')
    f.reply({ protocol: 1, id: f.sent[1].id, ok: true, state: {} }); await next; f.client.close()
})

for (const webview of [false, true]) test(`transport exceptions offer recovery without exposing bridge internals (${webview})`, async () => {
    const f = fixture(webview)
    const transport = webview ? f.window.TwiccAndroid : f.window
    transport.postMessage = () => { throw new Error('The cookie export bridge disconnected.') }
    try {
        await assert.rejects(f.client.request('tts.manual'), {
            message: 'Could not contact the Android app. Reload this page and try again.',
        })
    } finally { f.client.close() }
})

test('invalid request data offers recovery without exposing serialization errors', async () => {
    const f = fixture()
    const args = {}; args.circular = args
    try {
        await assert.rejects(f.client.request('tts.manual', args), {
            message: 'Could not complete this action. Reload this page and try again.',
        })
    } finally { f.client.close() }
})

for (const webview of [false, true]) test(`language rejections retain authoritative state and reactive explanation (${webview})`, async () => {
    const f = fixture(webview)
    const state = { locale: 'de', revision: 8, supportedLocales: ['en', 'de', 'es', 'fr', 'pt-BR'] }
    const pending = f.client.request('locale.set', { locale: 'fr', expectedRevision: 7 })
    f.reply({ protocol: 1, id: f.sent[0].id, ok: false, state,
        ui_message: { key: 'android.language.errors.changed', params: {} } })
    const original = i18n.global.locale.value
    try {
        await assert.rejects(pending, error => {
            assert.deepEqual(error.state, state)
            assert.equal(error.uiMessage.key, 'android.language.errors.changed')
            i18n.global.locale.value = 'en'
            const english = error.message
            i18n.global.locale.value = 'fr'
            assert.notEqual(error.message, english)
            return true
        })
    } finally { i18n.global.locale.value = original; f.client.close() }
})

test('WebView language reads work before document readiness and selection uses the immutable document token', async () => {
    const f = fixture(true)
    delete f.window.__twiccNativeTtsDocument
    for (const command of ['getState', 'locale.get']) {
        const read = f.client.request(command)
        const request = f.sent.at(-1)
        assert.equal(request.command, command)
        assert.equal(request.documentToken, undefined)
        f.reply({ protocol: 1, id: request.id, ok: true, state: {} })
        await read
        delete f.window.__twiccNativeTtsDocument
    }
    const selection = f.client.request('locale.set', { locale: 'fr', expectedRevision: 0 })
    assert.equal(f.sent.length, 2)
    f.window.__twiccNativeTtsDocument = 'language-document'
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(f.sent[2].documentToken, 'language-document')
    f.reply({ protocol: 1, id: f.sent[2].id, ok: true, state: {} }); await selection
    f.window.__twiccNativeTtsDocument = 'replacement'
    const next = f.client.request('locale.set', { locale: 'de', expectedRevision: 1 })
    assert.equal(f.sent[3].documentToken, 'language-document')
    f.reply({ protocol: 1, id: f.sent[3].id, ok: true, state: {} }); await next
    f.client.close()
})

test('WebView missing document rejects language selection with a language explanation', async () => {
    const f = fixture(true)
    delete f.window.__twiccNativeTtsDocument
    try {
        await assert.rejects(f.client.request('locale.set', { locale: 'fr', expectedRevision: 0 }), error => {
            assert.equal(error.uiMessage.key, 'android.language.errors.staleDocument')
            assert.equal(f.sent.length, 0)
            return true
        })
    } finally { f.client.close() }
})


for (const webview of [false, true]) test(`bridgeErrorsRemainReactive (${webview})`, async () => {
    const f = fixture(webview)
    const pending = f.client.request('getState')
    f.reply({ protocol: 1, id: f.sent[0].id, ok: false,
        ui_message: { key: 'common.android.errors.actionFailed', params: {} } })
    try {
        await assert.rejects(pending, error => {
            i18n.global.locale.value = 'en'
            const english = error.message
            assert.equal(error.uiMessage.key, 'common.android.errors.actionFailed')
            i18n.global.locale.value = 'fr'
            assert.notEqual(error.message, english)
            return true
        })
    } finally { i18n.global.locale.value = 'en'; f.client.close() }
})

test('WebView proxy marks Android before any request or native response', async () => {
    const { isAndroidApp } = await import('./androidEnvironment.js')
    isAndroidApp.value = false
    const f = fixture(true)
    try { assert.equal(isAndroidApp.value, true); assert.equal(f.sent.length, 0) }
    finally { f.client.close(); isAndroidApp.value = false }
})

test('a matching native rejection marks Android independently of language or push readiness', async () => {
    const { isAndroidApp } = await import('./androidEnvironment.js')
    isAndroidApp.value = false
    const f = fixture()
    try {
        const pending = f.client.request('getState')
        f.reply({ protocol: 1, id: f.sent[0].id, ok: false })
        await assert.rejects(pending)
        assert.equal(isAndroidApp.value, true)
    } finally { f.client.close(); isAndroidApp.value = false }
})

test('Gecko environment evidence requires matching getState, protocol, source, origin, and a pending ID', async () => {
    const { isAndroidApp } = await import('./androidEnvironment.js')
    isAndroidApp.value = false
    const f = fixture()
    function acknowledge(data, { source = f.window, origin = f.window.location.origin } = {}) {
        // Exercise the registered document listener through the same fixture transport.
        f.environment(data, { source, origin })
    }
    try {
        const pending = f.client.request('getState'), other = f.client.request('locale.get')
        const valid = { channel: 'twicc.android.environment', protocol: 1, id: f.sent[0].id }
        acknowledge(valid, { source: {} })
        acknowledge(valid, { origin: 'https://foreign.example' })
        acknowledge({ ...valid, protocol: 2 })
        acknowledge({ ...valid, id: 'unknown' })
        acknowledge({ ...valid, id: f.sent[1].id })
        assert.equal(isAndroidApp.value, false)
        acknowledge(valid)
        assert.equal(isAndroidApp.value, true)
        f.reply({ protocol: 1, id: f.sent[0].id, ok: true, state: {} })
        f.reply({ protocol: 1, id: f.sent[1].id, ok: true, state: {} })
        await Promise.all([pending, other])
        isAndroidApp.value = false
        acknowledge(valid)
        assert.equal(isAndroidApp.value, false)
    } finally { f.client.close(); isAndroidApp.value = false }
})
