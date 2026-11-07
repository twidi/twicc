import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { computed } from 'vue'
import { i18n, setLocale, loadLocaleMessages } from '../i18n/index.js'
import { createAndroidAppClient } from './androidAppClient.js'
import { fetchChangelog } from './changelog.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

afterEach(() => setLocale('en'))

function clientFixture() {
    let receive
    const requests = []
    const target = { location: { origin: 'https://twicc.example' },
        addEventListener(type, callback) { receive = callback }, removeEventListener() {},
        postMessage(data) { requests.push(data.request) },
    }
    const client = createAndroidAppClient(target, { timeoutMs: 30 })
    return { client, reply(error) { receive({ source: target, origin: target.location.origin,
        data: { channel: 'twicc.android.response', response: { protocol: 1, id: requests.at(-1).id, ok: false, error } },
    }) } }
}

function assertLocalError(error, key) {
    assert.ok(error instanceof Error)
    assert.equal(error.uiMessage.key, key)
    const message = computed(() => error.message)
    const english = message.value
    setLocale('pt-BR')
    assert.equal(message.value, i18n.global.t(key, error.uiMessage.params))
    assert.notEqual(message.value, english)
}

for (const failure of ['closed', 'tooLarge', 'fallback']) {
    test(`Android ${failure} failure keeps metadata and follows later locale changes`, async () => {
        const f = clientFixture()
        let result
        if (failure === 'closed') {
            f.client.close()
            result = f.client.request('getState').catch(error => error)
        } else if (failure === 'tooLarge') {
            result = f.client.request('getState', { value: 'x'.repeat(20_000) }).catch(error => error)
        } else {
            result = f.client.request('getState').catch(error => error)
            f.reply(null)
        }
        assertLocalError(await result, `common.android.errors.${failure === 'fallback' ? 'actionFailed' : failure}`)
        f.client.close()
    })
}

test('Android request timeout retains a locale-independent descriptor', async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const f = clientFixture()
    const result = f.client.request('getState').catch(error => error)
    t.mock.timers.tick(30)
    assertLocalError(await result, 'common.android.errors.unreachable')
    f.client.close()
})

test('native Android diagnostic stays literal after locale changes', async () => {
    const f = clientFixture()
    const result = f.client.request('getState').catch(error => error)
    f.reply('Native detail @value {data} | reason')
    const error = await result
    setLocale('pt-BR')
    assert.equal(error.message, 'Native detail @value {data} | reason')
    assert.equal(error.uiMessage, undefined)
    f.client.close()
})

test('changelog HTTP failure keeps status parameters and follows later locale changes', async t => {
    t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 503 }))
    const error = await fetchChangelog().catch(error => error)
    assert.deepEqual(error.uiMessage.params, { status: 503 })
    assertLocalError(error, 'help.changelog.errors.fetch')
    assert.match(error.message, /503/)
})
