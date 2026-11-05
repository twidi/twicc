import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createAndroidAppClient } from './androidAppClient.js'

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
    return { window, client, sent, reply }
}
for (const webview of [false, true]) {
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
