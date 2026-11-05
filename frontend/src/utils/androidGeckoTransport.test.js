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
