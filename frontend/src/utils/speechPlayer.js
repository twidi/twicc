// The audio output of the text-to-speech store: one HTMLAudioElement for the whole app.

/** A WAV file with no samples: playing it inside a click unlocks the element for later playback. */
const SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='

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
            audio.src = SILENT_WAV
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
