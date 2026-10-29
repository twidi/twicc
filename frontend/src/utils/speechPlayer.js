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
 * - `play(blob, onEnded, onError)` plays the blob and resolves when the audio starts. It
 *   rejects like `HTMLMediaElement.play()` (an `AbortError` when `stop()` interrupts it).
 * - `stop()` pauses the audio and releases it.
 *
 * The player owns every object URL it creates and revokes it on the next `play()` or `stop()`.
 */
export function createSpeechPlayer() {
    return playerFactory ? playerFactory() : createAudioElementPlayer()
}

function createAudioElementPlayer() {
    const audio = new Audio()
    let objectUrl = null

    function detach() {
        audio.onended = null
        audio.onerror = null
    }

    function releaseUrl() {
        if (objectUrl) {
            URL.revokeObjectURL(objectUrl)
            objectUrl = null
        }
    }

    return {
        prime() {
            detach()
            silentWav ??= silentWavDataUri()
            audio.src = silentWav
            audio.play()?.catch(() => {})
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
