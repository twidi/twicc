import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'

import { audioUrl, installPageHideRelease, useTextToSpeechStore, _resetSpeechForTests } from './textToSpeech.js'
import { setSpeechPlayerFactory } from '../utils/speechPlayer.js'
import { setPcmStreamPlayerFactory } from '../utils/pcmStreamPlayer.js'
import { CUT, GONE, REFUSED, UNREACHABLE } from '../utils/speechOutcome.js'
import { createScriptConfig, createTtsConfig } from '../utils/ttsConfig.js'

const FIELDS = {
    model: 'openai/gpt-4o-mini-tts',
    voice: 'alloy',
    response_format: 'mp3',
    speed: null,
    instructions: '',
}

const ABORTED = { ok: false, aborted: true }
const PCM_TYPE = 'audio/pcm;rate=24000;channels=1'

const SCRIPT_FIELDS = {
    command: 'say -o -',
    output: 'stream',
    audio_format: 'mp3',
    pcm_rate: null,
    pcm_channels: 1,
    max_chars: null,
}

function makeConfig(id = 'c1', overrides = {}) {
    return { id, name: 'Voice', ...FIELDS, validated: { ...FIELDS }, ...overrides }
}

function makeScriptConfig(id = 's1', overrides = {}) {
    const fields = { ...SCRIPT_FIELDS, ...overrides }
    return { id, kind: 'script', name: 'Local', ...fields, note: '', validated: { kind: 'script', ...fields } }
}

function payload({ activeId = 'c1', configs = [makeConfig()], keyPresent = true } = {}) {
    return { config: { active_id: activeId, configs }, key_present: keyPresent, env_path: '/data/.env' }
}

function jsonResponse(status, body) {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function opened(audioId = 'a1', contentType = 'audio/mpeg') {
    return jsonResponse(200, { audio_id: audioId, content_type: contentType })
}

function abortableRequest(name) {
    return (_body, signal) => {
        log.push(name)
        return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        })
    }
}

/** Let the pending promise continuations run until `predicate()` is truthy. */
async function until(predicate) {
    for (let i = 0; i < 100; i++) {
        if (predicate()) return
        await new Promise(resolve => setImmediate(resolve))
    }
    throw new Error('until: the condition never came true')
}

/** A few turns of the event loop, to show that something does NOT happen. */
async function settle() {
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve))
}

let log
let fakePlayer
let fakePcmPlayer
let store
let released
let endings
let ending

function createFakePlayer() {
    const player = {
        calls: [],
        handlers: null,
        prime() { log.push('prime') },
        playUrl(url, handlers) {
            log.push('playUrl')
            player.calls.push({ url })
            player.handlers = handlers
        },
        playBlob(blob, handlers) {
            log.push('playBlob')
            player.calls.push({ blob })
            player.handlers = handlers
        },
        stop() { log.push('stop') },
    }
    return player
}

function createFakePcmPlayer() {
    const player = {
        calls: [],
        handlers: null,
        prime() { log.push('pcm-prime') },
        play(url, format, handlers) {
            log.push('pcm-play')
            player.calls.push({ url, format })
            player.handlers = handlers
        },
        stop() { log.push('pcm-stop') },
    }
    return player
}

/** Start `play(text)` and let the media player report the first sound. */
async function startPlaying(text = 'hello') {
    const pending = store.play(text)
    const calls = fakePlayer.calls.length
    await until(() => fakePlayer.calls.length > calls)
    fakePlayer.handlers.onPlaying()
    return pending
}

const IPHONE = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', platform: 'iPhone', maxTouchPoints: 5 }
const DESKTOP_SAFARI = {
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
    platform: 'MacIntel',
    maxTouchPoints: 0,
}

/** Run `fn` with `nav` as the `navigator` (Node's own `navigator` is a getter-only global). */
async function onNavigator(nav, fn) {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
    Object.defineProperty(globalThis, 'navigator', { value: nav, configurable: true })
    try {
        return await fn()
    } finally {
        if (original) Object.defineProperty(globalThis, 'navigator', original)
        else delete globalThis.navigator
    }
}

beforeEach(() => {
    _resetSpeechForTests()
    setActivePinia(createPinia())
    log = []
    released = []
    endings = []
    ending = { state: 'complete', error: null }
    fakePlayer = createFakePlayer()
    fakePcmPlayer = createFakePcmPlayer()
    setSpeechPlayerFactory(() => fakePlayer)
    setPcmStreamPlayerFactory(() => fakePcmPlayer)
    store = useTextToSpeechStore()
    store._sendConfig = () => {}
    store._openSpeech = async () => {
        log.push('open')
        return opened()
    }
    store._fetchAudio = async () => { throw new Error('unexpected audio download') }
    store._fetchEnding = async (audioId) => {
        log.push('ending')
        endings.push(audioId)
        return jsonResponse(200, ending)
    }
    store._releaseAudio = (audioId, options) => {
        log.push('release')
        released.push([audioId, options])
    }
    store.applyConfig(payload())
})

// ── Opening ──────────────────────────────────────────────

test('primes the player before the request starts', async () => {
    await startPlaying()
    assert.deepEqual(log.filter(entry => !entry.endsWith('stop')), ['prime', 'pcm-prime', 'open', 'playUrl'])
})

test('sends the text, the active speech fields and cache: true', async () => {
    let sent
    store._openSpeech = async (body) => {
        sent = body
        return opened()
    }
    await startPlaying()
    assert.deepEqual(sent, { text: 'hello', kind: 'openrouter', ...FIELDS, cache: true })
})

test('a settings test sends its config id and cache: false, a message read does not send a config id', async () => {
    const bodies = []
    store._openSpeech = async (body) => {
        bodies.push(body)
        return opened()
    }
    const pending = store.playWith('hello', FIELDS, { configId: 'c1' })
    await until(() => fakePlayer.calls.length === 1)
    fakePlayer.handlers.onPlaying()
    await pending
    await startPlaying('hello again')
    assert.equal(bodies[0].config_id, 'c1')
    assert.equal(bodies[0].cache, false)
    assert.equal('config_id' in bodies[1], false)
    assert.equal(bodies[1].cache, true)
})

test('play goes loading → playing → idle on end', async () => {
    let resolveOpen
    store._openSpeech = () => new Promise(resolve => { resolveOpen = resolve })
    const pending = store.play('hello')
    assert.equal(store.status, 'loading')
    assert.equal(store.statusForText('hello'), 'loading')
    assert.equal(store.statusForText('other'), 'idle')
    resolveOpen(opened())
    await until(() => fakePlayer.handlers)
    // The audio is open, but nothing sounds yet.
    assert.equal(store.status, 'loading')
    fakePlayer.handlers.onPlaying()
    const result = await pending
    assert.equal(result.ok, true)
    assert.ok(result.ended instanceof Promise)
    assert.equal(store.status, 'playing')
    assert.equal(store.statusForText('hello'), 'playing')
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })
    assert.equal(store.status, 'idle')
    assert.equal(store.statusForText('hello'), 'idle')
})

test('stop during loading resolves as aborted without error', async () => {
    store._openSpeech = abortableRequest('open')
    const pending = store.play('hello')
    store.stop()
    assert.deepEqual(await pending, ABORTED)
    assert.equal(store.status, 'idle')
    assert.deepEqual(released, [])
})

test('ignores a response that lands after a newer play', async () => {
    const resolvers = []
    store._openSpeech = (body, signal) => new Promise(resolve => { resolvers.push({ text: body.text, signal, resolve }) })
    const first = store.play('a')
    assert.equal(resolvers[0].signal.aborted, false)
    const second = store.play('b')
    assert.equal(resolvers[0].signal.aborted, true)
    assert.equal(resolvers[1].signal.aborted, false)
    resolvers[1].resolve(opened('b1'))
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onPlaying()
    assert.equal((await second).ok, true)
    resolvers[0].resolve(opened('a1'))
    assert.deepEqual(await first, ABORTED)
    // The stale answer's audio is released at once, and never played.
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.deepEqual(fakePlayer.calls, [{ url: '/api/tts/audio/b1/' }])
    assert.equal(store.playback.text, 'b')
    assert.equal(store.status, 'playing')
})

test('a network failure resolves as an error and returns to idle', async () => {
    store._openSpeech = async () => { throw new TypeError('Failed to fetch') }
    assert.deepEqual(await store.play('hello'), { ok: false, error: UNREACHABLE })
    assert.equal(store.status, 'idle')
})

test('a non-JSON error body falls back to the status', async () => {
    store._openSpeech = async () => new Response('<html>Bad gateway</html>', { status: 502, headers: { 'Content-Type': 'text/html' } })
    assert.deepEqual(await store.play('hello'), { ok: false, error: 'Request failed (502)' })
    assert.equal(store.status, 'idle')
})

test('an error body becomes the result error', async () => {
    const error = 'Provider returned 400. Check the model, the voice, the format and the text length.'
    store._openSpeech = async () => jsonResponse(502, { error, upstream_status: 400 })
    assert.deepEqual(await store.play('hello'), { ok: false, error })
    assert.equal(store.status, 'idle')
})

// ── Players ──────────────────────────────────────────────

test('an mp3 plays its audio url through the media player', async () => {
    assert.equal(audioUrl('a/1 b'), '/api/tts/audio/a%2F1%20b/')
    const result = await startPlaying()
    assert.equal(result.ok, true)
    assert.deepEqual(fakePlayer.calls, [{ url: '/api/tts/audio/a1/' }])
    assert.deepEqual(fakePcmPlayer.calls, [])
})

test('a pcm type streams through the pcm player with its rate', async () => {
    store._openSpeech = async () => opened('a1', PCM_TYPE)
    const pending = store.play('hello')
    await until(() => fakePcmPlayer.handlers)
    assert.deepEqual(fakePcmPlayer.calls, [{ url: '/api/tts/audio/a1/', format: { rate: 24000, channels: 1 } }])
    assert.deepEqual(fakePlayer.calls, [])
    fakePcmPlayer.handlers.onPlaying()
    const result = await pending
    assert.equal(result.ok, true)
    assert.equal(store.status, 'playing')
    fakePcmPlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
})

test('an mp3 playback suspends the pcm context the click primed', async () => {
    const result = await startPlaying()
    const afterPrime = log.slice(log.indexOf('pcm-prime'))
    assert.ok(afterPrime.indexOf('pcm-stop') < afterPrime.indexOf('playUrl'))
    assert.ok(afterPrime.includes('pcm-stop'))
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })
})

test('a pcm playback keeps the context until its end, then suspends it', async () => {
    store._openSpeech = async () => opened('a1', PCM_TYPE)
    const pending = store.play('hello')
    await until(() => fakePcmPlayer.handlers)
    fakePcmPlayer.handlers.onPlaying()
    const result = await pending
    const afterPrime = log.slice(log.indexOf('pcm-prime'))
    assert.deepEqual(afterPrime.filter(entry => entry.startsWith('pcm')), ['pcm-prime', 'pcm-play'])
    fakePcmPlayer.handlers.onEnded()
    await result.ended
    assert.equal(log.at(-1), 'pcm-stop')
})

test('an open failure suspends the pcm context', async () => {
    store._openSpeech = async () => jsonResponse(502, { error: 'Provider returned 400' })
    await store.play('hello')
    assert.equal(log.at(-1), 'pcm-stop')
    assert.equal(log.lastIndexOf('pcm-stop') > log.indexOf('pcm-prime'), true)
})

test('a pcm type without a rate and channels is refused', async () => {
    store._openSpeech = async () => opened('a1', 'audio/pcm')
    assert.deepEqual(await store.play('hello'), { ok: false, error: REFUSED })
    assert.deepEqual(fakePcmPlayer.calls, [])
    assert.deepEqual(fakePlayer.calls, [])
    assert.deepEqual(endings, ['a1'])
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'idle')
})

test('a buffered platform plays pcm as a wav blob', async () => {
    await onNavigator(IPHONE, async () => {
        const fetched = []
        store._openSpeech = async () => opened('a1', PCM_TYPE)
        store._fetchAudio = async (url, signal) => {
            fetched.push({ url, signal })
            return new Response(new Uint8Array([1, 2, 3, 4]), { status: 200 })
        }
        const pending = store.play('hello')
        await until(() => fakePlayer.handlers)
        assert.equal(fetched.length, 1)
        assert.equal(fetched[0].url, '/api/tts/audio/a1/')
        assert.ok(fetched[0].signal instanceof AbortSignal)
        assert.deepEqual(fakePcmPlayer.calls, [])
        const { blob } = fakePlayer.calls[0]
        assert.equal(blob.type, 'audio/wav')
        const bytes = new Uint8Array(await blob.arrayBuffer())
        assert.equal(bytes.length, 44 + 4)
        assert.equal(new DataView(bytes.buffer).getUint32(24, true), 24000)
        assert.deepEqual([...bytes.slice(44)], [1, 2, 3, 4])
        fakePlayer.handlers.onPlaying()
        assert.equal((await pending).ok, true)
    })
})

test('a failed buffered download counts as a network error before the start', async () => {
    await onNavigator(IPHONE, async () => {
        store._openSpeech = async () => opened('a1', PCM_TYPE)
        store._fetchAudio = async () => new Response('gone', { status: 404 })
        ending = { state: 'unknown', error: null }
        assert.deepEqual(await store.play('hello'), { ok: false, error: GONE })
        assert.deepEqual(fakePlayer.calls, [])
        assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    })
})

test('a buffered platform asks for buffered sending', async () => {
    await onNavigator(IPHONE, async () => {
        let sent
        store._openSpeech = async (body) => {
            sent = body
            return opened('a1')
        }
        store._fetchAudio = async () => new Response(new Uint8Array([1, 2]), { status: 200 })
        const pending = store.play('hello')
        await until(() => fakePlayer.handlers)
        assert.deepEqual(sent, { text: 'hello', kind: 'openrouter', ...FIELDS, cache: true, buffered: true })
        fakePlayer.handlers.onPlaying()
        assert.equal((await pending).ok, true)
    })
})

test('desktop safari asks for buffered sending for mp3 only', async () => {
    await onNavigator(DESKTOP_SAFARI, async () => {
        const pcmFields = { ...FIELDS, response_format: 'pcm' }
        for (const [fields, expected] of [
            [FIELDS, { text: 'hello', kind: 'openrouter', ...FIELDS, cache: true, buffered: true }],
            [pcmFields, { text: 'hello', kind: 'openrouter', ...pcmFields, cache: true }],
        ]) {
            store.applyConfig(payload({ configs: [makeConfig('c1', { ...fields, validated: { ...fields } })] }))
            let sent
            store._openSpeech = (body, signal) => {
                sent = body
                return abortableRequest('open')(body, signal)
            }
            const pending = store.play('hello')
            await until(() => sent)
            assert.deepEqual(sent, expected)
            store.stop()
            assert.deepEqual(await pending, ABORTED)
        }
    })
})

// ── Script configs ───────────────────────────────────────

/** Apply `configs` with `activeId`, start `play('hello')` and return the body it sends (then stop). */
async function sentForPlay(configs, activeId, { keyPresent = true } = {}) {
    store.applyConfig(payload({ activeId, configs, keyPresent }))
    let sent
    store._openSpeech = (body, signal) => {
        sent = body
        return abortableRequest('open')(body, signal)
    }
    const pending = store.play('hello')
    await until(() => sent)
    store.stop()
    assert.deepEqual(await pending, ABORTED)
    return sent
}

test('a script message read sends its kind, its fields, its script id and cache: true, no config id', async () => {
    const sent = await sentForPlay([makeScriptConfig('s1')], 's1', { keyPresent: false })
    assert.deepEqual(sent, { text: 'hello', kind: 'script', ...SCRIPT_FIELDS, cache: true, script_id: 's1' })
})

test('a script settings test sends its config id and its script id, equal, and cache: false', async () => {
    let sent
    store._openSpeech = (body, signal) => {
        sent = body
        return abortableRequest('open')(body, signal)
    }
    const pending = store.playWith('hello', makeScriptConfig('s1'), { configId: 's1' })
    await until(() => sent)
    store.stop()
    assert.deepEqual(await pending, ABORTED)
    assert.deepEqual(sent, { text: 'hello', kind: 'script', ...SCRIPT_FIELDS, cache: false, config_id: 's1', script_id: 's1' })
})

test('an openrouter settings test sends no script id', async () => {
    let sent
    store._openSpeech = (body, signal) => {
        sent = body
        return abortableRequest('open')(body, signal)
    }
    const pending = store.playWith('hello', FIELDS, { configId: 'c1' })
    await until(() => sent)
    store.stop()
    assert.deepEqual(await pending, ABORTED)
    assert.equal('script_id' in sent, false)
})

test('a wav script on an iphone asks for buffered sending, like pcm', async () => {
    await onNavigator(IPHONE, async () => {
        const sent = await sentForPlay([makeScriptConfig('s1', { audio_format: 'wav' })], 's1')
        assert.equal(sent.buffered, true)
    })
})

test('a pcm script on an iphone asks for buffered sending', async () => {
    await onNavigator(IPHONE, async () => {
        const sent = await sentForPlay([makeScriptConfig('s1', { audio_format: 'pcm', pcm_rate: 24000 })], 's1')
        assert.equal(sent.buffered, true)
    })
})

test('desktop safari asks for buffered sending for an mp3 script only', async () => {
    await onNavigator(DESKTOP_SAFARI, async () => {
        assert.equal((await sentForPlay([makeScriptConfig('s1')], 's1')).buffered, true)
        assert.equal('buffered' in await sentForPlay([makeScriptConfig('s1', { audio_format: 'wav' })], 's1'), false)
    })
})

test('a validated script active config is available without the key', () => {
    store.applyConfig(payload({ activeId: 's1', configs: [makeScriptConfig('s1')], keyPresent: false }))
    assert.equal(store.available, true)
    store.applyConfig(payload({ activeId: 'c1', configs: [makeConfig('c1')], keyPresent: false }))
    assert.equal(store.available, false)
})

test('addConfig takes a kind: openrouter by default, or script', () => {
    store.applyConfig(payload({ activeId: null, configs: [] }))
    const sent = []
    store._sendConfig = () => sent.push(store.configs.map(c => c.kind))
    const openRouterId = store.addConfig()
    const scriptId = store.addConfig('script')
    assert.deepEqual(store.configs[0], createTtsConfig(openRouterId))
    assert.deepEqual(store.configs[1], createScriptConfig(scriptId))
    assert.deepEqual(sent, [['openrouter'], ['openrouter', 'script']])
})

test('stop aborts the buffered fetch', async () => {
    await onNavigator(IPHONE, async () => {
        let signal
        store._openSpeech = async () => opened('a1', PCM_TYPE)
        store._fetchAudio = (_url, fetchSignal) => {
            signal = fetchSignal
            return abortableRequest('download')(null, fetchSignal)
        }
        const pending = store.play('hello')
        await until(() => signal)
        store.stop()
        assert.equal(signal.aborted, true)
        assert.deepEqual(await pending, ABORTED)
        await settle()
        assert.deepEqual(fakePlayer.calls, [])
        assert.deepEqual(endings, [])
        assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    })
})

// ── Ending ───────────────────────────────────────────────

test('the ending step runs when the player ends, not when the download ends', async () => {
    const result = await startPlaying()
    await settle()
    assert.deepEqual(endings, [])
    assert.deepEqual(released, [])
    fakePlayer.handlers.onEnded()
    await until(() => endings.length)
    assert.deepEqual(endings, ['a1'])
    assert.deepEqual(await result.ended, { ok: true })
})

test('a pcm playback runs the ending step when the pcm player ends, not when the download ends', async () => {
    store._openSpeech = async () => opened('a1', PCM_TYPE)
    const pending = store.play('hello')
    await until(() => fakePcmPlayer.handlers)
    fakePcmPlayer.handlers.onPlaying()
    const result = await pending
    await settle()
    assert.deepEqual(endings, [])
    assert.deepEqual(released, [])
    fakePcmPlayer.handlers.onEnded()
    await until(() => endings.length)
    assert.deepEqual(endings, ['a1'])
    assert.deepEqual(await result.ended, { ok: true })
})

test('an ending answer that lands after a stop changes nothing', async () => {
    let resolveEnding
    store._fetchEnding = async (audioId) => {
        endings.push(audioId)
        return new Promise(resolve => { resolveEnding = resolve })
    }
    const result = await startPlaying()
    fakePlayer.handlers.onEnded()
    await until(() => resolveEnding)
    store.stop()
    assert.deepEqual(await result.ended, ABORTED)
    assert.equal(store.status, 'idle')
    resolveEnding(jsonResponse(200, { state: 'failed', error: 'Could not reach OpenRouter' }))
    await settle()
    // One release only: the stop's.
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'idle')
    assert.deepEqual(store.playback, { text: null, status: 'idle', source: null, configId: null })
})

test('the ending request has a timeout; a timed-out request is no answer', async () => {
    const signals = []
    store._fetchEnding = async (audioId, signal) => {
        signals.push(signal)
        throw new DOMException('The operation timed out.', 'TimeoutError')
    }
    let result = await startPlaying()
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })
    result = await startPlaying()
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await result.ended, { ok: false, error: UNREACHABLE })
    assert.equal(signals.length, 2)
    assert.ok(signals.every(signal => signal instanceof AbortSignal && !signal.aborted))
})

test('a normal end with a complete answer ends ok and releases', async () => {
    const result = await startPlaying()
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })
    assert.deepEqual(log.filter(entry => !entry.endsWith('stop')).slice(-2), ['ending', 'release'])
    assert.deepEqual(released, [['a1', undefined]])
    assert.equal(store.status, 'idle')
})

test('a late failure reaches the ended promise', async () => {
    ending = { state: 'failed', error: 'Could not reach OpenRouter' }
    const result = await startPlaying()
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: false, error: 'Could not reach OpenRouter' })
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'idle')
})

test('a cut while the backend still runs ends as cut', async () => {
    ending = { state: 'running', error: null }
    const result = await startPlaying()
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await result.ended, { ok: false, error: CUT })
})

test('a player error before the start resolves the start with the end table result', async () => {
    const pending = store.play('hello')
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await pending, { ok: false, error: UNREACHABLE })
    assert.deepEqual(endings, ['a1'])
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'idle')
})

test('a refused play resolves REFUSED', async () => {
    const pending = store.play('hello')
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onError('refused')
    assert.deepEqual(await pending, { ok: false, error: REFUSED })
    assert.equal(store.status, 'idle')
})

test('a refused source the backend no longer knows resolves GONE', async () => {
    // A media url that answers an HTTP error reaches the player as an unsupported source.
    ending = { state: 'unknown', error: null }
    const pending = store.play('hello')
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onError('refused')
    assert.deepEqual(await pending, { ok: false, error: GONE })
})

test('an empty audio that ends before any sound resolves the start as ok', async () => {
    const pending = store.play('hello')
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onEnded()
    const result = await pending
    assert.equal(result.ok, true)
    assert.deepEqual(await result.ended, { ok: true })
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'idle')
})

test('an ending answer that is not 2xx counts as no answer', async () => {
    // As an answer, this body would turn the normal end into an error.
    store._fetchEnding = async () => jsonResponse(500, { state: 'failed', error: 'boom' })
    let result = await startPlaying()
    fakePlayer.handlers.onEnded()
    assert.deepEqual(await result.ended, { ok: true })

    // No answer after an error: the backend cannot be reached.
    result = await startPlaying()
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await result.ended, { ok: false, error: UNREACHABLE })

    // A failed request and an unknown state are no answer either.
    store._fetchEnding = async () => { throw new TypeError('Failed to fetch') }
    result = await startPlaying()
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await result.ended, { ok: false, error: UNREACHABLE })

    store._fetchEnding = async () => jsonResponse(200, { state: 'paused', error: null })
    result = await startPlaying()
    fakePlayer.handlers.onError('network')
    assert.deepEqual(await result.ended, { ok: false, error: UNREACHABLE })
    assert.equal(store.status, 'idle')
})

// ── Stop and release ─────────────────────────────────────

test('stop releases the audio id and resolves ended as aborted, without an ending request', async () => {
    const result = await startPlaying()
    store.stop()
    assert.deepEqual(await result.ended, ABORTED)
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.ok(log.includes('stop'))
    assert.ok(log.includes('pcm-stop'))
    // A callback of the stopped player changes nothing.
    fakePlayer.handlers.onEnded()
    await settle()
    assert.deepEqual(endings, [])
    assert.equal(released.length, 1)
    assert.equal(store.status, 'idle')
})

test('stop before the first sound resolves the start as aborted', async () => {
    const pending = store.play('hello')
    await until(() => fakePlayer.handlers)
    store.stop()
    assert.deepEqual(await pending, ABORTED)
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
})

test('a newer play releases the previous audio id', async () => {
    const first = await startPlaying('a')
    let resolveOpen
    store._openSpeech = () => new Promise(resolve => { resolveOpen = resolve })
    const second = store.play('b')
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.deepEqual(await first.ended, ABORTED)
    resolveOpen(opened('b1'))
    await until(() => fakePlayer.calls.length === 2)
    fakePlayer.handlers.onPlaying()
    assert.equal((await second).ok, true)
    assert.equal(store.playback.text, 'b')
})

test('a stale player callback is ignored', async () => {
    await startPlaying('a')
    const stale = fakePlayer.handlers
    store._openSpeech = async () => opened('b1')
    await startPlaying('b')
    stale.onEnded()
    stale.onError('network')
    stale.onPlaying()
    await settle()
    assert.deepEqual(endings, [])
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
    assert.equal(store.status, 'playing')
    assert.equal(store.playback.text, 'b')
})

/** A `pagehide` event; `persisted`: the page goes to the back-forward cache. */
function pageHide(persisted = false) {
    return Object.assign(new Event('pagehide'), { persisted })
}

/** A fetch that records its calls and answers a 204. */
function recordingFetch() {
    const calls = []
    const fetchImpl = (url, options) => {
        calls.push([url, options])
        return Promise.resolve(new Response(null, { status: 204 }))
    }
    return { calls, fetchImpl }
}

test('pagehide releases the current audio id at once, with keepalive', async () => {
    const target = new EventTarget()
    const network = recordingFetch()
    installPageHideRelease(target, network.fetchImpl)
    installPageHideRelease(target, network.fetchImpl)
    // Nothing to release yet.
    target.dispatchEvent(pageHide())
    assert.deepEqual(network.calls, [])
    const result = await startPlaying()
    target.dispatchEvent(pageHide())
    // Synchronous, inside the event; one listener per target, however many calls.
    assert.deepEqual(network.calls, [['/api/tts/audio/a1/', { method: 'DELETE', keepalive: true }]])
    // The audio id is forgotten: neither a second pagehide nor a stop releases it again.
    target.dispatchEvent(pageHide())
    store.stop()
    assert.equal(network.calls.length, 1)
    assert.deepEqual(released, [])
    assert.deepEqual(await result.ended, ABORTED)
})

test('pagehide into the back-forward cache also stops the playback', async () => {
    const target = new EventTarget()
    const network = recordingFetch()
    installPageHideRelease(target, network.fetchImpl)
    const result = await startPlaying()
    target.dispatchEvent(pageHide(true))
    assert.deepEqual(network.calls, [['/api/tts/audio/a1/', { method: 'DELETE', keepalive: true }]])
    assert.equal(store.status, 'idle')
    assert.ok(log.includes('stop'))
    assert.deepEqual(await result.ended, ABORTED)
    assert.deepEqual(released, [])  // released once, by the pagehide

    // A reload (not persisted) leaves the playback alone: the page goes away.
    const next = await startPlaying('again')
    target.dispatchEvent(pageHide(false))
    assert.equal(store.status, 'playing')
    assert.equal(network.calls.length, 2)
    store.stop()
    assert.deepEqual(await next.ended, ABORTED)
})

test('pagehide into the back-forward cache stops a playback still loading', async () => {
    const target = new EventTarget()
    const network = recordingFetch()
    installPageHideRelease(target, network.fetchImpl)
    store._openSpeech = abortableRequest('open')
    const pending = store.play('hello')
    target.dispatchEvent(pageHide(true))
    assert.deepEqual(await pending, ABORTED)
    assert.equal(store.status, 'idle')
    assert.deepEqual(network.calls, [])
})

test('applyConfig stops a message playback when availability is lost', async () => {
    const result = await startPlaying()
    assert.equal(store.status, 'playing')
    store.applyConfig(payload({ keyPresent: false }))
    assert.equal(store.status, 'idle')
    assert.ok(log.includes('stop'))
    assert.deepEqual(await result.ended, ABORTED)
    assert.deepEqual(released.map(([audioId]) => audioId), ['a1'])
})

test('applyConfig aborts a message request when availability is lost', async () => {
    store._openSpeech = abortableRequest('open')
    const pending = store.play('hello')
    store.applyConfig(payload({ activeId: null }))
    assert.deepEqual(await pending, ABORTED)
    assert.equal(store.status, 'idle')
})

test('applyConfig keeps a test playback running', async () => {
    const pending = store.playWith('hello', FIELDS)
    await until(() => fakePlayer.handlers)
    fakePlayer.handlers.onPlaying()
    await pending
    store.applyConfig(payload({ keyPresent: false }))
    assert.equal(store.status, 'playing')
    assert.equal(store.playback.source, 'test')
})

// ── Configs and models ───────────────────────────────────

test('applyConfig fills the state from the payload', () => {
    assert.equal(store.activeId, 'c1')
    assert.equal(store.keyPresent, true)
    assert.equal(store.envPath, '/data/.env')
    assert.equal(store.activeConfig.id, 'c1')
    assert.equal(store.available, true)
})

test('the test sentence comes from tts.json and is saved back', () => {
    assert.equal(store.testText, '')
    store.applyConfig({ ...payload(), config: { ...payload().config, test_text: 'Bonjour' } })
    assert.equal(store.testText, 'Bonjour')
    const sent = []
    store._sendConfig = () => sent.push(store.testText)
    store.setTestText('Bonjour')
    assert.deepEqual(sent, [])
    store.setTestText('Salut')
    assert.equal(store.testText, 'Salut')
    assert.deepEqual(sent, ['Salut'])
    store.applyConfig({ ...payload(), config: { ...payload().config, test_text: 7 } })
    assert.equal(store.testText, '')
})

test('the adaptation choice comes from tts.json and is saved back with the configs', () => {
    assert.equal(store.adapt, null)
    store.applyConfig({ ...payload(), config: { ...payload().config, adapt: 'luna' } })
    assert.equal(store.adapt, 'luna')
    assert.equal(store._configPayload().adapt, 'luna')
    const sent = []
    store._sendConfig = () => sent.push(store._configPayload())
    assert.equal(store.setAdapt('luna'), true)
    assert.deepEqual(sent, [])
    assert.equal(store.setAdapt('off'), true)
    assert.equal(store.adapt, 'off')
    assert.deepEqual(sent, [{ active_id: 'c1', configs: store.configs, test_text: '', adapt: 'off' }])
    // An unknown choice is refused; null brings back the default.
    assert.equal(store.setAdapt('opus'), false)
    assert.equal(store.adapt, 'off')
    assert.equal(store.setAdapt(null), true)
    assert.equal(store.adapt, null)
    assert.equal(sent.length, 2)
    assert.equal(sent[1].adapt, null)
    // Any config write carries the stored choice: it never resets it.
    store.setAdapt('haiku')
    store.setTestText('Salut')
    assert.equal(sent.at(-1).adapt, 'haiku')
    assert.equal(sent.at(-1).test_text, 'Salut')
    // Missing or unknown in the payload: no stored choice.
    store.applyConfig({ ...payload(), config: { ...payload().config, adapt: 'opus' } })
    assert.equal(store.adapt, null)
    store.applyConfig(payload())
    assert.equal(store.adapt, null)
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
    assert.deepEqual(store.configs[0].validated, { kind: 'openrouter', ...tested })

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
    const pending = store.loadModels()
    assert.equal(store.modelsStatus, 'loading')
    await pending
    assert.deepEqual(store.models, models)
    assert.equal(store.modelsStatus, 'loaded')
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
    assert.equal(store.modelsStatus, 'failed')

    // The backend answers an empty list when OpenRouter is unreachable.
    store._requestModels = async () => jsonResponse(200, { models: [] })
    await store.loadModels()
    assert.equal(store.modelsStatus, 'failed')
})

test('only the latest model list load applies', async () => {
    let finishFirst
    store._requestModels = () => new Promise(resolve => { finishFirst = resolve })
    const first = store.loadModels()
    store._requestModels = async () => jsonResponse(200, { models: [{ id: 'm', name: 'M', voices: [] }] })
    await store.loadModels()
    assert.equal(store.modelsStatus, 'loaded')
    finishFirst(new Response('down', { status: 502 }))
    await first
    assert.equal(store.modelsStatus, 'loaded')
    assert.equal(store.models.length, 1)
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
