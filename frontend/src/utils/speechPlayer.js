// The audio output of the text-to-speech store: one HTMLAudioElement for the whole app.

/**
 * A silent WAV data URI: 50 ms of zero samples, 8 kHz mono, 16-bit PCM (844 bytes).
 * Playing it inside a click unlocks the element for later playback. It carries real samples:
 * a WAV with an empty data chunk can fail to decode, which would leave the element locked.
 */
export function silentWavDataUri() {
    const sampleRate = 8000
    const dataBytes = (sampleRate / 20) * 2
    const view = new DataView(new ArrayBuffer(44 + dataBytes))
    const writeTag = (offset, tag) => [...tag].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)))
    writeTag(0, 'RIFF')
    view.setUint32(4, 36 + dataBytes, true)
    writeTag(8, 'WAVE')
    writeTag(12, 'fmt ')
    view.setUint32(16, 16, true) // fmt chunk size
    view.setUint16(20, 1, true) // PCM
    view.setUint16(22, 1, true) // mono
    view.setUint32(24, sampleRate, true)
    view.setUint32(28, sampleRate * 2, true) // byte rate
    view.setUint16(32, 2, true) // block align
    view.setUint16(34, 16, true) // bits per sample
    writeTag(36, 'data')
    view.setUint32(40, dataBytes, true)
    // The samples stay at zero (ArrayBuffer is zero-filled).
    const binary = String.fromCharCode(...new Uint8Array(view.buffer))
    return `data:audio/wav;base64,${btoa(binary)}`
}

let silentWav = null
let playerFactory = null

/** Tests only: make `createSpeechPlayer()` return `factory()` instead of the real player. */
export function setSpeechPlayerFactory(factory) {
    playerFactory = factory
}

/**
 * Create the speech player.
 *
 * - `prime()` runs synchronously inside the user's click. It plays a silent sound on the
 *   element, so a `play()` that follows a long request still counts as user-activated.
 * - `playUrl(url, handlers)` plays the audio at `url` while it downloads.
 * - `playBlob(blob, handlers)` plays an audio held in memory.
 *
 *   `handlers` is `{ onPlaying(), onEnded(), onError(reason) }`. Each handler fires at most once,
 *   and only one of `onEnded` and `onError` fires. The reason is `'refused'` when the browser
 *   cannot or will not play the audio, `'network'` when the download fails.
 * - `play(blob, onEnded, onError)` plays the blob and resolves when the audio starts. It
 *   rejects like `HTMLMediaElement.play()` (an `AbortError` when `stop()` interrupts it).
 *   Legacy: the store moves to `playUrl` and `playBlob`, then this method goes away.
 * - `stop()` pauses the audio and releases it. No handler fires after it.
 *
 * The player owns every object URL it creates and revokes it on the next playback or `stop()`.
 */
export function createSpeechPlayer() {
    return playerFactory ? playerFactory() : createAudioElementPlayer()
}

function createAudioElementPlayer() {
    const audio = new Audio()
    let objectUrl = null
    // A token of the playback in progress (`playUrl` or `playBlob`), or null.
    let current = null

    function detach() {
        current = null
        audio.onplaying = null
        audio.onended = null
        audio.onerror = null
    }

    function releaseUrl() {
        if (objectUrl) {
            URL.revokeObjectURL(objectUrl)
            objectUrl = null
        }
    }

    function start(src, handlers) {
        detach()
        const playback = {}
        current = playback
        let playing = false
        // `onEnded` or `onError`: the last call for this playback.
        const finish = callback => {
            if (current !== playback) return
            detach()
            callback()
        }
        audio.onplaying = () => {
            // `playing` fires again after each stall: only the first one counts.
            if (current !== playback || playing) return
            playing = true
            handlers.onPlaying()
        }
        audio.onended = () => finish(() => handlers.onEnded())
        audio.onerror = () => {
            const code = audio.error?.code
            const refused = code === MediaError.MEDIA_ERR_DECODE || code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
            finish(() => handlers.onError(refused ? 'refused' : 'network'))
        }
        audio.src = src
        audio.play()?.catch(error => {
            // `stop()` or a newer playback interrupted this one: nothing to report.
            if (error?.name === 'AbortError') return
            // `NotAllowedError` (no user activation), `NotSupportedError` (no usable source).
            finish(() => handlers.onError('refused'))
        })
    }

    return {
        prime() {
            detach()
            silentWav ??= silentWavDataUri()
            audio.src = silentWav
            audio.play()?.catch(() => {})
        },

        playUrl(url, handlers) {
            const previousUrl = objectUrl
            objectUrl = null
            start(url, handlers)
            if (previousUrl) URL.revokeObjectURL(previousUrl)
        },

        playBlob(blob, handlers) {
            const previousUrl = objectUrl
            objectUrl = URL.createObjectURL(blob)
            start(objectUrl, handlers)
            if (previousUrl) URL.revokeObjectURL(previousUrl)
        },

        async play(blob, onEnded, onError) {
            detach()
            const previousUrl = objectUrl
            objectUrl = URL.createObjectURL(blob)
            audio.src = objectUrl
            if (previousUrl) URL.revokeObjectURL(previousUrl)
            audio.onended = () => onEnded()
            audio.onerror = () => onError()
            await audio.play()
        },

        stop() {
            detach()
            audio.pause()
            // Removing the attribute (not `src = ''`) empties the element without a media error.
            audio.removeAttribute('src')
            audio.load()
            releaseUrl()
        },
    }
}
