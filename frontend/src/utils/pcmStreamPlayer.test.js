import test from 'node:test'
import assert from 'node:assert/strict'
import { CATCH_UP_LEAD_SECONDS, createPcmStreamPlayer, START_BUFFER_SECONDS } from './pcmStreamPlayer.js'

// 1000 Hz mono keeps the numbers readable: the start buffer is 300 frames (600 bytes).
const FORMAT = { rate: 1000, channels: 1 }
const START_FRAMES = START_BUFFER_SECONDS * FORMAT.rate

/** `frames` mono samples, all at 0.5 (0x4000 little-endian). */
function pcm(frames) {
    const bytes = new Uint8Array(frames * 2)
    for (let i = 1; i < bytes.length; i += 2) bytes[i] = 0x40
    return bytes
}

/** A context that runs; `resume()` makes a suspended one run again. */
function fakeContext() {
    const ctx = {
        currentTime: 0,
        destination: { name: 'destination' },
        state: 'running',
        resumeCalls: 0,
        suspendCalls: 0,
        sources: [],
        resume() {
            ctx.resumeCalls++
            ctx.state = 'running'
            return Promise.resolve()
        },
        suspend() {
            ctx.suspendCalls++
            ctx.state = 'suspended'
            return Promise.resolve()
        },
        createBuffer(channels, length, sampleRate) {
            const data = Array.from({ length: channels }, () => new Float32Array(length))
            return {
                numberOfChannels: channels,
                length,
                sampleRate,
                duration: length / sampleRate,
                getChannelData: channel => data[channel],
            }
        },
        createBufferSource() {
            const source = {
                buffer: null,
                onended: null,
                when: null,
                connectedTo: null,
                stopped: false,
                disconnected: false,
                connect(node) {
                    source.connectedTo = node
                },
                disconnect() {
                    source.disconnected = true
                },
                start(when) {
                    source.when = when
                },
                stop() {
                    source.stopped = true
                },
            }
            ctx.sources.push(source)
            return source
        },
    }
    return ctx
}

/** A fetch whose body the test feeds; aborting the signal errors the body, like a real fetch. */
function fakeFetch({ status = 200 } = {}) {
    const calls = []
    let controller
    const body = new ReadableStream({
        start(c) {
            controller = c
        },
    })
    const fetchImpl = (url, { signal }) => {
        calls.push({ url, signal })
        signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
        return Promise.resolve({ ok: status >= 200 && status < 300, status, body })
    }
    return {
        fetchImpl,
        calls,
        push: bytes => controller.enqueue(bytes),
        close: () => controller.close(),
        fail: () => controller.error(new TypeError('network error')),
    }
}

function recordingHandlers() {
    const events = []
    return {
        events,
        handlers: {
            onPlaying: () => events.push('playing'),
            onEnded: () => events.push('ended'),
            onError: reason => events.push(`error:${reason}`),
        },
    }
}

function setup(fetchOptions, playerOptions = {}) {
    const ctx = fakeContext()
    const network = fakeFetch(fetchOptions)
    const player = createPcmStreamPlayer({ fetchImpl: network.fetchImpl, audioContextFactory: () => ctx, ...playerOptions })
    const { events, handlers } = recordingHandlers()
    return { ctx, network, player, events, handlers }
}

// Lets the fetch promise and the body reads settle.
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

test('prime creates the context once and resumes it', () => {
    let created = 0
    const ctx = fakeContext()
    const player = createPcmStreamPlayer({ fetchImpl: fakeFetch().fetchImpl, audioContextFactory: () => (created++, ctx) })
    player.prime()
    player.prime()
    assert.equal(created, 1)
    assert.equal(ctx.resumeCalls, 2)
})

test('waits for the start buffer before scheduling', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.prime()
    player.play('/api/tts/audio/a1/', FORMAT, handlers)
    await flush()
    assert.equal(network.calls[0].url, '/api/tts/audio/a1/')

    network.push(pcm(START_FRAMES - 100))
    await flush()
    assert.equal(ctx.sources.length, 0)
    assert.deepEqual(events, [])

    network.push(pcm(100))
    await flush()
    assert.equal(ctx.sources.length, 2)
    assert.deepEqual(events, ['playing'])
    const [first] = ctx.sources
    assert.equal(first.connectedTo, ctx.destination)
    assert.equal(first.buffer.sampleRate, FORMAT.rate)
    assert.equal(first.buffer.length, START_FRAMES - 100)
    assert.equal(first.buffer.getChannelData(0)[0], 0.5)
})

test('schedules buffers back to back', async () => {
    const { ctx, network, player, handlers } = setup()
    ctx.currentTime = 5
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    network.push(pcm(120))
    await flush()
    network.push(pcm(80))
    await flush()
    const [first, second, third] = ctx.sources
    // The clock already passed the start of the playback: the first buffer starts just ahead of it.
    assert.equal(first.when, 5 + CATCH_UP_LEAD_SECONDS)
    assert.equal(second.when, first.when + first.buffer.duration)
    assert.equal(third.when, second.when + second.buffer.duration)
})

test('catches up after an underrun', async () => {
    const { ctx, network, player, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    assert.equal(ctx.sources[0].when, 0)

    // The clock passed the end of the scheduled audio: the next buffer starts just ahead of the
    // clock, not in the past (its start would be cut).
    ctx.currentTime = 2
    network.push(pcm(100))
    await flush()
    assert.equal(ctx.sources[1].when, 2 + CATCH_UP_LEAD_SECONDS)

    // The following buffers abut exactly, even when the clock moved on a little.
    ctx.currentTime = 2.05
    network.push(pcm(100))
    await flush()
    assert.equal(ctx.sources[2].when, ctx.sources[1].when + ctx.sources[1].buffer.duration)
})

test('fires onPlaying once and onEnded after the last source', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    network.push(pcm(100))
    network.close()
    await flush()
    assert.equal(ctx.sources.length, 2)
    assert.deepEqual(events, ['playing'])

    ctx.sources[0].onended()
    assert.deepEqual(events, ['playing'])
    ctx.sources[1].onended()
    assert.deepEqual(events, ['playing', 'ended'])
    assert.ok(ctx.sources.every(source => source.disconnected))
})

test('ends at the end of the body when every source already ended', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    ctx.sources[0].onended()
    assert.deepEqual(events, ['playing'])

    network.close()
    await flush()
    assert.deepEqual(events, ['playing', 'ended'])
})

test('a short body still plays', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(50))
    network.push(pcm(50))
    await flush()
    assert.equal(ctx.sources.length, 0)

    network.close()
    await flush()
    assert.equal(ctx.sources.length, 2)
    assert.deepEqual(events, ['playing'])
    ctx.sources.forEach(source => source.onended())
    assert.deepEqual(events, ['playing', 'ended'])
})

test('an empty body ends without playing', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.close()
    await flush()
    assert.equal(ctx.sources.length, 0)
    assert.deepEqual(events, ['ended'])
})

test('a read error after the start reports network after the scheduled sound', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    network.fail()
    await flush()
    assert.deepEqual(events, ['playing'])
    assert.equal(ctx.sources[0].stopped, false)

    ctx.sources[0].onended()
    assert.deepEqual(events, ['playing', 'error:network'])
})

test('a failed fetch before the start reports network', async () => {
    // The request fails.
    const ctx = fakeContext()
    const rejected = recordingHandlers()
    const player = createPcmStreamPlayer({
        fetchImpl: () => Promise.reject(new TypeError('Failed to fetch')),
        audioContextFactory: () => ctx,
    })
    player.play('/a', FORMAT, rejected.handlers)
    await flush()
    assert.deepEqual(rejected.events, ['error:network'])

    // The answer is not 2xx.
    const notFound = setup({ status: 404 })
    notFound.player.play('/a', FORMAT, notFound.handlers)
    await flush()
    assert.deepEqual(notFound.events, ['error:network'])

    // The body breaks before the start buffer is full: no wait for audio that never played.
    const cut = setup()
    cut.player.play('/a', FORMAT, cut.handlers)
    await flush()
    cut.network.push(pcm(100))
    await flush()
    cut.network.fail()
    await flush()
    assert.equal(cut.ctx.sources.length, 0)
    assert.deepEqual(cut.events, ['error:network'])
})

test('stop aborts the fetch and silences every handler', async () => {
    const { ctx, network, player, events, handlers } = setup()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    assert.deepEqual(events, ['playing'])
    const [source] = ctx.sources
    const { onended } = source

    player.stop()
    assert.equal(network.calls[0].signal.aborted, true)
    assert.equal(source.stopped, true)
    assert.equal(source.disconnected, true)
    // A real source still fires `ended` after `stop()`.
    onended?.()
    await flush()
    assert.deepEqual(events, ['playing'])

    // A stop during the request: the aborted fetch reports nothing either.
    let rejectFetch
    const pending = createPcmStreamPlayer({
        fetchImpl: (url, { signal }) =>
            new Promise((resolve, reject) => {
                rejectFetch = reject
                signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
            }),
        audioContextFactory: () => fakeContext(),
    })
    const silent = recordingHandlers()
    pending.play('/a', FORMAT, silent.handlers)
    pending.stop()
    rejectFetch(new TypeError('Failed to fetch'))
    await flush()
    assert.deepEqual(silent.events, [])
})

test('a new play stops the previous one', async () => {
    const ctx = fakeContext()
    const first = fakeFetch()
    const second = fakeFetch()
    const fetches = [first.fetchImpl, second.fetchImpl]
    const player = createPcmStreamPlayer({
        fetchImpl: (...args) => fetches.shift()(...args),
        audioContextFactory: () => ctx,
    })
    const old = recordingHandlers()
    player.play('/a', FORMAT, old.handlers)
    await flush()
    first.push(pcm(START_FRAMES))
    await flush()
    const [oldSource] = ctx.sources

    const next = recordingHandlers()
    player.play('/b', FORMAT, next.handlers)
    assert.equal(first.calls[0].signal.aborted, true)
    assert.equal(oldSource.stopped, true)
    await flush()
    second.push(pcm(START_FRAMES))
    second.close()
    await flush()
    // The new playback is not scheduled after the old, stopped one.
    assert.equal(ctx.sources[1].when, 0)
    ctx.sources[1].onended()
    assert.deepEqual(old.events, ['playing'])
    assert.deepEqual(next.events, ['playing', 'ended'])
})

test('an audio failure inside the player reports refused, logs it and stops', async (t) => {
    const logged = t.mock.method(console, 'error', () => {})
    const { ctx, network, player, events, handlers } = setup()
    const failure = new DOMException('Unsupported rate', 'NotSupportedError')
    ctx.createBuffer = () => {
        throw failure
    }
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    assert.deepEqual(events, ['error:refused'])
    assert.equal(network.calls[0].signal.aborted, true)
    assert.deepEqual(logged.mock.calls.map(call => call.arguments), [[failure]])
    assert.equal(ctx.state, 'suspended')
})

test('a stop from onPlaying silences the rest of the playback', async () => {
    const { ctx, network, player, events } = setup()
    player.play('/a', FORMAT, {
        onPlaying: () => {
            events.push('playing')
            player.stop()
        },
        onEnded: () => events.push('ended'),
        onError: reason => events.push(`error:${reason}`),
    })
    await flush()
    network.push(pcm(50))
    network.push(pcm(50))
    network.close()
    await flush()
    assert.equal(ctx.sources.length, 1)
    assert.equal(ctx.sources[0].stopped, true)
    assert.deepEqual(events, ['playing'])
})

test('plays interleaved stereo on two channels', async () => {
    const { ctx, network, player, events, handlers } = setup()
    const stereo = { rate: 1000, channels: 2 }
    player.play('/a', stereo, handlers)
    await flush()
    // Every frame: left -1 (0x8000), right 0.5 (0x4000).
    const frames = START_FRAMES
    const bytes = new Uint8Array(frames * 4)
    for (let frame = 0; frame < frames; frame++) {
        bytes[frame * 4 + 1] = 0x80
        bytes[frame * 4 + 3] = 0x40
    }
    network.push(bytes)
    await flush()
    assert.deepEqual(events, ['playing'])
    const [source] = ctx.sources
    assert.equal(source.buffer.numberOfChannels, 2)
    assert.equal(source.buffer.length, frames)
    assert.ok(source.buffer.getChannelData(0).every(sample => sample === -1))
    assert.ok(source.buffer.getChannelData(1).every(sample => sample === 0.5))
})

test('the context is suspended between playbacks', async () => {
    const { ctx, network, player, events, handlers } = setup()
    // A stop without a playback (the speech played through another player) suspends it too.
    player.prime()
    assert.equal(ctx.state, 'running')
    player.stop()
    assert.equal(ctx.state, 'suspended')

    // An ended playback.
    player.prime()
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    network.close()
    await flush()
    assert.equal(ctx.state, 'running')
    ctx.sources[0].onended()
    assert.deepEqual(events, ['playing', 'ended'])
    assert.equal(ctx.state, 'suspended')

    // A failed playback.
    const failed = setup({ status: 404 })
    failed.player.prime()
    failed.player.play('/a', FORMAT, failed.handlers)
    await flush()
    assert.deepEqual(failed.events, ['error:network'])
    assert.equal(failed.ctx.state, 'suspended')

    // A stopped playback; the next prime, inside the click, makes it run again.
    const stopped = setup()
    stopped.player.prime()
    stopped.player.play('/a', FORMAT, stopped.handlers)
    await flush()
    stopped.player.stop()
    assert.equal(stopped.ctx.state, 'suspended')
    stopped.player.prime()
    assert.equal(stopped.ctx.state, 'running')
})

test('a new play keeps the context running', async () => {
    const ctx = fakeContext()
    const first = fakeFetch()
    const second = fakeFetch()
    const fetches = [first.fetchImpl, second.fetchImpl]
    const player = createPcmStreamPlayer({
        fetchImpl: (...args) => fetches.shift()(...args),
        audioContextFactory: () => ctx,
    })
    player.prime()
    player.play('/a', FORMAT, recordingHandlers().handlers)
    await flush()
    const next = recordingHandlers()
    player.play('/b', FORMAT, next.handlers)
    assert.equal(first.calls[0].signal.aborted, true)
    assert.equal(ctx.suspendCalls, 0)
    assert.equal(ctx.state, 'running')
    await flush()
    second.push(pcm(START_FRAMES))
    await flush()
    assert.deepEqual(next.events, ['playing'])
    player.stop()
})

test('without an audio context, prime does not throw and play reports refused', async (t) => {
    const logged = t.mock.method(console, 'error', () => {})
    const network = fakeFetch()
    const failure = new Error('No AudioContext')
    const player = createPcmStreamPlayer({
        fetchImpl: network.fetchImpl,
        audioContextFactory: () => {
            throw failure
        },
    })
    player.prime()
    const { events, handlers } = recordingHandlers()
    player.play('/a', FORMAT, handlers)
    await flush()
    assert.deepEqual(events, ['error:refused'])
    assert.equal(network.calls.length, 0)
    assert.deepEqual(logged.mock.calls.map(call => call.arguments), [[failure]])
    player.stop()
})

test('a suspended context resumes before the first buffer', async () => {
    const { ctx, network, player, events, handlers } = setup()
    ctx.state = 'suspended'
    player.play('/a', FORMAT, handlers)
    await flush()
    network.push(pcm(START_FRAMES))
    await flush()
    assert.equal(ctx.resumeCalls, 1)
    assert.equal(ctx.sources.length, 1)
    assert.deepEqual(events, ['playing'])
})

test('a context that does not run refuses the playback', async () => {
    // The resume answers, but the context stays suspended.
    const answered = setup()
    answered.ctx.state = 'suspended'
    answered.ctx.resume = () => Promise.resolve()
    answered.player.play('/a', FORMAT, answered.handlers)
    await flush()
    answered.network.push(pcm(START_FRAMES))
    await flush()
    assert.equal(answered.ctx.sources.length, 0)
    assert.deepEqual(answered.events, ['error:refused'])
    assert.equal(answered.network.calls[0].signal.aborted, true)

    // The resume never answers (the browser waits for a user gesture): refused after the timeout.
    const pending = setup({}, { resumeTimeoutMs: 5 })
    pending.ctx.state = 'suspended'
    pending.ctx.resume = () => new Promise(() => {})
    pending.player.play('/a', FORMAT, pending.handlers)
    await flush()
    pending.network.push(pcm(50))
    pending.network.close()
    await flush()
    assert.deepEqual(pending.events, [])
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(pending.ctx.sources.length, 0)
    assert.deepEqual(pending.events, ['error:refused'])
})
