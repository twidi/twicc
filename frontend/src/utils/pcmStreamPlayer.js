// The streaming audio output of the text-to-speech store for raw PCM: one AudioContext for the whole app.

import { createPcm16Decoder } from './pcmAudio.js'

/** The audio held back before the first buffer plays, so a small network delay does not cut the sound. */
export const START_BUFFER_SECONDS = 0.3

/** After an underrun, the next buffer starts this far ahead of the clock: a start in the past is cut. */
export const CATCH_UP_LEAD_SECONDS = 0.02

/** How long the first buffer waits for a suspended context to run, before the playback is refused. */
const RESUME_TIMEOUT_MS = 2000

let playerFactory = null

/** Tests only: make `createPcmStreamPlayer()` return `factory()` instead of the real player. */
export function setPcmStreamPlayerFactory(factory) {
    playerFactory = factory
}

/**
 * Create the PCM streaming player. `fetchImpl`, `audioContextFactory` and `resumeTimeoutMs` are
 * seams for the tests.
 *
 * - `prime()` runs synchronously inside the user's click. It creates the context once and resumes
 *   it, so the sound of a playback that starts after a long request is allowed. It never throws:
 *   without an audio context, the next `play()` reports `'refused'`.
 * - `play(url, { rate, channels }, handlers)` reads the 16-bit PCM body of `url` and plays it chunk
 *   after chunk. `handlers` is `{ onPlaying(), onEnded(), onError(reason) }`. Each handler fires
 *   at most once, and only one of `onEnded` and `onError` fires. An empty body fires `onEnded`
 *   without `onPlaying`. The reason is `'network'` for a failed request or body, `'refused'` when
 *   the browser cannot play the samples (no context, a context that does not run, a refused buffer).
 * - `stop()` aborts the request, silences the scheduled audio and suspends the context. No handler
 *   fires after it.
 *
 * The context is reused, and suspended between playbacks: a running context keeps the audio
 * output busy for the life of the tab.
 */
export function createPcmStreamPlayer({
    fetchImpl = fetch,
    audioContextFactory = () => new AudioContext(),
    resumeTimeoutMs = RESUME_TIMEOUT_MS,
} = {}) {
    return playerFactory ? playerFactory() : createWebAudioPlayer(fetchImpl, audioContextFactory, resumeTimeoutMs)
}

function createWebAudioPlayer(fetchImpl, audioContextFactory, resumeTimeoutMs) {
    let ctx = null
    // The playback in progress: `{ controller, sources }`, or null.
    let current = null

    function context() {
        ctx ??= audioContextFactory()
        return ctx
    }

    function suspend() {
        ctx?.suspend()?.catch(() => {})
    }

    /** True once the context runs: at once, or after a resume within the timeout. */
    async function running(audio) {
        if (audio.state === 'running') return true
        let timer
        try {
            await Promise.race([
                audio.resume(),
                new Promise(resolve => {
                    timer = setTimeout(resolve, resumeTimeoutMs)
                }),
            ])
        } catch {
            // Refused: the state below tells.
        } finally {
            clearTimeout(timer)
        }
        return audio.state === 'running'
    }

    function stop() {
        halt()
        suspend()
    }

    // Abort the playback in progress and silence its sources. The context keeps its state.
    function halt() {
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
            suspend()
            if (readFailed) handlers.onError('network')
            else handlers.onEnded()
        }

        function fail() {
            if (!live()) return
            current = null
            suspend()
            handlers.onError('network')
        }

        function refuse() {
            if (!live()) return
            stop()
            handlers.onError('refused')
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
            // After an underrun the clock passed `next`: start a little ahead, then abut again.
            const when = next >= audio.currentTime ? next : audio.currentTime + CATCH_UP_LEAD_SECONDS
            source.start(when)
            next = when + frames / rate
            playback.sources.add(source)
            if (!started) {
                started = true
                handlers.onPlaying()
            }
        }

        async function release() {
            if (!held.length) return
            // A suspended context has a still clock: its sources would never play nor end.
            if (!(await running(audio))) {
                refuse()
                return
            }
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
                if (heldFrames / rate >= START_BUFFER_SECONDS) await release()
            }
        }

        // A cut before the start: nothing played, so the error comes at once.
        if (readFailed && !started) {
            fail()
            return
        }
        // A body shorter than the start buffer plays when it ends.
        await release()
        if (!live()) return
        bodyDone = true
        if (playback.sources.size === 0) finish()
    }

    return {
        prime() {
            try {
                context()
                    .resume()
                    ?.catch(() => {})
            } catch {
                // No audio context in this browser: the next play reports it refused.
            }
        },

        play(url, format, handlers) {
            halt()
            const playback = { controller: new AbortController(), sources: new Set() }
            current = playback
            // The browser could not take the samples (no context, a buffer it refuses to create, for
            // example): stop the playback and report it, so the caller does not wait for an end that
            // never comes.
            run(playback, url, format, handlers).catch(error => {
                // A throw after the end comes from a handler: it is not this player's to hide.
                if (current !== playback) throw error
                console.error(error)
                stop()
                handlers.onError('refused')
            })
        },

        stop,
    }
}
