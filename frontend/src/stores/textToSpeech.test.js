import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'

import { useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'

const FIELDS = {
    model: 'openai/gpt-4o-mini-tts',
    voice: 'alloy',
    response_format: 'mp3',
    speed: null,
    instructions: '',
}

function makeConfig(id = 'c1', overrides = {}) {
    return { id, name: 'Voice', ...FIELDS, validated: { ...FIELDS }, ...overrides }
}

function payload({ activeId = 'c1', configs = [makeConfig()], keyPresent = true } = {}) {
    return { config: { active_id: activeId, configs }, key_present: keyPresent, env_path: '/data/.env' }
}

function audioResponse(bytes = 'audio') {
    return new Response(new Blob([bytes], { type: 'audio/mpeg' }), { status: 200 })
}

function jsonResponse(status, body) {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function abortableRequest(log) {
    return (body, signal) => {
        log.push('fetch')
        return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        })
    }
}

let log
let fakePlayer
let store

function createFakePlayer() {
    const player = {
        onEnded: null,
        onError: null,
        played: [],
        playImpl: null,
        prime() { log.push('prime') },
        play(blob, onEnded, onError) {
            log.push('play')
            player.played.push(blob)
            player.onEnded = onEnded
            player.onError = onError
            return player.playImpl ? player.playImpl() : Promise.resolve()
        },
        stop() {
            log.push('stop')
            player.onStop?.()
        },
    }
    return player
}

beforeEach(() => {
    _resetSpeechForTests()
    setActivePinia(createPinia())
    log = []
    fakePlayer = createFakePlayer()
    setSpeechPlayerFactory(() => fakePlayer)
    store = useTextToSpeechStore()
    store._sendConfig = () => {}
    store.applyConfig(payload())
})

test('primes the player before the request starts', async () => {
    store._requestSpeech = async () => {
        log.push('fetch')
        return audioResponse()
    }
    const result = await store.play('hello')
    assert.deepEqual(result, { ok: true })
    assert.deepEqual(log.filter(entry => entry !== 'stop'), ['prime', 'fetch', 'play'])
})

test('sends the text and the active speech fields', async () => {
    let sent
    store._requestSpeech = async (body) => {
        sent = body
        return audioResponse()
    }
    await store.play('hello')
    assert.deepEqual(sent, { text: 'hello', ...FIELDS })
})

test('play goes loading → playing → idle on end', async () => {
    let resolveRequest
    store._requestSpeech = () => new Promise(resolve => { resolveRequest = resolve })
    const pending = store.play('hello')
    assert.equal(store.status, 'loading')
    assert.equal(store.statusForText('hello'), 'loading')
    assert.equal(store.statusForText('other'), 'idle')
    resolveRequest(audioResponse())
    assert.deepEqual(await pending, { ok: true })
    assert.equal(store.status, 'playing')
    assert.equal(store.statusForText('hello'), 'playing')
    fakePlayer.onEnded()
    assert.equal(store.status, 'idle')
    assert.equal(store.statusForText('hello'), 'idle')
})

test('stop during loading resolves as aborted without error', async () => {
    store._requestSpeech = abortableRequest(log)
    const pending = store.play('hello')
    store.stop()
    assert.deepEqual(await pending, { ok: false, aborted: true })
    assert.equal(store.status, 'idle')
})

test('ignores a response that lands after a newer play', async () => {
    const resolvers = []
    store._requestSpeech = (body, signal) => new Promise(resolve => { resolvers.push({ text: body.text, signal, resolve }) })
    const first = store.play('a')
    assert.equal(resolvers[0].signal.aborted, false)
    const second = store.play('b')
    assert.equal(resolvers[0].signal.aborted, true)
    assert.equal(resolvers[1].signal.aborted, false)
    resolvers[1].resolve(audioResponse('b-audio'))
    assert.deepEqual(await second, { ok: true })
    resolvers[0].resolve(audioResponse('a-audio'))
    assert.deepEqual(await first, { ok: false, aborted: true })
    assert.equal(fakePlayer.played.length, 1)
    assert.equal(await fakePlayer.played[0].text(), 'b-audio')
    assert.equal(store.playback.text, 'b')
    assert.equal(store.status, 'playing')
})

test('stop while audio.play() is pending is not an error', async () => {
    store._requestSpeech = async () => audioResponse()
    fakePlayer.playImpl = () => new Promise((_resolve, reject) => {
        fakePlayer.onStop = () => reject(new DOMException('', 'AbortError'))
    })
    const pending = store.play('hello')
    while (!log.includes('play')) await new Promise(resolve => setImmediate(resolve))
    store.stop()
    assert.deepEqual(await pending, { ok: false, aborted: true })
    assert.equal(store.status, 'idle')
})

test('a refused audio.play() resolves as an error and returns to idle', async () => {
    store._requestSpeech = async () => audioResponse()
    fakePlayer.playImpl = () => Promise.reject(new DOMException('', 'NotAllowedError'))
    assert.deepEqual(await store.play('hello'), { ok: false, error: 'The browser refused to play the audio' })
    assert.equal(store.status, 'idle')
})

test('a media error during playback returns to idle', async () => {
    store._requestSpeech = async () => audioResponse()
    await store.play('hello')
    assert.equal(store.status, 'playing')
    fakePlayer.onError()
    assert.equal(store.status, 'idle')
})

test('a stale end callback does not reset a newer playback', async () => {
    store._requestSpeech = async () => audioResponse()
    await store.play('a')
    const staleEnded = fakePlayer.onEnded
    await store.play('b')
    staleEnded()
    assert.equal(store.status, 'playing')
    assert.equal(store.playback.text, 'b')
})

test('a network failure resolves as an error and returns to idle', async () => {
    store._requestSpeech = async () => { throw new TypeError('Failed to fetch') }
    assert.deepEqual(await store.play('hello'), { ok: false, error: 'Could not reach TwiCC' })
    assert.equal(store.status, 'idle')
})

test('a non-JSON error body falls back to the status', async () => {
    store._requestSpeech = async () => new Response('<html>Bad gateway</html>', { status: 502, headers: { 'Content-Type': 'text/html' } })
    assert.deepEqual(await store.play('hello'), { ok: false, error: 'Request failed (502)' })
    assert.equal(store.status, 'idle')
})

test('an error body becomes the result error', async () => {
    const error = 'Provider returned 400. Check the model, the voice, the format and the text length.'
    store._requestSpeech = async () => jsonResponse(502, { error, upstream_status: 400 })
    assert.deepEqual(await store.play('hello'), { ok: false, error })
    assert.equal(store.status, 'idle')
})

test('a cached text plays without a second request', async () => {
    let requests = 0
    store._requestSpeech = async () => {
        requests += 1
        return audioResponse()
    }
    assert.deepEqual(await store.play('hello'), { ok: true })
    fakePlayer.onEnded()
    assert.deepEqual(await store.play('hello'), { ok: true })
    assert.equal(requests, 1)
    assert.equal(fakePlayer.played.length, 2)
    assert.equal(store.status, 'playing')
})

test('a test playback bypasses the cache', async () => {
    let requests = 0
    store._requestSpeech = async () => {
        requests += 1
        return audioResponse()
    }
    const result1 = await store.playWith('hello', FIELDS)
    const result2 = await store.playWith('hello', FIELDS)
    assert.deepEqual([result1, result2], [{ ok: true }, { ok: true }])
    assert.equal(requests, 2)
    assert.equal(store.playback.source, 'test')
    assert.equal(store.statusForText('hello'), 'idle')
    assert.equal(store.status, 'playing')
})

test('applyConfig stops a message playback when availability is lost', async () => {
    store._requestSpeech = async () => audioResponse()
    await store.play('hello')
    assert.equal(store.status, 'playing')
    store.applyConfig(payload({ keyPresent: false }))
    assert.equal(store.status, 'idle')
    assert.ok(log.includes('stop'))
})

test('applyConfig aborts a message request when availability is lost', async () => {
    store._requestSpeech = abortableRequest(log)
    const pending = store.play('hello')
    store.applyConfig(payload({ activeId: null }))
    assert.deepEqual(await pending, { ok: false, aborted: true })
    assert.equal(store.status, 'idle')
})

test('applyConfig keeps a test playback running', async () => {
    store._requestSpeech = async () => audioResponse()
    await store.playWith('hello', FIELDS)
    store.applyConfig(payload({ keyPresent: false }))
    assert.equal(store.status, 'playing')
    assert.equal(store.playback.source, 'test')
})

test('applyConfig fills the state from the payload', () => {
    assert.equal(store.activeId, 'c1')
    assert.equal(store.keyPresent, true)
    assert.equal(store.envPath, '/data/.env')
    assert.equal(store.activeConfig.id, 'c1')
    assert.equal(store.available, true)
})

test('mutations never touch the applied payload', () => {
    const received = payload({ configs: [makeConfig('c1'), makeConfig('c2')] })
    const snapshot = structuredClone(received)
    store.applyConfig(received)
    store.addConfig()
    store.duplicateConfig('c1')
    store.updateConfig('c2', { voice: 'other' })
    store.markValidated('c2', { ...FIELDS, voice: 'other' })
    store.deleteConfig('c1')
    assert.deepEqual(received, snapshot)
})

test('activate refuses an unvalidated config; markValidated checks the tested fields', () => {
    const sent = []
    store._sendConfig = () => sent.push({ active_id: store.activeId, configs: store.configs.map(c => ({ ...c })) })
    store.applyConfig(payload({ activeId: null, configs: [] }))

    const id = store.addConfig()
    assert.equal(typeof id, 'string')
    assert.equal(store.activateConfig(id), false)
    assert.equal(store.activeId, null)

    store.updateConfig(id, { model: 'm', voice: 'v' })
    const tested = { ...FIELDS, model: 'm', voice: 'v' }
    assert.equal(store.markValidated(id, { ...tested, voice: 'other' }), false)
    assert.equal(store.configs[0].validated, null)
    assert.equal(store.markValidated(id, tested), true)
    assert.deepEqual(store.configs[0].validated, tested)

    assert.equal(store.activateConfig(id), true)
    assert.equal(store.activeId, id)
    assert.equal(store.available, true)

    store.updateConfig(id, { voice: 'changed' })
    assert.equal(store.configs[0].validated, null)
    assert.equal(store.available, false)

    assert.equal(sent.length, 5)
    assert.equal(sent.at(-1).active_id, id)
    assert.equal(sent.at(-1).configs[0].voice, 'changed')
})

test('duplicate, deactivate and delete send the config', () => {
    const sent = []
    store._sendConfig = () => sent.push({ active_id: store.activeId, ids: store.configs.map(c => c.id) })

    const copyId = store.duplicateConfig('c1')
    assert.notEqual(copyId, 'c1')
    assert.deepEqual(store.configs.map(c => c.id), ['c1', copyId])
    assert.equal(store.configs[1].name, 'Voice (copy)')

    store.deactivateConfig()
    assert.equal(store.activeId, null)

    assert.equal(store.activateConfig('c1'), true)
    assert.equal(store.deleteConfig('c1'), true)
    assert.equal(store.activeId, null)
    assert.deepEqual(store.configs.map(c => c.id), [copyId])

    assert.equal(sent.length, 4)
    assert.deepEqual(sent.at(-1), { active_id: null, ids: [copyId] })
})

test('loadModels fills the models', async () => {
    const models = [{ id: 'm', name: 'Model', voices: ['alloy'] }]
    store._requestModels = async () => jsonResponse(200, { models })
    await store.loadModels()
    assert.deepEqual(store.models, models)
})

test('loadModels never rejects and leaves the models empty on failure', async () => {
    store._requestModels = async () => { throw new TypeError('Failed to fetch') }
    await store.loadModels()
    assert.deepEqual(store.models, [])

    store._requestModels = async () => jsonResponse(503, { error: 'No key' })
    await store.loadModels()
    assert.deepEqual(store.models, [])

    store._requestModels = async () => new Response('not json', { status: 200 })
    await store.loadModels()
    assert.deepEqual(store.models, [])

    store._requestModels = async () => jsonResponse(200, { models: 'nope' })
    await store.loadModels()
    assert.deepEqual(store.models, [])
})

test('stop() on a fresh store is a no-op', () => {
    _resetSpeechForTests()
    setActivePinia(createPinia())
    assert.doesNotThrow(() => useTextToSpeechStore().stop())
    assert.equal(useTextToSpeechStore().status, 'idle')
})

test('a fresh store is not available (share pages never feed it)', () => {
    setActivePinia(createPinia())
    assert.equal(useTextToSpeechStore().available, false)
})
