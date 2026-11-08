import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
const script = readFileSync(new URL('../../../android/app/src/gecko/assets/android-app-bridge/content.js', import.meta.url), 'utf8')
test('Gecko transport decodes the native JSON string and ignores child-frame requests', async () => {
    let listener, sent = 0
    const replies = []
    const window = { addEventListener: (_, fn) => { listener = fn }, postMessage: (...args) => replies.push(args) }
    const response = { protocol: 1, id: 'one', ok: true, state: { engine: 'Gecko' } }
    runInNewContext(script, { crypto: globalThis.crypto, window, location: { origin: 'https://twicc.example' }, browser: { runtime: {
        sendNativeMessage: async (app, request) => { assert.equal(app, 'twicc_android'); assert.equal(request.id, 'one'); sent++; return JSON.stringify(response) },
    } } })
    const data = { channel: 'twicc.android.request', request: { protocol: 1, id: 'one', command: 'getState' } }
    await listener({ source: {}, origin: 'https://twicc.example', data })
    assert.equal(sent, 0)
    await listener({ source: window, origin: 'https://foreign.example', data })
    assert.equal(sent, 0)
    await listener({ source: window, origin: 'https://twicc.example', data })
    assert.equal(sent, 1)
    assert.equal(JSON.stringify(replies[0][0].response), JSON.stringify(response))
    assert.equal(replies[0][1], 'https://twicc.example')
})

test('Gecko language reads bypass document registration and mutations wait for its committed identity', async () => {
    let listener, registerDocument
    const sent = [], replies = [], notifications = []
    const ready = new Promise(resolve => { registerDocument = resolve })
    const window = { addEventListener: (_, fn) => { listener = fn }, postMessage: data => replies.push(data) }
    runInNewContext(script, { crypto: globalThis.crypto, window, location: { origin: 'https://twicc.example' }, browser: { runtime: {
        sendNativeMessage(app, request) {
            if (app === 'twicc_tts_document') return ready
            sent.push(request)
            return Promise.resolve(JSON.stringify({ protocol: 1, id: request.id, ok: true, state: {} }))
        },
        connectNative: () => ({ onMessage: { addListener: callback => notifications.push(callback) } }),
        connect: () => ({ postMessage() {} }),
    } } })
    function request(command, id) {
        return listener({ source: window, origin: 'https://twicc.example',
            data: { channel: 'twicc.android.request', request: { protocol: 1, id, command, documentToken: 'page-forgery' } } })
    }
    const read = request('locale.get', 'read')
    await Promise.resolve()
    assert.equal(sent.length, 1)
    await read
    const selection = request('locale.set', 'select')
    await Promise.resolve()
    assert.equal(sent.length, 1)
    registerDocument('{}')
    await selection
    assert.equal(sent[1].id, 'select')
    assert.notEqual(sent[1].documentToken, 'page-forgery')
    assert.match(sent[1].documentToken, /^[a-f0-9-]{36}$/)
    notifications[0]({ protocol: 1, event: 'stateChanged' })
    assert.equal(replies.at(-1).response.event, 'stateChanged')
})
