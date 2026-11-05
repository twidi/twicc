import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createAndroidAppClient } from './androidAppClient.js'

function fixture(webview = false) {
    const listeners = new Set(), sent = []
    const window = { location: { origin: 'https://twicc.example' },
        addEventListener(type, fn) { listeners.add(fn) }, removeEventListener(type, fn) { listeners.delete(fn) },
        postMessage(data) { sent.push(data.request) },
    }
    if (webview) window.TwiccAndroid = { postMessage: text => sent.push(JSON.parse(text)) }
    const client = createAndroidAppClient(window, { timeoutMs: 30 })
    function reply(response, { origin = window.location.origin, source = window } = {}) {
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
    await assert.rejects(pending, /unavailable|timed out/i)
    f.client.close()
})
test('ordinary browser times out and closing a client releases pending requests', async () => {
    const f = fixture()
    await assert.rejects(f.client.request('getState'), /unavailable|timed out/i)
    const pending = f.client.request('getState')
    f.client.close()
    await assert.rejects(pending, /closed/i)
})
