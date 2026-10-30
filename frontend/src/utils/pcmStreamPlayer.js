// The streaming audio output of the text-to-speech store for raw PCM: one AudioContext for the whole app.

import { createPcm16Decoder } from './pcmAudio.js'

/** The audio held back before the first buffer plays, so a small network delay does not cut the sound. */
export const START_BUFFER_SECONDS = 0.3

let playerFactory = null

/** Tests only: make `createPcmStreamPlayer()` return `factory()` instead of the real player. */
export function setPcmStreamPlayerFactory(factory) {
    playerFactory = factory
}

/**
 * Create the PCM streaming player. `fetchImpl` and `audioContextFactory` are seams for the tests.
 *
 * - `prime()` runs synchronously inside the user's click. It creates the context once and resumes
 *   it, so the sound of a playback that starts after a long request is allowed.
 * - `play(url, { rate, channels }, handlers)` reads the 16-bit PCM body of `url` and plays it chunk
 *   after chunk. `handlers` is `{ onPlaying(), onEnded(), onError(reason) }`. Each handler fires
 *   at most once, and only one of `onEnded` and `onError` fires. The reason is `'network'` for a
 *   failed request or body, `'refused'` when the browser cannot play the samples.
 * - `stop()` aborts the request and silences the scheduled audio. No handler fires after it.
 *
 * The context stays open between playbacks: it is reused.
 */
export function createPcmStreamPlayer({ fetchImpl = fetch, audioContextFactory = () => new AudioContext() } = {}) {
    return playerFactory ? playerFactory() : createWebAudioPlayer(fetchImpl, audioContextFactory)
}

function createWebAudioPlayer(fetchImpl, audioContextFactory) {
    let ctx = null
    // The playback in progress: `{ controller, sources }`, or null.
    let current = null

    function context() {
        ctx ??= audioContextFactory()
        return ctx
    }

    function stop() {
        if (!current) return
        const { controller, sources } = current
        current = null
        controller.abort()
        for (const source of sources) {
            source.onended = null
            try {
                source.stop()
            } catch {
                // Already stopped.
            }
            source.disconnect()
        }
        sources.clear()
    }

    async function run(playback, url, { rate, channels }, handlers) {
        const audio = context()
        const live = () => current === playback
        const decoder = createPcm16Decoder(channels)
        // Decoded chunks held back until the start buffer is full.
        const held = []
        let heldFrames = 0
        let started = false
        let bodyDone = false
        let readFailed = false
        // When the next buffer starts, on the context clock.
        let next = 0

        // The last call for this playback: `onEnded`, or `onError` after a cut body.
        function finish() {
            if (!live()) return
            current = null
            if (readFailed) handlers.onError('network')
            else handlers.onEnded()
        }

        function fail() {
            if (!live()) return
            current = null
            handlers.onError('network')
        }

        function schedule(samples) {
            const frames = samples[0].length
            const buffer = audio.createBuffer(channels, frames, rate)
            samples.forEach((channelSamples, channel) => buffer.getChannelData(channel).set(channelSamples))
            const source = audio.createBufferSource()
            source.buffer = buffer
            source.connect(audio.destination)
            source.onended = () => {
                source.disconnect()
                playback.sources.delete(source)
                if (bodyDone && playback.sources.size === 0) finish()
            }
            const when = Math.max(next, audio.currentTime)
            source.start(when)
            next = when + frames / rate
            playback.sources.add(source)
            if (!started) {
                started = true
                handlers.onPlaying()
            }
        }

        function release() {
            // A handler can stop the playback while the held chunks are scheduled.
            for (const samples of held.splice(0)) if (live()) schedule(samples)
        }

        let response
        try {
            response = await fetchImpl(url, { signal: playback.controller.signal })
        } catch {
            fail()
            return
        }
        if (!live()) return
        if (!response.ok || !response.body) {
            response.body?.cancel().catch(() => {})
            fail()
            return
        }

        const reader = response.body.getReader()
        for (;;) {
            let chunk
            try {
                chunk = await reader.read()
            } catch {
                if (!live()) return
                readFailed = true
                break
            }
            if (!live()) return
            if (chunk.done) break
            const samples = decoder.decode(chunk.value)
            if (samples[0].length === 0) continue
            if (started) {
                schedule(samples)
            } else {
                held.push(samples)
                heldFrames += samples[0].length
                if (heldFrames / rate >= START_BUFFER_SECONDS) release()
            }
        }

        // A cut before the start: nothing played, so the error comes at once.
        if (readFailed && !started) {
            fail()
            return
        }
        // A body shorter than the start buffer plays when it ends.
        release()
        bodyDone = true
        if (playback.sources.size === 0) finish()
    }

    return {
        prime() {
            context()
                .resume()
                ?.catch(() => {})
        },

        play(url, format, handlers) {
            stop()
            const playback = { controller: new AbortController(), sources: new Set() }
            current = playback
            // The browser could not take the samples (a buffer it refuses to create, for example):
            // stop the playback and report it, so the caller does not wait for an end that never comes.
            run(playback, url, format, handlers).catch(error => {
                // A throw after the end comes from a handler: it is not this player's to hide.
                if (current !== playback) throw error
                stop()
                handlers.onError('refused')
            })
        },

        stop,
    }
}
